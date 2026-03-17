"""
Backend Handlers Package
Database and other handlers for direct operations.
"""

from .database_handler import DatabaseHandler, create_database_handler

__all__ = [
    "DatabaseHandler",
    "create_database_handler",
]
