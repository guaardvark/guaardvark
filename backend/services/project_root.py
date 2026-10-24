"""A project's folder on disk.

A project's root is its ``root_path`` when that is an existing folder, else the
top-most Library folder linked to the project and marked as a repository. A
chat in the project lists, searches and reads code there, and loads the root's
instructions file (``GUAARDVARK.md``, else ``AGENTS.md`` or ``CLAUDE.md``) into
every turn.

A root must be a folder Bulk Import may read: inside the home folder or a
configured import root, not hidden, and not Guaardvark's own data or backup
folders (its uploads folder is allowed).
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def validate_root_path(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """``(resolved_path, None)`` for an allowed folder, ``(None, None)`` to clear
    the root, or ``(None, reason)`` when the folder may not be used."""
    if raw is None or not str(raw).strip():
        return None, None
    from backend.api.files_api import resolve_import_directory

    path, refusal = resolve_import_directory(os.path.expanduser(str(raw).strip()))
    if refusal:
        return None, refusal.replace("Bulk Import can only use folders in", "A project folder must be in")
    if not os.path.isdir(path):
        return None, f"{raw} is not a folder on this machine"
    return str(path), None


def _linked_repository_folder(project_id: int) -> Optional[str]:
    from backend.models import Folder, db
    from backend.api.files_api import get_physical_path

    folders = (
        db.session.query(Folder)
        .filter(Folder.project_id == project_id, Folder.is_repository.is_(True))
        .all()
    )
    if not folders:
        return None
    top = min(folders, key=lambda f: (f.path.count("/"), f.path))
    physical = get_physical_path(top.path)
    return str(physical) if physical.is_dir() else None


def project_root_for(project_id) -> Optional[str]:
    """The project's folder on disk, or None when it has none."""
    if not project_id:
        return None
    try:
        from backend.models import Project, db

        project = db.session.get(Project, int(project_id))
        if project is None:
            return None
        if project.root_path and os.path.isdir(project.root_path):
            return os.path.realpath(project.root_path)
        return _linked_repository_folder(project.id)
    except Exception as e:  # noqa: BLE001 - a lookup failure means no root, never a failed turn
        logger.warning(f"Could not resolve the folder of project {project_id}: {e}")
        return None


def turn_project_root(tool) -> Optional[str]:
    """The folder of the chat turn a tool is running in, or None over MCP or in a
    turn without one."""
    from backend.utils.backend_http import is_mcp_transport

    if is_mcp_transport(tool):
        return None
    root = (getattr(tool, "_context", None) or {}).get("project_root")
    return root if root and os.path.isdir(root) else None
