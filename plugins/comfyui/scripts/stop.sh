#!/bin/bash
# Stop ComfyUI server

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
PID_FILE="$PROJECT_ROOT/pids/comfyui.pid"
# The port start.sh used: plugin.local.json (untracked per-install override) or
# plugin.json; 8188 fallback.
PORT=$(python3 -c '
import json, sys
for name in ("plugin.local.json", "plugin.json"):
    try:
        print(int(json.load(open(sys.argv[1] + "/" + name))["port"])); break
    except Exception:
        continue
else:
    print(8188)
' "$PLUGIN_ROOT" 2>/dev/null || echo 8188)
CURRENT_USER=$(whoami)

# shellcheck source=scripts/lib/proc_owner.sh
. "$PROJECT_ROOT/scripts/lib/proc_owner.sh"

# Free the ComfyUI port of a listener from this install still bound to it.
# A crashed ComfyUI (or an orphaned child) can keep the port held with no usable
# PID file; the next start then dies with "OSError: [Errno 98] address already
# in use", which trips the circuit breaker and spams the boot log on every
# restart. Mirrors Ollama stop.sh Step 4. Only a current-user process of this
# install is killed (scripts/lib/proc_owner.sh): a system service, a ComfyUI the
# user runs separately, or another install's, a worktree inside this one included,
# is left alone.
free_port_8188() {
    command -v lsof >/dev/null 2>&1 || return 0
    local remaining_pids
    remaining_pids=$(lsof -i TCP:$PORT -sTCP:LISTEN -t 2>/dev/null || true)
    [ -n "$remaining_pids" ] || return 0
    for pid in $remaining_pids; do
        local proc_owner cwd
        proc_owner=$(ps -o user= -p "$pid" 2>/dev/null | tr -d ' ')
        if ! proc_in_install "$pid" "$PROJECT_ROOT"; then
            cwd=$(proc_install_root "$pid")
            [ -n "$cwd" ] || cwd=$(proc_cwd "$pid")
            echo "Port $PORT is held by a process outside this install (PID: $pid${cwd:+, $cwd}); leaving it running."
            continue
        fi
        if [ "$proc_owner" = "$CURRENT_USER" ]; then
            echo "Freeing port $PORT — killing remaining ComfyUI listener (PID: $pid)..."
            kill -TERM "$pid" 2>/dev/null || true
            sleep 1
            kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
        fi
    done
}

# True when PID $1 is alive and its command line contains $2. A PID file
# outlives a reboot or a crash, and by then the number can belong to another
# process: one this script must neither report as the service nor signal.
pid_is_service() {
    kill -0 "$1" 2>/dev/null || return 1
    local args=""
    if [ -r "/proc/$1/cmdline" ]; then
        args=$(tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null) || args=""
    else
        args=$(ps -o args= -p "$1" 2>/dev/null) || args=""
    fi
    [ -n "$args" ] || return 0  # command line unreadable: trust the PID file
    case "$args" in *"$2"*) return 0 ;; esac
    return 1
}

if [ ! -f "$PID_FILE" ]; then
    # No PID file — ComfyUI may simply not be started, OR a prior crash left an
    # orphan holding the port. Sweep the port either way, then exit cleanly.
    free_port_8188
    exit 0
fi

PID=$(cat "$PID_FILE")

if [ -z "$PID" ] || ! pid_is_service "$PID" "main.py"; then
    echo "ComfyUI is not running (PID: $PID)"
    rm -f "$PID_FILE"
    free_port_8188
    exit 0
fi

if ! proc_in_install "$PID" "$PROJECT_ROOT"; then
    owner=$(proc_install_root "$PID")
    if [ -n "$owner" ]; then
        echo "ComfyUI PID $PID belongs to the Guaardvark install at $owner, not this one; leaving it running."
    else
        echo "PID $PID is not this install's ComfyUI (the pid file is stale); leaving it running."
    fi
    rm -f "$PID_FILE"
    free_port_8188
    exit 0
fi

echo "Stopping ComfyUI (PID: $PID)..."
kill "$PID"

for i in {1..10}; do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "ComfyUI stopped successfully"
        rm -f "$PID_FILE"
        free_port_8188
        exit 0
    fi
    sleep 1
done

if kill -0 "$PID" 2>/dev/null; then
    echo "Force killing ComfyUI..."
    kill -9 "$PID"
    rm -f "$PID_FILE"
fi

# Final guard: make sure nothing current-user is still holding the port.
free_port_8188
