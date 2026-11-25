#!/usr/bin/env python3
"""Backend paths the global voice session uses.

The browser sends one POST /api/voice/speech-to-text per utterance, so every
outcome must hand its rate-limit slot back; the socket stream handlers answer
with the stream id they were started with and leave its room; both transcribe
with the model chosen in Settings → Voice unless the request names one;
faster-whisper picks its device against free VRAM instead of taking CUDA
unasked.
"""

import io
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask  # noqa: E402

import backend.api.voice_api as voice_api  # noqa: E402
import backend.socketio_events as sio  # noqa: E402
from backend.utils import faster_whisper_utils as fw  # noqa: E402

needs_faster_whisper = pytest.mark.skipif(
    not fw.FASTER_WHISPER_AVAILABLE, reason="faster-whisper is not installed"
)


def _client():
    app = Flask(__name__)
    app.register_blueprint(voice_api.voice_bp)
    app.config["TESTING"] = True
    return app.test_client()


def _post(client, filename="utterance.webm"):
    return client.post(
        "/api/voice/speech-to-text",
        data={"audio": (io.BytesIO(b"\x00" * 64), filename)},
        content_type="multipart/form-data",
    )


@pytest.fixture
def fresh_limiter(monkeypatch):
    limiter = voice_api.VoiceRateLimiter()
    monkeypatch.setattr(voice_api, "rate_limiter", limiter)
    monkeypatch.setattr(voice_api, "USE_WHISPER_SERVER", False)
    monkeypatch.setattr(
        voice_api.process_monitor, "get_system_status", lambda: {"system_overloaded": False}
    )
    return limiter


@pytest.fixture
def chosen_model(monkeypatch):
    """Save a speech model choice, with the given ids' weights installed."""
    voice_api._speech_model_fallback_warned.clear()

    def choose(model_id, installed=None):
        installed = {model_id} if installed is None else set(installed)
        monkeypatch.setattr(
            "backend.utils.settings_utils.get_setting",
            lambda key, default=None, cast=str: model_id if key == voice_api.SPEECH_MODEL_SETTING else default,
        )
        monkeypatch.setattr(fw, "is_model_installed", lambda m: m in installed)

    yield choose
    voice_api._speech_model_fallback_warned.clear()


@needs_faster_whisper
@pytest.mark.parametrize(
    "transcribe, status",
    [
        (lambda *_a, **_k: ("turn on the lights", 0.1), 200),
        (lambda *_a, **_k: ("", 0.1), 400),
        (MagicMock(side_effect=RuntimeError("decoder blew up")), 500),
    ],
    ids=["speech", "silence", "failure"],
)
def test_every_outcome_returns_the_rate_limit_slot(fresh_limiter, transcribe, status):
    import numpy as np

    with patch.object(fw, "decode_audio", return_value=np.zeros(16000)), \
         patch.object(fw, "transcribe_audio_faster", side_effect=transcribe):
        response = _post(_client())
    assert response.status_code == status
    assert fresh_limiter.active_requests == set()


def test_overloaded_answer_returns_the_slot(fresh_limiter, monkeypatch):
    monkeypatch.setattr(
        voice_api.process_monitor, "get_system_status", lambda: {"system_overloaded": True}
    )
    response = _post(_client())
    assert response.status_code == 503
    assert fresh_limiter.active_requests == set()


@needs_faster_whisper
def test_speech_reaches_the_client_and_ogg_is_accepted(fresh_limiter):
    import numpy as np

    with patch.object(fw, "decode_audio", return_value=np.zeros(32000)), \
         patch.object(fw, "transcribe_audio_faster", return_value=("hello there", 0.2)) as stt:
        response = _post(_client(), filename="utterance.ogg")
    assert response.status_code == 200
    body = response.get_json()
    assert body["text"] == "hello there"
    assert body["engine"] == "faster-whisper"
    stt.assert_called_once()


