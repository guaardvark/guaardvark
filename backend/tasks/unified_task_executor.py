#!/usr/bin/env python3
"""
Unified Task Executor - Celery task that routes to appropriate handlers
Version 1.0: Routes task execution through Celery instead of daemon threads

This module provides:
- Celery task that loads tasks from database and executes them
- Progress tracking via unified_progress_system
- Automatic retry with exponential backoff
- Fallback to generic LLM execution if no handler registered
- Isolated database connections for worker processes
"""

import os
import logging
import datetime
import json
import time
import re
import csv
from celery import shared_task, current_task
from typing import Optional, Dict, Any
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.orm import sessionmaker

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Storage directory for file system paths (outputs, etc.)
STORAGE_DIR = os.environ.get(
    "GUAARDVARK_STORAGE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data"),
)


def _get_database_url():
    """Get DATABASE_URL from environment (set by start_postgres.sh in .env)."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    return "postgresql://guaardvark:guaardvark@localhost:5432/guaardvark"


_engine = None
_SessionFactory = None


def get_db_session():
    """Get a SQLAlchemy session for database operations without Flask."""
    global _engine, _SessionFactory
    if _engine is None:
        _engine = create_engine(_get_database_url(), pool_pre_ping=True)
        _SessionFactory = sessionmaker(bind=_engine)
    return _SessionFactory()


def get_task_by_id(task_id: int) -> Optional[Dict[str, Any]]:
    """Get task from database without Flask"""
    session = get_db_session()
    try:
        result = session.execute(
            sa_text("""
            SELECT id, name, description, status, type, priority, prompt_text,
                   model_name, output_filename, job_id, project_id, workflow_config,
                   client_name, target_website, competitor_url, retry_count,
                   created_at, updated_at, due_date
            FROM tasks
            WHERE id = :task_id
        """),
            {"task_id": task_id},
        )
        row = result.fetchone()
        if row:
            return {
                "id": row[0],
                "name": row[1],
                "description": row[2],
                "status": row[3],
                "type": row[4],
                "priority": row[5],
                "prompt_text": row[6],
                "model_name": row[7],
                "output_filename": row[8],
                "job_id": row[9],
                "project_id": row[10],
                "workflow_config": json.loads(row[11]) if row[11] else None,
                "client_name": row[12],
                "target_website": row[13],
                "competitor_url": row[14],
                "retry_count": row[15] or 0,
                "created_at": row[16],
                "updated_at": row[17],
                "due_date": row[18],
            }
        return None
    finally:
        session.close()


def update_task_status(
    task_id: int,
    status: str,
    error_message: str = None,
    job_id: str = None,
    progress: int = None,
    retry_count: int = None,
) -> bool:
    """Update task status in database without Flask"""
    session = get_db_session()
    try:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Build dynamic update query based on provided fields
        updates = ["status = :status", "updated_at = :updated_at"]
        params = {"status": status, "updated_at": now}

        if error_message is not None:
            updates.append("error_message = :error_message")
            params["error_message"] = error_message

        if job_id is not None:
            updates.append("job_id = :job_id")
            params["job_id"] = job_id

        if progress is not None:
            updates.append("progress = :progress")
            params["progress"] = progress

        if retry_count is not None:
            updates.append("retry_count = :retry_count")
            params["retry_count"] = retry_count

        params["task_id"] = task_id

        session.execute(
            sa_text(f"""
            UPDATE tasks
            SET {', '.join(updates)}
            WHERE id = :task_id
        """),
            params,
        )
        session.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to update task status: {e}")
        session.rollback()
        return False
    finally:
        session.close()


def update_task_result(task_id: int, result: str, output_filename: str = None) -> bool:
    """Update task result in database"""
    session = get_db_session()
    try:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        if output_filename:
            session.execute(
                sa_text("""
                UPDATE tasks
                SET result = :result, output_filename = :output_filename, updated_at = :updated_at
                WHERE id = :task_id
            """),
                {
                    "result": result,
                    "output_filename": output_filename,
                    "updated_at": now,
                    "task_id": task_id,
                },
            )
        else:
            session.execute(
                sa_text("""
                UPDATE tasks
                SET result = :result, updated_at = :updated_at
                WHERE id = :task_id
            """),
                {"result": result, "updated_at": now, "task_id": task_id},
            )
        session.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to update task result: {e}")
        session.rollback()
        return False
    finally:
        session.close()


def get_handler_queue(task_type: str) -> str:
    """Determine which Celery queue to use based on task type"""
    queue_mapping = {
        "file_generation": "generation",
        "csv_generation": "generation",
        "code_generation": "generation",
        "content_generation": "generation",
        "image_generation": "generation",
        "video_generation": "generation",
        "indexing": "indexing",
        "data_analysis": "default",
        "web_scraping": "default",
    }
    return queue_mapping.get(task_type, "default")


def execute_generic_llm_task(task: Dict[str, Any], progress_callback) -> str:
    """
    Fallback generic LLM execution for tasks without specific handlers.
    Uses the same logic as task_scheduler._execute_task but isolated from Flask.
    """
    logger.info(f"Executing generic LLM task {task['id']}: {task['name']}")

    try:
        # Get model name - use task model or fall back to default
        model_name = task.get("model_name")
        if not model_name:
            model_name = os.environ.get("DEFAULT_LLM_MODEL", "")
        if not model_name:
            # Query Ollama for available models
            try:
                import requests as _requests

                ollama_base_url = os.environ.get(
                    "OLLAMA_BASE_URL", "http://localhost:11434"
                )
                resp = _requests.get(f"{ollama_base_url}/api/tags", timeout=5)
                if resp.ok:
                    models = resp.json().get("models", [])
                    if models:
                        model_name = models[0]["name"]
                        logger.info(f"Using first available Ollama model: {model_name}")
            except Exception as e:
                logger.warning(f"Could not query Ollama for models: {e}")
        if not model_name:
            raise ValueError(
                "No LLM model available. Pull a model with 'ollama pull <model>' or set DEFAULT_LLM_MODEL env var."
            )

        prompt = task.get("prompt_text") or task.get("name") or ""
        if not prompt:
            return "Error: No prompt provided for task"

        progress_callback(25, f"Generating content for: {task['name']}")

        # Detect if this is a code generation task
        is_code_request = _detect_code_file_request(task)

        if is_code_request:
            logger.info(f"Task {task['id']} detected as code file generation")
            output = _generate_code_file(task, model_name, progress_callback)
        else:
            # Standard LLM generation
            output = _generate_llm_content(task, model_name, progress_callback)

        progress_callback(75, f"Saving output for: {task['name']}")

        # Write output to file if filename specified
        if task.get("output_filename") and output:
            _write_output_file(task["output_filename"], output)

        return output

    except Exception as e:
        logger.error(f"Generic LLM task failed for {task['id']}: {e}", exc_info=True)
        raise


def _detect_code_file_request(task: Dict[str, Any]) -> bool:
    """Detect if a task is requesting code file generation"""
    # Check output filename for code file extensions
    output_filename = task.get("output_filename", "")
    if output_filename:
        code_extensions = {
            ".py",
            ".jsx",
            ".js",
            ".ts",
            ".tsx",
            ".html",
            ".htm",
            ".css",
            ".php",
            ".java",
            ".c",
            ".cpp",
            ".h",
            ".hpp",
            ".go",
            ".rs",
            ".rb",
            ".sql",
        }
        data_extensions = {".json", ".xml", ".yml", ".yaml", ".csv", ".txt", ".md"}

        filename_lower = output_filename.lower()
        if any(filename_lower.endswith(ext) for ext in code_extensions):
            return True
        if any(filename_lower.endswith(ext) for ext in data_extensions):
            return False

    # Check task type
    if task.get("type") in ["code_generation", "file_generation"]:
        return True

    # Check task content for code-related keywords
    text_to_check = f"{task.get('name', '')} {task.get('description', '')} {task.get('prompt_text', '')}".lower()
    code_keywords = [
        "code",
        "script",
        "function",
        "class",
        "component",
        "jsx",
        "react",
        "javascript",
        "python",
        "html",
        "css",
        "refactor",
        "debug",
    ]

    return any(keyword in text_to_check for keyword in code_keywords)


def _generate_llm_content(
    task: Dict[str, Any], model_name: str, progress_callback
) -> str:
    """Generate content using LLM"""
    try:
        from llama_index.llms.ollama import Ollama

        ollama_base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        timeout = float(os.environ.get("LLM_REQUEST_TIMEOUT", "300"))

        llm = Ollama(
            model=model_name,
            base_url=ollama_base_url,
            request_timeout=min(timeout, 300.0),
        )

        prompt = task.get("prompt_text") or task.get("name")

        progress_callback(50, f"LLM generating content...")

        # Use basic completion
        response = llm.complete(prompt)
        output = str(response) if response else ""

        return output.strip() if output else "Error: Empty response from LLM"

    except ImportError as e:
        logger.error(f"Failed to import LlamaIndex: {e}")
        return f"Error: LlamaIndex not available - {str(e)}"
    except Exception as e:
        logger.error(f"LLM generation failed: {e}", exc_info=True)
        raise


def _generate_code_file(
    task: Dict[str, Any], model_name: str, progress_callback
) -> str:
    """Generate code file content using specialized prompting"""
    try:
        from llama_index.llms.ollama import Ollama
        from llama_index.core.llms import ChatMessage, MessageRole

        ollama_base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        timeout = float(os.environ.get("LLM_REQUEST_TIMEOUT", "600"))

        llm = Ollama(
            model=model_name,
            base_url=ollama_base_url,
            request_timeout=min(timeout, 600.0),
        )

        prompt = task.get("prompt_text") or task.get("name")
        output_filename = task.get("output_filename", "")

        # Build enhanced prompt based on file type
        enhanced_prompt = _build_code_generation_prompt(prompt, output_filename)

        progress_callback(50, f"Generating code file...")

        messages = [
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are an expert software developer. Generate complete, working code files exactly as requested. Do not include explanations or markdown formatting.",
            ),
            ChatMessage(role=MessageRole.USER, content=enhanced_prompt),
        ]

        response = llm.chat(messages)
        if response.message:
            try:
                output = response.message.content
            except (ValueError, AttributeError):
                blocks = getattr(response.message, "blocks", [])
                output = next(
                    (
                        getattr(b, "text", str(b))
                        for b in blocks
                        if getattr(b, "text", None)
                    ),
                    "",
                )
        else:
            output = ""

        return output.strip() if output else "Error: Empty code generated"

    except Exception as e:
        logger.error(f"Code generation failed: {e}", exc_info=True)
        raise


def _build_code_generation_prompt(prompt: str, output_filename: str) -> str:
    """Build enhanced prompt for code generation based on file type"""
    filename_lower = output_filename.lower()

    if ".jsx" in filename_lower or ".tsx" in filename_lower:
        return f"""You are an expert React developer. Generate JSX component.

