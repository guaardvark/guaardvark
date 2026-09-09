#!/bin/bash
# scripts/install_pytorch.sh
# Smart PyTorch installer that detects GPU and installs correct CUDA version

set -e

# Colors for output (matching Vader theme from start.sh)
VADER_RED="\033[38;5;196m"       # #d32f2f - primary red
VADER_RED_DARK="\033[38;5;88m"   # #b71c1c - dark red
VADER_RED_LIGHT="\033[38;5;203m" # #f44336 - light red
VADER_GRAY="\033[38;5;244m"      # Lighter gray for better visibility
VADER_GRAY_DARK="\033[38;5;238m" # Dark gray
VADER_WHITE="\033[38;5;255m"     # Pure white
VADER_WHITE_DIM="\033[38;5;250m" # Dim white
VADER_RESET="\033[0m"
VADER_BOLD="\033[1m"

# Output helpers
vader_header() { echo -e "\n${VADER_RED}${VADER_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${VADER_RESET}\n${VADER_WHITE}${VADER_BOLD}  $1${VADER_RESET}\n${VADER_RED}${VADER_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${VADER_RESET}"; }
vader_info() { echo -e "  ${VADER_GRAY}·${VADER_RESET} ${VADER_WHITE_DIM}$1${VADER_RESET}"; }
vader_success() { echo -e "  ${VADER_RED}✔${VADER_RESET} ${VADER_WHITE}$1${VADER_RESET}"; }
vader_warn() { echo -e "  ${VADER_RED_LIGHT}⚠${VADER_RESET} ${VADER_RED_LIGHT}$1${VADER_RESET}"; }
vader_detail() { echo -e "    ${VADER_GRAY}·${VADER_RESET} ${VADER_WHITE_DIM}$1${VADER_RESET}"; }
vader_section() { echo -e "\n${VADER_RED}${VADER_BOLD}► $1${VADER_RESET}"; }

vader_header "PyTorch Smart Installer"

# Venv safety: detect the project's venv and use its pip explicitly.
# Without this, running this script directly (not via start.sh) resolves
# pip to the system Python, which on modern Debian/Ubuntu triggers the
# PEP 668 "externally-managed-environment" error. start.sh activates the
# venv before calling us, so in that path nothing changes — but direct
# invocation now works too.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Use a dedicated tmp for large CUDA wheels (avoids ENOSPC on small /tmp tmpfs
# such as 8 GB tmpfs on some boxes). Respect an explicit TMPDIR if the caller
# already set one. See 2026-06-14 hardware provisioning notes.
if [ -z "${TMPDIR:-}" ]; then
    PIP_TMP="$PROJECT_ROOT/data/piptmp"
    mkdir -p "$PIP_TMP" 2>/dev/null || true
    if [ -d "$PIP_TMP" ] && [ -w "$PIP_TMP" ]; then
        export TMPDIR="$PIP_TMP"
        export PIP_CACHE_DIR="$PIP_TMP"
    fi
fi

# --venv <path> (or TARGET_VENV env) selects which venv to install into.
# Defaults to the backend venv, preserving all existing call sites.
TARGET_VENV="${TARGET_VENV:-$PROJECT_ROOT/backend/venv}"
while [ $# -gt 0 ]; do
    case "$1" in
        --venv)
            if [ -z "${2:-}" ]; then
                vader_warn "--venv requires a path argument"; exit 1
            fi
            TARGET_VENV="$2"; shift 2 ;;
        --venv=*) TARGET_VENV="${1#*=}"; shift ;;
        *) shift ;;
    esac
done

VENV_PIP="$TARGET_VENV/bin/pip"
VENV_PYTHON="$TARGET_VENV/bin/python"

# --- safety: stage wheels BEFORE destroying the working install -------------
# Every branch below uninstalls torch and then downloads the replacement. A
# network drop (or Ctrl-C) between those two steps leaves the venv with NO
# torch at all — not the new one, not the old one. Observed 2026-08-05: an
# unplugged ethernet cable mid-run stripped torch/torchvision/torchaudio from
# a working box and took the whole stack down.
#
# _pt_stage_wheels downloads every wheel (plus deps) into PT_STAGE first and
# fails BEFORE anything is uninstalled. The installs then run offline from
# that directory, so the destructive window no longer depends on the network.
PT_STAGE="$PROJECT_ROOT/data/.pt-wheels-$$"
_pt_stage_cleanup() { [ -n "${PT_STAGE:-}" ] && rm -rf "$PT_STAGE" 2>/dev/null || true; }
trap _pt_stage_cleanup EXIT INT TERM

