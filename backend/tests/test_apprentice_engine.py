"""Tests for ApprenticeEngine — demonstration replay with graduated autonomy."""
import pytest
import uuid
import threading
from unittest.mock import MagicMock, patch, PropertyMock
from PIL import Image

from backend.services.apprentice_engine import ApprenticeEngine, AttemptResult


@pytest.fixture
def mock_screen():
    screen = MagicMock()
    img = Image.new("RGB", (1024, 1024), color="white")
    screen.capture.return_value = (img, (640, 360))
    screen.screen_size.return_value = (1024, 1024)
    screen.click.return_value = {"success": True, "x": 640, "y": 400}
    screen.type_text.return_value = {"success": True}
    screen.hotkey.return_value = {"success": True}
    screen.scroll.return_value = {"success": True}
    return screen


@pytest.fixture
def mock_analyzer():
    analyzer = MagicMock()
    result = MagicMock()
    result.description = '{"matches": true, "description": "Screen matches expected state"}'
    result.success = True
    analyzer.analyze.return_value = result
    analyzer.text_query.return_value = result
    return analyzer


@pytest.fixture
def mock_servo():
    servo = MagicMock()
    servo.click_target.return_value = {
        "success": True,
        "verified": True,
        "x": 640,
        "y": 400,
        "corrections": 0,
        "attempt": 1,
        "time_ms": 500,
    }
    return servo


@pytest.fixture
def demo_steps():
    return [
        {
            "step_index": 0,
            "action_type": "click",
            "target_description": "the Login button",
            "element_context": "centered below the password field",
            "precondition": "Login form visible",
            "variability": False,
            "text": None,
            "keys": None,
            "screenshot_after": None,
        },
        {
            "step_index": 1,
            "action_type": "type",
            "target_description": "the username field",
            "element_context": "first input in login form",
            "precondition": "Username field focused",
            "variability": True,
            "text": "admin@example.com",
            "keys": None,
            "screenshot_after": None,
        },
    ]


@pytest.fixture
def engine(mock_screen, mock_analyzer, mock_servo):
    return ApprenticeEngine(
        screen=mock_screen,
        analyzer=mock_analyzer,
        servo=mock_servo,
    )


class TestPreconditionCheck:
    def test_precondition_passes(self, engine, mock_analyzer):
        result_mock = MagicMock()
        result_mock.success = True
        result_mock.description = "Login form with fields"
        mock_analyzer.analyze.return_value = result_mock

        text_mock = MagicMock()
        text_mock.success = True
        text_mock.description = '{"matches": true, "description": "matches"}'
        mock_analyzer.text_query.return_value = text_mock

        result = engine._check_precondition("Login form visible")
        assert result["matches"] is True

    def test_precondition_fails(self, engine, mock_analyzer):
        result_mock = MagicMock()
        result_mock.success = True
        result_mock.description = "Dashboard showing"
        mock_analyzer.analyze.return_value = result_mock

        text_mock = MagicMock()
        text_mock.success = True
        text_mock.description = '{"matches": false, "description": "Dashboard is showing, not login form"}'
        mock_analyzer.text_query.return_value = text_mock

        result = engine._check_precondition("Login form visible")
        assert result["matches"] is False

    @staticmethod
    def _reply_with(mock_analyzer, text):
        vision_mock = MagicMock()
        vision_mock.success = True
        vision_mock.description = "Dashboard showing"
        mock_analyzer.analyze.return_value = vision_mock

        text_mock = MagicMock()
        text_mock.success = True
        text_mock.description = text
        mock_analyzer.text_query.return_value = text_mock

    def test_fallback_matches_false(self, engine, mock_analyzer):
        # Not valid JSON, and contains the word "matches".
        self._reply_with(mock_analyzer, "matches: false, the dashboard is showing")

        result = engine._check_precondition("Login form visible")
        assert result["matches"] is False
        assert result["checked"] is True

    def test_fenced_json_false(self, engine, mock_analyzer):
        self._reply_with(
            mock_analyzer,
            '```json\n{"matches": false, "description": "Dashboard, not login form"}\n```',
        )

        result = engine._check_precondition("Login form visible")
        assert result["matches"] is False
        assert result["checked"] is True

    def test_string_false(self, engine, mock_analyzer):
        self._reply_with(mock_analyzer, '{"matches": "false", "description": "Dashboard showing"}')

        result = engine._check_precondition("Login form visible")
        assert result["matches"] is False
        assert result["checked"] is True

    def test_missing_key(self, engine, mock_analyzer):
        self._reply_with(mock_analyzer, '{"description": "The screen matches the login form"}')

        result = engine._check_precondition("Login form visible")
        assert result["matches"] is False
        assert result["checked"] is False

    def test_does_not_match_prose(self, engine, mock_analyzer):
        self._reply_with(mock_analyzer, "The screen does not match: it shows a dashboard, not the login form.")

        result = engine._check_precondition("Login form visible")
        assert result["matches"] is False
        assert result["checked"] is True

    def test_plain_yes_is_match(self, engine, mock_analyzer):
        self._reply_with(mock_analyzer, "Yes, the login form is visible.")

        result = engine._check_precondition("Login form visible")
        assert result["matches"] is True
        assert result["checked"] is True

    def test_vision_failure_fails_closed(self, engine, mock_analyzer):
        vision_mock = MagicMock()
        vision_mock.success = False
        vision_mock.error = "vision model not loaded"
        mock_analyzer.analyze.return_value = vision_mock

        result = engine._check_precondition("Login form visible")
        assert result["matches"] is False
        assert result["checked"] is False
        mock_analyzer.text_query.assert_not_called()

    def test_text_failure_fails_closed(self, engine, mock_analyzer):
        vision_mock = MagicMock()
        vision_mock.success = True
        vision_mock.description = "Login form with fields"
        mock_analyzer.analyze.return_value = vision_mock

        text_mock = MagicMock()
        text_mock.success = False
        text_mock.error = "text model not loaded"
        mock_analyzer.text_query.return_value = text_mock

        result = engine._check_precondition("Login form visible")
        assert result["matches"] is False
        assert result["checked"] is False

    def test_exception_fails_closed(self, engine, mock_screen):
        mock_screen.capture.side_effect = RuntimeError("no display")

        result = engine._check_precondition("Login form visible")
        assert result["matches"] is False
        assert result["checked"] is False


