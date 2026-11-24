"""Consent records and exported chats are never served or registered.

Both live under the output folder, which is otherwise a folder files are
served from. Their owners read them from disk; no route hands them out.
Scratch install root, no database rows needed for the checks here.
"""
from __future__ import annotations

import os

import pytest
from flask import Flask

from backend import config
from backend.services import document_path_resolver as resolver
from backend.services.document_path_resolver import private_output_folder, servable_file


@pytest.fixture
def install(monkeypatch, tmp_path):
    root = tmp_path / "install"
    outputs = root / "data" / "outputs"
    uploads = root / "data" / "uploads"
    for d in (outputs / "consent", outputs / "chat-exports" / "chats-1" / "sessions",
              outputs / ".progress_jobs", outputs / "videos", uploads):
        d.mkdir(parents=True)
    (outputs / "consent" / "abc.consent").write_text("{}")
    (outputs / "chat-exports" / "chats-1" / "sessions" / "s1.md").write_text("private chat")
    (outputs / ".progress_jobs" / "job.json").write_text("{}")
    (outputs / "videos" / "clip.mp4").write_bytes(b"video")
    (outputs / "consent-form-poster.png").write_bytes(b"png")
    monkeypatch.setattr(config, "GUAARDVARK_ROOT", root)
    monkeypatch.setattr(config, "STORAGE_DIR", str(root / "data"))
    monkeypatch.setattr(config, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(config, "OUTPUT_DIR", str(outputs))
    monkeypatch.setattr(resolver, "_project_root", lambda: root)
    return root, outputs, uploads


def test_private_folders_are_named_and_ordinary_outputs_are_not(install):
    _root, outputs, _uploads = install
    assert private_output_folder(outputs / "consent" / "abc.consent") == "consent"
    assert private_output_folder(outputs / "chat-exports" / "chats-1" / "sessions" / "s1.md") == "chat-exports"
    assert private_output_folder(outputs / ".progress_jobs" / "job.json") == ".progress_jobs"
    assert private_output_folder(outputs / "videos" / "clip.mp4") is None
    # A file at the top of the output folder whose name merely starts like one.
    assert private_output_folder(outputs / "consent-form-poster.png") is None


def test_the_shared_check_refuses_them_and_still_serves_the_rest(install):
    _root, outputs, _uploads = install
    assert servable_file(outputs / "consent" / "abc.consent") is None
    assert servable_file(outputs / "chat-exports" / "chats-1" / "sessions" / "s1.md") is None
    assert servable_file(outputs / ".progress_jobs" / "job.json") is None
    assert servable_file(outputs / "videos" / "clip.mp4") is not None
    assert servable_file(outputs / "consent-form-poster.png") is not None


def test_a_link_from_a_served_folder_into_a_private_one_is_refused(install):
    _root, outputs, uploads = install
    link = uploads / "holiday.md"
    os.symlink(outputs / "chat-exports" / "chats-1" / "sessions" / "s1.md", link)
    assert servable_file(link) is None
    in_outputs = outputs / "videos" / "notes.consent"
    os.symlink(outputs / "consent" / "abc.consent", in_outputs)
    assert servable_file(in_outputs) is None


def test_the_download_route_answers_not_found(install):
    _root, outputs, _uploads = install
    from backend.routes.download_route import download_bp

    app = Flask(__name__)
    app.config.update(TESTING=True, OUTPUT_DIR=str(outputs))
    app.register_blueprint(download_bp)
    client = app.test_client()
    assert client.get("/outputs/consent/abc.consent").status_code == 404
    assert client.get("/outputs/chat-exports/chats-1/sessions/s1.md").status_code == 404
    assert client.get("/outputs/.progress_jobs/job.json").status_code == 404
    assert client.get("/outputs/videos/clip.mp4").status_code == 200


def test_the_owners_still_read_their_own_folders(install):
    _root, outputs, _uploads = install
    # consent_records and chat_export_service open these paths directly.
    assert (outputs / "consent" / "abc.consent").read_text() == "{}"
    assert (outputs / "chat-exports" / "chats-1" / "sessions" / "s1.md").read_text() == "private chat"
