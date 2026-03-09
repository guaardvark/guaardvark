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


def test_docs_list_empty(client):
    resp = client.get("/api/docs/")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["documents"] == []
    assert data["total"] == 0


def test_document_status_endpoint(client):
    with client.application.app_context():
        doc = Document(filename="test.txt", path="test.txt")
        db.session.add(doc)
        db.session.commit()
        doc_id = doc.id
    resp = client.get(f"/api/docs/{doc_id}/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["doc_id"] == doc_id


def test_tasks_crud_flow(client):
    resp = client.post("/api/tasks", json={"name": "task1"})
    assert resp.status_code == 201
    task = resp.get_json()
    task_id = task["id"]

    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    tasks = resp.get_json()
    assert any(t["id"] == task_id for t in tasks)

    resp = client.put(f"/api/tasks/{task_id}", json={"status": "completed"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "completed"

    resp = client.delete(f"/api/tasks/{task_id}")
    assert resp.status_code == 200

    resp = client.get("/api/tasks")
    remaining_ids = [t["id"] for t in resp.get_json()]
    assert task_id not in remaining_ids
