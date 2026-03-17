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
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


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

    # Stats
    total = len(jobs)
    running_count = JobRun.query.filter_by(status="running").count()
    success_today = (
        JobRun.query.filter(
            JobRun.status == "success", JobRun.started_at >= today_start
        ).count()
    )
    failed_today = (
        JobRun.query.filter(
            JobRun.status == "failed", JobRun.started_at >= today_start
        ).count()
    )

    return jsonify(
        {
            "jobs": payload,
            "stats": {
                "total_jobs": total,
                "running": running_count,
                "success_today": success_today,
                "failed_today": failed_today,
            },
            "server_time": now.isoformat(),
        }
    )


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
            datetime.fromisoformat(data["next_run_at"])
            if data.get("next_run_at")
            else None
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
# API – job runs
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
    """Manually enqueue a run (creates a 'running' record; caller completes it)."""
    job = Job.query.get_or_404(job_id)
    run = JobRun(job_id=job.id, started_at=datetime.utcnow(), status="running")
    db.session.add(run)
    db.session.commit()
    return jsonify(run.to_dict()), 201


@app.route("/api/runs/<int:run_id>", methods=["PATCH"])
def api_update_run(run_id):
    """Complete or update a run record."""
    run = JobRun.query.get_or_404(run_id)
    data = request.get_json(force=True)
    for field in ("status", "output", "error", "duration_ms"):
        if field in data:
            setattr(run, field, data[field])
    if data.get("status") in ("success", "warning", "failed") and not run.completed_at:
        run.completed_at = datetime.utcnow()
        if run.started_at:
            delta = (run.completed_at - run.started_at).total_seconds() * 1000
            run.duration_ms = run.duration_ms or int(delta)
    db.session.commit()
    return jsonify(run.to_dict())


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def init_db():
    db.create_all()
    if Job.query.count() == 0:
        from seed import seed
        seed()


if __name__ == "__main__":
    with app.app_context():
        init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