class TestStepExecution:
    def test_execute_click_step(self, engine, demo_steps, mock_servo):
        result = engine._execute_step(demo_steps[0])
        assert result["success"] is True
        mock_servo.click_target.assert_called_once_with("the Login button centered below the password field")

    def test_a_click_that_changed_nothing_fails_the_step(self, engine, demo_steps, mock_servo):
        mock_servo.click_target.return_value = {
            "success": True, "verified": False, "post_action_effect": "no_visible_change", "x": 104, "y": 378}
        result = engine._execute_step(demo_steps[0])
        assert result["success"] is False
        assert "changed nothing" in result["error"]

    def test_a_click_that_changed_the_screen_passes(self, engine, demo_steps, mock_servo):
        mock_servo.click_target.return_value = {
            "success": True, "verified": True, "post_action_effect": "changed", "x": 104, "y": 460}
        assert engine._execute_step(demo_steps[0])["success"] is True

    def test_execute_type_step(self, engine, demo_steps, mock_screen):
        result = engine._execute_step(demo_steps[1], variable_input="admin@test.com")
        assert result["success"] is True
        mock_screen.type_text.assert_called_once_with("admin@test.com")

    def test_execute_type_step_uses_demo_text_when_no_override(self, engine, demo_steps, mock_screen):
        result = engine._execute_step(demo_steps[1])
        assert result["success"] is True
        mock_screen.type_text.assert_called_once_with("admin@example.com")

    def test_execute_hotkey_step(self, engine, mock_screen):
        step = {
            "step_index": 0,
            "action_type": "hotkey",
            "target_description": "keyboard shortcut",
            "element_context": "",
            "precondition": "",
            "variability": False,
            "text": None,
            "keys": "ctrl+l",
            "screenshot_after": None,
        }
        result = engine._execute_step(step)
        assert result["success"] is True
        mock_screen.hotkey.assert_called_once_with("ctrl", "l")


class TestGraduationLogic:
    def test_guided_to_supervised_after_3_successes(self, engine):
        assert engine._should_promote("guided", 3) is True
        assert engine._should_promote("guided", 2) is False

    def test_supervised_to_autonomous_after_3_successes(self, engine):
        assert engine._should_promote("supervised", 3) is True

    def test_demotion_on_failure(self, engine):
        assert engine._demote("autonomous") == "supervised"
        assert engine._demote("supervised") == "guided"
        assert engine._demote("guided") == "guided"

    def test_promotion_levels(self, engine):
        assert engine._promote("guided") == "supervised"
        assert engine._promote("supervised") == "autonomous"
        assert engine._promote("autonomous") == "autonomous"


class TestAttemptResult:
    def test_attempt_result_creation(self):
        result = AttemptResult(
            success=True,
            steps_completed=5,
            total_steps=5,
            step_results=[],
        )
        assert result.success is True
        assert result.steps_completed == 5


def _vision_reply(mock_analyzer, text, success=True):
    reply = MagicMock()
    reply.success = success
    reply.description = text
    reply.error = None if success else "vision model not loaded"
    mock_analyzer.analyze.return_value = reply


