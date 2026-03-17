#!/usr/bin/env bash
# Quick-start script for Claude Cowork Dashboard
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Create venv if missing
if [ ! -d ".venv" ]; then
  echo "→ Creating virtual environment…"
  python3 -m venv .venv
fi

source .venv/bin/activate

# Install / upgrade deps
echo "→ Installing dependencies…"
pip install --quiet -r requirements.txt

# Start
PORT="${PORT:-5000}"
echo "→ Starting dashboard on http://0.0.0.0:${PORT}"
python app.py
