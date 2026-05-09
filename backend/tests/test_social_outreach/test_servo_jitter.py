"""Servo timing should use randomized jitter to avoid deterministic bot patterns."""
from unittest.mock import patch
import statistics

from backend.services.social_outreach import reddit_outreach


def test_servo_sleep_is_jittered():
    """Multiple type/click sequences should produce varied sleep durations."""
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)

    with patch("backend.services.social_outreach.reddit_outreach.time.sleep", side_effect=fake_sleep):
        for _ in range(20):
            reddit_outreach._human_pause()

    assert len(sleeps) == 20
    # Variance should be non-trivial (not all the same value)
    assert statistics.stdev(sleeps) > 0.05, f"stdev too low: {statistics.stdev(sleeps)}"
    # All durations within reasonable bounds
    assert all(0.3 <= s <= 2.0 for s in sleeps), f"out of range: {[s for s in sleeps if not (0.3 <= s <= 2.0)]}"
