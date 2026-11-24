#!/bin/bash


# shellcheck source=scripts/lib/terminal_ui.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/lib/terminal_ui.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDS_DIR="$SCRIPT_DIR/pids"
# shellcheck source=scripts/lib/proc_owner.sh
. "$SCRIPT_DIR/scripts/lib/proc_owner.sh"
# shellcheck source=scripts/lib/start_lock.sh
. "$SCRIPT_DIR/scripts/lib/start_lock.sh"
# shellcheck source=scripts/lib/ollama_lifecycle.sh
. "$SCRIPT_DIR/scripts/lib/ollama_lifecycle.sh"

# Flags. Ollama policy: by default only the instance start.sh launched is stopped;
# --keep-ollama (or GUAARDVARK_OLLAMA_KEEP_RUNNING=1 in .env) touches nothing;
# --all also stops user-owned serves, the systemd service and a dead port holder.
STOP_ALL=0
KEEP_OLLAMA=0
for arg in "$@"; do
    case "$arg" in
        --all) STOP_ALL=1 ;;
        --keep-ollama) KEEP_OLLAMA=1 ;;
        --help|-h)
            echo "Usage: ./stop.sh [--keep-ollama] [--all]"
            echo "  --keep-ollama   leave Ollama running even if start.sh launched it"
            echo "  --all           also stop user-owned ollama serve processes and the systemd service"
            exit 0 ;;
        *) echo "Unknown option: $arg (see --help)" >&2; exit 1 ;;
    esac
done
if [ -f "$SCRIPT_DIR/.env" ]; then
    _keep_line=$(grep -E '^GUAARDVARK_OLLAMA_KEEP_RUNNING=' "$SCRIPT_DIR/.env" | tail -1)
    [ -n "$_keep_line" ] && export "$_keep_line"
    _ext_line=$(grep -E '^GUAARDVARK_OLLAMA_EXTERNAL=' "$SCRIPT_DIR/.env" | tail -1)
    [ -n "$_ext_line" ] && export "$_ext_line"
    _comfy_ext_line=$(grep -E '^GUAARDVARK_COMFYUI_EXTERNAL=' "$SCRIPT_DIR/.env" | tail -1)
    [ -n "$_comfy_ext_line" ] && export "$_comfy_ext_line"
fi
# An external Ollama is never ours to stop (1/true/yes/on, as the backend reads it).
ollama_flag_on "${GUAARDVARK_OLLAMA_EXTERNAL:-}" && KEEP_OLLAMA=1
# Nor is an external ComfyUI (the ComfyUI card's switch, same values).
COMFYUI_EXTERNAL=0
ollama_flag_on "${GUAARDVARK_COMFYUI_EXTERNAL:-}" && COMFYUI_EXTERNAL=1

vader_stop_begin

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
        
        if kill -0 "$pid" 2>/dev/null && ! _ours "$pid"; then
            _not_ours_note "$service_name" "$pid"
        elif kill -0 "$pid" 2>/dev/null; then
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
_proc_cwd() { proc_cwd "$1"; }

# True when PID runs from this checkout and not from another install, a worktree
# nested inside this one included (scripts/lib/proc_owner.sh). "cwd" judges by the
# working directory alone, for sweeps that match on a command-line pattern.
_ours() { proc_in_install "$1" "$SCRIPT_DIR" "${2:-}"; }

