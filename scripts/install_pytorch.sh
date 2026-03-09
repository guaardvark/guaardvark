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

        # Determine which CUDA version to use with detailed explanation
        vader_section "Architecture Detection:"

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
            CUDA_VERSION="cu121"
            CUDA_NAME="12.1"
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

        vader_section "Installation Plan:"

        if [ "$CUDA_VERSION" != "cpu" ]; then
            vader_detail "PyTorch Index: https://download.pytorch.org/whl/${CUDA_VERSION}"
            vader_detail "CUDA Version:  ${CUDA_NAME}"
            vader_detail "Target Arch:   ${ARCH_NAME}"
            echo ""
            vader_info "Installing PyTorch with CUDA ${CUDA_NAME} support..."
            echo ""
            pip install ${USE_PRE:-}torch torchvision torchaudio --index-url "https://download.pytorch.org/whl/$CUDA_VERSION"
        else
            vader_detail "PyTorch Index: https://download.pytorch.org/whl/cpu"
            vader_detail "Mode:          CPU-only (GPU not supported)"
            echo ""
            vader_info "Installing CPU-only PyTorch..."
            echo ""
            pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
        fi

        # Verification
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
        pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

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
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

    vader_section "Verification:"
    python3 -c "import torch; print(f'    PyTorch Version: {torch.__version__}'); print(f'    Mode: CPU-only')"
fi

vader_header "PyTorch Installation Complete"
