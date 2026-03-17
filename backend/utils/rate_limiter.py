"""
Rate limiting utilities for API endpoints.

This module provides rate limiting functionality to prevent DoS attacks
and abuse of API endpoints.
"""

import time
import logging
from typing import Dict, Optional, Tuple
from functools import wraps
from collections import defaultdict, deque
from threading import Lock

from flask import request, jsonify, current_app

logger = logging.getLogger(__name__)

# Global rate limiter instances
_rate_limiters: Dict[str, "RateLimiter"] = {}
_limiter_lock = Lock()


class RateLimiter:
    """
    Token bucket rate limiter implementation.
    """

    def __init__(
        self, max_requests: int, window_seconds: int, burst_size: Optional[int] = None
    ):
        """
        Initialize rate limiter.

        Args:
            max_requests: Maximum requests allowed in the time window
            window_seconds: Time window in seconds
            burst_size: Maximum burst size (defaults to max_requests)
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.burst_size = burst_size or max_requests

        # Store request timestamps for each client
        self.clients: Dict[str, deque] = defaultdict(lambda: deque())
        self.lock = Lock()

    def is_allowed(self, client_id: str) -> Tuple[bool, Optional[int]]:
        """
        Check if a request from a client is allowed.

        Args:
            client_id: Unique identifier for the client (e.g., IP address)

        Returns:
            Tuple of (is_allowed, retry_after_seconds)
        """
        current_time = time.time()

        with self.lock:
            client_requests = self.clients[client_id]

            # Remove old requests outside the window
            while (
                client_requests
                and client_requests[0] <= current_time - self.window_seconds
            ):
                client_requests.popleft()

            # Check if client has exceeded the limit
            if len(client_requests) >= self.max_requests:
                # Calculate retry after time
                oldest_request = client_requests[0]
                retry_after = (
                    int(oldest_request + self.window_seconds - current_time) + 1
                )
                return False, retry_after

            # Allow the request and record it
            client_requests.append(current_time)
            return True, None

    def cleanup_old_clients(self, max_age_seconds: int = 3600):
        """
        Clean up old client entries to prevent memory leaks.

        Args:
            max_age_seconds: Remove clients not seen for this many seconds
        """
        current_time = time.time()
        cutoff_time = current_time - max_age_seconds

        with self.lock:
            clients_to_remove = []
            for client_id, requests in self.clients.items():
                if not requests or requests[-1] < cutoff_time:
                    clients_to_remove.append(client_id)

            for client_id in clients_to_remove:
                del self.clients[client_id]

            if clients_to_remove:
                logger.debug(
                    f"Cleaned up {len(clients_to_remove)} old rate limiter clients"
                )


def get_client_id(request) -> str:
    """
    Get a unique identifier for the client making the request.

    Args:
        request: Flask request object

    Returns:
        Client identifier string
    """
    # Try to get real IP from headers (for proxy setups)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take the first IP in the chain
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.remote_addr or "unknown"

    # Include user agent for better client identification
    user_agent = request.headers.get("User-Agent", "")[:100]  # Limit length

    return f"{client_ip}:{hash(user_agent)}"


def get_rate_limiter(
    name: str, max_requests: int, window_seconds: int, burst_size: Optional[int] = None
) -> RateLimiter:
    """
    Get or create a rate limiter instance.

    Args:
        name: Name of the rate limiter
        max_requests: Maximum requests allowed in the time window
        window_seconds: Time window in seconds
        burst_size: Maximum burst size

    Returns:
        RateLimiter instance
    """
    with _limiter_lock:
        if name not in _rate_limiters:
            _rate_limiters[name] = RateLimiter(max_requests, window_seconds, burst_size)
        return _rate_limiters[name]


def rate_limit(
    max_requests: int = 60,
    window_seconds: int = 60,
    burst_size: Optional[int] = None,
    per_endpoint: bool = True,
):
    """
    Decorator to apply rate limiting to Flask routes.

    Args:
        max_requests: Maximum requests allowed in the time window
        window_seconds: Time window in seconds
        burst_size: Maximum burst size
        per_endpoint: If True, rate limit per endpoint; if False, globally

    Returns:
        Decorator function
    """

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Determine rate limiter name
            if per_endpoint:
                limiter_name = f"{f.__module__}.{f.__name__}"
            else:
                limiter_name = "global"

            # Get rate limiter
            limiter = get_rate_limiter(
                limiter_name, max_requests, window_seconds, burst_size
            )

            # Get client identifier
            client_id = get_client_id(request)

            # Check rate limit
            is_allowed, retry_after = limiter.is_allowed(client_id)

            if not is_allowed:
                logger.warning(
                    f"Rate limit exceeded for client {client_id} on endpoint {limiter_name}"
                )

                response = jsonify(
                    {
                        "error": "Rate limit exceeded",
                        "message": f"Too many requests. Try again in {retry_after} seconds.",
                        "retry_after": retry_after,
                    }
                )
                response.status_code = 429
                response.headers["Retry-After"] = str(retry_after)
                return response

            return f(*args, **kwargs)

        return decorated_function

    return decorator


def strict_rate_limit(max_requests: int = 10, window_seconds: int = 60):
    """
    Strict rate limiting decorator for sensitive endpoints.

    Args:
        max_requests: Maximum requests allowed in the time window
        window_seconds: Time window in seconds

    Returns:
        Decorator function
    """
    return rate_limit(max_requests, window_seconds, max_requests, per_endpoint=True)


def cleanup_rate_limiters():
    """
    Clean up old entries from all rate limiters.
    Call this periodically to prevent memory leaks.
    """
    with _limiter_lock:
        for limiter in _rate_limiters.values():
            limiter.cleanup_old_clients()


# Predefined rate limiting decorators for common use cases
api_rate_limit = rate_limit(100, 60)  # 100 requests per minute for general API
upload_rate_limit = rate_limit(10, 60)  # 10 uploads per minute
auth_rate_limit = strict_rate_limit(5, 60)  # 5 auth attempts per minute
sensitive_rate_limit = strict_rate_limit(
    3, 300
)  # 3 requests per 5 minutes for sensitive ops
