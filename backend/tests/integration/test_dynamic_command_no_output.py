import pytest

try:
    from flask import Flask
    from tests.helpers import make_mock_llm

    from backend.api.generation_api import generation_bp
    from backend.api.query_api import query_bp
    from backend.models import db
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
        monkeypatch.setattr(
            llm_service, "generate_text_basic", lambda *a, **k: "RESULT"
        )
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_dynamic_command_without_output_file(client):
    resp = client.post("/api/query", json={"prompt": "/testcmd some text"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["response"]
