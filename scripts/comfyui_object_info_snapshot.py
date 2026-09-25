#!/usr/bin/env python3
"""Write the ComfyUI /object_info snapshot the video workflow contract tests read.

The tests in backend/tests/services/test_workflow_contract_*.py check every
graph the video builders emit against the node classes a real ComfyUI serves:
class names, input names, value types and ranges, and output slots. This script
captures those definitions from a running ComfyUI, keeping only the classes the
builders use (every ``"class_type": "..."`` literal in the two builder modules).

Model-file dropdowns list whatever happens to be on the capturing machine, so
any option that looks like a file name is dropped and the list is left empty;
the tests check file names against the video model registry instead.

Usage, on a machine running ComfyUI with the pinned custom nodes
(plugins/comfyui/custom_nodes.manifest):

    python scripts/comfyui_object_info_snapshot.py
    python scripts/comfyui_object_info_snapshot.py --url http://127.0.0.1:8188 \
        --out backend/tests/fixtures/comfyui_object_info.json

Exits non-zero when a class the builders emit is not served, so a missing
custom node is caught at capture time rather than in a render.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER_MODULES = (
    ROOT / "backend" / "services" / "comfyui_video_workflows.py",
    ROOT / "backend" / "services" / "comfyui_video_generator.py",
)
DEFAULT_OUT = ROOT / "backend" / "tests" / "fixtures" / "comfyui_object_info.json"
FILE_SUFFIXES = (
    ".safetensors", ".sft", ".gguf", ".pth", ".pt", ".ckpt", ".bin", ".onnx", ".pkl",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm", ".mov", ".mkv",
    ".wav", ".mp3", ".flac", ".ogg", ".m4a",
)


def builder_class_types() -> list[str]:
    """Every node class the video builders can emit, from their literals."""
    pattern = re.compile(r'"class_type":\s*"([^"]+)"')
    names: set[str] = set()
    for module in BUILDER_MODULES:
        names.update(pattern.findall(module.read_text(encoding="utf-8")))
    return sorted(names)


def _get_json(url: str):
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


# Built-in entries ComfyUI appends to a model-file list (VAELoader adds these
# to the files in models/vae). A list made only of files and these is a file list.
LOADER_BUILTINS = {"pixel_space", "taesd", "taesdxl", "taesd3", "taef1"}


def _is_file_list(options) -> bool:
    if not isinstance(options, list) or not options:
        return False
    strings = [o for o in options if isinstance(o, str)]
    if len(strings) != len(options):
        return False
    files = [o for o in strings if o.lower().endswith(FILE_SUFFIXES)]
    return bool(files) or set(strings) <= LOADER_BUILTINS


def _strip_file_lists(spec):
    """Empty the option list of a dropdown whose options are file names.

    Handles both input shapes ComfyUI serves: ``[[options...], {...}]`` and
    ``["COMBO", {"options": [...]}]``."""
    if not isinstance(spec, list) or not spec:
        return spec
    head = spec[0]
    if _is_file_list(head):
        return [[], *spec[1:]]
    if head == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        opts = spec[1].get("options")
        if _is_file_list(opts):
            return [head, {**spec[1], "options": []}, *spec[2:]]
    return spec


def _normalise(node: dict) -> dict:
    keep = ("input", "input_order", "output", "output_is_list", "output_name", "python_module")
    out = {k: node[k] for k in keep if k in node}
    for group in ("required", "optional"):
        inputs = (out.get("input") or {}).get(group) or {}
        for name, spec in list(inputs.items()):
            inputs[name] = _strip_file_lists(spec)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default=os.environ.get("GUAARDVARK_COMFYUI_URL", "http://127.0.0.1:8188"))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    base = args.url.rstrip("/")
    info = _get_json(f"{base}/object_info")
    try:
        system = (_get_json(f"{base}/system_stats") or {}).get("system") or {}
    except Exception:  # noqa: BLE001 — the version is informative only
        system = {}

    wanted = builder_class_types()
    missing = [c for c in wanted if c not in info]
    snapshot = {
        "_meta": {
            "comfyui_version": system.get("comfyui_version"),
            "captured": _dt.date.today().isoformat(),
            "source": "scripts/comfyui_object_info_snapshot.py",
            "missing": missing,
        },
        "nodes": {c: _normalise(info[c]) for c in wanted if c in info},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(snapshot, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(snapshot['nodes'])} node classes to {args.out}")
    if missing:
        print("not served by this ComfyUI: " + ", ".join(missing), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
