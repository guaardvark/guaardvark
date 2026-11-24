"""Cast Library images are served only from the reference folder, uploads and
the output folders, with symlinks followed.

A stored ref or sample path is settable through the API, so an image-named
link to a key under data/, or a ``..`` path to an image elsewhere in the
install, must answer 404. In-memory SQLite and a scratch install root.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from backend import config
from backend.api import cast_library_api
from backend.models import Subject, SubjectSample, db

SECRET = b"TOPSECRET"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def install(monkeypatch, tmp_path):
    root = tmp_path / "install"
    data = root / "data"
    refs = data / "cast_refs" / "1"
    uploads, outputs = data / "uploads", data / "outputs"
    samples = outputs / "character_samples" / "1"
    for d in (refs, uploads, samples, data / "interconnector", data / "agent"):
        d.mkdir(parents=True)
    (data / ".swarm_internal_secret").write_bytes(SECRET)
    (data / "interconnector" / "peer.key").write_bytes(SECRET)
    (root / ".env").write_bytes(SECRET)
    (data / "agent" / "screen.png").write_bytes(SECRET)  # an image outside the served folders
    monkeypatch.setattr(config, "GUAARDVARK_ROOT", root)
    monkeypatch.setattr(config, "STORAGE_DIR", str(data))
    monkeypatch.setattr(config, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(config, "OUTPUT_DIR", str(outputs))
    monkeypatch.setattr(cast_library_api, "_PROJECT_ROOT", root)
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      STORAGE_DIR=str(data), UPLOAD_FOLDER=str(uploads))
    db.init_app(app)
    app.register_blueprint(cast_library_api.bp)
    with app.app_context():
        db.create_all()
        yield root, app.test_client()
        db.session.remove()
        db.drop_all()


def _subject(*paths) -> Subject:
    s = Subject(kind="character", name="Test", ref_image_paths=[str(p) for p in paths])
    db.session.add(s)
    db.session.commit()
    return s


def _sample(subject, path) -> SubjectSample:
    row = SubjectSample(subject_id=subject.id, index=0, image_path=str(path), status="done")
    db.session.add(row)
    db.session.commit()
    return row


def _link(at: Path, target: Path) -> Path:
    at.symlink_to(target)
    return at


def _ref_url(s, i=0):
    return f"/api/cast-library/subjects/{s.id}/refs/{i}/image"


def _preview_url(s):
    return f"/api/cast-library/subjects/{s.id}/preview"


def _sample_url(row):
    return f"/api/cast-library/subjects/{row.subject_id}/samples/{row.id}/image"


# --- GET /subjects/<id>/refs/<i>/image ------------------------------------

def test_ref_image_in_cast_refs_is_served(install):
    root, client = install
    ref = root / "data" / "cast_refs" / "1" / "face.png"
    ref.write_bytes(PNG)
    answer = client.get(_ref_url(_subject(ref)))
    assert answer.status_code == 200 and answer.data == PNG


def test_ref_image_relative_to_storage_is_served(install):
    root, client = install
    (root / "data" / "cast_refs" / "1" / "face.png").write_bytes(PNG)
    answer = client.get(_ref_url(_subject("cast_refs/1/face.png")))
    assert answer.status_code == 200 and answer.data == PNG


@pytest.mark.parametrize("key", ["data/.swarm_internal_secret", "data/interconnector/peer.key", ".env"])
def test_ref_image_link_to_a_key_is_404(install, key):
    root, client = install
    link = _link(root / "data" / "cast_refs" / "1" / "face.png", root / key)
    answer = client.get(_ref_url(_subject(link)))
    assert answer.status_code == 404 and SECRET not in answer.data


def test_ref_image_dotdot_out_of_cast_refs_is_404(install):
    root, client = install
    answer = client.get(_ref_url(_subject("cast_refs/1/../../agent/screen.png")))
    assert answer.status_code == 404 and SECRET not in answer.data


def test_ref_image_in_uploads_is_served(install):
    root, client = install
    up = root / "data" / "uploads" / "photo.jpg"
    up.write_bytes(PNG)
    assert client.get(_ref_url(_subject(up))).status_code == 200


def test_ref_folder_linked_to_another_disk_is_served(install, tmp_path):
    root, client = install
    elsewhere = tmp_path / "other-disk" / "cast_refs"
    (elsewhere / "1").mkdir(parents=True)
    (elsewhere / "1" / "face.png").write_bytes(PNG)
    import shutil
    shutil.rmtree(root / "data" / "cast_refs")
    (root / "data" / "cast_refs").symlink_to(elsewhere)
    answer = client.get(_ref_url(_subject(root / "data" / "cast_refs" / "1" / "face.png")))
    assert answer.status_code == 200 and answer.data == PNG


def test_ref_folder_linked_to_data_is_not_a_root(install):
    root, client = install
    import shutil
    shutil.rmtree(root / "data" / "cast_refs")
    (root / "data" / "cast_refs").symlink_to(root / "data")
    answer = client.get(_ref_url(_subject(root / "data" / "cast_refs" / "agent" / "screen.png")))
    assert answer.status_code == 404


# --- GET /subjects/<id>/preview -------------------------------------------

def test_preview_serves_the_first_real_ref(install):
    root, client = install
    ref = root / "data" / "cast_refs" / "1" / "face.png"
    ref.write_bytes(PNG)
    answer = client.get(_preview_url(_subject(ref)))
    assert answer.status_code == 200 and answer.data == PNG


def test_preview_link_to_a_key_is_404(install):
    root, client = install
    link = _link(root / "data" / "cast_refs" / "1" / "face.png", root / "data" / "interconnector" / "peer.key")
    answer = client.get(_preview_url(_subject(link)))
    assert answer.status_code == 404 and SECRET not in answer.data


def test_preview_dotdot_is_404(install):
    root, client = install
    answer = client.get(_preview_url(_subject(root / "data" / "cast_refs" / "1" / ".." / ".." / "agent" / "screen.png")))
    assert answer.status_code == 404 and SECRET not in answer.data


def test_preview_skips_a_bad_ref_for_a_good_one(install):
    root, client = install
    bad = _link(root / "data" / "cast_refs" / "1" / "a.png", root / ".env")
    good = root / "data" / "cast_refs" / "1" / "b.png"
    good.write_bytes(PNG)
    answer = client.get(_preview_url(_subject(bad, good)))
    assert answer.status_code == 200 and answer.data == PNG


# --- GET /subjects/<id>/samples/<id>/image --------------------------------

def test_sample_image_is_served(install):
    root, client = install
    png = root / "data" / "outputs" / "character_samples" / "1" / "sample_0.png"
    png.write_bytes(PNG)
    answer = client.get(_sample_url(_sample(_subject(), png)))
    assert answer.status_code == 200 and answer.data == PNG


def test_sample_image_link_to_a_key_is_404(install):
    root, client = install
    link = _link(root / "data" / "outputs" / "character_samples" / "1" / "sample_0.png",
                 root / "data" / ".swarm_internal_secret")
    answer = client.get(_sample_url(_sample(_subject(), link)))
    assert answer.status_code == 404 and SECRET not in answer.data


def test_sample_image_dotdot_is_404(install):
    root, client = install
    path = root / "data" / "outputs" / "character_samples" / ".." / ".." / "agent" / "screen.png"
    answer = client.get(_sample_url(_sample(_subject(), path)))
    assert answer.status_code == 404 and SECRET not in answer.data


def test_image_named_link_to_a_non_image_output_is_404(install):
    root, client = install
    (root / "data" / "outputs" / "notes.json").write_bytes(b"{}")
    link = _link(root / "data" / "cast_refs" / "1" / "face.png", root / "data" / "outputs" / "notes.json")
    assert client.get(_ref_url(_subject(link))).status_code == 404


# --- DELETE /subjects/<id>/refs/<i> ---------------------------------------

def test_delete_ref_removes_the_cast_refs_file(install):
    root, client = install
    ref = root / "data" / "cast_refs" / "1" / "face.png"
    ref.write_bytes(PNG)
    s = _subject(ref)
    assert client.delete(f"/api/cast-library/subjects/{s.id}/refs/0").status_code == 200
    assert not ref.exists()


def test_delete_ref_link_removes_the_link_not_the_key(install):
    root, client = install
    secret = root / "data" / ".swarm_internal_secret"
    link = _link(root / "data" / "cast_refs" / "1" / "face.png", secret)
    s = _subject(link)
    assert client.delete(f"/api/cast-library/subjects/{s.id}/refs/0").status_code == 200
    assert secret.read_bytes() == SECRET


def test_delete_ref_link_inside_cast_refs_keeps_its_target(install):
    root, client = install
    target = root / "data" / "cast_refs" / "1" / "keep.png"
    target.write_bytes(PNG)
    link = _link(root / "data" / "cast_refs" / "1" / "face.png", target)
    s = _subject(link)
    assert client.delete(f"/api/cast-library/subjects/{s.id}/refs/0").status_code == 200
    assert not link.is_symlink() and target.read_bytes() == PNG


def test_delete_ref_that_is_a_sample_keeps_the_sample(install):
    root, client = install
    png = root / "data" / "outputs" / "character_samples" / "1" / "sample_0.png"
    png.write_bytes(PNG)
    s = _subject(png)
    assert client.delete(f"/api/cast-library/subjects/{s.id}/refs/0").status_code == 200
    assert png.read_bytes() == PNG
    assert db.session.get(Subject, s.id).ref_image_paths == []
