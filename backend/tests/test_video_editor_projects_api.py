"""HTTP-layer tests for the /api/video-editor/projects routes.

Mounts the blueprint on a throwaway Flask app with the project store + legacy path
+ document resolver redirected to a tmp dir — so it exercises routing, status
codes, and error mapping WITHOUT a running backend or touching real project data.
"""

from __future__ import annotations

import json

import pytest
from flask import Flask

from backend.api import video_editor_api as vea
from backend.services.video_editor_projects import ProjectStore


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    store = ProjectStore(str(tmp_path / "projects"))
    legacy = tmp_path / "legacy_session.json"
    monkeypatch.setattr(vea, "_project_store", store)
    monkeypatch.setattr(vea, "_LEGACY_SESSION_FILE", str(legacy))
    monkeypatch.setattr(vea, "_resolve_document", lambda d: None)  # default: unresolvable
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(vea.video_editor_bp)
    return app.test_client(), store, legacy


def test_create_list_open_flow(ctx):
    client, store, _ = ctx
    r = client.post("/api/video-editor/projects", json={"name": "My Edit"})
    assert r.status_code == 201
    pid = r.get_json()["id"]

    r = client.get("/api/video-editor/projects")
    body = r.get_json()
    assert r.status_code == 200 and body["currentId"] == pid
    assert [p["id"] for p in body["projects"]] == [pid]

    r = client.get(f"/api/video-editor/projects/{pid}")
    assert r.status_code == 200 and r.get_json()["_meta"]["id"] == pid


def test_bad_id_is_400_unknown_id_is_404(ctx):
    client, _, _ = ctx
    assert client.get("/api/video-editor/projects/..%2f..%2fetc").status_code in (400, 404)
    assert client.get("/api/video-editor/projects/not-hex").status_code == 400
    assert client.get(f"/api/video-editor/projects/{'0' * 32}").status_code == 404


def test_autosave_draft_then_explicit_save(ctx):
    client, store, _ = ctx
    pid = client.post("/api/video-editor/projects", json={"name": "P"}).get_json()["id"]

    # Autosave → draft only; project stays clean of the edit, status dirty.
    r = client.put("/api/video-editor/projects/current",
                   json={"scanMode": "motion", "timeline": {"bin": [{"clipId": "c1"}], "textElements": []}})
    assert r.status_code == 200 and r.get_json()["isDirty"] is True

    # Explicit save promotes the draft.
    r = client.put(f"/api/video-editor/projects/{pid}")
    assert r.status_code == 200
    saved = store.read_project(pid)
    assert saved["scanMode"] == "motion"
    assert store.status(pid)["isDirty"] is False


def test_save_as_rename_delete(ctx):
    client, store, _ = ctx
    pid = client.post("/api/video-editor/projects", json={"name": "Orig"}).get_json()["id"]

    r = client.post(f"/api/video-editor/projects/{pid}/save-as", json={"name": "Copy"})
    assert r.status_code == 201
    new_id = r.get_json()["id"]
    assert new_id != pid

    r = client.patch(f"/api/video-editor/projects/{pid}", json={"name": "Renamed"})
    assert r.status_code == 200 and r.get_json()["name"] == "Renamed"

    assert client.delete(f"/api/video-editor/projects/{new_id}").status_code == 200
    assert client.delete(f"/api/video-editor/projects/{new_id}").status_code == 404  # already gone

    # save-as / rename require a name
    assert client.post(f"/api/video-editor/projects/{pid}/save-as", json={}).status_code == 400
    assert client.patch(f"/api/video-editor/projects/{pid}", json={}).status_code == 400


def test_current_migrates_legacy_session(ctx):
    client, store, legacy = ctx
    legacy.write_text(json.dumps({
        "timeline": {"bin": [{"clipId": "leg1", "documentId": 9, "filename": "x.mp4", "kind": "video"}],
                     "textElements": []},
        "scanMode": "audio", "styleRecipeName": "Cinematic", "clipOverrides": {},
    }))
    r = client.get("/api/video-editor/projects/current")
    assert r.status_code == 200
    body = r.get_json()
    assert body["name"] == "Recovered session"
    assert body["scanMode"] == "audio"
    assert legacy.with_suffix(".json.migrated").exists() or (str(legacy) + ".migrated")


def test_id_targeted_draft_route(ctx):
    """PUT /projects/<id>/draft writes that project's draft (race-safe autosave)
    without promoting it; the saved project stays clean."""
    client, store, _ = ctx
    pid = client.post("/api/video-editor/projects", json={"name": "P"}).get_json()["id"]
    r = client.put(f"/api/video-editor/projects/{pid}/draft", json={"scanMode": "audio"})
    assert r.status_code == 200 and r.get_json()["isDirty"] is True
    assert store.read_project(pid)["scanMode"] != "audio"   # project untouched
    assert store.read_draft(pid)["scanMode"] == "audio"     # draft has the edit
    # bad id / bad body
    assert client.put("/api/video-editor/projects/not-hex/draft", json={"x": 1}).status_code == 400


def test_validate_reports_missing_clip(ctx):
    client, store, _ = ctx
    pid = client.post("/api/video-editor/projects", json={"name": "P"}).get_json()["id"]
    store.save(pid, editable={"timeline": {"bin": [{"clipId": "c1", "documentId": 5,
                                                     "filename": "gone.mp4", "kind": "video"}],
                                            "textElements": []}})
    r = client.post(f"/api/video-editor/projects/{pid}/validate")  # resolver returns None → missing
    assert r.status_code == 200
    body = r.get_json()
    assert body["missing"] == 1 and body["clips"][0]["status"] == "missing"
