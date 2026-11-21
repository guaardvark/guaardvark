"""Document → physical-path resolver.

`Document.path` is a virtual tree path (e.g.
'Videos/VideoBatch_04-28-2026_013/uuid/videos/wan22_t2v_00422.mp4').
`get_physical_path` (backend/api/files_api.py) joins UPLOAD_BASE + path,
which works when files are written under data/uploads/. ComfyUI
generations land under plugins/comfyui/ComfyUI/output/ — outside
UPLOAD_BASE — and `register_file`'s fallback path stores a constructed
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
    """True when ``path`` lies under this install's data folder or a known
    generator output base, so a row's absolute path never reaches other files."""
    root = _project_root()
    bases = [root / b for b in ("data", *_GENERATOR_OUTPUT_BASES)]
    try:
        from backend.config import OUTPUT_DIR, STORAGE_DIR
        bases += [Path(STORAGE_DIR), Path(OUTPUT_DIR)]
    except Exception:  # noqa: BLE001 — the repo-relative bases still apply
        pass
    try:
        real = path.resolve()
    except (OSError, ValueError):
        return False
    for base in bases:
        try:
            real.relative_to(base.resolve())
            return True
        except (OSError, ValueError):
            continue
    return False


def _candidates_for_doc(doc) -> list[Path]:
    """Build an ordered list of paths to try for `doc`.

    Order:
    1. Absolute Document.path, when it is inside this install's folders.
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
    #    way), only inside this install's own data and generator folders.
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


def _servable(candidate: Path) -> bool:
    """True when a candidate lies in the uploads folder or in this install's
    data and generator folders. Document.path is joined onto the install root
    and the generator bases as it is stored, so a row holding ``..`` segments
    or naming a file at the root (``.env``) must not resolve."""
    try:
        from backend.config import UPLOAD_DIR
        from backend.utils.path_guard import PathEscapesRoot, contained_path
        try:
            contained_path(UPLOAD_DIR, candidate)
            return True
        except PathEscapesRoot:
            pass
    except ImportError:
        pass
    return _inside_install_outputs(candidate)


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
