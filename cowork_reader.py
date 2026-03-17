"""
cowork_reader.py
----------------
Reads Claude Cowork scheduled task definitions and session history
from Claude for Desktop's local storage on macOS.

Paths (all relative to the running user's home directory):
  Task defs : ~/Documents/Claude/Scheduled/<task-name>/SKILL.md
  Sessions  : ~/Library/Application Support/Claude/claude-code-sessions/**/*.json
  Transcripts: ~/.claude/projects/<project-dir>/<cliSessionId>.jsonl
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

TASKS_DIR    = Path.home() / "Documents" / "Claude" / "Scheduled"
SESSIONS_DIR = (
    Path.home() / "Library" / "Application Support" / "Claude" / "claude-code-sessions"
)
PROJECTS_DIR = Path.home() / ".claude" / "projects"


# ── Task definition parser ─────────────────────────────────────────────────

def parse_skill_md(skill_path: Path) -> dict:
    """Extract name, description, and schedule from a SKILL.md file."""
    try:
        text = skill_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""

    # Default to directory name
    name        = skill_path.parent.name
    description = ""
    schedule    = None

    # Parse YAML frontmatter between --- markers
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).splitlines():
            if m := re.match(r'name\s*:\s*["\']?(.*?)["\']?\s*$', line, re.IGNORECASE):
                name = m.group(1).strip() or name
            elif m := re.match(r'description\s*:\s*["\']?(.*?)["\']?\s*$', line, re.IGNORECASE):
                description = m.group(1).strip()
            elif m := re.match(r'schedule\s*:\s*["\']?(.*?)["\']?\s*$', line, re.IGNORECASE):
                schedule = m.group(1).strip()

    # Fall back to first non-empty body line as description
    if not description:
        body = re.sub(r"^---\s*\n.*?\n---\s*\n?", "", text, flags=re.DOTALL)
        for line in body.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                description = stripped[:200]
                break

    return {
        "name":        name,
        "description": description,
        "schedule":    schedule or "Scheduled (via Cowork)",
        "task_dir":    skill_path.parent.name,
        "skill_path":  str(skill_path),
    }


# ── Session loader ─────────────────────────────────────────────────────────

def load_all_sessions() -> list[dict]:
    """Load all session JSON files from Claude Desktop's session store."""
    sessions = []
    if not SESSIONS_DIR.exists():
        return sessions
    for json_file in SESSIONS_DIR.rglob("*.json"):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                data["_file"] = str(json_file)
                sessions.append(data)
        except Exception:
            pass
    return sessions


def _norm(s: str) -> str:
    """Lowercase, collapse whitespace and common separators for fuzzy matching."""
    return re.sub(r"[\s\-_/]+", " ", str(s).lower()).strip()


def find_sessions_for_task(task_dir_name: str, all_sessions: list[dict]) -> list[dict]:
    """
    Return sessions that belong to a given task, matched by:
      1. The last segment of cwd (VM working directory)
      2. The session title (first user message)
    """
    needle = _norm(task_dir_name)
    matched = []
    seen = set()
    for s in all_sessions:
        sid = s.get("sessionId", s.get("_file", ""))
        if sid in seen:
            continue
        cwd_tail = _norm(s.get("cwd", "").rsplit("/", 1)[-1])
        title    = _norm(s.get("title", ""))
        if needle in cwd_tail or cwd_tail in needle or needle in title:
            matched.append(s)
            seen.add(sid)

    # Newest first
    matched.sort(key=lambda x: x.get("lastActivityAt") or x.get("createdAt") or 0, reverse=True)
    return matched


# ── Transcript analyser ────────────────────────────────────────────────────

_FAIL_KW    = frozenset(["error:", "failed", "traceback", "exception", "could not", "unable to", "timed out"])
_SUCCESS_KW = frozenset(["complete", "done", "finish", "success", "saved", "written", "created", "updated", "added"])


