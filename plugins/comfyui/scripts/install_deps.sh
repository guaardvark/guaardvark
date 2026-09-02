#!/bin/bash
# Install ComfyUI core + custom-node Python deps into backend/venv.
#
# ComfyUI has NO separate plugin venv — it shares backend/venv/bin/python.
# Called from plugins/comfyui/scripts/start.sh on every start, and from
# scripts/heal_backend_venv.sh after a venv rebuild/restore.
#
# Env:
#   GUAARDVARK_HEAL_FORCE=1  — clear install stamps and reinstall everything
#   VENV_PYTHON                — override python path (default: backend/venv)

# Resolve this library's directory once (works sourced or executed directly).
_COMFYUI_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Force ComfyUI-Manager to network_mode=offline (OFFLINE-FIRST: Manager otherwise
# fetches 5 cache JSONs from raw.githubusercontent.com + ComfyRegistry on every
# ComfyUI start). Runs on every start so it covers fresh installs, restore_app.sh,
# and a Manager added later. Deliberate node installs/updates: set
# GUAARDVARK_COMFYUI_NETWORK_MODE=public (or private) for that start; the next
# unflagged start reverts to offline.
ensure_comfyui_manager_offline() {
    local SCRIPT_DIR PLUGIN_ROOT PROJECT_ROOT COMFYUI_DIR MODE PY
    SCRIPT_DIR="$_COMFYUI_SCRIPTS_DIR"
    PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
    PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
    COMFYUI_DIR="${1:-$PLUGIN_ROOT/ComfyUI}"
    MODE="${GUAARDVARK_COMFYUI_NETWORK_MODE:-offline}"

    [ -d "$COMFYUI_DIR" ] || return 0

    case "$MODE" in
        offline|private|public) ;;
        *)
            echo "WARNING: invalid GUAARDVARK_COMFYUI_NETWORK_MODE='$MODE' — using offline" >&2
            MODE=offline
            ;;
    esac

    PY="${VENV_PYTHON:-$PROJECT_ROOT/backend/venv/bin/python}"
    [ -x "$PY" ] || PY="$(command -v python3 || true)"
    if [ -z "$PY" ]; then
        echo "WARNING: no python found — cannot enforce ComfyUI-Manager network_mode" >&2
        return 0
    fi

    "$PY" - "$COMFYUI_DIR" "$MODE" <<'PYEOF'
import configparser, pathlib, sys

comfy = pathlib.Path(sys.argv[1])
mode = sys.argv[2]
# Manager v3.x layout + legacy 2.x layout; the unused one is inert.
for rel in ("user/__manager/config.ini", "user/default/ComfyUI-Manager/config.ini"):
    path = comfy / rel
    cfg = configparser.ConfigParser(interpolation=None)
    if path.exists():
        try:
            cfg.read(path)
        except configparser.Error:
            cfg = configparser.ConfigParser(interpolation=None)
    if not cfg.has_section("default"):
        cfg.add_section("default")
    if cfg.get("default", "network_mode", fallback=None) == mode:
        continue
    cfg.set("default", "network_mode", mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        cfg.write(fh)
    print(f"ComfyUI-Manager network_mode -> {mode} ({path})")
PYEOF
}

