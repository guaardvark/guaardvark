#!/bin/bash
# Heal backend/venv after a rebuild, restore, or incomplete pip install.
#
# ComfyUI shares this venv (no plugins/comfyui/venv). Run this after any venv
# wipe, then restart the backend + ComfyUI (or use --restart-comfyui).
#
# Usage:
#   ./scripts/heal_backend_venv.sh                 # full heal
#   ./scripts/heal_backend_venv.sh --skip-cv       # skip requirements-cv.txt
#   ./scripts/heal_backend_venv.sh --comfyui-only    # ComfyUI deps only (fast)
#   ./scripts/heal_backend_venv.sh --no-restart      # don't bounce ComfyUI
#
# Logs: logs/heal_backend_venv.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
VENV_DIR="$BACKEND_DIR/venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
LOG_DIR="$REPO_ROOT/logs"
LOG_FILE="$LOG_DIR/heal_backend_venv.log"

SKIP_CV=0
COMFYUI_ONLY=0
RESTART_COMFYUI=1

while [ $# -gt 0 ]; do
    case "$1" in
        --skip-cv) SKIP_CV=1 ;;
        --comfyui-only) COMFYUI_ONLY=1 ;;
        --no-restart) RESTART_COMFYUI=0 ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
    shift
done

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# Pin-convergence for EVERY pip call in this script: without it, install_pytorch
# and the ComfyUI custom-node reqs re-resolve numpy to 2.x and force the
# repin-downgrade churn (see backend/constraints.txt). Caller override respected.
if [ -z "${PIP_CONSTRAINT:-}" ] && [ -f "$BACKEND_DIR/constraints.txt" ]; then
    export PIP_CONSTRAINT="$BACKEND_DIR/constraints.txt"
fi

# Scratch space for EVERY pip call in this script. pip buffers each wheel body
# in full under TMPDIR (and fetches several in parallel), so the multi-GB CUDA
# stack blows up on boxes where /tmp is a small tmpfs — ENOSPC mid-download
# while `df /` shows plenty free. This script is the documented recovery path
# for exactly that failure, so it must not re-trigger it. Matches start.sh's
# ensure_pip_tmpdir() and install_pytorch.sh (same dir, one shared cache).
if [ -z "${TMPDIR:-}" ] || [ ! -w "${TMPDIR:-/nonexistent}" ]; then
    PIP_TMP="$REPO_ROOT/data/piptmp"
    mkdir -p "$PIP_TMP" 2>/dev/null || true
    if [ -d "$PIP_TMP" ] && [ -w "$PIP_TMP" ]; then
        export TMPDIR="$PIP_TMP"
        export PIP_CACHE_DIR="$PIP_TMP"
    fi
fi

ensure_python_headers() {
    # Native sdist builds (evdev in requirements-base) need Python.h. On Ubuntu
    # 24.04 the system python3.12 ships WITHOUT python3.12-dev — the client box
    # hit this repeatedly (pip run dies at evdev, NOTHING from the file installs).
    local inc
    inc=$("$VENV_PYTHON" -c 'import sysconfig; print(sysconfig.get_paths()["include"])' 2>/dev/null || true)
    if [ -n "$inc" ] && [ -f "$inc/Python.h" ]; then
        return 0
    fi
    log "Python dev headers missing (Python.h) — native wheels (evdev) cannot build."
    if command -v apt-get >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        log "Installing python3.12-dev + python3.12-venv via apt..."
        sudo -n apt-get install -y python3.12-dev python3.12-venv >/dev/null 2>&1 || true
        inc=$("$VENV_PYTHON" -c 'import sysconfig; print(sysconfig.get_paths()["include"])' 2>/dev/null || true)
        if [ -n "$inc" ] && [ -f "$inc/Python.h" ]; then
            log "Python dev headers installed."
            return 0
        fi
    fi
    log "WARNING: could not install headers automatically. Run: sudo apt-get install -y python3.12-dev python3.12-venv"
    return 0   # non-fatal here — the pip step below fails loudly if it matters
}

