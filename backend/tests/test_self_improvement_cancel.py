"""A self-improvement scan can be cancelled, and the cancel sticks.

The runner usually lives in a Celery worker while the cancel arrives at the
API, so the flag is the run row's status. These tests exercise the flag from
both sides: through the service (same process) and through a bare row update
that the service's in-memory set never sees.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

SVC = "backend.services.self_improvement_service"

FAILING_PYTEST = MagicMock(returncode=1, stderr="", stdout=(
    "FAILED backend/tests/test_a.py::test_one - AssertionError: no\n"
    "FAILED backend/tests/test_a.py::test_two - AssertionError: no\n"
    "2 failed\n"
))


@pytest.fixture
def app():
    from backend.models import db
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def _own_cancel_flags(monkeypatch):
    """The cancelled-run ids are class-level and each test's database numbers
    its runs from 1, so every test gets its own set and leaves none behind."""
    from backend.services.self_improvement_service import SelfImprovementService
    monkeypatch.setattr(SelfImprovementService, "_cancel_requested_ids", set())


def _fresh_service():
    """A service instance that is not the process singleton, with a clean flag set."""
    from backend.services.self_improvement_service import SelfImprovementService
    svc = object.__new__(SelfImprovementService)
    svc._initialized = False
    svc.__init__()
    svc._is_safe_to_run = lambda: True
    svc._gates_open = lambda: True
    SelfImprovementService._cancel_requested_ids.clear()
    return svc


def _running_run():
    from backend.models import db, SelfImprovementRun
    run = SelfImprovementRun(trigger="scheduled", status="running", node_id="local")
    db.session.add(run)
    db.session.commit()
    return run


def _cancel_from_another_process(run_id):
    """What the API does in its own process: a row update, no shared memory."""
    from backend.models import db, SelfImprovementRun
    db.session.query(SelfImprovementRun).filter_by(id=run_id).update({"status": "cancelled"})
    db.session.commit()


# ---- the flag -----------------------------------------------------------------

def test_request_cancel_flips_a_running_row_and_is_reported(app):
    from backend.models import db, SelfImprovementRun
    svc = _fresh_service()
    run = _running_run()
    assert svc.request_cancel(run.id) is True
    assert db.session.get(SelfImprovementRun, run.id).status == "cancelled"
    assert svc._cancel_requested(run.id) is True


def test_request_cancel_refuses_finished_or_unknown_runs(app):
    from backend.models import db, SelfImprovementRun
    svc = _fresh_service()
    run = _running_run()
    run.status = "success"
    db.session.commit()
    assert svc.request_cancel(run.id) is False
    assert db.session.get(SelfImprovementRun, run.id).status == "success"
    assert svc.request_cancel(999_999) is False
    assert svc._cancel_requested(None) is False


def test_checkpoint_sees_a_cancel_committed_elsewhere(app):
    svc = _fresh_service()
    run = _running_run()
    assert svc._cancel_requested(run.id) is False
    _cancel_from_another_process(run.id)
    assert svc._cancel_requested(run.id) is True


# ---- the runner ----------------------------------------------------------------

def test_scan_cancelled_between_fixes_keeps_its_work_and_the_status(app):
    from backend.models import db, SelfImprovementRun
    svc = _fresh_service()
    calls = []

    def first_fix_then_cancel(failure, message=None):
        from backend.models import PendingFix
        calls.append(failure["test_name"])
        db.session.add(PendingFix(run_id=svc._current_run_id, file_path="backend/a.py",
                                  proposed_diff="+x", status="proposed"))
        db.session.commit()
        _cancel_from_another_process(svc._current_run_id)
        return {"file": "backend/tests/test_a.py", "test": failure["test_name"], "diff": "+x"}

    with patch(f"{SVC}.subprocess.run", return_value=FAILING_PYTEST), \
         patch.object(svc, "_attempt_fix", side_effect=first_fix_then_cancel), \
         patch.object(svc, "_verify_fix", side_effect=AssertionError("verification must not run")):
        result = svc.run_self_check()

    assert calls == ["test_one"], "the second failure was not attempted"
    assert result["cancelled"] is True and result["success"] is False
    run = db.session.get(SelfImprovementRun, result["run_id"])
    assert run.status == "cancelled"
    assert json.loads(run.changes_made)[0]["test"] == "test_one"
    assert run.duration_seconds is not None
    assert svc._running is False and svc._current_run_id is None


def test_scan_cancelled_during_the_test_step_attempts_no_fix(app):
    from backend.models import db, SelfImprovementRun
    svc = _fresh_service()

    def pytest_then_cancel(*args, **kwargs):
        _cancel_from_another_process(svc._current_run_id)
        return FAILING_PYTEST

    with patch(f"{SVC}.subprocess.run", side_effect=pytest_then_cancel), \
         patch.object(svc, "_attempt_fix", side_effect=AssertionError("no fix after cancel")):
        result = svc.run_self_check()

    assert result["cancelled"] is True and result["fixes_staged"] == 0
    run = db.session.get(SelfImprovementRun, result["run_id"])
    assert run.status == "cancelled"
    assert json.loads(run.test_results_before)["total_failures"] == 2


def test_scan_not_cancelled_finishes_as_before(app):
    from backend.models import SelfImprovementRun
    svc = _fresh_service()
    with patch(f"{SVC}.subprocess.run", return_value=FAILING_PYTEST), \
         patch.object(svc, "_attempt_fix", return_value=None):
        result = svc.run_self_check()
    assert "cancelled" not in result and result["fixes_staged"] == 0
    assert SelfImprovementRun.query.order_by(SelfImprovementRun.id.desc()).first().status == "failed"


def test_directed_run_cancelled_mid_fix_does_not_become_no_change(app):
    from backend.models import db, SelfImprovementRun
    svc = _fresh_service()

    def fix_then_cancel(failure, message=None):
        _cancel_from_another_process(svc._current_run_id)
        return {"fix_description": "half done"}

    with patch.object(svc, "_attempt_fix", side_effect=fix_then_cancel):
        result = svc.submit_directed_task("tidy x", ["x.py"])
    assert result["cancelled"] is True
    assert db.session.get(SelfImprovementRun, result["run_id"]).status == "cancelled"


# ---- the routes ----------------------------------------------------------------

@pytest.fixture
def client(app):
    from backend.api.self_improvement_api import self_improvement_bp
    app.register_blueprint(self_improvement_bp)
    return app.test_client()


def test_cancel_route_marks_the_scan_and_every_status_route_reports_it(app, client):
    run = _running_run()
    with patch(f"{SVC}.get_self_improvement_service", return_value=_fresh_service()):
        res = client.post(f"/api/self-improvement/scans/{run.id}/cancel")
    assert res.status_code == 200
    assert res.get_json()["data"] == {"scan_id": run.id, "status": "cancelled"}

    assert client.get(f"/api/self-improvement/scans/{run.id}").get_json()["data"]["status"] == "cancelled"
    assert client.get("/api/self-improvement/runs").get_json()["data"]["runs"][0]["status"] == "cancelled"
    status = client.get("/api/self-improvement/status").get_json()["data"]
    assert status["last_run"]["status"] == "cancelled"


def test_cancel_route_refuses_finished_and_unknown_scans(app, client):
    from backend.models import db
    run = _running_run()
    run.status = "success"
    db.session.commit()
    res = client.post(f"/api/self-improvement/scans/{run.id}/cancel")
    assert res.status_code == 409
    assert res.get_json()["error"]["context"]["status"] == "success"
    assert client.post("/api/self-improvement/scans/424242/cancel").status_code == 404
    assert client.get("/api/self-improvement/scans/424242").status_code == 404
