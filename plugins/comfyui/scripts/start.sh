#!/bin/bash
# Start ComfyUI server

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
COMFYUI_DIR="$PLUGIN_ROOT/ComfyUI"
VENV_PYTHON="$PROJECT_ROOT/backend/venv/bin/python"
# Port from plugin.local.json (untracked per-install override) or plugin.json; 8188 fallback.
PORT=$(python3 -c '
import json, sys
for name in ("plugin.local.json", "plugin.json"):
    try:
        print(int(json.load(open(sys.argv[1] + "/" + name))["port"])); break
    except Exception:
        continue
else:
    print(8188)
' "$PLUGIN_ROOT" 2>/dev/null || echo 8188)

# Errors go to stderr so plugin_manager can surface them (it used to only
# show stderr; blank UI failures were start.sh writing only to stdout).
err() { echo "Error: $*" >&2; }

# Check ComfyUI app tree exists
if [ ! -f "$COMFYUI_DIR/main.py" ]; then
    err "ComfyUI not found at $COMFYUI_DIR/main.py"
    err "Restore the app tree (preserves models/): bash $SCRIPT_DIR/restore_app.sh"
    exit 1
fi

# Incomplete clone/rsync can drop nested package comfy/ldm/models (not the
# top-level weights tree). Detect early with a clear restore hint.
if [ ! -f "$COMFYUI_DIR/comfy/ldm/models/autoencoder.py" ]; then
    err "Incomplete ComfyUI install: missing comfy/ldm/models/autoencoder.py"
    err "A bad rsync --exclude 'models' can delete nested packages. Fix with:"
    err "  bash $SCRIPT_DIR/restore_app.sh"
    exit 1
fi

# Check if already running
if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "ComfyUI is already running on port $PORT"
    exit 0
fi

# Check venv python exists
if [ ! -f "$VENV_PYTHON" ]; then
    err "Python venv not found at $VENV_PYTHON"
    exit 1
fi

# Install ComfyUI + custom-node deps into backend/venv (shared — no plugin venv)
# shellcheck source=install_deps.sh
source "$SCRIPT_DIR/install_deps.sh"
install_comfyui_python_deps

# Comfy-Org/ComfyUI#15441: comfy-kitchen>=0.2.28 annotates torch.library
# custom ops with PEP 585 list[int] / X | None. infer_schema accepts those
# only from torch 2.7+. This venv is 2.6+cu124, so a raw 0.2.31 import
# crashed main.py before --listen bound the plugin port. Rewrite those
# annotations in site-packages (re-applied after pip). Do not downgrade
# kitchen — current ComfyUI needs 0.2.31 APIs (int8_attention_is_available).
"$VENV_PYTHON" - <<'PY'
import importlib.metadata as m, sys
from pathlib import Path
def ver(name):
    try:
        return tuple(int(x) for x in m.version(name).split(".")[:3])
    except Exception:
        return (0, 0, 0)
if ver("torch") >= (2, 7):
    sys.exit(0)
# Do not import comfy_kitchen here — 0.2.31 crashes on torch<2.7 before we patch.
root = Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "comfy_kitchen"
if not root.is_dir():
    sys.exit(0)
changed = 0
for path in root.rglob("*.py"):
    src = path.read_text(encoding="utf-8")
    if "custom_op" not in src:
        continue
    new = src
    new = new.replace("list[int]", "List[int]")
    new = new.replace("list[bool]", "List[bool]")
    new = new.replace("float | None", "Optional[float]")
    new = new.replace("torch.Tensor | None", "Optional[torch.Tensor]")
    new = new.replace("int | None", "Optional[int]")
    new = new.replace("str | None", "Optional[str]")
    new = new.replace("torch.dtype | None", "Optional[torch.dtype]")
    new = new.replace("torch.device | int | None", "Optional[object]")
    if new == src:
        continue
    if "from typing import" in new:
        line = [ln for ln in new.splitlines() if ln.startswith("from typing import")][0]
        extras = [n for n in ("List", "Optional") if n not in line]
        if extras:
            new = new.replace(line, line.rstrip() + ", " + ", ".join(extras), 1)
    else:
        lines = new.splitlines(keepends=True)
        insert_at = 0
        for i, ln in enumerate(lines):
            if ln.startswith("from __future__"):
                insert_at = i + 1
        lines.insert(insert_at, "from typing import List, Optional\n")
        new = "".join(lines)
    path.write_text(new, encoding="utf-8")
    changed += 1
if changed:
    print(f"Patched {changed} comfy_kitchen file(s) for torch<2.7 custom-op schemas")
PY

# Upstream leak (Comfy-Org/ComfyUI#13109, open as of 0.30.0): unpatch_model()
# clears self.backup but never self.patches, and offloaded weights hold a live
# reference to that dict — so on the lowvram path (any 16GB card running Wan)
# LoRA patch tensors accumulate in system RAM every generation until the OS
# locks up. Applied here, not just in the tree: ComfyUI/ is sync-excluded and
# restore_app.sh re-clones upstream, so a direct edit never reaches clients
# and doesn't survive a restore. Safe only together with --cache-none below
# (forces LoraLoader re-execution, so cleared patches are rebuilt every run).
"$VENV_PYTHON" - "$COMFYUI_DIR/comfy/model_patcher.py" <<'PYEOF'
import sys
path = sys.argv[1]
src = open(path).read()
if "self.patches.clear()" in src:
    sys.exit(0)  # already patched (or upstream fixed it)
needle = "            self.model.current_weight_patches_uuid = None\n            self.backup.clear()\n"
if src.count(needle) != 1:
    print("Warning: model_patcher.py layout changed; ComfyUI#13109 leak patch "
          "NOT applied — RAM will climb during video batches. Re-check the "
          "unpatch_model() fix against upstream.", file=sys.stderr)
    sys.exit(0)
src = src.replace(needle, needle + "            self.patches.clear()\n")
open(path, "w").write(src)
print("Applied ComfyUI#13109 leak patch (self.patches.clear() in unpatch_model)")
PYEOF

# Comfy-Org/ComfyUI#15441: comfy_kitchen 0.2.28+ crashes torch<=2.6 on import
# (PEP 585 list[int] vs infer_schema). quant_ops.py only caught ImportError, so
# main.py died before --listen bound the plugin port. Widen to Exception.
# Re-applied here because ComfyUI/ is restore_app.sh-cloned.
"$VENV_PYTHON" - "$COMFYUI_DIR/comfy/quant_ops.py" <<'PYEOF'
import sys
path = sys.argv[1]
src = open(path).read()
old = "except ImportError as e:\n    logging.error(f\"Failed to import comfy_kitchen, Error: {e}, fp8 and fp4 support will not be available.\")"
new = "except Exception as e:\n    logging.error(f\"Failed to import comfy_kitchen, Error: {e}, fp8 and fp4 support will not be available.\")"
if old not in src:
    sys.exit(0)  # already patched or upstream changed
src = src.replace(old, new, 1)
open(path, "w").write(src)
print("Applied ComfyUI#15441 kitchen-import guard (except Exception)")
PYEOF

# Log file
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/comfyui.log"

echo "Starting ComfyUI..."
echo "Dir: $COMFYUI_DIR"
echo "Port: $PORT"
echo "Python: $VENV_PYTHON"
echo "Log: $LOG_FILE"

# Start ComfyUI. Memory flags (with the #13109 patch above, one package):
#   --disable-smart-memory  unload models after each run; RAM/VRAM exhaustion
#                           becomes a graceful OOM instead of an OS lockup
#   --cache-none            no execution cache: caps the RAM-pressure cache AND
#                           re-runs LoraLoader each prompt, which the patched
#                           unpatch_model() relies on to rebuild patches
#   --reserve-vram 1.0      the desktop compositor holds 600-800MB VRAM; without
#                           headroom a maxed 16GB card starves it and Wayland dies
#   --disable-pinned-memory ComfyUI otherwise page-locks up to ~90% of system RAM
#                           for offload buffers; pinned pages cannot be swapped or
#                           reclaimed, so the desktop starves before the OOM killer
#                           acts. GUAARDVARK_COMFYUI_PINNED_MEMORY=1 re-enables it.
#   --listen 127.0.0.1      ComfyUI has no auth; every consumer is on this host.
#                           GUAARDVARK_COMFYUI_LISTEN overrides for deliberate LAN use.
#   --preview-method/size   ComfyUI defaults to none, so API runs emit no sampler
#                           thumbnails. auto → Latent2RGB. Keep in lockstep with
#                           backend/services/comfyui_launch_flags.py.
#                           GUAARDVARK_COMFYUI_PREVIEW_METHOD=none turns them off.
cd "$COMFYUI_DIR"
# Under memory pressure the kernel must kill ComfyUI, never the desktop
# (2026-08-04 client box lockups). Children inherit; unprivileged raises allowed.
echo "${GUAARDVARK_OOM_SCORE_ADJ:-500}" > /proc/self/oom_score_adj 2>/dev/null || true
PIN_FLAG="--disable-pinned-memory"
[ "${GUAARDVARK_COMFYUI_PINNED_MEMORY:-0}" = "1" ] && PIN_FLAG=""
PREVIEW_METHOD="${GUAARDVARK_COMFYUI_PREVIEW_METHOD:-auto}"
case "$PREVIEW_METHOD" in
    none|auto|latent2rgb|taesd) ;;
    *) PREVIEW_METHOD=auto ;;
esac
PREVIEW_SIZE="${GUAARDVARK_COMFYUI_PREVIEW_SIZE:-256}"
PREVIEW_FLAGS="--preview-method ${PREVIEW_METHOD}"
if [ "$PREVIEW_METHOD" != "none" ]; then
    PREVIEW_FLAGS="$PREVIEW_FLAGS --preview-size ${PREVIEW_SIZE}"
fi
"$VENV_PYTHON" main.py --listen "${GUAARDVARK_COMFYUI_LISTEN:-127.0.0.1}" --port "$PORT" --disable-smart-memory --cache-none --reserve-vram 1.0 $PIN_FLAG $PREVIEW_FLAGS >> "$LOG_FILE" 2>&1 &

# Save PID
PID_DIR="$PROJECT_ROOT/pids"
mkdir -p "$PID_DIR"
echo $! > "$PID_DIR/comfyui.pid"

echo "ComfyUI started (PID: $(cat $PID_DIR/comfyui.pid))"
