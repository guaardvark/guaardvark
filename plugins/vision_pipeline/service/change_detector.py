"""Two-layer change detection: perceptual hash (fast) + semantic comparison (accurate).

Layer 1: Perceptual hash via imagehash — immune to JPEG noise and minor lighting shifts.
Layer 2: Token overlap ratio — compares vision model text outputs, no ML needed.
Periodic refresh: Forces re-analysis every N seconds even on static scenes.
"""
import base64
import io
import time
import logging
from typing import Tuple

import imagehash
from PIL import Image

logger = logging.getLogger("vision_pipeline.change_detector")


class ChangeDetector:
    # Note: pixel_threshold uses hamming distance (integer) rather than the spec's
    # float ratio, because imagehash.phash returns integer hamming distances.
    def __init__(self, pixel_threshold: int = 8, semantic_threshold: float = 0.3,
                 periodic_refresh_seconds: int = 10):
        self.pixel_threshold = pixel_threshold  # hamming distance threshold for phash
        self.semantic_threshold = semantic_threshold  # token overlap below this = changed
        self.periodic_refresh_seconds = periodic_refresh_seconds
        self.last_frame_hash = None
        self.last_description: str | None = None
        self._last_analysis_time: float = 0

    def has_visual_change(self, frame_base64: str) -> bool:
        """Compare perceptual hash of frame to previous frame.
        Returns True if frames differ beyond threshold."""
        try:
            img_bytes = base64.b64decode(frame_base64)
            img = Image.open(io.BytesIO(img_bytes))
            current_hash = imagehash.phash(img)

            if self.last_frame_hash is None:
                return True

            distance = current_hash - self.last_frame_hash
            return distance > self.pixel_threshold
        except Exception as e:
            logger.warning(f"Visual change detection failed: {e}")
            return True  # process on error — safer than skipping

    def has_semantic_change(self, new_description: str, old_description: str) -> bool:
        """Compare two descriptions using token overlap ratio.
        Returns True if overlap is below semantic_threshold (i.e., they're different)."""
        if not old_description or not new_description:
            return True

        new_tokens = set(new_description.lower().split())
        old_tokens = set(old_description.lower().split())

        if not new_tokens or not old_tokens:
            return True

        intersection = new_tokens & old_tokens
        union = new_tokens | old_tokens
        overlap = len(intersection) / len(union) if union else 0

        return overlap < self.semantic_threshold

    def should_process(self, frame_base64: str) -> Tuple[bool, str]:
        """Decide whether to run vision inference on this frame.

        Returns (should_process, reason):
            - ('new_scene') — first frame ever
            - ('visual_change') — perceptual hash differs
            - ('periodic_refresh') — static scene, but refresh interval elapsed
            - ('no_change') — skip this frame
        """
        now = time.time()

        # First frame
        if self.last_frame_hash is None:
            self._update_hash(frame_base64)
            self._last_analysis_time = now
            return True, "new_scene"

        # Periodic refresh
        if now - self._last_analysis_time >= self.periodic_refresh_seconds:
            self._update_hash(frame_base64)
            self._last_analysis_time = now
            return True, "periodic_refresh"

        # Visual change
        if self.has_visual_change(frame_base64):
            self._update_hash(frame_base64)
            self._last_analysis_time = now
            return True, "visual_change"

        return False, "no_change"

    def update_last_description(self, description: str):
        """Called after vision inference completes — stores for semantic comparison."""
        self.last_description = description

    def _update_hash(self, frame_base64: str):
        """Update stored hash for next comparison."""
        try:
            img_bytes = base64.b64decode(frame_base64)
            img = Image.open(io.BytesIO(img_bytes))
            self.last_frame_hash = imagehash.phash(img)
        except Exception as e:
            logger.warning(f"Hash update failed: {e}")