class TestSupervisedReplay:
    @pytest.fixture
    def click_steps(self):
        # No precondition, so the only vision call is the confidence estimate.
        return [
            {"step_index": 0, "action_type": "click", "target_description": "the Save button",
             "element_context": "", "precondition": ""},
            {"step_index": 1, "action_type": "click", "target_description": "the Close button",
             "element_context": "", "precondition": ""},
        ]

    def test_low_confidence_without_confirmation_aborts(self, engine, mock_analyzer, mock_servo,
                                                         click_steps, monkeypatch):
        monkeypatch.setattr("backend.services.apprentice_engine.QUESTION_TIMEOUT", 0.05)
        _vision_reply(mock_analyzer, '{"visible": false, "confidence": 0.1}')
        emitted = []

        result = engine.execute(click_steps, autonomy_level="supervised",
                                emit_fn=lambda name, data: emitted.append(name))

        assert result.success is False
        assert result.failure_reason == "Timeout waiting for confirmation at step 0"
        assert result.steps_completed == 0
        assert result.step_results == []
        assert emitted == ["step_preview"]
        mock_servo.click_target.assert_not_called()

    def test_low_confidence_with_confirmation_runs(self, engine, mock_analyzer, mock_servo, click_steps):
        _vision_reply(mock_analyzer, '{"visible": false, "confidence": 0.1}')
        engine._wait_for_confirmation = MagicMock(return_value={"confirmed": True})

        result = engine.execute(click_steps, autonomy_level="supervised")

        assert result.success is True
        assert engine._wait_for_confirmation.call_count == 2
        assert mock_servo.click_target.call_count == 2

    def test_high_confidence_runs_without_asking(self, engine, mock_analyzer, mock_servo, click_steps):
        _vision_reply(mock_analyzer, '{"visible": true, "confidence": 0.9}')
        engine._wait_for_confirmation = MagicMock(return_value=None)

        result = engine.execute(click_steps, autonomy_level="supervised")

        assert result.success is True
        engine._wait_for_confirmation.assert_not_called()
        assert mock_servo.click_target.call_count == 2

    def test_not_visible_with_high_confidence_asks(self, engine, mock_analyzer, mock_servo, click_steps):
        _vision_reply(mock_analyzer, '{"visible": false, "confidence": 0.9}')
        engine._wait_for_confirmation = MagicMock(return_value=None)

        result = engine.execute(click_steps, autonomy_level="supervised")

        assert result.success is False
        engine._wait_for_confirmation.assert_called_once()
        mock_servo.click_target.assert_not_called()


class TestConfidenceEstimate:
    STEP = {"step_index": 0, "action_type": "click", "target_description": "the Save button"}

    @pytest.mark.parametrize("reply, expected", [
        ('{"visible": false, "confidence": 0.9}', 0.0),
        ("The Save button is not visible on this screen.", 0.0),
        ("Yes, it is visible.", 0.0),
        ('```json\n{"visible": true, "confidence": 0.85}\n```', 0.85),
        ('Here you go: {"visible": "true", "confidence": "0.7"}', 0.7),
        ('{"visible": "false", "confidence": 0.9}', 0.0),
        ('{"visible": true, "confidence": 0.9,}', 0.9),
        ('{"confidence": 0.9}', 0.0),
        ('{"visible": true}', 0.0),
        ('{"visible": true, "confidence": "high"}', 0.0),
        ('{"visible": true, "confidence": 7}', 1.0),
        ("asdf ;; <garbage>", 0.0),
        ("", 0.0),
    ])
    def test_reply_is_read_fail_closed(self, engine, mock_analyzer, reply, expected):
        _vision_reply(mock_analyzer, reply)

        assert engine._estimate_confidence(self.STEP) == pytest.approx(expected)

    def test_analyzer_failure_scores_zero(self, engine, mock_analyzer):
        _vision_reply(mock_analyzer, "", success=False)

        assert engine._estimate_confidence(self.STEP) == 0.0

    def test_capture_error_scores_zero(self, engine, mock_screen):
        mock_screen.capture.side_effect = RuntimeError("no display")

        assert engine._estimate_confidence(self.STEP) == 0.0


class TestTrainerConfirmReachesTheReplay:
    """A Confirm from the Trainer must reach the event the replay waits on."""

    def test_confirm_goes_to_the_running_replay(self, engine):
        from backend.services.agent_control_service import AgentControlService
        svc = AgentControlService.__new__(AgentControlService)
        svc._apprentice_engine = engine
        waiter = {}

        def wait():
            waiter["got"] = engine._wait_for_confirmation(timeout=5)

        t = threading.Thread(target=wait)
        t.start()
        import time
        time.sleep(0.2)
        assert svc.confirm_apprentice_step({"step_index": 0}) is True
        t.join(timeout=5)
        assert waiter["got"] == {"step_index": 0}

    def test_confirm_with_no_replay_running_says_so(self):
        from backend.services.agent_control_service import AgentControlService
        svc = AgentControlService.__new__(AgentControlService)
        svc._apprentice_engine = None
        assert svc.confirm_apprentice_step({"step_index": 0}) is False
