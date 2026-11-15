"""Tests for DemoRecorder service."""
import pytest
import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch
from PIL import Image

from backend.services.demo_recorder import DemoRecorder, InputEvent, XinputBlockParser


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
            recorder._append_step(recorder._process_event(evt))
        steps = recorder.get_steps()
        assert len(steps) == 3
        assert steps[0]["step_index"] == 0
        assert steps[2]["step_index"] == 2

    def test_detect_mistake_rapid_reclick(self, recorder):
        evt1 = InputEvent(event_type="click", x=100, y=200, button=1, timestamp=1000.0)
        evt2 = InputEvent(event_type="click", x=300, y=400, button=1, timestamp=1000.3)
        recorder._append_step(recorder._process_event(evt1))
        recorder._append_step(recorder._process_event(evt2))
        steps = recorder.get_steps()
        assert steps[0]["is_potential_mistake"] is True

    def test_typed_text_gets_a_precondition(self, recorder):
        events = [InputEvent(event_type="key", key=c, timestamp=1000.0) for c in "ab"]
        step = recorder._collapse_keystrokes(events)
        assert step["precondition"] == "a blue Login button centered on the page"

    def test_keys_that_type_nothing_make_no_step(self, recorder):
        assert recorder._collapse_keystrokes([InputEvent(event_type="key", key="", timestamp=1.0)]) is None

    def test_target_and_precondition_describe_the_screen_before_the_click(
            self, recorder, mock_screen, mock_analyzer):
        before = Image.new("RGB", (64, 64), color="white")
        after = Image.new("RGB", (64, 64), color="black")
        recorder._last_screenshot = before
        mock_screen.capture.side_effect = None
        mock_screen.capture.return_value = (after, (0, 0))
        recorder._process_event(InputEvent(event_type="click", x=1, y=2, button=1, timestamp=1.0))
        target_img, precondition_img = [c.args[0] for c in mock_analyzer.analyze.call_args_list]
        assert precondition_img is before
        # The target is a close-up cut from the before screen, away from the ring.
        assert target_img.getpixel((target_img.width - 1, target_img.height - 1)) == (255, 255, 255)

    def test_click_target_is_shown_as_a_close_up_without_coordinates(self, recorder, mock_analyzer):
        recorder._last_screenshot = Image.new("RGB", (1024, 1024), color="white")
        recorder._process_event(InputEvent(event_type="click", x=500, y=500, button=1, timestamp=1.0))
        target_call = mock_analyzer.analyze.call_args_list[0]
        assert target_call.args[0].size == (340, 340)
        assert "500" not in target_call.kwargs["prompt"]


# One xdotool click, Ctrl+L, Shift+e and Return as `xinput test-xi2 --root`
# prints them: each press arrives as a Raw* event plus one copy from the slave
# device and one from the master device.
XI2_SAMPLE = """\
EVENT type 15 (RawButtonPress)
    device: 2 (4)
    time:   35002541
    detail: 1
    flags: 
    valuators:

EVENT type 4 (ButtonPress)
    device: 4 (4)
    time: 35002541
    detail: 1
    flags: 
    root: 100.00/200.00
    event: 100.00/200.00
    buttons:
    modifiers: locked 0 latched 0 base 0 effective: 0
    group: locked 0 latched 0 base 0 effective: 0
    valuators:
    windows: root 0x3a7 event 0x3a7 child 0x0
EVENT type 4 (ButtonPress)
    device: 2 (4)
    time: 35002541
    detail: 1
    flags: 
    root: 100.00/200.00
    event: 100.00/200.00
    buttons:
    modifiers: locked 0 latched 0 base 0 effective: 0
    group: locked 0 latched 0 base 0 effective: 0
    valuators:
    windows: root 0x3a7 event 0x3a7 child 0x0
EVENT type 5 (ButtonRelease)
    device: 2 (4)
    time: 35002541
    detail: 1
    flags: 
    root: 100.00/200.00
    event: 100.00/200.00
    buttons: 1
    modifiers: locked 0 latched 0 base 0 effective: 0
    group: locked 0 latched 0 base 0 effective: 0
    valuators:
    windows: root 0x3a7 event 0x3a7 child 0x0
"""


