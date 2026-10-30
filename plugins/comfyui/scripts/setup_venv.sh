#!/bin/bash
# plugins/comfyui/scripts/setup_venv.sh
# Build ComfyUI its own Python environment at plugins/comfyui/venv, on a newer
# PyTorch than the backend's, and leave the backend venv as it is.
#
# Why: Ampere/Ada cards get the cu124 channel for the backend (hardware_policy),
# which stops at torch 2.6. Measured 2026-10-30 on a 16 GB card, ComfyUI 0.43.0:
# on torch 2.15 MOSS-Image renders 84 -> 40 s, MiniMax H3 140 -> 92 s, and FLUX
# 3 Klein's prompt upsampler runs (~93 tokens/s; 1.1 s per token on 2.6), while
# the backend's own Z-Image pipeline was ~40% slower on 2.15, so only ComfyUI
# moves.
#
# Used when GUAARDVARK_COMFYUI_OWN_VENV=1 (start.sh and the Update action read
# it). Idempotent: a venv that imports torch, runs a CUDA kernel and imports
# comfy_kitchen is left alone unless --rebuild is given.
#
# Channel: cu132 (torch 2.15) when the driver supports CUDA 13.2, else cu128
# (torch 2.11). Cards below compute 7.5 keep the shared backend venv: the CUDA
# 13 wheels drop them and the cu128 builds have not been checked on them.
set -u
PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
[ -n "$PLUGIN_DIR" ] && [ -d "$PLUGIN_DIR" ] || { echo "FATAL: PLUGIN_DIR unresolved" >&2; exit 1; }
[ -n "$REPO_ROOT" ] && [ -d "$REPO_ROOT" ] || { echo "FATAL: REPO_ROOT unresolved" >&2; exit 1; }
VENV="$PLUGIN_DIR/venv"
PY="${PYTHON_CMD:-python3.12}"
CONSTRAINTS="$REPO_ROOT/backend/constraints.txt"

log() { echo "  [comfyui/setup_venv] $*"; }

verify_venv() {
    [ -x "$VENV/bin/python" ] || return 1
    "$VENV/bin/python" - <<'PYEOF'
import sys
try:
    import torch
    assert torch.cuda.is_available(), "CUDA not available"
    torch.zeros(1).cuda()
    import comfy_kitchen, torchsde, av  # noqa: F401
    print(f"torch {torch.__version__} on {torch.cuda.get_device_name(0)}")
except Exception as e:
    print(f"VERIFY_FAIL: {e!r}", file=sys.stderr)
    sys.exit(1)
PYEOF
}

if [ "${1:-}" != "--rebuild" ] && verify_venv; then
    log "venv already good; nothing to do (pass --rebuild to build it again)"
    exit 0
fi

command -v nvidia-smi >/dev/null 2>&1 || { log "no NVIDIA driver found; ComfyUI keeps the backend venv"; exit 2; }
CAP="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -n 1 | tr -d ' ')"
DRIVER_CUDA="$(nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: *\([0-9.]*\).*/\1/p' | head -n 1)"
cap_ok="$(python3 -c "import sys; print(1 if float(sys.argv[1] or 0) >= 7.5 else 0)" "$CAP" 2>/dev/null || echo 0)"
if [ "$cap_ok" != "1" ]; then
    log "GPU compute capability ${CAP:-unknown} is below 7.5; ComfyUI keeps the backend venv"
    exit 2
fi
CHANNEL="$(python3 -c "
import sys
v = tuple(int(x) for x in (sys.argv[1] or '0.0').split('.')[:2])
print('cu132' if v >= (13, 2) else 'cu128' if v >= (12, 8) else '')" "$DRIVER_CUDA")"
if [ -z "$CHANNEL" ]; then
    log "driver supports CUDA ${DRIVER_CUDA:-unknown}; a newer torch needs 12.8 or later. ComfyUI keeps the backend venv"
    exit 2
fi
log "GPU compute $CAP, driver CUDA $DRIVER_CUDA -> torch channel $CHANNEL"

rm -rf "$VENV"
"$PY" -m venv "$VENV" || { log "venv create failed"; exit 1; }
"$VENV/bin/python" -m pip install --upgrade pip >/dev/null 2>&1
export PIP_CONSTRAINT="$CONSTRAINTS"
# torchaudio has no cu132 build; PyPI's serves the audio nodes (MiniMax H3).
"$VENV/bin/python" -m pip install torch torchvision torchaudio \
    --index-url "https://download.pytorch.org/whl/$CHANNEL" --extra-index-url https://pypi.org/simple \
    || { log "torch install failed"; exit 1; }

# ComfyUI core + custom-node requirements, the same installer start.sh runs.
VENV_PYTHON="$VENV/bin/python"
# The install stamps live in the plugin folder, shared with the backend venv;
# a fresh venv gets a full pass.
GUAARDVARK_HEAL_FORCE=1
export VENV_PYTHON GUAARDVARK_HEAL_FORCE
# shellcheck source=/dev/null
source "$PLUGIN_DIR/scripts/install_deps.sh"
install_comfyui_python_deps || { log "ComfyUI requirements install failed"; exit 1; }

if verify_venv; then
    log "done: set GUAARDVARK_COMFYUI_OWN_VENV=1 in .env and restart the ComfyUI plugin"
    exit 0
fi
log "the new venv failed its check; ComfyUI keeps the backend venv"
exit 1
