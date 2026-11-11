"""Facebook comments through the agent's Firefox, read and driven over WebDriver BiDi.

The page is read rather than looked at. The comment box is found by its role
and label, the text goes in as keystrokes and is read back out of the box, and
the comment counts as posted only once a comment by the posting account with
that text is on the page. Nothing is scrolled by wheel or by sight: on the reel
viewer one scroll moves to the next person's reel, so the address is checked
again before typing and before submitting.

The box names the account it posts as ("Comment as <name>"). Set
GUAARDVARK_OUTREACH_FACEBOOK_AS in .env to that name (a Page, say) and a box
that would post as anyone else is refused before anything is typed. A group
is joined by a profile, so its box may name someone else: the name wanted on
group posts is GUAARDVARK_OUTREACH_FACEBOOK_GROUP_AS, and the first setting
applies there too while that one is unset.

Groups are also read here, for the scout: a group's newest posts, one post
with its comments, the group's rules and the groups the account is in. A
feed post does not carry its own address until the pointer rests on its
date, so the reader hovers it.

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
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse, urlunparse

from backend.services.social_outreach import typing_rhythm
from backend.services.social_outreach.transitions import WITHDRAWN_BEFORE_SUBMIT

logger = logging.getLogger(__name__)

POST_AS_ENV = "GUAARDVARK_OUTREACH_FACEBOOK_AS"
GROUP_POST_AS_ENV = "GUAARDVARK_OUTREACH_FACEBOOK_GROUP_AS"

# A reel or post can take a few seconds to render its comment panel after load.
_COMPOSER_WAIT_S = 8.0
# A new comment can take a few seconds to appear in the list after the submit.
_VERIFY_WAIT_S = 12.0
_POLL_S = 1.0
# Characters of the comment looked for in the comment list after the submit.
_NEEDLE_CHARS = 60

# Labels of the box that writes a top-level comment ("Comment as <your Page>",
# "Answer as <name>" under a question, "Write a comment…", "Write a public
# comment…"); reply boxes are left out.
_TOP_LEVEL_LABEL = re.compile(r"^((comment|answer) as\b|write a (public )?(comment|answer))", re.IGNORECASE)

# Pages that name the post in the query string rather than in the path.
_ID_PARAMS = ("story_fbid", "fbid", "v", "id")

# The scripts are joined into one line by the browser, so they hold no // comments.
_JS_HELPERS = r"""
  const TOP = /^((comment|answer) as\b|write a (public )?(comment|answer))/i;
  const labelOf = (el) => ((el.getAttribute('aria-label') || el.getAttribute('aria-placeholder') || '')).trim();
  const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const STORY = '[data-ad-rendering-role="story_message"], [data-ad-comet-preview="message"], [data-ad-preview="message"]';
  const postDialog = () => {
    const held = [...document.querySelectorAll('[role="dialog"]')]
      .filter((d) => d.querySelector(STORY) || d.querySelector('[aria-label^="Actions for this post"]'));
    return held.length ? held[held.length - 1] : null;
  };
  const scope = () => postDialog() || document;
  const boxes = () => [...scope().querySelectorAll('[role="textbox"][contenteditable="true"]')]
    .filter((el) => shown(el) && TOP.test(labelOf(el)));
