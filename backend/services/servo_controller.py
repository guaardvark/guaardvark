#!/usr/bin/env python3
"""
Servo Controller — Closed-loop motor control for precise mouse targeting.

Replaces grid-based coordinate estimation with an iterative observe-correct-click
loop. The agent moves the cursor like a human: approach, observe error, correct, click.

Every interaction can be recorded by a TrainingDataCollector for self-supervised learning.
"""

import json
import math
import logging
import os
import time
from dataclasses import dataclass, field
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

CORRECTION_MODES = ("off", "shadow", "on", "auto")
CORRECTION_ENV = "GUAARDVARK_SERVO_CORRECTION"
PROBE_RING = (220, 30, 30)   # the correction probe's marker: red, unlike the cursor reticle
PROBE_MARKER_PX = 48
PROBE_CROP_MIN = 320
PROBE_CROP_MAX = 600
SEED_HALF_MAX = 600.0
FINAL_DRIFT_MAX = 250.0


@dataclass
class CorrectionOutcome:
    """What one run of the correction loop did, and why it stopped."""
    estimate: Tuple[int, int]
    final: Tuple[int, int]
    mode: str
    armed_reason: str
    stop_reason: str = ""
    steps: List[Dict[str, Any]] = field(default_factory=list)
    elapsed_ms: int = 0
    applied: bool = False
    clamped: bool = False
    unparsed: int = 0

    @property
    def drift_px(self) -> float:
        ex, ey = self.estimate
        fx, fy = self.final
        return ((fx - ex) ** 2 + (fy - ey) ** 2) ** 0.5

    def summary(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "armed_reason": self.armed_reason,
            "stop_reason": self.stop_reason,
            "estimate": list(self.estimate),
            "final": list(self.final),
            "drift_px": round(self.drift_px, 1),
            "steps": len(self.steps),
            "applied": self.applied,
            "clamped": self.clamped,
            "unparsed": self.unparsed,
            "elapsed_ms": self.elapsed_ms,
        }


