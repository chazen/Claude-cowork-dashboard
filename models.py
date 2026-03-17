from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Job(db.Model):
    __tablename__ = "jobs"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    schedule = db.Column(db.String(100), nullable=False)  # cron or human-readable
    enabled = db.Column(db.Boolean, default=True)
    next_run_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Cowork integration
    source = db.Column(db.String(20), default="manual")     # "cowork" | "manual"
    cowork_task_dir = db.Column(db.String(300), nullable=True)  # SKILL.md parent dir name
    runs = db.relationship("JobRun", backref="job", lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "schedule": self.schedule,
            "enabled": self.enabled,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "created_at": self.created_at.isoformat(),
            "source": self.source,
            "cowork_task_dir": self.cowork_task_dir,
        }


class JobRun(db.Model):
    __tablename__ = "job_runs"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id"), nullable=False)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(30), default="running")  # running / success / warning / failed
    output = db.Column(db.Text, default="")
    error = db.Column(db.Text, default="")
    duration_ms = db.Column(db.Integer, nullable=True)
    cowork_session_id = db.Column(db.String(200), nullable=True)  # dedup key

    def to_dict(self):
        return {
            "id": self.id,
            "job_id": self.job_id,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "status": self.status,
            "output": self.output,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "cowork_session_id": self.cowork_session_id,
        }
