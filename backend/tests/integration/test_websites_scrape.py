import types

import pytest

try:
    from flask import Flask

    from backend.api.websites_api import websites_bp
    from backend.models import Task, Website, db
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(app)
    if websites_bp.name not in app.blueprints:
        app.register_blueprint(websites_bp)
    with app.app_context():
        db.create_all()
        site = Website(url="https://example.com", project_id=None)
        db.session.add(site)
        db.session.commit()
        site_id = site.id

        # The route queues a Task-backed crawl; the Celery hand-off is recorded
        # here so no broker is needed.
        from backend.tasks.unified_task_executor import execute_unified_task

        queued = []

        def fake_apply_async(args=None, queue=None, **_):
            queued.append({"args": args, "queue": queue})
            return types.SimpleNamespace(id="celery-1")

        monkeypatch.setattr(execute_unified_task, "apply_async", fake_apply_async)
        yield app.test_client(), site_id, queued
        db.session.remove()
        db.drop_all()


def test_scrape_route(client):
    c, site_id, queued = client
    resp = c.post(f"/api/websites/{site_id}/scrape")
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["type"] == "website_crawl"
    assert data["target_website"] == "https://example.com"
    assert data["status"] == "queued"
    assert data["job_id"] == f"task_{data['task_id']}"
    assert data["celery_task_id"] == "celery-1"
    assert queued == [{"args": [data["task_id"]], "queue": "default"}]

    task = db.session.get(Task, data["task_id"])
    assert task.website_id == site_id
    assert task.task_handler == "website_crawl"


def test_scrape_route_unknown_website(client):
    c, _, queued = client
    resp = c.post("/api/websites/999999/scrape")
    assert resp.status_code == 404
    assert queued == []
