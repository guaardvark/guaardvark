# backend/services/entity_indexing_service.py
# Entity Indexing Service - Indexes Clients, Projects, Websites, Tasks as searchable documents
# Version 1.0: Initial implementation for entity context in LLM

import logging
import json
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any
from flask import current_app

from llama_index.core import Document as LlamaDocument
from llama_index.core.schema import TextNode

from backend.models import Client, Project, Website, Task, Document as DBDocument, db
from backend.services.indexing_service import get_or_create_index, _index_operation_lock
from backend.utils.unified_progress_system import get_unified_progress, ProcessType

logger = logging.getLogger(__name__)


class EntityIndexingService:
    """Service for indexing entities as searchable documents"""

    def __init__(self):
        self.index = None
        self.storage_context = None
        # Don't initialize index during module import - use lazy initialization

    def _ensure_index(self):
        """Ensure the index is available (lazy initialization)"""
        if self.index is None:
            try:
                # BUG FIX #15: Handle new return format from get_or_create_index
                result = get_or_create_index()
                self.index = result[0] if isinstance(result, tuple) else result
                if self.index is None:
                    raise RuntimeError("Failed to create or load index")

                # Get storage context from the indexing service
                from backend.services.indexing_service import (
                    storage_context as global_storage_context,
                )

                self.storage_context = global_storage_context

                if self.storage_context is None:
                    logger.warning(
                        "Storage context not available from indexing service"
                    )

            except Exception as e:
                logger.error(
                    f"Failed to initialize index in EntityIndexingService: {e}"
                )
                self.index = None
                self.storage_context = None
                raise RuntimeError(f"Index initialization failed: {e}")

    def index_all_entities(self) -> Dict[str, int]:
        """Index all entities in the database"""
        logger.info("Starting full entity indexing...")

        # Ensure index is initialized
        self._ensure_index()

        # Count total entities for progress tracking
        total_entities = (
            db.session.query(Client).count()
            + db.session.query(Project).count()
            + db.session.query(Website).count()
            + db.session.query(Task).count()
        )

        # Create unified progress tracking
        progress_system = get_unified_progress()
        process_id = progress_system.create_process(
            ProcessType.INDEXING,
            description=f"Indexing {total_entities} entities",
            additional_data={
                "total_entities": total_entities,
                "target_count": total_entities,
                "generated_count": 0,
                "entity_types": ["clients", "projects", "websites", "tasks"],
            },
        )

        results = {"clients": 0, "projects": 0, "websites": 0, "tasks": 0, "errors": 0}

        processed_count = 0

        try:
            # Index all clients
            clients = db.session.query(Client).all()
            for client in clients:
                progress_pct = (
                    int((processed_count / total_entities) * 100)
                    if total_entities > 0
                    else 0
                )
                progress_system.update_process(
                    process_id,
                    progress_pct,
                    f"Indexing client: {client.name}",
                    additional_data={
                        "generated_count": processed_count,
                        "entity_type": "client",
                        "entity_name": client.name,
                    },
                )
                if self.index_client(client):
                    results["clients"] += 1
                else:
                    results["errors"] += 1
                processed_count += 1

            # Index all projects
            projects = db.session.query(Project).all()
            for project in projects:
                progress_pct = (
                    int((processed_count / total_entities) * 100)
                    if total_entities > 0
                    else 0
                )
                progress_system.update_process(
                    process_id,
                    progress_pct,
                    f"Indexing project: {project.name}",
                    additional_data={
                        "generated_count": processed_count,
                        "entity_type": "project",
                        "entity_name": project.name,
                    },
                )
                if self.index_project(project):
                    results["projects"] += 1
                else:
                    results["errors"] += 1
                processed_count += 1

            # Index all websites
            websites = db.session.query(Website).all()
            for website in websites:
                progress_pct = (
                    int((processed_count / total_entities) * 100)
                    if total_entities > 0
                    else 0
                )
                progress_system.update_process(
                    process_id,
                    progress_pct,
                    f"Indexing website: {website.url}",
                    additional_data={
                        "generated_count": processed_count,
                        "entity_type": "website",
                        "entity_name": website.url,
                    },
                )
                if self.index_website(website):
                    results["websites"] += 1
                else:
                    results["errors"] += 1
                processed_count += 1

            # Index all tasks
            tasks = db.session.query(Task).all()
            for task in tasks:
                progress_pct = (
                    int((processed_count / total_entities) * 100)
                    if total_entities > 0
                    else 0
                )
                progress_system.update_process(
                    process_id,
                    progress_pct,
                    f"Indexing task: {task.name}",
                    additional_data={
                        "generated_count": processed_count,
                        "entity_type": "task",
                        "entity_name": task.name,
                    },
                )
                if self.index_task(task):
                    results["tasks"] += 1
                else:
                    results["errors"] += 1
                processed_count += 1

            # Persist changes
            if self.storage_context:
                progress_system.update_process(
                    process_id, 95, "Persisting entity index..."
                )
                self.storage_context.persist()
                logger.info("Entity index changes persisted")

            # Complete progress tracking
            progress_system.complete_process(
                process_id,
                f"Indexed {processed_count} entities ({results['errors']} errors)",
                additional_data={
                    "generated_count": processed_count,
                    "clients": results["clients"],
                    "projects": results["projects"],
                    "websites": results["websites"],
                    "tasks": results["tasks"],
                    "errors": results["errors"],
                },
            )

            logger.info(f"Entity indexing complete: {results}")
            return results

        except Exception as e:
            logger.error(f"Error in full entity indexing: {e}", exc_info=True)

            # Report error to progress system
            progress_system.error_process(
                process_id, f"Entity indexing failed: {str(e)[:100]}"
            )

            results["errors"] += 1
            return results

    def index_client(self, client: Client) -> bool:
        """Index a single client"""
        try:
            if not self.index:
                self._ensure_index()

            # Create comprehensive client summary
            client_summary = self._create_client_summary(client)

            # Create LlamaDocument with null-safe metadata
            doc = LlamaDocument(
                text=client_summary,
                metadata={
                    "entity_type": "client",
                    "entity_id": str(client.id),
                    "client_id": str(client.id),
                    "client_name": client.name or "Unknown",
                    "client_email": client.email or "",
                    "client_phone": client.phone or "",
                    "indexed_at": datetime.now().isoformat(),
                    "content_type": "entity_summary",
                    "searchable_content": f"client {client.name or 'unknown'} {client.email or ''} {client.phone or ''} {client.notes or ''}".lower(),
                },
            )
            doc.id_ = f"client_{client.id}"

            # Convert to node and insert
            node = TextNode(text=client_summary, metadata=doc.metadata, id_=doc.id_)

            if self.index is None:
                raise RuntimeError("Index not available for client indexing")

            # VECTOR INDEX LOCK CONTENTION FIX: Use thread lock for concurrent operations
            with _index_operation_lock:
                self.index.insert_nodes([node])
            logger.info(f"Indexed client: {client.name} (ID: {client.id})")
            return True

        except Exception as e:
            logger.error(f"Error indexing client {client.id}: {e}", exc_info=True)
            return False

    def index_project(self, project: Project) -> bool:
        """Index a single project"""
        try:
            if not self.index:
                self._ensure_index()

            # Create comprehensive project summary
            project_summary = self._create_project_summary(project)

            # Get client info if available
            client_info = {}
            if project.client_ref:
                client_info = {
                    "client_id": str(project.client_ref.id),
                    "client_name": project.client_ref.name,
                    "client_email": project.client_ref.email,
                    "client_phone": project.client_ref.phone,
                }

            # Create LlamaDocument
            doc = LlamaDocument(
                text=project_summary,
                metadata={
                    "entity_type": "project",
                    "entity_id": str(project.id),
                    "project_id": str(project.id),
                    "project_name": project.name,
                    "project_description": project.description,
                    "indexed_at": datetime.now().isoformat(),
                    "content_type": "entity_summary",
                    "searchable_content": f"project {project.name} {project.description or ''}".lower(),
                    **client_info,
                },
            )
            doc.id_ = f"project_{project.id}"

            # Convert to node and insert
            node = TextNode(text=project_summary, metadata=doc.metadata, id_=doc.id_)

            if self.index is None:
                raise RuntimeError("Index not available for project indexing")

            # VECTOR INDEX LOCK CONTENTION FIX: Use thread lock for concurrent operations
            with _index_operation_lock:
                self.index.insert_nodes([node])
            logger.info(f"Indexed project: {project.name} (ID: {project.id})")
            return True

        except Exception as e:
            logger.error(f"Error indexing project {project.id}: {e}", exc_info=True)
            return False

    def index_website(self, website: Website) -> bool:
        """Index a single website"""
        try:
            if not self.index:
                self._ensure_index()

            # Create comprehensive website summary
            website_summary = self._create_website_summary(website)

            # Get related entity info
            entity_info = {}
            if website.client_ref:
                entity_info.update(
                    {
                        "client_id": str(website.client_ref.id),
                        "client_name": website.client_ref.name,
                    }
                )
            if website.project:
                entity_info.update(
                    {
                        "project_id": str(website.project.id),
                        "project_name": website.project.name,
                    }
                )

            # Create LlamaDocument
            doc = LlamaDocument(
                text=website_summary,
                metadata={
                    "entity_type": "website",
                    "entity_id": str(website.id),
                    "website_id": str(website.id),
                    "website_url": website.url,
                    "website_status": website.status,
                    "indexed_at": datetime.now().isoformat(),
                    "content_type": "entity_summary",
                    "searchable_content": f"website {website.url} {website.status}".lower(),
                    **entity_info,
                },
            )
            doc.id_ = f"website_{website.id}"

            # Convert to node and insert
            node = TextNode(text=website_summary, metadata=doc.metadata, id_=doc.id_)

            self.index.insert_nodes([node])
            logger.info(f"Indexed website: {website.url} (ID: {website.id})")
            return True

        except Exception as e:
            logger.error(f"Error indexing website {website.id}: {e}", exc_info=True)
            return False

    def index_task(self, task: Task) -> bool:
        """Index a single task"""
        try:
            if not self.index:
                self._ensure_index()

            # Create comprehensive task summary
            task_summary = self._create_task_summary(task)

            # Get related entity info
            entity_info = {}
            if task.project:
                entity_info.update(
                    {
                        "project_id": str(task.project.id),
                        "project_name": task.project.name,
                    }
                )
                if task.project.client_ref:
                    entity_info.update(
                        {
                            "client_id": str(task.project.client_ref.id),
                            "client_name": task.project.client_ref.name,
                        }
                    )

            # Create LlamaDocument
            doc = LlamaDocument(
                text=task_summary,
                metadata={
                    "entity_type": "task",
                    "entity_id": str(task.id),
                    "task_id": str(task.id),
                    "task_name": task.name,
                    "task_status": task.status,
                    "task_priority": str(task.priority),
                    "task_type": task.type,
                    "indexed_at": datetime.now().isoformat(),
                    "content_type": "entity_summary",
                    "searchable_content": f"task {task.name} {task.description or ''} {task.status} {task.type or ''}".lower(),
                    **entity_info,
                },
            )
            doc.id_ = f"task_{task.id}"

            # Convert to node and insert
            node = TextNode(text=task_summary, metadata=doc.metadata, id_=doc.id_)

            self.index.insert_nodes([node])
            logger.info(f"Indexed task: {task.name} (ID: {task.id})")
            return True

        except Exception as e:
            logger.error(f"Error indexing task {task.id}: {e}", exc_info=True)
            return False

    def _create_client_summary(self, client: Client) -> str:
        """Create a comprehensive summary of a client"""
        summary_parts = [
            f"Client: {client.name}",
            f"Client ID: {client.id}",
        ]

        if client.email:
            summary_parts.append(f"Email: {client.email}")
        if client.phone:
            summary_parts.append(f"Phone: {client.phone}")
        if client.notes:
            summary_parts.append(f"Notes: {client.notes}")

        # Add project information
        project_count = client.projects.count()
        if project_count > 0:
            summary_parts.append(f"Projects: {project_count} total")
            for project in client.projects.limit(5):
                summary_parts.append(
                    f"  - {project.name}: {project.description or 'No description'}"
                )

        # Add website information
        website_count = client.websites.count()
        if website_count > 0:
            summary_parts.append(f"Websites: {website_count} total")
            for website in client.websites.limit(5):
                summary_parts.append(f"  - {website.url} ({website.status})")

        # Add document information
        doc_count = sum(project.documents.count() for project in client.projects)
        if doc_count > 0:
            summary_parts.append(f"Documents: {doc_count} total across all projects")

        summary_parts.append(f"Created: {client.created_at}")
        summary_parts.append(f"Updated: {client.updated_at}")

        return "\n".join(summary_parts)

    def _create_project_summary(self, project: Project) -> str:
        """Create a comprehensive summary of a project"""
        summary_parts = [
            f"Project: {project.name}",
            f"Project ID: {project.id}",
        ]

        if project.description:
            summary_parts.append(f"Description: {project.description}")

        # Add client information
        if project.client_ref:
            summary_parts.append(
                f"Client: {project.client_ref.name} (ID: {project.client_ref.id})"
            )
            if project.client_ref.email:
                summary_parts.append(f"Client Email: {project.client_ref.email}")

        # Add document information
        doc_count = project.documents.count()
        if doc_count > 0:
            summary_parts.append(f"Documents: {doc_count} total")
            for doc in project.documents.limit(5):
                summary_parts.append(
                    f"  - {doc.filename} ({doc.type or 'unknown type'})"
                )

        # Add website information
        website_count = project.websites.count()
        if website_count > 0:
            summary_parts.append(f"Websites: {website_count} total")
            for website in project.websites.limit(5):
                summary_parts.append(f"  - {website.url} ({website.status})")

        # Add task information
        task_count = project.tasks.count()
        if task_count > 0:
            summary_parts.append(f"Tasks: {task_count} total")
            for task in project.tasks.limit(5):
                summary_parts.append(f"  - {task.name} ({task.status})")

        summary_parts.append(f"Created: {project.created_at}")
        summary_parts.append(f"Updated: {project.updated_at}")

        return "\n".join(summary_parts)

    def _create_website_summary(self, website: Website) -> str:
        """Create a comprehensive summary of a website"""
        summary_parts = [
            f"Website: {website.url}",
            f"Website ID: {website.id}",
            f"Status: {website.status}",
        ]

        if website.sitemap:
            summary_parts.append(f"Sitemap: {website.sitemap}")

        # Add client information
        if website.client_ref:
            summary_parts.append(
                f"Client: {website.client_ref.name} (ID: {website.client_ref.id})"
            )

        # Add project information
        if website.project:
            summary_parts.append(
                f"Project: {website.project.name} (ID: {website.project.id})"
            )

        # Add document information
        doc_count = website.documents.count()
        if doc_count > 0:
            summary_parts.append(f"Documents: {doc_count} total")
            for doc in website.documents.limit(5):
                summary_parts.append(
                    f"  - {doc.filename} ({doc.type or 'unknown type'})"
                )

        if website.last_crawled:
            summary_parts.append(f"Last Crawled: {website.last_crawled}")

        summary_parts.append(f"Created: {website.created_at}")
        summary_parts.append(f"Updated: {website.updated_at}")

        return "\n".join(summary_parts)

    def _create_task_summary(self, task: Task) -> str:
        """Create a comprehensive summary of a task"""
        summary_parts = [
            f"Task: {task.name}",
            f"Task ID: {task.id}",
            f"Status: {task.status}",
            f"Priority: {task.priority}",
        ]

        if task.description:
            summary_parts.append(f"Description: {task.description}")

        if task.type:
            summary_parts.append(f"Type: {task.type}")

        if task.due_date:
            summary_parts.append(f"Due Date: {task.due_date}")

        # Add project information
        if task.project:
            summary_parts.append(
                f"Project: {task.project.name} (ID: {task.project.id})"
            )
            if task.project.client_ref:
                summary_parts.append(
                    f"Client: {task.project.client_ref.name} (ID: {task.project.client_ref.id})"
                )

        if task.prompt_text:
            summary_parts.append(f"Prompt: {task.prompt_text[:200]}...")

        if task.model_name:
            summary_parts.append(f"Model: {task.model_name}")

        if task.job_id:
            summary_parts.append(f"Job ID: {task.job_id}")

        if task.output_filename:
            summary_parts.append(f"Output File: {task.output_filename}")

        summary_parts.append(f"Created: {task.created_at}")
        summary_parts.append(f"Updated: {task.updated_at}")

        return "\n".join(summary_parts)

    def update_entity_index(self, entity_type: str, entity_id: int) -> bool:
        """Update index for a specific entity when it changes"""
        try:
            if entity_type == "client":
                client = db.session.get(Client, entity_id)
                if client:
                    return self.index_client(client)
            elif entity_type == "project":
                project = db.session.get(Project, entity_id)
                if project:
                    return self.index_project(project)
            elif entity_type == "website":
                website = db.session.get(Website, entity_id)
                if website:
                    return self.index_website(website)
            elif entity_type == "task":
                task = db.session.get(Task, entity_id)
                if task:
                    return self.index_task(task)

            return False
        except Exception as e:
            logger.error(
                f"Error updating {entity_type} index for ID {entity_id}: {e}",
                exc_info=True,
            )
            return False


# Global instance
entity_indexing_service = EntityIndexingService()


def get_entity_indexing_service() -> EntityIndexingService:
    """Get the global entity indexing service"""
    return entity_indexing_service
