import io
import os

import pytest
from werkzeug.datastructures import FileStorage

try:
    from flask import Flask

    from backend.api.clients_api import clients_bp
    from backend.models import Client, db
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def client(tmp_path):
    upload_root = tmp_path / "uploads"
    logo_dir = upload_root / "logos"
    app = Flask(__name__)
    app.config.update(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "UPLOAD_FOLDER": str(upload_root),
            "CLIENT_LOGO_FOLDER": str(logo_dir),
        }
    )
    db.init_app(app)
    if clients_bp.name not in app.blueprints:
        app.register_blueprint(clients_bp)
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_upload_logo_stores_relative_path(client, tmp_path):
    with client.application.app_context():
        c = Client(name="Logo Client")
        db.session.add(c)
        db.session.commit()
        cid = c.id

    file_storage = FileStorage(
        stream=io.BytesIO(b"fakeimage"), filename="logo.png", content_type="image/png"
    )
    data = {"file": file_storage}
    resp = client.post(
        f"/api/clients/{cid}/logo", data=data, content_type="multipart/form-data"
    )
    assert resp.status_code == 200
    logo_path = resp.get_json()["logo_path"]
    assert logo_path.startswith("logos/")
    saved_file = tmp_path / "uploads" / logo_path
    assert saved_file.is_file()
    with client.application.app_context():
        client_obj = db.session.get(Client, cid)
        assert client_obj.logo_path == logo_path


def test_upload_logo_missing_file_returns_400(client):
    with client.application.app_context():
        c = Client(name="Logo Client 2")
        db.session.add(c)
        db.session.commit()
        cid = c.id

    resp = client.post(
        f"/api/clients/{cid}/logo", data={}, content_type="multipart/form-data"
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data.get("error") == "Logo file missing. Use form field 'file'."
