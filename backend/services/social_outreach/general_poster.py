"""Platform-agnostic poster — drive the general agent loop to post anywhere.

The founding principle: put the work into the hand/eye/brain and the platform
stops mattering. Modern models already know how Reddit, X, YouTube, and Facebook
compose boxes work; the grounded eye (DOM element inventory in the decision
prompt) tells the brain what's actually on THIS page. So instead of a hand-written
BiDi poster per platform, one NL-driven loop finds the composer, types the text,
and submits — on any site the operator is logged into.

No posting path calls this today. It served X, which is refused
(platforms.REFUSED); Reddit, YouTube and Facebook comments have BiDi posters
and post only as watched posts (watched_post). A platform added here must be
in platforms.POLICIES as WATCHED, never posting on its own.

Contract mirrors reddit_outreach.post_comment_via_servo: returns (success, reason).
"""

from __future__ import annotations

import logging
import random
import time
from typing import Callable, Optional

from backend.services.social_outreach.transitions import WITHDRAWN_BEFORE_SUBMIT

logger = logging.getLogger(__name__)

# Words that, when a prominent element and NO composer is present, mean the
# cloned session is logged out on this platform.
_LOGIN_CTA_WORDS = ("log in", "sign in", "log-in", "sign-in", "login", "signin")


def _human_pause(min_s: float = 0.3, max_s: float = 2.0) -> None:
    time.sleep(random.uniform(min_s, max_s))


def _preflight_logged_in(platform: str) -> tuple[bool, str]:
    """Grounded-eye login check: is a composer present and no login wall?

    Uses the same DOM inventory that grounds the brain. Deterministic, no LLM.
    Conservative: only aborts on a CLEAR logged-out signal (a login CTA with no
    composer). If the page can't be introspected, proceeds and lets the post's
    own verification catch failure — better than never posting on pages we can't
    read.
    """
    try:
        from backend.services.dom_metadata_extractor import DOMMetadataExtractor
        snap = DOMMetadataExtractor.get_instance().extract()
    except Exception as e:  # noqa: BLE001
        logger.info("preflight: DOM introspection unavailable (%s) — proceeding", e)
        return True, "preflight_skipped_no_dom"

    if not snap or not getattr(snap, "success", False) or not snap.elements:
        return True, "preflight_skipped_no_elements"

    def _is_composer(el) -> bool:
        et = (getattr(el, "element_type", "") or "").lower()
        tag = (getattr(el, "tag", "") or "").lower()
        return (
            et == "composer"
            or "textbox" in et
            or tag == "textarea"
            or (tag == "div" and "contenteditable" in et)
        )

    has_composer = any(_is_composer(el) for el in snap.elements)
    has_login_cta = any(
        any(w in (getattr(el, "text", "") or "").lower() for w in _LOGIN_CTA_WORDS)
        for el in snap.elements
    )

    if has_login_cta and not has_composer:
        return False, f"logged_out:{platform}"
    return True, "ok"


# Characters of the posted text looked for on the page after a submit.
_PAGE_CHECK_CHARS = 60

# Reads the page after a submit. Text is collected outside every composer
# (textarea or contenteditable) and compared with all whitespace removed, so
# line breaks rendered as <br> still match. Composers are counted separately:
# one still holding text means the submit did not go through.
_PAGE_CHECK_JS = r"""(() => {
  const want = __NEEDLE__.replace(/\s+/g, '');
  const COMPOSER = 'textarea, [contenteditable]:not([contenteditable="false"])';
  const SKIP = 'script, style, noscript, template';
  let outside = '';
  const composers = [];
  const walk = (root) => {
    const w = document.createTreeWalker(
      root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
        acceptNode: (n) => {
          if (n.nodeType !== 1) return NodeFilter.FILTER_ACCEPT;
          if (n.matches(COMPOSER)) { composers.push(n); return NodeFilter.FILTER_REJECT; }
          if (n.matches(SKIP)) return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_ACCEPT;
        },
      });
    let n;
    while ((n = w.nextNode())) {
      if (n.nodeType === 3) outside += n.nodeValue;
      else if (n.shadowRoot) walk(n.shadowRoot);
    }
  };
  walk(document.body || document.documentElement);
  outside = outside.replace(/\s+/g, '');
  const held = composers.filter((c) => {
    const t = c.tagName === 'TEXTAREA' ? c.value : (c.innerText || c.textContent);
    return (t || '').replace(/\s+/g, '').length > 0;
  });
  return JSON.stringify({
    text_on_page: !!want && outside.includes(want),
    composers: composers.length,
    composers_with_text: held.length,
    url: location.href,
  });
})()"""


