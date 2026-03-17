from unittest.mock import MagicMock

import pytest

try:
    from flask import Flask
    from tests.helpers import make_mock_llm

    from backend.api import generation_api
    from backend.api.generation_api import generation_bp
    from backend.models import db
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def client(monkeypatch, tmp_path):
    app = Flask(__name__)
    app.config.update(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "OUTPUT_DIR": str(tmp_path),
        }
    )
    db.init_app(app)
    if generation_bp.name not in app.blueprints:
        app.register_blueprint(generation_bp)
    with app.app_context():
        db.create_all()

        class DummyLLM:
            def complete(self, prompt):
                return type("R", (), {"text": ""})()

            def chat(self, messages, **_):
                return type(
                    "Resp", (), {"message": type("Msg", (), {"content": ""})()}
                )()

        app.config["LLAMA_INDEX_LLM"] = DummyLLM()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_generate_from_command_llm_failure(client, monkeypatch, tmp_path):
    """Tests that the /generate/from_command endpoint handles LLM failures gracefully."""

    class DummyLLM:
        def complete(self, prompt):
            return type("R", (), {"text": ""})()

        def chat(self, messages, **_):
            return type("Resp", (), {"message": type("Msg", (), {"content": ""})()})()

    client.application.config["LLAMA_INDEX_LLM"] = DummyLLM()
    mock_rule = MagicMock()
    mock_rule.rule_text = "Test prompt"
    mock_rule.output_schema_name = None
    monkeypatch.setattr(
        "backend.api.generation_api.get_active_command_rule",
        lambda *args, **kwargs: mock_rule,
    )
    payload = {
        "command_label": "test_command",
        "output_filename": "test.txt",
        "generation_parameters": {},
    }
    response = client.post("/api/generate/from_command", json=payload)
    assert response.status_code == 200
    # Check that the file was written (even if empty)
    import os

    output_dir = client.application.config.get("OUTPUT_DIR", str(tmp_path))
    output_path = os.path.join(output_dir, "test.txt")
    assert os.path.exists(output_path)
