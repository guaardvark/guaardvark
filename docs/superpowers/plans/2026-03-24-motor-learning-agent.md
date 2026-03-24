# Motor Learning Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace grid-based click targeting with a closed-loop servo system that learns hand-eye coordination through self-supervised practice.

**Architecture:** A ServoController handles precise mouse targeting via an iterative observe-correct-click loop. Every interaction is silently recorded by a TrainingDataCollector. Recorded data feeds a QLoRA fine-tuning pipeline that produces progressively smarter `gvk-eye` vision models.

**Tech Stack:** Python 3.12, Ollama (qwen3-vl:2b-instruct, qwen3.5:9b), xdotool, mss, PIL, unsloth (QLoRA), PyTorch

**Spec:** `docs/superpowers/specs/2026-03-24-motor-learning-agent-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `backend/services/servo_controller.py` | CREATE | Closed-loop motor control: ballistic move → observe → correct → click |
| `backend/services/training_data_collector.py` | CREATE | Silently records servo interactions to disk as training data |
| `backend/services/agent_control_service.py` | MODIFY | Replace grid targeting with servo_controller calls |
| `backend/utils/vision_analyzer.py` | MODIFY | Accept num_predict/temperature overrides in analyze() |
| `backend/services/local_screen_backend.py` | MODIFY | Update screen_size() fallback to 1280x720 |
| `scripts/start_agent_display.sh` | MODIFY | Change resolution to 1280x720 |
| `backend/services/training/scripts/prepare_training_set.py` | CREATE | Convert raw servo logs to QLoRA training JSONL |
| `backend/services/training/scripts/generate_practice_data.py` | CREATE | Automated practice sessions on known target pages |
| `backend/services/training/scripts/evaluate_model.py` | CREATE | Eval fine-tuned model against held-out test set |
| `backend/services/training/scripts/register_model.py` | CREATE | GGUF convert + ollama create for deploying trained models |
| `backend/tests/test_servo_controller.py` | CREATE | Unit tests for servo loop |
| `backend/tests/test_training_data_collector.py` | CREATE | Unit tests for data recording |
| `backend/tests/test_screen_interface.py` | MODIFY | Update screen_size assertion to 1280x720 |

---

## Task 1: Parameterize VisionAnalyzer

**Files:**
- Modify: `backend/utils/vision_analyzer.py:175-215` (analyze method) and `:255-298` (analyze_base64 method)
- Test: `backend/tests/test_vision_analyzer_params.py`

- [ ] **Step 1: Write test for parameterized analyze()**

```python
# backend/tests/test_vision_analyzer_params.py
import unittest
from unittest.mock import patch, MagicMock
from PIL import Image

class TestVisionAnalyzerParams(unittest.TestCase):
    """Test that analyze() accepts num_predict and temperature overrides."""

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_analyze_uses_default_options(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"message": {"content": "test description"}}
        )
        analyzer = VisionAnalyzer(default_model="test-model")
        img = Image.new("RGB", (100, 100))
        analyzer.analyze(img, prompt="test")

        call_json = mock_post.call_args[1]["json"]
        assert call_json["options"]["num_predict"] == 256
        assert call_json["options"]["temperature"] == 0.3

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_analyze_accepts_num_predict_override(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"message": {"content": "test"}}
        )
        analyzer = VisionAnalyzer(default_model="test-model")
        img = Image.new("RGB", (100, 100))
        analyzer.analyze(img, prompt="test", num_predict=32)

        call_json = mock_post.call_args[1]["json"]
        assert call_json["options"]["num_predict"] == 32

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_analyze_accepts_temperature_override(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"message": {"content": "test"}}
        )
        analyzer = VisionAnalyzer(default_model="test-model")
        img = Image.new("RGB", (100, 100))
        analyzer.analyze(img, prompt="test", temperature=0.1)

        call_json = mock_post.call_args[1]["json"]
        assert call_json["options"]["temperature"] == 0.1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/llamax1/LLAMAX7 && source backend/venv/bin/activate && python3 -m pytest backend/tests/test_vision_analyzer_params.py -v`
Expected: FAIL — `analyze()` does not accept `num_predict` or `temperature` kwargs

- [ ] **Step 3: Add num_predict and temperature params to analyze()**

In `backend/utils/vision_analyzer.py`, change the `analyze()` signature (line 175) to:
```python
def analyze(
    self,
    image: Image.Image,
    prompt: str,
    model: str = None,
    num_predict: int = 256,
    temperature: float = 0.3,
) -> VisionResult:
```

And update the options dict (line 209) to use the parameters:
```python
"options": {
    "num_predict": num_predict,
    "temperature": temperature,
},
```

Apply the same change to `analyze_base64()` (line 255).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_vision_analyzer_params.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/utils/vision_analyzer.py backend/tests/test_vision_analyzer_params.py
git commit -m "feat: parameterize num_predict/temperature in VisionAnalyzer.analyze()"
```

---

## Task 2: Resize Virtual Display to 1280x720

**Files:**
- Modify: `scripts/start_agent_display.sh:6,131-132`
- Modify: `backend/services/local_screen_backend.py:185`
- Modify: `backend/tests/test_screen_interface.py:96`

- [ ] **Step 1: Update start_agent_display.sh**

Change line 6:
```bash
RESOLUTION="1280x720x24"
```

Change lines 131-132 (Firefox launch):
```bash
--width 1280 --height 720 \
```

- [ ] **Step 2: Update local_screen_backend.py fallback**

Change line 185:
```python
return (1280, 720)  # Default to what start_agent_display.sh creates
```

- [ ] **Step 3: Update test_screen_interface.py**

Change the `screen_size()` assertion from `(1920, 1080)` to `(1280, 720)`.

**Note:** The existing tests mock `pyautogui`, but `local_screen_backend.py` was rewritten to use `xdotool` (subprocess calls). Update the mocks to patch `subprocess.run` instead of `pyautogui` methods. For `screen_size()`, mock the `_xdotool` method to return `stdout="1280 720\n"` with `returncode=0`.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest backend/tests/test_screen_interface.py -v`
Expected: PASS

- [ ] **Step 5: Restart virtual display**

```bash
./scripts/start_agent_display.sh restart
```

Verify with: `DISPLAY=:99 xdotool getdisplaygeometry` → should output `1280 720`

- [ ] **Step 6: Commit**

```bash
git add scripts/start_agent_display.sh backend/services/local_screen_backend.py backend/tests/test_screen_interface.py
git commit -m "feat: resize agent virtual display to 1280x720 for motor learning"
```

---

## Task 3: ServoController — Core Loop

This is the main component. The closed-loop motor control engine.

**Files:**
- Create: `backend/services/servo_controller.py`
- Test: `backend/tests/test_servo_controller.py`

- [ ] **Step 1: Write failing tests for ServoController**

```python
# backend/tests/test_servo_controller.py
import unittest
from unittest.mock import MagicMock, patch, call
from PIL import Image
from dataclasses import dataclass


