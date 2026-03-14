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
for arg in "$@"; do
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
      echo "  --app-mode         Launch browser on startup"
      echo "  --no-browser       Do not launch browser"
      echo "  --help, -h         Show this help"
      exit 0
      ;;
    --fast) FAST_START=1 ;;
    --test) TEST_MODE=1 ;;
    --no-voice) VOICE_CHECK=0 ;;
    --parallel) PARALLEL_CHECKS=1 ;;
    --force-ports) FORCE_PORTS=1 ;;
    --no-force-ports) FORCE_PORTS=0 ;;
    --no-build-check) BUILD_CHECK=0 ;;
    --build-frontend) AUTO_BUILD_FRONTEND=1 ;;
    --no-auto-build) AUTO_BUILD_FRONTEND=0 ;;
    --skip-migrations) export GUAARDVARK_SKIP_MIGRATIONS=1 ;;
    --app-mode) LAUNCH_BROWSER=1 ;;
    --no-browser) LAUNCH_BROWSER=0 ;;
  esac
done

if [ -n "$CI" ] || [ -n "$CODEX_ENV" ]; then
  vader_info "CI or Codex environment detected. Exiting start.sh."
  exit 0
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

MANAGER_SCRIPT="$SCRIPT_DIR/scripts/system-manager/system-manager"
if [ -f "$MANAGER_SCRIPT" ]; then
    # Ensure ./manager symlink exists (may be missing after Code Release restore)
    if [ ! -L "$SCRIPT_DIR/manager" ]; then
        ln -sf "scripts/system-manager/system-manager" "$SCRIPT_DIR/manager"
    fi
    if [ -f "$SCRIPT_DIR/backend/venv/bin/flask" ]; then
        if ! "$MANAGER_SCRIPT" check "$SCRIPT_DIR"; then
            vader_warn "Environment issues detected by System Manager."
            vader_info "Auto-repairing environment..."
            "$MANAGER_SCRIPT" repair "$SCRIPT_DIR" || vader_warn "Auto-repair had issues, continuing with startup..."
        fi
    else
        vader_info "Fresh install detected (no venv). Skipping system-manager check."
    fi
fi

PYTHON_CMD="python3"
NPM_CMD="npm"
OLLAMA_SERVICE_NAME="ollama"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
VENV_DIR="$BACKEND_DIR/venv"
WITH_LLM="${WITH_LLM:-0}"
CACHE_DIR="$SCRIPT_DIR/.start_cache"
mkdir -p "$CACHE_DIR"

LOGS_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOGS_DIR"
SETUP_LOG="$LOGS_DIR/setup.log"

if [ -n "$GUAARDVARK_ROOT" ] && [ "$GUAARDVARK_ROOT" != "$SCRIPT_DIR" ]; then
  vader_warn "Ignoring GUAARDVARK_ROOT override ('$GUAARDVARK_ROOT'); using script directory '$SCRIPT_DIR'."
fi
export GUAARDVARK_ROOT="$SCRIPT_DIR"

if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a
  . "$SCRIPT_DIR/.env"
  set +a
  export GUAARDVARK_ROOT="$SCRIPT_DIR"
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

BACKEND_STARTUP_LOG_FILE="$LOGS_DIR/backend_startup.log"
FRONTEND_LOG_FILE="$LOGS_DIR/frontend.log"

FLASK_APP_TARGET="backend.app"
FLASK_PORT="${FLASK_PORT:-5000}"
VITE_PORT="${VITE_PORT:-5173}"
VITE_PROCESS_PATTERN="node.*vite"
FLASK_PROCESS_PATTERN="(python.*backend[./]app|flask run).*$FLASK_PORT"

FLASK_DEBUG_FLAG=""
if [[ " $* " == *" --debug "* ]]; then
  FLASK_DEBUG_FLAG="--debug"
  vader_info "Flask debug mode requested."
fi