# Same pip network hardening as start.sh: default 15s socket timeout is too
# tight for multi-hundred-MB wheels; a transient read timeout must not kill
# the heal (observed 2026-08-13). PIP_RESUME_RETRIES is ignored by pip < 25.1.
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-60}"
export PIP_RETRIES="${PIP_RETRIES:-5}"
export PIP_RESUME_RETRIES="${PIP_RESUME_RETRIES:-5}"

# pip install -r with the failure surfaced instead of silently killing the
# script (set -euo pipefail turned the old `pip | tail -5` into a message-less
# death on the first bad wheel). Transient network faults retry with backoff —
# completed wheels are cached, so each retry resumes where the last stopped.
pip_install_req() {
    local req="$1" scratch="$LOG_DIR/.heal_pip_last.log" attempt max_attempts=4
    log "pip install -r $(basename "$req")"
    for attempt in $(seq 1 "$max_attempts"); do
        if "$VENV_PIP" install -r "$req" > "$scratch" 2>&1; then
            tail -n 5 "$scratch"
            return 0
        fi
        if [ "$attempt" -lt "$max_attempts" ] \
           && grep -qE "Read timed out|ReadTimeoutError|Connection broken|ConnectionResetError|Connection aborted|NewConnectionError|ProtocolError|IncompleteRead|Temporary failure in name resolution|Network is unreachable" "$scratch"; then
            log "Transient network fault (attempt $attempt/$max_attempts) — retrying in $((attempt * 10))s..."
            sleep $((attempt * 10))
            continue
        fi
        break
    done
    tail -n 30 "$scratch"
    if grep -q "Python\.h: No such file or directory" "$scratch"; then
        log "ERROR: a native wheel build needs Python dev headers."
        log "Fix:   sudo apt-get install -y python3.12-dev python3.12-venv   — then re-run this script."
    fi
    log "ERROR: pip install -r $(basename "$req") FAILED — nothing from this file was installed. Full output: $scratch"
    exit 1
}

repin_numpy_setuptools() {
    log "Re-pinning numpy<2 and setuptools (ML stack guard)..."
    "$VENV_PIP" install --no-deps --force-reinstall \
        'numpy<2.0,>=1.26.4' 'setuptools>=80.9.0,<81' 2>&1 | tail -3 || true
    # Keep opencv on the project pin if CV deps bumped it to 5.x
    if "$VENV_PYTHON" -c 'import cv2' >/dev/null 2>&1; then
        "$VENV_PIP" install 'opencv-python==4.8.1.78' --quiet 2>&1 | tail -2 || true
    fi
}

heal_backend_core() {
    log "=== Step 1: backend core requirements ==="
    if [ -f "$BACKEND_DIR/requirements-base.txt" ]; then
        pip_install_req "$BACKEND_DIR/requirements-base.txt"
    fi
    if [ -f "$BACKEND_DIR/requirements.txt" ]; then
        pip_install_req "$BACKEND_DIR/requirements.txt"
    fi
    # Packages reconciler verifies but pip resolution sometimes drops
    "$VENV_PIP" install 'websocket-client==1.8.0' --quiet 2>&1 | tail -2 || true
}

heal_pytorch() {
    log "=== Step 2: PyTorch + CUDA family ==="
    if [ -f "$REPO_ROOT/scripts/install_pytorch.sh" ]; then
        GUAARDVARK_TORCH_CHANNEL="$("$VENV_PYTHON" -m backend.services.hardware_policy torch_channel 2>/dev/null || true)" \
            bash "$REPO_ROOT/scripts/install_pytorch.sh" 2>&1 | tail -8 || log "WARNING: install_pytorch.sh exited non-zero"
    fi
    if ! command -v nvidia-smi >/dev/null 2>&1; then
        "$VENV_PIP" uninstall -y nvidia-ml-py pynvml 2>/dev/null | tail -1 || true
    else
        "$VENV_PIP" install nvidia-ml-py --quiet 2>&1 | tail -2 || true
    fi
    repin_numpy_setuptools
    "$VENV_PIP" uninstall -y flash-attn flash_attn xformers 2>/dev/null | tail -1 || true
}

