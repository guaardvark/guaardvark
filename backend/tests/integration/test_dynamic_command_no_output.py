import pytest

try:
    from flask import Flask
    from tests.helpers import make_mock_llm

    from backend.api.generation_api import generation_bp
    from backend.api.query_api import query_bp
    from backend.models import Rule, db
    from backend.utils import llm_service
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


class DummyLLM:
    def chat(self, messages):
        class Msg:
            content = "pong"

        class Resp:
            message = Msg()

        return Resp()


@pytest.fixture
def client(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config.update(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "OUTPUT_DIR": str(tmp_path),
        }
    )
    db.init_app(app)
    if query_bp.name not in app.blueprints:
        app.register_blueprint(query_bp)
    if generation_bp.name not in app.blueprints:
        app.register_blueprint(generation_bp)
    with app.app_context():
        db.create_all()
        app.config["LLAMA_INDEX_LLM"] = make_mock_llm("pong")
        prompts = []

        def fake_generate(*a, **k):
            prompts.append(k.get("prompt"))
            return "RESULT"

        monkeypatch.setattr(llm_service, "generate_text_basic", fake_generate)
        app.config["PROMPTS_SENT"] = prompts
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_dynamic_command_without_output_file(client):
    resp = client.post("/api/query", json={"prompt": "/testcmd some text"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["response"]
    assert "/testcmd" in data["response"] and "not found" in data["response"]
    assert client.application.config["PROMPTS_SENT"] == []


def test_dynamic_command_runs_its_active_command_rule(client):
    db.session.add(
        Rule(
            command_label="/testcmd",
            level="SYSTEM",
            type="COMMAND_RULE",
            rule_text="Summarise: {{USER_INPUT}}",
            target_models_json='["__ALL__"]',
            is_active=True,
        )
    )
    db.session.commit()

    resp = client.post("/api/query", json={"prompt": "/testcmd some text"})
    assert resp.status_code == 200
    assert resp.get_json()["response"] == "RESULT"
    assert client.application.config["PROMPTS_SENT"] == ["Summarise: some text"]
