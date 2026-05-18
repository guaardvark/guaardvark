#!/bin/bash
# Stop Guaardvark Audio Foundry service.
# Gentle SIGTERM -> wait 5s -> SIGKILL if still alive. Matches vision_pipeline pattern.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
PID_FILE="$PROJECT_ROOT/pids/audio_foundry.pid"

if [ ! -f "$PID_FILE" ]; then
    # Not an error — Audio Foundry was simply not started. Enable from Plugins page.
    exit 0
fi

PID=$(cat "$PID_FILE")

if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
    echo "Audio Foundry is not running (stale PID: $PID)"
    rm -f "$PID_FILE"
    exit 0
fi

echo "Stopping Audio Foundry (PID: $PID)..."
kill "$PID"

for i in {1..5}; do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "Audio Foundry stopped cleanly"
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

if kill -0 "$PID" 2>/dev/null; then
    echo "Force killing Audio Foundry..."
    kill -9 "$PID"
    rm -f "$PID_FILE"
fi
