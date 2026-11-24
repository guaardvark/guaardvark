"""GET /api/files/thumbnail — serve the cached thumbnail, not the full image.

The batch image generator writes <batch>/images/<file> and
<batch>/thumbnails/<stem>.jpg; the endpoint used to look only in
<batch>/images/thumbnails/ and fell through to the full-size file.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.api.files_api import files_bp
from backend.models import Document as DBDocument, db

FULL = b"full-size-image-bytes" * 50
THUMB = b"thumb"


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


def _batch(uploads, name="ImageBatch_10-10-2026_151527_134"):
    batch = uploads / "Images" / name
    (batch / "images").mkdir(parents=True)
    (batch / "thumbnails").mkdir()
    (batch / "images" / "ImageGen_001.png").write_bytes(FULL)
    return batch


def test_batch_layout_serves_the_thumbnail_one_level_up(client, uploads):
    batch = _batch(uploads)
    (batch / "thumbnails" / "ImageGen_001.jpg").write_bytes(THUMB)

    resp = client.get("/api/files/thumbnail?path=Images/ImageBatch_10-10-2026_151527_134/images/ImageGen_001.png")

    assert resp.status_code == 200
    assert resp.mimetype == "image/jpeg"
    assert resp.data == THUMB


def test_batch_layout_by_document_id(client, uploads):
    batch = _batch(uploads)
    (batch / "thumbnails" / "ImageGen_001.jpg").write_bytes(THUMB)
    doc = DBDocument(filename="ImageGen_001.png",
                     path="Images/ImageBatch_10-10-2026_151527_134/images/ImageGen_001.png",
                     type=".png", size=len(FULL), index_status="STORED")
    db.session.add(doc)
    db.session.commit()

    resp = client.get(f"/api/files/thumbnail?document_id={doc.id}")

    assert resp.status_code == 200
    assert resp.mimetype == "image/jpeg"
    assert resp.data == THUMB


def test_sibling_thumbnails_dir_still_wins(client, uploads):
    """Uploads and copies keep thumbnails/ beside the file; that stays first."""
    batch = _batch(uploads)
    (batch / "images" / "thumbnails").mkdir()
    (batch / "images" / "thumbnails" / "ImageGen_001.jpg").write_bytes(b"sibling")
    (batch / "thumbnails" / "ImageGen_001.jpg").write_bytes(THUMB)

    resp = client.get("/api/files/thumbnail?path=Images/ImageBatch_10-10-2026_151527_134/images/ImageGen_001.png")

    assert resp.data == b"sibling"


def test_png_thumbnail_one_level_up(client, uploads):
    batch = _batch(uploads)
    (batch / "thumbnails" / "ImageGen_001.png").write_bytes(THUMB)

    resp = client.get("/api/files/thumbnail?path=Images/ImageBatch_10-10-2026_151527_134/images/ImageGen_001.png")

    assert resp.mimetype == "image/png"
    assert resp.data == THUMB


def test_no_thumbnail_anywhere_falls_back_to_the_file(client, uploads):
    _batch(uploads)

    resp = client.get("/api/files/thumbnail?path=Images/ImageBatch_10-10-2026_151527_134/images/ImageGen_001.png")

    assert resp.status_code == 200
    assert resp.data == FULL


def test_a_file_outside_an_images_dir_does_not_look_one_level_up(client, uploads):
    """Images/thumbnails/ belongs to the files directly in Images/, not to a
    file in Images/Trip/ (whose own thumbnails dir is Images/Trip/thumbnails/)."""
    (uploads / "Images" / "Trip").mkdir(parents=True)
    (uploads / "Images" / "thumbnails").mkdir()
    (uploads / "Images" / "Trip" / "photo.png").write_bytes(FULL)
    (uploads / "Images" / "thumbnails" / "photo.jpg").write_bytes(b"someone else's")

    resp = client.get("/api/files/thumbnail?path=Images/Trip/photo.png")

    assert resp.data == FULL


def test_document_id_and_path_forms_agree_when_only_the_app_names_the_uploads_folder(client, uploads):
    """This app sets UPLOAD_FOLDER and leaves backend.config.UPLOAD_DIR at its
    own value, so the two differ here. Both forms of the route must still find
    the same file: the resolver reads the folder the way the Files routes do."""
    from backend import config
    assert str(uploads) != str(config.UPLOAD_DIR)
    batch = _batch(uploads)
    (batch / "thumbnails" / "ImageGen_001.jpg").write_bytes(THUMB)
    doc = DBDocument(filename="ImageGen_001.png",
                     path="Images/ImageBatch_10-10-2026_151527_134/images/ImageGen_001.png",
                     type=".png", size=len(FULL), index_status="STORED")
    db.session.add(doc)
    db.session.commit()

    by_path = client.get("/api/files/thumbnail?path=" + doc.path)
    by_id = client.get(f"/api/files/thumbnail?document_id={doc.id}")

    assert by_path.status_code == by_id.status_code == 200
    assert by_path.data == by_id.data == THUMB


def test_a_row_naming_a_file_outside_uploads_and_outputs_is_still_refused(client, uploads, tmp_path):
    secret = tmp_path / "elsewhere" / "secret.png"
    secret.parent.mkdir()
    secret.write_bytes(b"not for serving")
    doc = DBDocument(filename="secret.png", path=str(secret), type=".png", size=5, index_status="STORED")
    db.session.add(doc)
    db.session.commit()

    assert client.get(f"/api/files/thumbnail?document_id={doc.id}").status_code == 404
