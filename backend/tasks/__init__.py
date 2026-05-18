"""
Backend Tasks Package
Celery tasks for training, CSV generation, task execution, and cleanup operations.
"""

# Importing celery_app FIRST runs celery.set_default(), binding @shared_task
# decorators (in this package and its submodules) to the configured Celery
# instance — not the empty default Celery() that the library creates on first
# lookup. Without this import, standalone dispatchers that do
# `from backend.tasks.X import some_task; some_task.delay()` route to the
# literal 'celery' queue instead of 'default', producing ghost task_ids the
# worker never receives. See test_celery_routing.py for the regression suite.
from backend import celery_app  # noqa: F401

from .training_tasks import (
    parse_transcripts_task,
    filter_dataset_task,
    finetune_model_task,
    export_gguf_task,
    import_ollama_task,
    full_training_pipeline_task,
)

from .proven_csv_generation import generate_proven_csv_task

from .cleanup_tasks import create_cleanup_tasks, schedule_periodic_cleanup

# Unified task executor - routes task execution through Celery
from .unified_task_executor import execute_unified_task

# Task scheduler Beat tasks - periodic task checking and stuck task recovery
from .task_scheduler_celery import (
    check_scheduled_tasks,
    recover_stuck_tasks,
    scheduler_health_check,
)

__all__ = [
    # Training tasks
    'parse_transcripts_task',
    'filter_dataset_task',
    'finetune_model_task',
    'export_gguf_task',
    'import_ollama_task',
    'full_training_pipeline_task',
    # CSV generation
    'generate_proven_csv_task',
    # Cleanup tasks
    'create_cleanup_tasks',
    'schedule_periodic_cleanup',
    # Unified task executor
    'execute_unified_task',
    # Task scheduler Beat tasks
    'check_scheduled_tasks',
    'recover_stuck_tasks',
    'scheduler_health_check',
]
