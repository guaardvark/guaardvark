#!/bin/bash
# Stop script for GPU Embedding Service plugin

set -e

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"

PID_FILE="$PROJECT_ROOT/pids/gpu_embedding_service.pid"

# shellcheck source=scripts/lib/proc_owner.sh
. "$PROJECT_ROOT/scripts/lib/proc_owner.sh"

# True when PID $1 is alive and its command line contains $2. A PID file
# outlives a reboot or a crash, and by then the number can belong to another
# process: one this script must neither report as the service nor signal.
pid_is_service() {
    proc_alive "$1" || return 1
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
    echo "PID file not found. Service may not be running."
    exit 0
fi

PID=$(cat "$PID_FILE")

if [ -z "$PID" ] || ! pid_is_service "$PID" "gpu_embedding.service.app"; then
    echo "Service is not running (PID: $PID)"
    rm -f "$PID_FILE"
    exit 0
fi

# The command line alone does not say whose process this is: a reused PID, or
# another install's service (a git worktree inside this one included), passes it.
if ! proc_in_install "$PID" "$PROJECT_ROOT"; then
    owner=$(proc_install_root "$PID")
    if [ -n "$owner" ]; then
        echo "GPU Embedding Service PID $PID belongs to the Guaardvark install at $owner, not this one; leaving it running."
    else
        echo "PID $PID is not this install's GPU Embedding Service (the pid file is stale); leaving it running."
    fi
    rm -f "$PID_FILE"
    exit 0
fi

echo "Stopping GPU Embedding Service (PID: $PID)..."
kill "$PID"

# Wait for process to stop
for i in {1..10}; do
    if ! proc_alive "$PID"; then
        echo "Service stopped successfully"
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

# Force kill if still running
if proc_alive "$PID"; then
    echo "Force killing service..."
    kill -9 "$PID"
    rm -f "$PID_FILE"
    echo "Service force stopped"
else
    echo "Service stopped"
    rm -f "$PID_FILE"
fi