command_exists() { command -v "$1" >/dev/null 2>&1; }

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
    if ! command_exists python3; then
        vader_error "python3 not found. Install via: apt-get install -y python3 python3-venv python3-dev python3-pip"
        return 1
    fi
    local ver
    ver=$(python3 --version 2>&1 | awk '{print $2}')
    local major=${ver%%.*}
    local minor=${ver#*.}
    minor=${minor%%.*}
    if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 12 ]; }; then
        vader_error "Python >=3.12 required. Install via: apt-get install -y python3 python3-venv python3-dev python3-pip"
        return 1
    fi
    if [ "$major" -eq 3 ] && [ "$minor" -ge 13 ]; then
        return 0
    fi
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
        vader_warn "Firefox not found. Cannot launch in app mode."
        vader_info "Install Firefox: sudo apt-get install -y firefox"
        return 1
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
                local proc_cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null)
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
                local proc_cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null)
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
            owner_cwd=$(readlink -f "/proc/$owner_pid/cwd" 2>/dev/null)
        fi
    elif command_exists ss; then
        owner_pid=$(ss -tlpn 2>/dev/null | awk -v p=":$port" '$4 ~ p {print $NF}' | sed 's/users://;s/"//g' | cut -d',' -f2 | cut -d'=' -f2 | head -1)
        if [ -n "$owner_pid" ]; then
            owner_cwd=$(readlink -f "/proc/$owner_pid/cwd" 2>/dev/null)
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
    dist_mtime=$(stat -c %Y "$dist_index" 2>/dev/null)

    if [ -n "$src_mtime" ] && [ -n "$dist_mtime" ]; then
        src_mtime=${src_mtime%.*}
        if [ "$src_mtime" -gt "$dist_mtime" ]; then
            return 1
        fi
    fi

    return 0
}

compute_migration_fingerprint() {
    local fingerprint_inputs=""
    if [ -f "$BACKEND_DIR/models.py" ]; then
        fingerprint_inputs+=$(md5sum "$BACKEND_DIR/models.py" 2>/dev/null | cut -d' ' -f1)
    fi
    if [ -d "$BACKEND_DIR/migrations/versions" ]; then
        fingerprint_inputs+=$(find "$BACKEND_DIR/migrations/versions" -name '*.py' -printf '%f:%T@\n' 2>/dev/null | sort | md5sum | cut -d' ' -f1)
    fi
    if [ -f "$BACKEND_DIR/migrations/env.py" ]; then
        fingerprint_inputs+=$(md5sum "$BACKEND_DIR/migrations/env.py" 2>/dev/null | cut -d' ' -f1)
    fi
    echo "$fingerprint_inputs" | md5sum | cut -d' ' -f1
}

save_migration_fingerprint() {
    local fp_file="$GUAARDVARK_ROOT/pids/.migration_fingerprint"
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
    local check_script="$GUAARDVARK_ROOT/scripts/check_migrations.py"

    if [ -n "$GUAARDVARK_SKIP_MIGRATIONS" ]; then
        vader_info "Migration check skipped (GUAARDVARK_SKIP_MIGRATIONS set)"
        return 0
    fi

    if [ ! -d "$BACKEND_DIR/migrations" ]; then
        vader_info "No migrations directory found - skipping check"
        return 0
    fi

    if migration_fingerprint_matches; then
        vader_success "Migration check: up to date (no changes since last check)"
        return 0
    fi

    if [ ! -f "$check_script" ]; then
        vader_warn "Migration check script not found - skipping"
        return 0
    fi

    local output
    local exit_code
    output=$("$VENV_DIR/bin/python" "$check_script" 2>&1)
    exit_code=$?

    case $exit_code in
        0)
            local msg
            msg=$(echo "$output" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message','OK'))" 2>/dev/null || echo "OK")
            vader_success "Migration check: $msg"
            save_migration_fingerprint
            return 0
            ;;
        1)
            local err_msg fix_msg
            err_msg=$(echo "$output" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message','Multiple heads'))" 2>/dev/null || echo "Multiple migration heads detected")
            fix_msg=$(echo "$output" | python3 -c "import sys,json; print(json.load(sys.stdin).get('fix',''))" 2>/dev/null || echo "")
            vader_error "Migration check failed: $err_msg"
            [ -n "$fix_msg" ] && vader_error "Fix: $fix_msg"
            return 1
            ;;
        2)
            vader_warn "Pending migrations detected - attempting auto-upgrade..."
            if "$VENV_DIR/bin/python" -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from backend.utils.migration_utils import auto_upgrade
result = auto_upgrade('$BACKEND_DIR/migrations')
if not result['success']:
    print(result['message'], file=sys.stderr)
    sys.exit(1)
