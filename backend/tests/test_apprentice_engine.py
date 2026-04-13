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


class TestStepExecution:
    def test_execute_click_step(self, engine, demo_steps, mock_servo):
        result = engine._execute_step(demo_steps[0])
        assert result["success"] is True
        mock_servo.click_target.assert_called_once_with("the Login button centered below the password field")

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
