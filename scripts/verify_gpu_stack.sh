#!/bin/bash
# scripts/verify_gpu_stack.sh
# Advisory verification: does each provisioned venv run a GPU kernel, and is
# Ollama serving on the GPU? NEVER blocks boot — exits 0, records degraded
# state to data/gpu_stack_status.json for the health layer.
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATUS_FILE="$REPO_ROOT/data/gpu_stack_status.json"
DEGRADED=()

# Palette only. The vader_* printers fold into the phase board while it is
# open, and this card scrolls under the ready line, so it draws itself.
# set +u: terminal_ui.sh is sourced by scripts that do not run under set -u.
set +u
# shellcheck source=lib/terminal_ui.sh
. "$REPO_ROOT/scripts/lib/terminal_ui.sh"
set -u

# kind is ok, skip, or bad. Plain mode keeps the historical "mark label: detail"
# line so a pipe, NO_COLOR, and the reboot log stay what they were.
gpu_row() {
    local kind="$1" label="$2" detail="$3" mark mcol ncol
    if [ "${_VADER_COLOR:-0}" != 1 ]; then
        case "$kind" in
            ok) mark="✔" ;;
            skip) mark="–" ;;
            *) mark="⚠" ;;
        esac
        echo "  $mark $label: $detail"
        return 0
    fi
    case "$kind" in
        ok) mark="✔"; mcol=$VADER_OK; ncol=$VADER_INK ;;
        skip) mark="–"; mcol=$VADER_MUTED; ncol=$VADER_DIM ;;
        *) mark="⚠"; mcol=$VADER_WARN; ncol=$VADER_DIM ;;
    esac
    printf '  %b%s%b  %b%-22s%b  %b%s%b\n' \
        "$mcol" "$mark" "$VADER_RESET" \
        "$ncol" "$label" "$VADER_RESET" \
        "$VADER_DIM" "$detail" "$VADER_RESET"
}

gpu_banner() {
    if [ "${_VADER_COLOR:-0}" != 1 ]; then
        echo "GPU stack verification (advisory):"
        return 0
    fi
    echo ""
    printf '  %s' "$(_vader_gradient "GPU stack" 186 178 255 80 220 176)"
    printf '   %badvisory%b\n' "$VADER_DIM" "$VADER_RESET"
    echo ""
    printf '  '
    _vader_rule "$(_vader_rule_width)"
}

gpu_footer() {
    if [ "${_VADER_COLOR:-0}" != 1 ]; then
        if [ ${#DEGRADED[@]} -eq 0 ]; then
            echo "GPU stack: healthy."
        else
            echo "GPU stack: DEGRADED (${DEGRADED[*]}). System still boots; see $STATUS_FILE."
        fi
        return 0
    fi
    echo ""
    printf '  '
    _vader_rule "$(_vader_rule_width)"
    echo ""
    if [ ${#DEGRADED[@]} -eq 0 ]; then
        printf '  %b%s%b\n' "$VADER_OK" "GPU stack: healthy." "$VADER_RESET"
    else
        printf '  %b%s%b\n' "$VADER_WARN" \
            "GPU stack: DEGRADED (${DEGRADED[*]}). System still boots; see $STATUS_FILE." \
            "$VADER_RESET"
    fi
    echo ""
}

# A plugin whose manifest declares requirements.gpu=false is a CPU pipeline by
# design (e.g. video_editor: MLT/auto-editor, no torch import anywhere). Its venv
# correctly carries the +cpu torch wheel, so torch.zeros(1).cuda() ALWAYS raises
# "Torch not compiled with CUDA enabled". That is expected, not degraded — the
# same false positive the darwin/MPS branch below already guards against.
# Installing a CUDA wheel there costs ~2.5GB and buys nothing.
plugin_declares_no_gpu() {
    local py="$1" manifest
    command -v python3 >/dev/null 2>&1 || return 1   # no parser: check as before
    # .../plugins/<name>/venv*/bin/python  ->  .../plugins/<name>/plugin.json
    manifest="$(cd "$(dirname "$py")/../.." 2>/dev/null && pwd)/plugin.json"
    [ -f "$manifest" ] || return 1
    python3 - "$manifest" <<'PYEOF' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
sys.exit(0 if (d.get("requirements") or {}).get("gpu") is False else 1)
PYEOF
}

check_venv() {
    local label="$1" py="$2"
    [ -x "$py" ] || { return 0; }   # venv absent = not provisioned, not degraded
    if plugin_declares_no_gpu "$py"; then
        gpu_row skip "$label" "CPU-only plugin (plugin.json requirements.gpu=false) — GPU check skipped"
        return 0
    fi
    # Distinguish a broken torch *import* from a torch that imports but can't run
    # a GPU kernel — totally different causes (missing/mismatched CUDA lib vs a
    # driver/VRAM issue). The old message blamed "GPU kernel" for both and sent
    # people down the wrong rabbit hole. Surface the real error.
    local err
    if err="$("$py" - <<'PY' 2>&1
import sys
try:
    import torch
except Exception as e:
    print(f"IMPORT_FAIL: {type(e).__name__}: {e}"); sys.exit(3)
# Apple Silicon has no CUDA — the correct accelerator is Metal/MPS. Testing
# .cuda() there always raises "Torch not compiled with CUDA enabled", which is
# expected, NOT degraded. Probe MPS instead so a healthy Mac reads as healthy.
if sys.platform == "darwin":
    try:
        if not torch.backends.mps.is_available():
            print("MPS_UNAVAIL: torch.backends.mps.is_available() is False"); sys.exit(5)
        torch.zeros(1).to("mps")
    except Exception as e:
        print(f"KERNEL_FAIL: {type(e).__name__}: {e}"); sys.exit(4)
    print("OK_MPS"); sys.exit(0)
try:
    torch.zeros(1).cuda()
except Exception as e:
    print(f"KERNEL_FAIL: {type(e).__name__}: {e}"); sys.exit(4)
print("OK")
PY
)"; then
        case "$err" in
            OK_MPS*) gpu_row ok "$label" "Metal/MPS active" ;;
            *)       gpu_row ok "$label" "GPU kernel OK" ;;
        esac
    else
        local short="${err:0:140}"
        case "$err" in
            IMPORT_FAIL:*) gpu_row bad "$label" "torch failed to IMPORT — ${short#IMPORT_FAIL: }" ;;
            MPS_UNAVAIL:*) gpu_row bad "$label" "Metal/MPS unavailable — ${short#MPS_UNAVAIL: }" ;;
            KERNEL_FAIL:*) gpu_row bad "$label" "torch imports but GPU kernel failed — ${short#KERNEL_FAIL: }" ;;
            *)             gpu_row bad "$label" "torch GPU check failed — ${short}" ;;
        esac
        DEGRADED+=("$label")
    fi
}

