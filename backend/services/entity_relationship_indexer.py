import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict

from backend.utils.db_utils import DatabaseConnectionManager
from backend.models import db, Task, Document, Client, Project
from backend.utils.unified_job_metadata import unified_job_metadata
from backend.services.metadata_indexing_service import metadata_indexing_service

logger = logging.getLogger(__name__)


class EntityRelationshipIndexer:

    def __init__(self):
        self.indexing_service = None
        self._initialize_indexing()

    def _initialize_indexing(self):
        try:
            from backend.services.indexing_service import add_text_to_index

            self.add_text_to_index = add_text_to_index
        except ImportError as e:
            logger.warning(f"Could not import indexing service: {e}")
            self.add_text_to_index = None

    def create_system_overview_document(self) -> str:
        try:
            with DatabaseConnectionManager():
                try:
                    clients = db.session.query(Client).all()
                    projects = db.session.query(Project).all()
                    documents = db.session.query(Document).all()
                    tasks = db.session.query(Task).all()
                except Exception as query_error:
                    logger.error(f"Error querying entities: {query_error}")
                    return f"Error querying entities: {str(query_error)}"

                try:
                    active_summary = unified_job_metadata.get_active_jobs_summary()
                except Exception as jobs_error:
                    logger.warning(f"Error getting active jobs summary: {jobs_error}")
                    active_summary = {}

                doc_lines = [
                    "GUAARDVARK SYSTEM OVERVIEW AND ENTITY RELATIONSHIPS",
                    "=" * 60,
                    f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    f"",
                    f"SYSTEM STATISTICS:",
                    f"- Total Clients: {len(clients)}",
                    f"- Total Projects: {len(projects)}",
                    f"- Total Documents: {len(documents)}",
                    f"- Total Tasks/Jobs: {len(tasks)}",
                    f"- Active Jobs: {active_summary.get('database_active_jobs', 0)}",
                    f"- Progress Processes: {active_summary.get('progress_active_jobs', 0)}",
                    f"",
                ]

                try:
                    doc_lines.extend(self._create_client_project_map(clients, projects))

                    doc_lines.extend(self._create_document_status_overview(documents))

                    doc_lines.extend(self._create_job_status_overview(tasks))

                    doc_lines.extend(
                        self._create_relationship_map(
                            clients, projects, documents, tasks
                        )
                    )

                    doc_lines.extend(self._create_system_capabilities())

                    return "\n".join(doc_lines)
                except Exception as build_error:
                    logger.error(
                        f"Error building system overview sections: {build_error}"
                    )
                    return f"Error building system overview: {str(build_error)}"

        except Exception as e:
            logger.error(f"Error creating system overview document: {e}")
            return f"Error creating system overview: {str(e)}"

    def _create_client_project_map(self, clients: List, projects: List) -> List[str]:
        lines = [
            "CLIENT-PROJECT RELATIONSHIPS:",
            "-" * 40,
        ]

        project_by_client = defaultdict(list)
        for project in projects:
            if project.client_id:
                project_by_client[project.client_id].append(project)

        for client in clients:
            client_projects = project_by_client.get(client.id, [])
            lines.extend(
                [
                    f"",
                    f"Client: {client.name} (ID: {client.id})",
                    f"  Description: {client.description or 'No description'}",
                    f"  Projects: {len(client_projects)}",
                ]
            )

            for project in client_projects:
                lines.append(f"    - {project.name} (ID: {project.id})")
                if project.description:
                    lines.append(f"      Description: {project.description}")

        lines.append("")
        return lines

    def _create_document_status_overview(self, documents: List) -> List[str]:
        lines = [
            "DOCUMENT STATUS OVERVIEW:",
            "-" * 40,
        ]

        status_counts = defaultdict(int)
        type_counts = defaultdict(int)
        project_doc_counts = defaultdict(int)

        for doc in documents:
            status_counts[doc.index_status or "UNKNOWN"] += 1
            type_counts[doc.type or "unknown"] += 1
            if doc.project_id:
                project_doc_counts[doc.project_id] += 1

        lines.extend(
            [
                "",
                "By Status:",
            ]
        )
        for status, count in status_counts.items():
            lines.append(f"  {status}: {count} documents")

        lines.extend(
            [
                "",
                "By Type:",
            ]
        )
        for doc_type, count in type_counts.items():
            lines.append(f"  {doc_type}: {count} documents")

        lines.extend(
            [
                "",
                "By Project:",
            ]
        )
        for project_id, count in project_doc_counts.items():
            lines.append(f"  Project {project_id}: {count} documents")

        lines.append("")
        return lines

    def _create_job_status_overview(self, tasks: List) -> List[str]:
        lines = [
            "JOB/TASK STATUS OVERVIEW:",
            "-" * 40,
        ]

        status_counts = defaultdict(int)
        type_counts = defaultdict(int)
        project_job_counts = defaultdict(int)
        recent_jobs = []

        for task in tasks:
            status_counts[task.status or "unknown"] += 1
            type_counts[task.type or "unknown"] += 1
            if task.project_id:
                project_job_counts[task.project_id] += 1

            if task.created_at and task.created_at > datetime.now() - timedelta(days=7):
                recent_jobs.append(task)

        lines.extend(
            [
                "",
                "By Status:",
            ]
        )
        for status, count in status_counts.items():
            lines.append(f"  {status}: {count} jobs")

        lines.extend(
            [
                "",
                "By Type:",
            ]
        )
        for job_type, count in type_counts.items():
            lines.append(f"  {job_type}: {count} jobs")

        lines.extend(
            [
                "",
                f"Recent Jobs (Last 7 Days): {len(recent_jobs)}",
            ]
        )
        for task in recent_jobs[-10:]:
            lines.append(f"  - {task.name} ({task.type}) - {task.status}")

        lines.append("")
        return lines

    def _create_relationship_map(
        self, clients: List, projects: List, documents: List, tasks: List
    ) -> List[str]:
        lines = [
            "CROSS-ENTITY RELATIONSHIPS:",
            "-" * 40,
        ]

        projects_by_client = defaultdict(list)
        docs_by_project = defaultdict(list)
        tasks_by_project = defaultdict(list)

        for project in projects:
            if project.client_id:
                projects_by_client[project.client_id].append(project)

        for doc in documents:
            if doc.project_id:
                docs_by_project[doc.project_id].append(doc)

        for task in tasks:
            if task.project_id:
                tasks_by_project[task.project_id].append(task)

        for client in clients:
            client_projects = projects_by_client.get(client.id, [])
            if not client_projects:
                continue

            total_docs = sum(
                len(docs_by_project.get(p.id, [])) for p in client_projects
            )
            total_tasks = sum(
                len(tasks_by_project.get(p.id, [])) for p in client_projects
            )

            lines.extend(
                [
                    "",
                    f"Client: {client.name}",
                    f"  Projects: {len(client_projects)}",
                    f"  Total Documents: {total_docs}",
                    f"  Total Jobs: {total_tasks}",
                ]
            )

            for project in client_projects:
                project_docs = docs_by_project.get(project.id, [])
                project_tasks = tasks_by_project.get(project.id, [])

                doc_statuses = defaultdict(int)
                for doc in project_docs:
                    doc_statuses[doc.index_status or "UNKNOWN"] += 1

                task_statuses = defaultdict(int)
                for task in project_tasks:
                    task_statuses[task.status or "unknown"] += 1

                lines.extend(
                    [
                        f"    Project: {project.name}",
                        f"      Documents: {len(project_docs)} ({dict(doc_statuses)})",
                        f"      Jobs: {len(project_tasks)} ({dict(task_statuses)})",
                    ]
                )

        lines.append("")
        return lines

    def _create_system_capabilities(self) -> List[str]:
        lines = [
            "SYSTEM CAPABILITIES AND CONTEXT:",
            "-" * 40,
            "",
            "Available Operations:",
            "- Document upload and indexing",
            "- Bulk content generation",
            "- Website analysis",
            "- Sequential task processing",
            "- Client and project management",
            "- Progress tracking and monitoring",
            "- Voice processing (STT/TTS)",
            "- Real-time chat with RAG",
            "",
            "Data Storage:",
            "- SQLite database for structured data",
            "- Vector index for document content",
            "- Redis for caching and job queues",
            "- File system for document storage",
            "- Progress tracking in memory and files",
            "",
            "Integration Points:",
            "- Celery for background processing",
            "- Socket.IO for real-time updates",
            "- LlamaIndex for RAG operations",
            "- Ollama for local LLM inference",
            "- Whisper.cpp for speech recognition",
            "- Piper TTS for text-to-speech",
            "",
            "Query Context Instructions:",
            "When answering questions about clients, projects, documents, or jobs:",
            "1. Reference specific IDs and names from the data above",
            "2. Provide current status information",
            "3. Include relevant relationships and context",
            "4. Suggest specific actions based on current system state",
            "5. Use actual data rather than making assumptions",
            "",
            "Example Queries This Context Supports:",
            "- What jobs are running for Client X?",
            "- How many documents has Project Y indexed?",
            "- What's the status of bulk generation task Z?",
            "- Which clients have the most active projects?",
            "- What files are pending indexing?",
            "- Show me recent job failures and their causes",
            "",
            f"Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "This context is automatically updated when entities change.",
        ]

        return lines

    def index_system_overview(self) -> bool:
        try:
            if not self.add_text_to_index:
                logger.warning("Indexing service not available")
                return False

            overview_text = self.create_system_overview_document()

            overview_doc = type(
                "Document",
                (),
                {
                    "id": "system_overview",
                    "filename": "Guaardvark_System_Overview.txt",
                    "type": "system_metadata",
                    "path": "metadata/system_overview.txt",
                },
            )()

            success = self.add_text_to_index(
                overview_text, overview_doc, progress_callback=None
            )

            if success:
                logger.info("Successfully indexed system overview for RAG context")
                return True
            else:
                logger.error("Failed to index system overview")
                return False

        except Exception as e:
            logger.error(f"Error indexing system overview: {e}")
            return False

    def create_client_relationship_documents(self) -> Dict[str, bool]:
        results = {}

        try:
            with DatabaseConnectionManager():
                clients = db.session.query(Client).all()

                for client in clients:
                    try:
                        doc_text = self._create_detailed_client_relationships(client)

                        if self.add_text_to_index and doc_text:
                            relationship_doc = type(
                                "Document",
                                (),
                                {
                                    "id": f"client_{client.id}_relationships",
                                    "filename": f"Client_{client.name}_Relationships.txt",
                                    "type": "relationship_metadata",
                                    "path": f"metadata/client_{client.id}_relationships.txt",
                                },
                            )()

                            success = self.add_text_to_index(
                                doc_text, relationship_doc, progress_callback=None
                            )

                            results[f"client_{client.id}"] = success
                            if success:
                                logger.info(
                                    f"Indexed relationship document for client {client.name}"
                                )
                            else:
                                logger.error(
                                    f"Failed to index relationships for client {client.name}"
                                )
                        else:
                            results[f"client_{client.id}"] = False

                    except Exception as e:
                        logger.error(
                            f"Error creating relationship doc for client {client.id}: {e}"
                        )
                        results[f"client_{client.id}"] = False

        except Exception as e:
            logger.error(f"Error creating client relationship documents: {e}")

        return results

    def _create_detailed_client_relationships(self, client) -> str:
        try:
            with DatabaseConnectionManager():
                projects = (
                    db.session.query(Project).filter_by(client_id=client.id).all()
                )

                all_docs = []
                all_tasks = []

                for project in projects:
                    project_docs = (
                        db.session.query(Document)
                        .filter_by(project_id=project.id)
                        .all()
                    )
                    project_tasks = (
                        db.session.query(Task).filter_by(project_id=project.id).all()
                    )
                    all_docs.extend(project_docs)
                    all_tasks.extend(project_tasks)

                lines = [
                    f"DETAILED RELATIONSHIPS FOR CLIENT: {client.name}",
                    "=" * 60,
                    f"Client ID: {client.id}",
                    f"Description: {client.description or 'No description'}",
                    f"Created: {client.created_at.strftime('%Y-%m-%d') if client.created_at else 'Unknown'}",
                    f"",
                    f"SUMMARY STATISTICS:",
                    f"- Projects: {len(projects)}",
                    f"- Documents: {len(all_docs)}",
                    f"- Jobs/Tasks: {len(all_tasks)}",
                    f"",
                ]

                for project in projects:
                    project_docs = [d for d in all_docs if d.project_id == project.id]
                    project_tasks = [t for t in all_tasks if t.project_id == project.id]

                    lines.extend(
                        [
                            f"PROJECT: {project.name} (ID: {project.id})",
                            f"  Description: {project.description or 'No description'}",
                            f"  Created: {project.created_at.strftime('%Y-%m-%d') if project.created_at else 'Unknown'}",
                            f"  Documents: {len(project_docs)}",
                            f"  Jobs: {len(project_tasks)}",
                            f"",
                        ]
                    )

                    if project_docs:
                        lines.append("  Documents:")
                        for doc in project_docs:
                            lines.extend(
                                [
                                    f"    - {doc.filename} (ID: {doc.id})",
                                    f"      Type: {doc.type or 'Unknown'}",
                                    f"      Status: {doc.index_status or 'Unknown'}",
                                    f"      Uploaded: {doc.uploaded_at.strftime('%Y-%m-%d') if doc.uploaded_at else 'Unknown'}",
                                ]
                            )
                        lines.append("")

                    if project_tasks:
                        lines.append("  Jobs/Tasks:")
                        for task in project_tasks:
                            lines.extend(
                                [
                                    f"    - {task.name} (ID: {task.id})",
                                    f"      Type: {task.type or 'Unknown'}",
                                    f"      Status: {task.status or 'Unknown'}",
                                    f"      Created: {task.created_at.strftime('%Y-%m-%d') if task.created_at else 'Unknown'}",
                                    f"      Description: {task.description or 'No description'}",
                                ]
                            )
                        lines.append("")

                lines.extend(
                    [
                        "SEARCHABLE CONTEXT FOR RAG QUERIES:",
                        f"This document contains complete relationship information for {client.name}.",
                        f"Use this to answer questions about {client.name}'s projects, documents, and jobs.",
                        f"The client has {len(projects)} projects, {len(all_docs)} documents, and {len(all_tasks)} jobs.",
                        f"Reference specific project names and IDs when answering questions.",
                    ]
                )

                return "\n".join(lines)

        except Exception as e:
            logger.error(
                f"Error creating detailed relationships for client {client.id}: {e}"
            )
            return f"Error creating relationships: {str(e)}"

    def reindex_all_relationships(self) -> Dict[str, Any]:
        results = {
            "system_overview": False,
            "client_relationships": {},
            "individual_metadata": {"clients": 0, "projects": 0, "jobs": 0},
            "errors": [],
        }

        try:
            results["system_overview"] = self.index_system_overview()

            results["client_relationships"] = (
                self.create_client_relationship_documents()
            )

            individual_results = metadata_indexing_service.reindex_all_metadata()
            results["individual_metadata"] = {
                "clients": individual_results.get("clients_indexed", 0),
                "projects": individual_results.get("projects_indexed", 0),
                "jobs": individual_results.get("jobs_indexed", 0),
            }

            logger.info(f"Complete relationship reindexing completed: {results}")

        except Exception as e:
            logger.error(f"Error during complete relationship reindexing: {e}")
            results["errors"].append(str(e))

        return results


entity_relationship_indexer = EntityRelationshipIndexer()
