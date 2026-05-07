"""Register production-pipeline outputs in the Documents/Folders hierarchy
so they surface in DocumentsPage and ImagesPage automatically.

Folder layout: project_<id>/productions/<prod_id>/<category>/
"""
from __future__ import annotations

from pathlib import Path

from backend.models import db, Production, Folder, Document


VALID_CATEGORIES = {"storyboard", "clips", "audio", "final"}


def _ensure_folder(name: str, parent_id: int | None, path: str) -> Folder:
    """Look up or create a folder by (parent_id, name). path is the unique
    full virtual path used for the Folder.path column."""
    f = Folder.query.filter_by(parent_id=parent_id, name=name).first()
    if f is not None:
        return f
    f = Folder(name=name, parent_id=parent_id, path=path)
    db.session.add(f)
    db.session.flush()
    return f


def register_production_output(
    *, production: Production, file_path: str, category: str,
) -> Document:
    """Create a Document row for a production output, building the folder
    hierarchy as needed. category must be one of {storyboard, clips, audio, final}.
    """
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"category must be one of {sorted(VALID_CATEGORIES)}, got {category!r}"
        )

    # Build the path: project_<n>/productions/<prod_id>/<category>
    if production.project_id is not None:
        root_name = f"project_{production.project_id}"
    else:
        root_name = "orphan"

    root = _ensure_folder(root_name, parent_id=None, path=root_name)
    productions = _ensure_folder(
        "productions", parent_id=root.id, path=f"{root.path}/productions"
    )
    prod_folder = _ensure_folder(
        str(production.id), parent_id=productions.id,
        path=f"{productions.path}/{production.id}",
    )
    leaf = _ensure_folder(
        category, parent_id=prod_folder.id,
        path=f"{prod_folder.path}/{category}",
    )

    p = Path(file_path)
    size = p.stat().st_size if p.exists() else 0

    doc = Document(
        filename=p.name,
        path=str(p),
        folder_id=leaf.id,
        size=size,
    )
    db.session.add(doc)
    db.session.commit()
    return doc
