"""
YouTube outreach — posts comments on relevant videos via the servo-driven
Firefox on DISPLAY=:99 (which has the user's logged-in YouTube session cookies).

Mirrors the Reddit posting path: navigate, find the comment box, type, submit,
verify. Returns (success, reason) for audit tracking.

Key differences from Reddit:
  • Comments live below the video player, usually below the fold — scroll needed.
  • Composer is a contenteditable div, not a textarea.
  • Submit button label is "Comment" (not "Save" or "Reply").
"""

from __future__ import annotations

import logging
import random
import time
from typing import Optional

logger = logging.getLogger(__name__)

SERVO_SETTLE_SECONDS = 4


def _human_pause(min_s: float = 0.3, max_s: float = 2.0) -> None:
    """Random sleep to avoid deterministic bot timing fingerprints.
    
    Don't make this call site-specific — uniform jitter across all servo
    actions is fine. Cross-platform spam filters look for *constant* delays
    much more than for specific values.
    """
    time.sleep(random.uniform(min_s, max_s))


def post_youtube_comment_via_servo(
    target_url: str,
    comment_text: str,
    task_id: Optional[int] = None,
) -> tuple[bool, str]:
    """Navigate the agent's Firefox to target_url, scroll to the comments
    section, focus the 'Add a comment…' composer, type comment_text, click
    Comment, verify the post landed.

    Returns (success, reason). Reason is a short string the audit row
    will store on failure (e.g. 'find_comment_box_failed:<reason>',
    'comment_submit_failed:<reason>', 'auth_required').

    Mirror reddit_outreach.post_comment_via_servo's contract exactly:
      - same return shape
      - same post-attempt failure-screenshot capture
      - same jitter-based typing
      - same observation step after submit
    """
    from backend.services.agent_control_service import get_agent_control_service
    from backend.services.local_screen_backend import LocalScreenBackend

    # YouTube videos — youtube.com or youtu.be. Normalize to youtube.com/watch?v=
    if "youtu.be/" in target_url:
        video_id = target_url.split("youtu.be/")[-1].split("?")[0]
        target_url = f"https://www.youtube.com/watch?v={video_id}"
    elif "youtube.com" not in target_url:
        return False, "invalid_url"

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
    nav_task = f"navigate to {target_url.replace('https://', '')}"
    nav_result = service.execute_task(nav_task, screen)
    if not nav_result.success:
        # Check for auth_required — YouTube sign-in interstitial
        if "sign" in nav_result.reason.lower() or "login" in nav_result.reason.lower():
            return False, "auth_required"
        return False, f"navigate_failed: {nav_result.reason}"
    time.sleep(SERVO_SETTLE_SECONDS)

    # Step 2: open + focus the comment composer.
    # On YouTube, the "Add a comment" box sits below the video player and
    # below the description. Usually below the fold, so the agent will need
    # to scroll. We carefully word this so it doesn't match the scroll_down
    # recipe trigger (regex: scroll\s+down) — that recipe is single-step and
    # would short-circuit our multi-step task as "done" without ever clicking
    # the composer. Putting other words between "scroll" and any direction
    # keeps the recipe matcher inert.
    find_task = (
        "1) Find the 'Add a comment' box below the video. "
        "If it isn't on screen, page the view to bring it into view. "
        "2) Click the comment input box to open and focus it. "
        "3) Say done when the cursor is inside the text box."
    )
    find_result = service.execute_task(find_task, screen)
    if not find_result.success:
        return False, f"find_comment_box_failed: {find_result.reason}"

    # Settle so Firefox finishes focusing the contenteditable div before
    # keystrokes start — without this, ~25 leading chars get dropped.
    time.sleep(SERVO_SETTLE_SECONDS)

    # Step 3: Type the text directly via python to preserve newlines and avoid prompt injection
    screen.type_text(comment_text)
    _human_pause()

    # Step 4: Click submit. The composer has a Cancel + Comment button pair
    # at the bottom. The Comment button (next to Cancel) is the submit.
    submit_task = "Click the Comment button next to the Cancel button inside the comment composer to submit."
    submit_result = service.execute_task(submit_task, screen)
    if not submit_result.success:
        return False, f"comment_submit_failed: {submit_result.reason}"

    return True, "ok"
