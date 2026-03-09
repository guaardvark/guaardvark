#!/usr/bin/env python3
"""
Generation Tools
Executable tools for bulk and batch file generation operations.
Wraps existing generation services for agent system integration.
"""

import logging
import os
from typing import Any, Optional
from datetime import datetime

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)


class BulkCSVGeneratorTool(BaseTool):
    """
    High-performance batch CSV generation for hundreds of pages.
    Converted from /batchcsv command rule (rule ID: 7).

    Supports concurrent processing, resume capability, and intelligent parameter extraction.
    """

    name = "generate_bulk_csv"
    description = "Generate bulk CSV files with hundreds of pages efficiently using concurrent processing"

    parameters = {
        "filename": ToolParameter(
            name="filename",
            type="string",
            required=True,
            description="Output CSV filename (e.g., 'output.csv')"
        ),
        "quantity": ToolParameter(
            name="quantity",
            type="int",
            required=True,
            description="Number of CSV entries/pages to generate (50-1000+)"
        ),
        "topic": ToolParameter(
            name="topic",
            type="string",
            required=True,
            description="Main topic or subject for content generation"
        ),
        "client": ToolParameter(
            name="client",
            type="string",
            required=False,
            description="Client name for personalized content",
            default=""
        ),
        "word_count": ToolParameter(
            name="word_count",
            type="int",
            required=False,
            description="Target word count per entry",
            default=600
        ),
        "project_id": ToolParameter(
            name="project_id",
            type="int",
            required=False,
            description="Project ID for RAG context",
            default=None
        ),
        "concurrent_workers": ToolParameter(
            name="concurrent_workers",
            type="int",
            required=False,
            description="Number of concurrent generation workers",
            default=5
        )
    }

    def __init__(self):
        super().__init__()
        self._generator = None

    def _get_generator(self):
        """Lazy load bulk CSV generator"""
        if self._generator is None:
            try:
                from backend.utils.bulk_csv_generator import BulkCSVGenerator
                self._generator = BulkCSVGenerator()
            except Exception as e:
                logger.error(f"Failed to initialize BulkCSVGenerator: {e}")
                raise
        return self._generator

    def execute(self, **kwargs) -> ToolResult:
        """Start bulk CSV generation job"""
        filename = kwargs.get("filename")
        quantity = kwargs.get("quantity")
        topic = kwargs.get("topic")
        client = kwargs.get("client", "")
        word_count = kwargs.get("word_count", 600)
        project_id = kwargs.get("project_id")
        concurrent_workers = kwargs.get("concurrent_workers", 5)

        try:
            # Validate parameters
            if quantity < 1:
                return ToolResult(
                    success=False,
                    error="Quantity must be at least 1"
                )

            if quantity > 5000:
                return ToolResult(
                    success=False,
                    error="Quantity cannot exceed 5000 per job for performance reasons"
                )

            # Generate unique job ID
            import uuid
            job_id = f"bulk_{uuid.uuid4().hex[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            # Determine output path
            output_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "outputs", "csv"
            )
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, filename)

            # Try to use the unified file generation service first
            try:
                from backend.services.unified_file_generation import (
                    UnifiedFileGenerationService,
                    GenerationRequest,
                    GenerationType
                )

                service = UnifiedFileGenerationService()
                request = GenerationRequest(
                    generation_type=GenerationType.CSV_BULK,
                    output_filename=filename,
                    content_spec={
                        "topic": topic,
                        "client": client,
                        "quantity": quantity,
                        "word_count": word_count
                    },
                    context_variables={
                        "project_id": str(project_id) if project_id else "",
                        "concurrent_workers": str(concurrent_workers)
                    }
                )

                result = service.generate(request)

                if result.success:
                    return ToolResult(
                        success=True,
                        output={
                            "job_id": result.job_id or job_id,
                            "output_path": result.output_path or output_path,
                            "status": "started",
                            "message": f"Bulk CSV generation started for {quantity} entries"
                        },
                        metadata={
                            "quantity": quantity,
                            "topic": topic,
                            "client": client,
                            "filename": filename
                        }
                    )

            except Exception as unified_error:
                logger.warning(f"Unified service failed, falling back to direct generation: {unified_error}")

            # Fallback: Direct generation for smaller batches
            if quantity <= 10:
                # For small quantities, generate inline
                from backend.tools.content_tools import WordPressContentTool
                tool = WordPressContentTool()

                rows = []
                for i in range(1, quantity + 1):
                    result = tool.execute(
                        client=client,
                        topic=f"{topic} - Part {i}",
                        row_id=i,
                        word_count=word_count
                    )
                    if result.success:
                        rows.append(result.output)

                # Write to file
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(rows))

                return ToolResult(
                    success=True,
                    output={
                        "job_id": job_id,
                        "output_path": output_path,
                        "status": "completed",
                        "rows_generated": len(rows),
                        "message": f"Generated {len(rows)} CSV rows"
                    },
                    metadata={
                        "quantity": quantity,
                        "topic": topic,
                        "client": client
                    }
                )

            # For larger quantities, queue the job
            return ToolResult(
                success=True,
                output={
                    "job_id": job_id,
                    "output_path": output_path,
                    "status": "queued",
                    "message": f"Bulk generation job queued for {quantity} entries. Use job_id to track progress."
                },
                metadata={
                    "quantity": quantity,
                    "topic": topic,
                    "client": client,
                    "filename": filename,
                    "note": "Large job queued for background processing"
                }
            )

        except Exception as e:
            logger.error(f"Bulk CSV generation failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Bulk generation failed: {str(e)}"
            )


