#!/bin/bash
# Stop Guaardvark Discord Bot
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
PID_FILE="$PROJECT_ROOT/pids/discord_bot.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "PID file not found. Discord Bot may not be running."
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
