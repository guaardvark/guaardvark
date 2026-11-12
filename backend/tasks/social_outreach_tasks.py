"""
Celery tasks for the social outreach loop.

The loops scout threads and draft replies and link posts; every draft waits
for a person's approval, and nothing here posts on a schedule.
post_watched_draft posts one approved Reddit, YouTube or Facebook draft when a
person starts it with the Agent Screen open; the Discord bot sends approved
Discord drafts itself.

Each task wraps the loop in a Flask app context — celery workers don't have one
by default, and the SQLAlchemy/audit code needs it.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

logger = logging.getLogger(__name__)


def _skip_if_kill_switch_off() -> dict | None:
    """Early exit before Flask bootstrap — beat ticks must not load the app when disabled."""
    from backend.services.social_outreach.kill_switch import is_enabled

    if not is_enabled():
        return {"skipped": True, "reason": "kill_switch_off"}
    return None


def _with_app_context(fn, *args, **kwargs):
    """Run fn inside the Flask app context so DB/Setting/audit calls work.

    The old version of this function had a bare except that swallowed import
    errors and called fn() WITHOUT a context — every DB call inside fn would
    then crash with "Working outside of application context" and the audit
    record would be silently lost. We'd rather fail loud and visibly: if we
    cannot acquire a Flask app context, the task should error so it's caught
    at Celery beat time, not silently degrade.
    """
    from backend.app import app
    with app.app_context():
        return fn(*args, **kwargs)


@shared_task(name="social_outreach.read_back_infographic", bind=True)
def read_back_infographic(self, document_id: int) -> dict:
    """Read the text on one infographic back with a vision model and store
    the verdict on its Library document. Posts nothing, so the kill switch
    does not gate it."""
    from backend.services.social_outreach import infographics
    return _with_app_context(infographics.readback, document_id)


@shared_task(name="social_outreach.engage_with_subreddit", bind=True)
def engage_with_subreddit(self, subreddit: str, task_id: Any = None) -> dict:
    skipped = _skip_if_kill_switch_off()
    if skipped:
        return skipped
    from backend.services.social_outreach.reddit_outreach import RedditOutreachLoop
    return _with_app_context(RedditOutreachLoop().run_one_pass, subreddit, task_id=task_id)


@shared_task(name="social_outreach.self_share", bind=True)
def self_share(self, subreddit: str, link_url: str, task_id: Any = None) -> dict:
    skipped = _skip_if_kill_switch_off()
    if skipped:
        return skipped
    from backend.services.social_outreach.self_share import SelfShareLoop
    return _with_app_context(SelfShareLoop().run_one_pass, subreddit, link_url, task_id=task_id)


@shared_task(name="social_outreach.discord_pass", bind=True)
def discord_pass(self, channel_ids: list = None) -> dict:
    """No-op for now — the Discord cog polls itself. This exists so the unified
    scheduler can in principle trigger an on-demand pass; wire it up later if
    we move away from the in-cog timer."""
    return {"status": "noop", "reason": "discord cog polls itself"}


# --- Beat-driven orchestrators -------------------------------------------
# These tick tasks read the targets JSON, round-robin through the configured
# subs, and fire off one outreach pass per tick. Beat schedule entries in
# celery_app.py drive the cadence (default: reddit every 45 min, share every
# 4 h). Round-robin index is kept in Redis.

import json
import os
from pathlib import Path


_REPO_ROOT = Path(os.environ.get("GUAARDVARK_ROOT") or Path(__file__).resolve().parents[2])
_TARGETS_FILE = _REPO_ROOT / "data" / "agent" / "social_outreach_targets.json"


def _load_targets() -> dict:
    try:
        return json.loads(_TARGETS_FILE.read_text())
    except Exception as e:
        logger.warning("targets file unreadable (%s): %s", _TARGETS_FILE, e)
        return {}


def _next_target(category: str, items: list[str]) -> str | None:
    if not items:
        return None
    try:
        import redis
        r = redis.Redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
            socket_timeout=2,
        )
        idx = r.incr(f"social_outreach:rr:{category}")
        return items[(idx - 1) % len(items)]
    except Exception:
        # Redis unavailable — just use the first one. Better than nothing.
        return items[0]


@shared_task(name="social_outreach.tick_reddit_outreach", bind=True)
def tick_reddit_outreach(self) -> dict:
    """Beat tick — pick the next outreach sub from targets.json and run a pass."""
    skipped = _skip_if_kill_switch_off()
    if skipped:
        return skipped
    targets = _load_targets()
    subs = (targets.get("reddit") or {}).get("outreach_subs") or []
    sub = _next_target("reddit_outreach", subs)
    if not sub:
        return {"skipped": True, "reason": "no_targets"}
    from backend.services.social_outreach.reddit_outreach import RedditOutreachLoop
    return _with_app_context(RedditOutreachLoop().run_one_pass, sub)


@shared_task(name="social_outreach.tick_draft_candidates", bind=True)
def tick_draft_candidates(self) -> dict:
    """Beat tick — Content agent drafts the oldest N candidate rows.

    Reads status="candidate" rows produced by Recon, runs each through
    persona.draft_outreach_text, transitions to "drafted" (if grade ≥ 0.7
    and draft non-empty) or "rejected" (otherwise). No servo, no posting.
    Disabled by default in beat schedule — fire on demand via /run-pass
    or by enabling the schedule entry.
    """
    skipped = _skip_if_kill_switch_off()
    if skipped:
        return skipped
    from backend.services.social_outreach.content_agent import (
        ContentAgent,
        DEFAULT_BATCH_SIZE,
    )
    return _with_app_context(ContentAgent().draft_batch, DEFAULT_BATCH_SIZE)


@shared_task(name="social_outreach.tick_recon_reddit", bind=True)
def tick_recon_reddit(self) -> dict:
    """Beat tick — Recon agent scouts the next outreach sub for candidates.

    Read-only: hits Reddit's public API, writes status="candidate" rows. Never
    drafts, never posts. Safe to run on cron without supervised gates because
    no servo path is involved. Disabled by default in celery_app.py beat
    schedule — flip the schedule entry to enable.
    """
    skipped = _skip_if_kill_switch_off()
    if skipped:
        return skipped
    targets = _load_targets()
    subs = (targets.get("reddit") or {}).get("outreach_subs") or []
    sub = _next_target("reddit_recon", subs)
    if not sub:
        return {"skipped": True, "reason": "no_targets"}
    from backend.services.social_outreach.recon import RecondAgent
    return _with_app_context(RecondAgent().scout_reddit, sub)


@shared_task(name="social_outreach.tick_recon_youtube", bind=True)
def tick_recon_youtube(self) -> dict:
    """Beat tick — Recon agent scouts YouTube via web_search for candidates.

    Read-only: pulls a web search result page filtered to site:youtube.com, writes
    status="candidate" rows for video URLs. Never drafts, never posts. Safe
    to run on cron — same kill-switch gate as the reddit recon. Disabled by
    default in celery_app.py beat schedule.

    Round-robins through `youtube.keyword_profiles` in social_outreach_targets.json
    so successive ticks scan different angles of the keyword space rather
    than repeatedly hitting the same query.
    """
    skipped = _skip_if_kill_switch_off()
    if skipped:
        return skipped
    targets = _load_targets()
    profiles = (targets.get("youtube") or {}).get("keyword_profiles") or []
    profile = _next_target("youtube_recon", profiles)
    if not profile:
        return {"skipped": True, "reason": "no_targets"}
    from backend.services.social_outreach.recon import RecondAgent
    return _with_app_context(RecondAgent().scout_youtube, profile)


@shared_task(name="social_outreach.tick_recon_youtube_replies", bind=True)
def tick_recon_youtube_replies(self) -> dict:
    """Beat tick — scan the channel's own YouTube videos for new replies to
    the account's comments. Emits status="candidate" rows with action="reply"
    that the Content agent drafts a response for and the grader scores; once
    approved, a person posts one with post_watched_draft
    (post_youtube_reply_via_servo).

    Read-only: no servo, no posting. Same kill-switch gate as other recon
    ticks. NOT scheduled in celery_app beat until
    recon._fetch_recent_replies_to_own_comments is implemented (stub returns []).
    Fire on demand via /run-pass or hand-seed via
    recon.enqueue_youtube_reply_candidate(...).

    Reads `youtube.monitored_videos` from social_outreach_targets.json.
    """
    skipped = _skip_if_kill_switch_off()
    if skipped:
        return skipped
    from backend.services.social_outreach.recon import RecondAgent
    return _with_app_context(RecondAgent().scout_youtube_my_video_replies)


@shared_task(name="social_outreach.tick_self_share", bind=True)
def tick_self_share(self) -> dict:
    """Beat tick — pick next share sub + URL, draft a supervised link post.

    Rotates `reddit.share_urls` via Redis round-robin so successive ticks
    market different assets. Falls back to `reddit.default_share_url`, then
    the profile's site; with none of those the tick is skipped.
    """
    skipped = _skip_if_kill_switch_off()
    if skipped:
        return skipped
    targets = _load_targets()
    reddit = targets.get("reddit") or {}
    subs = reddit.get("share_subs") or []
    sub = _next_target("reddit_share", subs)
    if not sub:
        return {"skipped": True, "reason": "no_targets"}
    from backend.services.social_outreach import profile
    share_urls = [
        u.strip() for u in (reddit.get("share_urls") or [])
        if isinstance(u, str) and u.strip()
    ]
    if share_urls:
        link_url = _next_target("reddit_share_url", share_urls) or share_urls[0]
    else:
        link_url = (reddit.get("default_share_url") or "").strip() or profile.site_url()
    if not link_url:
        return {"skipped": True, "reason": profile.no_link_reason()}
    from backend.services.social_outreach.self_share import SelfShareLoop
    return _with_app_context(SelfShareLoop().run_one_pass, sub, link_url)


@shared_task(name="social_outreach.tick_process_approved_drafts", bind=True)
def tick_process_approved_drafts(self) -> dict:
    """Beat tick — close approved drafts that nothing here will post. Posts nothing.

    Reddit, YouTube and Facebook drafts are posted one at a time, each started
    by a person watching the Agent Screen (post_watched_draft); Discord drafts
    are sent by the Discord bot. An approved draft for a platform nothing here
    posts to (platforms.REFUSED, X and LinkedIn) or for an action no poster
    handles on its platform moves to ``unsupported`` with the reason, rather
    than waiting forever. Other approved rows are left as they are.
    """
    skipped = _skip_if_kill_switch_off()
    if skipped:
        return {"unsupported": 0, "reason": "kill_switch_off"}

    def _run():
        from sqlalchemy import func

        from backend.models import SocialOutreachLog
        from backend.services.social_outreach import platforms, transitions

        watched = platforms.platforms_in(platforms.WATCHED)
        rows = (
            SocialOutreachLog.query
            .filter(SocialOutreachLog.status == "approved")
            .filter(func.lower(SocialOutreachLog.platform).in_(
                watched + platforms.platforms_in(platforms.REFUSED)))
            .order_by(SocialOutreachLog.created_at.asc())
            .limit(200)
            .all()
        )
        unsupported = waiting = 0
        for row in rows:
            platform = (row.platform or "").strip().lower()
            action = (row.action or "").strip().lower()
            reason = platforms.refusal_for(platform)
            if not reason and not platforms.posts(platform, action):
                reason = f"unsupported: no poster for action '{action}' on platform '{platform}'"
            if not reason:
                waiting += 1
                continue
            if transitions.mark_unsupported(row.id, reason):
                unsupported += 1
                logger.info("process-approved: row %s unsupported (%s on %s)", row.id, action, platform)
        return {"unsupported": unsupported, "waiting_for_watched_post": waiting}

    return _with_app_context(_run)


@shared_task(name="social_outreach.post_watched_draft", bind=True)
def post_watched_draft(self, event_id: int, clicked_at: float) -> dict:
    """One browser post a person started with "Post now, I'm watching"
    (watched_post.start). Posts that draft only, then frees the running slot."""
    from backend.services.social_outreach import watched_post

    try:
        return _with_app_context(watched_post.run, event_id, clicked_at)
    finally:
        watched_post.release_slot(event_id)


# How long a row may sit at status=processing or submitting before we abort it
# (worker kill).
_STUCK_PROCESSING_SECONDS = 30 * 60


@shared_task(name="social_outreach.tick_reap_stuck_processing", bind=True)
def tick_reap_stuck_processing(self) -> dict:
    """Abort outreach rows stuck in 'processing' or 'submitting' after a worker crash."""
    def _run():
        from datetime import datetime, timedelta, timezone
        from backend.models import SocialOutreachLog
        from backend.services.social_outreach import transitions

        now = datetime.now(timezone.utc)
        rows = (
            SocialOutreachLog.query
            .filter(SocialOutreachLog.status.in_(tuple(transitions.ABORT_FROM)))
            .limit(50)
            .all()
        )
        stuck = []
        for row in rows:
            claimed_at = transitions.in_flight_since(row.abort_reason)
            # Fallback: age by created_at if claim stamp missing (legacy rows).
            if claimed_at is None:
                created = row.created_at
                if created is None:
                    continue
                if created.tzinfo is None:
                    claimed_at = created.replace(tzinfo=timezone.utc)
                else:
                    claimed_at = created
            if now - claimed_at < timedelta(seconds=_STUCK_PROCESSING_SECONDS):
                continue
            stuck.append((row.id, row.status))
        reaped = 0
        for row_id, status in stuck:
            # A row that died while submitting may have been published; the
            # reason says so, so nobody re-drafts the same post blind.
            reason = (
                "stuck_submitting_reaped: the post may have gone out, check the target"
                if status == "submitting" else "stuck_processing_reaped"
            )
            if transitions.abort_in_flight(row_id, reason):
                reaped += 1
        return {"reaped": reaped}

    return _with_app_context(_run)