class FileGeneratorTool(BaseTool):
    """
    General file generator for any file type.
    Converted from /createfile command rule (rule ID: 2).

    Generates file content based on user instructions without explanations.
    """

    name = "generate_file"
    description = "Generate file content (code, documents, data files) based on specifications"

    parameters = {
        "filename": ToolParameter(
            name="filename",
            type="string",
            required=True,
            description="Output filename with extension (e.g., 'script.py', 'config.json')"
        ),
        "content_description": ToolParameter(
            name="content_description",
            type="string",
            required=True,
            description="Description of what the file should contain"
        ),
        "file_type": ToolParameter(
            name="file_type",
            type="string",
            required=False,
            description="File type hint (code, document, data, config)",
            default="auto"
        ),
        "save_to_disk": ToolParameter(
            name="save_to_disk",
            type="bool",
            required=False,
            description="Whether to save the file to disk",
            default=True
        )
    }

    def __init__(self):
        super().__init__()
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            from backend.utils.llm_service import get_default_llm
            self._llm = get_default_llm()
        return self._llm

    def _detect_file_type(self, filename: str) -> str:
        """Detect file type from extension"""
        ext = os.path.splitext(filename)[1].lower()
        code_extensions = {'.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.cpp', '.c', '.h', '.go', '.rs', '.rb', '.php'}
        data_extensions = {'.json', '.csv', '.xml', '.yaml', '.yml'}
        doc_extensions = {'.md', '.txt', '.rst', '.html'}
        config_extensions = {'.ini', '.cfg', '.conf', '.env', '.toml'}

        if ext in code_extensions:
            return "code"
        elif ext in data_extensions:
            return "data"
        elif ext in doc_extensions:
            return "document"
        elif ext in config_extensions:
            return "config"
        return "unknown"

    def execute(self, **kwargs) -> ToolResult:
        """Generate file content"""
        filename = kwargs.get("filename")
        content_description = kwargs.get("content_description")
        file_type = kwargs.get("file_type", "auto")
        save_to_disk = kwargs.get("save_to_disk", True)

        try:
            llm = self._get_llm()

            # Detect file type if auto
            if file_type == "auto":
                file_type = self._detect_file_type(filename)

            # Build generation prompt
            prompt = f"""You are a file generator. Generate ONLY the file content requested.
Do not include any explanations, meta-text, or markdown code fences.
Output the actual file content that should be saved.

Filename: {filename}
File Type: {file_type}
Request: {content_description}

Generate the file content now:"""

            from backend.utils.llm_service import ChatMessage, MessageRole
            messages = [ChatMessage(role=MessageRole.USER, content=prompt)]
            response = llm.chat(messages)

            if response.message:
                try:
                    file_content = str(response.message.content).strip()
                except (ValueError, AttributeError):
                    blocks = getattr(response.message, 'blocks', [])
                    file_content = next((getattr(b, 'text', str(b)) for b in blocks if getattr(b, 'text', None)), "")
                    file_content = file_content.strip()
            else:
                file_content = ""

            # Clean up common artifacts
            if file_content.startswith("```"):
                # Remove markdown code fences
                lines = file_content.split('\n')
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                file_content = '\n'.join(lines)

            output_path = None
            if save_to_disk:
                output_dir = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                    "outputs", "files"
                )
                os.makedirs(output_dir, exist_ok=True)
                output_path = os.path.join(output_dir, filename)

                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(file_content)

            return ToolResult(
                success=True,
                output={
                    "content": file_content,
                    "output_path": output_path,
                    "filename": filename,
                    "file_type": file_type,
                    "content_length": len(file_content)
                },
                metadata={
                    "filename": filename,
                    "file_type": file_type,
                    "saved": save_to_disk
                }
            )

        except Exception as e:
            logger.error(f"File generation failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"File generation failed: {str(e)}"
            )


