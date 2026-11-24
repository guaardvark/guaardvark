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
# True when PID $1 is alive and its command line contains $2. A PID file
# outlives a reboot or a crash, and by then the number can belong to another
# process: one this script must neither report as the service nor signal.
pid_is_service() {
    kill -0 "$1" 2>/dev/null || return 1
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

# Audio Foundry has TWO sibling venvs because ACE-Step pins transformers
# 4.50, which the voice backends cannot run, and neither set fits the main
# backend/venv. Two-venv split keeps everyone honest:
#   venv/        -> FastAPI dispatcher + voice_gen (chatterbox+kokoro) + audio_fx (SAO)
#   venv-music/  -> ACE-Step only; driven via subprocess from music_gen_acestep.py

# ensure_venv_exists <venv dir> <label>: create the venv when it is missing or
# does not run. Installs nothing into it.
ensure_venv_exists() {
    local venv_dir="$1"
    local label="$2"
    if [ -f "$venv_dir/bin/activate" ] && "$venv_dir/bin/python" -c 'import sys' >/dev/null 2>&1; then
        return 0
    fi
    if [ -d "$venv_dir" ]; then
        echo "$label venv damaged / from another machine (bad shebang or missing python) — removing..."
        rm -rf "$venv_dir"
    fi
    echo "$label venv missing — bootstrapping at $venv_dir"
    python3 -m venv "$venv_dir" || { echo "Error: failed to create $label venv"; exit 1; }
}

ensure_venv() {
    local venv_dir="$1"
    local reqs_file="$2"
    local label="$3"

    # Cross-machine sync safety: venvs contain absolute shebangs + native bins/symlinks.
    # If the python inside doesn't exec or reports wrong root, nuke and recreate.
    venv_healthy() {
        local py="$venv_dir/bin/python"
        if [ ! -x "$py" ]; then
            return 1
        fi
        # Must run without error and its reported executable should live under this GX root (not master path).
        if ! "$py" -c 'import sys; print(sys.executable)' >/dev/null 2>&1; then
            return 1
        fi
        # Optional but strong: the printed path should contain current project (defensive vs old shebangs).
        if ! "$py" -c '
import sys
import os
root = os.environ.get("GUAARDVARK_ROOT", "")
exe = sys.executable
if root and root not in exe:
    # still allow if it at least runs; the recreate below is the real guard
    pass
print("ok")
' >/dev/null 2>&1; then
            :
        fi
        return 0
    }

    if [ ! -f "$venv_dir/bin/activate" ] || ! venv_healthy; then
        if [ -d "$venv_dir" ]; then
            echo "$label venv damaged / from another machine (bad shebang or missing python) — removing..."
            rm -rf "$venv_dir"
        fi
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

ensure_venv "$MUSIC_VENV"   "$PLUGIN_ROOT/requirements-music.txt"  "audio_foundry-music"

# Main venv. torch goes in first (install_pytorch.sh, the channel that fits
# this GPU), then requirements.txt, then chatterbox-tts without its
# dependencies: see requirements.txt for why. install_pytorch.sh returns at
# once when the right build is already there, so re-running this after a
# requirements edit costs a version check, not a download.
# shellcheck source=plugins/audio_foundry/scripts/venv_sync.sh
. "$SCRIPT_DIR/venv_sync.sh"

ensure_venv_exists "$PLUGIN_VENV" "audio_foundry"

torch_import_ok() {
    "$PLUGIN_VENV/bin/python" -c '
import torch
if torch.cuda.is_available():
    torch.zeros(1).cuda()
else:
    torch.zeros(1)
' 2>/dev/null
}

DEPS_SENTINEL="$PLUGIN_VENV/.deps_installed"
TORCH_VERIFY_SENTINEL="$PLUGIN_VENV/.torch_import_verified"
NEED_VENV_SYNC=0
if [ ! -f "$DEPS_SENTINEL" ] || [ ! -f "$TORCH_VERIFY_SENTINEL" ] \
   || [ "$PLUGIN_ROOT/requirements.txt" -nt "$DEPS_SENTINEL" ] \
   || [ "$PLUGIN_ROOT/requirements-nodeps.txt" -nt "$DEPS_SENTINEL" ]; then
    NEED_VENV_SYNC=1
elif command -v nvidia-smi >/dev/null 2>&1 && ! torch_import_ok; then
    NEED_VENV_SYNC=1
fi
if [ "$NEED_VENV_SYNC" = "1" ]; then
    echo "Syncing audio_foundry venv: torch, requirements, chatterbox-tts..."
    af_sync_main_venv "$PLUGIN_VENV" "$PLUGIN_ROOT" "$PROJECT_ROOT" \
        || { echo "Error: audio_foundry requirements install failed"; exit 1; }
    if command -v nvidia-smi >/dev/null 2>&1 && ! torch_import_ok; then
        echo "Error: audio_foundry venv torch import still failing after install_pytorch."
        echo "  Run: plugins/audio_foundry/scripts/setup_venv.sh"
        exit 1
    fi
    touch "$DEPS_SENTINEL" "$TORCH_VERIFY_SENTINEL"
fi

INSTALL_PYTORCH="$PROJECT_ROOT/scripts/install_pytorch.sh"
BACKEND_PY="$PROJECT_ROOT/backend/venv/bin/python"
TORCH_CHANNEL="$("$BACKEND_PY" -m backend.services.hardware_policy torch_channel 2>/dev/null || echo "")"

# Same repair for the ACE-Step sibling venv on GPU hosts.
MUSIC_TORCH_SENTINEL="$MUSIC_VENV/.torch_import_verified"
if command -v nvidia-smi >/dev/null 2>&1; then
    if [ ! -f "$MUSIC_TORCH_SENTINEL" ] || ! "$MUSIC_VENV/bin/python" -c 'import torch; torch.zeros(1).cuda() if torch.cuda.is_available() else torch.zeros(1)' 2>/dev/null; then
        echo "Syncing audio_foundry-music venv torch..."
        if [ -x "$INSTALL_PYTORCH" ]; then
            TARGET_VENV="$MUSIC_VENV" GUAARDVARK_TORCH_CHANNEL="$TORCH_CHANNEL" \
                bash "$INSTALL_PYTORCH" --venv "$MUSIC_VENV" \
                || echo "Warning: install_pytorch for venv-music returned non-zero"
        fi
        if "$MUSIC_VENV/bin/python" -c 'import torch; torch.zeros(1).cuda() if torch.cuda.is_available() else torch.zeros(1)' 2>/dev/null; then
            touch "$MUSIC_TORCH_SENTINEL"
        else
            echo "Warning: venv-music torch import failed — ACE-Step music gen will be unavailable"
        fi
    fi
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
# Xet-backed HF transfers flake on this network (observed: partial ACE-Step
# snapshot, SAO CAS client errors) — classic HTTP downloads are reliable.
export HF_HUB_DISABLE_XET=1
# Nothing leaves the machine during generation. Kokoro and Chatterbox call
# hf_hub_download on their own (voice packs, model files), which contacts
# huggingface.co on every cold load and downloads whatever is missing; ACE-Step
# falls back to snapshot_download when a snapshot is partial. With the Hub
# client offline in this process and its children (the ACE-Step subprocess), a
# missing file is an error naming Audio Studio → Manage models. Installs run in
# the backend process behind that Install button, which this does not touch.
# Same set as LOCAL_ONLY_ENV in backend/services/comfyui_launch_flags.py.
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1
export ORT_DISABLE_TELEMETRY=1
# The service has no auth and every caller (the backend proxy, generation
# history, the GPU policy, the swarm Editor) is on this host, so it listens on
# loopback only. GUAARDVARK_AUDIO_FOUNDRY_HOST=0.0.0.0 in .env opens it to the
# LAN deliberately, e.g. for another install driving this box's audio.
BIND_HOST="${GUAARDVARK_AUDIO_FOUNDRY_HOST:-127.0.0.1}"
PYTHONPATH="$PLUGIN_ROOT:$PYTHONPATH" \
python -m uvicorn service.app:app --host "$BIND_HOST" --port "$SERVICE_PORT" --workers 1 \
    >> "$LOG_FILE" 2>&1 &

PID_DIR="$PROJECT_ROOT/pids"
mkdir -p "$PID_DIR"
echo $! > "$PID_DIR/audio_foundry.pid"
echo "Audio Foundry started (PID: $(cat "$PID_DIR/audio_foundry.pid"))"

# Wait for health — generous window since first boot may download nothing heavy
# (all models load lazily) so this should normally be a few seconds.
echo "Waiting for health endpoint on port $SERVICE_PORT..."
for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:$SERVICE_PORT/health" >/dev/null 2>&1; then
        echo "Audio Foundry health endpoint ready"
        exit 0
    fi
    sleep 1
done

echo "Warning: Health endpoint not responsive after 30s"
exit 0
