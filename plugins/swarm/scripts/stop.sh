#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"

PID_FILE="$PROJECT_ROOT/pids/swarm.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "Swarm service not running (no PID file)"
    exit 0
fi

PID=$(cat "$PID_FILE")

if ! kill -0 "$PID" 2>/dev/null; then
    echo "Swarm service not running (stale PID file)"
    rm -f "$PID_FILE"
    exit 0
fi

# graceful stop
kill "$PID"
for i in {1..10}; do
    sleep 1
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "Swarm service stopped"
        rm -f "$PID_FILE"
        exit 0
    fi
done

# force kill
kill -9 "$PID" 2>/dev/null
rm -f "$PID_FILE"
echo "Swarm service force-stopped"
