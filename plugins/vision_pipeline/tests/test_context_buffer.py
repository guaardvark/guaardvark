import time
import pytest
from dataclasses import dataclass

from service.context_buffer import ContextBuffer, VisionContext


@dataclass
class FakeAnalysis:
    description: str
    model_used: str = "moondream"
    inference_ms: int = 100
    timestamp: float = 0
    frame_dimensions: tuple = (512, 512)


class TestContextBuffer:
    def test_empty_buffer_returns_inactive(self):
        buf = ContextBuffer()
        ctx = buf.get_context(current_interval=0.5)
        assert ctx.is_active is False
        assert ctx.current_scene == ""

    def test_add_single_entry(self):
        buf = ContextBuffer()
        buf.add(FakeAnalysis(description="person at desk", timestamp=time.time()))
        ctx = buf.get_context(current_interval=0.5)
        assert ctx.is_active is True
        assert ctx.current_scene == "person at desk"

    def test_recent_changes_limited_to_5(self):
        buf = ContextBuffer()
        for i in range(10):
            buf.add(FakeAnalysis(description=f"scene {i}", timestamp=time.time()))
        ctx = buf.get_context(current_interval=0.5)
        assert len(ctx.recent_changes) == 5
        assert ctx.recent_changes[-1] == "scene 9"

    def test_compression_brackets_and_joins(self):
        buf = ContextBuffer(window_seconds=2, compression_interval=0)
        # Add old entries
        old_time = time.time() - 10
        buf.add(FakeAnalysis(description="typing on laptop", timestamp=old_time))
        buf.add(FakeAnalysis(description="picked up phone", timestamp=old_time + 0.5))
        # Add recent entry
        buf.add(FakeAnalysis(description="reading document", timestamp=time.time()))
        buf.compress()
        assert "[typing on laptop]" in buf.compressed_summary
        assert "→" in buf.compressed_summary
        assert "Earlier:" in buf.compressed_summary

    def test_confidence_fresh(self):
        buf = ContextBuffer()
        buf.add(FakeAnalysis(description="test", timestamp=time.time()))
        ctx = buf.get_context(current_interval=0.5)
        assert ctx.confidence == "fresh"

    def test_confidence_stale(self):
        buf = ContextBuffer()
        buf.add(FakeAnalysis(description="test", timestamp=time.time() - 30))
        ctx = buf.get_context(current_interval=0.5)
        assert ctx.confidence == "stale"

    def test_clear_resets_state(self):
        buf = ContextBuffer()
        buf.add(FakeAnalysis(description="test", timestamp=time.time()))
        buf.clear()
        ctx = buf.get_context(current_interval=0.5)
        assert ctx.is_active is False

    def test_max_entries_enforced(self):
        buf = ContextBuffer(max_entries=5)
        for i in range(20):
            buf.add(FakeAnalysis(description=f"scene {i}", timestamp=time.time()))
        assert len(buf.entries) <= 5

    def test_truncation_keeps_newest(self):
        buf = ContextBuffer(window_seconds=1, compression_interval=0, max_context_tokens=50)
        old = time.time() - 10
        for i in range(20):
            buf.add(FakeAnalysis(description=f"very long scene description number {i}", timestamp=old + i * 0.1))
        buf.add(FakeAnalysis(description="current", timestamp=time.time()))
        buf.compress()
        assert len(buf.compressed_summary) <= 60  # some margin for prefix