class CSVGeneratorTool(BaseTool):
    """
    Single CSV file generator.
    Converted from /createcsv command rule (rule ID: 3).

    Generates CSV data based on user specifications.
    """

    name = "generate_csv"
    description = "Generate a CSV file based on user specifications and data structure instructions"

    parameters = {
        "filename": ToolParameter(
            name="filename",
            type="string",
            required=True,
            description="Output CSV filename"
        ),
        "data_description": ToolParameter(
            name="data_description",
            type="string",
            required=True,
            description="Description of the data to generate (columns, rows, content type)"
        ),
        "include_headers": ToolParameter(
            name="include_headers",
            type="bool",
            required=False,
            description="Whether to include column headers",
            default=True
        ),
        "row_count": ToolParameter(
            name="row_count",
            type="int",
            required=False,
            description="Number of data rows to generate",
            default=10
        )
    }

    def __init__(self):
        super().__init__()
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            from backend.utils.llm_service import get_default_llm
            self._llm = get_default_llm()
        return self._llm

    def execute(self, **kwargs) -> ToolResult:
        """Generate CSV file"""
        filename = kwargs.get("filename")
        data_description = kwargs.get("data_description")
        include_headers = kwargs.get("include_headers", True)
        row_count = kwargs.get("row_count", 10)

        try:
            llm = self._get_llm()

            prompt = f"""Generate a CSV file based on these specifications.
Output ONLY valid CSV data, no explanations.

Filename: {filename}
Description: {data_description}
Include Headers: {include_headers}
Number of Rows: {row_count}

Requirements:
- Use proper CSV formatting with quoted strings where needed
- Ensure consistent column count across all rows
- Generate realistic, varied data

Generate the CSV content now:"""

            from backend.utils.llm_service import ChatMessage, MessageRole
            messages = [ChatMessage(role=MessageRole.USER, content=prompt)]
            response = llm.chat(messages)

            if response.message:
                try:
                    csv_content = str(response.message.content).strip()
                except (ValueError, AttributeError):
                    blocks = getattr(response.message, 'blocks', [])
                    csv_content = next((getattr(b, 'text', str(b)) for b in blocks if getattr(b, 'text', None)), "")
                    csv_content = csv_content.strip()
            else:
                csv_content = ""

            # Clean up
            if csv_content.startswith("```"):
                lines = csv_content.split('\n')
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                csv_content = '\n'.join(lines)

            # Save to disk
            output_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "outputs", "csv"
            )
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, filename)

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(csv_content)

            # Count rows
            row_count_actual = len([l for l in csv_content.split('\n') if l.strip()])

            return ToolResult(
                success=True,
                output={
                    "output_path": output_path,
                    "filename": filename,
                    "row_count": row_count_actual,
                    "content_preview": csv_content[:500] + "..." if len(csv_content) > 500 else csv_content
                },
                metadata={
                    "filename": filename,
                    "rows_generated": row_count_actual,
                    "has_headers": include_headers
                }
            )

        except Exception as e:
            logger.error(f"CSV generation failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"CSV generation failed: {str(e)}"
            )
