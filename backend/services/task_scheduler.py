# backend/services/task_scheduler.py
# Version 1.1: Enhanced task scheduler with progress tracking and unified progress integration

import datetime
import logging
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
        task = db.session.get(Task, task_id)
        if not task:
            logger.warning("Task %s not found", task_id)
            return

        # Update task status to in-progress
        task.status = "in-progress"
        task.updated_at = datetime.datetime.now(datetime.timezone.utc)
        db.session.commit()

        # Create progress tracking job
        job_id = f"task_{task_id}"
        
        # Assign job_id to task
        task.job_id = job_id
        db.session.commit()
        
        process_id = None
        try:
            unified_progress = get_unified_progress()
            process_id = unified_progress.create_process(
                ProcessType.TASK_PROCESSING,
                f"Processing task: {task.name}"
            )
        except Exception as e:
            logger.warning(f"Failed to create progress tracking for task {task_id}: {e}")
            # Continue without progress tracking if it fails

        try:
            # Get the model to use (task-specific or default)
            model_name = task.model_name
            if not model_name:
                # Try to get default task model
                try:
                    from backend.api.tasks_api import get_default_task_model
                    default_model = get_default_task_model()
                    if default_model:
                        model_name = default_model
                    else:
                        # Fallback to active model
                        try:
                            from backend.models import get_active_model_name
                            model_name = get_active_model_name()
                        except (ImportError, AttributeError):
                            # Get available models from database instead of hardcoding
                            try:
                                from backend.models import Model
                                available_models = db.session.query(Model).limit(1).first()
                                if available_models:
                                    model_name = available_models.name
                                else:
                                    model_name = "default"  # Let the LLM service handle this
                                    logger.warning("No models found in database, using 'default'")
                            except Exception:
                                model_name = "default"  # Let the LLM service handle this
                except (ImportError, AttributeError) as e:
                    logger.warning(f"Could not get default task model (import error): {e}")
                    # Get available models from database instead of hardcoding
                    try:
                        from backend.models import Model
                        available_models = db.session.query(Model).limit(1).first()
                        if available_models:
                            model_name = available_models.name
                        else:
                            model_name = "default"  # Let the LLM service handle this
                            logger.warning("No models found in database, using 'default'")
                    except Exception:
                        model_name = "default"  # Let the LLM service handle this
                except Exception as e:
                    logger.warning(f"Could not get default task model: {e}")
                    # Get available models from database instead of hardcoding
                    try:
                        from backend.models import Model
                        available_models = db.session.query(Model).limit(1).first()
                        if available_models:
                            model_name = available_models.name
                        else:
                            model_name = "default"  # Let the LLM service handle this
                            logger.warning("No models found in database, using 'default'")
                    except Exception:
                        model_name = "default"  # Let the LLM service handle this

            # Validate model name with single database query
            if model_name and model_name != "default":
                try:
                    from backend.models import Model
                    # Single query to check if model exists and get fallback
                    models = db.session.query(Model).all()
                    model_names = [m.name for m in models]

                    if model_name not in model_names:
                        if model_names:
                            fallback_model = model_names[0]
                            logger.warning(f"Model '{model_name}' not found, using '{fallback_model}'")
                            model_name = fallback_model
                        else:
                            logger.warning("No models found in database, using 'default'")
                            model_name = "default"
                    else:
                        logger.info(f"Using validated model: {model_name}")

                except Exception as e:
                    logger.warning(f"Could not validate model '{model_name}': {e}, using 'default'")
                    model_name = "default"

            prompt = task.prompt_text or task.name

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
                    from llama_index.llms.ollama import Ollama
                    from backend.config import OLLAMA_BASE_URL, LLM_REQUEST_TIMEOUT

                    timeout_value = min(LLM_REQUEST_TIMEOUT, 300.0)  # Cap at 5 minutes for tasks
                    task_llm = Ollama(model=model_name, base_url=OLLAMA_BASE_URL, request_timeout=timeout_value)

                    output = llm_service.generate_text_basic(prompt=prompt, llm=task_llm)
                except Exception as llm_error:
                    logger.error(f"LLM generation failed for task {task_id}: {llm_error}")
                    output = f"Error generating content: {str(llm_error)}"
            
            # Validate output content
            if output is None or not str(output).strip():
                logger.warning("LLM produced no output for task %s", task_id)
                task.status = "failed"
                task.error_message = "LLM generated empty or invalid content"
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


def _write_output(app, filename: str, content: str) -> None:
    """Write task output to file."""
    try:
        import os
        from backend.config import OUTPUT_DIR
        
        output_path = os.path.join(OUTPUT_DIR, filename)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)
            
        logger.info("Task output written to: %s", output_path)
    except Exception as e:
        logger.error("Failed to write task output: %s", e)


def process_pending_tasks(app) -> None:
    """Process all tasks in the pending state that are due."""
    with app.app_context():
        now = datetime.datetime.now(datetime.timezone.utc)
        tasks = (
            db.session.query(Task)
            .filter(Task.status == "pending")
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
        prompt = task.prompt_text or task.name

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
        from llama_index.llms.ollama import Ollama
        from backend.config import OLLAMA_BASE_URL, LLM_REQUEST_TIMEOUT

        timeout_value = min(LLM_REQUEST_TIMEOUT, 600.0)  # Allow more time for code generation
        task_llm = Ollama(model=model_name, base_url=OLLAMA_BASE_URL, request_timeout=timeout_value)

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
