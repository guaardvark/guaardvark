#!/usr/bin/env python3
"""
DemoRecorder — Captures human demonstrations on the virtual display.

Two input paths feed it:

* ``record_event()`` — the /learn/input API, which injects the action with
  xdotool and already knows exactly what was done.
* An ``xinput test-xi2 --root`` listener on the display, which sees input
  arriving any other way (a person on the VNC viewer, a script driving
  xdotool). Each event is decoded from its full output block: one step per
  physical press (the master and slave copies and the Raw* events are
  dropped), keycodes translated through the display's keymap, chords
  recorded as hotkeys. Input injected by /learn/input is hidden from the
  listener so it is recorded once.

Each step carries before/after screenshots and VisionAnalyzer descriptions
of the target and of the screen before the step (its precondition).

Used by AgentControlService during LEARNING mode.
"""

import logging
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

# --- Constants ---

KEYSTROKE_COLLAPSE_WINDOW = 0.5      # seconds — keystrokes within this window merge into "type"
RAPID_RECLICK_THRESHOLD = 0.5        # seconds — clicks faster than this flag previous as mistake
SCREEN_SETTLE_TIMEOUT = 3.0          # seconds — max wait for screen to stop changing
SCREEN_SETTLE_INTERVAL = 0.3         # seconds — polling interval during settle detection
SCREEN_CHANGE_THRESHOLD = 0.005      # normalized pixel diff — below this means "settled"
INJECTION_ECHO_GRACE = 0.3           # seconds — listener events this soon after a /learn/input
                                     # injection are that injection's echo
# The click target is shown to the vision model as a close-up with a ring on
# the spot. Measured 2026-11-14 with gemma4:e4b on a 1000x1000 desktop of six
# labelled icons, three runs each: naming the pixel coordinates in words
# described the clicked element 13/18 times (the other five named a neighbour),
# a ring on the full screen 17/18 (one neighbour), this close-up 17/18 with no
# neighbour named.
TARGET_CLOSEUP_HALF = 170            # pixels each side of the click kept in the close-up
TARGET_RING_RADIUS = 46              # pixels
STOP_DRAIN_TIMEOUT = 60.0            # seconds — stop() waits this long for queued listener events

# X modifier mask bits as printed in xinput's "modifiers: ... effective: 0x.." field.
_MOD_SHIFT, _MOD_LOCK, _MOD_CONTROL, _MOD_ALT, _MOD_NUMLOCK, _MOD_SUPER = 0x1, 0x2, 0x4, 0x8, 0x10, 0x40

_MODIFIER_KEYSYMS = {
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
    "Meta_L", "Meta_R", "Super_L", "Super_R", "Hyper_L", "Hyper_R",
    "Caps_Lock", "Shift_Lock", "Num_Lock", "Mode_switch",
    "ISO_Level3_Shift", "ISO_Level5_Shift", "ISO_Group_Shift",
}

# Keysym names of the printable ASCII characters that are not their own name.
_KEYSYM_CHARS = {
    "space": " ", "exclam": "!", "quotedbl": '"', "numbersign": "#",
    "dollar": "$", "percent": "%", "ampersand": "&", "apostrophe": "'",
    "parenleft": "(", "parenright": ")", "asterisk": "*", "plus": "+",
    "comma": ",", "minus": "-", "period": ".", "slash": "/", "colon": ":",
    "semicolon": ";", "less": "<", "equal": "=", "greater": ">",
    "question": "?", "at": "@", "bracketleft": "[", "backslash": "\\",
    "bracketright": "]", "asciicircum": "^", "underscore": "_", "grave": "`",
    "braceleft": "{", "bar": "|", "braceright": "}", "asciitilde": "~",
    "KP_Decimal": ".", "KP_Add": "+", "KP_Subtract": "-",
    "KP_Multiply": "*", "KP_Divide": "/", "KP_Space": " ",
}


