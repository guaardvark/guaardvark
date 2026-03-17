from .base_handler import (
    BaseTaskHandler,
    TaskResult,
    TaskResultStatus,
    HandlerRegistry,
    get_handler_registry,
)

from .csv_generation_handler import CSVGenerationHandler
from .batch_image_handler import BatchImageHandler
from .indexing_handler import DocumentIndexingHandler
from .code_operations_handler import CodeOperationsHandler
from .web_research_handler import WebResearchHandler
from .system_maintenance_handler import SystemMaintenanceHandler

__all__ = [
    "BaseTaskHandler",
    "TaskResult",
    "TaskResultStatus",
    "HandlerRegistry",
    "get_handler_registry",
    "CSVGenerationHandler",
    "BatchImageHandler",
    "DocumentIndexingHandler",
    "CodeOperationsHandler",
    "WebResearchHandler",
    "SystemMaintenanceHandler",
]


def register_all_handlers():
    registry = get_handler_registry()

    registry.register(CSVGenerationHandler())
    registry.register(BatchImageHandler())
    registry.register(DocumentIndexingHandler())
    registry.register(CodeOperationsHandler())
    registry.register(WebResearchHandler())
    registry.register(SystemMaintenanceHandler())

    return registry
