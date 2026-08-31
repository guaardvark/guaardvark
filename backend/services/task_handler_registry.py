"""Task-type handlers an extension registers for the unified task executor.

The executor (backend/tasks/unified_task_executor.py) routes a Task row by
its ``type``. Core types are handled in-line there; a vertical's types used
to need an ``if task_type in (...)`` edit in that file. Registering here at
import time — from the extension's api/ or tasks/ module — keeps the
executor untouched.

A handler is ``fn(task: dict, update_progress: Callable[[int, str], None]) -> Any``
and returns the task output (any JSON-serialisable value) or raises.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

TaskHandler = Callable[[dict, Callable[[int, str], None]], Any]
_handlers: dict[str, TaskHandler] = {}


def register_task_handler(task_type: str, handler: TaskHandler, *, replace: bool = False) -> None:
    if not task_type or not callable(handler):
        raise ValueError("register_task_handler needs a task type and a callable")
    if task_type in _handlers and not replace:
        logger.warning("task handler for %r already registered; keeping the first", task_type)
        return
    _handlers[task_type] = handler


def get_task_handler(task_type: Optional[str]) -> Optional[TaskHandler]:
    return _handlers.get(task_type or "")


def registered_task_types() -> list[str]:
    return sorted(_handlers)


def clear_task_handlers() -> None:
    """Test hook."""
    _handlers.clear()
