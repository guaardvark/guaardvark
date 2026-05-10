#!/usr/bin/env python3
"""
Local Screen Backend — xdotool/mss implementation of ScreenInterface.

Uses mss for fast screenshots and xdotool for mouse/keyboard input injection.
All operations target a specific X11 display (default :99 virtual display)
so they never leak to the user's real screen.
"""

import logging
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Tuple

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

    # Chunk size keeps each xdotool type call well under the 10s subprocess timeout.
    # At the default 80ms/char interval, 80 chars ≈ 6.4s — leaves headroom for
    # rich-text editors that buffer keystrokes (Reddit's shreddit-composer being
    # exhibit A: it dropped everything past char ~125 before this fix).
    _TYPE_CHUNK_SIZE = 80

    # Above this length, prefer clipboard paste when xclip is available — types
    # 700 chars in <1s instead of ~56s, and rich editors stop choking on the firehose.
    _PASTE_THRESHOLD = 200

    @staticmethod
    def _chunk_for_typing(text: str, max_size: int) -> List[str]:
        """Split text on newlines and word boundaries, keeping each chunk ≤ max_size."""
        chunks: List[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= max_size:
                chunks.append(remaining)
                break
            # Prefer breaking on a newline within the window
            nl = remaining.rfind("\n", 0, max_size)
            if nl > max_size // 2:
                chunks.append(remaining[: nl + 1])
                remaining = remaining[nl + 1:]
                continue
            # Else break on the last space within the window
            sp = remaining.rfind(" ", 0, max_size)
            if sp > max_size // 2:
                chunks.append(remaining[: sp + 1])
                remaining = remaining[sp + 1:]
                continue
            # No nice break point — hard cut
            chunks.append(remaining[:max_size])
            remaining = remaining[max_size:]
        return chunks

    def _paste_text(self, text: str) -> Dict[str, Any]:
        """Set clipboard via xclip and trigger ctrl+v paste. Sub-second regardless of length."""
        try:
            p = subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text, env=self._env, capture_output=True, text=True, timeout=5
            )
            if p.returncode != 0:
                err = p.stderr.strip() or f"xclip exited with code {p.returncode}"
                return {"success": False, "error": f"xclip set failed: {err}"}
            time.sleep(0.05)  # let the X selection settle before the paste hotkey
            r = self._xdotool("key", "--clearmodifiers", "ctrl+v")
            if r.returncode != 0:
                err = r.stderr.strip() or f"xdotool key exited with code {r.returncode}"
                return {"success": False, "error": f"paste hotkey failed: {err}"}
            return {"success": True, "action": "paste", "length": len(text)}
        except Exception as e:
            logger.error(f"Paste failed: {e}")
            return {"success": False, "error": str(e)}

    def type_text(self, text: str, interval: float = 0.08) -> Dict[str, Any]:
        """Type text on the virtual display.

        Long text takes the clipboard-paste path when xclip is available
        (sub-second, robust to rich editors). Otherwise, splits into chunks
        small enough to fit under the xdotool subprocess timeout.
        """
        try:
            if len(text) >= self._PASTE_THRESHOLD and shutil.which("xclip"):
                return self._paste_text(text)

            wid = self._get_window_id()
            delay_ms = str(int(interval * 1000))
            chunks = self._chunk_for_typing(text, self._TYPE_CHUNK_SIZE)

            typed = 0
            for chunk in chunks:
                if not chunk:
                    continue
                if wid:
                    r = self._xdotool("type", "--window", wid, "--clearmodifiers", "--delay", delay_ms, chunk)
                else:
                    r = self._xdotool("type", "--clearmodifiers", "--delay", delay_ms, chunk)
                if r.returncode != 0:
                    err = r.stderr.strip() or f"xdotool type exited with code {r.returncode}"
                    logger.error(f"Type failed at {typed}/{len(text)}: {err}")
                    return {"success": False, "error": err, "typed": typed}
                typed += len(chunk)
                if len(chunks) > 1:
                    time.sleep(0.05)  # brief settle so rich editors digest each chunk
            return {"success": True, "action": "type", "length": len(text), "chunks": len(chunks)}
        except Exception as e:
            logger.error(f"Type failed: {e}")
            return {"success": False, "error": str(e)}

    def wait_until_settled(
        self,
        timeout_s: float = 5.0,
        stable_for_ms: int = 200,
        poll_interval_s: float = 0.1,
        diff_threshold: float = 1.0,
    ) -> Dict[str, Any]:
        """Block until consecutive screenshots stop changing.

        The pixel-delta gate Gemini's diagnosis prescribed for the recipe
        runner — replaces blind ``time.sleep`` waits with "wait until the
        screen is actually done painting." Cheap (no LLM call), well-suited
        to "did the page finish loading / did the URL bar finish opening"
        questions where the answer is just visual settling.

        Args:
            timeout_s: hard cap so a perpetually animated screen doesn't hang.
            stable_for_ms: how long pixels must stay quiet before we call it settled.
            poll_interval_s: spacing between screenshots while polling.
            diff_threshold: mean absolute pixel diff (0–255) below which a frame counts as "no change".
        """
        try:
            import numpy as np
        except ImportError:
            time.sleep(min(timeout_s, 1.0))
            return {"success": True, "action": "wait_until_settled", "fallback": "time.sleep (numpy missing)"}

        deadline = time.monotonic() + timeout_s
        prev = None
        stable_since = None
        polls = 0
        while time.monotonic() < deadline:
            try:
                img, _ = self.capture()
                arr = np.asarray(img, dtype=np.int16)
            except Exception as e:
                logger.warning(f"wait_until_settled capture failed: {e}")
                time.sleep(poll_interval_s)
                continue
            polls += 1
            if prev is not None:
                diff = float(np.abs(arr - prev).mean())
                if diff < diff_threshold:
                    if stable_since is None:
                        stable_since = time.monotonic()
                    elif (time.monotonic() - stable_since) * 1000 >= stable_for_ms:
                        return {
                            "success": True,
                            "action": "wait_until_settled",
                            "polls": polls,
                            "final_diff": round(diff, 3),
                        }
                else:
                    stable_since = None
            prev = arr
            time.sleep(poll_interval_s)
        return {
            "success": False,
            "action": "wait_until_settled",
            "error": f"screen never stabilized within {timeout_s}s",
            "polls": polls,
        }

    def read_text_region(self, x: int, y: int, width: int, height: int) -> Dict[str, Any]:
        """OCR a region of the virtual display. Returns the literal pixels-to-text reading.

        This bypasses the vision LLM, which has a documented tendency to fill in
        text-field contents from prompt-history rather than from pixels. Use this
        when you need ground truth about what's actually rendered in a field.
        """
        try:
            try:
                import pytesseract
            except ImportError:
                return {
                    "success": False,
                    "error": (
                        "pytesseract not installed — run: "
                        "sudo apt install tesseract-ocr && pip install pytesseract"
                    ),
                }

            image, _ = self.capture()
            iw, ih = image.size
            x0 = max(0, min(int(x), iw - 1))
            y0 = max(0, min(int(y), ih - 1))
            x1 = max(x0 + 1, min(int(x) + int(width), iw))
            y1 = max(y0 + 1, min(int(y) + int(height), ih))
            crop = image.crop((x0, y0, x1, y1))
            text = pytesseract.image_to_string(crop).strip()
            return {
                "success": True,
                "action": "read_text_region",
                "text": text,
                "bbox": [x0, y0, x1, y1],
            }
        except Exception as e:
            logger.error(f"read_text_region failed: {e}")
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
