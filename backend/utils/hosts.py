"""Hostname matching for URL platform detection and allowlists."""

from __future__ import annotations

from urllib.parse import urlparse


def url_host(url: str | None) -> str:
    """Lower-cased hostname of ``url``, or "" when it has none."""
    try:
        return (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""


def host_matches(host: str | None, *domains: str) -> bool:
    """True when ``host`` is one of ``domains`` or a subdomain of one.

    ``reddit.com`` matches ``reddit.com`` and ``old.reddit.com`` but not
    ``reddit.com.example.net`` or ``notreddit.com``.
    """
    h = (host or "").lower().rstrip(".")
    if not h:
        return False
    for domain in domains:
        d = domain.lower().rstrip(".")
        if h == d or h.endswith("." + d):
            return True
    return False


def url_host_matches(url: str | None, *domains: str) -> bool:
    """``host_matches`` applied to the hostname of ``url``."""
    return host_matches(url_host(url), *domains)
