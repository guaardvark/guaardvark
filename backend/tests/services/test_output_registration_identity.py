"""register_file keys a row on the file it registers, not on its display name.

In-memory SQLite with a scratch install root holding uploads and outputs.
"""

from __future__ import annotations

import pytest
from flask import Flask

from backend import config
from backend.models import Document, db
from backend.services import output_registration as reg
from backend.services.document_path_resolver import resolve_document_path


@pytest.fixture
def root(monkeypatch, tmp_path):
    uploads = tmp_path / "data" / "uploads"
    outputs = tmp_path / "data" / "outputs"
    (outputs / "videos" / "text-overlay").mkdir(parents=True)
    (outputs / "videos" / "editor-renders").mkdir(parents=True)
    uploads.mkdir(parents=True)
    monkeypatch.setattr(config, "GUAARDVARK_ROOT", tmp_path)
    monkeypatch.setattr(config, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(config, "OUTPUT_DIR", str(outputs))
    monkeypatch.setattr(reg, "UPLOAD_DIR", str(uploads))
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      UPLOAD_FOLDER=str(uploads))
    db.init_app(app)
    with app.app_context():
        db.create_all()
        reg.ensure_default_folders()
        yield tmp_path
        db.session.remove()
        db.drop_all()


def _bytes(doc) -> bytes:
    found = resolve_document_path(doc)
    assert found is not None, f"row {doc.path!r} does not resolve"
    return found.read_bytes()


def test_two_files_sharing_a_display_name_are_two_rows(root):
    overlays = root / "data" / "outputs" / "videos" / "text-overlay"
    first, second = overlays / "clip_001.mp4", overlays / "clip_002.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    a = reg.register_file(str(first), "Videos", filename="clip-text.mp4", subfolder_name="Text Overlay")
    b = reg.register_file(str(second), "Videos", filename="clip-text.mp4", subfolder_name="Text Overlay")

    assert a.id != b.id
    assert a.folder_id == b.folder_id
    assert {a.filename, b.filename} == {"clip-text.mp4", "clip-text (2).mp4"}
    assert (_bytes(a), _bytes(b)) == (b"first", b"second")
    assert Document.query.count() == 2


def test_two_files_sharing_a_file_name_in_different_folders_are_two_rows(root):
    videos = root / "data" / "outputs" / "videos"
    render, overlay = videos / "editor-renders" / "final.mp4", videos / "text-overlay" / "final.mp4"
    render.write_bytes(b"render")
    overlay.write_bytes(b"overlay")

    a = reg.register_file(str(render), "Videos")
    b = reg.register_file(str(overlay), "Videos")

    assert a.id != b.id
    assert (_bytes(a), _bytes(b)) == (b"render", b"overlay")


def test_the_same_file_twice_is_one_row(root):
    render = root / "data" / "outputs" / "videos" / "editor-renders" / "take_ab12cd34.mp4"
    render.write_bytes(b"render")

    a = reg.register_file(str(render), "Videos", subfolder_name="Editor Renders")
    b = reg.register_file(str(render), "Videos", subfolder_name="Editor Renders")

    assert a.id == b.id
    assert a.path == "data/outputs/videos/editor-renders/take_ab12cd34.mp4"
    assert Document.query.count() == 1


def test_a_row_under_the_folder_name_path_for_the_same_file_is_reused(root):
    render = root / "data" / "outputs" / "videos" / "editor-renders" / "take.mp4"
    render.write_bytes(b"render")
    folder = reg.ensure_subfolder("Videos", "Editor Renders")
    old = Document(filename="take.mp4", path="Videos/Editor Renders/take.mp4", type=".mp4",
                   folder_id=folder.id, index_status="STORED")
    db.session.add(old)
    db.session.commit()

    doc = reg.register_file(str(render), "Videos", subfolder_name="Editor Renders")

    assert doc.id == old.id
    assert Document.query.count() == 1


def test_a_file_inside_uploads_keeps_its_uploads_relative_path(root):
    batch = root / "data" / "uploads" / "Images" / "ImageBatch_01"
    batch.mkdir(parents=True)
    image = batch / "img_001.png"
    image.write_bytes(b"png")

    a = reg.register_file(str(image), "Images", subfolder_name="ImageBatch_01")
    b = reg.register_file(str(image), "Images", subfolder_name="ImageBatch_01")

    assert a.id == b.id
    assert a.path == "Images/ImageBatch_01/img_001.png"
    assert a.filename == "img_001.png"
    assert _bytes(a) == b"png"


def test_unsafe_folder_names_are_still_refused(root):
    render = root / "data" / "outputs" / "videos" / "editor-renders" / "take.mp4"
    render.write_bytes(b"render")

    assert reg.register_file(str(render), "Videos", filename="../take.mp4") is None
    assert reg.register_file(str(render), "Videos", subfolder_name="..") is None
    assert Document.query.count() == 0
