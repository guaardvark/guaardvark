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


from backend.plugins.plugin_registry import PluginRegistry


def test_registry_update_config_does_not_mutate_plugin_json_for_enabled(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "demo"
    json_path = _write_manifest(plugin_dir, "demo", enabled=False)
    before = _hash(json_path)

    registry = PluginRegistry(plugins_dir=plugins_dir)
    assert registry.is_registered("demo")

    # Caller tries to flip 'enabled' via the registry — must be refused
    # (or routed through user_enabled overlay), never written to disk.
    result = registry.update_plugin_config("demo", {"enabled": True})
    assert result is False, (
        "registry.update_plugin_config must refuse runtime-state keys "
        "(enabled, auto_start) — they belong in plugin_state.json"
    )

    after = _hash(json_path)
    assert before == after


def test_registry_update_config_allows_non_runtime_fields(tmp_path):
    """Static manifest fields like timeout are still editable via the registry.
    They are not per-machine state, so changing them in plugin.json is fine."""
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "demo"
    _write_manifest(plugin_dir, "demo", enabled=False)

    registry = PluginRegistry(plugins_dir=plugins_dir)
    result = registry.update_plugin_config("demo", {"timeout": 90})
    assert result is True

    json_path = plugin_dir / "plugin.json"
    data = json.loads(json_path.read_text())
    assert data["config"]["timeout"] == 90


from backend.plugins.plugin_base import PluginConfig


def test_plugin_config_accepts_default_enabled_field():
    cfg = PluginConfig.from_dict({"default_enabled": True, "timeout": 30})
    assert cfg.enabled is True
    assert cfg.timeout == 30


def test_plugin_config_default_enabled_takes_precedence_over_legacy_enabled():
    # If a manifest still has both fields (mid-migration), the explicit
    # default_enabled wins.
    cfg = PluginConfig.from_dict({"default_enabled": False, "enabled": True})
    assert cfg.enabled is False


def test_plugin_config_falls_back_to_legacy_enabled_when_default_enabled_absent():
    cfg = PluginConfig.from_dict({"enabled": True})
    assert cfg.enabled is True


def test_plugin_config_to_dict_emits_default_enabled_not_enabled():
    cfg = PluginConfig(enabled=True, auto_start=False, timeout=30)
    out = cfg.to_dict()
    assert "default_enabled" in out
    assert out["default_enabled"] is True
    # Old keys not emitted any more
    assert "enabled" not in out
    assert "auto_start" not in out
    assert "default_auto_start" in out
