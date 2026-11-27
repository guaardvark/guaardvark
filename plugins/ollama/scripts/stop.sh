#!/bin/bash
# Stop Ollama service
# Tries: sudo systemctl → PID file → pkill user processes → port cleanup

set -euo pipefail

HEALTH_URL="http://127.0.0.1:11434/"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PIDS_DIR="$PROJECT_ROOT/pids"
PID_FILE="$PIDS_DIR/ollama.pid"
CURRENT_USER=$(whoami)

# shellcheck source=scripts/lib/proc_owner.sh
. "$PROJECT_ROOT/scripts/lib/proc_owner.sh"

stopped=0

# The Guaardvark install PID runs from, when that is an install other than this one
# (a git worktree inside this one included). Empty for this install's process and for
# one no install claims, such as an `ollama serve` started by hand in a terminal.
other_install_of() {
    local root
    proc_in_install "$1" "$PROJECT_ROOT" && return 0
    root=$(proc_install_root "$1")
    printf '%s' "$root"
}

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
    # The number may since belong to another process, or the file to another install's
    # Ollama: only this install's is signalled.
    if [ -n "$PID" ] && proc_alive "$PID" && ! proc_in_install "$PID" "$PROJECT_ROOT"; then
        owner=$(proc_install_root "$PID")
        if [ -n "$owner" ]; then
            echo "Ollama PID $PID belongs to the Guaardvark install at $owner, not this one; leaving it running."
        else
            echo "PID $PID is not this install's Ollama (the pid file is stale); leaving it running."
        fi
    elif [ -n "$PID" ] && proc_alive "$PID"; then
        echo "Stopping ollama via PID file (PID: $PID)..."
        kill -TERM "$PID" 2>/dev/null || true
        sleep 2
        if proc_alive "$PID"; then
            kill -KILL "$PID" 2>/dev/null || true
            sleep 1
        fi
        stopped=1
    fi
    rm -f "$PID_FILE"
fi

# Step 3: pkill any 'ollama serve' owned by current user (not systemd's ollama user),
# except one another install started.
ollama_pids=$(pgrep -u "$CURRENT_USER" -f "ollama serve" 2>/dev/null || true)
if [ -n "$ollama_pids" ]; then
    for pid in $ollama_pids; do
        owner=$(other_install_of "$pid")
        if [ -n "$owner" ]; then
            echo "ollama serve (PID: $pid) belongs to the Guaardvark install at $owner; leaving it running."
            continue
        fi
        echo "Killing user-owned ollama serve (PID: $pid)..."
        kill -TERM "$pid" 2>/dev/null || true
        sleep 1
        if proc_alive "$pid"; then
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
            owner=$(other_install_of "$pid")
            if [ -n "$owner" ]; then
                echo "Port 11434 is held by the Guaardvark install at $owner (PID: $pid); leaving it running."
            elif [ "$proc_owner" = "$CURRENT_USER" ]; then
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
