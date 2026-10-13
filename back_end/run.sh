#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run.sh — start the Dev Agent Group backend (single-run reliable startup)
#
# Why NOT set -euo pipefail here:
#   pip / venv steps are best-effort; a non-zero exit from pip (e.g. "already
#   up to date") must not abort the whole script.  Each critical step checks
#   its own exit code explicitly instead.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 1. Resolve python3 ────────────────────────────────────────────────────
PYTHON=$(command -v python3 || command -v python || true)
if [[ -z "$PYTHON" ]]; then
  echo "[run.sh] ERROR: python3 not found. Install Python 3.9+ and retry."
  exit 1
fi

# ── 2. Virtual-environment setup ──────────────────────────────────────────
if [[ ! -f .venv/bin/activate ]]; then
  echo "[run.sh] No .venv found — creating virtual environment..."
  "$PYTHON" -m venv .venv
  if [[ $? -ne 0 ]]; then
    echo "[run.sh] ERROR: Failed to create .venv."
    echo "         On Debian/Ubuntu try: sudo apt install python3-venv"
    exit 1
  fi
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# ── 3. Install / sync dependencies ────────────────────────────────────────
if [[ -f requirements.txt ]]; then
  echo "[run.sh] Installing/verifying dependencies..."
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
  if [[ $? -ne 0 ]]; then
    echo "[run.sh] ERROR: pip install failed. Check requirements.txt and network."
    exit 1
  fi
fi

# ── 4. Ensure .env exists (copy from example if missing) ──────────────────
if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    cp .env.example .env
    echo "[run.sh] .env created from .env.example — please set DEEPSEEK_API_KEY in back_end/.env"
  else
    echo "[run.sh] WARNING: No .env file found. Create back_end/.env with DEEPSEEK_API_KEY=<your key>"
  fi
fi

# ── 5. Ensure data directory exists (avoids first-run DB init failure) ────
mkdir -p data

# ── 6. Check port 8000 is free ────────────────────────────────────────────
if command -v lsof &>/dev/null; then
  if lsof -ti tcp:8000 &>/dev/null; then
    echo "[run.sh] WARNING: Port 8000 is already in use."
    echo "         Kill the existing process first:  kill \$(lsof -ti tcp:8000)"
    echo "         Or change the port below and in front_end/app.js fetch calls."
  fi
fi

# ── 7. Export venv paths so uvicorn --reload workers inherit them ─────────
export VIRTUAL_ENV
export PATH="$VIRTUAL_ENV/bin:$PATH"

# ── 8. Launch ─────────────────────────────────────────────────────────────
echo ""
echo "[run.sh] ✓ Starting server..."
echo "[run.sh] ✓ Open http://127.0.0.1:8000 in your browser"
echo "[run.sh]   (If you see 'failed to fetch', check the terminal for startup errors)"
echo ""
exec uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
