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
    """One long job kind. ``start`` receives the row's
    ``workflow_config['request']`` as its JSON body and answers with a
    ``batch_id``; ``status`` is read with that id."""

    start: str
    status: str
    total: str
    done: str
    failed: str
    percent: str
    noun: str


HANDOFFS: dict[str, Handoff] = {
    "image_batch": Handoff(
        start="/api/batch-image/generate/prompts",
        status="/api/batch-image/status/{id}",
        total="total_images", done="completed_images", failed="failed_images",
        percent="progress_percentage", noun="images",
    ),
}

_ENDED = {"completed": "completed", "error": "failed", "failed": "failed", "cancelled": "cancelled"}


def is_handoff(task_type: Optional[str]) -> bool:
    return task_type in HANDOFFS


def _base() -> str:
    from backend.utils.backend_http import own_backend_url

    return own_backend_url()


def _payload(resp) -> dict:
    """The data of a success_response, or the body itself."""
    body = resp.json() if resp.content else {}
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, dict) else (body if isinstance(body, dict) else {})


def start(task: dict) -> dict:
    """Start the row's batch on the backend. Returns ``{"handoff": {...}}``
    for the row's ``handler_config``, or ``{"error": ...}``."""
    import requests

    kind = HANDOFFS[task["type"]]
    request_body = (task.get("workflow_config") or {}).get("request")
    if not isinstance(request_body, dict) or not request_body:
        return {"error": "the job has no saved request to run"}
    try:
        resp = requests.post(f"{_base()}{kind.start}", json=request_body, timeout=_START_TIMEOUT_S)
    except requests.RequestException as e:
        return {"error": f"the backend did not take the job ({type(e).__name__}: {e})"}
    body = resp.json() if resp.content else {}
    if resp.status_code >= 400:
        message = (body.get("message") or body.get("error")) if isinstance(body, dict) else None
        return {"error": str(message or f"the backend refused the job ({resp.status_code})")}
    batch_id = _payload(resp).get("batch_id")
    if not batch_id:
        return {"error": "the backend started the job without naming its batch"}
    return {"handoff": {"kind": task["type"], "batch_id": str(batch_id),
                        "started_at": datetime.datetime.now().isoformat(timespec="seconds")}}


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
    import requests

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
                resp = requests.get(f"{_base()}{kind.status.format(id=ref['batch_id'])}", timeout=_STATUS_TIMEOUT_S)
            except requests.RequestException:
                s.execute(text("UPDATE tasks SET updated_at = :utc WHERE id = :id AND status = 'in-progress'"), update)
                s.commit()
                continue
            if resp.status_code == 404:
                update["err"] = f"batch {ref['batch_id']} is no longer known to the backend"
                s.execute(text("UPDATE tasks SET status = 'failed', error_message = :err, updated_at = :utc "
                               "WHERE id = :id AND status = 'in-progress'"), update)
                s.commit()
                out["ended"] += 1
                continue
            if resp.status_code >= 400:
                s.execute(text("UPDATE tasks SET updated_at = :utc WHERE id = :id AND status = 'in-progress'"), update)
                s.commit()
                continue
            data = _payload(resp)
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