def _stt_model(fresh_limiter, seconds=2, **form):
    """The model size one /speech-to-text request transcribed with."""
    import numpy as np

    with patch.object(fw, "decode_audio", return_value=np.zeros(int(16000 * seconds))), \
         patch.object(fw, "transcribe_audio_faster", return_value=("hello", 0.1)) as stt:
        response = _client().post(
            "/api/voice/speech-to-text",
            data={"audio": (io.BytesIO(b"\x00" * 64), "utterance.webm"), **form},
            content_type="multipart/form-data",
        )
    assert response.status_code == 200
    assert response.get_json()["model_used"] == stt.call_args.kwargs["model_size"]
    return stt.call_args.kwargs["model_size"]


@needs_faster_whisper
def test_speech_to_text_uses_the_chosen_model_over_the_duration_pick(fresh_limiter, chosen_model):
    # Unpreferred, 20 s of audio would pick "tiny".
    chosen_model("base")
    assert _stt_model(fresh_limiter, seconds=20) == "base"


@needs_faster_whisper
def test_speech_to_text_reaches_turbo_by_its_id(fresh_limiter, chosen_model):
    chosen_model("large-v3-turbo")
    assert _stt_model(fresh_limiter) == "large-v3-turbo"


@needs_faster_whisper
def test_a_model_named_in_the_request_wins(fresh_limiter, chosen_model):
    chosen_model("base", installed={"base", "small"})
    assert _stt_model(fresh_limiter, model="small") == "small"


@needs_faster_whisper
def test_a_deleted_choice_falls_back_to_installed_tiny_en(fresh_limiter, chosen_model):
    chosen_model("small", installed={"tiny.en"})
    assert _stt_model(fresh_limiter) == "tiny.en"


@needs_faster_whisper
def test_a_deleted_choice_without_tiny_en_answers_409_naming_it(fresh_limiter, chosen_model):
    import numpy as np

    chosen_model("small", installed=set())
    with patch.object(fw, "decode_audio", return_value=np.zeros(16000)), \
         patch.object(fw, "transcribe_audio_faster", side_effect=fw.SpeechModelMissing("small")) as stt:
        response = _post(_client())
    assert stt.call_args.kwargs["model_size"] == "small"
    assert response.status_code == 409
    assert response.get_json()["model_id"] == "small"
    assert fresh_limiter.active_requests == set()


# --- socket stream handlers -------------------------------------------------


@pytest.fixture
def socket_calls(monkeypatch):
    calls = {"emit": [], "join": [], "leave": []}
    monkeypatch.setattr(sio, "emit", lambda event, payload=None, **kw: calls["emit"].append((event, payload, kw)))
    monkeypatch.setattr(sio, "join_room", lambda room: calls["join"].append(room))
    monkeypatch.setattr(sio, "leave_room", lambda room: calls["leave"].append(room))
    sio.voice_stream_buffers.clear()
    sio._voice_stream_meta.clear()
    return calls


@needs_faster_whisper
def test_stream_end_answers_with_its_own_id_and_leaves_the_room(socket_calls):
    import numpy as np

    sio.handle_voice_stream_start({"session_id": "floating_1_a"})
    sio.handle_voice_stream_chunk({"session_id": "floating_1_a", "audio": b"\x01" * 2000})
    # A second stream begins before the first has been answered.
    sio.handle_voice_stream_start({"session_id": "floating_1_b"})
    with patch.object(fw, "decode_audio", return_value=np.zeros(16000)), \
         patch.object(fw, "transcribe_audio_faster", return_value=("first words", 0.1)):
        sio.handle_voice_stream_end({"session_id": "floating_1_a"})

    transcripts = [(p, kw) for e, p, kw in socket_calls["emit"] if e == "voice:final_transcript"]
    assert transcripts == [
        ({"text": "first words", "session_id": "floating_1_a", "processing_time": 0.1},
         {"room": "voice_floating_1_a"})
    ]
    assert socket_calls["leave"] == ["voice_floating_1_a"]
    assert "floating_1_b" in sio.voice_stream_buffers


