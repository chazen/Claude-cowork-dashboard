"""Seed the database with sample scheduled Claude cowork jobs."""
from datetime import datetime, timedelta
import random
from models import db, Job, JobRun


SAMPLE_JOBS = [
    {
        "name": "Daily Standup Summarizer",
        "description": "Reads Slack standup messages and generates a concise team summary via Claude.",
        "schedule": "0 9 * * 1-5",  # Weekdays 9am
        "next_offset_hours": 20,
    },
    {
        "name": "PR Review Digest",
        "description": "Scans open pull requests and drafts review comments using Claude.",
        "schedule": "0 */4 * * *",  # Every 4 hours
        "next_offset_hours": 3,
    },
    {
        "name": "Codebase Health Report",
        "description": "Runs static analysis and feeds results to Claude for a narrative health report.",
        "schedule": "0 6 * * 1",  # Weekly Monday 6am
        "next_offset_hours": 84,
    },
    {
        "name": "Ticket Triage Agent",
        "description": "Classifies and prioritises new GitHub issues using Claude.",
        "schedule": "*/30 * * * *",  # Every 30 minutes
        "next_offset_hours": 0.4,
    },
    {
        "name": "Documentation Sync",
        "description": "Detects code changes and asks Claude to update corresponding docs.",
        "schedule": "0 2 * * *",  # Daily 2am
        "next_offset_hours": 8,
    },
    {
        "name": "Security Audit Sweep",
        "description": "Passes dependency graph and recent diffs to Claude for security review.",
        "schedule": "0 3 * * 0",  # Weekly Sunday 3am
        "next_offset_hours": 130,
    },
    {
        "name": "Customer Feedback Analyser",
        "description": "Aggregates new feedback entries and asks Claude to identify themes.",
        "schedule": "0 8 * * *",  # Daily 8am
        "next_offset_hours": 18,
    },
    {
        "name": "Release Notes Generator",
        "description": "Reads commits since last tag and generates release notes via Claude.",
        "schedule": "0 17 * * 5",  # Friday 5pm
        "next_offset_hours": 92,
    },
]

# Realistic outcome weights: mostly success, occasionally warnings, rare failures
OUTCOME_WEIGHTS = {
    "success": 0.72,
    "warning": 0.18,
    "failed": 0.10,
}

SAMPLE_OUTPUTS = {
    "success": [
        "Job completed. Claude processed 142 tokens. Summary generated and posted.",
        "Completed in 3.2s. 7 items processed, 7 successful.",
        "Claude responded with structured output. Results stored.",
        "All checks passed. Report dispatched to #team-digest.",
    ],
    "warning": [
        "Completed with warnings: rate limit hit once, retried successfully.",
        "3 of 5 items processed. 2 skipped due to missing context.",
        "Claude flagged low-confidence output. Manual review recommended.",
        "Token limit approached; output truncated. Consider chunking input.",
    ],
    "failed": [
        "Error: Claude API timeout after 30s. Job aborted.",
        "Failed: upstream data source returned 503.",
        "Unhandled exception in post-processing step. See logs.",
        "Authentication error: API key expired.",
    ],
}


def pick_outcome():
    r = random.random()
    if r < OUTCOME_WEIGHTS["success"]:
        return "success"
    elif r < OUTCOME_WEIGHTS["success"] + OUTCOME_WEIGHTS["warning"]:
        return "warning"
    return "failed"


def seed():
    now = datetime.utcnow()

    for job_data in SAMPLE_JOBS:
        job = Job(
            name=job_data["name"],
            description=job_data["description"],
            schedule=job_data["schedule"],
            enabled=True,
            next_run_at=now + timedelta(hours=job_data["next_offset_hours"]),
        )
        db.session.add(job)
        db.session.flush()  # get job.id

        # Generate 8-12 past runs spread over the last 30 days
        num_runs = random.randint(8, 12)
        run_times = sorted(
            [now - timedelta(hours=random.uniform(1, 720)) for _ in range(num_runs)]
        )

        for started_at in run_times:
            status = pick_outcome()
            duration_ms = random.randint(800, 15000)
            output_text = random.choice(SAMPLE_OUTPUTS[status])
            error_text = output_text if status == "failed" else ""
            run = JobRun(
                job_id=job.id,
                started_at=started_at,
                completed_at=started_at + timedelta(milliseconds=duration_ms),
                status=status,
                output=output_text if status != "failed" else "",
                error=error_text,
                duration_ms=duration_ms,
            )
            db.session.add(run)

        # Optionally add one currently-running job
        if random.random() < 0.2:
            run = JobRun(
                job_id=job.id,
                started_at=now - timedelta(seconds=random.randint(5, 60)),
                status="running",
            )
            db.session.add(run)

    db.session.commit()
    print(f"Seeded {len(SAMPLE_JOBS)} jobs with run history.")
