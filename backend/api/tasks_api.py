# backend/api/tasks_api.py
# Version 1.4: Added Celery-based task execution instead of daemon threads.
# - auto_start_job now submits tasks to Celery queue
# - start_task endpoint uses Celery for execution
# - Added "queued" status support

import datetime
import logging

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload  # Import joinedload if needed for relationships
from backend.utils.response_utils import success_response, error_response

# --- Relative Imports Added ---
try:
    # Assuming Task model might eventually link to Project, Client, Website
    from .model_api import get_available_ollama_models
    from ..models import Project, Task, Setting, Client, Website, db
except ImportError:
    try:
        from backend.api.model_api import get_available_ollama_models
        from backend.models import Project, Task, Setting, Client, Website, db
    except ImportError:
        db = Task = Project = Setting = Client = Website = None
        import logging  # Use standard logging if current_app logger fails

        logging.getLogger(__name__).critical(
            "Failed to import db/Task/Project/Client/Website models for tasks_api", exc_info=True
        )
        get_available_ollama_models = None

# --- End Relative Imports ---

tasks_bp = Blueprint("tasks", __name__, url_prefix="/api/tasks")

# Define allowed statuses for validation
ALLOWED_TASK_STATUSES = [
    "pending",
    "queued",
    "in-progress",
    "paused",
    "completed",
    "failed",
    "cancelled",
]

# Define allowed priorities for validation
ALLOWED_PRIORITIES = [1, 2, 3]  # High, Medium, Low

logger = logging.getLogger(__name__)


def get_default_task_model():
    """Get the default model for tasks from settings."""
    if not db or not Setting:
        return None
    try:
        setting = db.session.get(Setting, "default_task_model")
        return setting.value if setting else None
    except Exception as e:
        logger.error(f"Error getting default task model: {e}")
        return None


def set_default_task_model(model_name):
    """Set the default model for tasks in settings."""
    if not db or not Setting:
        return False
    try:
        setting = db.session.get(Setting, "default_task_model")
        if setting:
            setting.value = model_name
        else:
            setting = Setting(key="default_task_model", value=model_name)
            db.session.add(setting)
        db.session.commit()
        return True
    except Exception as e:
        logger.error(f"Error setting default task model: {e}")
        db.session.rollback()
        return False


@tasks_bp.route("/default-model", methods=["GET"])
def get_default_task_model_endpoint():
    """Get the default model for tasks."""
    try:
        default_model = get_default_task_model()
        return jsonify({"default_model": default_model}), 200
    except Exception as e:
        logger.error(f"Error getting default task model: {e}")
        return jsonify({"error": "Failed to get default task model"}), 500


@tasks_bp.route("/default-model", methods=["POST"])
def set_default_task_model_endpoint():
    """Set the default model for tasks."""
    try:
        data = request.get_json()
        if not data or "model" not in data:
            return jsonify({"error": "Missing 'model' in request body"}), 400

        model_name = data["model"]
        if set_default_task_model(model_name):
            return jsonify({"message": f"Default task model set to {model_name}"}), 200
        else:
            return jsonify({"error": "Failed to set default task model"}), 500
    except Exception as e:
        logger.error(f"Error setting default task model: {e}")
        return jsonify({"error": "Failed to set default task model"}), 500


# Import centralized serialization utilities
try:
    from ..utils.serialization_utils import serialize_task as utils_serialize_task
except ImportError:
    from backend.utils.serialization_utils import serialize_task as utils_serialize_task


# Helper to serialize task using centralized utility
def serialize_task(task):
    """Serialize task using centralized utility"""
    if not task:
        return None

    # Use centralized serialization - it handles project_info automatically
    return utils_serialize_task(task)