# Reconciler ids named in logs/.dep_reconcile_failed, comma-joined; empty when
# there is no sentinel. Entries look like "  - <id>: <message>", and an id may
# itself contain a colon (isolated_plugin_venv:<plugin>), so the id ends at the
# first ": " rather than the first ":".
sentinel_reconciler_ids() {
    local sentinel="$REPO_ROOT/logs/.dep_reconcile_failed"
    [ -f "$sentinel" ] || return 0
    sed -n 's/^[[:space:]]*- \([^[:space:]]*\): .*$/\1/p' "$sentinel" | sort -u | paste -sd, -
}

heal_dep_reconciler() {
    # The venv reconcilers always run. On top of those, re-run whatever the
    # failure sentinel names: the reconciler only clears the sentinel for the
    # ids a run covers, so a scoped run that skipped the failing step
    # (plugin_bundle, frontend, an isolated plugin venv) left preflight RED no
    # matter how many times this script was re-run — the loop reported on
    # issue #41.
    local only="backend_venv,cli_venv"
    local named
    named="$(sentinel_reconciler_ids)"
    if [ -n "$named" ]; then
        only="$only,$named"
    fi
    log "=== Step 3: dep reconciler ($only) ==="
    if [ -x "$REPO_ROOT/scripts/dep_reconciler.py" ] || [ -f "$REPO_ROOT/scripts/dep_reconciler.py" ]; then
        # MUST run under the venv python: the reconcilers pip-install via
        # sys.executable, so `python3 ...` here made every install hit PEP 668
        # "externally-managed-environment" on Ubuntu 24.04 and fail both
        # targets (Observed: client box sentinel 2026-08-04, backend_venv +
        # cli_venv exit 1). dep_reconciler.py now also self-re-execs as a
        # backstop, but call it correctly regardless.
        "$VENV_PYTHON" "$REPO_ROOT/scripts/dep_reconciler.py" \
            --force --only "$only" --repo-root "$REPO_ROOT" 2>&1 | tail -15 \
            || log "WARNING: dep_reconciler reported issues (see logs/dep_reconciler.log)"
    fi
    repin_numpy_setuptools
}

heal_cv_optional() {
    if [ "$SKIP_CV" -eq 1 ]; then
        log "Skipping requirements-cv.txt (--skip-cv)"
        return 0
    fi
    # Opt-in only (matches start.sh): install when forced, or repair a stack an
    # earlier opt-in already put in this venv. No auto-install on mere GPU
    # presence — that made every fresh GPU box pay a multi-hundred-MB stack for
    # two optional features (face-restore + anatomy/ControlNet).
    if [ "${GUAARDVARK_INSTALL_CV:-0}" != "1" ] \
       && ! "$VENV_PIP" show gfpgan >/dev/null 2>&1; then
        log "Skipping requirements-cv.txt (not opted in, not installed). Opt in with GUAARDVARK_INSTALL_CV=1"
        return 0
    fi
    log "=== Step 4: optional CV / face restoration (requirements-cv.txt) ==="
    if [ -f "$BACKEND_DIR/requirements-cv.txt" ]; then
        set +e
        "$VENV_PIP" install -r "$BACKEND_DIR/requirements-cv.txt" 2>&1 | tail -10
        local rc=$?
        set -e
        repin_numpy_setuptools
        if [ "$rc" -ne 0 ]; then
            log "WARNING: requirements-cv.txt install had errors (face-restore may stay disabled)"
        fi
    fi
}

heal_comfyui_deps() {
    log "=== Step 5: ComfyUI + custom-node deps (backend venv) ==="
    export GUAARDVARK_HEAL_FORCE=1
    export VENV_PYTHON
    bash "$REPO_ROOT/plugins/comfyui/scripts/install_deps.sh"
    repin_numpy_setuptools
}

