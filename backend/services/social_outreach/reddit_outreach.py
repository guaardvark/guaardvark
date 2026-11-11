"""
Reddit outreach loop — finds relevant threads, drafts a comment via the LLM,
and posts it through the servo-driven Firefox on DISPLAY=:99 (which has the
user's logged-in Reddit session cookies).

Hybrid architecture:
  • READ via Reddit's JSON API, authenticated with cookies from the agent
    Firefox profile when present. Where Reddit refuses the direct request
    (403), a read inside ``reddit_reading()`` goes through the agent Firefox
    instead, as a fetch from a reddit.com page.
  • WRITE via BiDi + LocalScreenBackend on the real Firefox profile
    (the only path with the user's login cookies for posting).

If write fails twice we abort the whole pass — better to skip than thrash.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import random
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import quote, urlencode

import requests

from backend.services.social_outreach import audit, external_grader, kill_switch, persona
from backend.services.social_outreach.transitions import WITHDRAWN_BEFORE_SUBMIT
from backend.utils.settings_utils import web_access_block_reason

logger = logging.getLogger(__name__)



def reddit_user_agent() -> str:
    """Reddit asks for a UA that names the software and the account behind it;
    the account comes from the outreach profile and is left out when unset."""
    from backend.services.social_outreach import profile
    from backend.utils.update_state import read_disk_version
    by = profile.load().account_handle
    by_part = f" (by /u/{by})" if by else ""
    return f"guaardvark-outreach/{read_disk_version() or '0'}{by_part} - local AI workstation"


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
    view. Returns (success, info_message, (x, y) of its centre in screen
    pixels, or None). The page reports positions inside the browser's
    viewport; the toolbars above it (about 110 px on the agent display) are
    added back, or the click lands above the box.

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
          // A candidate that is itself on screen wins: the hidden copies
          // Reddit keeps earlier in the page climb to a parent that holds
          // the vote and share row, not the box.
          const sized = (el) => { const r = el.getBoundingClientRect(); return r.width >= 30 && r.height >= 20; };
          found = candidates.find(sized) || null;
          // Otherwise the first candidate's closest ancestor with a size.
          for (const c of (found ? [] : candidates)) {
            let probe = c;
            while (probe) {
              if (sized(probe)) {
                found = probe;
                break;
              }
              probe = probe.parentElement;
            }
            if (found) break;
          }
          if (!found) return JSON.stringify({found:false, candidates: candidates.length});
          found.scrollIntoView({block:'center', behavior:'instant'});
          // Re-read rect after scroll; report the centre in screen pixels.
          const r = found.getBoundingClientRect();
          const dpr = window.devicePixelRatio || 1;
          return JSON.stringify({
            found: true,
            tag: found.tagName.toLowerCase(),
            x: Math.round(r.x), y: Math.round(r.y),
            w: Math.round(r.width), h: Math.round(r.height),
            cx: Math.round((window.mozInnerScreenX + r.x + r.width/2) * dpr),
            cy: Math.round((window.mozInnerScreenY + r.y + r.height/2) * dpr),
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


def bidi_evaluate_json(
    expression: str, await_promise: bool = False, timeout: float = 3.0,
) -> tuple[Optional[dict], str]:
    """Run ``expression`` in the agent Firefox's first tab and parse the JSON
    string it returns. ``await_promise`` waits for an async expression, up to
    ``timeout`` seconds.

    Returns ``(data, "")``, or ``(None, why)`` when the browser cannot be
    reached or the script returns nothing usable. The posters use it to read
    the page after a submit. The session is always ended: Firefox caps active
    BiDi sessions and refuses new ones once the cap is reached.
    """
    import json as _json
    import websocket as _ws

    try:
        ws = _ws.create_connection(
            f"ws://localhost:{BIDI_PORT}/session", timeout=3, suppress_origin=True,
        )
    except Exception as e:
        return None, f"connect failed: {e}"

    try:
        ws.send(_json.dumps({"id": 1, "method": "session.new", "params": {"capabilities": {}}}))
        if _json.loads(ws.recv()).get("type") != "success":
            return None, "session.new failed"
        ws.send(_json.dumps({"id": 2, "method": "browsingContext.getTree", "params": {}}))
        contexts = _json.loads(ws.recv()).get("result", {}).get("contexts", [])
        if not contexts:
            return None, "no browsing context"
        ws.send(_json.dumps({
            "id": 3, "method": "script.evaluate",
            "params": {
                "expression": expression,
                "target": {"context": contexts[0]["context"]},
                "awaitPromise": bool(await_promise),
            },
        }))
        ws.settimeout(timeout)
        raw = _json.loads(ws.recv())
        if raw.get("type") == "error":
            return None, f"evaluate error: {raw.get('message', '')[:200]}"
        value = raw.get("result", {}).get("result", {}).get("value", "")
        if not value:
            return None, "empty evaluate result"
        data = _json.loads(value)
        if not isinstance(data, dict):
            return None, "evaluate result is not an object"
        return data, ""
    except Exception as e:
        return None, f"exception: {e}"
    finally:
        try:
            ws.send(_json.dumps({"id": 99, "method": "session.end", "params": {}}))
        except Exception:
            pass
        try:
            ws.close()
        except Exception:
            pass



# Reason a poster gives when it refuses because the page could not be read back.
PAGE_CHECK_UNAVAILABLE = "page_check_unavailable"
# bidi_evaluate_json reasons given after the browser answered the session.
_BIDI_ANSWERED = ("evaluate error", "empty evaluate result", "evaluate result is not an object")


def bidi_reachable(wait_s: float = 10.0) -> tuple[bool, str]:
    """Whether the agent Firefox answers on its BiDi port; ``(ok, why)``.

    Posters that confirm a submit by reading the page check this before they
    touch anything: without the port a post that did go out is recorded as a
    failure, and re-approving the draft posts it again. Firefox opens the port
    only when launched with GUAARDVARK_AGENT_CDP=1 (scripts/agent_firefox_launch.sh).
    ``wait_s`` covers a Firefox the display start has just launched.
    """
    deadline = time.monotonic() + wait_s
    while True:
        data, why = bidi_evaluate_json("JSON.stringify({ok: true})")
        # A fresh Firefox opens on a built-in page that refuses scripts
        # ("System access is required"); the session still answered, and the
        # poster navigates away from that page before reading anything.
        if data is not None or why.startswith(_BIDI_ANSWERED):
            return True, ""
        if time.monotonic() >= deadline:
            return False, why
        time.sleep(1.0)


def page_check_unavailable_reason(why: str) -> str:
    return (
        f"{PAGE_CHECK_UNAVAILABLE}: nothing was posted; the agent browser's control port "
        f"is closed, so the post could not be confirmed ({why})"
    )

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


# How long one read through the agent browser may take.
BROWSER_FETCH_TIMEOUT_S = 20.0
# Reddit's own page the browser sits on while it fetches: small, and the same
# origin as every JSON address read here.
_BROWSER_READ_PAGE = f"{REDDIT_BASE}/robots.txt"

# A thread link in Reddit's pages: /r/<subreddit>/comments/<id>/. JavaScript
# source, run in the browser against the search page.
_THREAD_LINK_JS = r"/\/r\/([A-Za-z0-9_]{2,21})\/comments\/([a-z0-9]{4,12})\//g"
SEARCH_RESULT_LIMIT = 10
# How far back a search looks: Reddit's own "t" values.
SEARCH_WINDOW = "week"

_reading = threading.local()


class _BrowserReads:
    """Reddit reads through the agent Firefox for one ``reddit_reading()`` body.

    The browser is opened with its control port on the first read Reddit
    refuses, and put back when the body ends.
    """

    def __init__(self) -> None:
        self.use_browser = False
        self.problem: Optional[str] = None
        self._stack: Optional[contextlib.ExitStack] = None
        self._ready = False
        # A thread's JSON carries both the post and its comments; one read
        # serves fetch_thread and fetch_thread_comments.
        self._answers: dict[str, object] = {}

    def _open(self) -> None:
        from backend.utils.agent_browser_control import agent_browser_control

        self._stack = contextlib.ExitStack()
        refusal = self._stack.enter_context(agent_browser_control())
        if refusal:
            self.problem = f"the agent browser is in use ({refusal})"
            return
        ok, why = bidi_reachable(wait_s=0)
        if not ok:
            self.problem = f"the agent browser did not open ({why})"
            return
        if not _bidi_navigate(_BROWSER_READ_PAGE):
            self.problem = "the agent browser could not open reddit.com"
            return
        self._ready = True

    def _evaluate(self, expression: str) -> Optional[dict]:
        self.use_browser = True
        if self._stack is None:
            self._open()
        if not self._ready:
            return None
        _human_pause(0.3, 1.2)
        answer, why = bidi_evaluate_json(
            expression, await_promise=True, timeout=BROWSER_FETCH_TIMEOUT_S,
        )
        if answer is None:
            self.problem = f"the agent browser did not answer ({why})"
        return answer

    def search_links(self, url: str) -> list[tuple[str, str]]:
        """(subreddit, thread id) for each thread Reddit's search page at
        ``url`` lists, in page order."""
        expression = (
            "(async () => { try {"
            f" const r = await fetch({json.dumps(url)}, {{credentials: 'include'}});"
            " const page = await r.text(); const seen = new Set(); const found = [];"
            " for (const m of page.matchAll(" + _THREAD_LINK_JS + ")) {"
            "   if (!seen.has(m[2])) { seen.add(m[2]); found.push([m[1], m[2]]); } }"
            " return JSON.stringify({status: r.status, found});"
            " } catch (e) { return JSON.stringify({status: 0, found: []}); } })()"
        )
        answer = self._evaluate(expression)
        if answer is None:
            return []
        if answer.get("status") != 200:
            self.problem = f"Reddit answered {answer.get('status')} to the search in the agent browser"
            return []
        self.problem = None
        return [(str(sub), str(tid)) for sub, tid in answer.get("found") or []]

    def get_json(self, url: str) -> Optional[dict]:
        if url in self._answers:
            return self._answers[url]
        expression = (
            "(async () => { try {"
            f" const r = await fetch({json.dumps(url)}, "
            "{credentials: 'include', headers: {Accept: 'application/json'}});"
            " return JSON.stringify({status: r.status, body: await r.text()});"
            " } catch (e) { return JSON.stringify({status: 0, body: String(e)}); } })()"
        )
        answer = self._evaluate(expression)
        if answer is None:
            return None
        if answer.get("status") != 200:
            self.problem = f"Reddit answered {answer.get('status')} in the agent browser"
            logger.warning("reddit browser read of %s: status %s", url, answer.get("status"))
            return None
        try:
            data = json.loads(answer.get("body") or "")
        except ValueError:
            self.problem = "Reddit answered the agent browser with a page instead of data"
            return None
        self.problem = None
        self._answers[url] = data
        return data

    def close(self) -> None:
        if self._stack is not None:
            self._stack.close()


@contextlib.contextmanager
def reddit_reading():
    """Let the Reddit reads in the body go through the agent browser when
    Reddit refuses the direct request.

    The browser is restarted with its control port only if a read is refused,
    and is left alone while the agent runs a task
    (``agent_browser_control``). ``reading_problem()`` then says why a read
    came back empty. Nested use shares the outer body's browser.
    """
    if getattr(_reading, "session", None) is not None:
        yield
        return
    session = _BrowserReads()
    _reading.session = session
    _reading.problem = None
    try:
        yield
    finally:
        _reading.problem = session.problem
        _reading.session = None
        session.close()


def reading_problem() -> Optional[str]:
    """Why the agent browser could not read Reddit in the current or the last
    ``reddit_reading()`` body on this thread, or None."""
    session = getattr(_reading, "session", None)
    if session is not None:
        return session.problem
    return getattr(_reading, "problem", None)


def _http_get_json(url: str, retries: int = 1) -> Optional[dict]:
    """GET Reddit JSON: directly, or through the agent browser inside
    ``reddit_reading()`` once Reddit has refused a direct request."""
    session = getattr(_reading, "session", None)
    if session is not None and session.use_browser:
        return session.get_json(url)
    data, refused = _direct_get_json(url, retries)
    if data is None and refused and session is not None:
        return session.get_json(url)
    return data


def read_json(url: str):
    """One Reddit JSON address, read through the agent browser when Reddit
    refuses the direct request. None when it could not be read."""
    with reddit_reading():
        return _http_get_json(url)


def _direct_get_json(url: str, retries: int = 1) -> tuple[Optional[dict], bool]:
    """GET Reddit JSON with ``requests``; ``(data, refused)``.

    The agent Firefox's session cookies are sent when its profile has them.
    ``refused`` is True when Reddit answered 403, which it does to requests
    that do not come from a browser, with or without the cookies.
    """
    cookie_jar = _load_reddit_cookies_from_firefox()
    headers = {
        "User-Agent": REDDIT_BROWSER_USER_AGENT if cookie_jar else reddit_user_agent(),
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
                return _direct_get_json(url, retries=retries - 1)
            logger.warning("reddit JSON 429 on %s — out of retries", url)
            return None, False
        if resp.status_code == 403:
            if cookie_jar is not None and retries > 0:
                logger.warning("reddit JSON 403 with session cookies on %s — refreshing jar once", url)
                _REDDIT_COOKIE_CACHE["jar"] = None
                _REDDIT_COOKIE_CACHE["loaded_at"] = 0.0
                return _direct_get_json(url, retries=retries - 1)
            logger.warning("reddit JSON fetch refused (403) for %s", url)
            return None, True
        resp.raise_for_status()
        return resp.json(), False
    except Exception as e:
        logger.warning("reddit JSON fetch failed for %s: %s", url, e)
        return None, False


def fetch_subreddit_rules(subreddit: str) -> Optional[list[str]]:
    """The community's rules, one "title: description" line each.

    [] means the community has no rules. None means they could not be read
    (403, 429, timeout, a reply without a rules list); callers skip the
    community, because unread rules must not read as "promotion allowed".
    """
    data = _http_get_json(f"{REDDIT_BASE}/r/{subreddit}/about/rules.json")
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        return None
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
    # The community's front listing, which Reddit sorts by hot. Measured
    # 2026-11-11 from a signed-in browser: this address answers 200 where
    # /hot.json and /new.json answer 403.
    data = _http_get_json(f"{REDDIT_BASE}/r/{subreddit}/.json?limit={limit}")
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


def _thread_json_url(permalink: str, limit: int = THREAD_COMMENT_LIMIT) -> str:
    return f"{permalink.rstrip('/')}.json?limit={limit}&depth=1"


def search_thread_refs(
    query: str,
    subreddit: Optional[str] = None,
    limit: int = SEARCH_RESULT_LIMIT,
) -> list[tuple[str, str, str]]:
    """(subreddit, thread id, permalink) for the threads Reddit's search lists
    for ``query``, newest first; within ``subreddit`` when one is given.

    Read from the search page in the agent browser, so it answers only inside
    ``reddit_reading()``: Reddit serves search to nothing but a browser
    (measured 2026-11-11: search.json and search.rss answer 403 even there).
    """
    session = getattr(_reading, "session", None)
    if session is None:
        return []
    params = {"q": query, "sort": "new", "t": SEARCH_WINDOW}
    if subreddit:
        base = f"{REDDIT_BASE}/r/{subreddit}/search/"
        params["restrict_sr"] = "1"
    else:
        base = f"{REDDIT_BASE}/search/"
        params["type"] = "posts"
    found = session.search_links(f"{base}?{urlencode(params)}")
    return [
        (sub, tid, f"{REDDIT_BASE}/r/{sub}/comments/{tid}/")
        for sub, tid in found[:limit]
    ]


def fetch_thread(permalink: str) -> Optional[RedditThread]:
    """The post at ``permalink``, or None when it cannot be read or is one the
    scout leaves alone (stickied, over 18)."""
    data = _http_get_json(_thread_json_url(permalink))
    try:
        d = data[0]["data"]["children"][0]["data"]
    except (KeyError, IndexError, TypeError):
        return None
    if d.get("stickied") or d.get("is_pinned") or d.get("over_18"):
        return None
    return RedditThread(
        id=d.get("id", ""),
        url=d.get("url", ""),
        permalink=f"{REDDIT_BASE}{d.get('permalink', '')}",
        subreddit=d.get("subreddit", ""),
        title=(d.get("title") or "").strip(),
        selftext=(d.get("selftext") or "").strip(),
        score=int(d.get("score") or 0),
        num_comments=int(d.get("num_comments") or 0),
        created_utc=float(d.get("created_utc") or 0),
    )


def fetch_thread_comments(permalink: str, limit: int = THREAD_COMMENT_LIMIT) -> list[str]:
    """Returns flat list of comment bodies in display order, top-level first."""
    data = _http_get_json(_thread_json_url(permalink, limit))
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
    from backend.utils.backend_http import backend_base_url
    return f"{backend_base_url()}/api"


# Backwards-compat alias so any in-flight callers still work.
_backend_url = backend_url


def draft_via_backend(
    thread: RedditThread,
    comments: list[str],
    task_id: Optional[int],
    *,
    relevance_unchecked: bool = False,
) -> Optional[dict]:
    """Calls the social-outreach draft endpoint synchronously.

    ``relevance_unchecked`` says the thread-fit judge could not run on this
    thread; the endpoint notes that on the draft for the person reviewing it.
    The thread's keyword label is not sent: a feature_hint on /draft-comment
    is a person asking the draft to lead with that feature, and this loop
    leaves the angle to the pitch sheet.
    """
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
                "task_id": task_id,
                "mode": "comment",
                "relevance_unchecked": relevance_unchecked,
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


# The element holding keyboard focus, followed through shadow roots: what a
# typed comment actually lands in. Joined into one line, so no // comments.
_FOCUSED_EDITABLE_JS = (
    "JSON.stringify((() => {"
    "  let el = document.activeElement;"
    "  while (el && el.shadowRoot && el.shadowRoot.activeElement) el = el.shadowRoot.activeElement;"
    "  if (!el) return {editable: false};"
    "  const tag = el.tagName.toLowerCase();"
    "  const editable = !!el.isContentEditable || tag === 'textarea';"
    "  const label = ((el.getAttribute('placeholder') || '') + ' ' + (el.getAttribute('aria-label') || '')).trim();"
    "  return {editable, tag, label: label.slice(0, 80), text: el.isContentEditable ? (el.innerText || '') : (el.value || '')};"
    "})())"
)
_FOCUS_WAIT_S = 3.0


def _focused_editable() -> Optional[dict]:
    """What has keyboard focus in the agent Firefox, or None when unreadable."""
    data, _why = bidi_evaluate_json(_FOCUSED_EDITABLE_JS)
    return data


def _same_text(typed: str, intended: str) -> bool:
    """Whitespace-insensitive equality: the rich editor turns newlines into
    paragraphs, so only the words and their order are compared."""
    return " ".join((typed or "").split()) == " ".join((intended or "").split())


def _wait_for_composer_focus() -> tuple[bool, str]:
    """Wait for the clicked composer to take focus before typing.

    Reddit expands the composer into its editor on the click; keystrokes sent
    before the editor holds focus are lost, which once dropped the opening
    words of a published comment.
    """
    deadline = time.monotonic() + _FOCUS_WAIT_S
    last = None
    while True:
        last = _focused_editable()
        if last and last.get("editable") and not re.search(r"search", last.get("label") or "", re.I):
            return True, ""
        if time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    if last is None:
        return False, "composer_not_focused: the page could not be read"
    return False, f"composer_not_focused: focus is on <{last.get('tag')}> {last.get('label') or ''}".rstrip()


def post_comment_via_servo(
    permalink: str,
    comment_text: str,
    *,
    before_submit: Optional[Callable[[], bool]] = None,
) -> tuple[bool, str]:
    """
    Drive Firefox on DISPLAY=:99 to land the comment.
    Returns (success, reason).

    Strategy: navigate to the thread on www.reddit.com (modern UI matches
    vision model training distribution), then hand the see-think-act loop a
    precise instruction. The agent's vision model finds the comment box,
    clicks, types, and submits.

    On failure: ServoController records success=False; we treat that as servo
    failure and abort.

    ``before_submit`` is called once the text is typed and immediately before
    the step that publishes; when it returns False nothing is published and
    the reason is ``transitions.WITHDRAWN_BEFORE_SUBMIT``.
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
    focused, why = _wait_for_composer_focus()
    if not focused:
        return False, why
    # Reddit restores an unsent comment from an earlier visit into the box,
    # so it is emptied before the approved text goes in.
    held = _focused_editable()
    if held is not None and (held.get("text") or "").strip():
        screen.hotkey("ctrl", "a")
        screen.hotkey("BackSpace")
        time.sleep(0.3)
        held = _focused_editable()
        if held is None or (held.get("text") or "").strip():
            return False, "composer_not_empty: nothing was posted; the comment box held earlier text"
    _human_pause(0.5, 1.0)

    logger.warning("typing comment (%s chars)", len(comment_text))
    screen.type_text(comment_text)
    time.sleep(1.0)
    _human_pause()

    # Only the approved text is ever submitted: read the composer back and,
    # on any difference, empty it and stop.
    held = _focused_editable()
    if held is None or not _same_text(held.get("text", ""), comment_text):
        screen.hotkey("ctrl", "a")
        screen.hotkey("BackSpace")
        shown = " ".join(((held or {}).get("text") or "").split())[:60]
        return False, (
            "typed_text_mismatch: nothing was posted; the comment box held "
            f"{shown!r} instead of the approved text"
            if held is not None else
            "typed_text_unreadable: nothing was posted; the comment box could not be read back"
        )

    # Submit via Reddit's standard Ctrl+Enter shortcut. The textarea is
    # already focused from the click and type, so this keystroke routes to
    # the right element. Reddit interprets Ctrl+Enter as form-submit
    # for comment composers.
    if before_submit is not None and not before_submit():
        return False, WITHDRAWN_BEFORE_SUBMIT
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
    needle = comment_text[:60].strip()
    # Look for needle in any rendered comment OR for the
    # composer being empty (no error message and no value)
    # which also implies a successful post.
    # The pieces join into one line, so the script must not contain // comments.
    check_js = (
        "(() => {"
        "  const needle = " + _json.dumps(needle) + ";"
        "  const url = location.href;"
        "  /* 1) Primary: needle appears in an element that looks like a comment body. */"
        "  const sels = ['[data-testid=\"comment\"]', 'shreddit-comment', '[id^=\"comment-tree-content-anchor\"]', 'div[role=\"region\"]'];"
        "  let foundInThread = false;"
        "  for (const s of sels) {"
        "    const els = document.querySelectorAll(s);"
        "    for (const el of els) {"
        "      if ((el.textContent || '').includes(needle)) { foundInThread = true; break; }"
        "    }"
        "    if (foundInThread) break;"
        "  }"
        "  /* 2) Secondary: composer is empty and no error message. */"
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
    # A new comment can take a moment to render, so the thread is read a few times.
    for attempt in range(3):
        d, why = bidi_evaluate_json(check_js)
        if d is not None and d.get("foundInThread"):
            break
        if attempt < 2:
            time.sleep(2.0)
    if d is None:
        posted = False
        verify_msg = f"verify failed: {why}"
    else:
        posted = bool(d.get("foundInThread", False)) and not d.get("errorVisible", False)
        verify_msg = (
            f"foundInThread={d.get('foundInThread')} "
            f"composerEmpty={d.get('composerEmpty')} "
            f"errorVisible={d.get('errorVisible')} "
            f"url={d.get('url')}"
        )
    logger.warning("post-submit verify: posted=%s %s", posted, verify_msg)

    if not posted:
        if d is not None and d.get("composerEmpty") and not d.get("errorVisible"):
            # Reddit took the submit but the comment is not on the page yet:
            # it may well be live, and approving the draft again would post twice.
            return False, f"submitted_unconfirmed: may be live, check the thread before approving again ({verify_msg})"
        return False, f"submit_failed: {verify_msg}"
    return True, "ok"


def web_access_refused(subreddit: str, report: dict, task_id: Optional[int] = None) -> bool:
    """True, with the pass reported as skipped, when web access (Settings, off
    by default) is off. Reading a subreddit is an outside request, gated the
    way the YouTube scout and posting are."""
    blocked = web_access_block_reason("scout Reddit for outreach")
    if not blocked:
        return False
    logger.info("reddit outreach: r/%s pass skipped: %s", subreddit, blocked)
    report["reason"] = "web_access_off"
    audit.log_outreach_event(
        platform="reddit", action="abort",
        target_url=f"{REDDIT_BASE}/r/{subreddit}",
        status="aborted", abort_reason="web_access_off",
        task_id=task_id,
    )
    return True


class RedditOutreachLoop:
    """One pass = visit one subreddit, find up to MAX_THREADS_PER_PASS candidates, draft + maybe post."""

    def run_one_pass(self, subreddit: str, task_id: Optional[int] = None) -> dict:
        # recon imports this module, so its judge rule is read here, not at import.
        from backend.services.social_outreach.recon import judged_unfit

        report = {
            "subreddit": subreddit,
            "drafted": 0,
            "posted": 0,
            "aborted": 0,
            "skipped": 0,
            "skipped_by_llm": 0,
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
        if web_access_refused(subreddit, report, task_id):
            return report

        rules_list = fetch_subreddit_rules(subreddit)
        if rules_list is None:
            report["reason"] = "rules_unreadable"
            audit.log_outreach_event(
                platform="reddit", action="abort",
                target_url=f"{REDDIT_BASE}/r/{subreddit}",
                status="aborted", abort_reason="rules_unreadable",
                task_id=task_id,
            )
            report["aborted"] += 1
            return report
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
            # This loop only drafts (the approved-drafts tick posts), so the
            # cap counts drafts.
            if report["drafted"] + report["aborted"] >= MAX_THREADS_PER_PASS:
                break
            if thread.id in recent_done:
                report["skipped"] += 1
                continue

            comments = fetch_thread_comments(thread.permalink)
            feature_hint = thread_is_relevant(thread, comments)
            if feature_hint is None:
                report["skipped"] += 1
                continue

            # Thread-fit judge, asked the same way recon asks it: a keyword
            # match cannot tell a setup question from a rant against local AI.
            # A thread it grades below the bar, or says "skip" to, gets no
            # draft. When the judge cannot run, the thread is drafted marked
            # relevance unchecked for the person who reviews the draft.
            relevance = external_grader.score_thread_relevance(
                title=thread.title,
                selftext=thread.selftext,
                top_comments=comments,
                feature_hint=feature_hint,
                subreddit=subreddit,
            )
            relevance_unchecked = bool(relevance.get("skipped"))
            logger.info(
                "reddit loop: r/%s thread=%s relevance grade=%.2f verdict=%s unchecked=%s reason=%s",
                subreddit, thread.id, relevance.get("grade", 0.0), relevance.get("verdict") or "-",
                relevance_unchecked, (relevance.get("reason") or "")[:80],
            )
            if judged_unfit(relevance):
                report["skipped_by_llm"] += 1
                continue

            draft_result = draft_via_backend(
                thread, comments, task_id,
                relevance_unchecked=relevance_unchecked,
            )
            if not draft_result:
                report["skipped"] += 1
                continue
            report["drafted"] += 1

            # Draft-only: a person approves each draft and starts its post
            # (watched_post), which checks cadence and claims the row.
            if draft_result.get("would_post"):
                report["queued_for_post"] = report.get("queued_for_post", 0) + 1

        return report
