"""Where a chat's web requests may go: sites the person named, or a search found.

A model reading a web page can be told by that page to fetch an address with
the chat's contents in it. fetch_url, analyze_website, browser_navigate and a
URL inside a web_search query therefore go only to a host that came from the
person (their messages in this chat, or the steps of a plan they approved,
whose invented addresses the plan card already flags) or from the results of a
search made in this chat. A subdomain of a named host counts as named.
Calls from outside a chat (MCP clients, agents) are not limited here.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Dict, Iterable, Optional, Set
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# A URL, or a bare name like example.com or docs.python.org/3/.
URL_RE = re.compile(r"https?://[^\s<>\"'`)\]]+", re.IGNORECASE)
_BARE_HOST_RE = re.compile(r"(?<![@\w.-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})(?![\w-])",
                           re.IGNORECASE)

_lock = threading.Lock()
_search_hosts: Dict[str, Set[str]] = {}
_MAX_SEARCH_HOSTS = 500
_HISTORY_MESSAGES = 50


def host_of(url: str) -> Optional[str]:
    """The lower-case host of a URL or bare name, without a leading www."""
    text = (url or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = "https://" + text
    try:
        host = (urlsplit(text).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    return host[4:] if host.startswith("www.") else host or None


def hosts_in(text: str) -> Set[str]:
    """Hosts named in free text: full URLs and bare names like example.com."""
    found = set()
    for match in URL_RE.findall(text or ""):
        host = host_of(match)
        if host:
            found.add(host)
    for match in _BARE_HOST_RE.findall(text or ""):
        host = host_of(match)
        if host:
            found.add(host)
    return found


def note_search_results(session_id: Optional[str], urls: Iterable[str]) -> None:
    """Remember the hosts a search in this chat returned."""
    if not session_id:
        return
    hosts = {h for h in (host_of(u) for u in urls) if h}
    if not hosts:
        return
    with _lock:
        known = _search_hosts.setdefault(session_id, set())
        if len(known) < _MAX_SEARCH_HOSTS:
            known.update(hosts)


def _person_hosts(session_id: str, current_message: str) -> Set[str]:
    hosts = hosts_in(current_message)
    try:
        from backend.models import LLMMessage, db

        rows = (db.session.query(LLMMessage.content)
                .filter(LLMMessage.session_id == session_id, LLMMessage.role == "user")
                .order_by(LLMMessage.id.desc()).limit(_HISTORY_MESSAGES).all())
        for (content,) in rows:
            hosts |= hosts_in(content or "")
    except Exception as exc:  # noqa: BLE001 - no history means only this message counts
        logger.debug("web provenance: chat history unreadable for %s: %s", session_id, exc)
    return hosts


def _matches(host: str, allowed: Iterable[str]) -> bool:
    return any(host == a or host.endswith("." + a) for a in allowed)


def refusal(context: Optional[dict], url: str) -> Optional[str]:
    """None when ``url`` may be fetched for this call; otherwise why not."""
    ctx = context or {}
    if ctx.get("transport") != "chat":
        return None
    host = host_of(url)
    if not host:
        return None  # the tool's own URL checks answer this
    session_id = ctx.get("chat_session_id")
    current = str(ctx.get("user_message") or ctx.get("message") or "")
    if _matches(host, _person_hosts(session_id, current) if session_id else hosts_in(current)):
        return None
    with _lock:
        searched = set(_search_hosts.get(session_id or "", ()))
    if _matches(host, searched):
        return None
    return (f"{host} did not come from the person or from a search in this chat, so it is not "
            "fetched: web requests go only to sites the person named or a search found, which keeps "
            "a page from sending this chat anywhere else. Ask the person for the address if they "
            "want that site.")
