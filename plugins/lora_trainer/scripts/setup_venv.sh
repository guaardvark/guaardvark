#!/usr/bin/env bash
# Bootstrap the isolated torch venv for the lora_trainer plugin.
#
# Run once on a host with CUDA 12+. Takes ~5-10 min depending on bandwidth.
# Can re-run safely (uses --upgrade if requested).
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${PLUGIN_DIR}/venv-torch"
REQS="${PLUGIN_DIR}/requirements-torch.txt"

if [[ ! -f "${REQS}" ]]; then
    echo "ERROR: requirements-torch.txt missing at ${REQS}" >&2
    exit 1
fi

if [[ ! -d "${VENV}" ]]; then
    echo "Creating venv at ${VENV}…"
    python3 -m venv "${VENV}"
fi

echo "Upgrading pip in venv-torch…"
"${VENV}/bin/pip" install --upgrade pip wheel

echo "Installing torch first (CUDA wheel)…"
"${VENV}/bin/pip" install torch==2.11.0 --index-url https://download.pytorch.org/whl/cu130

echo "Installing remaining requirements…"
"${VENV}/bin/pip" install -r "${REQS}"

echo "Verifying CUDA in venv-torch…"
"${VENV}/bin/python" -c "import torch; assert torch.cuda.is_available(), 'CUDA not visible from venv-torch'; print(f'OK: torch {torch.__version__} on {torch.cuda.get_device_name(0)}')"

echo "Done. The plugin will auto-pick the real backend on next train dispatch."