def analyze_jsonl(cli_session_id: str) -> dict:
    """
    Parse a JSONL transcript and return:
      status  : "success" | "warning" | "failed" | "unknown"
      output  : last assistant text (snippet)
      error   : error text if applicable
      started_at / completed_at : datetime | None
      duration_ms : int | None
    """
    empty = {"status": "unknown", "output": "", "error": "",
             "started_at": None, "completed_at": None, "duration_ms": None}
    if not cli_session_id:
        return empty

    jsonl_path = None
    for candidate in PROJECTS_DIR.rglob(f"{cli_session_id}.jsonl"):
        jsonl_path = candidate
        break
    if not jsonl_path:
        return empty

    messages   = []
    timestamps = []
    try:
        for raw in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
                messages.append(msg)
                if ts := msg.get("timestamp"):
                    try:
                        timestamps.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
                    except ValueError:
                        pass
            except json.JSONDecodeError:
                pass
    except OSError:
        return empty

    if not messages:
        return empty

    # Walk messages to count errors and capture last assistant text
    tool_error_count = 0
    last_assistant   = ""

    for msg in messages:
        role    = msg.get("role", "")
        content = msg.get("content", "")

        if role == "tool":
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("is_error"):
                        tool_error_count += 1
            elif isinstance(content, str) and re.search(r"\berror\b", content, re.IGNORECASE):
                tool_error_count += 1

        elif role == "assistant":
            if isinstance(content, str):
                last_assistant = content
            elif isinstance(content, list):
                parts = [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                last_assistant = " ".join(parts)

    lower = last_assistant.lower()
    has_fail    = any(kw in lower for kw in _FAIL_KW)
    has_success = any(kw in lower for kw in _SUCCESS_KW)

    if tool_error_count == 0 and not has_fail:
        status = "success"
    elif tool_error_count > 0 and has_success:
        status = "warning"
    elif has_fail and not has_success:
        status = "failed"
    elif tool_error_count > 0:
        status = "warning"
    else:
        status = "success"

    started_at   = min(timestamps) if timestamps else None
    completed_at = max(timestamps) if timestamps else None
    duration_ms  = (
        int((completed_at - started_at).total_seconds() * 1000)
        if started_at and completed_at
        else None
    )

    return {
        "status":       status,
        "output":       last_assistant[:600] if status != "failed" else "",
        "error":        last_assistant[:600] if status == "failed"  else "",
        "started_at":   started_at,
        "completed_at": completed_at,
        "duration_ms":  duration_ms,
    }


# ── Main entry point ───────────────────────────────────────────────────────

def is_available() -> bool:
    """True if Claude Cowork task directory exists on this machine."""
    return TASKS_DIR.exists()


def read_cowork_tasks() -> list[dict]:
    """
    Return a list of task dicts:
      name, description, schedule, task_dir, skill_path, runs: [...]

    Each run dict:
      session_id, cli_session_id, started_at, completed_at,
      status, output, error, duration_ms
    """
    if not TASKS_DIR.exists():
        return []

    all_sessions = load_all_sessions()
    tasks = []

    for skill_md in sorted(TASKS_DIR.glob("*/SKILL.md")):
        task     = parse_skill_md(skill_md)
        sessions = find_sessions_for_task(task["task_dir"], all_sessions)
        runs = []

        for s in sessions[:30]:
            cli_id       = s.get("cliSessionId", "")
            created_ms   = s.get("createdAt")
            last_ms      = s.get("lastActivityAt")

            # Fast-path timestamps from session metadata
            meta_started   = (
                datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
                if created_ms else None
            )
            meta_completed = (
                datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc)
                if last_ms else None
            )

            analysis = analyze_jsonl(cli_id)

            runs.append({
                "session_id":     s.get("sessionId", ""),
                "cli_session_id": cli_id,
                "started_at":     analysis["started_at"] or meta_started,
                "completed_at":   analysis["completed_at"] or meta_completed,
                "status":         analysis["status"],
                "output":         analysis["output"],
                "error":          analysis["error"],
                "duration_ms":    analysis["duration_ms"] or (
                    int((meta_completed - meta_started).total_seconds() * 1000)
                    if meta_started and meta_completed else None
                ),
            })

        task["runs"] = runs
        tasks.append(task)

    return tasks
