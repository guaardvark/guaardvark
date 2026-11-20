"""Regression tests for the Batch-3 scheduler/gate fixes (2026-05-30).

Covers the dual-scheduler double-execution race fix (atomic claim + SELECT filter)
and the GPU-gate wrong-holder-id fix. Hermetic: in-memory sqlite, no Celery/GPU.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from flask import Flask

from backend.models import db, Task


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


# ---- dual-scheduler race: thread scheduler must skip beat-claimed tasks -----
def test_process_pending_skips_claimed_tasks(app, monkeypatch):
    import backend.services.task_scheduler as ts
    unclaimed = Task(name="unclaimed", status="pending", job_id=None, type="content_generation", priority=1)
    claimed = Task(name="claimed", status="pending", job_id="task_999", type="content_generation", priority=1)
    db.session.add_all([unclaimed, claimed])
    db.session.commit()
    uid, cid = unclaimed.id, claimed.id

    ran = []
    monkeypatch.setattr(ts, "_execute_task", lambda app, tid: ran.append(tid))
    ts.process_pending_tasks(app)

    assert uid in ran, "unclaimed pending task should be processed"
    assert cid not in ran, "a task the beat already claimed (job_id set) must be skipped"


# ---- atomic claim: _execute_task must not re-run an already-claimed task ----
def test_execute_task_skips_already_claimed(app, monkeypatch):
    import backend.services.task_scheduler as ts
    t = Task(name="taken", status="in-progress", job_id="task_1", type="content_generation")
    db.session.add(t)
    db.session.commit()

    # get_unified_progress() is the first thing called AFTER a successful claim; if the
    # atomic claim correctly finds 0 rows (job_id already set), _execute_task returns first.
    spy = MagicMock()
    monkeypatch.setattr(ts, "get_unified_progress", spy)
    ts._execute_task(app, t.id)
    spy.assert_not_called()


def test_atomic_claim_wins_once(app):
    """The conditional UPDATE claims a pending+unclaimed task exactly once."""
    import datetime
    t = Task(name="race", status="pending", job_id=None, type="content_generation")
    db.session.add(t)
    db.session.commit()
    now = datetime.datetime.now(datetime.timezone.utc)

    def claim():
        n = (db.session.query(Task)
             .filter(Task.id == t.id, Task.status == "pending", Task.job_id.is_(None))
             .update({"status": "in-progress", "job_id": f"task_{t.id}", "updated_at": now},
                     synchronize_session=False))
        db.session.commit()
        return n

    assert claim() == 1   # first claimer wins
    assert claim() == 0   # second sees job_id set → no rows → loses


# ---- GPU gate: different same-kind ids must NOT both be granted -------------
def test_gpu_gate_rejects_different_holder_same_kind():
    from backend.services.job_operation_gate import JobOperationGate, GPU_EXCLUSIVE_KINDS

    kind = next(iter(GPU_EXCLUSIVE_KINDS))  # any GPU-exclusive kind
    gate = JobOperationGate()
    ok1, _ = gate.try_claim_gpu_exclusive(kind, "id-A")
    assert ok1 is True
    # same kind, DIFFERENT id → must be refused (pre-fix bug wrongly returned True)
    ok2, reason = gate.try_claim_gpu_exclusive(kind, "id-B")
    assert ok2 is False, f"different id should not get the GPU; got: {reason}"
    # same kind, SAME id → idempotent True
    ok3, _ = gate.try_claim_gpu_exclusive(kind, "id-A")
    assert ok3 is True


# ---- rows with their own runner are left to the unified executor ----
def test_pending_loop_leaves_typed_rows_to_the_executor(app, monkeypatch):
    import backend.services.task_scheduler as ts
    prompt = Task(name="prompt", status="pending", type="content_generation", priority=1)
    crawl = Task(name="crawl", status="pending", type="website_crawl", priority=1)
    outreach = Task(name="outreach", status="pending", type="social_outreach_reddit", priority=1)
    publish = Task(name="publish", status="pending", type="connection_publish", priority=1)
    batch = Task(name="batch", status="pending", type="image_batch", priority=1)
    db.session.add_all([prompt, crawl, outreach, publish, batch])
    db.session.commit()
    prompt_id = prompt.id

    ran = []
    monkeypatch.setattr(ts, "_execute_task", lambda app, tid: ran.append(tid))
    ts.process_pending_tasks(app)

    assert ran == [prompt_id]
