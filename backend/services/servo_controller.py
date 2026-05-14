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
        self._last_raw_coords: Tuple[int, int] = (0, 0)
        self._last_scale: Tuple[float, float] = (1.0, 1.0)
        self._last_raw_response: str = ""
        self._last_parse_path: str = ""
        self._last_detection_source: str = ""

        # Get the actual screen size from the backend — no more hardcoded 1024x1024!
        # This fixes the "horizontally stretched vision" bug on 1280x720 screens.
        self.screen_w, self.screen_h = self.screen.screen_size()
        logger.info(f"Servo initialized for {self.screen_w}x{self.screen_h} screen")

    def click_target(self, target_description: str, button: str = "left", single_attempt: bool = False) -> Dict[str, Any]:
        """
        Click on a described target element. ONE-SHOT, human-pattern:
          1. See — capture screen, ask vision model to locate target
          2. Move — cursor to those coords (via screen.move → xdotool)
          3. Click — at those coords (via screen.click → xdotool)
          4. Record — to training archive

        That's it. No multi-attempt retry, no bullseye correction loop, no
        post-click pixel-diff verify. The whole point of the feature is for
        the vision model to drive a mouse and keyboard like a human would,
        not to behave like a bot pixel-comparing its way to certainty. A
        miss is a miss; the loop above can SEE the result and decide what
        to do next.

        REWRITTEN 2026-05-13 — was a 3-attempt retry with screen-change
        verify. The verify was producing false-failures when a click landed
        correctly but didn't produce a big pixel diff (e.g., clicking a
        launcher for a Firefox window that was already running just focuses
        it). Plus the retry storm — three clicks within 16s on the same
        target — looked bot-shaped on anti-automation surfaces like YouTube.
        Human pattern is single click → see what happened → decide.

        `single_attempt` kwarg is preserved for back-compat; ignored now
        (every click is one attempt).
        """
        start = time.time()

        # 1. SEE — capture + vision-model coordinate estimate
        screenshot, _ = self.screen.capture()
        coords = self._estimate_coordinates(screenshot, target_description)
        if coords is None:
            elapsed_ms = int((time.time() - start) * 1000)
            logger.info(f"Servo: target not visible (\"{target_description}\"), no click")
            self._record_interaction(
                screenshot=screenshot,
                target_description=target_description,
                coords=(0, 0),
                success=False,
                click_issued=False,
                elapsed_ms=elapsed_ms,
                reason="target_not_visible",
            )
            return {
                "success": False, "verified": False,
                "target_found": False, "click_issued": False,
                "post_action_effect": "not_checked",
                "x": 0, "y": 0,
                "corrections": 0, "attempt": 1,
                "time_ms": elapsed_ms,
                "reason": "target_not_visible",
                "detection_source": self._last_detection_source,
            }

        x, y = coords
        # 2. MOVE
        move_result = self.screen.move(x, y)
        if not move_result.get("success", False):
            elapsed_ms = int((time.time() - start) * 1000)
            self._record_interaction(
                screenshot=screenshot,
                target_description=target_description,
                coords=(x, y),
                success=False,
                click_issued=False,
                elapsed_ms=elapsed_ms,
                reason="move_failed",
            )
            return {
                "success": False, "verified": False,
                "target_found": True, "click_issued": False,
                "post_action_effect": "not_checked",
                "x": x, "y": y,
                "corrections": 0, "attempt": 1,
                "time_ms": elapsed_ms,
                "reason": "move_failed",
                "error": move_result.get("error", "move failed"),
                "detection_source": self._last_detection_source,
            }
        # 3. CLICK
        click_result = self.screen.click(x, y, button=button)
        elapsed_ms = int((time.time() - start) * 1000)
        click_issued = bool(click_result.get("success", False))

        # 4. RECORD — training data still captured; success=True because
        # the click physically happened. If it missed the visual target,
        # the post-click SEE in the agent loop will reveal that and the
        # model can decide its next move.
        self._record_interaction(
            screenshot=screenshot,
            target_description=target_description,
            coords=(x, y),
            success=click_issued,
            click_issued=click_issued,
            elapsed_ms=elapsed_ms,
            reason="" if click_issued else click_result.get("error", "click_failed"),
        )

        return {
            "success": click_issued, "verified": False,
            "target_found": True, "click_issued": click_issued,
            "post_action_effect": "pending_observation",
            "x": x, "y": y,
            "corrections": 0, "attempt": 1,
            "time_ms": elapsed_ms,
            "reason": "" if click_issued else "click_failed",
            "error": click_result.get("error"),
            "detection_source": self._last_detection_source,
            "parse_path": self._last_parse_path,
        }

    def _record_interaction(
        self,
        screenshot: Image.Image,
        target_description: str,
        coords: Tuple[int, int],
        success: bool,
        click_issued: bool,
        elapsed_ms: int,
        reason: str = "",
    ) -> None:
        """Record telemetry without treating predicted coords as ground truth."""
        x, y = coords
        raw = getattr(self, "_last_raw_coords", (0, 0))
        scale = getattr(self, "_last_scale", (1.0, 1.0))
        model_name = getattr(self.analyzer, "default_model", "unknown")
        metadata = {
            "model": model_name,
            "vision_config_source": self._vision_config.get("source", ""),
            "raw_response": self._last_raw_response,
            "parse_path": self._last_parse_path,
            "detection_source": self._last_detection_source,
            "screen_size": [self.screen_w, self.screen_h],
            "click_issued": click_issued,
            "reason": reason,
        }
        if self.collector:
            try:
                self.collector.record(
                    screenshot_before=screenshot,
                    crosshair_pos=(x, y),
                    target_description=target_description,
                    target_actual=(x, y),
                    corrections=[],
                    success=success,
                    metadata=metadata,
                )
            except Exception as e:
                logger.debug(f"Collector record failed (non-fatal): {e}")

        try:
            archive = get_servo_archive()
            archive.record(
                target_description=target_description,
                model_used=model_name,
                raw_model_coords=raw,
                scaled_coords=coords,
                actual_click_coords=(x, y),
                scale_factor=scale,
                success=success,
                corrections=0,
                attempt=1,
                time_ms=elapsed_ms,
                screen_size=(self.screen_w, self.screen_h),
                correction_log=[],
                raw_response=self._last_raw_response,
                parse_path=self._last_parse_path,
                detection_source=self._last_detection_source,
                vision_config=self._vision_config,
                click_issued=click_issued,
                reason=reason,
            )
        except Exception as e:
            logger.debug(f"Archive record failed (non-fatal): {e}")

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

        CRITICAL: The image must be sent at FULL resolution (1000x1000).
        Through Ollama with think:false, Gemma4 returns box_2d coordinates
        normalized to 1000 (Google's published spec). With a 1000x1000 screen,
        coord/1000*1000 = identity. Empirically verified 2026-04-10 on the
        old 1280x720 layout: full-size → 35px error (HIT),
        resized → 263px error (MISS). The 2026-05-11 1024 drift centred every
        bbox around X≈600 — symptom of a model that knows the target exists
        but lost the grid mapping.
        """
        # Try DOM shortcut first — instant if Firefox has the element
        dom_coords = self._lookup_dom_coordinates(target)
        if dom_coords:
            self._last_raw_coords = dom_coords
            self._last_scale = (1.0, 1.0)
            self._last_raw_response = ""
            self._last_parse_path = "dom"
            self._last_detection_source = "dom"
            return dom_coords

        # HALLUCINATION GUARD REMOVED 2026-05-12 — was a separate analyze() call
        # asking "Is there a {target} visible? yes/no" before detection. Verified
        # via servo logs that it false-negatived desktop targets (blue dot dead-
        # center on white background, qwen3-vl:2b answered "no") and blocked
        # detection from ever running. analyze_fullsize() below already returns
        # None when the target isn't found (parser handles empty/invalid output),
        # so the guard was a redundant second point of failure. Re-enable only
        # if false-positive clicks on noisy desktops become a problem.
        #
        # check = self.analyzer.analyze(
        #     screenshot,
        #     prompt=f"Is there a {target} visible in this image? Answer ONLY yes or no.",
        #     num_predict=32, temperature=0.1,
        # )
        # if check.success:
        #     answer = check.description.strip().lower()
        #     if answer.startswith("no") or "not visible" in answer or "don't see" in answer:
        #         logger.info(f"Servo: target not visible (\"{target}\"), skipping click")
        #         return None

        # Full-size image — resize destroys coordinate accuracy.
        # Prompt phrasing matters: `detect {target}` produces prose ("The icon
        # is in the center-right of the image") that `_parse_detection_response`
        # can't extract coordinates from, so the servo logs "target not visible"
        # even when the model literally sees the target. Asking explicitly for
        # box_2d in Google's documented format (object-wrapped, list-of-dicts)
        # gets the parser's existing happy path. Verified 2026-05-13:
        # `detect Firefox icon` → prose; this prompt → list of {box_2d, label}
        # objects on the same screenshot.
        prompt = (
            f"Point at the {target}. Reply with ONLY a JSON list "
            f'[{{"box_2d": [y1, x1, y2, x2], "label": "{target}"}}] '
            f"with coordinates normalized to 1000. If the target is not visible, "
            f"reply with an empty list []."
        )
        result = self.analyzer.analyze_fullsize(
            screenshot, prompt=prompt, num_predict=256, temperature=0.1
        )
        if not result.success:
            logger.error(f"Coordinate estimation failed: {result.error}")
            self._last_raw_response = result.error or ""
            self._last_parse_path = "vision_error"
            self._last_detection_source = "vision"
            return None
        self._last_raw_response = result.description or ""
        self._last_detection_source = "vision"

        # Parse detection response — handles both "point" and "box_2d" formats
        coords = self._parse_detection_response(result.description)
        if coords is None:
            # Fall back to legacy {"x","y"} format
            coords = self._parse_coordinates(result.description)
            if coords is not None:
                self._last_parse_path = "legacy_xy"

        if coords is None:
            return None

        raw_x, raw_y = coords
        self._last_raw_coords = coords
        self._last_scale = (1.0, 1.0)  # raw pixels, no scaling needed

        # Clamp to screen bounds
        x = max(0, min(self.screen_w - 1, int(raw_x)))
        y = max(0, min(self.screen_h - TASKBAR_H - 1, int(raw_y)))

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
          qwen3-vl: 1000 (Google standard)
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

            # Find the first valid JSON structure (array or object)
            obj_start = text.find("{")
            arr_start = text.find("[")
            
            if obj_start >= 0 and (arr_start < 0 or obj_start < arr_start):
                start = obj_start
                end = text.rfind("}") + 1
            elif arr_start >= 0:
                start = arr_start
                end = text.rfind("]") + 1
            else:
                return None
                
            if start < 0 or end <= start:
                return None

            data = json.loads(text[start:end])
            
            if isinstance(data, list) and data:
                entry = data[0]
                # Tolerate bare 4-int arrays as box_2d ([y1, x1, y2, x2]).
                # Gemma4 sometimes returns the array directly without the
                # {"box_2d": [...], "label": "..."} wrapper, even when asked
                # for the object form. Wrap it so the box-handling branch
                # below picks it up uniformly.
                if isinstance(entry, (int, float)) and len(data) == 4:
                    entry = {"box_2d": [int(v) for v in data]}
                elif not isinstance(entry, dict):
                    return None
            elif isinstance(data, dict):
                entry = data
            else:
                return None

            # Axis order varies *by format*, not just by model:
            #   - "point" is x-first across every model we've tested
            #     (Gemma4, qwen3-vl, moondream all return [x, y]).
            #   - "box_2d" follows Google's published format which is
            #     y-first ([y1, x1, y2, x2]). Some adapters re-emit it
            #     x-first, so the order is config-driven.
            # vision_config.coord_order applies only to box_2d / bbox_2d.
            # "xy" → [x1, y1, x2, y2]; "yx" → [y1, x1, y2, x2]. Default is
            # xy for back-compat. Gemma4 explicitly sets "yx".
            coord_order = (self._vision_config or {}).get("coord_order", "xy")

            # Format 1: "point" — always [x, y], all models.
            point = entry.get("point")
            if point and len(point) == 2:
                px, py = int(point[0]), int(point[1])
                grid = self._vision_config.get("internal_width", 1000) if self._vision_config else 1000
                if 0 <= px <= grid and 0 <= py <= grid and (px > self.screen_w or py > self.screen_h):
                    px = int((px / grid) * self.screen_w)
                    py = int((py / grid) * self.screen_h)
                    self._last_parse_path = "point_normalized"
                else:
                    self._last_parse_path = "point"
                logger.info(
                    f"Servo: point {point} → ({px},{py}) "
                    f"label=\"{entry.get('label', '?')}\""
                )
                return (px, py)

            # Format 2: bounding box, optionally normalized to model's grid.
            # coord_order decides the axis order of the four numbers.
            box = entry.get("box_2d") or entry.get("bbox_2d")
            if box and len(box) == 4:
                if coord_order == "yx":
                    y1, x1, y2, x2 = (int(c) for c in box)
                else:
                    x1, y1, x2, y2 = (int(c) for c in box)

                grid = self._vision_config.get("internal_width", 1000) if self._vision_config else 1000
                if grid > 0:
                    cx = int(((x1 + x2) / 2 / grid) * self.screen_w)
                    cy = int(((y1 + y2) / 2 / grid) * self.screen_h)
                else:
                    cx = (x1 + x2) // 2
                    cy = (y1 + y2) // 2
                
                if x1 == 0 and x2 == 0 and y1 == 0 and y2 == 0:
                    logger.warning(f"Servo: box [0,0,0,0] received (ignoring as null detection)")
                    return None
                if abs(x2 - x1) < 1 or abs(y2 - y1) < 1:
                    logger.warning(f"Servo: tiny/degenerate box {box} received (ignoring)")
                    return None

                self._last_parse_path = "box_2d"
                logger.info(
                    f"Servo: box {box} order={coord_order} grid={grid} → center ({cx},{cy}) "
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
