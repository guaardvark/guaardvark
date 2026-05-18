#!/bin/bash
# Start Guaardvark Vision Pipeline service
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
SERVICE_PORT=8201

# Load env
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a; source "$PROJECT_ROOT/.env"; set +a
fi
export GUAARDVARK_ROOT="$PROJECT_ROOT"

# Check if already running
PID_FILE="$PROJECT_ROOT/pids/vision_pipeline.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Vision Pipeline already running (PID: $OLD_PID)"
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

# Install requirements
pip install -q -r "$PLUGIN_ROOT/requirements.txt" 2>/dev/null || true

# Log file
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/vision_pipeline.log"

# Start agent virtual display if not already running
AGENT_DISPLAY_SCRIPT="$PROJECT_ROOT/scripts/start_agent_display.sh"
if [ -x "$AGENT_DISPLAY_SCRIPT" ]; then
    if ! pgrep -f "Xvfb :99" > /dev/null 2>&1; then
        echo "Starting agent virtual display..."
        bash "$AGENT_DISPLAY_SCRIPT" start 2>&1 | tail -5
    else
        echo "Agent virtual display already running"
    fi
fi

echo "Starting Vision Pipeline..."
echo "Plugin dir: $PLUGIN_ROOT"
echo "Service port: $SERVICE_PORT"
echo "Log: $LOG_FILE"

# Start uvicorn
cd "$PLUGIN_ROOT"
PYTHONPATH="$PLUGIN_ROOT:$PYTHONPATH" \
python -m uvicorn service.app:app --host 0.0.0.0 --port 8201 --workers 1 \
    >> "$LOG_FILE" 2>&1 &

# Save PID
PID_DIR="$PROJECT_ROOT/pids"
mkdir -p "$PID_DIR"
echo $! > "$PID_DIR/vision_pipeline.pid"
echo "Vision Pipeline started (PID: $(cat $PID_DIR/vision_pipeline.pid))"

# Wait for health endpoint
echo "Waiting for health endpoint on port $SERVICE_PORT..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:$SERVICE_PORT/health" >/dev/null 2>&1; then
        echo "Vision Pipeline health endpoint ready"
        exit 0
    fi
    sleep 1
done

echo "Warning: Health endpoint not responsive after 30s"
exit 0
