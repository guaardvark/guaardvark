"""Queue publishes as Task-backed jobs.

One PublishRecord and one Task per target: a failed YouTube upload must not
roll back a successful Bluesky post, and per-connection Tasks give each
platform its own progress bar on the Jobs page.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from backend.services.connections import gates, media as media_util, registry

logger = logging.getLogger(__name__)

TASK_TYPE = "connection_publish"
# Video uploads routinely outlast the executor's default soft limit.
PUBLISH_SOFT_TIME_LIMIT = 7200
PUBLISH_TIME_LIMIT = 7500


def preflight(
    connection_ids: List[int],
    document_ids: List[int],
    *,
    body: str = "",
    title: Optional[str] = None,
    visibility: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate without queueing, for live feedback in the compose modal."""
    from backend.models import Connection

    try:
        items = media_util.resolve_media(document_ids)
    except media_util.MediaResolveError as e:
        return {"ok": False, "per_connection": {}, "violations": [str(e)]}

    per_connection: Dict[str, Any] = {}
    for cid in connection_ids or []:
        connection = Connection.query.get(cid)
        if connection is None:
            per_connection[str(cid)] = {"violations": ["Connection not found."]}
            continue
        problems = _connection_problems(connection)
        final_body = _body_with_disclosure(body, items, connection)
        if not problems:
            caps = registry.spec_for(connection.provider).capabilities
            problems = media_util.validate_against(
                caps, items, body=final_body, title=title, visibility=visibility
            )
        per_connection[str(cid)] = {
            "provider": connection.provider,
            "label": connection.display_name or connection.provider,
            "violations": problems,
            "body": final_body,
        }

    return {
        "ok": all(not v["violations"] for v in per_connection.values()) and bool(per_connection),
        "per_connection": per_connection,
        "violations": [],
    }


def _discloses_ai_media(connection) -> bool:
    """Per-connection opt-out for the disclosure line (config.disclose_ai_media,
    default on). Config is the connection's non-secret JSON options."""
    try:
        cfg = json.loads(connection.config) if isinstance(connection.config, str) else (connection.config or {})
    except (ValueError, TypeError):
        cfg = {}
    return bool(cfg.get("disclose_ai_media", True))


def _body_with_disclosure(body: str, items, connection) -> str:
    line = media_util.disclosure_line(items)
    if not line or not _discloses_ai_media(connection):
        return body
    text = (body or "").rstrip()
    if line in text:
        return body
    return f"{text}\n\n{line}" if text else line


def _connection_problems(connection) -> List[str]:
    if not connection.enabled:
        return ["This connection is disabled."]
    if connection.status not in ("connected", "unconfigured"):
        return [connection.error_message or f"Connection is {connection.status}."]
    try:
        registry.spec_for(connection.provider)
    except KeyError:
        return [f"Provider '{connection.provider}' is not available."]
    return []


def queue_publish(
    *,
    connection_ids: List[int],
    document_ids: Optional[List[int]] = None,
    body: str = "",
    title: Optional[str] = None,
    link_url: Optional[str] = None,
    tags: Optional[List[str]] = None,
    visibility: Optional[str] = None,
    requested_by: str = "ui",
) -> Dict[str, Any]:
    """Create one PublishRecord + Task per connection and dispatch them.

    Raises:
        ValueError: nothing selected, or the post violates a target's limits.
        RuntimeError: publishing is gated off.
    """
    from backend.models import Connection, PublishRecord, db

    if not connection_ids:
        raise ValueError("Select at least one connection to publish to.")

    if not gates.publish_enabled():
        raise RuntimeError("Publishing is disabled.")

    items = media_util.resolve_media(document_ids or [])
    needs_approval = gates.requires_approval(requested_by)

    queued: List[Dict[str, Any]] = []
    for cid in connection_ids:
        connection = Connection.query.get(cid)
        if connection is None:
            raise ValueError(f"Connection {cid} not found.")

        problems = _connection_problems(connection)
        spec = registry.spec_for(connection.provider)
        target_visibility = visibility or spec.capabilities.default_visibility
        final_body = _body_with_disclosure(body, items, connection)
        problems += media_util.validate_against(
            spec.capabilities, items, body=final_body, title=title, visibility=target_visibility
        )
        if problems:
            raise ValueError(f"{spec.label}: {problems[0]}")

        record = PublishRecord(
            connection_id=connection.id,
            platform=connection.provider,
            document_id=items[0].document_id if items else None,
            media_refs=media_util.media_refs_json(items),
            title=title,
            body=final_body,
            link_url=link_url,
            tags=json.dumps(tags or []),
            visibility=target_visibility,
            status="awaiting_approval" if needs_approval else "queued",
            requested_by=requested_by,
        )
        db.session.add(record)
        db.session.commit()

        if needs_approval:
            queued.append({"publish_record_id": record.id, "status": record.status})
            continue

        queued.append(_dispatch(record, connection, spec))

    return {
        "queued": queued,
        "requires_approval": needs_approval,
        "count": len(queued),
    }


