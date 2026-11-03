#!/usr/bin/env python3
"""
Servo Knowledge Store — Two-tiered motor memory for vision-based clicking.

Tier 1 (Reflexes): Calibration constants embedded in this file. Fast, no lookup.
    Updated by the self-improvement engine when it discovers better values.
    Uncle Claude reviews all changes before they go live.

Tier 2 (Archives): Universal interaction history in JSONL. Mined by the
    self-improvement engine to discover patterns and promote them to Tier 1.

The self-improvement loop:
    1. Servo clicks → raw data saved to archives (Tier 2)
    2. Self-improvement engine analyzes archives
    3. Discovers patterns (e.g., "model X returns coords 20% too low")
    4. Proposes code change to promote pattern into reflexes (Tier 1)
    5. Uncle Claude reviews
    6. If approved → reflex updated → next click is better → cycle continues
"""

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from backend.utils.path_guard import PathEscapesRoot, contained, contained_path

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# TIER 1 — REFLEXES
# These constants are the system's muscle memory. They are applied instantly
# with zero lookup cost. The self-improvement engine updates them by
# modifying this file directly (with Uncle Claude review).
#
# FORMAT: Each reflex has a value, a source (how it was learned), and a
# confidence score (0-1) based on how many data points confirmed it.
# ═══════════════════════════════════════════════════════════════════════════

