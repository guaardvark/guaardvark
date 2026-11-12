"""Posts of the account's own that carry an infographic.

A pass picks, for each community, an infographic the person marked as a
keeper whose text was read back, and leaves a candidate row for the content
agent to write the words for. Nothing here posts; the draft waits for the
person like any other.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from backend.services.social_outreach import audit, infographics, kill_switch
from backend.utils.settings_utils import web_access_block_reason

logger = logging.getLogger(__name__)

ACTION = "post"
DEFAULT_POSTS_PER_PASS = 2

# Days before the account posts in the same community again. One infographic
# a week keeps a member's own posts occasional among the group's; measured
# against nothing, so it is the person's to change.
DAYS_BETWEEN_POSTS = 7

# A row in one of these states is a post made or still on its way.
_LIVE_STATUSES = ("candidate", "drafted", "approved", "processing", "submitting", "posted")

REDDIT_BASE = "https://www.reddit.com"


def community_url(platform: str, community: str) -> str:
    if platform == "reddit":
        return f"{REDDIT_BASE}/r/{community}"
    return f"https://www.facebook.com/groups/{community}/"


def _live_rows(platform: str) -> list:
    from backend.models import SocialOutreachLog

    from sqlalchemy import and_, or_

    return (
        SocialOutreachLog.query
        .filter(SocialOutreachLog.platform == platform)
        .filter(SocialOutreachLog.action == ACTION)
        .filter(or_(
            SocialOutreachLog.status.in_(_LIVE_STATUSES),
            # A submit the page did not confirm may be live, or held for an admin.
            and_(
                SocialOutreachLog.status == "aborted",
                SocialOutreachLog.abort_reason.like("%submitted_unconfirmed%"),
            ),
        ))
        .all()
    )


def _document_id(row) -> Optional[int]:
    media = infographics.media_of(row)
    try:
        return int(media["document_id"]) if media else None
    except (KeyError, TypeError, ValueError):
        return None


# (platform, action) pairs whose poster can attach an image, and whether the
# post may also go out with none.
_IMAGE_POSTERS = {
    ("facebook", "comment"): True,
    ("facebook", ACTION): True,
    ("reddit", ACTION): False,
}


def takes_image(platform: str, action: str) -> bool:
    return (platform, action) in _IMAGE_POSTERS


def posts_without_image(platform: str, action: str) -> bool:
    return _IMAGE_POSTERS.get((platform, action), True)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower())}


def best_for(text: str, exclude: Optional[int] = None) -> Optional[dict]:
    """The usable infographic whose own text shares the most words with
    ``text`` (the post and the reply), leaving out ``exclude``; the newest
    when none shares any."""
    usable = [
        item for item in infographics.items(keepers_only=True)
        if item.get("usable") and int(item["document_id"]) != exclude
    ]
    if not usable:
        return None
    wanted = _words(text)

    def shared(item: dict) -> int:
        said = " ".join([item.get("headline") or ""] + list(item.get("lines") or []))
        return len(wanted & _words(said))

    return max(usable, key=shared)


def choose(usable: list[dict], used_here: set[int], uses: dict[int, int]) -> Optional[dict]:
    """The infographic to post in a community: one never posted there, the
    least used anywhere first, the newest among equals."""
    fresh = [item for item in usable if int(item["document_id"]) not in used_here]
    if not fresh:
        return None
    return min(fresh, key=lambda item: uses.get(int(item["document_id"]), 0))


def plan(
    platform: str,
    communities: list[str],
    max_posts: int = DEFAULT_POSTS_PER_PASS,
) -> dict:
    """Leave a candidate post, with its infographic, for up to ``max_posts`` of
    ``communities`` (Facebook group refs, or subreddit names).

    A community is passed over when the account posted there within
    ``DAYS_BETWEEN_POSTS``, when its rules cannot be read or ban promotion,
    or when every usable infographic was already posted there.
    """
    report = {
        "platform": platform,
        "kind": "own_posts",
        "communities": [],
        "candidates": 0,
        "skipped_recent": 0,
        "skipped_rules": 0,
        "skipped_no_image": 0,
        "reason": None,
    }
    if not kill_switch.is_enabled():
        report["reason"] = "kill_switch_off"
        return report
    backlog = kill_switch.review_backlog_reason(platform)
    if backlog:
        report["reason"] = backlog
        return report
    if web_access_block_reason("plan outreach posts"):
        report["reason"] = "web_access_off"
        return report
    if not communities:
        report["reason"] = "no_groups" if platform == "facebook" else "no_communities"
        return report
    usable = [item for item in infographics.items(keepers_only=True) if item.get("usable")]
    if not usable:
        report["reason"] = "no_usable_infographics"
        return report

    rows = _live_rows(platform)
    uses: dict[int, int] = {}
    for row in rows:
        document_id = _document_id(row)
        if document_id is not None:
            uses[document_id] = uses.get(document_id, 0) + 1
    # Rows are stamped in local time; the week runs from when the post was drafted.
    cutoff = datetime.now() - timedelta(days=DAYS_BETWEEN_POSTS)

    def consider(community: str, rules_text: Optional[str], name: str) -> None:
        from backend.services.social_outreach.reddit_outreach import is_self_promo_banned

        entry = {"community": community, "name": name, "candidates": 0, "reason": None}
        report["communities"].append(entry)
        url = community_url(platform, community)
        here = [row for row in rows if (row.target_url or "").rstrip("/") == url.rstrip("/")]
        if any(row.created_at and row.created_at >= cutoff for row in here):
            entry["reason"] = "posted_recently"
            report["skipped_recent"] += 1
            return
        if rules_text is None:
            entry["reason"] = "rules_unreadable"
            report["skipped_rules"] += 1
            return
        ban_match = is_self_promo_banned(rules_text)
        if ban_match:
            entry["reason"] = f"bans_self_promo:{ban_match}"
            report["skipped_rules"] += 1
            return
        used_here = {d for d in (_document_id(row) for row in here) if d is not None}
        item = choose(usable, used_here, uses)
        if item is None:
            entry["reason"] = "no_unused_infographic"
            report["skipped_no_image"] += 1
            return
        document_id = int(item["document_id"])
        try:
            media = infographics.media_for(document_id)
        except Exception as e:  # noqa: BLE001 - a picture that cannot be attached is not planned
            logger.warning("own posts: infographic %s cannot be attached: %s", document_id, e)
            entry["reason"] = "image_unavailable"
            report["skipped_no_image"] += 1
            return
        said = [item.get("headline") or ""] + list(item.get("lines") or [])
        described = "Infographic: " + " / ".join(line for line in said if line)
        audit_id = audit.log_candidate(
            platform=platform,
            action=ACTION,
            target_url=url,
            target_thread_id=f"post:{community}:{document_id}",
            feature_hint="",
            extras={
                "title": "",
                "community": name or community,
                "group" if platform == "facebook" else "subreddit": community,
                "infographic": {
                    "document_id": document_id,
                    "headline": item.get("headline") or "",
                    "lines": list(item.get("lines") or []),
                },
                "selftext_preview": described,
                "source_text": described,
            },
        )
        if not audit_id:
            entry["reason"] = "not_recorded"
            return
        if not audit.set_media(audit_id, media):
            # Without its picture the row must not go on to be drafted.
            audit.mark_rejected(audit_id, "image_not_recorded")
            entry["reason"] = "not_recorded"
            return
        uses[document_id] = uses.get(document_id, 0) + 1
        entry.update(candidates=1, infographic=item.get("headline") or "", audit_id=audit_id)
        report["candidates"] += 1

    if platform == "reddit":
        from backend.services.social_outreach.reddit_outreach import fetch_subreddit_rules, reddit_reading

        with reddit_reading():
            for sub in communities:
                if report["candidates"] >= max_posts:
                    break
                rules = fetch_subreddit_rules(sub)
                consider(sub, None if rules is None else "\n".join(rules), f"r/{sub}")
        return report

    from backend.services.social_outreach import facebook_outreach as fb
    from backend.services.social_outreach.reddit_outreach import bidi_reachable
    from backend.utils.agent_browser_control import agent_browser_control

    with agent_browser_control() as refusal:
        if refusal:
            report["reason"] = "browser_in_use"
            report["detail"] = str(refusal)
            return report
        ok, why = bidi_reachable()
        if not ok:
            report["reason"] = "browser_unreadable"
            report["detail"] = why
            return report
        for group in communities:
            if report["candidates"] >= max_posts:
                break
            ref = fb.group_ref(group)
            if not ref:
                continue
            rules = fb.read_group_rules(ref)
            name, _why = fb._read(
                "JSON.stringify({name: ((document.querySelector('[role=\"main\"] h1') || {}).innerText || '').trim()})"
            )
            consider(ref, rules, str((name or {}).get("name") or ""))
    return report

