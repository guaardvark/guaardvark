import pytest
import json

try:
    from flask import Flask
    from backend.models import db, Subject
    from backend.api.cast_library_api import bp as cast_library_bp
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(app)
    app.register_blueprint(cast_library_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def test_list_empty_cast_library(client):
    resp = client.get("/api/cast-library")
    assert resp.status_code == 200
    assert resp.get_json() == {"subjects": []}


def test_create_subject_returns_201(client):
    resp = client.post("/api/cast-library/subjects", json={
        "kind": "character", "name": "Dean", "description": "the protagonist",
    })
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["name"] == "Dean"
    assert data["kind"] == "character"
    assert data["training_status"] == "untrained"


def test_create_subject_validates_kind(client):
    resp = client.post("/api/cast-library/subjects", json={
        "kind": "alien", "name": "X", "description": "y",
    })
    assert resp.status_code == 400


def test_list_after_create(client):
    client.post("/api/cast-library/subjects", json={
        "kind": "character", "name": "A", "description": "x",
    })
    client.post("/api/cast-library/subjects", json={
        "kind": "environment", "name": "B", "description": "y",
    })
    resp = client.get("/api/cast-library")
    data = resp.get_json()
    assert len(data["subjects"]) == 2


def test_delete_subject(client, app):
    create = client.post("/api/cast-library/subjects", json={
        "kind": "character", "name": "Dean", "description": "x",
    })
    subj_id = create.get_json()["id"]
    delete = client.delete(f"/api/cast-library/subjects/{subj_id}")
    assert delete.status_code == 204
    listing = client.get("/api/cast-library")
    assert listing.get_json()["subjects"] == []


def test_delete_unknown_subject_404(client):
    resp = client.delete("/api/cast-library/subjects/9999")
    assert resp.status_code == 404
