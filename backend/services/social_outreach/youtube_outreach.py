"""
YouTube outreach — posts comments and replies on YouTube videos via the
servo-driven Firefox on DISPLAY=:99 (which has the user's logged-in
YouTube session cookies).

Both functions return (success, reason) for audit tracking. They mirror
reddit_outreach.post_comment_via_servo's contract.

Implementation note (2026-07-19 rewrite):
  Recipe-chain posting (navigate_url → find_on_page → vision click) was
  aborting at focus_youtube_comment_field — Cancel never appeared because
  the vision click missed the composer, then submit_unverified fired after
  a false-positive Ctrl+Enter. Mirror Reddit's BiDi path instead:

    BiDi navigate → pause (Esc/k) → BiDi scrollIntoView composer →
    click coords → type_text → Ctrl+Enter → BiDi DOM verify
"""

from __future__ import annotations

import logging
import random
import re
import time
from typing import Callable, Optional

from backend.services.social_outreach.transitions import WITHDRAWN_BEFORE_SUBMIT

logger = logging.getLogger(__name__)

SERVO_SETTLE_SECONDS = 4
BIDI_PORT = 9222

# Min length for a parent-comment anchor passed to find_on_page. Too short
# and find-on-page lands on something unrelated (e.g. matching "the" all
# over the page); too long and YouTube's HTML escaping breaks the match.
MIN_REPLY_ANCHOR_LEN = 12
MAX_REPLY_ANCHOR_LEN = 80


def _human_pause(min_s: float = 0.3, max_s: float = 2.0) -> None:
    """Random sleep to avoid deterministic bot timing fingerprints."""
    time.sleep(random.uniform(min_s, max_s))


def _normalize_youtube_url(target_url: str) -> Optional[str]:
    """Coerce youtu.be / mobile / share URLs to canonical youtube.com/watch?v=.
    Returns None if the URL isn't a YouTube watch URL at all.
    """
    from urllib.parse import urlparse

    from backend.utils.hosts import host_matches

    parsed = urlparse(target_url or "")
    host = (parsed.hostname or "").lower()
    if host_matches(host, "youtu.be"):
        vid = parsed.path.strip("/").split("/")[0]
        return f"https://www.youtube.com/watch?v={vid}" if vid else None
    if host_matches(host, "youtube.com"):
        return target_url
    return None


def _run_recipe_step(service, screen, chat_message: str, failure_tag: str) -> tuple[bool, str]:
    """Hand chat_message to execute_task. Expect a single recipe to match.

    Kept for reply path / tests that still exercise the recipe chain.
    """
    result = service.execute_task(chat_message, screen)
    if not result.success:
        reason_lc = (result.reason or "").lower()
        if "sign" in reason_lc and ("in" in reason_lc or "-in" in reason_lc):
            return False, "auth_required"
        return False, f"{failure_tag}: {result.reason}"
    return True, "ok"


def _trim_anchor(parent_text: str) -> Optional[str]:
    """Pick a distinctive substring of the parent comment to feed find_on_page."""
    if not parent_text:
        return None
    cleaned = re.sub(r'^\s*@\S+\s*', '', parent_text).strip()
    cleaned = cleaned.replace('"', '').replace("\n", " ")
    if len(cleaned) < MIN_REPLY_ANCHOR_LEN:
        return None
    return cleaned[:MAX_REPLY_ANCHOR_LEN].strip()


def _bidi_navigate(url: str, settle_seconds: float = 2.0, nav_timeout: float = 20.0) -> bool:
    """Direct browser navigation via Firefox BiDi (no address-bar autocomplete)."""
    import json as _json
    import websocket as _ws

    try:
        ws = _ws.create_connection(
            f"ws://localhost:{BIDI_PORT}/session", timeout=3, suppress_origin=True,
        )
    except Exception as e:
        logger.warning("yt bidi navigate connect failed: %s", e)
        return False

    try:
        ws.send(_json.dumps({"id": 1, "method": "session.new", "params": {"capabilities": {}}}))
        if _json.loads(ws.recv()).get("type") != "success":
            return False

        ws.send(_json.dumps({"id": 2, "method": "browsingContext.getTree", "params": {}}))
        contexts = _json.loads(ws.recv()).get("result", {}).get("contexts", [])
        if not contexts:
            return False
        ctx_id = contexts[0]["context"]

        ws.settimeout(nav_timeout)
        ws.send(_json.dumps({
            "id": 3,
            "method": "browsingContext.navigate",
            "params": {"context": ctx_id, "url": url, "wait": "complete"},
        }))
        nav = _json.loads(ws.recv())
        if nav.get("type") == "error":
            logger.warning("yt bidi navigate error: %s", nav.get("message", "")[:200])
            return False

        time.sleep(settle_seconds)
        return True
    except Exception as e:
        logger.warning("yt bidi navigate exception: %s", e)
        return False
    finally:
        try:
            ws.send(_json.dumps({"id": 99, "method": "session.end", "params": {}}))
        except Exception:
            pass
        try:
            ws.close()
        except Exception:
            pass


