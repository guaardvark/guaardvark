"""
Background tasks for cleaning up chat data and progress jobs to prevent memory leaks and system bloat.
Enhanced with progress jobs cleanup integration.
"""
import logging
from datetime import datetime, timezone, timedelta

from celery import Celery

logger = logging.getLogger(__name__)


def create_cleanup_tasks(celery_app: Celery):
    """Create cleanup tasks for the given Celery app."""
    
    @celery_app.task(name="cleanup_old_chat_sessions")
    def cleanup_old_chat_sessions(max_age_days: int = 30, max_sessions: int = 1000):
        """Background task to clean up old chat sessions."""
        from backend.utils.chat_utils import cleanup_old_sessions
        
        logger.info(f"Starting background cleanup of chat sessions (max_age_days={max_age_days}, max_sessions={max_sessions})")
        
        try:
            result = cleanup_old_sessions(max_age_days, max_sessions)
            
            if result.get("success"):
                logger.info(
                    f"Background cleanup completed successfully: "
                    f"{result['sessions_deleted']} sessions deleted, "
                    f"{result['messages_deleted']} messages deleted"
                )
            else:
                logger.error(f"Background cleanup failed: {result.get('error', 'Unknown error')}")
                
            return result
            
        except Exception as e:
            logger.error(f"Background cleanup task failed: {e}", exc_info=True)
            return {"error": str(e)}
    
    @celery_app.task(name="cleanup_user_behavior_log")
    def cleanup_user_behavior_log_task(max_size_mb: int = 100, max_age_days: int = 90):
        """Background task to clean up user behavior log."""
        from backend.utils.chat_utils import cleanup_user_behavior_log
        
        logger.info(f"Starting background cleanup of user behavior log (max_size_mb={max_size_mb}, max_age_days={max_age_days})")
        
        try:
            result = cleanup_user_behavior_log(max_size_mb, max_age_days)
            
            if result.get("success"):
                logger.info(
                    f"Background behavior log cleanup completed: "
                    f"{result['entries_removed']} entries removed, "
                    f"size reduced from {result['size_before_mb']} MB to {result['size_after_mb']} MB"
                )
            else:
                logger.info(f"Behavior log cleanup: {result.get('message', 'No action needed')}")
                
            return result
            
        except Exception as e:
            logger.error(f"Background behavior log cleanup task failed: {e}", exc_info=True)
            return {"error": str(e)}
    
    @celery_app.task(name="cleanup_progress_jobs")
    def cleanup_progress_jobs_task(max_age_hours: int = 24, clean_completed: bool = False, max_completed_age_days: int = 7):
        """Background task to clean up stuck and old progress jobs."""
        import subprocess
        import sys
        from pathlib import Path
        
        logger.info(f"Starting background cleanup of progress jobs (max_age_hours={max_age_hours}, clean_completed={clean_completed})")
        
        try:
            # Path to the cleanup script
            script_path = Path(__file__).parent.parent.parent / "scripts" / "cleanup_stuck_progress_jobs.py"
            
            if not script_path.exists():
                logger.error(f"Progress jobs cleanup script not found: {script_path}")
                return {"error": "Cleanup script not found"}
            
            # Build command
            cmd = [sys.executable, str(script_path), "--execute"]
            if clean_completed:
                cmd.extend(["--clean-completed", "--max-age-days", str(max_completed_age_days)])
            
            # Run the cleanup script
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60  # 1 minute timeout
            )
            
            if result.returncode == 0:
                # Parse output for cleaned count
                cleaned_count = 0
                for line in result.stdout.split('\n'):
                    if "Cleaned up" in line and "orphaned jobs" in line:
                        try:
                            parts = line.split()
                            for i, part in enumerate(parts):
                                if part.isdigit() and i + 1 < len(parts) and "orphaned" in parts[i + 1]:
                                    cleaned_count = int(part)
                                    break
                        except (ValueError, IndexError):
                            pass
                
                logger.info(f"Background progress jobs cleanup completed: {cleaned_count} orphaned jobs removed")
                return {
                    "success": True,
                    "cleaned_count": cleaned_count,
                    "output": result.stdout
                }
            else:
                logger.error(f"Progress jobs cleanup script failed: {result.stderr}")
                return {"error": f"Cleanup script failed: {result.stderr}"}
                
        except subprocess.TimeoutExpired:
            logger.error("Progress jobs cleanup script timed out")
            return {"error": "Cleanup script timed out"}
        except Exception as e:
            logger.error(f"Background progress jobs cleanup task failed: {e}", exc_info=True)
            return {"error": str(e)}
    
    @celery_app.task(name="periodic_comprehensive_cleanup")
    def periodic_comprehensive_cleanup():
        """Enhanced comprehensive periodic cleanup of all system data."""
        logger.info("Starting periodic comprehensive system cleanup")
        
        results = {
            "session_cleanup": cleanup_old_chat_sessions.delay(max_age_days=7, max_sessions=500),
            "behavior_log_cleanup": cleanup_user_behavior_log_task.delay(max_size_mb=50, max_age_days=30),
            "progress_jobs_cleanup": cleanup_progress_jobs_task.delay(max_age_hours=24, clean_completed=True, max_completed_age_days=7),
        }
        
        return {"status": "comprehensive_cleanup_tasks_scheduled", "task_ids": [str(r.id) for r in results.values()]}
    
    # Legacy task name for backward compatibility
    @celery_app.task(name="periodic_chat_cleanup")
    def periodic_chat_cleanup():
        """Comprehensive periodic cleanup of all chat data (legacy name - use periodic_comprehensive_cleanup)."""
        logger.info("Starting periodic comprehensive chat cleanup (legacy)")
        
        results = {
            "session_cleanup": cleanup_old_chat_sessions.delay(max_age_days=7, max_sessions=500),
            "behavior_log_cleanup": cleanup_user_behavior_log_task.delay(max_size_mb=50, max_age_days=30),
        }
        
        return {"status": "cleanup_tasks_scheduled", "task_ids": [str(r.id) for r in results.values()]}


