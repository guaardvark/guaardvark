"""Shared clock helpers.

Timestamps across the backend are stored as naive UTC datetimes so
``isoformat()`` values do not gain a ``+00:00`` suffix and comparisons with
existing database rows keep working. Use ``utcnow()`` from this module
instead of the deprecated ``datetime.utcnow()``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


def utcnow() -> datetime:
    """Return the current UTC time as a naive datetime.

    Naive-UTC is a storage contract, not an oversight: do not return an
    aware datetime here without a dedicated migration of stored rows and
    the comparisons that read them.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso_utc(value: Optional[datetime]) -> Optional[str]:
    """A stored naive-UTC datetime as ISO-8601 ending in ``Z``, which a browser reads as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def iso_local(value: Optional[datetime]) -> Optional[str]:
    """A naive datetime written as local wall time (``datetime.now()``) as ISO-8601 with its offset."""
    if value is None:
        return None
    return value.astimezone().isoformat(timespec="seconds")


def iso_epoch(value: Optional[float]) -> Optional[str]:
    """Seconds since the epoch (a file's mtime) as ISO-8601 UTC."""
    if value is None:
        return None
    return iso_utc(datetime.fromtimestamp(value, tz=timezone.utc))


def iso_text(value: Optional[str]) -> Optional[str]:
    """An ISO-8601 string with an explicit offset (``Z`` or ``±HH:MM``); None when unreadable.

    Accepts ``±HHMM`` offsets; a string with no offset is read as local wall time.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return iso_local(parsed)
    if parsed.utcoffset() == timedelta(0):
        return iso_utc(parsed)
    return parsed.isoformat(timespec="seconds")
