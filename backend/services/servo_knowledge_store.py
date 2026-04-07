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
        "internal_width": 1024,          # Gemma4 uses 1024x1024 normalized grid
        "scale_x": 1.25,                 # 1280 / 1024 = 1.25
        "scale_y": 0.703125,             # 720 / 1024 = 0.703125
        "native_pointing": True,         # uses box_2d [y1, x1, y2, x2] format natively
        "coord_order": "yx",             # Gemma4 returns Y first, then X
        "source": "google_docs_2026_04_06",
        "notes": "Gemma4 1024x1024 normalized grid. box_2d format [y1,x1,y2,x2]. Descale: px = round((coord/1024) * screen_dim)",
    },
    "qwen3-vl:4b-instruct": {
        "has_vision": True,
        "vision_model": None,
        "internal_width": 1024,
        "scale_x": 1.25,
        "scale_y": 1.25,
        "source": "calibration_2026_03_25",
        "notes": "Qwen3-VL internally resizes to ~1024px wide",
    },
    "qwen3-vl:8b-instruct": {
        "has_vision": True,
        "vision_model": None,
        "internal_width": 1024,
        "scale_x": 1.25,
        "scale_y": 1.25,
        "source": "calibration_2026_03_25",
        "notes": "Same internal resolution as 4b variant",
    },
    "qwen3-vl:2b-instruct": {
        "has_vision": True,
        "vision_model": None,
        "internal_width": 1024,
        "scale_x": 1.25,
        "scale_y": 1.25,
        "source": "calibration_2026_03_25",
        "notes": "Small vision model, used as servo eyes",
    },
    "qwen2.5vl:7b-q4_K_M": {
        "has_vision": True,
        "vision_model": None,
        "internal_width": 1024,
        "scale_x": 1.25,
        "scale_y": 1.25,
        "source": "manual_calibration_2026_03_25",
        "notes": "Original calibration model. 1280/1024 = 1.25",
    },
    "moondream:latest": {
        "has_vision": True,
        "vision_model": None,
        "internal_width": 1024,
        "scale_x": 1.25,
        "scale_y": 1.25,
        "source": "initial_design",
        "notes": "Moondream uses ~1024px internal width",
    },

    # -- Text-only models (need an external vision model for eyes) --
    "llama3:latest": {
        "has_vision": False,
        "vision_model": "moondream:latest",
        "internal_width": 1024,
        "scale_x": 1.25,
        "scale_y": 1.25,
        "source": "initial_design",
        "notes": "Llama3 has no vision — uses moondream as eyes",
    },
    "qwen3.5:9b": {
        "has_vision": False,
        "vision_model": "qwen3-vl:2b-instruct",
        "internal_width": 1024,
        "scale_x": 1.25,
        "scale_y": 1.25,
        "source": "initial_design",
        "notes": "Text-only, uses qwen3-vl:2b as eyes",
    },
    "ministral-3:latest": {
        "has_vision": False,
        "vision_model": "qwen3-vl:2b-instruct",
        "internal_width": 1024,
        "scale_x": 1.25,
        "scale_y": 1.25,
        "source": "initial_design",
        "notes": "Text-only, uses qwen3-vl:2b as eyes",
    },
}

# Fallback for models not in the config — assumes text-only with qwen3-vl:2b as eyes
_DEFAULT_VISION_CONFIG = {
    "has_vision": False,
    "vision_model": "qwen3-vl:2b-instruct",
    "internal_width": 1024,
    "scale_x": 1.25,
    "scale_y": 1.25,
    "source": "default_fallback",
    "notes": "Model not calibrated yet — using safe defaults",
}


def get_reflex(name: str, default=None):
    """Get a reflex value instantly. No I/O, no lookup."""
    reflex = REFLEXES.get(name)
    if reflex is None:
        return default
    return reflex["value"]


def get_vision_config(model_name: str = "") -> Dict[str, Any]:
    """Get the vision config for a specific model.

    Matches by prefix — "gemma4:e4b" matches "gemma4" entries.
    Falls back to _DEFAULT_VISION_CONFIG for unknown models.
    """
    if not model_name:
        # Try to detect active model
        model_name = _detect_active_model()

    # Exact match first
    if model_name in MODEL_VISION_CONFIGS:
        return MODEL_VISION_CONFIGS[model_name]

    # Prefix match (e.g., "gemma4:e4b-q4" matches "gemma4:e4b")
    for key, config in MODEL_VISION_CONFIGS.items():
        if model_name.startswith(key.split(":")[0]) or key.startswith(model_name.split(":")[0]):
            return config

    logger.info(f"No vision config for '{model_name}', using defaults")
    return _DEFAULT_VISION_CONFIG


def _detect_active_model() -> str:
    """Detect the currently active chat model from Ollama."""
    try:
        import requests as _requests
        resp = _requests.get("http://localhost:11434/api/ps", timeout=3)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            if models:
                return models[0].get("name", "")
    except Exception:
        pass
    return ""


def get_scale_factors(screen_w: int = 1280, screen_h: int = 720, model_name: str = "") -> Tuple[float, float]:
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


def get_servo_archive() -> ServoArchive:
    """Get the singleton ServoArchive instance."""
    return ServoArchive()
