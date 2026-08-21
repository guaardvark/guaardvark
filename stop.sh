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

vader_header() { echo -e "\n${VADER_RED}${VADER_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${VADER_RESET}\n${VADER_WHITE}${VADER_BOLD}  $1${VADER_RESET}\n${VADER_RED}${VADER_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${VADER_RESET}"; }
vader_info() { echo -e "  ${VADER_GRAY}·${VADER_RESET} ${VADER_WHITE_DIM}$1${VADER_RESET}"; }
vader_success() { echo -e "  ${VADER_RED}✔${VADER_RESET} ${VADER_WHITE}$1${VADER_RESET}"; }
vader_warn() { echo -e "  ${VADER_RED_LIGHT}⚠${VADER_RESET} ${VADER_RED_LIGHT}$1${VADER_RESET}"; }
vader_error() { echo -e "  ${VADER_RED_DARK}✖${VADER_RESET} ${VADER_RED}$1${VADER_RESET}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDS_DIR="$SCRIPT_DIR/pids"
# shellcheck source=scripts/lib/start_lock.sh
. "$SCRIPT_DIR/scripts/lib/start_lock.sh"

vader_header "Guaardvark Stop Script"

# Reap a leftover ./start.sh (including Ctrl+Z / STAT=T) so the next
# ./start.sh is not blocked by the overlap guard. Must run even when no
# backend/frontend pidfiles exist.
while IFS= read -r _lock_line; do
    [ -n "$_lock_line" ] && vader_info "$_lock_line"
done < <(start_lock_reap)

kill_and_cleanup() {
    local service_name=$1
    local pid_file="$PIDS_DIR/${service_name}.pid"
    
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        vader_info "Stopping $service_name (PID: $pid)..."
        
        if kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null
            sleep 2
            
            if kill -0 "$pid" 2>/dev/null; then
                vader_warn "$service_name still running, using SIGKILL..."
                kill -KILL "$pid" 2>/dev/null
                sleep 1
            fi
            
            if ! kill -0 "$pid" 2>/dev/null; then
                vader_success "$service_name stopped successfully."
            else
                vader_error "Failed to stop $service_name."
            fi
        else
            vader_info "$service_name process (PID: $pid) not running."
        fi
        
        rm -f "$pid_file"
    else
        vader_info "No PID file found for $service_name."
    fi
}

# ── Helper: portable process working-directory lookup ──
# Linux exposes it at /proc/<pid>/cwd; macOS/BSD have no /proc, so fall back to lsof's cwd
# fd. Returns the absolute cwd or empty. The frontend/backend/celery confinement checks below
# funnel through here — without the lsof fallback the cwd check returned empty on macOS, so our
# own stale processes (e.g. the zombie vite on :4173 in issue #41) were never reaped.
_proc_cwd() {
    local pid="$1" cwd=""
    if [ -e "/proc/$pid/cwd" ]; then
        cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null)
    elif command -v lsof >/dev/null 2>&1; then
        cwd=$(lsof -a -d cwd -p "$pid" -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)
    fi
    printf '%s' "$cwd"
}

# ── Helper: is a plugin enabled? data/plugin_state.json (user choice) wins over
# the manifest's default_enabled (plugin.local.json overrides plugin.json). ──
_plugin_enabled() {
    if command -v python3 >/dev/null 2>&1; then
        python3 - "$SCRIPT_DIR" "$1" <<'PY' 2>/dev/null || echo "False"
import json, sys
root, pid = sys.argv[1], sys.argv[2]
try:
    state = json.load(open(f"{root}/data/plugin_state.json")).get("user_enabled", {})
    if pid in state:
        print(bool(state[pid])); sys.exit(0)
except Exception:
    pass
for name in ("plugin.local.json", "plugin.json"):
    try:
        cfg = json.load(open(f"{root}/plugins/{pid}/{name}")).get("config", {})
        if "default_enabled" in cfg:
            print(bool(cfg["default_enabled"])); sys.exit(0)
    except Exception:
        continue
print(False)
PY
    else
        echo "False"
    fi
}

