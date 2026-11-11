"""Facebook comments through the agent's Firefox, read and driven over WebDriver BiDi.

The page is read rather than looked at. The comment box is found by its role
and label, the text goes in as keystrokes and is read back out of the box, and
the comment counts as posted only once a comment by the posting account with
that text is on the page. Nothing is scrolled by wheel or by sight: on the reel
viewer one scroll moves to the next person's reel, so the address is checked
again before typing and before submitting.

The box names the account it posts as ("Comment as <name>"). Set
GUAARDVARK_OUTREACH_FACEBOOK_AS in .env to that name (a Page, say) and a box
that would post as anyone else is refused before anything is typed.

Contract matches the other posters: ``(success, reason)``. A reason starting
``submitted_unconfirmed`` means Facebook took the submit and the comment may be
live, so approving the draft again could post it twice.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse, urlunparse

from backend.services.social_outreach import typing_rhythm
from backend.services.social_outreach.transitions import WITHDRAWN_BEFORE_SUBMIT

logger = logging.getLogger(__name__)

POST_AS_ENV = "GUAARDVARK_OUTREACH_FACEBOOK_AS"

# A reel or post can take a few seconds to render its comment panel after load.
_COMPOSER_WAIT_S = 8.0
# A new comment can take a few seconds to appear in the list after the submit.
_VERIFY_WAIT_S = 12.0
_POLL_S = 1.0
# Characters of the comment looked for in the comment list after the submit.
_NEEDLE_CHARS = 60

# Labels of the box that writes a top-level comment ("Comment as <your Page>",
# "Write a comment…", "Write a public comment…"); reply boxes are left out.
_TOP_LEVEL_LABEL = re.compile(r"^(comment as\b|write a (public )?comment)", re.IGNORECASE)

# Pages that name the post in the query string rather than in the path.
_ID_PARAMS = ("story_fbid", "fbid", "v", "id")

# The scripts are joined into one line by the browser, so they hold no // comments.
_JS_HELPERS = r"""
  const TOP = /^(comment as\b|write a (public )?comment)/i;
  const labelOf = (el) => ((el.getAttribute('aria-label') || el.getAttribute('aria-placeholder') || '')).trim();
  const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const boxes = () => [...document.querySelectorAll('[role="textbox"][contenteditable="true"]')]
    .filter((el) => shown(el) && TOP.test(labelOf(el)));
"""

# The top-level comment box: how many there are, and for exactly one, its label,
# the account it names, what it holds and its centre in screen pixels.
_FIND_COMPOSER_JS = "(() => {" + _JS_HELPERS + r"""
  const found = boxes();
  const loginWall = !found.length && !!document.querySelector(
    'form[action*="login"], input[name="pass"], [data-testid="royal_login_form"]');
  if (found.length !== 1) return JSON.stringify({count: found.length, login_wall: loginWall, url: location.href});
  const box = found[0];
  box.scrollIntoView({block: 'nearest', behavior: 'instant'});
  const r = box.getBoundingClientRect();
  const label = labelOf(box);
  const m = label.match(/^comment as (.+)$/i);
  const dpr = window.devicePixelRatio || 1;
  return JSON.stringify({
    count: 1, label, actor: m ? m[1].trim() : '', text: box.innerText || '',
    x: Math.round((window.mozInnerScreenX + r.left + r.width / 2) * dpr),
    y: Math.round((window.mozInnerScreenY + r.top + r.height / 2) * dpr),
    url: location.href,
  });
})()"""

# Opens the comment panel of the post on screen (a reel shows it closed). Only a
# button fully inside the viewport counts: the reel viewer keeps the next reel's
# buttons loaded below the fold.
_OPEN_COMMENTS_JS = r"""(() => {
  const inView = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.top >= 0 && r.bottom <= innerHeight; };
  const btn = [...document.querySelectorAll('[role="button"][aria-label="Comment"], [role="button"][aria-label="Leave a comment"]')].find(inView);
  if (!btn) return JSON.stringify({clicked: false});
  btn.click();
  return JSON.stringify({clicked: true, label: btn.getAttribute('aria-label')});
})()"""

# After the submit: is a comment by the posting account holding the text on the
# page, does a comment box still hold text, and what do alerts say. Emoji and
# whitespace are dropped on both sides, since Facebook draws emoji as images.
_VERIFY_JS = "(() => {" + _JS_HELPERS + r"""
  const STRIP = /[\p{Extended_Pictographic}\u200d\ufe0f\s]/gu;
  const want = __NEEDLE__.replace(STRIP, '');
  const actor = __ACTOR__.toLowerCase();
  let mine = false;
  let anyone = false;
  for (const a of document.querySelectorAll('[role="article"][aria-label]')) {
    if (!want || !(a.innerText || '').replace(STRIP, '').includes(want)) continue;
    anyone = true;
    const by = (a.getAttribute('aria-label') || '').toLowerCase();
    if (!actor || by.startsWith('comment by ' + actor + ' ')) { mine = true; break; }
  }
  const held = boxes().some((b) => (b.innerText || '').replace(/\s+/g, '').length > 0);
  const alerts = [...document.querySelectorAll('[role="alert"]')]
    .map((e) => (e.innerText || '').trim()).filter(Boolean).join(' | ').slice(0, 200);
  return JSON.stringify({mine, anyone, held, alerts, url: location.href});
})()"""


def canonical_post_url(url: str) -> str:
    """The post's address on www.facebook.com; m., web. and fb.com links map to it."""
    from backend.utils.hosts import host_matches

    parsed = urlparse(url or "")
    if host_matches(parsed.hostname, "facebook.com", "fb.com"):
        return urlunparse(parsed._replace(scheme="https", netloc="www.facebook.com"))
    return url


