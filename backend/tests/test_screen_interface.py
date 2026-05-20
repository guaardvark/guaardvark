#!/usr/bin/env python3

import os
import sys
import unittest
from unittest.mock import patch, MagicMock, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"
os.environ["GUAARDVARK_GUI_AUTOMATION"] = "false"


class TestScreenInterface(unittest.TestCase):

    def test_screen_interface_is_abstract(self):
        from backend.services.screen_interface import ScreenInterface
        with self.assertRaises(TypeError):
            ScreenInterface()

    def test_screen_interface_defines_methods(self):
        from backend.services.screen_interface import ScreenInterface
        self.assertTrue(hasattr(ScreenInterface, 'capture'))
        self.assertTrue(hasattr(ScreenInterface, 'click'))
        self.assertTrue(hasattr(ScreenInterface, 'move'))
        self.assertTrue(hasattr(ScreenInterface, 'type_text'))
        self.assertTrue(hasattr(ScreenInterface, 'hotkey'))
        self.assertTrue(hasattr(ScreenInterface, 'scroll'))
        self.assertTrue(hasattr(ScreenInterface, 'screen_size'))
        self.assertTrue(hasattr(ScreenInterface, 'cursor_position'))


class TestLocalBackend(unittest.TestCase):

    @patch("backend.services.local_screen_backend.mss")
    def test_capture_returns_image_and_cursor(self, mock_mss):
        from backend.services.local_screen_backend import LocalScreenBackend
        from PIL import Image

        # Mock mss screenshot
        mock_monitor = {"left": 0, "top": 0, "width": 1024, "height": 1024}
        mock_sct_instance = MagicMock()
        mock_sct_instance.monitors = [{}, mock_monitor]
        mock_sct_instance.grab.return_value = MagicMock()
        mock_sct_instance.grab.return_value.size = (1024, 1024)
        mock_sct_instance.grab.return_value.rgb = b'\x00' * (1024 * 1024 * 3)
        mock_mss.mss.return_value.__enter__ = MagicMock(return_value=mock_sct_instance)
        mock_mss.mss.return_value.__exit__ = MagicMock(return_value=False)

        backend = LocalScreenBackend()

        # Mock cursor_position (calls _xdotool internally)
        mock_cursor_result = MagicMock()
        mock_cursor_result.returncode = 0
        mock_cursor_result.stdout = "x:500 y:300 screen:0 window:123\n"

        with patch.object(backend, '_xdotool', return_value=mock_cursor_result):
            image, cursor_pos = backend.capture()

        self.assertIsInstance(image, Image.Image)
        self.assertEqual(cursor_pos, (500, 300))

    def test_click_calls_xdotool(self):
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch.object(backend, '_xdotool', return_value=mock_result) as mock_xdotool:
            result = backend.click(400, 300, button="left", clicks=1)

        self.assertTrue(result["success"])
        self.assertEqual(result["x"], 400)
        self.assertEqual(result["y"], 300)
        # First call should be mousemove
        first_call_args = mock_xdotool.call_args_list[0][0]
        self.assertIn("mousemove", first_call_args)
        self.assertIn("400", first_call_args)
        self.assertIn("300", first_call_args)

    def test_type_text_calls_xdotool(self):
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch.object(backend, '_xdotool', return_value=mock_result):
            with patch.object(backend, '_get_window_id', return_value=""):
                result = backend.type_text("hello world", interval=0.05)

        self.assertTrue(result["success"])
        self.assertEqual(result["length"], len("hello world"))

    def test_type_text_uses_double_dash_for_text_arg(self):
        # xdotool parses leading `-` as a flag. Without `--` before the text
        # arg, anything starting with `-` (markdown bullets, hyphenated leads)
        # gets silently dropped. Pin the separator so a future refactor can't
        # quietly regress.
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch.object(backend, '_xdotool', return_value=mock_result) as mock_xdotool:
            with patch.object(backend, '_get_window_id', return_value=""):
                backend.type_text("- bullet line", interval=0.05)

        call_args = mock_xdotool.call_args[0]
        self.assertIn("--", call_args, "type_text must pass `--` before the text arg")
        # And the text must come AFTER `--`, not before it.
        dash_idx = call_args.index("--")
        self.assertEqual(call_args[dash_idx + 1], "- bullet line")

    def test_double_click_uses_xdotool_repeat(self):
        # Browser dblclick requires both clicks within ~400ms. Sequential
        # click() calls have too much subprocess overhead. xdotool's
        # `--repeat 2 --delay 80` keeps both events inside the window.
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        ok = MagicMock(returncode=0, stdout="")

        with patch.object(backend, '_xdotool', return_value=ok) as mock_x:
            result = backend.double_click(100, 200)

        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "double_click")
        # Must have invoked xdotool click with --repeat 2
        click_calls = [c for c in mock_x.call_args_list if c[0][0] == "click"]
        self.assertEqual(len(click_calls), 1, "should use one --repeat 2 invocation, not two separate clicks")
        args = click_calls[0][0]
        self.assertIn("--repeat", args)
        self.assertEqual(args[args.index("--repeat") + 1], "2")

    def test_triple_click_uses_xdotool_repeat_three(self):
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        ok = MagicMock(returncode=0, stdout="")

        with patch.object(backend, '_xdotool', return_value=ok) as mock_x:
            result = backend.triple_click(100, 200)

        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "triple_click")
        click_calls = [c for c in mock_x.call_args_list if c[0][0] == "click"]
        self.assertEqual(len(click_calls), 1)
        args = click_calls[0][0]
        self.assertEqual(args[args.index("--repeat") + 1], "3")

    def test_drag_uses_mousedown_interpolation_mouseup(self):
        # Drag must press at source, interpolate motion, then release.
        # Smooth motion matters — drag-and-drop UIs that watch the cursor
        # path reject instant teleports.
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        ok = MagicMock(returncode=0, stdout="")

        with patch.object(backend, '_xdotool', return_value=ok) as mock_x:
            result = backend.drag(0, 0, 100, 100, duration_ms=60)

        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "drag")
        # Sequence: mousemove → mousedown → many mousemoves → mouseup
        commands = [c[0][0] for c in mock_x.call_args_list]
        self.assertEqual(commands[0], "mousemove")
        self.assertEqual(commands[1], "mousedown")
        self.assertEqual(commands[-1], "mouseup")
        # At least 2 interpolation steps (duration_ms=60 / 20ms step)
        intermediate_moves = [c for c in commands[2:-1] if c == "mousemove"]
        self.assertGreaterEqual(len(intermediate_moves), 2)

    def test_drag_releases_button_even_when_interpolation_raises(self):
        # If anything goes wrong mid-drag, we MUST mouseup or the X session
        # is wedged with a held button. Belt-and-suspenders.
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()

        call_count = {"n": 0}
        def fake_xdotool(*args):
            call_count["n"] += 1
            # Raise on the 4th call (after mousemove, mousedown, one interp move)
            if call_count["n"] == 4 and args[0] == "mousemove":
                raise RuntimeError("simulated X failure")
            return MagicMock(returncode=0, stdout="")

        with patch.object(backend, '_xdotool', side_effect=fake_xdotool) as mock_x:
            result = backend.drag(0, 0, 100, 100, duration_ms=60)

        # The drag itself failed (not success)
        self.assertFalse(result["success"])
        # But mouseup was still issued — find it in the call history
        commands = [c[0][0] for c in mock_x.call_args_list]
        self.assertIn("mouseup", commands, "mouseup must fire even when interpolation crashes")

    def test_hover_moves_and_settles(self):
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        ok = MagicMock(returncode=0, stdout="")

        with patch.object(backend, '_xdotool', return_value=ok) as mock_x:
            result = backend.hover(100, 200, settle_ms=0)

        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "hover")
        commands = [c[0][0] for c in mock_x.call_args_list]
        self.assertEqual(commands, ["mousemove"], "hover should only mousemove, no click")


