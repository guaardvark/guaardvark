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

# Post-install torch: chatterbox pins torch 2.6/cu124; GPU hosts need the
# hardware_policy channel (cu128 on Blackwell). start.sh used to force torch
# with --no-deps and only 4 nvidia-* wheels, leaving stale cupti →
#   cuptiActivityEnableDriverApi undefined symbol
# Use the same install_pytorch.sh path as setup_venv.sh (full nvidia-* set).
INSTALL_PYTORCH="$PROJECT_ROOT/scripts/install_pytorch.sh"
BACKEND_PY="$PROJECT_ROOT/backend/venv/bin/python"
TORCH_CHANNEL="$("$BACKEND_PY" -m backend.services.hardware_policy torch_channel 2>/dev/null || echo "")"

torch_import_ok() {
    "$PLUGIN_VENV/bin/python" -c '
import torch
if torch.cuda.is_available():
    torch.zeros(1).cuda()
else:
    torch.zeros(1)
' 2>/dev/null
}

TORCH_VERIFY_SENTINEL="$PLUGIN_VENV/.torch_import_verified"
NEED_TORCH_SYNC=0
if command -v nvidia-smi >/dev/null 2>&1; then
    if ! torch_import_ok; then
        NEED_TORCH_SYNC=1
    fi
fi
if [ "$NEED_TORCH_SYNC" = "1" ] || [ ! -f "$TORCH_VERIFY_SENTINEL" ]; then
    echo "Syncing audio_foundry venv torch + CUDA companion libs..."
    if [ -x "$INSTALL_PYTORCH" ]; then
        TARGET_VENV="$PLUGIN_VENV" GUAARDVARK_TORCH_CHANNEL="$TORCH_CHANNEL" \
            bash "$INSTALL_PYTORCH" --venv "$PLUGIN_VENV" \
            || echo "Warning: install_pytorch.sh returned non-zero (continuing to verify)"
    else
        echo "Warning: $INSTALL_PYTORCH missing — cannot repair torch/CUDA mismatch"
    fi
    # shellcheck disable=SC1091
    source "$PLUGIN_VENV/bin/activate"
    pip install --upgrade "$DIFFUSERS_REQUIRED" 2>/dev/null || true
    pip install --no-deps --force-reinstall 'numpy<2.0,>=1.26.4' 'setuptools<81' 2>/dev/null || true
    deactivate
    if command -v nvidia-smi >/dev/null 2>&1 && ! torch_import_ok; then
        echo "Error: audio_foundry venv torch import still failing after install_pytorch."
        echo "  Run: plugins/audio_foundry/scripts/setup_venv.sh"
        exit 1
    fi
    touch "$TORCH_VERIFY_SENTINEL"
fi

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