def _us_fallback_keymap() -> Dict[int, List[str]]:
    """US evdev keycodes, used only when ``xmodmap -pke`` cannot be read."""
    keymap: Dict[int, List[str]] = {}
    rows = (
        (10, "1234567890-=", "!@#$%^&*()_+"),
        (24, "qwertyuiop[]", "QWERTYUIOP{}"),
        (38, "asdfghjkl;'`", 'ASDFGHJKL:"~'),
        (51, "\\zxcvbnm,./", "|ZXCVBNM<>?"),
    )
    by_char = {v: k for k, v in _KEYSYM_CHARS.items() if not k.startswith("KP_")}
    for first, plain, shifted in rows:
        for i, (lo, hi) in enumerate(zip(plain, shifted)):
            keymap[first + i] = [by_char.get(lo, lo), by_char.get(hi, hi)]
    named = {
        9: "Escape", 22: "BackSpace", 23: "Tab", 36: "Return", 37: "Control_L",
        50: "Shift_L", 62: "Shift_R", 64: "Alt_L", 65: "space", 66: "Caps_Lock",
        105: "Control_R", 108: "Alt_R", 110: "Home", 111: "Up", 112: "Prior",
        113: "Left", 114: "Right", 115: "End", 116: "Down", 117: "Next",
        118: "Insert", 119: "Delete", 133: "Super_L", 134: "Super_R",
        95: "F11", 96: "F12",
    }
    named.update({67 + i: f"F{i + 1}" for i in range(10)})
    for code, sym in named.items():
        keymap[code] = [sym]
    return keymap


def _keysym_to_char(sym: str) -> Optional[str]:
    """The character a keysym types, or None for keys that type nothing (Return, F5, ...)."""
    if len(sym) == 1:
        return sym
    if sym in _KEYSYM_CHARS:
        return _KEYSYM_CHARS[sym]
    if sym.startswith("KP_") and len(sym) == 4 and sym[3].isdigit():
        return sym[3]
    if re.fullmatch(r"U[0-9A-Fa-f]{4,6}", sym):
        return chr(int(sym[1:], 16))
    return None


_EVENT_HEADER = re.compile(r"^EVENT type \d+ \((\w+)\)")


