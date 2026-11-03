# backend/tasks/backup_tasks.py
"""Automated backup Celery tasks.

The daily backup makes a data backup and then applies the retention
settings to the automatic backups it made earlier. A person's own backups
and pinned backups are never touched.
"""

import logging
from datetime import datetime

from celery import shared_task

logger = logging.getLogger(__name__)

GB = 1024 ** 3

# Settings table keys, with their defaults. Settings → Backups writes them.
SETTING_KEYS = {
    "backup_daily_enabled": "true",
    "backup_keep_last": "7",
    "backup_max_age_days": "30",
    "backup_max_total_gb": "0",
    "backup_min_free_gb": "5",
}


def _read(key: str) -> str:
    from backend.celery_beat_gates import _read_setting
    return _read_setting(key, SETTING_KEYS[key]) or SETTING_KEYS[key]


def _int(key: str) -> int:
    try:
        return max(0, int(float(_read(key))))
    except (TypeError, ValueError):
        return int(SETTING_KEYS[key])


def retention_settings() -> dict:
    """The retention settings as the task will apply them."""
    return {
        "daily_enabled": _read("backup_daily_enabled").strip().lower() in ("true", "1", "yes", "on"),
        "keep_last": _int("backup_keep_last"),
        "max_age_days": _int("backup_max_age_days"),
        "max_total_gb": _int("backup_max_total_gb"),
        "min_free_gb": _int("backup_min_free_gb"),
    }


@shared_task(bind=True, name='maintenance.daily_backup', max_retries=2, default_retry_delay=300)
def daily_backup(self):
    """Create a daily automatic backup, then prune older automatic backups.

    Skips, with a logged reason, when the disk has no room for another
    backup of the usual size plus the configured free-space margin.
    """
    from backend.services import backup_service

    settings = retention_settings()
    if not settings["daily_enabled"]:
        logger.info("[BACKUP] Daily backup is switched off in Settings")
        return {"status": "disabled"}

    room = backup_service.backup_disk_check(settings["min_free_gb"] * GB)
    if not room["ok"]:
        logger.warning(
            "[BACKUP] Daily backup skipped: %.1f GB free, %.1f GB needed "
            "(last backup %.1f GB plus a %d GB margin)",
            room["free"] / GB, room["needed"] / GB,
            room["expected_backup_size"] / GB, settings["min_free_gb"],
        )
        return {"status": "skipped_low_disk", **room}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"auto_daily_{timestamp}"
    try:
        zip_path = backup_service.create_data_backup(name=name)
        logger.info(f"[BACKUP] Daily backup created: {zip_path}")
    except Exception as e:
        logger.error(f"[BACKUP] Daily backup failed: {e}")
        raise self.retry(exc=e)

    removed = []
    try:
        removed = backup_service.prune_auto_backups(
            keep_last=settings["keep_last"],
            max_age_days=settings["max_age_days"],
            max_total_bytes=settings["max_total_gb"] * GB,
        )
        for n in removed:
            logger.info(f"[BACKUP] Removed old automatic backup: {n}")
    except Exception as e:
        logger.warning(f"[BACKUP] Pruning old backups failed (non-fatal): {e}")

    return {"status": "ok", "backup": name, "cleaned": len(removed), "removed": removed}
