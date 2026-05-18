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
        self._last_inference_ms: int = 0
        # Distinguishes "vision Ollama call itself failed" (transient, worth
        # retrying or surfacing as a different signal) from "vision succeeded
        # but the target genuinely isn't on screen". Both used to collapse
        # to None → "target_not_visible", which primed the model to abandon
        # targets that were actually present but Ollama was just slow.
        self._last_failure_reason: str = ""

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
          4. Verify — Differential Pixel Comparison (DPC) polling
          5. Record — to training archive
        """
        start = time.time()

        # 1. SEE — capture + vision-model coordinate estimate
        screenshot, _ = self.screen.capture()
        self._last_failure_reason = ""
        coords = self._estimate_coordinates(screenshot, target_description)
        if coords is None:
            # Distinguish transient vision-call failure from genuine
            # target-not-on-screen. The former is worth one retry; the
            # latter is a real signal the model should pivot on.
            failure_reason = self._last_failure_reason or "target_not_visible"
            if failure_reason == "vision_call_failed":
                logger.warning(
                    f"Servo: vision call failed for \"{target_description}\" — "
                    f"retrying once after 0.5s (Ollama may be busy)"
                )
                time.sleep(0.5)
                self._last_failure_reason = ""
                screenshot, _ = self.screen.capture()
                coords = self._estimate_coordinates(screenshot, target_description)
                if coords is None:
                    failure_reason = self._last_failure_reason or "target_not_visible"
            if coords is None:
                elapsed_ms = int((time.time() - start) * 1000)
                if failure_reason == "vision_call_failed":
                    log_msg = (
                        f"Servo: vision call failed twice for \"{target_description}\" "
                        f"— Ollama unresponsive, no click issued"
                    )
                else:
                    log_msg = f"Servo: target not visible (\"{target_description}\"), no click"
                logger.info(log_msg)
                self._record_interaction(
                    screenshot=screenshot,
                    target_description=target_description,
                    coords=(0, 0),
                    success=False,
                    target_found=False,
                    click_issued=False,
                    elapsed_ms=elapsed_ms,
                    reason=failure_reason,
                    post_action_effect="not_checked",
                )
                return {
                    "success": False, "verified": False,
                    "target_found": False, "click_issued": False,
                    "post_action_effect": "not_checked",
                    "x": 0, "y": 0,
                    "corrections": 0, "attempt": 1,
                    "time_ms": elapsed_ms,
                    "reason": failure_reason,
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
                target_found=True,
                click_issued=False,
                elapsed_ms=elapsed_ms,
                reason="move_failed",
                post_action_effect="not_checked",
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

        # Briefly wait for cursor to settle before the hardware click
        time.sleep(0.1)

        # 3. CLICK
        click_result = self.screen.click(x, y, button=button)
        click_issued = bool(click_result.get("success", False))

        # 4. VERIFY — Differential Pixel Comparison (DPC) Polling
        # High-frequency, localized polling centered on the action site.
        # This replaces the brittle fixed-time wait with an adaptive deadline.
        verified = False
        post_action_effect = "no_visible_change"
        
        if click_issued:
            poll_start = time.time()
            deadline = 3.0
            while time.time() - poll_start < deadline:
                time.sleep(0.25)  # 4Hz polling
                shot_after, _ = self.screen.capture()
                if self._screen_changed(screenshot, shot_after, click_pos=(x, y)):
                    verified = True
                    post_action_effect = "changed"
                    break

        elapsed_ms = int((time.time() - start) * 1000)
        logger.info(f"Servo: clicked \"{target_description}\" at ({x}, {y}) effect={post_action_effect} ({elapsed_ms}ms)")

        # 5. RECORD
        self._record_interaction(
            screenshot=screenshot,
            target_description=target_description,
            coords=(x, y),
            success=click_issued,
            target_found=True,
            click_issued=click_issued,
            elapsed_ms=elapsed_ms,
            reason="" if click_issued else click_result.get("error", "click_failed"),
            post_action_effect=post_action_effect,
        )

        return {
            "success": click_issued, "verified": verified,
            "target_found": True, "click_issued": click_issued,
            "post_action_effect": post_action_effect,
            "x": x, "y": y,
            "corrections": 0, "attempt": 1,
            "time_ms": elapsed_ms,
            "reason": "" if click_issued else "click_failed",
            "error": click_result.get("error"),
            "detection_source": self._last_detection_source,
            "parse_path": self._last_parse_path,
        }

    def calibrate(self) -> Dict[str, Any]:
        """
        Interactive calibration routine (The "Optician").
        Navigates to a local calibration page and measures vision drift.
        """
        import os
        from pathlib import Path
        
        root = os.environ.get("GUAARDVARK_ROOT", ".")
        cal_file = Path(root) / "backend" / "static" / "calibrate.html"
        cal_url = f"file://{cal_file.resolve()}"
        
        logger.info(f"Starting Optician calibration at {cal_url}")
        
        # 1. Navigate to calibration page
        # We'll use the browser directly via xdotool to avoid task_execute recursion
        self.screen.hotkey("ctrl", "l")
        time.sleep(0.5)
        self.screen.type_text(cal_url)
        time.sleep(0.5)
        self.screen.hotkey("Return")
        time.sleep(5) # Wait for page load
        
        points = [
            {"label": "P1 (top-left)", "target": [100, 100]},
            {"label": "P2 (top-right)", "target": [1180, 100]},
            {"label": "P3 (bottom-left)", "target": [100, 620]},
            {"label": "P4 (bottom-right)", "target": [1180, 620]},
            {"label": "P5 (center)", "target": [640, 360]},
        ]
        
        results = []
        
        for p in points:
            logger.info(f"Calibrating {p['label']}...")
            screenshot, _ = self.screen.capture()
            # Explicitly use pass 1 only for anchor points
            coords = self._estimate_coordinates(screenshot, f"red crosshair labeled {p['label'].split(' ')[0]}")
            if coords:
                gx, gy = coords
                results.append({
                    "target": p["target"],
                    "detected": [gx, gy],
                    "error": [gx - p["target"][0], gy - p["target"][1]]
                })
            else:
                logger.warning(f"Failed to detect crosshair {p['label']}")
        
        if len(results) < 3:
            return {"success": False, "error": "Not enough calibration points detected"}
            
        # Calculate scale factors
        # For simplicity, we use the average scale across all detected points
        scale_xs = [r["target"][0] / (r["detected"][0] / 1000 * self.screen_w) for r in results if r["detected"][0] > 0]
        scale_ys = [r["target"][1] / (r["detected"][1] / 1000 * self.screen_h) for r in results if r["detected"][1] > 0]
        
        # Actually, our _estimate_coordinates already applies current scaling.
        # We want the RAW model coordinates vs Target pixels.
        # Let's adjust the logic to use raw detections if possible.
        # But _estimate_coordinates currently returns scaled pixels.
        
        return {
            "success": True,
            "points_count": len(results),
            "results": results,
            "screen_size": [self.screen_w, self.screen_h]
        }


    def _record_interaction(
        self,
        screenshot: Image.Image,
        target_description: str,
        coords: Tuple[int, int],
        success: bool,
        target_found: bool,
        click_issued: bool,
        elapsed_ms: int,
        reason: str = "",
        post_action_effect: str = "",
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
            "target_found": target_found,
            "click_issued": click_issued,
            "post_action_effect": post_action_effect,
            "reason": reason,
            "inference_ms": self._last_inference_ms,
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
                target_found=target_found,
                click_issued=click_issued,
                post_action_effect=post_action_effect,
                reason=reason,
                inference_ms=self._last_inference_ms,
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
        """Find where the target is on screen using a Two-Pass Zoom-In pipeline.

        1. Anchor Pass: Find the macro-region or element on the full screen.
        2. ROI Crop: Extract a 300x300 (or scaled) crop around the anchor.
        3. Refinement Pass: Pinpoint the exact interactive point on the crop.
        4. Translation: Map local crop coordinates back to global space.
        """
        # Try DOM shortcut first — instant if Firefox has the element.
        # Gated behind dom_assist_enabled() (default off). Viewport→screen
        # translation has known gaps that misplace clicks; vision path below
        # is the calibrated default.
        try:
            from backend.services.dom_metadata_extractor import dom_assist_enabled
            _dom_on = dom_assist_enabled()
        except Exception:
            _dom_on = False
        if _dom_on:
            dom_coords = self._lookup_dom_coordinates(target)
            if dom_coords:
                self._last_raw_coords = dom_coords
                self._last_scale = (1.0, 1.0)
                self._last_raw_response = ""
                self._last_parse_path = "dom"
                self._last_detection_source = "dom"
                return dom_coords

        # --- PASS 1: ANCHOR PASS ---
        # Get the macro-region (bounding box) on the full screen.
        # Asking explicitly for 'box_2d' normalized to 1000.
        prompt_pass1 = (
            f"Detect the {target}. Reply with ONLY a JSON list "
            f'[{{"box_2d": [y1, x1, y2, x2], "label": "{target}"}}] '
            f"with coordinates normalized to 1000. If the target is not visible, "
            f"reply with an empty list []."
        )
        result1 = self.analyzer.analyze_fullsize(
            screenshot, prompt=prompt_pass1, num_predict=128, temperature=0.1
        )
        if not result1.success or not result1.description:
            # Vision Ollama call itself failed (timeout, network, model
            # unloaded). This is transient and NOT the same as "target not
            # on screen". The caller (click_target) will see this reason
            # and retry once before reporting back to the agent.
            logger.error(f"Anchor pass failed: {result1.error}")
            self._last_failure_reason = "vision_call_failed"
            return None

        # Parse Anchor (accepts point or box, but box is better for ROI)
        anchor_coords = self._parse_detection_response(result1.description)
        if anchor_coords is None:
            anchor_coords = self._parse_coordinates(result1.description)

        if anchor_coords is None:
            # Vision succeeded but said "target not present" (empty list,
            # null detection, or malformed response). This is a real signal.
            self._last_failure_reason = "target_not_visible"
            return None

        ax, ay = anchor_coords
        
        # --- PASS 2: REFINEMENT PASS (ZOOM-IN) ---
        # Extract a localized crop centered on the anchor
        crop_size = 300
        left = max(0, int(ax - crop_size // 2))
        top = max(0, int(ay - crop_size // 2))
        right = min(self.screen_w, left + crop_size)
        bottom = min(self.screen_h, top + crop_size)
        
        # Adjust if we hit right/bottom edges
        if right == self.screen_w: left = max(0, right - crop_size)
        if bottom == self.screen_h: top = max(0, bottom - crop_size)
        
        crop = screenshot.crop((left, top, right, bottom))
        # Scale up the crop to give the model more detail for the refinement pass
        crop_scaled = crop.resize((crop_size * 2, crop_size * 2), Image.LANCZOS)
        
        # Refinement Pass: ask for a precise 'point' [y, x] on the crop
        prompt_pass2 = (
            f"Point at the {target} within this localized crop. "
            f"Reply with ONLY a JSON list [{{\"point\": [y, x], \"label\": \"{target}\"}}] "
            f"normalized to 1000. Be extremely precise."
        )
        result2 = self.analyzer.analyze_fullsize(
            crop_scaled, prompt=prompt_pass2, num_predict=128, temperature=0.1
        )
        
        if result2.success and result2.description:
            refinement = self._parse_detection_response(result2.description)
            if refinement:
                lx, ly = refinement
                # Translate local crop coords back to global space
                # lx/1000 * actual_crop_width + left_offset
                gx = int((lx / 1000.0) * (right - left) + left)
                gy = int((ly / 1000.0) * (bottom - top) + top)
                
                # Phase 1.4: Apply global calibration offsets
                ox = self._vision_config.get("offset_x", 0)
                oy = self._vision_config.get("offset_y", 0)
                gx += ox
                gy += oy
                
                self._last_raw_coords = (gx, gy)
                self._last_parse_path = "zoom_refinement"
                self._last_detection_source = "vision"
                self._last_inference_ms = getattr(result1, "inference_ms", 0) + getattr(result2, "inference_ms", 0)
                
                # Clamp to screen bounds
                x = max(0, min(self.screen_w - 1, gx))
                y = max(0, min(self.screen_h - TASKBAR_H - 1, gy))
                
                logger.info(f"Servo Zoom-In: anchor ({ax},{ay}) -> refined ({gx},{gy}) (offset {ox},{oy}) -> final ({x},{y})")
                return (x, y)

        # Fallback to anchor if refinement fails
        raw_x, raw_y = anchor_coords
        ox = self._vision_config.get("offset_x", 0)
        oy = self._vision_config.get("offset_y", 0)
        raw_x += ox
        raw_y += oy
        
        self._last_raw_coords = (raw_x, raw_y)
        self._last_parse_path = "anchor_only"
        self._last_detection_source = "vision"
        self._last_inference_ms = getattr(result1, "inference_ms", 0)
        
        # Clamp to screen bounds
        x = max(0, min(self.screen_w - 1, int(raw_x)))
        y = max(0, min(self.screen_h - TASKBAR_H - 1, int(raw_y)))
        
        logger.info(f"Servo coords (anchor fallback): ({x}, {y}) (offset {ox},{oy}) model={getattr(self.analyzer, 'default_model', '?')}")
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
            #     (Gemma4, moondream all return [x, y]).
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
