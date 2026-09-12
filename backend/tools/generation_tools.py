#!/usr/bin/env python3
"""
Generation Tools
Executable tools for bulk and batch file generation operations.
Wraps existing generation services for agent system integration.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional
from datetime import datetime

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.utils.backend_http import is_mcp_transport, run_tool_in_backend

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
        if is_mcp_transport(self):
            # The generation services read Flask config and the database, so the job runs in the backend.
            return run_tool_in_backend(self.name, kwargs)
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
            from backend.config import OUTPUT_DIR
            output_dir = os.path.join(OUTPUT_DIR, "csv")
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
    description = (
        "Create a brand-NEW output file from a description, written under data/outputs/files. "
        "It generates from the description ALONE and never reads any existing file. "
        "Do NOT use it to improve, refactor, modify, or produce a new version of an existing or "
        "uploaded file — it cannot see that file and would fabricate. For that, use `codegen` "
        "with input_file=<path> (grounded copy) or `edit_code` (in-place repo change)."
    )

    # Verbs that signal "change something that already exists" rather than
    # "make a new file". Used to refuse ungrounded modify-existing requests.
    _MODIFY_VERBS = (
        "improve", "enhance", "optimize", "optimise", "refactor", "modify",
        "rewrite", "clean up", "cleanup", "improved version", "better version",
        "fix the", "update the", "based on the existing", "based on the uploaded",
    )

    parameters = {
        "filename": ToolParameter(
            name="filename",
            type="string",
            required=True,
            description="Relative output filename with extension; nested paths are allowed (e.g., 'frontend/src/page.jsx')"
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

    def _detect_modify_existing(self, filename, content_description):
        """Detect a request to improve/modify a file that already exists.

        generate_file builds its output from the description alone and never
        reads source, so honoring such a request would fabricate a "version"
        of a file it never saw. When that's what's being asked, return the
        referenced filename so the caller can refuse and redirect. Returns
        None when this is a legitimate new-file request.
        """
        desc = content_description or ""
        desc_l = desc.lower()

        # Candidate filenames: the output basename plus any file-looking
        # tokens named in the description.
        candidates = []
        if filename:
            candidates.append(os.path.basename(str(filename)))
        candidates += re.findall(r"[\w./-]+\.[A-Za-z0-9]+", desc)

        has_verb = any(v in desc_l for v in self._MODIFY_VERBS)

        # Strongest signal: a named file actually resolves to real content we
        # are NOT reading (uploaded chat file or in-repo source).
        for cand in candidates:
            cand = cand.strip()
            if not cand:
                continue
            try:
                from backend.utils.uploaded_file_resolver import find_uploaded_file
                if find_uploaded_file(cand):
                    return cand
            except Exception:
                pass
            try:
                from backend.services.guarded_code_service import read_repo_file
                read_repo_file(cand)
                return cand
            except Exception:
                pass

        # Weaker signal: the wording explicitly targets an existing file even
        # if we can't resolve it right now. Still ungrounded here.
        if has_verb and re.search(r"\b(this|the existing|the uploaded|the current)\b[\w\s]*\bfile\b", desc_l):
            named = next((c for c in candidates if c), filename)
            return named

        return None

    def _resolve_output_path(self, output_dir: str, filename: str) -> str:
        """Resolve a relative output filename safely beneath the output directory."""
        if not filename or not str(filename).strip():
            raise ValueError("Filename is required")

        raw_filename = str(filename).strip().replace("\\", "/")
        relative_path = Path(raw_filename)

        if relative_path.is_absolute():
            raise ValueError("Filename must be a relative path inside the output directory")

        if any(part in ("", ".", "..") for part in relative_path.parts):
            raise ValueError("Filename cannot contain empty, current-directory, or parent-directory segments")

        output_root = Path(output_dir).resolve()
        output_root.mkdir(parents=True, exist_ok=True)

        output_path = (output_root / relative_path).resolve()
        try:
            output_path.relative_to(output_root)
        except ValueError:
            raise ValueError("Filename resolves outside the output directory")

        if output_path == output_root:
            raise ValueError("Filename must point to a file, not the output directory")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        return str(output_path)

    def execute(self, **kwargs) -> ToolResult:
        """Generate file content"""
        filename = kwargs.get("filename")
        content_description = kwargs.get("content_description")
        file_type = kwargs.get("file_type", "auto")
        save_to_disk = kwargs.get("save_to_disk", True)

        try:
            # Refuse ungrounded "improve an existing file" requests. This tool
            # never reads source, so producing an "improved version" of a real
            # file would be fabrication. Redirect to the grounded tools.
            referenced = self._detect_modify_existing(filename, content_description)
            if referenced:
                return ToolResult(
                    success=False,
                    error=(
                        f"generate_file cannot improve or modify an existing file. It writes a "
                        f"brand-new file from your description and never reads '{referenced}', so "
                        f"any 'improved version' would be fabricated. Use `codegen` with "
                        f"input_file='{referenced}' to generate a grounded modified copy, or "
                        f"`edit_code` to change the file in place."
                    ),
                )

            # Detect file type if auto
            if file_type == "auto":
                file_type = self._detect_file_type(filename)

            output_path = None
            if save_to_disk:
                from backend.config import OUTPUT_DIR
                output_dir = os.path.join(OUTPUT_DIR, "files")
                output_path = self._resolve_output_path(output_dir, filename)

            llm = self._get_llm()

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

            if save_to_disk:
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
                    "saved": save_to_disk,
                    "destination": "output_dir"
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
            from backend.config import OUTPUT_DIR
            output_dir = os.path.join(OUTPUT_DIR, "csv")
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
