#!/bin/bash
# Start Guaardvark Upscaling Service
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
SERVICE_PORT=8202

# Load env
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a; source "$PROJECT_ROOT/.env"; set +a
fi
export GUAARDVARK_ROOT="$PROJECT_ROOT"

# Check if already running
PID_FILE="$PROJECT_ROOT/pids/upscaling.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Upscaling Service already running (PID: $OLD_PID)"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

# Check port is free
if lsof -Pi :$SERVICE_PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Error: Port $SERVICE_PORT is already in use"
    exit 1
fi

# Activate venv
source "$PROJECT_ROOT/backend/venv/bin/activate"

# Install plugin deps into the shared backend venv.
# Filter torch*/xformers/flash/pynvml/numpy: core owns those (install_pytorch.sh +
# backend/constraints.txt). Blind `pip install -r` here used to pull numpy 2.x over
# the ML stack's numpy<2 pin and corrupt C extensions mid-restart.
if [ -z "${PIP_CONSTRAINT:-}" ] && [ -f "$PROJECT_ROOT/backend/constraints.txt" ]; then
    export PIP_CONSTRAINT="$PROJECT_ROOT/backend/constraints.txt"
fi
FILTERED_REQS="$(mktemp)"
# Match bare pins too (numpy<2.0, torch>=2.0.0) — `=`-only patterns miss `<`/`>`.
grep -v -iE '^(torch|torchvision|torchaudio|xformers|flash|pynvml|nvidia-ml-py|numpy)([<>=!~]|[[:space:]]|$)' \
    "$PLUGIN_ROOT/requirements.txt" > "$FILTERED_REQS" 2>/dev/null \
    || cp "$PLUGIN_ROOT/requirements.txt" "$FILTERED_REQS"
pip install -q -r "$FILTERED_REQS" 2>/dev/null || true
rm -f "$FILTERED_REQS"

# Log file
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/upscaling.log"

echo "Starting Upscaling Service..."
echo "Plugin dir: $PLUGIN_ROOT"
echo "Service port: $SERVICE_PORT"
echo "Log: $LOG_FILE"

# Start uvicorn. /health hands out the bearer token, so the bind stays on
# loopback unless GUAARDVARK_UPSCALING_HOST says otherwise.
cd "$PLUGIN_ROOT"
PYTHONPATH="$PLUGIN_ROOT:$PYTHONPATH" \
CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-0} \
PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True" \
python -m uvicorn service.app:app --host "${GUAARDVARK_UPSCALING_HOST:-127.0.0.1}" --port $SERVICE_PORT --workers 1 \
    >> "$LOG_FILE" 2>&1 &

# Save PID
PID_DIR="$PROJECT_ROOT/pids"
mkdir -p "$PID_DIR"
echo $! > "$PID_DIR/upscaling.pid"
echo "Upscaling Service started (PID: $(cat $PID_DIR/upscaling.pid))"

# Wait for health endpoint
echo "Waiting for health endpoint on port $SERVICE_PORT..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:$SERVICE_PORT/health" >/dev/null 2>&1; then
        echo "Upscaling Service health endpoint ready"
        exit 0
    fi
    sleep 1
done

echo "Warning: Health endpoint not responsive after 30s"
exit 0
