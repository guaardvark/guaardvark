"""Tests for DemoRecorder service."""
import pytest
import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch
from PIL import Image

from backend.services.demo_recorder import DemoRecorder, InputEvent


class TestInputEvent:
    def test_click_event(self):
        evt = InputEvent(event_type="click", x=640, y=400, button=1, timestamp=1000.0)
        assert evt.event_type == "click"
        assert evt.x == 640
        assert evt.y == 400

    def test_key_event(self):
        evt = InputEvent(event_type="key", key="a", timestamp=1000.0)
        assert evt.key == "a"

    def test_hotkey_event(self):
        evt = InputEvent(event_type="hotkey", key="ctrl+l", timestamp=1000.0)
        assert evt.key == "ctrl+l"


class TestDemoRecorder:
    @pytest.fixture
    def mock_screen(self):
        screen = MagicMock()
        img = Image.new("RGB", (1024, 1024), color="white")
        screen.capture.return_value = (img, (640, 360))
        screen.screen_size.return_value = (1024, 1024)
        return screen

    @pytest.fixture
    def mock_analyzer(self):
        analyzer = MagicMock()
        result = MagicMock()
        result.description = "a blue Login button centered on the page"
        result.success = True
        analyzer.analyze.return_value = result
        return analyzer

    @pytest.fixture
    def recorder(self, mock_screen, mock_analyzer):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec = DemoRecorder(
                screen=mock_screen,
                analyzer=mock_analyzer,
                screenshots_dir=tmpdir,
            )
            yield rec

    def test_process_click_event(self, recorder, mock_analyzer):
        evt = InputEvent(event_type="click", x=640, y=400, button=1, timestamp=1000.0)
        step = recorder._process_event(evt)
        assert step is not None
        assert step["action_type"] == "click"
        assert step["coordinates_x"] == 640
        assert step["coordinates_y"] == 400
        assert step["target_description"] == "a blue Login button centered on the page"
        mock_analyzer.analyze.assert_called()

    def test_process_key_events_collapsed(self, recorder):
        events = [
            InputEvent(event_type="key", key="h", timestamp=1000.0),
            InputEvent(event_type="key", key="e", timestamp=1000.05),
            InputEvent(event_type="key", key="l", timestamp=1000.10),
            InputEvent(event_type="key", key="l", timestamp=1000.15),
            InputEvent(event_type="key", key="o", timestamp=1000.20),
        ]
        step = recorder._collapse_keystrokes(events)
        assert step["action_type"] == "type"
        assert step["text"] == "hello"

    def test_process_hotkey_event(self, recorder):
        evt = InputEvent(event_type="hotkey", key="ctrl+l", timestamp=1000.0)
        step = recorder._process_event(evt)
        assert step is not None
        assert step["action_type"] == "hotkey"
        assert step["keys"] == "ctrl+l"

    def test_screenshot_saved(self, recorder):
        evt = InputEvent(event_type="click", x=100, y=200, button=1, timestamp=1000.0)
        step = recorder._process_event(evt)
        assert step["screenshot_before"] is not None
        assert Path(step["screenshot_before"]).suffix == ".jpg"

    def test_screen_settle_detection(self, recorder, mock_screen):
        img1 = Image.new("RGB", (1024, 1024), color="white")
        img2 = Image.new("RGB", (1024, 1024), color="blue")
        mock_screen.capture.side_effect = [
            (img1, (640, 360)),  # before (initial)
            (img2, (640, 360)),  # settle check 1 (changed)
            (img2, (640, 360)),  # settle check 2 (same = settled)
        ]
        evt = InputEvent(event_type="click", x=640, y=400, button=1, timestamp=1000.0)
        step = recorder._process_event(evt)
        assert step["screenshot_after"] is not None

    def test_get_steps_returns_ordered_list(self, recorder):
        for i in range(3):
            evt = InputEvent(event_type="click", x=100 * i, y=200, button=1, timestamp=1000.0 + i)
            recorder._process_event(evt)
        steps = recorder.get_steps()
        assert len(steps) == 3
        assert steps[0]["step_index"] == 0
        assert steps[2]["step_index"] == 2

    def test_detect_mistake_rapid_reclick(self, recorder):
        evt1 = InputEvent(event_type="click", x=100, y=200, button=1, timestamp=1000.0)
        evt2 = InputEvent(event_type="click", x=300, y=400, button=1, timestamp=1000.3)
        recorder._process_event(evt1)
        recorder._process_event(evt2)
        steps = recorder.get_steps()
        assert steps[0]["is_potential_mistake"] is True
