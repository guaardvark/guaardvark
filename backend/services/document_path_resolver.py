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
_GENERATOR_OUTPUT_BASES = [
    "plugins/comfyui/ComfyUI/output",
    "data/outputs",
    "data/outputs/videos",
    "data/outputs/audio",
    "data/outputs/images",
    "data/outputs/videos/text-overlay",
    "data/outputs/videos/editor-renders",
]


def _candidates_for_doc(doc) -> list[Path]:
    """Build an ordered list of paths to try for `doc`.

    Order:
    1. Absolute Document.path (some legacy code stores absolute).
    2. UPLOAD_BASE + Document.path (the canonical join).
    3. project-root + Document.path (relative to repo root).
    4. Each known generator output base + basename(path).
    5. Each known generator output base + Document.filename.
    """
    candidates: list[Path] = []
    raw_path = (doc.path or "").lstrip("/")
    filename = doc.filename or ""

    if not raw_path and not filename:
        return candidates

    # 1. Absolute path stored as-is.
    if raw_path.startswith("/"):
        candidates.append(Path(raw_path))

    # 2. UPLOAD_BASE join — use existing helper if available.
    try:
        from backend.api.files_api import get_physical_path
        if raw_path:
            candidates.append(get_physical_path(raw_path))
    except Exception:
        pass

    # 3. Project-root relative.
    if raw_path:
        candidates.append(Path.cwd() / raw_path)

    # 4 + 5. Generator output bases — try basename(path) and filename.
    base_root = Path.cwd()
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


def resolve_document_path(doc) -> Optional[Path]:
    """Return the first candidate path that actually exists on disk.

    `doc` is a SQLAlchemy Document row. Returns None if no candidate
    resolves — caller should treat that as a 404.
    """
    if doc is None:
        return None

    for candidate in _candidates_for_doc(doc):
        try:
            if candidate.is_file():
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
