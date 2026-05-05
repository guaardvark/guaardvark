"""
Reddit outreach loop — finds relevant threads, drafts a comment via the LLM,
and posts it through the servo-driven Firefox on DISPLAY=:99 (which has the
user's logged-in Reddit session cookies).

Hybrid architecture:
  • READ via Reddit's public JSON API (no auth needed for discovery — cleaner
    than screenshot-extracting rules + comments).
  • WRITE via agent_control_service.execute_task on the real Firefox profile
    (the only path with the user's login cookies).

If write fails twice we abort the whole pass — better to skip than thrash.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import requests

from backend.services.social_outreach import audit, kill_switch, persona

logger = logging.getLogger(__name__)


REDDIT_USER_AGENT = "guaardvark-outreach/0.1 (by /u/guaardvark) - local AI workstation"
REDDIT_BASE = "https://www.reddit.com"
HTTP_TIMEOUT = 10
SUBREDDIT_HOT_LIMIT = 10
THREAD_COMMENT_LIMIT = 10
MAX_THREADS_PER_PASS = 2
SERVO_SETTLE_SECONDS = 4


@dataclass
class RedditThread:
    id: str
    url: str
    permalink: str
    subreddit: str
    title: str
    selftext: str
    score: int
    num_comments: int
    created_utc: float


def _http_get_json(url: str, retries: int = 1) -> Optional[dict]:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": REDDIT_USER_AGENT},
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code == 429:
            if retries > 0:
                retry_after = int(resp.headers.get("Retry-After", 5))
                logger.warning("reddit JSON 429 on %s — backing off for %ds", url, retry_after)
                time.sleep(retry_after)
                return _http_get_json(url, retries=retries - 1)
            logger.warning("reddit JSON 429 on %s — out of retries", url)
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("reddit JSON fetch failed for %s: %s", url, e)
        return None


def fetch_subreddit_rules(subreddit: str) -> list[str]:
    data = _http_get_json(f"{REDDIT_BASE}/r/{subreddit}/about/rules.json")
    if not data:
        return []
    rules = []
    for r in data.get("rules", []):
        title = (r.get("short_name") or "").strip()
        desc = (r.get("description") or "").strip()
        if title or desc:
            rules.append(f"{title}: {desc}".strip(": "))
    return rules


def is_self_promo_banned(rules_text: str) -> Optional[str]:
    """Returns the matched rule text if the sub bans self-promotion, else None."""
    for pattern in persona.NO_PROMO_RULE_PATTERNS:
        m = re.search(pattern, rules_text, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


def fetch_hot_threads(subreddit: str, limit: int = SUBREDDIT_HOT_LIMIT) -> list[RedditThread]:
    data = _http_get_json(f"{REDDIT_BASE}/r/{subreddit}/hot.json?limit={limit}")
    if not data:
        return []
    out = []
    for entry in data.get("data", {}).get("children", []):
        d = entry.get("data") or {}
        if d.get("stickied") or d.get("is_pinned"):
            continue
        if d.get("over_18"):
            continue
        out.append(RedditThread(
            id=d.get("id", ""),
            url=d.get("url", ""),
            permalink=f"{REDDIT_BASE}{d.get('permalink', '')}",
            subreddit=d.get("subreddit", subreddit),
            title=(d.get("title") or "").strip(),
            selftext=(d.get("selftext") or "").strip(),
            score=int(d.get("score") or 0),
            num_comments=int(d.get("num_comments") or 0),
            created_utc=float(d.get("created_utc") or 0),
        ))
    return out


def fetch_thread_comments(permalink: str, limit: int = THREAD_COMMENT_LIMIT) -> list[str]:
    """Returns flat list of comment bodies in display order, top-level first."""
    if not permalink.endswith("/"):
        permalink = permalink + "/"
    data = _http_get_json(f"{permalink.rstrip('/')}.json?limit={limit}&depth=1")
    if not isinstance(data, list) or len(data) < 2:
        return []
    out = []
    for entry in data[1].get("data", {}).get("children", []):
        kind = entry.get("kind")
        if kind != "t1":
            continue
        body = (entry.get("data") or {}).get("body", "").strip()
        if body and body != "[deleted]" and body != "[removed]":
            out.append(body)
        if len(out) >= limit:
            break
    return out


def thread_is_relevant(thread: RedditThread, comments: list[str]) -> Optional[str]:
    """Returns the matched feature key if any local-AI keyword matches, else None."""
    haystack = "\n".join([thread.title, thread.selftext, *comments[:5]])
    return persona.find_relevant_feature(haystack)


def backend_url() -> str:
    """Resolve the local Flask API base URL. Public (no underscore) because
    self_share imports it and we don't want cross-module private-name coupling
    to silently break on rename."""
    import os
    port = os.environ.get("FLASK_PORT", "5002")
    return f"http://localhost:{port}/api"


# Backwards-compat alias so any in-flight callers still work.
_backend_url = backend_url


def draft_via_backend(thread: RedditThread, comments: list[str], feature_hint: Optional[str], task_id: Optional[int]) -> Optional[dict]:
    """Calls the social-outreach draft endpoint synchronously."""
    thread_context = (
        f"TITLE: {thread.title}\n\n"
        f"OP BODY:\n{thread.selftext or '(link-only post)'}\n\n"
        f"TOP COMMENTS:\n" + "\n---\n".join(c[:600] for c in comments[:5])
    )
    try:
        resp = requests.post(
            f"{backend_url()}/social-outreach/draft-comment",
            json={
                "platform": "reddit",
                "thread_context": thread_context,
                "target_url": thread.permalink,
                "target_thread_id": thread.id,
                "feature_hint": feature_hint,
                "task_id": task_id,
                "mode": "comment",
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("draft request failed for thread %s: %s", thread.id, e)
        return None


def record_post_via_backend(audit_id: Optional[int], permalink: str, thread_id: str, posted_text: str, task_id: Optional[int]) -> None:
    try:
        requests.post(
            f"{backend_url()}/social-outreach/record-post",
            json={
                "audit_id": audit_id,
                "platform": "reddit",
                "posted_text": posted_text,
                "target_url": permalink,
                "target_thread_id": thread_id,
                "task_id": task_id,
            },
            timeout=10,
        )
    except Exception as e:
        logger.warning("record-post call failed: %s", e)


def post_comment_via_servo(permalink: str, comment_text: str) -> tuple[bool, str]:
    """
    Drive Firefox on DISPLAY=:99 to land the comment.
    Returns (success, reason).

    Strategy: navigate to the thread on old.reddit.com (cleaner DOM, comment
    textarea is high on the page and reachable in 1 click), then hand the
    see-think-act loop a precise instruction. The agent's vision model finds
    the comment box, clicks, types, and submits.

    On failure: ServoController records success=False; we treat that as servo
    failure and abort.
    """
    from backend.services.agent_control_service import get_agent_control_service
    from backend.services.local_screen_backend import LocalScreenBackend

    # old.reddit.com is the easiest target — single textarea, "save" button right below it.
    old_url = permalink.replace("www.reddit.com", "old.reddit.com").replace("https://reddit.com", "https://old.reddit.com")

    service = get_agent_control_service()
    if service.is_active:
        return False, "agent_busy"

    # The agent display (Xvfb on :99) might not be running — happens during
    # CI, headless deploys, or right after a host reboot if start.sh hasn't
    # finished the display step yet. Without this guard, mss / xdotool throw
    # at construction time and the Celery task gets retried forever.
    try:
        screen = LocalScreenBackend()
    except Exception as e:
        logger.warning("display not available for outreach: %s", e)
        return False, "display_unavailable"

    # Step 1: navigate. Use the existing navigate_url recipe.
    nav_task = f"navigate to {old_url.replace('https://', '')}"
    nav_result = service.execute_task(nav_task, screen)
    if not nav_result.success:
        return False, f"navigate_failed: {nav_result.reason}"
    time.sleep(SERVO_SETTLE_SECONDS)

    # Step 2: find and click the comment box
    find_task = (
        "On the open Reddit thread, do these steps in order. "
        "1) Find the comment textarea below the post body. "
        "2) Click inside the textarea. "
        "3) Say done."
    )
    find_result = service.execute_task(find_task, screen)
    if not find_result.success:
        return False, f"find_comment_box_failed: {find_result.reason}"
        
    # Step 3: Type the text directly via python to preserve newlines and avoid prompt injection
    screen.type_text(comment_text)
    time.sleep(1)
    
    # Step 4: Click save
    save_task = "Find the button labeled save and click it."
    save_result = service.execute_task(save_task, screen)
    if not save_result.success:
        return False, f"click_save_failed: {save_result.reason}"

    return True, "ok"


class RedditOutreachLoop:
    """One pass = visit one subreddit, find up to MAX_THREADS_PER_PASS candidates, draft + maybe post."""

    def run_one_pass(self, subreddit: str, task_id: Optional[int] = None) -> dict:
        report = {
            "subreddit": subreddit,
            "drafted": 0,
            "posted": 0,
            "aborted": 0,
            "skipped": 0,
            "reason": None,
        }

        if not kill_switch.is_enabled():
            report["reason"] = "kill_switch_off"
            audit.log_outreach_event(
                platform="reddit", action="abort",
                target_url=f"{REDDIT_BASE}/r/{subreddit}",
                status="aborted", abort_reason="kill_switch_off",
                task_id=task_id,
            )
            return report

        rules_list = fetch_subreddit_rules(subreddit)
        rules_text = "\n".join(rules_list)
        ban_match = is_self_promo_banned(rules_text)
        if ban_match:
            report["reason"] = f"no_self_promo_rule:{ban_match}"
            audit.log_outreach_event(
                platform="reddit", action="abort",
                target_url=f"{REDDIT_BASE}/r/{subreddit}",
                status="aborted",
                abort_reason=f"sub bans self-promo: {ban_match}",
                task_id=task_id,
            )
            report["aborted"] += 1
            return report

        threads = fetch_hot_threads(subreddit)
        if not threads:
            report["reason"] = "no_hot_threads"
            return report

        recent_done = audit.recent_thread_ids("reddit", hours=168)

        servo_failures = 0
        for thread in threads:
            if report["posted"] + report["aborted"] >= MAX_THREADS_PER_PASS:
                break
            if thread.id in recent_done:
                report["skipped"] += 1
                continue

            comments = fetch_thread_comments(thread.permalink)
            feature_hint = thread_is_relevant(thread, comments)
            if feature_hint is None:
                report["skipped"] += 1
                continue

            draft_result = draft_via_backend(thread, comments, feature_hint, task_id)
            if not draft_result:
                report["skipped"] += 1
                continue
            report["drafted"] += 1

            if not draft_result.get("would_post"):
                # supervised, low grade, cadence block, or empty draft —
                # already logged in audit by /draft-comment endpoint
                continue

            draft_text = draft_result.get("draft", "").strip()
            audit_id = draft_result.get("audit_id")

            success, reason = post_comment_via_servo(thread.permalink, draft_text)
            if success:
                record_post_via_backend(audit_id, thread.permalink, thread.id, draft_text, task_id)
                report["posted"] += 1
                # Cadence enforced inside record-post; one post per pass is the cap anyway.
                break
            else:
                servo_failures += 1
                audit.log_outreach_event(
                    platform="reddit", action="abort",
                    target_url=thread.permalink,
                    target_thread_id=thread.id,
                    status="aborted",
                    abort_reason=f"servo: {reason}",
                    task_id=task_id,
                )
                report["aborted"] += 1
                if servo_failures >= kill_switch.SERVO_FAILURE_ABORT_THRESHOLD:
                    report["reason"] = "servo_threshold_hit"
                    break

        return report
