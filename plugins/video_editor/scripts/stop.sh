#!/bin/bash
# Stop the Guaardvark Video Editor plugin service.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
PID_FILE="$PROJECT_ROOT/pids/video_editor.pid"

if [ ! -f "$PID_FILE" ]; then
    exit 0
fi

PID=$(cat "$PID_FILE")

if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
    echo "Video Editor is not running (stale PID: $PID)"
    rm -f "$PID_FILE"
    exit 0
fi

echo "Stopping Video Editor (PID: $PID)..."
kill "$PID"

for i in {1..5}; do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "Video Editor stopped cleanly"
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

if kill -0 "$PID" 2>/dev/null; then
    echo "Force killing Video Editor..."
    kill -9 "$PID"
    rm -f "$PID_FILE"
fi
