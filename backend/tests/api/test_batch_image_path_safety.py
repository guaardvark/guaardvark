"""Path-containment and auth tests for the per-image batch-image routes.

The image routes take ``<image_name>`` from the URL and resolve it inside the
batch's ``images/`` and ``thumbnails/`` directories. These tests prove a name
cannot escape that directory (``..``, separators, symlinks, a second round of
URL decoding) and that destructive image operations are auth-protected while
reads and generation stay public.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

import backend.api.batch_image_generation_api as m
from backend.api.batch_image_generation_api import _contained_file


# ---- _contained_file -------------------------------------------------------
def test_contained_file_accepts_plain_and_spaced_names(tmp_path):
    (tmp_path / "a b.png").write_bytes(b"x")
    assert _contained_file(tmp_path, "a b.png") == (tmp_path / "a b.png").resolve()
    assert _contained_file(tmp_path, "ImageGen_08-10-2026_223341_002.png") is not None
    # A literal percent sequence is an ordinary (nonexistent) filename, not a path.
    assert _contained_file(tmp_path, "..%2F..%2Fetc%2Fpasswd") is not None


@pytest.mark.parametrize("bad", ["", ".", "..", "../x", "a/b", "a\\b", "/etc/passwd", "x\x00y"])
def test_contained_file_rejects_escapes(tmp_path, bad):
    assert _contained_file(tmp_path, bad) is None


def test_contained_file_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    images = tmp_path / "images"
    images.mkdir()
    os.symlink(outside, images / "link.png")
    assert _contained_file(images, "link.png") is None


# ---- routes ----------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    batch_dir = tmp_path / "ImageBatch_01"
    (batch_dir / "images").mkdir(parents=True)
    (batch_dir / "thumbnails").mkdir()
    (batch_dir / "images" / "ok.png").write_bytes(b"\x89PNG")
    (batch_dir / "images" / "noext").write_bytes(b"x")
    (tmp_path / "secret.txt").write_text("TOPSECRET-CONTENT-7731")

    status = MagicMock()
    status.batch_id = "ImageBatch_01"
    status.output_dir = str(batch_dir)
    gen = MagicMock()
    gen.get_batch_status.return_value = status
    gen.list_all_batches.return_value = [status]
    gen.base_output_dir = str(tmp_path)

    monkeypatch.setattr(m, "service_available", True, raising=False)
    monkeypatch.setattr(m, "get_batch_image_generator", lambda: gen)

    app = Flask(__name__)
    app.register_blueprint(m.batch_image_bp)
    return app.test_client(), tmp_path


def test_get_image_serves_contained_file(client):
    c, _ = client
    resp = c.get("/api/batch-image/image/ImageBatch_01/ok.png")
    assert resp.status_code == 200
    assert resp.data.startswith(b"\x89PNG")


def test_get_image_double_encoded_traversal_is_not_decoded_twice(client):
    c, root = client
    # Werkzeug decodes the segment once to "..%2F..%2Fsecret.txt"; the route must
    # treat that as a literal filename rather than decoding it again.
    resp = c.get("/api/batch-image/image/ImageBatch_01/..%252F..%252Fsecret.txt")
    assert resp.status_code == 404
    assert b"TOPSECRET-CONTENT-7731" not in resp.data


def test_get_image_rejects_dotdot(client):
    c, _ = client
    resp = c.get("/api/batch-image/image/ImageBatch_01/..")
    assert resp.status_code in (400, 404, 405)


def test_delete_and_rename_refuse_traversal(client):
    c, root = client
    assert c.delete("/api/batch-image/image/ImageBatch_01/..%252F..%252Fsecret.txt").status_code == 404
    assert (root / "secret.txt").exists()
    resp = c.put("/api/batch-image/image/ImageBatch_01/..%252Fsecret.txt/rename", json={"new_name": "x.png"})
    assert resp.status_code == 404
    assert (root / "secret.txt").exists()


def test_rename_rejects_empty_new_name(client):
    c, root = client
    # No extension to re-append, and secure_filename("...") is empty.
    resp = c.put("/api/batch-image/image/ImageBatch_01/noext/rename", json={"new_name": "..."})
    assert resp.status_code == 400
    assert (root / "ImageBatch_01" / "images" / "noext").exists()


# ---- auth_guard ------------------------------------------------------------
def test_destructive_batch_image_routes_are_protected():
    from backend.utils.auth_guard import _is_protected
    app = Flask(__name__)
    protected = [
        ("DELETE", "/api/batch-image/image/b/x.png"),
        ("PUT", "/api/batch-image/image/b/x.png/rename"),
        ("DELETE", "/api/batch-image/delete/b"),
        ("PUT", "/api/batch-image/rename/b"),
        ("POST", "/api/batch-image/move/b"),
        ("POST", "/api/batch-image/folder"),
    ]
    for method, path in protected:
        with app.test_request_context(path, method=method):
            assert _is_protected() is True, f"{method} {path}"
    public = [
        ("GET", "/api/batch-image/image/b/x.png"),
        ("GET", "/api/batch-image/list"),
        ("POST", "/api/batch-image/generate/prompts"),
        ("POST", "/api/batch-image/cancel/b"),
    ]
    for method, path in public:
        with app.test_request_context(path, method=method):
            assert _is_protected() is False, f"{method} {path}"


def test_gpu_and_upscaling_mutations_are_protected():
    from backend.utils.auth_guard import _is_protected
    app = Flask(__name__)
    for method, path in [
        ("POST", "/api/gpu/ollama/stop"),
        ("POST", "/api/gpu/comfyui/stop"),
        ("POST", "/api/gpu/memory/force-release"),
        ("POST", "/api/upscaling/upscale/video"),
        ("DELETE", "/api/upscaling/jobs"),
    ]:
        with app.test_request_context(path, method=method):
            assert _is_protected() is True, f"{method} {path}"
    for path in ["/api/gpu/status", "/api/upscaling/jobs", "/api/upscaling/health"]:
        with app.test_request_context(path, method="GET"):
            assert _is_protected() is False, f"GET {path}"


def test_cast_library_deletes_are_protected_but_posts_stay_public():
    from backend.utils.auth_guard import _is_protected
    app = Flask(__name__)
    for path in ["/api/cast-library/subjects/3", "/api/cast-library/subjects/3/refs/0",
                 "/api/cast-library/subjects/3/samples/9"]:
        with app.test_request_context(path, method="DELETE"):
            assert _is_protected() is True, f"DELETE {path}"
    for path in ["/api/cast-library/subjects/3/train", "/api/cast-library/subjects/3/generate"]:
        with app.test_request_context(path, method="POST"):
            assert _is_protected() is False, f"POST {path}"
