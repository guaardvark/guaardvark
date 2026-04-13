#!/usr/bin/env python3
"""
Local Screen Backend — xdotool/mss implementation of ScreenInterface.

Uses mss for fast screenshots and xdotool for mouse/keyboard input injection.
All operations target a specific X11 display (default :99 virtual display)
so they never leak to the user's real screen.
"""

import logging
import os
import subprocess
from typing import Any, Dict, Tuple

import mss
from PIL import Image

from backend.services.screen_interface import ScreenInterface

logger = logging.getLogger(__name__)


class LocalScreenBackend(ScreenInterface):
    """Screen control via xdotool (input) and mss (capture) targeting a virtual display."""

    def __init__(self, display: str = None):
        self.display = display or os.environ.get("GUAARDVARK_AGENT_DISPLAY", ":99")
        self._env = {**os.environ, "DISPLAY": self.display}
        self._window_id = None

    def _get_window_id(self) -> str:
        """Get the active window ID on the target display, cached after first call."""
        if self._window_id:
            # Verify window still exists
            try:
                r = subprocess.run(
                    ["xdotool", "getwindowname", self._window_id],
                    env=self._env, capture_output=True, text=True, timeout=5
                )
                if r.returncode == 0:
                    return self._window_id
            except Exception:
                pass
            self._window_id = None

        # Find the Firefox window on the virtual display
        try:
            r = subprocess.run(
                ["xdotool", "search", "--name", "Mozilla Firefox"],
                env=self._env, capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0 and r.stdout.strip():
                # Take the last window (most recently mapped)
                windows = r.stdout.strip().split("\n")
                self._window_id = windows[-1]
                logger.info(f"Found Firefox window {self._window_id} on {self.display}")
                return self._window_id
        except Exception as e:
            logger.warning(f"xdotool search failed: {e}")

        # Fall back to active window
        try:
            r = subprocess.run(
                ["xdotool", "getactivewindow"],
                env=self._env, capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                self._window_id = r.stdout.strip()
                return self._window_id
        except Exception as e:
            logger.warning(f"xdotool getactivewindow failed: {e}")

        return ""

    def _xdotool(self, *args) -> subprocess.CompletedProcess:
        """Run xdotool command targeting the virtual display."""
        cmd = ["xdotool"] + list(args)
        logger.debug(f"xdotool: {' '.join(cmd)}")
        return subprocess.run(cmd, env=self._env, capture_output=True, text=True, timeout=10)

    def capture(self) -> Tuple[Image.Image, Tuple[int, int]]:
        """Capture screenshot from the virtual display via mss."""
        # mss respects the DISPLAY env var when constructed with it
        env_backup = os.environ.get("DISPLAY")
        os.environ["DISPLAY"] = self.display
        try:
            with mss.mss() as sct:
                if len(sct.monitors) < 2:
                    raise IndexError(
                        f"No monitors found on display {self.display} — is Xvfb running?"
                    )
                monitor = sct.monitors[1]
                sct_img = sct.grab(monitor)
                if sct_img.size.width == 0 or sct_img.size.height == 0:
                    raise RuntimeError(
                        f"Captured empty frame ({sct_img.size.width}x{sct_img.size.height})"
                    )
                image = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
        finally:
            if env_backup:
                os.environ["DISPLAY"] = env_backup
            elif "DISPLAY" in os.environ:
                del os.environ["DISPLAY"]

        cursor_pos = self.cursor_position()
        return image, cursor_pos

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> Dict[str, Any]:
        """Click at coordinates on the virtual display."""
        try:
            # Move mouse to position
            r = self._xdotool("mousemove", "--screen", "0", str(x), str(y))
            if r.returncode != 0:
                err = r.stderr.strip() or f"mousemove failed (rc={r.returncode})"
                logger.error(f"Click move failed at ({x}, {y}): {err}")
                return {"success": False, "error": err}

            # Map button name to xdotool button number
            btn_map = {"left": "1", "middle": "2", "right": "3"}
            btn = btn_map.get(button, "1")

            for _ in range(clicks):
                r = self._xdotool("click", btn)
                if r.returncode != 0:
                    err = r.stderr.strip() or f"click failed (rc={r.returncode})"
                    logger.error(f"Click failed at ({x}, {y}): {err}")
                    return {"success": False, "error": err}

            return {"success": True, "action": "click", "x": x, "y": y}
        except Exception as e:
            logger.error(f"Click failed at ({x}, {y}): {e}")
            return {"success": False, "error": str(e)}

    def move(self, x: int, y: int) -> Dict[str, Any]:
        """Move cursor on the virtual display."""
        try:
            self._xdotool("mousemove", "--screen", "0", str(x), str(y))
            return {"success": True, "action": "move", "x": x, "y": y}
        except Exception as e:
            logger.error(f"Move failed to ({x}, {y}): {e}")
            return {"success": False, "error": str(e)}

    def type_text(self, text: str, interval: float = 0.08) -> Dict[str, Any]:
        """Type text on the virtual display using xdotool."""
        try:
            wid = self._get_window_id()
            delay_ms = str(int(interval * 1000))
            if wid:
                r = self._xdotool("type", "--window", wid, "--clearmodifiers", "--delay", delay_ms, text)
            else:
                r = self._xdotool("type", "--clearmodifiers", "--delay", delay_ms, text)
            if r.returncode != 0:
                err = r.stderr.strip() or f"xdotool type exited with code {r.returncode}"
                logger.error(f"Type failed (rc={r.returncode}): {err}")
                return {"success": False, "error": err}
            return {"success": True, "action": "type", "length": len(text)}
        except Exception as e:
            logger.error(f"Type failed: {e}")
            return {"success": False, "error": str(e)}

    # xdotool is picky about key names — LLMs often get them wrong
    _KEY_MAP = {
        "enter": "Return", "return": "Return",
        "esc": "Escape", "escape": "Escape",
        "backspace": "BackSpace", "back": "BackSpace",
        "delete": "Delete", "del": "Delete",
        "tab": "Tab",
        "space": "space",
        "up": "Up", "down": "Down", "left": "Left", "right": "Right",
        "pageup": "Page_Up", "page_up": "Page_Up",
        "pagedown": "Page_Down", "page_down": "Page_Down",
        "home": "Home", "end": "End",
    }

    def hotkey(self, *keys: str) -> Dict[str, Any]:
        """Press keyboard shortcut on the virtual display."""
        try:
            # Normalize key names — Gemma4 says "enter", xdotool wants "Return"
            mapped = [self._KEY_MAP.get(k.lower(), k) for k in keys]
            combo = "+".join(mapped)
            # Single keys (Return, Escape, Tab, etc.) go to the focused window
            # without --window targeting. Firefox has multiple internal X windows
            # and --window can misfire, sending the key to a background frame
            # instead of the address bar or active input.
            is_single_key = len(keys) == 1 and keys[0] not in ("ctrl", "alt", "shift", "super")
            if is_single_key:
                r = self._xdotool("key", "--clearmodifiers", combo)
                logger.debug(f"hotkey (focused): {combo}")
            else:
                wid = self._get_window_id()
                if wid:
                    r = self._xdotool("key", "--window", wid, combo)
                else:
                    r = self._xdotool("key", combo)
                logger.debug(f"hotkey (window={wid or 'none'}): {combo}")
            if r.returncode != 0:
                err = r.stderr.strip() or f"xdotool key exited with code {r.returncode}"
                logger.error(f"Hotkey failed (rc={r.returncode}): {err}")
                return {"success": False, "error": err}
            return {"success": True, "action": "hotkey", "keys": list(keys)}
        except Exception as e:
            logger.error(f"Hotkey failed {keys}: {e}")
            return {"success": False, "error": str(e)}

    def scroll(self, x: int, y: int, amount: int = -3) -> Dict[str, Any]:
        """Scroll at position on the virtual display."""
        try:
            # Move to position first
            self._xdotool("mousemove", "--screen", "0", str(x), str(y))

            # xdotool: button 4 = scroll up, button 5 = scroll down
            if amount < 0:
                btn, count = "5", abs(amount)  # scroll down
            else:
                btn, count = "4", abs(amount)  # scroll up

            for _ in range(count):
                self._xdotool("click", btn)

            return {"success": True, "action": "scroll", "amount": amount}
        except Exception as e:
            logger.error(f"Scroll failed: {e}")
            return {"success": False, "error": str(e)}

    def screen_size(self) -> Tuple[int, int]:
        """Return virtual display dimensions."""
        try:
            r = self._xdotool("getdisplaygeometry")
            if r.returncode == 0:
                parts = r.stdout.strip().split()
                return (int(parts[0]), int(parts[1]))
        except Exception:
            pass
        return (1024, 1024)  # Default to what start_agent_display.sh creates

    def cursor_position(self) -> Tuple[int, int]:
        """Return cursor position on the virtual display."""
        try:
            r = self._xdotool("getmouselocation")
            if r.returncode == 0:
                # Output: x:123 y:456 screen:0 window:789
                parts = r.stdout.strip().split()
                x = int(parts[0].split(":")[1])
                y = int(parts[1].split(":")[1])
                return (x, y)
        except Exception:
            pass
        return (0, 0)
