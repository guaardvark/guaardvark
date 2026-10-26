"""When a stored map is current, who rebuilds it, and what reads cost.

The map is keyed to the tree (git HEAD, file list, newest modification
time, mapper version), reads never build, one background build runs per
root, and each file is parsed once per change. tmp_path trees, a store under
tmp_path, no backend.
"""
import os
import threading
import time
from pathlib import Path

import pytest

from backend.services.system_mapper import facts, store


def _write(root: Path, rel: str, text: str = "") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_STORAGE_DIR", str(tmp_path / "storage"))
    root = tmp_path / "checkout"
    _write(root, "backend/a.py", "from backend import b\n")
    _write(root, "backend/b.py", "x = 1\n")
    monkeypatch.setattr(store, "_jobs", {})
    return root


def test_a_built_map_is_current_until_a_file_changes(tree):
    payload = store.build(tree)
    assert store.is_current(payload, store.current_key(tree))
    later = time.time() + 5
    os.utime(tree / "backend" / "b.py", (later, later))
    assert not store.is_current(payload, store.current_key(tree))


def test_adding_or_removing_a_file_makes_it_stale(tree):
    payload = store.build(tree)
    _write(tree, "backend/c.py", "")
    assert not store.is_current(payload, store.current_key(tree))
    (tree / "backend" / "c.py").unlink()
    assert store.is_current(payload, store.current_key(tree))


def test_a_map_from_older_mapper_code_is_stale(tree):
    payload = store.build(tree)
    payload["cache_key"]["mapper"] = "0.0.0"
    assert not store.is_current(payload, store.current_key(tree))


def test_state_never_builds_in_the_calling_thread(tree, monkeypatch):
    store.build(tree)
    _write(tree, "backend/c.py", "")
    started = []
    monkeypatch.setattr(store, "build", lambda r: (_ for _ in ()).throw(AssertionError("built inline")))
    monkeypatch.setattr(store, "start", lambda r: started.append(r) or store.Job())
    payload, note = store.state(tree)
    assert payload is not None and note["stale"] is True
    assert started == [tree.resolve()]


def test_one_build_per_root_at_a_time(tree, monkeypatch):
    gate = threading.Event()
    calls = []

    def slow_build(r):
        calls.append(r)
        gate.wait(5)
        return {"file_count": 2}

    monkeypatch.setattr(store, "build", slow_build)
    first = store.start(tree)
    second = store.start(tree)
    assert first is second
    gate.set()
    first.done.wait(5)
    assert len(calls) == 1
    third = store.start(tree)
    assert third is not first
    third.done.wait(5)


def test_the_index_belongs_to_its_snapshot(tree):
    payload = store.build(tree)
    store._index_cache.clear()
    index = store.load_index(tree, payload)
    assert index is not None and "backend/a.py" in index["files"]
    stale = dict(payload, cache_key=dict(payload["cache_key"], listing="other"))
    assert store.load_index(tree, stale) is None


def test_writes_are_atomic_and_leave_no_temp_files(tree):
    store.build(tree)
    leftovers = [p for p in store.cache_dir().iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    assert store.snapshot_path(tree).is_file() and store.index_path(tree).is_file()


def test_each_file_is_parsed_once_per_change(tmp_path):
    facts.clear_cache()
    p = _write(tmp_path, "m.py", "def f():\n    return 1\n")
    first = facts.file_facts(p, "m.py")
    again = facts.file_facts(p, "m.py")
    assert first is again
    assert facts.cache_stats()["misses"] == 1 and facts.cache_stats()["hits"] == 1
    later = time.time() + 5
    p.write_text("def g():\n    return 2\n")
    os.utime(p, (later, later))
    changed = facts.file_facts(p, "m.py")
    assert [d["name"] for d in changed["defs"]] == ["g"]


def test_post_refresh_starts_a_build_and_returns_at_once(tree, monkeypatch):
    from flask import Flask

    import backend.config as cfg
    from backend.api.system_map_api import system_map_bp

    monkeypatch.setattr(cfg, "GUAARDVARK_ROOT", str(tree))
    started = []
    monkeypatch.setattr(store, "start", lambda r: started.append(r) or store.Job())
    app = Flask(__name__)
    app.register_blueprint(system_map_bp)
    response = app.test_client().post("/api/system-map/refresh", json={})
    assert response.status_code == 202
    assert response.get_json()["status"] == "computing"
    assert started == [tree.resolve()]


def test_get_before_the_first_map_says_computing(tree, monkeypatch):
    from flask import Flask

    import backend.config as cfg
    from backend.api.system_map_api import system_map_bp

    monkeypatch.setattr(cfg, "GUAARDVARK_ROOT", str(tree))
    monkeypatch.setattr(store, "start", lambda r: store.Job())
    app = Flask(__name__)
    app.register_blueprint(system_map_bp)
    response = app.test_client().get("/api/system-map/snapshot")
    assert response.status_code == 202
    assert response.get_json()["status"] == "computing"
