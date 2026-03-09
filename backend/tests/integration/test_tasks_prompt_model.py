import pytest

try:
    from flask import Flask

    from backend.api.tasks_api import get_available_ollama_models, tasks_bp
    from backend.models import Task, db
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(app)
    if tasks_bp.name not in app.blueprints:
        app.register_blueprint(tasks_bp)
    with app.app_context():
        db.create_all()
        # default patch for model list
        monkeypatch.setattr(
            "backend.api.tasks_api.get_available_ollama_models",
            lambda: [{"name": "orca-mini"}],
        )
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_create_task_with_prompt_and_model(client):
    resp = client.post(
        "/api/tasks",
        json={"name": "test", "prompt_text": "Hello", "model_name": "orca-mini"},
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["prompt_text"] == "Hello"
    assert data["model_name"] == "orca-mini"


def test_update_task_prompt_and_model(client):
    resp = client.post("/api/tasks", json={"name": "update"})
    tid = resp.get_json()["id"]
    resp = client.put(
        f"/api/tasks/{tid}", json={"prompt_text": "Bye", "model_name": "orca-mini"}
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["prompt_text"] == "Bye"
    assert data["model_name"] == "orca-mini"


def test_invalid_model_name(client, monkeypatch):
    monkeypatch.setattr(
        "backend.api.tasks_api.get_available_ollama_models",
        lambda: [{"name": "good-model"}],
    )
    resp = client.post("/api/tasks", json={"name": "bad", "model_name": "unknown"})
    assert resp.status_code == 404
