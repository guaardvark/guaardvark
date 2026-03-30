#!/usr/bin/env python3
"""
DemoRecorder — Captures human demonstrations on the virtual display.

Listens for input events via `xinput test-xi2` on display :99,
captures screenshot pairs (before/after) around events, and uses
VisionAnalyzer to generate target descriptions for each action.

Used by AgentControlService during LEARNING mode.
"""

import logging
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# --- Constants ---

KEYSTROKE_COLLAPSE_WINDOW = 0.5      # seconds — keystrokes within this window merge into "type"
RAPID_RECLICK_THRESHOLD = 0.5        # seconds — clicks faster than this flag previous as mistake
SCREEN_SETTLE_TIMEOUT = 3.0          # seconds — max wait for screen to stop changing
SCREEN_SETTLE_INTERVAL = 0.3         # seconds — polling interval during settle detection
SCREEN_CHANGE_THRESHOLD = 0.005      # normalized pixel diff — below this means "settled"


@dataclass
class InputEvent:
    """A raw input event captured from xinput or injected for processing."""
    event_type: str           # click, key, hotkey, scroll
    x: Optional[int] = None
    y: Optional[int] = None
    button: Optional[int] = None
    key: Optional[str] = None
    timestamp: float = 0.0
    scroll_amount: Optional[int] = None


