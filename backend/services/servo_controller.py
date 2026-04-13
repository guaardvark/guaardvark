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

from backend.services.servo_knowledge_store import get_reflex, get_scale_factors, get_servo_archive

logger = logging.getLogger(__name__)

# Nudge distances pulled from reflexes (Tier 1) — self-improvement engine can tune these
NUDGE_MAP = {
    "small": get_reflex("nudge_small", 10),
    "medium": get_reflex("nudge_medium", 40),
    "large": get_reflex("nudge_large", 80),
}

DIRECTION_MAP = {
    "left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1),
    "left_and_up": (-1, -1), "right_and_up": (1, -1),
    "left_and_down": (-1, 1), "right_and_down": (1, 1),
}

TASKBAR_H = 30  # tint2 taskbar at the bottom — never click here


class ServoController:
    """
    Closed-loop motor control for precise mouse targeting.

    Usage:
        servo = ServoController(screen, analyzer)
        result = servo.click_target("Reply button under first comment")
    """

    def __init__(self, screen, analyzer, max_corrections: int = 4, collector=None, vision_config: Dict = None):
        self.screen = screen
        self.analyzer = analyzer
        self.max_corrections = max_corrections
        self.collector = collector
        # The vision config tells us what scale factors the model *theoretically* needs.
        # We record these honestly in the archive — the self-improvement engine
        # decides when (if ever) to actually apply scaling.
        self._vision_config = vision_config or {}

        # Get the actual screen size from the backend — no more hardcoded 1024x1024!
        # This fixes the "horizontally stretched vision" bug on 1280x720 screens.
        self.screen_w, self.screen_h = self.screen.screen_size()
        logger.info(f"Servo initialized for {self.screen_w}x{self.screen_h} screen")

    def click_target(self, target_description: str, button: str = "left", single_attempt: bool = False) -> Dict[str, Any]:
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
        all_corrections = []  # accumulate across all attempts for the archive

        max_attempts = 1 if single_attempt else 3
        for attempt in range(1, max_attempts + 1):
            # Attempt 1: ballistic only (no correction), trust the scaling
            # Attempt 2: ballistic + 1 correction
            # Attempt 3: ballistic + full corrections + zoom
            max_corr = 0 if attempt == 1 else (1 if attempt == 2 else min(self.max_corrections, 3))
            use_zoom = attempt >= 3

            # Pause between attempts — gives time to re-observe and think
            if attempt > 1:
                time.sleep(5.0)

            corrections_made = 0
            correction_log = []

            # 1. BALLISTIC MOVE — fresh screenshot for coordinate estimation
            screenshot, cursor_pos = self.screen.capture()
            # Send CLEAN screenshot for detection — the bullseye overlay can confuse
            # the model into thinking the crosshair is the target
            coords = self._estimate_coordinates(screenshot, target_description)
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
                                min(self.screen_w, cx + 160), min(self.screen_h, cy + 160))
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
                new_x = max(0, min(self.screen_w, current_x + dx))
                new_y = max(0, min(self.screen_h - TASKBAR_H, current_y + dy))

                self.screen.move(new_x, new_y)
                corr_entry = {
                    "direction": direction, "distance": distance,
                    "pixels": pixels, "from": (current_x, current_y), "to": (new_x, new_y)
                }
                correction_log.append(corr_entry)
                all_corrections.append(corr_entry)
                current_x, current_y = new_x, new_y
                prev_direction = direction
                corrections_made += 1

            # 5. CLICK
            self.screen.click(current_x, current_y, button=button)

            # 6. VERIFY — did the screen change?
            time.sleep(0.3)
            verify_shot, _ = self.screen.capture()
            screen_changed = self._screen_changed(screenshot, verify_shot, click_pos=(current_x, current_y))

            # Success = the screen actually changed after we clicked.
            # Previously this also counted "ballistic with no corrections" as success,
            # which poisoned training data — clicks that hit nothing were recorded as hits.
            click_landed = screen_changed

            elapsed_ms = int((time.time() - start) * 1000)

            if self.collector:
                self.collector.record(
                    screenshot_before=screenshot,
                    crosshair_pos=(current_x, current_y),
                    target_description=target_description,
                    target_actual=(current_x, current_y),
                    corrections=correction_log,
                    success=click_landed,
                )

            # Record to Tier 2 knowledge archive (universal, model-agnostic)
            raw = getattr(self, '_last_raw_coords', (0, 0))
            scale = getattr(self, '_last_scale', (1.0, 1.0))
            model_name = getattr(self.analyzer, 'default_model', 'unknown')
            try:
                archive = get_servo_archive()
                archive.record(
                    target_description=target_description,
                    model_used=model_name,
                    raw_model_coords=raw,
                    scaled_coords=coords if coords else (0, 0),
                    actual_click_coords=(current_x, current_y),
                    scale_factor=scale,
                    success=click_landed,
                    corrections=corrections_made,
                    attempt=attempt,
                    time_ms=elapsed_ms,
                    correction_log=correction_log,
                )
            except Exception as e:
                logger.debug(f"Archive record failed (non-fatal): {e}")

            if click_landed:
                return {
                    "success": True, "verified": screen_changed,
                    "x": current_x, "y": current_y,
                    "corrections": corrections_made, "attempt": attempt,
                    "time_ms": elapsed_ms,
                }

            logger.info(f"Servo attempt {attempt} missed (screen unchanged), retrying...")

        # All 3 attempts exhausted without screen change — this is a failure.
        # Recording it honestly is critical for training data quality.
        elapsed_ms = int((time.time() - start) * 1000)

        if self.collector:
            self.collector.record(
                screenshot_before=screenshot,
                crosshair_pos=(current_x, current_y),
                target_description=target_description,
                target_actual=(current_x, current_y),
                corrections=[],
                success=False,
            )

        raw = getattr(self, '_last_raw_coords', (0, 0))
        scale = getattr(self, '_last_scale', (1.0, 1.0))
        model_name = getattr(self.analyzer, 'default_model', 'unknown')
        try:
            archive = get_servo_archive()
            archive.record(
                target_description=target_description,
                model_used=model_name,
                raw_model_coords=raw,
                scaled_coords=(current_x, current_y),
                actual_click_coords=(current_x, current_y),
                scale_factor=scale,
                success=False,
                corrections=corrections_made,
                attempt=3,
                time_ms=elapsed_ms,
                correction_log=all_corrections,
            )
        except Exception as e:
            logger.debug(f"Archive record failed (non-fatal): {e}")

        return {
            "success": False, "verified": False,
            "x": current_x, "y": current_y,
            "corrections": corrections_made, "attempt": 3,
            "time_ms": elapsed_ms,
        }

    @staticmethod
    def _screen_changed(before: Image.Image, after: Image.Image,
                        click_pos: Tuple[int, int] = None, threshold: float = 0.005) -> bool:
        """Check if the screen changed. Uses both global and local comparison.

        Global: any 0.5% mean pixel change across the whole screen.
        Local (if click_pos given): any 2% change in a 200x200 area around the click.
        Either passing means the screen changed.
        """
        import numpy as np
        # Global check (lowered threshold — subtle changes matter)
        arr_before = np.array(before.resize((320, 180))).astype(float)
        arr_after = np.array(after.resize((320, 180))).astype(float)
        global_diff = np.abs(arr_before - arr_after).mean() / 255.0
        if global_diff > threshold:
            return True

        # Local check around click position (catches cursor blinks, button highlights)
        if click_pos:
            x, y = click_pos
            r = 100  # 100px radius
            box = (max(0, x - r), max(0, y - r),
                   min(before.width, x + r), min(before.height, y + r))
            local_before = np.array(before.crop(box)).astype(float)
            local_after = np.array(after.crop(box)).astype(float)
            local_diff = np.abs(local_before - local_after).mean() / 255.0
            if local_diff > 0.02:
                return True

        return False

    def _lookup_dom_coordinates(self, target: str) -> Optional[Tuple[int, int]]:
        """Try to find target coordinates from DOM metadata — no vision call needed.

        Fuzzy-matches the target description against interactive elements
        extracted from Firefox's DOM. Returns center coords if confident match found.
        """
        try:
            from backend.services.dom_metadata_extractor import DOMMetadataExtractor
            snapshot = DOMMetadataExtractor.get_instance().extract()
            if not snapshot.success or not snapshot.elements:
                return None

            target_lower = target.lower()
            best_match = None
            best_score = 0

            for el in snapshot.elements:
                score = 0
                el_text = (el.text or "").lower()

                # Text content match
                if el_text and el_text in target_lower:
                    score = len(el_text) / max(len(target_lower), 1)
                elif el_text and target_lower in el_text:
                    score = len(target_lower) / max(len(el_text), 1)

                # ID or name match
                if el.id and el.id.lower() in target_lower:
                    score = max(score, 0.8)
                if el.name and el.name.lower() in target_lower:
                    score = max(score, 0.7)

                # Element type match (e.g., "search box" matches input[text])
                if el.element_type:
                    et = el.element_type.lower()
                    if et in target_lower or (et == "text" and "search" in target_lower):
                        score = max(score, 0.5)

                # Tag match (e.g., "button" in target and el is a button)
                if el.tag in target_lower:
                    score = max(score, 0.3)

                if score > best_score and score >= 0.4:
                    best_score = score
                    best_match = el

            if best_match:
                logger.info(
                    f"Servo DOM shortcut: \"{target}\" → \"{best_match.text[:30]}\" "
                    f"at ({best_match.cx},{best_match.cy}) score={best_score:.2f}"
                )
                return (best_match.cx, best_match.cy)

        except Exception as e:
            logger.debug(f"DOM lookup failed (non-fatal): {e}")

        return None

    def _estimate_coordinates(self, screenshot: Image.Image, target: str) -> Optional[Tuple[int, int]]:
        """Find where the target is on screen.

        Priority: DOM metadata → native detection (full-size image) → legacy fallback.

        CRITICAL: The image must be sent at FULL resolution (1024x1024).
        Through Ollama with think:false, Gemma4 returns box_2d coordinates
        normalized to 1024. With a 1024x1024 screen, coord/1024*1024 = identity.
        Empirically verified 2026-04-10: full-size → 35px error (HIT),
        resized → 263px error (MISS).
        """
        # Try DOM shortcut first — instant if Firefox has the element
        dom_coords = self._lookup_dom_coordinates(target)
        if dom_coords:
            self._last_raw_coords = dom_coords
            self._last_scale = (1.0, 1.0)
            return dom_coords

        # Hallucination guard — ask if the target actually exists
        check = self.analyzer.analyze(
            screenshot,
            prompt=f"Is there a {target} visible in this image? Answer ONLY yes or no.",
            num_predict=32, temperature=0.1,
        )
        if check.success:
            answer = check.description.strip().lower()
            if answer.startswith("no") or "not visible" in answer or "don't see" in answer:
                logger.info(f"Servo: target not visible (\"{target}\"), skipping click")
                return None

        # Full-size image — resize destroys coordinate accuracy
        prompt = f"detect {target}"
        result = self.analyzer.analyze_fullsize(
            screenshot, prompt=prompt, num_predict=256, temperature=0.1
        )
        if not result.success:
            logger.error(f"Coordinate estimation failed: {result.error}")
            return None

        # Parse detection response — handles both "point" and "box_2d" formats
        coords = self._parse_detection_response(result.description)
        if coords is None:
            # Fall back to legacy {"x","y"} format
            coords = self._parse_coordinates(result.description)

        if coords is None:
            return None

        raw_x, raw_y = coords
        self._last_raw_coords = coords
        self._last_scale = (1.0, 1.0)  # raw pixels, no scaling needed

        # Clamp to screen bounds
        x = max(0, min(self.screen_w, int(raw_x)))
        y = max(0, min(self.screen_h - TASKBAR_H, int(raw_y)))

        logger.info(f"Servo coords: ({x}, {y}) raw=({raw_x},{raw_y}) model={getattr(self.analyzer, 'default_model', '?')}")
        return (x, y)

    def _check_on_target(self, screenshot: Image.Image, target: str) -> Dict[str, Any]:
        prompt = (
            f'Is the crosshair on the {target}? Reply ONLY JSON: '
            f'{{"on_target": true}} or '
            f'{{"on_target": false, "direction": "left|right|up|down", "distance": "small|medium|large"}}'
        )
        result = self.analyzer.analyze(screenshot, prompt=prompt, num_predict=128, temperature=0.1)
        if not result.success:
            return {"on_target": False, "direction": "down", "distance": "small"}
        return self._parse_correction(result.description)

    def _parse_detection_response(self, text: str) -> Optional[Tuple[int, int]]:
        """Parse detection response — handles both point and box_2d formats.

        box_2d coordinates are normalized to the model's internal grid:
          Gemma4: 1000 (confirmed by Google docs)
          qwen3-vl: 1024
        The divisor comes from vision_config["internal_width"].
        """
        try:
            text = text.strip()
            if "```json" in text:
                start = text.index("```json") + 7
                end = text.index("```", start)
                text = text[start:end].strip()
            elif "```" in text:
                start = text.index("```") + 3
                end = text.index("```", start)
                text = text[start:end].strip()

            arr_start = text.find("[")
            arr_end = text.rfind("]") + 1
            if arr_start < 0 or arr_end <= arr_start:
                return None

            data = json.loads(text[arr_start:arr_end])
            if not data or not isinstance(data, list):
                return None

            entry = data[0]

            # Format 1: "point" — raw pixel coordinates [x, y]
            point = entry.get("point")
            if point and len(point) == 2:
                x, y = int(point[0]), int(point[1])
                logger.info(f"Servo: point [{point[0]},{point[1]}] → ({x},{y}) label=\"{entry.get('label', '?')}\"")
                return (x, y)

            # Format 2: bounding box normalized to model's internal grid
            # Gemma4 via Ollama: [x1,y1,x2,y2] (empirically verified, not y-first like Google HF spec)
            box = entry.get("box_2d") or entry.get("bbox_2d")
            if box and len(box) == 4:
                grid = self._vision_config.get("internal_width", 1000)
                if grid > 0:
                    norm = [int(c) / grid for c in box]
                    cx = int(((norm[0] + norm[2]) / 2) * self.screen_w)
                    cy = int(((norm[1] + norm[3]) / 2) * self.screen_h)
                else:
                    # Raw pixel mode (internal_width: 0)
                    cx = int((int(box[0]) + int(box[2])) / 2)
                    cy = int((int(box[1]) + int(box[3])) / 2)
                
                logger.info(
                    f"Servo: box {box} (grid={grid}) → center ({cx},{cy}) "
                    f"label=\"{entry.get('label', '?')}\""
                )
                return (cx, cy)

            return None

        except (json.JSONDecodeError, ValueError, TypeError, KeyError, IndexError) as e:
            logger.debug(f"box_2d parse failed (will try legacy): {e}")
            return None

    def _parse_coordinates(self, text: str) -> Optional[Tuple[float, float]]:
        try:
            text = text.strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                raw_x = data.get("x", 0)
                raw_y = data.get("y", 0)
                # Handle model returning lists like {"x": [531, 544], "y": 544}
                if isinstance(raw_x, list):
                    raw_x = raw_x[0] if raw_x else 0
                if isinstance(raw_y, list):
                    raw_y = raw_y[0] if raw_y else 0
                x = float(raw_x)
                y = float(raw_y)
                return (x, y)
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