def _dispatch(record, connection, spec) -> Dict[str, Any]:
    """Create the Task row and hand it to Celery."""
    from backend.models import Task, db

    task = Task(
        name=f"Publish to {spec.label}",
        description=f"Publish record {record.id} to {spec.label}",
        status="queued",
        priority=2,
        type=TASK_TYPE,
        workflow_config=json.dumps(
            {"publish_record_id": record.id, "platform": connection.provider}
        ),
        schedule_type="immediate",
        task_handler="connections",
        handler_config={"platform": connection.provider, "source": record.requested_by},
        progress=0,
    )
    db.session.add(task)
    db.session.commit()

    task.job_id = f"task_{task.id}"
    record.task_id = task.id
    db.session.commit()

    try:
        from backend.tasks.unified_task_executor import execute_unified_task

        celery_result = execute_unified_task.apply_async(
            args=[task.id],
            queue="default",
            soft_time_limit=PUBLISH_SOFT_TIME_LIMIT,
            time_limit=PUBLISH_TIME_LIMIT,
        )
    except Exception as exc:  # noqa: BLE001
        task.status = "failed"
        task.error_message = f"Failed to queue publish: {exc}"
        record.status = "failed"
        record.error_message = str(exc)
        db.session.commit()
        logger.exception("Failed to enqueue publish task for record %s", record.id)
        raise RuntimeError(str(exc)) from exc

    return {
        "publish_record_id": record.id,
        "task_id": task.id,
        "job_id": task.job_id,
        "celery_task_id": celery_result.id,
        "status": record.status,
        "platform": connection.provider,
    }


def approve(record) -> Dict[str, Any]:
    """Release a supervised publish to the queue."""
    from backend.models import Connection, db

    if record.status != "awaiting_approval":
        raise ValueError(f"Cannot approve a record that is '{record.status}'.")
    connection = Connection.query.get(record.connection_id)
    if connection is None:
        raise ValueError("The target connection no longer exists.")

    record.status = "queued"
    db.session.commit()
    return _dispatch(record, connection, registry.spec_for(connection.provider))


def reject(record, reason: str = "") -> None:
    from backend.models import db

    if record.status not in ("awaiting_approval", "queued"):
        raise ValueError(f"Cannot reject a record that is '{record.status}'.")
    record.status = "rejected"
    record.error_message = reason or "Rejected by the operator."
    db.session.commit()


def cancel(record) -> None:
    from backend.models import db

    if record.status in ("posted", "failed", "cancelled", "rejected"):
        raise ValueError(f"Cannot cancel a record that is '{record.status}'.")
    # The runner checks status once, on entry. Flipping a record that is
    # already mid-flight would report a cancellation that never happened — the
    # publish can still go out and then overwrite itself to 'posted'. Cancel
    # the underlying job instead, which can actually revoke the work.
    if record.status == "processing":
        raise ValueError(
            "This publish is already being sent. Cancel its job from Activity "
            "to stop it."
        )
    record.status = "cancelled"
    db.session.commit()
