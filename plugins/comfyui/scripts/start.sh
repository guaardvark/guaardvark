#!/bin/bash
# Start ComfyUI server

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
COMFYUI_DIR="$PLUGIN_ROOT/ComfyUI"
VENV_PYTHON="$PROJECT_ROOT/backend/venv/bin/python"
PORT=8188

# Check ComfyUI exists
if [ ! -f "$COMFYUI_DIR/main.py" ]; then
    echo "Error: ComfyUI not found at $COMFYUI_DIR/main.py"
    exit 1
fi

# Check if already running
if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "ComfyUI is already running on port $PORT"
    exit 0
fi

# Check venv python exists
if [ ! -f "$VENV_PYTHON" ]; then
    echo "Error: Python venv not found at $VENV_PYTHON"
    exit 1
fi

# Install/update ComfyUI requirements
COMFYUI_REQS="$COMFYUI_DIR/requirements.txt"
REQS_STAMP="$PLUGIN_ROOT/.requirements_installed"
if [ -f "$COMFYUI_REQS" ]; then
    if [ ! -f "$REQS_STAMP" ] || [ "$COMFYUI_REQS" -nt "$REQS_STAMP" ]; then
        echo "Installing ComfyUI requirements..."
        "$VENV_PYTHON" -m pip install -r "$COMFYUI_REQS" --quiet 2>&1 | tail -5
        touch "$REQS_STAMP"
    fi
fi

# Log file
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/comfyui.log"

echo "Starting ComfyUI..."
echo "Dir: $COMFYUI_DIR"
echo "Port: $PORT"
echo "Python: $VENV_PYTHON"
echo "Log: $LOG_FILE"

# Start ComfyUI
cd "$COMFYUI_DIR"
"$VENV_PYTHON" main.py --listen --port "$PORT" >> "$LOG_FILE" 2>&1 &

# Save PID
PID_DIR="$PROJECT_ROOT/pids"
mkdir -p "$PID_DIR"
echo $! > "$PID_DIR/comfyui.pid"

echo "ComfyUI started (PID: $(cat $PID_DIR/comfyui.pid))"
