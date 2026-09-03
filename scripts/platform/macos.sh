#!/usr/bin/env bash
# scripts/platform/macos.sh — macOS (Darwin) platform backend.
#
# STATUS: PROPOSAL — not sourced by anything yet.
#
# Implements the platform interface the launcher calls. Apple Silicon is the
# primary target (Metal/MPS); Intel is supported-but-untested. Uses Homebrew +
# brew services — no apt, no systemd, no sudoers, no NVIDIA.
# Assumes the vader_* log helpers from start.sh are in scope (it sources this).
#
# Interface (every backend implements these identically):
#   platform_install_system_deps   platform_ensure_python
#   platform_gpu_setup             platform_service_start <postgres|redis|ollama>

# brew lives at /opt/homebrew (Apple Silicon) or /usr/local (Intel). Never hardcode —
# resolve via `brew --prefix`.
_have_brew() { command -v brew >/dev/null 2>&1; }
_brew() {
    _have_brew || { vader_error "Homebrew is required on macOS — install from https://brew.sh, then re-run."; return 1; }
    brew "$@"
}

platform_install_system_deps() {
    # Presence check ONLY — never run bare `brew` (no args), which prints usage to
    # stderr and exits 1, making this function bail before installing anything.
    _have_brew || { vader_error "Homebrew is required on macOS — install from https://brew.sh, then re-run."; return 1; }
    vader_info "Installing system deps via Homebrew (postgresql@16, redis, ffmpeg, node, cmake, zstd)..."
    _brew install postgresql@16 redis ffmpeg node cmake zstd || return 1
    # Postgres/Redis run under launchd via brew services (the macOS analog of systemd).
    _brew services start postgresql@16 2>/dev/null || vader_warn "Could not auto-start postgresql@16 — run: brew services start postgresql@16"
    _brew services start redis        2>/dev/null || vader_warn "Could not auto-start redis — run: brew services start redis"
}

platform_ensure_python() {
    # 3.12 required: the pandas==2.2.2 pin (and the face-restoration extra) has no 3.13 wheels.
    # Homebrew's default `python3` is 3.13 now, so we ask for python@3.12 explicitly.
    if command -v python3.12 >/dev/null 2>&1; then
        PYTHON_CMD=python3.12; export PYTHON_CMD; return 0
    fi
    vader_info "Installing Python 3.12 via Homebrew..."
    if _brew install python@3.12; then
        PYTHON_CMD="$(_brew --prefix)/opt/python@3.12/bin/python3.12"
        export PYTHON_CMD
    else
        vader_error "Install Python 3.12: 'brew install python@3.12', then re-run with 'PYTHON_CMD=python3.12 ./start.sh'."
        return 1
    fi
}

platform_gpu_setup() {
    # No NVIDIA/CUDA on Mac. torch MPS is installed by scripts/install_pytorch.sh (Darwin branch).
    # No systemd drop-ins, no sudoers writes, no nvidia persistence/power — all Linux-only. No-op here.
    vader_info "macOS: accelerator = Metal/MPS (handled by install_pytorch.sh); skipping NVIDIA/systemd tuning."
}

platform_service_start() {  # $1 = postgres | redis | ollama
    case "$1" in
        postgres) _brew services start postgresql@16 2>/dev/null || vader_warn "start: brew services start postgresql@16" ;;
        redis)    _brew services start redis 2>/dev/null || vader_warn "start: brew services start redis" ;;
        ollama)   command -v ollama >/dev/null 2>&1 \
                    || vader_warn "Ollama not found — install the macOS app from https://ollama.com/download (it uses Metal)." ;;
        *)        vader_warn "platform_service_start: unknown service '$1'" ;;
    esac
}
