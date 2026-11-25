import importlib
import os
import types

import pytest

if importlib.util.find_spec("backend.services.task_scheduler") is None:
    pytest.skip("task scheduler unavailable", allow_module_level=True)

try:
    from flask import Flask
    from tests.helpers import make_mock_llm

    from backend.api.tasks_api import tasks_bp
    from backend.models import Task, db
    from backend.services import task_scheduler
    from backend.utils import llm_service, ollama_resource_manager
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_MODE", "test")
    app = Flask(__name__)
    app.config.update(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        }
    )
    db.init_app(app)
    # Progress events go to the app's own SocketIO; without one the progress
    # system imports backend.app, which starts the real application.
    app.extensions["socketio"] = types.SimpleNamespace(emit=lambda *a, **k: None)

    class BaseLLM:
        def chat(self, messages):
            raise NotImplementedError

    monkeypatch.setattr(llm_service, "LLM", BaseLLM)
    app.config["LLAMA_INDEX_LLM"] = make_mock_llm("done")
    # The scheduler checks a task's model against Ollama and builds its own
    # client for it; both are stood in for so no Ollama is asked.
    monkeypatch.setattr(task_scheduler, "_installed_ollama_models", lambda: ["task-model:latest"])

    class TaskLLM:
        def chat(self, messages):
            return types.SimpleNamespace(message=types.SimpleNamespace(content="done"))

    monkeypatch.setattr(ollama_resource_manager, "build_ollama", lambda *a, **k: TaskLLM())

    if tasks_bp.name not in app.blueprints:
        app.register_blueprint(tasks_bp)

    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_force_process_updates_pending_tasks(client):
    resp = client.post("/api/tasks", json={"name": "task1", "prompt_text": "hi"})
    assert resp.status_code == 201
    id1 = resp.get_json()["id"]

    resp = client.post("/api/tasks", json={"name": "task2", "prompt_text": "hi"})
    assert resp.status_code == 201
    id2 = resp.get_json()["id"]

    resp = client.get("/api/tasks")
    tasks = {t["id"]: t["status"] for t in resp.get_json()}
    assert tasks[id1] == "pending"
    assert tasks[id2] == "pending"

    resp = client.post("/api/tasks/force_process")
    assert resp.status_code == 200
    assert resp.get_json().get("status") == "processed"

    resp = client.get("/api/tasks")
    tasks = {t["id"]: t["status"] for t in resp.get_json()}
    assert tasks[id1] == "completed"
    assert tasks[id2] == "completed"
