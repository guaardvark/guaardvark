#!/bin/bash
# Start Ollama service
# Ollama runs as a system service (not user-level), so we need sudo for systemctl.
# Tries: sudo -n (passwordless) → systemctl --user → direct ollama serve.

set -euo pipefail

HEALTH_URL="http://localhost:11434/"

# Check if already running
if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
    echo "Ollama is already running"
    exit 0
fi

echo "Starting Ollama..."

started=0

if command -v systemctl >/dev/null 2>&1; then
    # 1. Try passwordless sudo (system service — this is how Ollama is installed)
    if sudo -n systemctl start ollama 2>/dev/null; then
        echo "Started via sudo systemctl"
        started=1
    # 2. Try user-level service (rare, but possible)
    elif systemctl --user start ollama 2>/dev/null; then
        echo "Started via systemctl --user"
        started=1
    fi
fi

# 3. Fallback: run ollama serve directly
if [ "$started" -eq 0 ]; then
    if command -v ollama >/dev/null 2>&1; then
        echo "systemctl not available or failed, starting directly..."
        ollama serve > /dev/null 2>&1 &
        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
        mkdir -p "$PROJECT_ROOT/pids"
        echo $! > "$PROJECT_ROOT/pids/ollama.pid"
        started=1
    else
        echo "Error: ollama command not found"
        exit 1
    fi
fi

# Wait for Ollama to respond (up to 15 seconds)
for i in {1..15}; do
    if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
        echo "Ollama started successfully"
        exit 0
    fi
    sleep 1
done

echo "Warning: Ollama may not have started (health check timed out after 15s)"
exit 1
