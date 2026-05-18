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

# Audio Foundry has TWO sibling venvs because chatterbox-tts and ACE-Step
# pin mutually-incompatible transformers versions (5.2 vs 4.50). Both also
# conflict with the main backend/venv (ComfyUI / vision_pipeline want
# torch 2.11, transformers <5). Two-venv split keeps everyone honest:
#   venv/        -> FastAPI dispatcher + voice_gen (chatterbox+kokoro) + audio_fx (SAO)
#   venv-music/  -> ACE-Step only; driven via subprocess from music_gen_acestep.py
ensure_venv() {
    local venv_dir="$1"
    local reqs_file="$2"
    local label="$3"

    if [ ! -f "$venv_dir/bin/activate" ]; then
        echo "$label venv missing — bootstrapping at $venv_dir"
        python3 -m venv "$venv_dir" || { echo "Error: failed to create $label venv"; exit 1; }
        # shellcheck disable=SC1091
        source "$venv_dir/bin/activate"
        pip install --upgrade pip setuptools wheel
        pip install -r "$reqs_file" || { echo "Error: $label requirements install failed"; exit 1; }
        touch "$venv_dir/.deps_installed"
        deactivate
    else
        # shellcheck disable=SC1091
        source "$venv_dir/bin/activate"
        local sentinel="$venv_dir/.deps_installed"
        if [ ! -f "$sentinel" ] || [ "$reqs_file" -nt "$sentinel" ]; then
            echo "$label requirements changed — updating..."
            pip install -r "$reqs_file" || { echo "Error: $label requirements update failed"; exit 1; }
            touch "$sentinel"
        fi
        deactivate
    fi
}

PLUGIN_VENV="$PLUGIN_ROOT/venv"
MUSIC_VENV="$PLUGIN_ROOT/venv-music"

ensure_venv "$PLUGIN_VENV"  "$PLUGIN_ROOT/requirements.txt"        "audio_foundry"
ensure_venv "$MUSIC_VENV"   "$PLUGIN_ROOT/requirements-music.txt"  "audio_foundry-music"

# audio_fx (Stable Audio Open) needs diffusers >= 0.30, but chatterbox-tts
# pins diffusers == 0.29.0 in its setup.py. Listing both pins together in
# requirements.txt makes pip's strict resolver fail with ResolutionImpossible.
# So requirements.txt only has chatterbox; we do a forced upgrade pass here.
# pip prints a "dependency conflict" warning that is benign — chatterbox's
# actual usage is limited to scheduler classes that have been stable across
# diffusers 0.29 → 0.37.
DIFFUSERS_UPGRADE_SENTINEL="$PLUGIN_VENV/.diffusers_upgraded"
DIFFUSERS_REQUIRED='diffusers>=0.30,<0.40'
# Re-run the upgrade whenever requirements.txt has been edited (which would
# have just triggered a `pip install -r` that downgrades diffusers back to
# chatterbox's 0.29.0 pin). The sentinel lets idempotent restarts skip the
# step on cold-cache cases.
if [ ! -f "$DIFFUSERS_UPGRADE_SENTINEL" ] || [ "$PLUGIN_ROOT/requirements.txt" -nt "$DIFFUSERS_UPGRADE_SENTINEL" ]; then
    echo "Forcing diffusers upgrade for Stable Audio Open compatibility..."
    # shellcheck disable=SC1091
    source "$PLUGIN_VENV/bin/activate"
    pip install --upgrade "$DIFFUSERS_REQUIRED" || { echo "Error: diffusers upgrade failed"; exit 1; }
    touch "$DIFFUSERS_UPGRADE_SENTINEL"
    deactivate
fi

# Activate the main venv for uvicorn — music venv is invoked on demand via
# subprocess by backends/music_gen_acestep.py.
# shellcheck disable=SC1091
source "$PLUGIN_VENV/bin/activate"

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
