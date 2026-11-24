"""Cancel character sample generation — marks in-flight samples and jobs."""
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

try:
    from flask import Flask
    from backend.models import db, Subject, SubjectSample
    from backend.services import character_generation_cancel as cgc
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def comfy_posts(monkeypatch):
    """Every requests.post a cancel makes; ComfyUI is reached by the render loop, not here."""
    import requests
    calls = []
    monkeypatch.setattr(requests, "post", lambda url, **kw: calls.append((url, kw.get("json"))))
    return calls


def test_cancel_marks_pending_and_generating(app, monkeypatch, comfy_posts):
    with app.app_context():
        s = Subject(name="Test", kind="character")
        db.session.add(s)
        db.session.commit()
        sid = s.id
        for i, st in enumerate(("done", "pending", "generating")):
            db.session.add(SubjectSample(
                subject_id=sid, index=i, status=st, approved=(st == "done"),
            ))
        db.session.commit()

        monkeypatch.setattr(cgc, "_find_active_jobs", lambda _sid: [])

        result = cgc.cancel_character_generation(sid)
        assert result["cancelled"] is True
        assert result["samples_marked"] == 2

        statuses = {
            r.index: r.status
            for r in SubjectSample.query.filter_by(subject_id=sid).all()
        }
        assert statuses[0] == "done"
        assert statuses[1] == "cancelled"
        assert statuses[2] == "cancelled"
        # No bare /interrupt and no /queue clear: other ComfyUI work keeps running.
        assert comfy_posts == []


def test_cancel_noop_when_idle(app, monkeypatch):
    with app.app_context():
        s = Subject(name="Idle", kind="character")
        db.session.add(s)
        db.session.commit()
        monkeypatch.setattr(cgc, "_find_active_jobs", lambda _sid: [])

        result = cgc.cancel_character_generation(s.id)
        assert result["cancelled"] is False
        assert result["reason"] == "not_generating"


def test_cancel_revokes_celery_and_progress(app, monkeypatch):
    with app.app_context():
        s = Subject(name="Busy", kind="character")
        db.session.add(s)
        db.session.commit()
        sid = s.id
        db.session.add(SubjectSample(subject_id=sid, index=0, status="generating"))
        db.session.commit()

        cancelled = []
        revoked = []

        monkeypatch.setattr(
            cgc, "_find_active_jobs",
            lambda _sid: [("job-1", {"celery_task_id": "celery-abc", "operation": "generate_samples"})],
        )

        fake_celery = MagicMock()
        fake_celery.control.revoke = lambda tid, **k: revoked.append(tid)

        @contextmanager
        def _noop_app_ctx():
            yield

        monkeypatch.setattr(
            "backend.celery_app.celery", fake_celery, raising=False,
        )
        # Import path used inside cancel_character_generation
        import sys
        mod = MagicMock()
        mod.celery = fake_celery
        monkeypatch.setitem(sys.modules, "backend.celery_app", mod)

        ups = MagicMock()
        ups.cancel_process = lambda job_id, msg: cancelled.append(job_id)
        monkeypatch.setattr(
            "backend.utils.unified_progress_system.get_unified_progress",
            lambda: ups,
        )

        result = cgc.cancel_character_generation(sid)
        assert result["cancelled"] is True
        assert revoked == ["celery-abc"]
        assert cancelled == ["job-1"]


def test_the_render_loop_sees_the_cancel_while_comfyui_samples(app, monkeypatch):
    """The render stops its own prompt once the cancel is committed."""
    from backend.services import character_generation_cancel
    from backend.tasks.character_generation_tasks import _sample_stop_check

    monkeypatch.setattr(character_generation_cancel, "job_is_cancelled", lambda _job: False)
    with app.app_context():
        s = Subject(name="Busy", kind="character")
        db.session.add(s)
        db.session.commit()
        row = SubjectSample(subject_id=s.id, index=0, status="generating")
        db.session.add(row)
        db.session.commit()

        should_stop = _sample_stop_check(row.id, "job-1")
        assert should_stop() is False
        row.status = "cancelled"
        db.session.commit()
        assert should_stop() is True