REFLEXES = {
    # Nudge distances for correction loop (pixels)
    "nudge_small": {"value": 10, "source": "initial_design", "confidence": 0.5, "model": "universal"},
    "nudge_medium": {"value": 40, "source": "initial_design", "confidence": 0.5, "model": "universal"},
    "nudge_large": {"value": 80, "source": "initial_design", "confidence": 0.5, "model": "universal"},

    # Screen change detection threshold
    "screen_change_threshold": {
        "value": 0.005,
        "source": "initial_design",
        "confidence": 0.5,
        "model": "universal",
        "notes": "Global pixel diff threshold. Lower = more sensitive.",
    },

    # ---- Second-pass zoom refinement (servo_controller._estimate_coordinates) ----
    # These three live here, next to the thing they constrain, rather than as an
    # `if` at the call site — a per-family conditional in one function is how the
    # next model gets missed.
    "refine_enabled": {
        "value": False,
        "source": "servo_20260921_222821 blind-calibration session, n=15, gemma4:e4b @1000x1000",
        "confidence": 0.4,
        "model": "universal",
        "notes": (
            "Second zoom-in pass over a crop around the anchor. OFF, measured on two "
            "boards.\n"
            "  Fixed five-dot board (2026-09-21, 15 clicks): catastrophic. Median "
            "absolute X error 6.0px anchor-alone to 54.2px after refine, worse than the "
            "anchor on 10 of 15. Not a framing problem — the target was fully inside the "
            "crop in 15 of 15, so a wider crop does not fix it.\n"
            "  Jittered board, 40 fresh positions (2026-09-22): roughly neutral. With "
            "calibration on, median distance 45.4px without refine against 49.9px with "
            "it, at double the inference cost. Hit rate slightly favours refine, median "
            "error does not.\n"
            "  The pattern across both: refine hurts most when the anchor is already "
            "good and has room to help only when the anchor is poor. That is a bad trade "
            "for a pass you cannot condition on knowing which case you are in.\n"
            "  Re-enable only when eye_bakeoff --mode pipeline,full beats --mode "
            "anchor,calibrated on BOTH median |X| and median distance, on frames the "
            "threshold was not tuned against. As of 2026-09-22 it loses on both."
        ),
    },
    "refine_max_disagreement_px": {
        "value": 40,
        "source": "servo_20260921_222821: anchor |X| error spanned -7 to +8.5px on the 11 "
                  "uncontaminated clicks; +38.5px was the worst case, and only on the "
                  "target the page had poisoned with its own red click markers",
        "confidence": 0.4,
        "model": "universal",
        "notes": (
            "Discard a refined point that disagrees with the calibration-corrected anchor "
            "by more than this in EITHER axis, and keep the anchor. A refine asking for a "
            "larger X correction than that is asserting an anchor error never once observed "
            "on this display. Loosening it puts the damage straight back: at 60 the gate "
            "keeps 9 of 15 and median |X| returns to 37px."
        ),
    },
    # ---- Correction loop (servo_controller._correct_estimate) ----
    # Armed from the eye's MEASURED accuracy, not from being stuck: an eye
    # coarser than the target earns a second look before the click. Numbers
    # below carry what they were measured against; change them with a new
    # measurement, not a hunch.
    "correction_mode": {
        "value": "auto",
        "source": "operator decision 2026-09-24 (was shadow since 2026-09-22): on for eyes "
                  "measured to judge well, shadow for unmeasured ones. Measured with "
                  "eye_bakeoff --mode corrected on 30 trainer targets: gemma4:e4b 5-7 hits "
                  "on its estimate alone, 16 with the loop applied",
        "confidence": 0.5,
        "model": "universal",
        "notes": (
            "off: never probe. shadow: run the loop, log estimate/final/drift, click "
            "the ESTIMATE. on: click the FINAL. auto: on when the eye's measured judge "
            "rate meets EYE_JUDGE_MIN_BOTH_RATE, shadow when unmeasured (an eye measured "
            "below it is never armed). Precedence: vision_config "
            "correction_mode > env GUAARDVARK_SERVO_CORRECTION > this value. The "
            "shadow pass criterion (plan 2026-09-22): median |final-truth| on Y at "
            "most 0.7 of |estimate-truth|, X no worse than estimate+5px, unparseable "
            "or not-visible stops at most 15% of armed rows."
        ),
    },
    "correction_target_px": {
        "value": 24,
        "source": "vision_trainer_grid dot diameter 24px; a UI button is rarely smaller",
        "confidence": 0.5,
        "model": "universal",
        "notes": "An eye measured at or under this needs no second look. A box narrowed "
                 "to this on both axes counts as converged.",
    },
    "correction_max_steps": {
        "value": 4,
        "source": "initial_design; the ServoController ctor's max_corrections default",
        "confidence": 0.5,
        "model": "universal",
        "notes": "Probes per click after probe 0 (the estimate itself).",
    },
    "correction_max_steps_cap": {
        "value": 10,
        "source": "2026-09-24: a 600px seed box needs ~10 side calls to reach the 24px "
                  "target at the 0.725 per-step keep; measured probe cost 0.2-0.5s",
        "confidence": 0.5,
        "model": "universal",
        "notes": "Upper bound on probes per click once the budget is sized from the "
                 "search box; correction_max_steps is the floor.",
    },
    "correction_deadline_s": {
        "value": 4.0,
        "source": "gemma4:e4b anchor inference 0.6-1.2s per call on this box, 2026-09-22",
        "confidence": 0.5,
        "model": "universal",
        "notes": "Wall-clock budget for the probes. A probe that cannot finish inside "
                 "it is never started, so the worst case is deadline + one in flight.",
    },
    "correction_session_cap": {
        "value": 12,
        "source": "initial_design: 12 armed clicks x ~5.5s worst case = about 66s per task",
        "confidence": 0.5,
        "model": "universal",
        "notes": "Armed clicks (shadow or on) per servo instance; past it the loop disarms.",
    },
    "correction_keep_fraction": {
        "value": 0.55,
        "source": "initial_design",
        "confidence": 0.4,
        "model": "universal",
        "notes": "After a left/right or above/below call the box is cut at the probe and "
                 "(1 - this) of the discarded half is kept, so one wrong call cannot "
                 "exclude the target.",
    },
    "correction_gain_y": {
        "value": 0.69,
        "source": "gemma4:e4b Y compression slope 0.65-0.70 across three boards @1000x1000, "
                  "2026-09-22 (servo_calibrate --from-bench)",
        "confidence": 0.6,
        "model": "universal",
        "notes": "How much of the true centre-to-target distance the eye reports on Y. With "
                 "no calibration active the seed box is extended away from screen centre "
                 "by 1.15 * |v| * (1/gain - 1) so the box still covers the target.",
    },
    "correction_gain_x": {
        "value": 1.0,
        "source": "gemma4:e4b X error is 22px noise with no centre-pull, same boards",
        "confidence": 0.6,
        "model": "universal",
        "notes": "1.0 means no spoke extension on X.",
    },
    "refine_y_echo_band": {
        "value": 20,
        "source": "servo_20260921_222821: refine returned y of exactly 499 or 500 on 9 of 15 "
                  "crops, and the crop's centre y equalled the anchor's y in 15 of 15 "
                  "(edge clamping never fired)",
        "confidence": 0.5,
        "model": "universal",
        "notes": (
            "Half-width, in the refine's own normalised-to-1000 space, around the crop's "
            "vertical centre. The crop is built centred on the anchor, so a refine y inside "
            "this band restates the anchor rather than measuring anything — keep the "
            "anchor's y and let the gate judge x on its own."
        ),
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# MODEL VISION CONFIGS
# Per-model calibration: scale factors, which vision model to use for
# coordinate estimation, and whether the model can see screenshots natively.
#
# When the user selects a chat model on the frontend, the agent loads
# the matching vision config so coordinates land correctly.
#
# "vision_model": None means the model sees screenshots itself.
# "vision_model": "moondream:latest" means use moondream as external eyes.
# "internal_width": the pixel width the model thinks in (for scaling).
# ═══════════════════════════════════════════════════════════════════════════

MODEL_VISION_CONFIGS = {
    # -- Models with native vision (can see screenshots directly) --
    "gemma4:e4b": {
        "has_vision": True,
        "vision_model": None,            # gemma4 does its own coordinate estimation — no middleman
        "internal_width": 1000,          # Gemma4 via Ollama returns box_2d normalized to 1000 (Google standard) — parser denormalizes to actual screen pixels
        "scale_x": 1.0,
        "scale_y": 1.0,
        "offset_x": 0,
        "offset_y": 0,
        "native_pointing": True,         # uses box_2d natively
        "coord_order": "yx",             # Gemma4 via Ollama returns Google's box_2d format: [y1, x1, y2, x2]
        "source": "google_box_2d_normalized_1000_2026_05_11",
        "notes": "Gemma4 via Ollama returns box_2d normalized to 1000 (Google standard). Parser denormalizes: (coord/1000)*screen_size. Works on any screen resolution.",
    },
    "moondream:latest": {
        "has_vision": True,
        "vision_model": None,
        "internal_width": 1024,
        "scale_x": 1.25,
        "scale_y": 0.7031,
        "source": "16_9_screen_calibration_2026_04_10",
        "notes": "Moondream uses ~1024px internal width. 1280x720 screen / 1024 internal = 1.25x scale.",
    },

    # -- Text-only models (need an external vision model for eyes) --
    "llama3:latest": {
        "has_vision": False,
        "vision_model": "moondream:latest",
        "internal_width": 1024,
        "scale_x": 1.25,
        "scale_y": 0.7031,
        "source": "16_9_screen_calibration_2026_04_10",
        "notes": "Llama3 has no vision — uses moondream as eyes. 1280x720 screen / 1024 internal = 1.25x scale.",
    },
    "ministral-3:latest": {
        "has_vision": False,
        "vision_model": "moondream:latest",
        "internal_width": 1024,
        "scale_x": 1.25,
        "scale_y": 0.7031,
        "source": "16_9_screen_calibration_2026_04_10",
        "notes": "Text-only, uses moondream as eyes. 1280x720 screen / 1024 internal = 1.25x scale.",
    },
}

# Fallback for models not in the config — text-only until proven multimodal.
# Unknown names previously defaulted has_vision=True, which made VisionAnalyzer
# send images to whatever was in VRAM (e.g. qwen3.5-uncensored) → Ollama 400
# "Multimodal data provided, but model does not support multimodal requests."
_DEFAULT_VISION_CONFIG = {
    "has_vision": False,
    "vision_model": "gemma4:e4b",
    "internal_width": 1000,
    "scale_x": 1.0,
    "scale_y": 1.0,
    "source": "default_text_only_external_eyes",
    "notes": (
        "Unknown model — assume text-only; use gemma4:e4b as external eyes. "
        "Do not send images to this model."
    ),
}

# Name heuristics for families known to accept images (when not in MODEL_VISION_CONFIGS).
_VISION_NAME_MARKERS = (
    "gemma4",
    "moondream",
    "llava",
    "bakllava",
    "qwen2.5vl",
    "qwen2.5-vl",
    "qwen3-vl",
    "minicpm-v",
    "pixtral",
    "llama3.2-vision",
)


def model_name_looks_vision(model_name: str) -> bool:
    """True if the Ollama tag looks like a multimodal / vision model."""
    n = (model_name or "").strip().lower()
    if not n:
        return False
    # Strip org prefix: jaahas/qwen3.5-uncensored:latest → qwen3.5-uncensored:latest
    short = n.rsplit("/", 1)[-1]
    return any(m in short for m in _VISION_NAME_MARKERS)

MODEL_ALIASES = {
    "gemma4": "gemma4:e4b",
    "gemma4:e4b-q4": "gemma4:e4b",
}


def get_reflex(name: str, default=None):
    """Get a reflex value instantly. No I/O, no lookup."""
    reflex = REFLEXES.get(name)
    if reflex is None:
        return default
    return reflex["value"]


def get_vision_config(model_name: str = "") -> Dict[str, Any]:
    """Get the vision config for a specific model.

    Matches exact names plus explicit aliases.
    Falls back to _DEFAULT_VISION_CONFIG for unknown models.
    """
    if not model_name:
        # Try to detect active model
        model_name = _detect_active_model()
    model_name = (model_name or "").strip()
    alias_target = MODEL_ALIASES.get(model_name)
    if alias_target:
        model_name = alias_target

    # Exact match first
    if model_name in MODEL_VISION_CONFIGS:
        return MODEL_VISION_CONFIGS[model_name]

    # Conservative variant match: allow suffixes on a full configured tag only.
    for key, config in MODEL_VISION_CONFIGS.items():
        if model_name.startswith(f"{key}-") or model_name.startswith(f"{key}:"):
            return config

    # A model with no row: whether it sees is the capability resolver's answer
    # (Ollama's capabilities first; the name guess only when Ollama is down).
    # The name markers alone got both ways wrong: qwen3.5, qwen3.6 and
    # ministral-3 see, a gemma4-named build without a vision tower does not.
    try:
        from backend.services.model_capability_resolver import _vision_with_evidence
        sees, evidence = _vision_with_evidence(model_name)
    except Exception:  # noqa: BLE001 - fall back to the name markers
        sees, evidence = model_name_looks_vision(model_name), "name_markers"
    if sees:
        return {
            **_DEFAULT_VISION_CONFIG,
            "has_vision": True,
            "vision_model": None,
            "source": f"resolver_{evidence}",
            "notes": f"'{model_name}' sees natively ({evidence}).",
        }

    logger.info(f"No vision config for '{model_name}', using text-only defaults")
    return dict(_DEFAULT_VISION_CONFIG)


def _detect_active_model() -> str:
    """Detect the currently active chat model from Ollama."""
    try:
        import requests as _requests
        from backend.config import OLLAMA_BASE_URL
        resp = _requests.get(f"{OLLAMA_BASE_URL}/api/ps", timeout=3)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            if models:
                return models[0].get("name", "")
    except Exception:
        pass
    return ""


def get_scale_factors(screen_w: int = 1024, screen_h: int = 1024, model_name: str = "") -> Tuple[float, float]:
    """Get coordinate scaling factors for a specific model.

    Uses per-model calibration from MODEL_VISION_CONFIGS.
    Returns (scale_x, scale_y) to multiply raw model coordinates by.
    """
    config = get_vision_config(model_name)
    return config["scale_x"], config["scale_y"]


# ═══════════════════════════════════════════════════════════════════════════
# TIER 2 — ARCHIVES
# Universal interaction history. Every servo click is recorded here.
# The self-improvement engine mines this for patterns.
# ═══════════════════════════════════════════════════════════════════════════

class ServoArchive:
    """Universal knowledge archive for servo interactions.

    Stores every click attempt with full context. Model-agnostic —
    survives model upgrades because it records both raw model output
    AND actual screen coordinates.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        root = os.environ.get("GUAARDVARK_ROOT", ".")
        self._archive_dir = Path(root) / "data" / "training" / "knowledge"
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        self._archive_path = self._archive_dir / "servo_archive.jsonl"
        self._write_lock = threading.Lock()

    def record(
        self,
        target_description: str,
        model_used: str,
        raw_model_coords: Tuple[int, int],
        scaled_coords: Tuple[int, int],
        actual_click_coords: Tuple[int, int],
        scale_factor: Tuple[float, float],
        success: bool,
        corrections: int = 0,
        attempt: int = 1,
        time_ms: int = 0,
        screen_size: Tuple[int, int] = (1280, 720),
        ui_element_type: str = "",
        correction_log: Optional[List[Dict]] = None,
        raw_response: str = "",
        parse_path: str = "",
        detection_source: str = "",
        vision_config: Optional[Dict[str, Any]] = None,
        target_found: bool = False,
        click_issued: bool = False,
        post_action_effect: str = "",
        reason: str = "",
        inference_ms: int = 0,
        truth: Optional[Dict[str, Any]] = None,
        correction: Optional[Dict[str, Any]] = None,
        correction_skip: str = "",
    ):
        """Record a servo interaction to the universal archive."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "target": target_description,
            "model": model_used,
            "raw_coords": list(raw_model_coords),
            "scaled_coords": list(scaled_coords),
            "click_coords": list(actual_click_coords),
            "scale_factor": list(scale_factor),
            "success": success,
            "corrections": corrections,
            "attempt": attempt,
            "time_ms": time_ms,
            "screen_size": list(screen_size),
            "ui_element_type": ui_element_type,
            "raw_response": raw_response[:2000],
            "parse_path": parse_path,
            "detection_source": detection_source,
            "vision_config_source": (vision_config or {}).get("source", ""),
            "vision_internal_width": (vision_config or {}).get("internal_width"),
            "target_found": target_found,
            "click_issued": click_issued,
            "post_action_effect": post_action_effect,
            "reason": reason,
            "inference_ms": inference_ms,
            # Deprecated: this is prediction-vs-issued-click, not target error.
            # Keep the field for readers, but do not treat it as calibration truth.
            "error_px": None,
        }

        # Ground truth from the trainer page (TrainerTruthProbe): tri-state
        # true_hit + real target center. This — not success/click_issued — is
        # what the Archive Miner and calibration fits must consume. When
        # present, an honest error is computable: |click_coords − target_c*|.
        if truth is not None:
            entry["truth"] = truth
            tx, ty = truth.get("target_cx"), truth.get("target_cy")
            if tx is not None and ty is not None:
                cx, cy = actual_click_coords
                entry["true_error_px"] = round(((cx - tx) ** 2 + (cy - ty) ** 2) ** 0.5, 1)

        # Include correction directions so the self-improvement engine
        # can see which way the model was consistently off
        if correction_log:
            entry["correction_log"] = correction_log
        # Correction-loop summary (mode, why it armed, estimate, final, drift,
        # why it stopped). ADDITIVE. With truth present both |estimate - truth|
        # and |final - truth| are computable per row, which is the shadow
        # measurement the default mode is decided from.
        if correction_skip:
            # Why the loop did not arm on this click (eye_accurate(13px<=24px),
            # mode_off, session_cap, ...). Proves the gate reads the store.
            entry["correction_skip"] = correction_skip
        if correction:
            entry["correction"] = correction
            tx, ty = (truth or {}).get("target_cx"), (truth or {}).get("target_cy")
            est = correction.get("estimate")
            fin = correction.get("final")
            if tx is not None and ty is not None and est and fin:
                entry["correction"]["estimate_error_px"] = round(((est[0] - tx) ** 2 + (est[1] - ty) ** 2) ** 0.5, 1)
                entry["correction"]["final_error_px"] = round(((fin[0] - tx) ** 2 + (fin[1] - ty) ** 2) ** 0.5, 1)

        with self._write_lock:
            with open(self._archive_path, "a") as f:
                f.write(json.dumps(entry) + "\n")

        logger.debug(
            f"Archive: {target_description} success={success} "
            f"source={detection_source or 'unknown'} issued={click_issued}"
        )

    def get_stats(self) -> Dict[str, Any]:
        """Get aggregate statistics from the archive."""
        if not self._archive_path.exists():
            return {"total": 0, "success_rate": 0, "avg_error_px": 0}

        total = 0
        successful = 0
        total_error = 0
        by_model = {}

        with open(self._archive_path) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    total += 1
                    if entry.get("success"):
                        successful += 1
                    error_px = entry.get("error_px")
                    error_value = float(error_px) if isinstance(error_px, (int, float)) else 0.0
                    total_error += error_value

                    model = entry.get("model", "unknown")
                    if model not in by_model:
                        by_model[model] = {"total": 0, "successful": 0, "total_error": 0}
                    by_model[model]["total"] += 1
                    if entry.get("success"):
                        by_model[model]["successful"] += 1
                    by_model[model]["total_error"] += error_value
                except json.JSONDecodeError:
                    continue

        model_stats = {}
        for model, stats in by_model.items():
            model_stats[model] = {
                "total": stats["total"],
                "success_rate": round(stats["successful"] / stats["total"] * 100, 1) if stats["total"] else 0,
                "avg_error_px": round(stats["total_error"] / stats["total"], 1) if stats["total"] else 0,
            }

        return {
            "total": total,
            "successful": successful,
            "success_rate": round(successful / total * 100, 1) if total else 0,
            "avg_error_px": round(total_error / total, 1) if total else 0,
            "by_model": model_stats,
            "archive_path": str(self._archive_path),
        }

    def get_run_metrics(self, since: str | None = None) -> Dict[str, Any]:
        """Aggregate post-hardening servo metrics from the archive.

        Args:
            since: Optional ISO timestamp. Entries older than this are ignored.
        """
        if not self._archive_path.exists():
            return {
                "total": 0,
                "task_success_rate": 0.0,
                "verified_outcome_rate": 0.0,
                "target_not_visible_rate": 0.0,
                "parse_failure_rate": 0.0,
                "recipe_fallback_rate": 0.0,
                "mean_vlm_latency_ms": 0.0,
            }

        since_dt = None
        if since:
            try:
                since_dt = datetime.fromisoformat(str(since).replace("Z", "+00:00"))
            except Exception:
                since_dt = None

        total = successes = verified = target_not_visible = parse_failures = recipe_fallbacks = 0
        latency_total = latency_count = 0

        with open(self._archive_path) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since_dt is not None:
                    try:
                        ts = datetime.fromisoformat(str(entry.get("timestamp", "")).replace("Z", "+00:00"))
                        if ts < since_dt:
                            continue
                    except Exception:
                        pass
                total += 1
                if entry.get("success"):
                    successes += 1
                if entry.get("post_action_effect") == "verified" or entry.get("verified"):
                    verified += 1
                reason = (entry.get("reason") or "").lower()
                if entry.get("target_found") is False or reason == "target_not_visible":
                    target_not_visible += 1
                parse_path = (entry.get("parse_path") or "").lower()
                if parse_path in ("", "vision_error", "parse_failed") and entry.get("detection_source") == "vision":
                    parse_failures += 1
                if "recipe_fallback" in reason:
                    recipe_fallbacks += 1
                latency = entry.get("inference_ms")
                if isinstance(latency, (int, float)) and latency > 0:
                    latency_total += float(latency)
                    latency_count += 1

        def rate(count: int) -> float:
            return round((count / total) * 100, 2) if total else 0.0

        return {
            "total": total,
            "task_success_rate": rate(successes),
            "verified_outcome_rate": rate(verified),
            "target_not_visible_rate": rate(target_not_visible),
            "parse_failure_rate": rate(parse_failures),
            "recipe_fallback_rate": rate(recipe_fallbacks),
            "mean_vlm_latency_ms": round(latency_total / latency_count, 1) if latency_count else 0.0,
        }

    def get_calibration_data(self, model: str, limit: int = 50) -> List[Dict]:
        """Get recent calibration data for a specific model.

        Used by the self-improvement engine to discover scaling patterns.
        """
        entries = []
        if not self._archive_path.exists():
            return entries

        with open(self._archive_path) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("model") == model:
                        entries.append(entry)
                except json.JSONDecodeError:
                    continue

        return entries[-limit:]

    def suggest_scale_factor(self, model: str) -> Optional[Dict[str, float]]:
        """Analyze archive data and suggest optimal scale factors for a model.

        This is what the self-improvement engine calls to discover
        if the current reflexes need updating.
        """
        data = self.get_calibration_data(model, limit=100)
        if len(data) < 10:
            return None  # Not enough data

        # Only use successful interactions with known raw coords
        valid = [d for d in data if d.get("success") and
                 d.get("raw_coords", [0, 0]) != [0, 0] and
                 d.get("click_coords", [0, 0]) != [0, 0]]

        if len(valid) < 5:
            return None

        # Calculate average actual scale factor from successful clicks
        scale_x_samples = []
        scale_y_samples = []
        for d in valid:
            raw_x, raw_y = d["raw_coords"]
            click_x, click_y = d["click_coords"]
            if raw_x > 0 and raw_y > 0:
                scale_x_samples.append(click_x / raw_x)
                scale_y_samples.append(click_y / raw_y)

        if not scale_x_samples:
            return None

        avg_scale_x = sum(scale_x_samples) / len(scale_x_samples)
        avg_scale_y = sum(scale_y_samples) / len(scale_y_samples)

        return {
            "scale_x": round(avg_scale_x, 4),
            "scale_y": round(avg_scale_y, 4),
            "sample_count": len(valid),
            "model": model,
        }


    def get_learning_summary(self, model: str = "") -> Dict[str, Any]:
        """Cross-reference servo archive with human feedback to produce
        an actionable learning summary.

        Returns stats, patterns, and suggested improvements per model.
        Called by the self-improvement engine or manually via API.
        """
        # Load servo archive data
        archive_data = self.get_calibration_data(model, limit=200) if model else []
        if not model:
            # Load all
            if self._archive_path.exists():
                archive_data = []
                with open(self._archive_path) as f:
                    for line in f:
                        if line.strip():
                            try:
                                archive_data.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue

        # Load human feedback
        feedback_path = self._archive_dir / "feedback.jsonl"
        feedback = []
        if feedback_path.exists():
            with open(feedback_path) as f:
                for line in f:
                    if line.strip():
                        try:
                            feedback.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

        # Analyze
        total_clicks = len(archive_data)
        successful_clicks = sum(1 for d in archive_data if d.get("success"))
        total_feedback = len(feedback)
        positive_feedback = sum(1 for f in feedback if f.get("positive"))
        negative_feedback = total_feedback - positive_feedback

        # Find worst targets (most failures)
        from collections import Counter
        fail_targets = Counter(
            d.get("target", "?") for d in archive_data if not d.get("success")
        )

        # Suggested scale factor
        scale_suggestion = self.suggest_scale_factor(model) if model else None

        # Negative feedback patterns — what tasks get thumbs down?
        neg_tasks = Counter(
            f.get("task", "?")[:60] for f in feedback if not f.get("positive")
        )

        return {
            "model": model or "all",
            "servo": {
                "total_clicks": total_clicks,
                "successful": successful_clicks,
                "success_rate": round(successful_clicks / total_clicks * 100, 1) if total_clicks else 0,
                "worst_targets": fail_targets.most_common(5),
            },
            "feedback": {
                "total": total_feedback,
                "positive": positive_feedback,
                "negative": negative_feedback,
                "approval_rate": round(positive_feedback / total_feedback * 100, 1) if total_feedback else 0,
                "top_complaints": neg_tasks.most_common(5),
            },
            "suggestions": {
                "scale_factor": scale_suggestion,
            },
        }


    def rotate_archive(self, reason: str = "manual") -> str:
        """Move current archive to a dated backup and start fresh.

        We never delete — old data might be useful for forensics even if
        it was recorded with busted scale factors. Rotate and move on.
        """
        if not self._archive_path.exists():
            return ""

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"servo_archive_{timestamp}_{reason}.jsonl"
        backup_path = contained(self._archive_dir, backup_name)

        with self._write_lock:
            self._archive_path.rename(backup_path)

        logger.info(f"Archive rotated → {backup_path} (fresh start, let's do better this time)")
        return str(backup_path)


def get_servo_archive() -> ServoArchive:
    """Get the singleton ServoArchive instance."""
    return ServoArchive()


# ═══════════════════════════════════════════════════════════════════════════
# TIER 1.5 — RUNTIME CALIBRATION (restored 2026-08-01)
# The measured per-model, per-resolution aim correction — the module header's
# "model X returns coords 20% too low" pattern, made concrete. A calibration
# run (backend/tools/servo_calibrate.py) fits raw anchor output against DOM
# ground truth on the vision trainer and stores a linear map per axis:
#     raw ≈ a + b·truth   →   corrected = (raw − a) / b
# Stored per-machine (gitignored) because it bakes in this display + model
# build. It is a TRANSFORM, not stored coordinates — LEARNING_PRINCIPLES-safe.
# ═══════════════════════════════════════════════════════════════════════════

_CALIBRATION_PATH = Path(__file__).resolve().parents[2] / "data" / "training" / "servo_calibration.json"
_calibration_cache: Dict[str, Any] = {"mtime": None, "data": {}}
_calibration_lock = threading.Lock()

# Sanity bounds on the fitted slope — outside this, the fit is degenerate
# (bad samples, moving UI) and must not be applied.
CALIBRATION_SLOPE_MIN = 0.3
CALIBRATION_SLOPE_MAX = 1.7


def _calibration_key(model: str, screen_w: int, screen_h: int) -> str:
    return f"{(model or 'unknown').strip()}@{int(screen_w)}x{int(screen_h)}"


def _load_calibration_file() -> Dict[str, Any]:
    """Read the calibration file with an mtime cache (hot path: servo init)."""
    try:
        mtime = _CALIBRATION_PATH.stat().st_mtime
    except OSError:
        return {}
    with _calibration_lock:
        if _calibration_cache["mtime"] == mtime:
            return _calibration_cache["data"]
        try:
            data = json.loads(_CALIBRATION_PATH.read_text())
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"servo_calibration.json unreadable: {e}")
            return {}
        _calibration_cache.update(mtime=mtime, data=data)
        return data


