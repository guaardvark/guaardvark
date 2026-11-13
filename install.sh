#!/bin/bash
# Guaardvark bootstrap installer — safe to run via:
#   curl -fsSL https://raw.githubusercontent.com/guaardvark/guaardvark/main/install.sh | bash
#
# Clones the repo and hands off to ./start.sh, which does the real work
# (Python 3.12, venv, Node deps, PostgreSQL, Redis, Ollama, migrations,
# frontend build). Overridables:
#   GUAARDVARK_HOME      install directory        (default: ~/guaardvark)
#   GUAARDVARK_REPO_URL  repo to clone            (default: official GitHub)
#   GUAARDVARK_BRANCH    branch to check out      (default: main)
#   GUAARDVARK_NO_START  set to 1 to clone/update only, without launching
#
# Everything lives inside main(), called on the last line — a truncated
# download can never execute a half-script.

set -euo pipefail

REPO_URL="${GUAARDVARK_REPO_URL:-https://github.com/guaardvark/guaardvark.git}"
BRANCH="${GUAARDVARK_BRANCH:-main}"
DEST="${GUAARDVARK_HOME:-$HOME/guaardvark}"

if [ -t 1 ]; then
    C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[1;33m'; C_RED=$'\033[0;31m'; C_RESET=$'\033[0m'
else
    C_GREEN=""; C_YELLOW=""; C_RED=""; C_RESET=""
fi
info()  { echo "${C_GREEN}[guaardvark]${C_RESET} $*"; }
warn()  { echo "${C_YELLOW}[guaardvark]${C_RESET} $*"; }
fail()  { echo "${C_RED}[guaardvark] ERROR:${C_RESET} $*" >&2; exit 1; }

ensure_git() {
    command -v git >/dev/null 2>&1 && return 0
    warn "git is not installed — attempting to install it."
    if command -v apt-get >/dev/null 2>&1; then
        # sudo may prompt for a password; read it from the terminal, not the
        # curl pipe. Without a controlling terminal (headless), require
        # passwordless sudo.
        if ( : < /dev/tty ) 2>/dev/null; then
            sudo apt-get update -qq && sudo apt-get install -y git < /dev/tty \
                || fail "Could not install git. Install it manually (sudo apt-get install -y git) and re-run."
        else
            sudo -n apt-get install -y git \
                || fail "Could not install git non-interactively. Install it manually and re-run."
        fi
    else
        fail "git is required. Install it with your package manager and re-run."
    fi
}

clone_or_update() {
    if [ -d "$DEST/.git" ]; then
        # Existing checkout: only touch it if it is actually a guaardvark clone.
        local origin
        origin="$(git -C "$DEST" remote get-url origin 2>/dev/null || true)"
        case "$origin" in
            *guaardvark*|"$REPO_URL")
                info "Existing install found at $DEST — updating (git pull --ff-only)..."
                git -C "$DEST" pull --ff-only \
                    || warn "Could not fast-forward $DEST (local changes?). Continuing with the current checkout."
                ;;
            *)
                fail "$DEST exists but is not a Guaardvark checkout (origin: ${origin:-none}).
       Set GUAARDVARK_HOME to a different directory and re-run."
                ;;
        esac
    elif [ -e "$DEST" ] && [ -n "$(ls -A "$DEST" 2>/dev/null)" ]; then
        fail "$DEST exists and is not empty. Set GUAARDVARK_HOME to a different directory and re-run."
    else
        info "Cloning $REPO_URL (branch: $BRANCH) into $DEST..."
        # Shallow clone keeps the first download small; start.sh needs the
        # working tree, not history. `git pull` still works for updates.
        git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$DEST" \
            || fail "git clone failed. Check your network connection and re-run — cloning resumes from scratch."
    fi
}

main() {
    [ "$(uname -s)" = "Linux" ] || warn "Guaardvark's installer targets Linux; other platforms are unsupported and may not work."
    ensure_git
    clone_or_update
    chmod +x "$DEST/start.sh" "$DEST/stop.sh" 2>/dev/null || true

    if [ "${GUAARDVARK_NO_START:-0}" = "1" ]; then
        info "Clone complete (GUAARDVARK_NO_START=1). To launch:  cd $DEST && ./start.sh"
        return 0
    fi

    info "Handing off to start.sh — first run installs everything and can take a while."
    cd "$DEST"
    # Under `curl | bash`, stdin is the script pipe. start.sh (and sudo/apt
    # inside it) must talk to the real terminal, so reattach stdin to /dev/tty
    # when one exists; headless runs proceed with stdin at /dev/null.
    if ( : < /dev/tty ) 2>/dev/null; then
        exec ./start.sh < /dev/tty
    else
        warn "No controlling terminal — running start.sh non-interactively."
        exec ./start.sh < /dev/null
    fi
}

main "$@"