# ── Helper: a plugin's port from plugin.local.json / plugin.json ──
_plugin_port() {
    python3 - "$SCRIPT_DIR" "$1" "$2" <<'PY' 2>/dev/null || echo "$2"
import json, sys
root, pid, fallback = sys.argv[1], sys.argv[2], sys.argv[3]
for name in ("plugin.local.json", "plugin.json"):
    try:
        print(int(json.load(open(f"{root}/plugins/{pid}/{name}"))["port"])); sys.exit(0)
    except Exception:
        continue
print(fallback)
PY
}

# ── Helper: check if a plugin is actually running (PID file + process alive) ──
_plugin_running() {
    local pid_file="$PIDS_DIR/$1.pid"
    [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file" 2>/dev/null)" 2>/dev/null
}

# ── Cancel in-flight VideoGen batches (backend may still be up) ──
FLASK_PORT=${FLASK_PORT:-5000}
if [ -f "$SCRIPT_DIR/.env" ]; then
    _flask_port_env=$(grep -oP '^FLASK_PORT=\K.*' "$SCRIPT_DIR/.env" 2>/dev/null)
    [ -n "$_flask_port_env" ] && FLASK_PORT="$_flask_port_env"
fi

if command -v curl >/dev/null 2>&1; then
    cancel_resp=$(curl -sf --max-time 5 -X POST "http://localhost:${FLASK_PORT}/api/batch-video/cancel-all" 2>/dev/null)
    if [ -n "$cancel_resp" ] && command -v python3 >/dev/null 2>&1; then
        cancelled_count=$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); r=d.get('data',d); print(r.get('count', 0))" <<< "$cancel_resp" 2>/dev/null)
        if [ -n "$cancelled_count" ] && [ "$cancelled_count" -gt 0 ] 2>/dev/null; then
            vader_info "Cancelled ${cancelled_count} in-flight VideoGen batch(es)."
        fi
    fi
fi

# ── Stop ComfyUI first (free GPU memory before other shutdowns) ──
# Only check ComfyUI if it's enabled or actually running
comfyui_enabled=$(_plugin_enabled "comfyui")
comfyui_running=false
_plugin_running "comfyui" && comfyui_running=true

comfyui_stopped=false

COMFYUI_PORT=$(_plugin_port comfyui 8188)

if [ "$comfyui_enabled" = "False" ] && [ "$comfyui_running" = false ]; then
    vader_info "ComfyUI: not enabled, skipping."
else

# 1. Use the plugin's own stop script if it exists
    COMFYUI_STOP_SCRIPT="$SCRIPT_DIR/plugins/comfyui/scripts/stop.sh"
    if [ -f "$COMFYUI_STOP_SCRIPT" ]; then
        vader_info "Running ComfyUI plugin stop script..."
        bash "$COMFYUI_STOP_SCRIPT" 2>/dev/null && comfyui_stopped=true
    fi

    # 2. Kill via PID file (in case the stop script didn't handle it)
    if [ -f "$PIDS_DIR/comfyui.pid" ]; then
        comfyui_pid=$(cat "$PIDS_DIR/comfyui.pid" 2>/dev/null)
        if [ -n "$comfyui_pid" ] && kill -0 "$comfyui_pid" 2>/dev/null; then
            vader_info "Stopping ComfyUI (PID: $comfyui_pid)..."
            kill -TERM "$comfyui_pid" 2>/dev/null
            sleep 2
            if kill -0 "$comfyui_pid" 2>/dev/null; then
                vader_warn "ComfyUI still running, using SIGKILL..."
                kill -KILL "$comfyui_pid" 2>/dev/null
                sleep 1
            fi
            if ! kill -0 "$comfyui_pid" 2>/dev/null; then
                vader_success "ComfyUI stopped (PID: $comfyui_pid)"
                comfyui_stopped=true
            else
                vader_error "Failed to stop ComfyUI (PID: $comfyui_pid)"
            fi
        fi
        rm -f "$PIDS_DIR/comfyui.pid"
    fi

if [ "$comfyui_stopped" = true ]; then
    vader_success "ComfyUI shutdown complete."
else
    vader_info "ComfyUI was not running."
fi
fi  # end comfyui_enabled/running check

