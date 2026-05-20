#!/usr/bin/env bash
# Production-grade installer for business-bot
# Target: Ubuntu 22/24, Debian 12, and Debian-family fallback

set -Eeuo pipefail

SCRIPT_NAME="$(basename "$0")"
PROJECT_NAME="business-bot"
PROJECT_DIR="${PROJECT_DIR:-$PWD}"
DEFAULT_REPO_URL="https://github.com/rezajavadi995/business-bot.git"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"
REQUIREMENTS_FILE="${REQUIREMENTS_FILE:-$PROJECT_DIR/requirements.txt}"
APT_FRONTEND="noninteractive"
MIN_DISK_MB=512

# ---------- Colors ----------
if [[ -t 1 ]]; then
  C_RESET='\033[0m'
  C_BOLD='\033[1m'
  C_BLUE='\033[34m'
  C_YELLOW='\033[33m'
  C_RED='\033[31m'
  C_GREEN='\033[32m'
else
  C_RESET=''
  C_BOLD=''
  C_BLUE=''
  C_YELLOW=''
  C_RED=''
  C_GREEN=''
fi

# ---------- Logging helpers ----------
log() {
  printf "%b[INFO]%b %s\n" "$C_BLUE" "$C_RESET" "$*"
}

warn() {
  printf "%b[WARN]%b %s\n" "$C_YELLOW" "$C_RESET" "$*"
}

success() {
  printf "%b[SUCCESS]%b %s\n" "$C_GREEN" "$C_RESET" "$*"
}

error_exit() {
  local message="$1"
  local code="${2:-1}"
  printf "%b[ERROR]%b %s\n" "$C_RED" "$C_RESET" "$message" >&2
  exit "$code"
}

on_error() {
  local exit_code=$?
  local line_no=${1:-unknown}
  local cmd="${BASH_COMMAND:-unknown}"
  printf "%b[ERROR]%b Installer failed at line %s (exit: %s).\n" "$C_RED" "$C_RESET" "$line_no" "$exit_code" >&2
  printf "%b[ERROR]%b Command: %s\n" "$C_RED" "$C_RESET" "$cmd" >&2
  printf "%b[ERROR]%b احتمالا یکی از پیش‌نیازها، شبکه، یا وضعیت apt/dpkg مشکل دارد.\n" "$C_RED" "$C_RESET" >&2
  exit "$exit_code"
}
trap 'on_error $LINENO' ERR

# ---------- Core helpers ----------
require_bash() {
  [[ -n "${BASH_VERSION:-}" ]] || error_exit "This installer must be run with bash. Example: bash install.sh"
}

require_root() {
  if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    error_exit "Root privileges are required. Run: sudo bash install.sh"
  fi
}

retry_command() {
  local max_attempts="$1"
  local sleep_seconds="$2"
  shift 2

  local attempt=1
  until "$@"; do
    local exit_code=$?
    if (( attempt >= max_attempts )); then
      return "$exit_code"
    fi
    warn "Command failed (attempt ${attempt}/${max_attempts}): $*"
    warn "Retrying in ${sleep_seconds}s..."
    sleep "$sleep_seconds"
    attempt=$((attempt + 1))
  done
}

wait_for_apt_locks() {
  local timeout=300
  local elapsed=0
  local step=5

  while fuser /var/lib/dpkg/lock >/dev/null 2>&1 \
     || fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 \
     || fuser /var/lib/apt/lists/lock >/dev/null 2>&1 \
     || fuser /var/cache/apt/archives/lock >/dev/null 2>&1; do
    if (( elapsed >= timeout )); then
      error_exit "Timeout waiting for apt/dpkg locks after ${timeout}s."
    fi
    warn "apt/dpkg lock detected. waiting ${step}s..."
    sleep "$step"
    elapsed=$((elapsed + step))
  done
}

repair_apt_state() {
  log "Repairing apt/dpkg state if needed..."
  wait_for_apt_locks
  dpkg --configure -a
  retry_command 3 5 apt-get -y -o Dpkg::Use-Pty=0 -f install
  success "apt/dpkg state repair completed."
}

apt_update() {
  wait_for_apt_locks
  retry_command 5 5 apt-get update -y -o Acquire::Retries=3
}

install_packages() {
  local packages=("$@")
  wait_for_apt_locks
  retry_command 3 5 apt-get install -y -o Dpkg::Use-Pty=0 "${packages[@]}"
}