def same_post(url: str, target_url: str) -> bool:
    """Same host and path, and the same post id where the query string carries it."""
    a, b = urlparse(url or ""), urlparse(target_url or "")

    def host(u):
        return (u.hostname or "").lower().removeprefix("www.").removeprefix("m.").removeprefix("web.")

    if host(a) != host(b) or a.path.rstrip("/") != b.path.rstrip("/"):
        return False
    qa, qb = parse_qs(a.query), parse_qs(b.query)
    return all(qa.get(k) == qb.get(k) for k in _ID_PARAMS if k in qa or k in qb)


def _read(expression: str) -> tuple[Optional[dict], str]:
    from backend.services.social_outreach.reddit_outreach import bidi_evaluate_json

    return bidi_evaluate_json(expression)


def _location() -> str:
    data, _why = _read("JSON.stringify({url: location.href})")
    return str((data or {}).get("url") or "")


def _find_composer(wait_s: float = _COMPOSER_WAIT_S) -> tuple[Optional[dict], str]:
    """The one top-level comment box, opening the comment panel if it is closed.

    Returns ``(box, "")`` or ``(None, reason)``. More than one box means the
    page holds several posts and the right one cannot be told apart.
    """
    deadline = time.monotonic() + wait_s
    opened = False
    data: Optional[dict] = None
    why = ""
    while True:
        data, why = _read(_FIND_COMPOSER_JS)
        if data is not None:
            count = int(data.get("count") or 0)
            if count == 1:
                return data, ""
            if count > 1:
                return None, f"composer_ambiguous: nothing was posted; the page has {count} comment boxes"
            if not opened:
                clicked, _ = _read(_OPEN_COMMENTS_JS)
                opened = bool((clicked or {}).get("clicked"))
        if time.monotonic() >= deadline:
            break
        time.sleep(_POLL_S)
    if data is None:
        return None, f"composer_not_found: nothing was posted; the page could not be read ({why})"
    if data.get("login_wall"):
        return None, "logged_out:facebook: nothing was posted; the agent browser is not signed in to Facebook"
    return None, f"composer_not_found: nothing was posted; no comment box on {str(data.get('url') or '')[:200]}"


def _composer_focused() -> Optional[dict]:
    """The focused element when it is a top-level comment box, else None."""
    from backend.services.social_outreach.reddit_outreach import _focused_editable

    held = _focused_editable()
    if held and held.get("editable") and _TOP_LEVEL_LABEL.search((held.get("label") or "").strip()):
        return held
    return None


def _wait_for_focus(wait_s: float = 3.0) -> Optional[dict]:
    deadline = time.monotonic() + wait_s
    while True:
        held = _composer_focused()
        if held is not None or time.monotonic() >= deadline:
            return held
        time.sleep(0.25)


def _typed_ok(result) -> bool:
    return not (isinstance(result, dict) and result.get("success") is False)


