"""Per-user sliding window rate limiter."""
import time
from collections import defaultdict


class RateLimiter:
    """Sliding window rate limiter keyed by (user_id, command)."""

    def __init__(self, max_requests: int = 10, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[tuple, list[float]] = defaultdict(list)

    def check(self, user_id: int, command: str) -> tuple[bool, int, float]:
        """Check if a request is allowed. Returns (allowed, remaining, retry_after_seconds)."""
        key = (user_id, command)
        now = time.monotonic()
        cutoff = now - self.window_seconds
        self._requests[key] = [t for t in self._requests[key] if t > cutoff]
        count = len(self._requests[key])
        if count >= self.max_requests:
            oldest = self._requests[key][0]
            retry_after = round(oldest + self.window_seconds - now, 1)
            return False, 0, max(0, retry_after)
        self._requests[key].append(now)
        remaining = self.max_requests - count - 1
        return True, remaining, 0
