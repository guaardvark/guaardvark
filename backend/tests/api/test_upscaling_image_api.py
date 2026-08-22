"""Tests for the image-upscale proxy routes.

Covers the staging rules for uploaded images (nothing the caller sends reaches
the filesystem as a path), the containment of the output route, and the payload
the proxy hands to the plugin.
"""
from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from flask import Flask

import backend.api.upscaling_api as m
from backend.api.upscaling_api import _contained_file


# ---- _contained_file -------------------------------------------------------
def test_contained_file_accepts_plain_name(tmp_path):
    (tmp_path / "still_upscaled.png").write_bytes(b"\x89PNG")
    assert _contained_file(tmp_path, "still_upscaled.png") is not None


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
    monkeypatch.setattr(m, "_get_upload_dir", lambda: tmp_path)
    (tmp_path / "secret.txt").write_text("TOPSECRET-CONTENT-7731")

    app = Flask(__name__)
    app.register_blueprint(m.upscaling_bp)
    return app.test_client(), tmp_path


def _png(name="shot.png"):
    return (io.BytesIO(b"\x89PNG\r\n\x1a\n"), name)


def test_single_upscale_stages_upload_and_returns_output_url(client, monkeypatch):
    c, root = client
    captured = {}

    def fake_post(path, payload, timeout=None):
        captured["path"] = path
        captured["payload"] = payload
        Path(payload["output_path"]).write_bytes(b"\x89PNG")
        return {"status": "completed", "output_path": payload["output_path"]}, 200

    monkeypatch.setattr(m, "_proxy_post", fake_post)

    resp = c.post(
        "/api/upscaling/upscale/image",
        data={"file": _png(), "model": "4x-UltraSharp", "scale": "2"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["url"] == f"/api/upscaling/output/image/{body['output_file']}"

    assert captured["path"] == "/upscale/image"
    payload = captured["payload"]
    assert payload["model"] == "4x-UltraSharp"
    assert payload["scale"] == 2.0
    # Both sides of the job stay inside the plugin's own directories.
    assert Path(payload["input_path"]).parent == (root / "input" / "images")
    assert Path(payload["output_path"]).parent == (root / "output" / "images")


def test_single_upscale_rejects_non_image(client):
    c, _ = client
    resp = c.post(
        "/api/upscaling/upscale/image",
        data={"file": (io.BytesIO(b"MZ"), "payload.exe")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_upload_name_cannot_traverse(client, monkeypatch):
    c, root = client
    monkeypatch.setattr(
        m, "_proxy_post",
        lambda path, payload, timeout=None: ({"status": "completed"}, 200),
    )
    resp = c.post(
        "/api/upscaling/upscale/image",
        data={"file": (io.BytesIO(b"\x89PNG"), "../../secret.png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    staged = list((root / "input" / "images").iterdir())
    assert len(staged) == 1
    assert staged[0].parent == (root / "input" / "images")
    assert (root / "secret.txt").read_text() == "TOPSECRET-CONTENT-7731"


def test_batch_upscale_submits_one_job_for_every_file(client, monkeypatch):
    c, root = client
    captured = {}

    def fake_post(path, payload, timeout=None):
        captured["path"] = path
        captured["payload"] = payload
        return {"job_id": "abc123", "status": "pending"}, 202

    monkeypatch.setattr(m, "_proxy_post", fake_post)

    resp = c.post(
        "/api/upscaling/upscale/images",
        data={"files": [_png("a.png"), _png("b.jpg")]},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["queued"] == 2

    assert captured["path"] == "/upscale/images"
    payload = captured["payload"]
    assert len(payload["inputs"]) == 2
    assert payload["output_dir"] == str(root / "output" / "images")


def test_batch_upscale_reports_rejected_files(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(
        m, "_proxy_post",
        lambda path, payload, timeout=None: ({"job_id": "abc123"}, 202),
    )
    resp = c.post(
        "/api/upscaling/upscale/images",
        data={"files": [_png("a.png"), (io.BytesIO(b"MZ"), "payload.exe")]},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["queued"] == 1
    assert len(data["rejected"]) == 1


def test_batch_upscale_without_usable_files_is_rejected(client):
    c, _ = client
    resp = c.post(
        "/api/upscaling/upscale/images",
        data={"files": [(io.BytesIO(b"MZ"), "payload.exe")]},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_serve_image_output_is_contained(client):
    c, root = client
    out = root / "output" / "images"
    out.mkdir(parents=True)
    (out / "ok.png").write_bytes(b"\x89PNG")

    assert c.get("/api/upscaling/output/image/ok.png").status_code == 200
    escape = c.get("/api/upscaling/output/image/..%252F..%252Fsecret.txt")
    assert escape.status_code == 404
    assert b"TOPSECRET-CONTENT-7731" not in escape.data
