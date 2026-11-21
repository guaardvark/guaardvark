"""Which task rows have a runner of their own in the unified executor.

Two loops release pending rows: the scheduler beat hands them to
``execute_unified_task``, and the in-process loop in ``task_scheduler`` runs
a row as one LLM prompt. A row whose type has its own runner (an outreach
pass, a website crawl, a publish, a render batch) must not go through the
second, or it is answered by the chat model instead of doing its work.

Keep this in step with the runner chain in
``backend/tasks/unified_task_executor.execute_unified_task``.
"""

from __future__ import annotations

from typing import Any, Optional

_OWN_RUNNER_PREFIXES = ("social_outreach_", "website_")
_OWN_RUNNER_TYPES = frozenset({"connection_publish"})
_WORKFLOW_TYPES = frozenset({"file_generation", "csv_generation"})


def has_own_runner(task_type: Optional[str], workflow_config: Any = None) -> bool:
    """True when the unified executor runs this row with a dedicated runner."""
    if not task_type:
        return False
    if task_type in _OWN_RUNNER_TYPES or task_type.startswith(_OWN_RUNNER_PREFIXES):
        return True
    if task_type in _WORKFLOW_TYPES and workflow_config:
        return True
    from backend.services.job_handoff import is_handoff
    from backend.services.task_handler_registry import get_task_handler

    return is_handoff(task_type) or get_task_handler(task_type) is not None
