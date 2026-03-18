"""Celery periodic tasks for self-improvement."""
import logging
from celery import Celery

logger = logging.getLogger(__name__)


def create_self_improvement_tasks(celery_app: Celery):
    @celery_app.task(name="self_improvement.scheduled_check")
    def scheduled_self_check():
        """Periodic self-improvement check."""
        try:
            from backend.app import create_app
            app = create_app()
            with app.app_context():
                from backend.services.self_improvement_service import get_self_improvement_service
                service = get_self_improvement_service()
                result = service.run_self_check()
                logger.info(f"Scheduled self-check result: {result}")
                return result
        except Exception as e:
            logger.error(f"Scheduled self-check failed: {e}", exc_info=True)
            return {"error": str(e)}

    @celery_app.task(name="self_improvement.uncle_advice")
    def scheduled_uncle_advice():
        """Periodic Uncle Claude advice check."""
        try:
            from backend.app import create_app
            app = create_app()
            with app.app_context():
                from backend.services.claude_advisor_service import get_claude_advisor
                advisor = get_claude_advisor()
                if not advisor.is_available():
                    return {"skipped": True, "reason": "Claude not available"}

                import subprocess, os
                system_state = {
                    "timestamp": __import__("datetime").datetime.now().isoformat(),
                    "node_id": os.environ.get("GUAARDVARK_NODE_ID", "local"),
                }
                try:
                    gpu = subprocess.run(
                        ["nvidia-smi", "--query-gpu=memory.used,memory.total,name", "--format=csv,noheader"],
                        capture_output=True, text=True, timeout=5
                    )
                    system_state["gpu"] = gpu.stdout.strip() if gpu.returncode == 0 else "unavailable"
                except Exception:
                    system_state["gpu"] = "unavailable"

                result = advisor.advise(system_state)
                logger.info(f"Uncle advice result: {result}")
                return result
        except Exception as e:
            logger.error(f"Uncle advice task failed: {e}", exc_info=True)
            return {"error": str(e)}

    @celery_app.task(name="self_improvement.run_check_async", bind=True)
    def run_check_async(self):
        """On-demand self-improvement check (dispatched from API)."""
        try:
            app = celery_app.flask_app if hasattr(celery_app, 'flask_app') else None
            if not app:
                from backend.app import create_app
                app = create_app()
            with app.app_context():
                from backend.services.self_improvement_service import get_self_improvement_service
                service = get_self_improvement_service()
                result = service.run_self_check()
                logger.info(f"Async self-check result: {result}")
                return result
        except Exception as e:
            logger.error(f"Async self-check failed: {e}", exc_info=True)
            return {"error": str(e)}

    @celery_app.task(name="self_improvement.run_directed_async", bind=True)
    def run_directed_async(self, task_description: str):
        """On-demand directed improvement (dispatched from API)."""
        try:
            app = celery_app.flask_app if hasattr(celery_app, 'flask_app') else None
            if not app:
                from backend.app import create_app
                app = create_app()
            with app.app_context():
                from backend.services.self_improvement_service import get_self_improvement_service
                service = get_self_improvement_service()
                result = service.submit_directed_task(task_description)
                logger.info(f"Async directed task result: {result}")
                return result
        except Exception as e:
            logger.error(f"Async directed task failed: {e}", exc_info=True)
            return {"error": str(e)}


def schedule_self_improvement_tasks(celery_app: Celery):
    from celery.schedules import crontab

    interval_hours = int(__import__("os").environ.get("GUAARDVARK_SELF_IMPROVEMENT_INTERVAL", "6"))

    celery_app.conf.beat_schedule = {
        **getattr(celery_app.conf, "beat_schedule", {}),
        "self-improvement-check": {
            "task": "self_improvement.scheduled_check",
            "schedule": crontab(minute=0, hour=f"*/{interval_hours}"),
        },
        "uncle-claude-advice": {
            "task": "self_improvement.uncle_advice",
            "schedule": crontab(minute=30, hour="*/12"),  # Twice daily
        },
    }