gpu_banner
check_venv "backend"             "$REPO_ROOT/backend/venv/bin/python"
check_venv "audio_foundry"       "$REPO_ROOT/plugins/audio_foundry/venv/bin/python"
check_venv "audio_foundry-music" "$REPO_ROOT/plugins/audio_foundry/venv-music/bin/python"
check_venv "video_editor"        "$REPO_ROOT/plugins/video_editor/venv/bin/python"

# ComfyUI: is the service reachable on its plugin port and serving the GUI?
# It is off by default and installed from the Plugins page, so only an enabled,
# installed ComfyUI that does not answer counts as degraded. Enabled comes from
# backend/plugins/boot_autostart.py (stdlib only, loaded by path the way start.sh
# loads it); it also merges plugin.local.json, so a local port override holds.
# Prints "<port> <enabled 1|0>"; any failure falls back to the default port and
# "enabled", the old behaviour.
COMFY_STATE=$(python3 - "$REPO_ROOT" <<'PY' 2>/dev/null
import importlib.util, pathlib, sys
root = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location(
    "boot_autostart", root / "backend" / "plugins" / "boot_autostart.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
manifest = mod._manifest(root)
state = mod._load_json(root / "data" / "plugin_state.json")
try:
    port = int(manifest.get("port") or mod.DEFAULT_PORT)
except (TypeError, ValueError):
    port = mod.DEFAULT_PORT
print(port, 1 if mod.effective_enabled(root, state, manifest) else 0)
PY
)
read -r COMFY_PORT COMFY_ENABLED <<<"${COMFY_STATE:-8188 1}"
COMFY_PORT="${COMFY_PORT:-8188}"
COMFY_ENABLED="${COMFY_ENABLED:-1}"
if [ ! -f "$REPO_ROOT/plugins/comfyui/ComfyUI/comfyui_version.py" ]; then
    gpu_row skip "comfyui" "not installed; install it from the Plugins page"
elif [ "$COMFY_ENABLED" != 1 ]; then
    gpu_row skip "comfyui" "off"
elif curl -sf --max-time 3 "http://localhost:${COMFY_PORT}/" >/dev/null 2>&1; then
    gpu_row ok "comfyui" "reachable on port ${COMFY_PORT}"
else
    gpu_row bad "comfyui" "not reachable on port ${COMFY_PORT} (start with: bash $REPO_ROOT/plugins/comfyui/scripts/start.sh)"
    DEGRADED+=("comfyui")
fi
# Ollama: is a model loaded fully on GPU? `ollama ps` prints a PROCESSOR column.
if command -v ollama >/dev/null 2>&1 && ollama ps >/dev/null 2>&1; then
    # Match the PROCESSOR column ("NN% CPU"), not a model NAME that contains
    # "cpu" (e.g. a model called cpu-bench would otherwise false-positive).
    if ollama ps 2>/dev/null | grep -qiE '[0-9]+%[[:space:]]*cpu'; then
        gpu_row bad "ollama" "a model is (partly) on CPU — check NUM_PARALLEL vs VRAM"
        DEGRADED+=("ollama-cpu-offload")
    else
        gpu_row ok "ollama" "reachable (no CPU-offload flagged)"
    fi
fi

mkdir -p "$REPO_ROOT/data"
if [ ${#DEGRADED[@]} -eq 0 ]; then
    printf '{"degraded": false, "components": []}\n' > "$STATUS_FILE"
    gpu_footer
else
    printf '{"degraded": true, "components": [%s]}\n' \
        "$(printf '"%s",' "${DEGRADED[@]}" | sed 's/,$//')" > "$STATUS_FILE"
    gpu_footer
fi
exit 0