class TestSlowEffectClassifier(unittest.TestCase):
    """Pin the verifier-selection keyword matcher. Slow patterns must route
    to the 12s vision verifier; everything else trusts the servo region DPC."""

    def test_slow_patterns_match(self):
        from backend.services.agent_control_service import _looks_like_slow_effect
        for s in (
            "Firefox opens with the homepage",
            "the new page loads",
            "browser launches and navigates to youtube.com",
            "a new tab appears",
            "page navigates to the comments section",
            "the modal appears in the center of the screen",
        ):
            self.assertTrue(_looks_like_slow_effect(s), f"expected slow: {s!r}")

    def test_fast_patterns_do_not_match(self):
        from backend.services.agent_control_service import _looks_like_slow_effect
        for s in (
            "comment now appears in the thread",  # 'appears' alone is too loose; we require 'modal/dialog/window appears'
            "the button turns blue",
            "form field gets the typed text",
            "the upvote count increments by 1",
            "",
        ):
            self.assertFalse(_looks_like_slow_effect(s), f"expected fast: {s!r}")

    def test_hotkey_calls_xdotool(self):
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch.object(backend, '_xdotool', return_value=mock_result) as mock_xdotool:
            with patch.object(backend, '_get_window_id', return_value=""):
                result = backend.hotkey("ctrl", "c")

        self.assertTrue(result["success"])
        self.assertEqual(result["keys"], ["ctrl", "c"])
        call_args = mock_xdotool.call_args[0]
        self.assertIn("key", call_args)
        self.assertIn("ctrl+c", call_args)

    def test_scroll_calls_xdotool(self):
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch.object(backend, '_xdotool', return_value=mock_result):
            result = backend.scroll(400, 300, amount=-3)

        self.assertTrue(result["success"])
        self.assertEqual(result["amount"], -3)

    def test_screen_size(self):
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "1024 1024\n"

        with patch.object(backend, '_xdotool', return_value=mock_result):
            size = backend.screen_size()

        self.assertEqual(size, (1024, 1024))

    def test_screen_size_fallback(self):
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch.object(backend, '_xdotool', return_value=mock_result):
            size = backend.screen_size()

        self.assertEqual(size, (1024, 1024))

    def test_cursor_position(self):
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "x:123 y:456 screen:0 window:789\n"

        with patch.object(backend, '_xdotool', return_value=mock_result):
            pos = backend.cursor_position()

        self.assertEqual(pos, (123, 456))

    def test_move_calls_xdotool(self):
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch.object(backend, '_xdotool', return_value=mock_result) as mock_xdotool:
            result = backend.move(800, 600)

        self.assertTrue(result["success"])
        self.assertEqual(result["x"], 800)
        self.assertEqual(result["y"], 600)
        call_args = mock_xdotool.call_args[0]
        self.assertIn("mousemove", call_args)
        self.assertIn("800", call_args)
        self.assertIn("600", call_args)


if __name__ == "__main__":
    unittest.main()
