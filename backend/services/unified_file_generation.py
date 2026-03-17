#!/usr/bin/env python3
"""
Unified File Generation Service
Consolidates all file generation capabilities into a single coordinated service
"""

import logging
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class GenerationType(Enum):
    """Types of file generation supported"""

    CSV_BULK = "csv_bulk"
    CSV_BATCH = "csv_batch"
    SINGLE_FILE = "single_file"
    MULTI_FORMAT = "multi_format"


@dataclass
class GenerationRequest:
    """Unified request structure for all generation types"""

    generation_type: GenerationType
    output_filename: str
    content_spec: Dict[str, Any]  # Content specifications
    format_options: Dict[str, Any] = None  # Format-specific options
    template_name: str = "generic"
    context_variables: Dict[str, str] = None

    def __post_init__(self):
        if self.format_options is None:
            self.format_options = {}
        if self.context_variables is None:
            self.context_variables = {}


@dataclass
class GenerationResult:
    """Unified result structure for all generation types"""

    success: bool
    output_path: str = None
    job_id: str = None
    error_message: str = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class UnifiedFileGenerationService:
    """
    Unified service that coordinates all file generation capabilities
    """

    def __init__(self):
        self.generators = {}
        self._register_generators()

    def _register_generators(self):
        """Register all available generation handlers"""
        try:
            # CSV Bulk Generation
            from backend.utils.bulk_csv_generator import BulkCSVGenerator

            self.generators[GenerationType.CSV_BULK] = self._handle_csv_bulk

            # Enhanced File Processor
            from backend.utils.enhanced_file_processor import create_file_processor

            self.file_processor = create_file_processor()
            self.generators[GenerationType.MULTI_FORMAT] = self._handle_multi_format

            # CSV Formatter
            from backend.utils.csv_formatter import CSVFormatter

            self.csv_formatter = CSVFormatter()
            self.generators[GenerationType.CSV_BATCH] = self._handle_csv_batch

            logger.info(
                "Unified file generation service initialized with all generators"
            )

        except ImportError as e:
            logger.error(f"Failed to initialize some generators: {e}")

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """
        Main generation method that routes to appropriate handler
        """
        try:
            if request.generation_type not in self.generators:
                return GenerationResult(
                    success=False,
                    error_message=f"Unsupported generation type: {request.generation_type}",
                )

            handler = self.generators[request.generation_type]
            return handler(request)

        except Exception as e:
            logger.error(f"Generation failed: {e}", exc_info=True)
            return GenerationResult(success=False, error_message=str(e))

    def _handle_csv_bulk(self, request: GenerationRequest) -> GenerationResult:
        """Handle bulk CSV generation requests"""
        try:
            from backend.utils.bulk_csv_generator import (
                BulkCSVGenerator,
                create_tasks_from_topics,
            )
            from backend.api.bulk_generation_api import get_output_dir
            from flask import current_app

            # Extract content specifications
            spec = request.content_spec
            topics = spec.get("topics", [])
            client = request.context_variables.get("client", "Client")
            project = request.context_variables.get("project", "Content Generation")
            website = request.context_variables.get("website", "website.com")

            # Create generator
            output_dir = current_app.config.get("OUTPUT_DIR")
            generator = BulkCSVGenerator(
                output_dir=output_dir,
                concurrent_workers=spec.get("concurrent_workers", 5),
                target_word_count=spec.get("target_word_count", 500),
            )

            # Create tasks
            tasks = create_tasks_from_topics(topics, client, project, website)

            # Generate CSV
            output_path, stats = generator.generate_bulk_csv(
                tasks, request.output_filename
            )

            return GenerationResult(
                success=True, output_path=output_path, metadata=stats
            )

        except Exception as e:
            logger.error(f"Bulk CSV generation failed: {e}")
            return GenerationResult(success=False, error_message=str(e))

    def _handle_csv_batch(self, request: GenerationRequest) -> GenerationResult:
        """Handle batch CSV generation with templates"""
        try:
            # Use CSV formatter for proper template handling
            template = self.csv_formatter.templates.get(
                request.template_name, self.csv_formatter.templates["general"]
            )

            # Generate structured prompt
            user_request = request.content_spec.get(
                "description", "Generate CSV content"
            )
            prompt = self.csv_formatter.generate_structured_csv_prompt(
                user_request, template
            )

            # This would integrate with LLM generation
            # For now, return success with template info
            return GenerationResult(
                success=True,
                metadata={
                    "template_used": request.template_name,
                    "prompt_generated": True,
                    "prompt_length": len(prompt),
                },
            )

        except Exception as e:
            logger.error(f"Batch CSV generation failed: {e}")
            return GenerationResult(success=False, error_message=str(e))

    def _handle_multi_format(self, request: GenerationRequest) -> GenerationResult:
        """Handle multi-format file generation"""
        try:
            # Use enhanced file processor for format detection and generation
            from backend.utils.enhanced_file_processor import FileFormat

            # Detect format from filename
            file_format = self.file_processor.detect_format(request.output_filename)

            if not file_format:
                return GenerationResult(
                    success=False,
                    error_message="Could not detect file format from filename",
                )

            # Generate file using appropriate processor
            content = request.content_spec.get("content", "")
            success = self.file_processor.generate_file(
                content, request.output_filename, file_format, **request.format_options
            )

            return GenerationResult(
                success=success,
                output_path=request.output_filename if success else None,
                metadata={"format": file_format.value},
            )

        except Exception as e:
            logger.error(f"Multi-format generation failed: {e}")
            return GenerationResult(success=False, error_message=str(e))

    def get_supported_types(self) -> List[GenerationType]:
        """Get list of supported generation types"""
        return list(self.generators.keys())

    def get_available_templates(self) -> Dict[str, List[str]]:
        """Get available templates for each generation type"""
        templates = {}

        try:
            # CSV templates
            if hasattr(self, "csv_formatter"):
                templates["csv"] = list(self.csv_formatter.templates.keys())

            # Context templates
            from backend.utils.context_variables import context_manager

            templates["context"] = list(context_manager.templates.keys())

        except Exception as e:
            logger.warning(f"Could not load all templates: {e}")

        return templates


# Global service instance
_unified_service = None


def get_unified_file_generation_service() -> UnifiedFileGenerationService:
    """Get the global unified file generation service instance"""
    global _unified_service
    if _unified_service is None:
        _unified_service = UnifiedFileGenerationService()
    return _unified_service


# Convenience functions for common operations
def generate_csv_bulk(
    topics: List[str], client: str, project: str, output_filename: str, **kwargs
) -> GenerationResult:
    """Generate bulk CSV content"""
    service = get_unified_file_generation_service()
    request = GenerationRequest(
        generation_type=GenerationType.CSV_BULK,
        output_filename=output_filename,
        content_spec={
            "topics": topics,
            "concurrent_workers": kwargs.get("concurrent_workers", 5),
            "target_word_count": kwargs.get("target_word_count", 500),
        },
        context_variables={
            "client": client,
            "project": project,
            "website": kwargs.get("website", "website.com"),
        },
    )
    return service.generate(request)


def generate_file_multi_format(
    content: str, output_filename: str, **format_options
) -> GenerationResult:
    """Generate file in any supported format"""
    service = get_unified_file_generation_service()
    request = GenerationRequest(
        generation_type=GenerationType.MULTI_FORMAT,
        output_filename=output_filename,
        content_spec={"content": content},
        format_options=format_options,
    )
    return service.generate(request)
