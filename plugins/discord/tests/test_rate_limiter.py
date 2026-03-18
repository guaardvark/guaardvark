"""Tests for the sliding window rate limiter."""
import time
from unittest.mock import patch
from core.rate_limiter import RateLimiter


class TestRateLimiter:
    """Tests for RateLimiter."""

    def test_allows_first_request(self):
        rl = RateLimiter(max_requests=5, window_seconds=60)
        allowed, remaining, retry_after = rl.check(1, "ask")
        assert allowed is True
        assert remaining == 4
        assert retry_after == 0

    def test_blocks_after_limit(self):
        rl = RateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            rl.check(1, "ask")
        allowed, remaining, retry_after = rl.check(1, "ask")
        assert allowed is False
        assert remaining == 0
        assert retry_after > 0

    def test_different_users_independent(self):
        rl = RateLimiter(max_requests=2, window_seconds=60)
        rl.check(1, "ask")
        rl.check(1, "ask")
        # User 1 is now exhausted
        allowed_u1, _, _ = rl.check(1, "ask")
        assert allowed_u1 is False
        # User 2 should still be fine
        allowed_u2, remaining, _ = rl.check(2, "ask")
        assert allowed_u2 is True
        assert remaining == 1

    def test_different_commands_independent(self):
        rl = RateLimiter(max_requests=1, window_seconds=60)
        rl.check(1, "ask")
        # "ask" is now exhausted for user 1
        allowed_ask, _, _ = rl.check(1, "ask")
        assert allowed_ask is False
        # "imagine" should still be fine for same user
        allowed_imagine, remaining, _ = rl.check(1, "imagine")
        assert allowed_imagine is True
        assert remaining == 0

    def test_window_expires(self):
        rl = RateLimiter(max_requests=1, window_seconds=1.0)
        rl.check(1, "ask")
        allowed, _, _ = rl.check(1, "ask")
        assert allowed is False
        # Wait for the window to expire
        time.sleep(1.1)
        allowed, remaining, _ = rl.check(1, "ask")
        assert allowed is True
        assert remaining == 0

    def test_remaining_count_decrements(self):
        rl = RateLimiter(max_requests=5, window_seconds=60)
        _, r1, _ = rl.check(1, "ask")
        _, r2, _ = rl.check(1, "ask")
        _, r3, _ = rl.check(1, "ask")
        assert r1 == 4
        assert r2 == 3
        assert r3 == 2