def test_empty_stream_end_still_leaves_the_room(socket_calls):
    sio.handle_voice_stream_start({"session_id": "s_empty"})
    sio.handle_voice_stream_end({"session_id": "s_empty"})
    assert socket_calls["leave"] == ["voice_s_empty"]
    assert socket_calls["emit"][-1][1] == {"text": "", "session_id": "s_empty"}


@needs_faster_whisper
def test_failed_stream_end_reports_the_stream_and_leaves_the_room(socket_calls):
    sio.handle_voice_stream_start({"session_id": "s_bad"})
    sio.handle_voice_stream_chunk({"session_id": "s_bad", "audio": b"\x01" * 100})
    with patch.object(fw, "decode_audio", side_effect=ValueError("not audio")):
        sio.handle_voice_stream_end({"session_id": "s_bad"})
    errors = [p for e, p, _kw in socket_calls["emit"] if e == "voice:error"]
    assert errors == [{"message": "not audio", "session_id": "s_bad"}]
    assert socket_calls["leave"] == ["voice_s_bad"]


@needs_faster_whisper
def test_stream_end_transcribes_with_the_chosen_model(socket_calls, chosen_model):
    import numpy as np

    chosen_model("base")
    sio.handle_voice_stream_start({"session_id": "s_model"})
    sio.handle_voice_stream_chunk({"session_id": "s_model", "audio": b"\x01" * 2000})
    with patch.object(fw, "decode_audio", return_value=np.zeros(16000)), \
         patch.object(fw, "transcribe_audio_faster", return_value=("hello", 0.1)) as stt:
        sio.handle_voice_stream_end({"session_id": "s_model"})
    assert stt.call_args.kwargs["model_size"] == "base"


# --- device choice ------------------------------------------------------------


@pytest.fixture
def no_override(monkeypatch):
    monkeypatch.delenv("GUAARDVARK_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("GUAARDVARK_MCP_PROCESS", raising=False)
    monkeypatch.delenv("GUAARDVARK_WHISPER_MIN_VRAM_MB", raising=False)


@pytest.mark.parametrize(
    "has_gpu, free_mb, expected",
    [(False, 0, "cpu"), (True, 800, "cpu"), (True, 12000, "cuda")],
    ids=["no-gpu", "busy-card", "free-card"],
)
def test_device_follows_free_vram(no_override, has_gpu, free_mb, expected):
    with patch("backend.services.gpu_resource_coordinator.has_gpu", return_value=has_gpu), \
         patch("backend.services.gpu_resource_coordinator.get_available_vram",
               return_value={"success": True, "available_mb": free_mb}):
        assert fw.pick_device() == expected


def test_device_override_and_mcp_process(no_override, monkeypatch):
    with patch("backend.services.gpu_resource_coordinator.has_gpu", return_value=True), \
         patch("backend.services.gpu_resource_coordinator.get_available_vram",
               return_value={"success": True, "available_mb": 12000}):
        monkeypatch.setenv("GUAARDVARK_MCP_PROCESS", "1")
        assert fw.pick_device() == "cpu"
        monkeypatch.setenv("GUAARDVARK_WHISPER_DEVICE", "cuda")
        assert fw.pick_device() == "cuda"


def test_model_load_uses_the_picked_device(no_override, monkeypatch):
    created = {}

    class FakeModel:
        def __init__(self, size, device, compute_type, local_files_only):
            created.update(size=size, device=device, local_files_only=local_files_only)

    monkeypatch.setattr(fw, "FASTER_WHISPER_AVAILABLE", True)
    monkeypatch.setattr(fw, "WhisperModel", FakeModel)
    monkeypatch.setattr(fw, "_whisper_model", None)
    monkeypatch.setattr(fw, "_current_model_size", None)
    monkeypatch.setattr(fw, "local_model_path", lambda size: "/cache/" + size)
    monkeypatch.setattr(fw, "pick_device", lambda: "cpu")
    fw.get_faster_whisper_model("tiny.en")
    assert created == {"size": "tiny.en", "device": "cpu", "local_files_only": True}
