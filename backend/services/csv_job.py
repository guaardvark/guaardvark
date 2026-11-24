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


def csv_request(task: Any) -> dict:
    """The ``/api/bulk-generate/csv`` body for a saved row (a dict or a Task)."""
    config = _config(_get(task, "workflow_config"))
    items = [str(i).strip() for i in (config.get("items") or config.get("topics") or []) if str(i).strip()]
    pages = (_positive_int(config.get("page_count")) or _positive_int(config.get("items_count"))
             or len(items) or _DEFAULT_PAGES)
    body = {
        "output_filename": config.get("output_filename") or _get(task, "output_filename")
        or f"job_{_get(task, 'id')}.csv",
        "topics": items,
        "num_items": pages,
        "project": config.get("project") or _get(task, "name") or "Content Generation",
        "website": _get(task, "target_website") or config.get("target_website") or config.get("website") or "",
        "client_notes": config.get("client_notes") or "",
        "competitor_url": config.get("competitor_url") or _get(task, "competitor_url") or "",
        "target_word_count": _positive_int(config.get("target_word_count")) or 1000,
        "concurrent_workers": _positive_int(config.get("concurrent_workers")) or 3,
        "batch_size": _positive_int(config.get("batch_size")) or 10,
        "insert_content": config.get("insert_content") or None,
        "insert_position": config.get("insert_position") or None,
        "prompt_rule_id": config.get("prompt_rule_id") or None,
        "model_name": _get(task, "model_name") or None,
        "existing_task_id": _get(task, "id"),
    }
    # Without a client name the writer's own default applies.
    client = (_get(task, "client_name") or config.get("client_name") or config.get("client") or "").strip()
    if client:
        body["client"] = client
    return body


def start(task: Any) -> dict:
    """Start the writer for a saved row. Returns the writer's reply (``job_id``,
    ``output_filename``) or ``{"error": ...}``.

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
            return {"error": str(data.get("error") or f"the CSV writer refused the job (HTTP {status})")}
        return data

    from backend.utils import backend_http

    try:
        return backend_http.request_json("POST", "/api/bulk-generate/csv", payload=body,
                                         read_timeout=_START_TIMEOUT_S, own_backend=True).data or {}
    except backend_http.BackendError as e:
        return {"error": str(e)}
