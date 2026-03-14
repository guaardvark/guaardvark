#!/bin/bash
# Start Ollama service
# Tries: already running → sudo systemctl (fast if sudoers rule exists) → direct ollama serve

set -euo pipefail

HEALTH_URL="http://localhost:11434/"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PIDS_DIR="$PROJECT_ROOT/pids"
LOGS_DIR="$PROJECT_ROOT/logs"
PID_FILE="$PIDS_DIR/ollama.pid"

mkdir -p "$PIDS_DIR" "$LOGS_DIR"

# Step 1: Check if already running
if curl -sf --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
    echo "Ollama is already running"
    exit 0
fi

echo "Starting Ollama..."

# Kill any zombie process holding the port but not responding
if command -v lsof >/dev/null 2>&1; then
    zombie_pid=$(lsof -ti :11434 2>/dev/null | head -1)
    if [ -n "$zombie_pid" ]; then
        echo "Killing unresponsive process on port 11434 (PID: $zombie_pid)..."
        kill -9 "$zombie_pid" 2>/dev/null || true
        sleep 2
    fi
fi

started=0

# Step 2: Try sudo systemctl start ollama (fast path if sudoers rule exists)
if command -v systemctl >/dev/null 2>&1; then
    if sudo -n systemctl start ollama 2>/dev/null; then
        echo "Started via sudo systemctl"
        started=1
    fi
fi

# Step 3: Fallback — run ollama serve directly with PID tracking
if [ "$started" -eq 0 ]; then
    if command -v ollama >/dev/null 2>&1; then
        echo "systemctl not available or failed, starting directly..."
        nohup ollama serve > "$LOGS_DIR/ollama_serve.log" 2>&1 &
        OLLAMA_PID=$!
        echo "$OLLAMA_PID" > "$PID_FILE"
        echo "Started ollama serve (PID: $OLLAMA_PID)"
        started=1
    else
        echo "Error: ollama command not found"
        exit 1
    fi
fi

# Step 4: Health check loop (up to 20 seconds)
for i in $(seq 1 10); do
    if curl -sf --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
        echo "Ollama started successfully"
        exit 0
    fi
    sleep 2
done

echo "Warning: Ollama may not have started (health check timed out after 20s)"
# Clean up PID file if process didn't respond
if [ -f "$PID_FILE" ]; then
    pid=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$PID_FILE"
    fi
fi
exit 1
