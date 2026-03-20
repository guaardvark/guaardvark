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
    @patch("backend.services.local_screen_backend.pyautogui")
    def test_capture_returns_image_and_cursor(self, mock_pyautogui, mock_mss):
        from backend.services.local_screen_backend import LocalScreenBackend
        from PIL import Image

        # Mock mss screenshot
        mock_monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        mock_sct_instance = MagicMock()
        mock_sct_instance.monitors = [{}, mock_monitor]
        mock_sct_instance.grab.return_value = MagicMock()
        mock_sct_instance.grab.return_value.size = (1920, 1080)
        mock_sct_instance.grab.return_value.rgb = b'\x00' * (1920 * 1080 * 3)
        mock_mss.mss.return_value.__enter__ = MagicMock(return_value=mock_sct_instance)
        mock_mss.mss.return_value.__exit__ = MagicMock(return_value=False)

        # Mock cursor position
        mock_pyautogui.position.return_value = (500, 300)

        backend = LocalScreenBackend()
        image, cursor_pos = backend.capture()

        self.assertIsInstance(image, Image.Image)
        self.assertEqual(cursor_pos, (500, 300))

    @patch("backend.services.local_screen_backend.pyautogui")
    def test_click_calls_pyautogui(self, mock_pyautogui):
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        result = backend.click(400, 300, button="left", clicks=1)
        mock_pyautogui.click.assert_called_once_with(x=400, y=300, button="left", clicks=1)
        self.assertTrue(result["success"])

    @patch("backend.services.local_screen_backend.pyautogui")
    def test_type_text_calls_pyautogui(self, mock_pyautogui):
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        result = backend.type_text("hello world", interval=0.05)
        mock_pyautogui.write.assert_called_once_with("hello world", interval=0.05)
        self.assertTrue(result["success"])

    @patch("backend.services.local_screen_backend.pyautogui")
    def test_hotkey_calls_pyautogui(self, mock_pyautogui):
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        result = backend.hotkey("ctrl", "c")
        mock_pyautogui.hotkey.assert_called_once_with("ctrl", "c")
        self.assertTrue(result["success"])

    @patch("backend.services.local_screen_backend.pyautogui")
    def test_scroll_calls_pyautogui(self, mock_pyautogui):
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        result = backend.scroll(400, 300, amount=-3)
        mock_pyautogui.scroll.assert_called_once_with(-3, x=400, y=300)
        self.assertTrue(result["success"])

    @patch("backend.services.local_screen_backend.pyautogui")
    def test_screen_size(self, mock_pyautogui):
        from backend.services.local_screen_backend import LocalScreenBackend
        mock_pyautogui.size.return_value = (1920, 1080)
        backend = LocalScreenBackend()
        self.assertEqual(backend.screen_size(), (1920, 1080))

    @patch("backend.services.local_screen_backend.pyautogui")
    def test_cursor_position(self, mock_pyautogui):
        from backend.services.local_screen_backend import LocalScreenBackend
        mock_pyautogui.position.return_value = (123, 456)
        backend = LocalScreenBackend()
        self.assertEqual(backend.cursor_position(), (123, 456))

    @patch("backend.services.local_screen_backend.pyautogui")
    def test_move_calls_pyautogui(self, mock_pyautogui):
        from backend.services.local_screen_backend import LocalScreenBackend
        backend = LocalScreenBackend()
        result = backend.move(800, 600)
        mock_pyautogui.moveTo.assert_called_once_with(800, 600)
        self.assertTrue(result["success"])


if __name__ == "__main__":
    unittest.main()
