#!/usr/bin/env python3
"""
Database Handler - Direct SQL Queries for Fast Responses
Handles count/list queries without RAG overhead
"""

import logging
from typing import Dict, List, Optional, Tuple
from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


class DatabaseHandler:
    """
    Direct database query handler for fast responses
    Bypasses RAG system for simple count/list operations
    """

    def __init__(self, db_session):
        self.db = db_session
        # Import models once during initialization
        try:
            from backend.models import Client, Project, Document, Task, Website

            self.Client = Client
            self.Project = Project
            self.Document = Document
            self.Task = Task
            self.Website = Website
        except ImportError as e:
            logger.error(f"Failed to import database models: {e}")
            self.Client = self.Project = self.Document = self.Task = self.Website = None

    def handle_query(self, message: str, metadata: Dict) -> Dict:
        """
        Handle database query with direct SQL

        Args:
            message: User query message
            metadata: Intent classification metadata

        Returns:
            Dict with response data
        """
        message_lower = message.lower().strip()

        try:
            # Check if models are available
            if not all(
                [self.Client, self.Project, self.Document, self.Task, self.Website]
            ):
                return {
                    "success": False,
                    "error": "Database models not available",
                    "fallback_to_rag": True,
                }

            # Detect query type and execute
            if any(
                word in message_lower
                for word in ["count", "how many", "total", "number"]
            ):
                return self._handle_count_query(message_lower)
            elif any(word in message_lower for word in ["list", "show me", "all"]):
                return self._handle_list_query(message_lower)
            else:
                return self._handle_general_database_query(message_lower)

        except Exception as e:
            logger.error(f"Database handler error: {e}")
            return {"success": False, "error": str(e), "fallback_to_rag": True}

    def _handle_count_query(self, message: str) -> Dict:
        """Handle count queries with fast SQL"""

        try:
            counts = {}
            response_parts = []

            # Get counts for all entities
            if any(word in message for word in ["client", "all"]):
                counts["clients"] = self.db.query(func.count(self.Client.id)).scalar()
                response_parts.append(f"**{counts['clients']} clients**")

            if any(word in message for word in ["project", "all"]):
                counts["projects"] = self.db.query(func.count(self.Project.id)).scalar()
                response_parts.append(f"**{counts['projects']} projects**")

            if any(word in message for word in ["document", "file", "all"]):
                counts["documents"] = self.db.query(
                    func.count(self.Document.id)
                ).scalar()
                response_parts.append(f"**{counts['documents']} documents**")

            if any(word in message for word in ["task", "all"]):
                counts["tasks"] = self.db.query(func.count(self.Task.id)).scalar()
                response_parts.append(f"**{counts['tasks']} tasks**")

            if any(word in message for word in ["website", "all"]):
                counts["websites"] = self.db.query(func.count(self.Website.id)).scalar()
                response_parts.append(f"**{counts['websites']} websites**")

            # If no specific entity mentioned, show all
            if not response_parts:
                counts["clients"] = self.db.query(func.count(self.Client.id)).scalar()
                counts["projects"] = self.db.query(func.count(self.Project.id)).scalar()
                counts["documents"] = self.db.query(
                    func.count(self.Document.id)
                ).scalar()
                counts["tasks"] = self.db.query(func.count(self.Task.id)).scalar()
                counts["websites"] = self.db.query(func.count(self.Website.id)).scalar()

                response_parts = [
                    f"**{counts['clients']} clients**",
                    f"**{counts['projects']} projects**",
                    f"**{counts['documents']} documents**",
                    f"**{counts['tasks']} tasks**",
                    f"**{counts['websites']} websites**",
                ]

            response = "## Database Summary\n\n" + " • ".join(response_parts)

            # Add contextual information
            if counts.get("clients", 0) > 0:
                response += f"\n\n*Database contains active data across {len([k for k,v in counts.items() if v > 0])} entity types*"

            logger.info(f"Database count query completed: {counts}")

            return {
                "success": True,
                "response": response,
                "counts": counts,
                "query_type": "count",
                "execution_time": "fast",
                "used_rag": False,
            }

        except SQLAlchemyError as e:
            logger.error(f"SQL error in count query: {e}")
            return {
                "success": False,
                "error": f"Database query failed: {str(e)}",
                "fallback_to_rag": True,
            }

    def _handle_list_query(self, message: str) -> Dict:
        """Handle list queries with basic entity information"""

        try:
            response_parts = []

            # Client listing
            if any(word in message for word in ["client", "all"]):
                clients = self.db.query(self.Client.name).limit(10).all()
                if clients:
                    client_names = [c.name for c in clients]
                    response_parts.append(
                        f"**Clients:** {', '.join(client_names[:5])}"
                        + (
                            f" (and {len(client_names)-5} more)"
                            if len(client_names) > 5
                            else ""
                        )
                    )

            # Project listing
            if any(word in message for word in ["project", "all"]):
                projects = self.db.query(self.Project.name).limit(10).all()
                if projects:
                    project_names = [p.name for p in projects]
                    response_parts.append(
                        f"**Projects:** {', '.join(project_names[:5])}"
                        + (
                            f" (and {len(project_names)-5} more)"
                            if len(project_names) > 5
                            else ""
                        )
                    )

            # Document listing
            if any(word in message for word in ["document", "file", "all"]):
                documents = self.db.query(self.Document.filename).limit(10).all()
                if documents:
                    doc_names = [d.filename for d in documents]
                    response_parts.append(
                        f"**Recent Documents:** {', '.join(doc_names[:3])}"
                        + (
                            f" (and {len(doc_names)-3} more)"
                            if len(doc_names) > 3
                            else ""
                        )
                    )

            if not response_parts:
                response_parts.append(
                    "No specific entity requested. Use 'list clients', 'list projects', or 'list documents' for specific listings."
                )

            response = "## Database Listings\n\n" + "\n\n".join(response_parts)

            logger.info("Database list query completed")

            return {
                "success": True,
                "response": response,
                "query_type": "list",
                "execution_time": "fast",
                "used_rag": False,
            }

        except SQLAlchemyError as e:
            logger.error(f"SQL error in list query: {e}")
            return {
                "success": False,
                "error": f"Database query failed: {str(e)}",
                "fallback_to_rag": True,
            }

    def _handle_general_database_query(self, message: str) -> Dict:
        """Handle general database queries that don't fit count/list patterns"""

        # For now, fall back to RAG for complex queries
        logger.info("Complex database query detected, falling back to RAG")
        return {
            "success": False,
            "error": "Complex query requires RAG processing",
            "fallback_to_rag": True,
            "reason": "Query too complex for direct SQL",
        }

    def get_database_stats(self) -> Dict:
        """Get overall database statistics for context"""
        try:
            from backend.models import Client, Project, Document, Task, Website

            stats = {
                "clients": self.db.query(func.count(Client.id)).scalar(),
                "projects": self.db.query(func.count(Project.id)).scalar(),
                "documents": self.db.query(func.count(Document.id)).scalar(),
                "tasks": self.db.query(func.count(Task.id)).scalar(),
                "websites": self.db.query(func.count(Website.id)).scalar(),
            }

            return {
                "success": True,
                "stats": stats,
                "total_entities": sum(stats.values()),
            }

        except Exception as e:
            logger.error(f"Error getting database stats: {e}")
            return {"success": False, "error": str(e)}


# Convenience function for easy import
def create_database_handler(db_session):
    """Create database handler instance"""
    return DatabaseHandler(db_session)
