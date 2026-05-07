import pytest
import json

try:
    from flask import Flask
    from backend.models import db, Subject
    from backend.api.cast_library_api import bp as cast_library_bp
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        # Cast-ref upload endpoint resolves data/cast_refs/ under DATA_DIR;
        # tests redirect to a tmp dir so the repo's data/ doesn't get polluted.
        "DATA_DIR": str(tmp_path),
    })
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


# --- upload-refs ---------------------------------------------------------

def _png_bytes() -> bytes:
    """Smallest possible valid PNG. Used so the upload path doesn't have to
    inspect contents — extension validation is enough."""
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfa\xcf"
        b"\x00\x00\x00\x02\x00\x01\xe5\x27\xde\xfc\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _create_subject(client, name="Dean"):
    return client.post("/api/cast-library/subjects", json={
        "kind": "character", "name": name, "description": "x",
    }).get_json()


def test_upload_refs_persists_files_and_appends_paths(client, app):
    from io import BytesIO
    subj = _create_subject(client)
    resp = client.post(
        f"/api/cast-library/subjects/{subj['id']}/upload-refs",
        data={"files": [(BytesIO(_png_bytes()), "headshot.png")]},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["saved"]) == 1
    assert data["saved"][0].endswith("headshot.png")
    assert data["subject"]["ref_image_paths"] == data["saved"]
    # File actually exists on disk
    import os
    assert os.path.exists(data["saved"][0])


def test_upload_refs_skips_unsupported_extension(client):
    from io import BytesIO
    subj = _create_subject(client)
    resp = client.post(
        f"/api/cast-library/subjects/{subj['id']}/upload-refs",
        data={"files": [(BytesIO(b"not an image"), "evil.exe")]},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["saved"] == []
    assert len(data["skipped"]) == 1
    assert "unsupported" in data["skipped"][0]["reason"].lower()


def test_upload_refs_appends_to_existing_list(client):
    from io import BytesIO
    subj = client.post("/api/cast-library/subjects", json={
        "kind": "character", "name": "Dean", "description": "x",
        "ref_image_paths": ["/already/there.png"],
    }).get_json()
    resp = client.post(
        f"/api/cast-library/subjects/{subj['id']}/upload-refs",
        data={"files": [(BytesIO(_png_bytes()), "new.png")]},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    refs = resp.get_json()["subject"]["ref_image_paths"]
    assert len(refs) == 2
    assert refs[0] == "/already/there.png"
    assert refs[1].endswith("new.png")


def test_upload_refs_404_for_unknown_subject(client):
    from io import BytesIO
    resp = client.post(
        "/api/cast-library/subjects/9999/upload-refs",
        data={"files": [(BytesIO(_png_bytes()), "x.png")]},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 404


def test_upload_refs_resolves_filename_collisions(client):
    from io import BytesIO
    subj = _create_subject(client)
    paths = []
    for _ in range(3):
        resp = client.post(
            f"/api/cast-library/subjects/{subj['id']}/upload-refs",
            data={"files": [(BytesIO(_png_bytes()), "ref.png")]},
            content_type="multipart/form-data",
        )
        paths.append(resp.get_json()["saved"][0])
    # Same source name three times → three distinct on-disk paths.
    assert len(set(paths)) == 3