class TestServoController(unittest.TestCase):

    def _make_screen(self, cursor_pos=(640, 360)):
        """Create a mock screen backend."""
        screen = MagicMock()
        screen.capture.return_value = (Image.new("RGB", (1280, 720)), cursor_pos)
        screen.move.return_value = {"success": True}
        screen.click.return_value = {"success": True}
        screen.cursor_position.return_value = cursor_pos
        return screen

    def _make_analyzer(self, responses):
        """Create a mock analyzer that returns responses in order."""
        analyzer = MagicMock()
        results = []
        for resp in responses:
            r = MagicMock()
            r.success = True
            r.description = resp
            r.model_used = "test-model"
            r.inference_ms = 100
            results.append(r)
        analyzer.analyze.side_effect = results
        return analyzer

    def test_on_target_first_try_clicks_immediately(self):
        from backend.services.servo_controller import ServoController
        screen = self._make_screen(cursor_pos=(400, 250))
        # First call: coordinate estimation returns target
        # Second call: on-target check returns yes
        analyzer = self._make_analyzer([
            '{"x": 400, "y": 250}',  # coordinate estimation
            '{"on_target": true}',     # on-target check
        ])
        servo = ServoController(screen, analyzer)
        result = servo.click_target("Reply button")

        assert result["success"] is True
        screen.click.assert_called_once()

    def test_one_correction_then_click(self):
        from backend.services.servo_controller import ServoController
        screen = self._make_screen(cursor_pos=(400, 250))
        analyzer = self._make_analyzer([
            '{"x": 400, "y": 250}',  # coordinate estimation
            '{"on_target": false, "direction": "right", "distance": "small"}',  # miss
            '{"on_target": true}',  # after correction, on target
        ])
        servo = ServoController(screen, analyzer)
        result = servo.click_target("Reply button")

        assert result["success"] is True
        # Should have moved once (ballistic) + once (correction) + clicked
        assert screen.move.call_count == 2
        screen.click.assert_called_once()

    def test_max_corrections_exceeded(self):
        from backend.services.servo_controller import ServoController
        screen = self._make_screen()
        # Always says "not on target" — should give up after max corrections
        analyzer = self._make_analyzer([
            '{"x": 400, "y": 250}',
            '{"on_target": false, "direction": "left", "distance": "large"}',
            '{"on_target": false, "direction": "left", "distance": "medium"}',
            '{"on_target": false, "direction": "left", "distance": "small"}',
            '{"on_target": false, "direction": "left", "distance": "small"}',
            '{"on_target": false, "direction": "left", "distance": "small"}',
        ])
        servo = ServoController(screen, analyzer, max_corrections=4)
        result = servo.click_target("Reply button")

        # Should still click at best-effort position
        assert result["success"] is True
        assert result.get("corrections", 0) == 4

    def test_oscillation_dampening(self):
        from backend.services.servo_controller import ServoController
        screen = self._make_screen(cursor_pos=(400, 250))
        analyzer = self._make_analyzer([
            '{"x": 400, "y": 250}',
            '{"on_target": false, "direction": "right", "distance": "large"}',  # go right 80
            '{"on_target": false, "direction": "left", "distance": "medium"}',  # reverse! halve to 20
            '{"on_target": true}',
        ])
        servo = ServoController(screen, analyzer)
        result = servo.click_target("Reply button")

        assert result["success"] is True
        # Check that the second move was smaller due to dampening
        moves = screen.move.call_args_list
        assert len(moves) >= 2

    def test_verification_detects_screen_change(self):
        from backend.services.servo_controller import ServoController
        screen = self._make_screen()
        # First capture: coordinate estimation. Second: on-target. Third: post-click verify (different image)
        screenshots = [
            (Image.new("RGB", (1280, 720), color=(50, 50, 50)), (400, 250)),
            (Image.new("RGB", (1280, 720), color=(50, 50, 50)), (400, 250)),
            (Image.new("RGB", (1280, 720), color=(200, 200, 200)), (400, 250)),  # screen changed
        ]
        screen.capture.side_effect = screenshots
        analyzer = self._make_analyzer([
            '{"x": 400, "y": 250}',
            '{"on_target": true}',
        ])
        servo = ServoController(screen, analyzer)
        result = servo.click_target("Reply button")
        assert result["verified"] is True

    def test_retry_on_no_screen_change(self):
        from backend.services.servo_controller import ServoController
        screen = self._make_screen()
        # Same image before and after click = miss → retry
        same_img = Image.new("RGB", (1280, 720), color=(50, 50, 50))
        diff_img = Image.new("RGB", (1280, 720), color=(200, 200, 200))
        screen.capture.side_effect = [
            (same_img, (400, 250)),  # attempt 1: estimate
            (same_img, (400, 250)),  # attempt 1: on-target check
            (same_img, (400, 250)),  # attempt 1: verify (no change → retry)
            (same_img, (400, 250)),  # attempt 2: estimate
            (same_img, (400, 250)),  # attempt 2: on-target check
            (diff_img, (400, 250)),  # attempt 2: verify (changed → success)
        ]
        analyzer = self._make_analyzer([
            '{"x": 400, "y": 250}', '{"on_target": true}',  # attempt 1
            '{"x": 405, "y": 248}', '{"on_target": true}',  # attempt 2
        ])
        servo = ServoController(screen, analyzer)
        result = servo.click_target("Reply button")
        assert result["verified"] is True
        assert result["attempt"] == 2

    def test_nudge_distances(self):
        from backend.services.servo_controller import ServoController
        assert ServoController._nudge_pixels("small") == 10
        assert ServoController._nudge_pixels("medium") == 40
        assert ServoController._nudge_pixels("large") == 80

    def test_parse_coordinate_response(self):
        from backend.services.servo_controller import ServoController
        servo = ServoController.__new__(ServoController)
        x, y = servo._parse_coordinates('{"x": 412, "y": 287}')
        assert x == 412
        assert y == 287

    def test_parse_correction_response(self):
        from backend.services.servo_controller import ServoController
        servo = ServoController.__new__(ServoController)
        correction = servo._parse_correction('{"on_target": false, "direction": "right_and_up", "dx": 12, "dy": -13}')
        assert correction["on_target"] is False
        assert correction["direction"] == "right_and_up"

    def test_screen_changed_detection(self):
        from backend.services.servo_controller import ServoController
        img_a = Image.new("RGB", (1280, 720), color=(50, 50, 50))
        img_b = Image.new("RGB", (1280, 720), color=(200, 200, 200))
        assert ServoController._screen_changed(img_a, img_b) is True
        assert ServoController._screen_changed(img_a, img_a) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest backend/tests/test_servo_controller.py -v`
Expected: FAIL — `ImportError: cannot import name 'ServoController'`

- [ ] **Step 3: Implement ServoController**

```python
# backend/services/servo_controller.py
#!/usr/bin/env python3
"""
Servo Controller — Closed-loop motor control for precise mouse targeting.

Replaces grid-based coordinate estimation with an iterative observe-correct-click
loop. The agent moves the cursor like a human: approach, observe error, correct, click.

Every interaction can be recorded by a TrainingDataCollector for self-supervised learning.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

logger = logging.getLogger(__name__)

# Nudge distances in pixels
NUDGE_MAP = {"small": 10, "medium": 40, "large": 80}

# Direction vectors
DIRECTION_MAP = {
    "left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1),
    "left_and_up": (-1, -1), "right_and_up": (1, -1),
    "left_and_down": (-1, 1), "right_and_down": (1, 1),
}

SCREEN_W, SCREEN_H = 1280, 720


class ServoController:
    """
    Closed-loop motor control for precise mouse targeting.

    Usage:
        servo = ServoController(screen, analyzer)
        result = servo.click_target("Reply button under first comment")
    """

    def __init__(self, screen, analyzer, max_corrections: int = 4, collector=None):
        """
        Args:
            screen: ScreenInterface implementation (LocalScreenBackend)
            analyzer: VisionAnalyzer instance
            max_corrections: max correction attempts before best-effort click
            collector: optional TrainingDataCollector for recording interactions
        """
        self.screen = screen
        self.analyzer = analyzer
        self.max_corrections = max_corrections
        self.collector = collector

    def click_target(self, target_description: str) -> Dict[str, Any]:
        """
        Click on a described target element using the adaptive servo loop.

        Adaptive escalation:
        - Attempt 1: ballistic move + 1 correction max. If screen changes, done.
        - Attempt 2: ballistic move + 3 corrections. If screen changes, done.
        - Attempt 3: zoom-crop area around cursor for higher-precision vision.

        Returns:
            dict with success, x, y, corrections count, time_ms, verified
        """
        from backend.utils.cursor_overlay import composite_bullseye

        start = time.time()

        for attempt in range(1, 4):
            # Adaptive correction limits per attempt
            max_corr = 1 if attempt == 1 else min(self.max_corrections, 3)
            use_zoom = attempt >= 3

            corrections_made = 0
            correction_log = []

            # 1. BALLISTIC MOVE — estimate target coordinates and move there
            # Always use full screenshot for ballistic estimation (absolute coords)
            screenshot, cursor_pos = self.screen.capture()
            annotated = composite_bullseye(screenshot, cursor_pos)
            coords = self._estimate_coordinates(annotated, target_description)
            if coords is None:
                continue  # retry with next attempt

            self.screen.move(coords[0], coords[1])
            current_x, current_y = coords

            # 2-4. OBSERVE + CORRECT loop
            prev_direction = None
            nudge_scale = 1.0

            for i in range(max_corr):
                screenshot, cursor_pos = self.screen.capture()
                annotated = composite_bullseye(screenshot, cursor_pos)

                if use_zoom:
                    cx, cy = cursor_pos
                    crop_box = (max(0, cx - 160), max(0, cy - 160),
                                min(SCREEN_W, cx + 160), min(SCREEN_H, cy + 160))
                    annotated = annotated.crop(crop_box)

                correction = self._check_on_target(annotated, target_description)

                if correction.get("on_target", False):
                    break

                direction = correction.get("direction", "")
                distance = correction.get("distance", "small")
                pixels = int(self._nudge_pixels(distance) * nudge_scale)

                # Oscillation dampening: halve nudge if direction reversed
                if prev_direction and self._direction_reversed(prev_direction, direction):
                    nudge_scale *= 0.5
                    pixels = max(5, int(self._nudge_pixels(distance) * nudge_scale))

                dx, dy = self._direction_to_delta(direction, pixels)
                new_x = max(0, min(SCREEN_W, current_x + dx))
                new_y = max(0, min(SCREEN_H, current_y + dy))

                self.screen.move(new_x, new_y)
                correction_log.append({
                    "direction": direction, "distance": distance,
                    "pixels": pixels, "from": (current_x, current_y), "to": (new_x, new_y)
                })
                current_x, current_y = new_x, new_y
                prev_direction = direction
                corrections_made += 1

            # 5. CLICK
            self.screen.click(current_x, current_y)

            # 6. VERIFY — did the screen change?
            import time as _time
            _time.sleep(0.5)  # brief wait for UI to react
            verify_shot, _ = self.screen.capture()
            screen_changed = self._screen_changed(screenshot, verify_shot)

            elapsed_ms = int((time.time() - start) * 1000)

            # Record for training
            if self.collector:
                self.collector.record(
                    screenshot_before=screenshot,
                    crosshair_pos=(current_x, current_y),
                    target_description=target_description,
                    target_actual=(current_x, current_y),
                    corrections=correction_log,
                    success=screen_changed,
                )

            if screen_changed:
                return {
                    "success": True, "verified": True,
                    "x": current_x, "y": current_y,
                    "corrections": corrections_made, "attempt": attempt,
                    "time_ms": elapsed_ms,
                }

            # Screen didn't change — click missed, try next attempt
            logger.info(f"Servo attempt {attempt} missed (screen unchanged), retrying...")

        # All attempts exhausted — return best-effort
        elapsed_ms = int((time.time() - start) * 1000)
        return {
            "success": True, "verified": False,
            "x": current_x, "y": current_y,
            "corrections": corrections_made, "attempt": 3,
            "time_ms": elapsed_ms,
        }

    @staticmethod
    def _screen_changed(before: Image.Image, after: Image.Image, threshold: float = 0.02) -> bool:
        """Check if the screen changed significantly between two captures."""
        import numpy as np
        arr_before = np.array(before.resize((320, 180))).astype(float)
        arr_after = np.array(after.resize((320, 180))).astype(float)
        diff = np.abs(arr_before - arr_after).mean() / 255.0
        return diff > threshold

    def _estimate_coordinates(self, screenshot: Image.Image, target: str) -> Optional[Tuple[int, int]]:
        """Ask vision model to estimate target coordinates."""
        prompt = (
            f"Screen is {SCREEN_W}x{SCREEN_H}. Top-left is (0,0). Bottom-right is ({SCREEN_W},{SCREEN_H}). "
            f"Where is the {target}? Respond with ONLY a JSON object: {{\"x\": 123, \"y\": 456}}"
        )
        result = self.analyzer.analyze(screenshot, prompt=prompt, num_predict=128, temperature=0.3)
        if not result.success:
            logger.error(f"Coordinate estimation failed: {result.error}")
            return None
        return self._parse_coordinates(result.description)

    def _check_on_target(self, screenshot: Image.Image, target: str) -> Dict[str, Any]:
        """Ask vision model if crosshair is on target."""
        prompt = (
            f"The crosshair (bullseye) is visible on screen. Is it directly on the {target}? "
            f"Respond with ONLY a JSON object: "
            f"{{\"on_target\": true}} or "
            f"{{\"on_target\": false, \"direction\": \"left|right|up|down|left_and_up|right_and_up|left_and_down|right_and_down\", \"distance\": \"small|medium|large\"}}"
        )
        result = self.analyzer.analyze(screenshot, prompt=prompt, num_predict=64, temperature=0.1)
        if not result.success:
            return {"on_target": False, "direction": "down", "distance": "small"}
        return self._parse_correction(result.description)

    def _parse_coordinates(self, text: str) -> Optional[Tuple[int, int]]:
        """Parse {x, y} JSON from model output."""
        try:
            text = text.strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                x = int(data.get("x", 0))
                y = int(data.get("y", 0))
                # Clamp to screen bounds
                return (max(0, min(SCREEN_W, x)), max(0, min(SCREEN_H, y)))
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"Failed to parse coordinates: {e} — raw: {text[:100]}")
        return None

    def _parse_correction(self, text: str) -> Dict[str, Any]:
        """Parse correction JSON from model output."""
        try:
            text = text.strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse correction: {e} — raw: {text[:100]}")
        return {"on_target": False, "direction": "down", "distance": "small"}

    @staticmethod
    def _nudge_pixels(distance: str) -> int:
        """Convert distance label to pixel count."""
        return NUDGE_MAP.get(distance, 10)

    @staticmethod
    def _direction_to_delta(direction: str, pixels: int) -> Tuple[int, int]:
        """Convert direction string to (dx, dy) pixel delta."""
        vec = DIRECTION_MAP.get(direction, (0, 0))
        return (vec[0] * pixels, vec[1] * pixels)

    @staticmethod
    def _direction_reversed(prev: str, current: str) -> bool:
        """Check if direction reversed (indicating oscillation)."""
        opposites = {
            "left": "right", "right": "left", "up": "down", "down": "up",
            "left_and_up": "right_and_down", "right_and_down": "left_and_up",
            "left_and_down": "right_and_up", "right_and_up": "left_and_down",
        }
        return opposites.get(prev) == current
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_servo_controller.py -v`
Expected: 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/servo_controller.py backend/tests/test_servo_controller.py
git commit -m "feat: servo controller — closed-loop motor control for mouse targeting"
```

---

## Task 4: TrainingDataCollector

**Files:**
- Create: `backend/services/training_data_collector.py`
- Test: `backend/tests/test_training_data_collector.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_training_data_collector.py
import json
import os
import tempfile
import unittest
from PIL import Image


class TestTrainingDataCollector(unittest.TestCase):

    def test_record_creates_log_entry(self):
        from backend.services.training_data_collector import TrainingDataCollector
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = TrainingDataCollector(base_dir=tmpdir)
            img = Image.new("RGB", (1280, 720))
            collector.record(
                screenshot_before=img,
                crosshair_pos=(400, 300),
                target_description="Reply button",
                target_actual=(412, 287),
                corrections=[{"direction": "right", "distance": "small", "pixels": 10}],
                success=True,
            )
            # Check log file exists and has one entry
            log_files = [f for f in os.listdir(os.path.join(tmpdir, "servo_logs")) if f.endswith(".jsonl")]
            assert len(log_files) == 1
            with open(os.path.join(tmpdir, "servo_logs", log_files[0])) as f:
                entry = json.loads(f.readline())
            assert entry["crosshair_pos"] == [400, 300]
            assert entry["target_actual"] == [412, 287]
            assert entry["success"] is True

    def test_record_saves_screenshot(self):
        from backend.services.training_data_collector import TrainingDataCollector
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = TrainingDataCollector(base_dir=tmpdir)
            img = Image.new("RGB", (1280, 720), color=(255, 0, 0))
            collector.record(
                screenshot_before=img,
                crosshair_pos=(100, 100),
                target_description="test",
                target_actual=(100, 100),
                corrections=[],
                success=True,
            )
            screenshots = os.listdir(os.path.join(tmpdir, "screenshots"))
            assert len(screenshots) == 1
            assert screenshots[0].endswith(".jpg")

    def test_mark_unreliable(self):
        from backend.services.training_data_collector import TrainingDataCollector
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = TrainingDataCollector(base_dir=tmpdir)
            img = Image.new("RGB", (1280, 720))
            collector.record(
                screenshot_before=img,
                crosshair_pos=(400, 300),
                target_description="button",
                target_actual=(400, 300),
                corrections=[],
                success=False,
            )
            log_files = [f for f in os.listdir(os.path.join(tmpdir, "servo_logs")) if f.endswith(".jsonl")]
            with open(os.path.join(tmpdir, "servo_logs", log_files[0])) as f:
                entry = json.loads(f.readline())
            assert entry["success"] is False

    def test_stats(self):
        from backend.services.training_data_collector import TrainingDataCollector
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = TrainingDataCollector(base_dir=tmpdir)
            img = Image.new("RGB", (1280, 720))
            for i in range(3):
                collector.record(
                    screenshot_before=img,
                    crosshair_pos=(i*100, i*100),
                    target_description=f"target_{i}",
                    target_actual=(i*100+5, i*100+5),
                    corrections=[],
                    success=i < 2,
                )
            stats = collector.stats()
            assert stats["total"] == 3
            assert stats["successful"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest backend/tests/test_training_data_collector.py -v`
Expected: FAIL — ImportError

- [ ] **Step 3: Implement TrainingDataCollector**

```python
# backend/services/training_data_collector.py
#!/usr/bin/env python3
"""
Training Data Collector — silently records servo interactions for model training.

