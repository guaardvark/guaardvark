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
    monkeypatch.setattr(
        "backend.api.model_api.get_available_ollama_models",
        lambda: [{"name": "big-model"}],
    )

    class DummyOllama:
        def __init__(self, *_, **__):
            raise RuntimeError("cudaMalloc failed: out of memory")

    monkeypatch.setattr("backend.api.model_api.Ollama", DummyOllama)
    caplog.set_level("ERROR", logger="backend.api.model_api")
    resp = client.post("/api/model/set", json={"model": "big-model"})
    assert resp.status_code == 500
    data = resp.get_json()
    assert "Runtime error: cudaMalloc failed: out of memory" in data.get("error", "")
    from backend.api.model_api import Settings as S

    assert S.llm is client.application.config["LLAMA_INDEX_LLM"]
