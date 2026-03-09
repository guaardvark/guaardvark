#!/bin/bash
# Stop script for GPU Embedding Service plugin

set -e

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"

PID_FILE="$PROJECT_ROOT/pids/gpu_embedding_service.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "PID file not found. Service may not be running."
    exit 0
fi

PID=$(cat "$PID_FILE")

if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
    echo "Service is not running (PID: $PID)"
    rm -f "$PID_FILE"
    exit 0
fi

echo "Stopping GPU Embedding Service (PID: $PID)..."
kill "$PID"

# Wait for process to stop
for i in {1..10}; do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "Service stopped successfully"
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

# Force kill if still running
if kill -0 "$PID" 2>/dev/null; then
    echo "Force killing service..."
    kill -9 "$PID"
    rm -f "$PID_FILE"
    echo "Service force stopped"
else
    echo "Service stopped"
    rm -f "$PID_FILE"
fi

