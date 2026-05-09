import json
import os
from pathlib import Path

from scripts.dep_reconciler.state import (
    State,
    default_state_path,
    load_state,
    save_state,
)


def test_default_state_path_uses_hostname(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GUAARDVARK_DEP_STATE_FILE", raising=False)
    p = default_state_path()
    assert p.parent == Path("data/dep_reconciler").resolve()
    assert p.name.startswith("state-") and p.name.endswith(".json")


def test_default_state_path_respects_env_override(tmp_path, monkeypatch):
    target = tmp_path / "custom-state.json"
    monkeypatch.setenv("GUAARDVARK_DEP_STATE_FILE", str(target))
    assert default_state_path() == target


def test_load_state_returns_empty_when_missing(tmp_path):
    s = load_state(tmp_path / "nope.json")
    assert s.reconcilers == {}
    assert s.version == 1


def test_load_state_returns_empty_on_corrupt_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json}")
    s = load_state(p)
    assert s.reconcilers == {}


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    s = State(version=1, hostname="test-host", reconcilers={
        "backend_venv": {
            "manifest_hash": "sha256:abc",
            "extra": {"numpy_major": 2},
            "last_installed_at": "2026-05-08T00:00:00Z",
        }
    })
    save_state(p, s)
    loaded = load_state(p)
    assert loaded.reconcilers["backend_venv"]["manifest_hash"] == "sha256:abc"
    assert loaded.reconcilers["backend_venv"]["extra"]["numpy_major"] == 2


def test_save_is_atomic_via_temp_rename(tmp_path):
    p = tmp_path / "state.json"
    s = State(version=1, hostname="h", reconcilers={"x": {"manifest_hash": "sha256:1"}})
    save_state(p, s)
    # No leftover .tmp file
    assert not (tmp_path / "state.json.tmp").exists()
    # File is valid JSON
    json.loads(p.read_text())
