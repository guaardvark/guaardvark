#!/usr/bin/env bash
# scripts/platform/linux.sh — Linux platform backend (x86_64, aarch64/Pi, WSL).
#
# Sourced by start.sh after detect_platform(). Implements the platform interface:
#   platform_install_system_deps   platform_ensure_python   ensure_node_npm
#   platform_gpu_setup             platform_service_start
#
# Assumes the vader_* log helpers from start.sh are in scope.

_linux_os_release() {
    if [ -f /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        export _LINUX_OS_ID="${ID:-unknown}"
        export _LINUX_OS_VERSION="${VERSION_ID:-}"
    else
        export _LINUX_OS_ID=unknown
        export _LINUX_OS_VERSION=
    fi
}

_linux_sudo_available() {
    sudo -n true 2>/dev/null && return 0
    if declare -F _vader_sudo_v >/dev/null 2>&1; then
        _vader_sudo_v
        return $?
    fi
    sudo -v 2>/dev/null
}

_linux_python312_usable() {
    local py="$1"
    [ -n "$py" ] && command -v "$py" >/dev/null 2>&1 || return 1
    local ver minor
    ver=$("$py" --version 2>&1 | awk '{print $2}')
    minor=${ver#*.}
    minor=${minor%%.*}
    [ "${ver%%.*}" = "3" ] && [ "$minor" = "12" ]
}

_linux_python_headers_ok() {
    # Native sdist builds (evdev in requirements-base) need Python.h. Ubuntu 24.04
    # ships python3.12 WITHOUT python3.12-dev, so "3.12 is on PATH" is not enough.
    local py="$1" inc
    command -v "$py" >/dev/null 2>&1 || return 1
    inc=$("$py" -c 'import sysconfig; print(sysconfig.get_paths()["include"])' 2>/dev/null) || return 1
    [ -n "$inc" ] && [ -f "$inc/Python.h" ]
}

_linux_ensure_python_headers() {
    # Best-effort: verify headers for $1, apt-install the matching -dev (+venv)
    # package when absent. Returns 1 only when headers are missing AND unfixable
    # here, so platform_ensure_python can fall through to uv (bundles headers).
    local py="$1" pyver
    _linux_python_headers_ok "$py" && return 0
    pyver=$("$py" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "3.12")
    if command -v apt-get >/dev/null 2>&1 && _linux_sudo_available; then
        vader_info "Python dev headers missing (Python.h) — installing python${pyver}-dev + python${pyver}-venv via apt..."
        sudo apt-get install -y "python${pyver}-dev" "python${pyver}-venv" >/dev/null 2>&1 || true
        if _linux_python_headers_ok "$py"; then
            vader_success "Python ${pyver} dev headers installed (native wheels like evdev can build now)"
            return 0
        fi
    fi
    vader_warn "Python dev headers still missing for $py — pip will fail building native wheels (evdev: 'Python.h: No such file or directory')."
    vader_info "Fix manually: sudo apt-get install -y python${pyver}-dev python${pyver}-venv"
    return 1
}

# Public wrapper for start.sh: cheap no-op when headers are present. Called
# EARLY (before system-manager repair) because that layer pip-installs
# requirements on fresh boxes and hits the evdev/Python.h wall on 24.04.
# Only acts when a 3.12 interpreter already exists (the 24.04 case) — when
# 3.12 is absent, platform_ensure_python installs it WITH headers later.
platform_ensure_python_headers() {
    local py="${PYTHON_CMD:-}"
    _linux_python312_usable "$py" || py=python3.12
    _linux_python312_usable "$py" || return 0
    _linux_ensure_python_headers "$py"
}

_linux_set_python_cmd() {
    local py="$1"
    PYTHON_CMD="$py"
    export PYTHON_CMD
}

_linux_persist_python_cmd() {
    local env_file="${GUAARDVARK_ROOT:-$SCRIPT_DIR}/.env"
    [ -n "$PYTHON_CMD" ] || return 0
    if [ -f "$env_file" ] && grep -q '^PYTHON_CMD=' "$env_file" 2>/dev/null; then
        return 0
    fi
    echo "PYTHON_CMD=$PYTHON_CMD" >> "$env_file"
    chmod 600 "$env_file" 2>/dev/null || true
}

_linux_install_uv() {
    if command -v uv >/dev/null 2>&1; then
        return 0
    fi
    vader_info "Installing uv (user-local Python manager)..."
    if ! curl -fsSL https://astral.sh/uv/install.sh | sh; then
        vader_error "Failed to install uv."
        return 1
    fi
    export PATH="$HOME/.local/bin:$PATH"
}

_linux_ensure_python_via_uv() {
    _linux_install_uv || return 1
    export PATH="$HOME/.local/bin:$PATH"
    vader_info "Installing Python 3.12 via uv (no sudo required)..."
    if ! uv python install 3.12; then
        vader_error "uv python install 3.12 failed."
        return 1
    fi
    local found
    found=$(uv python find 3.12 2>/dev/null || true)
    if [ -z "$found" ] || ! _linux_python312_usable "$found"; then
        vader_error "uv installed Python 3.12 but could not locate the interpreter."
        return 1
    fi
    _linux_set_python_cmd "$found"
    vader_success "Python 3.12 ready via uv ($PYTHON_CMD)"
    return 0
}

_linux_apt_install_python312() {
    local use_deadsnakes="${1:-0}"
    if ! command -v apt-get >/dev/null 2>&1; then
        return 1
    fi
    if ! _linux_sudo_available; then
        return 1
    fi
    if [ "$use_deadsnakes" = "1" ]; then
        vader_info "Adding deadsnakes PPA for Python 3.12..."
        sudo add-apt-repository -y ppa:deadsnakes/ppa >/dev/null 2>&1 || return 1
    fi
    vader_info "Installing Python 3.12 via apt..."
    sudo apt-get update -qq >/dev/null 2>&1 || true
    if sudo apt-get install -y python3.12 python3.12-venv python3.12-dev >/dev/null 2>&1; then
        if _linux_python312_usable python3.12; then
            _linux_set_python_cmd python3.12
            vader_success "Python 3.12 installed via apt"
            return 0
        fi
    fi
    return 1
}

platform_install_system_deps() {
    vader_info "Installing system deps via apt (postgresql, redis, ffmpeg, node, build tools, zstd)..."
    local log="${SETUP_LOG:-${SCRIPT_DIR:-.}/logs/setup.log}"
    declare -F _vader_sudo_v >/dev/null 2>&1 && { _vader_sudo_v || true; }
    if declare -F _vader_run >/dev/null 2>&1; then
        _vader_run "$log" -- sudo apt-get install -y postgresql postgresql-contrib redis-server ffmpeg nodejs npm cmake build-essential zstd python3.12-dev python3.12-venv || return 1
    else
        sudo apt-get install -y postgresql postgresql-contrib redis-server ffmpeg nodejs npm cmake build-essential zstd python3.12-dev python3.12-venv || return 1
    fi
}

platform_ensure_python() {
    # Fast path: already on PATH — but ONLY with dev headers. Ubuntu 24.04's
    # system python3.12 lands here headerless and used to sail straight into
    # the evdev 'Python.h: No such file' pip abort (client box install, 2026-08).
    # If headers are missing and can't be apt-fixed, fall through to uv below
    # (python-build-standalone bundles its own headers).
    if _linux_python312_usable python3.12 && _linux_ensure_python_headers python3.12; then
        _linux_set_python_cmd python3.12
        return 0
    fi
    # Respect an explicit PYTHON_CMD if it points at 3.12 (same header gate)
    if [ -n "${PYTHON_CMD:-}" ] && [ "$PYTHON_CMD" != python3 ] && _linux_python312_usable "$PYTHON_CMD" && _linux_ensure_python_headers "$PYTHON_CMD"; then
        return 0
    fi

    _linux_os_release

    # arm64 (Pi): apt python3.12 is usually unavailable — prefer uv
    if [ "${GUAARDVARK_ARCH:-}" = arm64 ]; then
        if _linux_ensure_python_via_uv; then
            _linux_persist_python_cmd
            return 0
        fi
        vader_error "Python 3.12 required. On ARM/Pi: ensure uv works, or install python3.12 manually."
        return 1
    fi

    # x86_64: try apt first when sudo is available
    if command -v apt-get >/dev/null 2>&1 && _linux_sudo_available; then
        # Ubuntu 24.04 ships python3.12 in main repos
        if [ "$_LINUX_OS_ID" = ubuntu ] && [ "$_LINUX_OS_VERSION" = "24.04" ]; then
            if _linux_apt_install_python312 0; then
                _linux_persist_python_cmd
                return 0
            fi
        else
            # 22.04, 26.04, and other derivatives: try plain apt, then deadsnakes
            if _linux_apt_install_python312 0 || _linux_apt_install_python312 1; then
                _linux_persist_python_cmd
                return 0
            fi
        fi
    fi

    # No sudo or apt failed — uv fallback
    if _linux_ensure_python_via_uv; then
        _linux_persist_python_cmd
        return 0
    fi

    vader_error "Could not install Python 3.12 automatically."
    vader_info "Try: sudo apt-get install -y python3.12 python3.12-venv python3.12-dev"
    vader_info "Or:  curl -LsSf https://astral.sh/uv/install.sh | sh && uv python install 3.12"
    vader_info "Then: PYTHON_CMD=\$(uv python find 3.12) ./start.sh"
    return 1
}

_linux_ensure_node_via_binary() {
    local node_dir="$HOME/.local/node"
    local node_bin="$node_dir/bin/node"
    local npm_bin="$node_dir/bin/npm"
    # A Node left here by an earlier run is reused only while it meets the
    # frontend's floor; an older one is replaced.
    if [ -x "$node_bin" ] && [ -x "$npm_bin" ] && node_version_supported "$("$node_bin" --version 2>/dev/null)"; then
        export PATH="$node_dir/bin:$PATH"
        NPM_CMD=npm
        export NPM_CMD
        return 0
    fi
    vader_info "Installing Node.js 22 LTS to ~/.local/node (no sudo required)..."
    local ver="v22.22.1"
    local arch="linux-x64"
    case "$(uname -m)" in
        aarch64|arm64) arch="linux-arm64" ;;
        x86_64|amd64) arch="linux-x64" ;;
    esac
    local tarball="node-${ver}-${arch}.tar.xz"
    local url="https://nodejs.org/dist/${ver}/${tarball}"
    mkdir -p "$HOME/.local"
    if ! curl -fsSL "$url" | tar -xJ -C "$HOME/.local" 2>/dev/null; then
        vader_error "Failed to download Node.js from nodejs.org"
        return 1
    fi
    rm -rf "$node_dir"
    mv "$HOME/.local/node-${ver}-${arch}" "$node_dir"
    export PATH="$node_dir/bin:$PATH"
    # node_modules was installed for the previous Node; start.sh reinstalls it.
    GUAARDVARK_NODE_REPLACED=1
    export GUAARDVARK_NODE_REPLACED
    if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
        NPM_CMD=npm
        export NPM_CMD
        vader_success "Node.js $(node --version) ready (~/.local/node)"
        return 0
    fi
    return 1
}

