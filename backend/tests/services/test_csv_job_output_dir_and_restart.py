"""Bulk CSV writer jobs with OUTPUT_DIR moved away from data/outputs, and
writer rows a backend restart left open. Scratch sqlite and tmp folders only;
no model, broker or Postgres."""

import csv
import json
import sys
import threading
import time
import types

import pytest

from backend.services import csv_job

ROWS = [{"ID": "a-001", "Title": "Plumbing in Tampa", "Content": "<p>Tampa</p>", "Excerpt": "e",
         "Category": "Services", "Tags": "plumbing", "slug": "plumbing-tampa"}]


@pytest.fixture
def moved_output_dir(tmp_path, monkeypatch):
    """OUTPUT_DIR outside the working directory, as GUAARDVARK_OUTPUT_DIR sets it."""
    from backend import config

    moved = tmp_path / "moved-outputs"
    moved.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", str(moved))
    monkeypatch.chdir(cwd)
    return moved


def _in_thread(fn, *args):
    """Run ``fn`` the way the writer does: a thread with no app context."""
    box = {}

    def run():
        try:
            box["value"] = fn(*args)
        except Exception as e:  # handed back to the test
            box["error"] = e

    t = threading.Thread(target=run)
    t.start()
    t.join(10)
    return box


def test_writer_thread_writes_into_the_configured_output_dir(moved_output_dir, tmp_path):
    from backend.tools.file_processor import write_csv

    target = moved_output_dir / "moved.csv"
    box = _in_thread(write_csv, ROWS, str(target))

    assert "error" not in box, box.get("error")
    with open(target, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == list(ROWS[0]) and rows[1][1] == "Plumbing in Tampa"
    assert not (tmp_path / "cwd" / "data").exists()


def test_writer_thread_still_refuses_a_path_outside_the_output_dir(moved_output_dir, tmp_path):
    from backend.tools.file_processor import write_csv

    box = _in_thread(write_csv, ROWS, str(tmp_path / "elsewhere.csv"))

    assert isinstance(box.get("error"), ValueError)
    assert not (tmp_path / "elsewhere.csv").exists()


def test_retry_reads_the_tracking_log_from_the_output_dir(moved_output_dir, monkeypatch):
    from flask import Flask

    from backend.api import bulk_generation_api as bga

    (moved_output_dir / "tracking").mkdir()
    (moved_output_dir / "tracking" / "job_tracking.json").write_text("{}", encoding="utf-8")
    opened = []

    class Analyzer:
        def __init__(self, path):
            opened.append(path)

        def analyze_tracking_file(self):
            return {}

        def extract_failed_topics(self, mode):
            return []

    monkeypatch.setattr(bga, "TrackingAnalyzer", Analyzer)
    app = Flask(__name__)
    app.config.update(TESTING=True, OUTPUT_DIR=str(moved_output_dir))
    app.register_blueprint(bga.bulk_gen_bp)

    reply = app.test_client().post("/api/bulk-generate/retry", json={"tracking_file": "job_tracking.json"})

    assert reply.status_code == 400  # found, nothing to retry
    assert opened == [str(moved_output_dir / "tracking" / "job_tracking.json")]


# --- rows a restart left open ----------------------------------------------


def _app(db_path, output_dir):
    from flask import Flask

    from backend.models import db

    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_path}", OUTPUT_DIR=str(output_dir))
    db.init_app(app)
    return app


@pytest.fixture
def ended(monkeypatch):
    """Progress entries the sweep ends."""
    from backend.utils import unified_progress_system as ups

    calls = []
    monkeypatch.setattr(ups, "get_unified_progress",
                        lambda: types.SimpleNamespace(error_process=lambda job_id, msg: calls.append((job_id, msg))))
    return calls


def _seed(app):
    from backend.models import Task, db

    rows = {
        "writer_running": ("in-progress", "bulk_gen_1795483560_782edf"),
        "writer_not_started": ("pending", "bulk_gen_1795483561_aaaaaa"),
        "writer_done": ("completed", "bulk_gen_1795483562_bbbbbb"),
        "writer_failed": ("failed", "bulk_gen_1795483563_cccccc"),
        "celery_running": ("in-progress", "task_7"),
        "celery_queued": ("queued", "6f1c2a9e-0d1b-4c55-9a43-0a1b2c3d4e5f"),
        "waiting": ("pending", None),
    }
    ids = {}
    with app.app_context():
        db.create_all()
        for name, (status, job_id) in rows.items():
            task = Task(name=name, type="file_generation", status=status, job_id=job_id)
            db.session.add(task)
            db.session.commit()
            ids[name] = task.id
        db.session.remove()
    return ids


def _rows(app):
    from backend.models import Task, db

    with app.app_context():
        out = {t.name: (t.status, t.error_message) for t in db.session.query(Task).all()}
        db.session.remove()
        return out


