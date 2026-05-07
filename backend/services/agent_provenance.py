"""Persist agent/tool outcomes for audit (AgentActionProvenance)."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def record_tool_outcome(
    session_id: str,
    request_id: Optional[str],
    iteration: Optional[int],
    tool_name: str,
    params: Optional[Dict[str, Any]],
    outcome_success: bool,
    outcome_preview: Optional[str],
    approval_scope: Optional[str] = None,
    approved: Optional[bool] = None,
) -> None:
    """Best-effort insert; never raises to callers."""
    try:
        from backend.models import AgentActionProvenance, db

        snap = None
        if params is not None:
            try:
                snap = dict(params)
                if len(str(snap)) > 4000:
                    snap = {"_truncated": True, "keys": list(snap.keys())}
            except Exception:
                snap = {"_error": "non-serializable-params"}

        row = AgentActionProvenance(
            session_id=session_id,
            request_id=request_id,
            iteration=iteration,
            tool_name=tool_name,
            params_snapshot=snap,
            approval_scope=approval_scope,
            approved=approved,
            outcome_success=outcome_success,
            outcome_preview=(outcome_preview or "")[:2000] if outcome_preview else None,
        )
        db.session.add(row)
        db.session.commit()
    except Exception as e:
        logger.debug("agent provenance insert skipped: %s", e)
        try:
            from backend.models import db

            db.session.rollback()
        except Exception:
            pass
