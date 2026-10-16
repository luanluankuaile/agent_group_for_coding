#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run.sh — start the Dev Agent Group backend (single-run reliable startup)
#
# Design notes:
#   - No set -euo pipefail: pip steps are best-effort; each critical step
#     checks its own exit code and prints a clear error message.
#   - VENV_DIR is set explicitly BEFORE source so that the export below
#     is never empty even on first run.
#   - uvicorn is called directly via its absolute path (not via PATH lookup)
#     so --reload worker forks always find the right interpreter.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 1. Resolve python3 ────────────────────────────────────────────────────
PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)
if [[ -z "$PYTHON" ]]; then
  echo "[run.sh] ERROR: python3 not found. Install Python 3.9+ and retry."
  exit 1
fi
echo "[run.sh] Using Python: $($PYTHON --version 2>&1)  ($PYTHON)"

# ── 2. Virtual-environment setup ──────────────────────────────────────────
VENV_DIR="$SCRIPT_DIR/.venv"

if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
  echo "[run.sh] No .venv found — creating virtual environment..."
  "$PYTHON" -m venv "$VENV_DIR"
  if [[ $? -ne 0 ]]; then
    echo "[run.sh] ERROR: Failed to create .venv."
    echo "         On Debian/Ubuntu try:  sudo apt install python3-venv"
    exit 1
  fi
  echo "[run.sh] Virtual environment created at $VENV_DIR"
fi

# Activate (sets VIRTUAL_ENV, prepends bin to PATH inside this shell)
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Export explicitly so child processes (uvicorn --reload workers) inherit them
export VIRTUAL_ENV="$VENV_DIR"
export PATH="$VENV_DIR/bin:$PATH"

UVICORN="$VENV_DIR/bin/uvicorn"

# ── 3. Install / sync dependencies ────────────────────────────────────────
if [[ -f requirements.txt ]]; then
  echo "[run.sh] Installing/verifying dependencies (this is fast when nothing changed)..."
  "$VENV_DIR/bin/pip" install --quiet --upgrade pip
  "$VENV_DIR/bin/pip" install --quiet -r requirements.txt
  if [[ $? -ne 0 ]]; then
    echo "[run.sh] ERROR: pip install failed. Check requirements.txt and your network."
    exit 1
  fi
  echo "[run.sh] Dependencies OK."
fi

# Confirm uvicorn is present after install
if [[ ! -f "$UVICORN" ]]; then
  echo "[run.sh] ERROR: uvicorn not found at $UVICORN"
  echo "         Make sure 'uvicorn' is listed in requirements.txt"
  exit 1
fi

# ── 4. Ensure .env exists ─────────────────────────────────────────────────
if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    cp .env.example .env
    echo "[run.sh] .env created from .env.example"
    echo "         ⚠  Set DEEPSEEK_API_KEY in back_end/.env before using the app."
  else
    echo "[run.sh] WARNING: No .env file found."
    echo "         Create back_end/.env containing:  DEEPSEEK_API_KEY=<your-key>"
  fi
fi

# ── 5. Ensure data directory exists ───────────────────────────────────────
mkdir -p data

# ── 6. Check port 8000 is free ────────────────────────────────────────────
if command -v lsof &>/dev/null; then
  if lsof -ti tcp:8000 &>/dev/null; then
    echo ""
    echo "[run.sh] ⚠  WARNING: Port 8000 is already in use."
    echo "         Kill the existing process first:"
    echo "           kill \$(lsof -ti tcp:8000)"
    echo "         Then re-run this script."
    echo ""
  fi
fi

# ── 7. Smoke-test: can Python import the app? ─────────────────────────────
echo "[run.sh] Checking app imports..."
"$VENV_DIR/bin/python" -c "from app.main import app" 2>&1
if [[ $? -ne 0 ]]; then
  echo ""
  echo "[run.sh] ERROR: App failed to import. Fix the errors above, then retry."
  echo "         Common causes:"
  echo "           - Missing package in requirements.txt"
  echo "           - Syntax error in a .py file"
  echo "           - Agent .md file has invalid YAML frontmatter"
  echo "           - default_promt/ directory missing or empty"
  exit 1
fi
echo "[run.sh] Import check passed."

# ── 8. Launch ─────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " ✓  Starting Dev Agent Group"
echo " ✓  Open in browser:  http://127.0.0.1:8000"
echo " ✓  API health check: http://127.0.0.1:8000/api/health"
echo "    Press Ctrl+C to stop."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

exec "$UVICORN" app.main:app --reload --host 127.0.0.1 --port 8000
