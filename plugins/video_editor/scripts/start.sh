#!/bin/bash
# Start the Guaardvark Video Editor plugin service.
# Mirrors audio_foundry / vision_pipeline: own venv, uvicorn, pid file, health wait.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
# shellcheck source=scripts/lib/proc_owner.sh
. "$PROJECT_ROOT/scripts/lib/proc_owner.sh"   # proc_alive
SERVICE_PORT=8207

if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a; source "$PROJECT_ROOT/.env"; set +a
fi
export GUAARDVARK_ROOT="$PROJECT_ROOT"

PID_FILE="$PROJECT_ROOT/pids/video_editor.pid"
# True when PID $1 is alive and its command line contains $2. A PID file
# outlives a reboot or a crash, and by then the number can belong to another
# process: one this script must neither report as the service nor signal.
pid_is_service() {
    proc_alive "$1" || return 1
    local args=""
    if [ -r "/proc/$1/cmdline" ]; then
        args=$(tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null) || args=""
    else
        args=$(ps -o args= -p "$1" 2>/dev/null) || args=""
    fi
    [ -n "$args" ] || return 0  # command line unreadable: trust the PID file
    case "$args" in *"$2"*) return 0 ;; esac
    return 1
}

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if pid_is_service "$OLD_PID" "--port $SERVICE_PORT"; then
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

bash "$SCRIPT_DIR/setup_venv.sh" || { echo "Error: setup_venv.sh failed"; exit 1; }

# Cache the resolved melt path with cross-platform support.
# Priority: explicit env > snap wrapper (Linux) > PATH > platform candidates.
# The snap /snap/shotcut/current/melt (not /.../bin/melt) is required for LD_LIBRARY_PATH.
if [ -z "${VIDEO_EDITOR_MELT_PATH:-}" ]; then
    MELT_CANDIDATE=""
    # 1. Snap (original default on Ubuntu)
    if [ -z "$MELT_CANDIDATE" ]; then
        MELT_CANDIDATE=$(readlink -f /snap/shotcut/current/melt 2>/dev/null || true)
    fi
    # 2. PATH
    if [ -z "$MELT_CANDIDATE" ] || [ ! -x "$MELT_CANDIDATE" ]; then
        MELT_CANDIDATE=$(command -v melt 2>/dev/null || true)
    fi
    # 3. macOS common locations (Homebrew / Applications)
    if [ -z "$MELT_CANDIDATE" ] || [ ! -x "$MELT_CANDIDATE" ]; then
        for p in \
            /opt/homebrew/bin/melt \
            /usr/local/bin/melt \
            "/Applications/Shotcut.app/Contents/MacOS/melt"; do
            if [ -x "$p" ]; then MELT_CANDIDATE="$p"; break; fi
        done
    fi
    # 4. Other Linux locations (flatpak, apt, etc.)
    if [ -z "$MELT_CANDIDATE" ] || [ ! -x "$MELT_CANDIDATE" ]; then
        for p in \
            /usr/bin/melt \
            /opt/shotcut/melt; do
            if [ -x "$p" ]; then MELT_CANDIDATE="$p"; break; fi
        done
    fi

    if [ -n "$MELT_CANDIDATE" ] && [ -x "$MELT_CANDIDATE" ]; then
        echo "Resolved melt: $MELT_CANDIDATE"
        export VIDEO_EDITOR_MELT_PATH="$MELT_CANDIDATE"
    else
        echo "Warning: melt not found — render-to-MP4 endpoint will return error until installed."
        echo "  macOS: brew install --cask shotcut   (or brew install mlt)"
        echo "  Linux: apt install melt | flatpak install ...shotcut | snap install shotcut"
        echo "  Override: VIDEO_EDITOR_MELT_PATH=/path/to/melt or config.yaml melt.path"
    fi
else
    echo "Using VIDEO_EDITOR_MELT_PATH from environment: $VIDEO_EDITOR_MELT_PATH"
fi

# shellcheck disable=SC1091
source "$PLUGIN_VENV/bin/activate"

# plugin.json lists auto-editor as a required binary; requirements.txt puts it
# in this venv, and config.yaml's auto_editor.path may name another install.
# Like melt above, a missing one is reported, not fatal: the beat-sync and
# compose endpoints work without it. The plugin manager keeps a "Warning:"
# line that says what will not work ("will fail", "until ... installed") as
# the card's status message.
AUTO_EDITOR_BIN="$(python - "$PLUGIN_ROOT/config.yaml" <<'PY' 2>/dev/null || true
import sys
try:
    import yaml
    with open(sys.argv[1], encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}
    print((config.get("auto_editor") or {}).get("path") or "auto-editor")
except Exception:
    print("auto-editor")
PY
)"
AUTO_EDITOR_BIN="${AUTO_EDITOR_BIN:-auto-editor}"
if ! command -v "$AUTO_EDITOR_BIN" >/dev/null 2>&1; then
    echo "Warning: auto-editor not found ($AUTO_EDITOR_BIN) — the silence-trim endpoint (/auto-editor/trim) will fail until it is installed."
    echo "  Install: $PLUGIN_VENV/bin/pip install -r $PLUGIN_ROOT/requirements.txt, or set auto_editor.path in config.yaml"
fi

LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/video_editor.log"

echo "Starting Video Editor..."
echo "Plugin dir: $PLUGIN_ROOT"
echo "Service port: $SERVICE_PORT"
echo "Log: $LOG_FILE"

# The service has no auth and every caller (the backend proxy, the music video
# and render tasks) is on this host, so it listens on loopback only.
# GUAARDVARK_VIDEO_EDITOR_HOST=0.0.0.0 in .env opens it to the LAN deliberately.
BIND_HOST="${GUAARDVARK_VIDEO_EDITOR_HOST:-127.0.0.1}"
cd "$PLUGIN_ROOT"
PYTHONPATH="$PLUGIN_ROOT:$PYTHONPATH" \
python -m uvicorn service.app:app --host "$BIND_HOST" --port "$SERVICE_PORT" --workers 1 \
    --no-access-log \
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