_pt_stage_wheels() {  # usage: _pt_stage_wheels <index-url|""> <pkg...>
    local idx="$1"; shift
    local dlargs=()
    [ -n "$idx" ] && dlargs=(--index-url "$idx")
    mkdir -p "$PT_STAGE" || { vader_warn "Could not create wheel staging dir; falling back to direct install."; PT_STAGE=""; return 0; }
    vader_info "Staging PyTorch wheels first (existing install untouched until this succeeds)..."
    if ! pip download --dest "$PT_STAGE" "${dlargs[@]}" "$@"; then
        vader_warn "PyTorch wheel download FAILED — existing install left intact, nothing was removed."
        vader_warn "Re-run once the network is healthy: bash scripts/install_pytorch.sh"
        _pt_stage_cleanup
        return 1
    fi
    vader_success "Wheels staged; the swap below now runs offline."
    return 0
}

# Installs from the staged dir when staging succeeded, else falls back to the
# original network install so behaviour is unchanged if staging was skipped.
_pt_repin_pillow() {
    # torch/torchvision depend on pillow, and --force-reinstall re-resolves it from
    # download.pytorch.org, whose index tops out at Pillow 12.2.0. That release has 10
    # HIGH CVEs (heap OOB writes in Image.paste/crop/RankFilter/ImageCmsTransform, plus
    # decompression-bomb bypasses), so every torch install silently un-patched them.
    #
    # This CANNOT be fixed with PIP_CONSTRAINT: constraining Pillow>=12.3.0 during the
    # torch install makes it unsatisfiable from that index and aborts the whole run -
    # exactly the failure the old setuptools pin used to cause. So re-pin AFTER, from
    # PyPI, with --no-deps so torch's own resolution is left untouched.
    command -v pip >/dev/null 2>&1 || return 0
    vader_info "Re-pinning Pillow (torch index caps it at a vulnerable 12.2.0)..."
    if pip install --no-deps --upgrade 'Pillow>=12.3.0' >/dev/null 2>&1; then
        vader_success "Pillow $(pip show Pillow 2>/dev/null | awk '/^Version/{print $2}') re-pinned"
    else
        vader_warn "Could not re-pin Pillow - 12.2.0 has 10 HIGH CVEs. Run: pip install --upgrade 'Pillow>=12.3.0'"
    fi
}

_pt_install_staged() {  # usage: _pt_install_staged <index-url|""> <pkg...>
    local idx="$1"; shift
    if [ -n "${PT_STAGE:-}" ] && [ -d "$PT_STAGE" ]; then
        pip install --upgrade --force-reinstall --no-index --find-links "$PT_STAGE" "$@"
    elif [ -n "$idx" ]; then
        pip install --upgrade --force-reinstall "$@" --index-url "$idx"
    else
        pip install --upgrade --force-reinstall "$@"
    fi
}

if [ -x "$VENV_PIP" ] && [ -x "$VENV_PYTHON" ]; then
    vader_info "Using venv: $TARGET_VENV"
    pip() { "$VENV_PIP" "$@"; }
    python3() { "$VENV_PYTHON" "$@"; }
else
    if [ -z "${VIRTUAL_ENV:-}" ]; then
        vader_warn "No venv at $TARGET_VENV AND no active virtualenv."
        vader_warn "Refusing to install torch into system Python. Activate a venv first, or"
        vader_warn "create it with: python3 -m venv $TARGET_VENV"
        exit 1
    fi
    vader_info "Using active virtualenv: $VIRTUAL_ENV"
fi

