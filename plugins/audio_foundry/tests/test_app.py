"""Skeleton-phase smoke tests — verify the wiring even before backends exist.

All three /generate/* endpoints must return HTTP 501 at this phase, not 500
or 404, because that's how the FastAPI client knows the service is healthy
but the feature isn't wired yet. When each backend lands, its test flips
from expecting 501 to expecting a real file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# The plugin service imports are not on sys.path when pytest runs from project
# root; the start.sh script sets PYTHONPATH but tests don't go through that.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from service.app import app  # noqa: E402

client = TestClient(app)


def test_health_returns_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["service"] == "audio_foundry"


def test_status_reports_three_unwired_backends():
    r = client.get("/status")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "audio_foundry"
    assert set(body["backends"].keys()) == {"fx", "voice", "music"}
    for intent, state in body["backends"].items():
        assert state["backend"] is None, f"{intent} should be unwired at skeleton phase"
        assert state["loaded"] is False


def test_config_endpoint_returns_manifest_and_runtime():
    r = client.get("/config")
    assert r.status_code == 200
    body = r.json()
    assert "manifest" in body
    assert "runtime" in body
    assert body["manifest"]["id"] == "audio_foundry"


@pytest.mark.parametrize(
    "route,payload",
    [
        ("/generate/fx", {"prompt": "rain", "duration_s": 5.0}),
        ("/generate/voice", {"text": "Hello world."}),
        ("/generate/music", {"style_prompt": "lofi", "duration_s": 30.0}),
    ],
)
def test_generate_endpoints_return_501_at_skeleton_phase(route, payload):
    r = client.post(route, json=payload)
    assert r.status_code == 501, f"{route} should return 501 until backend is wired"


def test_fx_duration_over_cap_is_rejected():
    r = client.post("/generate/fx", json={"prompt": "x", "duration_s": 100.0})
    assert r.status_code == 422  # pydantic validation kicks in before dispatcher


def test_voice_invalid_backend_is_rejected():
    r = client.post(
        "/generate/voice",
        json={"text": "hi", "backend": "not-a-backend"},
    )
    assert r.status_code == 422
