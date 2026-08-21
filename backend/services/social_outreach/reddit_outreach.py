"""
Reddit outreach loop — finds relevant threads, drafts a comment via the LLM,
and posts it through the servo-driven Firefox on DISPLAY=:99 (which has the
user's logged-in Reddit session cookies).

Hybrid architecture:
  • READ via Reddit's JSON API, authenticated with cookies from the agent
    Firefox profile when present (bare datacenter IPs often get 403).
  • WRITE via BiDi + LocalScreenBackend on the real Firefox profile
    (the only path with the user's login cookies for posting).

If write fails twice we abort the whole pass — better to skip than thrash.
"""

from __future__ import annotations

import logging
import os
import random
import re
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import requests

from backend.services.social_outreach import audit, kill_switch, persona

logger = logging.getLogger(__name__)


REDDIT_USER_AGENT = "guaardvark-outreach/0.1 (by /u/guaardvark) - local AI workstation"
# Browser UA used when attaching the agent Firefox session cookies. Reddit's
# datacenter IP block (403 HTML interstitial) often still allows cookie-authed
# requests that look like a real logged-in browser.
REDDIT_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
)
REDDIT_BASE = "https://www.reddit.com"
HTTP_TIMEOUT = 10
SUBREDDIT_HOT_LIMIT = 10
THREAD_COMMENT_LIMIT = 10
MAX_THREADS_PER_PASS = 2
SERVO_SETTLE_SECONDS = 4
BIDI_PORT = 9222

# Cached cookie jar loaded from the agent Firefox profile (refreshed periodically).
_REDDIT_COOKIE_CACHE: dict = {"jar": None, "loaded_at": 0.0}
_REDDIT_COOKIE_TTL_S = 120.0


