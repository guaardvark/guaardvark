"""
Kill switch + cadence enforcement for outreach.

Above everything sits the stop on all public posting, posting_stop_reason():
Setting('public_posting_stopped'). It covers outreach and Connections publishing
alike, is never set on a fresh install, and is its own setting rather than
social_outreach_enabled, which defaults off and must not hold back a publish.
stop_all_posting() engages it and holds what is queued; resume_posting() lifts it.

Brakes for outreach, under that stop:
  1. is_enabled() — global on/off via Setting('social_outreach_enabled', 'true'/'false'). Defaults false.
     Also false while public posting is stopped.
  2. A person's approval for every draft. There is no setting that lets a draft post
     without one; what may happen after the approval is per platform (platforms.py).
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

POSTING_STOP_KEY = "public_posting_stopped"
POSTING_STOPPED_REASON = "Public posting is stopped. Resume it on the Outreach page."
POSTING_STOP_UNREADABLE_REASON = "The public posting stop could not be read; refusing to post."


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


_direct_engine = None


def _get_direct_engine():
    """SQLAlchemy engine for reads outside Flask app context (Celery beat early-exit)."""
    global _direct_engine
    if _direct_engine is None:
        from sqlalchemy import create_engine

        url = os.environ.get(
            "DATABASE_URL",
            "postgresql://guaardvark:guaardvark@localhost:5432/guaardvark",
        )
        # A database host that accepts no connection must fail the read, not
        # hang the beat tick that asked.
        connect_args = {"connect_timeout": 3} if url.startswith("postgresql") else {}
        _direct_engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
    return _direct_engine


def _lookup_setting_direct(key: str) -> Optional[str]:
    from sqlalchemy import text

    with _get_direct_engine().connect() as conn:
        row = conn.execute(
            text("SELECT value FROM settings WHERE key = :key"),
            {"key": key},
        ).fetchone()
    return str(row[0]) if row and row[0] is not None else None


def _lookup_setting(key: str) -> Optional[str]:
    """The stored value, or None when the key was never set.

    Raises when the settings table cannot be read, so a caller can tell
    "off" from "unknown".
    """
    try:
        from flask import has_app_context

        if has_app_context():
            from backend.models import Setting

            row = Setting.query.filter_by(key=key).first()
            return str(row.value) if row and row.value is not None else None
    except Exception as e:
        logger.warning("setting read failed for %s: %s", key, e)
    return _lookup_setting_direct(key)


def _read_setting(key: str, default: str) -> str:
    try:
        value = _lookup_setting(key)
    except Exception as e:
        logger.warning("direct setting read failed for %s: %s", key, e)
        return default
    return default if value is None else value


def _is_on(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("true", "1", "yes", "on")


def posting_stop_reason() -> Optional[str]:
    """Why nothing may be posted publicly right now, or None when posting may go ahead.

    Covers outreach and Connections publishing. A stop that cannot be read
    counts as engaged: unknown must not mean "post".
    """
    try:
        value = _lookup_setting(POSTING_STOP_KEY)
    except Exception as e:
        logger.warning("posting stop unreadable, treating as stopped: %s", e)
        return POSTING_STOP_UNREADABLE_REASON
    return POSTING_STOPPED_REASON if _is_on(value) else None


def posting_stopped() -> bool:
    return posting_stop_reason() is not None


def is_enabled() -> bool:
    """Whether outreach may run: switched on and public posting not stopped.
    Off when either setting cannot be read."""
    return _is_on(_read_setting("social_outreach_enabled", "false")) and not posting_stopped()


def status_snapshot() -> dict:
    """What GET /api/social-outreach/status and outreach_status report.

    ``settings_readable`` is false when the settings table could not be read;
    ``enabled`` is then false, the value the posting paths use, not what the
    user set. ``enabled`` is the outreach switch as set; ``posting_stopped`` is
    the stop on all public posting, which holds outreach back whatever
    ``enabled`` says. ``supervised`` is always true: every draft waits for a
    person's approval, and clients that show the mode read it here.
    ``posting_modes`` is the per-platform table (platforms.to_dict).
    """
    from backend.services.social_outreach import platforms

    readable = True
    try:
        enabled = _is_on(_lookup_setting("social_outreach_enabled"))
    except Exception as e:
        logger.warning("outreach settings unreadable: %s", e)
        readable, enabled = False, False
    stop_reason = posting_stop_reason()
    return {
        "enabled": enabled,
        "supervised": True,
        "posting_stopped": stop_reason is not None,
        "posting_stop_reason": stop_reason,
        "settings_readable": readable,
        "posting_modes": platforms.to_dict(),
        "caps": {
            "min_gap_seconds": CADENCE_MIN_GAP_SECONDS,
            "daily_cap": CADENCE_DAILY_CAP,
            "servo_failure_abort_threshold": SERVO_FAILURE_ABORT_THRESHOLD,
        },
        "cadence": cadence_status(),
    }


def set_enabled(value: bool) -> None:
    _write_setting("social_outreach_enabled", "true" if value else "false")


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


# Drafts waiting for a person on one platform before a scout stops adding to
# them. A queue of ten is an evening's review; past that the oldest threads
# have gone stale by the time they are read. GUAARDVARK_OUTREACH_REVIEW_BACKLOG
# changes it; 0 removes the limit.
REVIEW_BACKLOG_LIMIT = 10


def review_backlog_reason(platform: str) -> Optional[str]:
    """``review_backlog:<n>`` when ``platform`` has that many drafts waiting
    for review and the limit is reached; None when a scout may add more."""
    try:
        limit = int(os.environ.get("GUAARDVARK_OUTREACH_REVIEW_BACKLOG", REVIEW_BACKLOG_LIMIT))
    except ValueError:
        limit = REVIEW_BACKLOG_LIMIT
    if limit <= 0:
        return None
    try:
        from backend.models import SocialOutreachLog

        waiting = (
            SocialOutreachLog.query
            .filter(SocialOutreachLog.platform == (platform or "").strip().lower())
            .filter(SocialOutreachLog.status == "drafted")
            .count()
        )
    except Exception as e:
        logger.warning("review backlog unreadable for %s: %s", platform, e)
        return None
    return f"review_backlog:{waiting}" if waiting >= limit else None


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


def _celery_message_is_outreach(raw) -> bool:
    """True if a Redis-broker Celery message is a social_outreach.* task."""
    try:
        import json

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        msg = json.loads(raw)
        task = (msg.get("headers") or {}).get("task") or ""
        return task.startswith("social_outreach.")
    except Exception:
        return False


def drain_pending_outreach_tasks(queues: tuple[str, ...] = ("default", "celery")) -> dict:
    """Revoke in-flight and drop queued social_outreach.* broker messages."""
    purged = 0
    revoked = 0
    errors: list[str] = []

    try:
        from backend.celery_app import celery

        insp = celery.control.inspect(timeout=3.0)
        prefix = "social_outreach."
        for snapshot in (
            insp.active() or {},
            insp.reserved() or {},
        ):
            for _worker, tasks in snapshot.items():
                for task in tasks:
                    name = task.get("name") or ""
                    if name.startswith(prefix):
                        celery.control.revoke(
                            task["id"],
                            terminate=True,
                            signal="SIGTERM",
                        )
                        revoked += 1
        for _worker, entries in (insp.scheduled() or {}).items():
            for entry in entries:
                req = entry.get("request") or {}
                name = req.get("name") or ""
                if name.startswith(prefix):
                    celery.control.revoke(req["id"], terminate=False)
                    revoked += 1
    except Exception as e:
        errors.append(f"revoke: {e}")

    r = _get_redis()
    if r is None:
        errors.append("purge: redis unavailable")
    else:
        try:
            for queue_name in queues:
                length = r.llen(queue_name)
                if not length:
                    continue
                kept: list = []
                for raw in r.lrange(queue_name, 0, -1):
                    if _celery_message_is_outreach(raw):
                        purged += 1
                    else:
                        kept.append(raw)
                pipe = r.pipeline()
                pipe.delete(queue_name)
                if kept:
                    pipe.rpush(queue_name, *kept)
                pipe.execute()
        except Exception as e:
            errors.append(f"purge: {e}")

    return {"purged": purged, "revoked": revoked, "errors": errors}


def _stored_celery_id(row) -> Optional[str]:
    """The Celery id a dispatcher kept in the Task's handler_config, if any.

    The tasks table has no column for it, and job_id holds "task_<id>" once
    the executor starts, so a row without this cannot be revoked by id.
    """
    config = row.handler_config if isinstance(row.handler_config, dict) else {}
    value = config.get("celery_task_id")
    return str(value) if value else None


def _cancel_task_rows(task_handler: str) -> dict:
    """Mark queued/in-progress Task rows of one handler as cancelled, revoking
    the Celery job where its id was stored."""
    cancelled = 0
    revoked = 0
    errors: list[str] = []

    try:
        from flask import has_app_context

        if not has_app_context():
            return {"cancelled_tasks": 0, "revoked_tasks": 0, "errors": ["no app context"]}

        from backend.models import Task, db

        rows = (
            Task.query.filter(Task.task_handler == task_handler)
            .filter(Task.status.in_(("queued", "pending", "in-progress")))
            .all()
        )
        if not rows:
            return {"cancelled_tasks": 0, "revoked_tasks": 0, "errors": errors}

        try:
            from backend.celery_app import celery
        except Exception as e:
            celery = None
            errors.append(f"celery import: {e}")

        for row in rows:
            celery_id = _stored_celery_id(row)
            if celery and celery_id:
                try:
                    celery.control.revoke(
                        celery_id,
                        terminate=True,
                        signal="SIGTERM",
                    )
                    revoked += 1
                except Exception as e:
                    errors.append(f"revoke task {row.id}: {e}")
            row.status = "cancelled"
            cancelled += 1
        db.session.commit()
    except Exception as e:
        errors.append(f"cancel tasks: {e}")
        try:
            from backend.models import db

            db.session.rollback()
        except Exception:
            pass

    return {"cancelled_tasks": cancelled, "revoked_tasks": revoked, "errors": errors}


def cancel_outreach_task_rows() -> dict:
    """Mark queued/in-progress Task-backed outreach runs as cancelled."""
    return _cancel_task_rows("social_outreach")


def hold_connection_publishes() -> dict:
    """Cancel Connections publish jobs and hold the publishes they carried.

    Each queued publish goes back to awaiting_approval, so it waits on the
    Approvals page for a person's click once posting resumes. A publish
    already being sent is counted in ``in_flight``: it cannot be recalled.
    """
    result = _cancel_task_rows("connections")
    held = in_flight = 0
    errors = list(result["errors"])
    try:
        from flask import has_app_context

        if has_app_context():
            from backend.services.connections import publish_service

            held, in_flight = publish_service.hold_queued()
    except Exception as e:
        errors.append(f"hold publishes: {e}")
        try:
            from backend.models import db

            db.session.rollback()
        except Exception:
            pass
    return {
        "held_publishes": held,
        "in_flight_publishes": in_flight,
        "cancelled_publish_tasks": result["cancelled_tasks"],
        "revoked_publish_tasks": result["revoked_tasks"],
        "errors": errors,
    }


def apply_kill_switch() -> dict:
    """Hard-stop outreach: flip off, drain broker queue, cancel Task rows."""
    set_enabled(False)
    result = {"enabled": False}
    result.update(drain_pending_outreach_tasks())
    result.update(cancel_outreach_task_rows())
    return result


def stop_all_posting() -> dict:
    """Engage the stop on all public posting, outreach and Connections alike.

    The stop is written first, so every posting path refuses from here on;
    then queued work is cleared, a watched post that has not reached its
    submit step included. Approved outreach drafts stay approved and post as
    usual after resume_posting(); held Connections publishes need a person's
    approval. ``posting_stopped`` in the result is
    read back, so a failed write shows as false.
    """
    _write_setting(POSTING_STOP_KEY, "true")
    drained = drain_pending_outreach_tasks()
    outreach_rows = cancel_outreach_task_rows()
    publishes = hold_connection_publishes()
    return {
        "posting_stopped": posting_stopped(),
        "purged": drained["purged"],
        "revoked": drained["revoked"],
        "cancelled_tasks": outreach_rows["cancelled_tasks"],
        "revoked_tasks": outreach_rows["revoked_tasks"],
        "held_publishes": publishes["held_publishes"],
        "in_flight_publishes": publishes["in_flight_publishes"],
        "cancelled_publish_tasks": publishes["cancelled_publish_tasks"],
        "revoked_publish_tasks": publishes["revoked_publish_tasks"],
        "errors": drained["errors"] + outreach_rows["errors"] + publishes["errors"],
    }


def resume_posting() -> dict:
    """Lift the stop. Nothing held is sent by this call: see stop_all_posting."""
    _write_setting(POSTING_STOP_KEY, "false")
    return {"posting_stopped": posting_stopped()}


def cadence_status() -> dict:
    """Snapshot for /status endpoint: every platform something here posts to."""
    from backend.services.social_outreach.platforms import POLICIES, REFUSED

    r = _get_redis()
    out = {}
    platforms = sorted(name for name, policy in POLICIES.items() if policy.mode != REFUSED)
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