# Ensure REQUIRED custom nodes exist, from the pinned manifest
# (plugins/comfyui/custom_nodes.manifest). A fresh box previously had NO
# mechanism to obtain these: plugins/comfyui/ComfyUI/ is excluded from
# Interconnector sync, restore_app.sh deliberately never touches
# custom_nodes/, and this script only installed reqs for nodes ALREADY on
# disk — so clients booted with facerestore_cf only and video generation
# failed with "missing VHS_VideoCombine" (Observed: client box, 2026-08-04).
# Pinned-SHA fetch keeps clients on master's tested revisions; falls back to
# a shallow default-branch clone when the pinned fetch isn't possible.
# Offline: per-node warning, non-fatal. Skip: GUAARDVARK_COMFYUI_AUTONODES=0.
ensure_custom_nodes() {
    local COMFYUI_DIR="$1"
    local PLUGIN_ROOT MANIFEST CN_DIR
    PLUGIN_ROOT="$(cd "$_COMFYUI_SCRIPTS_DIR/.." && pwd)"
    MANIFEST="$PLUGIN_ROOT/custom_nodes.manifest"
    CN_DIR="$COMFYUI_DIR/custom_nodes"

    [ "${GUAARDVARK_COMFYUI_AUTONODES:-1}" = "0" ] && return 0
    [ -f "$MANIFEST" ] || return 0
    if ! command -v git >/dev/null 2>&1; then
        echo "WARNING: git not found — cannot install missing custom nodes (video generation may be unavailable)"
        return 0
    fi
    mkdir -p "$CN_DIR"

    local name url sha dest
    while IFS='|' read -r name url sha; do
        case "$name" in ''|'#'*) continue ;; esac
        [ -n "$url" ] || continue
        dest="$CN_DIR/$name"
        [ -d "$dest" ] && continue
        echo "Custom node missing: $name — fetching pinned revision ${sha:0:12}..."
        if git init -q "$dest" 2>/dev/null \
           && git -C "$dest" remote add origin "$url" 2>/dev/null \
           && git -C "$dest" fetch -q --depth 1 origin "$sha" 2>/dev/null \
           && git -C "$dest" checkout -q FETCH_HEAD 2>/dev/null; then
            echo "  installed $name @ ${sha:0:12}"
        else
            # Pinned fetch can fail (old git, server refuses SHA fetch) — a
            # current default-branch node beats no node at all.
            rm -rf "$dest"
            if git clone -q --depth 1 "$url" "$dest" 2>/dev/null; then
                echo "  installed $name @ default branch (pinned fetch unavailable)"
            else
                rm -rf "$dest"
                echo "  WARNING: could not fetch $name from $url (offline?) — features needing it stay unavailable."
            fi
        fi
    done < "$MANIFEST"
}

# Print the requirements.txt paths of the manifest's nodes that exist on disk,
# one per line, in manifest order. Usage: _manifest_node_requirements <manifest> <custom_nodes dir>
_manifest_node_requirements() {
    local manifest="$1" cn_dir="$2" name url sha
    [ -f "$manifest" ] || return 0
    while IFS='|' read -r name url sha; do
        case "$name" in ''|'#'*) continue ;; esac
        [ -f "$cn_dir/$name/requirements.txt" ] && printf '%s\n' "$cn_dir/$name/requirements.txt"
    done < "$manifest"
}

