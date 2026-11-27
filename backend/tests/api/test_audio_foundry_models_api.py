"""GET/POST /api/audio-foundry/models — catalog and explicit Install."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

try:
    from flask import Flask
    from backend.api.audio_foundry_api import audio_foundry_bp
    from backend.services import audio_foundry_models as afm
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


def _minimax_stub(*, installed=False):
    return {
        "id": afm.MINIMAX_MUSIC3_ID,
        "name": "MiniMax Music 3 (Int8)",
        "description": "songs",
        "group": "music",
        "hf_repo": "Comfy-Org/MiniMax-Music-3",
        "probe_file": None,
        "size_gb": 11.09,
        "gated": False,
        "terms_url": None,
        "installed": installed,
        "delegate": "video",
        "missing_files": [] if installed else ["dit"],
    }


@pytest.fixture
def client(monkeypatch):
    afm.reset_download_state()
    monkeypatch.setattr(afm, "is_hub_cached", lambda repo, probe: False)
    # No plugin venv to inspect, whatever the machine running the tests has.
    monkeypatch.setattr(afm, "venv_has_package", lambda name: None)
    monkeypatch.setattr(
        afm,
        "plugin_snapshot",
        lambda: {"running": False, "enabled": False, "status": "stopped"},
    )
    monkeypatch.setattr(afm, "_minimax_row", lambda: _minimax_stub())
    monkeypatch.setattr(afm, "hf_token_present", lambda: False)
    app = Flask(__name__)
    app.config.update({"TESTING": True})
    app.register_blueprint(audio_foundry_bp)
    return app.test_client()


def test_list_models_shape(client):
    payload = client.get("/api/audio-foundry/models").get_json()
    assert payload["success"] is True
    assert payload["plugin"]["running"] is False
    ids = [m["id"] for m in payload["models"]]
    assert ids == [
        "chatterbox",
        "kokoro",
        "kokoro-japanese",
        "kokoro-mandarin",
        "ace-step",
        "ace-step-1.5",
        "stable-audio-open",
        "minimax-music3-int8",
    ]
    sao = next(m for m in payload["models"] if m["id"] == "stable-audio-open")
    assert sao["gated"] is True and sao["installed"] is False
    assert "huggingface.co" in (sao["terms_url"] or "")


def test_download_requires_id(client):
    res = client.post("/api/audio-foundry/models/download", json={})
    assert res.status_code == 400
    assert res.get_json()["success"] is False


def test_download_unknown_id(client):
    res = client.post("/api/audio-foundry/models/download", json={"id": "not-a-model"})
    assert res.status_code == 400
    assert "unknown" in res.get_json()["error"]


def test_download_409_while_busy(client):
    with afm._download_lock:
        afm._download_state.update({
            "is_downloading": True,
            "current_id": "ace-step",
            "updated_at": 9e12,
        })
    res = client.post("/api/audio-foundry/models/download", json={"id": "kokoro"})
    assert res.status_code == 409
    assert "already downloading" in res.get_json()["error"]


def test_gated_sao_without_token(client):
    res = client.post("/api/audio-foundry/models/download", json={"id": "stable-audio-open"})
    assert res.status_code == 400
    err = res.get_json()["error"]
    assert "gated" in err.lower()
    assert "HF_TOKEN" in err
    assert "stable-audio-open-1.0" in err


def test_gated_sao_with_token_starts(client, monkeypatch):
    monkeypatch.setattr(afm, "hf_token_present", lambda: True)
    started = {}

    def fake_thread(*args, **kwargs):
        started["ok"] = True
        return SimpleNamespace(start=lambda: None)

    monkeypatch.setattr(afm.threading, "Thread", fake_thread)
    res = client.post("/api/audio-foundry/models/download", json={"id": "stable-audio-open"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True and body["id"] == "stable-audio-open"
    assert started.get("ok") is True


def test_already_installed(client, monkeypatch):
    monkeypatch.setattr(afm, "is_hub_cached", lambda repo, probe: True)
    res = client.post("/api/audio-foundry/models/download", json={"id": "kokoro"})
    assert res.status_code == 200
    assert res.get_json()["already_installed"] is True


def test_minimax_delegates_to_video(client, monkeypatch):
    called = {}

    def fake_start(model_id):
        called["id"] = model_id
        resp = SimpleNamespace(get_json=lambda: {
            "success": True,
            "message": "Started downloading minimax-music3-int8",
            "data": {"message": "Started downloading minimax-music3-int8"},
        })
        return resp, 200

    monkeypatch.setattr(
        "backend.api.batch_video_generation_api.start_video_model_download",
        fake_start,
    )
    res = client.post("/api/audio-foundry/models/download", json={"id": "minimax-music3-int8"})
    assert res.status_code == 200
    assert called["id"] == "minimax-music3-int8"
    assert res.get_json()["status"] == "started"


def test_minimax_delegate_409(client, monkeypatch):
    def fake_start(_model_id):
        resp = SimpleNamespace(get_json=lambda: {
            "success": False,
            "error": {"message": "Already downloading: wan22"},
        })
        return resp, 409

    monkeypatch.setattr(
        "backend.api.batch_video_generation_api.start_video_model_download",
        fake_start,
    )
    res = client.post("/api/audio-foundry/models/download", json={"id": "minimax-music3-int8"})
    assert res.status_code == 409
    assert "Already downloading" in res.get_json()["error"]


def test_download_status_idle(client):
    payload = client.get("/api/audio-foundry/models/download-status").get_json()
    assert payload["success"] is True
    assert payload["is_downloading"] is False
    assert payload["status"] == "idle"


def test_delegated_status_says_it_is_the_video_downloaders_run(client, monkeypatch):
    # The frontend reads both routes; the flag lets it count the run once.
    from backend.api import batch_video_generation_api as video_api

    def fake_start(model_id):
        return SimpleNamespace(get_json=lambda: {"success": True, "data": {}}), 200

    monkeypatch.setattr(video_api, "start_video_model_download", fake_start)
    monkeypatch.setattr(video_api, "_video_model_download_status", {
        "is_downloading": True,
        "current_model": "minimax-music3-dit",
        "progress": 12,
        "status": "downloading",
        "downloaded_gb": 1.3,
        "total_gb": 11.09,
    })
    assert client.post("/api/audio-foundry/models/download", json={"id": "minimax-music3-int8"}).status_code == 200

    payload = client.get("/api/audio-foundry/models/download-status").get_json()
    assert payload["is_downloading"] is True
    assert payload["progress"] == 12
    assert payload["delegated_to"] == "video"

