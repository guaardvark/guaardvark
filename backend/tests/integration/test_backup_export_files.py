import io
import json
import os
import zipfile

import pytest
from flask import Flask

from backend.api.meta_api import meta_bp
from backend.api.upload_api import upload_bp
from backend.models import Document, db


@pytest.fixture
def client(tmp_path):
    upload_dir = tmp_path / "uploads"
    app = Flask(__name__)
    app.config.update(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "UPLOAD_FOLDER": str(upload_dir),
        }
    )
    db.init_app(app)
    if meta_bp.name not in app.blueprints:
        app.register_blueprint(meta_bp)
    if upload_bp.name not in app.blueprints:
        app.register_blueprint(upload_bp)
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_export_includes_uploaded_file(client, tmp_path):
    data = {"file": (io.BytesIO(b"hello"), "test.txt")}
    resp = client.post("/api/upload/", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    doc_id = resp.get_json()["document_id"]

    payload = {"entities": ["documents"], "include_files": True}
    resp = client.post("/api/meta/backup/export", json=payload)
    assert resp.status_code == 200

    z = zipfile.ZipFile(io.BytesIO(resp.data))
    assert "backup.json" in z.namelist()
    backup_data = json.loads(z.read("backup.json").decode())
    assert backup_data["documents"][0]["id"] == doc_id
    file_path = backup_data["documents"][0]["path"]
    assert file_path.startswith("files/")
    assert file_path in z.namelist()
    z.close()
