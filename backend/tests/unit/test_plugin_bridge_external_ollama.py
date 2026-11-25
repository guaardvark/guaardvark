"""The route orchestrator leaves an externally run service alone.

With a card's "I run it myself" switch on, the manager refuses to stop that
service. The orchestrator used to try again on every route change and log a
warning each time; now the first refusal drops its claim with one log line,
and an external plugin is never claimed or started by a route in the first
place. A GPU swap only ever stops ComfyUI (Ollama is in ROUTE_KEEPS_RUNNING),
so ComfyUI is the service refused here.
"""

import logging
from types import SimpleNamespace

import pytest

from backend.services import plugin_bridge as pb

REFUSAL = {"success": False, "error": "Not stopped: the ComfyUI card has the switch on", "external": True}


@pytest.fixture
def bridge(monkeypatch):
    calls = []

    def stop_plugin(plugin_id):
        calls.append(plugin_id)
        return dict(REFUSAL)

    monkeypatch.setattr(pb, "_plugin_manager", lambda: SimpleNamespace(stop_plugin=stop_plugin))
    monkeypatch.setattr(pb, "_is_running", lambda plugin_id: True)
    monkeypatch.setattr(pb, "_stop_blocked_reason", lambda plugin_id: None)
    monkeypatch.setattr(pb, "_orchestrator_claims", {"comfyui", "ollama"})
    monkeypatch.setattr(pb, "_user_controlled", set())
    monkeypatch.setattr(pb, "_external_logged", set())
    return calls


def test_first_refusal_drops_the_claim_with_one_line(bridge, caplog):
    caplog.set_level(logging.INFO, logger=pb.logger.name)

    first = pb._resolve_gpu_conflict({"ollama"})
    second = pb._resolve_gpu_conflict({"ollama"})

    assert bridge == ["comfyui"]
    assert first == [{"action": "gpu_swap_left_external", "plugin_id": "comfyui"}]
    assert second == []
    assert pb._orchestrator_claims == {"ollama"}
    external_lines = [r for r in caplog.records if "marked external" in r.getMessage()]
    assert len(external_lines) == 1
    assert external_lines[0].levelno == logging.INFO
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_second_claim_is_refused_quietly(bridge, caplog):
    caplog.set_level(logging.INFO, logger=pb.logger.name)
    pb._resolve_gpu_conflict({"ollama"})
    pb._orchestrator_claims.add("comfyui")

    pb._resolve_gpu_conflict({"ollama"})

    assert bridge == ["comfyui", "comfyui"]
    assert "comfyui" not in pb._orchestrator_claims
    assert len([r for r in caplog.records if "marked external" in r.getMessage()]) == 1


def test_a_gpu_swap_never_asks_ollama_to_stop(bridge):
    assert pb._resolve_gpu_conflict({"comfyui"}) == []
    assert bridge == []
    assert "ollama" in pb._orchestrator_claims


def test_is_external_follows_each_cards_switch(monkeypatch):
    from backend.plugins import plugin_manager as pm

    monkeypatch.setattr(pm, "ollama_is_external", lambda: True)
    monkeypatch.setattr(pm, "comfyui_is_external", lambda: False)
    assert pb._is_external("ollama") is True
    assert pb._is_external("comfyui") is False
    assert pb._is_external("audio_foundry") is False
    monkeypatch.setattr(pm, "ollama_is_external", lambda: False)
    monkeypatch.setattr(pm, "comfyui_is_external", lambda: True)
    assert pb._is_external("ollama") is False
    assert pb._is_external("comfyui") is True
