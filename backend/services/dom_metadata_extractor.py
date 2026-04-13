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
