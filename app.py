import os
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template, request, abort
from models import db, Job, JobRun

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///jobs.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)


# ---------------------------------------------------------------------------
# Cowork sync
# ---------------------------------------------------------------------------

def sync_from_cowork():
    """
    Read Claude Cowork scheduled tasks from the macOS filesystem and
    upsert them into the local SQLite DB.  Returns (added, updated) counts.
    """
    from cowork_reader import read_cowork_tasks, is_available
    if not is_available():
        return 0, 0

    tasks  = read_cowork_tasks()
    added  = 0
    updated = 0

    for task in tasks:
        # Find or create the Job record keyed on cowork_task_dir
        job = Job.query.filter_by(cowork_task_dir=task["task_dir"]).first()
        if not job:
            job = Job(
                source="cowork",
                cowork_task_dir=task["task_dir"],
            )
            db.session.add(job)
            added += 1
        else:
            updated += 1

        job.name        = task["name"]
        job.description = task["description"]
        job.schedule    = task["schedule"]
        job.enabled     = True

        # Upsert runs keyed on cowork_session_id
        for run_data in task["runs"]:
            sid = run_data.get("session_id") or run_data.get("cli_session_id")
            if not sid:
                continue

            run = JobRun.query.filter_by(cowork_session_id=sid).first()
            if not run:
                run = JobRun(job=job, cowork_session_id=sid)
                db.session.add(run)

            def _dt(v):
                if v is None:
                    return None
                if isinstance(v, datetime):
                    return v.replace(tzinfo=None)  # store as naive UTC
                return v

            run.started_at   = _dt(run_data.get("started_at"))   or run.started_at
            run.completed_at = _dt(run_data.get("completed_at"))
            run.status       = run_data.get("status", "unknown")
            run.output       = run_data.get("output", "")
            run.error        = run_data.get("error",  "")
            run.duration_ms  = run_data.get("duration_ms")

    db.session.commit()
    return added, updated


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# API – sync
# ---------------------------------------------------------------------------

@app.route("/api/sync", methods=["POST"])
def api_sync():
    """Trigger a re-scan of the Claude Cowork filesystem data."""
    from cowork_reader import is_available
    if not is_available():
        return jsonify({
            "ok": False,
            "message": "Claude Cowork task directory not found on this machine. "
                       "Make sure Claude for Desktop is installed and you have created "
                       "at least one scheduled task in Cowork.",
            "cowork_available": False,
        }), 404

    added, updated = sync_from_cowork()
    return jsonify({
        "ok": True,
        "added":   added,
        "updated": updated,
        "cowork_available": True,
        "synced_at": datetime.utcnow().isoformat(),
    })


@app.route("/api/status")
def api_status():
    """Return whether Cowork data is available on this machine."""
    from cowork_reader import is_available, TASKS_DIR
    return jsonify({
        "cowork_available": is_available(),
        "tasks_dir": str(TASKS_DIR),
        "job_count": Job.query.count(),
        "cowork_job_count": Job.query.filter_by(source="cowork").count(),
    })


# ---------------------------------------------------------------------------
# API – dashboard summary
# ---------------------------------------------------------------------------

@app.route("/api/dashboard")
def api_dashboard():
    """Return all jobs with their last 4 runs and top-level stats."""
    jobs = Job.query.order_by(Job.name).all()
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    payload = []
    for job in jobs:
        runs = (
            JobRun.query.filter_by(job_id=job.id)
            .order_by(JobRun.started_at.desc())
            .limit(4)
            .all()
        )
        d = job.to_dict()
        d["recent_runs"] = [r.to_dict() for r in runs]
        payload.append(d)

    running_count = JobRun.query.filter_by(status="running").count()
    success_today = JobRun.query.filter(
        JobRun.status == "success", JobRun.started_at >= today_start
    ).count()
    failed_today = JobRun.query.filter(
        JobRun.status == "failed", JobRun.started_at >= today_start
    ).count()

    from cowork_reader import is_available
    return jsonify({
        "jobs": payload,
        "stats": {
            "total_jobs":    len(jobs),
            "running":       running_count,
            "success_today": success_today,
            "failed_today":  failed_today,
        },
        "cowork_available": is_available(),
        "server_time": now.isoformat(),
    })


# ---------------------------------------------------------------------------
# API – jobs
# ---------------------------------------------------------------------------

