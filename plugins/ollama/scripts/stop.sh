#!/bin/bash
# Stop Ollama service
# Tries: sudo systemctl → PID file → pkill user processes → port cleanup

set -euo pipefail

HEALTH_URL="http://localhost:11434/"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PIDS_DIR="$PROJECT_ROOT/pids"
PID_FILE="$PIDS_DIR/ollama.pid"
CURRENT_USER=$(whoami)

stopped=0

# Step 1: Try sudo systemctl stop ollama (fast path if sudoers rule exists)
if command -v systemctl >/dev/null 2>&1; then
    if sudo -n systemctl stop ollama 2>/dev/null; then
        echo "Stopped via sudo systemctl"
        stopped=1
    fi
fi

# Step 2: Kill by PID file
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "Stopping ollama via PID file (PID: $PID)..."
        kill -TERM "$PID" 2>/dev/null || true
        sleep 2
        if kill -0 "$PID" 2>/dev/null; then
            kill -KILL "$PID" 2>/dev/null || true
            sleep 1
        fi
        stopped=1
    fi
    rm -f "$PID_FILE"
fi

# Step 3: pkill any 'ollama serve' owned by current user (not systemd's ollama user)
ollama_pids=$(pgrep -u "$CURRENT_USER" -f "ollama serve" 2>/dev/null || true)
if [ -n "$ollama_pids" ]; then
    for pid in $ollama_pids; do
        echo "Killing user-owned ollama serve (PID: $pid)..."
        kill -TERM "$pid" 2>/dev/null || true
        sleep 1
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
        stopped=1
    done
fi

# Step 4: Verify port 11434 is free — kill any remaining listener
if command -v lsof >/dev/null 2>&1; then
    remaining_pids=$(lsof -i TCP:11434 -sTCP:LISTEN -t 2>/dev/null || true)
    if [ -n "$remaining_pids" ]; then
        for pid in $remaining_pids; do
            proc_owner=$(ps -o user= -p "$pid" 2>/dev/null | tr -d ' ')
            if [ "$proc_owner" = "$CURRENT_USER" ]; then
                echo "Force-killing remaining process on port 11434 (PID: $pid)..."
                kill -KILL "$pid" 2>/dev/null || true
                stopped=1
            fi
        done
    fi
fi

# Clean up PID file
rm -f "$PID_FILE" 2>/dev/null

# Wait for port to actually free up (up to 10 seconds)
for i in $(seq 1 10); do
    if ! curl -sf --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
        # Double-check no listener on port
        if command -v lsof >/dev/null 2>&1; then
            if [ -z "$(lsof -i TCP:11434 -sTCP:LISTEN -t 2>/dev/null)" ]; then
                echo "Ollama stopped successfully (port 11434 is free)"
                exit 0
            fi
        else
            echo "Ollama stopped successfully"
            exit 0
        fi
    fi
    sleep 1
done

# Final status
if ! curl -sf --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
    echo "Ollama stopped successfully"
    exit 0
fi

echo "Warning: Ollama may still be running on port 11434"
exit 1
