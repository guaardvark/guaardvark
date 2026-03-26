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
    # Coordinate scaling: vision models return coords in internal resolution,
    # not screen pixels. Multiply raw coords by this factor.
    # Discovered: 2026-03-25 manual debugging session
    # Confirmed by: 3 buttons tested, all within 16px after scaling
    "coordinate_scale_x": {
        "value": 1.25,
        "source": "manual_calibration_2026_03_25",
        "confidence": 0.85,
        "model": "qwen2.5vl:7b-q4_K_M",
        "notes": "Vision models internally resize to ~1024px wide. Screen is 1280px. 1280/1024 = 1.25",
    },
    "coordinate_scale_y": {
        "value": 1.25,
        "source": "manual_calibration_2026_03_25",
        "confidence": 0.85,
        "model": "qwen2.5vl:7b-q4_K_M",
        "notes": "Same ratio applies vertically: 720 / (1024 * 720/1280) = 1.25",
    },

    # Model-specific internal resolution (pixels).
    # The servo divides screen_width by this to get the scale factor.
    "model_internal_width": {
        "value": 1024,
        "source": "manual_calibration_2026_03_25",
        "confidence": 0.85,
        "model": "universal",
        "notes": "Both moondream and qwen2.5vl use ~1024px internal width",
    },

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
}


def get_reflex(name: str, default=None):
    """Get a reflex value instantly. No I/O, no lookup."""
    reflex = REFLEXES.get(name)
    if reflex is None:
        return default
    return reflex["value"]


def get_scale_factors(screen_w: int = 1280, screen_h: int = 720) -> Tuple[float, float]:
    """Get coordinate scaling factors for the current model.

    Returns (scale_x, scale_y) to multiply raw model coordinates by.
    """
    internal_w = get_reflex("model_internal_width", 1024)
    scale_x = screen_w / internal_w
    scale_y = screen_h / (internal_w * screen_h / screen_w)
    return scale_x, scale_y


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
            # Computed: error between scaled prediction and actual click
            "error_px": round(
                ((scaled_coords[0] - actual_click_coords[0]) ** 2 +
                 (scaled_coords[1] - actual_click_coords[1]) ** 2) ** 0.5, 1
            ),
        }

        with self._write_lock:
            with open(self._archive_path, "a") as f:
                f.write(json.dumps(entry) + "\n")

        logger.debug(f"Archive: {target_description} success={success} error={entry['error_px']}px")

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
                    total_error += entry.get("error_px", 0)

                    model = entry.get("model", "unknown")
                    if model not in by_model:
                        by_model[model] = {"total": 0, "successful": 0, "total_error": 0}
                    by_model[model]["total"] += 1
                    if entry.get("success"):
                        by_model[model]["successful"] += 1
                    by_model[model]["total_error"] += entry.get("error_px", 0)
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


def get_servo_archive() -> ServoArchive:
    """Get the singleton ServoArchive instance."""
    return ServoArchive()
