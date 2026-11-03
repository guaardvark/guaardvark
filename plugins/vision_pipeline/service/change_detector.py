"""Perceptual-hash change gate with a periodic refresh.

One layer: a 64-bit perceptual hash of each frame, compared by Hamming
distance to the last analysed frame. It is a "worth looking again" gate
for a live camera, not a shot-boundary detector: it fires on pans, large
subject motion and lighting shifts, and cannot see dissolves.

Periodic refresh forces re-analysis every N seconds even on static scenes.
"""
import base64
import io
import logging
import time
from typing import Optional, Tuple

from PIL import Image

from service.phash import hamming, phash

logger = logging.getLogger("vision_pipeline.change_detector")


class ChangeDetector:
    def __init__(self, phash_threshold: int = 8, periodic_refresh_seconds: int = 10):
        # Hamming distance (0..64) above which two frames count as different.
        self.phash_threshold = int(phash_threshold)
        self.periodic_refresh_seconds = periodic_refresh_seconds
        self.last_frame_hash: Optional[int] = None
        self.last_description: Optional[str] = None
        self._last_analysis_time: float = 0

    def has_visual_change(self, frame_base64: str) -> bool:
        """True if the frame's hash differs from the last one beyond the threshold."""
        try:
            current_hash = self._hash(frame_base64)
            if self.last_frame_hash is None:
                return True
            return hamming(current_hash, self.last_frame_hash) > self.phash_threshold
        except Exception as e:  # noqa: BLE001
            logger.warning("Visual change detection failed: %s", e)
            return True  # process on error, safer than skipping

    def should_process(self, frame_base64: str) -> Tuple[bool, str]:
        """Decide whether to run vision inference on this frame.

        Returns (should_process, reason):
            - 'new_scene': first frame ever
            - 'periodic_refresh': static scene, but refresh interval elapsed
            - 'visual_change': perceptual hash differs
            - 'no_change': skip this frame
        """
        now = time.time()

        if self.last_frame_hash is None:
            self._update_hash(frame_base64)
            self._last_analysis_time = now
            return True, "new_scene"

        if now - self._last_analysis_time >= self.periodic_refresh_seconds:
            self._update_hash(frame_base64)
            self._last_analysis_time = now
            return True, "periodic_refresh"

        if self.has_visual_change(frame_base64):
            self._update_hash(frame_base64)
            self._last_analysis_time = now
            return True, "visual_change"

        return False, "no_change"

    def update_last_description(self, description: str):
        """Keep the last description so /status and the context sink can show it."""
        self.last_description = description

    @staticmethod
    def _hash(frame_base64: str) -> int:
        img = Image.open(io.BytesIO(base64.b64decode(frame_base64)))
        return phash(img)

    def _update_hash(self, frame_base64: str):
        try:
            self.last_frame_hash = self._hash(frame_base64)
        except Exception as e:  # noqa: BLE001
            logger.warning("Hash update failed: %s", e)
