"""Shared clock helpers.

Timestamps across the backend are stored as naive UTC datetimes so
``isoformat()`` values do not gain a ``+00:00`` suffix and comparisons with
existing database rows keep working. Use ``utcnow()`` from this module
instead of the deprecated ``datetime.utcnow()``.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current UTC time as a naive datetime.

    Naive-UTC is a storage contract, not an oversight: do not return an
    aware datetime here without a dedicated migration of stored rows and
    the comparisons that read them.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
