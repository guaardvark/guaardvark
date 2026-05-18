"""Tests for PluginStateStore — the typed wrapper around plugin_state.json.

The store owns the file's schema, atomic writes, and read/modify/write
semantics. PluginManager talks to it; tests inject a store pointed at a
tmp_path and never touch the real state file."""

import json
from pathlib import Path

import pytest

from backend.plugins.plugin_state_store import PluginStateStore, SCHEMA_VERSION


def test_read_missing_file_returns_empty_normalized(tmp_path):
    store = PluginStateStore(tmp_path / "plugin_state.json")
    snap = store.snapshot()
    assert snap == {"version": SCHEMA_VERSION, "user_enabled": {}, "running": []}


def test_set_user_enabled_persists_and_creates_file(tmp_path):
    path = tmp_path / "plugin_state.json"
    store = PluginStateStore(path)
    store.set_user_enabled("comfyui", True)

    raw = json.loads(path.read_text())
    assert raw["user_enabled"] == {"comfyui": True}
    assert raw["version"] == SCHEMA_VERSION
    assert "updated_at" in raw


def test_set_user_enabled_preserves_other_entries(tmp_path):
    store = PluginStateStore(tmp_path / "plugin_state.json")
    store.set_user_enabled("comfyui", True)
    store.set_user_enabled("ollama", False)
    store.set_user_enabled("audio_foundry", False)

    prefs = store.get_user_enabled()
    assert prefs == {"comfyui": True, "ollama": False, "audio_foundry": False}


def test_set_running_preserves_user_enabled(tmp_path):
    store = PluginStateStore(tmp_path / "plugin_state.json")
    store.set_user_enabled("comfyui", True)
    store.set_user_enabled("ollama", False)

    store.set_running(["comfyui"])

    assert store.get_user_enabled() == {"comfyui": True, "ollama": False}
    assert store.get_running() == ["comfyui"]


def test_set_user_enabled_preserves_running(tmp_path):
    store = PluginStateStore(tmp_path / "plugin_state.json")
    store.set_running(["comfyui", "ollama"])
    store.set_user_enabled("comfyui", False)

    assert store.get_running() == ["comfyui", "ollama"]
    assert store.get_user_enabled() == {"comfyui": False}


def test_legacy_running_only_file_upgrades_in_place(tmp_path):
    path = tmp_path / "plugin_state.json"
    path.write_text(json.dumps({"running": ["ollama"]}))

    store = PluginStateStore(path)
    snap = store.snapshot()

    assert snap["version"] == SCHEMA_VERSION
    assert snap["user_enabled"] == {}
    assert snap["running"] == ["ollama"]


def test_corrupt_file_returns_fresh_state(tmp_path):
    path = tmp_path / "plugin_state.json"
    path.write_text("{not valid json")

    store = PluginStateStore(path)
    snap = store.snapshot()

    assert snap == {"version": SCHEMA_VERSION, "user_enabled": {}, "running": []}


def test_atomic_write_uses_temp_then_rename(tmp_path):
    """Verify the write path leaves no partial file on disk after success."""
    path = tmp_path / "plugin_state.json"
    store = PluginStateStore(path)
    store.set_user_enabled("comfyui", True)

    # Final file exists, .tmp does not (rename completed).
    assert path.exists()
    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_two_stores_pointing_at_same_path_see_each_others_writes(tmp_path):
    """The store doesn't cache state — each call hits disk. Important for
    tests that simulate multi-process scenarios and for the migration script
    running while the backend is down."""
    path = tmp_path / "plugin_state.json"
    a = PluginStateStore(path)
    b = PluginStateStore(path)

    a.set_user_enabled("comfyui", True)
    assert b.get_user_enabled() == {"comfyui": True}

    b.set_user_enabled("ollama", False)
    assert a.get_user_enabled() == {"comfyui": True, "ollama": False}


def test_get_user_enabled_returns_a_copy(tmp_path):
    """Mutating the returned dict must not affect the store."""
    store = PluginStateStore(tmp_path / "plugin_state.json")
    store.set_user_enabled("comfyui", True)

    prefs = store.get_user_enabled()
    prefs["mutated"] = True

    assert "mutated" not in store.get_user_enabled()


def test_creates_parent_directory_on_write(tmp_path):
    """If data/ doesn't exist yet, the store should create it."""
    path = tmp_path / "data" / "plugin_state.json"
    assert not path.parent.exists()

    store = PluginStateStore(path)
    store.set_user_enabled("comfyui", True)

    assert path.exists()