# --- fast path: skip the swap when the right build is already in place -------
# Every branch below force-reinstalls, because pip treats +cu128 / +cpu / +rocm
# as the SAME version and a plain install cannot fix a wrong variant. Right
# after a backup-restore, wrong. But it also meant EVERY bootstrap re-downloaded
# the ~3 GB torch+CUDA set on a box that already had the exact build — and a
# flaky link during those minutes failed the reconciler, withheld the bootstrap
# stamp, and forced yet another full bootstrap next boot (observed 2026-08-31:
# ten DNS drops across one setup, torch never wrong once).
#
# So probe first. "Correct" means torch, torchvision AND torchaudio import,
# all three carry the expected local tag (none at all for the default PyPI
# wheel on macOS), and the accelerator the tag promises is actually reachable.
# Anything short of that falls through to the full swap unchanged.
# GUAARDVARK_TORCH_FORCE=1 restores the old always-reinstall behaviour.
_pt_already_correct() {  # usage: _pt_already_correct <tag-substring|""> <cuda|none>; prints version on hit
    if [ "${GUAARDVARK_TORCH_FORCE:-0}" = "1" ] || [ -n "${USE_PRE:-}" ]; then
        return 1
    fi
    python3 - "$1" "$2" <<'EOF' 2>/dev/null
import sys
tag, accel = sys.argv[1], sys.argv[2]
try:
    import torch, torchvision, torchaudio
except Exception:
    sys.exit(1)
for v in (torch.__version__, torchvision.__version__, torchaudio.__version__):
    if tag and f"+{tag}" not in v:
        sys.exit(1)
    if not tag and "+" in v:
        sys.exit(1)
if accel == "cuda" and not torch.cuda.is_available():
    sys.exit(1)
try:
    torch.zeros(1)
except Exception:
    sys.exit(1)
print(torch.__version__)
EOF
}

_pt_report_installed() {
    vader_section "Verification:"
    python3 - <<'EOF'
import torch
print(f"    PyTorch Version:    {torch.__version__}")
print(f"    CUDA Available:     {torch.cuda.is_available()}")
mps = getattr(torch.backends, "mps", None)
if mps is not None:
    print(f"    MPS Available:      {bool(mps.is_available())}")
if torch.cuda.is_available():
    try:
        print(f"    GPU Device:         {torch.cuda.get_device_name(0)}")
    except Exception as e:
        print(f"    GPU Device:         N/A ({e})")
EOF
}

_pt_skip_if_current() {  # usage: _pt_skip_if_current <tag-substring|""> <cuda|none>; exits 0 on hit
    local ver
    ver="$(_pt_already_correct "$1" "$2")" || return 0
    vader_success "PyTorch ${ver} already matches the target build — skipping download and reinstall."
    vader_detail "Force the full swap with GUAARDVARK_TORCH_FORCE=1"
    # Keep the shared-venv contract on the fast path too: these are the known
    # diffusers-import breakers and the deprecated pynvml dist. Offline, cheap.
    pip uninstall -y flash-attn flash_attn xformers pynvml 2>/dev/null | tail -1 || true
    _pt_report_installed
    vader_header "PyTorch Installation Complete"
    exit 0
}

# Pin-convergence: constrain the SHARED backend venv so `--upgrade
# --force-reinstall torch...` can't re-resolve numpy off the ML stack's line
# and force the repin churn (see backend/constraints.txt; numpy 2.1-2.5 cp312
# wheels verified present on the pytorch cu124/cu128/cpu indexes 2026-09-02).
# Deliberately NOT applied to isolated plugin venvs (--venv elsewhere) or a
# caller-set override.
if [ -z "${PIP_CONSTRAINT:-}" ] \
   && [ "$TARGET_VENV" = "$PROJECT_ROOT/backend/venv" ] \
   && [ -f "$PROJECT_ROOT/backend/constraints.txt" ]; then
    export PIP_CONSTRAINT="$PROJECT_ROOT/backend/constraints.txt"
    vader_info "Applying pip constraints: backend/constraints.txt (numpy line, opencv distributions)"
fi

