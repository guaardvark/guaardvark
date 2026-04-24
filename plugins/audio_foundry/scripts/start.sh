#!/bin/bash
# Start Guaardvark Audio Foundry service.
# Matches the vision_pipeline / swarm plugin pattern: uvicorn, pid file, health wait.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
SERVICE_PORT=8206

# Load env from project root (if present)
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a; source "$PROJECT_ROOT/.env"; set +a
fi
export GUAARDVARK_ROOT="$PROJECT_ROOT"

# Check if already running — idempotent re-start
PID_FILE="$PROJECT_ROOT/pids/audio_foundry.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Audio Foundry already running (PID: $OLD_PID)"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

# Port conflict check — fail fast
if lsof -Pi :$SERVICE_PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Error: Port $SERVICE_PORT is already in use"
    exit 1
fi

# Activate shared backend venv (audio model libs go here as each backend is wired)
source "$PROJECT_ROOT/backend/venv/bin/activate"

# Ensure core service deps exist — backend-specific deps install lazily on first use
pip install -q -r "$PLUGIN_ROOT/requirements.txt" 2>/dev/null || true
pip install -q fastapi uvicorn 2>/dev/null || true

# Log setup
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/audio_foundry.log"

echo "Starting Audio Foundry..."
echo "Plugin dir: $PLUGIN_ROOT"
echo "Service port: $SERVICE_PORT"
echo "Log: $LOG_FILE"

cd "$PLUGIN_ROOT"
PYTHONPATH="$PLUGIN_ROOT:$PYTHONPATH" \
python -m uvicorn service.app:app --host 0.0.0.0 --port "$SERVICE_PORT" --workers 1 \
    >> "$LOG_FILE" 2>&1 &

PID_DIR="$PROJECT_ROOT/pids"
mkdir -p "$PID_DIR"
echo $! > "$PID_DIR/audio_foundry.pid"
echo "Audio Foundry started (PID: $(cat "$PID_DIR/audio_foundry.pid"))"

# Wait for health — generous window since first boot may download nothing heavy
# (all models load lazily) so this should normally be a few seconds.
echo "Waiting for health endpoint on port $SERVICE_PORT..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:$SERVICE_PORT/health" >/dev/null 2>&1; then
        echo "Audio Foundry health endpoint ready"
        exit 0
    fi
    sleep 1
done

echo "Warning: Health endpoint not responsive after 30s"
exit 0
