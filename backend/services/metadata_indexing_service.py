# backend/services/metadata_indexing_service.py
# RAG Metadata Indexing Service - Makes jobs/clients/projects searchable

import logging
import json
from datetime import datetime
from typing import List, Dict, Optional, Any
from sqlalchemy.exc import SQLAlchemyError

from backend.utils.db_utils import DatabaseConnectionManager
from backend.models import db, Task, Document, Client, Project
from backend.utils.unified_job_metadata import unified_job_metadata

logger = logging.getLogger(__name__)


class MetadataIndexingService:
    """
    Service to index job, client, and project metadata into RAG system
    for comprehensive context awareness
    """

    def __init__(self):
        self.indexing_service = None
        self._initialize_indexing_service()

    def _initialize_indexing_service(self):
        """Initialize the indexing service for metadata"""
        try:
            # Import indexing service (handle potential import issues)
            from backend.services.indexing_service import add_text_to_index

            self.add_text_to_index = add_text_to_index
            logger.info("Metadata indexing service initialized successfully")
        except ImportError as e:
            logger.warning(f"Could not import indexing service: {e}")
            self.add_text_to_index = None

    def index_client_metadata(self, client_id: int) -> bool:
        """
        Index comprehensive client information including projects and jobs
        """
        try:
            with DatabaseConnectionManager():
                client = db.session.get(Client, client_id)
                if not client:
                    logger.warning(f"Client {client_id} not found for indexing")
                    return False

                # Get all projects for this client
                projects = (
                    db.session.query(Project).filter_by(client_id=client_id).all()
                )

                # Get all jobs for this client
                jobs = unified_job_metadata.get_jobs_for_client(client_id)

                # Create comprehensive client document
                client_doc = self._create_client_document(client, projects, jobs)

                # Index the document
                if self.add_text_to_index and client_doc:
                    try:
                        # Create a synthetic document for the client metadata
                        metadata_doc = type(
                            "Document",
                            (),
                            {
                                "id": f"client_{client_id}_metadata",
                                "filename": f"Client_{client.name}_Metadata.txt",
                                "type": "metadata",
                                "path": f"metadata/client_{client_id}.txt",
                            },
                        )()

                        success = self.add_text_to_index(client_doc, metadata_doc)

                        if success:
                            logger.info(
                                f"Successfully indexed metadata for client {client_id} ({client.name})"
                            )
                            return True
                        else:
                            logger.error(
                                f"Failed to index metadata for client {client_id}"
                            )
                            return False

                    except Exception as indexing_error:
                        logger.error(
                            f"Error indexing client {client_id} metadata: {indexing_error}"
                        )
                        return False
                else:
                    logger.warning(
                        "Indexing service not available, skipping client metadata indexing"
                    )
                    return False

        except Exception as e:
            logger.error(f"Error indexing client {client_id} metadata: {e}")
            return False

    def _create_client_document(self, client, projects: List, jobs: List) -> str:
        """
        Create a comprehensive text document about a client
        """
        doc_lines = [
            f"CLIENT INFORMATION",
            f"Client ID: {client.id}",
            f"Client Name: {client.name}",
            f"Description: {client.description or 'No description'}",
            f"Created: {client.created_at.strftime('%Y-%m-%d') if client.created_at else 'Unknown'}",
            f"",
            f"PROJECTS ({len(projects)} total):",
        ]

        for project in projects:
            doc_lines.extend(
                [
                    f"- Project: {project.name} (ID: {project.id})",
                    f"  Description: {project.description or 'No description'}",
                    f"  Created: {project.created_at.strftime('%Y-%m-%d') if project.created_at else 'Unknown'}",
                    f"",
                ]
            )

        doc_lines.extend([f"", f"JOBS AND TASKS ({len(jobs)} total):"])

        for job in jobs:
            task = job.get("task", {})
            progress = job.get("progress", {})
            workflow_config = job.get("workflow_config", {})

            doc_lines.extend(
                [
                    f"- Job: {task.get('name', 'Unknown')} (Task ID: {task.get('id', 'Unknown')})",
                    f"  Type: {task.get('type', 'Unknown')}",
                    f"  Status: {task.get('status', 'Unknown')}",
                    f"  Progress: {progress.get('percentage', 0)}% - {progress.get('status', 'unknown')}",
                    f"  Created: {task.get('created_at', 'Unknown')[:10] if task.get('created_at') else 'Unknown'}",
                    f"  Description: {task.get('description', 'No description')}",
                ]
            )

            if workflow_config:
                doc_lines.append(
                    f"  Configuration: {workflow_config.get('execution_type', 'standard')}"
                )
                if workflow_config.get("quantity"):
                    doc_lines.append(
                        f"  Quantity: {workflow_config.get('quantity')} items"
                    )
                if workflow_config.get("target_word_count"):
                    doc_lines.append(
                        f"  Target Words: {workflow_config.get('target_word_count')} per item"
                    )

            doc_lines.append("")

        # Add context for RAG queries
        doc_lines.extend(
            [
                f"",
                f"SEARCHABLE CONTEXT:",
                f"This client ({client.name}) has {len(projects)} projects and {len(jobs)} jobs/tasks.",
                f"Use this information to answer questions about {client.name}'s work, projects, and job status.",
                f"Client contact and project management information for {client.name}.",
                f"Job history and task status for all work related to {client.name}.",
            ]
        )

        return "\n".join(doc_lines)

    def index_project_metadata(self, project_id: int) -> bool:
        """
        Index comprehensive project information including jobs
        """
        try:
            with DatabaseConnectionManager():
                project = db.session.get(Project, project_id)
                if not project:
                    logger.warning(f"Project {project_id} not found for indexing")
                    return False

                # Get client information
                client = (
                    db.session.get(Client, project.client_id)
                    if project.client_id
                    else None
                )

                # Get all jobs for this project
                jobs = unified_job_metadata.get_jobs_for_project(project_id)

                # Get documents related to this project
                documents = (
                    db.session.query(Document).filter_by(project_id=project_id).all()
                )

                # Create comprehensive project document
                project_doc = self._create_project_document(
                    project, client, jobs, documents
                )

                # Index the document
                if self.add_text_to_index and project_doc:
                    try:
                        # Create a synthetic document for the project metadata
                        metadata_doc = type(
                            "Document",
                            (),
                            {
                                "id": f"project_{project_id}_metadata",
                                "filename": f"Project_{project.name}_Metadata.txt",
                                "type": "metadata",
                                "path": f"metadata/project_{project_id}.txt",
                            },
                        )()

                        success = self.add_text_to_index(project_doc, metadata_doc)

                        if success:
                            logger.info(
                                f"Successfully indexed metadata for project {project_id} ({project.name})"
                            )
                            return True
                        else:
                            logger.error(
                                f"Failed to index metadata for project {project_id}"
                            )
                            return False

                    except Exception as indexing_error:
                        logger.error(
                            f"Error indexing project {project_id} metadata: {indexing_error}"
                        )
                        return False
                else:
                    logger.warning(
                        "Indexing service not available, skipping project metadata indexing"
                    )
                    return False

        except Exception as e:
            logger.error(f"Error indexing project {project_id} metadata: {e}")
            return False

    def _create_project_document(
        self, project, client, jobs: List, documents: List
    ) -> str:
        """
        Create a comprehensive text document about a project
        """
        doc_lines = [
            f"PROJECT INFORMATION",
            f"Project ID: {project.id}",
            f"Project Name: {project.name}",
            f"Description: {project.description or 'No description'}",
            f"Created: {project.created_at.strftime('%Y-%m-%d') if project.created_at else 'Unknown'}",
            f"",
        ]

        if client:
            doc_lines.extend(
                [
                    f"CLIENT INFORMATION:",
                    f"Client: {client.name} (ID: {client.id})",
                    f"Client Description: {client.description or 'No description'}",
                    f"",
                ]
            )

        doc_lines.extend([f"DOCUMENTS ({len(documents)} total):"])

        for doc in documents:
            doc_lines.extend(
                [
                    f"- Document: {doc.filename} (ID: {doc.id})",
                    f"  Type: {doc.type or 'Unknown'}",
                    f"  Status: {doc.index_status or 'Unknown'}",
                    f"  Uploaded: {doc.uploaded_at.strftime('%Y-%m-%d') if doc.uploaded_at else 'Unknown'}",
                    f"",
                ]
            )

        doc_lines.extend([f"JOBS AND TASKS ({len(jobs)} total):"])

        for job in jobs:
            task = job.get("task", {})
            progress = job.get("progress", {})

            doc_lines.extend(
                [
                    f"- Job: {task.get('name', 'Unknown')} (Task ID: {task.get('id', 'Unknown')})",
                    f"  Type: {task.get('type', 'Unknown')}",
                    f"  Status: {task.get('status', 'Unknown')}",
                    f"  Progress: {progress.get('percentage', 0)}% - {progress.get('status', 'unknown')}",
                    f"  Description: {task.get('description', 'No description')}",
                    f"",
                ]
            )

        # Add context for RAG queries
        doc_lines.extend(
            [
                f"",
                f"SEARCHABLE CONTEXT:",
                f"This project ({project.name}) belongs to {client.name if client else 'Unknown Client'}.",
                f"It has {len(documents)} documents and {len(jobs)} jobs/tasks.",
                f"Use this information to answer questions about {project.name} work and status.",
                f"Project management and task status information for {project.name}.",
            ]
        )

        return "\n".join(doc_lines)

    def index_job_metadata(self, task_id: int) -> bool:
        """
        Index comprehensive job information
        """
        try:
            job_context = unified_job_metadata.get_job_with_context(task_id)
            if not job_context:
                logger.warning(f"Job context not found for task {task_id}")
                return False

            # Create comprehensive job document
            job_doc = self._create_job_document(job_context)

            # Index the document
            if self.add_text_to_index and job_doc:
                try:
                    task_name = job_context.get("task", {}).get("name", "Unknown")

                    # Create a synthetic document for the job metadata
                    metadata_doc = type(
                        "Document",
                        (),
                        {
                            "id": f"job_{task_id}_metadata",
                            "filename": f"Job_{task_name}_Metadata.txt",
                            "type": "metadata",
                            "path": f"metadata/job_{task_id}.txt",
                        },
                    )()

                    success = self.add_text_to_index(job_doc, metadata_doc)

                    if success:
                        logger.info(
                            f"Successfully indexed metadata for job {task_id} ({task_name})"
                        )
                        return True
                    else:
                        logger.error(f"Failed to index metadata for job {task_id}")
                        return False

                except Exception as indexing_error:
                    logger.error(
                        f"Error indexing job {task_id} metadata: {indexing_error}"
                    )
                    return False
            else:
                logger.warning(
                    "Indexing service not available, skipping job metadata indexing"
                )
                return False

        except Exception as e:
            logger.error(f"Error indexing job {task_id} metadata: {e}")
            return False

    def _create_job_document(self, job_context: Dict) -> str:
        """
        Create a comprehensive text document about a job
        """
        task = job_context.get("task", {})
        client = job_context.get("client")
        project = job_context.get("project")
        progress = job_context.get("progress")
        workflow_config = job_context.get("workflow_config", {})

        doc_lines = [
            f"JOB INFORMATION",
            f"Job ID: {task.get('id', 'Unknown')}",
            f"Job Name: {task.get('name', 'Unknown')}",
            f"Type: {task.get('type', 'Unknown')}",
            f"Status: {task.get('status', 'Unknown')}",
            f"Priority: {task.get('priority', 'Unknown')}",
            f"Description: {task.get('description', 'No description')}",
            f"Created: {task.get('created_at', 'Unknown')[:10] if task.get('created_at') else 'Unknown'}",
            f"Updated: {task.get('updated_at', 'Unknown')[:10] if task.get('updated_at') else 'Unknown'}",
            f"",
        ]

        if client:
            doc_lines.extend(
                [
                    f"CLIENT INFORMATION:",
                    f"Client: {client.get('name', 'Unknown')} (ID: {client.get('id', 'Unknown')})",
                    f"Client Description: {client.get('description', 'No description')}",
                    f"",
                ]
            )

        if project:
            doc_lines.extend(
                [
                    f"PROJECT INFORMATION:",
                    f"Project: {project.get('name', 'Unknown')} (ID: {project.get('id', 'Unknown')})",
                    f"Project Description: {project.get('description', 'No description')}",
                    f"",
                ]
            )

        if progress:
            doc_lines.extend(
                [
                    f"PROGRESS INFORMATION:",
                    f"Progress: {progress.get('percentage', 0)}%",
                    f"Status: {progress.get('status', 'unknown')}",
                    f"Message: {progress.get('message', 'No message')}",
                    f"Last Update: {progress.get('last_update', 'Unknown')[:16] if progress.get('last_update') else 'Unknown'}",
                    f"",
                ]
            )

        if workflow_config:
            doc_lines.extend(
                [
                    f"WORKFLOW CONFIGURATION:",
                    f"Execution Type: {workflow_config.get('execution_type', 'standard')}",
                ]
            )

            if workflow_config.get("quantity"):
                doc_lines.append(f"Quantity: {workflow_config.get('quantity')} items")
            if workflow_config.get("target_word_count"):
                doc_lines.append(
                    f"Target Word Count: {workflow_config.get('target_word_count')} per item"
                )
            if workflow_config.get("output_filename"):
                doc_lines.append(
                    f"Output File: {workflow_config.get('output_filename')}"
                )

            doc_lines.append("")

        # Add context for RAG queries
        client_name = (
            client.get("name", "Unknown Client") if client else "Unknown Client"
        )
        project_name = (
            project.get("name", "Unknown Project") if project else "Unknown Project"
        )

        doc_lines.extend(
            [
                f"SEARCHABLE CONTEXT:",
                f"This job ({task.get('name', 'Unknown')}) belongs to {client_name} in project {project_name}.",
                f"Current status: {task.get('status', 'Unknown')} with {progress.get('percentage', 0)}% progress.",
                f"Job type: {task.get('type', 'Unknown')} - {task.get('description', 'No description')}",
                f"Use this information to answer questions about this specific job's status and details.",
            ]
        )

        return "\n".join(doc_lines)

    def reindex_all_metadata(self) -> Dict[str, int]:
        """
        Reindex all client, project, and job metadata
        """
        results = {
            "clients_indexed": 0,
            "projects_indexed": 0,
            "jobs_indexed": 0,
            "errors": 0,
        }

        try:
            with DatabaseConnectionManager():
                # Index all clients
                clients = db.session.query(Client).all()
                for client in clients:
                    try:
                        if self.index_client_metadata(client.id):
                            results["clients_indexed"] += 1
                        else:
                            results["errors"] += 1
                    except Exception as e:
                        logger.error(f"Error indexing client {client.id}: {e}")
                        results["errors"] += 1

                # Index all projects
                projects = db.session.query(Project).all()
                for project in projects:
                    try:
                        if self.index_project_metadata(project.id):
                            results["projects_indexed"] += 1
                        else:
                            results["errors"] += 1
                    except Exception as e:
                        logger.error(f"Error indexing project {project.id}: {e}")
                        results["errors"] += 1

                # Index all jobs
                tasks = db.session.query(Task).all()
                for task in tasks:
                    try:
                        if self.index_job_metadata(task.id):
                            results["jobs_indexed"] += 1
                        else:
                            results["errors"] += 1
                    except Exception as e:
                        logger.error(f"Error indexing job {task.id}: {e}")
                        results["errors"] += 1

        except Exception as e:
            logger.error(f"Error during full metadata reindexing: {e}")
            results["errors"] += 1

        logger.info(f"Metadata reindexing completed: {results}")
        return results


# Global instance
metadata_indexing_service = MetadataIndexingService()
