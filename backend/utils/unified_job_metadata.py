# backend/utils/unified_job_metadata.py
# Unified Job Metadata Storage and Retrieval System

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from sqlalchemy.exc import SQLAlchemyError

from backend.utils.db_utils import DatabaseConnectionManager
from backend.models import db, Task, Document, Client, Project
from backend.utils.unified_progress_system import get_unified_progress

logger = logging.getLogger(__name__)


class UnifiedJobMetadata:
    """
    Centralized metadata management for all jobs, tasks, and processes.
    Bridges the gap between progress tracking and database persistence.
    """

    def __init__(self):
        self.progress_system = get_unified_progress()

    def create_job_record(
        self,
        job_type: str,
        name: str,
        description: str = "",
        client_id: Optional[int] = None,
        project_id: Optional[int] = None,
        workflow_config: Optional[Dict] = None,
        metadata: Optional[Dict] = None,
        progress_job_id: Optional[str] = None,
    ) -> Optional[int]:
        """
        Create a unified job record that links database and progress systems
        """
        try:
            # Prepare workflow config and metadata
            workflow_json = json.dumps(workflow_config) if workflow_config else None

            # Create task record
            task = Task(
                name=name,
                description=description,
                type=job_type,
                status="pending",
                priority=metadata.get("priority", 2) if metadata else 2,
                project_id=project_id,
                job_id=progress_job_id,
                workflow_config=workflow_json,
                prompt_text=(
                    workflow_config.get("prompt_text") if workflow_config else None
                ),
                output_filename=(
                    workflow_config.get("output_filename") if workflow_config else None
                ),
                model_name=(
                    workflow_config.get("model_name") if workflow_config else None
                ),
                created_at=datetime.now(),
            )

            db.session.add(task)
            db.session.flush()  # Get the task.id without committing

            task_id = task.id

            # Update progress system with database link
            if progress_job_id:
                try:
                    self.progress_system.update_process(
                        progress_job_id,
                        5,
                        f"Database record created (Task ID: {task_id})",
                    )
                except Exception as progress_error:
                    logger.warning(
                        f"Could not update progress for job {progress_job_id}: {progress_error}"
                    )

            logger.info(
                f"Created unified job record: Task {task_id}, Progress {progress_job_id}"
            )
            return task_id

        except SQLAlchemyError as e:
            logger.error(f"Database error creating job record: {e}")
            return None
        except Exception as e:
            logger.error(f"Error creating job record: {e}")
            return None

    def get_job_with_context(self, task_id: int) -> Optional[Dict]:
        """
        Get comprehensive job information including client/project context
        """
        try:
            with DatabaseConnectionManager():
                task = db.session.get(Task, task_id)
                if not task:
                    return None

                # Get related entities
                project = (
                    db.session.get(Project, task.project_id)
                    if task.project_id
                    else None
                )
                client = None
                if project and project.client_id:
                    client = db.session.get(Client, project.client_id)

                # Get progress information
                progress_info = None
                if task.job_id:
                    try:
                        progress = self.progress_system.get_process(task.job_id)
                        if progress:
                            progress_info = {
                                "progress_id": task.job_id,
                                "percentage": progress.progress,
                                "status": progress.status.value,
                                "message": progress.message,
                                "last_update": (
                                    progress.timestamp.isoformat()
                                    if progress.timestamp
                                    else None
                                ),
                            }
                    except Exception as progress_error:
                        logger.warning(
                            f"Could not get progress for job {task.job_id}: {progress_error}"
                        )

                # Parse workflow config
                workflow_config = None
                if task.workflow_config:
                    try:
                        workflow_config = json.loads(task.workflow_config)
                    except json.JSONDecodeError as e:
                        logger.warning(
                            f"Invalid workflow config JSON for task {task_id}: {e}"
                        )

                return {
                    "task": {
                        "id": task.id,
                        "name": task.name,
                        "description": task.description,
                        "type": task.type,
                        "status": task.status,
                        "priority": task.priority,
                        "created_at": (
                            task.created_at.isoformat() if task.created_at else None
                        ),
                        "updated_at": (
                            task.updated_at.isoformat() if task.updated_at else None
                        ),
                        "output_filename": task.output_filename,
                        "model_name": task.model_name,
                    },
                    "client": (
                        {
                            "id": client.id,
                            "name": client.name,
                            "description": client.description,
                        }
                        if client
                        else None
                    ),
                    "project": (
                        {
                            "id": project.id,
                            "name": project.name,
                            "description": project.description,
                            "client_id": project.client_id,
                        }
                        if project
                        else None
                    ),
                    "progress": progress_info,
                    "workflow_config": workflow_config,
                }

        except Exception as e:
            logger.error(f"Error getting job context for task {task_id}: {e}")
            return None

    def get_jobs_for_client(self, client_id: int) -> List[Dict]:
        """
        Get all jobs associated with a client
        """
        try:
            with DatabaseConnectionManager():
                # Get projects for client
                projects = (
                    db.session.query(Project).filter_by(client_id=client_id).all()
                )
                project_ids = [p.id for p in projects]

                # Get tasks for those projects
                tasks = (
                    db.session.query(Task)
                    .filter(Task.project_id.in_(project_ids))
                    .all()
                )

                jobs = []
                for task in tasks:
                    job_context = self.get_job_with_context(task.id)
                    if job_context:
                        jobs.append(job_context)

                return jobs

        except Exception as e:
            logger.error(f"Error getting jobs for client {client_id}: {e}")
            return []

    def get_jobs_for_project(self, project_id: int) -> List[Dict]:
        """
        Get all jobs associated with a project
        """
        try:
            with DatabaseConnectionManager():
                tasks = db.session.query(Task).filter_by(project_id=project_id).all()

                jobs = []
                for task in tasks:
                    job_context = self.get_job_with_context(task.id)
                    if job_context:
                        jobs.append(job_context)

                return jobs

        except Exception as e:
            logger.error(f"Error getting jobs for project {project_id}: {e}")
            return []

    def update_job_status(
        self,
        task_id: int,
        status: str,
        message: Optional[str] = None,
        progress_percentage: Optional[int] = None,
    ) -> bool:
        """
        Update job status in both database and progress system
        """
        try:
            with DatabaseConnectionManager():
                task = db.session.get(Task, task_id)
                if not task:
                    logger.warning(f"Task {task_id} not found for status update")
                    return False

                # Update database
                task.status = status
                task.updated_at = datetime.now()
                db.session.commit()

                # Update progress system
                if task.job_id:
                    try:
                        if status in ["completed", "complete"]:
                            self.progress_system.complete_process(
                                task.job_id, message or f"Task {task_id} completed"
                            )
                        elif status in ["failed", "error"]:
                            self.progress_system.error_process(
                                task.job_id, message or f"Task {task_id} failed"
                            )
                        elif progress_percentage is not None:
                            self.progress_system.update_process(
                                task.job_id,
                                progress_percentage,
                                message or f"Task {task_id} at {progress_percentage}%",
                            )
                    except Exception as progress_error:
                        logger.warning(
                            f"Could not update progress for job {task.job_id}: {progress_error}"
                        )

                logger.info(f"Updated job status: Task {task_id} -> {status}")
                return True

        except Exception as e:
            logger.error(f"Error updating job status for task {task_id}: {e}")
            return False

    def get_active_jobs_summary(self) -> Dict:
        """
        Get summary of all active jobs across the system
        """
        try:
            with DatabaseConnectionManager():
                # Get active tasks from database
                active_tasks = (
                    db.session.query(Task)
                    .filter(
                        Task.status.in_(
                            ["pending", "in-progress", "processing", "running"]
                        )
                    )
                    .all()
                )

                # Get progress information for active jobs
                active_jobs = []
                for task in active_tasks:
                    job_context = self.get_job_with_context(task.id)
                    if job_context:
                        active_jobs.append(job_context)

                # Get all active progress processes
                all_progress = self.progress_system.get_all_active_processes()

                return {
                    "database_active_jobs": len(active_tasks),
                    "progress_active_jobs": len(all_progress),
                    "unified_active_jobs": active_jobs,
                    "summary": {
                        "total_active": len(active_jobs),
                        "by_type": {},
                        "by_status": {},
                        "by_client": {},
                    },
                }

        except Exception as e:
            logger.error(f"Error getting active jobs summary: {e}")
            return {
                "database_active_jobs": 0,
                "progress_active_jobs": 0,
                "unified_active_jobs": [],
                "summary": {
                    "total_active": 0,
                    "by_type": {},
                    "by_status": {},
                    "by_client": {},
                },
                "error": str(e),
            }


# Global instance
unified_job_metadata = UnifiedJobMetadata()