@app.route("/api/jobs")
def api_jobs():
    jobs = Job.query.order_by(Job.name).all()
    return jsonify([j.to_dict() for j in jobs])


@app.route("/api/jobs/<int:job_id>")
def api_job(job_id):
    job = Job.query.get_or_404(job_id)
    runs = (
        JobRun.query.filter_by(job_id=job_id)
        .order_by(JobRun.started_at.desc())
        .limit(20)
        .all()
    )
    d = job.to_dict()
    d["runs"] = [r.to_dict() for r in runs]
    return jsonify(d)


@app.route("/api/jobs", methods=["POST"])
def api_create_job():
    data = request.get_json(force=True)
    if not data or not data.get("name") or not data.get("schedule"):
        abort(400, "name and schedule are required")
    job = Job(
        name=data["name"],
        description=data.get("description", ""),
        schedule=data["schedule"],
        enabled=data.get("enabled", True),
        next_run_at=(
            datetime.fromisoformat(data["next_run_at"]) if data.get("next_run_at") else None
        ),
    )
    db.session.add(job)
    db.session.commit()
    return jsonify(job.to_dict()), 201


@app.route("/api/jobs/<int:job_id>", methods=["PATCH"])
def api_update_job(job_id):
    job = Job.query.get_or_404(job_id)
    data = request.get_json(force=True)
    for field in ("name", "description", "schedule", "enabled"):
        if field in data:
            setattr(job, field, data[field])
    db.session.commit()
    return jsonify(job.to_dict())


@app.route("/api/jobs/<int:job_id>", methods=["DELETE"])
def api_delete_job(job_id):
    job = Job.query.get_or_404(job_id)
    db.session.delete(job)
    db.session.commit()
    return "", 204


# ---------------------------------------------------------------------------
# API – runs
# ---------------------------------------------------------------------------

@app.route("/api/jobs/<int:job_id>/runs")
def api_job_runs(job_id):
    Job.query.get_or_404(job_id)
    limit = min(int(request.args.get("limit", 20)), 100)
    runs = (
        JobRun.query.filter_by(job_id=job_id)
        .order_by(JobRun.started_at.desc())
        .limit(limit)
        .all()
    )
    return jsonify([r.to_dict() for r in runs])


@app.route("/api/jobs/<int:job_id>/trigger", methods=["POST"])
def api_trigger_job(job_id):
    job = Job.query.get_or_404(job_id)
    run = JobRun(job_id=job.id, started_at=datetime.utcnow(), status="running")
    db.session.add(run)
    db.session.commit()
    return jsonify(run.to_dict()), 201


@app.route("/api/runs/<int:run_id>", methods=["PATCH"])
def api_update_run(run_id):
    run = JobRun.query.get_or_404(run_id)
    data = request.get_json(force=True)
    for field in ("status", "output", "error", "duration_ms"):
        if field in data:
            setattr(run, field, data[field])
    if data.get("status") in ("success", "warning", "failed") and not run.completed_at:
        run.completed_at = datetime.utcnow()
        if run.started_at:
            run.duration_ms = run.duration_ms or int(
                (run.completed_at - run.started_at).total_seconds() * 1000
            )
    db.session.commit()
    return jsonify(run.to_dict())


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def init_db():
    db.create_all()
    # Migrate: add new columns if upgrading from old schema
    from sqlalchemy import text, inspect
    inspector = inspect(db.engine)
    job_cols = [c["name"] for c in inspector.get_columns("jobs")]
    run_cols = [c["name"] for c in inspector.get_columns("job_runs")]
    with db.engine.connect() as conn:
        if "source" not in job_cols:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN source VARCHAR(20) DEFAULT 'manual'"))
        if "cowork_task_dir" not in job_cols:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN cowork_task_dir VARCHAR(300)"))
        if "cowork_session_id" not in run_cols:
            conn.execute(text("ALTER TABLE job_runs ADD COLUMN cowork_session_id VARCHAR(200)"))
        conn.commit()

    # Prefer real Cowork data; fall back to seed only if DB is empty and Cowork unavailable
    from cowork_reader import is_available
    if is_available():
        print("Claude Cowork detected — syncing tasks…")
        added, updated = sync_from_cowork()
        print(f"  Sync complete: {added} new jobs, {updated} updated.")
    elif Job.query.count() == 0:
        print("Cowork not found — seeding sample data for preview.")
        from seed import seed
        seed()


if __name__ == "__main__":
    with app.app_context():
        init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
