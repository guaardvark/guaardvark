"""Sliding window context buffer with temporal compression.

Maintains a rolling window of frame descriptions. Older entries are
compressed into a summary string (no LLM call — just concatenation
and truncation). Provides FPS-adaptive confidence labels.
"""
import time
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("vision_pipeline.context_buffer")


@dataclass
class VisionContext:
    current_scene: str = ""
    recent_changes: List[str] = field(default_factory=list)
    summary: str = ""
    is_active: bool = False
    last_update: float = 0
    confidence: str = "stale"
    model_used: str = ""


class ContextBuffer:
    def __init__(self, window_seconds: int = 30, max_entries: int = 60,
                 compression_interval: int = 15, max_context_tokens: int = 500,
                 default_max_fps: float = 2.0):
        self.window_seconds = window_seconds
        self.max_entries = max_entries
        self.compression_interval = compression_interval
        self.max_context_tokens = max_context_tokens
        self.default_max_fps = default_max_fps
        self.entries = deque(maxlen=max_entries)
        self.compressed_summary = ""
        self._last_compression = time.time()

    def add(self, analysis):
        """Add a FrameAnalysis entry. Auto-compresses if interval elapsed."""
        self.entries.append(analysis)
        if time.time() - self._last_compression >= self.compression_interval:
            self.compress()

    def compress(self):
        """Compression algorithm:
        1. Partition entries: recent = within window_seconds/2, old = before that
        2. Extract description from old entries, bracket-wrap each
        3. Join with ' → ', prefix with 'Earlier: '
        4. Truncate from LEFT (keep newest) to max_context_tokens chars
        5. Remove old entries from self.entries
        """
        now = time.time()
        cutoff = now - (self.window_seconds / 2)

        recent = []
        old_descriptions = []
        for entry in self.entries:
            if entry.timestamp >= cutoff:
                recent.append(entry)
            else:
                old_descriptions.append(f"[{entry.description}]")

        if old_descriptions:
            joined = " → ".join(old_descriptions)
            # Accumulate with any previously compressed content
            if self.compressed_summary:
                # Strip existing "Earlier: " prefix and merge
                existing = self.compressed_summary[len("Earlier: "):] if self.compressed_summary.startswith("Earlier: ") else self.compressed_summary
                summary = f"Earlier: {existing} → {joined}"
            else:
                summary = f"Earlier: {joined}"
            # Truncate from LEFT to keep newest summaries
            if len(summary) > self.max_context_tokens:
                summary = "Earlier: ..." + summary[-(self.max_context_tokens - 14):]
            self.compressed_summary = summary

        # Keep only recent entries
        self.entries = deque(recent, maxlen=self.max_entries)
        self._last_compression = time.time()

    def get_context(self, current_interval: float = None) -> VisionContext:
        """Return context package for chat injection.

        Args:
            current_interval: Current seconds between analyses (from adaptive throttle).
                If None, defaults to 1.0 / default_max_fps.
        """
        if not self.entries:
            return VisionContext()

        if current_interval is None:
            current_interval = 1.0 / self.default_max_fps

        latest = self.entries[-1]
        recent = [e.description for e in list(self.entries)[-5:]]
        age = time.time() - latest.timestamp
        confidence = self._compute_confidence(age, current_interval)

        return VisionContext(
            current_scene=latest.description,
            recent_changes=recent,
            summary=self.compressed_summary,
            is_active=True,
            last_update=latest.timestamp,
            confidence=confidence,
            model_used=latest.model_used,
        )

    def _compute_confidence(self, age: float, current_interval: float) -> str:
        """FPS-adaptive confidence labels."""
        if age < 2 * current_interval:
            return "fresh"
        elif age < 5 * current_interval:
            return "recent"
        return "stale"

    def clear(self):
        """Reset all state."""
        self.entries = deque(maxlen=self.max_entries)
        self.compressed_summary = ""
        self._last_compression = time.time()
