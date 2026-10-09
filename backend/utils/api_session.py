"""A browser signed in with this install's API key, as an HttpOnly cookie.

The web UI never keeps the key. Settings → Access sends it once to
POST /api/auth/session, which answers with a cookie holding a token derived
from the key, HMAC-SHA256(key, "guaardvark-session-v1"), not the key itself.
The cookie is HttpOnly (page scripts, and so an injected script, cannot read
it), SameSite=Strict (a page on another site cannot make the browser send it),
Path=/, and Secure when the browser reached us over HTTPS. Replacing or
removing the key changes or ends the token, so every browser signed in with
the old key is signed out at once.

SameSite treats the same host on another port as the same site, so the cookie
is also refused when the browser says the request came from a page on another
origin (the Sec-Fetch-Site header), unless that page is this install's own
frontend served from a separate origin: cors_policy.frontend_origins(), the
frontend's port on this machine's names and addresses plus VITE_FRONTEND_URL
and GUAARDVARK_CORS_ORIGINS. Browsers that send no Sec-Fetch-Site (Safari
before 16.4) are held by SameSite alone.

Command-line clients, the MCP server and scripts keep sending the key in the
X-API-Key header; the cookie is for browsers only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from pathlib import Path
from typing import Optional

from flask import request

from backend.utils.cors_policy import frontend_origins, normalize_origin

COOKIE_PREFIX = "guaardvark_session"
_TOKEN_CONTEXT = b"guaardvark-session-v1"
# A browser stays signed in until the key changes, it signs out, or a year
# passes without a new sign-in.
MAX_AGE_SECONDS = 365 * 24 * 60 * 60

# session_state() answers
NONE = "none"
VALID = "valid"
REJECTED = "rejected"


def cookie_name(root: Optional[Path] = None) -> str:
    """The cookie's name for this install.

    Cookies are kept per host, not per port, so two installs on one machine
    (each on its own ports) would overwrite each other's sign-in under one
    name. The name carries a short hash of the install's folder instead.
    """
    if root is None:
        try:
            from backend.config import GUAARDVARK_ROOT
            root = Path(GUAARDVARK_ROOT)
        except Exception:
            root = Path(__file__).resolve().parents[2]
    tag = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:10]
    return f"{COOKIE_PREFIX}_{tag}"


def session_token(key: str) -> str:
    digest = hmac.new(key.encode("utf-8"), _TOKEN_CONTEXT, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def request_origin_allowed() -> bool:
    """False when the browser reports the request came from another origin's
    page that is not this install's own frontend."""
    site = (request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if not site or site in ("same-origin", "none"):
        return True
    origin = normalize_origin(request.headers.get("Origin"))
    return origin is not None and origin in frontend_origins()


def session_state() -> str:
    """NONE (no cookie), VALID, or REJECTED (a cookie this install does not
    accept now: the key changed or was removed, or another origin's page sent it)."""
    value = request.cookies.get(cookie_name())
    if not value:
        return NONE
    from backend.utils.auth_guard import configured_api_key

    key = configured_api_key()
    if not key or not request_origin_allowed():
        return REJECTED
    if hmac.compare_digest(value.encode("utf-8"), session_token(key).encode("ascii")):
        return VALID
    return REJECTED


def _request_is_https() -> bool:
    if request.is_secure:
        return True
    # The local dev/preview proxy talks plain HTTP to Flask and says what the
    # browser used in X-Forwarded-Proto; trust that only from this machine.
    from backend.utils.auth_guard import _is_localhost

    if _is_localhost(request.remote_addr or ""):
        proto = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        return proto == "https"
    return False


def set_session_cookie(response, key: str):
    response.set_cookie(
        cookie_name(),
        session_token(key),
        max_age=MAX_AGE_SECONDS,
        path="/",
        httponly=True,
        samesite="Strict",
        secure=_request_is_https(),
    )
    return response


def clear_session_cookie(response):
    response.delete_cookie(
        cookie_name(),
        path="/",
        httponly=True,
        samesite="Strict",
        secure=_request_is_https(),
    )
    return response


def strip_session_cookies(cookie_header: str) -> str:
    """A Cookie header without any install's sign-in, for requests passed on
    to another machine."""
    kept = [part.strip() for part in cookie_header.split(";")
            if part.strip() and not part.strip().startswith(COOKIE_PREFIX)]
    return "; ".join(kept)
