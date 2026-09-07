#!/bin/bash


VADER_RED="\033[38;5;196m"
VADER_RED_DARK="\033[38;5;88m"
VADER_RED_LIGHT="\033[38;5;203m"
VADER_GRAY="\033[38;5;244m"
VADER_GRAY_DARK="\033[38;5;238m"
VADER_WHITE="\033[38;5;255m"
VADER_WHITE_DIM="\033[38;5;250m"
VADER_RESET="\033[0m"
VADER_BOLD="\033[1m"

vader_header() { echo -e "${VADER_RED}${VADER_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${VADER_RESET}"; }
vader_separator() { echo -e "${VADER_GRAY_DARK}─────────────────────────────────────────────────────────────────${VADER_RESET}"; }
vader_title() { echo -e "${VADER_WHITE}${VADER_BOLD}$1${VADER_RESET}"; }
vader_info() { echo -e "  ${VADER_GRAY}·${VADER_RESET} ${VADER_WHITE_DIM}$1${VADER_RESET}"; }
vader_success() { echo -e "  ${VADER_RED}✔${VADER_RESET} ${VADER_WHITE}$1${VADER_RESET}"; }
vader_warn() { echo -e "  ${VADER_RED_LIGHT}⚠${VADER_RESET} ${VADER_RED_LIGHT}$1${VADER_RESET}"; }
vader_error() { echo -e "  ${VADER_RED_DARK}✖${VADER_RESET} ${VADER_RED}$1${VADER_RESET}"; }
vader_step() { echo -e "\n${VADER_RED}${VADER_BOLD}► [$1/${TOTAL_STEPS}]${VADER_RESET} ${VADER_WHITE}${VADER_BOLD}$2${VADER_RESET}"; }

START_TIME=$(date +%s)
TOTAL_STEPS=11

FAST_START=0
TEST_MODE=0
VOICE_CHECK=1
VOICE_AVAILABLE=1
PARALLEL_CHECKS=0
FORCE_PORTS=1
BUILD_CHECK=1
AUTO_BUILD_FRONTEND=1
LAUNCH_BROWSER=0
if [ "${GUAARDVARK_APP_MODE}" = "true" ] || [ "${GUAARDVARK_APP_MODE}" = "1" ]; then
  LAUNCH_BROWSER=1
fi
REQUESTED_PROFILE=""
EXPECT_PROFILE=0
VOICE_FLAG_GIVEN=0
EXTERNAL_OLLAMA_FLAG=0
for arg in "$@"; do
  if [ "$EXPECT_PROFILE" = 1 ]; then
    REQUESTED_PROFILE="$arg"; EXPECT_PROFILE=0; continue
  fi
  case "$arg" in
    --help|-h)
      echo "Guaardvark Start Script"
      echo ""
      echo "Usage: ./start.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --fast              Skip dependency checks and builds"
      echo "  --test              Run with comprehensive health diagnostics"
      echo "  --no-voice          Skip voice API health check"
      echo "  --parallel          Run checks in parallel"
      echo "  --force-ports       Force port allocation (default)"
      echo "  --no-force-ports    Do not force port allocation"
      echo "  --no-build-check    Skip build verification"
      echo "  --build-frontend   Enable automatic frontend rebuild"
      echo "  --no-auto-build    Disable automatic frontend rebuild"
      echo "  --skip-migrations  Skip database migration checks"
      echo "  --skip-postgres    Skip PostgreSQL setup (for external DB users)"
      echo "  --external-ollama  Use an Ollama you run yourself: never start it, never stop it"
      echo "                     (persisted as GUAARDVARK_OLLAMA_EXTERNAL=1 in .env)"
      echo "  --app-mode         Launch browser on startup"
      echo "  --no-browser       Do not launch browser"
      echo "  --discord          Also start the Discord bot plugin"
      echo "  --plugins          Start all enabled plugins after backend is up"
      echo "  --profile NAME     Select a profile (workstation, creator, or an extension's);"
      echo "                     written to .env so it persists. See backend/profiles/README.md"
      echo "  --help, -h         Show this help"
      exit 0
      ;;
    --fast) FAST_START=1 ;;
    --external-ollama) EXTERNAL_OLLAMA_FLAG=1 ;;
    --test) TEST_MODE=1 ;;
    --no-voice) VOICE_CHECK=0; VOICE_FLAG_GIVEN=1 ;;
    --profile) EXPECT_PROFILE=1 ;;
    --profile=*) REQUESTED_PROFILE="${arg#--profile=}" ;;
    --parallel) PARALLEL_CHECKS=1 ;;
    --force-ports) FORCE_PORTS=1 ;;
    --no-force-ports) FORCE_PORTS=0 ;;
    --no-build-check) BUILD_CHECK=0 ;;
    --build-frontend) AUTO_BUILD_FRONTEND=1 ;;
    --no-auto-build) AUTO_BUILD_FRONTEND=0 ;;
    --skip-migrations) export GUAARDVARK_SKIP_MIGRATIONS=1 ;;
    --skip-postgres) export GUAARDVARK_SKIP_POSTGRES=1 ;;
    --app-mode) LAUNCH_BROWSER=1 ;;
    --no-browser) LAUNCH_BROWSER=0 ;;
    --discord) START_DISCORD=1 ;;
    --plugins) START_ALL_PLUGINS=1 ;;
  esac
done

if [ -n "$CI" ] || [ -n "$CODEX_ENV" ]; then
  vader_info "CI or Codex environment detected. Exiting start.sh."
  exit 0
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# ── Single-instance guard (must run BEFORE any installing layer, including the
# system-manager repair below) ───────────────────────────────────────────────
# Overlapping ./start.sh runs interleave pip/npm/ollama output in setup.log and
# race each other's installs; worse, concurrent multi-GB downloads (torch wheels
# + npm + ollama pulls) saturate the link until systemd-resolved DNS lookups
# time out (Observed on the 24.04 client box install: registry.ollama.ai i/o
# timeout on 127.0.0.53 while cudnn/nccl wheels pulled at 40MB/s).
# PID file + liveness check rather than flock: daemons we spawn would inherit
# a lock fd and block legitimate restarts after this script exits.
# stop.sh reaps this lock (including Ctrl+Z / STAT=T leftovers). A stopped or
# foreign pidfile is not a live overlap — take it over.
START_PID_FILE="$SCRIPT_DIR/.start_cache/start.sh.pid"
mkdir -p "$SCRIPT_DIR/.start_cache"
# shellcheck source=scripts/lib/start_lock.sh
. "$SCRIPT_DIR/scripts/lib/start_lock.sh"
. "$SCRIPT_DIR/scripts/lib/venv_pins.sh"
if [ -f "$START_PID_FILE" ]; then
    _prev_pid=$(cat "$START_PID_FILE" 2>/dev/null)
    if [ "$(start_lock_guard_decision "$_prev_pid")" = "live" ]; then
        vader_error "Another ./start.sh (PID $_prev_pid) is still running — refusing to overlap."
        vader_error "Concurrent runs corrupt installs and starve DNS while big downloads compete."
        vader_info "Wait for it to finish (watch: tail -f logs/setup.log), or stop everything first: ./stop.sh"
        exit 1
    fi
    if [ -n "$_prev_pid" ] && [ "$_prev_pid" != "$$" ] \
       && start_lock_is_our_script "$_prev_pid"; then
        # Abandoned (stopped/zombie) copy of this script — reap so the pid
        # cannot race us after we write the new pidfile.
        start_lock_kill_pid "$_prev_pid"
        vader_info "Reaped abandoned start.sh (PID $_prev_pid)."
    fi
fi
echo $$ > "$START_PID_FILE"
trap 'rm -f "$START_PID_FILE" 2>/dev/null' EXIT

# ── Platform detection (detect → route to a per-OS backend) ──────────────────
# Sets GUAARDVARK_OS/_ARCH/_ACCEL/_IS_WSL and sources the matching backend
# (scripts/platform/{linux,macos}.sh), which DEFINES platform_install_system_deps /
# platform_ensure_python / platform_gpu_setup / platform_service_start / ensure_node_npm.
# macOS and Linux both call platform_ensure_python before version gates (Linux also
# ensures Node/npm). Guarded so an older checkout without scripts/platform/ still boots.
if [ -f "$SCRIPT_DIR/scripts/platform/detect.sh" ]; then
    source "$SCRIPT_DIR/scripts/platform/detect.sh"
    detect_platform
    [ -f "$GUAARDVARK_PLATFORM_BACKEND" ] && source "$GUAARDVARK_PLATFORM_BACKEND"
fi

# is_macos - single source of truth for "are we on Darwin?". Prefers the
# detect.sh flag, falls back to `uname` so it still works on older checkouts
# that predate scripts/platform/. Used to gate Linux-only steps (inotify/sysctl,
# apt advice, Xvfb/X11 agent display) so they no-op cleanly on macOS (#41).
is_macos() {
    [ "${GUAARDVARK_OS:-}" = macos ] && return 0
    [ "$(uname -s 2>/dev/null)" = Darwin ] && return 0
    return 1
}

# Python dev headers BEFORE any pip-installing layer runs. The system-manager
# repair just below installs requirements on fresh/broken boxes; on Ubuntu 24.04
# the system python3.12 has no python3.12-dev, so evdev's sdist build aborts the
# whole pip run ('Python.h: No such file or directory') and nothing installs.
# Cheap no-op (one file test) when headers are already present.
if ! is_macos && declare -F platform_ensure_python_headers >/dev/null 2>&1; then
    platform_ensure_python_headers || true
fi

MANAGER_SCRIPT="$SCRIPT_DIR/scripts/system-manager/system-manager"
if [ -f "$MANAGER_SCRIPT" ]; then
    # Ensure ./manager symlink exists (may be missing after Code Release restore)
    if [ ! -L "$SCRIPT_DIR/manager" ]; then
        ln -sf "scripts/system-manager/system-manager" "$SCRIPT_DIR/manager"
    fi
    # Stronger guard: trigger repair on fresh install (no venv) *or* incomplete/broken venv.
    # The old "only if flask binary exists" check was a chicken-and-egg that skipped
    # the only code path capable of running `pip install -r requirements*` on first run.
    VENV_PY="$SCRIPT_DIR/backend/venv/bin/python"
    if [ -x "$VENV_PY" ] && "$VENV_PY" -c "import numpy, flask, celery" >/dev/null 2>&1; then
        if ! "$MANAGER_SCRIPT" check "$SCRIPT_DIR"; then
            vader_warn "Environment issues detected by System Manager."
            vader_info "Auto-repairing environment..."
            "$MANAGER_SCRIPT" repair "$SCRIPT_DIR" || vader_warn "Auto-repair had issues, continuing with startup..."
        fi
    else
        # No usable venv: step 5 (ensure_python_env) creates it with the Python
        # 3.12 that platform_ensure_python resolves below, then bootstraps every
        # requirements file. Running system-manager repair here first built the
        # venv from whatever `python3` is — 3.14 on Ubuntu 26.04 — and pip then
        # compiled numpy/pandas from source and failed, minutes before step 5
        # threw that venv away and started over (client box, 2026-08-31).
        vader_info "No usable Python venv yet — step 5 creates it with Python 3.12 and bootstraps dependencies."
    fi
fi

# Interpreter used for the version gate and venv creation. Override when the
# system `python3` isn't 3.12 (e.g. deadsnakes installs `python3.12` side-by-side
# without repointing the symlink): `PYTHON_CMD=python3.12 ./start.sh` (or set it in .env).
PYTHON_CMD="${PYTHON_CMD:-python3}"
NPM_CMD="npm"
OLLAMA_SERVICE_NAME="ollama"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
VENV_DIR="$BACKEND_DIR/venv"
CACHE_DIR="$SCRIPT_DIR/.start_cache"
mkdir -p "$CACHE_DIR"

LOGS_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOGS_DIR"
SETUP_LOG="$LOGS_DIR/setup.log"

if [ -n "$GUAARDVARK_ROOT" ] && [ "$GUAARDVARK_ROOT" != "$SCRIPT_DIR" ]; then
  vader_warn "Ignoring GUAARDVARK_ROOT override ('$GUAARDVARK_ROOT'); using script directory '$SCRIPT_DIR'."
fi
export GUAARDVARK_ROOT="$SCRIPT_DIR"

# Harden .env permissions — secrets should not be group/world-readable
for _envfile in "$SCRIPT_DIR/.env" "$SCRIPT_DIR/OLD.env"; do
  if [ -f "$_envfile" ]; then
    _perms=$(stat -c '%a' "$_envfile" 2>/dev/null || stat -f '%Lp' "$_envfile" 2>/dev/null)
    if [ "$_perms" != "600" ]; then
      vader_warn "$(basename $_envfile) has insecure permissions ($_perms), fixing to 600..."
      chmod 600 "$_envfile"
    fi
  fi
done

if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a
  . "$SCRIPT_DIR/.env"
  set +a
  export GUAARDVARK_ROOT="$SCRIPT_DIR"
fi

# ─── Profile ──────────────────────────────────────────────────────────────────
# `--profile NAME` is persisted to .env so the backend (which reads .env itself)
# and every later start agree. The profile then fills in defaults for env keys
# .env did not set — an explicit value always wins, and `workstation` sets nothing.
if [ -n "$REQUESTED_PROFILE" ]; then
  if ! printf '%s' "$REQUESTED_PROFILE" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$'; then
    echo "Invalid profile name: $REQUESTED_PROFILE" >&2
    exit 1
  fi
  touch "$SCRIPT_DIR/.env"
  if grep -q '^GUAARDVARK_PROFILE=' "$SCRIPT_DIR/.env"; then
    sed -i.bak "s/^GUAARDVARK_PROFILE=.*/GUAARDVARK_PROFILE=$REQUESTED_PROFILE/" "$SCRIPT_DIR/.env" && rm -f "$SCRIPT_DIR/.env.bak"
  else
    echo "GUAARDVARK_PROFILE=$REQUESTED_PROFILE" >> "$SCRIPT_DIR/.env"
  fi
  export GUAARDVARK_PROFILE="$REQUESTED_PROFILE"
fi
if [ "$EXTERNAL_OLLAMA_FLAG" = 1 ]; then
  touch "$SCRIPT_DIR/.env"
  if grep -q '^GUAARDVARK_OLLAMA_EXTERNAL=' "$SCRIPT_DIR/.env"; then
    sed -i.bak "s/^GUAARDVARK_OLLAMA_EXTERNAL=.*/GUAARDVARK_OLLAMA_EXTERNAL=1/" "$SCRIPT_DIR/.env" && rm -f "$SCRIPT_DIR/.env.bak"
  else
    echo "GUAARDVARK_OLLAMA_EXTERNAL=1" >> "$SCRIPT_DIR/.env"
  fi
  export GUAARDVARK_OLLAMA_EXTERNAL=1
fi
_PROFILE_EXPORTS="$("$PYTHON_CMD" "$SCRIPT_DIR/backend/profiles/__main__.py" export --shell 2>"$SCRIPT_DIR/.start_cache/profile.err" || true)"
if [ -n "$_PROFILE_EXPORTS" ]; then
  eval "$_PROFILE_EXPORTS"
fi
if [ -s "$SCRIPT_DIR/.start_cache/profile.err" ]; then
  while IFS= read -r _line; do echo "  ⚠ profile: ${_line#\# WARNING: }"; done < "$SCRIPT_DIR/.start_cache/profile.err"
fi
# startup skips the profile asks for, unless the flag was passed explicitly
if [ "$VOICE_FLAG_GIVEN" = 0 ] && [ "${GUAARDVARK_PROFILE_VOICE_CHECK:-1}" = "0" ]; then
  VOICE_CHECK=0
fi

# ─── Backend port ─────────────────────────────────────────────────────────────
# macOS reserves 5000 for the AirPlay Receiver (Monterey and later), so the default
# there is 5055. An explicit FLASK_PORT (environment or .env) always wins. The chosen
# default is written to .env so the backend, Vite and the CLI agree on every start.
if [ -z "${FLASK_PORT:-}" ] && is_macos; then
  FLASK_PORT=5055
  touch "$SCRIPT_DIR/.env"
  if ! grep -q '^FLASK_PORT=' "$SCRIPT_DIR/.env"; then
    echo "FLASK_PORT=$FLASK_PORT" >> "$SCRIPT_DIR/.env"
  fi
  export FLASK_PORT
fi

# Generate SECRET_KEY if not set — prevents "Using default SECRET_KEY" warning.
# Handles three cases: line missing, line present-but-empty, line present-with-value.
# The first two need regeneration; only the third is a no-op.
if [ -z "$SECRET_KEY" ]; then
  _generated_key=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null)
  if [ -n "$_generated_key" ]; then
    if grep -q '^SECRET_KEY=' "$SCRIPT_DIR/.env" 2>/dev/null; then
      # Line exists but is empty (e.g. stripped by code-release sanitizer or
      # a commented-out variant). Replace in place.
      sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$_generated_key|" "$SCRIPT_DIR/.env"
    else
      echo "SECRET_KEY=$_generated_key" >> "$SCRIPT_DIR/.env"
    fi
    export SECRET_KEY="$_generated_key"
  fi
fi

# Remove TMOUT auto-logout if present (causes terminal windows to close after idle)
if [ -f /etc/profile.d/timeout.sh ]; then
  if sudo -n rm -f /etc/profile.d/timeout.sh 2>/dev/null; then
    vader_success "Removed /etc/profile.d/timeout.sh (prevents terminal auto-close)"
  else
    vader_warn "Cannot remove /etc/profile.d/timeout.sh (needs sudo). Run: sudo rm -f /etc/profile.d/timeout.sh"
  fi
fi
# Unset TMOUT for this session in case it was already sourced
unset TMOUT 2>/dev/null || true

CURRENT_PWD="$(pwd)"
if [ "$CURRENT_PWD" != "$GUAARDVARK_ROOT" ]; then
  vader_warn "Running from '$CURRENT_PWD' but root is '$GUAARDVARK_ROOT'. cd into the target install to avoid cross-install confusion."
fi