# A pid file whose number now belongs to another install or an unrelated process.
_not_ours_note() {
    local root
    root=$(proc_install_root "$2")
    if [ -n "$root" ]; then
        vader_warn "$1: PID $2 belongs to the Guaardvark install at $root, not this one; leaving it running and dropping this install's stale pid file."
    else
        vader_warn "$1: PID $2 is not a process of this install; leaving it running and dropping the stale pid file."
    fi
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
    # No grep -P here: macOS grep does not have it.
    _flask_port_env=$(grep -E '^FLASK_PORT=' "$SCRIPT_DIR/.env" 2>/dev/null | tail -n 1 | cut -d= -f2-)
    [ -n "$_flask_port_env" ] && FLASK_PORT="$_flask_port_env"
fi

# Only when this install's backend holds the port: another install's renders are not ours to cancel.
_backend_is_ours=0
for _pid in $(port_listener_pids "$FLASK_PORT"); do
    _ours "$_pid" && _backend_is_ours=1
done
if [ "$_backend_is_ours" = 1 ] && command -v curl >/dev/null 2>&1; then
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
vader_step 1 "ComfyUI"
comfyui_enabled=$(_plugin_enabled "comfyui")
comfyui_running=false
_plugin_running "comfyui" && comfyui_running=true

comfyui_stopped=false

COMFYUI_PORT=$(_plugin_port comfyui 8188)

if [ "$COMFYUI_EXTERNAL" = 1 ]; then
    vader_info "Leaving ComfyUI alone: you run it yourself (GUAARDVARK_COMFYUI_EXTERNAL=1)."
    vader_quiet "left running"
elif [ "$comfyui_enabled" = "False" ] && [ "$comfyui_running" = false ]; then
    vader_quiet "not running"
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
        if [ -n "$comfyui_pid" ] && kill -0 "$comfyui_pid" 2>/dev/null && ! _ours "$comfyui_pid"; then
            _not_ours_note "ComfyUI" "$comfyui_pid"
        elif [ -n "$comfyui_pid" ] && kill -0 "$comfyui_pid" 2>/dev/null; then
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
#    direct-launch or a crashed plugin leaves no PID file. Only a process
#    running from this install is ours: a ComfyUI the user runs separately on
#    the same port is left alone, as is one whose working directory can't be read.
if [ "$COMFYUI_EXTERNAL" != 1 ] && command -v lsof >/dev/null 2>&1; then
    port_pids=$(lsof -i TCP:"$COMFYUI_PORT" -sTCP:LISTEN -t 2>/dev/null)
    if [ -n "$port_pids" ]; then
        for pid in $port_pids; do
            proc_cwd=$(_proc_cwd "$pid")
            if ! _ours "$pid" cwd; then
                vader_info "Port $COMFYUI_PORT is held by a process outside this install (PID: $pid${proc_cwd:+, $proc_cwd}); leaving it running."
                continue
            fi
            vader_info "Killing orphaned ComfyUI process on port $COMFYUI_PORT (PID: $pid)..."
            kill -TERM "$pid" 2>/dev/null
            sleep 1
            if kill -0 "$pid" 2>/dev/null; then
                kill -KILL "$pid" 2>/dev/null
            fi
        done
    fi
fi

# ── Stop Ollama (policy in scripts/lib/ollama_lifecycle.sh) ──
vader_step 2 "Ollama"
OLLAMA_STOP_MODE=$(ollama_stop_mode "$STOP_ALL" "$KEEP_OLLAMA")
vader_info "Ollama: $OLLAMA_STOP_MODE"
stop_ollama "$OLLAMA_STOP_MODE" "$PIDS_DIR/ollama.pid" "$SCRIPT_DIR"
if [ "$OLLAMA_STOP_MODE" = "keep" ]; then
    vader_quiet "left running"
elif [ "$ollama_killed" -gt 0 ]; then
    vader_success "Ollama stopped ($ollama_killed action(s) taken)."
else
    vader_quiet "not running"
fi

# ── Stop Guaardvark-owned alt Redis (sidecar on 6380–6399 only) ──
vader_step 3 "Studio"
# Never kill system Redis on :6379 managed by systemd. We only stop a process
# whose PID we recorded in pids/redis.pid when start_redis.sh launched a sidecar.
if [ -f "$PIDS_DIR/redis.pid" ]; then
    redis_pid=$(cat "$PIDS_DIR/redis.pid" 2>/dev/null)
    if [ -n "$redis_pid" ] && kill -0 "$redis_pid" 2>/dev/null && ! _ours "$redis_pid"; then
        _not_ours_note "Redis sidecar" "$redis_pid"
    elif [ -n "$redis_pid" ] && kill -0 "$redis_pid" 2>/dev/null; then
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
# Optional external whisper.cpp server started by start.sh when
# GUAARDVARK_USE_WHISPER_SERVER=1.
kill_and_cleanup "whisper_server"

# Clear Python bytecode cache so stale .pyc files never load old code
find "$SCRIPT_DIR/backend" -path "*/venv" -prune -o -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
find "$SCRIPT_DIR/backend" -path "*/venv" -prune -o -name "*.pyc" -type f -exec rm -f {} + 2>/dev/null

vader_step 4 "Workers"
vader_info "Cleaning up any remaining processes from this environment..."

flask_pids=$(pgrep -f "(python.*backend[./]app|flask run)" 2>/dev/null)
if [ -n "$flask_pids" ]; then
    for pid in $flask_pids; do
        if _ours "$pid" cwd; then
            vader_info "Force killing Flask/SocketIO process (PID: $pid) from this environment..."
            kill -TERM "$pid" 2>/dev/null
            sleep 2
            if kill -0 "$pid" 2>/dev/null; then
                kill -KILL "$pid" 2>/dev/null
            fi
        fi
    done
fi

# Pattern catches both worker AND beat processes from this checkout, the
# on-demand research worker (backend/services/research_worker.py) included.
# ERE alternation — `\(worker\|beat\)` was BRE syntax that pgrep -f (ERE)
# matched literally, so it killed nothing and leaked beat/training workers.
celery_pids=$(pgrep -f "celery.*(worker|beat)" 2>/dev/null)
if [ -n "$celery_pids" ]; then
    env_celery_pids=()
    for pid in $celery_pids; do
        if _ours "$pid" cwd; then
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
                if _ours "$pid" cwd; then
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
        if _ours "$pid" cwd; then
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
# The display number and VNC port are one per machine; stop them only when no other
# install's Xvfb holds that display.
AGENT_DISPLAY_SCRIPT="$SCRIPT_DIR/scripts/start_agent_display.sh"
agent_display_owner=""
for pid in $(pgrep -f "Xvfb :${GUAARDVARK_AGENT_DISPLAY:-99}( |$)" 2>/dev/null); do
    _ours "$pid" && continue
    agent_display_owner=$(proc_install_root "$pid")
    [ -n "$agent_display_owner" ] && break
done
if [ -n "$agent_display_owner" ]; then
    vader_info "Agent virtual display :${GUAARDVARK_AGENT_DISPLAY:-99} belongs to the install at $agent_display_owner; leaving it running."
elif [ -x "$AGENT_DISPLAY_SCRIPT" ]; then
    vader_info "Stopping agent virtual display..."
    bash "$AGENT_DISPLAY_SCRIPT" stop 2>&1 | while read line; do vader_info "  $line"; done
    # PIPESTATUS[0] is the stop script's real exit code (the `while` is last in the pipe).
    _agent_stop_rc=${PIPESTATUS[0]}
    if [ "$_agent_stop_rc" -eq 0 ]; then
        vader_success "Agent virtual display stopped."
    else
        vader_warn "Agent virtual display stop reported errors (exit $_agent_stop_rc). Check above."
    fi
fi

# ── Stop enabled plugins (Discord bot, etc.) ──
vader_step 5 "Plugins"
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

# Extension sidecars (extensions/<id>/plugin/), usually linked from a project
# checked out elsewhere. Their stop scripts find their pid files through
# GUAARDVARK_ROOT. A leading underscore parks an extension: never loaded, never stopped.
for ext_plugin in "$SCRIPT_DIR"/extensions/*/plugin/; do
    [ -d "$ext_plugin" ] || continue
    ext_name=$(basename "$(dirname "$ext_plugin")")
    case "$ext_name" in _*) continue ;; esac
    ext_stop="$ext_plugin/scripts/stop.sh"
    [ -f "$ext_stop" ] || continue
    vader_info "Stopping $ext_name extension..."
    if GUAARDVARK_ROOT="$SCRIPT_DIR" bash "$ext_stop" 2>/dev/null; then
        vader_success "$ext_name stopped."
    else
        vader_warn "$ext_name stop script exited non-zero — may not have stopped cleanly."
    fi
done

# Drop pid files whose process is gone. One whose process still runs is kept,
# so the next start and the Plugins page can still find and stop it. A kept
# Ollama (--keep-ollama) loses its pid file as before: start.sh only stops an
# Ollama it launched itself.
rm -f "$PIDS_DIR/ollama.pid"
for pid_file in "$PIDS_DIR"/*.pid; do
    [ -f "$pid_file" ] || continue
    pid=$(tr -dc '0-9' < "$pid_file" 2>/dev/null)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && _ours "$pid"; then
        vader_warn "$(basename "$pid_file" .pid) is still running (pid $pid); keeping its pid file."
    else
        rm -f "$pid_file"
    fi
done

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

vader_stop_done