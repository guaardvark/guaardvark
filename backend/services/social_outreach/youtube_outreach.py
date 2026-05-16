"""
YouTube outreach — posts comments and replies on YouTube videos via the
servo-driven Firefox on DISPLAY=:99 (which has the user's logged-in
YouTube session cookies).

Both functions return (success, reason) for audit tracking. They mirror
reddit_outreach.post_comment_via_servo's contract.

Implementation note (2026-05-13 rewrite):
  Previous version handed multi-step natural-language instruction blobs to
  service.execute_task() and relied on the see-think-act loop to figure
  out the YouTube-specific dance. That had three documented failure modes:
    • brittle composer-find on a page that scrolls past the fold
    • vision-clicked submit button that misfired on the '0 Comments' header
    • autoplay drift to a related video mid-flow

  This version chains deterministic recipes instead. Each step is one
  execute_task call whose message hits exactly one recipe in
  data/agent/recipes.json — no see-think-act lottery. The recipes:

    navigate_url               (case-preserving — task_effective post-fix)
    pause_youtube_video        (Esc + k — prevents autoplay drift)
    find_on_page               (Ctrl+F — bypasses YouTube search-bar focus theft)
    press_escape
    focus_youtube_comment_field  / focus_youtube_reply_field
    type_comment_text          / type_reply_text   (but we type the body
                                                    directly via screen.type_text
                                                    so newlines and quotes survive)
    submit_youtube_comment     / submit_youtube_reply  (Ctrl+Enter)

  See memory: youtube-focus-and-comments, agent-recipe-design.
"""

from __future__ import annotations

import logging
import random
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

SERVO_SETTLE_SECONDS = 4

# Min length for a parent-comment anchor passed to find_on_page. Too short
# and find-on-page lands on something unrelated (e.g. matching "the" all
# over the page); too long and YouTube's HTML escaping breaks the match.
MIN_REPLY_ANCHOR_LEN = 12
MAX_REPLY_ANCHOR_LEN = 80


def _human_pause(min_s: float = 0.3, max_s: float = 2.0) -> None:
    """Random sleep to avoid deterministic bot timing fingerprints.

    Don't make this call site-specific — uniform jitter across all servo
    actions is fine. Cross-platform spam filters look for *constant* delays
    much more than for specific values.
    """
    time.sleep(random.uniform(min_s, max_s))


def _normalize_youtube_url(target_url: str) -> Optional[str]:
    """Coerce youtu.be / mobile / share URLs to canonical youtube.com/watch?v=.
    Returns None if the URL isn't a YouTube watch URL at all.
    """
    if "youtu.be/" in target_url:
        vid = target_url.split("youtu.be/")[-1].split("?")[0].split("&")[0]
        return f"https://www.youtube.com/watch?v={vid}"
    if "youtube.com" in target_url:
        return target_url
    return None


def _run_recipe_step(service, screen, chat_message: str, failure_tag: str) -> tuple[bool, str]:
    """Hand chat_message to execute_task. Expect a single recipe to match.

    Returns (True, "ok") on recipe success, (False, "{failure_tag}: {reason}")
    on failure. The caller decides whether failure is fatal or recoverable.
    """
    result = service.execute_task(chat_message, screen)
    if not result.success:
        # The auth interstitial shows up on the navigate step typically;
        # bubble it as a distinct reason so the caller can short-circuit.
        reason_lc = (result.reason or "").lower()
        if "sign" in reason_lc and ("in" in reason_lc or "-in" in reason_lc):
            return False, "auth_required"
        return False, f"{failure_tag}: {result.reason}"
    return True, "ok"