verify_heal() {
    log "=== Step 6: verification ==="
    local failed=0
    "$VENV_PYTHON" -c "
import sys
checks = [
    ('numpy 1.x', 'import numpy; assert numpy.__version__.startswith(\"1.\")'),
    ('torch', 'import torch'),
    ('flask', 'import flask'),
    ('celery', 'import celery'),
    ('cv2', 'import cv2'),
    ('gguf', 'import gguf'),
    ('websocket', 'import websocket'),
]
for label, stmt in checks:
    try:
        exec(stmt)
        print(f'  OK  {label}')
    except Exception as e:
        print(f'  FAIL {label}: {e}', file=sys.stderr)
        sys.exit(1)
" || failed=1

    if command -v curl >/dev/null 2>&1 && curl -sf http://127.0.0.1:8188/ >/dev/null 2>&1; then
        log "ComfyUI is up — checking critical nodes..."
        "$VENV_PYTHON" -c "
import requests
d = requests.get('http://127.0.0.1:8188/object_info', timeout=10).json()
for n in ('UnetLoaderGGUF', 'VHS_VideoCombine', 'RIFE VFI'):
    ok = n in d
    print(f'  {\"OK\" if ok else \"FAIL\"}  ComfyUI node {n}')
    if not ok:
        raise SystemExit(1)
" || failed=1
    else
        log "ComfyUI not running — skip live node check (restart ComfyUI after heal)"
    fi

    if [ "$failed" -ne 0 ]; then
        log "Verification FAILED — see $LOG_FILE"
        return 1
    fi
    log "Verification passed"
    return 0
}

restart_comfyui_if_requested() {
    if [ "$RESTART_COMFYUI" -ne 1 ]; then
        log "Skipping ComfyUI restart (--no-restart)"
        return 0
    fi
    log "=== Step 7: restart ComfyUI ==="
    if [ -x "$REPO_ROOT/plugins/comfyui/scripts/stop.sh" ]; then
        bash "$REPO_ROOT/plugins/comfyui/scripts/stop.sh" || true
        sleep 2
    fi
    if [ -x "$REPO_ROOT/plugins/comfyui/scripts/start.sh" ]; then
        bash "$REPO_ROOT/plugins/comfyui/scripts/start.sh"
    fi
}

main() {
    log "========== heal_backend_venv START (repo: $REPO_ROOT) =========="

    if [ ! -x "$VENV_PYTHON" ]; then
        log "ERROR: $VENV_PYTHON not found."
        log "Create the venv first (e.g. ./start.sh or system-manager repair), then re-run this script."
        exit 1
    fi

    ensure_python_headers

    if [ "$COMFYUI_ONLY" -eq 1 ]; then
        heal_comfyui_deps
        # See comment below — the leak breaks the comfyui-only path identically.
        unset GUAARDVARK_HEAL_FORCE
        restart_comfyui_if_requested
        repin_numpy_setuptools
        verify_heal || exit 1
        log "========== heal_backend_venv DONE (comfyui-only) =========="
        exit 0
    fi

    heal_backend_core
    heal_pytorch
    heal_dep_reconciler
    heal_cv_optional
    heal_comfyui_deps
    # GUAARDVARK_HEAL_FORCE must NOT leak into the restart: ComfyUI's start.sh
    # re-runs install_deps.sh, and force mode clears the stamps and reinstalls
    # unpinned custom-node reqs AFTER the last repin above — exactly how the
    # client box heal (2026-08-04 00:36) ended on numpy 2.5.1 with verify FAILing.
    unset GUAARDVARK_HEAL_FORCE
    restart_comfyui_if_requested
    # Belt-and-braces: repin once more after the restart's install_deps re-run,
    # so verify below judges the venv state that will actually serve traffic.
    repin_numpy_setuptools
    verify_heal || exit 1

    log "========== heal_backend_venv DONE =========="
    log "Restart the Flask backend if it is running (./start.sh or restart backend service)."
}

main "$@"
