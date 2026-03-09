import importlib

import pytest

if importlib.util.find_spec("backend.services.task_scheduler") is None:
    pytest.skip("task scheduler unavailable", allow_module_level=True)

try:
    from flask import Flask
    from tests.helpers import make_mock_llm

    from backend.api.tasks_api import tasks_bp
    from backend.models import Task, db
    from backend.utils import llm_service
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config.update(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        }
    )
    db.init_app(app)

    class BaseLLM:
        def chat(self, messages):
            raise NotImplementedError

    llm_service.LLM = BaseLLM
    app.config["LLAMA_INDEX_LLM"] = make_mock_llm("done")

    if tasks_bp.name not in app.blueprints:
        app.register_blueprint(tasks_bp)

    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_force_process_completes_tasks(client):
    resp = client.post("/api/tasks", json={"name": "task1", "prompt_text": "hi"})
    assert resp.status_code == 201
    id1 = resp.get_json()["id"]

    resp = client.post("/api/tasks", json={"name": "task2", "prompt_text": "hi"})
    assert resp.status_code == 201
    id2 = resp.get_json()["id"]

    resp = client.post("/api/tasks/force_process")
    assert resp.status_code == 200
    assert resp.get_json().get("status") == "processed"

    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    tasks = {t["id"]: t["status"] for t in resp.get_json()}
    assert tasks[id1] == "completed"
    assert tasks[id2] == "completed"
