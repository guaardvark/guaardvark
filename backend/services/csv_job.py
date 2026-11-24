"""Start a saved CSV file job with the structured CSV writer.

The Jobs page saves a CSV job's items, page count and page settings in the
row's ``workflow_config``. However the job is started (Create and start, a
card's Start, Run now, the night shift, a re-run), the row reaches the
unified executor, which starts it here. The request is built from the saved
row and handed to the writer behind ``POST /api/bulk-generate/csv``, which
runs in the backend process and keeps the row's status until the file is
written (backend/api/bulk_generation_api.py).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

CSV_TYPES = frozenset({"file_generation", "csv_generation"})
# A row's job_id while the writer's thread runs it. Celery has never heard of
# these ids, so stuck-job recovery reads them as queued and leaves them alone.
WRITER_JOB_PREFIX = "bulk_gen_"
# The wording hand-off jobs use when the backend restarted under them (job_handoff.sync).
INTERRUPTED = "the backend restarted while this job was running"
_OPEN_STATUSES = ("pending", "queued", "in-progress")
_START_TIMEOUT_S = 120
# The page count the New job form starts with.
_DEFAULT_PAGES = 50


def _config(workflow_config: Any) -> dict:
    if isinstance(workflow_config, str):
        try:
            workflow_config = json.loads(workflow_config)
        except ValueError:
            return {}
    return workflow_config if isinstance(workflow_config, dict) else {}


def _get(task: Any, key: str) -> Any:
    return task.get(key) if isinstance(task, dict) else getattr(task, key, None)


def _positive_int(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def is_csv_job(task_type: Optional[str], workflow_config: Any) -> bool:
    """True for a row the structured CSV writer runs."""
    return task_type in CSV_TYPES and bool(_config(workflow_config))


def writer_holds(task: Any) -> bool:
    """True when a writer already runs this row (a redelivered or retried start)."""
    return (is_csv_job(_get(task, "type"), _get(task, "workflow_config"))
            and str(_get(task, "job_id") or "").startswith(WRITER_JOB_PREFIX)
            and _get(task, "status") in _OPEN_STATUSES)


def _items(value: Any) -> list:
    """Saved items as a list; a single value such as ``"auto"`` is not a list of items."""
    if not isinstance(value, (list, tuple)):
        return []
    return [str(i).strip() for i in value if str(i).strip()]


def csv_request(task: Any) -> dict:
    """The ``/api/bulk-generate/csv`` body for a saved row (a dict or a Task)."""
    config = _config(_get(task, "workflow_config"))
    items = _items(config.get("items")) or _items(config.get("topics"))
    # The File Generation page saves the competitor site as target_website and
    # has no competitor_url key; the New job form saves both.
    page_saved = "context_mode" in config and "competitor_url" not in config
    pages = (_positive_int(config.get("page_count")) or _positive_int(config.get("items_count"))
             or len(items) or _DEFAULT_PAGES)
    body = {
        "output_filename": config.get("output_filename") or _get(task, "output_filename")
        or f"job_{_get(task, 'id')}.csv",
        "topics": items,
        "num_items": pages,
        "project": config.get("project") or _get(task, "name") or "Content Generation",
        "website": (_get(task, "target_website") or config.get("client_website")
                    or (None if page_saved else config.get("target_website")) or config.get("website") or ""),
        "client_notes": config.get("client_notes") or "",
        "competitor_url": (config.get("competitor_url") or _get(task, "competitor_url")
                           or (config.get("target_website") if page_saved else None) or ""),
        "target_word_count": _positive_int(config.get("target_word_count")) or 1000,
        "concurrent_workers": _positive_int(config.get("concurrent_workers")) or 3,
        "batch_size": _positive_int(config.get("batch_size")) or 10,
        "insert_content": config.get("insert_content") or None,
        "insert_position": config.get("insert_position") or None,
        "prompt_rule_id": config.get("prompt_rule_id") or None,
        "model_name": _get(task, "model_name") or None,
        "existing_task_id": _get(task, "id"),
        "client_id": config.get("client_id") or _get(task, "client_id"),
        "website_id": config.get("website_id") or _get(task, "website_id"),
        "project_id": _get(task, "project_id"),
    }
    for key in ("context_mode", "use_entity_context", "use_competitor_analysis",
                "use_document_intelligence", "project_notes"):
        if config.get(key) not in (None, ""):
            body[key] = config[key]
    # Without a client name the writer's own default applies.
    client = (_get(task, "client_name") or config.get("client_name") or config.get("client") or "").strip()
    if client:
        body["client"] = client
    return body


def start(task: Any) -> dict:
    """Start the writer for a saved row. Returns the writer's reply (``job_id``,
    ``output_filename``) or ``{"error": ...}``, with ``refused`` set when the
    writer turned the request down and starting again would not help.

    In the backend process the writer is called directly; from the Celery
    worker it is reached over this checkout's own backend, where its thread
    and the RAG index live.
    """
    body = csv_request(task)
    from flask import current_app, has_app_context

    if has_app_context() and current_app.config.get("OUTPUT_DIR"):
        from backend.api.bulk_generation_api import start_bulk_csv_job

        response = start_bulk_csv_job(body)
        reply, status = response if isinstance(response, tuple) else (response, 200)
        data = reply.get_json(silent=True) or {}
        if status >= 400:
            return {"error": str(data.get("error") or f"the CSV writer refused the job (HTTP {status})"),
                    "refused": status < 500}
        return data

    from backend.utils import backend_http

    try:
        return backend_http.request_json("POST", "/api/bulk-generate/csv", payload=body,
                                         read_timeout=_START_TIMEOUT_S, own_backend=True).data or {}
    except backend_http.BackendError as e:
        return {"error": str(e), "refused": e.kind == "http" and 400 <= (e.status or 0) < 500}


def fail_interrupted(app) -> int:
    """At boot: fail the rows a writer thread still held open.

    The writer runs in a daemon thread of the backend process, so once the
    backend starts again no such row has anything running it. The row is
    failed, not re-run, and its progress entry ends; Start runs it again.
    Server only. Returns the number of rows failed; never raises.
    """
    import datetime

    try:
        with app.app_context():
            from backend.models import Task, db

            rows = (Task.query
                    .filter(Task.job_id.like(f"{WRITER_JOB_PREFIX}%"), Task.status.in_(_OPEN_STATUSES))
                    .all())
            if not rows:
                return 0
            now = datetime.datetime.now(datetime.timezone.utc)
            job_ids = []
            for row in rows:
                row.status = "failed"
                row.error_message = INTERRUPTED
                row.updated_at = now
                job_ids.append(row.job_id)
            db.session.commit()
    except Exception:
        logger.warning("CSV writer row sweep skipped", exc_info=True)
        return 0

    try:
        from backend.utils.unified_progress_system import get_unified_progress

        progress = get_unified_progress()
        for job_id in job_ids:
            progress.error_process(job_id, INTERRUPTED)
    except Exception:
        logger.debug("progress entries for interrupted CSV jobs not closed", exc_info=True)
    logger.warning("Failed %d CSV job(s) a restart interrupted", len(job_ids))
    return len(job_ids)
