"""Files move and delete, and the overlay record repair, for generated files
that live outside the uploads folder.

In-memory SQLite and a scratch install root.
"""
from __future__ import annotations

import os
import time

import pytest
from flask import Flask

from backend import config
from backend.models import Document, Folder, db
from backend.services import output_registration as reg
from backend.services import overlay_record_repair as repair
from backend.services.document_path_resolver import file_outside_uploads, resolve_document_path


@pytest.fixture
def install(monkeypatch, tmp_path):
    uploads = tmp_path / "data" / "uploads"
    outputs = tmp_path / "data" / "outputs"
    overlays = outputs / "videos" / "text-overlay"
    overlays.mkdir(parents=True)
    uploads.mkdir(parents=True)
    monkeypatch.setattr(config, "GUAARDVARK_ROOT", tmp_path)
    monkeypatch.setattr(config, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(config, "OUTPUT_DIR", str(outputs))
    monkeypatch.setattr(reg, "UPLOAD_DIR", str(uploads))
    from backend.api import files_api

    monkeypatch.setattr(files_api, "get_upload_base_path", lambda: uploads)
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:", UPLOAD_FOLDER=str(uploads))
    db.init_app(app)
    app.register_blueprint(files_api.files_bp)
    with app.app_context():
        db.create_all()
        reg.ensure_default_folders()
        yield tmp_path, overlays, app.test_client()
        db.session.remove()
        db.drop_all()


def _overlay(overlays, name, data=b"overlay"):
    path = overlays / name
    path.write_bytes(data)
    return reg.register_file(str(path), "Videos", filename="clip-text.mp4", subfolder_name="Text Overlay")


def test_moving_an_outside_file_keeps_the_record_on_its_file(install):
    root, overlays, client = install
    doc = _overlay(overlays, "clip_001.mp4")
    stored = doc.path
    db.session.add(Folder(name="Keepers", path="Keepers"))
    db.session.commit()

    answer = client.post(f"/api/files/document/{doc.id}/move", json={"destination_path": "Keepers"})

    assert answer.status_code == 200, answer.get_json()
    moved = db.session.get(Document, doc.id)
    assert moved.path == stored
    assert moved.folder_id == Folder.query.filter_by(path="Keepers").one().id
    assert resolve_document_path(moved).read_bytes() == b"overlay"
    assert (overlays / "clip_001.mp4").is_file()


def test_deleting_an_outside_file_removes_it_from_disk(install):
    root, overlays, client = install
    doc = _overlay(overlays, "clip_001.mp4")

    answer = client.delete(f"/api/files/document/{doc.id}")

    assert answer.status_code == 200, answer.get_json()
    assert db.session.get(Document, doc.id) is None
    assert not (overlays / "clip_001.mp4").exists()


def test_a_record_that_only_shares_a_name_never_deletes_the_file(install):
    root, overlays, client = install
    (overlays / "clip_001.mp4").write_bytes(b"someone else's")
    folder = Folder.query.filter_by(path="Videos").first()
    row = Document(filename="clip_001.mp4", path="Videos/Text Overlay/clip_001.mp4", type=".mp4",
                   folder_id=folder.id if folder else None)
    db.session.add(row)
    db.session.commit()
    assert file_outside_uploads(row) is None

    client.delete(f"/api/files/document/{row.id}")

    assert (overlays / "clip_001.mp4").read_bytes() == b"someone else's"


def _old_row(name="clip-text.mp4", source="clip.mp4", doc_id=None):
    row = Document(filename=name, path=f"{repair.OLD_PATH_PREFIX}{name}", type=".mp4",
                   file_metadata=f'{{"source_filename": "{source}", "overlay_text": "hi"}}')
    db.session.add(row)
    db.session.commit()
    return row


def _plan(root, overlays):
    rows = [{"id": d.id, "filename": d.filename, "path": d.path, "file_metadata": d.file_metadata}
            for d in Document.query.all()]
    return repair.plan_repair(rows, root=root, upload_dir=root / "data" / "uploads", overlay_dirs=[overlays])


def test_repair_repoints_a_record_with_one_fitting_file_and_changes_nothing_until_applied(install):
    root, overlays, _client = install
    (overlays / "clip_001.mp4").write_bytes(b"one")
    (overlays / "other_001.mp4").write_bytes(b"not this clip")
    row = _old_row()
    assert resolve_document_path(row) is None

    plan = _plan(root, overlays)

    assert [(p.action, p.new_path) for p in plan] == [("repoint", "data/outputs/videos/text-overlay/clip_001.mp4")]
    assert db.session.get(Document, row.id).path == f"{repair.OLD_PATH_PREFIX}clip-text.mp4"

    assert repair.apply_plan(plan, db.session, Document) == {"repointed": 1, "skipped": 0}
    assert resolve_document_path(db.session.get(Document, row.id)).read_bytes() == b"one"
    assert _plan(root, overlays) == []


def test_repair_lists_but_does_not_pick_when_several_files_fit(install):
    root, overlays, _client = install
    (overlays / "clip_001.mp4").write_bytes(b"one")
    time.sleep(0.01)
    (overlays / "clip_002.mp4").write_bytes(b"two")
    os.utime(overlays / "clip_001.mp4", (1, 1))
    row = _old_row()

    plan = _plan(root, overlays)

    assert plan[0].action == "choose" and plan[0].new_path is None
    assert [c.rsplit("/", 1)[1] for c in plan[0].candidates] == ["clip_001.mp4", "clip_002.mp4"]
    assert repair.apply_plan(plan, db.session, Document) == {"repointed": 0, "skipped": 0}
    assert db.session.get(Document, row.id).path.startswith(repair.OLD_PATH_PREFIX)


def test_repair_leaves_alone_files_another_record_already_names(install):
    root, overlays, _client = install
    _overlay(overlays, "clip_001.mp4", b"registered the new way")
    (overlays / "clip_002.mp4").write_bytes(b"orphan")
    _old_row()

    plan = _plan(root, overlays)

    assert [(p.action, p.new_path) for p in plan] == [("repoint", "data/outputs/videos/text-overlay/clip_002.mp4")]


def test_repair_says_missing_when_the_file_is_gone(install):
    root, overlays, _client = install
    _old_row()
    plan = _plan(root, overlays)
    assert plan[0].action == "missing" and "clip_NNN.mp4" in plan[0].note