class XinputBlockParser:
    """Groups ``xinput test-xi2`` output into one dict per event.

    Each event prints a header line followed by indented fields::

        EVENT type 2 (KeyPress)
            device: 3 (5)
            time: 35003144
            detail: 46
            root: 100.00/200.00
            modifiers: locked 0 latched 0 base 0x4 effective: 0x4
            windows: root 0x3a7 event 0x3a7 child 0x0

    ``windows:`` ends a device event; anything else (Raw*, DeviceChanged)
    ends at the next header.
    """

    def __init__(self):
        self._block: Optional[Dict[str, Any]] = None

    def feed(self, line: str) -> Optional[Dict[str, Any]]:
        """Consume one line; return the event it completes, if any."""
        stripped = line.strip()
        header = _EVENT_HEADER.match(stripped)
        if header:
            done, self._block = self._block, {"name": header.group(1)}
            return done
        if self._block is None or ":" not in stripped:
            return None
        name, _, value = stripped.partition(":")
        value = value.strip()
        block = self._block
        try:
            if name == "device":
                # "device: 2 (4)": the delivering device, then the physical source.
                ids = value.replace("(", " ").replace(")", " ").split()
                block["device"], block["source"] = int(ids[0]), int(ids[1])
            elif name == "time":
                block["time"] = int(value)
            elif name == "detail":
                block["detail"] = int(value)
            elif name == "root":
                x, _, y = value.partition("/")
                block["root"] = (int(float(x)), int(float(y)))
            elif name == "modifiers":
                block["mods"] = int(value.rsplit("effective:", 1)[1].strip(), 0)
            elif name == "windows":
                self._block = None
                return block
        except (ValueError, IndexError):
            logger.debug(f"[DEMO] Unparsed xinput field: {stripped!r}")
        return None

    def flush(self) -> Optional[Dict[str, Any]]:
        done, self._block = self._block, None
        return done


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
        self._worker_thread: Optional[threading.Thread] = None
        self._events: "queue.Queue[Optional[InputEvent]]" = queue.Queue()
        self._pending_keystrokes: List[InputEvent] = []
        self._pending_scroll: List[InputEvent] = []
        self._lock = threading.Lock()
        self._shot_seq = 0
        self._last_event_timestamp = 0.0

        # /learn/input injections in flight, and when their echo stops arriving.
        self._injecting = 0
        self._suppress_until = 0.0
        # (event name, detail, server time) of recent presses: X reports each
        # press once per slave device and again per master device.
        self._seen_presses: deque = deque(maxlen=64)
        self._keymap: Dict[int, List[str]] = {}

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
        self._pending_scroll = []
        self._events = queue.Queue()
        self._capture_initial_screenshot()

        # The worker turns listener events into steps; the listener only reads
        # xinput so each event is timestamped when it happens, not when the
        # previous step's settle wait and vision calls finish.
        self._worker_thread = threading.Thread(
            target=self._event_worker,
            daemon=True,
            name=f"demo-steps-{self._demo_id[:8]}",
        )
        self._worker_thread.start()

        # Start xinput listener thread (optional — may fail on some X servers)
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
        """Stop recording, finish any queued listener events, and clean up."""
        self._recording = False

        # Kill xinput process
        if self._xinput_proc:
            try:
                self._xinput_proc.kill()
                self._xinput_proc.wait(timeout=5)
            except Exception:
                pass
            self._xinput_proc = None

        worker = self._worker_thread
        if worker is not None and worker.is_alive():
            self._events.put(None)
            worker.join(timeout=STOP_DRAIN_TIMEOUT)
            if worker.is_alive():
                logger.warning("[DEMO] Listener events still processing at stop; later steps are dropped")
        else:
            self._flush_pending()
        self._worker_thread = None

        logger.info(f"[DEMO] Recording stopped: {self._demo_id}, {len(self._steps)} steps captured")

    @contextmanager
    def injected_input(self):
        """Hide input this process injects from the xinput listener.

        /learn/input injects each action with xdotool and records it through
        record_event(); the listener would otherwise see the same action on
        the display and record it a second time.
        """
        with self._lock:
            self._injecting += 1
        try:
            yield
        finally:
            with self._lock:
                self._injecting -= 1
                self._suppress_until = time.time() + INJECTION_ECHO_GRACE

    def record_event(self, action: str, x: int = 0, y: int = 0,
                     text: str = "", keys: str = "", button: int = 1,
                     amount: Optional[int] = None) -> Optional[Dict[str, Any]]:
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
            scroll_amount=amount,
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

        return self._append_step(step)

    def get_steps(self) -> List[Dict[str, Any]]:
        """Return all recorded steps in order."""
        with self._lock:
            return list(self._steps)

    # ---- Event Processing ----

    def _append_step(self, step: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Add a finished step, numbering it by its place in the list."""
        if step:
            with self._lock:
                step["step_index"] = len(self._steps)
                self._steps.append(step)
        return step

    def _next_shot_name(self, suffix: str) -> str:
        with self._lock:
            self._shot_seq += 1
            return f"step_{self._shot_seq:04d}_{suffix}"

    @staticmethod
    def _target_closeup(image: Image.Image, x: int, y: int) -> Image.Image:
        """The part of the screen around a click, with a ring on the clicked spot."""
        marked = image.convert("RGB")
        draw = ImageDraw.Draw(marked)
        r = TARGET_RING_RADIUS
        for width, colour in ((5, (255, 255, 255)), (3, (255, 0, 0))):
            draw.ellipse([x - r, y - r, x + r, y + r], outline=colour, width=width)
        half = TARGET_CLOSEUP_HALF
        return marked.crop((
            max(0, x - half), max(0, y - half),
            min(marked.width, x + half), min(marked.height, y + half),
        ))

    def _describe_precondition(self, image: Optional[Image.Image]) -> str:
        """What the screen showed before a step; replay checks it before acting."""
        if image is None:
            return ""
        try:
            result = self.analyzer.analyze(
                image,
                prompt="Describe what is currently visible on screen in one sentence. "
                       "Focus on the main content area and any notable UI state.",
            )
            if result.success and result.description:
                return result.description
        except Exception as e:
            logger.warning(f"[DEMO] VisionAnalyzer precondition failed: {e}")
        return ""

    def _process_event(self, evt: InputEvent) -> Optional[Dict[str, Any]]:
        """
        Process a single input event into a structured step dict.

        1. Use self._last_screenshot as screenshot_before
        2. Save before screenshot as JPEG
        3. Wait for screen to settle (_wait_for_settle)
        4. Save after screenshot
        5. For click/scroll: describe the target on the before screenshot
        6. Describe the before screenshot as the precondition
        7. Detect rapid re-clicks (flag previous step as potential mistake)
        8. Return step dict

        Args:
            evt: The input event to process

        Returns:
            Step dict or None if event was ignored
        """
        step_index = len(self._steps)

        # --- Screenshot before ---
        before_img = self._last_screenshot
        screenshot_before_path = None
        if before_img is not None:
            screenshot_before_path = self._save_screenshot(
                before_img, self._next_shot_name("before")
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
                after_img, self._next_shot_name("after")
            )
            step["screenshot_after"] = screenshot_after_path
            self._last_screenshot = after_img
        else:
            # Screen didn't change — use current capture as after
            try:
                img, _ = self.screen.capture()
                screenshot_after_path = self._save_screenshot(
                    img, self._next_shot_name("after")
                )
                step["screenshot_after"] = screenshot_after_path
                self._last_screenshot = img
            except Exception:
                pass

        # --- VisionAnalyzer for click/scroll targets ---
        # The element is described as it looked when it was clicked; replay
        # looks for it on the screen before acting.
        target_img = before_img if before_img is not None else self._last_screenshot
        if evt.event_type in ("click", "scroll") and target_img is not None:
            try:
                target_result = self.analyzer.analyze(
                    self._target_closeup(target_img, evt.x, evt.y),
                    prompt="This is a close-up of part of a screen. A red ring marks one UI element. "
                           "Describe that element in one sentence. Include its type (button, link, field, etc.), "
                           "label text, and visual appearance. Do not mention the ring.",
                )
                if target_result.success and target_result.description:
                    step["target_description"] = target_result.description
                    step["element_context"] = target_result.description
            except Exception as e:
                logger.warning(f"[DEMO] VisionAnalyzer target failed: {e}")

        # --- VisionAnalyzer for precondition ---
        step["precondition"] = self._describe_precondition(before_img)

        # --- Rapid re-click detection ---
        if evt.event_type == "click" and len(self._steps) > 0:
            prev = self._steps[-1]
            if prev["action_type"] == "click":
                if evt.timestamp - self._last_event_timestamp < RAPID_RECLICK_THRESHOLD:
                    self._steps[-1]["is_potential_mistake"] = True

        self._last_event_timestamp = evt.timestamp

        return step

    def _collapse_keystrokes(self, events: List[InputEvent]) -> Optional[Dict[str, Any]]:
        """
        Collapse sequential key events into a single 'type' step.

        Args:
            events: List of key InputEvents to collapse

        Returns:
            Step dict with action_type='type' and combined text, or None
            when the events type nothing.
        """
        if not events:
            return None

        text = "".join(e.key or "" for e in events)
        if not text:
            return None
        step_index = len(self._steps)

        # Screenshot from before the first keystroke
        before_img = self._last_screenshot
        screenshot_before_path = None
        if before_img is not None:
            screenshot_before_path = self._save_screenshot(
                before_img, self._next_shot_name("before")
            )

        # Capture after screenshot
        screenshot_after_path = None
        try:
            img, _ = self.screen.capture()
            screenshot_after_path = self._save_screenshot(
                img, self._next_shot_name("after")
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
            "precondition": self._describe_precondition(before_img),
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

    def _load_keymap(self) -> Dict[int, List[str]]:
        """Keycode -> keysym names for the display, from ``xmodmap -pke``."""
        env = {**os.environ, "DISPLAY": self.display}
        keymap: Dict[int, List[str]] = {}
        try:
            out = subprocess.run(
                ["xmodmap", "-pke"], env=env, capture_output=True, text=True, timeout=5,
            ).stdout
            for line in out.splitlines():
                # "keycode  46 = l L l L"
                left, _, right = line.partition("=")
                parts = left.split()
                if len(parts) == 2 and parts[0] == "keycode" and right.strip():
                    keymap[int(parts[1])] = right.split()
        except Exception as e:
            logger.warning(f"[DEMO] xmodmap unavailable ({e}); using the US keymap")
        return keymap or _us_fallback_keymap()

    def _xinput_listener(self):
        """
        Background thread that reads input events via xinput test-xi2.

        Decodes each event block and queues button and key presses for the
        worker thread; does nothing slow, so each event's timestamp is when
        it reached the display.
        """
        env = {**os.environ, "DISPLAY": self.display}
        self._keymap = self._load_keymap()

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
        parser = XinputBlockParser()

        try:
            for line in self._xinput_proc.stdout:
                if not self._recording:
                    break
                block = parser.feed(line)
                if block is None:
                    continue
                arrived = time.time()
                if self._injecting or arrived <= self._suppress_until:
                    continue
                evt = self._event_from_block(block, arrived)
                if evt is not None:
                    self._events.put(evt)
        except Exception as e:
            if self._recording:
                logger.error(f"[DEMO] xinput listener error: {e}")
        finally:
            logger.info("[DEMO] xinput listener stopped")

    def _event_from_block(self, block: Dict[str, Any], timestamp: float) -> Optional[InputEvent]:
        """Turn one decoded xinput event into an InputEvent, or None to skip it.

        Only ButtonPress and KeyPress count (releases, motion and the Raw*
        copies do not), and each press once.
        """
        name = block.get("name")
        if name not in ("ButtonPress", "KeyPress") or "detail" not in block:
            return None
        press_id = (name, block["detail"], block.get("time"))
        if block.get("time") is not None:
            if press_id in self._seen_presses:
                return None
            self._seen_presses.append(press_id)
        elif block.get("device") == block.get("source"):
            return None  # no server time to pair copies by: keep the master's copy

        x, y = block.get("root", (None, None))
        detail = block["detail"]
        if name == "ButtonPress":
            if detail in (1, 2, 3):
                return InputEvent(event_type="click", x=x, y=y, button=detail, timestamp=timestamp)
            if detail in (4, 5):
                # Wheel up / down, in LocalScreenBackend.scroll's sign convention.
                return InputEvent(event_type="scroll", x=x, y=y,
                                  scroll_amount=1 if detail == 4 else -1, timestamp=timestamp)
            return None  # horizontal wheel, back/forward: replay has no action for them
        return self._key_event(detail, block.get("mods", 0), timestamp)

    def _key_event(self, keycode: int, mods: int, timestamp: float) -> Optional[InputEvent]:
        """A typed character ('key'), a named key or chord ('hotkey'), or None for a bare modifier."""
        syms = [s for s in self._keymap.get(keycode, []) if s != "NoSymbol"]
        if not syms:
            logger.debug(f"[DEMO] Keycode {keycode} has no keysym; skipped")
            return None
        base = syms[0]
        if base in _MODIFIER_KEYSYMS:
            return None
        shifted = bool(mods & _MOD_SHIFT)

        chord = [name for bit, name in ((_MOD_CONTROL, "ctrl"), (_MOD_ALT, "alt"), (_MOD_SUPER, "super"))
                 if mods & bit]
        if chord:
            if shifted:
                chord.append("shift")
            return InputEvent(event_type="hotkey", key="+".join(chord + [base]), timestamp=timestamp)

        sym = base
        if base.startswith("KP_") and mods & _MOD_NUMLOCK and len(syms) > 1:
            sym = syms[1]
        elif shifted and len(syms) > 1:
            sym = syms[1]
        if mods & _MOD_LOCK and len(sym) == 1 and sym.isalpha():
            sym = sym.swapcase()

        char = _keysym_to_char(sym)
        if char is not None:
            return InputEvent(event_type="key", key=char, timestamp=timestamp)
        return InputEvent(event_type="hotkey", key=("shift+" if shifted else "") + base,
                          timestamp=timestamp)

    def _event_worker(self):
        """Turn queued listener events into steps, in order."""
        while True:
            try:
                evt = self._events.get(timeout=KEYSTROKE_COLLAPSE_WINDOW)
            except queue.Empty:
                self._flush_pending()   # a pause ends the text being typed
                continue
            if evt is None:
                break
            try:
                self._handle_listener_event(evt)
            except Exception as e:
                logger.warning(f"[DEMO] Failed to record {evt.event_type} event: {e}")
        self._flush_pending()

    def _handle_listener_event(self, evt: InputEvent):
        if evt.event_type == "key":
            self._flush_scroll()
            if (self._pending_keystrokes and
                    evt.timestamp - self._pending_keystrokes[-1].timestamp > KEYSTROKE_COLLAPSE_WINDOW):
                self._flush_keystrokes()
            self._pending_keystrokes.append(evt)
            return

        if evt.event_type == "hotkey" and evt.key == "BackSpace" and self._pending_keystrokes:
            # A correction inside the text being typed: record what was meant.
            self._pending_keystrokes.pop()
            return

        if evt.event_type == "scroll":
            self._flush_keystrokes()
            pending = self._pending_scroll
            if pending and (
                (pending[-1].scroll_amount > 0) != (evt.scroll_amount > 0)
                or evt.timestamp - pending[-1].timestamp > KEYSTROKE_COLLAPSE_WINDOW
            ):
                self._flush_scroll()
            self._pending_scroll.append(evt)
            return

        self._flush_pending()
        self._append_step(self._process_event(evt))

    def _flush_keystrokes(self):
        if self._pending_keystrokes:
            events, self._pending_keystrokes = self._pending_keystrokes, []
            self._append_step(self._collapse_keystrokes(events))

    def _flush_scroll(self):
        if self._pending_scroll:
            events, self._pending_scroll = self._pending_scroll, []
            first = events[0]
            self._append_step(self._process_event(InputEvent(
                event_type="scroll", x=first.x, y=first.y, timestamp=first.timestamp,
                scroll_amount=sum(e.scroll_amount or 0 for e in events),
            )))

    def _flush_pending(self):
        self._flush_keystrokes()
        self._flush_scroll()
