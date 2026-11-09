"""Audio Studio's Start button goes through the route path.

A plugin the button starts is claimed like one the page started, so leaving
Audio Studio stops it; one already running stays up. The click lifts the
person's earlier disable. Leaving never stops Audio Foundry while its own job
list shows a queued or running job. With the auto-orchestrator off the button
is a plain start that stays up.
"""

import sys
from types import SimpleNamespace

import pytest
import requests

from backend.services import plugin_bridge as pb

AF = "audio_foundry"


@pytest.fixture
def world(monkeypatch):
    state = SimpleNamespace(running=set(), prefs={}, jobs=[], calls=[], start_error=None)
    meta = SimpleNamespace(config=SimpleNamespace(service_url="http://127.0.0.1:8206", enabled=False))

    def start_plugin(plugin_id):
        if state.start_error:
            state.calls.append(f"start-failed {plugin_id}")
            return {"success": False, "error": state.start_error}
        state.calls.append(f"start {plugin_id}")
        state.running.add(plugin_id)
        return {"success": True}

    def stop_plugin(plugin_id, **_):
        state.calls.append(f"stop {plugin_id}")
        state.running.discard(plugin_id)
        return {"success": True}

    def enable_plugin(plugin_id):
        state.calls.append(f"enable {plugin_id}")
        state.prefs[plugin_id] = True
        return {"success": True}

    pm = SimpleNamespace(
        state_store=SimpleNamespace(get_user_enabled=lambda: dict(state.prefs)),
        registry=SimpleNamespace(get_plugin=lambda pid: meta),
        _service_url=lambda m: m.config.service_url,
        is_effectively_enabled=lambda pid: bool(state.prefs.get(pid, False)),
        enable_plugin=enable_plugin,
        start_plugin=start_plugin,
        stop_plugin=stop_plugin,
    )

    def get(url, *_, **__):
        assert url.endswith("/jobs"), url
        if AF not in state.running:
            raise requests.ConnectionError("refused")
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"jobs": list(state.jobs)})

    monkeypatch.setenv("GUAARDVARK_PLUGIN_AUTO_ORCHESTRATOR", "1")
    monkeypatch.setattr(pb, "_plugin_manager", lambda: pm)
    monkeypatch.setattr(pb, "_is_running", lambda pid: pid in state.running)
    monkeypatch.setattr(pb, "_is_external", lambda pid: False)
    monkeypatch.setattr(pb, "_emit_plugins_status", lambda *a, **k: None)
    monkeypatch.setattr(pb, "ensure_plugins_for_stage", lambda *a, **k: None)
    monkeypatch.setattr(pb, "_orchestrator_claims", set())
    monkeypatch.setattr(pb, "_user_controlled", set())
    monkeypatch.setattr(pb, "_last_route", None)
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setitem(
        sys.modules,
        "backend.services.job_operation_gate",
        SimpleNamespace(get_gate=lambda: SimpleNamespace(snapshot=lambda: {"gpu_holder": None})),
    )
    return state


def test_a_plugin_the_button_started_stops_when_audio_studio_is_left(world):
    world.prefs[AF] = True

    result = pb.start_route_plugins("/audio")
    pb.prepare_plugins_for_route("/")

    assert result["success"] is True
    assert result["started"] == [{"plugin_id": AF, "running": True, "outcome": "start", "error": None}]
    assert world.calls == [f"start {AF}", f"stop {AF}"]


def test_a_plugin_already_running_survives_the_visit_and_the_button(world):
    world.prefs[AF] = True
    world.running.add(AF)

    pb.prepare_plugins_for_route("/audio")
    pb.start_route_plugins("/audio")
    pb.prepare_plugins_for_route("/")

    assert world.calls == []
    assert AF in world.running


@pytest.mark.parametrize("status", ["queued", "running"])
def test_leaving_keeps_audio_foundry_while_it_has_a_job(world, status):
    world.prefs[AF] = True
    pb.start_route_plugins("/audio")
    world.jobs = [{"id": "j1", "status": status}]

    result = pb.prepare_plugins_for_route("/")

    assert AF in world.running
    assert {"action": "stop_blocked", "plugin_id": AF,
            "error": "1 audio job(s) queued or running in Audio Foundry"} in result["actions"]

    world.jobs = [{"id": "j1", "status": "done"}]
    pb.prepare_plugins_for_route("/settings")
    assert AF not in world.running


def test_the_button_lifts_a_disable_the_visit_respects(world):
    world.prefs[AF] = False

    visit = pb.prepare_plugins_for_route("/audio")
    assert [a["action"] for a in visit["actions"] if a["plugin_id"] == AF] == ["user_disabled"]

    result = pb.start_route_plugins("/audio")

    assert result["success"] is True
    assert world.prefs[AF] is True
    assert world.calls == [f"enable {AF}", f"start {AF}"]


def test_a_refused_start_returns_the_reason(world):
    world.prefs[AF] = True
    world.start_error = "Plugin started but health check failed (timeout)"

    result = pb.start_route_plugins("/audio")

    assert result["success"] is False
    assert result["error"] == f"could not start '{AF}': Plugin started but health check failed (timeout)"


def test_with_the_orchestrator_off_the_button_is_a_plain_start(world, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_PLUGIN_AUTO_ORCHESTRATOR", "0")
    world.prefs[AF] = False

    result = pb.start_route_plugins("/audio")
    pb.prepare_plugins_for_route("/")

    assert result["success"] is True
    assert world.calls == [f"enable {AF}", f"start {AF}"]
    assert AF in world.running
    assert AF in pb._user_controlled
