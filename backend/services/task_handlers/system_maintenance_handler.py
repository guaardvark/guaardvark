
import logging
import os
import shutil
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Callable

from .base_handler import BaseTaskHandler, TaskResult, TaskResultStatus

logger = logging.getLogger(__name__)


class SystemMaintenanceHandler(BaseTaskHandler):

    @property
    def handler_name(self) -> str:
        return "system_maintenance"

    @property
    def display_name(self) -> str:
        return "System Maintenance"

    @property
    def process_type(self) -> str:
        return "system_maintenance"

    @property
    def celery_queue(self) -> str:
        return "default"

    @property
    def default_priority(self) -> int:
        return 8

    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "required": ["maintenance_type"],
            "properties": {
                "maintenance_type": {
                    "type": "string",
                    "enum": [
                        "cleanup_chat_sessions",
                        "cleanup_progress_jobs",
                        "cleanup_behavior_logs",
                        "cleanup_temp_files",
                        "database_vacuum",
                        "cache_clear",
                        "comprehensive_cleanup",
                        "cleanup_old_indexes",
                        "cleanup_output_files"
                    ],
                    "description": "Type of maintenance operation"
                },
                "max_age_days": {
                    "type": "integer",
                    "default": 30,
                    "description": "Maximum age in days for cleanup operations"
                },
                "max_sessions": {
                    "type": "integer",
                    "default": 1000,
                    "description": "Maximum number of sessions to keep"
                },
                "max_size_mb": {
                    "type": "integer",
                    "default": 100,
                    "description": "Maximum size in MB for log cleanup"
                },
                "dry_run": {
                    "type": "boolean",
                    "default": False,
                    "description": "Perform dry run without actual changes"
                },
                "clean_completed": {
                    "type": "boolean",
                    "default": False,
                    "description": "Also clean completed progress jobs"
                }
            }
        }

    def execute(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable[[int, str, Optional[Dict[str, Any]]], None]
    ) -> TaskResult:
        started_at = datetime.now()

        try:
            maintenance_type = config.get("maintenance_type", "comprehensive_cleanup")

            operations = {
                "cleanup_chat_sessions": self._cleanup_chat_sessions,
                "cleanup_progress_jobs": self._cleanup_progress_jobs,
                "cleanup_behavior_logs": self._cleanup_behavior_logs,
                "cleanup_temp_files": self._cleanup_temp_files,
                "database_vacuum": self._database_vacuum,
                "cache_clear": self._cache_clear,
                "comprehensive_cleanup": self._comprehensive_cleanup,
                "cleanup_old_indexes": self._cleanup_old_indexes,
                "cleanup_output_files": self._cleanup_output_files
            }

            handler = operations.get(maintenance_type)
            if not handler:
                return TaskResult(
                    status=TaskResultStatus.FAILED,
                    message=f"Unknown maintenance type: {maintenance_type}",
                    error_message=f"maintenance_type must be one of: {', '.join(operations.keys())}",
                    started_at=started_at,
                    completed_at=datetime.now()
                )

            return handler(task, config, progress_callback, started_at)

        except Exception as e:
            logger.error(f"System maintenance handler error: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Maintenance failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now()
            )

    def _cleanup_chat_sessions(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime
    ) -> TaskResult:
        max_age_days = config.get("max_age_days", 30)
        max_sessions = config.get("max_sessions", 1000)
        dry_run = config.get("dry_run", False)

        progress_callback(0, f"Cleaning chat sessions older than {max_age_days} days...", None)

        try:
            from backend.utils.chat_utils import cleanup_old_sessions

            if dry_run:
                progress_callback(50, "Dry run - analyzing sessions to clean...", None)
                from backend.handlers.db_handler import DatabaseHandler
                from backend.models import Conversation

                db = DatabaseHandler()
                cutoff = datetime.now() - timedelta(days=max_age_days)

                with db.session_scope() as session:
                    old_sessions = session.query(Conversation).filter(
                        Conversation.updated_at < cutoff
                    ).count()

                    result = {
                        "success": True,
                        "sessions_would_delete": old_sessions,
                        "dry_run": True
                    }
            else:
                progress_callback(30, "Running cleanup...", None)
                result = cleanup_old_sessions(max_age_days, max_sessions)

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            progress_callback(100, "Chat session cleanup complete", None)

            if result.get("success"):
                return TaskResult(
                    status=TaskResultStatus.SUCCESS,
                    message=f"Cleaned {result.get('sessions_deleted', 0)} sessions, {result.get('messages_deleted', 0)} messages",
                    output_data=result,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration
                )
            else:
                return TaskResult(
                    status=TaskResultStatus.FAILED,
                    message=f"Cleanup failed: {result.get('error', 'Unknown error')}",
                    error_message=result.get("error"),
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration
                )

        except Exception as e:
            logger.error(f"Chat session cleanup failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Cleanup failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now()
            )

    def _cleanup_progress_jobs(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime
    ) -> TaskResult:
        max_age_hours = config.get("max_age_days", 1) * 24
        clean_completed = config.get("clean_completed", False)
        dry_run = config.get("dry_run", False)

        progress_callback(0, "Cleaning progress jobs...", None)

        try:
            from backend.utils.unified_progress_system import cleanup_stuck_jobs

            progress_callback(30, "Identifying stuck jobs...", None)

            cleaned = 0
            if not dry_run:
                cleaned = cleanup_stuck_jobs(max_age_hours=max_age_hours)

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            progress_callback(100, "Progress job cleanup complete", None)

            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message=f"Cleaned {cleaned} stuck progress jobs",
                output_data={
                    "cleaned_count": cleaned,
                    "max_age_hours": max_age_hours,
                    "dry_run": dry_run
                },
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration
            )

        except ImportError:
            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message="Progress job cleanup not available",
                output_data={"note": "cleanup_stuck_jobs function not available"},
                started_at=started_at,
                completed_at=datetime.now()
            )
        except Exception as e:
            logger.error(f"Progress job cleanup failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Cleanup failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now()
            )

    def _cleanup_behavior_logs(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime
    ) -> TaskResult:
        max_size_mb = config.get("max_size_mb", 100)
        max_age_days = config.get("max_age_days", 90)
        dry_run = config.get("dry_run", False)

        progress_callback(0, "Cleaning behavior logs...", None)

        try:
            from backend.utils.chat_utils import cleanup_user_behavior_log

            if dry_run:
                progress_callback(50, "Dry run - analyzing logs...", None)
                result = {"success": True, "dry_run": True, "message": "Would clean old behavior logs"}
            else:
                progress_callback(30, "Running cleanup...", None)
                result = cleanup_user_behavior_log(max_size_mb, max_age_days)

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            progress_callback(100, "Behavior log cleanup complete", None)

            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message=f"Cleaned behavior logs: {result.get('entries_removed', 0)} entries removed",
                output_data=result,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration
            )

        except Exception as e:
            logger.error(f"Behavior log cleanup failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Cleanup failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now()
            )

    def _cleanup_temp_files(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime
    ) -> TaskResult:
        max_age_days = config.get("max_age_days", 7)
        dry_run = config.get("dry_run", False)

        progress_callback(0, "Cleaning temporary files...", None)

        try:
            from backend.config import CACHE_DIR, GUAARDVARK_ROOT
            import tempfile

            temp_dirs = [
                CACHE_DIR,
                os.path.join(GUAARDVARK_ROOT, "data", "temp"),
                tempfile.gettempdir()
            ]

            files_cleaned = 0
            space_freed = 0
            cutoff = datetime.now() - timedelta(days=max_age_days)

            for temp_dir in temp_dirs:
                if not os.path.exists(temp_dir):
                    continue

                progress_callback(30, f"Scanning {temp_dir}...", None)

                for root, dirs, files in os.walk(temp_dir):
                    for filename in files:
                        filepath = os.path.join(root, filename)
                        try:
                            stat = os.stat(filepath)
                            mtime = datetime.fromtimestamp(stat.st_mtime)

                            if mtime < cutoff:
                                if not dry_run:
                                    file_size = stat.st_size
                                    os.remove(filepath)
                                    space_freed += file_size
                                files_cleaned += 1
                        except (OSError, IOError) as e:
                            logger.warning(f"Could not process {filepath}: {e}")

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            progress_callback(100, "Temp file cleanup complete", None)

            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message=f"Cleaned {files_cleaned} temp files, freed {space_freed / 1024 / 1024:.2f} MB",
                output_data={
                    "files_cleaned": files_cleaned,
                    "space_freed_bytes": space_freed,
                    "space_freed_mb": round(space_freed / 1024 / 1024, 2),
                    "max_age_days": max_age_days,
                    "dry_run": dry_run
                },
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration
            )

        except Exception as e:
            logger.error(f"Temp file cleanup failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Cleanup failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now()
            )

    def _database_vacuum(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime
    ) -> TaskResult:
        dry_run = config.get("dry_run", False)

        progress_callback(0, "Starting database optimization...", None)

        try:
            from sqlalchemy import create_engine, text
            from backend.config import DATABASE_URL

            engine = create_engine(DATABASE_URL)

            # Get database size before optimization
            with engine.connect() as conn:
                result = conn.execute(text("SELECT pg_database_size(current_database())"))
                size_before = result.scalar()

            if not dry_run:
                progress_callback(30, "Running VACUUM...", None)

                # VACUUM must run outside a transaction in PostgreSQL
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.execute(text("VACUUM"))

                progress_callback(70, "Running ANALYZE...", None)

                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.execute(text("ANALYZE"))

            # Get database size after optimization
            with engine.connect() as conn:
                result = conn.execute(text("SELECT pg_database_size(current_database())"))
                size_after = result.scalar() if not dry_run else size_before

            engine.dispose()

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            progress_callback(100, "Database optimization complete", None)

            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message=f"Database optimized: {(size_before - size_after) / 1024:.1f} KB freed",
                output_data={
                    "size_before_bytes": size_before,
                    "size_after_bytes": size_after,
                    "space_freed_bytes": size_before - size_after,
                    "dry_run": dry_run
                },
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration
            )

        except Exception as e:
            logger.error(f"Database vacuum failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Database optimization failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now()
            )

    def _cache_clear(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime
    ) -> TaskResult:
        dry_run = config.get("dry_run", False)

        progress_callback(0, "Clearing caches...", None)

        cleared_caches = []

        try:
            progress_callback(20, "Clearing chunk metadata cache...", None)
            try:
                from backend.utils.chunk_metadata_cache import clear_cache as clear_chunk_cache
                if not dry_run:
                    cleared = clear_chunk_cache()
                    cleared_caches.append({"name": "chunk_metadata", "entries_cleared": cleared})
                else:
                    cleared_caches.append({"name": "chunk_metadata", "dry_run": True})
            except ImportError:
                pass

            progress_callback(40, "Clearing query cache...", None)
            try:
                from backend.utils.query_cache import get_query_cache
                cache = get_query_cache()
                if not dry_run:
                    result = cache.cleanup_expired()
                    cleared_caches.append({"name": "query_cache", "entries_cleared": result.get("removed", 0)})
                else:
                    cleared_caches.append({"name": "query_cache", "dry_run": True})
            except ImportError:
                pass

            progress_callback(60, "Clearing general cache...", None)
            try:
                from backend.utils.cache_manager import get_unified_cache_manager
                cache_mgr = get_unified_cache_manager()
                if not dry_run:
                    result = cache_mgr.cleanup_expired()
                    cleared_caches.append({"name": "unified_cache", "entries_cleared": result})
                else:
                    cleared_caches.append({"name": "unified_cache", "dry_run": True})
            except ImportError:
                pass

            progress_callback(80, "Clearing index cache...", None)
            try:
                from backend.utils.unified_index_manager import get_unified_index_manager
                idx_mgr = get_unified_index_manager()
                if not dry_run:
                    idx_mgr.clear_cache()
                    cleared_caches.append({"name": "index_manager", "cleared": True})
                else:
                    cleared_caches.append({"name": "index_manager", "dry_run": True})
            except ImportError:
                pass

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            progress_callback(100, "Cache clearing complete", None)

            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message=f"Cleared {len(cleared_caches)} caches",
                output_data={
                    "cleared_caches": cleared_caches,
                    "dry_run": dry_run
                },
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration
            )

        except Exception as e:
            logger.error(f"Cache clearing failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Cache clearing failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now()
            )

    def _comprehensive_cleanup(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime
    ) -> TaskResult:
        dry_run = config.get("dry_run", False)

        progress_callback(0, "Starting comprehensive cleanup...", None)

        results = {}
        errors = []

        progress_callback(10, "Cleaning chat sessions...", None)
        try:
            chat_result = self._cleanup_chat_sessions(task, config, lambda p, m, d=None: None, started_at)
            results["chat_sessions"] = chat_result.message
        except Exception as e:
            errors.append(f"chat_sessions: {str(e)}")

        progress_callback(25, "Cleaning behavior logs...", None)
        try:
            logs_result = self._cleanup_behavior_logs(task, config, lambda p, m, d=None: None, started_at)
            results["behavior_logs"] = logs_result.message
        except Exception as e:
            errors.append(f"behavior_logs: {str(e)}")

        progress_callback(40, "Cleaning temp files...", None)
        try:
            temp_result = self._cleanup_temp_files(task, config, lambda p, m, d=None: None, started_at)
            results["temp_files"] = temp_result.message
        except Exception as e:
            errors.append(f"temp_files: {str(e)}")

        progress_callback(55, "Clearing caches...", None)
        try:
            cache_result = self._cache_clear(task, config, lambda p, m, d=None: None, started_at)
            results["caches"] = cache_result.message
        except Exception as e:
            errors.append(f"caches: {str(e)}")

        progress_callback(75, "Optimizing database...", None)
        try:
            db_result = self._database_vacuum(task, config, lambda p, m, d=None: None, started_at)
            results["database"] = db_result.message
        except Exception as e:
            errors.append(f"database: {str(e)}")

        progress_callback(90, "Cleaning old indexes...", None)
        try:
            idx_result = self._cleanup_old_indexes(task, config, lambda p, m, d=None: None, started_at)
            results["old_indexes"] = idx_result.message
        except Exception as e:
            errors.append(f"old_indexes: {str(e)}")

        completed_at = datetime.now()
        duration = (completed_at - started_at).total_seconds()

        progress_callback(100, "Comprehensive cleanup complete", None)

        status = TaskResultStatus.SUCCESS
        if errors:
            status = TaskResultStatus.PARTIAL if results else TaskResultStatus.FAILED

        return TaskResult(
            status=status,
            message=f"Comprehensive cleanup completed: {len(results)} tasks, {len(errors)} errors",
            output_data={
                "results": results,
                "errors": errors,
                "dry_run": dry_run
            },
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration
        )

    def _cleanup_old_indexes(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime
    ) -> TaskResult:
        max_age_days = config.get("max_age_days", 30)
        dry_run = config.get("dry_run", False)

        progress_callback(0, "Cleaning old indexes...", None)

        try:
            from backend.utils.unified_index_manager import get_unified_index_manager

            idx_mgr = get_unified_index_manager()

            if not dry_run:
                progress_callback(50, "Running index cleanup...", None)
                idx_mgr.cleanup_old_indexes(max_age_days)

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            progress_callback(100, "Index cleanup complete", None)

            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message=f"Cleaned indexes older than {max_age_days} days",
                output_data={
                    "max_age_days": max_age_days,
                    "dry_run": dry_run
                },
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration
            )

        except Exception as e:
            logger.error(f"Index cleanup failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Index cleanup failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now()
            )

    def _cleanup_output_files(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable,
        started_at: datetime
    ) -> TaskResult:
        max_age_days = config.get("max_age_days", 30)
        dry_run = config.get("dry_run", False)

        progress_callback(0, "Cleaning old output files...", None)

        try:
            from backend.config import OUTPUT_DIR

            if not os.path.exists(OUTPUT_DIR):
                return TaskResult(
                    status=TaskResultStatus.SUCCESS,
                    message="Output directory does not exist",
                    started_at=started_at,
                    completed_at=datetime.now()
                )

            files_cleaned = 0
            space_freed = 0
            cutoff = datetime.now() - timedelta(days=max_age_days)

            progress_callback(30, f"Scanning {OUTPUT_DIR}...", None)

            for root, dirs, files in os.walk(OUTPUT_DIR):
                for filename in files:
                    filepath = os.path.join(root, filename)
                    try:
                        stat = os.stat(filepath)
                        mtime = datetime.fromtimestamp(stat.st_mtime)

                        if mtime < cutoff:
                            if not dry_run:
                                file_size = stat.st_size
                                os.remove(filepath)
                                space_freed += file_size
                            files_cleaned += 1
                    except (OSError, IOError) as e:
                        logger.warning(f"Could not process {filepath}: {e}")

            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            progress_callback(100, "Output file cleanup complete", None)

            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message=f"Cleaned {files_cleaned} output files, freed {space_freed / 1024 / 1024:.2f} MB",
                output_data={
                    "files_cleaned": files_cleaned,
                    "space_freed_bytes": space_freed,
                    "space_freed_mb": round(space_freed / 1024 / 1024, 2),
                    "max_age_days": max_age_days,
                    "dry_run": dry_run
                },
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration
            )

        except Exception as e:
            logger.error(f"Output file cleanup failed: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Output cleanup failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now()
            )

    def get_estimated_duration(self, config: Dict[str, Any]) -> Optional[int]:
        maintenance_type = config.get("maintenance_type", "comprehensive_cleanup")

        estimates = {
            "cleanup_chat_sessions": 60,
            "cleanup_progress_jobs": 30,
            "cleanup_behavior_logs": 45,
            "cleanup_temp_files": 60,
            "database_vacuum": 120,
            "cache_clear": 30,
            "comprehensive_cleanup": 300,
            "cleanup_old_indexes": 60,
            "cleanup_output_files": 60
        }

        return estimates.get(maintenance_type, 60)

    def can_retry(self, task: Any, error: Exception) -> bool:
        error_msg = str(error).lower()
        if "locked" in error_msg or "corrupt" in error_msg:
            return False
        return super().can_retry(task, error)
