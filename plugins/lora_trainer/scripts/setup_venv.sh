#!/usr/bin/env bash
# Bootstrap the isolated torch venv for the lora_trainer plugin.
#
# Run once on a host with a real NVIDIA GPU (a 12-16 GB consumer card is enough).
# Auto-detects the driver's CUDA version and installs a matching torch wheel
# so that torch.cuda.is_available() returns True inside the venv at runtime.
# Takes ~5-10 min. Can be re-run.
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${PLUGIN_DIR}/venv-torch"
REQS="${PLUGIN_DIR}/requirements-torch.txt"

if [[ ! -f "${REQS}" ]]; then
    echo "ERROR: requirements-torch.txt missing at ${REQS}" >&2
    exit 1
fi

# Pip wheels (CUDA bundles especially) total ~4 GB. /tmp is tmpfs on most
# Linux setups and runs out of space mid-install. Force pip to spill to a
# disk-backed dir under the venv so the install doesn't OOM the tmpfs.
export TMPDIR="${VENV}/.tmp"
mkdir -p "${TMPDIR}"

if [[ ! -d "${VENV}" ]]; then
    echo "Creating venv at ${VENV}…"
    python3 -m venv "${VENV}"
fi

echo "Upgrading pip in venv-torch…"
"${VENV}/bin/pip" install --upgrade pip wheel

echo "Detecting host CUDA version for correct torch wheel..."
# Prefer the CUDA version reported by the installed NVIDIA driver.
# This is critical on Ubuntu 24.04 + RTX 40-series (like 4070 Ti SUPER) so
# the venv-torch can actually see cuda.is_available() == True at runtime.
HOST_CUDA=""
if command -v nvidia-smi >/dev/null 2>&1; then
    HOST_CUDA=$(nvidia-smi | grep -oP 'CUDA Version:\s*\K[0-9.]+' | head -1 || true)
fi

CU_INDEX="cu130"   # matches the working install on the development box (CUDA 13 driver)
# Pin the torch version that the live, known-good venv-torch actually runs. The old
# 2.5.1 pin did NOT match disk (the working venv has 2.11.0+cu130), so re-running this
# script would have downgraded torch and broken the real LoRA trainer. Verified via
# `plugins/lora_trainer/venv-torch/bin/python -c "import torch; print(torch.__version__)"`.
TORCH_VER="2.11.0"

if [[ -n "$HOST_CUDA" ]]; then
    # Turn 12.6 into cu126, 12.4 into cu124, 13.x into cu130 etc.
    MAJOR_MINOR=$(echo "$HOST_CUDA" | cut -d. -f1-2 | tr -d '.')
    if [[ "$MAJOR_MINOR" -ge 130 ]]; then
        CU_INDEX="cu130"
    elif [[ "$MAJOR_MINOR" -ge 128 ]]; then
        CU_INDEX="cu128"
    elif [[ "$MAJOR_MINOR" -ge 124 ]]; then
        CU_INDEX="cu124"
    elif [[ "$MAJOR_MINOR" -ge 121 ]]; then
        CU_INDEX="cu121"
    fi
    echo "Host reports CUDA $HOST_CUDA -> using torch index $CU_INDEX"
else
    echo "WARNING: nvidia-smi did not report CUDA version. Defaulting to $CU_INDEX."
    echo "         Please ensure proprietary NVIDIA drivers are installed:"
    echo "         sudo ubuntu-drivers autoinstall && sudo reboot"
fi

echo "Installing torch + torchvision (CUDA wheels)…"
# Use the detected index. torchvision must come from the same index.
"${VENV}/bin/pip" install \
    "torch==${TORCH_VER}" torchvision \
    --index-url "https://download.pytorch.org/whl/${CU_INDEX}"

echo "Installing remaining requirements…"
"${VENV}/bin/pip" install -r "${REQS}"

echo "Verifying CUDA in venv-torch…"
"${VENV}/bin/python" -c '
import torch, sys
print("Python torch:", torch.__version__)
print("CUDA available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("Device:", torch.cuda.get_device_name(0))
    print("Build CUDA:", getattr(torch.version, "cuda", "n/a"))
else:
    print("ERROR: CUDA not visible inside venv-torch.")
    print("Fix: ensure nvidia-smi works on host, then re-run this script.")
    sys.exit(1)
'

echo "Done. The plugin will auto-pick the real backend on next train dispatch from CastMemberPage."
echo "If you still see 'CUDA not available' at runtime after this, reboot the machine."
