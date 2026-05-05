#!/usr/bin/env python3
"""
DOM Metadata Extractor — gives the agent structured knowledge of what's on screen.

Connects to Firefox via Chrome DevTools Protocol (CDP) on port 9222,
extracts interactive elements with bounding boxes, and returns them
as structured data the agent can use for precise clicking.

Fails gracefully — if Firefox isn't running or CDP isn't available,
returns empty results. Never blocks the agent loop.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

CDP_PORT = 9222
CDP_DISCOVER_URL = f"http://localhost:{CDP_PORT}/json/list"
CDP_TIMEOUT = 3  # seconds — fast fail if CDP isn't available
CACHE_TTL = 1.0  # seconds — one agent step reuses the same snapshot
MAX_ELEMENTS = 50  # cap to keep prompt concise

# JavaScript that runs inside Firefox to enumerate interactive elements
# and return their bounding boxes in viewport coordinates.
EXTRACT_JS = """(() => {
  const selectors = 'a,button,input,textarea,select,[role="button"],[role="link"],[role="tab"],[role="menuitem"],[contenteditable="true"]';
  const els = document.querySelectorAll(selectors);
  const results = [];
  const seen = new Set();
  for (const el of els) {
    if (results.length >= """ + str(MAX_ELEMENTS) + """) break;
    const rect = el.getBoundingClientRect();
    if (rect.width < 5 || rect.height < 5) continue;
    if (rect.bottom < 0 || rect.top > window.innerHeight) continue;
    if (rect.right < 0 || rect.left > window.innerWidth) continue;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
    const text = (el.textContent || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
    const key = el.tagName + '|' + Math.round(rect.x) + '|' + Math.round(rect.y) + '|' + text.slice(0,20);
    if (seen.has(key)) continue;
    seen.add(key);
    results.push({
      tag: el.tagName.toLowerCase(),
      text: text,
      type: el.type || el.getAttribute('role') || '',
      x: Math.round(rect.x), y: Math.round(rect.y),
      w: Math.round(rect.width), h: Math.round(rect.height),
      cx: Math.round(rect.x + rect.width / 2),
      cy: Math.round(rect.y + rect.height / 2),
      id: el.id || '',
      name: el.name || '',
      href: (el.href || '').slice(0, 200),
      focused: document.activeElement === el
    });
  }
  const chrome = {
    screenX: window.screenX || 0,
    screenY: window.screenY || 0,
    chromeTop: (window.outerHeight - window.innerHeight) || 0,
    chromeLeft: (window.outerWidth - window.innerWidth) || 0
  };
  return JSON.stringify({
    url: location.href,
    title: document.title,
    elements: results,
    chrome: chrome
  });
})()"""


@dataclass
class ElementInfo:
    """A single interactive element on the page."""
    tag: str
    text: str
    element_type: str
    x: int  # screen-space left
    y: int  # screen-space top
    w: int  # width
    h: int  # height
    cx: int  # screen-space center x
    cy: int  # screen-space center y
    id: str = ""
    name: str = ""
    href: str = ""
    focused: bool = False


@dataclass
class DOMSnapshot:
    """Snapshot of interactive elements from the current page."""
    url: str = ""
    title: str = ""
    elements: List[ElementInfo] = field(default_factory=list)
    success: bool = False
    error: str = ""
    timestamp: float = 0.0


class DOMMetadataExtractor:
    """Extracts interactive element metadata from Firefox via CDP."""

    _instance: Optional["DOMMetadataExtractor"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._cache: Optional[DOMSnapshot] = None
        self._cache_time: float = 0.0

    @classmethod
    def get_instance(cls) -> "DOMMetadataExtractor":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def extract(self) -> DOMSnapshot:
        """Extract interactive elements from the active Firefox tab.

        Returns cached result if within TTL. Never raises — returns
        DOMSnapshot(success=False) on any error.
        """
        # Check cache
        now = time.time()
        if self._cache and (now - self._cache_time) < CACHE_TTL:
            return self._cache

        try:
            snapshot = self._extract_impl()
            self._cache = snapshot
            self._cache_time = now
            return snapshot
        except Exception as e:
            logger.debug(f"DOM extraction failed (non-fatal): {e}")
            return DOMSnapshot(success=False, error=str(e))

    def _extract_impl(self) -> DOMSnapshot:
        """Internal extraction via Firefox BiDi WebSocket protocol.

        Creates a session, gets the browsing context, evaluates JS to
        enumerate interactive elements, then closes the session cleanly.
        """
        import websocket as _ws

        WS_URL = f"ws://localhost:{CDP_PORT}/session"

        # 1. Connect and create session
        try:
            ws = _ws.create_connection(WS_URL, timeout=CDP_TIMEOUT, suppress_origin=True)
        except Exception as e:
            return DOMSnapshot(success=False, error=f"BiDi connect failed: {e}")

        try:
            # Create session
            ws.send(json.dumps({"id": 1, "method": "session.new", "params": {"capabilities": {}}}))
            session = json.loads(ws.recv())
            if session.get("type") != "success":
                return DOMSnapshot(success=False, error=f"Session failed: {session.get('message', '')[:100]}")

            # Get browsing contexts (tabs)
            ws.send(json.dumps({"id": 2, "method": "browsingContext.getTree", "params": {}}))
            tree = json.loads(ws.recv())
            contexts = tree.get("result", {}).get("contexts", [])
            if not contexts:
                return DOMSnapshot(success=False, error="No browsing contexts")

            ctx_id = contexts[0]["context"]

            # Evaluate element extraction JS
            ws.send(json.dumps({
                "id": 3,
                "method": "script.evaluate",
                "params": {
                    "expression": EXTRACT_JS,
                    "target": {"context": ctx_id},
                    "awaitPromise": False,
                }
            }))
            result = json.loads(ws.recv())

        except Exception as e:
            return DOMSnapshot(success=False, error=f"BiDi evaluate failed: {e}")
        finally:
            # Always clean up the session
            try:
                ws.send(json.dumps({"id": 99, "method": "session.end", "params": {}}))
                ws.close()
            except Exception:
                pass

        # 3. Parse result
        try:
            value = result.get("result", {}).get("result", {}).get("value", "")
            if not value:
                return DOMSnapshot(success=False, error="Empty CDP result")

            data = json.loads(value)
            chrome_info = data.get("chrome", {})
            offset_x = chrome_info.get("screenX", 0) + chrome_info.get("chromeLeft", 0)
            offset_y = chrome_info.get("screenY", 0) + chrome_info.get("chromeTop", 0)

            elements = []
            for el in data.get("elements", []):
                # Convert viewport coords to screen coords
                screen_x = el["x"] + offset_x
                screen_y = el["y"] + offset_y
                screen_cx = el["cx"] + offset_x
                screen_cy = el["cy"] + offset_y

                elements.append(ElementInfo(
                    tag=el["tag"],
                    text=el["text"],
                    element_type=el.get("type", ""),
                    x=screen_x, y=screen_y,
                    w=el["w"], h=el["h"],
                    cx=screen_cx, cy=screen_cy,
                    id=el.get("id", ""),
                    name=el.get("name", ""),
                    href=el.get("href", ""),
                    focused=el.get("focused", False),
                ))

            logger.info(f"DOM extracted: {len(elements)} elements from {data.get('title', '?')}")

            return DOMSnapshot(
                url=data.get("url", ""),
                title=data.get("title", ""),
                elements=elements,
                success=True,
                timestamp=time.time(),
            )

        except Exception as e:
            return DOMSnapshot(success=False, error=f"Parse failed: {e}")

    # ── Social-outreach scouting ──────────────────────────────────────────
    # Same BiDi plumbing, different goal: drive Firefox to a URL and harvest
    # the post-JS-execution DOM so the outreach LLM gets real OP + comments
    # instead of an HTML-shell scrape. The agent's logged-in cookies come
    # along for free — that's the whole point.

    # Per-platform extractor scripts. Each returns JSON:
    #   {title, op_body, op_author, comments[], target_thread_id, suggested_platform}
    # Selectors will rot when platforms redesign; that's expected. When a
    # selector breaks, the script returns empty fields, the LLM grades the
    # resulting draft 0.10, the human notices, the selector gets fixed.
    _PLATFORM_EXTRACTORS = {
        "discord": r"""(() => {
            const titleEl = document.querySelector('header [class*="title-"]') ||
                            document.querySelector('[class*="channelName-"]');
            const title = (titleEl?.textContent || document.title || "").trim();
            const messageEls = document.querySelectorAll('[id^="chat-messages-"]');
            const messages = [];
            for (const m of messageEls) {
                const author = (m.querySelector('[class*="username-"]')?.textContent || "").trim();
                const content = (m.querySelector('[id^="message-content-"]')?.textContent || "").trim();
                if (content) messages.push({author, content: content.slice(0, 800)});
                if (messages.length >= 10) break;
            }
            const op = messages.shift() || {author: "", content: ""};
            const m = location.pathname.match(/channels\/[^/]+\/(\d+)/);
            return JSON.stringify({
                title: title.slice(0, 200),
                op_body: op.content,
                op_author: op.author,
                comments: messages.map(x => `${x.author}: ${x.content}`),
                target_thread_id: (m && m[1]) || "",
                suggested_platform: "discord"
            });
        })()""",
        "twitter": r"""(() => {
            const articles = document.querySelectorAll('article[data-testid="tweet"]');
            if (!articles.length) return JSON.stringify({title:"", op_body:"", op_author:"", comments:[], target_thread_id:"", suggested_platform:"twitter"});
            const tweetText = a => (a.querySelector('[data-testid="tweetText"]')?.textContent || "").trim();
            const author = a => (a.querySelector('[data-testid="User-Name"]')?.textContent || "").trim();
            const op = articles[0];
            const replies = Array.from(articles).slice(1, 6);
            const m = location.pathname.match(/status\/(\d+)/);
            return JSON.stringify({
                title: tweetText(op).slice(0, 120),
                op_body: tweetText(op),
                op_author: author(op),
                comments: replies.map(r => `${author(r)}: ${tweetText(r)}`).filter(s => s.length > 3),
                target_thread_id: (m && m[1]) || "",
                suggested_platform: "twitter"
            });
        })()""",
        "facebook": r"""(() => {
            const posts = document.querySelectorAll('div[role="article"]');
            if (!posts.length) return JSON.stringify({title:"", op_body:"", op_author:"", comments:[], target_thread_id:"", suggested_platform:"facebook"});
            const txt = p => {
                const msg = p.querySelector('[data-ad-comet-preview="message"]') ||
                            p.querySelector('[data-ad-preview="message"]') ||
                            p.querySelector('[dir="auto"]');
                return (msg?.textContent || "").trim();
            };
            const author = p => {
                const a = p.querySelector('h2,h3,h4 a, strong a');
                return (a?.textContent || "").trim();
            };
            const op = posts[0];
            const comments = Array.from(posts).slice(1, 6);
            return JSON.stringify({
                title: txt(op).slice(0, 120),
                op_body: txt(op),
                op_author: author(op),
                comments: comments.map(txt).filter(Boolean),
                target_thread_id: "",
                suggested_platform: "facebook"
            });
        })()""",
        # Reddit fallback only — the JSON API path in scout-url is faster and
        # doesn't need Firefox running. This kicks in if the JSON API gets
        # rate-limited or hit by the verification wall.
        "reddit": r"""(() => {
            const post = document.querySelector('shreddit-post') ||
                         document.querySelector('[data-test-id="post-content"]');
            const titleEl = post?.querySelector('h1') || document.querySelector('h1');
            const opBody = (post?.querySelector('[slot="text-body"]')?.textContent ||
                            post?.querySelector('[data-test-id="post-body"]')?.textContent ||
                            "").trim();
            const commentEls = document.querySelectorAll('shreddit-comment, [data-testid="comment"]');
            const comments = [];
            for (const c of commentEls) {
                const t = (c.textContent || "").trim();
                if (t) comments.push(t.slice(0, 600));
                if (comments.length >= 5) break;
            }
            const m = location.pathname.match(/comments\/(\w+)/);
            return JSON.stringify({
                title: (titleEl?.textContent || "").trim(),
                op_body: opBody.slice(0, 2000),
                op_author: "",
                comments: comments,
                target_thread_id: (m && m[1]) || "",
                suggested_platform: "reddit"
            });
        })()""",
    }

    @staticmethod
    def detect_platform(url: str) -> Optional[str]:
        """Map a URL host to one of the platform slugs we have extractors for."""
        try:
            from urllib.parse import urlparse
            host = (urlparse(url).hostname or "").lower()
        except Exception:
            return None
        if "reddit.com" in host:
            return "reddit"
        if "discord.com" in host or "discord.gg" in host:
            return "discord"
        if "facebook.com" in host or "fb.com" in host:
            return "facebook"
        if "twitter.com" in host or "x.com" in host:
            return "twitter"
        return None

    @staticmethod
    def _bidi_reachable() -> bool:
        """Cheap check: is BiDi listening on :9222 for our agent Firefox?"""
        try:
            import socket as _s
            with _s.create_connection(("127.0.0.1", CDP_PORT), timeout=0.5):
                return True
        except OSError:
            return False

    @staticmethod
    def ensure_agent_firefox(profile_dir: str = "/home/llamax1/LLAMAX8/data/agent/firefox_profile",
                             display: str = ":99",
                             timeout: float = 25.0) -> Tuple[bool, str]:
        """Make sure Firefox is running on the agent display with BiDi enabled.

        Wayland-aware: explicitly clears WAYLAND_DISPLAY and forces GDK/Moz to
        X11, otherwise the launched Firefox latches onto the host's Wayland
        compositor and shows up on the user's real screen instead of :99.
        Idempotent — if BiDi is already up, returns immediately.
        """
        if DOMMetadataExtractor._bidi_reachable():
            return True, "already running"

        import os
        import subprocess
        import time as _time
        from pathlib import Path

        if not Path(profile_dir).exists():
            return False, f"profile dir missing: {profile_dir}"

        # Sanitized env: keep HOME/PATH, drop everything that could route the
        # Firefox window back to the host display.
        env = {
            "DISPLAY": display,
            "HOME": os.environ.get("HOME", "/home/llamax1"),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "USER": os.environ.get("USER", "llamax1"),
            # Force X11 even when host is Wayland.
            "WAYLAND_DISPLAY": "",
            "XDG_SESSION_TYPE": "x11",
            "MOZ_ENABLE_WAYLAND": "0",
            "GDK_BACKEND": "x11",
            # Don't share DBus with the host session — that's how a stray
            # `firefox --no-remote` ends up rendering on the user's screen.
            "DBUS_SESSION_BUS_ADDRESS": "",
        }

        cmd = [
            "firefox",
            "--no-remote",
            "--remote-debugging-port", str(CDP_PORT),
            "--profile", profile_dir,
        ]
        try:
            subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            return False, f"launch failed: {e}"

        # Poll for BiDi to come up.
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            if DOMMetadataExtractor._bidi_reachable():
                return True, "launched"
            _time.sleep(0.5)
        return False, f"BiDi did not come up within {timeout}s"

    def extract_thread_context(
        self,
        url: str,
        platform: Optional[str] = None,
        nav_timeout: float = 12.0,
        settle_seconds: float = 1.5,
    ) -> Dict[str, Any]:
        """Drive Firefox to a URL and pull OP + comments out of the rendered DOM.

        Returns the same shape /scout-url already returns:
            {title, description, hostname, thread_context, target_thread_id?,
             suggested_platform?, source}
        """
        if platform is None:
            platform = self.detect_platform(url)
        if not platform or platform not in self._PLATFORM_EXTRACTORS:
            return {"error": f"no DOM extractor for platform '{platform}' (host {url[:60]})"}

        # Make sure Firefox is up on :99 with BiDi exposed before we try to
        # talk to it. Idempotent — fast-returns if already running.
        ok, msg = self.ensure_agent_firefox()
        if not ok:
            return {"error": f"agent Firefox not available: {msg}"}

        import websocket as _ws

        WS_URL = f"ws://localhost:{CDP_PORT}/session"
        try:
            ws = _ws.create_connection(WS_URL, timeout=CDP_TIMEOUT, suppress_origin=True)
        except Exception as e:
            return {"error": f"BiDi connect failed: {e}"}

        try:
            # 1. New session
            ws.send(json.dumps({"id": 1, "method": "session.new", "params": {"capabilities": {}}}))
            session = json.loads(ws.recv())
            if session.get("type") != "success":
                return {"error": f"session failed: {session.get('message','')[:120]}"}

            # 2. Get tab id
            ws.send(json.dumps({"id": 2, "method": "browsingContext.getTree", "params": {}}))
            tree = json.loads(ws.recv())
            contexts = tree.get("result", {}).get("contexts", [])
            if not contexts:
                return {"error": "no browsing contexts"}
            ctx_id = contexts[0]["context"]

            # 3. Navigate. wait="complete" means BiDi blocks until the load
            # event fires — saves us a polling loop.
            ws.settimeout(nav_timeout)
            ws.send(json.dumps({
                "id": 3,
                "method": "browsingContext.navigate",
                "params": {"context": ctx_id, "url": url, "wait": "complete"},
            }))
            nav = json.loads(ws.recv())
            if nav.get("type") == "error":
                return {"error": f"navigate failed: {nav.get('message','')[:200]}"}

            # 4. Let JS settle. Discord/Twitter/FB all hydrate after load.
            time.sleep(settle_seconds)

            # 5. Run the platform extractor.
            ws.send(json.dumps({
                "id": 4,
                "method": "script.evaluate",
                "params": {
                    "expression": self._PLATFORM_EXTRACTORS[platform],
                    "target": {"context": ctx_id},
                    "awaitPromise": False,
                }
            }))
            result = json.loads(ws.recv())

            # 6. Parse the extractor result. If selectors didn't match (page
            # redesign, unfamiliar layout), capture a screenshot from the same
            # tab and let the vision model read it. The agent isn't OCR — it's
            # a full screen-reading actor; falling back to vision is the right
            # universal path when fixed selectors don't fit.
            extractor_data: Dict[str, Any] = {}
            try:
                value = result.get("result", {}).get("result", {}).get("value", "")
                if value:
                    extractor_data = json.loads(value)
            except Exception:
                extractor_data = {}

            op_body = (extractor_data.get("op_body") or "").strip()
            op_author = (extractor_data.get("op_author") or "").strip()
            comments = extractor_data.get("comments") or []
            title = (extractor_data.get("title") or "").strip()
            target_thread_id = extractor_data.get("target_thread_id") or ""
            source = "cdp_dom"

            if not op_body and not comments:
                # Vision fallback path — capture screenshot via the same BiDi
                # session, then ask the vision model to extract content.
                vision_blob = self._vision_extract_via_bidi(ws, ctx_id, platform, url)
                if vision_blob:
                    title = title or vision_blob.get("title", "").strip()
                    op_body = vision_blob.get("op_body", "").strip()
                    op_author = op_author or vision_blob.get("op_author", "").strip()
                    comments = vision_blob.get("comments") or []
                    source = "cdp_vision"

        except Exception as e:
            return {"error": f"BiDi flow failed: {e}"}
        finally:
            try:
                ws.send(json.dumps({"id": 99, "method": "session.end", "params": {}}))
                ws.close()
            except Exception:
                pass

        if not op_body and not comments:
            return {
                "error": "scout returned no content — DOM and vision both came up empty",
                "title": title,
                "hostname": url.split("/")[2] if "://" in url else "",
                "suggested_platform": platform,
                "source": source + "_empty",
            }

        blocks = []
        if title:
            blocks.append(title if not op_author else f"{title} — by {op_author}")
        if op_body:
            blocks.append(f"OP body:\n{op_body[:1800]}")
        if comments:
            blocks.append("Top comments:\n" + "\n".join(f"- {str(c)[:600]}" for c in comments[:5]))

        from urllib.parse import urlparse
        return {
            "title": title[:300],
            "description": op_body[:300],
            "hostname": (urlparse(url).hostname or ""),
            "thread_context": "\n\n".join(blocks)[:6000],
            "target_thread_id": target_thread_id or None,
            "suggested_platform": platform,
            "source": source,
        }

    def _vision_extract_via_bidi(self, ws, ctx_id: str, platform: str, url: str) -> Optional[Dict[str, Any]]:
        """Capture a screenshot via the open BiDi session and ask the vision
        model to extract the main post + surrounding messages.

        Universal fallback when platform-specific DOM selectors come up empty
        (page redesign, unfamiliar layout, modal in the way, friends list page
        instead of a channel, etc.). Returns {title, op_body, op_author,
        comments[]} or None on failure.
        """
        try:
            ws.send(json.dumps({
                "id": 5,
                "method": "browsingContext.captureScreenshot",
                "params": {"context": ctx_id, "origin": "viewport"},
            }))
            shot_resp = json.loads(ws.recv())
        except Exception as e:
            logger.warning("BiDi screenshot failed for vision fallback: %s", e)
            return None

        b64 = shot_resp.get("result", {}).get("data") or ""
        if not b64:
            return None

        prompt = (
            f"You are looking at a screenshot of {platform} in the user's browser. "
            "Extract the main post or top message and the visible replies/comments. "
            "Return STRICT JSON only, no prose, no markdown fences:\n"
            "{\"title\": \"page or thread title\", "
            "\"op_body\": \"main post text\", "
            "\"op_author\": \"author username if visible\", "
            "\"comments\": [\"author: reply text\", ...]}\n"
            "If a field is unknown, use an empty string or empty array. "
            "Limit to the 5 most recent or most relevant comments."
        )

        try:
            from backend.utils.vision_analyzer import VisionAnalyzer
            vision = VisionAnalyzer()
            vresult = vision.analyze_base64(b64, prompt, num_predict=512, temperature=0.1)
        except Exception as e:
            logger.warning("vision fallback failed for %s: %s", url, e)
            return None

        text = (getattr(vresult, "description", "") or "").strip()
        if not text:
            return None

        # Vision models occasionally wrap JSON in prose — extract the {} block.
        import re as _re
        m = _re.search(r"\{.*\}", text, _re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return {
            "title": (data.get("title") or "")[:300],
            "op_body": (data.get("op_body") or "")[:2000],
            "op_author": (data.get("op_author") or "")[:120],
            "comments": [str(c)[:800] for c in (data.get("comments") or [])][:5],
        }

    @staticmethod
    def format_for_prompt(snapshot: DOMSnapshot) -> str:
        """Format a DOM snapshot as compact text for the LLM prompt."""
        if not snapshot.success or not snapshot.elements:
            return ""

        lines = [f"Page: {snapshot.title} ({snapshot.url})"]
        lines.append("Interactive elements (screen pixel coordinates):")

        for i, el in enumerate(snapshot.elements, 1):
            label = el.text[:50] if el.text else el.id or el.name or el.tag
            type_hint = f"[{el.element_type}]" if el.element_type else ""
            focused = " (focused)" if el.focused else ""
            lines.append(
                f"  [{i}] {el.tag}{type_hint} \"{label}\" at ({el.cx},{el.cy}) {el.w}x{el.h}{focused}"
            )

        return "\n".join(lines)