# 3. Orphaned listener on the ComfyUI port — always swept, because a router
#    direct-launch or a crashed plugin leaves no PID file.
if command -v lsof >/dev/null 2>&1; then
    port_pids=$(lsof -i TCP:"$COMFYUI_PORT" -sTCP:LISTEN -t 2>/dev/null)
    if [ -n "$port_pids" ]; then
        for pid in $port_pids; do
            vader_info "Killing orphaned ComfyUI process on port $COMFYUI_PORT (PID: $pid)..."
            kill -TERM "$pid" 2>/dev/null
            sleep 1
            if kill -0 "$pid" 2>/dev/null; then
                kill -KILL "$pid" 2>/dev/null
            fi
        done
    fi
fi

# ── Stop Ollama (PID file → user processes → systemd → port cleanup) ──
vader_info "Stopping Ollama..."
ollama_killed=0

# 1. Kill by PID file first
OLLAMA_PID_FILE="$PIDS_DIR/ollama.pid"
if [ -f "$OLLAMA_PID_FILE" ]; then
    OLLAMA_PID=$(cat "$OLLAMA_PID_FILE" 2>/dev/null)
    if [ -n "$OLLAMA_PID" ] && kill -0 "$OLLAMA_PID" 2>/dev/null; then
        vader_info "Stopping Ollama via PID file (PID: $OLLAMA_PID)..."
        kill -TERM "$OLLAMA_PID" 2>/dev/null
        sleep 2
        if kill -0 "$OLLAMA_PID" 2>/dev/null; then
            kill -KILL "$OLLAMA_PID" 2>/dev/null
            sleep 1
        fi
        if ! kill -0 "$OLLAMA_PID" 2>/dev/null; then
            ollama_killed=$((ollama_killed + 1))
        fi
    fi
    rm -f "$OLLAMA_PID_FILE"
fi

# 2. Kill any 'ollama serve' process owned by the current user (NOT the systemd 'ollama' user)
CURRENT_USER=$(whoami)
ollama_serve_pids=$(pgrep -f "ollama serve" 2>/dev/null)
if [ -n "$ollama_serve_pids" ]; then
    for pid in $ollama_serve_pids; do
        # Check process owner — only kill our own user's processes
        proc_owner=$(ps -o user= -p "$pid" 2>/dev/null | tr -d ' ')
        if [ "$proc_owner" = "$CURRENT_USER" ]; then
            vader_info "Killing user-owned ollama serve (PID: $pid, owner: $proc_owner)..."
            kill -TERM "$pid" 2>/dev/null
            sleep 1
            if kill -0 "$pid" 2>/dev/null; then
                kill -KILL "$pid" 2>/dev/null
            fi
            ollama_killed=$((ollama_killed + 1))
        fi
    done
fi

# 3. Try stopping the systemd service (passwordless if sudoers rule exists)
if command -v systemctl >/dev/null 2>&1; then
    if sudo -n systemctl stop ollama 2>/dev/null; then
        vader_info "Stopped Ollama systemd service"
        ollama_killed=$((ollama_killed + 1))
    fi
fi

# 4. Final check — if port 11434 is still occupied, kill whatever is holding it
if command -v lsof >/dev/null 2>&1; then
    port_11434_pids=$(lsof -i TCP:11434 -sTCP:LISTEN -t 2>/dev/null)
    if [ -n "$port_11434_pids" ]; then
        for pid in $port_11434_pids; do
            # Only kill if it doesn't respond to health check (zombie)
            if ! curl -sf --max-time 2 http://127.0.0.1:11434/ >/dev/null 2>&1; then
                vader_info "Killing unresponsive process on port 11434 (PID: $pid)..."
                kill -TERM "$pid" 2>/dev/null
                sleep 1
                if kill -0 "$pid" 2>/dev/null; then
                    kill -KILL "$pid" 2>/dev/null
                fi
                ollama_killed=$((ollama_killed + 1))
            fi
        done
    fi
fi

if [ "$ollama_killed" -gt 0 ]; then
    vader_success "Ollama stopped ($ollama_killed action(s) taken)."
else
    vader_info "Ollama was not running (or managed externally)."
fi

