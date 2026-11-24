"""A CSV file job reaches the structured CSV writer with its saved items,
whichever way it is started. No model, broker or Postgres: the writer and
the executor's database helpers are replaced."""

import json
import sys
import threading
import time
import types

import pytest

from backend.services import csv_job

SAVED = {
    "page_count": 3, "items": ["Tampa", "Orlando", "Miami"], "client_name": "Acme Plumbing",
    "target_website": "acme.example", "insert_content": "Call today.", "insert_position": "bottom",
    "client_notes": "Family plumbers.", "target_word_count": 300, "concurrent_workers": 1, "batch_size": 3,
    "prompt_rule_id": 7,
}


def saved_row(**over):
    row = {"id": 5, "name": "Florida pages", "type": "file_generation", "client_name": "Acme Plumbing",
           "target_website": "acme.example", "output_filename": "florida.csv", "model_name": "m1",
           "workflow_config": json.dumps(SAVED)}
    row.update(over)
    return row


def test_request_carries_the_saved_items_and_page_settings():
    body = csv_job.csv_request(saved_row())
    assert body["topics"] == ["Tampa", "Orlando", "Miami"]
    assert body["num_items"] == 3
    assert body["insert_content"] == "Call today." and body["insert_position"] == "bottom"
    assert body["client"] == "Acme Plumbing" and body["project"] == "Florida pages"
    assert body["prompt_rule_id"] == 7 and body["model_name"] == "m1"
    assert body["existing_task_id"] == 5 and body["output_filename"] == "florida.csv"
    assert (body["target_word_count"], body["concurrent_workers"], body["batch_size"]) == (300, 1, 3)


def test_blank_client_is_left_to_the_writer_default():
    config = {**SAVED, "client_name": ""}
    body = csv_job.csv_request(saved_row(client_name="", workflow_config=config))
    assert "client" not in body


def test_the_name_asked_for_is_reused_after_a_run_renamed_the_row():
    config = {**SAVED, "output_filename": "florida.csv"}
    body = csv_job.csv_request(saved_row(output_filename="florida-001.csv", workflow_config=config))
    assert body["output_filename"] == "florida.csv"


@pytest.mark.parametrize("task_type, config, expected", [
    ("file_generation", SAVED, True),
    ("csv_generation", json.dumps(SAVED), True),
    ("file_generation", None, False),
    ("code_generation", SAVED, False),
])
def test_is_csv_job(task_type, config, expected):
    assert csv_job.is_csv_job(task_type, config) is expected


def test_executor_hands_a_csv_row_to_the_writer_not_the_generic_runner(monkeypatch):
    from backend.tasks import unified_task_executor as ute

    statuses = []
    started = []
    monkeypatch.setattr(ute, "get_task_by_id", lambda task_id: {**saved_row(), "workflow_config": SAVED,
                                                                  "status": "queued", "max_retries": 3})
    monkeypatch.setattr(ute, "update_task_status", lambda task_id, status, **kw: statuses.append(status) or True)
    monkeypatch.setattr(ute, "update_task_result", lambda *a, **k: pytest.fail("the writer finishes the row"))
    monkeypatch.setattr(ute, "execute_generic_llm_task", lambda *a, **k: pytest.fail("generic runner used"))
    monkeypatch.setattr(csv_job, "start", lambda task: started.append(task["id"]) or {"job_id": "bulk_gen_1"})

    result = ute.execute_unified_task.run(5)

    assert started == [5]
    assert result["status"] == "handed_off" and result["csv_job"] == "bulk_gen_1"
    assert "completed" not in statuses


@pytest.fixture
def db_app(tmp_path, monkeypatch):
    from flask import Flask

    from backend.api import bulk_generation_api as bga
    from backend.models import db

    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'jobs.db'}",
                      OUTPUT_DIR=str(tmp_path))
    db.init_app(app)
    # The writer's thread borrows the running app; here that is this one, never a full backend.
    monkeypatch.setitem(sys.modules, "backend.app",
                        types.SimpleNamespace(get_or_create_app=lambda: app, socketio=None))
    written = []

    class Writer:
        def __init__(self, **kwargs):
            pass

        def generate_bulk_csv(self, tasks, filename):
            written.append([t.topic for t in tasks])
            return filename, {}

    progress = types.SimpleNamespace(create_process=lambda *a, **k: "p", complete_process=lambda *a, **k: None,
                                     error_process=lambda *a, **k: None)
    monkeypatch.setattr(bga, "EnhancedBulkCSVGenerator", lambda **kw: Writer(**kw))
    monkeypatch.setattr(bga, "get_unified_progress", lambda: progress)
    monkeypatch.setattr(bga, "get_active_model_name", lambda: "active-model")
    with app.app_context():
        db.create_all()
        app.written = written
        yield app
        db.session.remove()
        db.drop_all()


def _wait_for_writer():
    end = time.time() + 5
    while time.time() < end and any(t.name.startswith("csv_gen_") for t in threading.enumerate()):
        time.sleep(0.05)


def add_row(status="queued"):
    from backend.models import Task, db

    task = Task(name="Florida pages", type="file_generation", status=status, client_name="Acme Plumbing",
                target_website="acme.example", output_filename="florida.csv", model_name="m1",
                workflow_config=json.dumps(SAVED))
    db.session.add(task)
    db.session.commit()
    return task.id


def test_writer_links_the_row_for_a_client_not_in_the_clients_table(db_app):
    from backend.models import Task, db

    task_id = add_row()
    data = csv_job.start(db.session.get(Task, task_id))
    _wait_for_writer()

    row = db.session.get(Task, task_id)
    db.session.refresh(row)
    assert data["job_id"] == row.job_id
    assert row.status == "in-progress" and row.model_name == "m1"
    assert db_app.written == [["Tampa", "Orlando", "Miami"]]
    config = json.loads(row.workflow_config)
    assert config["items"] == SAVED["items"] and config["insert_content"] == "Call today."
    assert config["output_filename"] == "florida.csv"


def test_in_process_runner_starts_the_writer(db_app, monkeypatch):
    from backend.models import Task, db
    from backend.services import task_scheduler

    monkeypatch.setattr(task_scheduler, "_resolve_task_model", lambda t: pytest.fail("generic runner used"))
    task_id = add_row(status="pending")
    task_scheduler._execute_task(db_app, task_id)
    _wait_for_writer()

    row = db.session.get(Task, task_id)
    assert row.job_id.startswith("bulk_gen_")
    assert db_app.written == [["Tampa", "Orlando", "Miami"]]
