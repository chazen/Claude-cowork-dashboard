# Claude Cowork – Job Dashboard

A simple Flask dashboard for monitoring scheduled Claude cowork jobs over your Tailscale VPN.

## Features

- **Stats bar** – total jobs, running now, succeeded today, failed today
- **Running lane** – live elapsed timer for in-flight jobs
- **Up Next lane** – next scheduled executions sorted by time
- **Jobs grid** – every job with its last 4 run results shown as coloured status pips (✓ success, ⚠ warning, ✕ failed)
- **Run detail modal** – click any pip to see timestamps, duration, and output/error logs
- **Search** – filter jobs by name or description
- **Auto-refresh** every 30 seconds

## Quick start

```bash
./run.sh          # creates .venv, installs deps, seeds DB, starts on :5000
```

Or manually:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://<tailscale-ip>:5000` in your browser.

## REST API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/dashboard` | All jobs + last-4 runs + stats |
| GET | `/api/jobs` | List jobs |
| POST | `/api/jobs` | Create job `{name, schedule, description?, enabled?}` |
| PATCH | `/api/jobs/<id>` | Update job fields |
| DELETE | `/api/jobs/<id>` | Delete job |
| GET | `/api/jobs/<id>/runs` | Run history (up to 100) |
| POST | `/api/jobs/<id>/trigger` | Manually start a run (creates `running` record) |
| PATCH | `/api/runs/<id>` | Complete a run `{status, output?, error?, duration_ms?}` |

## Integrating your real jobs

From your scheduler (cron, APScheduler, Celery, etc.) wrap each job like this:

```python
import requests, time

BASE = "http://localhost:5000"

def run_my_job():
    r = requests.post(f"{BASE}/api/jobs/1/trigger")
    run_id = r.json()["id"]
    t0 = time.time()
    try:
        output = do_actual_work()
        requests.patch(f"{BASE}/api/runs/{run_id}", json={
            "status": "success",
            "output": output,
            "duration_ms": int((time.time() - t0) * 1000),
        })
    except Exception as e:
        requests.patch(f"{BASE}/api/runs/{run_id}", json={
            "status": "failed",
            "error": str(e),
            "duration_ms": int((time.time() - t0) * 1000),
        })
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `5000` | Listen port |
| `DATABASE_URL` | `sqlite:///jobs.db` | SQLAlchemy DB URI |
| `FLASK_DEBUG` | `0` | Set to `1` for debug mode |