# One source of truth for the CUDA channel. start.sh and heal_backend_venv.sh
# pass GUAARDVARK_TORCH_CHANNEL from backend.services.hardware_policy; the
# dep_reconciler and manual runs did not, so the arch table further down
# decided instead — and it disagreed with the policy for Ampere/Ada (cu121 vs
# cu124). Every reconciler pass then missed the fast path, swapped the whole
# CUDA stack to cu121, and the next start.sh swapped it back to cu124: two
# multi-GB downloads per boot (this repo's own logs, 2026-08-31). Ask the
# policy ourselves whenever the caller did not.
if [ -z "${GUAARDVARK_TORCH_CHANNEL:-}" ] && [ -x "$VENV_PYTHON" ]; then
    _pt_policy_channel="$(cd "$PROJECT_ROOT" && "$VENV_PYTHON" -m backend.services.hardware_policy torch_channel 2>/dev/null || true)"
    case "$_pt_policy_channel" in
        cu[0-9]*|cpu) export GUAARDVARK_TORCH_CHANNEL="$_pt_policy_channel" ;;
    esac
    unset _pt_policy_channel
fi

# ---------------------------------------------------------------------------
# Accelerator branching.
#
# Historically this installer branched ONLY on `nvidia-smi`: every non-NVIDIA
# host (AMD ROCm, Apple Silicon, plain CPU) got the whl/cpu wheel. That meant
# AMD boxes ran torch on the CPU and Macs never got MPS. We now branch FIRST on
# the two previously-missing accelerators (Apple Metal, AMD ROCm); if neither
# applies we fall through to the original NVIDIA-or-CPU logic UNCHANGED.
#
# Detection order is deliberate:
#   1. Darwin (uname)         -> default PyPI wheel (MPS-capable; never cpu URL)
#   2. AMD ROCm (rocm-smi /   -> whl/rocmX.Y  (version overridable via env)
#      hardware.json vendor)
#   3. NVIDIA (nvidia-smi)    -> existing CUDA-arch logic (unchanged)
#   4. anything else / failed -> existing whl/cpu fallback (unchanged)
#
# The ROCm wheel index version is overridable so a host on a newer/older ROCm
# runtime can pin it without editing this script:
#     GUAARDVARK_ROCM_WHL=rocm6.2 bash scripts/install_pytorch.sh
ROCM_WHL="${GUAARDVARK_ROCM_WHL:-rocm6.3}"
HARDWARE_JSON="${GUAARDVARK_HARDWARE_JSON:-$HOME/.guaardvark/hardware.json}"

# --- helper: does hardware.json report an AMD GPU? -------------------------
# hardware_detector.py writes {"gpu": {"vendor": "amd", ...}}. We treat that as
# a secondary AMD signal in case rocm-smi isn't on PATH yet (fresh provision).
# Pure text probe (no python/jq dependency) so it works before the venv exists.
_hardware_json_says_amd() {
    [ -f "$HARDWARE_JSON" ] || return 1
    grep -q '"vendor"[[:space:]]*:[[:space:]]*"amd"' "$HARDWARE_JSON" 2>/dev/null
}

UNAME_S="$(uname -s 2>/dev/null || echo unknown)"

# === Branch 1: Apple Silicon / Intel Mac (Metal/MPS) =======================
if [ "$UNAME_S" = "Darwin" ]; then
    vader_success "macOS (Darwin) detected"
    vader_section "Accelerator: Apple Metal (MPS)"
    vader_detail "Platform:      $(uname -m 2>/dev/null || echo unknown)"
    vader_detail "PyTorch Index: default PyPI (MPS-capable wheel)"
    vader_detail "Note:          NOT using the whl/cpu index — that wheel has no MPS."
    echo ""
    vader_info "Installing default PyTorch (MPS where the OS/GPU supports it)..."
    echo ""
    # Mac: do NOT pass an --index-url. The default PyPI macOS wheel is the
    # MPS-capable build; the whl/cpu index would strip Metal support. Swap-safety
    # uninstall first (same rationale as the other branches) but no CUDA/triton
    # cleanup — those never exist on macOS — and no pynvml removal.
    _pt_skip_if_current "" none
    _pt_stage_wheels "" torch torchvision torchaudio || exit 1
    pip uninstall -y torch torchvision torchaudio 2>/dev/null | tail -3 || true
    _pt_install_staged "" torch torchvision torchaudio

    _pt_repin_pillow

