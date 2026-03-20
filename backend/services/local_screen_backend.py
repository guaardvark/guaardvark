#!/usr/bin/env python3
"""
Local Screen Backend — pyautogui/mss implementation of ScreenInterface.

Uses mss for fast screenshots (Wayland-compatible) and pyautogui for
mouse/keyboard input injection.
"""

import logging
import os
import sys
import types
from typing import Any, Dict, Tuple

import mss
from PIL import Image

from backend.services.screen_interface import ScreenInterface

logger = logging.getLogger(__name__)

# Import pyautogui, gracefully handling environments where tkinter is absent
# (e.g., headless CI or test runs). In test mode everything is mocked anyway.
try:
    import pyautogui
except SystemExit:
    # mouseinfo calls sys.exit() when tkinter is unavailable on Linux.
    # Provide a minimal stub so the module loads; tests patch this name.
    pyautogui = types.ModuleType("pyautogui")  # type: ignore[assignment]
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.0

# Only configure pyautogui if not in test mode
if os.environ.get("GUAARDVARK_MODE") != "test":
    pyautogui.FAILSAFE = False  # We have our own kill switch
    pyautogui.PAUSE = 0.1  # 100ms pause between actions


class LocalScreenBackend(ScreenInterface):
    """Screen control via pyautogui (input) and mss (capture) on the local machine."""

    def capture(self) -> Tuple[Image.Image, Tuple[int, int]]:
        """Capture screenshot using mss and return with cursor position."""
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # Primary monitor
            sct_img = sct.grab(monitor)
            image = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
        cursor_pos = pyautogui.position()
        return image, (cursor_pos[0], cursor_pos[1])

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> Dict[str, Any]:
        """Click at coordinates using pyautogui."""
        try:
            pyautogui.click(x=x, y=y, button=button, clicks=clicks)
            return {"success": True, "action": "click", "x": x, "y": y}
        except Exception as e:
            logger.error(f"Click failed at ({x}, {y}): {e}")
            return {"success": False, "error": str(e)}

    def move(self, x: int, y: int) -> Dict[str, Any]:
        """Move cursor to coordinates."""
        try:
            pyautogui.moveTo(x, y)
            return {"success": True, "action": "move", "x": x, "y": y}
        except Exception as e:
            logger.error(f"Move failed to ({x}, {y}): {e}")
            return {"success": False, "error": str(e)}

    def type_text(self, text: str, interval: float = 0.05) -> Dict[str, Any]:
        """Type text using pyautogui."""
        try:
            pyautogui.write(text, interval=interval)
            return {"success": True, "action": "type", "length": len(text)}
        except Exception as e:
            logger.error(f"Type failed: {e}")
            return {"success": False, "error": str(e)}

    def hotkey(self, *keys: str) -> Dict[str, Any]:
        """Press keyboard shortcut."""
        try:
            pyautogui.hotkey(*keys)
            return {"success": True, "action": "hotkey", "keys": list(keys)}
        except Exception as e:
            logger.error(f"Hotkey failed {keys}: {e}")
            return {"success": False, "error": str(e)}

    def scroll(self, x: int, y: int, amount: int = -3) -> Dict[str, Any]:
        """Scroll wheel at position."""
        try:
            pyautogui.scroll(amount, x=x, y=y)
            return {"success": True, "action": "scroll", "amount": amount}
        except Exception as e:
            logger.error(f"Scroll failed: {e}")
            return {"success": False, "error": str(e)}

    def screen_size(self) -> Tuple[int, int]:
        """Return screen dimensions."""
        return pyautogui.size()

    def cursor_position(self) -> Tuple[int, int]:
        """Return current cursor position."""
        pos = pyautogui.position()
        return (pos[0], pos[1])
