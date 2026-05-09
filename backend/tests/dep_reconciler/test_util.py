import hashlib
from pathlib import Path

from scripts.dep_reconciler.util import hash_file, hash_dir


def test_hash_file_returns_sha256_hex(tmp_path):
    f = tmp_path / "manifest.txt"
    f.write_text("hello\n")
    expected = hashlib.sha256(b"hello\n").hexdigest()
    assert hash_file(f) == f"sha256:{expected}"


def test_hash_file_missing_returns_none(tmp_path):
    assert hash_file(tmp_path / "nope.txt") is None


def test_hash_dir_is_deterministic(tmp_path):
    (tmp_path / "a.txt").write_text("A")
    (tmp_path / "b.txt").write_text("B")
    h1 = hash_dir(tmp_path)
    h2 = hash_dir(tmp_path)
    assert h1 == h2
    assert h1 is not None and h1.startswith("sha256:")


def test_hash_dir_excludes_pycache(tmp_path):
    (tmp_path / "a.py").write_text("print('a')")
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "a.cpython-312.pyc").write_bytes(b"junk")

    # Hash without the pyc file should equal hash with it (because we exclude).
    h_with_pyc = hash_dir(tmp_path)
    (pycache / "a.cpython-312.pyc").unlink()
    pycache.rmdir()
    h_without = hash_dir(tmp_path)
    assert h_with_pyc == h_without


def test_hash_dir_changes_when_file_changes(tmp_path):
    (tmp_path / "a.txt").write_text("v1")
    h1 = hash_dir(tmp_path)
    (tmp_path / "a.txt").write_text("v2")
    h2 = hash_dir(tmp_path)
    assert h1 != h2