# One entry per model@WxH now carries three kinds of fact about a model on this
# machine: the calibration FIT (flat keys, as always), the coordinate CONVENTION
# it was measured to speak, and its measured pointing ACCURACY. They used to
# live in three files written by three tools, and the resolver could read only
# one of them — which is why a hand-written row outranked an eye four times more
# accurate. Same file, same lock, same mtime cache; only the shape grows.
_FIT_KEYS = ("model", "a_x", "b_x", "a_y", "b_y", "k", "cx", "cy", "elbow")
_MEASUREMENT_SECTIONS = ("coords", "accuracy", "judge")


def _has_fit(entry: Optional[Dict[str, Any]]) -> bool:
    return bool(entry) and any(k in entry for k in _FIT_KEYS)


def load_servo_calibration(model: str, screen_w: int, screen_h: int) -> Optional[Dict[str, Any]]:
    """Return the calibration fit for (model, resolution), or None.

    Three model families (entry["model"], default "linear" for legacy entries):
      linear      — per-axis: corrected = (raw − a)/b
      radial      — the operator's X-leg insight (2026-08-01): the eye pulls raw
                    coords toward screen center along the center→target spoke;
                    corrected = C + (raw − C)/k
      piecewise_y — identity below elbow (eye is accurate there), linear
                    correction above (the top-of-screen collapse zone)

    Refuses (returns None) on out-of-bounds gains so a corrupt fit can never
    make aim WORSE than uncalibrated.
    """
    entry = _load_calibration_file().get(_calibration_key(model, screen_w, screen_h))
    # A measurement-only entry (coords/accuracy, no fit) is not malformed; it is
    # a model that has been probed but never calibrated. Without this guard the
    # KeyError below logged "malformed" on every servo init for such a model.
    if not entry or not _has_fit(entry):
        return None
    try:
        family = str(entry.get("model", "linear"))
        def _sane(v: float) -> bool:
            return CALIBRATION_SLOPE_MIN <= abs(float(v)) <= CALIBRATION_SLOPE_MAX

        if family == "linear":
            if not (_sane(entry["b_x"]) and _sane(entry["b_y"])):
                logger.warning("servo calibration: linear slope out of bounds — ignoring")
                return None
            out = {k: float(entry[k]) for k in ("a_x", "b_x", "a_y", "b_y")}
        elif family == "radial":
            if not _sane(entry["k"]):
                logger.warning("servo calibration: radial gain out of bounds — ignoring")
                return None
            out = {"k": float(entry["k"]), "cx": float(entry["cx"]), "cy": float(entry["cy"])}
        elif family == "piecewise_y":
            if not _sane(entry["b_y"]):
                logger.warning("servo calibration: piecewise slope out of bounds — ignoring")
                return None
            out = {"elbow": float(entry["elbow"]),
                   "a_y": float(entry["a_y"]), "b_y": float(entry["b_y"])}
        else:
            logger.warning(f"servo calibration: unknown model family {family!r} — ignoring")
            return None
        out["model"] = family
        return out
    except (KeyError, TypeError, ValueError) as e:
        logger.warning(f"servo calibration entry malformed: {e}")
        return None