ensure_node_npm() {
    # A user-local install from a prior run goes first, while the frontend can
    # still build with it; an older one must not shadow a good system Node.
    if [ -x "$HOME/.local/node/bin/node" ] && node_version_supported "$("$HOME/.local/node/bin/node" --version 2>/dev/null)"; then
        export PATH="$HOME/.local/node/bin:$PATH"
    fi
    # Require both node and npm — reject PATH entries that only provide node (e.g. IDE bundles)
    local node_ok=0 npm_ok=0
    if command -v node >/dev/null 2>&1; then
        node_version_supported && node_ok=1
    fi
    if command -v npm >/dev/null 2>&1; then
        npm_ok=1
    fi
    if [ "$node_ok" -eq 1 ] && [ "$npm_ok" -eq 1 ]; then
        NPM_CMD=npm
        export NPM_CMD
        return 0
    fi

    # A Node that is present but too old is left in place: apt rarely has a
    # newer one (Ubuntu 24.04 ships 18), and replacing a NodeSource or nvm
    # install is the user's call. A supported Node goes in ~/.local/node instead.
    if [ "$node_ok" -eq 0 ] && command -v node >/dev/null 2>&1; then
        vader_warn "Node.js $(node --version) is older than the frontend build needs ($GUAARDVARK_NODE_FLOOR_TEXT)."
        if _linux_ensure_node_via_binary; then
            return 0
        fi
        vader_error "Could not install Node.js to ~/.local/node. Upgrade Node.js to $GUAARDVARK_NODE_FLOOR_TEXT and re-run."
        return 1
    fi

    if ! command -v apt-get >/dev/null 2>&1 || ! _linux_sudo_available; then
        if _linux_ensure_node_via_binary; then
            return 0
        fi
        vader_error "Node.js $GUAARDVARK_NODE_FLOOR_TEXT and npm are required but not found, and apt/sudo is unavailable."
        vader_info "Install Node.js 22 LTS from https://nodejs.org and re-run."
        return 1
    fi

    vader_info "Installing Node.js and npm via apt..."
    sudo apt-get update -qq >/dev/null 2>&1 || true
    if sudo apt-get install -y nodejs npm >/dev/null 2>&1; then
        # Re-check the VERSION, not just presence: Ubuntu 24.04's apt ships Node 18,
        # which fails the frontend's Node floor. Without this the function reports
        # success, the binary fallback below is never reached, and start.sh exits.
        if command -v npm >/dev/null 2>&1 && node_version_supported; then
            NPM_CMD=npm
            export NPM_CMD
            vader_success "Node.js $(node --version) and npm ready"
            return 0
        fi
    fi
    vader_error "Failed to install nodejs/npm via apt."
    _linux_ensure_node_via_binary || return 1
    return 0
}

platform_gpu_setup() {
    if [ "$GUAARDVARK_ACCEL" = cuda ]; then
        vader_info "Linux + NVIDIA: GPU tuning (persistence/power, ollama systemd drop-in)."
    else
        vader_info "Linux ($GUAARDVARK_ARCH, accel=$GUAARDVARK_ACCEL): no NVIDIA tuning."
    fi
}

platform_service_start() {  # $1 = postgres | redis | ollama  (systemctl)
    if [ "$GUAARDVARK_IS_WSL" = 1 ] && ! systemctl is-system-running >/dev/null 2>&1; then
        vader_info "WSL without systemd: start '$1' via its init script (e.g. 'sudo service $1 start') or manually."
        return 0
    fi
    sudo systemctl start "$1" 2>/dev/null || vader_warn "Could not start '$1' via systemctl — start it manually."
}