"""

# The top-level comment box: how many there are, and for exactly one, its label,
# the account it names, what it holds and its centre in screen pixels. A post
# opened by its address shows in a dialog over whatever feed was behind it;
# only the dialog's box is counted, so a post in that feed is never answered.
_FIND_COMPOSER_JS = "(() => {" + _JS_HELPERS + r"""
  const found = boxes();
  const loginWall = !found.length && !!document.querySelector(
    'form[action*="login"], input[name="pass"], [data-testid="royal_login_form"]');
  if (found.length !== 1) return JSON.stringify({count: found.length, login_wall: loginWall, url: location.href});
  const box = found[0];
  box.scrollIntoView({block: 'nearest', behavior: 'instant'});
  const r = box.getBoundingClientRect();
  const label = labelOf(box);
  const m = label.match(/^(?:comment|answer) as (.+)$/i);
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
  for (const a of scope().querySelectorAll('[role="article"][aria-label]')) {
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


_GROUP_POST = re.compile(r"/groups/([^/?#]+)/(?:posts|permalink)/(\d+)")
_GROUP = re.compile(r"/groups/([^/?#]+)")
# Parts of a /groups/ address that are not a group.
_NOT_A_GROUP = {"joins", "feed", "discover", "create", "search", "notifications"}


def group_post_ref(url: str) -> Optional[tuple[str, str]]:
    """(group, post id) when ``url`` is a post in a group, else None."""
    m = _GROUP_POST.search(urlparse(url or "").path)
    return (m.group(1), m.group(2)) if m else None


def group_ref(text: str) -> Optional[str]:
    """The group a link or a bare id or name-in-address points at, else None."""
    text = (text or "").strip()
    if re.fullmatch(r"[A-Za-z0-9.]+", text):
        return text
    m = _GROUP.search(urlparse(text).path)
    if m and m.group(1) not in _NOT_A_GROUP:
        return m.group(1)
    return None


def group_post_url(group: str, post_id: str) -> str:
    return f"https://www.facebook.com/groups/{group}/posts/{post_id}/"


def wanted_identity(url: str) -> tuple[str, str]:
    """(name the comment box must post as, the setting it came from) for the
    post at ``url``; ("", "") when no name is required."""
    if group_post_ref(url):
        name = (os.environ.get(GROUP_POST_AS_ENV) or "").strip()
        if name:
            return name, GROUP_POST_AS_ENV
    name = (os.environ.get(POST_AS_ENV) or "").strip()
    return (name, POST_AS_ENV) if name else ("", "")


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
    wanted, setting = wanted_identity(landed)
    if wanted and actor.casefold() != wanted.casefold():
        return False, (
            f"wrong_identity: nothing was posted; the comment box posts as "
            f"{actor or 'an unnamed account'!r}, not {wanted!r} ({setting})"
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


# --- Reading groups ----------------------------------------------------------

# Feed scrolls per group, and seconds given to each for new posts to load.
FEED_SCROLLS = 8
_FEED_SETTLE_S = 2.0
# Seconds the pointer rests on a post's date before its address is read.
_HOVER_SETTLE_S = 0.9
# Comments of a post handed to the drafter.
POST_COMMENT_LIMIT = 8

_GROUP_JS = r"""
  const STORY = '[data-ad-rendering-role="story_message"], [data-ad-comet-preview="message"], [data-ad-preview="message"]';
  const clean = (t) => (t || '').replace(/…\s*See more\s*$/i, '').trim();
  const authorOf = (el) => {
    const a = el.querySelector('[aria-label^="Actions for this post by "]');
    return a ? a.getAttribute('aria-label').replace(/^Actions for this post by /, '').trim() : '';
  };
  const feedPosts = () => {
    const feed = document.querySelector('[role="feed"]');
    return feed ? [...feed.children].filter((k) => k.querySelector(STORY)) : [];
  };
  const keyOf = (k) => authorOf(k) + '|' + clean(k.querySelector(STORY).innerText).slice(0, 80);
  const dateLinks = (k) => [...k.querySelectorAll('a[role="link"]')].filter((a) => {
    const h = a.getAttribute('href') || '';
    const r = a.getBoundingClientRect();
    return (h.startsWith('?') || h === '#' || /\/(posts|permalink)\/\d+/.test(h)) && r.width > 0 && r.height > 0 && r.height < 30;
  });
  const addressOf = (k) => {
    const hit = [...k.querySelectorAll('a[href]')].map((a) => a.href).find((h) => /\/groups\/[^\/]+\/(posts|permalink)\/\d+/.test(h));
    return hit || '';
  };
"""

# The posts the feed holds right now. The feed keeps only the posts near the
# window loaded, so this is read again after every scroll.
_FEED_JS = "(() => {" + _GROUP_JS + r"""
  const name = (document.querySelector('[role="main"] h1') || {}).innerText || '';
  const posts = feedPosts().map((k) => ({
    key: keyOf(k), author: authorOf(k), text: clean(k.querySelector(STORY).innerText).slice(0, 1500), url: addressOf(k),
  }));
  return JSON.stringify({url: location.href, name: name.trim(), posts, login_wall: !!document.querySelector('input[name="pass"]')});
})()"""

# Bring the date of the feed post with __KEY__ into the window and say where
# it is on screen; __INDEX__ picks among the links that could be the date.
_FEED_DATE_JS = "(() => {" + _GROUP_JS + r"""
  const k = feedPosts().find((p) => keyOf(p) === __KEY__);
  if (!k) return JSON.stringify({found: false});
  const links = dateLinks(k);
  const a = links[__INDEX__];
  if (!a) return JSON.stringify({found: true, links: links.length, url: addressOf(k)});
  a.scrollIntoView({block: 'center', behavior: 'instant'});
  const r = a.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  return JSON.stringify({
    found: true, links: links.length, url: addressOf(k),
    x: Math.round((window.mozInnerScreenX + r.left + r.width / 2) * dpr),
    y: Math.round((window.mozInnerScreenY + r.top + r.height / 2) * dpr),
  });
})()"""

_FEED_SCROLL_JS = "(() => { window.scrollBy(0, Math.round(innerHeight * 1.6)); return JSON.stringify({y: scrollY}); })()"

# One post opened by its address: its text, who wrote it, the group, its
# comments and the name its comment box posts as.
_POST_JS = "(() => {" + _JS_HELPERS + r"""
  const root = scope();
  const story = root.querySelector(STORY);
  const actions = root.querySelector('[aria-label^="Actions for this post by "]');
  const comments = [...root.querySelectorAll('[role="article"][aria-label^="Comment by "]')].map((a) => {
    const by = a.getAttribute('aria-label').replace(/^Comment by /, '').replace(/\s+(about\s+)?(an?|\d+)\s+\w+\s+ago$/i, '');
    const body = [...a.querySelectorAll('div[dir="auto"]')].map((d) => d.innerText.trim()).filter(Boolean).join(' ');
    return {by, text: body.slice(0, 600)};
  }).filter((c) => c.text);
  const found = boxes();
  const label = found.length === 1 ? labelOf(found[0]) : '';
  const m = label.match(/^(?:comment|answer) as (.+)$/i);
  const heads = [...root.querySelectorAll('h1, h2, h3')].map((h) => h.innerText.trim()).filter(Boolean);
  return JSON.stringify({
    url: location.href, dialog: root !== document,
    text: story ? story.innerText.trim() : '',
    author: actions ? actions.getAttribute('aria-label').replace(/^Actions for this post by /, '').trim() : '',
    heads: heads.slice(0, 4), comments, boxes: found.length, actor: m ? m[1].trim() : '',
    login_wall: !!document.querySelector('input[name="pass"]'),
  });
})()"""

_RULES_JS = r"""(() => {
  const main = document.querySelector('[role="main"]');
  if (!main) return JSON.stringify({readable: false});
  const text = main.innerText || '';
  const at = text.search(/\bGroup rules\b/i);
  return JSON.stringify({readable: text.length > 200, rules: at >= 0 ? text.slice(at, at + 4000) : ''});
})()"""

_JOINED_JS = r"""(() => {
  const seen = new Map();
  for (const a of document.querySelectorAll('[role="main"] a[href*="/groups/"]')) {
    const m = a.href.match(/facebook\.com\/groups\/([^\/?#]+)\/?(\?|$)/);
    const name = (a.innerText || '').trim().split('\n')[0];
    if (m && name && !seen.has(m[1])) seen.set(m[1], name);
  }
  return JSON.stringify({groups: [...seen], height: document.body.scrollHeight});
})()"""


@dataclass
class GroupPost:
    group: str
    post_id: str
    url: str
    author: str
    text: str
    group_name: str = ""
    comments: list[str] = field(default_factory=list)
    # The name the comment box posts as; "" when the post takes no comment.
    actor: str = ""
    # The account behind the comment box has already commented here.
    answered: bool = False


def _open(url: str, settle_s: float = 4.0) -> bool:
    from backend.services.social_outreach.reddit_outreach import _bidi_navigate

    return _bidi_navigate(url, settle_seconds=settle_s, nav_timeout=30.0)


def joined_groups(max_scrolls: int = 12) -> list[tuple[str, str]]:
    """(group, name) for the groups the signed-in account is in."""
    if not _open("https://www.facebook.com/groups/joins/"):
        return []
    groups: list = []
    for _ in range(max_scrolls):
        data, _why = _read(_JOINED_JS)
        found = (data or {}).get("groups") or []
        if data is not None and len(found) == len(groups) and groups:
            break
        groups = found or groups
        _read("(() => { window.scrollTo(0, document.body.scrollHeight); return JSON.stringify({ok: true}); })()")
        time.sleep(1.5)
    return [(str(g), str(n)) for g, n in groups if str(g) not in _NOT_A_GROUP]


def read_group_rules(group: str) -> Optional[str]:
    """The group's rules as its About page lists them, "" when it lists none,
    None when the page could not be read."""
    if not _open(f"https://www.facebook.com/groups/{group}/about"):
        return None
    data, _why = _read(_RULES_JS)
    if not data or not data.get("readable"):
        return None
    return str(data.get("rules") or "")


def read_group_feed(
    group: str,
    wanted: Callable[[str], bool],
    max_posts: int,
    *,
    skip_post_ids: Optional[set] = None,
    scrolls: int = FEED_SCROLLS,
) -> tuple[str, list[GroupPost], dict]:
    """The group's newest posts whose text ``wanted`` accepts, each with its
    address, newest first; at most ``max_posts``.

    Returns ``(group name, posts, counts)``; counts holds ``seen`` (posts
    read), ``unwanted``, ``known`` (in ``skip_post_ids``) and ``no_address``,
    and ``problem`` when the feed could not be read. The text is the feed's
    preview, which Facebook cuts; ``read_group_post`` reads all of it.
    """
    from backend.services.local_screen_backend import LocalScreenBackend

    counts = {"seen": 0, "unwanted": 0, "known": 0, "no_address": 0}
    if not _open(f"https://www.facebook.com/groups/{group}/?sorting_setting=CHRONOLOGICAL", settle_s=6.0):
        counts["problem"] = "the group did not load"
        return "", [], counts
    try:
        screen = LocalScreenBackend()
    except Exception as e:  # noqa: BLE001
        counts["problem"] = f"the agent display is unavailable ({e})"
        return "", [], counts

    name = ""
    handled: set[str] = set()
    posts: list[GroupPost] = []
    for step in range(scrolls + 1):
        data, why = _read(_FEED_JS)
        if data is None:
            counts["problem"] = f"the feed could not be read ({why})"
            break
        if data.get("login_wall"):
            counts["problem"] = "the agent browser is not signed in to Facebook"
            break
        if urlparse(str(data.get("url") or "")).path.rstrip("/") != f"/groups/{group}":
            counts["problem"] = f"the browser left the group ({str(data.get('url') or '')[:120]})"
            break
        name = name or str(data.get("name") or "")
        for item in data.get("posts") or []:
            key = str(item.get("key") or "")
            if not key or key in handled:
                continue
            handled.add(key)
            counts["seen"] += 1
            text = str(item.get("text") or "")
            if not wanted(text):
                counts["unwanted"] += 1
                continue
            url = str(item.get("url") or "") or _feed_post_address(screen, key)
            ref = group_post_ref(url)
            if ref is None:
                counts["no_address"] += 1
                continue
            if skip_post_ids and ref[1] in skip_post_ids:
                counts["known"] += 1
                continue
            posts.append(GroupPost(
                group=group, post_id=ref[1], url=group_post_url(group, ref[1]),
                author=str(item.get("author") or ""), text=text, group_name=name,
            ))
            if len(posts) >= max_posts:
                return name, posts, counts
        if step < scrolls:
            _read(_FEED_SCROLL_JS)
            time.sleep(_FEED_SETTLE_S)
    return name, posts, counts


def _feed_post_address(screen, key: str) -> str:
    """Rest the pointer on the date of the feed post with ``key`` and read the
    address Facebook then gives it; "" when none of its links turns into one."""
    script = _FEED_DATE_JS.replace("__KEY__", json.dumps(key))
    for index in range(3):
        spot, _why = _read(script.replace("__INDEX__", str(index)))
        if not spot or not spot.get("found"):
            return ""
        if spot.get("url"):
            return str(spot["url"])
        if "x" not in spot:
            return ""
        screen.hover(int(spot["x"]), int(spot["y"]), settle_ms=int(_HOVER_SETTLE_S * 1000))
        after, _why = _read(script.replace("__INDEX__", str(index)))
        if after and after.get("url"):
            return str(after["url"])
    return ""


def read_group_post(url: str) -> tuple[Optional[GroupPost], str]:
    """The group post at ``url`` with its comments; ``(post, "")`` or
    ``(None, why)``."""
    ref = group_post_ref(url)
    if ref is None:
        return None, "that link is not a post in a Facebook group"
    target = group_post_url(*ref)
    if not _open(target, settle_s=5.0):
        return None, "the post did not load"
    data: Optional[dict] = None
    why = ""
    deadline = time.monotonic() + _COMPOSER_WAIT_S
    while True:
        data, why = _read(_POST_JS)
        if data is not None and (data.get("text") or data.get("login_wall")):
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(_POLL_S)
    if data is None:
        return None, f"the post could not be read ({why})"
    if data.get("login_wall"):
        return None, "the agent browser is not signed in to Facebook"
    if not same_post(str(data.get("url") or ""), target):
        return None, f"the post's address led elsewhere ({str(data.get('url') or '')[:120]})"
    text = str(data.get("text") or "").strip()
    if not text:
        return None, "the post has no text to answer (a photo or video alone)"
    actor = str(data.get("actor") or "")
    comments = data.get("comments") or []
    heads = [str(h) for h in data.get("heads") or []]
    # The dialog's headings are "<author>'s Post" and then the group's name.
    group_name = next((h for h in heads if not h.endswith("'s Post") and not h.endswith("' Post")), "")
    return GroupPost(
        group=ref[0], post_id=ref[1], url=target,
        author=str(data.get("author") or ""), text=text, group_name=group_name,
        comments=[f"{c.get('by', '')}: {c.get('text', '')}"[:600] for c in comments[:POST_COMMENT_LIMIT]],
        actor=actor if int(data.get("boxes") or 0) == 1 else "",
        answered=bool(actor) and any(
            str(c.get("by") or "").casefold().startswith(actor.casefold() + " ") for c in comments
        ),
    ), ""
