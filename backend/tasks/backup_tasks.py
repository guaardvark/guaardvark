# backend/tasks/backup_tasks.py
"""Automated backup Celery tasks.

Provides a daily backup task that creates a full backup and
cleans up auto-generated backups older than 30 days.
"""

import logging
from datetime import datetime, timedelta

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, name='maintenance.daily_backup', max_retries=2, default_retry_delay=300)
def daily_backup(self):
    """Create a daily automatic backup and clean up old auto-backups.

    Scheduled via Celery Beat (every 24 hours). Creates a full backup
    with an 'auto_daily_' prefix and removes auto backups older than
    30 days to prevent disk bloat.
    """
    from backend.services import backup_service

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"auto_daily_{timestamp}"

    # Create backup
    try:
        zip_path = backup_service.create_data_backup(name=name)
        logger.info(f"[BACKUP] Daily backup created: {zip_path}")
    except Exception as e:
        logger.error(f"[BACKUP] Daily backup failed: {e}")
        raise self.retry(exc=e)

    # Clean up old auto backups (>30 days)
    cleaned = 0
    try:
        cutoff = datetime.now() - timedelta(days=30)
        for backup_name in backup_service.list_backups():
            if not backup_name.startswith("auto_daily_"):
                continue
            # Extract YYYYMMDD from auto_daily_YYYYMMDD_HHMMSS.zip
            date_part = backup_name[len("auto_daily_"):][:8]
            try:
                backup_date = datetime.strptime(date_part, "%Y%m%d")
                if backup_date < cutoff:
                    backup_service.delete_backup(backup_name)
                    cleaned += 1
                    logger.info(f"[BACKUP] Cleaned up old backup: {backup_name}")
            except ValueError:
                continue
    except Exception as e:
        logger.warning(f"[BACKUP] Cleanup of old backups failed (non-fatal): {e}")

    return {"status": "ok", "backup": name, "cleaned": cleaned}
