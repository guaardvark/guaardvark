import threading
import types

import pytest
from tests.helpers import make_mock_llm

try:
    from flask import Flask

    from backend.api.model_api import Settings, model_bp
except Exception:
    pytest.skip("Flask or model_api unavailable", allow_module_level=True)


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.config.update({"TESTING": True})
    if model_bp.name not in app.blueprints:
        app.register_blueprint(model_bp)
    Settings.llm = make_mock_llm("original")
    app.config["LLAMA_INDEX_LLM"] = Settings.llm
    with app.app_context():
        yield app.test_client()


def test_set_model_out_of_memory(client, monkeypatch, caplog):
    from backend.api import model_api
    from backend.utils import ollama_resource_manager as orm

    monkeypatch.setattr(
        model_api, "get_available_ollama_models", lambda use_cache=True: [{"name": "big-model"}]
    )
    # The switch runs in a background thread and reports over SocketIO; the
    # thread is kept so the test can wait for it, and the machine probes it
    # makes on the way to building the client are stood in for.
    started = []

    class RecordedThread(threading.Thread):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            started.append(self)

    monkeypatch.setattr(model_api.threading, "Thread", RecordedThread)
    monkeypatch.setattr(orm, "compute_optimal_num_ctx", lambda name: 8192)
    monkeypatch.setattr(orm, "get_system_resources", lambda: {})
    monkeypatch.setattr(orm, "get_model_info", lambda name: None)
    monkeypatch.setattr(orm, "thinking_kwargs", lambda name: {})
    events = []
    monkeypatch.setattr(
        model_api.socketio, "emit", lambda event, payload=None, **_: events.append((event, payload))
    )

    class DummyOllama:
        def __init__(self, *_, **__):
            raise RuntimeError("cudaMalloc failed: out of memory")

    monkeypatch.setattr(model_api, "Ollama", DummyOllama)
    caplog.set_level("ERROR", logger="backend.api.model_api")
    resp = client.post("/api/model/set", json={"model": "big-model"})
    assert resp.status_code == 202
    assert resp.get_json()["status"] == "switching"

    assert len(started) == 1
    started[0].join(timeout=30)
    assert not started[0].is_alive()

    switch_events = [payload for event, payload in events if event == "model_switch"]
    payload = switch_events[-1]
    assert payload["status"] == "error"
    assert payload["model"] == "big-model"
    assert "cudaMalloc failed: out of memory" in payload["message"]
    assert "Background model switch failed" in caplog.text
    from backend.api.model_api import Settings as S

    assert S.llm is client.application.config["LLAMA_INDEX_LLM"]
    assert getattr(S.llm, "model", None) == "mock"