def test_restart_fails_the_writer_rows_it_left_open_and_nothing_else(tmp_path, ended):
    from backend.models import db

    db_path = tmp_path / "jobs.db"
    before_restart = _app(db_path, tmp_path)
    _seed(before_restart)

    after_restart = _app(db_path, tmp_path)
    failed = csv_job.fail_interrupted(after_restart)

    rows = _rows(after_restart)
    assert failed == 2
    assert rows["writer_running"] == ("failed", csv_job.INTERRUPTED)
    assert rows["writer_not_started"] == ("failed", csv_job.INTERRUPTED)
    assert rows["writer_done"] == ("completed", None)
    assert rows["writer_failed"] == ("failed", None)
    assert rows["celery_running"] == ("in-progress", None)
    assert rows["celery_queued"] == ("queued", None)
    assert rows["waiting"] == ("pending", None)
    assert sorted(ended) == [("bulk_gen_1795483560_782edf", csv_job.INTERRUPTED),
                             ("bulk_gen_1795483561_aaaaaa", csv_job.INTERRUPTED)]
    with after_restart.app_context():
        db.drop_all()


def test_a_second_boot_finds_nothing_left_to_fail(tmp_path, ended):
    db_path = tmp_path / "jobs.db"
    app = _app(db_path, tmp_path)
    _seed(app)
    csv_job.fail_interrupted(app)

    assert csv_job.fail_interrupted(_app(db_path, tmp_path)) == 0


def test_the_sweep_never_raises_on_a_database_it_cannot_read(tmp_path, ended):
    app = _app(tmp_path / "no-tables.db", tmp_path)

    assert csv_job.fail_interrupted(app) == 0


def test_a_failed_row_can_be_started_again(tmp_path, ended):
    """The sweep closes the run; Start's claim takes a failed row at once."""
    from backend.models import Task, db

    db_path = tmp_path / "jobs.db"
    app = _app(db_path, tmp_path)
    ids = _seed(app)
    csv_job.fail_interrupted(app)

    with app.app_context():
        claimed = (db.session.query(Task)
                   .filter(Task.id == ids["writer_running"], Task.status.notin_(("in-progress", "queued")))
                   .update({"job_id": "task_x", "status": "queued"}, synchronize_session=False))
        db.session.commit()
        db.session.remove()
    assert claimed == 1


def test_writer_job_ids_carry_the_prefix_the_sweep_looks_for(tmp_path, monkeypatch):
    from backend.api import bulk_generation_api as bga
    from backend.models import Task, db

    app = _app(tmp_path / "jobs.db", tmp_path)
    monkeypatch.setitem(sys.modules, "backend.app", types.SimpleNamespace(get_or_create_app=lambda: app, socketio=None))

    class Writer:
        def __init__(self, **kwargs):
            pass

        def generate_bulk_csv(self, tasks, filename):
            return filename, {}

    progress = types.SimpleNamespace(create_process=lambda *a, **k: "p", complete_process=lambda *a, **k: None,
                                     error_process=lambda *a, **k: None)
    monkeypatch.setattr(bga, "EnhancedBulkCSVGenerator", lambda **kw: Writer(**kw))
    monkeypatch.setattr(bga, "get_unified_progress", lambda: progress)
    monkeypatch.setattr(bga, "get_active_model_name", lambda: "m1")
    with app.app_context():
        db.create_all()
        response, status = bga.start_bulk_csv_job({"output_filename": "a.csv", "topics": ["Tampa"], "num_items": 1})
        _wait_for_writer()
        row = db.session.get(Task, response.get_json()["task_id"])
        assert status == 200 and row.job_id.startswith(csv_job.WRITER_JOB_PREFIX)
        db.session.remove()
        db.drop_all()


def _wait_for_writer():
    end = time.time() + 5
    while time.time() < end and any(t.name.startswith("csv_gen_") for t in threading.enumerate()):
        time.sleep(0.05)


def test_a_writer_that_raises_fails_its_row(tmp_path, monkeypatch):
    """A failure the writer does not record itself still ends the row."""
    from backend.api import bulk_generation_api as bga
    from backend.models import Task, db
    from backend.utils.bulk_csv_generator import BulkCSVGenerator

    app = _app(tmp_path / "jobs.db", tmp_path)
    monkeypatch.setitem(sys.modules, "backend.app", types.SimpleNamespace(get_or_create_app=lambda: app, socketio=None))

    class Writer:
        _update_task_status = BulkCSVGenerator._update_task_status
        _update_task_status_with_context = BulkCSVGenerator._update_task_status_with_context

        def __init__(self, **kwargs):
            self.job_id = kwargs["job_id"]

        def generate_bulk_csv(self, tasks, filename):
            raise OSError("disk full")

    progress = types.SimpleNamespace(create_process=lambda *a, **k: "p", complete_process=lambda *a, **k: None,
                                     error_process=lambda *a, **k: None)
    monkeypatch.setattr(bga, "EnhancedBulkCSVGenerator", lambda **kw: Writer(**kw))
    monkeypatch.setattr(bga, "get_unified_progress", lambda: progress)
    monkeypatch.setattr(bga, "get_active_model_name", lambda: "m1")
    with app.app_context():
        db.create_all()
        task = Task(name="pages", type="file_generation", status="queued",
                    workflow_config=json.dumps({"items": ["Tampa"]}))
        db.session.add(task)
        db.session.commit()
        task_id = task.id
        response, status = bga.start_bulk_csv_job({"output_filename": "a.csv", "topics": ["Tampa"], "num_items": 1,
                                                   "existing_task_id": task_id})
        assert status == 200
    _wait_for_writer()
    with app.app_context():
        row = db.session.get(Task, task_id)
        assert row.status == "failed"
        assert "disk full" in (row.description or "")
        db.session.remove()
        db.drop_all()