Every servo loop interaction (screenshot, crosshair position, corrections, outcome)
is written to disk as labeled training data. No human labeling needed.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image

logger = logging.getLogger(__name__)


class TrainingDataCollector:
    """Records servo interactions to disk for training data generation."""

    def __init__(self, base_dir: str = None):
        root = os.environ.get("GUAARDVARK_ROOT", ".")
        self.base_dir = Path(base_dir) if base_dir else Path(root) / "data" / "training"
        self.screenshots_dir = self.base_dir / "screenshots"
        self.logs_dir = self.base_dir / "servo_logs"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._counter = 0
        self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_path = self.logs_dir / f"servo_{self._session_id}.jsonl"

    def record(
        self,
        screenshot_before: Image.Image,
        crosshair_pos: Tuple[int, int],
        target_description: str,
        target_actual: Tuple[int, int],
        corrections: List[Dict[str, Any]],
        success: bool,
        app_context: str = "",
    ):
        """Record a single servo interaction."""
        self._counter += 1
        img_name = f"{self._session_id}_{self._counter:05d}.jpg"
        img_path = self.screenshots_dir / img_name

        # Save screenshot
        screenshot_before.save(str(img_path), format="JPEG", quality=80)

        # Write log entry
        entry = {
            "timestamp": datetime.now().isoformat(),
            "screenshot_path": str(img_path),
            "crosshair_pos": list(crosshair_pos),
            "target_description": target_description,
            "target_actual": list(target_actual),
            "corrections": corrections,
            "success": success,
            "app_context": app_context,
        }

        with open(self._log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

        logger.debug(f"Recorded servo interaction #{self._counter}: {target_description} success={success}")

    def stats(self) -> Dict[str, int]:
        """Return stats about recorded data."""
        total = 0
        successful = 0
        for log_file in self.logs_dir.glob("*.jsonl"):
            with open(log_file) as f:
                for line in f:
                    entry = json.loads(line)
                    total += 1
                    if entry.get("success"):
                        successful += 1
        return {"total": total, "successful": successful, "log_dir": str(self.logs_dir)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_training_data_collector.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/training_data_collector.py backend/tests/test_training_data_collector.py
git commit -m "feat: training data collector — records servo interactions for model training"
```

---

## Task 5: Integrate Servo into AgentControlService

Replace the grid-based targeting in `agent_control_service.py` with the servo controller.

**Files:**
- Modify: `backend/services/agent_control_service.py:149-315`
- Test: `backend/tests/test_agent_control_service.py` (update existing)

- [ ] **Step 1: Update execute_task() to use ServoController**

In `backend/services/agent_control_service.py`, replace the imports and targeting logic inside `execute_task()`. The full replacement of the method body:

Remove imports (lines 160-163):
```python
from backend.utils.cursor_overlay import composite_bullseye
from backend.utils.grid_overlay import (
    overlay_grid, create_grid_spec, crop_grid_cell, refine_coordinates
)
```

Replace with:
```python
from backend.services.servo_controller import ServoController
from backend.services.training_data_collector import TrainingDataCollector
```

Initialize servo after analyzer (line 175):
```python
analyzer = VisionAnalyzer(default_model=self.config.vision_model)
collector = TrainingDataCollector()
servo = ServoController(screen, analyzer, collector=collector)
```

Replace the full per-iteration body (lines 197-296) — the SEE/ANALYZE/THINK/REFINE/ACT/VERIFY block. The complete loop body becomes:

```python
self._current_iteration = iteration

# 1. SEE — Capture screenshot (with black frame detection)
screenshot, cursor_pos = self._capture_with_retry(screen)

# 2. ANALYZE — Vision model describes the screen
vision_prompt = self._build_vision_prompt(task, self._action_history)
scene = analyzer.analyze(screenshot, prompt=vision_prompt)
if not scene.success:
    logger.error(f"Vision analysis failed: {scene.error}")
    consecutive_failures += 1
    continue

# 3. THINK — Text LLM decides next action
decision_prompt = self._build_decision_prompt(
    task, scene.description, self._action_history
)
decision_result = analyzer.text_query(decision_prompt)
if not decision_result.success:
    consecutive_failures += 1
    continue

decision = self._parse_decision(decision_result.description)

if decision.task_complete:
    return self._store_and_return(AgentResult(
        success=True, reason="completed",
        steps=self._action_history,
        total_time_seconds=time.time() - start_time
    ))

if decision.stuck:
    consecutive_failures += 1
    continue

# 4. ACT — Execute via servo (for clicks) or direct (for type/hotkey/scroll)
if decision.action.action_type == "click":
    servo_result = servo.click_target(decision.action.target_description)
    decision.action.coordinates = (servo_result.get("x", 0), servo_result.get("y", 0))
    result = {"success": servo_result.get("success", False)}
    failed = not servo_result.get("verified", False)
else:
    result = self._execute_action(decision.action, screen)
    failed = not result.get("success", False)

# 5. RECORD step
step = ActionStep(
    iteration=iteration,
    scene_description=scene.description,
    action=decision.action,
    result=result,
    failed=failed,
)
self._action_history.append(step)

if failed:
    consecutive_failures += 1
    if consecutive_failures >= self.config.max_consecutive_failures:
        logger.warning(f"Kill switch: {consecutive_failures} consecutive failures")
        self.kill()
        return self._store_and_return(AgentResult(
            success=False, reason="max_failures",
            steps=self._action_history,
            total_time_seconds=time.time() - start_time
        ))
else:
    consecutive_failures = 0
```

**Key differences from old code:**
- `click` actions go through `servo.click_target()` which handles its own coordinate estimation, corrections, and verification
- `type`, `hotkey`, `scroll` actions still use `_execute_action()` directly (no servo needed)
- Grid overlay imports and all grid-related code (overlay, crop_grid_cell, refine_coordinates) are completely removed
- Step recording and failure counting are preserved from the old code

- [ ] **Step 2: Update _build_decision_prompt — remove grid references**

Change the signature to remove `grid_spec`:
```python
def _build_decision_prompt(self, task, scene, history):
```

Remove: `available_cells = ", ".join(sorted(grid_spec.keys()))` and the "Available grid cells" line.

Remove `target_cell` from the JSON template in the prompt. Replace with:
```python
"target_description": "what you are clicking (the servo controller will find and click it precisely)",
```

Keep the browser shortcuts section and loop warning — those are still needed.

- [ ] **Step 3: Update AgentAction and _parse_decision**

`target_cell` field in `AgentAction` can stay (backwards compat) but `_parse_decision` should no longer require it. For click actions, `target_description` is the key field — the servo controller uses it to find the target.

- [ ] **Step 4: Run existing tests**

Run: `python3 -m pytest backend/tests/test_agent_control_service.py backend/tests/test_agent_control_api.py -v`
Expected: PASS (existing tests should still work since they mock the screen interface)

- [ ] **Step 5: Live test — YouTube navigation**

```bash
source backend/venv/bin/activate && timeout 120 python3 -c "
import os
os.environ['GUAARDVARK_ROOT'] = '/home/llamax1/LLAMAX7'
os.environ['GUAARDVARK_AGENT_DISPLAY'] = ':99'

from backend.services.local_screen_backend import LocalScreenBackend
from backend.services.agent_control_service import get_agent_control_service

service = get_agent_control_service()
service.start()
service.config.max_iterations = 10
service.config.verify_actions = False
service.config.vision_model = 'qwen3-vl:2b-instruct'

screen = LocalScreenBackend()
result = service.execute_task('Navigate to youtube.com', screen)
print(f'Result: {result.success}, {result.reason}, {len(result.steps)} steps')
"
```

Expected: Agent navigates to YouTube successfully via servo clicks (for URL bar) or hotkeys (Ctrl+L, type, Enter).

- [ ] **Step 6: Commit**

```bash
git add backend/services/agent_control_service.py
git commit -m "feat: integrate servo controller into agent task loop, replace grid targeting"
```

---

## Task 6: Create Training Data Directories

**Files:**
- Create directories under `data/training/`

- [ ] **Step 1: Create directories and .gitkeep files**

```bash
mkdir -p data/training/servo_logs data/training/screenshots data/training/datasets data/training/models
touch data/training/.gitkeep data/training/servo_logs/.gitkeep data/training/screenshots/.gitkeep
touch data/training/datasets/.gitkeep data/training/models/.gitkeep
```

- [ ] **Step 2: Add to .gitignore**

Add to `.gitignore`:
```
data/training/screenshots/*.jpg
data/training/servo_logs/*.jsonl
data/training/models/
```

We keep the directory structure in git but not the actual training data (too large).

- [ ] **Step 3: Commit**

```bash
git add data/training/ .gitignore
git commit -m "feat: create training data directory structure"
```

---

## Task 7: Prepare Training Set Script

Converts raw servo logs into QLoRA training JSONL format.

**Files:**
- Create: `backend/services/training/scripts/prepare_training_set.py`

- [ ] **Step 1: Write the script**

```python
# backend/services/training/scripts/prepare_training_set.py
#!/usr/bin/env python3
"""
Convert raw servo interaction logs into QLoRA training format.

Reads: data/training/servo_logs/*.jsonl
Writes: data/training/datasets/servo_train.jsonl, servo_eval.jsonl

Three training tasks generated per interaction:
1. Coordinate estimation (success=true only)
2. Correction prediction (all records)
3. On-target classification (all records)
"""

import json
import os
import random
from pathlib import Path

GUAARDVARK_ROOT = Path(os.environ.get("GUAARDVARK_ROOT", "."))
SERVO_LOGS = GUAARDVARK_ROOT / "data" / "training" / "servo_logs"
DATASETS_DIR = GUAARDVARK_ROOT / "data" / "training" / "datasets"
SCREENSHOTS_DIR = GUAARDVARK_ROOT / "data" / "training" / "screenshots"

SCREEN_W, SCREEN_H = 1280, 720


def load_servo_logs():
    """Load all servo interaction logs."""
    records = []
    for log_file in sorted(SERVO_LOGS.glob("*.jsonl")):
        with open(log_file) as f:
            for line in f:
                records.append(json.loads(line))
    return records


def generate_coordinate_examples(records):
    """Task 1: (screenshot, target_desc) → (x, y). Only from successful interactions."""
    examples = []
    for r in records:
        if not r.get("success"):
            continue
        examples.append({
            "image": r["screenshot_path"],
            "conversations": [
                {"role": "user", "content": (
                    f"Screen is {SCREEN_W}x{SCREEN_H}. "
                    f"Where is the {r['target_description']}? "
                    f"Respond with ONLY: {{\"x\": N, \"y\": N}}"
                )},
                {"role": "assistant", "content": json.dumps({
                    "x": r["target_actual"][0], "y": r["target_actual"][1]
                })},
            ]
        })
    return examples


def generate_correction_examples(records):
    """Task 2: (screenshot+crosshair, target_desc) → direction+distance."""
    examples = []
    for r in records:
        for corr in r.get("corrections", []):
            examples.append({
                "image": r["screenshot_path"],
                "conversations": [
                    {"role": "user", "content": (
                        f"The crosshair is at ({r['crosshair_pos'][0]}, {r['crosshair_pos'][1]}). "
                        f"How far is it from the {r['target_description']}? "
                        f"Respond with ONLY: {{\"on_target\": false, \"direction\": \"...\", \"distance\": \"...\"}}"
                    )},
                    {"role": "assistant", "content": json.dumps({
                        "on_target": False,
                        "direction": corr["direction"],
                        "distance": corr["distance"],
                    })},
                ]
            })
    return examples


def generate_on_target_examples(records):
    """Task 3: (screenshot+crosshair, target_desc) → on_target yes/no."""
    examples = []
    for r in records:
        if r.get("success"):
            examples.append({
                "image": r["screenshot_path"],
                "conversations": [
                    {"role": "user", "content": (
                        f"Is the crosshair directly on the {r['target_description']}? "
                        f"Respond with ONLY: {{\"on_target\": true}} or {{\"on_target\": false, ...}}"
                    )},
                    {"role": "assistant", "content": json.dumps({"on_target": True})},
                ]
            })
    return examples


def main():
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    records = load_servo_logs()
    print(f"Loaded {len(records)} servo interaction records")

    examples = []
    examples.extend(generate_coordinate_examples(records))
    examples.extend(generate_correction_examples(records))
    examples.extend(generate_on_target_examples(records))

    random.shuffle(examples)
    split = int(len(examples) * 0.8)
    train = examples[:split]
    eval_set = examples[split:]

    train_path = DATASETS_DIR / "servo_train.jsonl"
    eval_path = DATASETS_DIR / "servo_eval.jsonl"

    for path, data in [(train_path, train), (eval_path, eval_set)]:
        with open(path, "w") as f:
            for ex in data:
                f.write(json.dumps(ex) + "\n")

    print(f"Train: {len(train)} examples → {train_path}")
    print(f"Eval: {len(eval_set)} examples → {eval_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add backend/services/training/scripts/prepare_training_set.py
git commit -m "feat: prepare_training_set — converts servo logs to QLoRA training format"
```

---

## Task 8: Practice Data Generator

Automated practice sessions that click known targets on real web pages.

**Files:**
- Create: `backend/services/training/scripts/generate_practice_data.py`

- [ ] **Step 1: Write the script**

```python
# backend/services/training/scripts/generate_practice_data.py
#!/usr/bin/env python3
"""
Generate training data by running automated practice sessions.

Opens known web pages on the virtual display and directs the servo controller
to click specific targets. Every interaction is automatically recorded.

Usage:
    python3 generate_practice_data.py --rounds 50
"""

import argparse
import logging
import os
import random
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

os.environ.setdefault("GUAARDVARK_ROOT", "/home/llamax1/LLAMAX7")
os.environ.setdefault("GUAARDVARK_AGENT_DISPLAY", ":99")

# Practice targets: (url, list of target descriptions to click)
PRACTICE_PAGES = [
    ("https://www.google.com", [
        "Google Search button",
        "I'm Feeling Lucky button",
        "Gmail link in top right",
        "Images link in top right",
        "search input box",
    ]),
    ("https://www.youtube.com", [
        "search box at the top",
        "Home button in left sidebar",
        "Shorts button in left sidebar",
        "Subscriptions in left sidebar",
        "Sign in button",
    ]),
    ("https://en.wikipedia.org", [
        "search box",
        "Main page link",
        "Contents link in sidebar",
        "Random article link in sidebar",
        "English language link",
    ]),
    ("https://github.com", [
        "Sign in button",
        "search box at the top",
        "Explore link",
    ]),
]


def navigate_to(screen, url: str):
    """Navigate the agent browser to a URL."""
    screen.hotkey("ctrl", "l")
    time.sleep(0.3)
    screen.type_text(url)
    time.sleep(0.2)
    screen.hotkey("Return")
    time.sleep(4)  # wait for page load


def run_practice(rounds: int = 50):
    from backend.services.local_screen_backend import LocalScreenBackend
    from backend.services.servo_controller import ServoController
    from backend.services.training_data_collector import TrainingDataCollector
    from backend.utils.vision_analyzer import VisionAnalyzer

    screen = LocalScreenBackend()
    analyzer = VisionAnalyzer(default_model="qwen3-vl:2b-instruct")
    collector = TrainingDataCollector()
    servo = ServoController(screen, analyzer, collector=collector)

    completed = 0
    for i in range(rounds):
        page_url, targets = random.choice(PRACTICE_PAGES)
        target = random.choice(targets)

        logger.info(f"[{i+1}/{rounds}] Practice: click '{target}' on {page_url}")
        navigate_to(screen, page_url)

        result = servo.click_target(target)
        status = "HIT" if result["success"] else "MISS"
        corrections = result.get("corrections", 0)
        logger.info(f"  → {status} ({corrections} corrections, {result.get('time_ms', 0)}ms)")
        completed += 1

        time.sleep(1)

    stats = collector.stats()
    logger.info(f"\nPractice complete: {completed}/{rounds} rounds")
    logger.info(f"Training data: {stats['total']} interactions recorded")
    logger.info(f"Successful: {stats['successful']}")


def main():
    parser = argparse.ArgumentParser(description="Generate training data via practice sessions")
    parser.add_argument("--rounds", type=int, default=50, help="Number of practice rounds")
    args = parser.parse_args()
    run_practice(args.rounds)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add backend/services/training/scripts/generate_practice_data.py
git commit -m "feat: practice data generator — automated servo training sessions"
```

---

## Task 9: Model Evaluation Script

**Files:**
- Create: `backend/services/training/scripts/evaluate_model.py`

- [ ] **Step 1: Write the script**

```python
# backend/services/training/scripts/evaluate_model.py
#!/usr/bin/env python3
"""
Evaluate a vision model's servo accuracy against the held-out test set.

Metrics:
- Mean pixel error (coordinate estimation)
- On-target accuracy (classification)

Usage:
    python3 evaluate_model.py --model qwen3-vl:2b-instruct --data data/training/datasets/servo_eval.jsonl
    python3 evaluate_model.py --model gvk-eye-v1 --data data/training/datasets/servo_eval.jsonl
"""

import argparse
import json
import math
import os
from pathlib import Path

import requests
from PIL import Image

OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")


def evaluate(model: str, data_path: str, ollama_url: str = OLLAMA_URL):
    import base64
    from io import BytesIO

    records = []
    with open(data_path) as f:
        for line in f:
            records.append(json.loads(line))

    coord_errors = []
    on_target_correct = 0
    on_target_total = 0
    total = len(records)

    for i, record in enumerate(records):
        img_path = record.get("image", "")
        conversations = record.get("conversations", [])
        if not conversations or not img_path:
            continue

        user_msg = conversations[0]["content"]
        expected = conversations[1]["content"]

        # Encode image
        try:
            img = Image.open(img_path)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=70)
            img_b64 = base64.b64encode(buf.getvalue()).decode()
        except Exception:
            continue

        # Query model
        try:
            resp = requests.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": user_msg, "images": [img_b64]}],
                    "stream": False,
                    "options": {"num_predict": 128, "temperature": 0.1},
                },
                timeout=30,
            )
            if resp.status_code != 200:
                continue
            output = resp.json().get("message", {}).get("content", "").strip()
        except Exception:
            continue

        # Parse and compare
        try:
            expected_data = json.loads(expected)
            start = output.find("{")
            end = output.rfind("}") + 1
            if start >= 0 and end > start:
                actual_data = json.loads(output[start:end])
            else:
                continue

            if "x" in expected_data and "x" in actual_data:
                dx = actual_data["x"] - expected_data["x"]
                dy = actual_data["y"] - expected_data["y"]
                error = math.sqrt(dx * dx + dy * dy)
                coord_errors.append(error)

            if "on_target" in expected_data:
                on_target_total += 1
                if actual_data.get("on_target") == expected_data["on_target"]:
                    on_target_correct += 1

        except (json.JSONDecodeError, KeyError):
            continue

        if (i + 1) % 10 == 0:
            print(f"  Evaluated {i+1}/{total}...")

    # Report
    print(f"\n=== Evaluation: {model} ===")
    print(f"Total records: {total}")
    if coord_errors:
        mean_err = sum(coord_errors) / len(coord_errors)
        median_err = sorted(coord_errors)[len(coord_errors) // 2]
        print(f"Coordinate estimation: {len(coord_errors)} samples")
        print(f"  Mean pixel error: {mean_err:.1f}px")
        print(f"  Median pixel error: {median_err:.1f}px")
    if on_target_total:
        acc = on_target_correct / on_target_total * 100
        print(f"On-target accuracy: {acc:.1f}% ({on_target_correct}/{on_target_total})")

    return {
        "model": model,
        "mean_pixel_error": sum(coord_errors) / len(coord_errors) if coord_errors else None,
        "on_target_accuracy": on_target_correct / on_target_total if on_target_total else None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Ollama model name")
    parser.add_argument("--data", required=True, help="Path to eval JSONL")
    args = parser.parse_args()
    evaluate(args.model, args.data)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add backend/services/training/scripts/evaluate_model.py
git commit -m "feat: model evaluator — measures pixel error and on-target accuracy"
```

---

## Task 10: End-to-End Live Verification

Run the full pipeline on a real task to verify everything works together.

**Files:** None (verification only)

- [ ] **Step 1: Restart virtual display at 1280x720**

```bash
./scripts/start_agent_display.sh restart
sleep 5
DISPLAY=:99 xdotool getdisplaygeometry  # should print: 1280 720
```

- [ ] **Step 2: Screenshot to verify display**

```python
import os, mss
from PIL import Image
os.environ["DISPLAY"] = ":99"
with mss.mss() as s:
    shot = s.grab(s.monitors[1])
    img = Image.frombytes("RGB", shot.size, shot.rgb)
    img.save("/tmp/verify_1280x720.png")
    print(f"Size: {img.size}")  # should be (1280, 720)
```

- [ ] **Step 3: Test servo click on YouTube**

Navigate to YouTube, then use the servo controller to click the search box:

```python
import os
os.environ['GUAARDVARK_ROOT'] = '/home/llamax1/LLAMAX7'
os.environ['GUAARDVARK_AGENT_DISPLAY'] = ':99'

from backend.services.local_screen_backend import LocalScreenBackend
from backend.services.servo_controller import ServoController
from backend.services.training_data_collector import TrainingDataCollector
from backend.utils.vision_analyzer import VisionAnalyzer

screen = LocalScreenBackend()
analyzer = VisionAnalyzer(default_model="qwen3-vl:2b-instruct")
collector = TrainingDataCollector()
servo = ServoController(screen, analyzer, collector=collector)

# Navigate to YouTube
screen.hotkey("ctrl", "l")
import time; time.sleep(0.3)
screen.type_text("https://www.youtube.com")
time.sleep(0.2)
screen.hotkey("Return")
time.sleep(5)

# Servo click the search box
result = servo.click_target("YouTube search box at the top of the page")
print(f"Result: {result}")
print(f"Training stats: {collector.stats()}")
```

Expected: Servo clicks the search box. Training data is recorded to `data/training/`.

- [ ] **Step 4: Test full agent task — navigate + click**

```python
from backend.services.agent_control_service import get_agent_control_service
service = get_agent_control_service()
service.start()
service.config.max_iterations = 10
service.config.verify_actions = False
service.config.vision_model = "qwen3-vl:2b-instruct"

screen = LocalScreenBackend()
result = service.execute_task("Search YouTube for 'never gonna give you up'", screen)
print(f"Success: {result.success}, Steps: {len(result.steps)}")
```

- [ ] **Step 5: Verify training data was recorded**

```bash
ls -la data/training/servo_logs/
ls -la data/training/screenshots/ | head -20
cat data/training/servo_logs/*.jsonl | python3 -c "import sys,json; lines=[json.loads(l) for l in sys.stdin]; print(f'{len(lines)} interactions recorded')"
```

- [ ] **Step 6: Commit all working changes**

```bash
git add -A
git commit -m "feat: motor learning agent v1 — servo controller + training pipeline verified end-to-end"
```

---

## Task 11 (Future): Fine-Tune First gvk-eye Model

This task runs after enough training data has accumulated (~500+ interactions from real use and practice sessions).

**Files:**
- Create: `backend/services/training/scripts/register_model.py`

- [ ] **Step 1: Run practice sessions to accumulate data**

```bash
cd /home/llamax1/LLAMAX7
source backend/venv/bin/activate
python3 backend/services/training/scripts/generate_practice_data.py --rounds 100
```

- [ ] **Step 2: Prepare training set**

```bash
python3 backend/services/training/scripts/prepare_training_set.py
```

- [ ] **Step 3: Evaluate baseline model**

```bash
python3 backend/services/training/scripts/evaluate_model.py \
    --model qwen3-vl:2b-instruct \
    --data data/training/datasets/servo_eval.jsonl
```

Record baseline metrics.

- [ ] **Step 4: Fine-tune gvk-eye-v1**

```bash
python3 backend/services/training/scripts/finetune_vision.py train \
    --base unsloth/Qwen2.5-VL-2B-Instruct \
    --data data/training/datasets/servo_train.jsonl \
    --images data/training/screenshots \
    --name gvk-eye-v1 \
    --steps 500
```

- [ ] **Step 5: Write register_model.py and deploy**

```python
# backend/services/training/scripts/register_model.py
#!/usr/bin/env python3
"""Register a fine-tuned model with Ollama."""

import argparse
import os
import subprocess
from pathlib import Path

MODELS_DIR = Path(os.environ.get("GUAARDVARK_ROOT", ".")) / "data" / "training" / "models"


def register(model_name: str, base_model: str = "qwen3-vl:2b-instruct"):
    lora_dir = MODELS_DIR / model_name / "lora"
    if not lora_dir.exists():
        print(f"LoRA adapter not found at {lora_dir}")
        return

    modelfile = MODELS_DIR / model_name / "Modelfile"
    modelfile.write_text(
        f"FROM {base_model}\n"
        f"ADAPTER {lora_dir}\n"
    )
    subprocess.run(["ollama", "create", model_name, "-f", str(modelfile)], check=True)
    print(f"Registered {model_name} in Ollama")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="Model name (e.g., gvk-eye-v1)")
    parser.add_argument("--base", default="qwen3-vl:2b-instruct")
    args = parser.parse_args()
    register(args.name, args.base)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Evaluate fine-tuned model**

```bash
python3 backend/services/training/scripts/evaluate_model.py \
    --model gvk-eye-v1 \
    --data data/training/datasets/servo_eval.jsonl
```

Compare against baseline. If better on both metrics, promote.

- [ ] **Step 7: Update agent config to use gvk-eye-v1**

In `backend/services/agent_control_service.py`, change `AgentControlConfig`:
```python
vision_model: str = "gvk-eye-v1"
```

- [ ] **Step 8: Commit**

```bash
git add backend/services/training/scripts/register_model.py backend/services/agent_control_service.py
git commit -m "feat: deploy gvk-eye-v1 — first fine-tuned motor learning model"
```
