"""Files moves and renames onto names already taken on disk.

A document's shown name can differ from its file on disk (a rename changes
the shown name only), and a directory can exist with no folder record. Both
used to pass the record-only checks: the file was overwritten, the folder
was moved inside the directory.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.api.files_api import files_bp
from backend.models import Document as DBDocument, Folder, db


@pytest.fixture
def uploads(tmp_path):
    root = tmp_path / "uploads"
    root.mkdir()
    return root


@pytest.fixture
def app(uploads):
    app = Flask(__name__)
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "UPLOAD_FOLDER": str(uploads),
    })
    db.init_app(app)
    app.register_blueprint(files_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _folder(name, path, parent_id=None):
    folder = Folder(name=name, path=path, parent_id=parent_id)
    db.session.add(folder)
    db.session.flush()
    return folder


def test_moving_a_document_onto_a_renamed_files_disk_name_keeps_both(client, uploads):
    (uploads / "A").mkdir()
    (uploads / "B").mkdir()
    (uploads / "A" / "photo.png").write_text("first")
    (uploads / "B" / "photo.png").write_text("second")
    a = _folder("A", "A")
    b = _folder("B", "B")
    # Renamed in the UI: shown as beach.png, still A/photo.png on disk.
    db.session.add(DBDocument(filename="beach.png", path="A/photo.png", folder_id=a.id,
                              type=".png", size=5, index_status="STORED"))
    moving = DBDocument(filename="photo.png", path="B/photo.png", folder_id=b.id,
                        type=".png", size=6, index_status="STORED")
    db.session.add(moving)
    db.session.commit()

    resp = client.post(f"/api/files/document/{moving.id}/move", json={"destination_path": "A"})

    assert resp.status_code == 200, resp.get_json()
    assert (uploads / "A" / "photo.png").read_text() == "first"
    moved = db.session.get(DBDocument, moving.id)
    assert moved.path != "A/photo.png"
    assert (uploads / moved.path).read_text() == "second"


def test_renaming_a_folder_onto_an_unrecorded_directory_is_refused(client, uploads):
    (uploads / "Old").mkdir()
    (uploads / "Taken").mkdir()
    old = _folder("Old", "Old")
    db.session.commit()

    resp = client.put(f"/api/files/folder/{old.id}", json={"name": "Taken"})

    assert resp.status_code == 409
    assert (uploads / "Old").is_dir()
    assert not (uploads / "Taken" / "Old").exists()


def test_moving_a_folder_onto_an_unrecorded_directory_is_refused(client, uploads):
    (uploads / "Reports").mkdir()
    (uploads / "Archive" / "Reports").mkdir(parents=True)
    reports = _folder("Reports", "Reports")
    _folder("Archive", "Archive")
    db.session.commit()

    resp = client.post(f"/api/files/folder/{reports.id}/move", json={"destination_path": "Archive"})

    assert resp.status_code == 409
    assert (uploads / "Reports").is_dir()
    assert not (uploads / "Archive" / "Reports" / "Reports").exists()


def test_a_plain_folder_rename_still_works(client, uploads):
    (uploads / "Old").mkdir()
    old = _folder("Old", "Old")
    db.session.commit()

    resp = client.put(f"/api/files/folder/{old.id}", json={"name": "New"})

    assert resp.status_code == 200, resp.get_json()
    assert (uploads / "New").is_dir()
