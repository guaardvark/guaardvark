#!/bin/bash
# Start the Guaardvark Video Editor plugin service.
# Mirrors audio_foundry / vision_pipeline: own venv, uvicorn, pid file, health wait.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
SERVICE_PORT=8207

if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a; source "$PROJECT_ROOT/.env"; set +a
fi
export GUAARDVARK_ROOT="$PROJECT_ROOT"

PID_FILE="$PROJECT_ROOT/pids/video_editor.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Video Editor already running (PID: $OLD_PID)"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

if lsof -Pi :$SERVICE_PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Error: Port $SERVICE_PORT is already in use"
    exit 1
fi

# Own venv — librosa drags numba and a strict numpy floor that conflicts with
# the backend's pins. Same reason audio_foundry runs its own venv.
PLUGIN_VENV="$PLUGIN_ROOT/venv"

ensure_venv() {
    if [ ! -f "$PLUGIN_VENV/bin/activate" ]; then
        echo "video_editor venv missing — bootstrapping at $PLUGIN_VENV"
        python3 -m venv "$PLUGIN_VENV" || { echo "Error: venv creation failed"; exit 1; }
        # shellcheck disable=SC1091
        source "$PLUGIN_VENV/bin/activate"
        pip install --upgrade pip setuptools wheel
        pip install -r "$PLUGIN_ROOT/requirements.txt" || { echo "Error: pip install failed"; exit 1; }
        touch "$PLUGIN_VENV/.deps_installed"
        deactivate
    else
        # shellcheck disable=SC1091
        source "$PLUGIN_VENV/bin/activate"
        local sentinel="$PLUGIN_VENV/.deps_installed"
        if [ ! -f "$sentinel" ] || [ "$PLUGIN_ROOT/requirements.txt" -nt "$sentinel" ]; then
            echo "video_editor requirements changed — updating..."
            pip install -r "$PLUGIN_ROOT/requirements.txt" || { echo "Error: pip update failed"; exit 1; }
            touch "$sentinel"
        fi
        deactivate
    fi
}

ensure_venv

# Cache the resolved melt path. The snap binary at /snap/shotcut/current/bin/melt
# is the UNWRAPPED binary that fails to load libmlt; we want the WRAPPER script
# at /snap/shotcut/current/melt, which sets LD_LIBRARY_PATH.
MELT_CANDIDATE=$(readlink -f /snap/shotcut/current/melt 2>/dev/null || true)
if [ -z "$MELT_CANDIDATE" ] || [ ! -x "$MELT_CANDIDATE" ]; then
    MELT_CANDIDATE=$(command -v melt 2>/dev/null || true)
fi
if [ -n "$MELT_CANDIDATE" ]; then
    echo "Resolved melt: $MELT_CANDIDATE"
    export VIDEO_EDITOR_MELT_PATH="$MELT_CANDIDATE"
else
    echo "Warning: melt not found — render-to-MP4 endpoint will return error until installed."
fi

# shellcheck disable=SC1091
source "$PLUGIN_VENV/bin/activate"

LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/video_editor.log"

echo "Starting Video Editor..."
echo "Plugin dir: $PLUGIN_ROOT"
echo "Service port: $SERVICE_PORT"
echo "Log: $LOG_FILE"

cd "$PLUGIN_ROOT"
PYTHONPATH="$PLUGIN_ROOT:$PYTHONPATH" \
python -m uvicorn service.app:app --host 0.0.0.0 --port "$SERVICE_PORT" --workers 1 \
    >> "$LOG_FILE" 2>&1 &

PID_DIR="$PROJECT_ROOT/pids"
mkdir -p "$PID_DIR"
echo $! > "$PID_DIR/video_editor.pid"
echo "Video Editor started (PID: $(cat "$PID_DIR/video_editor.pid"))"

echo "Waiting for health endpoint on port $SERVICE_PORT..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:$SERVICE_PORT/health" >/dev/null 2>&1; then
        echo "Video Editor health endpoint ready"
        exit 0
    fi
    sleep 1
done

echo "Warning: Health endpoint not responsive after 30s"
exit 0
