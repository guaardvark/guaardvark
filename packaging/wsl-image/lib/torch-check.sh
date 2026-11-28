#!/usr/bin/env bash
# Match the backend's PyTorch to this PC's NVIDIA card and driver.
#
# The image ships the cu132 build (driver 580+, RTX 20 and newer). A card or
# driver that needs another channel (GTX 10 / Volta: cu126; RTX 50 on a driver
# before 580: cu128; RTX 20-40 on 525-579: cu126; before 525: cu118) would load
# that build without CUDA and Guaardvark would run on the CPU without saying so.
# This asks hardware_policy, the same table install.sh uses, which channel the
# card in front of it needs, and installs that channel once when it differs.
#
# Run it as the default user before start.sh, on every start: when the channel
# already matches it only reads nvidia-smi and the installed version (about a
# second). The desktop app runs it; by hand: /usr/lib/guaardvark-image/torch-check.sh
#
#   GUAARDVARK_PROGRESS_FILE=<path> (or --progress-file <path>) appends progress
#   lines in desktop/PROTOCOL.md's format under the step id "gpu_torch". It never
#   writes `ready` or `failed`; start.sh does that afterwards.
#
# Result: ~/.guaardvark/torch-check.json with "result" (match, installed,
# no_gpu, card_unsupported, driver_too_old, install_failed), the channels and
# "note", a sentence for the user or "". Exit 0 except for install_failed (1).
# A failed install leaves the previous build in place and is retried next start.

set -uo pipefail
GV="${GUAARDVARK_ROOT:-$HOME/guaardvark}"
VENV_PY="$GV/backend/venv/bin/python"
STATUS_DIR="$HOME/.guaardvark"
STATUS="$STATUS_DIR/torch-check.json"
LOG="$GV/logs/torch-check.log"
PROGRESS="${GUAARDVARK_PROGRESS_FILE:-}"
while [ $# -gt 0 ]; do
    case "$1" in
        --progress-file) PROGRESS="$2"; shift 2 ;;
        --progress-file=*) PROGRESS="${1#*=}"; shift ;;
        *) shift ;;
    esac
done
# cu118, the oldest channel install_pytorch.sh carries, runs on any driver CUDA 11
# minor-version compatibility covers: 452.39 or newer on Windows (NVIDIA's CUDA
# compatibility guide). Below that no channel can use the card.
MIN_ANY_DRIVER=452
mkdir -p "$STATUS_DIR" "$GV/logs"

progress() {  # state label [detail]
    echo "[gpu] $2${3:+ — $3}"
    [ -n "$PROGRESS" ] || return 0
    python3 -c 'import json,sys,time; print(json.dumps({"t": time.time(), "step": "gpu_torch", "state": sys.argv[1], "label": sys.argv[2], **({"detail": sys.argv[3]} if sys.argv[3] else {})}))' \
        "$1" "$2" "${3:-}" >> "$PROGRESS"
}
finish() {  # result note
    python3 -c 'import json,sys; json.dump(dict(zip(["result","installed","wanted","gpu","driver","compute_cap","note"], sys.argv[1:])), open(sys.argv[8], "w"), indent=2)' \
        "$1" "${INSTALLED:-}" "${WANTED:-}" "${GPU_NAME:-}" "${DRIVER:-}" "${CAP:-}" "$2" "$STATUS"
}

[ -x "$VENV_PY" ] || { echo "No Guaardvark venv at $GV" >&2; exit 1; }
progress start "Checking the graphics card"

# The installed build's channel from its version tag (2.14.1+cu132 -> cu132),
# read from package metadata so torch itself is not imported.
INSTALLED="$("$VENV_PY" -c 'from importlib.metadata import version; v = version("torch"); print(v.split("+", 1)[1] if "+" in v else "")' 2>/dev/null)"

