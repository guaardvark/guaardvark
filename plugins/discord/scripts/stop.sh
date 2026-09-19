#!/bin/bash
# Stop Guaardvark Discord Bot
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
PID_FILE="$PROJECT_ROOT/pids/discord_bot.pid"

# Stop the pi-omni voice stack only if guaardvark started it (marker present).
# If pi-omni's run_ngrok.sh owns it, the marker won't exist and we leave it alone.
VOICE_STACK_MARKER="$PROJECT_ROOT/pids/voice_stack.started"
if [ -f "$VOICE_STACK_MARKER" ]; then
    VOICE_STOP="$PROJECT_ROOT/../pi-omni-helper/voice-stop.sh"
    if [ -f "$VOICE_STOP" ]; then
        echo "Stopping pi-omni voice stack (started by guaardvark)..."
        bash "$VOICE_STOP" >/dev/null 2>&1 || true
    fi
    rm -f "$VOICE_STACK_MARKER"
fi

if [ ! -f "$PID_FILE" ]; then
    # Not an error — Discord Bot was simply not started. Enable it from the Plugins page.
    exit 0
fi

PID=$(cat "$PID_FILE")

if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
    echo "Discord Bot is not running (PID: $PID)"
    rm -f "$PID_FILE"
    exit 0
fi

echo "Stopping Discord Bot (PID: $PID)..."
kill "$PID"

for i in {1..5}; do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "Discord Bot stopped successfully"
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

if kill -0 "$PID" 2>/dev/null; then
    echo "Force killing Discord Bot..."
    kill -9 "$PID"
    rm -f "$PID_FILE"
fi