vader_section "Verification:"
    python3 << 'EOF'
import torch
print(f"    PyTorch Version:    {torch.__version__}")
mps = getattr(torch.backends, "mps", None)
avail = bool(mps and mps.is_available())
print(f"    MPS Available:      {avail}")
try:
    dev = "mps" if avail else "cpu"
    t = torch.zeros(1, device=dev)
    print(f"    {dev.upper()} Tensor Test:    PASSED")
except Exception as e:
    print(f"    Tensor Test:        FAILED ({e})")
    # Fall back to a CPU tensor so the verification still proves torch works.
    try:
        torch.zeros(1)
        print("    CPU Tensor Test:    PASSED")
    except Exception as e2:
        print(f"    CPU Tensor Test:    FAILED ({e2})")
EOF

    vader_header "PyTorch Installation Complete"
    exit 0
fi

# === Branch 2: AMD ROCm ====================================================
# rocm-smi on PATH is the primary signal; hardware.json vendor=="amd" is the
# fallback. We intentionally do NOT trigger ROCm just because nvidia-smi is
# absent — that would regress the CPU path for non-AMD machines.
if command -v rocm-smi &> /dev/null || _hardware_json_says_amd; then
    if command -v rocm-smi &> /dev/null; then
        vader_success "AMD ROCm runtime detected (rocm-smi)"
    else
        vader_success "AMD GPU detected (hardware.json vendor=amd)"
    fi
    vader_section "Accelerator: AMD ROCm"
    vader_detail "Platform:       $(uname -m 2>/dev/null || echo unknown)"
    vader_detail "PyTorch Index:  https://download.pytorch.org/whl/${ROCM_WHL}"
    vader_detail "ROCm wheel:     ${ROCM_WHL} (override with GUAARDVARK_ROCM_WHL)"
    echo ""
    vader_info "Installing PyTorch with ROCm (${ROCM_WHL}) support..."
    echo ""
    # Swap-safety: clean prior torch + any lingering CUDA/triton bloat from a
    # previous build, then force-reinstall the ROCm variant (the +rocm local
    # tag collides with +cpu/+cuXXX in pip's resolver, same as the CUDA path).
    _pt_skip_if_current "$ROCM_WHL" cuda
    _pt_stage_wheels "https://download.pytorch.org/whl/${ROCM_WHL}" torch torchvision torchaudio || exit 1
    pip uninstall -y torch torchvision torchaudio 2>/dev/null | tail -3 || true
    pip freeze 2>/dev/null | grep -iE "^(nvidia-|cuda-bindings|cuda-pathfinder|cuda-toolkit|triton)" | awk -F'==' '{print $1}' | xargs -r pip uninstall -y 2>/dev/null | tail -3 || true
    # Purge flash-attn/xformers/pynvml for the same reasons as CUDA/CPU paths (shared
    # backend/venv contract; custom nodes and some plugin reqs inject incompatible
    # versions leading to diffusers import crashes with aten schema errors).
    pip uninstall -y flash-attn flash_attn xformers pynvml nvidia-ml-py 2>/dev/null | tail -3 || true
    _pt_install_staged "https://download.pytorch.org/whl/${ROCM_WHL}" torch torchvision torchaudio

    _pt_repin_pillow

vader_section "Verification:"
    python3 << 'EOF'
import torch
print(f"    PyTorch Version:    {torch.__version__}")
# ROCm torch reports through the CUDA API surface (torch.cuda.is_available()
# is True, torch.version.hip is set). Report both so a misbuild is obvious.
print(f"    HIP Version:        {getattr(torch.version, 'hip', None)}")
print(f"    GPU Available:      {torch.cuda.is_available()}")
if torch.cuda.is_available():
    try:
        print(f"    GPU Device:         {torch.cuda.get_device_name(0)}")
    except Exception as e:
        print(f"    GPU Device:         N/A ({e})")
    try:
        torch.zeros(1).cuda()
        print("    GPU Tensor Test:    PASSED")
    except Exception as e:
        print(f"    GPU Tensor Test:    FAILED ({e})")