# The card as nvidia-smi reports it now, and the channel hardware_policy picks
# for it; a cached hardware.json could predate a driver update.
read -r WANTED DRIVER CAP GPU_NAME < <(cd "$GV" && "$VENV_PY" - <<'PY' 2>/dev/null
from backend.services.hardware_detector import HardwareDetector
from backend.services.hardware_policy import torch_channel
gpu = HardwareDetector()._probe_gpu() or {}
if gpu.get("vendor") != "nvidia":
    print("none - - -")
else:
    print(torch_channel(gpu), gpu.get("driver") or "-", gpu.get("compute_cap") or "-",
          (gpu.get("model") or "-").replace(" ", "_"))
PY
)
GPU_NAME="${GPU_NAME//_/ }"
DRIVER_MAJOR="${DRIVER%%.*}"; DRIVER_MAJOR="${DRIVER_MAJOR//[!0-9]/}"

if [ -z "${WANTED:-}" ] || [ "$WANTED" = none ]; then
    progress skip "No NVIDIA graphics card found" "Guaardvark runs on the processor."
    finish no_gpu ""
    exit 0
fi
if [ -n "$DRIVER_MAJOR" ] && [ "$DRIVER_MAJOR" -lt "$MIN_ANY_DRIVER" ]; then
    note="Update your NVIDIA driver to 580 or newer from nvidia.com, then restart Guaardvark."
    progress skip "The NVIDIA driver is too old for GPU acceleration" "$note"
    finish driver_too_old "$note"
    exit 0
fi
if [ "$WANTED" = cpu ]; then
    # Cards before Pascal: no PyTorch build runs on them. The CUDA build stays; it
    # runs on the processor like any other.
    note="This graphics card ($GPU_NAME) is too old for GPU acceleration. Guaardvark runs on the processor."
    progress skip "Graphics card too old for GPU acceleration" "$note"
    finish card_unsupported "$note"
    exit 0
fi
if [ "$WANTED" = "$INSTALLED" ]; then
    progress ok "Graphics card ready" "$GPU_NAME, driver $DRIVER, PyTorch $INSTALLED"
    finish match ""
    exit 0
fi

progress info "Installing PyTorch for $GPU_NAME" \
    "This card and driver need the $WANTED build (installed: ${INSTALLED:-none}). It is about a 3 GB download, once."
# install_pytorch.sh stages every wheel before it removes the old build, so a
# dropped connection leaves the installed one working. start.sh's bootstrap stamp
# covers the requirements files only, so it neither blocks this nor re-runs it.
if (cd "$GV" && GUAARDVARK_TORCH_CHANNEL="$WANTED" bash scripts/install_pytorch.sh) >> "$LOG" 2>&1; then
    # The pins start.sh re-asserts after every install_pytorch.sh run.
    # shellcheck source=/dev/null
    . "$GV/scripts/lib/venv_pins.sh"
    bad="$(venv_pins_violated "$VENV_PY" "${GV_ML_PINS[@]}")" || true
    if [ -n "$bad" ]; then
        # shellcheck disable=SC2086
        "$GV/backend/venv/bin/pip" install --no-deps --force-reinstall $bad >> "$LOG" 2>&1
    fi
    "$GV/backend/venv/bin/pip" uninstall -y flash-attn flash_attn xformers >> "$LOG" 2>&1 || true
    # install_pytorch.sh keeps its wheel downloads here; they are not needed again.
    rm -rf "$GV/data/piptmp"
    INSTALLED="$("$VENV_PY" -c 'from importlib.metadata import version; v = version("torch"); print(v.split("+", 1)[1] if "+" in v else "")' 2>/dev/null)"
fi
if [ "$INSTALLED" = "$WANTED" ]; then
    progress ok "PyTorch installed for $GPU_NAME" "$WANTED"
    finish installed ""
    exit 0
fi
note="PyTorch for this graphics card could not be installed (see $LOG). Guaardvark runs on the processor for now and tries again at the next start."
progress fail "PyTorch for the graphics card could not be installed" "$note"
finish install_failed "$note"
exit 1
