import time
import pytest
from unittest.mock import MagicMock, patch
from service.stream_manager import StreamManager, Stream


def _make_deps():
    """Create mock dependencies for StreamManager."""
    config = MagicMock()
    config.max_concurrent_streams = 2
    config.stale_timeout_seconds = 5
    frame_analyzer = MagicMock()
    frame_analyzer.analyze.return_value = MagicMock(
        description="test scene", model_used="qwen3.5:4b",
        inference_ms=100, timestamp=time.time(), frame_dimensions=(512, 512)
    )
    change_detector = MagicMock()
    change_detector.should_process.return_value = (True, "new_scene")
    context_buffer = MagicMock()
    model_tier = MagicMock()
    model_tier.select_model.return_value = ("qwen3.5:4b", "Describe.")
    adaptive_throttle = MagicMock()
    adaptive_throttle.get_interval.return_value = 0.5
    adaptive_throttle.is_paused = False
    return config, frame_analyzer, change_detector, context_buffer, model_tier, adaptive_throttle


class TestStreamManager:
    def test_start_stream_creates_stream(self):
        sm = StreamManager(*_make_deps())
        stream = sm.start_stream("test-1", "camera")
        assert isinstance(stream, Stream)
        assert stream.id == "test-1"
        assert stream.status == "active"

    def test_max_concurrent_streams_enforced(self):
        sm = StreamManager(*_make_deps())
        sm.start_stream("s1", "camera")
        sm.start_stream("s2", "camera")
        with pytest.raises(RuntimeError, match="max concurrent"):
            sm.start_stream("s3", "camera")

    def test_stop_stream(self):
        sm = StreamManager(*_make_deps())
        sm.start_stream("test-1", "camera")
        stats = sm.stop_stream("test-1")
        assert "test-1" not in sm.streams

    def test_submit_frame_accepted(self):
        sm = StreamManager(*_make_deps())
        sm.start_stream("test-1", "camera")
        result = sm.submit_frame("test-1", "base64data")
        assert result["accepted"] is True

    def test_submit_frame_keeps_the_callers_timestamp(self):
        sm = StreamManager(*_make_deps())
        sm.start_stream("test-1", "camera")
        sm.submit_frame("test-1", "base64data", timestamp=1700000000.5)
        assert sm.get_latest_frame("test-1")["timestamp"] == 1700000000.5

    def test_loop_passes_captured_at_to_the_analyzer(self):
        deps = _make_deps()
        sm = StreamManager(*deps)
        sm.start_stream("test-1", "camera")
        sm.submit_frame("test-1", "base64data", timestamp=1700000000.5)
        time.sleep(0.3)
        frame_analyzer = deps[1]
        assert frame_analyzer.analyze.call_args.kwargs["captured_at"] == 1700000000.5
        sm.stop_stream("test-1")

    def test_submit_frame_unknown_stream(self):
        sm = StreamManager(*_make_deps())
        result = sm.submit_frame("nonexistent", "base64data")
        assert result["accepted"] is False

    def test_pause_resume(self):
        sm = StreamManager(*_make_deps())
        sm.start_stream("test-1", "camera")
        sm.pause_stream("test-1")
        assert sm.streams["test-1"].status == "paused"
        sm.resume_stream("test-1")
        assert sm.streams["test-1"].status == "active"

    def test_get_status(self):
        sm = StreamManager(*_make_deps())
        sm.start_stream("test-1", "camera")
        status = sm.get_status()
        assert "test-1" in status

    def teardown_method(self):
        """Ensure all streams are stopped after each test."""
        # StreamManager instances are local, threads are daemon=True
        pass
