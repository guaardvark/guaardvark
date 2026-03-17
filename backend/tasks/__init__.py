"""
Backend Tasks Package
Celery tasks for training, CSV generation, task execution, and cleanup operations.
"""

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
    "parse_transcripts_task",
    "filter_dataset_task",
    "finetune_model_task",
    "export_gguf_task",
    "import_ollama_task",
    "full_training_pipeline_task",
    # CSV generation
    "generate_proven_csv_task",
    # Cleanup tasks
    "create_cleanup_tasks",
    "schedule_periodic_cleanup",
    # Unified task executor
    "execute_unified_task",
    # Task scheduler Beat tasks
    "check_scheduled_tasks",
    "recover_stuck_tasks",
    "scheduler_health_check",
]