def save_servo_calibration(model: str, screen_w: int, screen_h: int, fit: Dict[str, Any]) -> str:
    """Persist a calibration fit (overwrites the entry for this model+resolution)."""
    key = _calibration_key(model, screen_w, screen_h)
    with _calibration_lock:
        data = {}
        try:
            data = json.loads(_CALIBRATION_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            pass
        prior = data.get(key) or {}
        # Replace the fit, keep the measurements. The fit tool knows nothing about
        # coords/accuracy and must not be able to erase them by saving.
        merged = {k: prior[k] for k in _MEASUREMENT_SECTIONS if k in prior}
        merged.update(fit)
        rb = merged.get("_rollback")
        if isinstance(rb, dict):
            merged["_rollback"] = {k: v for k, v in rb.items() if k not in _MEASUREMENT_SECTIONS}
        data[key] = merged
        _CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CALIBRATION_PATH.write_text(json.dumps(data, indent=2) + "\n")
        _calibration_cache.update(mtime=None, data={})  # force re-read
    logger.info(f"servo calibration saved for {key}: {fit}")
    return key


def record_measurement(model: str, screen_w: int, screen_h: int,
                       section: str, data: Dict[str, Any]) -> str:
    """Write one measurement section for model@WxH, leaving the fit and the other
    sections untouched. `section` is "coords", "accuracy" or "judge"."""
    if section not in _MEASUREMENT_SECTIONS:
        raise ValueError(f"unknown measurement section {section!r}; expected one of {_MEASUREMENT_SECTIONS}")
    key = _calibration_key(model, screen_w, screen_h)
    with _calibration_lock:
        store = {}
        try:
            store = json.loads(_CALIBRATION_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            pass
        entry = store.setdefault(key, {})
        entry[section] = dict(data)
        _CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CALIBRATION_PATH.write_text(json.dumps(store, indent=2) + "\n")
        _calibration_cache.update(mtime=None, data={})
    try:
        from backend.services.model_capability_resolver import invalidate
        invalidate(model)
    except Exception:
        pass
    logger.info(f"servo measurement recorded for {key}: {section}")
    return key


def load_model_measurements(model: str, screen_w: Optional[int] = None,
                            screen_h: Optional[int] = None) -> Dict[str, Any]:
    """{"screen": "WxH"|None, "coords": dict|None, "accuracy": dict|None}.

    With a screen: that exact entry. Without one: the newest entry for the model
    at any resolution — a coordinate convention does not depend on resolution,
    accuracy does, so the returned "screen" says which resolution the accuracy
    was measured at and the ranker can discount a mismatch.
    """
    store = _load_calibration_file()
    if screen_w and screen_h:
        entry = store.get(_calibration_key(model, screen_w, screen_h)) or {}
        return {"screen": f"{int(screen_w)}x{int(screen_h)}" if entry else None,
                "coords": entry.get("coords"), "accuracy": entry.get("accuracy"),
                "judge": entry.get("judge")}
    prefix = f"{(model or 'unknown').strip()}@"
    best_key, best_stamp = None, ""
    for key, entry in store.items():
        if not key.startswith(prefix) or not isinstance(entry, dict):
            continue
        stamp = max(str((entry.get("coords") or {}).get("probed_at", "")),
                    str((entry.get("accuracy") or {}).get("measured_at", "")),
                    str(entry.get("fitted_at", "")))
        if best_key is None or stamp > best_stamp:
            best_key, best_stamp = key, stamp
    if best_key is None:
        return {"screen": None, "coords": None, "accuracy": None, "judge": None}
    entry = store[best_key]
    return {"screen": best_key.split("@", 1)[1], "coords": entry.get("coords"),
            "accuracy": entry.get("accuracy"), "judge": entry.get("judge")}