def _bidi_scroll_to_composer() -> tuple[bool, str, Optional[tuple[int, int]]]:
    """Use BiDi to scroll Reddit's 'Join the conversation' composer into
    view. Returns (success, info_message, (cx, cy) center coords or None).

    Why BiDi instead of xdotool wheel-click: on Xvfb, scroll-wheel events
    via `xdotool click 5` don't reliably propagate to the page body
    (delta=0.000 across multiple attempts even when cursor is over
    the page). `el.scrollIntoView()` runs in the page's JS context and
    deterministically positions the element in the viewport, regardless
    of which element has keyboard focus or which window has the cursor.

    This is NOT a vision bypass — vision still has to click the textarea
    after we scroll it into view. We're just making sure the textarea
    is on-screen so vision can see it.
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
            return False, "session.new failed (probably leak)", None

        ws.send(_json.dumps({"id": 2, "method": "browsingContext.getTree", "params": {}}))
        contexts = _json.loads(ws.recv()).get("result", {}).get("contexts", [])
        if not contexts:
            return False, "no contexts", None
        ctx_id = contexts[0]["context"]

        # Walk shadow DOMs too — Reddit's faceplate-textarea wraps a
        # contenteditable in shadow root that querySelectorAll alone misses.
        js = """
        (() => {
          // Reddit ships hidden/template faceplate-textarea instances with
          // 0x0 bounding rects — those are useless for scrollIntoView and
          // for the agent's vision. Pick the FIRST visible composer with
          // non-zero dimensions. Also accept elements whose own rect is
          // 0x0 but whose parent shreddit-composer has a real rect.
          let found = null;
          let foundRect = null;
          const candidates = [];
          const visit = (root) => {
            const els = root.querySelectorAll('faceplate-textarea, faceplate-textarea-input, textarea, div[contenteditable], shreddit-composer');
            for (const el of els) {
              const ph = (el.getAttribute && el.getAttribute('placeholder')) || '';
              const al = (el.getAttribute && el.getAttribute('aria-label')) || '';
              if (/join the conversation|add a comment/i.test(ph + ' ' + al)) {
                candidates.push(el);
              }
              if (el.shadowRoot) visit(el.shadowRoot);
            }
            const all = root.querySelectorAll('*');
            for (const el of all) {
              if (el.shadowRoot) visit(el.shadowRoot);
            }
          };
          visit(document);
          // Pick the first candidate (or its closest ancestor) that has
          // a non-zero rect.
          for (const c of candidates) {
            let probe = c;
            while (probe) {
              const r = probe.getBoundingClientRect();
              if (r.width >= 30 && r.height >= 20) {
                found = probe;
                foundRect = r;
                break;
              }
              probe = probe.parentElement;
            }
            if (found) break;
          }
          if (!found) return JSON.stringify({found:false, candidates: candidates.length});
          found.scrollIntoView({block:'center', behavior:'instant'});
          // Re-read rect after scroll so we report post-scroll viewport coords.
          const r = found.getBoundingClientRect();
          return JSON.stringify({
            found: true,
            tag: found.tagName.toLowerCase(),
            x: Math.round(r.x), y: Math.round(r.y),
            w: Math.round(r.width), h: Math.round(r.height),
            cx: Math.round(r.x + r.width/2),
            cy: Math.round(r.y + r.height/2),
            candidates: candidates.length
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
        if not data.get("found"):
            return False, f"composer not in DOM (candidates={data.get('candidates', 0)})", None
        time.sleep(0.6)  # let scrollIntoView animation settle
        cx, cy = int(data.get("cx", 0)), int(data.get("cy", 0))
        return True, f"composer at ({cx},{cy}) {data.get('w')}x{data.get('h')}", (cx, cy)
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


def _bidi_navigate(url: str, settle_seconds: float = 2.0, nav_timeout: float = 15.0) -> bool:
    """Direct browser navigation via Firefox BiDi protocol.

    Bypasses the address bar entirely (no autocomplete, no history shadow,
    no race with the search input). Returns True if the navigate event
    fires cleanly, False if BiDi can't reach Firefox or the navigate
    response is an error.

    See dom_metadata_extractor.py for the larger version with extractor
    payload — this stripped-down twin is the one Phase 3 uses to land on
    a thread URL deterministically before the see-think-act loop takes over.
    """
    import json as _json
    import websocket as _ws

    try:
        ws = _ws.create_connection(
            f"ws://localhost:{BIDI_PORT}/session", timeout=3, suppress_origin=True,
        )
    except Exception as e:
        logger.warning("bidi navigate connect failed: %s", e)
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
            logger.warning("bidi navigate error: %s", nav.get("message", "")[:200])
            return False

        time.sleep(settle_seconds)
        return True
    except Exception as e:
        logger.warning("bidi navigate exception: %s", e)
        return False
    finally:
        # Always end the BiDi session — Firefox caps "Maximum number of
        # active sessions" and silently fails session.new once that cap
        # hits. Without this finally, repeated runs leak sessions and
        # the third call onward returns "Maximum number of active sessions".
        try:
            ws.send(_json.dumps({"id": 99, "method": "session.end", "params": {}}))
        except Exception:
            pass
        try:
            ws.close()
        except Exception:
            pass


def _human_pause(min_s: float = 0.3, max_s: float = 2.0) -> None:
    """Random sleep to avoid deterministic bot timing fingerprints.
    
    Don't make this call site-specific — uniform jitter across all servo
    actions is fine. Cross-platform spam filters look for *constant* delays
    much more than for specific values.
    """
    time.sleep(random.uniform(min_s, max_s))


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


def _agent_firefox_cookies_db() -> Optional[str]:
    """Return path to the agent Firefox cookies.sqlite, or None if missing."""
    try:
        from backend.utils.agent_display_utils import get_firefox_profile_path
        path = os.path.join(get_firefox_profile_path(), "cookies.sqlite")
    except Exception:
        return None
    return path if os.path.isfile(path) else None


def _load_reddit_cookies_from_firefox() -> Optional[requests.cookies.RequestsCookieJar]:
    """Load Reddit cookies from the agent Firefox profile.

    Copies the SQLite DB first so a live Firefox lock doesn't block us.
    Returns None when the profile has no Reddit session (caller falls back
    to unauthenticated fetch).
    """
    now = time.time()
    cached = _REDDIT_COOKIE_CACHE.get("jar")
    if cached is not None and (now - float(_REDDIT_COOKIE_CACHE.get("loaded_at") or 0)) < _REDDIT_COOKIE_TTL_S:
        return cached

    db_path = _agent_firefox_cookies_db()
    if not db_path:
        return None

    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="reddit_ff_cookies_")
        tmp_db = os.path.join(tmp_dir, "cookies.sqlite")
        shutil.copy2(db_path, tmp_db)
        for suffix in ("-wal", "-shm"):
            side = db_path + suffix
            if os.path.isfile(side):
                try:
                    shutil.copy2(side, tmp_db + suffix)
                except OSError:
                    pass

        conn = sqlite3.connect(tmp_db)
        try:
            rows = conn.execute(
                "SELECT name, value, host FROM moz_cookies "
                "WHERE host LIKE '%reddit.com%'"
            ).fetchall()
        finally:
            conn.close()

        jar = requests.cookies.RequestsCookieJar()
        has_session = False
        for name, value, host in rows:
            if not name or value is None:
                continue
            if name in ("reddit_session", "token_v2"):
                has_session = True
            domain = host if host.startswith(".") else host
            jar.set(name, value, domain=domain, path="/")

        if not has_session:
            logger.info("agent Firefox profile has Reddit cookies but no session token")
            _REDDIT_COOKIE_CACHE["jar"] = None
            _REDDIT_COOKIE_CACHE["loaded_at"] = now
            return None

        _REDDIT_COOKIE_CACHE["jar"] = jar
        _REDDIT_COOKIE_CACHE["loaded_at"] = now
        return jar
    except Exception as e:
        logger.warning("failed to load Reddit cookies from Firefox profile: %s", e)
        return None
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _http_get_json(url: str, retries: int = 1) -> Optional[dict]:
    """GET Reddit JSON. Prefer agent-Firefox session cookies when available —

    bare datacenter IPs often get a 403 interstitial; the logged-in cookie jar
    from DISPLAY=:99's profile clears that for discovery (hot/rules/comments).
    """
    cookie_jar = _load_reddit_cookies_from_firefox()
    headers = {
        "User-Agent": REDDIT_BROWSER_USER_AGENT if cookie_jar else REDDIT_USER_AGENT,
        "Accept": "application/json",
    }
    try:
        resp = requests.get(
            url,
            headers=headers,
            cookies=cookie_jar,
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
        # 403 with cookies: invalidate cache once and retry bare (or vice versa)
        if resp.status_code == 403 and cookie_jar is not None and retries > 0:
            logger.warning("reddit JSON 403 with session cookies on %s — refreshing jar once", url)
            _REDDIT_COOKIE_CACHE["jar"] = None
            _REDDIT_COOKIE_CACHE["loaded_at"] = 0.0
            return _http_get_json(url, retries=retries - 1)
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


def record_post_via_backend(
    audit_id: Optional[int],
    permalink: str,
    thread_id: str,
    posted_text: str,
    task_id: Optional[int],
    platform: str = "reddit",
) -> None:
    try:
        requests.post(
            f"{backend_url()}/social-outreach/record-post",
            json={
                "audit_id": audit_id,
                "platform": platform or "reddit",
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

    Strategy: navigate to the thread on www.reddit.com (modern UI matches
    vision model training distribution), then hand the see-think-act loop a
    precise instruction. The agent's vision model finds the comment box,
    clicks, types, and submits.

    On failure: ServoController records success=False; we treat that as servo
    failure and abort.
    """
    from backend.services.agent_control_service import get_agent_control_service
    from backend.services.local_screen_backend import LocalScreenBackend
    from backend.utils.agent_display_utils import start_agent_display_if_needed

    # www.reddit.com — modern UI; vision model finds the comment box visually.
    from urllib.parse import urlparse, urlunparse

    from backend.utils.hosts import host_matches

    _parsed = urlparse(permalink)
    target_url = (
        urlunparse(_parsed._replace(scheme="https", netloc="www.reddit.com"))
        if host_matches(_parsed.hostname, "reddit.com")
        else permalink
    )

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

    # Step 1: navigate via BiDi, NOT via the navigate_url recipe.
    #
    # Why: the recipe types the URL into the address bar via Ctrl+L, but
    # Firefox autocomplete intercepts and lands on a previously visited
    # URL from history. In the 2026-05-09 demo this routinely sent the
    # agent to /r/{sub}/submit/?type=TEXT (a Create Post page) instead
    # of the comments thread, because the leader had visited /submit
    # while testing earlier. The recipe's `Delete` step is supposed to
    # dismiss the autocomplete suggestion but it's racy.
    #
    # Direct BiDi `browsingContext.navigate` is the browser's native API:
    # no address bar, no autocomplete, no history shadow. Cursor + Gemini
    # both flagged this independently as the actual root cause when
    # given the full session transcript.
    if not _bidi_navigate(target_url, settle_seconds=SERVO_SETTLE_SECONDS):
        return False, "navigate_failed: bidi_navigate returned False"

    # Drop keyboard focus from Reddit's permalink-load search bar. Escape
    # alone doesn't always stick (Reddit's SPA re-focuses search on
    # hydration), so we follow up with BiDi scrollIntoView to position
    # the comment composer mid-viewport.
    screen.hotkey("Escape")
    time.sleep(0.3)

    # Find + scroll the composer into view via BiDi. Returns the textarea's
    # center coords. Without this, the see-think-act loop wastes its
    # iteration budget — vision can't reliably distinguish the comment
    # composer from Reddit's search bar (both are wide white inputs)
    # and clicks on the search bar instead, then can't type, then loops.
    scrolled, info, coords = _bidi_scroll_to_composer()
    logger.warning("bidi scroll-to-composer: success=%s info=%s coords=%s",
                   scrolled, info, coords)
    if not scrolled or not coords:
        return False, f"composer_not_found: {info}"
    time.sleep(0.5)

    # Human-like interaction: click the composer, pause, and type the comment.
    # screen.type_text is robust against Reddit's single-key shortcuts because
    # it uses clipboard paste for longer texts.
    cx, cy = coords
    logger.warning("clicking composer at (%s, %s)", cx, cy)
    screen.click(cx, cy)
    _human_pause(0.5, 1.0)

    logger.warning("typing comment (%s chars)", len(comment_text))
    screen.type_text(comment_text)
    time.sleep(1.0)
    _human_pause()

    # Submit via Reddit's standard Ctrl+Enter shortcut. The textarea is
    # already focused from the click and type, so this keystroke routes to
    # the right element. Reddit interprets Ctrl+Enter as form-submit
    # for comment composers.
    logger.warning("submitting comment via Ctrl+Enter")
    screen.hotkey("ctrl", "Return")
    time.sleep(SERVO_SETTLE_SECONDS)

    # Verify the comment actually posted — look for the comment text
    # appearing in the thread's comment list. Anything else (URL still
    # /comments/, no error visible, etc.) is too weak: previous attempt
    # reported success when the textarea was actually empty and Reddit
    # showed "field is required" inline. Real check: search the DOM for
    # the first 60 chars of our comment text appearing in a comment-tree
    # element.
    import json as _json
    import websocket as _ws2
    posted = False
    verify_msg = "verify failed"
    needle = comment_text[:60].strip()
    try:
        ws = _ws2.create_connection(f"ws://localhost:{BIDI_PORT}/session", timeout=3, suppress_origin=True)
        ws.send(_json.dumps({"id": 1, "method": "session.new", "params": {"capabilities": {}}}))
        if _json.loads(ws.recv()).get("type") == "success":
            ws.send(_json.dumps({"id": 2, "method": "browsingContext.getTree", "params": {}}))
            ctxs = _json.loads(ws.recv()).get("result", {}).get("contexts", [])
            if ctxs:
                ctx_id = ctxs[0]["context"]
                # Look for needle in any rendered comment OR for the
                # composer being empty (no error message and no value)
                # which also implies a successful post.
                check_js = (
                    "(() => {"
                    "  const needle = " + _json.dumps(needle) + ";"
                    "  const url = location.href;"
                    "  // 1) Primary: needle appears in any element on the page"
                    "  //    that smells like a comment body."
                    "  const sels = ['[data-testid=\"comment\"]', 'shreddit-comment', '[id^=\"comment-tree-content-anchor\"]', 'div[role=\"region\"]'];"
                    "  let foundInThread = false;"
                    "  for (const s of sels) {"
                    "    const els = document.querySelectorAll(s);"
                    "    for (const el of els) {"
                    "      if ((el.textContent || '').includes(needle)) { foundInThread = true; break; }"
                    "    }"
                    "    if (foundInThread) break;"
                    "  }"
                    "  // 2) Secondary: composer is empty AND no error message."
                    "  let composerEmpty = false;"
                    "  let errorVisible = false;"
                    "  const composers = document.querySelectorAll('faceplate-textarea-input, textarea');"
                    "  for (const c of composers) {"
                    "    const ph = (c.getAttribute && c.getAttribute('placeholder')) || '';"
                    "    if (/join the conversation|add a comment/i.test(ph)) {"
                    "      composerEmpty = !((c.value || c.innerText || '').trim());"
                    "      break;"
                    "    }"
                    "  }"
                    "  const errEls = document.querySelectorAll('*');"
                    "  for (const e of errEls) {"
                    "    const t = (e.textContent || '');"
                    "    if (/field is required|cannot be empty|something went wrong|too fast/i.test(t)) { errorVisible = true; break; }"
                    "  }"
                    "  return JSON.stringify({foundInThread, composerEmpty, errorVisible, url});"
                    "})()"
                )
                ws.send(_json.dumps({
                    "id": 3, "method": "script.evaluate",
                    "params": {"expression": check_js, "target": {"context": ctx_id}, "awaitPromise": False},
                }))
                v = _json.loads(ws.recv()).get("result", {}).get("result", {}).get("value", "")
                if v:
                    d = _json.loads(v)
                    posted = d.get("foundInThread", False) and not d.get("errorVisible", False)
                    verify_msg = (
                        f"foundInThread={d.get('foundInThread')} "
                        f"composerEmpty={d.get('composerEmpty')} "
                        f"errorVisible={d.get('errorVisible')} "
                        f"url={d.get('url')}"
                    )
        try:
            ws.send(_json.dumps({"id": 99, "method": "session.end", "params": {}}))
        except Exception:
            pass
        ws.close()
    except Exception as e:
        verify_msg = f"verify exception: {e}"
    logger.warning("post-submit verify: posted=%s %s", posted, verify_msg)

    if not posted:
        return False, f"submit_failed: {verify_msg}"
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

            # Draft-only: posting is exclusively via tick_process_approved_drafts
            # (cadence + claim). When unsupervised, /draft-comment already set
            # status=approved for would_post drafts so the next beat posts them.
            if draft_result.get("would_post"):
                report["queued_for_post"] = report.get("queued_for_post", 0) + 1

        return report