def _bidi_scroll_to_yt_composer() -> tuple[bool, str, Optional[tuple[int, int]]]:
    """Scroll YouTube's top-level 'Add a comment...' composer into view via BiDi.

    Returns (success, info, (cx, cy) viewport center) — same contract as Reddit's
    _bidi_scroll_to_composer. Prefer #placeholder-area / #contenteditable-root
    inside ytd-comment-simplebox-renderer (not Reply boxes lower on the page).
    """
    import json as _json
    import websocket as _ws

    try:
        ws = _ws.create_connection(
            f"ws://localhost:{BIDI_PORT}/session", timeout=3, suppress_origin=True,
        )
    except Exception as e:
        return False, f"connect failed: {e}", None

    try:
        ws.send(_json.dumps({"id": 1, "method": "session.new", "params": {"capabilities": {}}}))
        if _json.loads(ws.recv()).get("type") != "success":
            return False, "session.new failed", None

        ws.send(_json.dumps({"id": 2, "method": "browsingContext.getTree", "params": {}}))
        contexts = _json.loads(ws.recv()).get("result", {}).get("contexts", [])
        if not contexts:
            return False, "no contexts", None
        ctx_id = contexts[0]["context"]

        js = """
        (() => {
          const auth = /sign in to|Sign in to YouTube/i.test(
            (document.body && document.body.innerText) || ''
          );
          if (auth) return JSON.stringify({found:false, auth:true});

          const selectors = [
            'ytd-comments #placeholder-area',
            'ytd-comment-simplebox-renderer #placeholder-area',
            'ytd-comment-simplebox-renderer #contenteditable-root',
            '#simplebox-placeholder',
            'ytd-comments div[contenteditable="true"]#contenteditable-root',
          ];
          let found = null;
          for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (!el) continue;
            // Skip reply composers nested under existing comments.
            if (el.closest('ytd-comment-replies-renderer') ||
                el.closest('ytd-comment-renderer')) {
              continue;
            }
            found = el;
            break;
          }
          // Fallback: any visible element whose text/placeholder says Add a comment
          if (!found) {
            const all = document.querySelectorAll(
              '#placeholder-area, #simplebox-placeholder, [contenteditable="true"], textarea'
            );
            for (const el of all) {
              if (el.closest('ytd-comment-replies-renderer') ||
                  el.closest('ytd-comment-renderer')) continue;
              const t = ((el.innerText || '') + ' ' +
                         (el.getAttribute('aria-label') || '') + ' ' +
                         (el.getAttribute('placeholder') || '')).toLowerCase();
              if (t.includes('add a comment') || t.includes('add a reply')) {
                if (t.includes('add a reply')) continue;
                found = el;
                break;
              }
            }
          }
          if (!found) {
            return JSON.stringify({
              found: false,
              auth: false,
              has_comments_header: /\\d+\\s*Comments/i.test(
                (document.body && document.body.innerText) || ''
              ),
            });
          }
          found.scrollIntoView({block: 'center', behavior: 'instant'});
          const r = found.getBoundingClientRect();
          return JSON.stringify({
            found: true,
            auth: false,
            tag: found.tagName.toLowerCase(),
            id: found.id || '',
            x: Math.round(r.x), y: Math.round(r.y),
            w: Math.round(r.width), h: Math.round(r.height),
            cx: Math.round(r.x + r.width / 2),
            cy: Math.round(r.y + r.height / 2),
          });
        })()
        """
        ws.send(_json.dumps({
            "id": 3,
            "method": "script.evaluate",
            "params": {"expression": js, "target": {"context": ctx_id}, "awaitPromise": False},
        }))
        result = _json.loads(ws.recv())
        value = result.get("result", {}).get("result", {}).get("value", "")
        if not value:
            return False, "empty script result", None
        data = _json.loads(value)
        if data.get("auth"):
            return False, "auth_required", None
        if not data.get("found"):
            return False, (
                f"composer not in DOM (comments_header={data.get('has_comments_header')})"
            ), None
        time.sleep(0.6)
        cx, cy = int(data.get("cx", 0)), int(data.get("cy", 0))
        if cx <= 0 or cy <= 0:
            return False, f"bad coords ({cx},{cy})", None
        return True, f"composer at ({cx},{cy}) {data.get('w')}x{data.get('h')} #{data.get('id')}", (cx, cy)
    except Exception as e:
        return False, f"exception: {e}", None
    finally:
        try:
            ws.send(_json.dumps({"id": 99, "method": "session.end", "params": {}}))
        except Exception:
            pass
        try:
            ws.close()
        except Exception:
            pass


