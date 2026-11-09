"""A page change stops only the plugins a page started, and never Ollama.

Opening Chat used to claim an Ollama that start.sh had started, and the next
page that does not list Ollama stopped it with `systemctl stop ollama`, which
left the Discord bot, CLI and MCP without chat. A plugin already running when
a page asks for it is now left alone.
"""

from types import SimpleNamespace

import pytest

from backend.services import plugin_bridge as pb


@pytest.fixture
def bridge(monkeypatch):
    running, stops, starts = set(), [], []

    def stop_plugin(plugin_id, **_):
        stops.append(plugin_id)
        running.discard(plugin_id)
        return {"success": True}

    def start(plugin_id, **_):
        starts.append(plugin_id)
        running.add(plugin_id)
        return True, "started"

    monkeypatch.setattr(pb, "_plugin_manager", lambda: SimpleNamespace(stop_plugin=stop_plugin))
    monkeypatch.setattr(pb, "_is_running", lambda plugin_id: plugin_id in running)
    monkeypatch.setattr(pb, "_is_external", lambda plugin_id: False)
    monkeypatch.setattr(pb, "_stop_blocked_reason", lambda plugin_id: None)
    monkeypatch.setattr(pb, "_try_start_plugin", start)
    monkeypatch.setattr(pb, "_emit_plugins_status", lambda *a, **k: None)
    monkeypatch.setattr(pb, "ensure_plugins_for_stage", lambda *a, **k: None)
    monkeypatch.setattr(pb, "_orchestrator_claims", set())
    monkeypatch.setattr(pb, "_user_controlled", set())
    monkeypatch.setattr(pb, "_last_route", None)
    return SimpleNamespace(running=running, stops=stops, starts=starts)


@pytest.mark.parametrize("next_page", ["/", "/training", "/video", "/settings"])
def test_a_running_ollama_survives_leaving_chat(bridge, next_page):
    bridge.running.add("ollama")

    pb.prepare_plugins_for_route("/chat")
    pb.prepare_plugins_for_route(next_page)

    assert "ollama" not in bridge.stops
    assert "ollama" in bridge.running
    assert "ollama" not in pb._orchestrator_claims


def test_a_plugin_a_page_started_is_stopped_when_the_page_is_left(bridge):
    pb.prepare_plugins_for_route("/video")
    pb.prepare_plugins_for_route("/chat")

    assert "comfyui" in bridge.starts
    assert bridge.stops == ["comfyui"]


def test_comfyui_a_job_started_survives_leaving_video(bridge):
    bridge.running.update({"ollama", "comfyui"})

    pb.prepare_plugins_for_route("/video")
    pb.prepare_plugins_for_route("/chat")

    assert bridge.stops == []


@pytest.mark.parametrize("next_page", ["/", "/video"])
def test_an_ollama_chat_started_survives_leaving_chat(bridge, next_page):
    pb.prepare_plugins_for_route("/chat")
    pb.prepare_plugins_for_route(next_page)

    assert "ollama" in bridge.starts
    assert "ollama" not in bridge.stops
    assert "ollama" in bridge.running