def _key_block(time_ms, keycode, mods, device=3, source=5):
    return (
        f"EVENT type 2 (KeyPress)\n    device: {device} ({source})\n    time: {time_ms}\n"
        f"    detail: {keycode}\n    flags: \n    root: 0.00/0.00\n    event: 0.00/0.00\n"
        f"    buttons:\n    modifiers: locked 0 latched 0 base {mods} effective: {mods}\n"
        f"    group: locked 0 latched 0 base 0 effective: 0\n    valuators:\n"
        f"    windows: root 0x3a7 event 0x3a7 child 0x0\n"
    )


def _key_press(time_ms, keycode, mods="0"):
    # Raw copy, slave copy, master copy.
    raw = f"EVENT type 13 (RawKeyPress)\n    device: 3 (5)\n    time:   {time_ms}\n    detail: {keycode}\n    valuators:\n\n"
    return raw + _key_block(time_ms, keycode, mods, device=5) + _key_block(time_ms, keycode, mods)


class TestXinputDecoding:
    @pytest.fixture
    def recorder(self):
        screen = MagicMock()
        screen.capture.return_value = (Image.new("RGB", (8, 8)), (0, 0))
        with tempfile.TemporaryDirectory() as tmpdir:
            rec = DemoRecorder(screen=screen, analyzer=MagicMock(), screenshots_dir=tmpdir)
            rec._keymap = {
                26: ["e", "E", "e", "E"], 37: ["Control_L", "NoSymbol", "Control_L"],
                46: ["l", "L", "l", "L"], 50: ["Shift_L", "NoSymbol", "Shift_L"],
                36: ["Return", "NoSymbol", "Return"], 60: ["period", "greater"],
                22: ["BackSpace", "BackSpace"],
            }
            yield rec

    def _events(self, recorder, text):
        parser = XinputBlockParser()
        blocks = [b for b in (parser.feed(line) for line in text.splitlines(True)) if b]
        tail = parser.flush()
        if tail:
            blocks.append(tail)
        return [e for e in (recorder._event_from_block(b, 1.0) for b in blocks) if e]

    def test_parser_reads_detail_root_time_and_modifiers(self):
        parser = XinputBlockParser()
        blocks = [b for b in (parser.feed(line) for line in _key_block(7, 46, "0x4").splitlines(True)) if b]
        assert blocks == [{"name": "KeyPress", "device": 3, "source": 5, "time": 7,
                           "detail": 46, "root": (0, 0), "mods": 4}]

    def test_one_click_is_one_event_at_its_root_position(self, recorder):
        events = self._events(recorder, XI2_SAMPLE)
        assert [(e.event_type, e.x, e.y, e.button) for e in events] == [("click", 100, 200, 1)]

    def test_keys_translate_to_text_and_hotkeys(self, recorder):
        stream = (
            _key_press(10, 37, "0") + _key_press(11, 46, "0x4")      # Ctrl+L
            + _key_press(12, 50, "0") + _key_press(13, 26, "0x1")    # Shift+e
            + _key_press(14, 60, "0")                                # .
            + _key_press(15, 36, "0")                                # Return
        )
        events = self._events(recorder, stream)
        assert [(e.event_type, e.key) for e in events] == [
            ("hotkey", "ctrl+l"), ("key", "E"), ("key", "."), ("hotkey", "Return"),
        ]

    def test_wheel_is_a_scroll(self, recorder):
        wheel = XI2_SAMPLE.replace("detail: 1", "detail: 5")
        events = self._events(recorder, wheel)
        assert [(e.event_type, e.scroll_amount) for e in events] == [("scroll", -1)]

    def test_listener_events_become_steps(self, recorder):
        for evt in [
            InputEvent(event_type="hotkey", key="ctrl+l", timestamp=1.0),
            InputEvent(event_type="key", key="e", timestamp=1.1),
            InputEvent(event_type="key", key="x", timestamp=1.2),
            InputEvent(event_type="key", key="z", timestamp=1.3),
            InputEvent(event_type="hotkey", key="BackSpace", timestamp=1.4),
            InputEvent(event_type="hotkey", key="Return", timestamp=1.5),
        ]:
            recorder._handle_listener_event(evt)
        recorder._flush_pending()
        steps = recorder.get_steps()
        assert [(s["action_type"], s["text"], s["keys"]) for s in steps] == [
            ("hotkey", None, "ctrl+l"), ("type", "ex", None), ("hotkey", None, "Return"),
        ]
        assert [s["step_index"] for s in steps] == [0, 1, 2]

    def test_injected_input_hides_the_listener_copy(self, recorder):
        assert not recorder._injecting
        with recorder.injected_input():
            assert recorder._injecting == 1
        assert recorder._injecting == 0
        assert recorder._suppress_until > 0
