#!/bin/bash
# Stop the Guaardvark Video Editor plugin service.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
PID_FILE="$PROJECT_ROOT/pids/video_editor.pid"

# shellcheck source=scripts/lib/proc_owner.sh
. "$PROJECT_ROOT/scripts/lib/proc_owner.sh"

# The port start.sh launches the service on.
SERVICE_PORT=8207

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
    exit 0
fi

PID=$(cat "$PID_FILE")

if [ -z "$PID" ] || ! pid_is_service "$PID" "--port $SERVICE_PORT"; then
    echo "Video Editor is not running (stale PID: $PID)"
    rm -f "$PID_FILE"
    exit 0
fi

# The command line alone does not say whose process this is: a reused PID, or
# another install's service (a git worktree inside this one included), passes it.
if ! proc_in_install "$PID" "$PROJECT_ROOT"; then
    owner=$(proc_install_root "$PID")
    if [ -n "$owner" ]; then
        echo "Video Editor PID $PID belongs to the Guaardvark install at $owner, not this one; leaving it running."
    else
        echo "PID $PID is not this install's Video Editor (the pid file is stale); leaving it running."
    fi
    rm -f "$PID_FILE"
    exit 0
fi

echo "Stopping Video Editor (PID: $PID)..."
kill "$PID"

for i in {1..5}; do
    if ! proc_alive "$PID"; then
        echo "Video Editor stopped cleanly"
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

if proc_alive "$PID"; then
    echo "Force killing Video Editor..."
    kill -9 "$PID"
    rm -f "$PID_FILE"
fi
