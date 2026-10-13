#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run.sh — start the Dev Agent Group backend (single-run reliable startup)
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 1. Virtual-environment setup ──────────────────────────────────────────
if [[ ! -f .venv/bin/activate ]]; then
  echo "[run.sh] No .venv found — creating virtual environment..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# ── 2. Install / sync dependencies ────────────────────────────────────────
if [[ -f requirements.txt ]]; then
  echo "[run.sh] Installing dependencies from requirements.txt..."
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
fi

# ── 3. Ensure .env exists (copy from example if missing) ──────────────────
if [[ ! -f .env && -f .env.example ]]; then
  echo "[run.sh] .env not found — copying from .env.example (please fill in your keys)"
  cp .env.example .env
fi

# ── 4. Ensure data directory exists (avoids first-run DB init failure) ────
mkdir -p data

# ── 5. Export venv paths explicitly so uvicorn --reload workers inherit them
export VIRTUAL_ENV
export PATH="$VIRTUAL_ENV/bin:$PATH"

# ── 6. Launch uvicorn (exec replaces shell; workers inherit the env above) ─
echo "[run.sh] Starting uvicorn on http://127.0.0.1:8000 ..."
exec uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