check_internet() {
  log "Checking internet connectivity..."
  retry_command 3 3 curl -fsSLI --connect-timeout 8 --max-time 20 https://pypi.org >/dev/null
  success "Internet connectivity OK."
}

check_disk_space() {
  local avail_kb
  avail_kb="$(df -Pk "$PROJECT_DIR" | awk 'NR==2 {print $4}')"
  [[ -n "$avail_kb" ]] || error_exit "Could not determine disk space for $PROJECT_DIR"

  local avail_mb=$((avail_kb / 1024))
  if (( avail_mb < MIN_DISK_MB )); then
    error_exit "Low disk space: ${avail_mb}MB available, ${MIN_DISK_MB}MB required minimum."
  fi
  success "Disk space OK (${avail_mb}MB available)."
}

validate_repo_files() {
  log "Validating repository files..."
  if [[ ! -f "$REQUIREMENTS_FILE" || ! -f "$PROJECT_DIR/bot.py" ]]; then
    warn "Repository looks incomplete at: $PROJECT_DIR"

    local repo_url="${REPO_URL:-$DEFAULT_REPO_URL}"
    local target_dir="$PROJECT_DIR"

    if [[ "$PROJECT_DIR" == "/root" ]]; then
      target_dir="/opt/business-bot"
      log "Detected one-liner/root mode. Using install path: $target_dir"
    fi

    warn "Attempting automatic repo recreation from: $repo_url"
    local tmp_clone
    tmp_clone="$(mktemp -d)"
    retry_command 3 5 git clone --depth 1 "$repo_url" "$tmp_clone"

    [[ -f "$tmp_clone/bot.py" && -f "$tmp_clone/requirements.txt" ]] || error_exit "Cloned repository is incomplete."

    if [[ "$target_dir" != "$PROJECT_DIR" ]]; then
      PROJECT_DIR="$target_dir"
      VENV_DIR="$PROJECT_DIR/.venv"
      ENV_FILE="$PROJECT_DIR/.env"
      REQUIREMENTS_FILE="$PROJECT_DIR/requirements.txt"
    fi

    rm -rf "$PROJECT_DIR"
    mkdir -p "$PROJECT_DIR"
    cp -a "$tmp_clone/." "$PROJECT_DIR/"
    rm -rf "$tmp_clone"
    success "Repository recreated successfully in $PROJECT_DIR."
  fi

  [[ -s "$REQUIREMENTS_FILE" ]] || error_exit "requirements.txt exists but is empty."
  success "Repository validation passed."
}

resolve_os() {
  [[ -r /etc/os-release ]] || error_exit "Cannot read /etc/os-release"
  # shellcheck disable=SC1091
  source /etc/os-release

  OS_ID="${ID:-unknown}"
  OS_VERSION_ID="${VERSION_ID:-unknown}"
  OS_PRETTY="${PRETTY_NAME:-unknown}"

  case "$OS_ID:$OS_VERSION_ID" in
    ubuntu:22.04)
      OS_FLAVOR="ubuntu22"
      ;;
    ubuntu:24.04)
      OS_FLAVOR="ubuntu24"
      ;;
    debian:12)
      OS_FLAVOR="debian12"
      ;;
    ubuntu:*|debian:*)
      OS_FLAVOR="debian_family"
      ;;
    *)
      error_exit "Unsupported OS: $OS_PRETTY (expected Ubuntu/Debian family)."
      ;;
  esac

  ARCH="$(dpkg --print-architecture)"
  success "Detected OS: $OS_PRETTY | Flavor: $OS_FLAVOR | Arch: $ARCH"
}

python_minor_version() {
  python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
}

install_system_dependencies() {
  log "Installing base dependencies..."
  export DEBIAN_FRONTEND="$APT_FRONTEND"

  repair_apt_state
  apt_update

  install_packages ca-certificates curl git lsb-release

  # Install core python stack (safe/idempotent for minimal systems)
  install_packages python3 python3-full python3-pip python3-venv

  local pyver
  pyver="$(python_minor_version)"
  log "Detected python version: $pyver"

  # Handle Ubuntu 24 / Debian edge cases where ensurepip lives in versioned venv package
  if [[ "$pyver" == 3.* ]]; then
    install_packages "python${pyver}-venv"
  fi

  # Validate python3 and pip3 existence
  command -v python3 >/dev/null 2>&1 || error_exit "python3 not found after installation."
  command -v pip3 >/dev/null 2>&1 || error_exit "pip3 not found after installation."

  # Validate ensurepip
  if ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
    error_exit "ensurepip module unavailable. Verify python3-full and python3.X-venv installation."
  fi

  success "System dependencies installed successfully."
}