GUAARDVARK_LOG_DIR="${GUAARDVARK_LOG_DIR:-logs}"
GUAARDVARK_OUTPUT_DIR="${GUAARDVARK_OUTPUT_DIR:-data/outputs}"
GUAARDVARK_STORAGE_DIR="${GUAARDVARK_STORAGE_DIR:-data}"
GUAARDVARK_UPLOAD_DIR="${GUAARDVARK_UPLOAD_DIR:-data/uploads}"
GUAARDVARK_CACHE_DIR="${GUAARDVARK_CACHE_DIR:-data/cache}"
if [[ "$GUAARDVARK_LOG_DIR" != /* ]]; then GUAARDVARK_LOG_DIR="$GUAARDVARK_ROOT/$GUAARDVARK_LOG_DIR"; fi
if [[ "$GUAARDVARK_OUTPUT_DIR" != /* ]]; then GUAARDVARK_OUTPUT_DIR="$GUAARDVARK_ROOT/$GUAARDVARK_OUTPUT_DIR"; fi
if [[ "$GUAARDVARK_STORAGE_DIR" != /* ]]; then GUAARDVARK_STORAGE_DIR="$GUAARDVARK_ROOT/$GUAARDVARK_STORAGE_DIR"; fi
if [[ "$GUAARDVARK_UPLOAD_DIR" != /* ]]; then GUAARDVARK_UPLOAD_DIR="$GUAARDVARK_ROOT/$GUAARDVARK_UPLOAD_DIR"; fi
if [[ "$GUAARDVARK_CACHE_DIR" != /* ]]; then GUAARDVARK_CACHE_DIR="$GUAARDVARK_ROOT/$GUAARDVARK_CACHE_DIR"; fi
export GUAARDVARK_LOG_DIR GUAARDVARK_OUTPUT_DIR GUAARDVARK_STORAGE_DIR GUAARDVARK_UPLOAD_DIR GUAARDVARK_CACHE_DIR

# HuggingFace transfer reliability: the Xet protocol (hf_xet) stalls near completion on
# some networks — it hung the FLUX.1-Kontext-dev pull (and left lock files on the Wan
# I2V quants). Force plain, resumable HTTPS downloads for the backend + Celery model
# downloaders (the "Install" buttons). Override with HF_HUB_DISABLE_XET=0 ./start.sh if
# Xet works on your network.
export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"

BACKEND_STARTUP_LOG_FILE="$LOGS_DIR/backend_startup.log"
FRONTEND_LOG_FILE="$LOGS_DIR/frontend.log"

FLASK_APP_TARGET="backend.app"
FLASK_PORT="${FLASK_PORT:-5000}"
VITE_PORT="${VITE_PORT:-5173}"
VITE_PROCESS_PATTERN="node.*vite"
# NOTE: the port is passed to the backend via the FLASK_PORT *env var*, not argv, so
# it never appears in /proc/PID/cmdline — a pattern with ".*$FLASK_PORT" can never match
# `pgrep -f`. Match on the module target only; port-ownership is resolved authoritatively
# by lsof/ss in kill_process() and repo_listener_pids().
FLASK_PROCESS_PATTERN="python.*backend[./]app|flask run"

# Portable process working-directory lookup. Linux exposes it at /proc/<pid>/cwd; macOS/BSD
# have no /proc, so fall back to lsof's cwd file descriptor. Returns the absolute cwd, or
# empty if it can't be determined. Every "is this process from THIS checkout?" guard funnels
# through here — without the lsof fallback, the cwd check silently returned empty on macOS and
# our own stale frontend/backend/celery were never reaped (issue #41: zombie vite on :4173).
_proc_cwd() {
    local pid="$1" cwd=""
    if [ -e "/proc/$pid/cwd" ]; then
        cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null)
    elif command -v lsof >/dev/null 2>&1; then
        cwd=$(lsof -a -d cwd -p "$pid" -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)
    fi
    printf '%s' "$cwd"
}

FLASK_DEBUG_FLAG=""
if [[ " $* " == *" --debug "* ]]; then
  FLASK_DEBUG_FLAG="--debug"
  vader_info "Flask debug mode requested."
fi

command_exists() { command -v "$1" >/dev/null 2>&1; }

# Portable `timeout`: base macOS ships no `timeout` (it's GNU coreutils). Without this,
# `timeout N ollama list` hits command-not-found, the `|| true` swallows it, and the
# caller sees an EMPTY result — which made the boot model-presence check report
# "No chat model found" and re-pull (a no-op) on every macOS start (#41). Prefer the real
# command, fall back to `gtimeout` (brew coreutils), else run with NO limit (drop the
# duration arg). No-op on Linux, where `timeout` already exists.
if ! command_exists timeout; then
    if command_exists gtimeout; then
        timeout() { gtimeout "$@"; }
    else
        timeout() { shift; "$@"; }
    fi
fi

# Resolve the EFFECTIVE enabled state for a plugin:
#   1. data/plugin_state.json's `user_enabled[<id>]` if the user has toggled it
#   2. plugin.json's `config.enabled` otherwise (manifest default)
#
# Echoes "True" or "False" — match the casing of Python's bool repr so the
# existing `[ "$x" = "True" ]` checks throughout this script keep working.
# Defaults to False on any read error (fail closed: don't start plugins
# whose state we can't determine).
#
# Defined up here near the other helpers because step-4 (Ollama) calls it
# WAY before the plugin loop later in the script — defining it down by the
# loop produced "command not found" on every boot.
plugin_effective_enabled() {
    local plugin_id="$1"
    local plugin_json="$2"
    python3 - "$plugin_id" "$plugin_json" "$SCRIPT_DIR/data/plugin_state.json" <<'PYEOF' 2>/dev/null || echo "False"
import json, os, sys
plugin_id, plugin_json, state_file = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(state_file) as f:
        prefs = (json.load(f) or {}).get("user_enabled", {})
    if plugin_id in prefs:
        print("True" if prefs[plugin_id] else "False")
        sys.exit(0)
except (OSError, ValueError):
    pass
# The active profile's plugin defaults (same string PluginMetadata.from_json_file reads).
for item in os.environ.get("GUAARDVARK_PROFILE_PLUGIN_DEFAULTS", "").split(","):
    if "=" in item and item.split("=", 1)[0].strip() == plugin_id:
        print("True" if item.split("=", 1)[1].strip().lower() in ("1", "true", "yes", "on") else "False")
        sys.exit(0)
try:
    with open(plugin_json) as f:
        cfg = (json.load(f) or {}).get("config", {})
    # Manifests store the fresh-install default under `default_enabled` (the
    # Python loader, PluginConfig.from_dict, reads the same key with an
    # `enabled` legacy fallback). Reading bare `enabled` here returned False for
    # every fresh clone — silently skipping Ollama startup + model bootstrap on
    # any box without a pre-existing ollama service (e.g. macOS). Keep shell and
    # Python in parity.
    print("True" if cfg.get("default_enabled", cfg.get("enabled", False)) else "False")
except (OSError, ValueError):
    print("False")
PYEOF
}

check_with_cache() {
  local cache_key="$1"
  local cache_file="$CACHE_DIR/$cache_key"
  local check_func="$2"
  
  if [ -f "$cache_file" ] && [ "$FAST_START" -eq 1 ]; then
    local cached_result=$(cat "$cache_file")
    if [ "$cached_result" = "0" ]; then
      return 0
    fi
  fi
  
  if $check_func; then
    echo "0" > "$cache_file"
    return 0
  else
    echo "1" > "$cache_file"
    return 1
  fi
}

check_python_version() {
    if ! command_exists "$PYTHON_CMD"; then
        vader_error "$PYTHON_CMD not found. Install via: apt-get install -y python3 python3-venv python3-dev python3-pip"
        return 1
    fi
    local ver
    ver=$("$PYTHON_CMD" --version 2>&1 | awk '{print $2}')
    local major=${ver%%.*}
    local minor=${ver#*.}
    minor=${minor%%.*}
    # Python 3.12 ONLY. pandas 2.2.2 and the face-restoration extra
    # (basicsr/gfpgan/realesrgan) have no wheels for 3.13/3.14 yet, so anything
    # newer sails into a marathon of build failures (issue #35). Fail fast with
    # the real reason instead. NOTE: the old logic was inverted — it returned
    # success for 3.13+ and fell through to a failure for 3.12, the one version
    # that works. Platform-aware install hint. The REQUIREMENT stays 3.12
    # everywhere — only the "how to get 3.12" guidance differs per OS/arch.
    # Defaults to the apt hint if detect_platform didn't run (older checkout).
    local py_hint
    case "${GUAARDVARK_OS:-linux}/${GUAARDVARK_ARCH:-x86_64}" in
        macos/*) py_hint="macOS: 'brew install python@3.12', then re-run with 'PYTHON_CMD=python3.12 ./start.sh'." ;;
        */arm64) py_hint="Raspberry Pi / ARM (no apt python3.12): 'uv python install 3.12' (or pyenv), then 'PYTHON_CMD=\$(uv python find 3.12) ./start.sh'." ;;
        *)       py_hint="On Ubuntu 26.04+ ./start.sh installs Python 3.12 automatically (deadsnakes or uv). Manual: 'sudo apt-get install -y python3.12 python3.12-venv python3.12-dev' or uv python install 3.12." ;;
    esac
    if [ "$major" -ne 3 ] || [ "$minor" -lt 12 ]; then
        vader_error "Python 3.12 is required (found $ver). $py_hint"
        return 1
    fi
    if [ "$minor" -ge 13 ]; then
        vader_error "Python $ver is not supported yet — the ML deps (pandas 2.2.2, basicsr/gfpgan) have no wheels for 3.13+. Use Python 3.12. $py_hint"
        return 1
    fi
    return 0
}

check_node_version() {
    if ! command_exists node; then
        vader_error "node not found. Install via: sudo apt-get install -y nodejs"
        return 1
    fi
    local ver
    ver=$(node --version | sed 's/v//')
    local major=${ver%%.*}
    if [ "$major" -lt 20 ]; then
        vader_error "Node.js >=20 required. Install via: sudo apt-get install -y nodejs"
        return 1
    fi
}

check_npm() {
    if ! command_exists npm; then
        vader_error "npm not found. Install via: sudo apt-get install -y npm"
        return 1
    fi
}

detect_browser() {
    # macOS: browsers are .app bundles launched via `open -a`, rarely on PATH.
    # Probe the standard install locations; report the bundle name (#41 — the
    # PATH-only check falsely claimed "no browser" on a Mac with Chrome/Safari).
    if is_macos; then
        local app
        for app in "Firefox" "Google Chrome" "Brave Browser" "Microsoft Edge" "Safari"; do
            if [ -d "/Applications/$app.app" ] || [ -d "$HOME/Applications/$app.app" ]; then
                echo "$app"
                return 0
            fi
        done
        # Safari ships with macOS — guaranteed fallback.
        echo "Safari"
        return 0
    fi
    if command_exists firefox; then
        echo "firefox"
        return 0
    else
        return 1
    fi
}

check_gpu_optimizations() {
    if ! command_exists nvidia-smi; then
        return 0
    fi

    vader_info "Checking GPU hardware optimizations..."
    
    local pm_status=$(nvidia-smi --query-gpu=persistence_mode --format=csv,noheader,nounits 2>/dev/null)
    if [[ "$pm_status" == "Enabled" ]]; then
        vader_success "GPU Persistence Mode: Enabled"
    else
        vader_warn "GPU Persistence Mode: $pm_status (Recommended: Enabled)"
        vader_info "  Fix: sudo nvidia-smi -pm 1"
    fi

    local pl_info=$(nvidia-smi --query-gpu=power.limit,power.default_limit,power.max_limit --format=csv,noheader,nounits 2>/dev/null)
    if [[ -n "$pl_info" ]]; then
        local current_pl=$(echo $pl_info | cut -d',' -f1 | xargs)
        local default_pl=$(echo $pl_info | cut -d',' -f2 | xargs)
        local max_pl=$(echo $pl_info | cut -d',' -f3 | xargs)

        # Skip power limit check if values are missing or non-numeric (e.g. [N/A])
        if ! echo "$current_pl" | grep -qE '^[0-9.]+$' || \
           ! echo "$default_pl" | grep -qE '^[0-9.]+$' || \
           ! echo "$max_pl" | grep -qE '^[0-9.]+$'; then
            vader_info "GPU Power Limit: N/A (not reported by this GPU)"
        elif ! command_exists bc; then
            vader_info "GPU Power Limit: ${current_pl}W (install 'bc' for detailed check)"
        elif (( $(echo "$current_pl >= $max_pl" | bc -l) )); then
            vader_success "GPU Power Limit: ${current_pl}W (Max Performance)"
        elif (( $(echo "$current_pl >= $default_pl" | bc -l) )); then
            vader_success "GPU Power Limit: ${current_pl}W (Default: ${default_pl}W)"
        else
            vader_warn "GPU Power Limit: ${current_pl}W (Below default: ${default_pl}W)"
            vader_info "  Fix: sudo nvidia-smi -pl ${default_pl}"
        fi
    fi
}

launch_browser_app() {
    local url="$1"
    local browser_cmd

    browser_cmd=$(detect_browser)
    if [ $? -ne 0 ]; then
        if is_macos; then
            vader_warn "No browser found. Open manually: $url"
        else
            vader_warn "Firefox not found. Cannot launch in app mode."
            vader_info "Install Firefox: sudo apt-get install -y firefox"
        fi
        return 1
    fi

    # macOS: hand the URL to the detected .app via `open -a` (no systemd/setsid).
    if is_macos; then
        vader_info "Launching $browser_cmd: $url"
        open -a "$browser_cmd" "$url" >/dev/null 2>&1 \
            && { vader_success "$browser_cmd launched"; return 0; } \
            || { vader_warn "Could not launch $browser_cmd. Open manually: $url"; return 1; }
    fi

    vader_info "Launching Firefox in new window: $browser_cmd"

    if [[ "$browser_cmd" == "firefox" ]]; then
        # Use systemd-run to isolate browser in its own cgroup.
        # Without this, Firefox inherits the terminal's cgroup, and if the
        # cgroup is killed (OOM, resource limits), the terminal server dies too.
        if command -v systemd-run >/dev/null 2>&1; then
            systemd-run --user --scope -q "$browser_cmd" --new-window "$url" >/dev/null 2>&1 &
        else
            setsid "$browser_cmd" --new-window "$url" >/dev/null 2>&1 &
        fi
    fi
    
    sleep 1
    
    if [ $? -eq 0 ]; then
        vader_success "Firefox launched in new window"
        return 0
    else
        vader_warn "Failed to launch Firefox in new window"
        return 1
    fi
}

kill_process() {
    local port=$1
    local name=$2
    local pgrep_pattern=$3
    local pid_list
    local confirmed_stopped=0
    local max_wait=5

    if command_exists ss && ! ss -tlpn 2>/dev/null | grep -q ":$port\b"; then
        return 0
    fi

    if command_exists lsof; then
        local port_pids=$(lsof -i TCP:"$port" -sTCP:LISTEN -t 2>/dev/null)
        if [ -n "$port_pids" ]; then
            for pid in $port_pids; do
                local proc_cwd=$(_proc_cwd "$pid")
                if [ -n "$proc_cwd" ] && [[ "$proc_cwd" == "$SCRIPT_DIR"* ]]; then
                    kill -15 "$pid" 2>/dev/null
                    sleep 1
                    if kill -0 "$pid" 2>/dev/null; then
                        kill -9 "$pid" 2>/dev/null
                    fi
                    confirmed_stopped=1
                fi
            done
        fi
    fi

    if [ "$confirmed_stopped" -eq 0 ]; then
        if [ -f "$SCRIPT_DIR/pids/backend.pid" ] && [ "$name" = "Flask backend" ]; then
            local saved_pid=$(cat "$SCRIPT_DIR/pids/backend.pid" 2>/dev/null)
            if [ -n "$saved_pid" ] && kill -0 "$saved_pid" 2>/dev/null; then
                kill -15 "$saved_pid" 2>/dev/null
                sleep 2
                if kill -0 "$saved_pid" 2>/dev/null; then
                    kill -9 "$saved_pid" 2>/dev/null
                fi
                confirmed_stopped=1
            fi
        fi

        if [ -f "$SCRIPT_DIR/pids/frontend.pid" ] && [ "$name" = "Vite frontend" ]; then
            local saved_pid=$(cat "$SCRIPT_DIR/pids/frontend.pid" 2>/dev/null)
            if [ -n "$saved_pid" ] && kill -0 "$saved_pid" 2>/dev/null; then
                kill -15 "$saved_pid" 2>/dev/null
                sleep 2
                if kill -0 "$saved_pid" 2>/dev/null; then
                    kill -9 "$saved_pid" 2>/dev/null
                fi
                confirmed_stopped=1
            fi
        fi
    fi

    if [ "$confirmed_stopped" -eq 0 ] && [ -n "$pgrep_pattern" ]; then
        pid_list=$(pgrep -f "$pgrep_pattern" 2>/dev/null)
        if [ -n "$pid_list" ]; then
            local env_pids=""
            for pid in $pid_list; do
                local proc_cwd=$(_proc_cwd "$pid")
                if [ -n "$proc_cwd" ] && [[ "$proc_cwd" == "$SCRIPT_DIR"* ]]; then
                    env_pids="$env_pids $pid"
                fi
            done

            if [ -n "$env_pids" ]; then
                for pid in $env_pids; do
                    kill -15 "$pid" 2>/dev/null
                done
                sleep 2
                for pid in $env_pids; do
                    if kill -0 "$pid" 2>/dev/null; then
                        kill -9 "$pid" 2>/dev/null
                    fi
                done
                confirmed_stopped=1
            fi
        fi
    fi

    if command_exists ss && ! ss -tlpn 2>/dev/null | grep -q ":$port\b"; then
        confirmed_stopped=1
    fi

    if [ "$confirmed_stopped" -eq 1 ]; then
        return 0
    else
        return 1
    fi
}

check_service_status() {
    local service_name=$1
    if systemctl --user is-active --quiet "$service_name" 2>/dev/null; then return 0;
    elif systemctl is-active --quiet "$service_name" 2>/dev/null; then return 0;
    else return 1; fi
}

start_service() {
    local service_name=$1
    # Try user-level service first, then system-level (Ollama runs as system service)
    if systemctl --user start "$service_name" >> "$BACKEND_STARTUP_LOG_FILE" 2>&1; then
        return 0;
    elif sudo -n systemctl start "$service_name" >> "$BACKEND_STARTUP_LOG_FILE" 2>&1; then
        return 0;
    else
        return 1;
    fi
}

