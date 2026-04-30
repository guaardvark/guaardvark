"""Tests that enforce: plugin.json is a static manifest. Nothing in the
backend code path is permitted to mutate it at runtime. Drift between
client and master machines was caused by code that did mutate it."""

import hashlib
import json
from pathlib import Path

import pytest

from backend.plugins.plugin_base import PluginBase, PluginMetadata, PluginStatus


class _StubPlugin(PluginBase):
    """Concrete subclass for testing — abstract methods stubbed out."""
    def start(self) -> bool: return True
    def stop(self) -> bool: return True
    def health_check(self) -> dict: return {"status": "ok"}


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(plugin_dir: Path, plugin_id: str, enabled: bool) -> Path:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": plugin_id,
        "name": plugin_id.title(),
        "version": "1.0.0",
        "type": "service",
        "config": {"enabled": enabled, "auto_start": False},
    }
    json_path = plugin_dir / "plugin.json"
    json_path.write_text(json.dumps(manifest, indent=2))
    return json_path


def test_plugin_base_enable_does_not_mutate_plugin_json(tmp_path):
    plugin_dir = tmp_path / "plugins" / "test_plugin"
    json_path = _write_manifest(plugin_dir, "test_plugin", enabled=False)
    before = _hash(json_path)

    plugin = _StubPlugin(plugin_dir)
    assert plugin.metadata.config.enabled is False

    plugin.enable()

    after = _hash(json_path)
    assert before == after, (
        f"plugin.json was rewritten by PluginBase.enable() — "
        f"this is the drift bug we're fixing"
    )
    # In-memory state still flips so existing callers see the change.
    assert plugin.metadata.config.enabled is True


def test_plugin_base_disable_does_not_mutate_plugin_json(tmp_path):
    plugin_dir = tmp_path / "plugins" / "test_plugin"
    json_path = _write_manifest(plugin_dir, "test_plugin", enabled=True)
    before = _hash(json_path)

    plugin = _StubPlugin(plugin_dir)
    assert plugin.metadata.config.enabled is True

    plugin.disable()

    after = _hash(json_path)
    assert before == after
    assert plugin.metadata.config.enabled is False