def _type_comment(screen, text: str) -> bool:
    """Type ``text`` with Shift+Enter between lines: a bare Enter posts the comment."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line and not _typed_ok(screen.type_text(line, key_delays=typing_rhythm.delays(line))):
            return False
        if i < len(lines) - 1:
            screen.hotkey("shift", "Return")
    return True


def _clear_composer(screen) -> None:
    screen.hotkey("ctrl", "a")
    screen.hotkey("BackSpace")


def _verify(text: str, actor: str) -> tuple[bool, dict, str]:
    """Poll the page for the comment; returns ``(posted, last_reading, why)``."""
    needle = (text or "").strip()[:_NEEDLE_CHARS]
    script = _VERIFY_JS.replace("__NEEDLE__", json.dumps(needle)).replace("__ACTOR__", json.dumps(actor or ""))
    deadline = time.monotonic() + _VERIFY_WAIT_S
    last: dict = {}
    why = ""
    while True:
        data, why = _read(script)
        if data is not None:
            last = data
            if data.get("mine"):
                return True, last, ""
        if time.monotonic() >= deadline:
            return False, last, why
        time.sleep(_POLL_S)


def post_comment_via_bidi(
    target_url: str,
    comment_text: str,
    *,
    before_submit: Optional[Callable[[], bool]] = None,
) -> tuple[bool, str]:
    """Post ``comment_text`` as a top-level comment on the Facebook post or reel.

    ``before_submit`` is called once the text is typed and read back, immediately
    before the Enter that publishes; when it returns False nothing is published
    and the reason is ``transitions.WITHDRAWN_BEFORE_SUBMIT``.
    """
    from backend.services.agent_control_service import get_agent_control_service
    from backend.services.local_screen_backend import LocalScreenBackend
    from backend.services.social_outreach.reddit_outreach import (
        SERVO_SETTLE_SECONDS,
        _bidi_navigate,
        _same_text,
        bidi_reachable,
        page_check_unavailable_reason,
    )
    from backend.utils.agent_display_utils import start_agent_display_if_needed

    text = (comment_text or "").replace("\r\n", "\n").strip()
    if not text:
        return False, "empty_text"

    if get_agent_control_service().is_active:
        return False, "agent_busy"
    if not start_agent_display_if_needed():
        return False, "display_unavailable"
    try:
        screen = LocalScreenBackend()
    except Exception as e:  # noqa: BLE001
        logger.warning("facebook poster: display unavailable: %s", e)
        return False, "display_unavailable"
    readable, why = bidi_reachable()
    if not readable:
        return False, page_check_unavailable_reason(why)

    target = canonical_post_url(target_url)
    if not _bidi_navigate(target, settle_seconds=SERVO_SETTLE_SECONDS, nav_timeout=30.0):
        return False, "navigate_failed: nothing was posted; the browser did not load the post"
    # Nothing has been clicked yet, so where the browser landed is the post
    # itself, after any redirect Facebook applied; later reads are held to it.
    from backend.utils.hosts import host_matches

    landed = _location()
    if not landed:
        return False, "page_check_unavailable: nothing was posted; the post's address could not be read"
    if not host_matches(urlparse(landed).hostname, "facebook.com"):
        return False, f"wrong_page: nothing was posted; the post's address led off Facebook ({landed[:200]})"
    if not same_post(landed, target):
        logger.info("facebook poster: %s redirected to %s", target, landed)

    box, why = _find_composer()
    if box is None:
        return False, why

    actor = str(box.get("actor") or "")
    wanted = (os.environ.get(POST_AS_ENV) or "").strip()
    if wanted and actor.casefold() != wanted.casefold():
        return False, (
            f"wrong_identity: nothing was posted; the comment box posts as "
            f"{actor or 'an unnamed account'!r}, not {wanted!r} ({POST_AS_ENV})"
        )

    screen.click(int(box["x"]), int(box["y"]))
    held = _wait_for_focus()
    if held is None:
        return False, "composer_not_focused: nothing was posted; the click did not reach the comment box"
    if (held.get("text") or "").strip():
        # A box left holding text by an earlier attempt is emptied first.
        _clear_composer(screen)
        held = _composer_focused()
        if held is None or (held.get("text") or "").strip():
            return False, "composer_not_empty: nothing was posted; the comment box held earlier text"

    if not same_post(_location(), landed):
        return False, f"wrong_page: nothing was posted; the browser left the post before typing ({_location()[:200]})"
    if not _type_comment(screen, text):
        _clear_composer(screen)
        return False, "type_failed: nothing was posted; typing into the comment box failed"
    time.sleep(0.8)

    held = _composer_focused()
    if held is None or not _same_text(held.get("text", ""), text):
        if held is not None:
            _clear_composer(screen)
        shown = " ".join(((held or {}).get("text") or "").split())[:60]
        return False, (
            f"typed_text_mismatch: nothing was posted; the comment box held {shown!r} instead of the approved text"
            if held is not None else
            "typed_text_unreadable: nothing was posted; the comment box lost focus or could not be read back"
        )

    where = _location()
    if not same_post(where, landed):
        return False, f"wrong_page: nothing was posted; the browser left the post before submitting ({where[:200]})"
    if before_submit is not None and not before_submit():
        return False, WITHDRAWN_BEFORE_SUBMIT
    screen.hotkey("Return")

    posted, reading, why = _verify(text, actor)
    detail = (
        f"mine={reading.get('mine')} anyone={reading.get('anyone')} held={reading.get('held')} "
        f"alerts={reading.get('alerts')!r} url={str(reading.get('url') or '')[:200]}"
        if reading else f"page not readable ({why})"
    )
    logger.info("facebook poster: post-submit check: posted=%s as=%r %s", posted, actor, detail)
    if posted:
        return True, "ok"
    if reading and not reading.get("held"):
        return False, f"submitted_unconfirmed: may be live, check the post before approving again ({detail})"
    return False, f"submit_failed: {detail}"
