"""Address suggestions.

Two sources, always in this order: addresses already held by this instance,
then an optional third-party provider. The local source needs no key, no
network and no settings, so the field keeps working offline.

Guaardvark itself stores no street addresses, so the local list is empty until
a distribution registers a source. A distribution that has address-bearing
tables calls ``register_local_source`` at import time:

    from backend.api.addresses_api import register_local_source
    register_local_source(WorkOrder, "work order")

The model must expose ``address``, ``city``, ``state`` and ``zip`` columns.
"""

import logging
from typing import List, Tuple

from flask import Blueprint, jsonify, request

from backend.services import address_lookup

logger = logging.getLogger(__name__)

addresses_bp = Blueprint("addresses_api", __name__, url_prefix="/api/addresses")

MIN_QUERY_CHARS = 2
DEFAULT_LIMIT = 8
MAX_LIMIT = 25

# (model, label describing where the address came from)
_LOCAL_SOURCES: List[Tuple[object, str]] = []


def register_local_source(model, label: str) -> None:
    """Add a table to the on-file address search. Idempotent."""
    if not any(existing is model for existing, _ in _LOCAL_SOURCES):
        _LOCAL_SOURCES.append((model, label))


def _limit_arg():
    try:
        value = int(request.args.get("limit", DEFAULT_LIMIT))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(value, MAX_LIMIT))


def _key(row):
    return tuple((part or "").strip().lower() for part in row[:4])


def _label(address, city, state, zip_code):
    tail = " ".join(part for part in ((city or "").strip(), (state or "").strip()) if part)
    tail = ", ".join(part for part in (tail, (zip_code or "").strip()) if part).strip(", ")
    return ", ".join(part for part in ((address or "").strip(), tail) if part)


def _local_matches(query, limit):
    like = f"%{query}%"
    seen = set()
    out = []
    for model, origin in _LOCAL_SOURCES:
        if len(out) >= limit:
            break
        try:
            rows = (
                model.query.with_entities(
                    model.address, model.city, model.state, model.zip
                )
                .filter(model.address.isnot(None))
                .filter(
                    model.address.ilike(like)
                    | model.city.ilike(like)
                    | model.zip.ilike(like)
                )
                .limit(limit * 2)
                .all()
            )
        except Exception:
            # A registered source that cannot be queried must not take the
            # whole suggestion list down with it.
            logger.warning("address source %s failed", origin, exc_info=True)
            continue
        for row in rows:
            address, city, state, zip_code = row
            if not (address or "").strip():
                continue
            key = _key(row)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "label": _label(address, city, state, zip_code),
                    "address": address,
                    "city": city or "",
                    "state": state or "",
                    "zip": zip_code or "",
                    "source": origin,
                }
            )
            if len(out) >= limit:
                break
    return out


@addresses_bp.route("", methods=["GET"])
def suggest_addresses():
    query = (request.args.get("q") or "").strip()
    limit = _limit_arg()
    if len(query) < MIN_QUERY_CHARS:
        return jsonify({"items": [], "provider": None, "attribution": None})

    items = _local_matches(query, limit)
    seen = {_key((i["address"], i["city"], i["state"], i["zip"])) for i in items}

    provider = None
    for row in address_lookup.suggest(query):
        key = _key((row["address"], row["city"], row["state"], row["zip"]))
        if key in seen:
            continue
        seen.add(key)
        items.append(row)
        provider = row.get("source")

    return jsonify(
        {
            "items": items,
            "provider": provider,
            "attribution": address_lookup.ATTRIBUTION if provider else None,
            "provider_unavailable": address_lookup.unavailable_reason(),
        }
    )
