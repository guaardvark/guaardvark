"""Hermetic unit tests for the Video Editor named-project store.

Exercises backend/services/video_editor_projects.py::ProjectStore directly
against pytest's tmp_path (file-per-project JSON). No Flask, no network, no
real Documents DB — validate_refs gets a fake resolver lambda.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from backend.services.video_editor_projects import (
    PROJECT_SCHEMA_VERSION,
    ProjectStore,
)


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture()
def store(tmp_path):
    return ProjectStore(str(tmp_path / "video-editor-projects"))


def _ids(listing):
    return [r["id"] for r in listing["projects"]]


# --------------------------------------------------------------------------- #
# 1. create()
# --------------------------------------------------------------------------- #
def test_create_writes_file_lists_and_sets_current(store, tmp_path):
    project = store.create("My First Cut")

    pid = project["id"]
    # File on disk.
    proj_file = tmp_path / "video-editor-projects" / f"{pid}.project.json"
    assert proj_file.exists()

    # schemaVersion + blank timeline.
    assert project["schemaVersion"] == PROJECT_SCHEMA_VERSION
    assert project["name"] == "My First Cut"
    assert project["timeline"] == {"bin": [], "textElements": []}

    # Appears in list_projects() and is current.
    listing = store.list_projects()
    assert pid in _ids(listing)
    assert listing["currentId"] == pid
    assert store.get_current_id() == pid

    # On-disk content matches what was returned.
    disk = json.loads(proj_file.read_text())
    assert disk["id"] == pid
    assert disk["schemaVersion"] == PROJECT_SCHEMA_VERSION


# --------------------------------------------------------------------------- #
# 2. save_draft() writes ONLY the draft
# --------------------------------------------------------------------------- #
def test_save_draft_writes_only_draft(store):
    project = store.create("Draft Test")
    pid = project["id"]
    before = store.read_project(pid)

    edited_timeline = {"bin": [{"clipId": "c1", "documentId": 42}], "textElements": []}
    st = store.save_draft(pid, {"timeline": edited_timeline, "scanMode": "and-only"})

    # The saved project file is unchanged.
    after = store.read_project(pid)
    assert after == before
    assert after["timeline"] == {"bin": [], "textElements": []}

    # Dirty now.
    assert st["isDirty"] is True
    assert store.status(pid)["isDirty"] is True

    # The draft carries the edited fields.
    draft = store.read_draft(pid)
    assert draft is not None
    assert draft["timeline"] == edited_timeline
    assert draft["scanMode"] == "and-only"


# --------------------------------------------------------------------------- #
# 3. save() promotes draft -> project, removes draft, clears dirty
# --------------------------------------------------------------------------- #
def test_save_promotes_draft(store, tmp_path):
    project = store.create("Promote Test")
    pid = project["id"]

    edited = {"timeline": {"bin": [{"clipId": "x"}], "textElements": []},
              "styleRecipeName": "Cinematic"}
    store.save_draft(pid, edited)
    assert store.status(pid)["isDirty"] is True

    saved = store.save(pid)

    # Draft file removed.
    draft_file = tmp_path / "video-editor-projects" / f"{pid}.draft.json"
    assert not draft_file.exists()
    assert store.read_draft(pid) is None

    # Not dirty.
    assert store.status(pid)["isDirty"] is False

    # Edited fields now live in the project.
    proj = store.read_project(pid)
    assert proj["styleRecipeName"] == "Cinematic"
    assert proj["timeline"] == {"bin": [{"clipId": "x"}], "textElements": []}
    assert saved["styleRecipeName"] == "Cinematic"


# --------------------------------------------------------------------------- #
# 4. save() with explicit editable body (no prior draft)
# --------------------------------------------------------------------------- #
def test_save_with_explicit_body_no_draft(store):
    project = store.create("Explicit Save")
    pid = project["id"]
    assert store.read_draft(pid) is None  # no draft yet

    store.save(pid, {"scanMode": "or-only",
                     "timeline": {"bin": [{"clipId": "z"}], "textElements": []}})

    proj = store.read_project(pid)
    assert proj["scanMode"] == "or-only"
    assert proj["timeline"]["bin"] == [{"clipId": "z"}]
    assert store.status(pid)["isDirty"] is False


# --------------------------------------------------------------------------- #
# 5. open() returns the DRAFT when newer (crash-recovery), else the project
# --------------------------------------------------------------------------- #
def test_open_returns_draft_when_newer_else_project(store):
    project = store.create("Open Test")
    pid = project["id"]

    # No draft -> returns the project, _meta present, not dirty.
    opened_clean = store.open(pid)
    assert "_meta" in opened_clean
    assert opened_clean["_meta"]["isDirty"] is False
    assert opened_clean["timeline"] == {"bin": [], "textElements": []}

    # Make a newer draft -> open() returns it.
    store.save_draft(pid, {"styleRecipeName": "DraftStyle",
                           "timeline": {"bin": [{"clipId": "d"}], "textElements": []}})
    opened_dirty = store.open(pid)
    assert opened_dirty["_meta"]["isDirty"] is True
    assert opened_dirty["styleRecipeName"] == "DraftStyle"
    assert opened_dirty["timeline"]["bin"] == [{"clipId": "d"}]


# --------------------------------------------------------------------------- #
# 6. save_as() duplicates under new id + name, preserves content, switches current
# --------------------------------------------------------------------------- #
def test_save_as_duplicates_and_switches_current(store):
    project = store.create("Original")
    pid = project["id"]
    store.save(pid, {"styleRecipeName": "Preserved",
                     "timeline": {"bin": [{"clipId": "orig"}], "textElements": []}})

    dup = store.save_as(pid, "Copy Of Original")
    new_pid = dup["id"]

    # New id + name.
    assert new_pid != pid
    assert dup["name"] == "Copy Of Original"

    # Content preserved.
    assert dup["styleRecipeName"] == "Preserved"
    assert dup["timeline"]["bin"] == [{"clipId": "orig"}]

    # Current switched to the new one.
    assert store.get_current_id() == new_pid

    # Original untouched.
    orig = store.read_project(pid)
    assert orig["name"] == "Original"
    assert orig["styleRecipeName"] == "Preserved"
    assert store.exists(pid)


# --------------------------------------------------------------------------- #
# 7. rename() changes name in project (and in-flight draft if present)
# --------------------------------------------------------------------------- #
def test_rename_updates_project_and_draft(store):
    project = store.create("Old Name")
    pid = project["id"]
    # In-flight draft present.
    store.save_draft(pid, {"styleRecipeName": "WIP"})

    renamed = store.rename(pid, "New Name")
    assert renamed["name"] == "New Name"
    assert store.read_project(pid)["name"] == "New Name"

    # The draft's name is kept consistent.
    draft = store.read_draft(pid)
    assert draft is not None
    assert draft["name"] == "New Name"

    # Reflected in the index listing.
    listing = store.list_projects()
    row = next(r for r in listing["projects"] if r["id"] == pid)
    assert row["name"] == "New Name"


# --------------------------------------------------------------------------- #
# 8. delete() removes both files, reassigns current to most-recent remaining / None
# --------------------------------------------------------------------------- #
def test_delete_removes_files_and_reassigns_current(store, tmp_path):
    base = tmp_path / "video-editor-projects"
    a = store.create("Alpha")
    time.sleep(0.01)
    b = store.create("Beta")
    time.sleep(0.01)
    c = store.create("Gamma")  # most recent, current
    assert store.get_current_id() == c["id"]

    # Give c a draft so we can confirm both files go.
    store.save_draft(c["id"], {"styleRecipeName": "x"})
    assert (base / f"{c['id']}.draft.json").exists()

    res = store.delete(c["id"])

    # Both files gone.
    assert not (base / f"{c['id']}.project.json").exists()
    assert not (base / f"{c['id']}.draft.json").exists()

    # Current reassigned to most-recent remaining (index is sorted updatedAt desc).
    remaining = store.list_projects()["projects"]
    expected_current = remaining[0]["id"]
    assert res["currentId"] == expected_current
    assert store.get_current_id() == expected_current
    assert c["id"] not in _ids({"projects": remaining})

    # Delete the rest -> current becomes None when empty.
    store.delete(remaining[0]["id"])
    last = store.list_projects()["projects"][0]["id"]
    res_empty = store.delete(last)
    assert res_empty["currentId"] is None
    assert store.get_current_id() is None
    assert store.list_projects()["projects"] == []


# --------------------------------------------------------------------------- #
# 9. status()/dirty timestamp semantics: draft.updatedAt > project.updatedAt
# --------------------------------------------------------------------------- #
def test_status_dirty_timestamp_semantics(store):
    project = store.create("Dirty Clock")
    pid = project["id"]

    # Freshly created, no draft -> clean.
    st0 = store.status(pid)
    assert st0["isDirty"] is False
    assert st0["draftAt"] is None
    assert st0["savedAt"] == project["updatedAt"]

    # A draft written after creation has a strictly greater updatedAt -> dirty.
    store.save_draft(pid, {"styleRecipeName": "later"})
    st1 = store.status(pid)
    assert st1["draftAt"] is not None
    assert st1["savedAt"] is not None
    assert st1["draftAt"] > st1["savedAt"]
    assert st1["isDirty"] is True

    # Saving advances the project clock past the (now-removed) draft -> clean.
    store.save(pid)
    st2 = store.status(pid)
    assert st2["draftAt"] is None
    assert st2["isDirty"] is False


# --------------------------------------------------------------------------- #
# 10. validate_refs(): ok / missing / stale
# --------------------------------------------------------------------------- #
def test_validate_refs_ok_missing_stale(store, tmp_path):
    # Real temp media files with known sizes.
    media = tmp_path / "media"
    media.mkdir()
    ok_file = media / "ok.mp4"
    ok_file.write_bytes(b"x" * 100)        # 100 bytes
    stale_file = media / "stale.mp4"
    stale_file.write_bytes(b"y" * 250)     # 250 bytes on disk

    timeline = {
        "bin": [
            # ok: resolves to an existing file, fingerprint matches.
            {"clipId": "ok", "documentId": 1, "filename": "ok.mp4",
             "mediaFingerprint": {"sizeBytes": 100}},
            # missing: resolver returns None.
            {"clipId": "missing", "documentId": 2, "filename": "gone.mp4"},
            # stale: file exists but fingerprint disagrees with on-disk size (999 != 250).
            {"clipId": "stale", "documentId": 3, "filename": "stale.mp4",
             "mediaFingerprint": {"sizeBytes": 999}},
        ],
        "textElements": [],
    }
    project = store.create("Refs", editable={"timeline": timeline})
    pid = project["id"]

    resolver_map = {1: str(ok_file), 3: str(stale_file)}  # doc 2 absent -> None

    def resolver(doc_id):
        return resolver_map.get(doc_id)

    report = store.validate_refs(pid, resolver)

    by_clip = {r["clipId"]: r["status"] for r in report["clips"]}
    assert by_clip == {"ok": "ok", "missing": "missing", "stale": "stale"}
    assert report["missing"] == 1
    assert report["stale"] == 1


# --------------------------------------------------------------------------- #
# 11. migrate_legacy_session()
# --------------------------------------------------------------------------- #
def test_migrate_legacy_session(store, tmp_path):
    legacy_path = tmp_path / "legacy_session.json"
    legacy = {
        "timeline": {"bin": [{"clipId": "legacy"}], "textElements": []},
        "scanMode": "or-only",
        "styleRecipeName": "Vintage",
        "clipOverrides": {"legacy": {"trimIn": 1.0}},
    }
    legacy_path.write_text(json.dumps(legacy))

    project = store.migrate_legacy_session(str(legacy_path))

    # A "Recovered session" project was created carrying the legacy content.
    assert project is not None
    assert project["name"] == "Recovered session"
    assert project["timeline"]["bin"] == [{"clipId": "legacy"}]
    assert project["scanMode"] == "or-only"
    assert project["styleRecipeName"] == "Vintage"
    assert project["clipOverrides"] == {"legacy": {"trimIn": 1.0}}

    # Legacy file renamed to *.migrated (not deleted).
    assert not legacy_path.exists()
    assert (tmp_path / "legacy_session.json.migrated").exists()

    # Second call is a no-op (a project now exists) -> None.
    # Recreate the legacy file to prove the no-op is driven by index state, not file absence.
    legacy_path.write_text(json.dumps(legacy))
    assert store.migrate_legacy_session(str(legacy_path)) is None


# --------------------------------------------------------------------------- #
# 12. ensure_current(): migrate / existing-current / fresh Untitled
# --------------------------------------------------------------------------- #
def test_ensure_current_migrates_legacy(tmp_path):
    store = ProjectStore(str(tmp_path / "p"))
    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(json.dumps({
        "timeline": {"bin": [{"clipId": "leg"}], "textElements": []},
        "scanMode": "both-and",
        "styleRecipeName": "Default",
        "clipOverrides": {},
    }))

    opened = store.ensure_current(legacy_path=str(legacy_path))
    assert opened["name"] == "Recovered session"
    assert opened["timeline"]["bin"] == [{"clipId": "leg"}]
    assert "_meta" in opened
    assert (tmp_path / "legacy.json.migrated").exists()


def test_ensure_current_returns_existing_current(store):
    project = store.create("Existing")
    pid = project["id"]
    opened = store.ensure_current()  # no legacy path
    assert opened["id"] == pid
    assert opened["name"] == "Existing"
    assert "_meta" in opened


def test_ensure_current_creates_untitled_when_empty(tmp_path):
    store = ProjectStore(str(tmp_path / "empty"))
    assert store.list_projects()["projects"] == []

    opened = store.ensure_current()  # empty store, no legacy
    assert opened["name"] == "Untitled"
    assert "_meta" in opened
    # And it is now persisted + current.
    assert store.get_current_id() == opened["id"]
    assert store.exists(opened["id"])