print(result['message'])
" >> "$SETUP_LOG" 2>&1; then
                vader_success "Migrations applied successfully"
                save_migration_fingerprint
                return 0
            else
                vader_error "Migration auto-upgrade failed."
                vader_error "Your data is preserved — NOT deleting the database."
                vader_error "Manual fix options:"
                vader_error "  1. cd backend && source venv/bin/activate && flask db upgrade"
                vader_error "  2. Restore from backup via the Guaardvark UI"
                vader_error "  3. Set GUAARDVARK_SKIP_MIGRATIONS=1 to bypass (risky)"
                vader_error "Check $SETUP_LOG for details."
                return 1
            fi
            ;;
        5)
            # Model changes detected by Alembic autogenerate. Since db.create_all()
            # already creates the schema from models.py, these are typically phantom
            # differences (column ordering, defaults, etc.) — safe to ignore.
            vader_info "Minor model metadata differences detected (safe to ignore)."
            save_migration_fingerprint
            return 0
            ;;
        *)
            local err_msg
            err_msg=$(echo "$output" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message','Unknown error'))" 2>/dev/null || echo "$output")
            vader_error "Migration check failed: $err_msg"
            return 1
            ;;
    esac
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

check_ollama_model() {
    if [ "$OLLAMA_AVAILABLE" -eq 0 ]; then
        return 0
    fi
    local model_name="llama2"
    timeout 5 ollama list 2>/dev/null | grep -q "$model_name"
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

ensure_pip_requirements() {
    local req_file="$1"
    [ -f "$req_file" ] || return 0
    
    if [ "$FAST_START" -eq 1 ]; then
        return 0
    fi
    
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%%#*}"
        line="${line%% *}"
        [ -z "$line" ] && continue
        local pkg="$line"
        pkg="${pkg%%==*}"
        pkg="${pkg%%>=*}"
        pkg="${pkg%%<=*}"
        pkg="${pkg%%>*}"
        pkg="${pkg%%<*}"
        pkg="${pkg%%!=*}"
        pkg="${pkg%%~=*}"
        if ! pip show "$pkg" >/dev/null 2>&1; then
            pip install "$line" >> "$SETUP_LOG" 2>&1
        fi
    done < "$req_file"
}

ensure_npm_package() {
    local pkg="$1"
    npm ls --prefix "$FRONTEND_DIR" --depth=0 "$pkg" >/dev/null 2>&1 || \
        (cd "$FRONTEND_DIR" && npm install --save-dev "$pkg" >> "$SETUP_LOG" 2>&1)
}

vader_header
vader_title "  Guaardvark Startup Script v5.1 - Smart Install Mode"
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
    "$SCRIPT_DIR/stop.sh" >/dev/null 2>&1
fi

kill_process "$FLASK_PORT" "Flask backend" "$FLASK_PROCESS_PATTERN" &
kill_process "$VITE_PORT" "Vite frontend" "$VITE_PROCESS_PATTERN" &
wait

if [ -f "$SCRIPT_DIR/pids/celery.pid" ]; then
    celery_pid=$(cat "$SCRIPT_DIR/pids/celery.pid" 2>/dev/null)
    if [ -n "$celery_pid" ] && kill -0 "$celery_pid" 2>/dev/null; then
        proc_cwd=$(readlink -f "/proc/$celery_pid/cwd" 2>/dev/null)
        if [ -n "$proc_cwd" ] && [[ "$proc_cwd" == "$SCRIPT_DIR"* ]]; then
            kill -15 "$celery_pid" 2>/dev/null
            sleep 2
            if kill -0 "$celery_pid" 2>/dev/null; then
                kill -9 "$celery_pid" 2>/dev/null
            fi
        fi
    fi
fi

celery_pids=$(pgrep -f "celery.*worker" 2>/dev/null)
if [ -n "$celery_pids" ]; then
    for pid in $celery_pids; do
        proc_cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null)
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

vader_step 2 "Checking environment dependencies..."
if ! check_with_cache "python_check" check_python_version; then
    vader_error "Python 3.12+ required. Exiting."
    exit 1
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
vader_separator

vader_step 3 "Setting up Python environment..."
FIRST_SETUP_DONE=1
if [ ! -d "$VENV_DIR" ]; then
    FIRST_SETUP_DONE=0
    vader_info "Creating Python venv at $VENV_DIR"
    $PYTHON_CMD -m venv "$VENV_DIR" || { vader_error "Failed to create venv"; exit 1; }
fi

source "$VENV_DIR/bin/activate" || { vader_error "Failed to activate venv"; exit 1; }

if [ ! -f "$VENV_DIR/.deps_installed" ] && [ "$FAST_START" -ne 1 ]; then
    FIRST_SETUP_DONE=0
    vader_info "Installing base backend requirements..."
    
    if [ -f "$BACKEND_DIR/requirements-base.txt" ]; then
        pip install -r "$BACKEND_DIR/requirements-base.txt" >> "$SETUP_LOG" 2>&1
    else
        pip install -r "$BACKEND_DIR/requirements.txt" >> "$SETUP_LOG" 2>&1
    fi
    
    vader_info "Detecting GPU and installing PyTorch..."
    if [ -f "$GUAARDVARK_ROOT/scripts/install_pytorch.sh" ]; then
        bash "$GUAARDVARK_ROOT/scripts/install_pytorch.sh" 2>&1 | tee -a "$SETUP_LOG"
    else
        vader_warn "PyTorch install script not found - using CPU fallback"
        pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu 2>&1 | tee -a "$SETUP_LOG"
    fi
    
    NP_MAJOR=$($PYTHON_CMD - <<'EOF'
import numpy, sys
print(numpy.__version__.split('.')[0])
EOF
    )
    if [ "$NP_MAJOR" -ge 2 ]; then
        vader_warn "NumPy $NP_MAJOR.x detected; forcing reinstall of pinned packages."
        if [ -f "$BACKEND_DIR/requirements-base.txt" ]; then
            pip install --force-reinstall -r "$BACKEND_DIR/requirements-base.txt" >> "$SETUP_LOG" 2>&1
        else
            pip install --force-reinstall -r "$BACKEND_DIR/requirements.txt" >> "$SETUP_LOG" 2>&1
        fi
    fi

    # Verify critical packages that pip dependency resolution may silently drop
    # Use version pins to ensure correct versions (not just latest)
    declare -A CRITICAL_PACKAGES=(
        ["duckduckgo-search"]="duckduckgo-search==8.1.1"
        ["flask"]="Flask==3.0.0"
        ["celery"]="celery==5.4.0"
        ["redis"]="redis==5.0.4"
        ["llama-index-core"]="llama-index-core>=0.13.0,<0.15.0"
        ["lxml"]="lxml==6.0.2"
    )
    for pkg in "${!CRITICAL_PACKAGES[@]}"; do
        if ! pip show "$pkg" >/dev/null 2>&1; then
            vader_warn "Critical package $pkg missing after requirements install — installing individually..."
            pip install "${CRITICAL_PACKAGES[$pkg]}" >> "$SETUP_LOG" 2>&1
        fi
    done

    touch "$VENV_DIR/.deps_installed"

    if command_exists "nvidia-smi"; then
        nvidia-smi --query-gpu=uuid --format=csv,noheader 2>/dev/null | head -1 > "$VENV_DIR/.gpu_hw_id"
    fi
fi
deactivate

# --- CLI tool setup ---
CLI_DIR="$SCRIPT_DIR/cli"
CLI_VENV_DIR="$CLI_DIR/venv"
if [ -d "$CLI_DIR" ] && [ -f "$CLI_DIR/setup.py" ]; then
    if [ ! -d "$CLI_VENV_DIR" ]; then
        vader_info "Creating CLI venv..."
        $PYTHON_CMD -m venv "$CLI_VENV_DIR" || vader_warn "Failed to create CLI venv"
    fi
    if [ -d "$CLI_VENV_DIR" ]; then
        source "$CLI_VENV_DIR/bin/activate"
        if [ ! -f "$CLI_VENV_DIR/.deps_installed" ] && [ "$FAST_START" -ne 1 ]; then
            vader_info "Installing CLI tool (guaardvark/llx)..."
            pip install --upgrade pip setuptools >> "$SETUP_LOG" 2>&1
            pip install -e "$CLI_DIR" >> "$SETUP_LOG" 2>&1
            if [ $? -eq 0 ]; then
                touch "$CLI_VENV_DIR/.deps_installed"
                vader_success "CLI tool installed"
            else
                vader_warn "CLI tool installation failed - check $SETUP_LOG"
            fi
        elif [ "$FAST_START" -ne 1 ]; then
            # Re-check if requirements changed
            if [ "$CLI_DIR/requirements.txt" -nt "$CLI_VENV_DIR/.deps_installed" ] || \
               [ "$CLI_DIR/setup.py" -nt "$CLI_VENV_DIR/.deps_installed" ]; then
                vader_info "CLI dependencies changed - reinstalling..."
                pip install -e "$CLI_DIR" >> "$SETUP_LOG" 2>&1
                touch "$CLI_VENV_DIR/.deps_installed"
            fi
        fi
        deactivate

        # Symlink CLI commands into ~/.local/bin so they work system-wide
        LOCAL_BIN="$HOME/.local/bin"
        mkdir -p "$LOCAL_BIN"
        for cmd in guaardvark llx; do
            CLI_BIN="$CLI_VENV_DIR/bin/$cmd"
            if [ -f "$CLI_BIN" ]; then
                ln -sf "$CLI_BIN" "$LOCAL_BIN/$cmd"
            fi
        done
        # Verify PATH includes ~/.local/bin
        if echo "$PATH" | tr ':' '\n' | grep -qx "$LOCAL_BIN"; then
            vader_success "Commands 'guaardvark' and 'llx' are available globally"
        else
            vader_success "CLI installed to $LOCAL_BIN"
            vader_warn "$LOCAL_BIN is not in PATH. Add to ~/.bashrc:  export PATH=\"\$HOME/.local/bin:\$PATH\""
        fi
    fi
fi

if [ "$FAST_START" -ne 1 ]; then
    vader_info "Installing frontend dependencies..."
    (cd "$FRONTEND_DIR" && $NPM_CMD install >> "$SETUP_LOG" 2>&1)

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

vader_step 4 "Ensuring Ollama service is running..."
if [ "$OLLAMA_AVAILABLE" -eq 1 ]; then
    if check_service_status "$OLLAMA_SERVICE_NAME" 2>/dev/null || curl -sf http://localhost:11434/ >/dev/null 2>&1; then
        vader_success "Ollama service is already active"
    else
        vader_info "Starting Ollama service..."
        OLLAMA_STARTED=0

        # Try 1: systemctl (system-level via sudo, then user-level)
        if command_exists "systemctl"; then
            if start_service "$OLLAMA_SERVICE_NAME"; then
                sleep 3
                if curl -sf http://localhost:11434/ >/dev/null 2>&1; then
                    OLLAMA_STARTED=1
                    vader_success "Ollama service started (systemctl)"
                fi
            fi
        fi

        # Try 2: direct ollama serve (fallback when systemctl fails — e.g. no passwordless sudo)
        if [ "$OLLAMA_STARTED" -eq 0 ]; then
            vader_info "systemctl failed, starting Ollama directly..."
            nohup ollama serve > "$LOGS_DIR/ollama_serve.log" 2>&1 &
            sleep 3
            if curl -sf http://localhost:11434/ >/dev/null 2>&1; then
                OLLAMA_STARTED=1
                vader_success "Ollama process started (direct)"
            fi
        fi

        if [ "$OLLAMA_STARTED" -eq 0 ]; then
            vader_error "Failed to start Ollama. Check $LOGS_DIR/ollama_serve.log. Exiting."
            exit 1
        fi
    fi
else
    vader_warn "Ollama CLI not available; skipping service check."
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

vader_step 5 "Ensuring Redis service is running..."
"$(dirname "$0")/start_redis.sh" || { vader_error "Redis failed to start"; exit 1; }
vader_separator

vader_step 6 "Ensuring PostgreSQL database is ready..."
"$(dirname "$0")/start_postgres.sh" || { vader_error "PostgreSQL setup failed"; exit 1; }
vader_separator

vader_step 7 "Checking Whisper.cpp voice processing..."
if [ "$VOICE_CHECK" -eq 0 ]; then
    vader_info "Voice check disabled (--no-voice). Skipping Whisper.cpp build."
else
    WHISPER_DIR="$BACKEND_DIR/tools/voice/whisper.cpp"
    WHISPER_BUILD_DIR="$WHISPER_DIR/build"
    WHISPER_CLI="$WHISPER_BUILD_DIR/bin/whisper-cli"
    WHISPER_LIB="$WHISPER_BUILD_DIR/src/libwhisper.so.1"

    if [ ! -d "$WHISPER_DIR" ]; then
        vader_warn "Whisper.cpp folder missing. Install via Settings > Voice to enable speech recognition."
        VOICE_AVAILABLE=0
    else
        if [ ! -f "$WHISPER_CLI" ] || [ ! -f "$WHISPER_LIB" ]; then
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
                            if [ -f "$WHISPER_CLI" ] && [ -f "$WHISPER_LIB" ]; then
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
            if LD_LIBRARY_PATH="$WHISPER_BUILD_DIR/src" "$WHISPER_CLI" --help >/dev/null 2>&1; then
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
PYCACHE_COUNT=$(find "$BACKEND_DIR" -path "*/venv" -prune -o -type d -name "__pycache__" -print 2>/dev/null | wc -l)
if [ "$PYCACHE_COUNT" -gt 0 ]; then
    find "$BACKEND_DIR" -path "*/venv" -prune -o -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
    vader_info "Cleared $PYCACHE_COUNT __pycache__ directories"
fi

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    vader_info "Creating Python venv..."
    if ! $PYTHON_CMD -m venv "$VENV_DIR"; then
        vader_error "Failed to create Python venv. Exiting."
        cd "$SCRIPT_DIR"
        exit 1
    fi
fi

vader_info "Activating Python venv..."
source "$VENV_DIR/bin/activate" || { vader_error "Failed to activate venv."; cd "$SCRIPT_DIR"; exit 1; }

SENTINEL="$VENV_DIR/.deps_installed"

if [ -f "$SENTINEL" ] && [ "$BACKEND_DIR/requirements.txt" -nt "$SENTINEL" ]; then
    vader_info "requirements.txt changed - updating backend dependencies..."
    if [ -f "$BACKEND_DIR/requirements-base.txt" ]; then
        pip install -r "$BACKEND_DIR/requirements-base.txt" >> "$SETUP_LOG" 2>&1
    else
        pip install -r "$BACKEND_DIR/requirements.txt" >> "$SETUP_LOG" 2>&1
    fi
    touch "$SENTINEL"

    # Verify critical packages that pip dependency resolution may silently drop
    # Use version pins to ensure correct versions (not just latest)
    declare -A CRITICAL_PACKAGES_2=(
        ["duckduckgo-search"]="duckduckgo-search==8.1.1"
        ["flask"]="Flask==3.0.0"
        ["celery"]="celery==5.4.0"
        ["redis"]="redis==5.0.4"
        ["llama-index-core"]="llama-index-core>=0.13.0,<0.15.0"
        ["lxml"]="lxml==6.0.2"
    )
    for pkg in "${!CRITICAL_PACKAGES_2[@]}"; do
        if ! pip show "$pkg" >/dev/null 2>&1; then
            vader_warn "Critical package $pkg missing after requirements install — installing individually..."
            pip install "${CRITICAL_PACKAGES_2[$pkg]}" >> "$SETUP_LOG" 2>&1
        fi
    done
fi

if [ -f "$BACKEND_DIR/requirements-base.txt" ]; then
    ensure_pip_requirements "$BACKEND_DIR/requirements-base.txt"
else
    ensure_pip_requirements "$BACKEND_DIR/requirements.txt"
fi

if command_exists "nvidia-smi"; then
    CURRENT_GPU=$(nvidia-smi --query-gpu=uuid --format=csv,noheader 2>/dev/null | head -1)
    if [ -n "$CURRENT_GPU" ]; then
        if [ -f "$VENV_DIR/.gpu_hw_id" ]; then
            SAVED_GPU=$(cat "$VENV_DIR/.gpu_hw_id" 2>/dev/null)
            if [ "$CURRENT_GPU" != "$SAVED_GPU" ]; then
                vader_info "GPU hardware change detected. Reinstalling PyTorch..."
                bash "$GUAARDVARK_ROOT/scripts/install_pytorch.sh" >> "$SETUP_LOG" 2>&1
                echo "$CURRENT_GPU" > "$VENV_DIR/.gpu_hw_id"
            fi
        else
            echo "$CURRENT_GPU" > "$VENV_DIR/.gpu_hw_id"
        fi
    fi
fi

if [ ! -f "$SENTINEL" ]; then
    vader_error "Backend dependencies missing. Exiting."
    deactivate
    cd "$SCRIPT_DIR"
    exit 1
fi

vader_info "Verifying LLM-related Python modules..."
LLM_MODULES=(llama_index.core)
MISSING_LLM_MODULES=()
for mod in "${LLM_MODULES[@]}"; do
    python - <<EOF >> /dev/null 2>&1
import sys
try:
    import $mod
    sys.exit(0)
except ImportError:
    sys.exit(1)
EOF
    if [ $? -ne 0 ]; then
        MISSING_LLM_MODULES+=("$mod")
    fi
done

if [ ${#MISSING_LLM_MODULES[@]} -gt 0 ]; then
    vader_warn "Missing LLM modules: ${MISSING_LLM_MODULES[*]}"
    vader_info "Auto-installing LLM requirements..."
    ensure_pip_requirements "$BACKEND_DIR/requirements.txt"
fi

if [ "$WITH_LLM" -eq 1 ]; then
    ensure_pip_requirements "$BACKEND_DIR/requirements.txt"
fi

vader_info "Deactivating venv for now."
deactivate
cd "$SCRIPT_DIR"

vader_info "Setting up frontend..."
cd "$FRONTEND_DIR" || { vader_error "Failed to cd to $FRONTEND_DIR"; exit 1; }

if [ ! -d node_modules ]; then
    vader_info "Installing frontend dependencies..."
    $NPM_CMD install >> "$SETUP_LOG" 2>&1
else
    if [ "package.json" -nt "node_modules" ] || [ "package-lock.json" -nt "node_modules" ]; then
        vader_info "package.json changed - updating frontend dependencies..."
        $NPM_CMD install >> "$SETUP_LOG" 2>&1
    elif ! $NPM_CMD ls >/dev/null 2>&1; then
        vader_info "Updating frontend dependencies..."
        $NPM_CMD install >> "$SETUP_LOG" 2>&1
    fi
fi

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

# Quick import validation (catches stale cache / missing symbols after sync)
if [ "$FAST_START" -eq 0 ]; then
    cd "$SCRIPT_DIR"
    PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH" python3 scripts/preflight_check.py --quick >> "$GUAARDVARK_LOG_DIR/preflight.log" 2>&1
    if [ $? -ne 0 ]; then
        vader_warn "Preflight check found import errors — see logs/preflight.log"
        vader_info "Attempting to continue anyway..."
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
export OLLAMA_NUM_PARALLEL=4

export OLLAMA_NUM_CTX=8192

GPU_VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || echo 0)
if [ "${GPU_VRAM_MB:-0}" -gt 12000 ]; then
    export OLLAMA_MAX_LOADED_MODELS=2
    vader_info "OLLAMA_MAX_LOADED_MODELS=2 (${GPU_VRAM_MB}MB VRAM detected)"
elif [ "${GPU_VRAM_MB:-0}" -gt 0 ]; then
    export OLLAMA_MAX_LOADED_MODELS=1
    vader_info "OLLAMA_MAX_LOADED_MODELS=1 (${GPU_VRAM_MB}MB VRAM — small GPU)"
else
    export OLLAMA_MAX_LOADED_MODELS=1
    vader_info "OLLAMA_MAX_LOADED_MODELS=1 (no GPU detected)"
fi

export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True,max_split_size_mb:512,garbage_collection_threshold:0.8"
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

vader_info "Running pre-flight migration check..."

# Re-source .env to pick up the DATABASE_URL that start_postgres.sh may have written/updated.
# The initial source at the top of start.sh may have had a stale or missing DATABASE_URL.
if [ -f "$SCRIPT_DIR/.env" ]; then
    _db_url=$(grep -E '^DATABASE_URL=' "$SCRIPT_DIR/.env" | tail -1 | sed 's/^DATABASE_URL=//')
    if [ -n "$_db_url" ]; then
        export DATABASE_URL="$_db_url"
    fi
fi

# Migration shortcut: handles fresh installs and post-squash re-stamps.
# Uses SQLAlchemy to connect to PostgreSQL (no sqlite3).
# Returns: 0 = fresh install (no tables), 2 = stale revision (needs re-stamp), 1 = normal flow
if migration_fingerprint_matches; then
    vader_success "Database migrations: up to date (fingerprint match)"
    MIGRATION_CHECK=1  # normal flow, skip re-stamp/fresh-install paths
else
"$VENV_DIR/bin/python" -c "
import os, sys
sys.path.insert(0, '$SCRIPT_DIR')
os.environ.setdefault('GUAARDVARK_ROOT', '$SCRIPT_DIR')
from sqlalchemy import create_engine, inspect, text
from backend.config import DATABASE_URL

try:
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        if not tables or 'clients' not in tables:
            sys.exit(0)  # Fresh install — no core tables
        if 'alembic_version' not in tables:
            sys.exit(2)  # Tables exist but no alembic tracking — re-stamp
        rows = conn.execute(text('SELECT version_num FROM alembic_version')).fetchall()
        if not rows:
            sys.exit(2)  # Empty alembic_version — re-stamp
        # Check if current revision exists in migration files
        from pathlib import Path
        versions_dir = Path('$BACKEND_DIR/migrations/versions')
        known_revisions = set()
        for py_file in versions_dir.glob('*.py'):
            content = py_file.read_text()
            for line in content.splitlines():
                if line.strip().startswith('revision'):
                    rev = line.split('=')[1].strip().strip(\"'\").strip('\"')
                    known_revisions.add(rev)
        if rows[0][0] not in known_revisions:
            sys.exit(2)  # Stale revision
        sys.exit(1)  # Normal flow
except Exception:
    sys.exit(1)  # Fall through to normal migration check
" 2>/dev/null
MIGRATION_CHECK=$?
fi

if [ "$MIGRATION_CHECK" = "0" ]; then
    vader_info "Fresh install detected — creating database from models..."
    if "$VENV_DIR/bin/python" -c "
import sys, os
sys.path.insert(0, '$SCRIPT_DIR')
os.environ.setdefault('GUAARDVARK_ROOT', '$SCRIPT_DIR')
from backend.app import create_app
from backend.models import db
from alembic.config import Config
from alembic import command

app = create_app()
with app.app_context():
    db.create_all()
    cfg = Config('$BACKEND_DIR/migrations/alembic.ini')
    cfg.set_main_option('script_location', '$BACKEND_DIR/migrations')
    from backend.config import DATABASE_URL
    cfg.set_main_option('sqlalchemy.url', DATABASE_URL)
    command.stamp(cfg, 'head')
    print('Database created and stamped to head')
" >> "$SETUP_LOG" 2>&1; then
        vader_success "Fresh database created successfully"
        save_migration_fingerprint
    else
        vader_error "Failed to create fresh database. Check $SETUP_LOG for details."
        deactivate
        cd "$SCRIPT_DIR"
        exit 1
    fi
elif [ "$MIGRATION_CHECK" = "2" ]; then
    vader_info "Existing database with stale migration revision — re-stamping to current head..."
    if "$VENV_DIR/bin/python" -c "
import sys, os
sys.path.insert(0, '$SCRIPT_DIR')
os.environ.setdefault('GUAARDVARK_ROOT', '$SCRIPT_DIR')
from sqlalchemy import create_engine, text
from alembic.config import Config
from alembic import command
from backend.config import DATABASE_URL

cfg = Config('$BACKEND_DIR/migrations/alembic.ini')
cfg.set_main_option('script_location', '$BACKEND_DIR/migrations')
cfg.set_main_option('sqlalchemy.url', DATABASE_URL)
# Purge stale revision via SQLAlchemy and stamp to current head
engine = create_engine(DATABASE_URL)
with engine.connect() as conn:
    conn.execute(text('DELETE FROM alembic_version'))
    conn.commit()
command.stamp(cfg, 'head')
print('Database re-stamped to current head after migration squash')
" >> "$SETUP_LOG" 2>&1; then
        vader_success "Database re-stamped to current migration head"
        save_migration_fingerprint
    else
        vader_error "Failed to re-stamp database. Check $SETUP_LOG for details."
        deactivate
        cd "$SCRIPT_DIR"
        exit 1
    fi
else
    if ! check_migrations_preflight; then
        vader_error "Migration pre-flight check failed. Cannot start backend safely."
        vader_error "Fix migration issues and re-run start.sh"
        deactivate
        cd "$SCRIPT_DIR"
        exit 1
    fi
fi

# Tell app.py that start.sh has already verified migrations
export GUAARDVARK_MIGRATIONS_VERIFIED=1

if pgrep -f "(python.*backend[./]app|flask run).*$FLASK_PORT" > /dev/null; then
    vader_error "Flask backend already running on port $FLASK_PORT. Use ./stop.sh first."
    deactivate
    cd "$SCRIPT_DIR"
    exit 1
fi

vader_info "Launching Flask backend in background..."
export GUAARDVARK_ROOT="$SCRIPT_DIR"

ulimit -n 65535
vader_info "File descriptor limit set to: $(ulimit -n)"

nohup env GUAARDVARK_ROOT="$SCRIPT_DIR" FLASK_PORT="$FLASK_PORT" GUAARDVARK_MIGRATIONS_VERIFIED="${GUAARDVARK_MIGRATIONS_VERIFIED:-}" "$VENV_DIR/bin/python" -m backend.app >> "$BACKEND_STARTUP_LOG_FILE" 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" > "$SCRIPT_DIR/pids/backend.pid"

sleep 4

if ! is_port_listening "$FLASK_PORT" 30 "Backend"; then
    vader_error "Backend failed to start listening on port $FLASK_PORT after 30 seconds."
    if [ -f "$BACKEND_STARTUP_LOG_FILE" ]; then
        vader_error "Last 10 lines of startup log:"
        tail -n 10 "$BACKEND_STARTUP_LOG_FILE"
    fi
    kill -9 $BACKEND_PID > /dev/null 2>&1
    deactivate
    cd "$SCRIPT_DIR"
    exit 1
fi

vader_success "Backend is running"
deactivate
cd "$SCRIPT_DIR"
vader_separator

vader_step 10 "Starting enhanced Celery workers..."
if [ -f "$SCRIPT_DIR/start_celery.sh" ]; then
    bash "$SCRIPT_DIR/start_celery.sh"
    if pgrep -f "celery.*worker" >/dev/null 2>&1; then
        CELERY_PID=$(pgrep -f "celery.*worker" | head -1)
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

    nohup celery -A backend.celery_app.celery worker --loglevel=info --concurrency=2 >> "$LOGS_DIR/celery.log" 2>&1 &
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
INOTIFY_CURRENT=$(cat /proc/sys/fs/inotify/max_user_watches 2>/dev/null || echo 0)
if [ "$INOTIFY_CURRENT" -lt "$INOTIFY_MIN" ]; then
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
vader_info "Launching Vite frontend in background..."
nohup $NPM_CMD run dev -- --host --port=$VITE_PORT >> "$FRONTEND_LOG_FILE" 2>&1 &
FRONTEND_PID=$!
echo "$FRONTEND_PID" > "$SCRIPT_DIR/pids/frontend.pid"
sleep 3

if ! kill -0 $FRONTEND_PID > /dev/null 2>&1; then
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
        vader_info "You can manually open: http://localhost:$VITE_PORT"
    fi
fi

vader_separator

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

exit 0
