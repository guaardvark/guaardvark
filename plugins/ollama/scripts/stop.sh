#!/bin/bash
# Stop Ollama service
# Ollama runs as a system service (not user-level), so we need sudo for systemctl.
# Tries: sudo -n (passwordless) → systemctl --user → pkill fallback.

set -euo pipefail

HEALTH_URL="http://localhost:11434/"

# Check if running
if ! curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
    echo "Ollama is not running"
    exit 0
fi

echo "Stopping Ollama..."

stopped=0

if command -v systemctl >/dev/null 2>&1; then
    # 1. Try passwordless sudo (system service — this is how Ollama is installed)
    if sudo -n systemctl stop ollama 2>/dev/null; then
        echo "Stopped via sudo systemctl"
        stopped=1
    # 2. Try user-level service
    elif systemctl --user stop ollama 2>/dev/null; then
        echo "Stopped via systemctl --user"
        stopped=1
    fi
fi

# 3. Fallback: pkill
if [ "$stopped" -eq 0 ]; then
    echo "systemctl failed, trying pkill..."
    pkill -f "ollama serve" 2>/dev/null || true
    # Also try killing by PID file
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
    PID_FILE="$PROJECT_ROOT/pids/ollama.pid"
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null || true
        fi
        rm -f "$PID_FILE"
    fi
    stopped=1
fi

# Clean up PID file
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
rm -f "$PROJECT_ROOT/pids/ollama.pid"

# Wait for service to actually stop (up to 10 seconds)
for i in {1..10}; do
    if ! curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
        echo "Ollama stopped successfully"
        exit 0
    fi
    sleep 1
done

echo "Warning: Ollama may still be running after 10s"
exit 1
