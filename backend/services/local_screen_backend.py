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

    def _crop_to_active_monitor(self, image: Image.Image, cursor_pos: Tuple[int, int]) -> Tuple[Image.Image, Tuple[int, int]]:
        """Crop a multi-monitor screenshot to the monitor containing the cursor.

        Detects multi-monitor by aspect ratio: if wider than 21:9 (2.4:1),
        it's likely side-by-side monitors. pyautogui.size() returns the full
        virtual screen on multi-monitor setups, so we can't rely on it.
        """
        w, h = image.size
        aspect = w / h if h > 0 else 1

        # Single monitor if aspect ratio is reasonable (up to ultrawide 21:9 = 2.33)
        if aspect <= 2.5:
            return image, cursor_pos

        # Estimate number of monitors assuming ~16:9 each
        single_monitor_w = round(h * 16 / 9)
        num_monitors = max(1, round(w / single_monitor_w))
        if num_monitors <= 1:
            return image, cursor_pos

        monitor_w = w // num_monitors
        monitor_idx = min(cursor_pos[0] // monitor_w, num_monitors - 1)
        x_offset = monitor_idx * monitor_w

        cropped = image.crop((x_offset, 0, x_offset + monitor_w, h))
        adjusted_cursor = (cursor_pos[0] - x_offset, cursor_pos[1])
        logger.debug(f"Cropped to monitor {monitor_idx + 1}/{num_monitors}: {cropped.size}")
        return cropped, adjusted_cursor

    def capture(self) -> Tuple[Image.Image, Tuple[int, int]]:
        """Capture screenshot and return with cursor position.

        Tries mss first (fast, X11), falls back to pyautogui (uses scrot/Pillow,
        works on Wayland with tkinter), then gnome-screenshot as last resort.
        """
        image = None

        # Try PipeWire first (VNC-style framebuffer capture, silent, Wayland-native)
        try:
            from backend.services.pipewire_capture import get_pipewire_capture
            pw = get_pipewire_capture()
            if pw.is_running:
                frame = pw.grab()
                if frame is not None:
                    image = frame
        except Exception as e:
            logger.debug(f"PipeWire capture unavailable: {e}")

        # Fallback: mss (fastest, but fails on Wayland)
        if image is None:
            try:
                with mss.mss() as sct:
                    monitor = sct.monitors[1]
                    sct_img = sct.grab(monitor)
                    image = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
            except Exception:
                pass

        # Last resort: pyautogui (Pillow ImageGrab → gnome-screenshot on Wayland = flash)
        if image is None:
            try:
                image = pyautogui.screenshot()
                logger.warning("Using pyautogui screenshot fallback (may flash on Wayland)")
            except Exception:
                pass

        if image is None:
            raise RuntimeError("All screenshot methods failed (pipewire, mss, pyautogui)")

        cursor_pos = pyautogui.position()
        cursor_xy = (cursor_pos[0], cursor_pos[1])

        # Crop to the monitor the cursor is on (avoids sending both monitors to vision model)
        image, cursor_xy = self._crop_to_active_monitor(image, cursor_xy)
        return image, cursor_xy

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
