import pytest

try:
    from flask import Flask

    from backend.api.clients_api import clients_bp
    from backend.api.projects_api import projects_bp
    from backend.models import Client, db
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(app)
    if projects_bp.name not in app.blueprints:
        app.register_blueprint(projects_bp)
    if clients_bp.name not in app.blueprints:
        app.register_blueprint(clients_bp)
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_get_projects_empty(client):
    resp = client.get("/api/projects/")
    assert resp.status_code == 200
    assert resp.get_json() == []
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_create_and_list_project(client):
    with client.application.app_context():
        owner = Client(name="Owner")
        db.session.add(owner)
        db.session.commit()
        cid = owner.id
    resp = client.post("/api/projects/", json={"name": "Example", "client_id": cid})
    assert resp.status_code == 201
    proj_id = resp.get_json()["id"]
    resp = client.get("/api/projects/")
    assert resp.status_code == 200
    ids = [p["id"] for p in resp.get_json()]
    assert proj_id in ids
