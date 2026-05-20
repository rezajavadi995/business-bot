#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$PWD}"
VENV_DIR="$PROJECT_DIR/.venv"
ENV_FILE="$PROJECT_DIR/.env"

log() { echo "[installer] $*"; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing command: $1"; exit 1; }
}

log "Checking prerequisites"
require_cmd python3
require_cmd pip3

if [ ! -d "$VENV_DIR" ]; then
  log "Creating virtualenv"
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
log "Upgrading pip"
pip install --upgrade pip

log "Installing requirements"
pip install -r "$PROJECT_DIR/requirements.txt"

if [ ! -f "$ENV_FILE" ]; then
  log "Creating .env template"
  cat > "$ENV_FILE" <<EOF
BOT_TOKEN=
ADMIN_ID=
EOF
fi

log "Validating environment variables"
if ! grep -q '^BOT_TOKEN=' "$ENV_FILE"; then
  echo "BOT_TOKEN not found in .env"
  exit 1
fi

log "Install complete"
log "Run: source .venv/bin/activate && python bot.py"
