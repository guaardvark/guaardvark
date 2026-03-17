"""
Database utility functions for proper session management and connection leak prevention.
"""

import logging
from functools import wraps
from typing import Callable, Any

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


def ensure_db_session_cleanup(func: Callable) -> Callable:
    """
    Decorator to ensure database session cleanup after API requests.

    This decorator ensures that database sessions are properly closed
    and rolled back in case of exceptions, preventing connection leaks.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            # Execute the original function
            result = func(*args, **kwargs)
            return result

        except SQLAlchemyError as e:
            # Database error - rollback and re-raise
            logger.error(f"Database error in {func.__name__}: {e}")
            _safe_rollback()
            raise

        except Exception as e:
            # Non-database error - still rollback to be safe
            logger.error(f"General error in {func.__name__}: {e}")
            _safe_rollback()
            raise

        finally:
            # Safe session cleanup
            _safe_session_cleanup()

    return wrapper


def _safe_rollback():
    """Safely rollback database session without causing state conflicts."""
    try:
        from backend.models import db

        if db and db.session:
            # Simple rollback without state checking to avoid conflicts
            try:
                db.session.rollback()
                logger.debug("Database session rolled back safely")
            except Exception as rollback_error:
                # Log but don't re-raise rollback errors
                logger.debug(
                    f"Rollback attempt failed (usually harmless): {rollback_error}"
                )
    except Exception as outer_error:
        # Don't log outer errors as they're often harmless
        pass


def _safe_session_cleanup():
    """Safely cleanup database session without causing state conflicts."""
    try:
        from backend.models import db

        if db and db.session:
            # Only close if session exists and is not in an active transaction
            if hasattr(db.session, "is_active") and not db.session.is_active:
                try:
                    db.session.close()
                    logger.debug("Database session closed safely")
                except Exception as close_error:
                    # Don't log close errors as they're often harmless
                    pass
    except Exception as cleanup_error:
        # Don't log cleanup errors as they're often harmless
        pass


def with_db_transaction(func: Callable) -> Callable:
    """
    Decorator to wrap a function in a database transaction.

    This decorator ensures that all database operations within the function
    are performed within a single transaction, with automatic rollback on errors.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        from backend.models import db

        if not db:
            raise RuntimeError("Database not available")

        try:
            # Begin transaction
            db.session.begin()

            # Execute function
            result = func(*args, **kwargs)

            # Commit transaction
            db.session.commit()
            logger.debug(f"Transaction committed for {func.__name__}")

            return result

        except Exception as e:
            # Rollback transaction on any error
            logger.error(f"Transaction error in {func.__name__}: {e}")
            try:
                db.session.rollback()
                logger.debug("Transaction rolled back")
            except Exception as rollback_error:
                logger.error(f"Failed to rollback transaction: {rollback_error}")
            raise

        finally:
            # Clean up session
            try:
                # Only close if session exists and is not already closed
                if hasattr(db, "session") and db.session:
                    db.session.close()
                    logger.debug("Database session closed")
            except Exception as cleanup_error:
                logger.error(f"Failed to close database session: {cleanup_error}")

    return wrapper


def get_db_connection_info() -> dict:
    """
    Get information about current database connections.

    Returns:
        Dictionary with connection pool information
    """
    try:
        from backend.models import db

        if not db or not db.engine:
            return {"error": "Database not available"}

        pool = db.engine.pool
        info = {
            "pool_size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
            "invalid": pool.invalid(),
        }

        return info

    except Exception as e:
        logger.error(f"Failed to get database connection info: {e}")
        return {"error": str(e)}


def safe_db_rollback(context: str = "operation") -> None:
    """
    Safely perform a database rollback with proper error handling.

    This function should be used instead of bare db.session.rollback() calls
    to prevent connection leaks and properly handle rollback failures.

    Args:
        context: Description of the operation being rolled back (for logging)
    """
    try:
        from backend.models import db

        if not db or not db.session:
            logger.warning(f"Database not available for rollback in {context}")
            return

        if db.session.is_active:
            db.session.rollback()
            logger.debug(f"Database rollback successful for {context}")
        else:
            logger.debug(f"No active transaction to rollback in {context}")

    except Exception as e:
        logger.error(f"Failed to rollback database transaction in {context}: {e}")
        # Don't re-raise rollback errors as they can mask the original error


def safe_db_commit(context: str = "operation") -> bool:
    """
    Safely perform a database commit with proper error handling.

    Args:
        context: Description of the operation being committed (for logging)

    Returns:
        True if commit was successful, False otherwise
    """
    try:
        from backend.models import db

        if not db or not db.session:
            logger.warning(f"Database not available for commit in {context}")
            return False

        # Simple commit without checking session state to avoid conflicts
        try:
            db.session.commit()
            logger.debug(f"Database commit successful for {context}")
            return True
        except Exception as commit_error:
            logger.error(
                f"Failed to commit database transaction in {context}: {commit_error}"
            )
            # Attempt rollback
            try:
                db.session.rollback()
                logger.debug(f"Rollback successful after commit failure in {context}")
            except Exception as rollback_error:
                logger.debug(
                    f"Rollback after commit failure also failed in {context}: {rollback_error}"
                )
            return False

    except Exception as e:
        logger.error(f"Outer error in safe_db_commit for {context}: {e}")
        return False


def cleanup_idle_connections() -> dict:
    """
    Clean up idle database connections.

    Returns:
        Dictionary with cleanup statistics
    """
    try:
        from backend.models import db

        if not db or not db.engine:
            return {"error": "Database not available"}

        # Get initial connection info
        initial_info = get_db_connection_info()

        # Dispose of the current connection pool
        db.engine.dispose()

        # Get final connection info
        final_info = get_db_connection_info()

        logger.info("Database connection pool disposed and recreated")

        return {
            "message": "Connection pool refreshed",
            "initial_connections": initial_info,
            "final_connections": final_info,
        }

    except Exception as e:
        logger.error(f"Failed to cleanup idle connections: {e}")
        return {"error": str(e)}


class DatabaseConnectionManager:
    """
    Context manager for database operations with automatic cleanup.

    Usage:
        with DatabaseConnectionManager() as db_manager:
            # Perform database operations
            pass
    """

    def __init__(self):
        self.db = None
        self.session = None

    def __enter__(self):
        from backend.models import db

        if not db:
            raise RuntimeError("Database not available")

        self.db = db
        self.session = db.session

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            try:
                if exc_type:
                    # Exception occurred - rollback
                    self.session.rollback()
                    logger.debug("Database session rolled back due to exception")
                else:
                    # No exception - commit if there are changes
                    if self.session.dirty or self.session.new or self.session.deleted:
                        self.session.commit()
                        logger.debug("Database session committed successfully")

            except Exception as e:
                logger.error(f"Error during database session cleanup: {e}")
                try:
                    self.session.rollback()
                except Exception:
                    pass

            finally:
                try:
                    self.session.close()
                    logger.debug("Database session closed")
                except Exception as e:
                    logger.error(f"Failed to close database session: {e}")

        return False  # Don't suppress exceptions
