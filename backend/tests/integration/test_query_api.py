import types

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


class BaseLLM:
    def chat(self, messages):
        raise NotImplementedError


llm_service.LLM = BaseLLM


class DummyLLM(BaseLLM):
    def chat(self, messages):
        class Msg:
            content = "pong"

        class Resp:
            message = Msg()

        return Resp()


class DummyIndex:
    def as_query_engine(self, *args, **kwargs):
        class Engine:
            def query(self, prompt):
                return types.SimpleNamespace(
                    response=f"Echo: {prompt}", source_nodes=[]
                )

        return Engine()


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
        app.config["LLAMA_INDEX_INDEX"] = DummyIndex()
        monkeypatch.setattr(
            llm_service, "generate_text_basic", lambda *a, **k: "FILECONTENT"
        )

        def fake_post(url, json=None, timeout=None):
            path = url.split("://", 1)[-1]
            path = path[path.find("/") :]  # drop scheme and host
            resp = app.test_client().post(path, json=json)

            class Resp:
                status_code = resp.status_code
                text = resp.get_data(as_text=True)

                def raise_for_status(self):
                    if self.status_code >= 400:
                        raise Exception("status error")

                def json(self):
                    return resp.get_json()

            return Resp()

        monkeypatch.setattr("requests.post", fake_post)
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_plain_chat_returns_content(client):
    resp = client.post("/api/query", json={"prompt": "hello"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert (
        data["data"]["response"]
        and data["data"]["response"] != "[The model returned no response.]"
    )


def test_createfile_command_generates_file(client, tmp_path, monkeypatch):
    # Insert a dummy command rule for /createfile
    from backend.models import db, Rule
    with client.application.app_context():
        rule = Rule(
            name="/createfile",
            type="COMMAND_RULE",
            level="SYSTEM",
            rule_text="Generate a file: {args_string}",
            is_active=True,
            target_models_json='["mock"]',
            command_label="/createfile",
        )
        db.session.add(rule)
        db.session.commit()
    resp = client.post(
        "/api/query", json={"prompt": '/createfile output_file="test.txt" some text'}
    )
    assert resp.status_code == 200
    # Check that the file was created
    import os
    output_path = os.path.join(tmp_path, "test.txt")
    assert os.path.exists(output_path)