@tasks_bp.route("", methods=["GET"])
def get_tasks():
    """
    API endpoint to get all tasks, optionally filtered by status, type, or project_id.
    """
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info(f"API: Received GET /api/tasks request. Args: {request.args}")

    if not db or not Task:
        logger.error("Tasks API: DB or Task model not available.")
        return jsonify({"error": "Database connection or Task model unavailable."}), 500

    status_filter = request.args.get("status")
    task_type_filter = request.args.get("type")
    # --- ADDED: Project ID Filter ---
    project_id_filter = request.args.get("project_id", type=int)
    # --- END ADDED ---

    try:
        query = db.session.query(Task)

        # Apply filters
        if status_filter:
            if status_filter in ALLOWED_TASK_STATUSES:
                query = query.filter(Task.status == status_filter)
            else:
                logger.warning(f"Invalid status filter received: {status_filter}")
        if task_type_filter:
            query = query.filter(Task.type == task_type_filter)
        # --- Apply Project Filter ---
        if project_id_filter is not None:
            logger.info(f"Filtering tasks by project_id: {project_id_filter}")
            # Ensure your Task model has a 'project_id' column/relationship
            if hasattr(Task, "project_id"):
                query = query.filter(Task.project_id == project_id_filter)
            else:
                logger.warning(
                    "Task model does not have 'project_id' attribute for filtering."
                )
        # --- End Apply Filter ---

        # Eager load relationships for serialization
        if Project and hasattr(Task, "project"):
            query = query.options(joinedload(Task.project))
        if Client and hasattr(Task, "client_ref"):
            query = query.options(joinedload(Task.client_ref))
        if Website and hasattr(Task, "website_ref"):
            query = query.options(joinedload(Task.website_ref))

        tasks = query.order_by(Task.created_at.desc()).all()
        tasks_list = [serialize_task(task) for task in tasks]

        logger.info(
            f"Retrieved {len(tasks_list)} tasks (status: {status_filter}, type: {task_type_filter}, project: {project_id_filter})."
        )
        return jsonify(tasks_list), 200
    except AttributeError as ae:
        logger.error(
            f"Attribute error in get_tasks (likely model mismatch): {ae}", exc_info=True
        )
        return (
            jsonify(
                {
                    "error": "Internal server error processing task data.",
                    "details": str(ae),
                }
            ),
            500,
        )
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_tasks: {e}", exc_info=True)
        return (
            jsonify({"error": "Database error occurred while retrieving tasks."}),
            500,
        )
    except Exception as e:
        logger.error(f"Unexpected error in get_tasks: {e}", exc_info=True)
        return jsonify({"error": "An unexpected error occurred."}), 500


