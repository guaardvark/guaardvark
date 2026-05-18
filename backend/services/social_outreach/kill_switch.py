"""
Kill switch + cadence enforcement for outreach.

Three layers of brakes:
  1. is_enabled() — global on/off via Setting('social_outreach_enabled', 'true'/'false'). Defaults false.
  2. is_supervised() — when true, drafts queue for review instead of posting. Defaults false (per user choice; flip to true if first night looks bot-y).
  3. cadence checks — Redis-backed, per-platform. Hard caps: 1 post / 30 min / platform, 8 posts / 24h / platform.

Plus task-level abort on 2 servo failures (enforced by the loop, not here).

If Redis is unavailable we fail closed — better to not post than to spam.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Hard caps — change these in code, not config, so a misclick can't open the firehose.
CADENCE_MIN_GAP_SECONDS = 30 * 60        # 1 per 30 min per platform
CADENCE_DAILY_CAP = 8                    # 8 per day per platform
CADENCE_DAILY_WINDOW_SECONDS = 24 * 3600
SERVO_FAILURE_ABORT_THRESHOLD = 2

REDIS_KEY_LAST_POST = "social_outreach:last_post:{platform}"
REDIS_KEY_DAILY_LIST = "social_outreach:posts_24h:{platform}"  # zset of timestamps


def _get_redis():
    """Lazy redis client. Returns None if unreachable."""
    try:
        import redis
        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2)
        client.ping()
        return client
    except Exception as e:
        logger.warning("redis unavailable for cadence: %s", e)
        return None


def _read_setting(key: str, default: str) -> str:
    try:
        from backend.models import Setting
        row = Setting.query.filter_by(key=key).first()
        if row and row.value is not None:
            return str(row.value)
    except Exception as e:
        logger.warning("setting read failed for %s: %s", key, e)
    return default


def is_enabled() -> bool:
    val = _read_setting("social_outreach_enabled", "false").strip().lower()
    return val in ("true", "1", "yes", "on")


def is_supervised() -> bool:
    val = _read_setting("social_outreach_supervised", "false").strip().lower()
    return val in ("true", "1", "yes", "on")


def set_enabled(value: bool) -> None:
    _write_setting("social_outreach_enabled", "true" if value else "false")


def set_supervised(value: bool) -> None:
    _write_setting("social_outreach_supervised", "true" if value else "false")


def _write_setting(key: str, value: str) -> None:
    try:
        from backend.models import Setting, db
        row = Setting.query.filter_by(key=key).first()
        if row is None:
            row = Setting(key=key, value=value)
            db.session.add(row)
        else:
            row.value = value
        db.session.commit()
    except Exception as e:
        logger.error("setting write failed for %s: %s", key, e)
        try:
            from backend.models import db
            db.session.rollback()
        except Exception:
            pass


def cadence_allows_post(platform: str) -> tuple[bool, Optional[str]]:
    """
    Returns (allowed, reason_if_not). Fails closed on Redis errors.
    """
    r = _get_redis()
    if r is None:
        return False, "redis unavailable (failing closed)"

    now = time.time()

    last_post_key = REDIS_KEY_LAST_POST.format(platform=platform)
    last = r.get(last_post_key)
    if last is not None:
        try:
            elapsed = now - float(last)
            if elapsed < CADENCE_MIN_GAP_SECONDS:
                return False, f"too soon ({int(elapsed)}s since last, need {CADENCE_MIN_GAP_SECONDS}s)"
        except ValueError:
            pass

    daily_key = REDIS_KEY_DAILY_LIST.format(platform=platform)
    cutoff = now - CADENCE_DAILY_WINDOW_SECONDS
    r.zremrangebyscore(daily_key, 0, cutoff)
    count_24h = r.zcard(daily_key)
    if count_24h >= CADENCE_DAILY_CAP:
        return False, f"daily cap hit ({count_24h}/{CADENCE_DAILY_CAP} in 24h)"

    return True, None


def record_post(platform: str) -> None:
    r = _get_redis()
    if r is None:
        logger.warning("record_post: redis unavailable, cadence will under-count")
        return
    now = time.time()
    r.set(REDIS_KEY_LAST_POST.format(platform=platform), str(now))
    r.zadd(REDIS_KEY_DAILY_LIST.format(platform=platform), {str(now): now})
    r.expire(REDIS_KEY_DAILY_LIST.format(platform=platform), CADENCE_DAILY_WINDOW_SECONDS + 60)


def cadence_status() -> dict:
    """Snapshot for /status endpoint."""
    r = _get_redis()
    out = {}
    platforms = ["reddit", "discord", "facebook"]
    if r is None:
        for p in platforms:
            out[p] = {"redis": "unavailable"}
        return out
    now = time.time()
    for p in platforms:
        last = r.get(REDIS_KEY_LAST_POST.format(platform=p))
        daily_key = REDIS_KEY_DAILY_LIST.format(platform=p)
        r.zremrangebyscore(daily_key, 0, now - CADENCE_DAILY_WINDOW_SECONDS)
        out[p] = {
            "last_post_seconds_ago": (int(now - float(last)) if last else None),
            "posts_in_24h": r.zcard(daily_key),
            "min_gap_s": CADENCE_MIN_GAP_SECONDS,
            "daily_cap": CADENCE_DAILY_CAP,
        }
    return out
