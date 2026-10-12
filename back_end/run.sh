#!/usr/bin/env bash
cd "$(dirname "$0")"
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
exec uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