@tasks_bp.route("", methods=["POST"])
def create_task():
    """API endpoint to create a new task."""
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received POST /api/tasks request")
    
    try:
        if not db or not Task:
            return jsonify({"error": "DB/Model unavailable."}), 500

        data = request.get_json()
        if not data or "name" not in data:
            return jsonify({"error": "Missing 'name' key"}), 400

        name = data["name"]
        status = data.get("status", "pending")
        if status not in ALLOWED_TASK_STATUSES:
            return (
                jsonify(
                    {
                        "error": f"Invalid status. Must be one of: {', '.join(ALLOWED_TASK_STATUSES)}"
                    }
                ),
                400,
            )

        description = data.get("description", "")
        task_type = data.get("type")
        prompt_text = data.get("prompt_text")
        model_name = data.get("model_name")
        output_filename = data.get("output_filename")
        priority = data.get("priority", 2)  # Default to Medium priority
        logger.info(f"DEBUG: output_filename from request: {output_filename}")
        # New: optional due_date for scheduling
        due_date = None
        if data.get("due_date"):
            try:
                due_date = datetime.datetime.fromisoformat(data["due_date"])
            except (ValueError, TypeError):
                logger.warning(f"Invalid due_date format received: {data['due_date']}")
                return jsonify({"error": "Invalid due_date format. Use ISO string."}), 400
        # --- ADDED: Handle project_id ---
        project_id = data.get("project_id")
        if project_id is not None:
            try:
                project_id = int(project_id)
                # Optional: Check if project exists
                if (
                    Project
                    and not db.session.query(Project.id).filter_by(id=project_id).scalar()
                ):
                    logger.warning(
                        f"Attempted to link task to non-existent project ID: {project_id}"
                    )
                    return (
                        jsonify({"error": f"Project with ID {project_id} not found."}),
                        404,
                    )
            except (ValueError, TypeError):
                logger.warning(f"Invalid project_id format received: {project_id}")
                return (
                    jsonify({"error": "Invalid project_id format. Must be an integer."}),
                    400,
                )
        # --- END ADDED ---

        if model_name:
            if not get_available_ollama_models:
                logger.error(
                    "Model validation unavailable: get_available_ollama_models not imported"
                )
                return jsonify({"error": "Model validation service unavailable"}), 500
            models_data = get_available_ollama_models()
            if isinstance(models_data, dict) and models_data.get("error"):
                return (
                    jsonify(
                        {"error": f"Failed to validate model name: {models_data['error']}"}
                    ),
                    502,
                )
            available_names = [m.get("name") for m in models_data if isinstance(m, dict)]
            if model_name not in available_names:
                return (
                    jsonify(
                        {
                            "error": f"Model '{model_name}' not found or available via Ollama API."
                        }
                    ),
                    404,
                )

        try:
            # Extract additional fields for enhanced task tracking
            client_id = data.get("client_id")
            website_id = data.get("website_id")
            client_name = data.get("client_name", "")
            target_website = data.get("target_website", "")
            competitor_url = data.get("competitor_url", "")
            auto_start_job = data.get("auto_start_job", False)
            workflow_config = data.get("workflow_config")

            # Handle workflow_config - ensure it's a JSON string
            workflow_config_json = None
            if workflow_config is not None:
                if isinstance(workflow_config, str):
                    workflow_config_json = workflow_config
                else:
                    import json
                    workflow_config_json = json.dumps(workflow_config)

            new_task = Task(
                name=name,
                status=status,
                description=description,
                type=task_type,
                project_id=project_id,
                client_id=client_id,
                website_id=website_id,
                due_date=due_date,
                prompt_text=prompt_text,
                model_name=model_name,
                output_filename=output_filename,
                priority=priority,
                client_name=client_name,
                target_website=target_website,
                competitor_url=competitor_url,
                workflow_config=workflow_config_json,
            )
            logger.info(
                f"DEBUG: Created task with output_filename: {new_task.output_filename}"
            )
            db.session.add(new_task)
            db.session.commit()
            logger.info(
                f"Created new task '{new_task.name}' (ID: {new_task.id}, Project: {project_id})."
            )
            
            # Auto-start job if requested - now uses Celery instead of threads
            if auto_start_job:
                try:
                    # Create job_id for the task
                    from datetime import timezone
                    job_id = f"task_{new_task.id}"
                    new_task.job_id = job_id
                    new_task.status = "queued"  # Mark as queued, Celery will move to in-progress
                    new_task.updated_at = datetime.datetime.now(timezone.utc)

                    # Commit the task first to ensure it exists before Celery picks it up
                    db.session.commit()

                    # Submit task to Celery queue instead of daemon thread
                    try:
                        from backend.tasks.unified_task_executor import execute_unified_task

                        # Determine queue based on task type
                        task_type = new_task.type or 'content_generation'
                        queue_mapping = {
                            'file_generation': 'generation',
                            'csv_generation': 'generation',
                            'code_generation': 'generation',
                            'content_generation': 'generation',
                            'image_generation': 'generation',
                            'indexing': 'indexing',
                        }
                        queue = queue_mapping.get(task_type, 'default')

                        # Submit to Celery with appropriate queue
                        celery_result = execute_unified_task.apply_async(
                            args=[new_task.id],
                            queue=queue
                        )

                        logger.info(f"Auto-started task {new_task.id} via Celery with job_id {job_id}, celery_id={celery_result.id}")

                    except ImportError as import_error:
                        logger.warning(f"Celery task executor not available, falling back to thread: {import_error}")
                        # Fallback to thread-based execution if Celery not available
                        from backend.services.task_scheduler import _execute_task
                        import threading
                        import time

                        def safe_execute_task():
                            try:
                                time.sleep(0.1)
                                _execute_task(current_app._get_current_object(), new_task.id)
                            except Exception as thread_error:
                                logger.error(f"Thread execution failed for task {new_task.id}: {thread_error}")

                        thread = threading.Thread(target=safe_execute_task, daemon=True)
                        thread.start()
                        new_task.status = "pending"  # Thread-based uses pending
                        db.session.commit()

                except Exception as e:
                    logger.error(f"Failed to auto-start task {new_task.id}: {e}")
                    # Update task status to indicate auto-start failed
                    try:
                        from datetime import timezone
                        new_task.status = "failed"
                        new_task.error_message = f"Auto-start failed: {str(e)}"
                        new_task.updated_at = datetime.datetime.now(timezone.utc)
                        db.session.commit()
                        logger.info(f"Task {new_task.id} status updated to 'failed' due to auto-start failure")
                    except Exception as rollback_error:
                        logger.error(f"Failed to update task status after auto-start failure: {rollback_error}")
                        db.session.rollback()
            
            return jsonify(serialize_task(new_task)), 201
                
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass  # Ignore rollback errors
            logger.error(f"Error in create_task: {e}", exc_info=True)
            return jsonify({"error": "Failed to create task."}), 500
            
    except Exception as e:
        logger.error(f"General error in create_task: {e}", exc_info=True)
        return jsonify({"error": "Failed to create task."}), 500