install_comfyui_python_deps() {
    local SCRIPT_DIR PLUGIN_ROOT PROJECT_ROOT COMFYUI_DIR
    SCRIPT_DIR="$_COMFYUI_SCRIPTS_DIR"
    PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
    PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
    COMFYUI_DIR="$PLUGIN_ROOT/ComfyUI"
    VENV_PYTHON="${VENV_PYTHON:-$PROJECT_ROOT/backend/venv/bin/python}"

    if [ ! -f "$VENV_PYTHON" ]; then
        echo "Error: Python venv not found at $VENV_PYTHON" >&2
        return 1
    fi

    if [ ! -f "$COMFYUI_DIR/main.py" ]; then
        echo "Error: ComfyUI not found at $COMFYUI_DIR/main.py" >&2
        return 1
    fi

    # Pin-convergence: ComfyUI core + custom-node requirements install into the
    # SHARED backend venv, and unpinned "numpy" lines in custom-node reqs were
    # one of the two sources dragging numpy 2.4.x in over the ML stack's
    # numpy<2.0 pin (install → force-downgrade churn on every boot; Observed in
    # the 24.04 client box setup.log). Constrain the resolver up front — the REPIN
    # step below stays as a no-op safety net. Caller override respected.
    if [ -z "${PIP_CONSTRAINT:-}" ] && [ -f "$PROJECT_ROOT/backend/constraints.txt" ]; then
        export PIP_CONSTRAINT="$PROJECT_ROOT/backend/constraints.txt"
    fi

    # Egress lockdown first, so it holds even if a later pip step fails.
    ensure_comfyui_manager_offline "$COMFYUI_DIR"

    # Required custom nodes BEFORE the reqs loop below, so freshly fetched
    # nodes get their requirements installed in the same pass.
    ensure_custom_nodes "$COMFYUI_DIR"

    local COMFYUI_REQS REQS_STAMP CN_DIR CN_STAMP
    COMFYUI_REQS="$COMFYUI_DIR/requirements.txt"
    REQS_STAMP="$PLUGIN_ROOT/.requirements_installed"
    CN_DIR="$COMFYUI_DIR/custom_nodes"
    CN_STAMP="$PLUGIN_ROOT/.custom_nodes_installed"

    if [ "${GUAARDVARK_HEAL_FORCE:-0}" = "1" ]; then
        echo "Force heal: clearing ComfyUI dependency stamps..."
        rm -f "$REQS_STAMP" "$CN_STAMP"
    fi

    # ComfyUI core requirements
    if [ -f "$COMFYUI_REQS" ]; then
        local REQS_HASH STAMP_HASH
        REQS_HASH=$(md5sum "$COMFYUI_REQS" 2>/dev/null | cut -d' ' -f1)
        STAMP_HASH=""
        [ -f "$REQS_STAMP" ] && STAMP_HASH=$(cat "$REQS_STAMP" 2>/dev/null)
        # The stamp records requirements CONTENT, not which venv it landed in.
        # A copied plugin folder brings the stamp to a machine whose venv never
        # installed these deps — probe imports ComfyUI hard-requires and clear
        # the stamp when they're missing (same pattern as the custom-nodes probe).
        if [ -f "$REQS_STAMP" ] && ! "$VENV_PYTHON" -c 'import comfy_aimdo, blake3, alembic, av' >/dev/null 2>&1; then
            echo "Requirements stamp present but core imports missing (copied venv/stamp?) — reinstalling..."
            rm -f "$REQS_STAMP"
            STAMP_HASH=""
        fi
        if [ "$REQS_HASH" != "$STAMP_HASH" ]; then
            echo "Installing ComfyUI requirements..."
            "$VENV_PYTHON" -m pip install -r "$COMFYUI_REQS" --quiet 2>&1 | tail -5
            echo "$REQS_HASH" > "$REQS_STAMP"
        fi

        local PINNED_FE INSTALLED_FE
        PINNED_FE=$(grep -E '^comfyui-frontend-package==' "$COMFYUI_REQS" 2>/dev/null | head -1 | cut -d= -f3 || true)
        if [ -n "$PINNED_FE" ]; then
            INSTALLED_FE=$("$VENV_PYTHON" -c "import comfyui_frontend_package as f; print(getattr(f,'__version__',''))" 2>/dev/null || true)
            if [ "$INSTALLED_FE" != "$PINNED_FE" ]; then
                echo "ComfyUI frontend drift ('$INSTALLED_FE' != '$PINNED_FE') — reinstalling..."
                "$VENV_PYTHON" -m pip install --quiet "comfyui-frontend-package==$PINNED_FE" 2>&1 | tail -3
            fi
        fi
    fi

    # LTX-2.5 audio VAE lives in models/vae/, but LTXVAudioVAELoader only lists
    # models/checkpoints/ — bridge with a symlink so folder copies to new
    # machines don't fail workflow validation on a missing checkpoints entry.
    local LTX_AUDIO_VAE="ltx-2.5-audio-vae-bf16.safetensors"
    local MODELS_DIR="$COMFYUI_DIR/models"
    local CKPT_LINK="$MODELS_DIR/checkpoints/$LTX_AUDIO_VAE"
    if [ -f "$MODELS_DIR/vae/$LTX_AUDIO_VAE" ]; then
        # A dangling link (e.g. from a partial copy) blocks ln -s; replace it.
        [ -L "$CKPT_LINK" ] && [ ! -e "$CKPT_LINK" ] && rm -f "$CKPT_LINK"
        if [ ! -e "$CKPT_LINK" ]; then
            echo "Linking LTX-2.5 audio VAE into checkpoints/ (loader lists that folder only)..."
            mkdir -p "$MODELS_DIR/checkpoints"
            ln -s "../vae/$LTX_AUDIO_VAE" "$CKPT_LINK" \
                || echo "Warning: could not create audio VAE symlink"
        fi
    fi

    # facerestore_cf node + weights (video face-restore path)
    local FACERESTORE_DIR FR_MODELS_DIR
    FACERESTORE_DIR="$CN_DIR/facerestore_cf"
    if [ ! -f "$FACERESTORE_DIR/__init__.py" ]; then
        echo "Installing facerestore_cf custom node (face restore / CodeFormer)..."
        rm -rf "$FACERESTORE_DIR"
        git clone --depth 1 https://github.com/mav-rik/facerestore_cf.git "$FACERESTORE_DIR" 2>&1 | tail -3
    fi
    FR_MODELS_DIR="$COMFYUI_DIR/models/facerestore_models"
    mkdir -p "$FR_MODELS_DIR"
    if [ ! -f "$FR_MODELS_DIR/codeformer.pth" ]; then
        echo "Downloading codeformer.pth for face restore..."
        curl -fsSL -o "$FR_MODELS_DIR/codeformer.pth" \
            "https://github.com/sczhou/CodeFormer/releases/download/v0.1.0/codeformer.pth" \
            || wget -q -O "$FR_MODELS_DIR/codeformer.pth" \
            "https://github.com/sczhou/CodeFormer/releases/download/v0.1.0/codeformer.pth"
    fi

    # Requirements of the REQUIRED custom nodes (custom_nodes.manifest) → backend
    # venv. Only manifest nodes: plugins/comfyui/ComfyUI/ is outside git and the
    # Interconnector sync, so every box carries its own extra nodes, and an
    # extra node's requirements.txt (comfyui_controlnet_aux asks for mediapipe,
    # unpinned opencv-python and scikit-image) would otherwise be installed into
    # the shared venv on every forced heal, on top of the ML stack's pins. Extra
    # nodes stay on disk and load whatever the venv already has.
    if [ -d "$CN_DIR" ]; then
        local CN_REQ_FILES CN_HASH CN_STAMP_HASH
        CN_REQ_FILES=$(_manifest_node_requirements "$PLUGIN_ROOT/custom_nodes.manifest" "$CN_DIR")
        if [ -n "$CN_REQ_FILES" ]; then
            CN_HASH=$(cat $CN_REQ_FILES 2>/dev/null | md5sum | cut -d' ' -f1 || true)
            CN_STAMP_HASH=""
            [ -f "$CN_STAMP" ] && CN_STAMP_HASH=$(cat "$CN_STAMP" 2>/dev/null)
            if [ -f "$CN_STAMP" ] && ! "$VENV_PYTHON" -c 'import cv2, gguf' >/dev/null 2>&1; then
                echo "Custom-node stamp present but video imports missing — reinstalling..."
                rm -f "$CN_STAMP"
                CN_STAMP_HASH=""
            fi
            if [ "$CN_HASH" != "$CN_STAMP_HASH" ]; then
                echo "Installing custom-node requirements..."
                set +e
                for req in $CN_REQ_FILES; do
                    local node_name
                    node_name=$(basename "$(dirname "$req")")
                    echo "  - $node_name"
                    "$VENV_PYTHON" -m pip install -r "$req" --quiet 2>&1 | tail -2
                    if [ $? -ne 0 ]; then
                        echo "    WARNING: pip install failed for $node_name (node may be disabled at runtime)."
                    fi
                done
                set -e
                echo "$CN_HASH" > "$CN_STAMP"
            fi
        fi
    fi

    # torchaudio/torchvision CUDA tag consistency (ComfyUI audio nodes)
    if ! "$VENV_PYTHON" -c 'import torchaudio' >/dev/null 2>&1; then
        local TORCH_CUDA TA_VER CH TV_VER REPIN
        TORCH_CUDA=$("$VENV_PYTHON" -c 'import torch; print((torch.version.cuda or "").replace(".",""))' 2>/dev/null || true)
        TA_VER=$("$VENV_PYTHON" -c 'import importlib.metadata as m,re; print(re.sub(r"\+.*","",m.version("torchaudio")))' 2>/dev/null || true)
        if [ -n "$TORCH_CUDA" ] && [ -n "$TA_VER" ]; then
            CH="cu${TORCH_CUDA}"
            TV_VER=$("$VENV_PYTHON" -c 'import importlib.metadata as m,re; print(re.sub(r"\+.*","",m.version("torchvision")))' 2>/dev/null || true)
            REPIN="torchaudio==${TA_VER}+${CH}"
            [ -n "$TV_VER" ] && REPIN="$REPIN torchvision==${TV_VER}+${CH}"
            echo "torch-family CUDA mismatch — re-pinning ($REPIN)..."
            "$VENV_PYTHON" -m pip install --no-deps --force-reinstall $REPIN \
                --index-url "https://download.pytorch.org/whl/${CH}" 2>&1 | tail -3
        else
            echo "WARNING: torchaudio import fails (cuda tag='$TORCH_CUDA', ver='$TA_VER') — audio nodes stay disabled (non-fatal)."
        fi
    fi

    # Video-critical deps (Wan GGUF + VHS encode)
    local VIDEO_DEPS_MISSING=()
    # opencv-python is the declared cv2 distribution; backend/constraints.txt fixes its version.
    "$VENV_PYTHON" -c 'import cv2' >/dev/null 2>&1 || VIDEO_DEPS_MISSING+=('opencv-python')
    "$VENV_PYTHON" -c 'import gguf' >/dev/null 2>&1 || VIDEO_DEPS_MISSING+=('gguf>=0.13.0' 'sentencepiece' 'protobuf')
    "$VENV_PYTHON" -c 'import imageio_ffmpeg' >/dev/null 2>&1 || VIDEO_DEPS_MISSING+=('imageio-ffmpeg')
    if [ ${#VIDEO_DEPS_MISSING[@]} -gt 0 ]; then
        echo "Installing video-critical ComfyUI deps: ${VIDEO_DEPS_MISSING[*]}"
        "$VENV_PYTHON" -m pip install "${VIDEO_DEPS_MISSING[@]}" --quiet 2>&1 | tail -5
    fi

    # Common lightweight custom-node deps
    local OPTIONAL_CN_DEPS=(matplotlib scikit-image deepdiff lpips piexif)
    local OPTIONAL_MISSING=() pkg mod
    for pkg in "${OPTIONAL_CN_DEPS[@]}"; do
        mod="$pkg"
        [ "$pkg" = "scikit-image" ] && mod="skimage"
        "$VENV_PYTHON" -c "import ${mod}" >/dev/null 2>&1 || OPTIONAL_MISSING+=("$pkg")
    done
    if [ ${#OPTIONAL_MISSING[@]} -gt 0 ]; then
        echo "Installing common ComfyUI custom-node deps: ${OPTIONAL_MISSING[*]}"
        "$VENV_PYTHON" -m pip install "${OPTIONAL_MISSING[@]}" --quiet 2>&1 | tail -5
    fi

    # websocket-client — ComfyUI progress bridge + outreach scrapers (not always pulled by node reqs)
    if ! "$VENV_PYTHON" -c 'import websocket' >/dev/null 2>&1; then
        echo "Installing websocket-client..."
        "$VENV_PYTHON" -m pip install 'websocket-client==1.8.0' --quiet 2>&1 | tail -2
    fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    set -e
    install_comfyui_python_deps
fi
