"""Hash stability + cache path tests."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mlt.clip_hash import cache_path_for, hash_clip


def test_hash_is_stable_across_calls(tmp_path: Path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x" * 1024)
    h1 = hash_clip(f)
    h2 = hash_clip(f)
    assert h1 == h2
    assert len(h1) == 16


def test_hash_changes_when_size_changes(tmp_path: Path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x" * 1024)
    h1 = hash_clip(f)
    f.write_bytes(b"x" * 2048)
    h2 = hash_clip(f)
    assert h1 != h2


def test_hash_changes_when_mtime_changes(tmp_path: Path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x" * 1024)
    h1 = hash_clip(f)
    # Bump mtime forward without touching contents
    new_mtime = time.time() + 100
    import os
    os.utime(f, (new_mtime, new_mtime))
    h2 = hash_clip(f)
    assert h1 != h2


def test_sha256_mode_differs_from_stat(tmp_path: Path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"hello world")
    assert hash_clip(f, mode="stat") != hash_clip(f, mode="sha256")


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        hash_clip(tmp_path / "nope.mp4")


def test_cache_path_uses_hash(tmp_path: Path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x" * 1024)
    cache_dir = tmp_path / "cache"
    p = cache_path_for(f, cache_dir, suffix=".json")
    assert p.parent == cache_dir
    assert p.suffix == ".json"
    assert p.stem == hash_clip(f)
