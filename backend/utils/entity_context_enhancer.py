# backend/utils/entity_context_enhancer.py
# Entity Context Enhancer - Maps entity relationships for better LLM context
# Version 1.1: SECURITY FIXES - Added input sanitization, null checks, and query limits

import logging
import re
from typing import Dict, List, Set, Optional, Any, Tuple
from flask import current_app
from backend.models import Client, Project, Website, Task, Document, db
from backend.utils.db_utils import DatabaseConnectionManager

logger = logging.getLogger(__name__)

# Security constants
MAX_ENTITY_NAME_LENGTH = 255
MAX_ENTITIES_PER_TYPE = 50
MAX_QUERY_RESULTS = 100


class EntityContextEnhancer:
    """Enhances LLM context by analyzing entity relationships and providing comprehensive context"""

    def __init__(self):
        self.entity_cache = {}
        self.relationship_cache = {}

    def enhance_query_context(
        self, query: str, retrieved_nodes: List[Any]
    ) -> Dict[str, Any]:
        """Enhance query context with entity relationships"""
        try:
            # Extract entity mentions from query with security validation
            entity_mentions = self._extract_entity_mentions(query)

            # Get entity relationships with proper error handling
            entity_relationships = self._get_entity_relationships(entity_mentions)

            # Enhance retrieved nodes with relationship context
            enhanced_context = self._build_enhanced_context(
                retrieved_nodes, entity_relationships
            )

            return {
                "enhanced_context": enhanced_context,
                "entity_mentions": entity_mentions,
                "entity_relationships": entity_relationships,
                "relationship_summary": self._build_relationship_summary(
                    entity_relationships
                ),
            }

        except Exception as e:
            logger.error(f"Error enhancing query context: {e}", exc_info=True)
            return {
                "enhanced_context": "",
                "entity_mentions": {},
                "entity_relationships": {},
                "relationship_summary": "",
            }

    def _sanitize_entity_mention(self, mention: str) -> Optional[str]:
        """Sanitize and validate entity mention for database queries"""
        if not mention or not isinstance(mention, str):
            return None

        # Strip and normalize
        sanitized = mention.strip()

        # Length validation
        if len(sanitized) > MAX_ENTITY_NAME_LENGTH:
            logger.warning(
                f"Entity mention too long, truncating: {len(sanitized)} chars"
            )
            sanitized = sanitized[:MAX_ENTITY_NAME_LENGTH]

        # Minimum length check
        if len(sanitized) < 2:
            return None

        # Basic content validation - allow alphanumeric, spaces, hyphens, dots, underscores
        if not re.match(r"^[a-zA-Z0-9\s\-\._]+$", sanitized):
            logger.warning(f"Entity mention contains invalid characters: {sanitized}")
            return None

        return sanitized

    def _extract_entity_mentions(self, query: str) -> Dict[str, List[str]]:
        """Extract entity mentions from query text with security validation"""
        entity_mentions = {
            "clients": [],
            "projects": [],
            "websites": [],
            "tasks": [],
            "documents": [],
        }

        if not query or len(query) > 10000:  # Prevent extremely long queries
            return entity_mentions

        # Patterns for entity extraction
        patterns = {
            "clients": [
                r"client\s+([\"']?[\w\s\.-]+[\"']?)(?:\s|$|[,.!?])",
                r"([\"']?[\w\s\.-]+[\"']?)\s+client(?:\s|$|[,.!?])",
                r"for\s+([\"']?[\w\s\.-]+[\"']?)(?:\s|$|[,.!?])",
            ],
            "projects": [
                r"project\s+([\"']?[\w\s\.-]+[\"']?)(?:\s|$|[,.!?])",
                r"([\"']?[\w\s\.-]+[\"']?)\s+project(?:\s|$|[,.!?])",
            ],
            "websites": [
                r"website\s+([\"']?[\w\s\.-]+[\"']?)(?:\s|$|[,.!?])",
                r"site\s+([\"']?[\w\s\.-]+[\"']?)(?:\s|$|[,.!?])",
                r"(https?://[\w\.-]+)(?:\s|$|[,.!?])",
            ],
            "tasks": [
                r"task\s+([\"']?[\w\s\.-]+[\"']?)(?:\s|$|[,.!?])",
                r"([\"']?[\w\s\.-]+[\"']?)\s+task(?:\s|$|[,.!?])",
            ],
            "documents": [
                r"document\s+([\"']?[\w\s\.-]+[\"']?)(?:\s|$|[,.!?])",
                r"file\s+([\"']?[\w\s\.-]+[\"']?)(?:\s|$|[,.!?])",
                r"([\"']?[\w\.-]+\.(?:pdf|csv|txt|xml|doc|docx)[\"']?)(?:\s|$|[,.!?])",
            ],
        }

        for entity_type, type_patterns in patterns.items():
            for pattern in type_patterns:
                try:
                    matches = re.findall(pattern, query, re.IGNORECASE)
                    for match in matches:
                        # Clean up match and sanitize
                        cleaned_match = match.strip("'\"").strip()
                        sanitized_match = self._sanitize_entity_mention(cleaned_match)

                        if sanitized_match:
                            entity_mentions[entity_type].append(sanitized_match)

                        # Limit entities per type to prevent DoS
                        if len(entity_mentions[entity_type]) >= MAX_ENTITIES_PER_TYPE:
                            logger.warning(
                                f"Too many {entity_type} entities extracted, limiting to {MAX_ENTITIES_PER_TYPE}"
                            )
                            break
                except re.error as e:
                    logger.error(f"Regex error in entity extraction: {e}")
                    continue

        # Remove duplicates
        for entity_type in entity_mentions:
            entity_mentions[entity_type] = list(set(entity_mentions[entity_type]))

        return entity_mentions

    def _get_entity_relationships(
        self, entity_mentions: Dict[str, List[str]]
    ) -> Dict[str, Any]:
        """Get entity relationships from database with comprehensive error handling and security"""
        relationships = {
            "clients": [],
            "projects": [],
            "websites": [],
            "tasks": [],
            "documents": [],
        }

        try:
            # Use database connection manager for proper session handling
            with DatabaseConnectionManager():
                # ENHANCED ERROR HANDLING: Test database connection
                try:
                    db.session.execute(db.text("SELECT 1"))
                except Exception as conn_error:
                    logger.error(
                        f"Database connection test failed in _get_entity_relationships: {conn_error}"
                    )
                    return relationships

                # ENHANCED ERROR HANDLING: Validate entity_mentions parameter
                if not entity_mentions or not isinstance(entity_mentions, dict):
                    logger.warning(
                        "Invalid entity_mentions parameter in _get_entity_relationships"
                    )
                    return relationships

                # Get client relationships with proper validation
                if entity_mentions.get("clients"):
                    try:
                        # Limit the number of mentions to query
                        client_mentions = entity_mentions["clients"][
                            :MAX_ENTITIES_PER_TYPE
                        ]

                        # ENHANCED ERROR HANDLING: Validate client mentions before querying
                        if not client_mentions or not all(
                            isinstance(mention, str) for mention in client_mentions
                        ):
                            logger.warning(
                                "Invalid client mentions in _get_entity_relationships"
                            )
                        else:
                            clients = (
                                db.session.query(Client)
                                .filter(Client.name.in_(client_mentions))
                                .limit(MAX_QUERY_RESULTS)
                                .all()
                            )

                            # ENHANCED ERROR HANDLING: Validate query results
                            if clients is not None:
                                for client in clients:
                                    if not client:  # Skip None clients
                                        continue

                                    try:
                                        # ENHANCED ERROR HANDLING: Validate client object before accessing attributes
                                        if (
                                            not hasattr(client, "id")
                                            or client.id is None
                                        ):
                                            logger.warning(
                                                f"Client object missing id attribute: {client}"
                                            )
                                            continue

                                        client_data = {
                                            "id": client.id,
                                            "name": getattr(client, "name", None),
                                            "email": getattr(client, "email", None),
                                            "phone": getattr(client, "phone", None),
                                            "projects": [],
                                            "websites": [],
                                            "total_documents": 0,
                                        }

                                        # ENHANCED ERROR HANDLING: Proper null checks and relationship handling
                                        if hasattr(client, "projects"):
                                            try:
                                                # Check if projects relationship is loaded
                                                if client.projects is not None:
                                                    client_data["projects"] = [
                                                        {
                                                            "id": p.id,
                                                            "name": getattr(
                                                                p, "name", None
                                                            ),
                                                        }
                                                        for p in client.projects
                                                        if p
                                                        and hasattr(p, "id")
                                                        and p.id is not None
                                                        and hasattr(p, "name")
                                                    ]

                                                    # ENHANCED ERROR HANDLING: Calculate total documents safely
                                                    try:
                                                        total_docs = 0
                                                        for project in client.projects:
                                                            if project and hasattr(
                                                                project, "documents"
                                                            ):
                                                                try:
                                                                    if (
                                                                        project.documents
                                                                        is not None
                                                                    ):
                                                                        total_docs += len(
                                                                            project.documents
                                                                        )
                                                                except (
                                                                    Exception
                                                                ) as doc_count_error:
                                                                    logger.debug(
                                                                        f"Error counting documents for project {project.id}: {doc_count_error}"
                                                                    )
                                                                    continue
                                                        client_data[
                                                            "total_documents"
                                                        ] = total_docs
                                                    except Exception as e:
                                                        logger.warning(
                                                            f"Error calculating documents for client {client.id}: {e}"
                                                        )
                                                        client_data[
                                                            "total_documents"
                                                        ] = 0
                                            except Exception as projects_error:
                                                logger.warning(
                                                    f"Error accessing projects for client {client.id}: {projects_error}"
                                                )

                                        if hasattr(client, "websites"):
                                            try:
                                                if client.websites is not None:
                                                    client_data["websites"] = [
                                                        {
                                                            "id": w.id,
                                                            "url": getattr(
                                                                w, "url", None
                                                            ),
                                                        }
                                                        for w in client.websites
                                                        if w
                                                        and hasattr(w, "id")
                                                        and w.id is not None
                                                        and hasattr(w, "url")
                                                    ]
                                            except Exception as websites_error:
                                                logger.warning(
                                                    f"Error accessing websites for client {client.id}: {websites_error}"
                                                )

                                        relationships["clients"].append(client_data)
                                    except Exception as e:
                                        logger.error(
                                            f"Error processing client {getattr(client, 'id', 'unknown')}: {e}"
                                        )
                                        continue
                    except Exception as client_query_error:
                        logger.error(f"Error querying clients: {client_query_error}")

                # Get project relationships with proper validation
                if entity_mentions.get("projects"):
                    try:
                        project_mentions = entity_mentions["projects"][
                            :MAX_ENTITIES_PER_TYPE
                        ]

                        # ENHANCED ERROR HANDLING: Validate project mentions
                        if not project_mentions or not all(
                            isinstance(mention, str) for mention in project_mentions
                        ):
                            logger.warning(
                                "Invalid project mentions in _get_entity_relationships"
                            )
                        else:
                            projects = (
                                db.session.query(Project)
                                .filter(Project.name.in_(project_mentions))
                                .limit(MAX_QUERY_RESULTS)
                                .all()
                            )

                            # ENHANCED ERROR HANDLING: Validate query results
                            if projects is not None:
                                for project in projects:
                                    if not project:  # Skip None projects
                                        continue

                                    try:
                                        # ENHANCED ERROR HANDLING: Validate project object
                                        if (
                                            not hasattr(project, "id")
                                            or project.id is None
                                        ):
                                            logger.warning(
                                                f"Project object missing id attribute: {project}"
                                            )
                                            continue

                                        project_data = {
                                            "id": project.id,
                                            "name": getattr(project, "name", None),
                                            "description": getattr(
                                                project, "description", None
                                            ),
                                            "client": None,
                                            "websites": [],
                                            "tasks": [],
                                            "documents": [],
                                        }

                                        # ENHANCED ERROR HANDLING: Proper null check for client_ref
                                        if hasattr(project, "client_ref"):
                                            try:
                                                if project.client_ref is not None:
                                                    client_ref = project.client_ref
                                                    if (
                                                        hasattr(client_ref, "id")
                                                        and client_ref.id is not None
                                                    ):
                                                        project_data["client"] = {
                                                            "id": client_ref.id,
                                                            "name": getattr(
                                                                client_ref, "name", None
                                                            ),
                                                        }
                                            except Exception as e:
                                                logger.warning(
                                                    f"Error accessing client_ref for project {project.id}: {e}"
                                                )

                                        # ENHANCED ERROR HANDLING: Safe relationship access
                                        if hasattr(project, "websites"):
                                            try:
                                                if project.websites is not None:
                                                    project_data["websites"] = [
                                                        {
                                                            "id": w.id,
                                                            "url": getattr(
                                                                w, "url", None
                                                            ),
                                                        }
                                                        for w in project.websites
                                                        if w
                                                        and hasattr(w, "id")
                                                        and w.id is not None
                                                        and hasattr(w, "url")
                                                    ]
                                            except Exception as websites_error:
                                                logger.warning(
                                                    f"Error accessing websites for project {project.id}: {websites_error}"
                                                )

                                        if hasattr(project, "tasks"):
                                            try:
                                                if project.tasks is not None:
                                                    project_data["tasks"] = [
                                                        {
                                                            "id": t.id,
                                                            "name": getattr(
                                                                t, "name", None
                                                            ),
                                                            "status": getattr(
                                                                t, "status", None
                                                            ),
                                                        }
                                                        for t in project.tasks
                                                        if t
                                                        and hasattr(t, "id")
                                                        and t.id is not None
                                                        and hasattr(t, "name")
                                                    ]
                                            except Exception as tasks_error:
                                                logger.warning(
                                                    f"Error accessing tasks for project {project.id}: {tasks_error}"
                                                )

                                        if hasattr(project, "documents"):
                                            try:
                                                if project.documents is not None:
                                                    project_data["documents"] = [
                                                        {
                                                            "id": d.id,
                                                            "filename": getattr(
                                                                d, "filename", None
                                                            ),
                                                        }
                                                        for d in project.documents
                                                        if d
                                                        and hasattr(d, "id")
                                                        and d.id is not None
                                                        and hasattr(d, "filename")
                                                    ]
                                            except Exception as documents_error:
                                                logger.warning(
                                                    f"Error accessing documents for project {project.id}: {documents_error}"
                                                )

                                        relationships["projects"].append(project_data)
                                    except Exception as e:
                                        logger.error(
                                            f"Error processing project {getattr(project, 'id', 'unknown')}: {e}"
                                        )
                                        continue

                    except Exception as project_query_error:
                        logger.error(f"Error querying projects: {project_query_error}")

                # Get website relationships with proper validation
                if entity_mentions.get("websites"):
                    try:
                        website_mentions = entity_mentions["websites"][
                            :MAX_ENTITIES_PER_TYPE
                        ]

                        # ENHANCED ERROR HANDLING: Validate website mentions
                        if not website_mentions or not all(
                            isinstance(mention, str) for mention in website_mentions
                        ):
                            logger.warning(
                                "Invalid website mentions in _get_entity_relationships"
                            )
                        else:
                            websites = (
                                db.session.query(Website)
                                .filter(Website.url.in_(website_mentions))
                                .limit(MAX_QUERY_RESULTS)
                                .all()
                            )

                            # ENHANCED ERROR HANDLING: Validate query results
                            if websites is not None:
                                for website in websites:
                                    if not website:  # Skip None websites
                                        continue

                                    try:
                                        # ENHANCED ERROR HANDLING: Validate website object
                                        if (
                                            not hasattr(website, "id")
                                            or website.id is None
                                        ):
                                            logger.warning(
                                                f"Website object missing id attribute: {website}"
                                            )
                                            continue

                                        website_data = {
                                            "id": website.id,
                                            "url": getattr(website, "url", None),
                                            "status": getattr(website, "status", None),
                                            "client": None,
                                            "project": None,
                                            "documents": [],
                                        }

                                        # ENHANCED ERROR HANDLING: Proper null checks
                                        if hasattr(website, "client_ref"):
                                            try:
                                                if website.client_ref is not None:
                                                    client_ref = website.client_ref
                                                    if (
                                                        hasattr(client_ref, "id")
                                                        and client_ref.id is not None
                                                    ):
                                                        website_data["client"] = {
                                                            "id": client_ref.id,
                                                            "name": getattr(
                                                                client_ref, "name", None
                                                            ),
                                                        }
                                            except Exception as e:
                                                logger.warning(
                                                    f"Error accessing client_ref for website {website.id}: {e}"
                                                )

                                        if hasattr(website, "project"):
                                            try:
                                                if website.project is not None:
                                                    project = website.project
                                                    if (
                                                        hasattr(project, "id")
                                                        and project.id is not None
                                                    ):
                                                        website_data["project"] = {
                                                            "id": project.id,
                                                            "name": getattr(
                                                                project, "name", None
                                                            ),
                                                        }
                                            except Exception as e:
                                                logger.warning(
                                                    f"Error accessing project for website {website.id}: {e}"
                                                )

                                        if hasattr(website, "documents"):
                                            try:
                                                if website.documents is not None:
                                                    website_data["documents"] = [
                                                        {
                                                            "id": d.id,
                                                            "filename": getattr(
                                                                d, "filename", None
                                                            ),
                                                        }
                                                        for d in website.documents
                                                        if d
                                                        and hasattr(d, "id")
                                                        and d.id is not None
                                                        and hasattr(d, "filename")
                                                    ]
                                            except Exception as documents_error:
                                                logger.warning(
                                                    f"Error accessing documents for website {website.id}: {documents_error}"
                                                )

                                        relationships["websites"].append(website_data)
                                    except Exception as e:
                                        logger.error(
                                            f"Error processing website {getattr(website, 'id', 'unknown')}: {e}"
                                        )
                                        continue

                    except Exception as website_query_error:
                        logger.error(f"Error querying websites: {website_query_error}")

                # Get task relationships with proper validation
                if entity_mentions.get("tasks"):
                    try:
                        task_mentions = entity_mentions["tasks"][:MAX_ENTITIES_PER_TYPE]

                        # ENHANCED ERROR HANDLING: Validate task mentions
                        if not task_mentions or not all(
                            isinstance(mention, str) for mention in task_mentions
                        ):
                            logger.warning(
                                "Invalid task mentions in _get_entity_relationships"
                            )
                        else:
                            tasks = (
                                db.session.query(Task)
                                .filter(Task.name.in_(task_mentions))
                                .limit(MAX_QUERY_RESULTS)
                                .all()
                            )

                            # ENHANCED ERROR HANDLING: Validate query results
                            if tasks is not None:
                                for task in tasks:
                                    if not task:  # Skip None tasks
                                        continue

                                    try:
                                        # ENHANCED ERROR HANDLING: Validate task object
                                        if not hasattr(task, "id") or task.id is None:
                                            logger.warning(
                                                f"Task object missing id attribute: {task}"
                                            )
                                            continue

                                        task_data = {
                                            "id": task.id,
                                            "name": getattr(task, "name", None),
                                            "status": getattr(task, "status", None),
                                            "description": getattr(
                                                task, "description", None
                                            ),
                                            "project": None,
                                            "client": None,
                                        }

                                        # ENHANCED ERROR HANDLING: Proper null checks for nested relationships
                                        if hasattr(task, "project"):
                                            try:
                                                if task.project is not None:
                                                    project = task.project
                                                    if (
                                                        hasattr(project, "id")
                                                        and project.id is not None
                                                    ):
                                                        task_data["project"] = {
                                                            "id": project.id,
                                                            "name": getattr(
                                                                project, "name", None
                                                            ),
                                                        }

                                                        # ENHANCED ERROR HANDLING: Check if project has client_ref
                                                        if hasattr(
                                                            project, "client_ref"
                                                        ):
                                                            try:
                                                                if (
                                                                    project.client_ref
                                                                    is not None
                                                                ):
                                                                    client_ref = (
                                                                        project.client_ref
                                                                    )
                                                                    if (
                                                                        hasattr(
                                                                            client_ref,
                                                                            "id",
                                                                        )
                                                                        and client_ref.id
                                                                        is not None
                                                                    ):
                                                                        task_data[
                                                                            "client"
                                                                        ] = {
                                                                            "id": client_ref.id,
                                                                            "name": getattr(
                                                                                client_ref,
                                                                                "name",
                                                                                None,
                                                                            ),
                                                                        }
                                                            except (
                                                                Exception
                                                            ) as client_ref_error:
                                                                logger.warning(
                                                                    f"Error accessing client_ref for task {task.id}: {client_ref_error}"
                                                                )
                                            except Exception as e:
                                                logger.warning(
                                                    f"Error accessing project for task {task.id}: {e}"
                                                )

                                        relationships["tasks"].append(task_data)
                                    except Exception as e:
                                        logger.error(
                                            f"Error processing task {getattr(task, 'id', 'unknown')}: {e}"
                                        )
                                        continue

                    except Exception as task_query_error:
                        logger.error(f"Error querying tasks: {task_query_error}")

                # Get document relationships with proper validation
                if entity_mentions.get("documents"):
                    try:
                        document_mentions = entity_mentions["documents"][
                            :MAX_ENTITIES_PER_TYPE
                        ]

                        # ENHANCED ERROR HANDLING: Validate document mentions
                        if not document_mentions or not all(
                            isinstance(mention, str) for mention in document_mentions
                        ):
                            logger.warning(
                                "Invalid document mentions in _get_entity_relationships"
                            )
                        else:
                            documents = (
                                db.session.query(Document)
                                .filter(Document.filename.in_(document_mentions))
                                .limit(MAX_QUERY_RESULTS)
                                .all()
                            )

                            # ENHANCED ERROR HANDLING: Validate query results
                            if documents is not None:
                                for document in documents:
                                    if not document:  # Skip None documents
                                        continue

                                    try:
                                        # ENHANCED ERROR HANDLING: Validate document object
                                        if (
                                            not hasattr(document, "id")
                                            or document.id is None
                                        ):
                                            logger.warning(
                                                f"Document object missing id attribute: {document}"
                                            )
                                            continue

                                        document_data = {
                                            "id": document.id,
                                            "filename": getattr(
                                                document, "filename", None
                                            ),
                                            "type": getattr(document, "type", None),
                                            "project": None,
                                            "website": None,
                                            "client": None,
                                        }

                                        # ENHANCED ERROR HANDLING: Proper null checks for document relationships
                                        if hasattr(document, "project"):
                                            try:
                                                if document.project is not None:
                                                    project = document.project
                                                    if (
                                                        hasattr(project, "id")
                                                        and project.id is not None
                                                    ):
                                                        document_data["project"] = {
                                                            "id": project.id,
                                                            "name": getattr(
                                                                project, "name", None
                                                            ),
                                                        }

                                                        # ENHANCED ERROR HANDLING: Check project's client_ref
                                                        if hasattr(
                                                            project, "client_ref"
                                                        ):
                                                            try:
                                                                if (
                                                                    project.client_ref
                                                                    is not None
                                                                ):
                                                                    client_ref = (
                                                                        project.client_ref
                                                                    )
                                                                    if (
                                                                        hasattr(
                                                                            client_ref,
                                                                            "id",
                                                                        )
                                                                        and client_ref.id
                                                                        is not None
                                                                    ):
                                                                        document_data[
                                                                            "client"
                                                                        ] = {
                                                                            "id": client_ref.id,
                                                                            "name": getattr(
                                                                                client_ref,
                                                                                "name",
                                                                                None,
                                                                            ),
                                                                        }
                                                            except (
                                                                Exception
                                                            ) as client_ref_error:
                                                                logger.warning(
                                                                    f"Error accessing client_ref for document {document.id}: {client_ref_error}"
                                                                )
                                            except Exception as e:
                                                logger.warning(
                                                    f"Error accessing project for document {document.id}: {e}"
                                                )

                                        if hasattr(document, "website"):
                                            try:
                                                if document.website is not None:
                                                    website = document.website
                                                    if (
                                                        hasattr(website, "id")
                                                        and website.id is not None
                                                    ):
                                                        document_data["website"] = {
                                                            "id": website.id,
                                                            "url": getattr(
                                                                website, "url", None
                                                            ),
                                                        }
                                            except Exception as e:
                                                logger.warning(
                                                    f"Error accessing website for document {document.id}: {e}"
                                                )

                                        relationships["documents"].append(document_data)
                                    except Exception as e:
                                        logger.error(
                                            f"Error processing document {getattr(document, 'id', 'unknown')}: {e}"
                                        )
                                        continue

                    except Exception as document_query_error:
                        logger.error(
                            f"Error querying documents: {document_query_error}"
                        )

        except Exception as e:
            logger.error(
                f"Critical error getting entity relationships: {e}", exc_info=True
            )

        return relationships

    def _build_enhanced_context(
        self, retrieved_nodes: List[Any], entity_relationships: Dict[str, Any]
    ) -> str:
        """Build enhanced context combining retrieved nodes and entity relationships"""
        context_parts = []

        # Add entity relationship context
        if any(entity_relationships.values()):
            context_parts.append("=== ENTITY RELATIONSHIPS ===")

            for client in entity_relationships["clients"]:
                context_parts.append(f"CLIENT: {client['name']}")
                if client["email"]:
                    context_parts.append(f"  Email: {client['email']}")
                if client["projects"]:
                    context_parts.append(
                        f"  Projects: {', '.join(p['name'] for p in client['projects'])}"
                    )
                if client["websites"]:
                    context_parts.append(
                        f"  Websites: {', '.join(w['url'] for w in client['websites'])}"
                    )
                if client["total_documents"] > 0:
                    context_parts.append(
                        f"  Total Documents: {client['total_documents']}"
                    )

            for project in entity_relationships["projects"]:
                context_parts.append(f"PROJECT: {project['name']}")
                if project["description"]:
                    context_parts.append(f"  Description: {project['description']}")
                if project["client"]:
                    context_parts.append(f"  Client: {project['client']['name']}")
                if project["tasks"]:
                    context_parts.append(
                        f"  Tasks: {', '.join(t['name'] for t in project['tasks'])}"
                    )
                if project["documents"]:
                    context_parts.append(
                        f"  Documents: {', '.join(d['filename'] for d in project['documents'])}"
                    )

            for website in entity_relationships["websites"]:
                context_parts.append(f"WEBSITE: {website['url']}")
                context_parts.append(f"  Status: {website['status']}")
                if website["client"]:
                    context_parts.append(f"  Client: {website['client']['name']}")
                if website["project"]:
                    context_parts.append(f"  Project: {website['project']['name']}")

            for task in entity_relationships["tasks"]:
                context_parts.append(f"TASK: {task['name']}")
                context_parts.append(f"  Status: {task['status']}")
                if task["description"]:
                    context_parts.append(f"  Description: {task['description']}")
                if task["project"]:
                    context_parts.append(f"  Project: {task['project']['name']}")
                if task["client"]:
                    context_parts.append(f"  Client: {task['client']['name']}")

            context_parts.append("")  # Empty line separator

        # Add retrieved node context
        if retrieved_nodes:
            context_parts.append("=== RETRIEVED CONTENT ===")
            for i, node in enumerate(retrieved_nodes[:5]):  # Limit to top 5
                if hasattr(node, "get_content"):
                    content = (
                        node.get_content()[:500] + "..."
                        if len(node.get_content()) > 500
                        else node.get_content()
                    )
                    context_parts.append(f"[CONTENT {i+1}] {content}")
                elif hasattr(node, "text"):
                    content = (
                        node.text[:500] + "..." if len(node.text) > 500 else node.text
                    )
                    context_parts.append(f"[CONTENT {i+1}] {content}")

        return "\n".join(context_parts)

    def _build_relationship_summary(self, entity_relationships: Dict[str, Any]) -> str:
        """Build a summary of entity relationships"""
        summary_parts = []

        total_clients = len(entity_relationships["clients"])
        total_projects = len(entity_relationships["projects"])
        total_websites = len(entity_relationships["websites"])
        total_tasks = len(entity_relationships["tasks"])
        total_documents = len(entity_relationships["documents"])

        if total_clients > 0:
            summary_parts.append(f"{total_clients} client(s)")
        if total_projects > 0:
            summary_parts.append(f"{total_projects} project(s)")
        if total_websites > 0:
            summary_parts.append(f"{total_websites} website(s)")
        if total_tasks > 0:
            summary_parts.append(f"{total_tasks} task(s)")
        if total_documents > 0:
            summary_parts.append(f"{total_documents} document(s)")

        if summary_parts:
            return f"Context includes: {', '.join(summary_parts)}"
        else:
            return "No specific entities identified in query"

    def get_entity_context_for_chat(self, query: str) -> Dict[str, Any]:
        """Get entity context specifically formatted for chat responses"""
        entity_mentions = self._extract_entity_mentions(query)
        entity_relationships = self._get_entity_relationships(entity_mentions)

        # Build a more conversational context
        context_parts = []

        # Add client context
        for client in entity_relationships["clients"]:
            context_parts.append(f"Client '{client['name']}' information:")
            if client["email"]:
                context_parts.append(f"- Contact: {client['email']}")
            if client["projects"]:
                context_parts.append(
                    f"- Projects: {', '.join(p['name'] for p in client['projects'])}"
                )
            if client["total_documents"] > 0:
                context_parts.append(f"- Has {client['total_documents']} documents")

        # Add project context
        for project in entity_relationships["projects"]:
            context_parts.append(f"Project '{project['name']}' details:")
            if project["description"]:
                context_parts.append(f"- Description: {project['description']}")
            if project["client"]:
                context_parts.append(f"- Client: {project['client']['name']}")
            if project["tasks"]:
                context_parts.append(
                    f"- Tasks: {', '.join(t['name'] + ' (' + t['status'] + ')' for t in project['tasks'])}"
                )

        return {
            "context": "\n".join(context_parts),
            "entity_count": sum(
                len(entities) for entities in entity_relationships.values()
            ),
            "has_entities": any(entity_relationships.values()),
        }


# Global instance
entity_context_enhancer = EntityContextEnhancer()


def get_entity_context_enhancer() -> EntityContextEnhancer:
    """Get the global entity context enhancer"""
    return entity_context_enhancer
