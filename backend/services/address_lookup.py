"""Optional third-party address suggestions.

The app is local-first: an address field works from the addresses already in
the database. This module is the opt-in second source, and it stays inert until
an operator both turns on web access and stores a provider key.

Geoapify is the default because its data is OpenStreetMap under ODbL, so a
suggestion may be stored on a client record. Providers that licence results as
"temporary" (results may be displayed but not persisted) are not usable here.
"""

from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

PROVIDER_SETTING = "address_provider"
API_KEY_SETTING = "address_provider_key"
DEFAULT_PROVIDER = "geoapify"

_GEOAPIFY_URL = "https://api.geoapify.com/v1/geocode/autocomplete"
_TIMEOUT_SECONDS = 6
_MAX_RESULTS = 5

# Geoapify asks for visible credit wherever its suggestions are shown.
ATTRIBUTION = "Powered by Geoapify"


def _setting(name: str, default: Optional[str] = None) -> Optional[str]:
    """Read a NON-SENSITIVE setting. The provider key does not come through here.

    The shared getter logs the setting name and the exception when a read
    fails, which is fine for a provider name and wrong for anything secret.
    """
    try:
        from backend.utils.settings_utils import get_setting

        value = get_setting(name, default)
    except Exception:
        logger.debug("address_lookup: could not read setting %s", name)
        return default
    if isinstance(value, str) and not value.strip():
        return default
    return value


def provider_name() -> str:
    return (_setting(PROVIDER_SETTING, DEFAULT_PROVIDER) or DEFAULT_PROVIDER).lower()


def api_key() -> Optional[str]:
    """The provider key, read straight from the settings table.

    Deliberately not routed through the shared getter: that helper logs on a
    failed read, which would put a secret's identifier — and whatever the
    exception carries — into the log. Nothing on this path logs at all, and the
    value is returned to the caller and nowhere else.

    Behaviour is identical to the shared getter for this key: it has no
    ENV_VAR_MAP entry, so that helper would resolve it from the database or
    fall through to the default exactly as this does.
    """
    try:
        from flask import has_app_context

        from backend.models import Setting, db

        if not has_app_context():
            return None
        row = db.session.get(Setting, API_KEY_SETTING)
    except Exception:
        return None
    value = getattr(row, "value", None) if row is not None else None
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def web_access_enabled() -> bool:
    try:
        from backend.utils.settings_utils import get_web_access

        return bool(get_web_access())
    except Exception:
        return False


def is_configured() -> bool:
    """True when a lookup would actually reach a provider."""
    return bool(api_key()) and web_access_enabled()


def unavailable_reason() -> Optional[str]:
    """Why suggestions are local-only, phrased for the UI. None when available."""
    if not api_key():
        return "No address provider key is set in Settings."
    if not web_access_enabled():
        return "Web access is turned off in Settings."
    return None


def _geoapify(query: str, limit: int, key: str) -> List[dict]:
    import requests

    response = requests.get(
        _GEOAPIFY_URL,
        params={
            "text": query,
            "format": "json",
            "filter": "countrycode:us",
            "limit": limit,
            "apiKey": key,
        },
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json() or {}
    out: List[dict] = []
    for row in payload.get("results") or []:
        street = " ".join(
            part for part in (row.get("housenumber"), row.get("street")) if part
        ).strip()
        out.append(
            {
                "label": row.get("formatted") or street,
                "address": street or row.get("address_line1") or "",
                "city": row.get("city") or "",
                "state": row.get("state_code") or row.get("state") or "",
                "zip": row.get("postcode") or "",
                "source": "geoapify",
            }
        )
    return out


def suggest(query: str, limit: int = _MAX_RESULTS) -> List[dict]:
    """Provider suggestions for `query`, or [] when unconfigured or failing.

    Never raises: an address field must keep working on the local source alone.
    """
    query = (query or "").strip()
    if len(query) < 3 or not is_configured():
        return []
    key = api_key()
    name = provider_name()
    try:
        if name == "geoapify":
            return _geoapify(query, min(limit, _MAX_RESULTS), key)
        logger.warning("address_lookup: unknown provider %r", name)
        return []
    except Exception:
        logger.warning("address_lookup: %s suggestion failed", name, exc_info=True)
        return []
