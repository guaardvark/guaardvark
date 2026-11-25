import logging
import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

try:
    from flask import Flask
    from tests.helpers import make_mock_llm

    from backend.models import Task, db
    from backend.services import task_scheduler
    from backend.utils import llm_service
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


class BaseLLM:
    def chat(self, messages):
        raise NotImplementedError


llm_service.LLM = BaseLLM


class DummyLLM(BaseLLM):
    def chat(self, messages):
        class Message:
            content = None

        class Response:
            message = Message()

        return Response()


class _RecordingProgress:
    """Stands in for the unified progress system and keeps what it was told."""

    def __init__(self):
        self.calls = []

    def create_process(self, process_type, description, process_id=None, **_kw):
        self.calls.append(("create_process", process_id))
        return process_id

    def update_process(self, process_id, *args, **_kw):
        self.calls.append(("update_process", process_id))

    def complete_process(self, process_id, *args, **_kw):
        self.calls.append(("complete_process", process_id))


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "OUTPUT_DIR": str(tmp_path),
            "LLAMA_INDEX_LLM": make_mock_llm(None),
        }
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def disable_analytics(monkeypatch):
    class DummyMsg:
        def __init__(self, role=None, content=None):
            self.role = role
            self.content = content

    monkeypatch.setattr(llm_service, "ChatMessage", DummyMsg)
    monkeypatch.setattr(
        llm_service,
        "MessageRole",
        types.SimpleNamespace(SYSTEM="system", USER="user"),
    )
    yield


@pytest.mark.llm
def test_llm_service_logs_no_content(app, caplog, monkeypatch):
    with app.app_context():
        monkeypatch.setattr(llm_service, "current_app", app)
        caplog.set_level(logging.WARNING, logger="backend.utils.llm_service")
        result = llm_service.run_llm_chat_prompt("hello")
    assert isinstance(result, str)
    assert result == "[LLM error occurred.]"


@pytest.mark.llm
def test_scheduler_logs_empty_output(app, caplog, monkeypatch):
    with app.app_context():
        task = Task(name="t1", prompt_text="prompt", output_filename="out.txt")
        db.session.add(task)
        db.session.commit()
        monkeypatch.setattr(llm_service, "current_app", app)
        # The task names no model, so the scheduler takes the active one and
        # checks it against what Ollama lists; the model it builds answers empty.
        monkeypatch.setattr("backend.api.tasks_api.get_default_task_model", lambda: None)
        monkeypatch.setattr("backend.models.get_active_model_name", lambda: "m")
        monkeypatch.setattr(task_scheduler, "_installed_ollama_models", lambda: ["m"])
        built = []
        monkeypatch.setattr(
            "backend.utils.ollama_resource_manager.build_ollama",
            lambda model_name, **_kw: built.append(model_name) or DummyLLM(),
        )
        # The real progress system emits over the app's socket, which imports
        # backend.app and boots the whole app inside the test.
        progress = _RecordingProgress()
        monkeypatch.setattr(task_scheduler, "get_unified_progress", lambda: progress)
        caplog.set_level(logging.WARNING, logger="backend.services.task_scheduler")
        task_scheduler._execute_task(app, task.id)
        assert built == ["m"]
        assert [name for name, _ in progress.calls] == ["create_process", "update_process", "complete_process"]
        # The scheduler worked in its own session; read the row again.
        db.session.refresh(task)
        assert task.status == "failed"

    # Check for the warning message in the captured logs
    warning_messages = [record.message for record in caplog.records if record.levelno >= logging.WARNING]
    assert any("LLM produced no output" in msg for msg in warning_messages)
