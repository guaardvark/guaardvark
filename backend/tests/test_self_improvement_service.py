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


SVC = "backend.services.self_improvement_service"

ONE_FAILING_TEST = MagicMock(returncode=1, stderr="", stdout=(
    "FAILED backend/tests/test_a.py::test_one - AssertionError: no\n1 failed\n"
))

PROSE_ANSWER = {"file": "backend/tests/test_a.py", "test": "test_one",
                "fix_description": "I could not find the problem.", "iterations": 15}


class TestScheduledAndReactiveRunHonesty:
    """A self-check or a heal is a success only when the agent staged a PendingFix."""

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

    @staticmethod
    def _service():
        """A service that is not the process singleton."""
        from backend.services.self_improvement_service import SelfImprovementService
        svc = object.__new__(SelfImprovementService)
        svc._initialized = False
        svc.__init__()
        svc._is_safe_to_run = lambda: True
        svc._gates_open = lambda: True
        SelfImprovementService._cancel_requested_ids.clear()
        return svc

    @staticmethod
    def _stage_for(svc, file_path="backend/a.py"):
        from backend.models import db, PendingFix
        db.session.add(PendingFix(run_id=svc._current_run_id, file_path=file_path,
                                  proposed_diff="--- a\n+++ b\n", fix_description="fix it",
                                  severity="low", status="proposed"))
        db.session.commit()

    @staticmethod
    def _learnings():
        from backend.models import InterconnectorLearning
        return InterconnectorLearning.query.count()

    def test_self_check_prose_answer_is_a_failed_run_with_nothing_broadcast(self, app):
        from backend.models import SelfImprovementRun
        svc = self._service()
        with patch(f"{SVC}.subprocess.run", return_value=ONE_FAILING_TEST) as pytest_run, \
             patch.object(svc, "_attempt_fix", return_value=PROSE_ANSWER), \
             patch.object(svc, "_verify_fix", side_effect=AssertionError("nothing staged, nothing to test")):
            result = svc.run_self_check()

        assert result["success"] is False
        assert result["fixes_staged"] == 0 and result["changes"] == []
        assert result["message"] == "No fix staged for 1 failure(s)"
        assert pytest_run.call_count == 2  # the check, then a re-run of its failing tests
        run = SelfImprovementRun.query.order_by(SelfImprovementRun.id.desc()).first()
        assert run.status == "failed"
        assert json.loads(run.changes_made) == []
        assert self._learnings() == 0

    def test_self_check_staged_fix_is_a_success_reported_as_staged(self, app):
        from backend.models import PendingFix, SelfImprovementRun
        svc = self._service()

        def stage(failure, message=None):
            self._stage_for(svc)
            return {**PROSE_ANSWER, "fix_description": "Returned the missing key."}

        passed = {"tests": ["backend/tests/test_a.py"], "all_passed": True, "total_failures": 0,
                  "failures": [], "return_code": 0, "error": None}
        with patch(f"{SVC}.subprocess.run", return_value=ONE_FAILING_TEST) as pytest_run, \
             patch.object(svc, "_attempt_fix", side_effect=stage), \
             patch.object(svc, "_verify_fix", return_value=passed) as verify:
            result = svc.run_self_check()

        assert result["success"] is True
        assert result["message"] == "1 fix(es) staged for review"
        assert result["fixes_staged"] == 1 and result["fixes_verified"] == 1
        assert pytest_run.call_count == 2  # the check, then a re-run of its failing tests
        fix = PendingFix.query.one()
        verify.assert_called_once_with(["backend/tests/test_a.py"], [fix])
        assert fix.status == "proposed"
        assert "passed with this fix applied" in fix.fix_description
        run = SelfImprovementRun.query.order_by(SelfImprovementRun.id.desc()).first()
        assert run.status == "success"
        change = json.loads(run.changes_made)[0]
        assert change["test"] == "test_one" and change["pending_fix_id"]
        assert change["fix_description"] == "Returned the missing key."
        assert change["verification"]["all_passed"] is True
        assert self._learnings() == 1

    def test_heal_prose_answer_is_a_failed_run(self, app):
        from backend.models import SelfImprovementRun
        svc = self._service()
        with patch.object(svc, "_attempt_fix", return_value=PROSE_ANSWER):
            svc.heal("backend/a.py", 12, "KeyError", "Traceback ...")

        run = SelfImprovementRun.query.order_by(SelfImprovementRun.id.desc()).first()
        assert run.trigger == "reactive"
        assert run.status == "failed"
        assert json.loads(run.changes_made) == []
        assert run.error_message == "I could not find the problem."
        assert self._learnings() == 0
        assert svc._running is False and svc._current_run_id is None

    def test_heal_staged_fix_is_a_success(self, app):
        from backend.models import SelfImprovementRun
        svc = self._service()
        seen_run_ids = []

        def stage(failure, message=None):
            seen_run_ids.append(svc._current_run_id)
            self._stage_for(svc)
            return PROSE_ANSWER

        with patch.object(svc, "_attempt_fix", side_effect=stage):
            svc.heal("backend/a.py", 12, "KeyError", "Traceback ...")

        run = SelfImprovementRun.query.order_by(SelfImprovementRun.id.desc()).first()
        assert seen_run_ids == [run.id]
        assert run.status == "success"
        assert json.loads(run.changes_made)[0]["file"] == "backend/a.py"
        assert svc._current_run_id is None

    def test_heal_marks_its_edits_unattended(self, app):
        """edit_code holds an Uncle Claude review for 'reactive' runs while
        Scheduled sends are off, so heal() must say it is one."""
        svc = self._service()
        seen = []

        def attempt(failure, message=None):
            seen.append(svc._current_trigger)
            return PROSE_ANSWER

        with patch.object(svc, "_attempt_fix", side_effect=attempt):
            svc.heal("backend/a.py", 12, "KeyError", "Traceback ...")

        assert seen == ["reactive"]
        assert svc._current_trigger is None

    def test_heal_hands_its_run_id_to_the_edit_tool_context(self, app):
        """_attempt_fix forwards the run id as _run_id, which edit_code stages under."""
        from backend.models import SelfImprovementRun
        svc = self._service()
        contexts = []

        class FakeExecutor:
            def __init__(self, **kwargs):
                pass

            def set_tool_context(self, **kwargs):
                contexts.append(kwargs)

            def execute(self, message, session_context=""):
                return MagicMock(final_answer="nothing found", iterations=1)

        agent = MagicMock(max_iterations=3, system_prompt="")
        with patch("backend.services.agent_executor.AgentExecutor", FakeExecutor), \
             patch("backend.services.agent_config.AgentConfigManager") as manager, \
             patch("backend.services.agent_tools.get_tool_registry", return_value=MagicMock()):
            manager.return_value.get_agent.return_value = agent
            svc.heal("backend/a.py", 12, "KeyError", "Traceback ...")

        run = SelfImprovementRun.query.order_by(SelfImprovementRun.id.desc()).first()
        assert contexts and contexts[0]["_run_id"] == run.id
        assert contexts[0]["_self_improvement_context"] is True
        assert run.status == "failed"

    @staticmethod
    def _latest_run():
        from backend.models import db, SelfImprovementRun
        db.session.expire_all()
        return SelfImprovementRun.query.order_by(SelfImprovementRun.id.desc()).first()

    def test_a_raising_heal_leaves_its_run_failed_with_the_error(self, app):
        svc = self._service()
        with patch.object(svc, "_attempt_fix", side_effect=RuntimeError("agent crashed")):
            svc.heal("backend/a.py", 12, "KeyError", "Traceback ...")

        run = self._latest_run()
        assert run.trigger == "reactive"
        assert run.status == "failed"
        assert run.error_message == "RuntimeError: agent crashed"
        assert svc._running is False and svc._current_run_id is None

    def test_a_heal_that_breaks_the_session_still_closes_its_run(self, app):
        from backend.models import db, PendingFix
        svc = self._service()

        def broken_write(failure, message=None):
            db.session.add(PendingFix(run_id=svc._current_run_id, file_path=None, proposed_diff="x"))
            db.session.flush()  # file_path is NOT NULL: IntegrityError, session needs a rollback

        with patch.object(svc, "_attempt_fix", side_effect=broken_write):
            svc.heal("backend/a.py", 12, "KeyError", "Traceback ...")

        run = self._latest_run()
        assert run.status == "failed"
        assert run.error_message.startswith("IntegrityError")
        assert PendingFix.query.count() == 0

    def test_a_heal_stopped_by_a_base_exception_closes_its_run(self, app):
        svc = self._service()
        with patch.object(svc, "_attempt_fix", side_effect=SystemExit(1)):
            with pytest.raises(SystemExit):
                svc.heal("backend/a.py", 12, "KeyError", "Traceback ...")

        run = self._latest_run()
        assert run.status == "failed"
        assert run.error_message == "Stopped before it finished"
        assert svc._running is False

    def test_a_cancelled_heal_that_raises_stays_cancelled(self, app):
        svc = self._service()

        def cancel_then_crash(failure, message=None):
            svc.request_cancel(svc._current_run_id)
            raise RuntimeError("agent crashed")

        with patch.object(svc, "_attempt_fix", side_effect=cancel_then_crash):
            svc.heal("backend/a.py", 12, "KeyError", "Traceback ...")

        run = self._latest_run()
        assert run.status == svc.CANCELLED
        assert run.error_message == "Cancelled by the user"

    def test_broadcast_skips_answers_that_staged_nothing(self, app):
        from backend.models import SelfImprovementRun, db
        svc = self._service()
        run = SelfImprovementRun(trigger="scheduled", status="success", node_id="local")
        db.session.add(run)
        db.session.commit()
        svc._broadcast_learnings([PROSE_ANSWER], run)
        assert self._learnings() == 0


