#!/usr/bin/env bash
# One-time setup on macOS / Linux. Needs Python 3.11+.
set -e
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
python manage.py init-db
echo "Setup complete. Run ./run.sh to start."
