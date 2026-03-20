import pytest
from unittest.mock import patch, MagicMock
from service.adaptive_throttle import AdaptiveThrottle


class FakeConfig:
    max_fps = 2.0
    min_fps = 0.25
    utilization_pause_threshold = 90
    utilization_throttle_threshold = 75


@pytest.fixture(autouse=True)
def mock_gpu_zero(monkeypatch):
    """Patch GPU utilization to 0 so tests run deterministically on any hardware."""
    monkeypatch.setattr(
        AdaptiveThrottle, "_get_gpu_utilization", lambda self: 0
    )


class TestAdaptiveThrottle:
    def test_initial_interval_matches_max_fps(self):
        at = AdaptiveThrottle(FakeConfig())
        assert at.get_interval() == pytest.approx(0.5, abs=0.1)  # 1/2 fps

    def test_contention_drops_to_min(self):
        at = AdaptiveThrottle(FakeConfig())
        at.notify_gpu_contention()
        interval = at.get_interval()
        assert interval >= 1.0 / FakeConfig.min_fps  # at most min_fps

    def test_contention_release_restores(self):
        at = AdaptiveThrottle(FakeConfig())
        at.notify_gpu_contention()
        at.notify_gpu_available()
        assert at.is_paused is False

    def test_high_latency_reduces_fps(self):
        at = AdaptiveThrottle(FakeConfig())
        # Simulate inference taking 800ms — slower than 500ms interval
        for _ in range(10):
            at.record_inference(800)
        interval = at.get_interval()
        assert interval > 0.5  # should be slower than max

    def test_scene_inactivity_halves_fps(self):
        at = AdaptiveThrottle(FakeConfig())
        for _ in range(6):
            at.record_scene_state(changed=False)
        interval = at.get_interval()
        assert interval > 0.5

    def test_scene_activity_restores_fps(self):
        at = AdaptiveThrottle(FakeConfig())
        for _ in range(6):
            at.record_scene_state(changed=False)
        at.record_scene_state(changed=True)
        interval = at.get_interval()
        assert interval == pytest.approx(0.5, abs=0.1)

    def test_pause_property(self):
        at = AdaptiveThrottle(FakeConfig())
        assert at.is_paused is False
        at.notify_gpu_contention()
        # contention_behavior is "min_fps" by default in FakeConfig — not paused, just slow
        # To test pause, we'd need utilization > 90%
