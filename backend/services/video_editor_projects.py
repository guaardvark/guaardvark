"""File-per-project store for the Video Editor's named projects.

Design (agreed 2026-05-30, see memory video-editor-project-save-load-design):
  data/video-editor-projects/
    index.json              catalog + currentId — fast gallery, no full-file parse
    {uuid}.project.json     last EXPLICITLY-saved state
    {uuid}.draft.json       autosave shadow; promoted to .project on explicit Save

Autosave = draft-buffer model: debounced edits write the draft; the named project
file only changes on explicit Save. `isDirty` = a draft exists that is newer than
the project (so a reload restores in-progress work and the UI can offer Revert).

This module is pure (no Flask) so it is unit-testable against a tmp dir. The Flask
layer in api/video_editor_api.py injects a `resolver(document_id) -> path|None` for
reference validation.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

PROJECT_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: str, data: Any) -> None:
    """Write JSON via temp + os.replace so a crash never leaves a half-written file."""
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "untitled"


class ProjectStore:
    """CRUD over the file-per-project directory. All times are ISO-8601 UTC."""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    # ---- paths -------------------------------------------------------------
    @property
    def _index_path(self) -> str:
        return os.path.join(self.base_dir, "index.json")

    @staticmethod
    def _check_pid(pid: str) -> str:
        """Guard against path traversal — ids are uuid4().hex (32 lowercase hex)."""
        if not isinstance(pid, str) or not re.fullmatch(r"[0-9a-f]{32}", pid):
            raise ValueError(f"invalid project id: {pid!r}")
        return pid

    def _project_path(self, pid: str) -> str:
        return os.path.join(self.base_dir, f"{self._check_pid(pid)}.project.json")

    def _draft_path(self, pid: str) -> str:
        return os.path.join(self.base_dir, f"{self._check_pid(pid)}.draft.json")

    # ---- index -------------------------------------------------------------
    def _read_index(self) -> dict[str, Any]:
        if not os.path.exists(self._index_path):
            return {"currentId": None, "projects": []}
        try:
            with open(self._index_path, "r", encoding="utf-8") as f:
                idx = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"currentId": None, "projects": []}
        idx.setdefault("currentId", None)
        idx.setdefault("projects", [])
        return idx

    def _write_index(self, idx: dict[str, Any]) -> None:
        _atomic_write_json(self._index_path, idx)

    def _index_entry(self, project: dict[str, Any]) -> dict[str, Any]:
        """The metadata-only row stored in index.json for fast listing."""
        bin_clips = (project.get("timeline") or {}).get("bin") or []
        return {
            "id": project["id"],
            "name": project.get("name", "Untitled"),
            "createdAt": project.get("createdAt"),
            "updatedAt": project.get("updatedAt"),
            "posterDocumentId": project.get("posterDocumentId"),
            "clipCount": len(bin_clips),
        }

    def _upsert_index(self, project: dict[str, Any], make_current: bool = False) -> None:
        idx = self._read_index()
        entry = self._index_entry(project)
        rows = [r for r in idx["projects"] if r.get("id") != project["id"]]
        rows.append(entry)
        rows.sort(key=lambda r: r.get("updatedAt") or "", reverse=True)
        idx["projects"] = rows
        if make_current:
            idx["currentId"] = project["id"]
        self._write_index(idx)

    # ---- current pointer ---------------------------------------------------
    def get_current_id(self) -> Optional[str]:
        return self._read_index().get("currentId")

    def set_current_id(self, pid: Optional[str]) -> None:
        idx = self._read_index()
        idx["currentId"] = pid
        self._write_index(idx)

    def list_projects(self) -> dict[str, Any]:
        idx = self._read_index()
        return {"currentId": idx.get("currentId"), "projects": idx.get("projects", [])}

    # ---- editable payload merge -------------------------------------------
    _EDITABLE_KEYS = (
        "timeline", "scanMode", "styleRecipeName", "clipOverrides", "plan", "ui",
    )

    def _blank_payload(self, pid: str, name: str) -> dict[str, Any]:
        now = _now()
        return {
            "schemaVersion": PROJECT_SCHEMA_VERSION,
            "id": pid,
            "name": name,
            "createdAt": now,
            "updatedAt": now,
            "posterDocumentId": None,
            "outputs": {"mltDocumentId": None, "mp4DocumentId": None, "lastRenderAt": None},
            "timeline": {"bin": [], "textElements": []},
            "scanMode": "both-and",
            "styleRecipeName": "Default",
            "clipOverrides": {},
            "plan": None,
            "ui": {},
        }

    def _merge_editable(self, base: dict[str, Any], editable: dict[str, Any]) -> dict[str, Any]:
        """Overlay the client-editable subset onto a project, preserving identity/metadata."""
        merged = dict(base)
        for k in self._EDITABLE_KEYS:
            if k in editable:
                merged[k] = editable[k]
        # Outputs are patched only by the render path, but accept an explicit patch.
        if isinstance(editable.get("outputs"), dict):
            merged["outputs"] = {**(base.get("outputs") or {}), **editable["outputs"]}
        if "posterDocumentId" in editable:
            merged["posterDocumentId"] = editable["posterDocumentId"]
        return merged

    # ---- read --------------------------------------------------------------
    def exists(self, pid: str) -> bool:
        return os.path.exists(self._project_path(pid))

    def read_project(self, pid: str) -> dict[str, Any]:
        with open(self._project_path(pid), "r", encoding="utf-8") as f:
            return json.load(f)

    def read_draft(self, pid: str) -> Optional[dict[str, Any]]:
        p = self._draft_path(pid)
        if not os.path.exists(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def _newer_draft(self, pid: str) -> Optional[dict[str, Any]]:
        """The draft IFF it is strictly newer than the saved project, else None.

        This is the single source of truth for 'is there unsaved work' and for
        which payload to use as the merge base — so a stale draft left behind by
        a crash-between-save-and-delete is never treated as live work.
        """
        draft = self.read_draft(pid)
        if draft is None:
            return None
        saved_at = self.read_project(pid).get("updatedAt")
        draft_at = draft.get("updatedAt")
        return draft if (draft_at and saved_at and draft_at > saved_at) else None

    def status(self, pid: str) -> dict[str, Any]:
        """Dirty-state: a draft newer than the saved project means unsaved work."""
        project = self.read_project(pid)
        draft = self.read_draft(pid)
        saved_at = project.get("updatedAt")
        draft_at = draft.get("updatedAt") if draft else None
        is_dirty = bool(draft_at and saved_at and draft_at > saved_at)
        return {"id": pid, "name": project.get("name"), "isDirty": is_dirty,
                "savedAt": saved_at, "draftAt": draft_at}

    def open(self, pid: str, *, make_current: bool = True) -> dict[str, Any]:
        """Return the working state to edit: the draft if it's newer (so a reload
        restores in-progress work), else the saved project. Includes _meta."""
        project = self.read_project(pid)
        draft = self.read_draft(pid)
        st = self.status(pid)
        working = draft if st["isDirty"] else project
        if make_current:
            self.set_current_id(pid)
        out = dict(working)
        out["_meta"] = st
        return out

    # ---- write -------------------------------------------------------------
    def create(self, name: str = "Untitled", *, editable: Optional[dict] = None) -> dict[str, Any]:
        pid = uuid.uuid4().hex
        project = self._blank_payload(pid, name)
        if editable:
            project = self._merge_editable(project, editable)
        _atomic_write_json(self._project_path(pid), project)
        self._upsert_index(project, make_current=True)
        return project

    def save_draft(self, pid: str, editable: dict[str, Any]) -> dict[str, Any]:
        """Autosave: write the draft shadow only. The saved project is untouched.

        Base on a *newer* draft (ongoing work) or the saved project — never on a
        stale leftover draft, which would silently drop the last explicit save.
        """
        base = self._newer_draft(pid) or self.read_project(pid)
        draft = self._merge_editable(base, editable)
        draft["updatedAt"] = _now()
        _atomic_write_json(self._draft_path(pid), draft)
        # Surface recent activity in the gallery without marking the project "saved".
        self._upsert_index({**draft, "updatedAt": draft["updatedAt"]})
        return self.status(pid)

    def save(self, pid: str, editable: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Explicit Save: promote the draft (or an explicit body) to the project file
        and clear the draft so isDirty → False."""
        # Always start from the newest state (a newer draft if dirty, else the
        # saved project); an explicit body then merges ON TOP so a partial save
        # body can never discard unsaved draft work.
        base = self._newer_draft(pid) or self.read_project(pid)
        project = self._merge_editable(base, editable) if editable is not None else base
        project["updatedAt"] = _now()
        project.setdefault("schemaVersion", PROJECT_SCHEMA_VERSION)
        _atomic_write_json(self._project_path(pid), project)
        # Draft is now redundant — remove it so dirty-state resets.
        draft_path = self._draft_path(pid)
        if os.path.exists(draft_path):
            os.remove(draft_path)
        self._upsert_index(project, make_current=True)
        return project

    def save_as(self, src_pid: str, name: str, editable: Optional[dict] = None) -> dict[str, Any]:
        """Duplicate the (optionally edited) current state under a new id + name."""
        base = self.read_draft(src_pid) or self.read_project(src_pid)
        if editable:
            base = self._merge_editable(base, editable)
        new_pid = uuid.uuid4().hex
        now = _now()
        project = {**base, "id": new_pid, "name": name,
                   "createdAt": now, "updatedAt": now,
                   "schemaVersion": PROJECT_SCHEMA_VERSION}
        _atomic_write_json(self._project_path(new_pid), project)
        self._upsert_index(project, make_current=True)
        return project

    def rename(self, pid: str, name: str) -> dict[str, Any]:
        """Rename is metadata-only and must NOT flip dirty→clean. If a dirty draft
        exists we leave the project's savedAt untouched and bump the draft so it
        stays newer (and carries the new name); otherwise we bump the project."""
        project = self.read_project(pid)
        project["name"] = name
        dirty_draft = self._newer_draft(pid)
        if dirty_draft is None:
            project["updatedAt"] = _now()
        _atomic_write_json(self._project_path(pid), project)
        # Keep any on-disk draft's name consistent so a recovery doesn't revert it.
        draft = self.read_draft(pid)
        if draft is not None:
            draft["name"] = name
            if dirty_draft is not None:
                draft["updatedAt"] = _now()  # remain strictly newer than the project
            _atomic_write_json(self._draft_path(pid), draft)
        self._upsert_index(project)
        return project

    def delete(self, pid: str) -> dict[str, Any]:
        proj_p, draft_p = self._project_path(pid), self._draft_path(pid)
        idx = self._read_index()
        in_index = any(r.get("id") == pid for r in idx["projects"])
        if not os.path.exists(proj_p) and not os.path.exists(draft_p) and not in_index:
            raise FileNotFoundError(pid)
        for p in (proj_p, draft_p):
            if os.path.exists(p):
                os.remove(p)
        idx["projects"] = [r for r in idx["projects"] if r.get("id") != pid]
        if idx.get("currentId") == pid:
            idx["currentId"] = idx["projects"][0]["id"] if idx["projects"] else None
        self._write_index(idx)
        return {"ok": True, "currentId": idx.get("currentId")}

    # ---- reference integrity ----------------------------------------------
    def validate_refs(
        self, pid: str, resolver: Callable[[Any], Optional[str]]
    ) -> dict[str, Any]:
        """Classify each bin clip ok|missing|stale via an injected document resolver.

        resolver(document_id) -> absolute path or None. 'stale' fires only when the
        clip carries a mediaFingerprint.sizeBytes that disagrees with the file on disk.
        """
        project = self.open(pid, make_current=False)
        clips = (project.get("timeline") or {}).get("bin") or []
        results = []
        for c in clips:
            doc_id = c.get("documentId")
            path = resolver(doc_id) if doc_id is not None else None
            if not path or not os.path.exists(path):
                status = "missing"
            else:
                status = "ok"
                fp = (c.get("mediaFingerprint") or {}).get("sizeBytes")
                if fp is not None:
                    try:
                        if os.path.getsize(path) != fp:
                            status = "stale"
                    except OSError:
                        status = "missing"
            results.append({"clipId": c.get("clipId"), "documentId": doc_id,
                            "filename": c.get("filename"), "status": status})
        return {"id": pid, "clips": results,
                "missing": sum(1 for r in results if r["status"] == "missing"),
                "stale": sum(1 for r in results if r["status"] == "stale")}

    # ---- legacy migration --------------------------------------------------
    def migrate_legacy_session(self, legacy_path: str) -> Optional[dict[str, Any]]:
        """One-shot: import the old single-slot session into a 'Recovered session'
        project. No-op if already migrated or the legacy file is absent. The legacy
        file is renamed to *.migrated (never deleted) so the operator can recover it."""
        if not os.path.exists(legacy_path):
            return None
        # Already migrated if any project exists.
        if self._read_index().get("projects"):
            return None
        try:
            with open(legacy_path, "r", encoding="utf-8") as f:
                session = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        project = self.create("Recovered session", editable={
            "timeline": session.get("timeline") or {"bin": [], "textElements": []},
            "scanMode": session.get("scanMode", "both-and"),
            "styleRecipeName": session.get("styleRecipeName", "Default"),
            "clipOverrides": session.get("clipOverrides") or {},
        })
        try:
            os.replace(legacy_path, legacy_path + ".migrated")
        except OSError:
            pass
        return project

    def ensure_current(self, legacy_path: Optional[str] = None) -> dict[str, Any]:
        """Resolve the project to open on load: migrate a legacy session if present,
        else the current project, else create a fresh Untitled. Always returns an
        opened working-state dict (with _meta)."""
        if legacy_path:
            migrated = self.migrate_legacy_session(legacy_path)
            if migrated:
                return self.open(migrated["id"])
        cur = self.get_current_id()
        if cur and self.exists(cur):
            return self.open(cur)
        # Fall back to the most-recent project file that still EXISTS (skip ghost
        # index rows whose .project.json was deleted/corrupted), else a new Untitled.
        idx = self._read_index()
        for row in idx["projects"]:
            if self.exists(row.get("id", "")):
                return self.open(row["id"])
        return self.open(self.create("Untitled")["id"])
