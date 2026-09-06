"""Unit tests for plugin auto-orchestration bridge."""
import pytest

import backend.services.plugin_bridge as pb
from backend.plugins.plugin_base import PluginStatus


class _FakeStateStore:
    def __init__(self):
        self.prefs = {}

    def get_user_enabled(self):
        return dict(self.prefs)


class _FakeConfig:
    def __init__(self):
        self.enabled = False


class _FakeMeta:
    def __init__(self):
        self.config = _FakeConfig()


class _FakeRegistry:
    def __init__(self):
        self._plugins = {}

    def get_plugin(self, plugin_id):
        if plugin_id not in self._plugins:
            self._plugins[plugin_id] = _FakeMeta()
        return self._plugins[plugin_id]


class _FakePM:
    def __init__(self):
        self.enabled = set()
        self.status = {}
        self.start_calls = []
        self.stop_calls = []
        self.state_store = _FakeStateStore()
        self.registry = _FakeRegistry()

    def is_effectively_enabled(self, plugin_id):
        return plugin_id in self.enabled

    def enable_plugin(self, plugin_id):
        self.enabled.add(plugin_id)
        return {"success": True}

    def get_status(self, plugin_id):
        return self.status.get(plugin_id, PluginStatus.STOPPED)

    def start_plugin(self, plugin_id):
        self.start_calls.append(plugin_id)
        # Only the cooldown retry test expects the first comfyui start to gate.
        # Skip that behavior when the plugin was user-disabled (job_critical path).
        if (
            plugin_id == "comfyui"
            and len([c for c in self.start_calls if c == "comfyui"]) == 1
            and self.state_store.prefs.get("comfyui") is not False
        ):
            return {
                "success": False,
                "gated": True,
                "cooldown_remaining": 0.01,
                "error": "Plugin system cooling down",
            }
        self.status[plugin_id] = PluginStatus.RUNNING
        return {"success": True, "message": "started"}

    def stop_plugin(self, plugin_id):
        self.stop_calls.append(plugin_id)
        self.status[plugin_id] = PluginStatus.STOPPED
        return {"success": True, "message": "stopped"}


@pytest.fixture(autouse=True)
def reset_bridge_state(monkeypatch):
    pb._last_route = None
    pb._orchestrator_claims.clear()
    pb._user_controlled.clear()
    monkeypatch.setattr(pb, "auto_orchestrator_enabled", lambda: True)
    monkeypatch.setattr(pb, "_emit_plugins_status", lambda *a, **k: None)
    monkeypatch.setattr(pb, "_stop_blocked_reason", lambda _pid: None)
    fake = _FakePM()
    monkeypatch.setattr(pb, "_plugin_manager", lambda: fake)
    yield fake


def test_plugins_for_route_normalizes_ids(reset_bridge_state):
    assert pb.plugins_for_route("/projects/abc123") == []
    assert pb.plugins_for_route("/video") == ["comfyui"]
    assert pb.plugins_for_route("/music-video") == ["comfyui", "video_editor", "ollama"]


def test_prepare_starts_needed_plugins(reset_bridge_state):
    fake = reset_bridge_state
    fake.enabled.update(["comfyui", "video_editor", "ollama"])

    result = pb.prepare_plugins_for_route("/music-video")

    assert "comfyui" in fake.start_calls
    assert "video_editor" in fake.start_calls
    assert "ollama" in fake.start_calls
    assert set(result["orchestrator_claims"]) == {"comfyui", "video_editor", "ollama"}


def test_prepare_stops_orchestrator_claims_on_route_change(reset_bridge_state):
    fake = reset_bridge_state
    fake.enabled.update(["comfyui", "ollama"])
    fake.status["comfyui"] = PluginStatus.RUNNING
    pb._orchestrator_claims.add("comfyui")
    pb._last_route = "/video"

    pb.prepare_plugins_for_route("/chat")

    assert "comfyui" in fake.stop_calls
    assert "ollama" in fake.start_calls


def test_user_controlled_plugin_not_auto_stopped(reset_bridge_state):
    fake = reset_bridge_state
    fake.enabled.add("comfyui")
    fake.status["comfyui"] = PluginStatus.RUNNING
    pb._orchestrator_claims.add("comfyui")
    pb._user_controlled.add("comfyui")
    pb._last_route = "/video"

    pb.prepare_plugins_for_route("/chat")

    assert "comfyui" not in fake.stop_calls


def test_start_retries_on_gate_cooldown(reset_bridge_state, monkeypatch):
    fake = reset_bridge_state
    fake.enabled.add("comfyui")
    sleeps = []
    monkeypatch.setattr(pb.time, "sleep", lambda s: sleeps.append(s))

    pb.ensure_plugin_running("comfyui")

    assert fake.start_calls.count("comfyui") == 2
    assert fake.status["comfyui"] == PluginStatus.RUNNING
    assert sleeps


def test_disabled_flag_skips_prepare(reset_bridge_state, monkeypatch):
    monkeypatch.setattr(pb, "auto_orchestrator_enabled", lambda: False)
    result = pb.prepare_plugins_for_route("/video")
    assert result.get("skipped") is True
    assert reset_bridge_state.start_calls == []


def test_cast_stage_plugin_map():
    assert pb.plugins_for_stage("cast", "planning") == ["ollama"]
    assert pb.plugins_for_stage("cast", "generate_samples") == ["comfyui"]
    assert pb.plugins_for_stage("cast", "regen_sample") == ["comfyui"]
    assert pb.plugins_for_stage("cast", "train") == []
    assert pb.plugins_for_route("/cast") == []
    assert pb.plugins_for_route("/cast/22") == []


def test_music_video_nav_does_not_start_user_disabled_video_editor(reset_bridge_state, monkeypatch):
    fake = reset_bridge_state
    fake.enabled.update(["comfyui", "ollama"])
    fake.state_store.prefs["video_editor"] = False
    monkeypatch.setattr(pb, "ensure_plugins_for_stage", lambda *a, **k: None)

    result = pb.prepare_plugins_for_route("/music-video")

    assert "video_editor" not in fake.start_calls
    assert fake.state_store.prefs["video_editor"] is False
    actions = {a["plugin_id"]: a["action"] for a in result.get("actions", [])}
    assert actions.get("video_editor") == "user_disabled"


def test_user_disabled_blocks_non_critical_start(reset_bridge_state):
    fake = reset_bridge_state
    fake.state_store.prefs["comfyui"] = False

    with pytest.raises(pb.PluginUnavailable, match="user-disabled"):
        pb.ensure_plugin_running("comfyui", persist_user_pref=False)

    assert "comfyui" not in fake.start_calls
    assert fake.state_store.prefs["comfyui"] is False


def test_job_critical_bypasses_user_disable(reset_bridge_state, monkeypatch):
    fake = reset_bridge_state
    fake.state_store.prefs["comfyui"] = False
    monkeypatch.setattr(pb, "_emit_plugins_status", lambda *a, **k: None)

    pb.ensure_plugins_for_stage("cast", "generate_samples", job_critical=True)

    assert "comfyui" in fake.start_calls
    assert fake.status["comfyui"] == PluginStatus.RUNNING
    # Must not stick a user preference re-enable
    assert fake.state_store.prefs["comfyui"] is False
    assert "comfyui" not in fake.enabled