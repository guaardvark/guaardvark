"""Stored files are served, and deleted, only from the uploads and output folders.

Each serving route resolves the real location (symlinks followed) when it
serves, so a record whose file was later swapped for a link to a key under
data/ answers 404. In-memory SQLite and a scratch install root.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from flask import Flask

from backend import config
from backend.models import Document, MusicVideo, Production, ProductionShot, db
from backend.services import output_registration as reg
from backend.services.document_path_resolver import servable_file

SECRET = b"TOPSECRET"


@pytest.fixture
def install(monkeypatch, tmp_path):
    root = tmp_path / "install"
    data = root / "data"
    uploads, outputs = data / "uploads", data / "outputs"
    for d in (uploads, outputs / "videos" / "text-overlay", data / "interconnector", data / "agent"):
        d.mkdir(parents=True)
    (data / ".swarm_internal_secret").write_bytes(SECRET)
    (data / "interconnector" / "peer.key").write_bytes(SECRET)
    (root / ".env").write_bytes(SECRET)
    monkeypatch.setattr(config, "GUAARDVARK_ROOT", root)
    monkeypatch.setattr(config, "STORAGE_DIR", str(data))
    monkeypatch.setattr(config, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(config, "OUTPUT_DIR", str(outputs))
    monkeypatch.setattr(reg, "UPLOAD_DIR", str(uploads))
    from backend.api import files_api, music_video_api, production_api, upscaling_api
    from backend.routes.download_route import download_bp
    from backend.routes.uploads_route import uploads_bp

    monkeypatch.setattr(files_api, "get_upload_base_path", lambda: uploads)
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      UPLOAD_FOLDER=str(uploads), OUTPUT_DIR=str(outputs))
    db.init_app(app)
    for bp in (files_api.files_bp, uploads_bp, download_bp, music_video_api.bp, production_api.bp,
               upscaling_api.upscaling_bp):
        app.register_blueprint(bp)
    with app.app_context():
        db.create_all()
        reg.ensure_default_folders()
        yield root, app.test_client()
        db.session.remove()
        db.drop_all()


def _row(path: str, filename: str | None = None) -> Document:
    doc = Document(filename=filename or Path(path).name, path=path, index_status="NOT_INDEXED")
    db.session.add(doc)
    db.session.commit()
    return doc


def _swap_for_link(path: Path, target: Path) -> None:
    path.unlink()
    path.symlink_to(target)


def _download(client, doc):
    return client.get(f"/api/files/document/{doc.id}/download")


# --- Files download -------------------------------------------------------

def test_a_record_in_uploads_is_served(install):
    root, client = install
    (root / "data" / "uploads" / "note.txt").write_bytes(b"hello")
    answer = _download(client, _row("note.txt"))
    assert answer.status_code == 200 and answer.data == b"hello"


def test_a_registered_output_swapped_for_a_link_to_a_secret_is_refused(install):
    root, client = install
    clip = root / "data" / "outputs" / "videos" / "text-overlay" / "clip_001.mp4"
    clip.write_bytes(b"clip")
    doc = reg.register_file(str(clip), "Videos", subfolder_name="Text Overlay")
    assert _download(client, doc).data == b"clip"

    for secret in (root / "data" / ".swarm_internal_secret", root / "data" / "interconnector" / "peer.key"):
        _swap_for_link(clip, secret)
        answer = _download(client, doc)
        assert answer.status_code == 404
        assert SECRET not in answer.data


def test_a_link_inside_uploads_that_leaves_the_served_folders_is_refused(install):
    root, client = install
    (root / "data" / "uploads" / "notes.txt").symlink_to(root / "data" / "interconnector" / "peer.key")
    answer = _download(client, _row("notes.txt"))
    assert answer.status_code == 404 and SECRET not in answer.data


@pytest.mark.parametrize("stored", [
    "../.swarm_internal_secret",
    "../../.env",
    "data/uploads/../interconnector/peer.key",
    "data/.swarm_internal_secret",
    "data/agent/state.json",
])
def test_paths_that_leave_the_served_folders_are_refused(install, stored):
    root, client = install
    (root / "data" / "agent" / "state.json").write_bytes(SECRET)
    answer = _download(client, _row(stored, filename="x.txt"))
    assert answer.status_code == 404 and SECRET not in answer.data


def test_an_absolute_film_crew_path_is_served_but_not_one_to_a_secret(install):
    root, client = install
    final = root / "data" / "outputs" / "productions" / "2" / "render_1" / "final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"film")
    assert _download(client, _row(str(final))).data == b"film"
    answer = _download(client, _row(str(root / "data" / ".swarm_internal_secret")))
    assert answer.status_code == 404


def test_film_crew_outputs_stay_served_when_the_output_folder_is_moved(install, monkeypatch, tmp_path):
    root, client = install
    moved = tmp_path / "elsewhere" / "outputs"
    moved.mkdir(parents=True)
    monkeypatch.setattr(config, "OUTPUT_DIR", str(moved))
    final = root / "data" / "outputs" / "productions" / "3" / "final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"film")
    assert _download(client, _row(str(final))).data == b"film"


def test_a_generator_output_folder_linked_into_the_outputs_is_served(install):
    root, client = install
    real = root / "data" / "outputs" / "comfy"
    real.mkdir()
    (real / "wan_00001.mp4").write_bytes(b"wan")
    link = root / "plugins" / "comfyui" / "ComfyUI" / "output"
    link.parent.mkdir(parents=True)
    link.symlink_to(real, target_is_directory=True)
    answer = _download(client, _row("Videos/wan_00001.mp4"))
    assert answer.status_code == 200 and answer.data == b"wan"


def test_an_output_folder_kept_on_another_disk_is_served(install, tmp_path):
    root, client = install
    disk = tmp_path / "disk" / "renders"
    disk.mkdir(parents=True)
    (disk / "take.mp4").write_bytes(b"take")
    editor = root / "data" / "outputs" / "videos" / "editor-renders"
    editor.symlink_to(disk, target_is_directory=True)
    answer = _download(client, _row("data/outputs/videos/editor-renders/take.mp4"))
    assert answer.status_code == 200 and answer.data == b"take"


# --- Files thumbnail ------------------------------------------------------

def test_thumbnail_by_document_refuses_a_link_to_a_secret(install):
    root, client = install
    image = root / "data" / "uploads" / "a.png"
    image.symlink_to(root / "data" / ".swarm_internal_secret")
    answer = client.get(f"/api/files/thumbnail?document_id={_row('a.png').id}")
    assert answer.status_code == 404 and SECRET not in answer.data


def test_thumbnail_by_path_refuses_a_link_to_a_secret(install):
    root, client = install
    (root / "data" / "uploads" / "b.png").symlink_to(root / "data" / ".swarm_internal_secret")
    answer = client.get("/api/files/thumbnail?path=b.png")
    assert answer.status_code == 404 and SECRET not in answer.data


def test_thumbnail_skips_a_cached_thumbnail_that_links_to_a_secret(install):
    root, client = install
    uploads = root / "data" / "uploads"
    (uploads / "c.png").write_bytes(b"image")
    (uploads / "thumbnails").mkdir()
    (uploads / "thumbnails" / "c.jpg").symlink_to(root / "data" / ".swarm_internal_secret")
    answer = client.get(f"/api/files/thumbnail?document_id={_row('c.png').id}")
    assert answer.status_code == 200 and answer.data == b"image"


def test_video_thumbnail_is_not_written_through_a_link(install, monkeypatch):
    root, client = install
    uploads = root / "data" / "uploads"
    (uploads / "v.mp4").write_bytes(b"video")
    victim = root / "data" / "agent" / "state.json"
    victim.write_bytes(b"keep me")
    (uploads / "thumbnails").mkdir()
    (uploads / "thumbnails" / "v.jpg").symlink_to(victim)
    import subprocess

    def fake_ffmpeg(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"frame")

    monkeypatch.setattr(subprocess, "run", fake_ffmpeg)
    answer = client.get(f"/api/files/thumbnail?document_id={_row('v.mp4').id}")
    assert victim.read_bytes() == b"keep me"
    assert answer.status_code == 200 and answer.data == b"video"


# --- /api/uploads and /outputs by path ------------------------------------

def test_uploads_route_serves_files_and_refuses_links_out(install):
    root, client = install
    uploads = root / "data" / "uploads"
    (uploads / "ok.txt").write_bytes(b"ok")
    (uploads / "bad.txt").symlink_to(root / "data" / "interconnector" / "peer.key")
    assert client.get("/api/uploads/ok.txt").data == b"ok"
    answer = client.get("/api/uploads/bad.txt")
    assert answer.status_code == 404 and SECRET not in answer.data


def test_outputs_route_serves_files_and_refuses_links_out(install):
    root, client = install
    outputs = root / "data" / "outputs"
    (outputs / "ok.txt").write_bytes(b"ok")
    (outputs / "bad.txt").symlink_to(root / ".env")
    assert client.get("/outputs/ok.txt").data == b"ok"
    answer = client.get("/outputs/bad.txt")
    assert answer.status_code == 404 and SECRET not in answer.data


# --- Music video and Film Crew storyboard records -------------------------

def test_music_video_clip_and_storyboard_refuse_links_out(install):
    root, client = install
    clips = root / "data" / "outputs" / "videos" / "music_video_1" / "clips"
    clips.mkdir(parents=True)
    (clips / "clip_0.mp4").write_bytes(b"clip")
    (clips / "storyboard_0.png").write_bytes(b"still")
    (clips / "clip_1.mp4").symlink_to(root / "data" / ".swarm_internal_secret")
    (clips / "storyboard_1.png").symlink_to(root / "data" / ".swarm_internal_secret")
    mv = MusicVideo(name="mv", style_prompt="s", clips=[
        {"index": i, "clip_path": str(clips / f"clip_{i}.mp4"),
         "storyboard_path": str(clips / f"storyboard_{i}.png")} for i in (0, 1)
    ])
    db.session.add(mv)
    db.session.commit()

    assert client.get(f"/api/music-video/{mv.id}/clip/0").data == b"clip"
    assert client.get(f"/api/music-video/{mv.id}/storyboard/0").data == b"still"
    for route in ("clip", "storyboard"):
        answer = client.get(f"/api/music-video/{mv.id}/{route}/1")
        assert answer.status_code == 404 and SECRET not in answer.data


def test_production_storyboard_refuses_links_out(install):
    root, client = install
    boards = root / "data" / "outputs" / "storyboards" / "1"
    boards.mkdir(parents=True)
    (boards / "shot_1_1.png").write_bytes(b"board")
    (boards / "shot_1_2.png").symlink_to(root / "data" / "interconnector" / "peer.key")
    prod = Production(name="p", script_text="x")
    db.session.add(prod)
    db.session.commit()
    shots = [ProductionShot(production_id=prod.id, scene_number=1, shot_number=n, description="d",
                            storyboard_image_path=str(boards / f"shot_1_{n}.png")) for n in (1, 2)]
    db.session.add_all(shots)
    db.session.commit()

    good = client.get(f"/api/production/{prod.id}/storyboard/shot/{shots[0].id}/image")
    assert good.status_code == 200 and good.data == b"board"
    bad = client.get(f"/api/production/{prod.id}/storyboard/shot/{shots[1].id}/image")
    assert bad.status_code == 404 and SECRET not in bad.data


# --- Upscale from the Files page ------------------------------------------

def test_upscale_document_refuses_a_link_to_a_secret(install, monkeypatch):
    root, client = install
    from backend.api import upscaling_api

    (root / "data" / "uploads" / "pic.png").symlink_to(root / "data" / ".swarm_internal_secret")
    called = []
    monkeypatch.setattr(upscaling_api, "_proxy_post", lambda *a, **k: called.append(a) or ({}, 200))
    answer = client.post("/api/upscaling/upscale/document", json={"document_id": _row("pic.png").id})
    assert answer.status_code == 404 and not called


# --- Registration shares the rule -----------------------------------------

def test_registration_and_serving_use_one_rule(install):
    root, client = install
    clip = root / "data" / "outputs" / "videos" / "text-overlay" / "link.mp4"
    clip.symlink_to(root / "data" / ".swarm_internal_secret")
    assert servable_file(clip) is None
    assert reg.output_location_problem(clip) is not None
    assert reg.register_file(str(clip), "Videos") is None


# --- Delete removes the row's own entry, never a link's target ------------

def _swapped_overlay(root):
    clip = root / "data" / "outputs" / "videos" / "text-overlay" / "swap.mp4"
    clip.write_bytes(b"clip")
    doc = reg.register_file(str(clip), "Videos", subfolder_name="Text Overlay")
    secret = root / "data" / ".swarm_internal_secret"
    _swap_for_link(clip, secret)
    return doc, clip, secret


def test_deleting_a_record_never_deletes_a_link_target(install):
    root, client = install
    doc, clip, secret = _swapped_overlay(root)
    answer = client.delete(f"/api/files/document/{doc.id}")
    assert answer.status_code == 200
    assert secret.read_bytes() == SECRET


def test_folder_delete_never_deletes_a_link_target(install):
    root, client = install
    doc, clip, secret = _swapped_overlay(root)
    answer = client.delete(f"/api/files/folder/{doc.folder_id}")
    assert answer.status_code == 200, answer.get_json()
    assert secret.read_bytes() == SECRET


def test_bulk_delete_never_deletes_a_link_target(install):
    root, client = install
    doc, clip, secret = _swapped_overlay(root)
    answer = client.post("/api/files/bulk-delete", json={"document_ids": [doc.id]})
    assert answer.status_code == 200, answer.get_json()
    assert secret.read_bytes() == SECRET


def test_deleting_a_linked_output_removes_the_link_not_the_file_it_names(install):
    root, client = install
    overlays = root / "data" / "outputs" / "videos" / "text-overlay"
    kept = root / "data" / "outputs" / "videos" / "kept.mp4"
    kept.write_bytes(b"kept")
    clip = overlays / "alias.mp4"
    clip.write_bytes(b"clip")
    doc = reg.register_file(str(clip), "Videos", subfolder_name="Text Overlay")
    _swap_for_link(clip, kept)
    answer = client.delete(f"/api/files/document/{doc.id}")
    assert answer.status_code == 200
    assert kept.read_bytes() == b"kept"
    assert not os.path.lexists(clip)


def test_an_output_folder_linked_to_the_data_folder_serves_nothing_from_it(install):
    root, client = install
    editor = root / "data" / "outputs" / "videos" / "editor-renders"
    editor.symlink_to(root / "data", target_is_directory=True)
    answer = _download(client, _row("data/outputs/videos/editor-renders/interconnector/peer.key"))
    assert answer.status_code == 404 and SECRET not in answer.data
