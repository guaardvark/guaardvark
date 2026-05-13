"""Stable hash for clip cache keys.

The Art Director's vision-model output is keyed by `(path, mtime, size)` —
not by full content hash, because hashing a 4 GB file is wasteful for the
cache lookup pattern we have. If a clip is replaced with the same path but
different content, mtime + size will catch it.

If the user later wants paranoid content-hash mode (e.g. for shared scratch
dirs where two files might collide on `(path, mtime, size)`), expose
`mode='sha256'` via the optional param.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def hash_clip(path: str | Path, *, mode: str = "stat") -> str:
    """Return a short stable identifier for the file at `path`.

    mode='stat'   — sha256 of "path|mtime|size" (fast, default)
    mode='sha256' — sha256 of file contents (paranoid, slow on big files)
    """
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"cannot hash missing file: {p}")

    if mode == "stat":
        st = p.stat()
        payload = f"{p}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]
    if mode == "sha256":
        h = hashlib.sha256()
        with open(p, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()[:16]
    raise ValueError(f"unknown hash mode {mode!r}")


def cache_path_for(
    clip_path: str | Path,
    cache_dir: str | Path,
    *,
    suffix: str = ".json",
) -> Path:
    """Return the canonical cache path for the file at `clip_path`."""
    h = hash_clip(clip_path)
    return Path(cache_dir) / f"{h}{suffix}"