def _trim_anchor(parent_text: str) -> Optional[str]:
    """Pick a distinctive substring of the parent comment to feed find_on_page.

    Strategy: strip leading mentions/whitespace, take the first
    [MIN_REPLY_ANCHOR_LEN, MAX_REPLY_ANCHOR_LEN] chars that don't contain
    a double-quote (which would break the find recipe's quoted-arg regex).
    """
    if not parent_text:
        return None
    cleaned = re.sub(r'^\s*@\S+\s*', '', parent_text).strip()
    cleaned = cleaned.replace('"', '').replace("\n", " ")
    if len(cleaned) < MIN_REPLY_ANCHOR_LEN:
        return None
    return cleaned[:MAX_REPLY_ANCHOR_LEN].strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def post_youtube_comment_via_servo(
    target_url: str,
    comment_text: str,
    task_id: Optional[int] = None,
) -> tuple[bool, str]:
    """Navigate to target_url, post comment_text as a top-level comment.

    Returns (success, reason). Reasons on failure:
      - "invalid_url"        — URL isn't a YouTube watch URL
      - "agent_busy"         — agent service is already executing a task
      - "display_unavailable"— Xvfb on :99 isn't reachable
      - "auth_required"      — YouTube sign-in interstitial appeared
      - "navigate_failed: …"
      - "pause_failed: …"
      - "find_comments_failed: …"
      - "focus_composer_failed: …"
      - "submit_failed: …"
    """
    from backend.services.agent_control_service import get_agent_control_service
    from backend.services.local_screen_backend import LocalScreenBackend

    normalized = _normalize_youtube_url(target_url)
    if not normalized:
        return False, "invalid_url"
    target_url = normalized

    service = get_agent_control_service()
    if service.is_active:
        return False, "agent_busy"
    try:
        screen = LocalScreenBackend()
    except Exception as e:
        logger.warning("display not available for outreach: %s", e)
        return False, "display_unavailable"

    # Recipe chain. The chat-message strings here are deliberately phrased
    # to hit exactly one recipe in data/agent/recipes.json — keep them in
    # sync if the recipe triggers change.
    #
    # find_on_page "Comments" + press_escape was the original middle here, but
    # it was unreliable: "Comments" matches early in the description on many
    # videos so the viewport never scrolled. scroll_to_youtube_comments uses
    # plain PageDown taps and reliably brings the composer into view.
    nav_msg = f"navigate to {target_url.replace('https://', '').replace('http://', '')}"
    for chat_msg, tag, settle in (
        (nav_msg,                              "navigate_failed",        SERVO_SETTLE_SECONDS),
        ("pause the video",                    "pause_failed",           1.0),
        ("scroll to comments",                 "scroll_to_comments_failed", 1.0),
        ("click the add a comment field",      "focus_composer_failed",  SERVO_SETTLE_SECONDS),
    ):
        ok, reason = _run_recipe_step(service, screen, chat_msg, tag)
        if not ok:
            logger.warning(
                "youtube comment chain aborted at %s (task_id=%s): %s",
                tag, task_id, reason,
            )
            return False, reason
        time.sleep(settle)

    # Type body directly via the screen backend — keeps newlines, special
    # chars, and quote marks intact (the type_comment_text recipe would
    # work too, but its {1} substitution and trigger-regex argument parsing
    # mangles anything with embedded quotes).
    screen.type_text(comment_text)
    _human_pause()

    ok, reason = _run_recipe_step(service, screen, "send the comment", "submit_failed")
    if not ok:
        return False, reason

    return True, "ok"


def post_youtube_reply_via_servo(
    target_url: str,
    parent_comment_match_text: str,
    reply_text: str,
    task_id: Optional[int] = None,
) -> tuple[bool, str]:
    """Navigate to target_url, find the parent comment by text substring,
    open its Reply composer, post reply_text under it.

    The `parent_comment_match_text` is fed to the find_on_page recipe to
    position the viewport at the right comment. The focus_youtube_reply_field
    recipe then clicks the topmost visible "Reply" button — which should be
    the parent's, since we just scrolled to it.

    Returns (success, reason). Reasons on failure mirror the comment path,
    plus:
      - "anchor_too_short"  — parent_comment_match_text wasn't distinctive
                              enough to safely find-on-page
      - "find_parent_failed: …"
      - "focus_reply_failed: …"
    """
    from backend.services.agent_control_service import get_agent_control_service
    from backend.services.local_screen_backend import LocalScreenBackend

    normalized = _normalize_youtube_url(target_url)
    if not normalized:
        return False, "invalid_url"
    target_url = normalized

    anchor = _trim_anchor(parent_comment_match_text)
    if not anchor:
        return False, "anchor_too_short"

    service = get_agent_control_service()
    if service.is_active:
        return False, "agent_busy"
    try:
        screen = LocalScreenBackend()
    except Exception as e:
        logger.warning("display not available for outreach: %s", e)
        return False, "display_unavailable"

    nav_msg = f"navigate to {target_url.replace('https://', '').replace('http://', '')}"
    find_parent_msg = f'find "{anchor}" on the page'
    for chat_msg, tag, settle in (
        (nav_msg,                "navigate_failed",      SERVO_SETTLE_SECONDS),
        ("pause the video",      "pause_failed",         1.0),
        (find_parent_msg,        "find_parent_failed",   1.0),
        ("press escape",         "escape_failed",        0.4),
        ("click reply",          "focus_reply_failed",   SERVO_SETTLE_SECONDS),
    ):
        ok, reason = _run_recipe_step(service, screen, chat_msg, tag)
        if not ok:
            logger.warning(
                "youtube reply chain aborted at %s (task_id=%s, anchor=%r): %s",
                tag, task_id, anchor, reason,
            )
            return False, reason
        time.sleep(settle)

    screen.type_text(reply_text)
    _human_pause()

    # "send reply" (not "submit the reply") — Gemma4 quirk: when the composer
    # is already populated, "submit" sometimes resolves to "task done" text
    # instead of an action, bypassing recipe match. "send" reliably fires.
    # See memory: youtube-focus-and-comments § Gemma4 quirk.
    ok, reason = _run_recipe_step(service, screen, "send reply", "submit_failed")
    if not ok:
        return False, reason

    return True, "ok"
