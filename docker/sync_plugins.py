#!/usr/bin/env python3
"""Bring the plugin sources shipped in this image into /app/plugins.

/app/plugins is a volume, so what plugins build there at runtime survives an
image update: the ComfyUI app and its models, plugin environments, install
stamps, per-install settings. The image's own copy of the plugin sources sits
at /app/.plugins-dist. Each start copies every shipped file whose content
changed, and deletes a file only when an earlier image shipped it and this one
no longer does (the list is kept in .shipped-files.json on the volume).
Nothing else on the volume is touched.
"""

from __future__ import annotations

import filecmp
import json
import os
import shutil
import sys
from pathlib import Path

DIST = Path(os.environ.get("GUAARDVARK_PLUGINS_DIST", "/app/.plugins-dist"))
LIVE = Path(os.environ.get("GUAARDVARK_PLUGINS_DIR", "/app/plugins"))
MANIFEST = ".shipped-files.json"


def shipped_files(root: Path) -> set[str]:
    out = set()
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            out.add(str((Path(dirpath) / name).relative_to(root)))
    return out


def main() -> int:
    if not DIST.is_dir():
        print(f"sync_plugins: {DIST} not found; plugins left as they are", file=sys.stderr)
        return 0
    LIVE.mkdir(parents=True, exist_ok=True)
    manifest_path = LIVE / MANIFEST
    try:
        before = set(json.loads(manifest_path.read_text()))
    except (OSError, ValueError):
        before = set()
    now = shipped_files(DIST)

    copied = 0
    for rel in sorted(now):
        src, dst = DIST / rel, LIVE / rel
        if dst.is_file() and not dst.is_symlink() and filecmp.cmp(src, dst, shallow=False):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_symlink():
            dst.unlink()
        shutil.copy2(src, dst)
        copied += 1

    removed = 0
    for rel in sorted(before - now):
        path = LIVE / rel
        if path.is_file() or path.is_symlink():
            path.unlink()
            removed += 1

    tmp = manifest_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(now)))
    tmp.replace(manifest_path)
    if copied or removed:
        print(f"sync_plugins: {copied} file(s) updated, {removed} no longer shipped removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
