"""Hashing helpers. Stdlib-only."""
from __future__ import annotations

import hashlib
from pathlib import Path

CHUNK = 65536
EXCLUDE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache"}
EXCLUDE_FILE_SUFFIXES = (".pyc", ".pyo", ".pyd")


def hash_file(path: Path) -> str | None:
    """Return 'sha256:<hex>' for the file, or None if missing."""
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def hash_dir(path: Path) -> str | None:
    """Return 'sha256:<hex>' for the directory's contents.

    Walks files in sorted order, excludes __pycache__ and *.pyc, and hashes
    a stream of `relpath\\0filehash\\n` lines for deterministic output.
    """
    if not path.is_dir():
        return None
    entries: list[tuple[str, str]] = []
    for sub in sorted(path.rglob("*")):
        if not sub.is_file():
            continue
        if any(part in EXCLUDE_DIR_NAMES for part in sub.relative_to(path).parts):
            continue
        if sub.suffix in EXCLUDE_FILE_SUFFIXES:
            continue
        rel = sub.relative_to(path).as_posix()
        fh = hash_file(sub)
        if fh is None:
            continue
        entries.append((rel, fh))

    h = hashlib.sha256()
    for rel, fh in entries:
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        h.update(fh.encode("ascii"))
        h.update(b"\n")
    return f"sha256:{h.hexdigest()}"