@tasks_bp.route("/<int:task_id>", methods=["PUT"])
def update_task(task_id):
    """API endpoint to update an existing task."""
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info(f"API: Received PUT /api/tasks/{task_id} request")
    if not db or not Task:
        return jsonify({"error": "DB/Model unavailable."}), 500

    data = request.get_json()
    if not data:
        return jsonify({"error": "No update data provided"}), 400

    try:
        logger.info(f"API: Attempting to update task {task_id} with data: {data}")
        task = db.session.get(Task, task_id)
        if not task:
            logger.warning(f"API: Task {task_id} not found")
            return jsonify({"error": "Task not found"}), 404

        logger.info(f"API: Found task {task_id}, current status: {task.status}")
        updated_fields = []
        if "name" in data:
            task.name = data["name"]
            updated_fields.append("name")
        if "status" in data:
            if data["status"] not in ALLOWED_TASK_STATUSES:
                return jsonify({"error": f"Invalid status value."}), 400
            task.status = data["status"]
            updated_fields.append("status")
        if "description" in data:
            task.description = data["description"]
            updated_fields.append("description")
        if "type" in data:
            task.type = data["type"]
            updated_fields.append("type")
        if "prompt_text" in data:
            task.prompt_text = data["prompt_text"]
            updated_fields.append("prompt_text")
        if "model_name" in data:
            model_name_val = data["model_name"]
            if model_name_val:
                if not get_available_ollama_models:
                    logger.error("Model validation unavailable during update")
                    return (
                        jsonify({"error": "Model validation service unavailable"}),
                        500,
                    )
                models_data = get_available_ollama_models()
                if isinstance(models_data, dict) and models_data.get("error"):
                    return (
                        jsonify(
                            {
                                "error": f"Failed to validate model name: {models_data['error']}"
                            }
                        ),
                        502,
                    )
                available_names = [
                    m.get("name") for m in models_data if isinstance(m, dict)
                ]
                if model_name_val not in available_names:
                    return (
                        jsonify(
                            {
                                "error": f"Model '{model_name_val}' not found or available via Ollama API."
                            }
                        ),
                        404,
                    )
            task.model_name = model_name_val
            updated_fields.append("model_name")
        if "priority" in data:
            try:
                task.priority = int(data["priority"])
                updated_fields.append("priority")
            except (ValueError, TypeError):
                logger.warning(f"Invalid priority value: {data['priority']}")
        if "due_date" in data:
            try:
                task.due_date = (
                    datetime.datetime.fromisoformat(data["due_date"])
                    if data["due_date"]
                    else None
                )
                updated_fields.append("due_date")
            except (ValueError, TypeError):
                logger.warning(f"Invalid due_date value: {data['due_date']}")
        
        # Update additional fields
        if "client_name" in data:
            task.client_name = data["client_name"]
            updated_fields.append("client_name")
        if "target_website" in data:
            task.target_website = data["target_website"]
            updated_fields.append("target_website")
        if "competitor_url" in data:
            task.competitor_url = data["competitor_url"]
            updated_fields.append("competitor_url")
        if "workflow_config" in data:
            workflow_config = data["workflow_config"]
            if workflow_config is not None:
                if isinstance(workflow_config, str):
                    task.workflow_config = workflow_config
                else:
                    import json
                    task.workflow_config = json.dumps(workflow_config)
            else:
                task.workflow_config = None
            updated_fields.append("workflow_config")
        
        # --- ADDED: Handle project_id update ---
        if "project_id" in data:
            new_project_id = data["project_id"]
            if new_project_id is not None:
                try:
                    new_project_id = int(new_project_id)
                    # Optional: Check if project exists
                    if (
                        Project
                        and not db.session.query(Project.id)
                        .filter_by(id=new_project_id)
                        .scalar()
                    ):
                        logger.warning(
                            f"Attempted to link task {task_id} to non-existent project ID: {new_project_id}"
                        )
                        return (
                            jsonify(
                                {
                                    "error": f"Project with ID {new_project_id} not found."
                                }
                            ),
                            404,
                        )
                    task.project_id = new_project_id
                    updated_fields.append("project_id")
                except (ValueError, TypeError):
                    logger.warning(
                        f"Invalid project_id format received: {new_project_id}"
                    )
                    return (
                        jsonify(
                            {"error": "Invalid project_id format. Must be an integer."}
                        ),
                        400,
                    )
            else:  # Allow unlinking by setting project_id to null
                task.project_id = None
                updated_fields.append("project_id (unlinked)")
        # --- END ADDED ---

        if not updated_fields:
            return jsonify({"message": "No valid fields provided for update."}), 200

        db.session.commit()
        logger.info(
            f"Updated task ID {task_id}. Fields changed: {', '.join(updated_fields)}."
        )
        return jsonify(serialize_task(task)), 200
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in update_task (ID: {task_id}): {e}", exc_info=True)
        return jsonify({"error": "Failed to update task."}), 500


