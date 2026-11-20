"""Long jobs as scheduler rows: started on the backend's own queue, then followed.

A render batch runs in the backend process and takes minutes to hours. The
scheduler's worker runs one task at a time and the release beat runs on it,
so a task that waited for a batch would stop every other job. A hand-off
kind is started with the same HTTP call its page makes, the executor returns
at once, and the every-minute beat copies the batch's progress onto the
``tasks`` row until the batch ends.

A kind is declared once in ``HANDOFFS``; the executor and the beat read it
from there.
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

_START_TIMEOUT_S = 120
_STATUS_TIMEOUT_S = 20


@dataclass(frozen=True)
class Handoff:
    """One long job kind. A start route receives the row's
    ``workflow_config['request']`` as its JSON body and answers with a
    ``batch_id``; ``status`` and ``cancel`` are called with that id. A kind
    with more than one way to start (video from text or from images) names
    them in ``starts`` and the row picks one with ``workflow_config['variant']``."""

    starts: dict
    status: str
    cancel: str
    total: str
    done: str
    failed: str
    percent: str
    noun: str


HANDOFFS: dict[str, Handoff] = {
    "image_batch": Handoff(
        starts={"prompts": "/api/batch-image/generate/prompts"},
        status="/api/batch-image/status/{id}",
        cancel="/api/batch-image/cancel/{id}",
        total="total_images", done="completed_images", failed="failed_images",
        percent="progress_percentage", noun="images",
    ),
    "video_batch": Handoff(
        starts={
            "text": "/api/batch-video/generate/text",
            "image": "/api/batch-video/generate/image",
            "references": "/api/batch-video/generate/references",
        },
        status="/api/batch-video/status/{id}",
        cancel="/api/batch-video/batch/{id}/cancel",
        total="total_videos", done="completed_videos", failed="failed_videos",
        percent="progress_pct", noun="clips",
    ),
}

_ENDED = {"completed": "completed", "error": "failed", "failed": "failed", "cancelled": "cancelled"}


def is_handoff(task_type: Optional[str]) -> bool:
    return task_type in HANDOFFS


def _call(method: str, path: str, payload: Any = None, timeout: float = _STATUS_TIMEOUT_S):
    """One call to this checkout's backend, with its API key where it has one."""
    from backend.utils.backend_http import request_json

    return request_json(method, path, payload=payload, read_timeout=timeout, own_backend=True)


def start(task: dict) -> dict:
    """Start the row's batch on the backend. Returns ``{"handoff": {...}}``
    for the row's ``handler_config``, or ``{"error": ...}``."""
    from backend.utils.backend_http import BackendError

    kind = HANDOFFS[task["type"]]
    config = task.get("workflow_config") or {}
    request_body = config.get("request")
    if not isinstance(request_body, dict) or not request_body:
        return {"error": "the job has no saved request to run"}
    variant = config.get("variant") or next(iter(kind.starts))
    if variant not in kind.starts:
        return {"error": f"unknown way to start this job: {variant!r}"}
    try:
        data = _call("POST", kind.starts[variant], request_body, _START_TIMEOUT_S).data
    except BackendError as e:
        return {"error": str(e)}
    batch_id = data.get("batch_id") if isinstance(data, dict) else None
    if not batch_id:
        return {"error": "the backend started the job without naming its batch"}
    return {"handoff": {"kind": task["type"], "batch_id": str(batch_id),
                        "started_at": datetime.datetime.now().isoformat(timespec="seconds")}}


def cancel(task_type: Optional[str], handler_config: Any) -> bool:
    """Cancel the batch a handed-off row is following. False when the row
    has no batch yet or the backend did not cancel it."""
    from backend.utils.backend_http import BackendError

    if isinstance(handler_config, str):
        try:
            handler_config = json.loads(handler_config)
        except ValueError:
            handler_config = None
    ref = handler_config.get("handoff") if isinstance(handler_config, dict) else None
    if not is_handoff(task_type) or not ref or not ref.get("batch_id"):
        return False
    try:
        _call("POST", HANDOFFS[task_type].cancel.format(id=ref["batch_id"]))
        return True
    except BackendError as e:
        logger.warning("could not cancel batch %s: %s", ref["batch_id"], e)
        return False


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def sync(session_factory) -> dict:
    """Copy each running hand-off's batch state onto its row.

    Every pass stamps ``updated_at``, which is what keeps stuck-job recovery
    from re-running a row whose batch is still rendering. No answer from the
    backend leaves the row as it is: a restart is not a failure, and video
    batches pick up again from disk. A batch the backend no longer knows
    fails the row.
    """
    from backend.utils.backend_http import BackendError

    out = {"followed": 0, "ended": 0}
    s = session_factory()
    try:
        rows = s.execute(
            text("SELECT id, type, handler_config FROM tasks WHERE status = 'in-progress' AND type = ANY(:kinds)"),
            {"kinds": list(HANDOFFS)},
        ).fetchall()
        for task_id, task_type, config in rows:
            if isinstance(config, str):
                try:
                    config = json.loads(config)
                except ValueError:
                    config = None
            ref = (config or {}).get("handoff") if isinstance(config, dict) else None
            if not ref or not ref.get("batch_id"):
                continue  # the executor has not recorded the batch yet
            kind = HANDOFFS[task_type]
            out["followed"] += 1
            update: dict[str, Any] = {"utc": _utcnow(), "id": task_id}
            try:
                data = _call("GET", kind.status.format(id=ref["batch_id"])).data
            except BackendError as e:
                if e.status == 404:
                    update["err"] = f"batch {ref['batch_id']} is no longer known to the backend"
                    s.execute(text("UPDATE tasks SET status = 'failed', error_message = :err, updated_at = :utc "
                                   "WHERE id = :id AND status = 'in-progress'"), update)
                    out["ended"] += 1
                else:
                    s.execute(text("UPDATE tasks SET updated_at = :utc WHERE id = :id AND status = 'in-progress'"),
                              update)
                s.commit()
                continue
            if not isinstance(data, dict):
                data = {}
            total, done, failed = (int(data.get(k) or 0) for k in (kind.total, kind.done, kind.failed))
            try:
                percent = int(float(data.get(kind.percent)))
            except (TypeError, ValueError):
                percent = int(100 * (done + failed) / total) if total else 0
            ended = _ENDED.get(str(data.get("status") or "").lower())
            if ended is None:
                update["p"] = max(0, min(percent, 99))
                s.execute(text("UPDATE tasks SET progress = :p, updated_at = :utc "
                               "WHERE id = :id AND status = 'in-progress'"), update)
                s.commit()
                continue
            summary = f"{done} of {total} {kind.noun} done" + (f", {failed} failed" if failed else "")
            # A batch that ended with nothing rendered failed, whatever word it ended on.
            if ended == "completed" and total and done == 0:
                ended = "failed"
            failure = data.get("failure") if isinstance(data.get("failure"), dict) else {}
            update.update(
                st=ended, res=f"{summary}. Batch {ref['batch_id']}.",
                err=None if ended == "completed" else str(
                    failure.get("message") or data.get("error") or summary)[:1000],
                p=100 if ended == "completed" else max(0, min(percent, 99)),
            )
            s.execute(text("UPDATE tasks SET status = :st, result = :res, error_message = :err, progress = :p, "
                           "updated_at = :utc WHERE id = :id AND status = 'in-progress'"), update)
            s.commit()
            out["ended"] += 1
    finally:
        s.close()
    return out
