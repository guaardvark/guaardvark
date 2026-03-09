import pytest

try:
    from flask import Flask

    from backend.api.docs_api import docs_bp
    from backend.api.tasks_api import tasks_bp
    from backend.models import Document, Task, db
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(app)
    if docs_bp.name not in app.blueprints:
        app.register_blueprint(docs_bp)
    if tasks_bp.name not in app.blueprints:
        app.register_blueprint(tasks_bp)
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_get_document_details(client):
    with client.application.app_context():
        doc = Document(filename="foo.txt", path="foo.txt")
        db.session.add(doc)
        db.session.commit()
        doc_id = doc.id
    resp = client.get(f"/api/docs/{doc_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["id"] == doc_id
    assert data["filename"] == "foo.txt"


def test_update_document_tags(client):
    with client.application.app_context():
        doc = Document(filename="bar.txt", path="bar.txt")
        db.session.add(doc)
        db.session.commit()
        doc_id = doc.id
    resp = client.put(f"/api/docs/{doc_id}", json={"tags": ["a", "b"]})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["tags"] == ["a", "b"]


def test_create_task_invalid_status(client):
    resp = client.post("/api/tasks", json={"name": "bad", "status": "unknown"})
    assert resp.status_code == 400


def test_get_tasks_status_filter(client):
    with client.application.app_context():
        t1 = Task(name="task1", status="pending")
        t2 = Task(name="task2", status="completed")
        db.session.add_all([t1, t2])
        db.session.commit()
        t1_id = t1.id
        t2_id = t2.id
    resp = client.get("/api/tasks?status=completed")
    assert resp.status_code == 200
    data = resp.get_json()
    ids = [t["id"] for t in data]
    assert t2_id in ids and t1_id not in ids
