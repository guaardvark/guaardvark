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
        source: str = "servo",
    ):
        self._counter += 1
        img_name = f"{self._session_id}_{self._counter:05d}.webp"
        img_path = self.screenshots_dir / img_name
        screenshot_before.save(str(img_path), format="WEBP", quality=75)

        entry = {
            "timestamp": datetime.now().isoformat(),
            "screenshot_path": str(img_path),
            "crosshair_pos": list(crosshair_pos),
            "target_description": target_description,
            "target_actual": list(target_actual),
            "corrections": corrections,
            "success": success,
            "app_context": app_context,
            "source": source,
        }

        with open(self._log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

        logger.debug(f"Recorded servo interaction #{self._counter}: {target_description} success={success}")

    def stats(self) -> Dict[str, int]:
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


def capture_servo_failure(
    screenshot,
    target_description: str,
    corrections_log: List[Dict[str, Any]],
    vision_model: str,
    reason: str,
) -> Path | None:
    """Save the last screenshot before a servo abort + sidecar JSON for training.

    Returns the path written, or None if capture failed (don't break the calling
    path on a save error — failure capture is best-effort).
    """
    import time
    import hashlib

    root = os.environ.get("GUAARDVARK_ROOT", ".")
    failures_dir = Path(root) / "data" / "training" / "failures"
    failures_dir.mkdir(parents=True, exist_ok=True)

    ts = int(time.time())
    target_hash = hashlib.sha256(target_description.encode("utf-8")).hexdigest()[:8]
    base = failures_dir / f"{ts}_{target_hash}"

    img_path = base.with_suffix(".webp")
    json_path = base.with_suffix(".json")

    try:
        # PIL Image or numpy array — convert if needed
        if hasattr(screenshot, 'save'):
            screenshot.save(str(img_path), "WEBP", quality=85)
        else:
            # Assume it's a numpy array
            Image.fromarray(screenshot).save(str(img_path), "WEBP", quality=85)
    except Exception as e:
        # Don't break the abort path — log and continue
        logger.warning(f"failure-trace screenshot save failed: {e}")
        return None

    metadata = {
        "timestamp": ts,
        "target_description": target_description,
        "vision_model": vision_model,
        "reason": reason,
        "corrections_log": [str(c) for c in corrections_log][:20],
    }
    try:
        json_path.write_text(json.dumps(metadata, indent=2))
    except Exception as e:
        logger.warning(f"failure-trace metadata save failed: {e}")

    return img_path
