"""Regression tests for bugs found in the 2026-05-30 review of ProjectStore.

Each test reproduces the scenario that WOULD trigger the bug, so it fails against
the pre-fix code and passes after. Numbers reference the review findings.
"""

from __future__ import annotations

import json

import pytest

from backend.services.video_editor_projects import ProjectStore


def _store(tmp_path):
    return ProjectStore(str(tmp_path / "projects"))


def _bin(clip_id):
    return {"timeline": {"bin": [{"clipId": clip_id, "documentId": 1, "filename": f"{clip_id}.mp4",
                                   "kind": "video"}], "textElements": []}}


def test_stale_leftover_draft_is_not_used_as_autosave_base(tmp_path):
    """#1 — a draft older than the project (crash between save and draft-delete)
    must NOT become the merge base; the last explicit save must survive."""
    s = _store(tmp_path)
    p = s.create("P")
    s.save(p["id"], editable=_bin("A"))                      # project now holds clip A, no draft
    # Forge a stale leftover draft with OLD content + an older timestamp.
    stale = {**s.read_project(p["id"]), **_bin("OLD"), "updatedAt": "2000-01-01T00:00:00+00:00"}
    with open(s._draft_path(p["id"]), "w", encoding="utf-8") as f:
        json.dump(stale, f)
    s.save_draft(p["id"], {"scanMode": "motion"})            # autosave a small edit
    draft = s.read_draft(p["id"])
    assert [c["clipId"] for c in draft["timeline"]["bin"]] == ["A"]  # not "OLD"
    assert draft["scanMode"] == "motion"


def test_explicit_save_with_partial_body_keeps_draft_work(tmp_path):
    """#2 — Save with a partial body must merge ON TOP of the newer draft, not
    rebase on the (older) project and drop unsaved draft content."""
    s = _store(tmp_path)
    p = s.create("P")
    s.save_draft(p["id"], _bin("A"))                         # dirty draft has clip A
    s.save(p["id"], editable={"scanMode": "motion"})         # partial body, no timeline
    proj = s.read_project(p["id"])
    assert [c["clipId"] for c in proj["timeline"]["bin"]] == ["A"]   # A preserved
    assert proj["scanMode"] == "motion"
    assert s.status(p["id"])["isDirty"] is False             # draft consumed


def test_rename_while_dirty_keeps_dirty_and_edits(tmp_path):
    """#3 — rename must not flip dirty→clean or hide unsaved draft edits."""
    s = _store(tmp_path)
    p = s.create("Old")
    s.save_draft(p["id"], _bin("A"))
    assert s.status(p["id"])["isDirty"] is True
    s.rename(p["id"], "New")
    st = s.status(p["id"])
    assert st["isDirty"] is True and st["name"] == "New"
    opened = s.open(p["id"])
    assert opened["name"] == "New"
    assert [c["clipId"] for c in opened["timeline"]["bin"]] == ["A"]  # draft edits still visible


def test_delete_unknown_id_raises(tmp_path):
    """#6 — deleting a non-existent (but well-formed) id is a 404, not a silent ok."""
    s = _store(tmp_path)
    with pytest.raises(FileNotFoundError):
        s.delete("0" * 32)


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "..", "DEADBEEF", "short", ""])
def test_path_traversal_ids_rejected(tmp_path, bad):
    """#13 — only uuid4-hex ids are accepted; traversal-ish ids raise ValueError."""
    s = _store(tmp_path)
    with pytest.raises(ValueError):
        s.open(bad)


def test_ensure_current_skips_ghost_index_rows(tmp_path):
    """#5 — a currentId / index row whose .project.json is gone must not 500;
    ensure_current falls back to an existing project."""
    s = _store(tmp_path)
    p1 = s.create("P1")
    p2 = s.create("P2")                                      # current = P2
    import os
    os.remove(s._project_path(p2["id"]))                    # P2 becomes a ghost row, still current
    opened = s.ensure_current()
    assert opened["_meta"]["id"] == p1["id"]                # skipped the ghost, opened P1