@tasks_bp.route("/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    """API endpoint to delete a task."""
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info(f"API: Received DELETE /api/tasks/{task_id} request")
    if not db or not Task:
        return jsonify({"error": "DB/Model unavailable."}), 500

    try:
        task = db.session.get(Task, task_id)
        if not task:
            return jsonify({"error": "Task not found"}), 404

        # Cancel any associated job before deletion
        if hasattr(task, "job_id") and task.job_id:
            try:
                from backend.utils.unified_progress_system import get_unified_progress

                progress_system = get_unified_progress()
                logger.info(f"Attempting to cancel job {task.job_id} for task {task_id}")

                # Try to cancel the process
                cancel_result = progress_system.cancel_process(task.job_id, f"Task {task_id} deleted")

                if cancel_result:
                    logger.info(f"Successfully cancelled job {task.job_id}")
                else:
                    logger.warning(f"Job {task.job_id} cancellation returned false - job may not exist or already completed")

            except ImportError as import_error:
                logger.warning(f"Progress system not available for job cancellation: {import_error}")
                # Continue with deletion - this is not critical for task deletion
            except AttributeError as attr_error:
                logger.warning(f"Progress system cancel_process method not available: {attr_error}")
                # Continue with deletion
            except Exception as cancel_error:
                logger.error(f"Unexpected error cancelling job {task.job_id}: {cancel_error}")
                # Continue with deletion - task deletion should not fail due to job cancellation issues

        db.session.delete(task)
        db.session.commit()
        logger.info(f"Deleted task ID {task_id} ('{task.name}').")
        return jsonify({"message": "Task deleted successfully"}), 200
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in delete_task (ID: {task_id}): {e}", exc_info=True)
        return jsonify({"error": "Failed to delete task."}), 500


@tasks_bp.route("/process-queue", methods=["POST"])
def process_queue():
    """Process all pending tasks sequentially."""
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received POST /api/tasks/process-queue request")
    if not db or not Task:
        return jsonify({"error": "DB/Model unavailable."}), 500

    try:
        from backend.services.task_scheduler import process_pending_tasks

        # Check if we have pending tasks first
        pending_tasks = db.session.query(Task).filter(Task.status == "pending").count()
        logger.info(f"Found {pending_tasks} pending tasks to process")
        
        if pending_tasks == 0:
            return jsonify({"message": "No pending tasks to process"}), 200

        process_pending_tasks(current_app._get_current_object())
        return jsonify({"message": "Task queue processed"}), 200
    except ImportError as import_error:
        logger.error(f"Failed to import task scheduler: {import_error}", exc_info=True)
        return jsonify({"error": "Task scheduler service unavailable"}), 500
    except Exception as e:
        logger.error(f"Error processing task queue: {e}", exc_info=True)
        return jsonify({"error": "Failed to process task queue."}), 500


# [CODEX PATCH APPLIED]: Force task processing endpoint
@tasks_bp.route("/force_process", methods=["POST"])
def force_process():
    from backend.services.task_scheduler import process_pending_tasks

    process_pending_tasks(current_app._get_current_object())
    return jsonify({"status": "processed"}), 200


@tasks_bp.route("/<int:task_id>/start", methods=["POST"])
def start_task(task_id):
    """API endpoint to start a task by submitting to Celery queue."""
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info(f"API: Received POST /api/tasks/{task_id}/start request")

    if not db or not Task:
        return jsonify({"error": "DB/Model unavailable."}), 500

    try:
        task = db.session.get(Task, task_id)
        if not task:
            return jsonify({"error": "Task not found"}), 404

        # Validate that task can be started
        if task.status in ["in-progress", "queued"]:
            return (
                jsonify({"error": f"Task is already {task.status}"}),
                400,
            )

        # Create job_id for the task
        from datetime import timezone
        job_id = f"task_{task_id}"
        task.job_id = job_id
        task.status = "queued"  # Mark as queued, Celery will move to in-progress
        task.updated_at = datetime.datetime.now(timezone.utc)

        db.session.commit()

        # Submit task to Celery queue
        try:
            from backend.tasks.unified_task_executor import execute_unified_task

            # Determine queue based on task type
            task_type = task.type or 'content_generation'
            queue_mapping = {
                'file_generation': 'generation',
                'csv_generation': 'generation',
                'code_generation': 'generation',
                'content_generation': 'generation',
                'image_generation': 'generation',
                'indexing': 'indexing',
            }
            queue = queue_mapping.get(task_type, 'default')

            # Submit to Celery
            celery_result = execute_unified_task.apply_async(
                args=[task_id],
                queue=queue
            )

            logger.info(f"Successfully queued task {task_id} with job_id {job_id}, celery_id={celery_result.id}")

            return (
                jsonify(
                    {
                        "success": True,
                        "message": f"Task {task_id} has been queued for execution",
                        "job_id": job_id,
                        "celery_task_id": celery_result.id,
                        "task": serialize_task(task),
                    }
                ),
                200,
            )

        except ImportError as import_error:
            logger.warning(f"Celery not available, falling back to thread: {import_error}")
            # Fallback to thread-based execution
            from backend.services.task_scheduler import _execute_task
            import threading

            task.status = "pending"
            db.session.commit()

            thread = threading.Thread(
                target=_execute_task,
                args=(current_app._get_current_object(), task_id),
                daemon=True
            )
            thread.start()

            return (
                jsonify(
                    {
                        "success": True,
                        "message": f"Task {task_id} has been started (thread fallback)",
                        "job_id": job_id,
                        "task": serialize_task(task),
                    }
                ),
                200,
            )

        except Exception as e:
            logger.error(f"Failed to submit task to Celery: {e}")
            # Revert status
            task.status = "pending"
            task.job_id = None
            db.session.commit()
            return jsonify({"error": f"Failed to queue task: {str(e)}"}), 500

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error starting task {task_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to start task"}), 500


@tasks_bp.route("/<int:task_id>/reprocess", methods=["POST"])
def reprocess_task(task_id):
    """API endpoint to reprocess a task by resetting it to pending status."""
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info(f"API: Received POST /api/tasks/{task_id}/reprocess request")

    if not db or not Task:
        return jsonify({"error": "DB/Model unavailable."}), 500

    try:
        task = db.session.get(Task, task_id)
        if not task:
            return jsonify({"error": "Task not found"}), 404

        # Validate that task can be reprocessed
        if task.status == "in-progress":
            return (
                jsonify({"error": "Cannot reprocess task that is currently running"}),
                400,
            )

        # Reset task to pending state for reprocessing
        task.status = "pending"
        task.progress = 0
        task.result = None
        task.error_message = None

        # Clear any existing job_id to allow fresh processing
        if hasattr(task, "job_id"):
            task.job_id = None

        # Update the updated_at timestamp
        # FIX BUG #32: Use timezone-aware datetime
        from datetime import timezone
        task.updated_at = datetime.datetime.now(timezone.utc)

        db.session.commit()

        logger.info(f"Successfully reset task {task_id} for reprocessing")

        return (
            jsonify(
                {
                    "success": True,
                    "message": f"Task {task_id} has been reset and queued for reprocessing",
                    "task": serialize_task(task),
                }
            ),
            200,
        )

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error reprocessing task {task_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to reprocess task"}), 500


@tasks_bp.route("/<int:task_id>/download", methods=["GET"])
def download_task_file(task_id):
    """API endpoint to download generated files from a completed task."""
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info(f"API: Received GET /api/tasks/{task_id}/download request")

    if not db or not Task:
        return jsonify({"error": "DB/Model unavailable."}), 500

    try:
        from flask import send_file
        from backend.config import OUTPUT_DIR
        import os

        task = db.session.get(Task, task_id)
        if not task:
            return jsonify({"error": "Task not found"}), 404

        # Check if task has generated a file
        if not task.output_filename:
            return jsonify({"error": "No output file available for this task"}), 404

        # Check if task is completed
        if task.status not in ["completed", "success"]:
            return (
                jsonify(
                    {
                        "error": "Task is not completed yet",
                        "status": task.status,
                        "message": "File will be available when task completes",
                    }
                ),
                400,
            )

        # Construct file path
        file_path = os.path.join(OUTPUT_DIR, task.output_filename)

        # Check if file exists
        if not os.path.exists(file_path):
            return (
                jsonify(
                    {
                        "error": "Generated file not found on disk",
                        "filename": task.output_filename,
                        "expected_path": file_path,
                    }
                ),
                404,
            )

        # Get file stats
        file_stats = os.stat(file_path)
        file_size = file_stats.st_size

        logger.info(
            f"Serving download for task {task_id}: {task.output_filename} ({file_size} bytes)"
        )

        # Send file with appropriate headers
        return send_file(
            file_path,
            as_attachment=True,
            download_name=task.output_filename,
            mimetype="application/octet-stream",
        )

    except Exception as e:
        logger.error(f"Error downloading file for task {task_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to download file"}), 500


@tasks_bp.route("/<int:task_id>/file-info", methods=["GET"])
def get_task_file_info(task_id):
    """API endpoint to get information about generated files from a task."""
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info(f"API: Received GET /api/tasks/{task_id}/file-info request")

    if not db or not Task:
        return jsonify({"error": "DB/Model unavailable."}), 500

    try:
        from backend.config import OUTPUT_DIR
        import os

        task = db.session.get(Task, task_id)
        if not task:
            return jsonify({"error": "Task not found"}), 404

        # Check if task has an output filename
        if not task.output_filename:
            return (
                jsonify(
                    {
                        "has_file": False,
                        "message": "No output file configured for this task",
                    }
                ),
                200,
            )

        # Construct file path
        file_path = os.path.join(OUTPUT_DIR, task.output_filename)

        # Check if file exists and get info
        if os.path.exists(file_path):
            file_stats = os.stat(file_path)
            file_size = file_stats.st_size
            # FIX BUG #33: Use timezone-aware datetime for file timestamp
            from datetime import timezone
            file_modified = datetime.datetime.fromtimestamp(
                file_stats.st_mtime, tz=timezone.utc
            ).isoformat()

            # Get file extension to determine type
            file_ext = os.path.splitext(task.output_filename)[1].lower()
            file_type = (
                "CSV" if file_ext == ".csv" else file_ext.upper().replace(".", "")
            )

            return (
                jsonify(
                    {
                        "has_file": True,
                        "filename": task.output_filename,
                        "file_size": file_size,
                        "file_size_mb": round(file_size / 1024 / 1024, 2),
                        "file_type": file_type,
                        "last_modified": file_modified,
                        "download_url": f"/api/tasks/{task_id}/download",
                        "task_status": task.status,
                        "can_download": task.status in ["completed", "success"],
                    }
                ),
                200,
            )
        else:
            return (
                jsonify(
                    {
                        "has_file": False,
                        "filename": task.output_filename,
                        "message": "File has not been generated yet",
                        "task_status": task.status,
                        "expected_path": file_path,
                    }
                ),
                200,
            )

    except Exception as e:
        logger.error(f"Error getting file info for task {task_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to get file information"}), 500


@tasks_bp.route("/<int:task_id>/duplicate", methods=["POST"])
def duplicate_task(task_id):
    """Duplicate an existing task with all its configuration."""
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info(f"API: Received POST /api/tasks/{task_id}/duplicate request")

    if not db or not Task:
        return jsonify({"error": "DB/Model unavailable."}), 500

    try:
        # Get the original task
        original_task = db.session.get(Task, task_id)
        if not original_task:
            return jsonify({"error": "Task not found"}), 404

        # Create a new task with the same configuration
        # FIX BUG #34: Use timezone-aware datetime for timestamps
        from datetime import timezone
        new_task = Task(
            name=f"{original_task.name} (Copy)",
            description=original_task.description,
            status="pending",  # Reset status to pending
            type=original_task.type,
            priority=original_task.priority,
            project_id=original_task.project_id,
            model_name=original_task.model_name,
            prompt_text=original_task.prompt_text,
            workflow_config=original_task.workflow_config,
            # Don't copy job_id, output_filename, or timestamps
            created_at=datetime.datetime.now(timezone.utc),
            updated_at=datetime.datetime.now(timezone.utc)
        )

        db.session.add(new_task)
        db.session.commit()

        logger.info(f"Task {task_id} duplicated as task {new_task.id}")

        return success_response(
            "Task duplicated successfully",
            {
                "original_task_id": task_id,
                "new_task_id": new_task.id,
                "new_task_name": new_task.name
            }
        )

    except SQLAlchemyError as e:
        logger.error(f"Database error duplicating task {task_id}: {e}", exc_info=True)
        db.session.rollback()
        return error_response("Database error occurred", 500, "DB_ERROR")
    except Exception as e:
        logger.error(f"Error duplicating task {task_id}: {e}", exc_info=True)
        return error_response("Failed to duplicate task", 500, "DUPLICATE_ERROR")
