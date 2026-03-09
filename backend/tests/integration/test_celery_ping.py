import pytest

from backend.celery_tasks_isolated import celery, ping


def test_celery_ping(monkeypatch):
    monkeypatch.setitem(celery.conf, "task_always_eager", True)
    result = ping.delay()
    assert result.get(timeout=5) == "pong"
