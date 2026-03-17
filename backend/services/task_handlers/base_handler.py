# backend/services/task_handlers/base_handler.py
# Base class for all task handlers in the unified scheduler
# Version 1.0

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Callable, List, Type
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class TaskResultStatus(Enum):
    """Result status for task execution"""

    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"  # Some items succeeded, some failed


@dataclass
class TaskResult:
    """Result of a task handler execution"""

    status: TaskResultStatus
    message: str
    output_data: Optional[Dict[str, Any]] = None
    output_files: List[str] = field(default_factory=list)
    error_message: Optional[str] = None
    items_processed: int = 0
    items_total: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "status": self.status.value,
            "message": self.message,
            "output_data": self.output_data,
            "output_files": self.output_files,
            "error_message": self.error_message,
            "items_processed": self.items_processed,
            "items_total": self.items_total,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "duration_seconds": self.duration_seconds,
        }


class BaseTaskHandler(ABC):
    """
    Abstract base class for all task handlers.

    Each handler wraps a specific feature (CSV gen, batch image, indexing, etc.)
    and provides a standardized interface for the scheduler to execute tasks.

    Usage:
        class CSVGenerationHandler(BaseTaskHandler):
            @property
            def handler_name(self) -> str:
                return "csv_generation"

            @property
            def process_type(self) -> str:
                return "csv_processing"

            def execute(self, task, config, progress_callback) -> TaskResult:
                # Implementation
                pass
    """

    def __init__(self):
        self._initialized = False

    @property
    @abstractmethod
    def handler_name(self) -> str:
        """
        Unique identifier for this handler.
        Used in task.task_handler field.
        Examples: 'csv_generation', 'batch_image', 'document_indexing'
        """
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """
        Human-readable name for UI display.
        Examples: 'CSV Generation', 'Batch Image Generation'
        """
        pass

    @property
    @abstractmethod
    def process_type(self) -> str:
        """
        ProcessType value for progress tracking.
        Must match a value from ProcessType enum.
        Examples: 'csv_processing', 'image_generation', 'indexing'
        """
        pass

    @property
    def celery_queue(self) -> str:
        """
        Celery queue to use for this handler's tasks.
        Override in subclass if different from default.
        """
        return "default"

    @property
    def default_priority(self) -> int:
        """Default priority for tasks using this handler (1=high, 5=normal, 10=low)"""
        return 5

    @property
    def default_max_retries(self) -> int:
        """Default maximum retry attempts"""
        return 3

    @property
    def default_retry_delay(self) -> int:
        """Default delay between retries in seconds"""
        return 60

    @property
    @abstractmethod
    def config_schema(self) -> Dict[str, Any]:
        """
        JSON Schema for handler_config validation.
        Defines required and optional configuration fields.

        Example:
            {
                "type": "object",
                "required": ["topics", "output_filename"],
                "properties": {
                    "topics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of topics to generate content for"
                    },
                    "output_filename": {
                        "type": "string",
                        "description": "Name of output CSV file"
                    },
                    "model_name": {
                        "type": "string",
                        "default": "default",
                        "description": "LLM model to use"
                    }
                }
            }
        """
        pass

    @abstractmethod
    def execute(
        self,
        task: Any,  # Task model instance
        config: Dict[str, Any],
        progress_callback: Callable[[int, str, Optional[Dict[str, Any]]], None],
    ) -> TaskResult:
        """
        Execute the task with the given configuration.

        Args:
            task: The Task model instance being executed
            config: Handler-specific configuration (from task.handler_config)
            progress_callback: Function to report progress
                - progress: 0-100 percentage
                - message: Status message
                - additional_data: Optional dict with extra info (e.g., items_completed)

        Returns:
            TaskResult with execution outcome

        Example:
            def execute(self, task, config, progress_callback):
                progress_callback(0, "Starting CSV generation...")

                topics = config.get("topics", [])
                for i, topic in enumerate(topics):
                    # Process topic
                    progress = int((i + 1) / len(topics) * 100)
                    progress_callback(progress, f"Processing topic {i+1}/{len(topics)}")

                return TaskResult(
                    status=TaskResultStatus.SUCCESS,
                    message="Generated CSV successfully",
                    output_files=["/path/to/output.csv"]
                )
        """
        pass

    def validate_config(self, config: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Validate handler configuration against schema.
        Override for custom validation beyond JSON schema.

        Returns:
            Tuple of (is_valid, error_message)
        """
        schema = self.config_schema
        required = schema.get("required", [])

        for field in required:
            if field not in config:
                return False, f"Missing required field: {field}"

        return True, None

    def before_execute(self, task: Any, config: Dict[str, Any]) -> Optional[str]:
        """
        Hook called before execute(). Override for pre-execution setup.
        Return error message to abort execution, or None to continue.
        """
        return None

    def after_execute(self, task: Any, result: TaskResult) -> None:
        """
        Hook called after execute(). Override for cleanup or post-processing.
        """
        pass

    def on_error(self, task: Any, error: Exception) -> None:
        """
        Hook called when execute() raises an exception.
        Override for custom error handling.
        """
        logger.error(f"Handler {self.handler_name} error for task {task.id}: {error}")

    def can_retry(self, task: Any, error: Exception) -> bool:
        """
        Determine if the task should be retried after an error.
        Override for custom retry logic.

        Args:
            task: The Task model instance
            error: The exception that occurred

        Returns:
            True if the task should be retried, False otherwise
        """
        # Default: retry on non-fatal errors
        fatal_errors = (KeyboardInterrupt, SystemExit, MemoryError)
        return not isinstance(error, fatal_errors)

    def get_estimated_duration(self, config: Dict[str, Any]) -> Optional[int]:
        """
        Estimate task duration in seconds based on configuration.
        Used for scheduling and UI display.
        Override for handler-specific estimates.

        Returns:
            Estimated seconds, or None if unknown
        """
        return None

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} handler_name='{self.handler_name}'>"


class HandlerRegistry:
    """
    Registry for task handlers.
    Manages handler registration and lookup.
    """

    _instance: Optional["HandlerRegistry"] = None
    _handlers: Dict[str, BaseTaskHandler] = {}

    def __new__(cls) -> "HandlerRegistry":
        """Singleton pattern"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._handlers = {}
        return cls._instance

    @classmethod
    def get_instance(cls) -> "HandlerRegistry":
        """Get the singleton instance"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, handler: BaseTaskHandler) -> None:
        """
        Register a handler instance.

        Args:
            handler: BaseTaskHandler subclass instance
        """
        name = handler.handler_name
        if name in self._handlers:
            logger.warning(f"Overwriting existing handler: {name}")
        self._handlers[name] = handler
        logger.info(f"Registered task handler: {name}")

    def register_class(self, handler_class: Type[BaseTaskHandler]) -> None:
        """
        Register a handler class (instantiates it).

        Args:
            handler_class: BaseTaskHandler subclass
        """
        handler = handler_class()
        self.register(handler)

    def get(self, handler_name: str) -> Optional[BaseTaskHandler]:
        """
        Get a handler by name.

        Args:
            handler_name: The handler_name property value

        Returns:
            Handler instance or None if not found
        """
        return self._handlers.get(handler_name)

    def get_all(self) -> Dict[str, BaseTaskHandler]:
        """Get all registered handlers"""
        return self._handlers.copy()

    def list_handlers(self) -> List[Dict[str, Any]]:
        """
        List all handlers with metadata for API responses.

        Returns:
            List of handler info dicts
        """
        return [
            {
                "name": handler.handler_name,
                "display_name": handler.display_name,
                "process_type": handler.process_type,
                "celery_queue": handler.celery_queue,
                "default_priority": handler.default_priority,
                "config_schema": handler.config_schema,
            }
            for handler in self._handlers.values()
        ]

    def unregister(self, handler_name: str) -> bool:
        """
        Unregister a handler.

        Returns:
            True if handler was removed, False if not found
        """
        if handler_name in self._handlers:
            del self._handlers[handler_name]
            logger.info(f"Unregistered task handler: {handler_name}")
            return True
        return False

    def clear(self) -> None:
        """Remove all registered handlers"""
        self._handlers.clear()


# Global registry instance
def get_handler_registry() -> HandlerRegistry:
    """Get the global handler registry instance"""
    return HandlerRegistry.get_instance()