else:
    print("    Mode:               CPU-only (ROCm wheel installed but GPU not visible)")
    try:
        torch.zeros(1)
        print("    CPU Tensor Test:    PASSED")
    except Exception as e:
        print(f"    CPU Tensor Test:    FAILED ({e})")
EOF

    vader_header "PyTorch Installation Complete"
    exit 0
fi

# === Branch 3 + 4: NVIDIA (CUDA arch logic) or CPU fallback ================
# Everything below is the ORIGINAL installer, unchanged. Reached only when the
# host is not macOS and not AMD/ROCm.
# Detect if NVIDIA GPU is present
if command -v nvidia-smi &> /dev/null; then
    vader_success "NVIDIA driver detected"

    # Get comprehensive GPU information
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    COMPUTE_CAP=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1)
    DRIVER_VERSION=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)
    GPU_MEMORY=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1)

    vader_section "GPU Information:"
    vader_detail "GPU Model:          ${GPU_NAME:-Unknown}"
    vader_detail "Compute Capability: ${COMPUTE_CAP:-Unknown}"
    vader_detail "Driver Version:     ${DRIVER_VERSION:-Unknown}"
    vader_detail "GPU Memory:         ${GPU_MEMORY:-Unknown}"

    if [ -n "$COMPUTE_CAP" ]; then
        # Convert compute capability to major version (e.g., "8.9" -> "8")
        COMPUTE_MAJOR=$(echo "$COMPUTE_CAP" | cut -d. -f1)
        COMPUTE_MINOR=$(echo "$COMPUTE_CAP" | cut -d. -f2)

        # If the caller resolved the channel from hardware_policy (single source
        # of truth), honor it and skip the built-in table below.
        if [ -n "${GUAARDVARK_TORCH_CHANNEL:-}" ]; then
            CUDA_VERSION="$GUAARDVARK_TORCH_CHANNEL"
            CUDA_NAME="$GUAARDVARK_TORCH_CHANNEL"
            ARCH_NAME="policy(${GUAARDVARK_TORCH_CHANNEL})"
            vader_info "Torch channel from hardware_policy: $CUDA_VERSION"
        fi

        # Determine which CUDA version to use with detailed explanation
        vader_section "Architecture Detection:"

        if [ -z "${GUAARDVARK_TORCH_CHANNEL:-}" ]; then
        if [ "$COMPUTE_MAJOR" -ge 12 ]; then
            CUDA_VERSION="cu128"
            CUDA_NAME="12.8"
            ARCH_NAME="Blackwell"
            vader_info "Detected ${ARCH_NAME} architecture (compute ${COMPUTE_CAP})"
            vader_detail "Using CUDA ${CUDA_NAME} for sm_120 kernel support"
        elif [ "$COMPUTE_MAJOR" -ge 9 ]; then
            CUDA_VERSION="cu128"
            CUDA_NAME="12.8"
            ARCH_NAME="Hopper"
            vader_info "Detected ${ARCH_NAME} architecture (compute ${COMPUTE_CAP})"
            vader_detail "Using CUDA ${CUDA_NAME} for optimal performance"
        elif [ "$COMPUTE_MAJOR" -ge 8 ]; then
            # cu124, matching hardware_policy.torch_channel: the cu121 index tops
            # out at torch 2.5.1 (CVE-2025-32434, torch.load RCE, fixed in 2.6.0).
            CUDA_VERSION="cu124"
            CUDA_NAME="12.4"
            ARCH_NAME="Ampere/Ada Lovelace"
            vader_info "Detected ${ARCH_NAME} architecture (compute ${COMPUTE_CAP})"
            vader_detail "Using CUDA ${CUDA_NAME} for modern GPU support"
        elif [ "$COMPUTE_MAJOR" -ge 7 ]; then
            CUDA_VERSION="cu118"
            CUDA_NAME="11.8"
            ARCH_NAME="Volta/Turing"
            vader_info "Detected ${ARCH_NAME} architecture (compute ${COMPUTE_CAP})"
            vader_detail "Using CUDA ${CUDA_NAME} for compatibility"
        elif [ "$COMPUTE_MAJOR" -ge 6 ]; then
            CUDA_VERSION="cu118"
            CUDA_NAME="11.8"
            ARCH_NAME="Pascal"
            vader_info "Detected ${ARCH_NAME} architecture (compute ${COMPUTE_CAP})"
            vader_detail "Using CUDA ${CUDA_NAME} for legacy GPU support"
        else
            CUDA_VERSION="cpu"
            CUDA_NAME="CPU-only"
            ARCH_NAME="Legacy (pre-Pascal)"
            vader_warn "GPU compute capability ${COMPUTE_CAP} is too old for CUDA support"
            vader_detail "Falling back to CPU-only mode"
        fi
        fi  # end: [ -z "${GUAARDVARK_TORCH_CHANNEL:-}" ]

        vader_section "Installation Plan:"

        if [ "$CUDA_VERSION" != "cpu" ]; then
            _pt_skip_if_current "$CUDA_VERSION" cuda
        else
            _pt_skip_if_current cpu none
        fi

        # --force-reinstall is required because pip's resolver treats the
        # local-version tag (e.g. +cu130 vs +cpu) as the SAME version number
        # for "already satisfied" purposes. Without --force-reinstall, a machine
        # restored from a GPU host's backup will report success but keep the
        # wrong variant. Also uninstall any lingering CUDA/triton deps that
        # were pulled in by a previous GPU build so we don't carry dead weight.
        vader_section "Cleaning prior torch variants and CUDA dependency bloat..."
        # IMPORTANT: This uninstall step runs *before* the reinstall. If you
        # Ctrl-C during this phase the target venv will be left without torch
        # (and without the old one either). Let the script finish. The verify
        # gate at the end of start.sh will surface the problem if it happens.
        if [ "$CUDA_VERSION" != "cpu" ]; then
            _pt_stage_wheels "https://download.pytorch.org/whl/$CUDA_VERSION" ${USE_PRE:-}torch torchvision torchaudio || exit 1
        else
            _pt_stage_wheels "https://download.pytorch.org/whl/cpu" torch torchvision torchaudio || exit 1
        fi
        pip uninstall -y torch torchvision torchaudio 2>/dev/null | tail -3 || true
        pip freeze 2>/dev/null | grep -iE "^(nvidia-|cuda-bindings|cuda-pathfinder|cuda-toolkit|triton)" | awk -F'==' '{print $1}' | xargs -r pip uninstall -y 2>/dev/null | tail -3 || true
        # Purge flash-attn / xformers (the #1 source of "Current Torch with Flash-Attention 2.5.7
        # doesnt have a compatible aten::_flash_attention_forward schema (philox_seed vs rng_state)"
        # errors on diffusers import in batch_image_generation_api / offline_image_generator).
        # Also purge pynvml/nvidia-ml-py (FutureWarning on every torch.cuda touch; re-pulled by
        # plugin reqs like upscaling/vision into the shared backend/venv). These are optional
        # accelerators; core diffusers/Comfy paths degrade gracefully without them.
        pip uninstall -y flash-attn flash_attn xformers pynvml nvidia-ml-py 2>/dev/null | tail -3 || true

        if [ "$CUDA_VERSION" != "cpu" ]; then
            vader_detail "PyTorch Index: https://download.pytorch.org/whl/${CUDA_VERSION}"
            vader_detail "CUDA Version:  ${CUDA_NAME}"
            vader_detail "Target Arch:   ${ARCH_NAME}"
            echo ""
            vader_info "Installing PyTorch with CUDA ${CUDA_NAME} support..."
            echo ""
            _pt_install_staged "https://download.pytorch.org/whl/$CUDA_VERSION" ${USE_PRE:-}torch torchvision torchaudio
            # Re-add nvidia-ml-py after the blanket purge above. The purge exists
            # to kill the DEPRECATED `pynvml` dist (torch FutureWarning source);
            # nvidia-ml-py is the official replacement and powers the VRAM queries
            # in gpu_resource_coordinator/ollama_resource_manager. Without this,
            # every GPU boot warned "pynvml not installed, falling back to
            # nvidia-smi" because NOTHING in the boot path reinstalled it
            # (only heal_backend_venv.sh did). See requirements.txt:35-44.
            pip install nvidia-ml-py 2>/dev/null | tail -1 || true
        else
            vader_detail "PyTorch Index: https://download.pytorch.org/whl/cpu"
            vader_detail "Mode:          CPU-only (GPU not supported)"
            echo ""
            vader_info "Installing CPU-only PyTorch..."
            echo ""
            _pt_install_staged "https://download.pytorch.org/whl/cpu" torch torchvision torchaudio
            # pynvml is deprecated and fires FutureWarning on every `import torch`
            # via torch/cuda/__init__.py. On CPU-only hosts it serves no purpose —
            # torch handles the ImportError gracefully. Remove it to silence the noise.
            pip uninstall -y pynvml 2>/dev/null | tail -2 || true
        fi

        # Verification
        _pt_repin_pillow