class ServoController:
    """Aim, look again, click.

    One shot by default: the eye estimates where the target is, the cursor
    moves, the click lands, and a pixel-change poll verifies the screen
    reacted. When the eye's measured accuracy is coarser than the target, or
    unknown, a correction loop runs first: a red marker is drawn at the
    estimate on the captured frame, the eye is asked only which way the
    target lies from the marker's centre, and a search box narrows until it
    says "same" or the budget runs out. The mouse never moves during probes.
    In shadow mode the loop runs and is logged but the estimate is clicked;
    in on mode the final is. The numbers live in REFLEXES["correction_*"]
    (servo_knowledge_store) next to what they were measured against.

    Usage:
        servo = ServoController(screen, analyzer)
        result = servo.click_target("Reply button under first comment")
    """

    def __init__(self, screen, analyzer, max_corrections: int = 4, collector=None,
                 vision_config: Dict = None, eye_accuracy_px: Optional[float] = None,
                 eye_judge_rate: Optional[float] = None):
        self.screen = screen
        self.analyzer = analyzer
        self.max_corrections = max_corrections
        self.collector = collector
        # Measured median pointing error of this eye on this screen, from the
        # measurement store; None when unmeasured. The correction loop arms on
        # it: an eye coarser than the target earns a second look.
        self.eye_accuracy_px = eye_accuracy_px
        # How often this eye judges a marker's offset right (None: unmeasured).
        self.eye_judge_rate = eye_judge_rate
        # Correction loop. Precedence: an explicit vision_config value, then the
        # environment, then the reflex. "explicit" is what lets a training
        # (single_attempt) run exercise the loop when it asks for it.
        self.correction_mode, self._correction_explicit = self._resolve_correction_mode(vision_config or {})
        if self.correction_mode == "auto":
            # On for an eye measured to judge well, where it roughly doubled
            # hits; shadow for an unmeasured one, whose judging is unknown and
            # whose shadow rows are the evidence for measuring it.
            from backend.services.model_capability_data import EYE_JUDGE_MIN_BOTH_RATE
            good = self.eye_judge_rate is not None and self.eye_judge_rate >= EYE_JUDGE_MIN_BOTH_RATE
            self.correction_mode = "on" if good else "shadow"
        self._corrections_armed_this_session = 0
        self._last_correction_skip = ""
        # Optional TrainerTruthProbe (trainer_truth_probe.py) — attached by the
        # agent loop during training sessions. When present, clicks on the
        # vision trainer get TRUE hit/miss labels from the page's own
        # scoreboard instead of only the DPC pixel proxy. None = no probing.
        self.truth_probe = None
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

        # Tier-1.5 runtime calibration (restored 2026-08-01): measured linear
        # correction for the model's raw anchor bias (e.g. the Y-center-pull
        # that dragged top-of-screen anchors ~150px down and pushed targets out
        # of the refine crop). Loaded per (model, resolution); None = identity.
        # A calibration RUN measures raw output, so it passes
        # vision_config={"disable_calibration": True} to see the uncorrected eye.
        self._calibration = None
        if not (self._vision_config or {}).get("disable_calibration"):
            try:
                from backend.services.servo_knowledge_store import load_servo_calibration
                model_name = getattr(analyzer, "default_model", "") or ""
                self._calibration = load_servo_calibration(model_name, self.screen_w, self.screen_h)
                if self._calibration:
                    logger.info(f"Servo calibration active for {model_name}: {self._calibration}")
            except Exception as e:
                logger.debug(f"servo calibration load skipped: {e}")

        # Coordinate convention. An explicit value in vision_config always wins —
        # that is how a hand-measured row and the eye bake-off's deliberate
        # overrides keep working. Everything else asks the capability resolver
        # rather than falling back to a silent "xy", which contradicted the very
        # prompt this class sends ("box_2d: [y1, x1, y2, x2]") and, on any model
        # without its own row, clicked with the axes swapped. Measured on
        # gemma4:e4b: 326px median error read the wrong way against 63px read
        # the right way, on the same frames.
        self._coords = None
        if not self._vision_config.get("coord_order"):
            try:
                from backend.services.model_capability_resolver import coords_for
                conv = coords_for(getattr(analyzer, "default_model", "") or "",
                                  (self.screen_w, self.screen_h))
                self._coords = conv
                if conv.order:
                    self._vision_config = dict(self._vision_config)
                    self._vision_config["coord_order"] = conv.order
                    # Assign, never setdefault: a grid of None means absolute
                    # pixels, and 0 is how the parser is told that. setdefault
                    # would let the text-only default's 1000 leak through and
                    # divide raw pixels by a thousand.
                    self._vision_config["internal_width"] = conv.grid or 0
                    self._vision_config.setdefault("coord_style", conv.style)
                    logger.info(
                        "Servo coordinate convention for %s: %s %s/%s (%s, confidence %.2f)",
                        getattr(analyzer, "default_model", "?"), conv.style, conv.order,
                        conv.grid, conv.source, conv.confidence,
                    )
                else:
                    logger.warning(
                        "Servo has no known coordinate convention for %s (%s) — clicks "
                        "will be refused rather than guessed. Run "
                        "backend.tools.probe_coord_order.",
                        getattr(analyzer, "default_model", "?"), conv.source,
                    )
            except Exception as e:  # noqa: BLE001
                logger.debug("coordinate convention lookup skipped: %s", e)

        logger.info(f"Servo initialized for {self.screen_w}x{self.screen_h} screen")

    def _apply_calibration(self, x: int, y: int) -> Tuple[int, int]:
        """Invert the measured aim bias, clamped on-screen. Identity when no
        calibration is loaded.

        Model families (see servo_knowledge_store.load_servo_calibration):
          linear      truth ≈ (raw − a)/b per axis
          radial      truth ≈ C + (raw − C)/k  — the operator's X-leg insight: the eye
                      pulls raw output toward screen center along the spoke
          piecewise_y identity below elbow (eye accurate there); linear above

        Applied to ANCHOR coordinates only — the anchor places the refine crop,
        so correcting it here fixes both the crop window and the anchor-fallback
        path. The refine pass is crop-relative and stays untouched.
        """
        cal = self._calibration
        if not cal:
            return (x, y)
        family = cal.get("model", "linear")
        if family == "radial":
            ccx, ccy = cal["cx"], cal["cy"]
            k = cal["k"]
            cx = ccx + (x - ccx) / k
            cy = ccy + (y - ccy) / k
        elif family == "piecewise_y":
            cx = float(x)
            cy = float(y) if y >= cal["elbow"] else (y - cal["a_y"]) / cal["b_y"]
        else:  # linear (legacy)
            cx = (x - cal["a_x"]) / cal["b_x"]
            cy = (y - cal["a_y"]) / cal["b_y"]
        cx = max(0, min(self.screen_w - 1, int(round(cx))))
        cy = max(0, min(self.screen_h - 1, int(round(cy))))
        return (cx, cy)

    def read_point(self, answer: Dict[str, Any],
                   image_size: Optional[Tuple[int, int]] = None) -> Optional[Tuple[int, int]]:
        """Screen coordinates for a point the model gave itself, in its own dialect.

        The model's convention and this machine's calibration for it are how its
        numbers are read, the same as for the servo's own anchor call. No second
        look: the model saw the frame and chose the spot.
        """
        try:
            coords = self._parse_detection_response(json.dumps(answer), image_size=image_size)
        except (TypeError, ValueError):
            coords = None
        if coords is None:
            return None
        x, y = coords
        if self._calibration:
            x, y = self._apply_calibration(x, y)
        return (max(0, min(self.screen_w - 1, int(x))), max(0, min(self.screen_h - 1, int(y))))

    def click_point(self, x: int, y: int, target_description: str = "",
                    button: str = "left", screenshot: Optional[Image.Image] = None,
                    answer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Click where the model itself placed the target and record it.

        Nothing here judges the result: the model's next look at the screen does.
        """
        start = time.time()
        move = self.screen.move(x, y)
        clicked = {"success": False, "error": move.get("error", "move failed")}
        if move.get("success", False):
            time.sleep(0.1)
            clicked = self.screen.click(x, y, button=button)
        issued = bool(clicked.get("success", False))
        elapsed_ms = int((time.time() - start) * 1000)
        self._last_raw_response = f"native: {json.dumps(answer)}" if answer else ""
        self._last_parse_path = "native"
        self._last_detection_source = "native"
        logger.info(f"Servo: clicked \"{target_description}\" at ({x}, {y}) where the model placed it")
        if screenshot is not None:
            self._record_interaction(
                screenshot=screenshot, target_description=target_description,
                coords=(x, y), success=issued, target_found=True, click_issued=issued,
                elapsed_ms=elapsed_ms, reason="" if issued else clicked.get("error", "click_failed"),
                post_action_effect="pending_observation",
            )
        return {
            "success": issued, "verified": False, "target_found": True, "click_issued": issued,
            "post_action_effect": "pending_observation", "x": x, "y": y,
            "corrections": 0, "attempt": 1, "time_ms": elapsed_ms,
            "reason": "" if issued else "click_failed", "error": clicked.get("error"),
            "detection_source": "native", "parse_path": "native",
        }

    def locate_target(self, target_description: str) -> Dict[str, Any]:
        """Find a described target on screen. SEE only — no move, no click,
        no archive write. Returns coords + parse telemetry for callers that
        need to act on the coordinates themselves (drag source/destination,
        double-click, hover).

        Returns:
            { "found": bool, "x": int, "y": int, "reason": str,
              "detection_source": str, "parse_path": str,
              "inference_ms": int }
        """
        screenshot, _ = self.screen.capture()
        self._last_failure_reason = ""
        coords = self._estimate_coordinates(screenshot, target_description)
        if coords is None:
            return {
                "found": False,
                "x": 0, "y": 0,
                "reason": self._last_failure_reason or "target_not_visible",
                "detection_source": self._last_detection_source,
                "parse_path": self._last_parse_path,
                "inference_ms": self._last_inference_ms,
            }
        return {
            "found": True,
            "x": coords[0], "y": coords[1],
            "reason": "",
            "detection_source": self._last_detection_source,
            "parse_path": self._last_parse_path,
            "inference_ms": self._last_inference_ms,
        }

    def click_target(self, target_description: str, button: str = "left", single_attempt: bool = False,
                     precision: Optional[bool] = None, attempt: int = 1) -> Dict[str, Any]:
        """
        Click on a described target element, human-pattern:
          1. See — capture screen, ask vision model to locate target
          1b. Look again — the correction loop, when the eye's measured
              accuracy says the estimate deserves it (see _should_correct)
          2. Move — cursor to those coords (via screen.move → xdotool)
          3. Click — at those coords (via screen.click → xdotool)
          4. Verify — Differential Pixel Comparison (DPC) polling
          5. Record — to training archive

        single_attempt: a training run; the loop stays off unless the mode was
            set explicitly for this run.
        precision: True arms the loop (the caller's second try at the same
            target); False forbids it; None leaves it to the accuracy gate.
        attempt: the caller's attempt number at this target, recorded as-is.
        """
        start = time.time()
        correction: Optional[CorrectionOutcome] = None
        self._last_correction_skip = ""

        # 1. SEE — capture + vision-model coordinate estimate
        screenshot, _ = self.screen.capture()
        # Truth before-probe MUST pair with this capture: on a hit the trainer
        # respawns the dot synchronously, so the target position that labels
        # this frame is only readable now. Inert (None) off the trainer page.
        truth_before = self.truth_probe.before() if self.truth_probe else None
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
        # 1b. LOOK AGAIN
        armed, armed_reason = self._should_correct(target_description, single_attempt, precision, screenshot)
        if armed:
            self._corrections_armed_this_session += 1
            correction = self._correct_estimate(screenshot, target_description, (x, y), armed_reason)
            if correction.applied:
                x, y = correction.final
        else:
            self._last_correction_skip = armed_reason
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
                correction=correction,
                attempt=attempt,
            )
            return {
                "success": False, "verified": False,
                "target_found": True, "click_issued": False,
                "post_action_effect": "not_checked",
                "x": x, "y": y,
                "corrections": len(correction.steps) if correction else 0, "attempt": attempt,
                "correction": correction.summary() if correction else None,
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

        # Truth after-probe: read the trainer's own scoreboard delta — the
        # honest outcome the DPC proxy can't see (a miss-marker also changes
        # pixels). Tri-state; None fields when off the trainer.
        truth = None
        if self.truth_probe and truth_before is not None and click_issued:
            truth = self.truth_probe.after(truth_before)

        elapsed_ms = int((time.time() - start) * 1000)
        logger.info(f"Servo: clicked \"{target_description}\" at ({x}, {y}) effect={post_action_effect} ({elapsed_ms}ms)"
                    + (f" true_hit={truth.get('true_hit')}" if truth else ""))

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
            truth=truth,
            correction=correction,
            attempt=attempt,
        )

        return {
            "success": click_issued, "verified": verified,
            "target_found": True, "click_issued": click_issued,
            "post_action_effect": post_action_effect,
            "x": x, "y": y,
            "corrections": len(correction.steps) if correction else 0, "attempt": attempt,
            "correction": correction.summary() if correction else None,
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
        truth: Dict[str, Any] | None = None,
        correction: Optional[CorrectionOutcome] = None,
        attempt: int = 1,
    ) -> None:
        """Record telemetry without treating predicted coords as ground truth."""
        x, y = coords
        corr_summary = correction.summary() if correction else None
        corr_log = [
            {"direction": st.get("direction", ""), "dx": st.get("dx"), "dy": st.get("dy"),
             "probe": st.get("probe"), "visible": st.get("visible")}
            for st in (correction.steps if correction else [])
            if st.get("direction")
        ]
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
        # Ground truth from the trainer page (TrainerTruthProbe), when present.
        # ADDITIVE — success/click_issued/post_action_effect keep their old
        # meanings so pre-truth rows stay comparable. Dataset builders should
        # gate on truth.true_hit and label from (target_cx, target_cy).
        if truth is not None:
            metadata["truth"] = truth
        if corr_summary is not None:
            metadata["correction"] = corr_summary
        else:
            metadata["correction_skip"] = self._last_correction_skip
        metadata["attempt"] = attempt
        if self.collector:
            try:
                self.collector.record(
                    screenshot_before=screenshot,
                    crosshair_pos=(x, y),
                    target_description=target_description,
                    target_actual=(x, y),
                    corrections=corr_log,
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
                corrections=len(corr_log),
                attempt=attempt,
                time_ms=elapsed_ms,
                screen_size=(self.screen_w, self.screen_h),
                correction_log=corr_log,
                raw_response=self._last_raw_response,
                parse_path=self._last_parse_path,
                detection_source=self._last_detection_source,
                vision_config=self._vision_config,
                target_found=target_found,
                click_issued=click_issued,
                post_action_effect=post_action_effect,
                reason=reason,
                inference_ms=self._last_inference_ms,
                truth=truth,
                correction=corr_summary,
                correction_skip="" if corr_summary else self._last_correction_skip,
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

    def _anchor_request(self, target: str):
        """(prompt, num_predict) for the anchor pass, from the eye's dialect.

        Token budget comes from the convention too. A thinking-variant model
        that ignores Ollama's think:false spends its budget thinking first;
        128 tokens produces an empty answer and looks like "cannot point".
        """
        from backend.services.model_capability_data import COORD_STYLES, DEFAULT_STYLE
        style = self._vision_config.get("coord_style") or DEFAULT_STYLE
        template = COORD_STYLES.get(style, COORD_STYLES[DEFAULT_STYLE])["prompt"]
        budget = 128
        if self._coords is not None:
            budget = max(budget, int(getattr(self._coords, "min_num_predict", 128) or 128))
        budget = max(budget, int(self._vision_config.get("min_num_predict", 0) or 0))
        return template.format(target=target), budget

    def _anchor_result(self, anchor_coords, result1, parse_path: str = "anchor_only"):
        """Return the anchor pass's own answer, offset-corrected and clamped.

        Single exit for every path that decides the refinement is not worth
        trusting: refinement disabled, refinement unparseable, or refinement
        rejected by the agreement gate. `parse_path` lands in the servo archive
        so which of those happened is answerable from the log alone.
        """
        raw_x, raw_y = anchor_coords
        ox = self._vision_config.get("offset_x", 0)
        oy = self._vision_config.get("offset_y", 0)
        raw_x += ox
        raw_y += oy

        self._last_raw_coords = (raw_x, raw_y)
        self._last_parse_path = parse_path
        self._last_detection_source = "vision"
        self._last_inference_ms = getattr(result1, "inference_ms", 0)

        x = max(0, min(self.screen_w - 1, int(raw_x)))
        y = max(0, min(self.screen_h - TASKBAR_H - 1, int(raw_y)))

        logger.info(
            f"Servo coords ({parse_path}): ({x}, {y}) (offset {ox},{oy}) "
            f"model={getattr(self.analyzer, 'default_model', '?')}"
        )
        return (x, y)

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
        # Reset raw-response telemetry so a failed run doesn't inherit the
        # previous target's model output in the servo log.
        self._last_raw_response = ""
        self._last_parse_path = ""

        # No known convention means no click. Reading a model's numbers with the
        # wrong axis order does not fail loudly — it lands a plausible click on
        # the wrong thing and reports success, which is worse than not clicking.
        if self._coords is not None and self._coords.order is None:
            self._last_failure_reason = "coord_convention_unknown"
            logger.warning(
                "Servo refusing to click: no measured coordinate convention for %s. "
                "Run backend.tools.probe_coord_order against this model.",
                getattr(self.analyzer, "default_model", "?"),
            )
            return None

        # The request dialect is part of the convention. Asking every model in
        # Google's box_2d form made models that answer only in their own form
        # look unable to point at all; ministral-3 says "[]" to this request
        # and answers a plain point request at ~140px. The default style's
        # prompt is the historical string verbatim, so gemma4's path is
        # byte-identical (a test pins that).
        prompt_pass1, num_predict1 = self._anchor_request(target)
        result1 = self.analyzer.analyze_fullsize(
            screenshot, prompt=prompt_pass1, num_predict=num_predict1, temperature=0.1
        )
        if not result1.success or not result1.description:
            # Vision Ollama call itself failed (timeout, network, model
            # unloaded). This is transient and NOT the same as "target not
            # on screen". The caller (click_target) will see this reason
            # and retry once before reporting back to the agent.
            logger.error(f"Anchor pass failed: {result1.error}")
            self._last_failure_reason = "vision_call_failed"
            return None

        # Record the anchor response verbatim — the servo log's raw_response
        # field was always empty on the vision path, which made aim drift
        # (e.g. clicks pulled toward screen center) impossible to attribute
        # to model output vs. parsing vs. coordinate translation.
        self._last_raw_response = f"anchor: {result1.description}"

        # Parse Anchor (accepts point or box, but box is better for ROI)
        anchor_coords = self._parse_detection_response(result1.description, image_size=screenshot.size)
        if anchor_coords is None:
            anchor_coords = self._parse_coordinates(result1.description)

        if anchor_coords is None:
            # Vision succeeded but said "target not present" (empty list,
            # null detection, or malformed response). This is a real signal.
            self._last_failure_reason = "target_not_visible"
            return None

        ax, ay = anchor_coords
        crop_size = 300
        if self._calibration:
            # Wider catch basin under calibration: the residual anchor error is
            # position-dependent (measured 2026-08-01: up to ~200px at right-
            # edge mid-height even after correction). A 300px crop misses the
            # target there — and a refine pass with no target in-crop INVENTS
            # one (observed: hallucinated "circle 9"/"circle 6" labels). 450px
            # covers the observed residuals; the 2x-scaled refine stays sharp.
            crop_size = 450
        if self._calibration:
            cal_ax, cal_ay = self._apply_calibration(ax, ay)
            shift = ((cal_ax - ax) ** 2 + (cal_ay - ay) ** 2) ** 0.5
            logger.info(f"Servo calibration: anchor ({ax},{ay}) → ({cal_ax},{cal_ay}) shift={shift:.0f}px")
            if shift > 120:
                # Ambiguity zone: the raw value could be a true mid-screen target
                # OR a collapsed top-screen one (measured 2026-08-01: Gemma dumps
                # top-third targets into a y≈360-390 attractor). A static map
                # can't disambiguate — so center the refine crop BETWEEN the two
                # candidates and widen it to cover both, letting the zoom pass
                # decide visually.
                crop_size = min(600, int(shift) + 360)
                ax = (ax + cal_ax) // 2
                ay = (ay + cal_ay) // 2
            else:
                ax, ay = cal_ax, cal_ay
            # The fallback path below reuses anchor_coords — keep it corrected too.
            anchor_coords = (cal_ax, cal_ay)

        # The second pass is opt-in. It is off by default because it measured as a
        # net loss (see REFLEXES["refine_enabled"] for the numbers and the
        # re-enable criterion). An explicit vision_config value still wins, which
        # is what lets eye_bakeoff compare both configurations in one run.
        _cfg_refine = self._vision_config.get("disable_refine")
        refine_on = (not _cfg_refine) if _cfg_refine is not None else bool(get_reflex("refine_enabled", False))
        if not refine_on:
            return self._anchor_result(anchor_coords, result1, "anchor_only_refine_disabled")

        # --- PASS 2: REFINEMENT PASS (ZOOM-IN) ---
        # Extract a localized crop centered on the anchor (widened when the
        # calibration shift was large — see ambiguity note above)
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
        
        # Refinement Pass: ask for a precise 'point' [x, y] on the crop.
        # ORDER MATTERS: the parser reads point as x-first. The prompt used to
        # request [y, x] — and gemma OBEYED it, so every refine was transposed
        # (banked telemetry 2026-08-01: median refine error 124px as-parsed vs
        # 49px axis-swapped, n=82). Ask for the order the parser expects.
        prompt_pass2 = (
            f"Point at the {target} within this localized crop. "
            f"Reply with ONLY a JSON list [{{\"point\": [x, y], \"label\": \"{target}\"}}] "
            f"normalized to 1000, x measured from the LEFT edge, y from the TOP. "
            f"Be extremely precise."
        )
        result2 = self.analyzer.analyze_fullsize(
            crop_scaled, prompt=prompt_pass2, num_predict=128, temperature=0.1
        )
        
        if result2.success and result2.description:
            self._last_raw_response = (
                f"anchor: {result1.description} | "
                f"crop: ({left},{top})-({right},{bottom}) | "
                f"refine: {result2.description}"
            )
            refinement = self._parse_detection_response(result2.description)
            if refinement:
                lx, ly = refinement
                # Translate local crop coords back to global space
                # lx/1000 * actual_crop_width + left_offset
                gx = int((lx / 1000.0) * (right - left) + left)
                gy = int((ly / 1000.0) * (bottom - top) + top)

                # The crop is built centred on the anchor, so a refine y sitting at
                # the crop's vertical centre is restating the anchor, not measuring
                # anything. Take the anchor's y verbatim and let the gate below
                # judge x on its own merits.
                if abs(ly - 500.0) <= get_reflex("refine_y_echo_band", 20):
                    gy = ay
                    self._last_parse_path = "zoom_refinement_y_echo"

                # Agreement gate: a refinement that wants to move the click further
                # than the anchor has ever been wrong is not a correction.
                cax, cay = anchor_coords
                _gate = get_reflex("refine_max_disagreement_px", 40)
                if max(abs(gx - cax), abs(gy - cay)) > _gate:
                    logger.info(
                        f"Servo: refine ({gx},{gy}) disagrees with anchor ({cax},{cay}) "
                        f"by more than {_gate}px — keeping the anchor"
                    )
                    return self._anchor_result(anchor_coords, result1, "anchor_refine_rejected")

                
                # Phase 1.4: Apply global calibration offsets
                ox = self._vision_config.get("offset_x", 0)
                oy = self._vision_config.get("offset_y", 0)
                gx += ox
                gy += oy
                
                self._last_raw_coords = (gx, gy)
                # Keep the y-echo tag if it was set above — it records that the
                # refinement contributed x only, which is the difference between
                # "the second pass agreed" and "the second pass said nothing".
                if self._last_parse_path != "zoom_refinement_y_echo":
                    self._last_parse_path = "zoom_refinement"
                self._last_detection_source = "vision"
                self._last_inference_ms = getattr(result1, "inference_ms", 0) + getattr(result2, "inference_ms", 0)
                
                # Clamp to screen bounds
                x = max(0, min(self.screen_w - 1, gx))
                y = max(0, min(self.screen_h - TASKBAR_H - 1, gy))
                
                logger.info(f"Servo Zoom-In: anchor ({ax},{ay}) -> refined ({gx},{gy}) (offset {ox},{oy}) -> final ({x},{y})")
                return (x, y)

        return self._anchor_result(anchor_coords, result1, "anchor_only")


    # ------------------------------------------------------------------
    # Correction loop
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_correction_mode(vision_config: Dict[str, Any]) -> Tuple[str, bool]:
        """(mode, explicit). Explicit means a caller or the environment chose it."""
        for value, explicit in ((vision_config.get("correction_mode"), True),
                                (os.environ.get(CORRECTION_ENV), True),
                                (get_reflex("correction_mode", "shadow"), False)):
            if value is None or value == "":
                continue
            mode = str(value).strip().lower()
            if mode in CORRECTION_MODES:
                return mode, explicit
            logger.warning("Servo: correction mode %r is not one of %s; ignoring it", value, CORRECTION_MODES)
        return "shadow", False

    def _should_correct(self, target: str, single_attempt: bool, precision: Optional[bool],
                        screenshot: Image.Image) -> Tuple[bool, str]:
        """Arm the loop from what is known, never from being stuck.

        Never: mode off, a DOM-sourced estimate (already exact), a calibration
        run (measuring the raw eye), the session cap, or a caller that said
        precision=False. A training run arms only when the mode was set
        explicitly for it. Otherwise arm when the caller asked (its second
        try at the same target), when the eye is unmeasured, or when its
        measured error exceeds the target size. An animating screen refuses
        the probes: the frame they would judge is already stale.
        """
        if self.correction_mode == "off":
            return False, "mode_off"
        if self._last_detection_source == "dom":
            return False, "dom_sourced"
        if (self._vision_config or {}).get("disable_calibration"):
            return False, "calibration_run"
        cap = int(get_reflex("correction_session_cap", 12))
        if self._corrections_armed_this_session >= cap:
            return False, f"session_cap({cap})"
        if precision is False:
            return False, "precision_off"
        if single_attempt and not self._correction_explicit:
            return False, "single_attempt"
        if self.eye_judge_rate is not None:
            from backend.services.model_capability_data import EYE_JUDGE_MIN_BOTH_RATE
            if self.eye_judge_rate < EYE_JUDGE_MIN_BOTH_RATE:
                # A loop steered by a judge that is often wrong spends probes
                # and moves nothing closer (gemma4:e2b at 0.76).
                return False, f"judge_unreliable({self.eye_judge_rate:.2f})"
        target_px = float(get_reflex("correction_target_px", 24))
        acc = self.eye_accuracy_px
        if precision is True:
            reason = "precision_requested"
        elif acc is None:
            reason = "eye_unmeasured"
        elif acc > target_px:
            reason = f"eye_coarse({acc:.0f}px>{target_px:.0f}px)"
        else:
            return False, f"eye_accurate({acc:.0f}px<={target_px:.0f}px)"
        try:
            time.sleep(0.15)
            again, _ = self.screen.capture()
            if self._screen_changed(screenshot, again, click_pos=None):
                return False, "screen_animating"
        except Exception as e:  # noqa: BLE001
            logger.debug("correction pre-check capture failed: %s", e)
        return True, reason

    def _seed_box(self, estimate: Tuple[int, int]) -> List[float]:
        """[lo_x, lo_y, hi_x, hi_y] around the estimate: ±1.15 × accuracy per
        axis (100 when unmeasured). With no calibration active the side away
        from screen centre is extended along the centre-to-estimate spoke by
        1.15 × |v| × (1/gain − 1), which is where a centre-pulling eye leaves
        the target. Capped and clamped on screen."""
        acc = float(self.eye_accuracy_px) if self.eye_accuracy_px is not None else 100.0
        half = min(SEED_HALF_MAX, 1.15 * acc)
        ex, ey = estimate
        cx, cy = self.screen_w / 2.0, self.screen_h / 2.0
        lo = [ex - half, ey - half]
        hi = [ex + half, ey + half]
        if not self._calibration:
            gains = (float(get_reflex("correction_gain_x", 1.0)), float(get_reflex("correction_gain_y", 0.69)))
            for axis, (e, c, k) in enumerate(((ex, cx, gains[0]), (ey, cy, gains[1]))):
                if k <= 0 or k >= 1.0:
                    continue
                v = e - c
                ext = min(SEED_HALF_MAX, 1.15 * abs(v) * (1.0 / k - 1.0))
                if v >= 0:
                    hi[axis] += ext
                else:
                    lo[axis] -= ext
        return [max(0.0, lo[0]), max(0.0, lo[1]),
                min(self.screen_w - 1.0, hi[0]), min(self.screen_h - TASKBAR_H - 1.0, hi[1])]

    @staticmethod
    def _update_axis(lo: float, hi: float, probe: float, call: str, keep: float, target_px: float) -> Tuple[float, float]:
        """`same` collapses the axis to the target width; a side call cuts at
        the probe and keeps (1 − keep) of the discarded half."""
        if call == "same":
            # An eye's "same" means within about a target's width, not on its
            # centre (gemma4:e4b said "same" 18px off a 26px dot). Collapsing
            # to exactly the target width left the true centre outside the
            # box, where no later call could reach it.
            return probe - target_px, probe + target_px
        slack = max(0.0, 1.0 - keep)
        if call in ("left", "above"):
            return lo, probe + slack * max(0.0, hi - probe)
        return probe - slack * max(0.0, probe - lo), hi

    @staticmethod
    def _parse_relative_judgment(text: str) -> Optional[Dict[str, Any]]:
        """Strict: a JSON object with a boolean `visible` and, when visible,
        `dx` in left/right/same and `dy` in above/below/same. Anything else is
        None. Never a default direction: a guessed "down" is how the old
        loop walked clicks off their targets."""
        if not text:
            return None
        t = text.strip()
        start, end = t.find("{"), t.rfind("}") + 1
        if start < 0 or end <= start:
            return None
        try:
            obj = json.loads(t[start:end])
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(obj, dict) or not isinstance(obj.get("visible"), bool):
            return None
        if not obj["visible"]:
            return {"visible": False, "dx": None, "dy": None}
        dx = str(obj.get("dx", "")).strip().lower()
        dy = str(obj.get("dy", "")).strip().lower()
        horizontal, vertical = ("left", "right", "same"), ("above", "below", "same")
        if dx in horizontal and dy in vertical:
            return {"visible": True, "dx": dx, "dy": dy}
        # The words name their own axis. gemma4:e2b answers with the fields
        # swapped ("dx": "below", "dy": "right") on most probes; the meaning
        # is not in doubt, so read it. Anything outside the vocabulary is
        # still refused.
        if dy in horizontal and dx in vertical:
            return {"visible": True, "dx": dy, "dy": dx}
        return None

    def _probe_relative(self, screenshot: Image.Image, target: str, probe: Tuple[int, int],
                        box: List[float]) -> Tuple[Optional[Dict[str, Any]], str, int]:
        """Draw the marker at the probe on the captured frame, crop around it,
        scale 2x, and ask one question. The mouse does not move."""
        from backend.utils.cursor_overlay import composite_bullseye
        px, py = int(probe[0]), int(probe[1])
        marked = composite_bullseye(screenshot, (px, py), size=PROBE_MARKER_PX, ring=PROBE_RING)
        # The crop covers the whole search box and the marker, so a target the
        # box still allows is in view. A fixed 600px cap hid targets from eyes
        # that miss by more than 300px and ended the loop on "not visible".
        # Small crops are enlarged 2x for the eye; a large one is sent as is.
        margin = 40
        left = min(box[0], px) - margin
        right = max(box[2], px) + margin
        top = min(box[1], py) - margin
        bottom = max(box[3], py) + margin
        for lo_name in ("x", "y"):
            lo, hi = (left, right) if lo_name == "x" else (top, bottom)
            if hi - lo < PROBE_CROP_MIN:
                centre = px if lo_name == "x" else py
                lo, hi = centre - PROBE_CROP_MIN / 2, centre + PROBE_CROP_MIN / 2
            if lo_name == "x":
                left, right = lo, hi
            else:
                top, bottom = lo, hi
        left, top = int(max(0, left)), int(max(0, top))
        right, bottom = int(min(self.screen_w, right)), int(min(self.screen_h, bottom))
        crop = marked.crop((left, top, right, bottom))
        if max(crop.width, crop.height) <= PROBE_CROP_MAX:
            crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
        prompt = (
            f"A red ring marker is drawn on this image. Is the {target} visible in this image, "
            f"and where is it relative to the CENTRE of the red marker?\n"
            f'Reply with ONLY this JSON: {{"visible": true or false, "dx": "left" or "right" or "same", '
            f'"dy": "above" or "below" or "same"}}\n'
            f'"same" means the marker\'s centre is already on it. No distances, no other words.'
        )
        t0 = time.monotonic()
        result = self.analyzer.analyze_fullsize(crop, prompt=prompt, num_predict=48, temperature=0.0)
        ms = int((time.monotonic() - t0) * 1000)
        raw = (result.description or "") if getattr(result, "success", False) else ""
        return self._parse_relative_judgment(raw), raw, ms

    def _correct_estimate(self, screenshot: Image.Image, target: str, estimate: Tuple[int, int],
                          armed_reason: str) -> CorrectionOutcome:
        """Narrow a search box around the estimate with relative judgments
        until the eye says "same" on both axes, the box is target-sized, the
        step or time budget runs out, or an axis reverses twice. Shadow logs
        and leaves the estimate; on applies the final, drift-clamped."""
        mode = self.correction_mode
        out = CorrectionOutcome(estimate=estimate, final=estimate, mode=mode, armed_reason=armed_reason)
        target_px = float(get_reflex("correction_target_px", 24))
        keep = float(get_reflex("correction_keep_fraction", 0.55))
        max_steps = int(get_reflex("correction_max_steps", self.max_corrections))
        deadline_s = float(get_reflex("correction_deadline_s", 4.0))
        box = self._seed_box(estimate)
        # Enough steps to narrow this box to the target: a side call keeps about
        # 0.725 of the axis, so a fixed four could not take a coarse eye's
        # 600px box below ~160px. The cap bounds the cost.
        span0 = max(box[2] - box[0], box[3] - box[1], target_px)
        need = int(math.ceil(math.log(target_px / span0) / math.log(0.5 + 0.5 * (1.0 - keep)))) + 1
        max_steps = min(int(get_reflex("correction_max_steps_cap", 10)), max(max_steps, need))
        widened = False
        t_start = time.monotonic()
        last_probe_s: Optional[float] = None
        judged = False
        calls_x: List[str] = []
        calls_y: List[str] = []
        reversals = {"x": 0, "y": 0}
        stop = "max_steps"
        for step in range(max_steps + 1):
            elapsed = time.monotonic() - t_start
            remaining = deadline_s - elapsed
            if remaining <= 0 or (last_probe_s is not None and last_probe_s > remaining):
                stop = "deadline"
                break
            probe = estimate if step == 0 else (int(round((box[0] + box[2]) / 2)), int(round((box[1] + box[3]) / 2)))
            # Whether this look is close up: only a zoomed view can tell a ring
            # 50px off a 26px dot from one on it. A wide view's "same" narrows
            # the box and is asked again close up rather than ending the search.
            zoomed = max(box[2] - box[0], box[3] - box[1], abs(probe[0] - (box[0] + box[2]) / 2) * 2,
                         abs(probe[1] - (box[1] + box[3]) / 2) * 2) + 80 <= PROBE_CROP_MAX
            judgment, raw, ms = self._probe_relative(screenshot, target, probe, box)
            last_probe_s = ms / 1000.0
            rec: Dict[str, Any] = {"probe": [int(probe[0]), int(probe[1])], "ms": ms, "raw": raw[:120]}
            if judgment is None:
                out.unparsed += 1
                rec.update({"visible": None, "dx": None, "dy": None, "direction": ""})
                out.steps.append(rec)
                stop = "unparseable"
                break
            if not judgment["visible"]:
                rec.update({"visible": False, "dx": None, "dy": None, "direction": ""})
                out.steps.append(rec)
                full = [0.0, 0.0, self.screen_w - 1.0, self.screen_h - TASKBAR_H - 1.0]
                if not widened and box != full:
                    # Not in view is not proof the target is absent: the eye's
                    # first guess can sit outside the box it seeds. Look once
                    # across the whole screen from the same probe.
                    widened = True
                    box = full
                    continue
                stop = "not_visible"
                break
            judged = True
            dx, dy = judgment["dx"], judgment["dy"]
            dy_name = {"above": "up", "below": "down", "same": "same"}[dy]
            if dx != "same" and dy != "same":
                direction = f"{dx}_and_{dy_name}"
            elif dx != "same":
                direction = dx
            elif dy != "same":
                direction = dy_name
            else:
                direction = "on_target"
            box[0], box[2] = self._update_axis(box[0], box[2], probe[0], dx, keep, target_px)
            box[1], box[3] = self._update_axis(box[1], box[3], probe[1], dy, keep, target_px)
            rec.update({"visible": True, "dx": dx, "dy": dy, "direction": direction,
                        "box_after": [round(v) for v in box]})
            out.steps.append(rec)
            if dx != "same":
                if calls_x and self._direction_reversed(calls_x[-1], dx):
                    reversals["x"] += 1
                calls_x.append(dx)
            if dy != "same":
                if calls_y and self._direction_reversed(calls_y[-1], dy_name):
                    reversals["y"] += 1
                calls_y.append(dy_name)
            if dx == "same" and dy == "same" and zoomed:
                stop = "on_target"
                break
            if (box[2] - box[0]) <= target_px and (box[3] - box[1]) <= target_px:
                stop = "converged"
                break
            if reversals["x"] >= 2 or reversals["y"] >= 2:
                stop = "oscillating_" + ("x" if reversals["x"] >= 2 else "y")
                break
        out.stop_reason = stop
        out.elapsed_ms = int((time.monotonic() - t_start) * 1000)

        if judged:
            fx = (box[0] + box[2]) / 2.0
            fy = (box[1] + box[3]) / 2.0
            ex, ey = estimate
            v = ((ex - self.screen_w / 2.0) ** 2 + (ey - self.screen_h / 2.0) ** 2) ** 0.5
            acc = float(self.eye_accuracy_px) if self.eye_accuracy_px is not None else 100.0
            # Drift bound: errors scale with distance from centre (the eye's
            # centre-pull), floored at the eye's own noise so a central target
            # still gets a noise-sized correction.
            max_drift = max(acc, min(FINAL_DRIFT_MAX, 0.75 * v))
            d = ((fx - ex) ** 2 + (fy - ey) ** 2) ** 0.5
            if d > max_drift and d > 0:
                fx = ex + (fx - ex) * max_drift / d
                fy = ey + (fy - ey) * max_drift / d
                out.clamped = True
            out.final = (max(0, min(self.screen_w - 1, int(round(fx)))),
                         max(0, min(self.screen_h - TASKBAR_H - 1, int(round(fy)))))
        out.applied = (mode == "on") and judged and out.final != estimate
        logger.info(
            "Servo correction[%s]: armed(%s) estimate=%s final=%s drift=%.0fpx steps=%d stop=%s %dms applied=%s%s",
            mode, armed_reason, estimate, out.final, out.drift_px, len(out.steps), stop,
            out.elapsed_ms, out.applied, " clamped" if out.clamped else "",
        )
        return out

    # Superseded by _correct_estimate (2026-09-22). _check_on_target and
    # _parse_correction answer with a DEFAULT direction on any failure, which
    # is how the earlier loop walked clicks off their targets; the nudge table
    # moved fixed distances instead of narrowing a box. Not called by the click
    # path; kept for readers and the tests that pin them, pending removal.
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

    def _parse_detection_response(self, text: str,
                                  image_size: Optional[Tuple[int, int]] = None) -> Optional[Tuple[int, int]]:
        """Parse a pointing answer: {"x","y"} object, "point" list, or box.

        internal_width > 0 means the numbers are normalised to that grid.
        internal_width == 0 means absolute pixels OF THE IMAGE THE MODEL SAW;
        when that image is not the screen (a crop, a resize) pass image_size so
        the answer is scaled back into screen space rather than trusted raw.
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
            grid = int(self._vision_config.get("internal_width", 1000)) if self._vision_config else 1000
            iw, ih = image_size if image_size else (self.screen_w, self.screen_h)

            def _abs_to_screen(px: float, py: float) -> Tuple[int, int]:
                # Absolute pixels of the image sent. Identity when that image
                # is the screen; otherwise scale into screen space.
                sx = self.screen_w / float(iw or self.screen_w)
                sy = self.screen_h / float(ih or self.screen_h)
                return int(px * sx), int(py * sy)

            # Format 0: {"x": .., "y": ..} object — the plain point dialect.
            # Must run before the legacy _parse_coordinates fallback, which
            # accepts the same shape and returns raw floats with no grid
            # handling.
            if "x" in entry and "y" in entry and not entry.get("box_2d") and not entry.get("bbox_2d"):
                px, py = float(entry["x"]), float(entry["y"])
                if grid > 0:
                    px, py = int((px / grid) * self.screen_w), int((py / grid) * self.screen_h)
                    self._last_parse_path = "point_obj_normalized"
                else:
                    px, py = _abs_to_screen(px, py)
                    self._last_parse_path = "point_obj"
                logger.info(f"Servo: point object ({entry['x']},{entry['y']}) grid={grid} → ({px},{py})")
                return (px, py)

            # Format 1: "point" — always [x, y], all models.
            point = entry.get("point")
            if point and len(point) == 2:
                px, py = int(point[0]), int(point[1])
                if grid > 0 and 0 <= px <= grid and 0 <= py <= grid and (px > self.screen_w or py > self.screen_h):
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

                if grid > 0:
                    cx = int(((x1 + x2) / 2 / grid) * self.screen_w)
                    cy = int(((y1 + y2) / 2 / grid) * self.screen_h)
                else:
                    cx, cy = _abs_to_screen((x1 + x2) / 2, (y1 + y2) / 2)
                
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
