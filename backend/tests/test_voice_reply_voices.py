"""The reply voice chooser lists the engine that will speak, and a stock machine
is told plainly when nothing can.

/text-to-speech tries Audio Foundry (Kokoro) first and falls back to Piper.
/voices used to list Piper voices and report "piper-tts" even while Kokoro
spoke every reply with its default voice, so the chooser changed nothing; and
a Kokoro id reaching the Piper fallback was a 400. No GPU, network or Audio
Foundry process here: the plugin snapshot and the voice packs are faked.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask  # noqa: E402

import backend.api.voice_api as voice_api  # noqa: E402
from backend.services import audio_foundry_models as afm  # noqa: E402


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(voice_api.voice_bp)
    return app.test_client()


@pytest.fixture
def foundry_running(monkeypatch):
    monkeypatch.setattr(afm, "plugin_snapshot", lambda: {"running": True, "enabled": True, "status": "running"})


@pytest.fixture
def foundry_off(monkeypatch):
    monkeypatch.setattr(afm, "plugin_snapshot", lambda: {"running": False, "enabled": False, "status": "stopped"})


@pytest.fixture
def two_kokoro_packs(monkeypatch):
    """Only af_heart and am_michael are in the Hugging Face cache."""
    monkeypatch.setattr(
        afm, "is_hub_cached",
        lambda repo, probe: probe in ("voices/af_heart.pt", "voices/am_michael.pt"),
    )


def test_voices_come_from_kokoro_while_audio_foundry_speaks(client, foundry_running, two_kokoro_packs):
    body = client.get("/api/voice/voices").get_json()
    assert body["engine"] == "kokoro"
    assert [v["id"] for v in body["voices"]] == ["af_heart", "am_michael"]
    assert body["default_voice"] == "af_heart"
    assert body["voices"][0]["name"] == "Heart"
    assert all(v["engine"] == "kokoro" and v["available"] for v in body["voices"])
    assert body["fallback_engine"] == "piper-tts"


def test_voices_fall_back_to_piper_when_the_plugin_is_off(client, foundry_off, monkeypatch):
    monkeypatch.setattr(voice_api, "_kokoro_reply_voices", lambda: pytest.fail("Kokoro must not be consulted"))
    body = client.get("/api/voice/voices").get_json()
    assert body["engine"] == "piper-tts"
    assert set(v["id"] for v in body["voices"]) == set(voice_api.PIPER_VOICES)
    assert all(v["engine"] == "piper-tts" for v in body["voices"])


def test_voices_fall_back_to_piper_when_no_kokoro_pack_is_installed(client, foundry_running, monkeypatch):
    monkeypatch.setattr(afm, "is_hub_cached", lambda repo, probe: False)
    assert client.get("/api/voice/voices").get_json()["engine"] == "piper-tts"


def test_reply_engine_is_kokoro_only_while_the_plugin_runs_with_a_pack(foundry_running, two_kokoro_packs, monkeypatch):
    assert voice_api.reply_tts_engine() == "kokoro"
    monkeypatch.setattr(afm, "plugin_snapshot", lambda: {"running": False, "enabled": True, "status": "stopped"})
    assert voice_api.reply_tts_engine() == "piper-tts"


def test_a_kokoro_voice_id_is_spoken_by_piper_when_audio_foundry_is_down(client, monkeypatch, tmp_path):
    monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
    monkeypatch.setattr(voice_api, "_try_audio_foundry_voice", lambda *a, **k: None)
    monkeypatch.setattr(voice_api, "_kokoro_voice_for", lambda voice: voice)
    monkeypatch.setattr(voice_api, "_installed_piper_voice", lambda voice: "libritts")
    resp = client.post("/api/voice/text-to-speech", json={"text": "Hello there.", "voice": "am_michael"})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["voice"] == "libritts"
    assert body["engine"] == "piper-tts-stream"
    assert body["audio_url"].startswith("/api/voice/stream-tts/")


def test_no_voice_installed_is_one_plain_message(client, monkeypatch, tmp_path):
    monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
    monkeypatch.setattr(voice_api, "_try_audio_foundry_voice", lambda *a, **k: None)
    monkeypatch.setattr(voice_api, "_installed_piper_voice", lambda voice: None)
    resp = client.post("/api/voice/text-to-speech", json={"text": "Hello there.", "voice": "libritts"})
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["code"] == "no_voice_installed"
    assert "Voice Settings" in body["error"] and "Kokoro voice pack" in body["error"]
