#!/bin/bash
# Stop Ollama

set -e

# Check if running
if ! curl -sf http://localhost:11434/ >/dev/null 2>&1; then
    echo "Ollama is not running"
    exit 0
fi

echo "Stopping Ollama..."

# Try systemctl
if command -v systemctl >/dev/null 2>&1; then
    sudo -n systemctl stop ollama 2>/dev/null || systemctl --user stop ollama 2>/dev/null || {
        echo "systemctl failed, trying direct kill..."
        pkill -f "ollama serve" 2>/dev/null || true
    }
else
    pkill -f "ollama serve" 2>/dev/null || true
fi

# Clean up PID file if it exists
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
rm -f "$PROJECT_ROOT/pids/ollama.pid"

sleep 2

if ! curl -sf http://localhost:11434/ >/dev/null 2>&1; then
    echo "Ollama stopped successfully"
else
    echo "Warning: Ollama may still be running"
fi
