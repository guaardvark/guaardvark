"""Celery tasks register themselves at import. Schedule them from
extension.json "beat"; do not edit backend/celery_app.py."""
from celery import shared_task


@shared_task(name="template.heartbeat")
def heartbeat():
    return "ok"
