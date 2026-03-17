# backend/services/task_handlers/indexing_handler.py
# Handler for document indexing tasks
# Version 2.0 - Full implementation wrapping indexing_service and entity_indexing_service

import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional, Callable, List

from .base_handler import BaseTaskHandler, TaskResult, TaskResultStatus

logger = logging.getLogger(__name__)


class DocumentIndexingHandler(BaseTaskHandler):
    """
    Handler for document indexing operations.
    Wraps: indexing_service.py, entity_indexing_service.py, celery_tasks_isolated.py
    """

    @property
    def handler_name(self) -> str:
        return "document_indexing"

    @property
    def display_name(self) -> str:
        return "Document Indexing"

    @property
    def process_type(self) -> str:
        return "indexing"

    @property
    def celery_queue(self) -> str:
        return "indexing"

    @property
    def default_priority(self) -> int:
        return 5  # Medium priority for indexing

    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "required": ["indexing_type"],
            "properties": {
                "indexing_type": {
                    "type": "string",
                    "enum": [
                        "document",
                        "entity",
                        "metadata",
                        "full_reindex",
                        "batch_documents",
                    ],
                    "description": "Type of indexing operation",
                },
                "document_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Document IDs to index (for document/batch_documents type)",
                },
                "entity_type": {
                    "type": "string",
                    "enum": ["client", "project", "website", "task", "all"],
                    "description": "Entity type for entity indexing",
                },
                "entity_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Specific entity IDs to index",
                },
                "project_id": {
                    "type": "integer",
                    "description": "Project ID for scoped indexing",
                },
                "client_id": {
                    "type": "integer",
                    "description": "Client ID for scoped indexing",
                },
                "code_aware": {
                    "type": "boolean",
                    "default": True,
                    "description": "Use code-aware chunking for code files",
                },
                "use_celery": {
                    "type": "boolean",
                    "default": True,
                    "description": "Use Celery for async execution",
                },
                "force_reindex": {
                    "type": "boolean",
                    "default": False,
                    "description": "Force reindex even if already indexed",
                },
            },
        }

    def execute(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable[[int, str, Optional[Dict[str, Any]]], None],
    ) -> TaskResult:
        """
        Execute document/entity indexing.
        Supports multiple indexing modes:
        - document: Index specific documents by ID
        - batch_documents: Index multiple documents
        - entity: Index entities (clients, projects, websites, tasks)
        - metadata: Index metadata for entities
        - full_reindex: Full system reindex
        """
        started_at = datetime.now()

        try:
            indexing_type = config.get("indexing_type", "document")

            if indexing_type == "document":
                return self._execute_document_indexing(
                    task, config, progress_callback, started_at
                )
            elif indexing_type == "batch_documents":
                return self._execute_batch_document_indexing(
                    task, config, progress_callback, started_at
                )
            elif indexing_type == "entity":
                return self._execute_entity_indexing(
                    task, config, progress_callback, started_at
                )
            elif indexing_type == "metadata":
                return self._execute_metadata_indexing(
                    task, config, progress_callback, started_at
                )
            elif indexing_type == "full_reindex":
                return self._execute_full_reindex(
                    task, config, progress_callback, started_at
                )
            else:
                return TaskResult(
                    status=TaskResultStatus.FAILED,
                    message=f"Unknown indexing type: {indexing_type}",
                    error_message=f"indexing_type must be one of: document, batch_documents, entity, metadata, full_reindex",
                    started_at=started_at,
                    completed_at=datetime.now(),
                )

        except Exception as e:
            logger.error(f"Document indexing handler error: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Indexing failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
            )

    def _execute_document_indexing(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime,
    ) -> TaskResult:
        """Index specific documents by ID"""
        document_ids = config.get("document_ids", [])
        use_celery = config.get("use_celery", True)

        if not document_ids:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="No document IDs provided",
                error_message="document_ids list is empty",
                started_at=started_at,
                completed_at=datetime.now(),
            )

        progress_callback(
            0,
            f"Starting indexing for {len(document_ids)} documents",
            {"total_documents": len(document_ids)},
        )

        if use_celery and len(document_ids) > 1:
            return self._execute_celery_batch(
                task, document_ids, progress_callback, started_at
            )
        else:
            return self._execute_sync_documents(
                task, document_ids, config, progress_callback, started_at
            )

    def _execute_celery_batch(
        self,
        task: Any,
        document_ids: List[int],
        progress_callback: Callable,
        started_at: datetime,
    ) -> TaskResult:
        """Execute document indexing via Celery"""
        try:
            from backend.celery_tasks_isolated import index_document_task

            job_id = task.job_id or f"idx_batch_{task.id}"
            celery_task_ids = []

            progress_callback(
                5,
                "Submitting documents to indexing queue...",
                {"job_id": job_id, "queue": self.celery_queue},
            )

            # Submit each document for indexing
            for i, doc_id in enumerate(document_ids):
                process_id = f"{job_id}_doc_{doc_id}"
                # Note: index_document_task is a regular function, call directly
                # In production, you'd use Celery's apply_async
                try:
                    from backend.celery_app import celery

                    celery_result = celery.send_task(
                        "backend.celery_tasks_isolated.index_document_task",
                        args=[doc_id, process_id],
                        queue=self.celery_queue,
                    )
                    celery_task_ids.append(celery_result.id)
                except Exception as celery_error:
                    logger.warning(
                        f"Celery submission failed for doc {doc_id}: {celery_error}"
                    )

            progress_callback(
                10,
                f"Submitted {len(document_ids)} documents to queue",
                {"celery_task_count": len(celery_task_ids), "job_id": job_id},
            )

            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message=f"Submitted {len(document_ids)} documents for indexing",
                output_data={
                    "celery_task_ids": celery_task_ids,
                    "job_id": job_id,
                    "document_count": len(document_ids),
                    "async": True,
                },
                items_total=len(document_ids),
                started_at=started_at,
                completed_at=datetime.now(),
            )

        except ImportError as e:
            logger.warning(f"Celery not available, falling back to sync: {e}")
            return self._execute_sync_documents(
                task, document_ids, {}, progress_callback, started_at
            )

    def _execute_sync_documents(
        self,
        task: Any,
        document_ids: List[int],
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime,
    ) -> TaskResult:
        """Execute document indexing synchronously"""
        try:
            from backend.services.indexing_service import add_file_to_index
            from backend.models import Document as DBDocument, db
            from backend.config import UPLOAD_DIR

            indexed_count = 0
            failed_count = 0
            failed_docs = []

            progress_callback(5, "Loading documents from database...", None)

            for i, doc_id in enumerate(document_ids):
                try:
                    # Get document from database
                    document = db.session.get(DBDocument, doc_id)
                    if not document:
                        logger.warning(f"Document {doc_id} not found")
                        failed_count += 1
                        failed_docs.append({"id": doc_id, "error": "Not found"})
                        continue

                    # Construct file path
                    file_path = os.path.join(UPLOAD_DIR, document.file_path)

                    if not os.path.exists(file_path):
                        logger.warning(f"File not found: {file_path}")
                        failed_count += 1
                        failed_docs.append({"id": doc_id, "error": "File not found"})
                        continue

                    # Update progress
                    progress = int(10 + (i / len(document_ids)) * 85)
                    progress_callback(
                        progress,
                        f"Indexing {document.filename} ({i+1}/{len(document_ids)})",
                        {"current_document": document.filename, "document_id": doc_id},
                    )

                    # Index the document
                    def doc_progress(pct, msg):
                        pass  # Nested progress is handled by add_file_to_index

                    success = add_file_to_index(file_path, document, doc_progress)

                    if success:
                        indexed_count += 1
                        # Update document status
                        document.index_status = "INDEXED"
                        document.indexed_at = datetime.now()
                        db.session.commit()
                    else:
                        failed_count += 1
                        failed_docs.append({"id": doc_id, "error": "Indexing failed"})

                except Exception as e:
                    logger.error(f"Error indexing document {doc_id}: {e}")
                    failed_count += 1
                    failed_docs.append({"id": doc_id, "error": str(e)})

            # Complete
            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            progress_callback(
                100,
                f"Indexing complete: {indexed_count} succeeded, {failed_count} failed",
                {"indexed_count": indexed_count, "failed_count": failed_count},
            )

            if failed_count > 0 and indexed_count > 0:
                return TaskResult(
                    status=TaskResultStatus.PARTIAL,
                    message=f"Indexed {indexed_count}/{len(document_ids)} documents ({failed_count} failed)",
                    output_data={
                        "indexed_count": indexed_count,
                        "failed_count": failed_count,
                        "failed_documents": failed_docs,
                    },
                    items_processed=indexed_count,
                    items_total=len(document_ids),
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration,
                )
            elif indexed_count == 0:
                return TaskResult(
                    status=TaskResultStatus.FAILED,
                    message=f"All {failed_count} documents failed to index",
                    error_message="No documents were successfully indexed",
                    output_data={"failed_documents": failed_docs},
                    items_processed=0,
                    items_total=len(document_ids),
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration,
                )
            else:
                return TaskResult(
                    status=TaskResultStatus.SUCCESS,
                    message=f"Successfully indexed {indexed_count} documents",
                    output_data={"indexed_count": indexed_count},
                    items_processed=indexed_count,
                    items_total=len(document_ids),
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration,
                )

        except Exception as e:
            logger.error(f"Sync document indexing failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Document indexing failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
            )

    def _execute_batch_document_indexing(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime,
    ) -> TaskResult:
        """Index documents in batch (by project or client scope)"""
        try:
            from backend.models import Document as DBDocument, db

            project_id = config.get("project_id")
            client_id = config.get("client_id")
            force_reindex = config.get("force_reindex", False)

            progress_callback(0, "Querying documents to index...", None)

            # Build query
            query = db.session.query(DBDocument)

            if project_id:
                query = query.filter(DBDocument.project_id == project_id)
            if client_id:
                query = query.filter(DBDocument.client_id == client_id)

            if not force_reindex:
                # Only index documents not already indexed
                query = query.filter(
                    (DBDocument.index_status != "INDEXED")
                    | (DBDocument.index_status.is_(None))
                )

            documents = query.all()
            document_ids = [doc.id for doc in documents]

            if not document_ids:
                return TaskResult(
                    status=TaskResultStatus.SUCCESS,
                    message="No documents to index (all up to date)",
                    items_processed=0,
                    items_total=0,
                    started_at=started_at,
                    completed_at=datetime.now(),
                )

            progress_callback(
                5,
                f"Found {len(document_ids)} documents to index",
                {"total_documents": len(document_ids)},
            )

            # Delegate to sync indexing
            return self._execute_sync_documents(
                task, document_ids, config, progress_callback, started_at
            )

        except Exception as e:
            logger.error(f"Batch document indexing failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Batch indexing failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
            )

    def _execute_entity_indexing(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime,
    ) -> TaskResult:
        """Index entities (clients, projects, websites, tasks)"""
        try:
            from backend.services.entity_indexing_service import (
                get_entity_indexing_service,
            )

            entity_service = get_entity_indexing_service()
            entity_type = config.get("entity_type", "all")
            entity_ids = config.get("entity_ids", [])

            progress_callback(
                0,
                f"Starting entity indexing: {entity_type}",
                {"entity_type": entity_type, "entity_ids": entity_ids},
            )

            if entity_type == "all":
                # Full entity indexing
                progress_callback(10, "Indexing all entities...", None)
                results = entity_service.index_all_entities()

                completed_at = datetime.now()
                duration = (completed_at - started_at).total_seconds()

                total_indexed = (
                    results["clients"]
                    + results["projects"]
                    + results["websites"]
                    + results["tasks"]
                )
                errors = results.get("errors", 0)

                progress_callback(
                    100, f"Entity indexing complete: {total_indexed} indexed", results
                )

                if errors > 0:
                    return TaskResult(
                        status=TaskResultStatus.PARTIAL,
                        message=f"Indexed {total_indexed} entities with {errors} errors",
                        output_data=results,
                        items_processed=total_indexed,
                        items_total=total_indexed + errors,
                        started_at=started_at,
                        completed_at=completed_at,
                        duration_seconds=duration,
                    )

                return TaskResult(
                    status=TaskResultStatus.SUCCESS,
                    message=f"Successfully indexed {total_indexed} entities",
                    output_data=results,
                    items_processed=total_indexed,
                    items_total=total_indexed,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration,
                )
            else:
                # Index specific entity type
                indexed_count = 0
                failed_count = 0

                if entity_ids:
                    # Index specific entities
                    for i, entity_id in enumerate(entity_ids):
                        progress = int(10 + (i / len(entity_ids)) * 85)
                        progress_callback(
                            progress, f"Indexing {entity_type} {entity_id}", None
                        )

                        success = entity_service.update_entity_index(
                            entity_type, entity_id
                        )
                        if success:
                            indexed_count += 1
                        else:
                            failed_count += 1
                else:
                    # Index all of the entity type
                    return self._index_all_of_type(
                        entity_service, entity_type, progress_callback, started_at
                    )

                completed_at = datetime.now()
                duration = (completed_at - started_at).total_seconds()

                progress_callback(
                    100, f"Entity indexing complete: {indexed_count} indexed", None
                )

                status = TaskResultStatus.SUCCESS
                if failed_count > 0:
                    status = (
                        TaskResultStatus.PARTIAL
                        if indexed_count > 0
                        else TaskResultStatus.FAILED
                    )

                return TaskResult(
                    status=status,
                    message=f"Indexed {indexed_count}/{len(entity_ids)} {entity_type}(s)",
                    output_data={
                        "indexed_count": indexed_count,
                        "failed_count": failed_count,
                        "entity_type": entity_type,
                    },
                    items_processed=indexed_count,
                    items_total=len(entity_ids),
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration,
                )

        except Exception as e:
            logger.error(f"Entity indexing failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Entity indexing failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
            )

    def _index_all_of_type(
        self,
        entity_service,
        entity_type: str,
        progress_callback: Callable,
        started_at: datetime,
    ) -> TaskResult:
        """Index all entities of a specific type"""
        from backend.models import Client, Project, Website, Task, db

        model_map = {
            "client": Client,
            "project": Project,
            "website": Website,
            "task": Task,
        }

        index_method_map = {
            "client": entity_service.index_client,
            "project": entity_service.index_project,
            "website": entity_service.index_website,
            "task": entity_service.index_task,
        }

        model = model_map.get(entity_type)
        index_method = index_method_map.get(entity_type)

        if not model or not index_method:
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Unknown entity type: {entity_type}",
                started_at=started_at,
                completed_at=datetime.now(),
            )

        entities = db.session.query(model).all()
        indexed_count = 0
        failed_count = 0

        for i, entity in enumerate(entities):
            progress = int(10 + (i / len(entities)) * 85)
            name = getattr(entity, "name", None) or getattr(
                entity, "url", str(entity.id)
            )
            progress_callback(progress, f"Indexing {entity_type}: {name}", None)

            if index_method(entity):
                indexed_count += 1
            else:
                failed_count += 1

        completed_at = datetime.now()
        duration = (completed_at - started_at).total_seconds()

        progress_callback(
            100,
            f"Entity indexing complete",
            {"indexed_count": indexed_count, "failed_count": failed_count},
        )

        status = TaskResultStatus.SUCCESS
        if failed_count > 0:
            status = (
                TaskResultStatus.PARTIAL
                if indexed_count > 0
                else TaskResultStatus.FAILED
            )

        return TaskResult(
            status=status,
            message=f"Indexed {indexed_count}/{len(entities)} {entity_type}(s)",
            output_data={
                "indexed_count": indexed_count,
                "failed_count": failed_count,
                "entity_type": entity_type,
            },
            items_processed=indexed_count,
            items_total=len(entities),
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration,
        )

    def _execute_metadata_indexing(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime,
    ) -> TaskResult:
        """Index metadata for entities"""
        try:
            from backend.services.metadata_indexing_service import (
                MetadataIndexingService,
            )

            metadata_service = MetadataIndexingService()
            entity_type = config.get("entity_type", "all")
            entity_ids = config.get("entity_ids", [])

            progress_callback(0, f"Starting metadata indexing: {entity_type}", None)

            indexed_count = 0
            failed_count = 0

            if entity_type == "client" or entity_type == "all":
                if entity_ids and entity_type == "client":
                    for client_id in entity_ids:
                        if metadata_service.index_client_metadata(client_id):
                            indexed_count += 1
                        else:
                            failed_count += 1
                elif entity_type == "all":
                    from backend.models import Client, db

                    clients = db.session.query(Client).all()
                    for client in clients:
                        if metadata_service.index_client_metadata(client.id):
                            indexed_count += 1
                        else:
                            failed_count += 1

            if entity_type == "project" or entity_type == "all":
                if entity_ids and entity_type == "project":
                    for project_id in entity_ids:
                        if metadata_service.index_project_metadata(project_id):
                            indexed_count += 1
                        else:
                            failed_count += 1
                elif entity_type == "all":
                    from backend.models import Project, db

                    projects = db.session.query(Project).all()
                    for project in projects:
                        if metadata_service.index_project_metadata(project.id):
                            indexed_count += 1
                        else:
                            failed_count += 1

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            progress_callback(
                100,
                f"Metadata indexing complete",
                {"indexed_count": indexed_count, "failed_count": failed_count},
            )

            status = TaskResultStatus.SUCCESS
            if failed_count > 0:
                status = (
                    TaskResultStatus.PARTIAL
                    if indexed_count > 0
                    else TaskResultStatus.FAILED
                )

            return TaskResult(
                status=status,
                message=f"Indexed metadata for {indexed_count} entities",
                output_data={
                    "indexed_count": indexed_count,
                    "failed_count": failed_count,
                },
                items_processed=indexed_count,
                items_total=indexed_count + failed_count,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
            )

        except Exception as e:
            logger.error(f"Metadata indexing failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Metadata indexing failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
            )

    def _execute_full_reindex(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime,
    ) -> TaskResult:
        """Execute full system reindex"""
        try:
            from backend.models import Document as DBDocument, db

            progress_callback(0, "Starting full system reindex...", None)

            results = {
                "documents_indexed": 0,
                "documents_failed": 0,
                "entities_indexed": 0,
                "entities_failed": 0,
            }

            # Phase 1: Index all documents
            progress_callback(5, "Phase 1: Indexing all documents...", None)

            all_documents = db.session.query(DBDocument).all()
            document_ids = [doc.id for doc in all_documents]

            if document_ids:
                doc_result = self._execute_sync_documents(
                    task,
                    document_ids,
                    {"force_reindex": True},
                    lambda p, m, d=None: progress_callback(
                        5 + int(p * 0.5), f"Documents: {m}", d
                    ),
                    started_at,
                )
                results["documents_indexed"] = doc_result.items_processed or 0
                results["documents_failed"] = (doc_result.items_total or 0) - results[
                    "documents_indexed"
                ]

            # Phase 2: Index all entities
            progress_callback(55, "Phase 2: Indexing all entities...", None)

            entity_result = self._execute_entity_indexing(
                task,
                {"entity_type": "all"},
                lambda p, m, d=None: progress_callback(
                    55 + int(p * 0.4), f"Entities: {m}", d
                ),
                started_at,
            )

            if entity_result.output_data:
                results["entities_indexed"] = sum(
                    [
                        entity_result.output_data.get("clients", 0),
                        entity_result.output_data.get("projects", 0),
                        entity_result.output_data.get("websites", 0),
                        entity_result.output_data.get("tasks", 0),
                    ]
                )
                results["entities_failed"] = entity_result.output_data.get("errors", 0)

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            total_indexed = results["documents_indexed"] + results["entities_indexed"]
            total_failed = results["documents_failed"] + results["entities_failed"]

            progress_callback(
                100, f"Full reindex complete: {total_indexed} items indexed", results
            )

            status = TaskResultStatus.SUCCESS
            if total_failed > 0:
                status = (
                    TaskResultStatus.PARTIAL
                    if total_indexed > 0
                    else TaskResultStatus.FAILED
                )

            return TaskResult(
                status=status,
                message=f"Full reindex: {total_indexed} items indexed, {total_failed} failed",
                output_data=results,
                items_processed=total_indexed,
                items_total=total_indexed + total_failed,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
            )

        except Exception as e:
            logger.error(f"Full reindex failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Full reindex failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
            )

    def get_estimated_duration(self, config: Dict[str, Any]) -> Optional[int]:
        """Estimate based on document count and indexing type"""
        indexing_type = config.get("indexing_type", "document")
        doc_ids = config.get("document_ids", [])

        if indexing_type == "full_reindex":
            # Full reindex takes much longer
            return 600  # 10 minutes estimate

        if doc_ids:
            # Rough estimate: 10 seconds per document
            return max(30, len(doc_ids) * 10)

        if indexing_type == "entity":
            return 120  # 2 minutes for entity indexing

        return 60  # Default estimate

    def can_retry(self, task: Any, error: Exception) -> bool:
        """Check if task can be retried"""
        error_msg = str(error).lower()
        # Don't retry on database or index corruption errors
        if "corruption" in error_msg or "invalid" in error_msg:
            return False
        return super().can_retry(task, error)
