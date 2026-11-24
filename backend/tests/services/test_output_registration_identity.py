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


@pytest.mark.parametrize("secret", [".swarm_internal_secret", "interconnector/peer.key"])
def test_a_file_under_data_outside_the_output_folders_is_refused(root, secret):
    target = root / "data" / secret
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("secret")

    assert reg.output_location_problem(target)
    assert reg.register_file(str(target), "Videos", filename="x.mp4") is None
    assert Document.query.count() == 0


def test_a_symlink_in_an_output_folder_pointing_out_is_refused(root):
    (root / "data" / ".swarm_internal_secret").write_text("secret")
    link = root / "data" / "outputs" / "videos" / "editor-renders" / "take.mp4"
    link.symlink_to(root / "data" / ".swarm_internal_secret")

    assert reg.register_file(str(link), "Videos") is None
    assert Document.query.count() == 0


def test_a_traversal_path_out_of_the_output_folder_is_refused(root):
    (root / "data" / ".swarm_internal_secret").write_text("secret")
    sneaky = str(root / "data" / "outputs" / "videos" / ".." / ".." / ".swarm_internal_secret")

    assert reg.register_file(sneaky, "Videos", filename="x.mp4") is None
    assert Document.query.count() == 0


def test_the_register_endpoint_answers_400_for_a_file_outside_the_output_folders(root):
    from flask import current_app
    from backend.api.outputs_api import outputs_bp
    (root / "data" / ".swarm_internal_secret").write_text("secret")
    current_app.register_blueprint(outputs_bp)

    r = current_app.test_client().post(
        "/api/outputs/register",
        json={"physical_path": str(root / "data" / ".swarm_internal_secret"), "folder_name": "Videos"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert r.status_code == 400
    assert Document.query.count() == 0


def test_an_ambiguous_folder_name_row_is_not_handed_to_another_file(root):
    videos = root / "data" / "outputs" / "videos"
    render, overlay = videos / "editor-renders" / "final.mp4", videos / "text-overlay" / "final.mp4"
    render.write_bytes(b"render")
    overlay.write_bytes(b"overlay")
    old = Document(filename="final.mp4", path="Videos/final.mp4", type=".mp4", index_status="STORED")
    db.session.add(old)
    db.session.commit()

    doc = reg.register_file(str(overlay), "Videos")

    assert doc.id != old.id
    assert _bytes(doc) == b"overlay"


def test_a_name_taken_by_a_parallel_registration_is_retried(root, monkeypatch):
    from backend.utils import filename_resolver
    overlays = root / "data" / "outputs" / "videos" / "text-overlay"
    first, second = overlays / "clip_001.mp4", overlays / "clip_002.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    a = reg.register_file(str(first), "Videos", filename="clip-text.mp4", subfolder_name="Text Overlay")
    real = filename_resolver.resolve_filename
    calls = []

    def stale_once(folder_id, name, *args, **kwargs):
        calls.append(name)
        return name if len(calls) == 1 else real(folder_id, name, *args, **kwargs)

    monkeypatch.setattr(filename_resolver, "resolve_filename", stale_once)
    b = reg.register_file(str(second), "Videos", filename="clip-text.mp4", subfolder_name="Text Overlay")

    assert b is not None and b.id != a.id
    assert b.filename == "clip-text (2).mp4"
    assert len(calls) == 2


def test_a_song_filed_in_audio_from_an_output_folder_is_listed(root):
    from backend.services.media_recipe import list_audio
    (root / "data" / "outputs" / "audio").mkdir(parents=True)
    song = root / "data" / "outputs" / "audio" / "song.wav"
    song.write_bytes(b"RIFF")

    doc = reg.register_file(str(song), "Audio", file_metadata={"source": "music_generation"})

    assert doc.path == "data/outputs/audio/song.wav"
    assert [row["document_id"] for row in list_audio()] == [doc.id]