def _bidi_fill_and_submit_comment(comment_text: str) -> tuple[bool, str]:
    """Expand the top-level composer, insert comment_text, click Comment.

    Pure BiDi/DOM — no vision, no xdotool typing. YouTube's Polymer composer
    accepts execCommand('insertText') on #contenteditable-root after the
    placeholder is clicked.
    """
    import json as _json
    import websocket as _ws

    text = (comment_text or "").strip()
    if not text:
        return False, "empty_comment"

    try:
        ws = _ws.create_connection(
            f"ws://localhost:{BIDI_PORT}/session", timeout=3, suppress_origin=True,
        )
    except Exception as e:
        return False, f"connect failed: {e}"

    try:
        ws.send(_json.dumps({"id": 1, "method": "session.new", "params": {"capabilities": {}}}))
        if _json.loads(ws.recv()).get("type") != "success":
            return False, "session.new failed"

        ws.send(_json.dumps({"id": 2, "method": "browsingContext.getTree", "params": {}}))
        contexts = _json.loads(ws.recv()).get("result", {}).get("contexts", [])
        if not contexts:
            return False, "no contexts"
        ctx_id = contexts[0]["context"]

        def _eval(expr_id: int, expression: str) -> dict:
            ws.send(_json.dumps({
                "id": expr_id, "method": "script.evaluate",
                "params": {
                    "expression": expression,
                    "target": {"context": ctx_id},
                    "awaitPromise": False,
                },
            }))
            raw = _json.loads(ws.recv())
            if raw.get("type") == "error":
                return {"ok": False, "stage": "bidi_error", "msg": raw.get("message", "")[:200]}
            value = raw.get("result", {}).get("result", {}).get("value", "")
            if not value:
                return {"ok": False, "stage": "empty_evaluate"}
            try:
                return _json.loads(value)
            except Exception:
                return {"ok": False, "stage": "bad_json", "raw": str(value)[:200]}

        open_d = _eval(3, """(() => {
          const ph = document.querySelector('ytd-comments #placeholder-area')
            || document.querySelector('#placeholder-area');
          if (!ph) return JSON.stringify({ok:false, stage:'no_placeholder'});
          ph.scrollIntoView({block:'center', behavior:'instant'});
          ph.click();
          return JSON.stringify({ok:true, stage:'clicked_placeholder'});
        })()""")
        if not open_d.get("ok"):
            return False, f"open_failed: {open_d}"

        time.sleep(0.9)

        # Embed comment via json.dumps so quotes/newlines stay valid JS.
        js_fill = f"""(() => {{
          const text = {_json.dumps(text)};
          const box = document.querySelector('ytd-comment-simplebox-renderer')
            || document.querySelector('ytd-comments ytd-commentbox');
          if (!box) return JSON.stringify({{ok:false, stage:'no_simplebox'}});

          let ce = box.querySelector('#contenteditable-root')
            || document.querySelector('ytd-comments #contenteditable-root');
          if (!ce) {{
            const ph = box.querySelector('#placeholder-area')
              || document.querySelector('#placeholder-area');
            if (ph) ph.click();
            ce = box.querySelector('#contenteditable-root')
              || document.querySelector('ytd-comments #contenteditable-root');
          }}
          if (!ce) return JSON.stringify({{ok:false, stage:'no_contenteditable'}});

          ce.focus();
          try {{ document.execCommand('selectAll', false, null); }} catch (e) {{}}
          let inserted = false;
          try {{ inserted = document.execCommand('insertText', false, text); }} catch (e) {{}}
          if (!inserted || !(ce.innerText || '').trim()) {{
            ce.textContent = text;
            ce.dispatchEvent(new InputEvent('input', {{
              bubbles: true, cancelable: true, inputType: 'insertText', data: text,
            }}));
          }}
          const filled = (ce.innerText || '').trim();
          if (!filled) return JSON.stringify({{ok:false, stage:'fill_empty'}});

          let btn = box.querySelector('#submit-button button')
            || box.querySelector('#submit-button yt-button-shape button')
            || box.querySelector('button[aria-label="Comment"]');
          if (!btn) {{
            btn = Array.from(box.querySelectorAll('button')).find(
              b => /^\\s*Comment\\s*$/i.test((b.innerText || '').trim())
            );
          }}
          // A disabled Comment button means YouTube did not register the
          // text; a keyboard submit would not post it either.
          if (btn && btn.disabled) {{
            return JSON.stringify({{
              ok:false, stage:'comment_button_disabled', filled_len: filled.length
            }});
          }}
          if (btn) {{
            btn.click();
            return JSON.stringify({{
              ok:true, stage:'clicked_submit', filled_len: filled.length
            }});
          }}
          // No Comment button found at all: try the Ctrl+Enter shortcut.
          ce.dispatchEvent(new KeyboardEvent('keydown', {{
            key: 'Enter', code: 'Enter', keyCode: 13, which: 13,
            ctrlKey: true, bubbles: true, cancelable: true,
          }}));
          return JSON.stringify({{
            ok: true, stage: 'ctrl_enter', filled_len: filled.length,
          }});
        }})()"""
        fill_d = _eval(4, js_fill)
        if not fill_d.get("ok"):
            return False, f"fill_failed: {fill_d}"
        return True, f"submitted via {fill_d.get('stage')} ({fill_d.get('filled_len')} chars)"
    except Exception as e:
        return False, f"exception: {e}"
    finally:
        try:
            ws.send(_json.dumps({"id": 99, "method": "session.end", "params": {}}))
        except Exception:
            pass
        try:
            ws.close()
        except Exception:
            pass


