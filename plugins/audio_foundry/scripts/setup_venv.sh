#!/bin/bash
# plugins/audio_foundry/scripts/setup_venv.sh
# Provision the audio_foundry isolated venvs against the hardware policy.
# Convention recognized by scripts/dep_reconciler registry.classify_plugin_venv_mode().
#
# Idempotent: a venv that satisfies the tracked requirements, imports torch,
# runs a GPU kernel and passes the Chatterbox import check is left untouched.
# A main venv that works but is behind the requirements is brought up to date
# in place (packages added through Audio Studio → Manage models survive); one
# that does not run at all is rebuilt once.
set -u

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
# Guard the paths we `rm -rf` against: if either cd failed and left an empty
# string, refuse rather than risk `rm -rf "/venv"`.
[ -n "$PLUGIN_DIR" ] && [ -d "$PLUGIN_DIR" ] || { echo "FATAL: PLUGIN_DIR unresolved" >&2; exit 1; }
[ -n "$REPO_ROOT" ] && [ -d "$REPO_ROOT" ] || { echo "FATAL: REPO_ROOT unresolved" >&2; exit 1; }
PY="${PYTHON_CMD:-python3.12}"
INSTALL_PYTORCH="$REPO_ROOT/scripts/install_pytorch.sh"
BACKEND_PY="$REPO_ROOT/backend/venv/bin/python"

# Resolve the torch channel from the single source of truth (backend venv).
# Empty string (backend venv/policy not importable yet) → install_pytorch.sh
# falls back to its own GPU detection.
TORCH_CHANNEL="$("$BACKEND_PY" -m backend.services.hardware_policy torch_channel 2>/dev/null || echo "")"

log() { echo "  [audio_foundry/setup_venv] $*"; }

# shellcheck source=plugins/audio_foundry/scripts/venv_sync.sh
. "$PLUGIN_DIR/scripts/venv_sync.sh"

# verify_venv <venv_path> <verify_python_snippet> -> 0 ok / 1 fail
verify_venv() {
    local venv="$1" snippet="$2"
    [ -x "$venv/bin/python" ] || return 1
    "$venv/bin/python" - <<PYEOF
import sys
try:
    import torch
    if torch.cuda.is_available():
        torch.zeros(1).cuda()  # exercise a real kernel on the detected arch
$snippet
except Exception as e:
    print(f"VERIFY_FAIL: {e!r}", file=sys.stderr)
    sys.exit(1)
sys.exit(0)
PYEOF
}

# build_main_venv: create an empty venv, then fill it the same way an
# existing one is brought up to date.
build_main_venv() {
    local venv="$PLUGIN_DIR/venv"
    log "Building main venv at $venv"
    rm -rf "$venv"
    "$PY" -m venv "$venv" || { log "venv create failed"; return 1; }
    af_sync_main_venv "$venv" "$PLUGIN_DIR" "$REPO_ROOT" || { log "requirements install failed"; return 1; }
}

# main_venv_runs: torch + Chatterbox import, a GPU kernel where there is a GPU.
# main_venv_current: the same, and every tracked requirement is satisfied.
main_venv_runs() { verify_venv "$PLUGIN_DIR/venv" "$CHATTERBOX_SNIPPET"; }
main_venv_current() {
    main_venv_runs && af_requirements_ok "$PLUGIN_DIR/venv" \
        "$PLUGIN_DIR/requirements.txt" "$PLUGIN_DIR/requirements-nodeps.txt"
}

# build_music_venv: ACE-Step sibling venv. Mirrors build_main_venv's explicit
# per-step guards so a failure is logged loudly, not swallowed.
build_music_venv() {
    local venv="$PLUGIN_DIR/venv-music"
    log "Building venv-music at $venv"
    rm -rf "$venv"
    "$PY" -m venv "$venv" || { log "venv-music create failed"; return 1; }
    "$venv/bin/pip" install --upgrade pip >/dev/null 2>&1
    "$venv/bin/pip" install -r "$PLUGIN_DIR/requirements-music.txt" \
        || { log "venv-music requirements install failed"; return 1; }
    TARGET_VENV="$venv" GUAARDVARK_TORCH_CHANNEL="$TORCH_CHANNEL" bash "$INSTALL_PYTORCH" --venv "$venv" \
        || log "install_pytorch returned non-zero for venv-music (continuing to verify)"
}

# NOTE: snippet strings below are injected INSIDE the verify_venv `try:` block,
# so each line MUST keep its leading 4-space indent or Python raises
# IndentationError (which would read as a false VERIFY_FAIL).
# Chatterbox load check — the decisive runtime verify for the torch override.
CHATTERBOX_SNIPPET='    from chatterbox.tts import ChatterboxTTS
    print("chatterbox import OK on torch", torch.__version__)'

ACE_SNIPPET='    print("venv-music torch OK", torch.__version__)'

rc=0

# --- main venv ---
# start.sh re-syncs the main venv when these are older than the requirement files.
stamp_main_venv() { touch "$PLUGIN_DIR/venv/.deps_installed" "$PLUGIN_DIR/venv/.torch_import_verified"; }

if main_venv_current; then
    log "main venv healthy — skipping"
    stamp_main_venv
else
    if main_venv_runs; then
        log "main venv is behind requirements.txt — updating in place"
        af_sync_main_venv "$PLUGIN_DIR/venv" "$PLUGIN_DIR" "$REPO_ROOT" \
            || log "in-place update returned non-zero (continuing to verify)"
    else
        build_main_venv
    fi
    if main_venv_current; then
        log "main venv up to date + verified"
        stamp_main_venv
    elif main_venv_runs; then
        log "DEGRADED: main venv runs but does not satisfy requirements.txt (see REQUIREMENTS_FAIL above)."
        log "  Fix: re-run this script with network access to PyPI and download.pytorch.org."
        rc=1
    else
        log "DEGRADED: Chatterbox failed to load on this venv's torch (see VERIFY_FAIL above)."
        log "  Fix: confirm chatterbox-tts supports this torch, or rely on Kokoro fallback."
        rc=1
    fi
fi

# --- venv-music (ACE-Step) ---
if verify_venv "$PLUGIN_DIR/venv-music" "$ACE_SNIPPET"; then
    log "venv-music healthy — skipping"
else
    build_music_venv
    if verify_venv "$PLUGIN_DIR/venv-music" "$ACE_SNIPPET"; then
        log "venv-music rebuilt + verified"
    else
        log "DEGRADED: venv-music failed to import torch."
        rc=1
    fi
fi

# rc=1 means DEGRADED (one venv unhealthy), NOT dead: the main venv's Kokoro
# fallback still produces voice. Callers should treat rc=1 as degraded, not absent.
exit $rc