class TestDistillationSkipsFailedModelCalls:
    """A failed model call's placeholder reply is never written as a learned strategy."""

    SECTION = (
        "# Self knowledge\n\n"
        "<!-- AUTO-DISTILLED START -->\n"
        "### Learned Strategies (auto-distilled from successful sessions)\n\n"
        "- **[2026-01-01, m]** Wait for the dialog before typing.\n\n"
        "<!-- AUTO-DISTILLED END -->\n"
    )
    STEPS = [{"action_type": "click", "target": "Save", "failed": True},
             {"action_type": "click", "target": "Save button"}]

    @pytest.fixture
    def knowledge(self, tmp_path):
        path = tmp_path / "data" / "agent" / "self_knowledge.md"
        path.parent.mkdir(parents=True)
        path.write_text(self.SECTION, encoding="utf-8")
        with patch("backend.config.GUAARDVARK_ROOT", str(tmp_path)), \
             patch("backend.services.inbound_guard_service.is_on", return_value=False):
            yield path

    @staticmethod
    def _service():
        from backend.services.self_improvement_service import SelfImprovementService
        return object.__new__(SelfImprovementService)

    @pytest.mark.parametrize("reply_name", [
        "LLM_UNAVAILABLE_REPLY", "LLM_ERROR_REPLY", "LLM_NO_RESPONSE_REPLY"])
    def test_failure_reply_leaves_self_knowledge_unchanged(self, knowledge, reply_name):
        from backend.utils import llm_service
        reply = getattr(llm_service, reply_name)
        with patch("backend.utils.llm_service.run_llm_chat_prompt", return_value=reply):
            self._service().distill_task_learning("save the file", self.STEPS, "m")
        assert knowledge.read_text(encoding="utf-8") == self.SECTION

    def test_unavailable_sentinel_text_is_the_one_the_service_returns(self, knowledge):
        with patch("backend.utils.llm_service.run_llm_chat_prompt", return_value="[LLM unavailable]"):
            self._service().distill_task_learning("save the file", self.STEPS, "m")
        assert "[LLM" not in knowledge.read_text(encoding="utf-8")

    def test_a_real_insight_is_still_written(self, knowledge):
        insight = "Click the labelled Save button rather than the toolbar icon."
        with patch("backend.utils.llm_service.run_llm_chat_prompt", return_value=insight):
            self._service().distill_task_learning("save the file", self.STEPS, "m")
        assert insight in knowledge.read_text(encoding="utf-8")


class TestPendingFixDisplayPath:
    def test_to_dict_path_is_repo_relative(self, tmp_path):
        from backend.models import PendingFix
        from backend import config
        with patch.object(config, "GUAARDVARK_ROOT", str(tmp_path)):
            inside = PendingFix(file_path=str(tmp_path / "backend" / "x.py"), proposed_diff="", status="proposed")
            outside = PendingFix(file_path="/elsewhere/y.py", proposed_diff="", status="proposed")
            assert inside.to_dict()["file_path"] == "backend/x.py"
            assert outside.to_dict()["file_path"] == "/elsewhere/y.py"


def test_test_counts_reads_pytests_closing_line():
    from backend.services.self_improvement_service import SelfImprovementService

    counts = SelfImprovementService._test_counts
    assert counts("....\n12 passed, 4 deselected in 1.62s\n") == {"passed": 12, "deselected": 4}
    assert counts("===== 3 failed, 9 passed, 2 warnings in 12.01s =====") == {"failed": 3, "passed": 9, "warnings": 2}
    assert counts("collected nothing") == {}
