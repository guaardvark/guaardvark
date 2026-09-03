#!/bin/bash
set -e

# Resolve paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
SERVICE_PORT=8210

# Load env
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a; source "$PROJECT_ROOT/.env"; set +a
fi
export GUAARDVARK_ROOT="$PROJECT_ROOT"

# Check if already running
PID_FILE="$PROJECT_ROOT/pids/swarm.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Swarm service already running (PID: $OLD_PID)"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

# Check port
if lsof -Pi :$SERVICE_PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Error: Port $SERVICE_PORT is already in use"
    exit 1
fi

# Setup venv
source "$PROJECT_ROOT/backend/venv/bin/activate"
# Plugin deps land in the shared backend venv; keep the project's pip
# constraints on so a plugin pass cannot move numpy/opencv off the ML stack's pins.
if [ -z "${PIP_CONSTRAINT:-}" ] && [ -f "$PROJECT_ROOT/backend/constraints.txt" ]; then
    export PIP_CONSTRAINT="$PROJECT_ROOT/backend/constraints.txt"
fi
pip install -q -r "$PLUGIN_ROOT/requirements.txt" 2>/dev/null || true
pip install -q fastapi uvicorn 2>/dev/null || true

# Start service
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/swarm.log"

# Bind loopback by default — the sidecar is reached via the Flask proxy, never
# directly. Overridable with SWARM_BIND_HOST but do not expose to the LAN.
BIND_HOST="${SWARM_BIND_HOST:-127.0.0.1}"

cd "$PLUGIN_ROOT"
PYTHONPATH="$PLUGIN_ROOT:$PYTHONPATH" \
python -m uvicorn service.app:app --host "$BIND_HOST" --port $SERVICE_PORT --workers 1 \
    >> "$LOG_FILE" 2>&1 &

PID_DIR="$PROJECT_ROOT/pids"
mkdir -p "$PID_DIR"
echo $! > "$PID_DIR/swarm.pid"

# Wait for health
echo "Waiting for swarm service on port $SERVICE_PORT..."
for i in $(seq 1 15); do
    if curl -sf "http://localhost:$SERVICE_PORT/health" >/dev/null 2>&1; then
        echo "Swarm service ready"
        exit 0
    fi
    sleep 1
done

echo "Warning: Health endpoint not responsive after 15s"
exit 0