port_owned_elsewhere() {
    local port=$1
    local owner_cwd=""
    local owner_pid=""

    if command_exists lsof; then
        owner_pid=$(lsof -i TCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -1)
        if [ -n "$owner_pid" ]; then
            owner_cwd=$(_proc_cwd "$owner_pid")
        fi
    elif command_exists ss; then
        owner_pid=$(ss -tlpn 2>/dev/null | awk -v p=":$port" '$4 ~ p {print $NF}' | sed 's/users://;s/"//g' | cut -d',' -f2 | cut -d'=' -f2 | head -1)
        if [ -n "$owner_pid" ]; then
            owner_cwd=$(_proc_cwd "$owner_pid")
        fi
    fi

    if [ -n "$owner_pid" ] && [ -n "$owner_cwd" ]; then
        case "$owner_cwd" in
            "$GUAARDVARK_ROOT"|"$GUAARDVARK_ROOT"/*) return 1 ;;
            *) echo "$owner_pid|$owner_cwd"; return 0 ;;
        esac
    fi
    return 1
}

check_frontend_build() {
    local dist_index="$FRONTEND_DIR/dist/index.html"

    if [ ! -f "$dist_index" ]; then
        return 2
    fi

    local src_mtime
    local dist_mtime
    src_mtime=$(find "$FRONTEND_DIR/src" -type f -printf '%T@\n' 2>/dev/null | sort -n | tail -1)
    dist_mtime=$(stat -c %Y "$dist_index" 2>/dev/null || stat -f %m "$dist_index" 2>/dev/null)

    if [ -n "$src_mtime" ] && [ -n "$dist_mtime" ]; then
        src_mtime=${src_mtime%.*}
        if [ "$src_mtime" -gt "$dist_mtime" ]; then
            return 1
        fi
    fi

    return 0
}

# get_lan_ips - return space-separated list of private LAN IPv4 addresses suitable
# for "access from phone on the same network". Used for the prominent LAN Access
# section and to opt the detected LAN IP into VITE_ALLOWED_HOSTS so `vite preview`
# (which carries its own proxy + host allowlist) will serve the page over the LAN.
get_lan_ips() {
    local ips
    local private_re='^(192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)'
    # macOS: no `hostname -I`, no `ip`/`ss`. Walk interfaces with ipconfig +
    # ifconfig (both BSD-native) so Wi-Fi/Ethernet LAN IPs are found (#41 —
    # "No private LAN IP detected" was a false negative on Mac).
    if is_macos; then
        if command -v ipconfig >/dev/null 2>&1; then
            ips=$(
                for iface in $(ifconfig -lu 2>/dev/null); do
                    ipconfig getifaddr "$iface" 2>/dev/null
                done | grep -E "$private_re" | tr '\n' ' ' | sed 's/ $//'
            )
        fi
        if [ -z "$ips" ] && command -v ifconfig >/dev/null 2>&1; then
            ips=$(ifconfig 2>/dev/null | awk '/inet /{print $2}' | grep -E "$private_re" | tr '\n' ' ' | sed 's/ $//')
        fi
        echo "$ips"
        return 0
    fi
    # Prefer hostname -I (common on Debian/Ubuntu etc.); fall back to ip tool.
    if command -v hostname >/dev/null 2>&1; then
        ips=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E "$private_re" | tr '\n' ' ' | sed 's/ $//')
    fi
    if [ -z "$ips" ] && command -v ip >/dev/null 2>&1; then
        ips=$(ip -o -4 addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | grep -E "$private_re" | tr '\n' ' ' | sed 's/ $//')
    fi
    echo "$ips"
}



is_port_listening() {
    local port=$1
    local timeout=$2
    local service_name=$3
    if ! command_exists ss; then
        sleep "$timeout"
        return 0
    fi
    
    local check_interval=0.5
    local max_checks=$((timeout * 2))
    for (( i=1; i<=max_checks; i++ )); do
        if ss -tlpn 2>/dev/null | grep -q ":$port\b"; then 
            return 0; 
        fi
        sleep $check_interval
    done
    return 1
}

check_backend_health() {
    local port=$1
    local timeout=${2:-8}
    local check_interval=0.5
    
    for (( i=1; i<=$((timeout * 2)); i++ )); do
        if curl -s --max-time 2 "http://localhost:$port/api/health" > /dev/null 2>&1; then
            return 0
        fi
        sleep $check_interval
    done
    return 1
}

# Print the PID(s) of LISTEN sockets on $1 that belong to THIS repo (process cwd under
# $SCRIPT_DIR). Empty output = no repo-owned listener on that port. This is the
# authoritative "is our backend running / what is its real PID" check — it does not rely
# on the launcher's $! (which can point at a parent that forked) or on a brittle argv
# pattern. On platforms without /proc (e.g. macOS) the cwd containment can't be verified,
# so we fall back to returning the raw listener PIDs rather than nothing.
repo_listener_pids() {
    local port=$1
    local pids=""
    if command_exists lsof; then
        pids=$(lsof -i TCP:"$port" -sTCP:LISTEN -t 2>/dev/null)
    elif command_exists ss; then
        pids=$(ss -tlpn 2>/dev/null | grep ":$port\b" | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u)
    fi
    [ -z "$pids" ] && return 0
    local out="" matched=0
    for pid in $pids; do
        local proc_cwd=$(_proc_cwd "$pid")
        if [ -n "$proc_cwd" ]; then
            if [[ "$proc_cwd" == "$SCRIPT_DIR"* ]]; then
                out="$out $pid"; matched=1
            fi
        fi
    done
    # No /proc available anywhere (couldn't verify any cwd) -> trust the raw listeners.
    if [ "$matched" -eq 0 ] && [ ! -e "/proc/self/cwd" ]; then
        out="$pids"
    fi
    echo "$out" | xargs 2>/dev/null
}

# Opt-in, bounded self-heal: if the backend is not answering /api/health AND nothing owns
# the port, relaunch it exactly ONCE via the same path as the main launch. Returns 0 if the
# backend is healthy (already, or after a successful relaunch), 1 otherwise. Never loops,
# never kills a foreign listener — the caller decides whether to retry.
relaunch_backend_if_dead() {
    local port=$1
    if check_backend_health "$port" 1; then
        return 0
    fi
    if command_exists ss && ss -tlpn 2>/dev/null | grep -q ":$port\b"; then
        # Port held by something not answering health — do NOT touch it (could be foreign).
        vader_warn "Port $port is occupied but not answering /api/health — not auto-relaunching."
        return 1
    fi
    vader_warn "Backend not responding and port $port is free — attempting a one-shot relaunch..."
    rm -f "$SCRIPT_DIR/pids/backend.pid" 2>/dev/null
    nohup env GUAARDVARK_ROOT="$SCRIPT_DIR" FLASK_PORT="$port" VITE_PORT="$VITE_PORT" GUAARDVARK_MIGRATIONS_VERIFIED="${GUAARDVARK_MIGRATIONS_VERIFIED:-}" "$VENV_DIR/bin/python" -m backend.app >> "$BACKEND_STARTUP_LOG_FILE" 2>&1 &
    local spawned=$!
    if ! is_port_listening "$port" 90 "Backend (self-heal)"; then
        if [ -n "$spawned" ] && kill -0 "$spawned" 2>/dev/null; then
            kill -9 "$spawned" > /dev/null 2>&1
        fi
        return 1
    fi
    local real_pid=$(repo_listener_pids "$port" | awk '{print $1}')
    [ -n "$real_pid" ] && echo "$real_pid" > "$SCRIPT_DIR/pids/backend.pid"
    check_backend_health "$port" 8
}

check_frontend_health() {
    local port=$1
    local timeout=${2:-10}
    local check_interval=0.5
    
    for (( i=1; i<=$((timeout * 2)); i++ )); do
        if curl -s --max-time 2 "http://localhost:$port" > /dev/null 2>&1; then
            return 0
        fi
        sleep $check_interval
    done
    return 1
}

check_celery_health() {
    local backend_port=$1
    local timeout=${2:-8}
    local check_interval=0.5
    
    for (( i=1; i<=$((timeout * 2)); i++ )); do
        if curl -s --max-time 2 "http://localhost:$backend_port/api/health/celery" 2>/dev/null | grep -q '"status":"up"'; then
            return 0
        fi
        sleep $check_interval
    done
    return 1
}

check_voice_health() {
    local backend_port=$1
    local timeout=${2:-5}
    local check_interval=0.5
    
    for (( i=1; i<=$((timeout * 2)); i++ )); do
        local response=$(curl -s --max-time 2 "http://localhost:$backend_port/api/voice/status" 2>/dev/null)
        if echo "$response" | grep -q '"status":"available"'; then
            if echo "$response" | grep -q '"speech_recognition":true' && echo "$response" | grep -q '"text_to_speech":true'; then
                return 0
            fi
        fi
        sleep $check_interval
    done
    return 1
}

compute_migration_fingerprint() {
    local fingerprint_inputs=""
    if [ -f "$BACKEND_DIR/models.py" ]; then
        fingerprint_inputs+=$(md5sum "$BACKEND_DIR/models.py" 2>/dev/null | cut -d' ' -f1)
    fi
    if [ -d "$BACKEND_DIR/migrations/versions" ]; then
        fingerprint_inputs+=$(find "$BACKEND_DIR/migrations/versions" -name '*.py' 2>/dev/null | LC_ALL=C sort | while read -r f; do md5sum "$f" 2>/dev/null; done | md5sum | cut -d' ' -f1)
    fi
    if [ -f "$BACKEND_DIR/migrations/env.py" ]; then
        fingerprint_inputs+=$(md5sum "$BACKEND_DIR/migrations/env.py" 2>/dev/null | cut -d' ' -f1)
    fi
    echo "$fingerprint_inputs" | md5sum | cut -d' ' -f1
}

save_migration_fingerprint() {
    local fp_file="$GUAARDVARK_ROOT/pids/.migration_fingerprint"
    mkdir -p "$GUAARDVARK_ROOT/pids"
    compute_migration_fingerprint > "$fp_file" 2>/dev/null
}

migration_fingerprint_matches() {
    local fp_file="$GUAARDVARK_ROOT/pids/.migration_fingerprint"
    [ -f "$fp_file" ] || return 1
    local saved current
    saved=$(cat "$fp_file" 2>/dev/null)
    current=$(compute_migration_fingerprint)
    [ "$saved" = "$current" ]
}

check_migrations_preflight() {
    local sync_script="$GUAARDVARK_ROOT/scripts/schema_sync.py"

    if [ -n "$GUAARDVARK_SKIP_MIGRATIONS" ]; then
        vader_info "Schema check skipped (GUAARDVARK_SKIP_MIGRATIONS set)"
        return 0
    fi

    if [ ! -f "$sync_script" ]; then
        vader_warn "Schema sync script not found — skipping"
        return 0
    fi

    if migration_fingerprint_matches; then
        vader_success "Schema check: up to date (no changes since last check)"
        return 0
    fi

    local output exit_code
    output=$("$VENV_DIR/bin/python" "$sync_script" 2>&1)
    exit_code=$?

    if [ $exit_code -eq 0 ]; then
        vader_success "Schema sync: OK"
        save_migration_fingerprint
        return 0
    else
        vader_error "Schema sync failed: $output"
        return 1
    fi
}

check_ollama_model() {
    if [ "$OLLAMA_AVAILABLE" -eq 0 ]; then
        return 0
    fi
    # Pass when ANY chat (non-embed) model is installed — the system doesn't default
    # to a single fixed tag (it auto-selects via config.get_default_llm, gemma/llama3
    # family), and the fresh-install bootstrap guarantees one. The old check grepped
    # the literal "llama2", a model the system never defaults to → always failed.
    timeout 5 ollama list 2>/dev/null | grep -viE 'embed|minilm' | grep -qE '[a-zA-Z0-9].*:'
}

run_health_checks() {
    echo ""
    vader_title "=== Running Health Checks ==="
    
    local all_passed=true
    local critical_failed=false
    
    if [ "$PARALLEL_CHECKS" -eq 1 ]; then
        check_backend_health "$FLASK_PORT" & BACKEND_CHECK_PID=$!
        check_frontend_health "$VITE_PORT" & FRONTEND_CHECK_PID=$!
        check_celery_health "$FLASK_PORT" & CELERY_CHECK_PID=$!
        
        wait $BACKEND_CHECK_PID
        if [ $? -eq 0 ]; then
            vader_success "Backend is healthy"
        else
            vader_error "Backend health check failed"
            all_passed=false
            critical_failed=true
        fi
        
        wait $FRONTEND_CHECK_PID
        if [ $? -eq 0 ]; then
            vader_success "Frontend is healthy"
        else
            vader_error "Frontend health check failed"
            all_passed=false
            critical_failed=true
        fi
        
        wait $CELERY_CHECK_PID
        if [ $? -eq 0 ]; then
            vader_success "Celery is healthy"
        else
            vader_error "Celery health check failed"
            all_passed=false
            critical_failed=true
        fi
        
        if [ "$VOICE_CHECK" -eq 1 ]; then
            check_voice_health "$FLASK_PORT" &
            VOICE_CHECK_PID=$!
            wait $VOICE_CHECK_PID
            if [ $? -eq 0 ]; then
                vader_success "Voice API is healthy"
            else
                vader_warn "Voice API health check failed"
                all_passed=false
            fi
        fi
    else
        if check_backend_health "$FLASK_PORT"; then
            vader_success "Backend is healthy"
        else
            vader_error "Backend health check failed"
            all_passed=false
            critical_failed=true
        fi
        
        if check_frontend_health "$VITE_PORT"; then
            vader_success "Frontend is healthy"
        else
            vader_error "Frontend health check failed"
            all_passed=false
            critical_failed=true
        fi
        
        if check_celery_health "$FLASK_PORT"; then
            vader_success "Celery is healthy"
        else
            vader_error "Celery health check failed"
            all_passed=false
            critical_failed=true
        fi
        
        if [ "$VOICE_CHECK" -eq 1 ]; then
            if check_voice_health "$FLASK_PORT"; then
                vader_success "Voice API is healthy"
            else
                vader_warn "Voice API health check failed"
                all_passed=false
            fi
        fi
    fi
    
    if check_ollama_model; then
        vader_success "Ollama model check passed"
    else
        vader_warn "Ollama model check failed (non-critical)"
    fi
    
    echo ""
    if [ "$critical_failed" = true ]; then
        vader_error "Critical health checks failed. System may not function properly."
        return 1
    elif [ "$all_passed" = true ]; then
        vader_success "All health checks passed!"
        return 0
    else
        vader_warn "Some non-critical health checks failed. Basic functionality should work."
        return 0
    fi
}

ensure_npm_package() {
    local pkg="$1"
    npm ls --prefix "$FRONTEND_DIR" --depth=0 "$pkg" >/dev/null 2>&1 || \
        (cd "$FRONTEND_DIR" && npm install --save-dev "$pkg" >> "$SETUP_LOG" 2>&1)
}

# -------------------------------------------------------------------
# Strong bootstrap helpers (restores intelligent first-run / repair behavior)
# These are the core of the fix: create venv is not enough — we must ensure
# it actually contains the requirements, and do the same for frontend.
# We prefer the project's own tools (system-manager repair already called above,
# dep_reconciler for full state/CRITICAL_PACKAGES/pytorch, npm ci for lock safety).
# -------------------------------------------------------------------

ensure_venv_python_version() {
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        return 0
    fi
    local venv_minor
    venv_minor=$("$VENV_DIR/bin/python" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo "")
    if [ -n "$venv_minor" ] && [ "$venv_minor" != "12" ]; then
        vader_warn "Existing venv uses Python 3.$venv_minor — recreating with Python 3.12..."
        rm -rf "$VENV_DIR"
    fi
}

# Fingerprint of the requirements files the bootstrap installs. Recorded in the
# bootstrap stamp on success so an EDIT to either file re-triggers the install
# instead of being masked by an import probe that only tests requirements-base
# packages.
BOOTSTRAP_STAMP="$VENV_DIR/.guaardvark_bootstrap_ts"
venv_reqs_fingerprint() {
    # constraints.txt and requirements-cv.txt shape the same venv (caps and CV
    # pins) — a change to either must invalidate the stamp too, or a box with a
    # stamped-but-drifted venv never picks the fix up.
    cat "$BACKEND_DIR/requirements-base.txt" "$BACKEND_DIR/requirements.txt" \
        "$BACKEND_DIR/requirements-cv.txt" "$BACKEND_DIR/constraints.txt" 2>/dev/null \
        | sha256sum 2>/dev/null | awk '{print $1}'
}

# Does the venv actually WORK right now? Pure functional probe, no stamp logic.
# This is what the post-bootstrap verification must use: the stamp is written
# only after a successful bootstrap, so a stamp-aware check there can never pass
# on a fresh box — it would gate the stamp on itself and fail forever.
backend_venv_functional() {
    [ -x "$VENV_DIR/bin/python" ] || return 1
    local venv_minor
    venv_minor=$("$VENV_DIR/bin/python" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo "")
    [ "$venv_minor" = "12" ] || return 1
    "$VENV_DIR/bin/python" -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
try:
    import numpy, flask, celery, redis, psycopg2
    import backend.config
    print('OK')
except Exception:
    sys.exit(1)
" >/dev/null 2>&1
}

# Can we SKIP the bootstrap? Functional AND provably complete. Use this only for
# the skip decision, never to verify a bootstrap that just ran.
backend_venv_healthy() {
    backend_venv_functional || return 1

    # The import probe only names packages from requirements-base.txt. If
    # requirements-base succeeds and requirements.txt then FAILS (ENOSPC, network
    # drop, bad sdist), the probe passes over a half-installed venv — so the next
    # ./start.sh sets needed=0 and SKIPS the very repair that would fix it. The
    # venv stays broken and start.sh never touches it again. Observed 2026-08-10.
    #
    # The stamp is written ONLY after a fully successful bootstrap, so its absence
    # is an exact marker for "bootstrap never finished". Consult it.
    if [ ! -f "$BOOTSTRAP_STAMP" ]; then
        return 1
    fi
    # Stamps written before this check existed hold only a timestamp — grandfather
    # them (a stamp at all means some past bootstrap ran to completion). A stamp
    # WITH a fingerprint must still match the current requirements files.
    local recorded current
    recorded="$(awk -F: '/^reqs:/ {print $2; exit}' "$BOOTSTRAP_STAMP" 2>/dev/null || true)"
    if [ -n "$recorded" ]; then
        current="$(venv_reqs_fingerprint)"
        [ "$recorded" = "$current" ] || return 1
    fi
    return 0
}

# Point pip's scratch space at real disk BEFORE any requirements install.
#
# pip's cachecontrol wrapper buffers each downloaded wheel body IN FULL into a
# tempfile under TMPDIR before committing it to the on-disk cache, and
# batch_download fetches several wheels in PARALLEL. The ML stack pulls
# multi-hundred-MB wheels (torch ~527MB, nvidia-cudnn ~366MB, nccl ~206MB,
# cusparselt ~170MB, cublas, cufft, cusolver, ...), so peak TMPDIR usage is the
# sum of the concurrent downloads — several GB.
#
# On any box where /tmp is a tmpfs (systemd default on Ubuntu 24.04+, sized
# from RAM) that blows the tmpfs ceiling and pip dies with
#   OSError: [Errno 28] No space left on device
# mid-download, WHILE `df /` still shows tens of GB free. Observed 2026-08-10 on
# this box: /tmp = 1.7G tmpfs, / = 68G free, install died fetching nccl and left
# a 14MB venv with numpy missing — which then surfaced as a bare
# "ModuleNotFoundError: No module named 'numpy'" at boot.
#
# scripts/install_pytorch.sh already does this for the torch step; the
# requirements-base.txt / requirements.txt installs run FIRST and were the
# actual casualty. Same convention (data/piptmp) so both share one cache.
ensure_pip_tmpdir() {
    # Network hardening for EVERY pip call this process spawns (requirements
    # installs, requirements-cv, install_pytorch.sh, dep_reconciler). pip's
    # default socket timeout is 15s — too tight for multi-hundred-MB wheels on
    # links that stall under sustained load. Observed 2026-08-13: a mid-download
    # read timeout on files.pythonhosted.org (fetching the 35MB av wheel)
    # aborted the whole bootstrap. Operator-set values win.
    export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-60}"
    export PIP_RETRIES="${PIP_RETRIES:-5}"
    # pip >= 25.1 resumes incomplete downloads instead of failing the run;
    # older pips have no such option and silently ignore this env var.
    export PIP_RESUME_RETRIES="${PIP_RESUME_RETRIES:-5}"

    # Respect an explicit TMPDIR from the operator/caller.
    if [ -n "${GUAARDVARK_PIP_TMPDIR_SET:-}" ]; then
        return 0
    fi
    local cand pip_tmp="" avail_kb avail_gb tmp_fs
    # Caller's TMPDIR first (operator override), then the repo default that
    # install_pytorch.sh also uses. An unusable candidate must not be left in
    # place — a stale TMPDIR pointing somewhere unwritable breaks pip harder
    # than no TMPDIR at all.
    for cand in "${TMPDIR:-}" "$SCRIPT_DIR/data/piptmp"; do
        [ -n "$cand" ] || continue
        mkdir -p "$cand" 2>/dev/null || true
        if [ -d "$cand" ] && [ -w "$cand" ]; then
            pip_tmp="$cand"
            break
        fi
        vader_warn "pip scratch dir $cand is not writable — trying next candidate."
    done
    if [ -z "$pip_tmp" ]; then
        vader_warn "No writable pip scratch dir found — clearing TMPDIR so pip uses the system default."
        unset TMPDIR
        return 0
    fi
    export TMPDIR="$pip_tmp"
    export PIP_CACHE_DIR="$pip_tmp"
    export GUAARDVARK_PIP_TMPDIR_SET=1

    # Zero placebo: prove the chosen scratch dir actually has room, and say so
    # loudly if it does not. 8 GB is the working headroom for the CUDA stack.
    avail_kb="$(df -Pk "$pip_tmp" 2>/dev/null | awk 'NR==2 {print $4}')"
    tmp_fs="$(df -Ph "$pip_tmp" 2>/dev/null | awk 'NR==2 {print $1}')"
    if [ -n "$avail_kb" ]; then
        avail_gb=$(( avail_kb / 1024 / 1024 ))
        if [ "$avail_kb" -lt 8388608 ]; then
            vader_warn "pip scratch dir $pip_tmp has only ${avail_gb}GB free on $tmp_fs — the CUDA/torch wheels need ~8GB."
            vader_warn "Free space or set TMPDIR to a roomier filesystem, or the install will die with ENOSPC."
        else
            vader_info "pip scratch: $pip_tmp (${avail_gb}GB free on $tmp_fs)"
        fi
    fi
}

# Install one requirements file into the active venv. NEVER '|| true' this:
# a single failed sdist build (evdev) makes pip install NOTHING from the run,
# which is how the 24.04 install died with numpy missing while setup.log said
# the error 800 lines earlier. On failure: scan THIS attempt's output for
# known-fixable signatures (missing dev headers → apt once; transient network
# fault → back off and retry, downloads resume from PIP_CACHE_DIR so every
# retry makes forward progress); else fail LOUD with the actual first error.
# Observed 2026-08-13: one PyPI read timeout mid-wheel aborted the entire
# bootstrap because nothing retried at this level.
PIP_NET_FAULT_RE="Read timed out|ReadTimeoutError|Connection broken|ConnectionResetError|Connection aborted|NewConnectionError|ProtocolError|IncompleteRead|Temporary failure in name resolution|Network is unreachable"
pip_install_requirements() {
    local req="$1" attempt max_attempts=4 devheaders_fixed=0 log_mark attempt_out
    for attempt in $(seq 1 "$max_attempts"); do
        log_mark=$(wc -c < "$SETUP_LOG" 2>/dev/null || echo 0)
        if pip install -r "$req" >> "$SETUP_LOG" 2>&1; then
            [ "$attempt" -gt 1 ] && vader_success "$(basename "$req") installed on retry $attempt."
            return 0
        fi
        # Scope diagnostics to THIS attempt — setup.log is append-only across
        # runs, so a plain tail can match a stale error from a previous attempt.
        attempt_out="$(tail -c +$((log_mark + 1)) "$SETUP_LOG")"
        if [ "$devheaders_fixed" -eq 0 ] \
           && grep -q "Python\.h: No such file or directory" <<< "$attempt_out"; then
            vader_warn "pip failed building a native wheel: Python dev headers missing (Python.h)."
            if command_exists apt-get; then
                vader_info "Auto-fix: installing python3.12-dev via apt, then retrying $(basename "$req")..."
                sudo apt-get install -y python3.12-dev python3.12-venv >> "$SETUP_LOG" 2>&1 || true
                devheaders_fixed=1
                continue
            fi
        fi
        if [ "$attempt" -lt "$max_attempts" ] \
           && grep -qE "$PIP_NET_FAULT_RE" <<< "$attempt_out"; then
            vader_warn "pip hit a transient network fault downloading $(basename "$req") (attempt $attempt/$max_attempts) — retrying in $((attempt * 10))s..."
            vader_info "Completed wheels are cached; the retry resumes where this attempt stopped."
            sleep $((attempt * 10))
            continue
        fi
        break
    done
    vader_error "pip install -r $(basename "$req") FAILED — dependencies from this file are NOT installed."
    # ENOSPC gets its own message: the raw pip traceback points at urllib3 and
    # reads like a network fault ("Connection broken"), which sends you chasing
    # the wrong bug. Name the real filesystem and its free space.
    if grep -q "No space left on device" <<< "$attempt_out"; then
        vader_error "CAUSE: ran out of disk in pip's scratch space (TMPDIR=${TMPDIR:-/tmp})."
        df -Ph "${TMPDIR:-/tmp}" 2>/dev/null | sed 's/^/      /'
        vader_error "If that filesystem is a small tmpfs, point TMPDIR at real disk:"
        vader_error "      TMPDIR=$SCRIPT_DIR/data/piptmp ./start.sh"
    elif grep -qE "$PIP_NET_FAULT_RE" <<< "$attempt_out"; then
        vader_error "CAUSE: network kept failing mid-download across $max_attempts attempts."
        vader_error "This is the box's connection (or NIC) dropping under sustained load, not PyPI."
        vader_error "Check the adapter (ip link / dmesg | grep -iE 'link|eth|enp'), then re-run ./start.sh —"
        vader_error "already-downloaded wheels are cached, so it resumes where it stopped."
    fi
    vader_error "First error in $SETUP_LOG:"
    grep -m1 -E "fatal error:|error: subprocess-exited-with-error|ERROR: " <<< "$attempt_out" | sed 's/^/      /'
    vader_info "After fixing, re-run ./start.sh (or: ./scripts/heal_backend_venv.sh)"
    return 1
}

ensure_backend_python_environment() {
    # Under --fast we still repair a completely broken venv (otherwise nothing works).
    # We only fast-skip when the probe already says it's healthy.
    if [ "$FAST_START" -eq 1 ] && backend_venv_healthy; then
        return 0
    fi

    local needed=0
    if [ ! -d "$VENV_DIR" ]; then
        needed=1
    elif ! backend_venv_healthy; then
        needed=1
    fi

    if [ "$needed" -eq 1 ]; then
        # DNS sanity probe before multi-GB downloads. On the 24.04 client box a
        # full resolution outage ("Temporary failure in name resolution" for
        # pypi.org) failed the whole reconcile mid-bootstrap. Warn loudly and
        # continue — the box may be deliberately offline with a warm cache.
        #
        # PROXY-AWARE: when http_proxy/https_proxy is set, local DNS is the WRONG
        # probe. pip does not resolve the hostname itself — it opens a CONNECT to
        # the proxy and the proxy's far side resolves. A box whose only route to
        # the internet is a proxy (e.g. an adb port-forward to a phone's proxy on
        # 127.0.0.1:8080, with no network interface but lo) therefore fails `getent
        # hosts pypi.org` permanently while pip works perfectly. Probing DNS there
        # prints a red "installs WILL fail" that is simply untrue, and sends the
        # operator off restarting systemd-resolved for nothing. Probe whichever
        # thing pip will actually use.
        _gv_proxy="${https_proxy:-${HTTPS_PROXY:-${http_proxy:-${HTTP_PROXY:-}}}}"
        if [ -n "$_gv_proxy" ]; then
            _gv_pp="${_gv_proxy#*://}"; _gv_pp="${_gv_pp%%/*}"; _gv_pp="${_gv_pp##*@}"
            _gv_ph="${_gv_pp%%:*}"
            _gv_pt="${_gv_pp##*:}"
            [ "$_gv_pt" = "$_gv_ph" ] && _gv_pt=80
            if timeout 5 bash -c "exec 3<>/dev/tcp/${_gv_ph}/${_gv_pt}" >/dev/null 2>&1; then
                vader_info "Proxy $_gv_proxy is reachable — pip resolves through it (local DNS not required)."
            else
                vader_error "Proxy $_gv_proxy is NOT reachable — dependency installs WILL fail."
                vader_error "Check the tunnel is up (e.g. adb forward / the proxy app on the phone)."
                vader_info "Continuing anyway in case a warm pip cache covers this run..."
            fi
            unset _gv_pp _gv_ph _gv_pt
        elif ! timeout 5 getent hosts pypi.org >/dev/null 2>&1; then
            vader_error "DNS resolution is broken on this box (cannot resolve pypi.org) — dependency installs WILL fail."
            vader_error "Try: sudo systemctl restart systemd-resolved   (check: resolvectl status)"
            vader_info "Continuing anyway in case this box is intentionally offline..."
        fi
        unset _gv_proxy
        vader_info "Python environment incomplete or first-time setup — bootstrapping dependencies (logged to $SETUP_LOG)..."
        source "$VENV_DIR/bin/activate" || { vader_error "Failed to activate venv for bootstrap"; return 1; }

        # MUST precede every pip call below (requirements-base, requirements,
        # requirements-cv, install_pytorch.sh, the reconciler). Exported, so the
        # install_pytorch.sh subprocess inherits it instead of re-deriving it.
        ensure_pip_tmpdir

        # Constrain EVERY bootstrap pip pass, not just install_pytorch.sh. The
        # requirements-cv resolve used to run unconstrained: a transitive with a
        # different numpy floor moved numpy, the constrained torch pass moved it
        # back, and every boot looped through a full torch re-stage. Operator-set
        # PIP_CONSTRAINT wins; unset again before the reconciler — isolated
        # plugin venvs manage their own stacks and must not inherit these caps
        # (see the NOTE in backend/constraints.txt).
        _gv_own_constraint=0
        if [ -z "${PIP_CONSTRAINT:-}" ] && [ -f "$BACKEND_DIR/constraints.txt" ]; then
            export PIP_CONSTRAINT="$BACKEND_DIR/constraints.txt"
            _gv_own_constraint=1
        fi

        # requirements-base first (matches system-manager + leaves room for smart torch).
        # Fail fast: if the core files can't install there is nothing to boot —
        # stop HERE with the real error instead of cascading numpy tracebacks
        # through the hardware probe, vite build, and reconciler (24.04 lesson).
        if [ -f "$BACKEND_DIR/requirements-base.txt" ]; then
            if ! pip_install_requirements "$BACKEND_DIR/requirements-base.txt"; then
                deactivate
                return 1
            fi
        fi
        if [ -f "$BACKEND_DIR/requirements.txt" ]; then
            if ! pip_install_requirements "$BACKEND_DIR/requirements.txt"; then
                deactivate
                return 1
            fi
        fi

        # Optional face-restoration extra (P3-10) — OPT-IN ONLY. These deps
        # (gfpgan/realesrgan/basicsr/facexlib) are a multi-hundred-MB stack
        # that lacks reliable aarch64 wheels, and the earlier auto-install on
        # any GPU box made every fresh install pay for an optional feature. The
        # consumer imports lazily inside try/except and degrades gracefully
        # when absent (restoration_available=False), so skipping costs nothing
        # at boot.
        # Failure here WARNS but never fails the core install.
        if [ -f "$BACKEND_DIR/requirements-cv.txt" ]; then
            if [ "${GUAARDVARK_INSTALL_CV:-0}" = "1" ]; then
                vader_info "Installing optional CV/face-restoration deps (requirements-cv.txt)..."
                pip install -r "$BACKEND_DIR/requirements-cv.txt" >> "$SETUP_LOG" 2>&1 \
                    || vader_warn "Optional CV deps failed to install (face-restore stays disabled; non-fatal). Retry later: pip install -r backend/requirements-cv.txt"
            else
                vader_info "Skipping optional CV deps (face-restore/anatomy stay disabled). Opt in with GUAARDVARK_INSTALL_CV=1"
            fi
        fi

        # The smart GPU/CUDA PyTorch installer (also called by dep_reconciler).
        # Use hardware_policy as single source of truth for the torch channel
        # (same as the isolated plugin setup_venv.sh scripts use). Falls back
        # gracefully if the policy module or backend venv is not ready yet.
        if [ -f "$SCRIPT_DIR/scripts/install_pytorch.sh" ]; then
            GUAARDVARK_TORCH_CHANNEL="$("$VENV_DIR/bin/python" -m backend.services.hardware_policy torch_channel 2>/dev/null || true)" \
                bash "$SCRIPT_DIR/scripts/install_pytorch.sh" >> "$SETUP_LOG" 2>&1 || vader_warn "install_pytorch.sh exited non-zero (GPU mode may be limited)"
            # Gate nvidia-ml-py post-torch per edge audit (avoid FutureWarning/unneeded dep on CPU/ARM/ROCm/Metal).
            if ! command -v nvidia-smi &> /dev/null; then
                "$VENV_DIR/bin/pip" uninstall -y nvidia-ml-py pynvml 2>/dev/null | tail -1 || true
            fi
            # install_pytorch.sh's `pip install --upgrade ... --index-url .../whl/<ver>` can
            # drag a different numpy + an old setuptools back in, violating the ML-stack
            # pins (scripts/lib/venv_pins.sh) and llama-index (setuptools>=80.9.0). Re-assert them without
            # touching torch (--no-deps) — but only the ones actually wrong: the probe
            # is offline, --force-reinstall is not (scripts/lib/venv_pins.sh).
            _gv_bad_pins="$(venv_pins_violated "$VENV_DIR/bin/python" "${GV_ML_PINS[@]}")" || true
            if [ -n "$_gv_bad_pins" ]; then
                # shellcheck disable=SC2086  # one spec per line, no spaces inside a spec
                pip install --no-deps --force-reinstall $_gv_bad_pins >> "$SETUP_LOG" 2>&1 \
                    || vader_warn "Could not re-pin ${_gv_bad_pins//$'\n'/ } after PyTorch — check 'pip check'."
            fi
            unset _gv_bad_pins
            # Extra safety: always purge flash-attn/xformers after torch (even on GPU). These
            # are the direct cause of the aten::_flash schema mismatch (flash 2.5.7 vs torch
            # 2.5.1+cu124 philox vs rng_state) logged in backend.log/preflight on diffusers
            # import for batch_image_generation_api. Custom nodes + plugin reqs re-introduce them.
            "$VENV_DIR/bin/pip" uninstall -y flash-attn flash_attn xformers 2>/dev/null | tail -1 || true
        fi

        if [ "${_gv_own_constraint:-0}" -eq 1 ]; then
            unset PIP_CONSTRAINT
        fi
        unset _gv_own_constraint

        # Full reconciler pass for state tracking, CRITICAL_PACKAGES verification, cli_venv, etc.
        # A failure here is NOT a soft warn: it means a dependency target is broken.
        # Surface the reconciler's own failure summary and drop a sentinel that
        # preflight_check.py turns into a red failure (no more green "All checks
        # passed" over a half-installed venv).
        if [ -x "$VENV_DIR/bin/python" ]; then
            if ! "$VENV_DIR/bin/python" "$SCRIPT_DIR/scripts/dep_reconciler.py" --force --only backend_venv,cli_venv --repo-root "$SCRIPT_DIR" >> "$SETUP_LOG" 2>&1; then
                vader_error "Dependency reconciler FAILED:"
                tail -n 200 "$SETUP_LOG" | grep -A4 "Reconciliation failed for:" | sed 's/^/      /'
                # pip reports an unreachable index as a resolver error ("No matching
                # distribution", even "ResolutionImpossible"); say what it really was.
                if tail -c 40000 "$LOGS_DIR/dep_reconciler.log" 2>/dev/null | grep -qE "$PIP_NET_FAULT_RE"; then
                    vader_error "Cause: pip could not reach its package index (DNS or link fault). Nothing was removed — fix the network and re-run ./start.sh."
                fi
                vader_info "Details: $SETUP_LOG and logs/dep_reconciler.log. Repair: ./scripts/heal_backend_venv.sh"
            fi
        fi

        deactivate

        # backend_venv_functional, NOT backend_venv_healthy: healthy() requires the
        # stamp, and the stamp is written on the next line. Gating it on itself
        # deadlocks every fresh box into "Bootstrap did not produce a working
        # Python environment" no matter how well the install went.
        if backend_venv_functional; then
            vader_success "Backend Python environment bootstrapped and healthy"
            # Written ONLY here, on a fully successful bootstrap. backend_venv_healthy()
            # treats its absence as "bootstrap never finished" and forces a re-run, so
            # a half-installed venv can no longer masquerade as healthy.
            { date +%s; printf 'reqs:%s\n' "$(venv_reqs_fingerprint)"; } \
                > "$BOOTSTRAP_STAMP" 2>/dev/null || true
        else
            vader_error "Bootstrap did not produce a working Python environment."
            # Name the import that fails instead of leaving it to the log.
            "$VENV_DIR/bin/python" -c "import sys; sys.path.insert(0, '$SCRIPT_DIR'); import numpy, flask, celery, redis, psycopg2; import backend.config" 2>&1 \
                | tail -n 3 | sed 's/^/      /'
            if tail -c 40000 "$SETUP_LOG" 2>/dev/null | grep -qE "$PIP_NET_FAULT_RE"; then
                vader_error "Cause: pip could not reach its package index (DNS or link fault) during the install above."
            fi
            vader_info "See $SETUP_LOG for details. Recommended manual steps:"
            vader_info "  ./scripts/dep_reconciler.py --force"
            vader_info "  or: ./scripts/system-manager/system-manager repair ."
            return 1
        fi
    fi
    return 0
}

ensure_frontend_deps() {
    local nm="$FRONTEND_DIR/node_modules"
    local lock="$FRONTEND_DIR/package-lock.json"
    local stamp="$FRONTEND_DIR/.npm_stamp"

    if [ "$FAST_START" -eq 1 ] && [ -d "$nm" ]; then
        return 0
    fi

    # Run npm ci (lockfile-strict, same strategy as scripts/dep_reconciler/reconcilers/frontend.py)
    # only when truly needed: missing node_modules, or lockfile newer than our stamp.
    if [ ! -d "$nm" ] || [ ! -f "$stamp" ] || [ "$lock" -nt "$stamp" 2>/dev/null ]; then
        vader_info "Ensuring frontend dependencies (using npm ci for lockfile safety)..."
        if (cd "$FRONTEND_DIR" && npm ci >> "$SETUP_LOG" 2>&1); then
            touch "$stamp" 2>/dev/null || true
            vader_success "Frontend node_modules ready"
        else
            vader_warn "npm ci failed — trying npm install (may touch package-lock.json)"
            if (cd "$FRONTEND_DIR" && npm install >> "$SETUP_LOG" 2>&1); then
                touch "$stamp" 2>/dev/null || true
            else
                vader_error "Frontend dependency installation failed. See $SETUP_LOG"
                return 1
            fi
        fi
    fi
    return 0
}

vader_header
vader_title "  Guaardvark Startup Script v5.1 - Smart Install Mode (intelligent bootstrap restored)"
vader_header

ACTIVE_MODEL_FILE="$GUAARDVARK_STORAGE_DIR/active_model.txt"
if [ -f "$ACTIVE_MODEL_FILE" ]; then
  ACTIVE_MODEL_NAME="$(cat "$ACTIVE_MODEL_FILE" | tr -d ' \n\r')"
  if [ -n "$ACTIVE_MODEL_NAME" ]; then
    vader_info "Last active model: $ACTIVE_MODEL_NAME"
  fi
fi

echo "--- Log Start: $(date) ---" > "$BACKEND_STARTUP_LOG_FILE"
echo "--- Log Start: $(date) ---" > "$FRONTEND_LOG_FILE"

for port_check in "$FLASK_PORT" "$VITE_PORT"; do
  conflict=$(port_owned_elsewhere "$port_check")
  if [ -n "$conflict" ]; then
    conflict_pid="${conflict%%|*}"
    conflict_cwd="${conflict#*|}"
    if [ "${FORCE_PORTS:-0}" -eq 1 ]; then
      vader_warn "Port $port_check is in use by PID $conflict_pid from '$conflict_cwd' (outside $GUAARDVARK_ROOT). Forcing stop."
      kill -15 "$conflict_pid" 2>/dev/null
      sleep 1
      if kill -0 "$conflict_pid" 2>/dev/null; then
        kill -9 "$conflict_pid" 2>/dev/null
      fi
    else
      vader_error "Port $port_check is in use by PID $conflict_pid from '$conflict_cwd' (outside $GUAARDVARK_ROOT). Stop that process or rerun with --force-ports."
      exit 1
    fi
  fi
done

vader_step 1 "Stopping previous application servers..."
if [ -f "$SCRIPT_DIR/stop.sh" ]; then
    # stop.sh reaps leftover start.sh; do not let it kill this boot.
    START_LOCK_PROTECT_PIDS="$$ ${START_LOCK_PROTECT_PIDS:-}"
    export START_LOCK_PROTECT_PIDS
    "$SCRIPT_DIR/stop.sh" >/dev/null 2>&1
fi

kill_process "$FLASK_PORT" "Flask backend" "$FLASK_PROCESS_PATTERN" &
kill_process "$VITE_PORT" "Vite frontend" "$VITE_PROCESS_PATTERN" &
wait

if [ -f "$SCRIPT_DIR/pids/celery.pid" ]; then
    celery_pid=$(cat "$SCRIPT_DIR/pids/celery.pid" 2>/dev/null)
    if [ -n "$celery_pid" ] && kill -0 "$celery_pid" 2>/dev/null; then
        proc_cwd=$(_proc_cwd "$celery_pid")
        if [ -n "$proc_cwd" ] && [[ "$proc_cwd" == "$SCRIPT_DIR"* ]]; then
            kill -15 "$celery_pid" 2>/dev/null
            sleep 2
            if kill -0 "$celery_pid" 2>/dev/null; then
                kill -9 "$celery_pid" 2>/dev/null
            fi
        fi
    fi
fi

# Pattern catches both worker AND beat from this checkout.
# ERE alternation — `\(worker\|beat\)` was BRE syntax pgrep -f matched literally.
celery_pids=$(pgrep -f "celery.*(worker|beat)" 2>/dev/null)
if [ -n "$celery_pids" ]; then
    for pid in $celery_pids; do
        proc_cwd=$(_proc_cwd "$pid")
        if [ -n "$proc_cwd" ] && [[ "$proc_cwd" == "$SCRIPT_DIR"* ]]; then
            kill -15 "$pid" 2>/dev/null
            sleep 1
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null
            fi
        fi
    done
fi
vader_separator

# ── macOS bootstrap (Darwin only — NO-OP on Linux / Pi / WSL) ────────────────
# Can't use apt/systemd on a Mac. Ensure Python 3.12 (Homebrew) BEFORE the gate
# below, install system deps via brew + start pg/redis as brew services, and skip
# NVIDIA/systemd tuning. Everything after this is platform-agnostic or already
# guarded (nvidia-smi / apt-get presence). On Linux this whole block is skipped.
if [ "${GUAARDVARK_OS:-linux}" = macos ]; then
    vader_info "macOS detected — bootstrapping via Homebrew + Python 3.12..."
    platform_ensure_python        || { vader_error "macOS: could not ensure Python 3.12 (see hint above)."; exit 1; }
    platform_install_system_deps  || vader_warn "macOS: some Homebrew deps failed; continuing (check brew output)."
    platform_gpu_setup
fi

# ── Linux bootstrap (auto Python 3.12 + node/npm before version gates) ───────
if [ "${GUAARDVARK_OS:-linux}" = linux ] && [ -f "$SCRIPT_DIR/scripts/platform/linux.sh" ]; then
    vader_info "Linux detected — ensuring Python 3.12..."
    if platform_ensure_python; then
        rm -f "$CACHE_DIR/python_check" 2>/dev/null || true
    else
        vader_error "Linux: could not ensure Python 3.12 (see hints above)."
        exit 1
    fi
    # Reload .env in case platform_ensure_python persisted PYTHON_CMD
    if [ -f "$SCRIPT_DIR/.env" ]; then
        set -a
        # shellcheck disable=SC1091
        . "$SCRIPT_DIR/.env"
        set +a
        export GUAARDVARK_ROOT="$SCRIPT_DIR"
    fi
fi

vader_step 2 "Checking environment dependencies..."
if ! check_with_cache "python_check" check_python_version; then
    vader_error "Python 3.12 required. Exiting."
    exit 1
fi
if [ "${GUAARDVARK_OS:-linux}" = linux ] && declare -F ensure_node_npm >/dev/null 2>&1; then
    ensure_node_npm || { vader_error "Node.js/npm setup failed. Exiting."; exit 1; }
    rm -f "$CACHE_DIR/node_check" "$CACHE_DIR/npm_check" 2>/dev/null || true
fi
if ! check_with_cache "node_check" check_node_version; then
    vader_error "Node.js 20+ required. Exiting."
    exit 1
fi
if ! check_with_cache "npm_check" check_npm; then
    vader_error "npm required. Exiting."
    exit 1
fi

OLLAMA_AVAILABLE=1
if ! command_exists "ollama"; then
    vader_warn "ollama command line tool not found."
    if command_exists apt-get && [ "$FAST_START" -ne 1 ]; then
        # Ollama installer's tarball is zstd-compressed — extraction silently fails on minimal images without it.
        if ! command_exists zstd; then
            vader_info "Installing zstd (required by ollama installer)..."
            sudo apt-get install -y zstd >/dev/null 2>&1 || vader_warn "zstd install failed; ollama install likely will too."
        fi
        curl -fsSL https://ollama.com/install.sh | sh || OLLAMA_AVAILABLE=0
        command_exists "ollama" || OLLAMA_AVAILABLE=0
    else
        OLLAMA_AVAILABLE=0
    fi
fi

if ! command_exists curl || ! command_exists wget || ! command_exists git; then
    vader_warn "Some system dependencies missing (curl, wget, git)"
fi

if ! command_exists ffmpeg; then
    vader_warn "FFmpeg not found. Voice features require FFmpeg."
    if command_exists apt-get && [ "$FAST_START" -ne 1 ]; then
        if sudo apt-get update && sudo apt-get install -y ffmpeg; then
            vader_success "FFmpeg installed successfully"
        else
            vader_warn "FFmpeg installation failed. Voice features will be unavailable."
            VOICE_AVAILABLE=0
        fi
    else
        vader_warn "FFmpeg not available. Voice features will be unavailable. Install FFmpeg to enable voice."
        VOICE_AVAILABLE=0
    fi
fi

# Early hardware profile (critical for cluster/Interconnector and operator visibility).
# We do this with system python + explicit PYTHONPATH so it works *before* the venv
# is populated and independent of any Python package state. The later call in step 8
# will refresh it with the project venv when available.
mkdir -p "$HOME/.guaardvark"
# Invoke by FILE PATH, not `-m backend.services...`: -m imports backend/__init__.py,
# which pulls socketio_events → numpy. Pre-venv (system python3, no numpy) that
# GUARANTEED a "ModuleNotFoundError: numpy" traceback at the top of setup.log on
# every fresh box — the red herring that derailed a real install diagnosis.
# The detector itself is pure stdlib and needs no package context.
if python3 "$SCRIPT_DIR/backend/services/hardware_detector.py" \
        --output "$HOME/.guaardvark/hardware.json" >> "$SETUP_LOG" 2>&1; then
    # Only print on success the first time or when it actually wrote something useful
    if [ -f "$HOME/.guaardvark/hardware.json" ]; then
        vader_success "Hardware profile written (~/.guaardvark/hardware.json)"
    fi
else
    vader_info "Hardware profile probe (non-fatal; will retry after venv bootstrap)"
fi

# Known-quirk NIC mitigation — BEFORE any heavy downloads (pip/npm/ollama/models).
#
# Intel I217/I218/I219 NICs (kernel driver e1000e — the PCH-integrated port on
# most Intel desktop boards) have a years-old, Intel-acknowledged TX engine bug:
# under sustained transmit load the NIC hangs ("Detected Hardware Unit Hang" in
# dmesg), the driver resets it, and the interface drops mid-transfer. Our
# bootstrap is exactly that load profile. On an ASRock Z390 Pro4 (I219-V),
# 2026-08-13: repeated bootstrap deaths (read timeouts, DNS gone, BrokenPipe),
# CURED by disabling offloads — install completed first try afterwards. BIOS
# ASPM settings do not govern this NIC (it is not a PCIe device); the offload
# fix is the real one. Runtime-only (no persistent host change): reapplied on
# every start, skipped once ethtool already reports TSO off. Opt out with
# GUAARDVARK_SKIP_NIC_FIX=1. hardware_detector.py mirrors this knowledge in
# hardware.json ("network" section, KNOWN_NIC_QUIRKS).
mitigate_nic_quirks() {
    if [ "${GUAARDVARK_SKIP_NIC_FIX:-0}" = "1" ]; then
        vader_info "Skipping NIC quirk mitigation (GUAARDVARK_SKIP_NIC_FIX=1)."
        return 0
    fi
    local netdir iface drv _sudo="sudo"
    # Never hang a non-interactive boot (systemd/cron) on a sudo password prompt.
    [ -t 0 ] || _sudo="sudo -n"
    for netdir in /sys/class/net/*; do
        [ -e "$netdir/device" ] || continue   # skip lo/veth/docker/bridges
        drv="$(basename "$(readlink -f "$netdir/device/driver" 2>/dev/null)" 2>/dev/null)"
        [ "$drv" = "e1000e" ] || continue
        iface="$(basename "$netdir")"
        if command_exists ethtool \
           && ethtool -k "$iface" 2>/dev/null | grep -q "^tcp-segmentation-offload: off"; then
            continue   # already mitigated (by us earlier, or by the operator)
        fi
        vader_warn "NIC $iface is an Intel e1000e (I217/I218/I219 family) — known to hang under sustained download load."
        vader_info "Applying known fix: disabling TSO/GSO/GRO offloads + EEE on $iface (runtime-only, reapplied each start; skip with GUAARDVARK_SKIP_NIC_FIX=1)."
        if ! command_exists ethtool; then
            $_sudo apt-get install -y ethtool >> "$SETUP_LOG" 2>&1 \
                || { vader_warn "ethtool unavailable — cannot apply NIC fix. Manual: sudo ethtool -K $iface tso off gso off gro off"; continue; }
        fi
        if $_sudo ethtool -K "$iface" tso off gso off gro off >> "$SETUP_LOG" 2>&1; then
            $_sudo ethtool --set-eee "$iface" eee off >> "$SETUP_LOG" 2>&1 || true
            vader_success "NIC offload fix applied to $iface (prevents e1000e 'Hardware Unit Hang' drops)."
        else
            vader_warn "Could not apply NIC fix on $iface (sudo unavailable?). Manual: sudo ethtool -K $iface tso off gso off gro off"
        fi
    done
    return 0
}
mitigate_nic_quirks

vader_separator

vader_step 3 "Ensuring Redis service is running..."
"$(dirname "$0")/start_redis.sh" || { vader_error "Redis failed to start"; exit 1; }
# start_redis.sh rewrites REDIS_URL when an alt port dies — pick it up immediately
# so later Celery / backend exports match a live broker.
if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a
  . "$SCRIPT_DIR/.env"
  set +a
fi
# Fail closed: never continue into Celery with a dead REDIS_URL.
_redis_check_url="${CELERY_BROKER_URL:-${REDIS_URL:-redis://localhost:6379/0}}"
_redis_check_port=$(echo "$_redis_check_url" | sed -E 's|^redis://([^@]*@)?[^:]+:([0-9]+).*|\2|')
_redis_check_pass=$(echo "$_redis_check_url" | sed -n 's|^redis://:\([^@]*\)@.*|\1|p')
[ -n "$_redis_check_port" ] || _redis_check_port=6379
if [ -n "$_redis_check_pass" ]; then
  _redis_pong=$(redis-cli -p "$_redis_check_port" -a "$_redis_check_pass" --no-auth-warning ping 2>/dev/null || true)
else
  _redis_pong=$(redis-cli -p "$_redis_check_port" ping 2>/dev/null || true)
fi
if [ "$_redis_pong" != "PONG" ]; then
  vader_error "Redis broker not reachable at $_redis_check_url after start_redis.sh"
  vader_info "Check logs above and REDIS_URL in .env — refusing to start with a dead broker."
  exit 1
fi
vader_success "Redis broker PING ok (:${_redis_check_port})"
vader_separator

vader_step 4 "Ensuring PostgreSQL database is ready..."
"$(dirname "$0")/start_postgres.sh" || { vader_error "PostgreSQL setup failed"; exit 1; }
if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a
  . "$SCRIPT_DIR/.env"
  set +a
fi
vader_separator

vader_step 5 "Setting up Python environment..."
ensure_venv_python_version
FIRST_SETUP_DONE=1
if [ ! -d "$VENV_DIR" ]; then
    FIRST_SETUP_DONE=0
    vader_info "Creating Python venv at $VENV_DIR"
    $PYTHON_CMD -m venv "$VENV_DIR" || { vader_error "Failed to create venv"; exit 1; }
fi

if [ "$FIRST_SETUP_DONE" -eq 0 ]; then
    vader_header
    vader_title "  First-time / recovery setup — installing core dependencies"
    vader_header
    vader_info "This can take several minutes on first run (PyTorch, etc.). Progress is logged to $SETUP_LOG"
fi

source "$VENV_DIR/bin/activate" || { vader_error "Failed to activate venv"; exit 1; }

# The real bootstrap work (was previously skipped with "Dependency reconciliation skipped").
# This is the key part of the strong fix: after creation (or if broken) we now ensure
# the venv actually has the packages via ensure_backend_python_environment.
if ! ensure_backend_python_environment; then
    vader_error "Python bootstrap failed. Cannot continue."
    # ensure_... already deactivated on its error path
    cd "$SCRIPT_DIR"
    exit 1
fi

# The ensure function manages its own activate/deactivate when it performs work.
# We only need to ensure we are not left inside the venv here.
if [ -n "${VIRTUAL_ENV:-}" ]; then
    deactivate 2>/dev/null || true
fi

# --- CLI tool setup ---
# Ensure the editable CLI install exists, then symlink into ~/.local/bin.
CLI_DIR="$SCRIPT_DIR/cli"
CLI_VENV_DIR="$VENV_DIR"
if [ -d "$CLI_DIR" ] && [ -f "$CLI_DIR/setup.py" ]; then
    if [ -d "$CLI_VENV_DIR" ]; then
        if [ ! -f "$CLI_VENV_DIR/bin/guaardvark" ] && [ -x "$CLI_VENV_DIR/bin/python" ]; then
            vader_info "CLI binary missing — installing editable cli package..."
            if "$CLI_VENV_DIR/bin/python" "$SCRIPT_DIR/scripts/dep_reconciler.py" --only cli_venv --force --repo-root "$SCRIPT_DIR" >> "$SETUP_LOG" 2>&1; then
                vader_success "CLI tool installed"
            else
                vader_warn "CLI install failed (see setup.log); guaardvark command may be unavailable"
            fi
        fi
        # Symlink CLI commands into ~/.local/bin so they work system-wide
        LOCAL_BIN="$HOME/.local/bin"
        mkdir -p "$LOCAL_BIN"
        for cmd in guaardvark; do
            CLI_BIN="$CLI_VENV_DIR/bin/$cmd"
            if [ -f "$CLI_BIN" ]; then
                ln -sf "$CLI_BIN" "$LOCAL_BIN/$cmd"
            elif [ -L "$LOCAL_BIN/$cmd" ]; then
                rm -f "$LOCAL_BIN/$cmd"
                vader_warn "Removed stale symlink $LOCAL_BIN/$cmd (target missing)"
            fi
        done
        # Verify PATH includes ~/.local/bin
        if echo "$PATH" | tr ':' '\n' | grep -qx "$LOCAL_BIN"; then
            vader_success "Command 'guaardvark' is available globally"
        else
            vader_success "CLI installed to $LOCAL_BIN"
            # Make it available for the rest of this script run + spawned children.
            export PATH="$LOCAL_BIN:$PATH"
            # Persist for new shells via ~/.bashrc — idempotent, marker-guarded.
            BASHRC="$HOME/.bashrc"
            BASHRC_MARKER="# Added by guaardvark CLI installer — do not remove without removing the export"
            if [ -f "$BASHRC" ] && ! grep -qF "$BASHRC_MARKER" "$BASHRC"; then
                {
                    echo ""
                    echo "$BASHRC_MARKER"
                    echo "export PATH=\"\$HOME/.local/bin:\$PATH\""
                } >> "$BASHRC"
                vader_success "Added \$HOME/.local/bin to PATH in ~/.bashrc (effective in new shells)"
            elif [ ! -f "$BASHRC" ]; then
                vader_warn "~/.bashrc not found — add manually:  export PATH=\"\$HOME/.local/bin:\$PATH\""
            fi
        fi
    fi
fi

if [ "$FAST_START" -ne 1 ]; then
    # Avoid dependency installs during startup. Running `npm install` here would
    # mutate package-lock.json on every boot and make startup less predictable.

    if [ "$BUILD_CHECK" -eq 1 ]; then
        check_frontend_build
        BUILD_STATUS=$?

        case $BUILD_STATUS in
            0)
                vader_info "Frontend build is up to date"
                ;;
            1)
                if [ "$AUTO_BUILD_FRONTEND" -eq 1 ]; then
                    vader_info "Frontend changes detected - rebuilding..."
                    (cd "$FRONTEND_DIR" && $NPM_CMD run build >> "$SETUP_LOG" 2>&1)
                    vader_success "Frontend rebuilt successfully"
                else
                    vader_warn "Frontend build is stale (src newer than dist). Run: (cd frontend && npm run build)"
                fi
                ;;
            2)
                if [ "$AUTO_BUILD_FRONTEND" -eq 1 ]; then
                    vader_info "Frontend dist missing - building..."
                    (cd "$FRONTEND_DIR" && $NPM_CMD run build >> "$SETUP_LOG" 2>&1)
                    vader_success "Frontend built successfully"
                else
                    vader_warn "Frontend dist missing. Run: (cd frontend && npm run build)"
                fi
                ;;
        esac
    fi
else
    vader_info "Fast start enabled - skipping frontend install/build."
fi
vader_separator

vader_step 6 "Ensuring Ollama service is running..."

# Set up passwordless Ollama + nvidia-smi control (one-time, during first-run setup)
# This runs alongside the first-run setup phase so sudo is already expected
OLLAMA_SUDOERS="/etc/sudoers.d/ollama-guaardvark"
GPU_SUDOERS="/etc/sudoers.d/gpu-guaardvark"

if [ "$OLLAMA_AVAILABLE" -eq 1 ] && [ ! -f "$OLLAMA_SUDOERS" ]; then
    OLLAMA_BIN=$(command -v ollama 2>/dev/null)
    SYSTEMCTL_BIN=$(command -v systemctl 2>/dev/null)
    if [ -n "$SYSTEMCTL_BIN" ] && [ -f "/etc/systemd/system/ollama.service" ]; then
        vader_info "Setting up passwordless Ollama control (one-time)..."
        CURRENT_USER=$(whoami)
        SUDOERS_LINE="$CURRENT_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_BIN start ollama, $SYSTEMCTL_BIN stop ollama, $SYSTEMCTL_BIN restart ollama, $SYSTEMCTL_BIN status ollama, $SYSTEMCTL_BIN is-active ollama"
        echo "$SUDOERS_LINE" | sudo tee "$OLLAMA_SUDOERS" > /dev/null 2>&1
        if [ -f "$OLLAMA_SUDOERS" ]; then
            sudo chmod 440 "$OLLAMA_SUDOERS" 2>/dev/null
            if sudo visudo -c -f "$OLLAMA_SUDOERS" >/dev/null 2>&1; then
                vader_success "Ollama passwordless control configured"
            else
                vader_warn "Sudoers syntax invalid, removing..."
                sudo rm -f "$OLLAMA_SUDOERS" 2>/dev/null
            fi
        else
            vader_info "Could not set up passwordless Ollama (non-critical, will use direct process)"
        fi
    fi
fi

# Set up passwordless nvidia-smi for GPU power management (one-time)
if command_exists nvidia-smi && [ ! -f "$GPU_SUDOERS" ]; then
    NVIDIA_SMI_BIN=$(command -v nvidia-smi 2>/dev/null)
    if [ -n "$NVIDIA_SMI_BIN" ]; then
        vader_info "Setting up passwordless GPU power management (one-time)..."
        CURRENT_USER=$(whoami)
        GPU_SUDOERS_LINE="$CURRENT_USER ALL=(ALL) NOPASSWD: $NVIDIA_SMI_BIN -pl *, $NVIDIA_SMI_BIN -pm *"
        echo "$GPU_SUDOERS_LINE" | sudo tee "$GPU_SUDOERS" > /dev/null 2>&1
        if [ -f "$GPU_SUDOERS" ]; then
            sudo chmod 440 "$GPU_SUDOERS" 2>/dev/null
            if sudo visudo -c -f "$GPU_SUDOERS" >/dev/null 2>&1; then
                vader_success "GPU passwordless power management configured"
            else
                vader_warn "GPU sudoers syntax invalid, removing..."
                sudo rm -f "$GPU_SUDOERS" 2>/dev/null
            fi
        else
            vader_info "Could not set up passwordless GPU control (non-critical)"
        fi
    fi
fi

# ── Ollama daemon tuning env (P1-6) ──
# These MUST be exported BEFORE `ollama serve` is launched below, otherwise the
# user-spawned fallback daemon (Step 4) never inherits them. (The previous block
# at ~line 1465 ran AFTER the launch, so the knobs were dead for the fallback path
# and irrelevant to the systemd path — see scripts/ollama-systemd-dropin.conf for
# that side.) Guarded by GUAARDVARK_OLLAMA_TUNING (default on; set =0 to disable).
# KV-cache q8_0 + flash-attention are GPU wins (harmless-but-pointless on CPU), so
# they are gated on the same nvidia-smi GPU detection start.sh already uses.
if [ "${GUAARDVARK_OLLAMA_TUNING:-1}" != "0" ]; then
    # Derive Ollama server env (NUM_PARALLEL etc.) from the single hardware policy.
    # This centralizes the VRAM-based decision (1 on 16 GB cards to avoid the
    # original 4-parallel CPU-offload bug) and also emits VULKAN/MAX_LOADED etc.
    # Fall back to conservative defaults if policy/venv not ready yet.
    if [ -x "$VENV_DIR/bin/python" ]; then
        _POLICY_OLLAMA="$("$VENV_DIR/bin/python" -m backend.services.hardware_policy ollama_env 2>/dev/null || true)"
        if [ -n "$_POLICY_OLLAMA" ]; then
            # Parse the Environment="OLLAMA_FOO=bar" lines emitted by the policy.
            eval "$(echo "$_POLICY_OLLAMA" | sed -n 's/^Environment="\([^"]*\)"/\1/p' | sed 's/^/export /')"
            vader_info "Ollama tuning: derived from hardware_policy (NUM_PARALLEL=${OLLAMA_NUM_PARALLEL:-?})"
        fi
    fi
    # Safe fallbacks (policy may have already set these).
    export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-1}"
    export OLLAMA_NUM_CTX="${OLLAMA_NUM_CTX:-8192}"
    export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-15m}"
    export OLLAMA_KV_CACHE_TYPE="${OLLAMA_KV_CACHE_TYPE:-q8_0}"
    export OLLAMA_FLASH_ATTENTION="${OLLAMA_FLASH_ATTENTION:-1}"
    vader_info "Ollama tuning: NUM_PARALLEL=${OLLAMA_NUM_PARALLEL} (policy or fallback)"
else
    vader_info "Ollama tuning disabled (GUAARDVARK_OLLAMA_TUNING=0)"
fi

# ── Ollama systemd drop-in installer (P1-6 fix-b) ──
# The preferred launch path below is `sudo systemctl start ollama`, which inherits
# systemd's environment — NOT this shell's. So the exports above don't reach a
# systemd-managed daemon. This step installs (or renders) a [Service] Environment=
# drop-in carrying the policy-derived vars.
#
# Preferred: render from hardware_policy.ollama_env (single source of truth,
# includes VULKAN/MAX_LOADED etc.). Falls back to the static template in
# scripts/ollama-systemd-dropin.conf. The rendered file lives in data/ so the
# committed template remains a safe fallback. Guarded by GUAARDVARK_OLLAMA_TUNING.
OLLAMA_DROPIN_TEMPLATE="$SCRIPT_DIR/scripts/ollama-systemd-dropin.conf"
OLLAMA_DROPIN_RENDERED="$SCRIPT_DIR/data/ollama-dropin.rendered.conf"
OLLAMA_DROPIN_DST="/etc/systemd/system/ollama.service.d/guaardvark-tuning.conf"

if [ "${GUAARDVARK_OLLAMA_TUNING:-1}" != "0" ] && command_exists systemctl; then
    DROPIN_SRC="$OLLAMA_DROPIN_TEMPLATE"
    if [ -x "$VENV_DIR/bin/python" ]; then
        # Try to render from policy (best: includes current NUM_PARALLEL + VULKAN etc.)
        if "$VENV_DIR/bin/python" -m backend.services.hardware_policy ollama_env > "$OLLAMA_DROPIN_RENDERED" 2>/dev/null; then
            # Prepend a header so humans know it came from the policy
            {
                echo "# RENDERED by start.sh from backend.services.hardware_policy — do not edit by hand."
                echo "# Regenerate by re-running start.sh or: python -m backend.services.hardware_policy ollama_env"
                # systemd requires a section header; hardware_policy emits bare
                # Environment= lines. Without this every setting is discarded with
                # "Assignment outside of section" and the tuning silently does nothing.
                echo "[Service]"
                cat "$OLLAMA_DROPIN_RENDERED"
            } > "$OLLAMA_DROPIN_RENDERED.tmp" && mv "$OLLAMA_DROPIN_RENDERED.tmp" "$OLLAMA_DROPIN_RENDERED"
            DROPIN_SRC="$OLLAMA_DROPIN_RENDERED"
        fi
    fi

    if [ -f "$DROPIN_SRC" ]; then
        if [ -f "$OLLAMA_DROPIN_DST" ] && cmp -s "$DROPIN_SRC" "$OLLAMA_DROPIN_DST"; then
            : # already installed and current
        elif sudo -n true 2>/dev/null; then
            if sudo -n install -D -m 0644 "$DROPIN_SRC" "$OLLAMA_DROPIN_DST" 2>/dev/null \
               && sudo -n systemctl daemon-reload 2>/dev/null; then
                vader_success "Ollama systemd tuning drop-in installed (applies on next ollama restart; not force-restarting)"
            else
                vader_info "Could not install Ollama systemd drop-in (non-critical); manual: sudo install -D -m 0644 '$DROPIN_SRC' '$OLLAMA_DROPIN_DST' && sudo systemctl daemon-reload && sudo systemctl restart ollama"
            fi
        else
            vader_info "Ollama systemd tuning available — to apply, run: sudo install -D -m 0644 '$DROPIN_SRC' '$OLLAMA_DROPIN_DST' && sudo systemctl daemon-reload && sudo systemctl restart ollama"
        fi
    fi
fi

# Check if Ollama plugin is enabled. Honors the user_enabled overlay in
# data/plugin_state.json (UI toggle) and falls back to plugin.json default.
OLLAMA_PLUGIN_JSON="$SCRIPT_DIR/plugins/ollama/plugin.json"
OLLAMA_ENABLED=$(plugin_effective_enabled "ollama" "$OLLAMA_PLUGIN_JSON")

if [ "${GUAARDVARK_OLLAMA_EXTERNAL:-0}" = 1 ] && [ "$OLLAMA_ENABLED" != "False" ]; then
    # External Ollama: the user runs it; start.sh only checks that it answers.
    if curl -sf --max-time 3 http://127.0.0.1:11434/ >/dev/null 2>&1; then
        vader_success "Using external Ollama on 127.0.0.1:11434 (GUAARDVARK_OLLAMA_EXTERNAL=1; never started or stopped by these scripts)"
    elif [ "${GUAARDVARK_OLLAMA_OPTIONAL:-0}" = "1" ]; then
        vader_warn "External Ollama is not answering on 127.0.0.1:11434 — continuing because GUAARDVARK_OLLAMA_OPTIONAL=1."
    else
        vader_error "GUAARDVARK_OLLAMA_EXTERNAL=1 but nothing answers on 127.0.0.1:11434. Start your Ollama (ollama serve) and re-run ./start.sh,"
        vader_error "or remove GUAARDVARK_OLLAMA_EXTERNAL from .env to let start.sh manage it."
        exit 1
    fi
elif [ "$OLLAMA_AVAILABLE" -eq 1 ] && [ "$OLLAMA_ENABLED" != "False" ]; then
    # Step 1: Check if already running
    if curl -sf --max-time 3 http://127.0.0.1:11434/ >/dev/null 2>&1; then
        vader_success "Ollama service is already active"
    else
        # Step 2: Kill any zombie process holding the port but not responding
        # Listeners only: a backend with an open client connection to Ollama
        # also shows up on this port — the other install's, when two share a
        # box — and that is not the process to kill.
        OLLAMA_ZOMBIE_PID=$(lsof -ti TCP:11434 -sTCP:LISTEN 2>/dev/null | head -1)
        if [ -n "$OLLAMA_ZOMBIE_PID" ]; then
            vader_info "Killing unresponsive process on port 11434 (PID: $OLLAMA_ZOMBIE_PID)..."
            kill -9 "$OLLAMA_ZOMBIE_PID" 2>/dev/null
            sleep 2
        fi

        vader_info "Starting Ollama service..."
        OLLAMA_STARTED=0

        # Step 3: Try sudo systemctl start ollama (works if sudoers rule exists)
        if command_exists "systemctl" && sudo -n systemctl start ollama >> "$BACKEND_STARTUP_LOG_FILE" 2>&1; then
            for _i in {1..5}; do
                sleep 2
                if curl -sf --max-time 3 http://127.0.0.1:11434/ >/dev/null 2>&1; then
                    OLLAMA_STARTED=1
                    vader_success "Ollama service started (systemctl)"
                    break
                fi
            done
        fi

        # Step 4: Fallback — run ollama serve as user process with PID tracking
        if [ "$OLLAMA_STARTED" -eq 0 ]; then
            vader_info "systemctl failed, starting Ollama directly..."
            nohup ollama serve > "$LOGS_DIR/ollama_serve.log" 2>&1 &
            OLLAMA_PID=$!
            mkdir -p "$SCRIPT_DIR/pids"
            echo "$OLLAMA_PID" > "$SCRIPT_DIR/pids/ollama.pid"
            for _i in {1..8}; do
                sleep 2
                if curl -sf --max-time 3 http://127.0.0.1:11434/ >/dev/null 2>&1; then
                    OLLAMA_STARTED=1
                    vader_success "Ollama process started (direct, PID: $OLLAMA_PID)"
                    break
                fi
            done
        fi

        # Step 5: HARD failure. Ollama is installed AND the plugin is enabled,
        # yet nothing listens on 11434 after systemctl + direct serve. Booting
        # anyway means every chat/RAG call dies with connection-refused (the
        # 24.04 client box boot shipped exactly that). Stop with remediation unless
        # the operator explicitly opts into a degraded boot.
        if [ "$OLLAMA_STARTED" -eq 0 ]; then
            rm -f "$SCRIPT_DIR/pids/ollama.pid" 2>/dev/null
            if [ "${GUAARDVARK_OLLAMA_OPTIONAL:-0}" = "1" ]; then
                vader_warn "Ollama failed to start — continuing because GUAARDVARK_OLLAMA_OPTIONAL=1. Chat/RAG features will be unavailable."
                vader_info "Check $LOGS_DIR/ollama_serve.log or start manually: ollama serve"
            else
                vader_error "Ollama failed to start: nothing is listening on 127.0.0.1:11434 after systemctl AND direct-serve attempts."
                vader_error "Chat and RAG cannot work in this state, so startup is stopping here."
                vader_info "Diagnose:  tail -n 50 $LOGS_DIR/ollama_serve.log ; systemctl status ollama"
                vader_info "Manual:    ollama serve   (then re-run ./start.sh)"
                vader_info "Boot anyway without chat/RAG:  GUAARDVARK_OLLAMA_OPTIONAL=1 ./start.sh  (or disable the Ollama plugin in the UI)"
                exit 1
            fi
        fi
    fi
elif [ "$OLLAMA_ENABLED" = "False" ]; then
    # WARN, not info: with the plugin off, chat and RAG are dead and the backend
    # will log connection-refused against 127.0.0.1:11434 on every boot. This
    # exact state masqueraded as an "Ollama install failure" on the 24.04
    # client box for a full day of debugging.
    vader_warn "Ollama plugin is DISABLED — skipping startup. Chat/RAG will not work."
    vader_info "Re-enable: Settings → Plugins → Ollama (or edit data/plugin_state.json), then re-run ./start.sh"
else
    vader_warn "Ollama CLI not available; skipping service check."
fi

# `ollama pull` with retry/backoff. Single-shot pulls failed on the 24.04
# install because DNS through systemd-resolved (127.0.0.53) times out
# transiently while torch wheels / HF snapshots saturate the link — the embed
# model never landed and RAG was silently dead. 3 attempts, 10s → 30s backoff.
ollama_pull_retry() {
    local model="$1" attempts="${2:-3}" delay=10 i
    for i in $(seq 1 "$attempts"); do
        echo "--- ollama_pull_retry: $model attempt $i/$attempts $(date '+%Y-%m-%d %H:%M:%S') ---" >> "$LOGS_DIR/ollama_bootstrap.log"
        if ollama pull "$model" >> "$LOGS_DIR/ollama_bootstrap.log" 2>&1; then
            return 0
        fi
        if [ "$i" -lt "$attempts" ]; then
            vader_warn "ollama pull $model failed (attempt $i/$attempts) — retrying in ${delay}s (transient DNS/network is common during first-boot downloads)"
            sleep "$delay"
            delay=$((delay * 3))
        fi
    done
    return 1
}

# ── Fresh-install model bootstrap (P0-3) ──
# A fresh box boots with ZERO models → first chat 404s and RAG's
# get_active_embedding_model() throws. start.sh never pulled anything before.
# This step: if Ollama is up and is missing a chat and/or embedding model, pull a
# small hardware-appropriate default of each. Sizes are chosen by RAM + arch:
#   ≤8GB RAM or aarch64  → chat llama3.2:1b   + embed nomic-embed-text
#   otherwise            → chat gemma4:e2b    + embed nomic-embed-text
# gemma4:e2b (5.1B, vision) is the standard default because the agentic screen-
# control system is validated against Gemma4; the small/ARM tier stays text-only
# (a 7.2GB Gemma4 won't fit ≤8GB). hardware_policy.model_tier is the source of
# truth; these inline values are the fallback when that module isn't importable.
# Models are per-machine (Ollama-local, under data/ for this box) — NOT synced via
# the Interconnector, so every node bootstraps its own. Guarded by
# GUAARDVARK_BOOTSTRAP_MODELS (default on; set =0 to skip pulls entirely).
if [ "$OLLAMA_AVAILABLE" -eq 1 ] && [ "$OLLAMA_ENABLED" != "False" ] \
   && [ "${GUAARDVARK_BOOTSTRAP_MODELS:-1}" != "0" ] \
   && curl -sf --max-time 3 http://127.0.0.1:11434/ >/dev/null 2>&1; then

    # Detect RAM(GB) + arch. Prefer hardware_detector's hardware.json if present
    # (authoritative, already arch/RAM-aware); else fall back to uname + meminfo.
    BOOT_RAM_GB=0
    BOOT_ARCH="$(uname -m 2>/dev/null || echo unknown)"
    HW_JSON="${HOME}/.guaardvark/hardware.json"
    if [ -f "$HW_JSON" ] && command_exists python3; then
        _hw=$(python3 -c "import json,sys
try:
    d=json.load(open('$HW_JSON'))
    print(int(d.get('ram',{}).get('total_gb',0) or 0), d.get('arch','') or '')
except Exception:
    print('0','')" 2>/dev/null)
        if [ -n "$_hw" ]; then
            BOOT_RAM_GB=$(echo "$_hw" | awk '{print $1}')
            [ -n "$(echo "$_hw" | awk '{print $2}')" ] && BOOT_ARCH="$(echo "$_hw" | awk '{print $2}')"
        fi
    fi
    if [ "${BOOT_RAM_GB:-0}" -eq 0 ] && [ -r /proc/meminfo ]; then
        _kb=$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null)
        [ -n "$_kb" ] && BOOT_RAM_GB=$(( _kb / 1024 / 1024 ))
    fi

    # Pick defaults by hardware. Try hardware_policy first (single source of truth).
    # Falls back to inline RAM/arch math if the policy module isn't importable yet.
    _MODEL_TIER="$("$VENV_DIR/bin/python" -m backend.services.hardware_policy model_tier 2>/dev/null || true)"
    if [ -n "$_MODEL_TIER" ]; then
        BOOT_CHAT_MODEL=$(echo "$_MODEL_TIER" | cut -f1)
        BOOT_EMBED_MODEL=$(echo "$_MODEL_TIER" | cut -f2)
        vader_info "Model tier from hardware_policy: chat=$BOOT_CHAT_MODEL embed=$BOOT_EMBED_MODEL"
    else
        # Fallback: inline RAM/arch math (runs only when policy module not yet importable).
        if [ "${BOOT_RAM_GB:-0}" -le 8 ] && [ "${BOOT_RAM_GB:-0}" -gt 0 ] || [ "$BOOT_ARCH" = "aarch64" ] || [ "$BOOT_ARCH" = "arm64" ]; then
            BOOT_CHAT_MODEL="${GUAARDVARK_DEFAULT_LLM:-llama3.2:1b}"
            vader_info "Model bootstrap: small-hardware tier (RAM=${BOOT_RAM_GB}GB arch=${BOOT_ARCH})"
        else
            BOOT_CHAT_MODEL="${GUAARDVARK_DEFAULT_LLM:-gemma4:e2b}"
            vader_info "Model bootstrap: standard tier (RAM=${BOOT_RAM_GB}GB arch=${BOOT_ARCH})"
        fi
        BOOT_EMBED_MODEL="${GUAARDVARK_EMBEDDING_MODEL:-nomic-embed-text}"
    fi
    # An explicit choice (.env or the active profile) beats the hardware tier.
    [ -n "${GUAARDVARK_DEFAULT_LLM:-}" ] && BOOT_CHAT_MODEL="$GUAARDVARK_DEFAULT_LLM"
    [ -n "${GUAARDVARK_EMBEDDING_MODEL:-}" ] && BOOT_EMBED_MODEL="$GUAARDVARK_EMBEDDING_MODEL"

    BOOT_LIST="$(timeout 10 ollama list 2>/dev/null || true)"
    # A "chat model" is any non-embed tag. Detect absence of either class.
    if ! echo "$BOOT_LIST" | grep -viE 'embed|minilm' | grep -qE '[a-zA-Z0-9].*:'; then
        vader_info "No chat model found — pulling $BOOT_CHAT_MODEL (one-time, ~minutes)..."
        ollama_pull_retry "$BOOT_CHAT_MODEL" \
            && vader_success "Chat model ready: $BOOT_CHAT_MODEL" \
            || vader_warn "Failed to pull $BOOT_CHAT_MODEL after retries (non-critical; see logs/ollama_bootstrap.log). Pull manually: ollama pull $BOOT_CHAT_MODEL"
    else
        vader_success "Chat model already present"
    fi
    if ! echo "$BOOT_LIST" | grep -qiE 'embed|minilm'; then
        vader_info "No embedding model found — pulling $BOOT_EMBED_MODEL (one-time)..."
        ollama_pull_retry "$BOOT_EMBED_MODEL" \
            && vader_success "Embedding model ready: $BOOT_EMBED_MODEL" \
            || vader_warn "Failed to pull $BOOT_EMBED_MODEL after retries (non-critical; RAG stays disabled until present). Pull manually: ollama pull $BOOT_EMBED_MODEL"
    else
        vader_success "Embedding model already present"
    fi
elif [ "${GUAARDVARK_BOOTSTRAP_MODELS:-1}" = "0" ]; then
    vader_info "Model bootstrap disabled (GUAARDVARK_BOOTSTRAP_MODELS=0)"
fi
vader_separator

# ── ComfyUI detection (on-demand start for video generation) ──
COMFYUI_DIR="${GUAARDVARK_COMFYUI_DIR:-$GUAARDVARK_ROOT/plugins/comfyui/ComfyUI}"
if [ -d "$COMFYUI_DIR" ]; then
    if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8188" 2>/dev/null | grep -q "200"; then
        vader_success "ComfyUI detected and running (port 8188)"
    else
        vader_info "ComfyUI detected at $COMFYUI_DIR (on-demand start for video generation)"
    fi
else
    vader_info "ComfyUI not installed — video generation via ComfyUI unavailable"
fi
vader_separator



vader_step 7 "Checking Whisper.cpp voice processing..."
if [ "$VOICE_CHECK" -eq 0 ]; then
    vader_info "Voice check disabled (--no-voice, or the active profile). Skipping Whisper.cpp build."
else
    WHISPER_DIR="$BACKEND_DIR/tools/voice/whisper.cpp"
    WHISPER_BUILD_DIR="$WHISPER_DIR/build"
    WHISPER_CLI="$WHISPER_BUILD_DIR/bin/whisper-cli"
    # whisper.cpp's shared library name is platform-specific: libwhisper.so* on
    # Linux, libwhisper*.dylib on macOS. Its OUTPUT DIR also varies by CMake version:
    # some emit the shared lib into build/src/, others (the macOS build #41's reporter
    # hit) into build/bin/ alongside whisper-cli. Match either name in either dir so a
    # SUCCESSFUL build isn't misreported as "binary/library not found" (#41).
    _whisper_lib_present() {
        ls "$WHISPER_BUILD_DIR"/bin/libwhisper*.dylib \
           "$WHISPER_BUILD_DIR"/src/libwhisper*.dylib \
           "$WHISPER_BUILD_DIR"/bin/libwhisper.so* \
           "$WHISPER_BUILD_DIR"/src/libwhisper.so* 2>/dev/null | grep -q .
    }

    if [ ! -d "$WHISPER_DIR" ]; then
        vader_warn "Whisper.cpp folder missing. Install via Settings > Voice to enable speech recognition."
        VOICE_AVAILABLE=0
    else
        if [ ! -f "$WHISPER_CLI" ] || ! _whisper_lib_present; then
            if [ -f "$WHISPER_DIR/Makefile" ] || [ -f "$WHISPER_DIR/CMakeLists.txt" ]; then
                vader_info "Whisper.cpp not built. Building from source..."
                if ! command_exists cmake || ! command_exists make || ! command_exists gcc; then
                    if command_exists apt-get; then
                        sudo apt-get update && sudo apt-get install -y cmake build-essential 2>/dev/null || {
                            vader_warn "Cannot install build dependencies. Install via Settings > Voice later."
                            VOICE_AVAILABLE=0
                        }
                    else
                        vader_warn "Build tools (cmake, make, gcc) not found. Install via Settings > Voice later."
                        VOICE_AVAILABLE=0
                    fi
                fi

                if [ "$VOICE_AVAILABLE" -eq 1 ]; then
                    cd "$WHISPER_DIR" || { vader_warn "Failed to cd to Whisper.cpp directory"; cd "$SCRIPT_DIR"; VOICE_AVAILABLE=0; }
                    if [ "$VOICE_AVAILABLE" -eq 1 ]; then
                        vader_info "Building Whisper.cpp from source..."
                        if make build >/dev/null 2>&1 || cmake --build . >/dev/null 2>&1; then
                            if [ -f "$WHISPER_CLI" ] && _whisper_lib_present; then
                                vader_success "Whisper.cpp built successfully"
                            else
                                vader_warn "Whisper.cpp build completed but binary/library not found"
                                VOICE_AVAILABLE=0
                            fi
                        else
                            vader_warn "Whisper.cpp build failed. Install via Settings > Voice later."
                            VOICE_AVAILABLE=0
                        fi
                    fi
                    cd "$SCRIPT_DIR"
                fi
            else
                vader_warn "Whisper.cpp source not found (placeholder only). Install via Settings > Voice to enable speech recognition."
                VOICE_AVAILABLE=0
            fi
        else
            # Linux honors LD_LIBRARY_PATH, macOS honors DYLD_LIBRARY_PATH; set both
            # (each platform ignores the other's) so the readiness probe works on Mac (#41).
            if LD_LIBRARY_PATH="$WHISPER_BUILD_DIR/bin:$WHISPER_BUILD_DIR/src" DYLD_LIBRARY_PATH="$WHISPER_BUILD_DIR/bin:$WHISPER_BUILD_DIR/src" "$WHISPER_CLI" --help >/dev/null 2>&1; then
                vader_success "Whisper.cpp is ready"
            else
                vader_warn "Whisper.cpp binary exists but may not be working properly"
            fi
        fi
    fi
fi
vader_separator

vader_step 8 "Setting up backend..."
cd "$BACKEND_DIR" || { vader_error "Failed to cd to $BACKEND_DIR"; exit 1; }

# Clear stale Python bytecode cache (prevents import errors after file sync)
# Scan entire project (not just backend/) — scripts/, plugins/, cli/ also have Python
PYCACHE_COUNT=$(find "$GUAARDVARK_ROOT" -path "*/venv" -prune -o -path "*/node_modules" -prune -o -type d -name "__pycache__" -print 2>/dev/null | wc -l)
if [ "$PYCACHE_COUNT" -gt 0 ]; then
    find "$GUAARDVARK_ROOT" -path "*/venv" -prune -o -path "*/node_modules" -prune -o -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
    vader_info "Cleared $PYCACHE_COUNT __pycache__ directories"
fi

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    ensure_venv_python_version
    vader_info "Creating Python venv..."
    if ! $PYTHON_CMD -m venv "$VENV_DIR"; then
        vader_error "Failed to create Python venv. Exiting."
        cd "$SCRIPT_DIR"
        exit 1
    fi
fi

cd "$SCRIPT_DIR"

# ---- cluster: hardware profile ------------------------------------------
# Refresh ~/.guaardvark/hardware.json on every boot so the Interconnector has
# a current picture of this box. Cluster routing is off by default; this
# profile is harmless in solo mode (just a file on disk).
#
# We now always set PYTHONPATH explicitly and have a fallback so the detector
# works reliably even if the venv python has limited packages.
ensure_hardware_profile() {
    mkdir -p "$HOME/.guaardvark"
    local wrote=0

    # File-path invocation on purpose (NOT -m): -m imports backend/__init__.py →
    # socketio_events → numpy, so on a half-installed venv the probe crashes with
    # a numpy traceback in setup.log. The detector is pure stdlib.
    if [ -x "$VENV_DIR/bin/python" ]; then
        if "$VENV_DIR/bin/python" "$SCRIPT_DIR/backend/services/hardware_detector.py" \
                --output "$HOME/.guaardvark/hardware.json" >> "$SETUP_LOG" 2>&1; then
            wrote=1
        fi
    fi

    # Fallback to system python3 (works pre-venv)
    if [ "$wrote" -eq 0 ]; then
        if python3 "$SCRIPT_DIR/backend/services/hardware_detector.py" \
                --output "$HOME/.guaardvark/hardware.json" >> "$SETUP_LOG" 2>&1; then
            wrote=1
        fi
    fi

    if [ "$wrote" -eq 1 ] && [ -f "$HOME/.guaardvark/hardware.json" ]; then
        vader_success "Hardware profile refreshed (~/.guaardvark/hardware.json)"
    else
        vader_warn "Hardware profile refresh had issues (non-fatal)"
    fi
}

# Call the (now hardened) hardware profile refresh. We already did an early
# best-effort version in step 2; this one runs after venv work and prefers the venv python.
ensure_hardware_profile

# Export the persistent node_id so the backend knows who it is without
# re-reading hardware.json.
if [ -f "$HOME/.guaardvark/hardware.json" ]; then
    CLUSTER_NODE_ID=$(python3 -c "import json; print(json.load(open('$HOME/.guaardvark/hardware.json'))['node_id'])" 2>/dev/null || echo "")
    export CLUSTER_NODE_ID
fi

vader_info "Setting up frontend..."
cd "$FRONTEND_DIR" || { vader_error "Failed to cd to $FRONTEND_DIR"; exit 1; }

# We now ensure frontend deps are present before any build (using the same
# npm ci strategy as the dep_reconciler). This fixes the "vite not found"
# and "no node_modules" class of failures on first run / after clean.
# The small ensure_npm_package calls below are for specific dev deps only.
ensure_frontend_deps

ensure_npm_package rollup-plugin-polyfill-node

if [ ! -f ".eslintrc.json" ]; then
    cat > .eslintrc.json <<'EOF'
{
  "extends": ["eslint:recommended", "plugin:react/recommended"],
  "parserOptions": {
    "ecmaVersion": "latest",
    "sourceType": "module",
    "ecmaFeatures": { "jsx": true }
  },
  "settings": { "react": { "version": "detect" } },
  "ignorePatterns": ["dist/"]
}
EOF
fi
cd "$SCRIPT_DIR"
vader_separator

vader_step 9 "Starting backend Flask server..."
check_gpu_optimizations
if [ ! -f "$VENV_DIR/bin/activate" ]; then
    vader_error "Backend venv not found. Cannot start Flask."
    cd "$SCRIPT_DIR"
    exit 1
fi

source "$VENV_DIR/bin/activate" || { vader_error "Failed to activate venv for Flask."; cd "$SCRIPT_DIR"; exit 1; }

# Strong post-bootstrap validation (the heart of the fix).
# If the ensure_ steps above did their job, this will pass quickly.
# If something is still wrong we fail here with a clear message instead of
# a confusing ModuleNotFoundError 30 lines later in the app.
_POST_BOOTSTRAP_ERR=$("$VENV_DIR/bin/python" -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
import numpy, flask, celery
import backend.config, backend.models, backend.app
print('Post-bootstrap core imports: OK')
" 2>&1)
_POST_BOOTSTRAP_RC=$?
if [ "$_POST_BOOTSTRAP_RC" -ne 0 ]; then
    vader_error "Post-bootstrap validation failed:"
    printf '%s\n' "$_POST_BOOTSTRAP_ERR"
    vader_info "(Often a missing synced module — not necessarily numpy/flask.)"
    vader_info "See $SETUP_LOG. Run one of:"
    vader_info "  ./scripts/dep_reconciler.py --force"
    vader_info "  ./scripts/system-manager/system-manager repair ."
    vader_info "Then re-run ./start.sh"
    deactivate
    cd "$SCRIPT_DIR"
    exit 1
fi
vader_success "Post-bootstrap validation passed (core Python environment is usable)"

# Quick import validation (catches stale cache / missing symbols after sync)
if [ "$FAST_START" -eq 0 ]; then
    cd "$SCRIPT_DIR"
    PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH" python3 scripts/preflight_check.py --quick >> "$GUAARDVARK_LOG_DIR/preflight.log" 2>&1
    if [ $? -ne 0 ]; then
        # RED and specific, not a soft one-liner: a failed preflight means a
        # known-broken capability (missing torch on a GPU box, unhealed
        # reconcile). Print the actual errors so nobody has to dig in logs.
        vader_error "Preflight check FAILED — the app will start DEGRADED:"
        grep -E "^    - " "$GUAARDVARK_LOG_DIR/preflight.log" | awk '!seen[$0]++' | tail -n 10 | sed 's/^/      /'
        vader_error "Full report: logs/preflight.log"
        vader_info "Continuing startup so you can use unaffected features; fix the above and re-run ./start.sh"
    else
        vader_success "Import validation passed"
    fi
fi

cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"
export FLASK_APP="$FLASK_APP_TARGET"
export GUAARDVARK_ENHANCED_MODE=true
export GUAARDVARK_CONTEXT_PERSISTENCE=true
export GUAARDVARK_RAG_DEBUG=true
export GUAARDVARK_UNIFIED_INDEX=true
export GUAARDVARK_ROOT="$SCRIPT_DIR"
export TZ="America/New_York"
export CUDA_DEVICE_ORDER="PCI_BUS_ID"
export TORCH_CUDNN_V8_API_ENABLED=1
export OLLAMA_NUM_CTX=8192
# OLLAMA_NUM_PARALLEL / OLLAMA_MAX_LOADED_MODELS come from hardware_policy (exported
# in the Ollama tuning step above); a backend-relaunched daemon inherits them.
export OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-1}"

# Must match start_celery.sh: both processes share the card and the allocator policy.
export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True,max_split_size_mb:512,garbage_collection_threshold:0.8"

# GPU power limit policy. The board limit is the operator's call: a limit the
# supply cannot sustain hard-freezes the box mid-generation, and an operator-set
# limit must survive every launch. Precedence, highest first:
#   GUAARDVARK_GPU_POWER_LIMIT=<watts>  explicit target, clamped to [min,max]
#   GUAARDVARK_GPU_POWER_LIMIT=max      raise to the card maximum
#   GUAARDVARK_GPU_POWER_LIMIT=off      leave the limit exactly as found
#   otherwise                           leave the limit as found (factory or operator)
# Read from the environment, or from .env so it survives reboots without touching
# a shell profile.
if command -v nvidia-smi &>/dev/null; then
    if [ -z "${GUAARDVARK_GPU_POWER_LIMIT:-}" ] && [ -f "$SCRIPT_DIR/.env" ]; then
        _envpl=$(grep -E "^GUAARDVARK_GPU_POWER_LIMIT=" "$SCRIPT_DIR/.env" 2>/dev/null | tail -1 | sed "s/^GUAARDVARK_GPU_POWER_LIMIT=//" | tr -d '"'"'"' ')
        [ -n "$_envpl" ] && GUAARDVARK_GPU_POWER_LIMIT="$_envpl"
    fi
    _pl_q() { nvidia-smi --query-gpu="$1" --format=csv,noheader,nounits 2>/dev/null | head -1 | cut -d. -f1; }
    MAX_PL=$(_pl_q power.max_limit)
    CUR_PL=$(_pl_q power.limit)
    DEF_PL=$(_pl_q power.default_limit)
    MIN_PL=$(_pl_q power.min_limit)
    _want="${GUAARDVARK_GPU_POWER_LIMIT:-}"

    if [ "${_want,,}" = "off" ]; then
        vader_info "GPU power limit: left at ${CUR_PL}W (GUAARDVARK_GPU_POWER_LIMIT=off)"
    elif [ "${_want,,}" = "max" ] && [ -n "$MAX_PL" ]; then
        if [ "$MAX_PL" = "$CUR_PL" ]; then
            vader_info "GPU power limit: already at card maximum ${CUR_PL}W"
        elif sudo -n nvidia-smi -pl "$MAX_PL" >/dev/null 2>&1; then
            vader_success "GPU power limit raised: ${CUR_PL}W → ${MAX_PL}W (GUAARDVARK_GPU_POWER_LIMIT=max)"
        else
            vader_warn "GPU power limit: could not raise to ${MAX_PL}W (needs: sudo nvidia-smi -pl ${MAX_PL})"
        fi
    elif [ -n "$_want" ] && [ "$_want" -eq "$_want" ] 2>/dev/null; then
        [ -n "$MIN_PL" ] && [ "$_want" -lt "$MIN_PL" ] && _want="$MIN_PL"
        [ -n "$MAX_PL" ] && [ "$_want" -gt "$MAX_PL" ] && _want="$MAX_PL"
        if [ "$_want" = "$CUR_PL" ]; then
            vader_info "GPU power limit: already ${CUR_PL}W"
        elif sudo -n nvidia-smi -pl "$_want" >/dev/null 2>&1; then
            vader_success "GPU power limit set: ${CUR_PL}W → ${_want}W (GUAARDVARK_GPU_POWER_LIMIT)"
        else
            vader_warn "GPU power limit: could not set ${_want}W (needs: sudo nvidia-smi -pl ${_want})"
        fi
    elif [ -n "$CUR_PL" ] && [ -n "$DEF_PL" ] && [ "$CUR_PL" != "$DEF_PL" ]; then
        vader_info "GPU power limit: respecting operator setting ${CUR_PL}W (factory default ${DEF_PL}W)"
    elif [ -n "$CUR_PL" ]; then
        vader_info "GPU power limit: ${CUR_PL}W (factory default; GUAARDVARK_GPU_POWER_LIMIT=<watts|max|off> to change)"
    fi
    unset -f _pl_q; unset _want _envpl
fi
# Pick up the auth-bearing URLs that start_redis.sh / start_postgres.sh wrote to .env.
# Without this, the `${X:-default}` exports below would set no-auth defaults that win
# over .env (because the Flask app uses `load_dotenv(override=False)`), and every
# subprocess from here on — schema_sync, the Flask app, Celery — would fail to
# authenticate to redis with a misleading "Authentication required" error.
if [ -f "$SCRIPT_DIR/.env" ]; then
    for _key in REDIS_URL CELERY_BROKER_URL CELERY_RESULT_BACKEND; do
        _val=$(grep -E "^${_key}=" "$SCRIPT_DIR/.env" | tail -1 | sed "s/^${_key}=//")
        if [ -n "$_val" ]; then
            export "${_key}=${_val}"
        fi
    done
fi
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
export CELERY_BROKER_URL="${CELERY_BROKER_URL:-redis://localhost:6379/0}"
export CELERY_RESULT_BACKEND="${CELERY_RESULT_BACKEND:-redis://localhost:6379/0}"

vader_info "Initializing enhanced LLM components..."
python3 - << 'EOF' 2>/dev/null
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))
try:
    from backend.config import STORAGE_DIR, UPLOAD_DIR, OUTPUT_DIR, CACHE_DIR
    os.makedirs(STORAGE_DIR, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
except Exception:
    pass
EOF

vader_info "Syncing database schema..."

# Re-source .env to pick up the DATABASE_URL that start_postgres.sh may have written/updated.
# The initial source at the top of start.sh may have had a stale or missing DATABASE_URL.
if [ -f "$SCRIPT_DIR/.env" ]; then
    _db_url=$(grep -E '^DATABASE_URL=' "$SCRIPT_DIR/.env" | tail -1 | sed 's/^DATABASE_URL=//')
    if [ -n "$_db_url" ]; then
        export DATABASE_URL="$_db_url"
    fi
fi

# Schema-first approach: models.py is the source of truth; schema_sync.py reconciles
# tables/columns and stamps Alembic. Prunes stale migration version files left by
# interconnector sync (additive-only — master squash files never auto-delete on clients).
SCHEMA_SYNC_SCRIPT="$GUAARDVARK_ROOT/scripts/schema_sync.py"

if [ -n "$GUAARDVARK_SKIP_MIGRATIONS" ]; then
    vader_info "Database schema sync skipped (GUAARDVARK_SKIP_MIGRATIONS set)"
elif [ -f "$SCHEMA_SYNC_SCRIPT" ]; then
    if migration_fingerprint_matches; then
        vader_success "Database schema: up to date (fingerprint match)"
    else
        sync_output=$("$VENV_DIR/bin/python" "$SCHEMA_SYNC_SCRIPT" 2>&1)
        sync_exit=$?
        if [ $sync_exit -eq 0 ]; then
            sync_msg=$(echo "$sync_output" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message','OK'))" 2>/dev/null || echo "OK")
            vader_success "Database schema: $sync_msg"
            save_migration_fingerprint
        else
            vader_error "Schema sync failed: $sync_output"
            vader_error "Check $SETUP_LOG for details."
            deactivate 2>/dev/null || true
            cd "$SCRIPT_DIR"
            exit 1
        fi
    fi
else
    vader_warn "Schema sync script not found — falling back to db.create_all() in app.py"
fi

# Tell app.py that start.sh already verified schema + stamped Alembic.
export GUAARDVARK_MIGRATIONS_VERIFIED=1

# Belt-and-suspenders: step 1 already stopped prior servers, but if a healthy backend is
# somehow still serving this port (started outside start.sh, or a kill that failed), ADOPT
# it instead of stacking a duplicate. A duplicate would die on EADDRINUSE and overwrite
# pids/backend.pid with its own dead PID — the historical "500-on-relaunch" bug.
BACKEND_ADOPTED=0
if check_backend_health "$FLASK_PORT" 1; then
    existing_pid=$(repo_listener_pids "$FLASK_PORT" | awk '{print $1}')
    if [ -n "$existing_pid" ]; then
        echo "$existing_pid" > "$SCRIPT_DIR/pids/backend.pid"
        vader_success "Backend already healthy on port $FLASK_PORT (adopted PID $existing_pid, skipping launch)"
    else
        vader_success "Backend already healthy on port $FLASK_PORT (adopted, skipping launch)"
    fi
    BACKEND_ADOPTED=1
elif { command_exists ss && ss -tlpn 2>/dev/null | grep -q ":$FLASK_PORT\b"; } \
     || { command_exists lsof && lsof -ti tcp:"$FLASK_PORT" >/dev/null 2>&1; }; then
    # Port is occupied but NOT answering /api/health — a foreign (non-Guaardvark) process.
    # macOS has no `ss`, so fall back to `lsof` (otherwise this guard silently never fires on
    # macOS and the backend just dies on bind with a cryptic "Address already in use").
    if [ "$(uname -s)" = "Darwin" ] && [ "$FLASK_PORT" = "5000" ]; then
        vader_error "Port 5000 is in use — on macOS this is almost always the 'AirPlay Receiver' (System Settings → General → AirDrop & Handoff → AirPlay Receiver)."
        vader_error "The macOS default is 5055; this run asked for 5000 explicitly. Remove FLASK_PORT=5000 from .env (or set another port there), or turn AirPlay Receiver off, then re-run ./start.sh."
    else
        vader_error "Port $FLASK_PORT is in use by a non-Guaardvark process that does not respond to /api/health. Free it or set FLASK_PORT, then retry."
    fi
    deactivate
    cd "$SCRIPT_DIR"
    exit 1
fi

if [ "$BACKEND_ADOPTED" -eq 0 ]; then
    vader_info "Launching Flask backend in background..."
    export GUAARDVARK_ROOT="$SCRIPT_DIR"

    ulimit -n 65535
    vader_info "File descriptor limit set to: $(ulimit -n)"

    nohup env GUAARDVARK_ROOT="$SCRIPT_DIR" FLASK_PORT="$FLASK_PORT" VITE_PORT="$VITE_PORT" GUAARDVARK_MIGRATIONS_VERIFIED="${GUAARDVARK_MIGRATIONS_VERIFIED:-}" "$VENV_DIR/bin/python" -m backend.app >> "$BACKEND_STARTUP_LOG_FILE" 2>&1 &
    BACKEND_PID=$!
    echo "$BACKEND_PID" > "$SCRIPT_DIR/pids/backend.pid"

    sleep 4

    if ! is_port_listening "$FLASK_PORT" 90 "Backend"; then
        vader_error "Backend failed to start listening on port $FLASK_PORT after 90 seconds."
        if [ -f "$BACKEND_STARTUP_LOG_FILE" ]; then
            vader_error "Last 10 lines of startup log:"
            tail -n 10 "$BACKEND_STARTUP_LOG_FILE"
        fi
        # Only kill the process WE spawned, and only if it's still alive — never a healthy
        # adopted/older backend.
        if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
            kill -9 "$BACKEND_PID" > /dev/null 2>&1
        fi
        deactivate
        cd "$SCRIPT_DIR"
        exit 1
    fi

    # Reconcile pids/backend.pid against the REAL listener. socketio.run()/werkzeug runs
    # in-process today (no fork), but a future wrapper (setsid, gunicorn) could fork and
    # leave $! pointing at a parent that exited. The authoritative PID is whatever owns the
    # listening socket under this repo.
    real_pid=$(repo_listener_pids "$FLASK_PORT" | awk '{print $1}')
    if [ -n "$real_pid" ]; then
        echo "$real_pid" > "$SCRIPT_DIR/pids/backend.pid"
        BACKEND_PID="$real_pid"
    fi

    vader_success "Backend is running (PID $(cat "$SCRIPT_DIR/pids/backend.pid" 2>/dev/null))"
fi

deactivate
cd "$SCRIPT_DIR"
vader_separator

vader_step 10 "Starting enhanced Celery workers..."
if [ -f "$SCRIPT_DIR/start_celery.sh" ]; then
    bash "$SCRIPT_DIR/start_celery.sh"
    # Only workers whose cwd is this checkout count — another install on the
    # same machine must neither satisfy this check nor lend its PID.
    CELERY_PID=""
    for _pid in $(pgrep -f "celery -A backend.celery_app.celery worker" 2>/dev/null); do
        _cwd=$(_proc_cwd "$_pid")
        case "$_cwd" in "$SCRIPT_DIR"|"$SCRIPT_DIR"/*) CELERY_PID="$_pid"; break ;; esac
    done
    if [ -n "$CELERY_PID" ]; then
        echo "$CELERY_PID" > "$SCRIPT_DIR/pids/celery.pid"
        vader_success "Enhanced Celery workers started"
    else
        vader_error "Enhanced Celery workers failed to start."
        exit 1
    fi
else
    source "$VENV_DIR/bin/activate" || { vader_error "Failed to activate venv for Celery."; cd "$SCRIPT_DIR"; exit 1; }
    cd "$SCRIPT_DIR"
    export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"
    export GUAARDVARK_ENHANCED_MODE=true
    export GUAARDVARK_ROOT="$SCRIPT_DIR"

    ulimit -n 65535

    # --concurrency=1 --pool=solo: prefork forks workers AFTER the parent may have initialized
    # CUDA, which is the documented leaked-semaphore death class (PIDs 3047360/3065470). solo +
    # single concurrency also upholds the single-GPU invariant (never two GPU tasks at once).
    nohup celery -A backend.celery_app.celery worker --loglevel=info --concurrency=1 --pool=solo >> "$LOGS_DIR/celery.log" 2>&1 &
    CELERY_PID=$!
    echo "$CELERY_PID" > "$SCRIPT_DIR/pids/celery.pid"
    vader_success "Single Celery worker started (PID: $CELERY_PID)"
    deactivate
fi

cd "$SCRIPT_DIR"
vader_separator

vader_step 11 "Launching Frontend..."
cd "$FRONTEND_DIR" || { vader_error "Failed to cd to $FRONTEND_DIR"; exit 1; }

INOTIFY_MIN=524288
# macOS watches files via FSEvents, not inotify — there is no
# /proc/sys/fs/inotify and no fs.inotify sysctl. Skip the whole tune (#41).
INOTIFY_CURRENT=$(cat /proc/sys/fs/inotify/max_user_watches 2>/dev/null || echo 0)
if is_macos; then
    : # no-op on macOS; Vite uses FSEvents
elif [ "$INOTIFY_CURRENT" -lt "$INOTIFY_MIN" ]; then
    vader_info "inotify watchers too low ($INOTIFY_CURRENT). Raising to $INOTIFY_MIN..."
    if sudo -n sysctl -q fs.inotify.max_user_watches=$INOTIFY_MIN 2>/dev/null; then
        vader_success "inotify watchers raised to $INOTIFY_MIN"
        if ! grep -q "max_user_watches" /etc/sysctl.d/50-guaardvark-inotify.conf 2>/dev/null; then
            echo "fs.inotify.max_user_watches=$INOTIFY_MIN" | sudo -n tee /etc/sysctl.d/50-guaardvark-inotify.conf >/dev/null 2>&1
        fi
    else
        vader_warn "Cannot raise inotify watchers (need sudo). Run manually:"
        vader_warn "  echo 'fs.inotify.max_user_watches=$INOTIFY_MIN' | sudo tee /etc/sysctl.d/50-guaardvark-inotify.conf && sudo sysctl -p /etc/sysctl.d/50-guaardvark-inotify.conf"
        vader_warn "Vite dev server may crash without this fix."
    fi
fi

export FLASK_PORT
export VITE_PORT

# ┌──────────────────────────────────────────────────────────────────────────────┐
# │ OPERATOR-CRITICAL — DO NOT REMOVE OR "SIMPLIFY" (operator standing rule, 2026-06-27).
# │ `./start.sh` MUST rebuild the frontend on every boot. This block + the default
# │ AUTO_BUILD_FRONTEND=1 (top of file) exist ON PURPOSE and have been silently
# │ discarded before. If you are an agent reading this: leave it. If it ever vanishes,
# │ the cause is a file-level overwrite (Interconnector sync of start.sh, remote_wins),
# │ NOT a code smell — fix the master copy / sync scope, never delete this.
# └──────────────────────────────────────────────────────────────────────────────┘
# Always build before serving. `vite preview` is a static server over dist/ with no
# watch/HMR — if we skip the build, it happily serves whatever stale bundle is on disk.
# The earlier mtime-gated check is a best-effort optimization; this is the guarantee.
# On build failure the behavior is HONEST, not silent:
#   - if a previous dist exists, we serve it but scream loudly (better than a dead
#     frontend on a local workstation) so the operator knows the code is stale;
#   - if there is NO dist at all, there is nothing to serve — we abort the launch.
FRONTEND_CAN_SERVE=1
# Belt-and-suspenders: make sure node_modules are present before the guaranteed build
ensure_frontend_deps

# LAN awareness for the preview (static) frontend bundle.
# `vite preview` now carries its own proxy + host allowlist (see the `preview:`
# block in frontend/vite.config.js, reconciled from PR #40): relative /api and
# /socket.io requests from a page loaded at a LAN IP are proxied server-side to
# Flask, and WebSockets upgrade through it. So we do NOT bake an absolute
# http://<lan-ip>:<FLASK> into the bundle anymore — that hardcoded a single
# build-time IP and broke on multi-NIC / DHCP-change boxes. Relative URLs through
# the preview proxy are robust to whichever host the client actually reaches.
#
# The one thing the preview server needs is permission to serve a non-localhost
# Host header (otherwise Vite answers "Blocked request"). Opt the detected LAN IP
# into VITE_ALLOWED_HOSTS so the normal start.sh path just works on the LAN, while
# a bare `vite preview`/`vite` run stays localhost-only by default (PR #40 policy).
PRIMARY_LAN_IP=$(get_lan_ips | awk '{print $1}')
# The machine's own short hostname (e.g. "vogon"). vite.config.js also allows this
# and its ".local" form on its own, but we pass it through too so the env-driven
# path is explicit and a pre-set VITE_ALLOWED_HOSTS doesn't accidentally drop it.
SELF_HOSTNAME=$(hostname 2>/dev/null | awk -F. '{print tolower($1)}')
if [ -n "$PRIMARY_LAN_IP" ]; then
    # Honor any pre-set VITE_ALLOWED_HOSTS (.env), else allow the detected LAN IP
    # plus this box's own hostname so reaching it by name also works.
    _default_hosts="$PRIMARY_LAN_IP"
    [ -n "$SELF_HOSTNAME" ] && _default_hosts="${_default_hosts},${SELF_HOSTNAME},${SELF_HOSTNAME}.local"
    export VITE_ALLOWED_HOSTS="${VITE_ALLOWED_HOSTS:-$_default_hosts}"
    vader_info "LAN IP detected (${PRIMARY_LAN_IP}); allowing it in vite preview (VITE_ALLOWED_HOSTS=${VITE_ALLOWED_HOSTS}). Frontend uses relative URLs proxied to Flask."
else
    # No LAN IP found — still allow this box's own hostname so the operator isn't
    # locked out with an opaque browser "403 Forbidden / Blocked request" (#41).
    if [ -n "$SELF_HOSTNAME" ]; then
        export VITE_ALLOWED_HOSTS="${VITE_ALLOWED_HOSTS:-${SELF_HOSTNAME},${SELF_HOSTNAME}.local}"
        vader_info "No private LAN IP detected; allowing localhost + this host (${SELF_HOSTNAME}/.local) in vite preview. A browser '403 Forbidden / Blocked request' means the host you typed isn't allowed — set VITE_ALLOWED_HOSTS=<your-ip-or-name> in .env to add it."
    else
        vader_info "No private LAN IP detected; preview stays localhost-only. A browser '403 Forbidden / Blocked request' means the host you typed isn't allowed — set VITE_ALLOWED_HOSTS=<your-ip-or-name> in .env to enable LAN access."
    fi
fi

vader_info "Building frontend (production) before serving..."
if (cd "$FRONTEND_DIR" && $NPM_CMD run build >> "$FRONTEND_LOG_FILE" 2>&1); then
    vader_success "Frontend build complete"
elif [ -f "$FRONTEND_DIR/dist/index.html" ]; then
    vader_error "Frontend build FAILED — serving the LAST-GOOD (stale) dist. Code is NOT current. Fix the build; see $FRONTEND_LOG_FILE"
else
    vader_error "Frontend build FAILED and no prior dist exists — cannot serve frontend. See $FRONTEND_LOG_FILE"
    FRONTEND_CAN_SERVE=0
fi

if [ "$FRONTEND_CAN_SERVE" -eq 1 ]; then
    # Serve via the Vite DEV server (not `vite preview`). The dev server's
    # `/socket.io` ws:true proxy reliably handles the WebSocket upgrade, which
    # `vite preview` does not — that broke realtime chat (Socket.IO connect_error
    # transport: websocket → no live thinking steps / stuck stop icon). This
    # matches the long-standing serving model (see good_phase backup, whose
    # vite.config had only the dev `server:` block). The build above is kept as a
    # belt-and-suspenders correctness check, but the dev server serves src/ directly.
    vader_info "Launching frontend (Vite dev server, reliable WS proxy) in background..."
    nohup $NPM_CMD run dev -- --host --port=$VITE_PORT >> "$FRONTEND_LOG_FILE" 2>&1 &
    FRONTEND_PID=$!
    echo "$FRONTEND_PID" > "$SCRIPT_DIR/pids/frontend.pid"
    sleep 3
fi

if [ "$FRONTEND_CAN_SERVE" -ne 1 ]; then
    vader_warn "Frontend not launched (no servable dist). Backend continues; fix the build then re-run."
elif ! kill -0 $FRONTEND_PID > /dev/null 2>&1; then
    vader_error "Frontend process exited unexpectedly. Check $FRONTEND_LOG_FILE."
    cd "$SCRIPT_DIR"
else
    if ! is_port_listening "$VITE_PORT" 20 "Frontend"; then
        vader_warn "Frontend failed to start listening on port $VITE_PORT after 20 seconds. Check $FRONTEND_LOG_FILE."
    else
        vader_success "Frontend is running"
    fi
fi
cd "$SCRIPT_DIR"
vader_separator

vader_info "Running health checks..."
if [ "$TEST_MODE" -eq 1 ]; then
    run_health_checks
else
    vader_info "Running basic health checks..."
    # If the backend isn't answering by now, try a single bounded self-heal before reporting
    # failure (covers a backend that died right after launch, e.g. a transient init crash).
    if ! check_backend_health "$FLASK_PORT" 1; then
        relaunch_backend_if_dead "$FLASK_PORT" || true
    fi
    if check_backend_health "$FLASK_PORT" && check_frontend_health "$VITE_PORT" && check_celery_health "$FLASK_PORT"; then
        vader_success "Basic health checks passed!"
        if [ "$VOICE_CHECK" -eq 1 ]; then
            if check_voice_health "$FLASK_PORT"; then
                vader_success "Voice API health check passed!"
            else
                vader_warn "Voice API health check failed (non-critical)."
            fi
        fi
    else
        vader_warn "Some health checks failed. Run with --test for detailed diagnostics."
    fi
fi
vader_separator

if [ "$LAUNCH_BROWSER" -eq 1 ] && [ "$TEST_MODE" -eq 0 ]; then
    vader_info "Launching browser in app mode..."
    if check_frontend_health "$VITE_PORT" 5; then
        launch_browser_app "http://localhost:$VITE_PORT"
    else
        vader_warn "Frontend not ready yet. Skipping browser launch."
        vader_info "You can manually open: http://localhost:$VITE_PORT (or the LAN IP printed below)"
    fi
fi

vader_separator

# ── Start plugins ──
# Plugins with auto_start:true + enabled:true always start.
# --discord forces Discord bot. --plugins forces ALL enabled plugins.

# Plugins that require the agent virtual display (Xvfb + VNC)
DISPLAY_PLUGINS="vision_pipeline"
# Plugins managed elsewhere in start.sh (skip in the loop)
SKIP_PLUGINS="ollama gpu_embedding"

AGENT_DISPLAY_STARTED=0

ensure_agent_display() {
    # Start the agent virtual display if not already running
    if [ "$AGENT_DISPLAY_STARTED" -eq 1 ]; then return 0; fi
    # The agent desktop is Xvfb + x11vnc + XFCE — X11-only. macOS has no X11
    # without XQuartz, so the attempt only emits BSD-sed errors and a bogus
    # "failed to start". Skip cleanly; everything else (chat/voice/API) works (#41).
    if is_macos; then
        vader_info "Agent virtual display (Xvfb/X11) is Linux-only — skipped on macOS. Chat, voice, and the API are unaffected."
        AGENT_DISPLAY_STARTED=1
        return 0
    fi
    AGENT_DISPLAY_SCRIPT="$SCRIPT_DIR/scripts/start_agent_display.sh"
    if [ -x "$AGENT_DISPLAY_SCRIPT" ]; then
        if pgrep -f "Xvfb :${GUAARDVARK_AGENT_DISPLAY:-99}" > /dev/null 2>&1; then
            vader_success "Agent virtual display already running (:${GUAARDVARK_AGENT_DISPLAY:-99})"
            # Even when the display is already up, re-sync the user's browser
            # profile so cookies/logins added since the last boot land in the
            # agent profile. Cheap idempotent operation; skips if agent
            # Firefox is currently running (would corrupt SQLite copies).
            vader_info "Re-syncing browser profile from user account..."
            bash "$AGENT_DISPLAY_SCRIPT" sync 2>&1 | while read line; do vader_info "  $line"; done
            AGENT_DISPLAY_STARTED=1
            return 0
        fi
        vader_info "Starting agent virtual display (Xvfb + VNC)..."
        bash "$AGENT_DISPLAY_SCRIPT" start 2>&1 | while read line; do vader_info "  $line"; done
        if pgrep -f "Xvfb :${GUAARDVARK_AGENT_DISPLAY:-99}" > /dev/null 2>&1; then
            vader_success "Agent virtual display active on :${GUAARDVARK_AGENT_DISPLAY:-99} (VNC port ${GUAARDVARK_AGENT_VNC_PORT:-5999})"
            AGENT_DISPLAY_STARTED=1
        else
            vader_warn "Agent virtual display failed to start"
        fi
    else
        vader_warn "Agent display script not found: scripts/start_agent_display.sh"
    fi
}

plugin_needs_display() {
    local name="$1"
    for dp in $DISPLAY_PLUGINS; do
        [ "$name" = "$dp" ] && return 0
    done
    return 1
}

plugin_should_skip() {
    local name="$1"
    for sp in $SKIP_PLUGINS; do
        [ "$name" = "$sp" ] && return 0
    done
    return 1
}

# Read a single key from plugin.json's `config` block (manifest only — never
# user-overridable). Used for `auto_start` which is purely a manifest hint.
plugin_manifest_flag() {
    local plugin_json="$1"
    local key="$2"
    python3 -c "import json,sys; c=json.load(open(sys.argv[1])).get('config',{}); print('True' if c.get(sys.argv[2], False) else 'False')" "$plugin_json" "$key" 2>/dev/null || echo "False"
}

start_plugin() {
    local plugin_dir="$1"
    local plugin_name=$(basename "$plugin_dir")
    local start_script="$plugin_dir/scripts/start.sh"

    if [ ! -f "$start_script" ]; then
        vader_warn "$plugin_name plugin has no start script"
        return 1
    fi

    # If this plugin needs the display, ensure it's running first
    if plugin_needs_display "$plugin_name"; then
        ensure_agent_display
    fi

    vader_info "Starting $plugin_name plugin..."
    bash "$start_script" 2>&1 | while read line; do vader_info "  $line"; done

    # Health check if plugin has a port
    local port=$(python3 -c "import json; print(json.load(open('$plugin_dir/plugin.json')).get('port',''))" 2>/dev/null)
    if [ -n "$port" ] && [ "$port" != "None" ]; then
        if curl -sf --max-time 5 "http://localhost:$port/health" >/dev/null 2>&1; then
            vader_success "$plugin_name is online (port $port)"
        else
            vader_warn "$plugin_name started but health check pending (port $port)"
        fi
    else
        vader_success "$plugin_name started"
    fi
}

# Agent virtual display (:99 + x11vnc) starts on demand — when the user opens
# Agent Screen in the UI, when agent tools need it, or when a display-dependent
# plugin (vision_pipeline) is started. Not started at boot to save RAM/VNC cost.

PLUGINS_STARTED=0

# Pass 1: Start auto_start plugins (always, no flag needed)
for plugin_dir in "$SCRIPT_DIR"/plugins/*/; do
    plugin_name=$(basename "$plugin_dir")
    plugin_json="$plugin_dir/plugin.json"

    plugin_should_skip "$plugin_name" && continue
    [ ! -f "$plugin_json" ] && continue

    # auto_start is a manifest hint (not user-toggleable); enabled honors the overlay.
    auto_start=$(plugin_manifest_flag "$plugin_json" "auto_start")
    enabled=$(plugin_effective_enabled "$plugin_name" "$plugin_json")
    if [ "$auto_start" = "True" ] && [ "$enabled" = "True" ]; then
        start_plugin "$plugin_dir"
        PLUGINS_STARTED=$((PLUGINS_STARTED + 1))
    fi
done

# Pass 2: Start Discord if --discord flag (even if not auto_start)
if [ "${START_DISCORD:-0}" -eq 1 ]; then
    DISCORD_DIR="$SCRIPT_DIR/plugins/discord"
    if [ -d "$DISCORD_DIR" ] && [ -f "$DISCORD_DIR/scripts/start.sh" ]; then
        # Only start if not already started in pass 1
        DISCORD_AUTO=$(plugin_manifest_flag "$DISCORD_DIR/plugin.json" "auto_start")
        DISCORD_ENABLED=$(plugin_effective_enabled "discord" "$DISCORD_DIR/plugin.json")
        if ! { [ "$DISCORD_AUTO" = "True" ] && [ "$DISCORD_ENABLED" = "True" ]; }; then
            start_plugin "$DISCORD_DIR"
            PLUGINS_STARTED=$((PLUGINS_STARTED + 1))
        fi
    else
        vader_warn "Discord bot plugin not found at plugins/discord/"
    fi
fi

# Pass 3: Start ALL enabled plugins if --plugins (skip already-started auto_start ones)
if [ "${START_ALL_PLUGINS:-0}" -eq 1 ]; then
    for plugin_dir in "$SCRIPT_DIR"/plugins/*/; do
        plugin_name=$(basename "$plugin_dir")
        plugin_json="$plugin_dir/plugin.json"

        plugin_should_skip "$plugin_name" && continue
        [ ! -f "$plugin_json" ] && continue

        enabled=$(plugin_effective_enabled "$plugin_name" "$plugin_json")
        auto_start=$(plugin_manifest_flag "$plugin_json" "auto_start")

        # Skip if already started in pass 1 (auto_start + effective_enabled)
        if [ "$auto_start" = "True" ] && [ "$enabled" = "True" ]; then
            continue
        fi

        if [ "$enabled" = "True" ]; then
            start_plugin "$plugin_dir"
            PLUGINS_STARTED=$((PLUGINS_STARTED + 1))
        fi
    done
fi

if [ "$PLUGINS_STARTED" -gt 0 ]; then
    vader_success "$PLUGINS_STARTED plugin(s) started"
fi

# Write runtime state for CLI auto-discovery
RUNTIME_DIR="$HOME/.guaardvark"
mkdir -p "$RUNTIME_DIR"
cat > "$RUNTIME_DIR/runtime.json" <<RTEOF
{
  "root": "$SCRIPT_DIR",
  "backend_port": $FLASK_PORT,
  "frontend_port": $VITE_PORT,
  "backend_pid": $(cat "$SCRIPT_DIR/pids/backend.pid" 2>/dev/null || echo 0),
  "started_at": "$(date -Iseconds)"
}
RTEOF
vader_success "Runtime state written to $RUNTIME_DIR/runtime.json"

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo ""
vader_header
vader_title "  Guaardvark Startup Script v5.1 Finished (Duration: ${DURATION}s)"
vader_header
echo ""

vader_title "Access URLs:"
echo -e "  ${VADER_WHITE}Frontend:${VADER_RESET} ${VADER_RED}http://localhost:$VITE_PORT${VADER_RESET}"
echo -e "  ${VADER_WHITE}Backend API:${VADER_RESET} ${VADER_RED}http://localhost:$FLASK_PORT${VADER_RESET}"
echo -e "  ${VADER_WHITE}Backend Health:${VADER_RESET} ${VADER_RED}http://localhost:$FLASK_PORT/api/health${VADER_RESET}"
if [ "$VOICE_CHECK" -eq 1 ]; then
echo -e "  ${VADER_WHITE}Voice API Status:${VADER_RESET} ${VADER_RED}http://localhost:$FLASK_PORT/api/voice/status${VADER_RESET}"
fi
echo ""

# LAN / phone / tablet access (the main addition for this feature).
# Printed using the same PRIMARY_LAN_IP we baked into the frontend bundle (if any).
# Users on the same Wi-Fi simply open the Frontend line in Android Chrome (or any browser)
# and use regular chat or the /voice-chat interface to drive agentic tasks.
LAN_IPS_PRINTED=""
if [ -n "$PRIMARY_LAN_IP" ]; then
    LAN_IPS_PRINTED="http://${PRIMARY_LAN_IP}:$VITE_PORT"
    vader_title "LAN / Network Access (phone, tablet, other devices on same Wi-Fi/LAN):"
    echo -e "  ${VADER_WHITE}Frontend (text chat + voice chat):${VADER_RESET} ${VADER_RED}http://${PRIMARY_LAN_IP}:$VITE_PORT${VADER_RESET}"
    echo -e "  ${VADER_WHITE}Backend (direct):${VADER_RESET} ${VADER_RED}http://${PRIMARY_LAN_IP}:$FLASK_PORT${VADER_RESET}"
    echo -e "  ${VADER_GRAY}Open the Frontend URL above from your Android device (same network).${VADER_RESET}"
    echo -e "  ${VADER_GRAY}Grant microphone permission for voice chat. All traffic is local.${VADER_RESET}"
    echo ""
else
    # Fallback: compute at print time in case the build-time one was empty
    _late_lan=$(get_lan_ips | awk '{print $1}')
    if [ -n "$_late_lan" ]; then
        LAN_IPS_PRINTED="http://${_late_lan}:$VITE_PORT"
        vader_title "LAN / Network Access (phone, tablet, other devices on same Wi-Fi/LAN):"
        echo -e "  ${VADER_WHITE}Frontend (text chat + voice chat):${VADER_RESET} ${VADER_RED}http://${_late_lan}:$VITE_PORT${VADER_RESET}"
        echo -e "  ${VADER_WHITE}Backend (direct):${VADER_RESET} ${VADER_RED}http://${_late_lan}:$FLASK_PORT${VADER_RESET}"
        echo -e "  ${VADER_GRAY}Open the Frontend URL above from your Android device (same network).${VADER_RESET}"
        echo -e "  ${VADER_GRAY}Grant microphone permission for voice chat. All traffic is local.${VADER_RESET}"
        echo ""
    fi
fi

vader_title "Log Files:"
echo -e "  ${VADER_GRAY}Backend startup:${VADER_RESET} ${VADER_WHITE}$BACKEND_STARTUP_LOG_FILE${VADER_RESET}"
echo -e "  ${VADER_GRAY}Celery worker:${VADER_RESET} ${VADER_WHITE}$LOGS_DIR/celery.log${VADER_RESET}"
echo -e "  ${VADER_GRAY}Frontend:${VADER_RESET} ${VADER_WHITE}$FRONTEND_LOG_FILE${VADER_RESET}"
echo -e "  ${VADER_GRAY}Setup:${VADER_RESET} ${VADER_WHITE}$SETUP_LOG${VADER_RESET}"
echo ""

vader_title "Management:"
echo -e "  ${VADER_GRAY}Stop services:${VADER_RESET} ${VADER_WHITE}./stop.sh${VADER_RESET}"
echo -e "  ${VADER_GRAY}View logs:${VADER_RESET} ${VADER_WHITE}tail -f $BACKEND_STARTUP_LOG_FILE${VADER_RESET}"
echo -e "  ${VADER_GRAY}Test mode:${VADER_RESET} ${VADER_WHITE}./start.sh --test${VADER_RESET}"
echo -e "  ${VADER_GRAY}Fast start:${VADER_RESET} ${VADER_WHITE}./start.sh --fast${VADER_RESET}"
echo -e "  ${VADER_GRAY}Parallel checks:${VADER_RESET} ${VADER_WHITE}./start.sh --parallel${VADER_RESET}"
echo -e "  ${VADER_GRAY}Skip voice check:${VADER_RESET} ${VADER_WHITE}./start.sh --no-voice${VADER_RESET}"
echo -e "  ${VADER_GRAY}Skip auto-build:${VADER_RESET} ${VADER_WHITE}./start.sh --no-auto-build${VADER_RESET}"
echo -e "  ${VADER_GRAY}Skip migrations:${VADER_RESET} ${VADER_WHITE}./start.sh --skip-migrations${VADER_RESET}"
echo -e "  ${VADER_GRAY}Launch in app mode:${VADER_RESET} ${VADER_WHITE}./start.sh --app-mode${VADER_RESET}"
echo -e "  ${VADER_GRAY}Disable browser launch:${VADER_RESET} ${VADER_WHITE}./start.sh --no-browser${VADER_RESET}"
echo ""

if [ "$TEST_MODE" -eq 1 ]; then
    vader_success "Test mode completed - all systems checked."
else
    vader_info "Run './start.sh --test' for comprehensive health diagnostics."
fi
echo ""

# Advisory GPU-stack verification — never blocks boot.
# Checks that each venv (backend + isolated audio/video) can run a real CUDA
# kernel and that Ollama is not forced into CPU-offload. Writes
# data/gpu_stack_status.json for the health layer / UI. See verify_gpu_stack.sh.
if [ -f "$SCRIPT_DIR/scripts/verify_gpu_stack.sh" ]; then
    bash "$SCRIPT_DIR/scripts/verify_gpu_stack.sh" || true
fi

exit 0