vader_section "Verification:"
        python3 << 'EOF'
import torch

# Basic info
print(f"    PyTorch Version:    {torch.__version__}")
print(f"    CUDA Available:     {torch.cuda.is_available()}")

if torch.cuda.is_available():
    print(f"    CUDA Version:       {torch.version.cuda}")
    try:
        print(f"    cuDNN Version:      {torch.backends.cudnn.version()}")
    except:
        print(f"    cuDNN Version:      N/A")
    print(f"    GPU Device:         {torch.cuda.get_device_name(0)}")
    cap = torch.cuda.get_device_capability(0)
    print(f"    Compute Capability: {cap[0]}.{cap[1]}")

    # Quick tensor test
    try:
        test_tensor = torch.zeros(1).cuda()
        print(f"    GPU Tensor Test:    PASSED")
    except Exception as e:
        print(f"    GPU Tensor Test:    FAILED ({e})")
else:
    print("    Mode:               CPU-only")

    # Quick CPU test
    try:
        test_tensor = torch.zeros(1)
        print(f"    CPU Tensor Test:    PASSED")
    except Exception as e:
        print(f"    CPU Tensor Test:    FAILED ({e})")
EOF

    else
        vader_warn "Could not detect GPU compute capability"
        vader_info "Installing CPU-only PyTorch as fallback..."
        echo ""
        _pt_skip_if_current cpu none
        _pt_stage_wheels "https://download.pytorch.org/whl/cpu" torch torchvision torchaudio || exit 1
        pip uninstall -y torch torchvision torchaudio 2>/dev/null | tail -3 || true
        _pt_install_staged "https://download.pytorch.org/whl/cpu" torch torchvision torchaudio
        pip uninstall -y pynvml flash-attn flash_attn xformers nvidia-ml-py 2>/dev/null | tail -2 || true

        _pt_repin_pillow

