"""
cowork_reader.py
----------------
Reads Claude Cowork scheduled task definitions and session history
from Claude for Desktop's local storage on macOS.

Paths (all relative to the running user's home directory):
  Task defs        : ~/Documents/Claude/Scheduled/<task-name>/SKILL.md
  Cowork sessions  : ~/Library/Application Support/Claude/local-agent-mode-sessions/**/*.json
  Regular sessions : ~/Library/Application Support/Claude/claude-code-sessions/**/*.json
  Transcripts      : ~/.claude/projects/<project-dir>/<cliSessionId>.jsonl
                     project-dir is the session cwd with / replaced by -
                     e.g. cwd=/sessions/my-task → dir=-sessions-my-task
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

_CLAUDE_SUPPORT = Path.home() / "Library" / "Application Support" / "Claude"

TASKS_DIR             = Path.home() / "Documents" / "Claude" / "Scheduled"
SESSIONS_DIR          = _CLAUDE_SUPPORT / "claude-code-sessions"
LOCAL_AGENT_SESS_DIR  = _CLAUDE_SUPPORT / "local-agent-mode-sessions"
PROJECTS_DIR          = Path.home() / ".claude" / "projects"


# ── Task definition parser ─────────────────────────────────────────────────

def parse_skill_md(skill_path: Path) -> dict:
    """Extract name, description, and schedule from a SKILL.md file."""
    try:
        text = skill_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""

    raw_dir_name = skill_path.parent.name
    # Prettify slug → "Arc to Obsidian Sync" as default; overridden by frontmatter
    name        = raw_dir_name.replace("-", " ").replace("_", " ").title()
    description = ""
    schedule    = None

    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).splitlines():
            if m := re.match(r'name\s*:\s*["\']?(.*?)["\']?\s*$', line, re.IGNORECASE):
                raw = m.group(1).strip()
                # Prettify slug-style names (e.g. "arc-to-obsidian-sync" → "Arc To Obsidian Sync")
                if raw and re.fullmatch(r'[a-z0-9][\w\-]*', raw):
                    name = raw.replace("-", " ").replace("_", " ").title()
                elif raw:
                    name = raw
            elif m := re.match(r'description\s*:\s*["\']?(.*?)["\']?\s*$', line, re.IGNORECASE):
                description = m.group(1).strip()
            elif m := re.match(r'schedule\s*:\s*["\']?(.*?)["\']?\s*$', line, re.IGNORECASE):
                schedule = m.group(1).strip()

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
    """
    Load all session JSON files from Claude Desktop's session stores.
    Scans both claude-code-sessions (regular) and local-agent-mode-sessions
    (Cowork / agent-mode scheduled tasks).
    """
    sessions = []
    dirs_to_scan = [SESSIONS_DIR, LOCAL_AGENT_SESS_DIR]
    for base_dir in dirs_to_scan:
        if not base_dir.exists():
            continue
        for json_file in base_dir.rglob("*.json"):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8", errors="replace"))
                if isinstance(data, dict):
                    data["_file"] = str(json_file)
                    sessions.append(data)
            except Exception:
                pass
    return sessions


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_/]+", " ", str(s).lower()).strip()


def find_sessions_for_task(task_dir_name: str, all_sessions: list[dict]) -> list[dict]:
    """Match sessions to a task by cwd tail or session title."""
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
    matched.sort(key=lambda x: x.get("lastActivityAt") or x.get("createdAt") or 0, reverse=True)
    return matched


# ── JSONL discovery ────────────────────────────────────────────────────────

def _cwd_to_project_dir(cwd: str) -> str:
    """
    Convert a session's cwd to the Claude project directory name.
    Claude Code encodes the cwd by replacing every '/' with '-'.
    e.g. /sessions/my-task  →  -sessions-my-task
    """
    return cwd.replace("/", "-")


def _find_jsonl(cli_session_id: str, cwd: str) -> Path | None:
    """
    Try to locate the JSONL transcript file.

    Strategy 1: search ~/.claude/projects/**/<cli_session_id>.jsonl
    Strategy 2: list all .jsonl files in the cwd-derived project dir
                and return the one whose stem matches cli_session_id,
                or the newest file if cli_session_id is missing.
    """
    if not PROJECTS_DIR.exists():
        return None

    # Strategy 1: direct lookup by cliSessionId
    if cli_session_id:
        for candidate in PROJECTS_DIR.rglob(f"{cli_session_id}.jsonl"):
            return candidate

    # Strategy 2: derive project dir from cwd and take the newest JSONL
    if cwd:
        project_dir = PROJECTS_DIR / _cwd_to_project_dir(cwd)
        if project_dir.exists():
            jsonl_files = sorted(
                project_dir.glob("*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if jsonl_files:
                # If we have a cli_session_id but didn't find it above,
                # try case-insensitive match inside this dir
                if cli_session_id:
                    for f in jsonl_files:
                        if f.stem.lower() == cli_session_id.lower():
                            return f
                # Otherwise return the most recently modified JSONL
                return jsonl_files[0]

    return None


# ── Transcript analyser ────────────────────────────────────────────────────

_FAIL_KW    = frozenset(["error:", "failed", "traceback", "exception",
                          "could not", "unable to", "timed out"])
_SUCCESS_KW = frozenset(["complete", "done", "finish", "success",
                          "saved", "written", "created", "updated", "added"])


def analyze_jsonl(cli_session_id: str, cwd: str = "") -> dict:
    """
    Parse a JSONL transcript and return status/output/timing.
    Falls back to None values when the file cannot be found.
    """
    empty = {"status": None, "output": "", "error": "",
             "started_at": None, "completed_at": None, "duration_ms": None}

    jsonl_path = _find_jsonl(cli_session_id, cwd)
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

    lower       = last_assistant.lower()
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
        if started_at and completed_at else None
    )

    return {
        "status":       status,
        "output":       last_assistant[:600] if status != "failed" else "",
        "error":        last_assistant[:600] if status == "failed"  else "",
        "started_at":   started_at,
        "completed_at": completed_at,
        "duration_ms":  duration_ms,
    }


def _status_from_metadata(created_ms, last_ms) -> str:
    """
    Heuristic when no JSONL is available: infer status from session timing.
    A very short session (<8 s) probably errored; anything longer is treated
    as success since we have no evidence of failure.
    """
    if not created_ms or not last_ms:
        return "success"
    duration_s = (last_ms - created_ms) / 1000
    if duration_s < 8:
        return "failed"
    return "success"


# ── Main entry point ───────────────────────────────────────────────────────

def is_available() -> bool:
    return TASKS_DIR.exists()


def read_cowork_tasks() -> list[dict]:
    """
    Return a list of task dicts, each with a 'runs' list.
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
            cli_id     = s.get("cliSessionId", "")
            cwd        = s.get("cwd", "")
            created_ms = s.get("createdAt")
            last_ms    = s.get("lastActivityAt")

            meta_started = (
                datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
                if created_ms else None
            )
            meta_completed = (
                datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc)
                if last_ms else None
            )
            meta_duration = (
                int((meta_completed - meta_started).total_seconds() * 1000)
                if meta_started and meta_completed else None
            )

            analysis = analyze_jsonl(cli_id, cwd)

            # Prefer JSONL-derived status; fall back to metadata heuristic
            status = analysis["status"] or _status_from_metadata(created_ms, last_ms)

            runs.append({
                "session_id":     s.get("sessionId", ""),
                "cli_session_id": cli_id,
                "started_at":     analysis["started_at"] or meta_started,
                "completed_at":   analysis["completed_at"] or meta_completed,
                "status":         status,
                "output":         analysis["output"],
                "error":          analysis["error"],
                "duration_ms":    analysis["duration_ms"] or meta_duration,
            })

        task["runs"] = runs
        tasks.append(task)

    return tasks
