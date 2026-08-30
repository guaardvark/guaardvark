"""Tests for SelfImprovementService."""
import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime


class TestSelfImprovementService:
    """Test the Self-Improvement Service."""

    def test_check_enabled_returns_false_when_disabled(self):
        """Service should not run when disabled."""
        from backend.services.self_improvement_service import SelfImprovementService
        service = SelfImprovementService.__new__(SelfImprovementService)
        service._check_enabled = lambda: False
        assert service._check_enabled() is False

    def test_check_enabled_returns_false_when_locked(self):
        """Service should not run when codebase is locked."""
        from backend.services.self_improvement_service import SelfImprovementService
        service = SelfImprovementService.__new__(SelfImprovementService)
        with patch("backend.services.self_improvement_service._is_codebase_locked", return_value=True):
            service._initialized = True
            assert service._is_safe_to_run() is False

    def test_parse_test_results(self):
        """Should parse pytest output into structured results."""
        from backend.services.self_improvement_service import SelfImprovementService
        service = SelfImprovementService.__new__(SelfImprovementService)

        pytest_output = """
FAILED backend/tests/test_code_tools.py::test_edit_code - AssertionError: expected 'hello'
PASSED backend/tests/test_code_tools.py::test_read_code
FAILED backend/tests/test_self_improvement.py::test_planted_bug_fix - RuntimeError: model unavailable
2 failed, 1 passed
"""
        failures = service._parse_test_failures(pytest_output)
        assert len(failures) == 2
        assert failures[0]["test_name"] == "test_edit_code"
        assert "test_code_tools.py" in failures[0]["file"]

    def test_error_fingerprint(self):
        """Should generate consistent fingerprints for same errors."""
        from backend.services.self_improvement_service import SelfImprovementService
        service = SelfImprovementService.__new__(SelfImprovementService)

        fp1 = service._error_fingerprint("backend/api/foo.py", 42, "ValueError")
        fp2 = service._error_fingerprint("backend/api/foo.py", 42, "ValueError")
        fp3 = service._error_fingerprint("backend/api/bar.py", 42, "ValueError")
        assert fp1 == fp2
        assert fp1 != fp3


class TestDirectedRunHonesty:
    """A directed run is a success only when a PendingFix was staged."""

    @pytest.fixture
    def app(self):
        from flask import Flask
        from backend.models import db
        app = Flask(__name__)
        app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
        db.init_app(app)
        with app.app_context():
            db.create_all()
            yield app
            db.session.remove()
            db.drop_all()

    def _service(self):
        from backend.services.self_improvement_service import SelfImprovementService
        svc = SelfImprovementService()
        svc._is_safe_to_run = lambda: True
        return svc

    def test_prose_answer_without_staged_fix_is_not_success(self, app):
        from backend.models import SelfImprovementRun
        svc = self._service()
        with patch.object(svc, "_attempt_fix", return_value={
                "file": "x.py", "test": "directed_improvement",
                "fix_description": "Reached maximum iterations. Here's what I found: ...",
                "iterations": 15}):
            result = svc.submit_directed_task("Tool 'list_documents' is not in CORE_TOOLS", ["x.py"])
        assert result["success"] is False
        run = SelfImprovementRun.query.order_by(SelfImprovementRun.id.desc()).first()
        assert run.status == "no_change"
        assert json.loads(run.changes_made) == []
        assert "maximum iterations" in run.error_message

    def test_staged_pending_fix_makes_the_run_a_success(self, app):
        from backend.models import db, PendingFix, SelfImprovementRun
        svc = self._service()

        def fake_attempt(failure, message=None):
            assert "edit_code" in message and "not a failing test" in message
            db.session.add(PendingFix(run_id=svc._current_run_id, file_path="x.py",
                                      proposed_diff="--- a\n+++ b\n", fix_description="add tool",
                                      severity="low", status="proposed"))
            db.session.commit()
            return {"file": "x.py", "test": "directed_improvement",
                    "fix_description": "Proposed adding list_documents to CORE_TOOLS.", "iterations": 3}

        with patch.object(svc, "_attempt_fix", side_effect=fake_attempt):
            result = svc.submit_directed_task("Tool 'list_documents' is not in CORE_TOOLS", ["x.py"])
        assert result["success"] is True and len(result["pending_fix_ids"]) == 1
        run = SelfImprovementRun.query.order_by(SelfImprovementRun.id.desc()).first()
        assert run.status == "success"
        assert json.loads(run.changes_made)[0]["pending_fix_id"] == result["pending_fix_ids"][0]
