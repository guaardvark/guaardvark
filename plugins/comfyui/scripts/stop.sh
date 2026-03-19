#!/bin/bash
# Stop ComfyUI server

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
PID_FILE="$PROJECT_ROOT/pids/comfyui.pid"

if [ ! -f "$PID_FILE" ]; then
    # Not an error — ComfyUI was simply not started. Enable it from the Plugins page.
    exit 0
fi

PID=$(cat "$PID_FILE")

if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
    echo "ComfyUI is not running (PID: $PID)"
    rm -f "$PID_FILE"
    exit 0
fi

echo "Stopping ComfyUI (PID: $PID)..."
kill "$PID"

for i in {1..10}; do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "ComfyUI stopped successfully"
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

if kill -0 "$PID" 2>/dev/null; then
    echo "Force killing ComfyUI..."
    kill -9 "$PID"
    rm -f "$PID_FILE"
fi