class DemoRecorder:
    """
    Records human demonstrations by capturing input events and screenshots.

    Pairs each action with before/after screenshots and VisionAnalyzer
    descriptions of the click target, producing a structured step list
    that can be saved as a Demonstration.
    """

    def __init__(
        self,
        screen,
        analyzer,
        screenshots_dir: Optional[str] = None,
        display: str = ":99",
    ):
        """
        Args:
            screen: ScreenInterface backend (e.g. LocalScreenBackend)
            analyzer: VisionAnalyzer instance for generating descriptions
            screenshots_dir: Directory to save screenshot JPEGs (default: /tmp/demo_screenshots)
            display: X11 display to monitor (default: :99)
        """
        self.screen = screen
        self.analyzer = analyzer
        self.display = display

        self.screenshots_dir = Path(screenshots_dir or "/tmp/demo_screenshots")
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

        # Recording state
        self._steps: List[Dict[str, Any]] = []
        self._recording = False
        self._demo_id: Optional[str] = None
        self._xinput_proc: Optional[subprocess.Popen] = None
        self._listener_thread: Optional[threading.Thread] = None
        self._pending_keystrokes: List[InputEvent] = []
        self._lock = threading.Lock()

        # Capture initial screenshot so first event has a "before"
        self._last_screenshot: Optional[Image.Image] = None
        self._capture_initial_screenshot()

    def _capture_initial_screenshot(self):
        """Take an initial screenshot so the first event has a 'before' image."""
        try:
            img, _ = self.screen.capture()
            self._last_screenshot = img.copy()
        except Exception as e:
            logger.warning(f"[DEMO] Failed to capture initial screenshot: {e}")
            self._last_screenshot = None

    # ---- Public API ----

    def start(self, demo_id: Optional[str] = None):
        """Start recording a demonstration."""
        self._demo_id = demo_id or str(uuid.uuid4())
        self._steps = []
        self._recording = True
        self._pending_keystrokes = []
        self._capture_initial_screenshot()

        # Start xinput listener thread (optional — may fail on some X servers)
        # The primary recording path is via record_event() called from /learn/input
        try:
            self._listener_thread = threading.Thread(
                target=self._xinput_listener,
                daemon=True,
                name=f"demo-recorder-{self._demo_id[:8]}",
            )
            self._listener_thread.start()
            logger.info(f"[DEMO] Recording started: {self._demo_id} (xinput listener active)")
        except Exception as e:
            logger.warning(f"[DEMO] xinput listener failed to start (non-critical): {e}")
            logger.info(f"[DEMO] Recording started: {self._demo_id} (API-only mode)")

    def stop(self):
        """Stop recording and clean up."""
        self._recording = False

        # Flush any pending keystrokes
        if self._pending_keystrokes:
            step = self._collapse_keystrokes(self._pending_keystrokes)
            if step:
                with self._lock:
                    self._steps.append(step)
            self._pending_keystrokes = []

        # Kill xinput process
        if self._xinput_proc:
            try:
                self._xinput_proc.kill()
                self._xinput_proc.wait(timeout=5)
            except Exception:
                pass
            self._xinput_proc = None

        logger.info(f"[DEMO] Recording stopped: {self._demo_id}, {len(self._steps)} steps captured")

    def record_event(self, action: str, x: int = 0, y: int = 0,
                     text: str = "", keys: str = "", button: int = 1) -> Optional[Dict[str, Any]]:
        """
        Record an externally-supplied input event (from /learn/input API).

        This bypasses xinput entirely — the API endpoint already knows
        exactly what the user did, so we just need to capture screenshots
        and run VisionAnalyzer.
        """
        if not self._recording:
            return None

        evt = InputEvent(
            event_type=action,
            x=x, y=y,
            button=button,
            key=text if action == "type" else keys,
            timestamp=time.time(),
        )

        if action == "type" and text:
            # For type events, create keystroke events and collapse them
            key_events = [
                InputEvent(event_type="key", key=ch, timestamp=time.time())
                for ch in text
            ]
            step = self._collapse_keystrokes(key_events)
        else:
            step = self._process_event(evt)

        if step:
            with self._lock:
                self._steps.append(step)
        return step

    def get_steps(self) -> List[Dict[str, Any]]:
        """Return all recorded steps in order."""
        with self._lock:
            return list(self._steps)

    # ---- Event Processing ----

    def _process_event(self, evt: InputEvent) -> Optional[Dict[str, Any]]:
        """
        Process a single input event into a structured step dict.

        1. Use self._last_screenshot as screenshot_before
        2. Save before screenshot as JPEG
        3. Wait for screen to settle (_wait_for_settle)
        4. Save after screenshot
        5. For click/scroll: run VisionAnalyzer for target_description
        6. Run VisionAnalyzer for precondition
        7. Detect rapid re-clicks (flag previous step as potential mistake)
        8. Return step dict

        Args:
            evt: The input event to process

        Returns:
            Step dict or None if event was ignored
        """
        step_index = len(self._steps)

        # --- Screenshot before ---
        screenshot_before_path = None
        if self._last_screenshot is not None:
            screenshot_before_path = self._save_screenshot(
                self._last_screenshot, f"step_{step_index:04d}_before"
            )

        # --- Build base step ---
        step = {
            "step_index": step_index,
            "action_type": evt.event_type,
            "target_description": "",
            "element_context": "",
            "coordinates_x": evt.x,
            "coordinates_y": evt.y,
            "text": None,
            "keys": None,
            "intent": None,
            "precondition": "",
            "variability": False,
            "wait_condition": None,
            "is_potential_mistake": False,
            "screenshot_before": screenshot_before_path,
            "screenshot_after": None,
        }

        # --- Action-specific fields ---
        if evt.event_type == "hotkey":
            step["action_type"] = "hotkey"
            step["keys"] = evt.key
            step["coordinates_x"] = None
            step["coordinates_y"] = None
        elif evt.event_type == "scroll":
            step["action_type"] = "scroll"
            step["text"] = str(evt.scroll_amount) if evt.scroll_amount else None

        # --- Wait for screen to settle & capture after ---
        after_img = self._wait_for_settle()
        if after_img is not None:
            screenshot_after_path = self._save_screenshot(
                after_img, f"step_{step_index:04d}_after"
            )
            step["screenshot_after"] = screenshot_after_path
            self._last_screenshot = after_img
        else:
            # Screen didn't change — use current capture as after
            try:
                img, _ = self.screen.capture()
                screenshot_after_path = self._save_screenshot(
                    img, f"step_{step_index:04d}_after"
                )
                step["screenshot_after"] = screenshot_after_path
                self._last_screenshot = img
            except Exception:
                pass

        # --- VisionAnalyzer for click/scroll targets ---
        if evt.event_type in ("click", "scroll") and self._last_screenshot is not None:
            try:
                # Describe what was clicked
                target_result = self.analyzer.analyze(
                    self._last_screenshot if screenshot_before_path else self._last_screenshot,
                    prompt=f"Describe the UI element at pixel coordinates ({evt.x}, {evt.y}) in one sentence. "
                           f"Include its type (button, link, field, etc.), label text, and visual appearance.",
                )
                if target_result.success and target_result.description:
                    step["target_description"] = target_result.description
                    step["element_context"] = target_result.description
            except Exception as e:
                logger.warning(f"[DEMO] VisionAnalyzer target failed: {e}")

        # --- VisionAnalyzer for precondition ---
        if self._last_screenshot is not None and screenshot_before_path:
            try:
                before_img = self._last_screenshot  # Use before screenshot for precondition
                precond_result = self.analyzer.analyze(
                    before_img,
                    prompt="Describe what is currently visible on screen in one sentence. "
                           "Focus on the main content area and any notable UI state.",
                )
                if precond_result.success and precond_result.description:
                    step["precondition"] = precond_result.description
            except Exception as e:
                logger.warning(f"[DEMO] VisionAnalyzer precondition failed: {e}")

        # --- Rapid re-click detection ---
        if evt.event_type == "click" and len(self._steps) > 0:
            prev = self._steps[-1]
            if prev["action_type"] == "click":
                # Check if the previous click was within the rapid threshold
                # We use step_index timing: if the gap is small, flag previous as mistake
                prev_timestamp = getattr(self, "_last_event_timestamp", 0)
                if evt.timestamp - prev_timestamp < RAPID_RECLICK_THRESHOLD:
                    self._steps[-1]["is_potential_mistake"] = True

        self._last_event_timestamp = evt.timestamp

        # --- Store and return ---
        with self._lock:
            self._steps.append(step)

        return step

    def _collapse_keystrokes(self, events: List[InputEvent]) -> Optional[Dict[str, Any]]:
        """
        Collapse sequential key events into a single 'type' step.

        Args:
            events: List of key InputEvents to collapse

        Returns:
            Step dict with action_type='type' and combined text
        """
        if not events:
            return None

        text = "".join(e.key or "" for e in events)
        step_index = len(self._steps)

        # Screenshot from before the first keystroke
        screenshot_before_path = None
        if self._last_screenshot is not None:
            screenshot_before_path = self._save_screenshot(
                self._last_screenshot, f"step_{step_index:04d}_before"
            )

        # Capture after screenshot
        screenshot_after_path = None
        try:
            img, _ = self.screen.capture()
            screenshot_after_path = self._save_screenshot(
                img, f"step_{step_index:04d}_after"
            )
            self._last_screenshot = img
        except Exception:
            pass

        step = {
            "step_index": step_index,
            "action_type": "type",
            "target_description": "",
            "element_context": "",
            "coordinates_x": None,
            "coordinates_y": None,
            "text": text,
            "keys": None,
            "intent": None,
            "precondition": "",
            "variability": False,
            "wait_condition": None,
            "is_potential_mistake": False,
            "screenshot_before": screenshot_before_path,
            "screenshot_after": screenshot_after_path,
        }

        return step

    # ---- Screen Settle Detection ----

    def _wait_for_settle(self) -> Optional[Image.Image]:
        """
        Poll screenshots until the screen stops changing.

        Returns the settled screenshot, or None if the screen never changed.
        Uses a threshold of SCREEN_CHANGE_THRESHOLD on normalized pixel diff.
        """
        deadline = time.time() + SCREEN_SETTLE_TIMEOUT
        prev_img = None

        try:
            prev_img_tuple = self.screen.capture()
            prev_img = prev_img_tuple[0]
        except Exception:
            return None

        while time.time() < deadline:
            time.sleep(SCREEN_SETTLE_INTERVAL)
            try:
                curr_img_tuple = self.screen.capture()
                curr_img = curr_img_tuple[0]
            except Exception:
                break

            diff = self._image_diff(prev_img, curr_img)
            if diff < SCREEN_CHANGE_THRESHOLD:
                # Screen has settled
                return curr_img
            prev_img = curr_img

        # Timed out — return last captured image
        return prev_img

    def _image_diff(self, img1: Image.Image, img2: Image.Image) -> float:
        """
        Compute normalized pixel difference between two images.

        Returns a float between 0.0 (identical) and 1.0 (completely different).
        """
        try:
            arr1 = np.array(img1, dtype=np.float32)
            arr2 = np.array(img2, dtype=np.float32)

            if arr1.shape != arr2.shape:
                # Resize img2 to match img1 if shapes differ
                img2 = img2.resize(img1.size, Image.LANCZOS)
                arr2 = np.array(img2, dtype=np.float32)

            diff = np.abs(arr1 - arr2).mean() / 255.0
            return float(diff)
        except Exception as e:
            logger.warning(f"[DEMO] Image diff error: {e}")
            return 1.0  # Assume different on error

    # ---- Screenshot I/O ----

    def _save_screenshot(self, image: Image.Image, name: str) -> str:
        """Save a screenshot as JPEG and return the file path."""
        filename = f"{name}.jpg"
        filepath = self.screenshots_dir / filename
        try:
            image.convert("RGB").save(str(filepath), format="JPEG", quality=80)
        except Exception as e:
            logger.error(f"[DEMO] Failed to save screenshot {filepath}: {e}")
        return str(filepath)

    # ---- xinput Listener ----

    def _xinput_listener(self):
        """
        Background thread that monitors input events via xinput test-xi2.

        Parses button press/release and key press events from xinput output
        and feeds them into _process_event / keystroke collapse logic.
        """
        env = {**os.environ, "DISPLAY": self.display}

        try:
            self._xinput_proc = subprocess.Popen(
                ["xinput", "test-xi2", "--root"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            logger.error("[DEMO] xinput not found — cannot record input events")
            return
        except Exception as e:
            logger.error(f"[DEMO] Failed to start xinput: {e}")
            return

        logger.info(f"[DEMO] xinput listener started on {self.display}")

        try:
            for line in self._xinput_proc.stdout:
                if not self._recording:
                    break

                line = line.strip()
                if not line:
                    continue

                evt = self._parse_xinput_line(line)
                if evt is None:
                    continue

                # Handle keystroke collapse
                if evt.event_type == "key":
                    now = time.time()
                    if (self._pending_keystrokes and
                            now - self._pending_keystrokes[-1].timestamp > KEYSTROKE_COLLAPSE_WINDOW):
                        # Flush previous keystrokes
                        step = self._collapse_keystrokes(self._pending_keystrokes)
                        if step:
                            with self._lock:
                                self._steps.append(step)
                        self._pending_keystrokes = []

                    self._pending_keystrokes.append(evt)
                    continue

                # Non-key event — flush pending keystrokes first
                if self._pending_keystrokes:
                    step = self._collapse_keystrokes(self._pending_keystrokes)
                    if step:
                        with self._lock:
                            self._steps.append(step)
                    self._pending_keystrokes = []

                # Process the non-key event
                self._process_event(evt)

        except Exception as e:
            if self._recording:
                logger.error(f"[DEMO] xinput listener error: {e}")
        finally:
            logger.info("[DEMO] xinput listener stopped")

    def _parse_xinput_line(self, line: str) -> Optional[InputEvent]:
        """
        Parse a single line from xinput test-xi2 output.

        xinput test-xi2 --root output looks like:
            EVENT type 4 (ButtonPress)
                detail: 1
                root: 640.00/400.00
            EVENT type 5 (ButtonRelease)
                detail: 1
                root: 640.00/400.00
            EVENT type 2 (KeyPress)
                detail: 38
            EVENT type 3 (KeyRelease)
                detail: 38

        We only care about ButtonPress and KeyPress events.
        """
        try:
            if "ButtonPress" in line:
                return self._parse_button_event(line, "click")
            elif "KeyPress" in line:
                return self._parse_key_event(line)
        except Exception as e:
            logger.debug(f"[DEMO] Failed to parse xinput line: {line!r} — {e}")
        return None

    def _parse_button_event(self, line: str, event_type: str = "click") -> Optional[InputEvent]:
        """Parse a button press event from xinput output."""
        # This is a simplified parser — the actual coordinates come on subsequent lines.
        # For the background listener, we'll use cursor position from xdotool.
        try:
            cursor_pos = self.screen.cursor_position() if hasattr(self.screen, "cursor_position") else (0, 0)
            return InputEvent(
                event_type=event_type,
                x=cursor_pos[0],
                y=cursor_pos[1],
                button=1,
                timestamp=time.time(),
            )
        except Exception:
            return InputEvent(
                event_type=event_type,
                x=0, y=0,
                button=1,
                timestamp=time.time(),
            )

    def _parse_key_event(self, line: str) -> Optional[InputEvent]:
        """Parse a key press event from xinput output."""
        # Key detail (keycode) needs mapping to a key name.
        # For now, use xdotool to get the current key name.
        # In practice, we'd use a keycode-to-keysym table.
        try:
            return InputEvent(
                event_type="key",
                key="",  # Will be enriched by keycode mapping
                timestamp=time.time(),
            )
        except Exception:
            return None
