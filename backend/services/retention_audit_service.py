"""Retention audit recording.

Single point through which every deletion in the system gets logged.
Callers invoke `record_deletion(...)` immediately before or after
performing the actual delete, supplying the kind, count, and any
filter parameters. The audit row goes into `retention_audit`.

Per plans/2026-04-29-data-retention.md §6.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def record_deletion(
    *,
    actor: str,                 # 'user' | 'system'
    kind: str,                  # 'job_history' | 'chat' | 'cache' | ...
    operation: str,             # 'manual_delete' | 'bulk_delete' | 'auto_purge'
    item_count: int,
    bytes_freed: Optional[int] = None,
    parameters: Optional[dict[str, Any]] = None,
    triggered_by: Optional[str] = None,
) -> Optional[int]:
    """Insert a retention_audit row. Returns the new row id, or None on
    failure. Never raises — fire-and-forget from any deletion call site.
    """
    try:
        from backend.models import RetentionAudit, db
        from flask import has_app_context
        if not has_app_context():
            logger.debug("record_deletion: no app context, skipping")
            return None
    except Exception as e:
        logger.warning("record_deletion: imports failed (%s)", e)
        return None

    try:
        row = RetentionAudit(
            actor=actor,
            kind=kind,
            operation=operation,
            item_count=int(item_count),
            bytes_freed=int(bytes_freed) if bytes_freed is not None else None,
            parameters=parameters or None,
            triggered_by=triggered_by,
        )
        db.session.add(row)
        db.session.commit()
        return row.id
    except Exception as e:
        logger.warning("record_deletion: insert failed (%s)", e)
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


def list_audit(
    *,
    kind: Optional[str] = None,
    operation: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict]:
    """Paginated audit read for export / Settings → Data Retention UI."""
    try:
        from backend.models import RetentionAudit, db
    except Exception as e:
        logger.warning("list_audit: imports failed (%s)", e)
        return []

    q = db.session.query(RetentionAudit).order_by(RetentionAudit.occurred_at.desc())
    if kind:
        q = q.filter(RetentionAudit.kind == kind)
    if operation:
        q = q.filter(RetentionAudit.operation == operation)
    rows = q.offset(offset).limit(limit).all()
    return [r.to_dict() for r in rows]