# Words that mean the comment did not go through. Looked for only in the
# comment box and the toast area, never in the comments themselves.
_YT_SUBMIT_ERROR = re.compile(r"sign in to|something went wrong|try again", re.IGNORECASE)

# Reads the comment section after a submit. Comment bodies are the
# #content-text of the comment renderers (ytd-comment-view-model in the
# current layout, ytd-comment-renderer in the older one); composers are the
# contenteditable roots inside the comment boxes. Text is compared with all
# whitespace removed, so line breaks and emoji images do not break a match.
_YT_VERIFY_JS = r"""(() => {
  const squash = (s) => (s || '').replace(/\s+/g, '');
  const want = squash(__NEEDLE__);
  const wantAscii = squash(__ASCII_NEEDLE__);
  // A few ASCII characters left from a mostly non-ASCII text would match
  // other people's comments.
  const asciiOk = wantAscii.length >= 20;
  const BOX = 'ytd-comment-simplebox-renderer, ytd-commentbox';
  const COMPOSER = '#contenteditable-root, [contenteditable]:not([contenteditable="false"]), textarea';
  const rendered = (el) => el.getClientRects().length > 0;

  const bodies = new Set();
  for (const el of document.querySelectorAll(
      'ytd-comment-view-model #content-text, ytd-comment-renderer #content-text, ytd-comments #content-text')) {
    if (!el.closest(BOX)) bodies.add(el);
  }
  let inComments = false;
  for (const el of bodies) {
    const t = squash(el.textContent);
    if ((want && t.includes(want)) || (asciiOk && t.includes(wantAscii))) { inComments = true; break; }
  }

  // Only composers on screen: a failed submit leaves its box open, while a
  // collapsed box may keep text the poster never typed.
  const composers = new Set();
  for (const box of document.querySelectorAll(BOX)) {
    for (const c of box.querySelectorAll(COMPOSER)) if (rendered(c)) composers.add(c);
  }
  let composerChars = 0;
  for (const c of composers) {
    composerChars += squash(c.tagName === 'TEXTAREA' ? c.value : (c.innerText || c.textContent)).length;
  }

  // Text of the comment boxes outside their composers, plus open toasts.
  const parts = [];
  for (const box of document.querySelectorAll(BOX)) {
    if (!rendered(box) || (box.parentElement && box.parentElement.closest(BOX))) continue;
    const w = document.createTreeWalker(box, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.nodeType === 1 && n.matches(COMPOSER))
        ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    let n;
    while ((n = w.nextNode())) if (n.nodeType === 3) parts.push(n.nodeValue);
  }
  for (const t of document.querySelectorAll('tp-yt-paper-toast, yt-notification-action-renderer')) {
    if (rendered(t)) parts.push(t.textContent || '');
  }

  return JSON.stringify({
    in_comments: inComments,
    comments_seen: bodies.size,
    composers: composers.size,
    composer_chars: composerChars,
    box_text: parts.join(' ').replace(/\s+/g, ' ').trim().slice(0, 2000),
    url: location.href,
  });
})()"""