def _same_page(url: str, target_url: str) -> bool:
    """Same host and path, ignoring scheme, query, fragment and a trailing slash."""
    from urllib.parse import urlparse

    a, b = urlparse(url or ""), urlparse(target_url or "")
    host = lambda u: (u.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    return host(a) == host(b) and a.path.rstrip("/") == b.path.rstrip("/")


def _still_on_target(target_url: str) -> tuple[bool, str]:
    """Whether the browser is still on the approved page.

    The loop clicks and scrolls by sight; on a reel viewer one scroll moves
    to someone else's video, and text typed or submitted there would land
    on the wrong post.
    """
    from backend.services.social_outreach.reddit_outreach import bidi_evaluate_json

    data, why = bidi_evaluate_json("JSON.stringify({url: location.href})")
    if data is None:
        return False, f"page not readable ({why})"
    url = str(data.get("url") or "")
    return _same_page(url, target_url), url[:200]


def _text_published(text: str) -> tuple[bool, str]:
    """Read the page and decide whether ``text`` was published.

    Published means the first characters of the text are on the page outside
    every composer, and no composer still holds text. Returns
    ``(published, detail)``; an unreadable page is not published.
    """
    import json

    from backend.services.social_outreach.reddit_outreach import bidi_evaluate_json

    needle = (text or "").strip()[:_PAGE_CHECK_CHARS]
    data, why = bidi_evaluate_json(_PAGE_CHECK_JS.replace("__NEEDLE__", json.dumps(needle)))
    if data is None:
        return False, f"page not readable ({why})"
    on_page = bool(data.get("text_on_page"))
    held = int(data.get("composers_with_text") or 0)
    detail = (
        f"text_on_page={on_page} composers_with_text={held} "
        f"url={str(data.get('url') or '')[:200]}"
    )
    return on_page and held == 0, detail


def post_via_agent_loop(
    platform: str,
    target_url: str,
    text: str,
    *,
    action: str = "comment",
    anchor_hint: Optional[str] = None,
    before_submit: Optional[Callable[[], bool]] = None,
) -> tuple[bool, str]:
    """Post `text` on `target_url` by driving the general see-think-act loop.

    action: "comment" | "reply" | "share" — shapes the NL instruction only; the
    loop discovers the actual controls. anchor_hint (for replies) names the
    parent comment to reply under.

    Prompt-injection safe: the user/draft text is NEVER interpolated into an LLM
    instruction. The loop is asked only to CLICK the composer and the submit
    control; the text is typed via screen.type_text(), bypassing the prompt (same
    hardening as self_share._submit_post_via_servo).

    Success also needs the page read after the submit (``_text_published``);
    otherwise the reason is ``submit_unverified: <what the page showed>``.

    ``before_submit`` is called once the text is typed and immediately before
    the step that publishes; when it returns False nothing is published and
    the reason is ``transitions.WITHDRAWN_BEFORE_SUBMIT``.
    """
    from backend.services.agent_control_service import get_agent_control_service
    from backend.services.local_screen_backend import LocalScreenBackend
    from backend.utils.agent_display_utils import start_agent_display_if_needed
    from backend.services.social_outreach.reddit_outreach import (
        SERVO_SETTLE_SECONDS,
        bidi_reachable,
        page_check_unavailable_reason,
    )

    if not (text or "").strip():
        return False, "empty_text"

    service = get_agent_control_service()
    if service.is_active:
        return False, "agent_busy"
    if not start_agent_display_if_needed():
        return False, "display_unavailable"
    try:
        screen = LocalScreenBackend()
    except Exception as e:  # noqa: BLE001
        logger.warning("general_poster: display unavailable: %s", e)
        return False, "display_unavailable"
    readable, why = bidi_reachable()
    if not readable:
        return False, page_check_unavailable_reason(why)

    # 1) Navigate to the target.
    nav = service.execute_task(f"navigate to {target_url}", screen)
    if not nav.success:
        return False, f"navigate_failed: {nav.reason}"
    time.sleep(SERVO_SETTLE_SECONDS)

    # 2) Login preflight (grounded eye).
    ok, reason = _preflight_logged_in(platform)
    if not ok:
        return False, reason

    # 3) Focus the composer. For replies, first open the reply UI under the
    #    named parent. anchor_hint is a page LABEL, not free instruction — but
    #    keep it short and non-directive.
    if action == "reply" and anchor_hint:
        open_reply = (
            "On this page, find the comment that contains this text: "
            f"\"{anchor_hint[:120]}\". Click its Reply control to open a reply box, "
            "then say done."
        )
        r = service.execute_task(open_reply, screen)
        if not r.success:
            return False, f"open_reply_failed: {r.reason}"

    focus_task = (
        f"On this page, find the main text box for writing a {action} "
        "(a comment/reply/post composer — look at the interactive elements list). "
        "Click it so the cursor is inside it, then say done."
    )
    focus = service.execute_task(focus_task, screen)
    if not focus.success:
        return False, f"focus_composer_failed: {focus.reason}"

    # 4) Type the user text directly — never through the LLM prompt.
    on_target, where = _still_on_target(target_url)
    if not on_target:
        return False, f"wrong_page: nothing was posted; the browser left the target before typing ({where})"
    from backend.services.social_outreach import typing_rhythm

    screen.type_text(text, key_delays=typing_rhythm.delays(text))
    _human_pause()

    # 5) Submit.
    on_target, where = _still_on_target(target_url)
    if not on_target:
        return False, f"wrong_page: nothing was posted; the browser left the target before submitting ({where})"
    if before_submit is not None and not before_submit():
        return False, WITHDRAWN_BEFORE_SUBMIT
    submit_task = (
        f"On this page, click the button that publishes/submits the {action} "
        "(e.g. Post, Reply, Comment, Tweet). Then say done."
    )
    submit = service.execute_task(submit_task, screen)
    if not submit.success:
        return False, f"submit_failed: {submit.reason}"

    # The loop's "done" means a click changed the screen, not that the text
    # was published, so the page itself is read before this counts as posted.
    time.sleep(SERVO_SETTLE_SECONDS)
    published, detail = _text_published(text)
    logger.info("general_poster: post-submit page check on %s: published=%s %s",
                platform, published, detail)
    if not published:
        return False, f"submit_unverified: {detail}"
    return True, "ok"
