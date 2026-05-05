"""
Celery tasks for the social outreach loop.

Three task types:
  social_outreach_reddit  — discover + draft + (maybe) post on a subreddit
  social_outreach_share   — submit a link-post to a subreddit
  social_outreach_discord — celery-driven discord pass (rarely used; the cog
                            polls itself, this is here so the unified scheduler
                            can trigger an on-demand pass)

Each task wraps the loop in a Flask app context — celery workers don't have one
by default, and the SQLAlchemy/audit code needs it.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

logger = logging.getLogger(__name__)


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


@shared_task(name="social_outreach.engage_with_subreddit", bind=True)
def engage_with_subreddit(self, subreddit: str, task_id: Any = None) -> dict:
    from backend.services.social_outreach.reddit_outreach import RedditOutreachLoop
    return _with_app_context(RedditOutreachLoop().run_one_pass, subreddit, task_id=task_id)


@shared_task(name="social_outreach.self_share", bind=True)
def self_share(self, subreddit: str, link_url: str, task_id: Any = None) -> dict:
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


_TARGETS_FILE = (
    Path(os.environ.get("GUAARDVARK_ROOT", "/home/llamax1/LLAMAX8"))
    / "data"
    / "agent"
    / "social_outreach_targets.json"
)


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
    targets = _load_targets()
    subs = (targets.get("reddit") or {}).get("outreach_subs") or []
    sub = _next_target("reddit_outreach", subs)
    if not sub:
        return {"skipped": True, "reason": "no_targets"}
    from backend.services.social_outreach.reddit_outreach import RedditOutreachLoop
    return _with_app_context(RedditOutreachLoop().run_one_pass, sub)


@shared_task(name="social_outreach.tick_self_share", bind=True)
def tick_self_share(self) -> dict:
    """Beat tick — pick next share sub + URL, submit a link post."""
    targets = _load_targets()
    subs = (targets.get("reddit") or {}).get("share_subs") or []
    sub = _next_target("reddit_share", subs)
    if not sub:
        return {"skipped": True, "reason": "no_targets"}
    # Default link URL — guaardvark.com. Could be parameterized later.
    from backend.services.social_outreach.persona import SITE_URL
    link_url = (targets.get("reddit") or {}).get("default_share_url") or SITE_URL
    from backend.services.social_outreach.self_share import SelfShareLoop
    return _with_app_context(SelfShareLoop().run_one_pass, sub, link_url)


@shared_task(name="social_outreach.tick_process_approved_drafts", bind=True)
def tick_process_approved_drafts(self) -> dict:
    """Beat tick — process UI-approved drafts for Reddit."""
    def _run():
        from backend.models import SocialOutreachLog, db
        from backend.services.social_outreach.reddit_outreach import post_comment_via_servo, record_post_via_backend
        from backend.services.social_outreach.self_share import _submit_post_via_servo
        import json
        import requests
        from backend.services.social_outreach.reddit_outreach import backend_url
        from backend.services.social_outreach.reddit_outreach import REDDIT_BASE

        rows = (
            SocialOutreachLog.query
            .filter(SocialOutreachLog.status == "approved")
            .filter(SocialOutreachLog.platform == "reddit")
            .order_by(SocialOutreachLog.created_at.asc())
            .limit(5)
            .all()
        )
        
        if not rows:
            return {"processed": 0, "reason": "no_approved_drafts"}
            
        processed = 0
        for row in rows:
            # Claim the row up-front so a mid-flight failure (servo crash,
            # record-post HTTP blip) doesn't leave it as "approved" and trigger
            # a double-post on the next 60s tick. If the post never happens,
            # the row stays at "processing" and a human deals with it.
            row.status = "processing"
            db.session.commit()

            if row.action == "comment":
                success, reason = post_comment_via_servo(row.target_url, row.draft_text)
                if success:
                    record_post_via_backend(row.id, row.target_url, row.target_thread_id, row.draft_text, row.task_id)
                    processed += 1
            elif row.action == "share":
                from backend.services.social_outreach.persona import SITE_URL
                payload = {}
                try:
                    payload = json.loads(row.draft_text or "{}")
                    title = (payload.get("title") or "").strip()
                except json.JSONDecodeError:
                    title = (row.draft_text or "").strip()

                # Extract subreddit from target_url (e.g. https://old.reddit.com/r/SideProject)
                import re
                # `or ""` so a row with a NULL target_url doesn't TypeError out
                # of the whole batch — re.search on "" just fails to match.
                m = re.search(r"/r/([^/]+)", row.target_url or "")
                subreddit = m.group(1) if m else ""

                # Read link_url from the draft payload — falls back to SITE_URL
                # only for legacy rows drafted before we started storing it.
                link_url = (payload.get("link_url") or "").strip() or SITE_URL
                
                if subreddit and title:
                    success, reason = _submit_post_via_servo(subreddit, title, link_url)
                    if success:
                        try:
                            requests.post(
                                f"{backend_url()}/social-outreach/record-post",
                                json={
                                    "audit_id": row.id,
                                    "platform": "reddit",
                                    "posted_text": f"{title}\n{link_url}",
                                    "target_url": row.target_url,
                                    "target_thread_id": None,
                                    "task_id": row.task_id,
                                },
                                timeout=10,
                            )
                        except Exception as e:
                            logger.warning("record-post failed: %s", e)
                        processed += 1
        return {"processed": processed}
    return _with_app_context(_run)