# ── Stop Guaardvark-owned alt Redis (sidecar on 6380–6399 only) ──
# Never kill system Redis on :6379 managed by systemd. We only stop a process
# whose PID we recorded in pids/redis.pid when start_redis.sh launched a sidecar.
if [ -f "$PIDS_DIR/redis.pid" ]; then
    redis_pid=$(cat "$PIDS_DIR/redis.pid" 2>/dev/null)
    if [ -n "$redis_pid" ] && kill -0 "$redis_pid" 2>/dev/null; then
        # Extra safety: only stop if the process looks like redis-server and is
        # not the distro service bound solely as the system unit (we check cmdline).
        redis_cmd=$(ps -p "$redis_pid" -o args= 2>/dev/null || true)
        if echo "$redis_cmd" | grep -q "redis-server"; then
            vader_info "Stopping Guaardvark Redis sidecar (PID: $redis_pid)..."
            kill -TERM "$redis_pid" 2>/dev/null
            sleep 1
            if kill -0 "$redis_pid" 2>/dev/null; then
                kill -KILL "$redis_pid" 2>/dev/null
            fi
            if ! kill -0 "$redis_pid" 2>/dev/null; then
                vader_success "Redis sidecar stopped."
            else
                vader_warn "Redis sidecar PID $redis_pid still running."
            fi
        else
            vader_info "pids/redis.pid ($redis_pid) is not redis-server — leaving alone."
        fi
    else
        vader_info "Redis sidecar not running (stale pidfile)."
    fi
    rm -f "$PIDS_DIR/redis.pid"
fi

# ── Stop Guaardvark services ──
kill_and_cleanup "backend"
kill_and_cleanup "frontend"
kill_and_cleanup "celery"

# Clear Python bytecode cache so stale .pyc files never load old code
find "$SCRIPT_DIR/backend" -path "*/venv" -prune -o -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
find "$SCRIPT_DIR/backend" -path "*/venv" -prune -o -name "*.pyc" -type f -exec rm -f {} + 2>/dev/null

vader_info "Cleaning up any remaining processes from this environment..."

flask_pids=$(pgrep -f "(python.*backend[./]app|flask run)" 2>/dev/null)
if [ -n "$flask_pids" ]; then
    for pid in $flask_pids; do
        proc_cwd=$(_proc_cwd "$pid")
        if [ -n "$proc_cwd" ] && [[ "$proc_cwd" == "$SCRIPT_DIR"* ]]; then
            vader_info "Force killing Flask/SocketIO process (PID: $pid) from this environment..."
            kill -TERM "$pid" 2>/dev/null
            sleep 2
            if kill -0 "$pid" 2>/dev/null; then
                kill -KILL "$pid" 2>/dev/null
            fi
        fi
    done
fi

