"""Document → physical-path resolver.

`Document.path` is a virtual tree path (e.g.
'Videos/VideoBatch_04-28-2026_013/uuid/videos/wan22_t2v_00422.mp4').
`get_physical_path` (backend/api/files_api.py) joins UPLOAD_BASE + path,
which works when files are written under data/uploads/. ComfyUI
generations land under plugins/comfyui/ComfyUI/output/ — outside
UPLOAD_BASE. `register_file` stores such a file by its real location
(relative to the install root), but older rows carry a constructed
virtual path with no link back to the real bytes.

This resolver tries every plausible location until one exists. Used by
the Video Editor render endpoint (and anywhere else that needs to
materialize a Document by id without caring where the bytes live).

The filename-structure plan (plans/2026-04-29-filename-structure.md)
establishes the long-term invariant that filename == basename(path);
this resolver bridges the gap for old rows that predate that invariant.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Known output bases for generators that write outside UPLOAD_DIR. New
# generators that land outside UPLOAD_DIR should add their base here.
def _project_root() -> Path:
    """Repo root the generator bases hang off; never the process cwd."""
    try:
        from backend.config import GUAARDVARK_ROOT
        return Path(GUAARDVARK_ROOT)
    except Exception:
        return Path(__file__).resolve().parents[2]


_GENERATOR_OUTPUT_BASES = [
    "plugins/comfyui/ComfyUI/output",
    "data/outputs",
    "data/outputs/videos",
    "data/outputs/audio",
    "data/outputs/images",
    "data/outputs/videos/text-overlay",
    "data/outputs/videos/editor-renders",
]


def _inside_install_outputs(path: Path) -> bool:
    """True when ``path``, symlinks followed, is a file in one of :func:`output_dirs`."""
    return _real_file_in(path, output_dirs()) is not None


# Folders directly under an output folder that hold records about people, not
# generated work: likeness-consent records and exported chat transcripts. They
# are read from disk by the code that owns them (consent_records,
# chat_export_service) and are never served, registered as documents or handed
# to another machine. A folder whose name starts with a dot is internal state.
PRIVATE_OUTPUT_FOLDERS = ("consent", "chat-exports")


def private_output_folder(path) -> Optional[str]:
    """The private folder ``path`` really sits in (its name), or None.

    Judged on the real location, so a link elsewhere that points into one of
    these folders is caught, and a file merely named like one is not."""
    try:
        real = Path(os.path.realpath(path))
    except (OSError, ValueError):
        return None
    for out in output_dirs():
        try:
            parts = real.relative_to(out).parts
        except ValueError:
            continue
        if len(parts) > 1 and (parts[0] in PRIVATE_OUTPUT_FOLDERS or parts[0].startswith(".")):
            return parts[0]
    return None


def _real_file_in(path, roots: list[Path]) -> Optional[Path]:
    """The real location of ``path`` when it is a file inside one of ``roots``
    and not in a private output folder."""
    from backend.utils.path_guard import PathEscapesRoot, contained_path
    try:
        real = os.path.realpath(path)
        if not os.path.isfile(real):
            return None
    except (OSError, ValueError):
        return None
    if private_output_folder(real) is not None:
        return None
    for root in roots:
        try:
            return Path(contained_path(root, real))
        except PathEscapesRoot:
            continue
    return None


def _candidates_for_doc(doc) -> list[Path]:
    """Build an ordered list of paths to try for `doc`.

    Order:
    1. Absolute Document.path, when it is inside an output folder.
    2. UPLOAD_BASE + Document.path (the canonical join).
    3. project-root + Document.path (relative to repo root).
    4. Each known generator output base + basename(path).
    5. Each known generator output base + Document.filename.
    """
    candidates: list[Path] = []
    stored = doc.path or ""
    raw_path = stored.lstrip("/")
    filename = doc.filename or ""

    if not raw_path and not filename:
        return candidates

    # 1. Absolute path stored as-is (the Film Crew registers its outputs this
    #    way), only when it is in an output folder.
    if stored.startswith("/") and _inside_install_outputs(Path(stored)):
        candidates.append(Path(stored))

    # 2. UPLOAD_BASE join — use existing helper if available.
    try:
        from backend.api.files_api import get_physical_path
        if raw_path:
            candidates.append(get_physical_path(raw_path))
    except Exception:
        pass

    # 3. Project-root relative.
    base_root = _project_root()
    if raw_path:
        candidates.append(base_root / raw_path)

    # 4 + 5. Generator output bases — try basename(path) and filename.
    leaf_path = Path(raw_path).name if raw_path else ""
    for base in _GENERATOR_OUTPUT_BASES:
        full_base = base_root / base
        if leaf_path:
            candidates.append(full_base / leaf_path)
            # Also try the path-as-is under the generator base (in case the
            # virtual path mirrors the on-disk layout below the base, like
            # 'Videos/VideoBatch_.../uuid/videos/wan22.mp4' under comfyui).
            if raw_path:
                candidates.append(full_base / raw_path)
        if filename and filename != leaf_path:
            candidates.append(full_base / filename)

    return candidates


def _uploads_root() -> Optional[Path]:
    """The uploads folder, read the way the Files routes read it: the running
    app's UPLOAD_FOLDER (set once at start from backend.config.UPLOAD_DIR),
    else that setting itself when there is no app context (a worker, a script).
    The candidates below are built from the app's folder, so the check on them
    must use the same one."""
    try:
        from backend.api.files_api import get_upload_base_path
        return get_upload_base_path()
    except Exception:  # noqa: BLE001 - no app context, or the key is unset
        pass
    try:
        from backend.config import UPLOAD_DIR
        return Path(UPLOAD_DIR)
    except ImportError:
        return None


def _servable(candidate: Path) -> bool:
    """True when :func:`servable_file` accepts ``candidate``."""
    return servable_file(candidate) is not None


def file_outside_uploads(doc) -> Optional[Path]:
    """The file a row names by its own location when that file lives outside
    the uploads folder (a text overlay, an editor render), else None.

    Only the row's stored path is followed: absolute, or relative to the
    install root. The name-based guesses resolve_document_path also tries can
    land on another document's file of the same name, which is fine for
    serving a best effort and wrong for moving or deleting. The path returned
    is the stored one, not its link target, so deleting it removes only the
    row's own entry; its real location must still be in an output folder.
    """
    stored = (getattr(doc, "path", None) or "").strip()
    if not stored:
        return None
    candidate = Path(os.path.abspath(stored if stored.startswith("/") else _project_root() / stored))
    uploads = _uploads_root()
    try:
        from backend.utils.path_guard import PathEscapesRoot, contained_path
        if uploads is None:
            return None
        try:
            contained_path(str(uploads), candidate)
            return None  # inside uploads: the ordinary join handles it
        except PathEscapesRoot:
            pass
        if not _inside_install_outputs(candidate):
            return None
        return candidate
    except (OSError, ValueError, ImportError):
        return None


def resolve_document_path(doc) -> Optional[Path]:
    """Return the first candidate path that actually exists on disk.

    `doc` is a SQLAlchemy Document row. Returns None if no candidate
    resolves — caller should treat that as a 404.
    """
    if doc is None:
        return None

    for candidate in _candidates_for_doc(doc):
        try:
            if candidate.is_file() and _servable(candidate):
                return candidate.resolve()
        except (OSError, ValueError):
            continue

    logger.debug(
        "resolve_document_path: no candidate exists for doc id=%s path=%s filename=%s",
        getattr(doc, "id", "?"),
        getattr(doc, "path", "?"),
        getattr(doc, "filename", "?"),
    )
    return None


def resolved_files(doc) -> list[Path]:
    """Every distinct servable file ``doc`` could resolve to, in the order
    :func:`resolve_document_path` tries them."""
    found: list[Path] = []
    if doc is None:
        return found
    for candidate in _candidates_for_doc(doc):
        try:
            if candidate.is_file() and _servable(candidate):
                real = candidate.resolve()
                if real not in found:
                    found.append(real)
        except (OSError, ValueError):
            continue
    return found


def output_dirs() -> list[Path]:
    """Folders generators write to outside the uploads folder (OUTPUT_DIR, the
    Film Crew's ``<STORAGE_DIR>/outputs`` and the known generator output
    bases), symlinks resolved."""
    root = _project_root()
    dirs = [root / base for base in _GENERATOR_OUTPUT_BASES]
    try:
        from backend.config import OUTPUT_DIR, STORAGE_DIR
        dirs[:0] = [Path(OUTPUT_DIR), Path(STORAGE_DIR) / "outputs"]
    except Exception:  # noqa: BLE001 — the repo-relative bases still apply
        pass
    return _without_install_folders(Path(os.path.realpath(d)) for d in dirs)


def _without_install_folders(dirs) -> list[Path]:
    """``dirs`` without any folder that is, or contains, the install or its
    data folder: an output folder linked there would expose keys."""
    held = [_project_root(), _project_root() / "data"]
    try:
        from backend.config import STORAGE_DIR
        held.append(Path(STORAGE_DIR))
    except Exception:  # noqa: BLE001 — the install root still applies
        pass
    held = [Path(os.path.realpath(h)) for h in held]
    return [d for d in dirs if not any(h == d or d in h.parents for h in held)]


def serve_roots() -> list[Path]:
    """Folders a stored file may be served from: the uploads folder and
    :func:`output_dirs`, symlinks resolved. The rest of the data folder holds
    keys and internal state."""
    uploads = _uploads_root()
    roots = [Path(os.path.realpath(uploads))] if uploads is not None else []
    return _without_install_folders(roots) + output_dirs()


def servable_file(path, *also: str) -> Optional[Path]:
    """The real location of ``path`` (symlinks and ``..`` resolved) when it is a
    file inside :func:`serve_roots` or one of the folders ``also``, else None.
    Checked when a file is served, so a link swapped in after a row was made
    cannot reach other files."""
    extra = _without_install_folders(Path(os.path.realpath(d)) for d in also)
    return _real_file_in(path, extra + serve_roots())


def resolve_by_id(doc_id) -> Optional[Path]:
    """Convenience: load Document by id and resolve its path."""
    try:
        from backend.models import Document, db
    except Exception:
        return None
    doc = db.session.get(Document, int(doc_id))
    if doc is None:
        return None
    return resolve_document_path(doc)
