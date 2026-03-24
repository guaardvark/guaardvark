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

NUDGE_MAP = {"small": 10, "medium": 40, "large": 80}

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
        self.screen = screen
        self.analyzer = analyzer
        self.max_corrections = max_corrections
        self.collector = collector

    def click_target(self, target_description: str) -> Dict[str, Any]:
        """
        Click on a described target element using the adaptive servo loop.

        Adaptive escalation:
        - Attempt 1: ballistic move + 1 correction max. If screen changes, done.
        - Attempt 2: ballistic move + up to 3 corrections. If screen changes, done.
        - Attempt 3: zoom-crop area around cursor for higher-precision vision.
        """
        from backend.utils.cursor_overlay import composite_bullseye

        start = time.time()
        current_x, current_y = 0, 0
        corrections_made = 0

        for attempt in range(1, 4):
            max_corr = 1 if attempt == 1 else min(self.max_corrections, 3)
            use_zoom = attempt >= 3

            corrections_made = 0
            correction_log = []

            # 1. BALLISTIC MOVE — full screenshot for absolute coordinate estimation
            screenshot, cursor_pos = self.screen.capture()
            annotated = composite_bullseye(screenshot, cursor_pos)
            coords = self._estimate_coordinates(annotated, target_description)
            if coords is None:
                continue

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
            time.sleep(0.5)
            verify_shot, _ = self.screen.capture()
            screen_changed = self._screen_changed(screenshot, verify_shot)

            elapsed_ms = int((time.time() - start) * 1000)

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

            logger.info(f"Servo attempt {attempt} missed (screen unchanged), retrying...")

        elapsed_ms = int((time.time() - start) * 1000)
        return {
            "success": True, "verified": False,
            "x": current_x, "y": current_y,
            "corrections": corrections_made, "attempt": 3,
            "time_ms": elapsed_ms,
        }

    @staticmethod
    def _screen_changed(before: Image.Image, after: Image.Image, threshold: float = 0.02) -> bool:
        import numpy as np
        arr_before = np.array(before.resize((320, 180))).astype(float)
        arr_after = np.array(after.resize((320, 180))).astype(float)
        diff = np.abs(arr_before - arr_after).mean() / 255.0
        return bool(diff > threshold)

    def _estimate_coordinates(self, screenshot: Image.Image, target: str) -> Optional[Tuple[int, int]]:
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
        try:
            text = text.strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                x = int(data.get("x", 0))
                y = int(data.get("y", 0))
                return (max(0, min(SCREEN_W, x)), max(0, min(SCREEN_H, y)))
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"Failed to parse coordinates: {e} — raw: {text[:100]}")
        return None

    def _parse_correction(self, text: str) -> Dict[str, Any]:
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
        return NUDGE_MAP.get(distance, 10)

    @staticmethod
    def _direction_to_delta(direction: str, pixels: int) -> Tuple[int, int]:
        vec = DIRECTION_MAP.get(direction, (0, 0))
        return (vec[0] * pixels, vec[1] * pixels)

    @staticmethod
    def _direction_reversed(prev: str, current: str) -> bool:
        opposites = {
            "left": "right", "right": "left", "up": "down", "down": "up",
            "left_and_up": "right_and_down", "right_and_down": "left_and_up",
            "left_and_down": "right_and_up", "right_and_up": "left_and_down",
        }
        return opposites.get(prev) == current
