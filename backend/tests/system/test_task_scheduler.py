import datetime
import os
import sys
import time
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

try:
    from flask import Flask
    from tests.helpers import make_mock_llm

    from backend.api.tasks_api import tasks_bp
    from backend.models import Task, db
    from backend.services import task_scheduler
    from backend.services.task_scheduler import init_task_scheduler
    from backend.utils import llm_service, ollama_resource_manager
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def sched_client(tmp_path, monkeypatch):
    app = Flask(__name__)
    # A file database: the scheduler thread and the request thread each get
    # their own connection to it.
    app.config.update(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'scheduler.db'}",
        }
    )
    # Progress events go to the app's own SocketIO; without one the progress
    # system imports backend.app, which starts the real application.
    app.extensions["socketio"] = types.SimpleNamespace(emit=lambda *a, **k: None)

    class BaseLLM:
        def chat(self, messages):
            raise NotImplementedError

    monkeypatch.setattr(llm_service, "LLM", BaseLLM)

    class DummyLLM(BaseLLM):
        def chat(self, messages):
            class Msg:
                content = "done"

            class Resp:
                message = Msg()

            return Resp()

    app.config["LLAMA_INDEX_LLM"] = make_mock_llm("done")
    # The scheduler checks a task's model against Ollama and builds its own
    # client for it; both are stood in for so no Ollama is asked.
    monkeypatch.setattr(task_scheduler, "_installed_ollama_models", lambda: ["task-model:latest"])
    monkeypatch.setattr(ollama_resource_manager, "build_ollama", lambda *a, **k: DummyLLM())
    db.init_app(app)
    if tasks_bp.name not in app.blueprints:
        app.register_blueprint(tasks_bp)
    with app.app_context():
        db.create_all()
        scheduler = init_task_scheduler(app)
        try:
            yield app.test_client()
        finally:
            scheduler.shutdown(wait=True)
            db.session.remove()
            db.drop_all()


@pytest.mark.llm
@pytest.mark.db
def test_scheduled_task_executes(sched_client):
    due = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=1)
    resp = sched_client.post(
        "/api/tasks",
        json={
            "name": "prompt run",
            "description": "test prompt",
            "due_date": due.isoformat(),
        },
    )
    assert resp.status_code == 201
    task_id = resp.get_json()["id"]

    # [CODEX PATCH APPLIED]: replaced sleep with polling loop
    # Poll until the task completes or timeout after ~7s
    deadline = time.time() + 7
    task = None
    while time.time() < deadline:
        resp = sched_client.get("/api/tasks")
        tasks = resp.get_json()
        task = next((t for t in tasks if t["id"] == task_id), None)
        if task and task.get("status") == "completed":
            break
        time.sleep(0.25)
    assert task and task["status"] == "completed"
    print("\u26d4\ufe0f Task never completed. Final task state:", task)

    resp = sched_client.get("/api/tasks")
    assert resp.status_code == 200
    tasks = resp.get_json()
    task = next(t for t in tasks if t["id"] == task_id)
    assert task["status"] == "completed"