create_venv() {
  log "Creating virtual environment at: $VENV_DIR"

  if [[ -d "$VENV_DIR" ]]; then
    if [[ -x "$VENV_DIR/bin/python" && -f "$VENV_DIR/bin/activate" ]]; then
      log "Existing venv detected and appears valid. Reusing it."
      return 0
    fi
    warn "Broken/partial venv detected. Removing and recreating..."
    rm -rf "$VENV_DIR"
  fi

  retry_command 2 3 python3 -m venv "$VENV_DIR"

  [[ -f "$VENV_DIR/bin/activate" ]] || error_exit "Venv creation failed: activate script missing at $VENV_DIR/bin/activate"
  [[ -x "$VENV_DIR/bin/python" ]] || error_exit "Venv creation failed: python missing in venv"

  # Validate ensurepip inside the venv context
  "$VENV_DIR/bin/python" -c 'import ensurepip' >/dev/null 2>&1 || error_exit "ensurepip unavailable inside venv"
  success "Virtual environment is ready."
}

activate_venv() {
  [[ -f "$VENV_DIR/bin/activate" ]] || error_exit "Cannot activate venv: $VENV_DIR/bin/activate not found"

  # shellcheck disable=SC1090
  source "$VENV_DIR/bin/activate"

  command -v python >/dev/null 2>&1 || error_exit "python command unavailable after venv activation"
  command -v pip >/dev/null 2>&1 || error_exit "pip command unavailable after venv activation"

  success "Virtual environment activated."
}

validate_text_file_not_html() {
  local file_path="$1"
  local file_name
  file_name="$(basename "$file_path")"

  [[ -s "$file_path" ]] || error_exit "$file_name is missing or empty."

  if head -n 5 "$file_path" | grep -Eqi '<!doctype html|<html|<head|<body'; then
    error_exit "$file_name appears to be HTML (likely bad download/auth issue). Aborting."
  fi
}

install_python_dependencies() {
  log "Installing Python dependencies from requirements..."

  validate_text_file_not_html "$REQUIREMENTS_FILE"

  python -m pip install --upgrade pip setuptools wheel
  python -m pip --version >/dev/null 2>&1 || error_exit "pip inside venv is not functional"

  retry_command 3 4 python -m pip install -r "$REQUIREMENTS_FILE"
  success "Python dependencies installed successfully."
}

ensure_env_file() {
  log "Ensuring .env exists..."

  if [[ ! -f "$ENV_FILE" ]]; then
    cat > "$ENV_FILE" <<'EOF'
BOT_TOKEN=
ADMIN_ID=
EOF
    success "Created .env template at $ENV_FILE"
  else
    log ".env already exists. Keeping existing values."
  fi

  grep -q '^BOT_TOKEN=' "$ENV_FILE" || error_exit "BOT_TOKEN key not found in .env"
  grep -q '^ADMIN_ID=' "$ENV_FILE" || error_exit "ADMIN_ID key not found in .env"

  success ".env validation passed."
}

print_summary() {
  local pyv
  pyv="$(python3 --version 2>/dev/null || true)"

  printf "\n%b========== INSTALL SUMMARY ==========%b\n" "$C_BOLD" "$C_RESET"
  printf "OS           : %s\n" "${OS_PRETTY:-unknown}"
  printf "Architecture : %s\n" "${ARCH:-unknown}"
  printf "Python       : %s\n" "${pyv:-unknown}"
  printf "Project path : %s\n" "$PROJECT_DIR"
  printf "Venv path    : %s\n" "$VENV_DIR"
  printf "Next steps   :\n"
  printf "  1) source '%s/bin/activate'\n" "$VENV_DIR"
  printf "  2) Edit '%s' and set BOT_TOKEN + ADMIN_ID\n" "$ENV_FILE"
  printf "  3) python bot.py\n"
  printf "%b=====================================%b\n\n" "$C_BOLD" "$C_RESET"
}

main() {
  require_bash
  require_root

  log "Starting ${PROJECT_NAME} production installer..."
  log "Working directory: $PROJECT_DIR"

  [[ -d "$PROJECT_DIR" ]] || error_exit "Project directory does not exist: $PROJECT_DIR"

  resolve_os
  check_disk_space
  check_internet
  validate_repo_files
  install_system_dependencies
  create_venv
  activate_venv
  install_python_dependencies
  ensure_env_file

  success "Installation completed successfully."
  print_summary
}

main "$@"
