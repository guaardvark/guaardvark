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
        mock_monitor = {"left": 0, "top": 0, "width": 1280, "height": 720}
        mock_sct_instance = MagicMock()
        mock_sct_instance.monitors = [{}, mock_monitor]
        mock_sct_instance.grab.return_value = MagicMock()
        mock_sct_instance.grab.return_value.size = (1280, 720)
        mock_sct_instance.grab.return_value.rgb = b'\x00' * (1280 * 720 * 3)
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
        mock_result.stdout = "1280 720\n"

        with patch.object(backend, '_xdotool', return_value=mock_result):
            size = backend.screen_size()

        self.assertEqual(size, (1280, 720))

    def test_screen_size_fallback(self):
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch.object(backend, '_xdotool', return_value=mock_result):
            size = backend.screen_size()

        self.assertEqual(size, (1280, 720))

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
