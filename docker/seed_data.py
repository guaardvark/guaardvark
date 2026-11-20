#!/usr/bin/env python3
"""Bring the default data files shipped in this image into /app/data.

/app/data is a volume and belongs to the install: uploads, outputs, and the
agent's knowledge files once the agent or the user has edited them. The image
keeps the repository's tracked defaults (self-knowledge, recipes, style
recipes, trainer pages, ...) at /app/.data-dist. Each start:

- copies a default that is missing from the volume;
- replaces a default the install never changed when this image ships a newer
  one (the sha256 recorded at the last seed still matches the file);
- keeps a file the user or the agent changed, and says so once for each newer
  default it passes over.

Nothing is ever deleted. The record lives in .seeded-defaults.json on the volume.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

DIST = Path(os.environ.get("GUAARDVARK_DATA_DIST", "/app/.data-dist"))
LIVE = Path(os.environ.get("GUAARDVARK_DATA_DIR", "/app/data"))
STATE = ".seeded-defaults.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def shipped_files(root: Path) -> list[str]:
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            out.append((Path(dirpath) / name).relative_to(root).as_posix())
    return sorted(out)


def load_state(path: Path) -> dict:
    try:
        files = json.loads(path.read_text())["files"]
        return files if isinstance(files, dict) else {}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _copy(src: Path, dst: Path) -> None:
    """Copy through a temp file so a reader never sees a half-written default."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".seed-tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def seed(dist: Path, live: Path, log=print) -> dict[str, list[str]]:
    """Seed ``live`` from ``dist``; returns the files added, updated and kept."""
    result: dict[str, list[str]] = {"added": [], "updated": [], "kept": []}
    live.mkdir(parents=True, exist_ok=True)
    state_path = live / STATE
    state = load_state(state_path)
    new_state: dict[str, dict] = {}

    for rel in shipped_files(dist):
        if rel == STATE:
            continue
        src, dst = dist / rel, live / rel
        shipped = sha256(src)
        entry = state.get(rel) if isinstance(state.get(rel), dict) else {}
        seeded, declined = entry.get("seeded"), entry.get("declined")

        if not dst.exists() and not dst.is_symlink():
            _copy(src, dst)
            new_state[rel] = {"seeded": shipped}
            result["added"].append(rel)
            continue
        if not dst.is_file() or dst.is_symlink():
            new_state[rel] = entry
            continue

        current = sha256(dst)
        if current == shipped:
            new_state[rel] = {"seeded": shipped}
        elif seeded is not None and current == seeded:
            _copy(src, dst)
            new_state[rel] = {"seeded": shipped}
            result["updated"].append(rel)
        elif seeded == shipped:
            new_state[rel] = {"seeded": seeded}
        else:
            new_state[rel] = {"seeded": seeded, "declined": shipped}
            if declined != shipped:
                result["kept"].append(rel)
                log(f"seed_data: kept {rel} as this install has it; "
                    f"the image's version is at {dist / rel}")

    if new_state != state or not state_path.exists():
        tmp = state_path.with_name(STATE + ".tmp")
        tmp.write_text(json.dumps({"version": 1, "files": new_state}, indent=1, sort_keys=True))
        os.replace(tmp, state_path)
    if result["added"] or result["updated"]:
        log(f"seed_data: {len(result['added'])} default file(s) added, "
            f"{len(result['updated'])} updated")
    return result


def main() -> int:
    if not DIST.is_dir():
        print(f"seed_data: {DIST} not found; data left as it is", file=sys.stderr)
        return 0
    seed(DIST, LIVE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