vader_section "Verification:"
        python3 -c "import torch; print(f'    PyTorch Version: {torch.__version__}'); print(f'    Mode: CPU-only')"
    fi
else
    vader_section "GPU Detection:"
    vader_detail "nvidia-smi:     Not found"
    vader_detail "CUDA Support:   Not available"
    echo ""
    vader_info "Installing CPU-only PyTorch..."
    echo ""
    # Same variant-swap safety: uninstall first, force-reinstall, drop pynvml.
    _pt_skip_if_current cpu none
    _pt_stage_wheels "https://download.pytorch.org/whl/cpu" torch torchvision torchaudio || exit 1
    pip uninstall -y torch torchvision torchaudio 2>/dev/null | tail -3 || true
    pip freeze 2>/dev/null | grep -iE "^(nvidia-|cuda-bindings|cuda-pathfinder|cuda-toolkit|triton)" | awk -F'==' '{print $1}' | xargs -r pip uninstall -y 2>/dev/null | tail -3 || true
    # Also purge flash/xformers/pynvml here (see main CUDA clean comment for rationale:
    # prevents schema mismatch on diffusers import + repeated FutureWarnings from plugins).
    pip uninstall -y flash-attn flash_attn xformers pynvml nvidia-ml-py 2>/dev/null | tail -3 || true
    _pt_install_staged "https://download.pytorch.org/whl/cpu" torch torchvision torchaudio

    _pt_repin_pillow

vader_section "Verification:"
    python3 -c "import torch; print(f'    PyTorch Version: {torch.__version__}'); print(f'    Mode: CPU-only')"
fi

vader_header "PyTorch Installation Complete"
