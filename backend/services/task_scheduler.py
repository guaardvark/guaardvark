# backend/services/task_scheduler.py
# Version 1.1: Enhanced task scheduler with progress tracking and unified progress integration

import datetime
import logging
import os
import threading
import time
from typing import Optional

# Local imports
try:
    from backend.models import Task, db
    from backend.utils import llm_service
    from backend.utils.unified_progress_system import get_unified_progress, ProcessType
except ImportError as e:
    logging.getLogger(__name__).error(f"Failed to import dependencies: {e}")
    Task = db = llm_service = None

logger = logging.getLogger(__name__)


def _execute_task(app, task_id: int) -> None:
    """Execute a task by ID, updating its status and progress."""
    logger.info("Executing task %s", task_id)

    with app.app_context():
        try:
            job_id = f"task_{task_id}"
            # Atomically CLAIM the task before doing anything, mirroring the Celery
            # beat's guard (status='pending' AND job_id IS NULL). Postgres row-locks
            # the conditional UPDATE, so the thread scheduler and the beat are mutually
            # exclusive — exactly one wins. This replaces the old non-atomic two-commit
            # (status, then job_id) that let both schedulers run the same task.
            now = datetime.datetime.now(datetime.timezone.utc)
            claimed = (
                db.session.query(Task)
                .filter(Task.id == task_id, Task.status == "pending", Task.job_id.is_(None))
                .update(
                    {"status": "in-progress", "job_id": job_id, "updated_at": now},
                    synchronize_session=False,
                )
            )
            db.session.commit()
            if claimed != 1:
                logger.debug("Task %s not claimable (already claimed / not pending) — skipping", task_id)
                return

            task = db.session.get(Task, task_id)  # fresh row after the claim
            if not task:
                logger.warning("Task %s not found after claim", task_id)
                return

            process_id = None
            try:
                unified_progress = get_unified_progress()
                process_id = unified_progress.create_process(
                    ProcessType.TASK_PROCESSING,
                    f"Processing task: {task.name}",
                    process_id=job_id
                )
            except Exception as e:
                logger.warning(f"Failed to create progress tracking for task {task_id}: {e}")
                # Continue without progress tracking if it fails

            try:
                model_name, model_note = _resolve_task_model(task)
                if model_note:
                    logger.warning("Task %s: %s", task_id, model_note)
                else:
                    logger.info("Task %s uses model %s", task_id, model_name)

                prompt = task_prompt(task)

                # Update progress to 25%
                if process_id:
                    try:
                        unified_progress.update_process(
                            process_id,
                            25,
                            f"Generating content for: {task.name}"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to update progress for task {task_id}: {e}")

                # Check if this is a code file generation request
                is_code_request = _detect_code_file_request(task)

                if is_code_request:
                    logger.info(f"Task {task_id} detected as code file generation request, using specialized file generation API")
                    # Use the file generation API for code requests
                    try:
                        output = _generate_code_file(task, model_name)
                    except Exception as file_gen_error:
                        logger.error(f"File generation failed for task {task_id}: {file_gen_error}")
                        output = f"Error generating file: {str(file_gen_error)}"
                else:
                    # Generate content using basic LLM for non-code requests
                    try:
                        # Create LLM instance with the specified model
                        from backend.utils.ollama_resource_manager import build_ollama
                        from backend.config import OLLAMA_BASE_URL, LLM_REQUEST_TIMEOUT

                        timeout_value = min(LLM_REQUEST_TIMEOUT, 300.0)  # Cap at 5 minutes for tasks
                        task_llm = build_ollama(model_name, base_url=OLLAMA_BASE_URL, request_timeout=timeout_value)

                        output = llm_service.generate_text_basic(prompt=prompt, llm=task_llm)
                    except Exception as llm_error:
                        logger.error(f"LLM generation failed for task {task_id}: {llm_error}")
                        output = f"Error generating content: {str(llm_error)}"

                # Validate output content
                if output is None or not str(output).strip():
                    logger.warning("LLM produced no output for task %s (model %s)", task_id, model_name)
                    task.status = "failed"
                    task.error_message = " ".join(filter(None, (
                        f"LLM generated empty or invalid content (model {model_name}).", model_note)))
                    task.updated_at = datetime.datetime.now(datetime.timezone.utc)
                    db.session.commit()

                    if process_id:
                        try:
                            unified_progress.complete_process(
                                process_id,
                                message=f"Task {task_id}: Empty output generated"
                            )
                        except Exception as e:
                            logger.warning(f"Failed to update error progress: {e}")
                    return

                output = str(output).strip()

                # Update progress to 75%
                if process_id:
                    try:
                        unified_progress.update_process(
                            process_id,
                            75,
                            f"Saving output for: {task.name}"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to update progress for task {task_id}: {e}")

                # Save output if filename specified
                if task.output_filename:
                    _write_output(app, task.output_filename, output)

                # Mark task as completed
                task.status = "completed"
                task.updated_at = datetime.datetime.now(datetime.timezone.utc)
                db.session.commit()

                # Update progress to 100%
                if process_id:
                    try:
                        unified_progress.complete_process(
                            process_id,
                            message=f"Completed: {task.name}"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to complete progress for task {task_id}: {e}")

                logger.info("Task %s completed successfully", task_id)

            except Exception as e:
                logger.error("Task execution failed: %s", e, exc_info=True)
                task.status = "failed"
                task.error_message = str(e)[:1000]
                task.updated_at = datetime.datetime.now(datetime.timezone.utc)
                db.session.commit()

                # Update progress with error
                if process_id:
                    try:
                        unified_progress.complete_process(
                            process_id,
                            message=f"Failed: {task.name} - {str(e)}"
                        )
                    except Exception as progress_error:
                        logger.warning(f"Failed to update error progress for task {task_id}: {progress_error}")
        finally:
            db.session.remove()


def _installed_ollama_models() -> Optional[list]:
    """Model tags Ollama has installed, or None when Ollama cannot be asked."""
    import requests as _requests
    base_url = os.environ.get('OLLAMA_BASE_URL', 'http://127.0.0.1:11434')
    try:
        resp = _requests.get(f"{base_url}/api/tags", timeout=5)
        resp.raise_for_status()
        return [m['name'] for m in resp.json().get('models', []) if m.get('name')]
    except Exception as e:
        logger.warning(f"Could not query Ollama for models: {e}")
        return None


def task_prompt(task) -> str:
    """What a job asks the model: its prompt, else its description, else its name.

    The Jobs page saves its prompt box as the job's description, so a job
    without prompt_text still carries what the person typed there.
    """
    for key in ("prompt_text", "description", "name"):
        value = task.get(key) if isinstance(task, dict) else getattr(task, key, None)
        if value and str(value).strip():
            return str(value).strip()
    return ""


def _resolve_task_model(task) -> tuple:
    """(model, note) for a task: its own model, else the default task model,
    else the active model, checked against what Ollama has installed. Falls
    back only when none is chosen or the chosen one is not installed: to the
    default task or active model if installed, else the first installed model
    that is not an embedding model; the note then says which and why. When
    Ollama cannot be asked, a chosen model is kept as is."""
    default_model = active_model = None
    try:
        from backend.api.tasks_api import get_default_task_model
        default_model = get_default_task_model()
    except Exception as e:
        logger.warning(f"Could not get default task model: {e}")
    try:
        from backend.models import get_active_model_name
        active_model = get_active_model_name()
    except Exception:
        pass
    if active_model == "default_model_name":  # get_active_model_name's "nothing set"
        active_model = None
    chosen = task.model_name or default_model or active_model

    installed = _installed_ollama_models()
    if installed is None:
        if chosen:
            return chosen, None
        raise ValueError("No model chosen for the task and Ollama could not be asked which are installed.")

    def usable(name):
        return bool(name) and (name in installed or f"{name}:latest" in installed)

    if usable(chosen):
        return chosen, None
    candidates = [m for m in (default_model, active_model) if usable(m)]
    candidates += [m for m in installed if "embed" not in m.lower()]
    if not candidates:
        raise ValueError("No LLM model available. Pull a model with 'ollama pull <model>' or set DEFAULT_LLM_MODEL env var.")
    fallback = candidates[0]
    if chosen:
        return fallback, f"Model {chosen} is not installed in Ollama; used {fallback} instead."
    return fallback, f"No model was chosen for the task; used {fallback}."


def _write_output(app, filename: str, content: str) -> None:
    """Write task output to file."""
    try:
        import os
        from backend.config import OUTPUT_DIR
        
        # Confine to OUTPUT_DIR — filename derives from task.output_filename (user-set);
        # prevent arbitrary file write via path traversal.
        output_path = os.path.realpath(os.path.join(OUTPUT_DIR, filename))
        if not output_path.startswith(os.path.realpath(OUTPUT_DIR) + os.sep):
            logger.error("Refusing to write task output outside OUTPUT_DIR: %r", filename)
            return
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)
            
        logger.info("Task output written to: %s", output_path)
    except Exception as e:
        logger.error("Failed to write task output: %s", e)


def process_pending_tasks(app) -> None:
    """Process all tasks in the pending state that are due."""
    # Job order on: while a chat or voice turn runs, due rows wait for a later pass.
    from backend.services import job_arbiter
    if job_arbiter.should_yield("scheduled_task"):
        return
    with app.app_context():
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            tasks = (
                db.session.query(Task)
                .filter(Task.status == "pending", Task.job_id.is_(None))  # mirror the beat; skip claimed tasks
                .order_by(Task.priority, Task.due_date)
                .all()
            )

            if not tasks:
                return

            logger.info(f"Processing {len(tasks)} pending tasks")

            for i, task in enumerate(tasks):
                task_due = task.due_date
                if task_due and task_due.tzinfo is None:
                    task_due = task_due.replace(tzinfo=datetime.timezone.utc)
                if task_due and task_due > now:
                    continue

                logger.info(f"Processing task {task.id}: {task.name}")
                _execute_task(app, task.id)
        finally:
            db.session.remove()


class TaskScheduler:
    """Enhanced task scheduler with progress tracking."""

    def __init__(self, app):
        self.app = app
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        """Start the task scheduler."""
        self._thread.start()
        logger.info("Task scheduler started")

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the task scheduler."""
        self._stop.set()
        if wait:
            self._thread.join()
        logger.info("Task scheduler shutdown")

    def _run(self) -> None:
        """Main scheduler loop."""
        while not self._stop.is_set():
            try:
                process_pending_tasks(self.app)
            except Exception as exc:
                logger.error("Scheduler loop error: %s", exc, exc_info=True)
            time.sleep(1)  # Check every second


def init_task_scheduler(app) -> TaskScheduler:
    """Initialise and start the task scheduler."""
    scheduler = TaskScheduler(app)
    scheduler.start()
    return scheduler


def _detect_code_file_request(task) -> bool:
    """
    Detect if a task is requesting code file generation based on various indicators.
    """
    # Check output filename for code file extensions (exclude data/config files)
    if task.output_filename:
        code_extensions = {'.py', '.jsx', '.js', '.ts', '.tsx', '.html', '.htm', '.css', '.php', '.java', '.c', '.cpp', '.h', '.hpp', '.go', '.rs', '.rb', '.sql'}
        # Separate data/config files that should not trigger code generation
        data_extensions = {'.json', '.xml', '.yml', '.yaml', '.csv', '.txt', '.md'}
        filename_lower = task.output_filename.lower()

        if any(filename_lower.endswith(ext) for ext in code_extensions):
            logger.info(f"Code file detected by extension: {task.output_filename}")
            return True
        elif any(filename_lower.endswith(ext) for ext in data_extensions):
            logger.info(f"Data/config file detected, not using code generation: {task.output_filename}")
            return False

    # Check task name and description for code-related keywords
    text_to_check = f"{task.name or ''} {task.description or ''} {task.prompt_text or ''}".lower()

    code_keywords = [
        'code', 'script', 'function', 'class', 'component', 'jsx', 'react',
        'javascript', 'python', 'html', 'css', 'php', 'java', 'typescript',
        'refactor', 'debug', 'optimize', 'remove button', 'add button',
        'modify code', 'update file', 'fix code', 'correct code', 'full file'
    ]

    # File modification keywords
    modification_keywords = [
        'remove the button', 'add the button', 'delete button', 'update button',
        'modify', 'change', 'fix', 'correct', 'update', 'refactor',
        'output the corrected code', 'output corrected code', 'full file'
    ]

    file_keywords = ['.jsx', '.js', '.py', '.css', '.html', '.php', '.json']

    # Check for code keywords
    code_match = any(keyword in text_to_check for keyword in code_keywords)
    modification_match = any(keyword in text_to_check for keyword in modification_keywords)
    file_match = any(keyword in text_to_check for keyword in file_keywords)

    if code_match or modification_match or file_match:
        logger.info(f"Code file detected by keywords in task {task.id}: code={code_match}, mod={modification_match}, file={file_match}")
        return True

    # Check task type (be more specific about which content generation is code)
    if task.type in ['code_generation', 'file_generation']:
        logger.info(f"Code file detected by task type: {task.type}")
        return True

    # For content_generation, only detect as code if other indicators are present
    if task.type == 'content_generation' and (code_match or modification_match or file_match):
        logger.info(f"Code file detected by task type + content indicators: {task.type}")
        return True

    return False


def _generate_code_file(task, model_name) -> str:
    """
    Generate code file content using internal file generation logic to avoid circular imports.
    """
    logger.info(f"Generating code file for task {task.id} using model {model_name}")

    try:
        # Use the enhanced fallback approach directly to avoid circular imports
        # This provides the same quality as the API without dependency issues
        return _generate_code_file_fallback(task, model_name)

    except Exception as e:
        logger.error(f"File generation error for task {task.id}: {e}")
        return f"Error in file generation: {str(e)}"


def _generate_code_file_fallback(task, model_name) -> str:
    """
    Fallback code file generation using enhanced prompting with the basic LLM service.
    """
    logger.info(f"Using fallback code generation for task {task.id}")

    try:
        # Create enhanced prompt for code generation
        prompt = task_prompt(task)

        # Enhanced system prompt for code generation with JSX-specific handling
        if task.output_filename:
            filename_lower = task.output_filename.lower()
            if '.jsx' in filename_lower:
                enhanced_prompt = f"""You are an expert React developer specializing in JSX components. The user has requested JSX component generation or modification.

CRITICAL JSX REQUIREMENTS:
1. Output ONLY the complete, working JSX component file
2. Include proper React imports (import React from 'react'; or import {{ useState, useEffect }} from 'react';)
3. Use functional components with proper JSX syntax
4. Include proper export statement (export default ComponentName;)
5. Ensure proper component naming (PascalCase)
6. Use proper JSX attributes (className instead of class, etc.)
7. Do NOT include explanations, comments about what you changed, or markdown formatting
8. Do NOT use code blocks (```) or any markup
9. Output the ENTIRE file, not just the changes
10. Ensure the JSX is syntactically correct and follows React best practices

User request: {prompt}

Output filename: {task.output_filename}

Generate the complete JSX component file now:"""
            elif '.py' in filename_lower:
                enhanced_prompt = f"""You are an expert Python developer. The user has requested Python code generation or modification.

CRITICAL PYTHON REQUIREMENTS:
1. Output ONLY the complete, working Python file
2. Include proper imports at the top of the file
3. Add comprehensive docstrings for modules, classes, and functions (Google/NumPy style)
4. Follow PEP 8 style guidelines (proper indentation, naming conventions)
5. Include type hints where appropriate (from typing import List, Dict, Optional, etc.)
6. Use proper exception handling with specific exception types
7. Add if __name__ == "__main__": block if it's a script
8. Include proper class structure with __init__ methods
9. Do NOT include explanations, comments about what you changed, or markdown formatting
10. Do NOT use code blocks (```) or any markup
11. Output the ENTIRE file, not just the changes
12. Ensure the code is syntactically correct and follows Python best practices

User request: {prompt}

Output filename: {task.output_filename}

Generate the complete Python file now:"""
            elif any(ext in filename_lower for ext in ['.js', '.html', '.css', '.php', '.ts', '.tsx']):
                enhanced_prompt = f"""You are an expert software developer. The user has requested code generation or modification.

CRITICAL REQUIREMENTS:
1. Output ONLY the complete, working code file
2. Do NOT include explanations, comments about what you changed, or markdown formatting
3. Do NOT use code blocks (```) or any markup
4. Output the ENTIRE file, not just the changes
5. Ensure the code is syntactically correct and complete

User request: {prompt}

Output filename: {task.output_filename}

Generate the complete code file now:"""
            else:
                enhanced_prompt = f"""You are an expert content generator. Generate complete, high-quality content as requested.

REQUIREMENTS:
1. Generate complete content that fully addresses the request
2. Ensure the output is comprehensive and well-structured
3. Focus on delivering exactly what was requested

User request: {prompt}

Generate the content now:"""
        else:
            enhanced_prompt = f"""You are an expert content generator. Generate complete, high-quality content as requested.

REQUIREMENTS:
1. Generate complete content that fully addresses the request
2. Ensure the output is comprehensive and well-structured
3. Focus on delivering exactly what was requested

User request: {prompt}

Generate the content now:"""

        # Create LLM instance with the specified model
        from backend.utils.ollama_resource_manager import build_ollama
        from backend.config import OLLAMA_BASE_URL, LLM_REQUEST_TIMEOUT

        timeout_value = min(LLM_REQUEST_TIMEOUT, 600.0)  # Allow more time for code generation
        task_llm = build_ollama(model_name, base_url=OLLAMA_BASE_URL, request_timeout=timeout_value)

        # Use enhanced generation instead of basic
        from llama_index.core.llms import ChatMessage, MessageRole

        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content="You are an expert software developer. Generate complete, working code files exactly as requested. Do not include explanations or markdown formatting."),
            ChatMessage(role=MessageRole.USER, content=enhanced_prompt),
        ]

        response = task_llm.chat(messages)
        try:
            output = response.message.content if response.message else ""
        except (ValueError, AttributeError):
            blocks = getattr(response.message, 'blocks', []) if response.message else []
            output = next((getattr(b, 'text', str(b)) for b in blocks if getattr(b, 'text', None)), str(blocks[0]) if blocks else "")

        logger.info(f"Fallback code generation completed for task {task.id}, output length: {len(output) if output else 0}")
        return output or "Error: No output generated"

    except Exception as e:
        logger.error(f"Fallback code generation failed for task {task.id}: {e}")
        return f"Error in fallback generation: {str(e)}"


def get_active_model_name() -> str:
    """Get the currently active model name from the database."""
    try:
        from backend.models import get_active_model_name as db_get_active_model_name
        return db_get_active_model_name()
    except Exception as e:
        logger.warning(f"Could not get active model name: {e}")
        return "default_model_name"


__all__ = [
    "init_task_scheduler",
    "process_pending_tasks",
    "get_active_model_name",
    "_execute_task",
    "TaskScheduler",
]