def _verify_youtube_text_in_dom(comment_text: str) -> tuple[bool, str]:
    """Is ``comment_text`` a posted comment on the open YouTube page?

    Posted means the start of the text is in a rendered comment or reply
    body, every comment-box composer is empty, and neither the comment box
    nor a toast shows an error. Returns ``(posted, reason)``; on failure the
    reason names each check that failed.
    """
    import json as _json

    from backend.services.social_outreach.reddit_outreach import bidi_evaluate_json

    # Also try an ASCII-only needle: emoji and curly quotes in a draft can
    # render differently on the page.
    raw = (comment_text or "").strip()
    needle = raw[:60]
    ascii_needle = re.sub(r"[^\x20-\x7E]", " ", raw)[:50].strip()
    if not needle:
        return False, "empty_needle"
    literals = {"__NEEDLE__": _json.dumps(needle), "__ASCII_NEEDLE__": _json.dumps(ascii_needle)}
    expression = re.sub(r"__(?:ASCII_)?NEEDLE__", lambda m: literals[m.group(0)], _YT_VERIFY_JS)
    d, why = bidi_evaluate_json(expression)
    if d is None:
        return False, f"page_not_readable: {why}"

    seen = int(d.get("comments_seen") or 0)
    failed = []
    if not d.get("in_comments"):
        failed.append(f"not_in_comment_bodies (comments_seen={seen})")
    composer_chars = int(d.get("composer_chars") or 0)
    if composer_chars:
        failed.append(f"composer_not_empty ({composer_chars} chars left)")
    error = _YT_SUBMIT_ERROR.search(d.get("box_text") or "")
    if error:
        failed.append(f"error_in_comment_box ({error.group(0)!r})")
    url = str(d.get("url") or "")[:200]
    if failed:
        return False, f"{'; '.join(failed)} url={url}"
    return True, f"in a comment body (comments_seen={seen}) url={url}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def post_youtube_comment_via_servo(
    target_url: str,
    comment_text: str,
    task_id: Optional[int] = None,
    *,
    before_submit: Optional[Callable[[], bool]] = None,
) -> tuple[bool, str]:
    """Navigate to target_url, post comment_text as a top-level comment.

    Returns (success, reason). Reasons on failure:
      - "invalid_url"        — URL isn't a YouTube watch URL
      - "agent_busy"         — agent service is already executing a task
      - "display_unavailable"— Xvfb on :99 isn't reachable
      - "auth_required"      — YouTube sign-in interstitial appeared
      - "navigate_failed: …"
      - "composer_not_found: …"
      - "submit_unverified: …"

    ``before_submit`` is called immediately before the step that publishes;
    when it returns False nothing is published and the reason is
    ``transitions.WITHDRAWN_BEFORE_SUBMIT``.
    """
    from backend.services.agent_control_service import get_agent_control_service
    from backend.services.local_screen_backend import LocalScreenBackend
    from backend.utils.agent_display_utils import start_agent_display_if_needed

    normalized = _normalize_youtube_url(target_url)
    if not normalized:
        return False, "invalid_url"
    target_url = normalized

    service = get_agent_control_service()
    if service.is_active:
        return False, "agent_busy"
    if not start_agent_display_if_needed():
        logger.warning("display not available for outreach: start failed")
        return False, "display_unavailable"
    try:
        screen = LocalScreenBackend()
    except Exception as e:
        logger.warning("display not available for outreach: %s", e)
        return False, "display_unavailable"

    # 1) BiDi navigate — deterministic, no address-bar autocomplete.
    if not _bidi_navigate(target_url, settle_seconds=SERVO_SETTLE_SECONDS):
        return False, "navigate_failed: bidi_navigate returned False"

    # 2) Close leftover find-bar / overlays so they don't steal focus.
    screen.hotkey("Escape")
    time.sleep(0.3)
    screen.click(500, 720)
    time.sleep(0.2)
    # Lazy-load the comments section (placeholder isn't in DOM until scrolled).
    for _ in range(6):
        screen.hotkey("Page_Down")
        time.sleep(0.35)
    time.sleep(1.0)

    # 3) Confirm composer exists (also surfaces auth_required).
    scrolled, info, coords = _bidi_scroll_to_yt_composer()
    logger.warning(
        "yt bidi scroll-to-composer: success=%s info=%s coords=%s task_id=%s",
        scrolled, info, coords, task_id,
    )
    if info == "auth_required":
        return False, "auth_required"
    if not scrolled:
        for _ in range(4):
            screen.hotkey("Page_Down")
            time.sleep(0.4)
        time.sleep(1.0)
        scrolled, info, coords = _bidi_scroll_to_yt_composer()
        if info == "auth_required":
            return False, "auth_required"
        if not scrolled:
            return False, f"composer_not_found: {info}"

    # 4) Fill + submit entirely via BiDi/DOM (xdotool paste was landing off-target).
    if before_submit is not None and not before_submit():
        return False, WITHDRAWN_BEFORE_SUBMIT
    ok, fill_msg = _bidi_fill_and_submit_comment(comment_text)
    logger.warning("yt bidi fill/submit: ok=%s msg=%s task_id=%s", ok, fill_msg, task_id)
    if not ok:
        return False, f"fill_submit_failed: {fill_msg}"
    time.sleep(2.5)

    verified, verify_msg = _verify_youtube_text_in_dom(comment_text)
    if not verified:
        time.sleep(3.0)
        verified, verify_msg = _verify_youtube_text_in_dom(comment_text)
    if not verified:
        return False, f"submit_unverified: {verify_msg}"

    return True, "ok"