CRITICAL REQUIREMENTS:
1. Output ONLY the complete, working JSX component file
2. Include proper React imports
3. Use functional components with proper JSX syntax
4. Include proper export statement
5. Do NOT include explanations or markdown formatting
6. Do NOT use code blocks (```)
7. Output the ENTIRE file, not just changes

User request: {prompt}

Output filename: {output_filename}

Generate the complete JSX component file now:"""

    elif ".py" in filename_lower:
        return f"""You are an expert Python developer. Generate Python code.

CRITICAL REQUIREMENTS:
1. Output ONLY the complete, working Python file
2. Include proper imports
3. Add docstrings for modules, classes, and functions
4. Follow PEP 8 style guidelines
5. Do NOT include explanations or markdown formatting
6. Do NOT use code blocks (```)
7. Output the ENTIRE file, not just changes

User request: {prompt}

Output filename: {output_filename}

Generate the complete Python file now:"""

    else:
        return f"""You are an expert software developer. Generate code.

CRITICAL REQUIREMENTS:
1. Output ONLY the complete, working code file
2. Do NOT include explanations or markdown formatting
3. Do NOT use code blocks (```)
4. Output the ENTIRE file, not just changes
5. Ensure the code is syntactically correct and complete

User request: {prompt}

Output filename: {output_filename}

Generate the complete code file now:"""


def _write_output_file(filename: str, content: str) -> bool:
    """Write task output to file"""
    try:
        output_dir = os.environ.get("GUAARDVARK_OUTPUT_DIR")
        if not output_dir:
            output_dir = os.path.join(STORAGE_DIR, "outputs")

        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, filename)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"Task output written to: {output_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to write task output: {e}")
        return False


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=1800,
    time_limit=2400,
)
def execute_unified_task(self, task_id: int):
    """
    Unified Celery task that:
    1. Loads task from database
    2. Gets handler from registry (or uses fallback)
    3. Executes with progress tracking
    4. Updates task status on completion/failure
    5. Handles retries via Celery's built-in mechanism

    Args:
        task_id: The database ID of the task to execute

    Returns:
        dict with task result and status
    """
    celery_task_id = self.request.id
    logger.info(
        f"Starting unified task execution for task_id={task_id}, celery_task_id={celery_task_id}"
    )

    # Initialize progress tracking
    progress_system = None
    process_id = None

    try:
        from backend.utils.unified_progress_system import (
            get_unified_progress,
            ProcessType,
        )

        progress_system = get_unified_progress()

        # Use task_id as progress ID for consistent tracking
        process_id = f"task_{task_id}"

        # Create or get progress process
        try:
            existing = progress_system.get_process(process_id)
            if not existing:
                progress_system.create_process(
                    ProcessType.TASK_PROCESSING,
                    f"Executing task {task_id}",
                    {"task_id": task_id, "celery_task_id": celery_task_id},
                    process_id=process_id,
                )
        except Exception as e:
            logger.warning(f"Could not initialize progress tracking: {e}")

    except Exception as e:
        logger.warning(f"Progress system not available: {e}")

    def update_progress(progress: int, message: str):
        """Update progress in unified system"""
        if progress_system and process_id:
            try:
                progress_system.update_process(process_id, progress, message)
            except Exception as e:
                logger.warning(f"Failed to update progress: {e}")
        # Also update task progress in database
        update_task_status(task_id, "in-progress", progress=progress)

    try:
        # Load task from database
        update_progress(5, "Loading task from database...")
        task = get_task_by_id(task_id)

        if not task:
            error_msg = f"Task {task_id} not found in database"
            logger.error(error_msg)
            if progress_system and process_id:
                progress_system.error_process(process_id, error_msg)
            return {"error": error_msg, "task_id": task_id}

        logger.info(
            f"Loaded task: {task['name']} (type: {task['type']}, status: {task['status']})"
        )

        # Update task status to in-progress with celery job ID
        update_task_status(task_id, "in-progress", job_id=f"task_{task_id}")
        update_progress(10, f"Starting execution: {task['name']}")

        # Determine execution strategy based on task type
        task_type = task.get("type", "content_generation")

        # Try to find a specific handler for the task type
        output = None
        handler_used = None

        # Check for CSV generation handler
        if task_type in ["file_generation", "csv_generation"] and task.get(
            "workflow_config"
        ):
            try:
                output = _execute_csv_generation(task, update_progress)
                handler_used = "csv_generation"
            except Exception as e:
                logger.warning(f"CSV handler failed, falling back to generic: {e}")

        # Fall back to generic LLM execution
        if output is None:
            update_progress(20, "Using generic LLM execution...")
            output = execute_generic_llm_task(task, update_progress)
            handler_used = "generic_llm"

        # Validate output
        if not output or (isinstance(output, str) and output.startswith("Error:")):
            error_msg = output if output else "No output generated"
            logger.error(f"Task {task_id} produced error output: {error_msg}")

            # BUG FIX #4: Use Celery's built-in retry count instead of manual tracking
            # The previous code set status to 'pending' then raised self.retry() which
            # would be caught by the outer except block, setting status to 'failed'.
            # Now we use Celery's retry mechanism properly.
            if self.request.retries < self.max_retries:
                # Update task with retry info but keep status as 'queued' for retry
                update_task_status(
                    task_id,
                    "queued",
                    retry_count=self.request.retries + 1,
                    error_message=f"Attempt {self.request.retries + 1} failed: {error_msg}",
                )
                logger.info(
                    f"Scheduling Celery retry {self.request.retries + 1}/{self.max_retries} for task {task_id}"
                )
                # Raise Retry exception - this will NOT be caught by the outer except
                # because Celery handles it specially
                raise self.retry(
                    countdown=60 * (self.request.retries + 1), exc=Exception(error_msg)
                )
            else:
                update_task_status(task_id, "failed", error_message=error_msg)
                if progress_system and process_id:
                    progress_system.error_process(process_id, error_msg)
                return {"error": error_msg, "task_id": task_id}

        # Success - update task with result
        update_progress(90, "Finalizing task...")
        update_task_result(
            task_id, str(output)[:10000], task.get("output_filename")
        )  # Truncate large outputs
        update_task_status(task_id, "completed", progress=100)

        # Complete progress tracking
        update_progress(100, f"Completed: {task['name']}")
        if progress_system and process_id:
            progress_system.complete_process(
                process_id, f"Task completed: {task['name']}"
            )

        logger.info(
            f"Task {task_id} completed successfully using handler: {handler_used}"
        )

        return {
            "task_id": task_id,
            "status": "completed",
            "handler": handler_used,
            "output_length": len(str(output)) if output else 0,
        }

    except Exception as e:
        error_msg = f"Task execution failed: {str(e)}"
        logger.error(f"Task {task_id} failed: {e}", exc_info=True)

        # Check if we should retry BEFORE marking as failed
        if self.request.retries < self.max_retries:
            update_task_status(
                task_id,
                "queued",
                retry_count=self.request.retries + 1,
                error_message=f"Attempt {self.request.retries + 1} failed: {error_msg}",
            )
            if progress_system and process_id:
                try:
                    progress_system.error_process(process_id, error_msg)
                except Exception:
                    pass
            logger.info(
                f"Scheduling Celery retry {self.request.retries + 1}/{self.max_retries} for task {task_id}"
            )
            raise self.retry(exc=e, countdown=60 * (self.request.retries + 1))

        # Final failure — no more retries
        update_task_status(task_id, "failed", error_message=error_msg)

        # Update progress with error
        if progress_system and process_id:
            try:
                progress_system.error_process(process_id, error_msg)
            except Exception:
                pass

        return {"error": error_msg, "task_id": task_id}


def _execute_csv_generation(task: Dict[str, Any], progress_callback) -> Optional[str]:
    """Execute CSV generation task using proven CSV generation"""
    workflow_config = task.get("workflow_config")
    if not workflow_config:
        return None

    topics = workflow_config.get("topics", [])
    if not topics:
        return None

    logger.info(f"Executing CSV generation with {len(topics)} topics")

    try:
        # BUG FIX #1: Import the underlying function directly instead of calling Celery task
        # Calling .apply().get() from within a Celery task can cause deadlocks
        # when all workers are busy. Instead, call the function's implementation directly.
        from backend.utils.llm_service import get_default_llm, ChatMessage, MessageRole

        output_filename = task.get("output_filename", f"generated_{task['id']}.csv")
        client_name = workflow_config.get("client_name", "Client")
        project_name = workflow_config.get("project_name", "Project")
        website = workflow_config.get("website", "")
        target_website = workflow_config.get("target_website", website)
        model_name = task.get("model_name")

        # Get LLM
        if model_name:
            from llama_index.llms.ollama import Ollama

            ollama_base_url = os.environ.get(
                "OLLAMA_BASE_URL", "http://localhost:11434"
            )
            timeout = float(os.environ.get("LLM_REQUEST_TIMEOUT", "180"))
            llm = Ollama(
                model=model_name,
                base_url=ollama_base_url,
                request_timeout=min(timeout, 180.0),
            )
        else:
            llm = get_default_llm()

        progress_callback(30, f"Generating {len(topics)} topics...")

        # Generate content for each topic
        generated_rows = []
        for idx, topic in enumerate(topics):
            progress_pct = 30 + int((idx / len(topics)) * 50)
            progress_callback(
                progress_pct, f"Processing topic {idx+1}/{len(topics)}: {topic[:30]}..."
            )

            try:
                prompt = f"Write 400-500 words of professional content about {topic} for {client_name}. Use HTML tags: h1, h2, h3, p, strong, em, ul, li."
                messages = [
                    ChatMessage(
                        role=MessageRole.SYSTEM,
                        content="You are a content writer. Write detailed HTML content. Respond with only the HTML content.",
                    ),
                    ChatMessage(role=MessageRole.USER, content=prompt),
                ]
                response = llm.chat(messages)
                if response and response.message:
                    try:
                        content = response.message.content.strip()
                    except (ValueError, AttributeError):
                        blocks = getattr(response.message, "blocks", [])
                        content = next(
                            (
                                getattr(b, "text", str(b))
                                for b in blocks
                                if getattr(b, "text", None)
                            ),
                            "",
                        )
                        content = content.strip()
                else:
                    content = f"<h1>{topic}</h1><p>Content for {topic}.</p>"
                content = re.sub(r"^.*?<h1>", "<h1>", content, flags=re.DOTALL)
                content = re.sub(r"```.*", "", content, flags=re.DOTALL)
            except Exception as e:
                logger.warning(f"Failed to generate content for {topic}: {e}")
                content = f"<h1>{topic}</h1><p>Content for {topic}.</p>"

            title = (
                f"{topic} - {client_name}" if len(topic) < 50 else f"{topic[:47]}..."
            )
            slug = re.sub(r"[^a-zA-Z0-9\s-]", "", topic.lower())
            slug = re.sub(r"\s+", "-", slug.strip()).strip("-")[:50] or f"page-{idx+1}"

            generated_rows.append(
                {
                    "ID": f"{10000 + idx + 1:05d}",
                    "Title": title,
                    "Content": content,
                    "Excerpt": f"Learn about {topic.lower()}. {client_name} provides expert services.",
                    "Category": "General",
                    "Tags": f"{topic.lower()}, {client_name.lower()}",
                    "slug": slug,
                    "Image": "General",
                }
            )

        progress_callback(85, "Writing CSV file...")

        # Write CSV
        output_dir = os.environ.get(
            "GUAARDVARK_OUTPUT_DIR", os.path.join(STORAGE_DIR, "outputs")
        )
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, output_filename)

        with open(output_path, "w", newline="", encoding="utf-8") as csvfile:
            fieldnames = [
                "ID",
                "Title",
                "Content",
                "Excerpt",
                "Category",
                "Tags",
                "slug",
                "Image",
            ]
            writer = csv.DictWriter(
                csvfile, fieldnames=fieldnames, quoting=csv.QUOTE_ALL
            )
            writer.writeheader()
            for row_data in generated_rows:
                writer.writerow(row_data)

        total_words = sum(len(row["Content"].split()) for row in generated_rows)
        return f"CSV generation completed: {len(generated_rows)} pages, {total_words} words, saved to {output_filename}"

    except Exception as e:
        logger.error(f"CSV generation failed: {e}")
        raise


# Export for Celery discovery
__all__ = ["execute_unified_task"]
