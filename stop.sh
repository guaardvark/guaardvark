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

vader_header "Guaardvark Stop Script"

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

# ── Stop ComfyUI first (free GPU memory before other shutdowns) ──
vader_info "Checking for ComfyUI..."

comfyui_stopped=false

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

# 3. Kill any remaining process on port 8188 (ComfyUI default)
if command -v lsof >/dev/null 2>&1; then
    port_8188_pids=$(lsof -i TCP:8188 -sTCP:LISTEN -t 2>/dev/null)
    if [ -n "$port_8188_pids" ]; then
        for pid in $port_8188_pids; do
            vader_info "Killing orphaned ComfyUI process on port 8188 (PID: $pid)..."
            kill -TERM "$pid" 2>/dev/null
            sleep 1
            if kill -0 "$pid" 2>/dev/null; then
                kill -KILL "$pid" 2>/dev/null
            fi
            comfyui_stopped=true
        done
    fi
fi

if [ "$comfyui_stopped" = true ]; then
    vader_success "ComfyUI shutdown complete."
else
    vader_info "ComfyUI was not running."
fi

# ── Stop orphaned ollama serve processes (not the systemd service) ──
vader_info "Checking for orphaned ollama serve processes..."
ollama_serve_pids=$(pgrep -f "ollama serve" 2>/dev/null)
if [ -n "$ollama_serve_pids" ]; then
    ollama_killed=0
    for pid in $ollama_serve_pids; do
        # Skip if owned by systemd (PPID 1 with systemd unit)
        ppid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
        unit=$(ps -o unit= -p "$pid" 2>/dev/null | tr -d ' ' 2>/dev/null || true)
        if [ "$unit" = "ollama.service" ] || [ "$unit" = "ollama" ]; then
            vader_info "Skipping systemd-managed ollama (PID: $pid)"
            continue
        fi
        vader_info "Killing orphaned ollama serve (PID: $pid, PPID: $ppid)..."
        kill -TERM "$pid" 2>/dev/null
        sleep 1
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null
        fi
        ollama_killed=$((ollama_killed + 1))
    done
    if [ "$ollama_killed" -gt 0 ]; then
        vader_success "Stopped $ollama_killed orphaned ollama serve process(es)."
    else
        vader_info "No orphaned ollama serve processes found."
    fi
else
    vader_info "No orphaned ollama serve processes found."
fi

# ── Stop Guaardvark services ──
kill_and_cleanup "backend"
kill_and_cleanup "frontend"
kill_and_cleanup "celery"

vader_info "Cleaning up any remaining processes from this environment..."

flask_pids=$(pgrep -f "(python.*backend[./]app|flask run)" 2>/dev/null)
if [ -n "$flask_pids" ]; then
    for pid in $flask_pids; do
        proc_cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null)
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

celery_pids=$(pgrep -f "celery.*worker" 2>/dev/null)
if [ -n "$celery_pids" ]; then
    env_celery_pids=()
    for pid in $celery_pids; do
        proc_cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null)
        if [ -n "$proc_cwd" ] && [[ "$proc_cwd" == "$SCRIPT_DIR"* ]]; then
            env_celery_pids+=("$pid")
        fi
    done
    
    if [ ${#env_celery_pids[@]} -gt 0 ]; then
        vader_info "Found ${#env_celery_pids[@]} Celery worker(s) from this environment"
        
        for pid in "${env_celery_pids[@]}"; do
            kill -TERM "$pid" 2>/dev/null
        done
        sleep 3
        
        for pid in "${env_celery_pids[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                vader_warn "Celery worker (PID: $pid) still running, using SIGKILL..."
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

for port in $VITE_PORT 5174 5175 5176 5177; do
    if command -v lsof >/dev/null 2>&1; then
        port_pids=$(lsof -i TCP:"$port" -sTCP:LISTEN -t 2>/dev/null)
        if [ -n "$port_pids" ]; then
            for pid in $port_pids; do
                proc_cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null)
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
        proc_cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null)
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