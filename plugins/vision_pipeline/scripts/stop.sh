#!/bin/bash
# Stop Guaardvark Vision Pipeline service
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
PID_FILE="$PROJECT_ROOT/pids/vision_pipeline.pid"

# shellcheck source=scripts/lib/proc_owner.sh
. "$PROJECT_ROOT/scripts/lib/proc_owner.sh"

# The port start.sh launches the service on.
SERVICE_PORT=8201

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
    # Not an error — Vision Pipeline was simply not started. Enable it from the Plugins page.
    exit 0
fi

PID=$(cat "$PID_FILE")

if [ -z "$PID" ] || ! pid_is_service "$PID" "--port $SERVICE_PORT"; then
    echo "Vision Pipeline is not running (PID: $PID)"
    rm -f "$PID_FILE"
    exit 0
fi

# The command line alone does not say whose process this is: a reused PID, or
# another install's service (a git worktree inside this one included), passes it.
if ! proc_in_install "$PID" "$PROJECT_ROOT"; then
    owner=$(proc_install_root "$PID")
    if [ -n "$owner" ]; then
        echo "Vision Pipeline PID $PID belongs to the Guaardvark install at $owner, not this one; leaving it running."
    else
        echo "PID $PID is not this install's Vision Pipeline (the pid file is stale); leaving it running."
    fi
    rm -f "$PID_FILE"
    exit 0
fi

echo "Stopping Vision Pipeline (PID: $PID)..."
kill "$PID"

for i in {1..5}; do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "Vision Pipeline stopped successfully"
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

if kill -0 "$PID" 2>/dev/null; then
    echo "Force killing Vision Pipeline..."
    kill -9 "$PID"
    rm -f "$PID_FILE"
fi
