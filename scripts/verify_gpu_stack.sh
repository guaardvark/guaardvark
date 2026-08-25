#!/bin/bash
# scripts/verify_gpu_stack.sh
# Advisory verification: does each provisioned venv run a GPU kernel, and is
# Ollama serving on the GPU? NEVER blocks boot — exits 0, records degraded
# state to data/gpu_stack_status.json for the health layer.
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATUS_FILE="$REPO_ROOT/data/gpu_stack_status.json"
DEGRADED=()

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
        echo "  – $label: CPU-only plugin (plugin.json requirements.gpu=false) — GPU check skipped"
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
            OK_MPS*) echo "  ✔ $label: Metal/MPS active" ;;
            *)       echo "  ✔ $label: GPU kernel OK" ;;
        esac
    else
        local short="${err:0:140}"
        case "$err" in
            IMPORT_FAIL:*) echo "  ⚠ $label: torch failed to IMPORT — ${short#IMPORT_FAIL: }" ;;
            MPS_UNAVAIL:*) echo "  ⚠ $label: Metal/MPS unavailable — ${short#MPS_UNAVAIL: }" ;;
            KERNEL_FAIL:*) echo "  ⚠ $label: torch imports but GPU kernel failed — ${short#KERNEL_FAIL: }" ;;
            *)             echo "  ⚠ $label: torch GPU check failed — ${short}" ;;
        esac
        DEGRADED+=("$label")
    fi
}

echo "GPU stack verification (advisory):"
check_venv "backend"             "$REPO_ROOT/backend/venv/bin/python"
check_venv "audio_foundry"       "$REPO_ROOT/plugins/audio_foundry/venv/bin/python"
check_venv "audio_foundry-music" "$REPO_ROOT/plugins/audio_foundry/venv-music/bin/python"
check_venv "video_editor"        "$REPO_ROOT/plugins/video_editor/venv/bin/python"

# Ollama: is a model loaded fully on GPU? `ollama ps` prints a PROCESSOR column.
if command -v ollama >/dev/null 2>&1 && ollama ps >/dev/null 2>&1; then
    # Match the PROCESSOR column ("NN% CPU"), not a model NAME that contains
    # "cpu" (e.g. a model called cpu-bench would otherwise false-positive).
    if ollama ps 2>/dev/null | grep -qiE '[0-9]+%[[:space:]]*cpu'; then
        echo "  ⚠ ollama: a model is (partly) on CPU — check NUM_PARALLEL vs VRAM"
        DEGRADED+=("ollama-cpu-offload")
    else
        echo "  ✔ ollama: reachable (no CPU-offload flagged)"
    fi
fi

mkdir -p "$REPO_ROOT/data"
if [ ${#DEGRADED[@]} -eq 0 ]; then
    printf '{"degraded": false, "components": []}\n' > "$STATUS_FILE"
    echo "GPU stack: healthy."
else
    printf '{"degraded": true, "components": [%s]}\n' \
        "$(printf '"%s",' "${DEGRADED[@]}" | sed 's/,$//')" > "$STATUS_FILE"
    echo "GPU stack: DEGRADED (${DEGRADED[*]}). System still boots; see $STATUS_FILE."
fi
exit 0