def schedule_periodic_cleanup(celery_app: Celery):
    """Enhanced periodic cleanup scheduling with progress jobs included."""
    from celery.schedules import crontab
    
    # Set up periodic tasks
    celery_app.conf.beat_schedule = {
        **celery_app.conf.beat_schedule,
        'cleanup-old-chat-sessions': {
            'task': 'cleanup_old_chat_sessions',
            'schedule': crontab(hour=2, minute=0),  # Daily at 2 AM
            'args': (30, 1000),  # 30 days, max 1000 sessions
        },
        'cleanup-user-behavior-log': {
            'task': 'cleanup_user_behavior_log',
            'schedule': crontab(hour=2, minute=30),  # Daily at 2:30 AM
            'args': (100, 90),  # 100 MB, 90 days
        },
        'cleanup-progress-jobs': {
            'task': 'cleanup_progress_jobs',
            'schedule': crontab(hour=1, minute=0),  # Daily at 1 AM
            'args': (24, False, 7),  # 24 hours for stuck jobs, don't clean completed jobs, 7 days for completed
        },
        'periodic-comprehensive-cleanup': {
            'task': 'periodic_comprehensive_cleanup',
            'schedule': crontab(hour=3, minute=0, day_of_week=1),  # Weekly on Monday at 3 AM
        },
        # Legacy task for backward compatibility
        'periodic-chat-cleanup': {
            'task': 'periodic_chat_cleanup',
            'schedule': crontab(hour=4, minute=0, day_of_week=0),  # Weekly on Sunday at 4 AM (legacy)
        },
    }
    
    logger.info("Scheduled enhanced periodic cleanup tasks (chat data + progress jobs)") 