#!/bin/bash
# Start Ollama via systemctl

set -e

# Check if already running
if curl -sf http://localhost:11434/ >/dev/null 2>&1; then
    echo "Ollama is already running"
    exit 0
fi

echo "Starting Ollama..."

# Try systemctl first (preferred — runs as ollama user)
if command -v systemctl >/dev/null 2>&1; then
    sudo -n systemctl start ollama 2>/dev/null || systemctl --user start ollama 2>/dev/null || {
        # Fallback: run directly
        echo "systemctl not available, starting directly..."
        ollama serve > /dev/null 2>&1 &
        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
        mkdir -p "$PROJECT_ROOT/pids"
        echo $! > "$PROJECT_ROOT/pids/ollama.pid"
    }
else
    ollama serve > /dev/null 2>&1 &
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
    mkdir -p "$PROJECT_ROOT/pids"
    echo $! > "$PROJECT_ROOT/pids/ollama.pid"
fi

# Wait for Ollama to respond
for i in {1..15}; do
    if curl -sf http://localhost:11434/ >/dev/null 2>&1; then
        echo "Ollama started successfully"
        exit 0
    fi
    sleep 1
done

echo "Warning: Ollama may not have started (health check timed out)"
exit 1