def post_youtube_reply_via_servo(
    target_url: str,
    parent_comment_match_text: str,
    reply_text: str,
    task_id: Optional[int] = None,
    *,
    before_submit: Optional[Callable[[], bool]] = None,
) -> tuple[bool, str]:
    """Navigate to target_url, find the parent comment by text substring,
    open its Reply composer, post reply_text under it.

    Reply path still uses the recipe chain (parent-comment find is unique to
    find_on_page). Top-level comments use the BiDi path above.

    ``before_submit`` is called immediately before the step that publishes;
    when it returns False nothing is published and the reason is
    ``transitions.WITHDRAWN_BEFORE_SUBMIT``.
    """
    from backend.services.agent_control_service import get_agent_control_service
    from backend.services.local_screen_backend import LocalScreenBackend
    from backend.utils.agent_display_utils import start_agent_display_if_needed

    normalized = _normalize_youtube_url(target_url)
    if not normalized:
        return False, "invalid_url"
    target_url = normalized

    anchor = _trim_anchor(parent_comment_match_text or "")
    if not anchor:
        return False, "invalid_parent_anchor"

    service = get_agent_control_service()
    if service.is_active:
        return False, "agent_busy"
    if not start_agent_display_if_needed():
        return False, "display_unavailable"
    try:
        screen = LocalScreenBackend()
    except Exception as e:
        logger.warning("display not available for outreach: %s", e)
        return False, "display_unavailable"

    if not _bidi_navigate(target_url, settle_seconds=SERVO_SETTLE_SECONDS):
        return False, "navigate_failed: bidi_navigate returned False"

    screen.hotkey("Escape")
    time.sleep(0.3)

    for chat_msg, tag, settle in (
        ("pause the video",                              "pause_failed",          1.0),
        (f'find "{anchor}" on the page',                 "find_parent_failed",    1.0),
        ("press escape",                                 "escape_failed",         0.4),
        ("click the reply button under this comment",    "open_reply_failed",     SERVO_SETTLE_SECONDS),
        ("click the reply comment field",                "focus_reply_failed",    SERVO_SETTLE_SECONDS),
    ):
        ok, reason = _run_recipe_step(service, screen, chat_msg, tag)
        if not ok:
            logger.warning(
                "youtube reply chain aborted at %s (task_id=%s): %s",
                tag, task_id, reason,
            )
            return False, reason
        time.sleep(settle)

    from backend.services.social_outreach import typing_rhythm

    screen.type_text(reply_text, key_delays=typing_rhythm.delays(reply_text))
    _human_pause()

    if before_submit is not None and not before_submit():
        return False, WITHDRAWN_BEFORE_SUBMIT
    ok, reason = _run_recipe_step(service, screen, "send the comment", "submit_failed")
    if not ok:
        return False, reason

    verified, verify_msg = _verify_youtube_text_in_dom(reply_text)
    if not verified:
        return False, f"submit_unverified: {verify_msg}"

    return True, "ok"