# Pattern catches both worker AND beat processes from this checkout.
# ERE alternation — `\(worker\|beat\)` was BRE syntax that pgrep -f (ERE)
# matched literally, so it killed nothing and leaked beat/training workers.
celery_pids=$(pgrep -f "celery.*(worker|beat)" 2>/dev/null)
if [ -n "$celery_pids" ]; then
    env_celery_pids=()
    for pid in $celery_pids; do
        proc_cwd=$(_proc_cwd "$pid")
        if [ -n "$proc_cwd" ] && [[ "$proc_cwd" == "$SCRIPT_DIR"* ]]; then
            env_celery_pids+=("$pid")
        fi
    done

    if [ ${#env_celery_pids[@]} -gt 0 ]; then
        vader_info "Found ${#env_celery_pids[@]} Celery worker/beat process(es) from this environment"

        for pid in "${env_celery_pids[@]}"; do
            kill -TERM "$pid" 2>/dev/null
        done
        sleep 3

        for pid in "${env_celery_pids[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                vader_warn "Celery process (PID: $pid) still running, using SIGKILL..."
                kill -KILL "$pid" 2>/dev/null
            fi
        done

        rm -f "$PIDS_DIR"/celery_*.pid
    fi
fi

VITE_PORT=5173
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env" 2>/dev/null
    set +a
    VITE_PORT=${VITE_PORT:-5173}
fi

# 4173 = `vite preview` default (an old-rev preview server outlived its rev and kept serving a
# stale bundle whose /api proxy 403'd — issue #41); 5173 = `vite dev` default when VITE_PORT differs.
for port in $VITE_PORT 4173 5173 5174 5175 5176 5177; do
    if command -v lsof >/dev/null 2>&1; then
        port_pids=$(lsof -i TCP:"$port" -sTCP:LISTEN -t 2>/dev/null)
        if [ -n "$port_pids" ]; then
            for pid in $port_pids; do
                proc_cwd=$(_proc_cwd "$pid")
                if [ -n "$proc_cwd" ] && [[ "$proc_cwd" == "$SCRIPT_DIR"* ]]; then
                    vader_info "Force killing node/vite process on port $port (PID: $pid) from this environment..."
                    kill -TERM "$pid" 2>/dev/null
                    sleep 1
                    if kill -0 "$pid" 2>/dev/null; then
                        kill -KILL "$pid" 2>/dev/null
                    fi
                fi
            done
        fi
    fi
done

vite_pids=$(pgrep -f "node.*vite" 2>/dev/null)
if [ -n "$vite_pids" ]; then
    for pid in $vite_pids; do
        proc_cwd=$(_proc_cwd "$pid")
        if [ -n "$proc_cwd" ] && [[ "$proc_cwd" == "$SCRIPT_DIR"* ]]; then
            vader_info "Force killing Vite process (PID: $pid) from this environment..."
            kill -TERM "$pid" 2>/dev/null
            sleep 1
            if kill -0 "$pid" 2>/dev/null; then
                kill -KILL "$pid" 2>/dev/null
            fi
        fi
    done
fi

# ── Stop agent virtual display (Xvfb + VNC + window manager) ──
AGENT_DISPLAY_SCRIPT="$SCRIPT_DIR/scripts/start_agent_display.sh"
if [ -x "$AGENT_DISPLAY_SCRIPT" ]; then
    vader_info "Stopping agent virtual display..."
    bash "$AGENT_DISPLAY_SCRIPT" stop 2>&1 | while read line; do vader_info "  $line"; done
    # PIPESTATUS[0] is the stop script's real exit code (the `while` is last in the pipe).
    if [ "${PIPESTATUS[0]}" -eq 0 ]; then
        vader_success "Agent virtual display stopped."
    else
        vader_warn "Agent virtual display stop reported errors (exit ${PIPESTATUS[0]}). Check above."
    fi
fi

# ── Stop enabled plugins (Discord bot, etc.) ──
vader_info "Stopping enabled plugins..."
for plugin_dir in "$SCRIPT_DIR"/plugins/*/; do
    plugin_name=$(basename "$plugin_dir")
    # Skip comfyui and ollama — already handled above
    [ "$plugin_name" = "comfyui" ] && continue
    [ "$plugin_name" = "ollama" ] && continue
    [ "$plugin_name" = "gpu_embedding" ] && continue

    stop_script="$plugin_dir/scripts/stop.sh"
    pid_file="$PIDS_DIR/${plugin_name}_bot.pid"
    [ ! -f "$pid_file" ] && pid_file="$PIDS_DIR/${plugin_name}.pid"

    if [ -f "$stop_script" ] && [ -f "$pid_file" ]; then
        vader_info "Stopping $plugin_name plugin..."
        if bash "$stop_script" 2>/dev/null; then
            vader_success "$plugin_name stopped."
        else
            vader_warn "$plugin_name stop script exited non-zero — may not have stopped cleanly."
        fi
    elif [ -f "$pid_file" ]; then
        kill_and_cleanup "${plugin_name}_bot"
    fi
done

rm -f "$PIDS_DIR"/*.pid

# Remove runtime state file used by CLI auto-discovery
RUNTIME_FILE="$HOME/.guaardvark/runtime.json"
if [ -f "$RUNTIME_FILE" ]; then
    # Only remove if it points to this installation
    if command -v python3 >/dev/null 2>&1; then
        RUNTIME_ROOT=$(python3 -c "import json; print(json.load(open('$RUNTIME_FILE')).get('root',''))" 2>/dev/null)
        if [ "$RUNTIME_ROOT" = "$SCRIPT_DIR" ]; then
            rm -f "$RUNTIME_FILE"
            vader_info "Removed runtime state file."
        fi
    else
        rm -f "$RUNTIME_FILE"
    fi
fi

vader_success "All Guaardvark services stopped"