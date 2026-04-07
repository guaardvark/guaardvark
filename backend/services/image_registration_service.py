"""
Service to register batch-generated images into the Documents/Files system.
Copies images from data/outputs/batch_images/ into data/uploads/Images/<batch-name>/
and creates Folder + Document DB records.

Idempotent: safe to re-run for the same batch.
"""

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple

from flask import current_app

from backend.models import Folder, Document as DBDocument, db

logger = logging.getLogger(__name__)

IMAGES_ROOT_FOLDER_NAME = "Images"
# Path without leading slash — matches Folder model convention
IMAGES_ROOT_PATH = IMAGES_ROOT_FOLDER_NAME


def _get_upload_base() -> Path:
    """Return the uploads base directory from Flask config."""
    upload_folder = current_app.config.get("UPLOAD_FOLDER")
    if not upload_folder:
        raise ValueError("UPLOAD_FOLDER not configured")
    return Path(upload_folder)


def _ensure_images_root_folder() -> Folder:
    """
    Ensure the /Images root folder exists in the DB and on disk.
    Returns the existing or newly-created Folder row.
    """
    folder = Folder.query.filter_by(path=IMAGES_ROOT_PATH).first()
    if not folder:
        folder = Folder(
            name=IMAGES_ROOT_FOLDER_NAME,
            path=IMAGES_ROOT_PATH,
            parent_id=None,
        )
        db.session.add(folder)
        db.session.flush()  # get folder.id before commit
        logger.info(f"Created /Images root folder (id={folder.id})")

    # Always ensure physical directory exists
    physical = _get_upload_base() / IMAGES_ROOT_FOLDER_NAME
    physical.mkdir(parents=True, exist_ok=True)
    return folder


def _ensure_batch_folder(batch_name: str, parent: Folder) -> Folder:
    """
    Ensure a sub-folder /Images/<batch_name> exists in the DB and on disk.
    Returns the existing or newly-created Folder row.
    """
    folder_path = f"{IMAGES_ROOT_PATH}/{batch_name}"
    folder = Folder.query.filter_by(path=folder_path).first()
    if not folder:
        folder = Folder(
            name=batch_name,
            path=folder_path,
            parent_id=parent.id,
        )
        db.session.add(folder)
        db.session.flush()
        logger.info(f"Created batch folder {folder_path} (id={folder.id})")

    # Always ensure physical directory exists
    physical = _get_upload_base() / IMAGES_ROOT_FOLDER_NAME / batch_name
    physical.mkdir(parents=True, exist_ok=True)
    return folder


def _find_thumbnail(thumbnails_dir: Path, image_stem: str) -> Optional[Path]:
    """
    Find a thumbnail file matching the image stem.
    The batch generator creates .jpg thumbnails, but some batches may have .png.
    Returns the first matching thumbnail path, or None.
    """
    if not thumbnails_dir.exists():
        return None

    # Check common extensions in priority order
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        candidate = thumbnails_dir / (image_stem + ext)
        if candidate.exists():
            return candidate
    return None


def register_batch_images(
    batch_id: str,
    batch_output_dir: str,
    batch_name: Optional[str] = None,
) -> Tuple[Folder, List[DBDocument]]:
    """
    Register all images from a batch generation run into the Documents/Files system.

    This function is idempotent: re-running it for the same batch will skip
    images that already have a Document record (matched by path).

    Args:
        batch_id: The unique batch identifier (e.g. "test_batch_001").
        batch_output_dir: Absolute path to the batch output directory,
            e.g. "data/outputs/batch_images/test_batch_001".
        batch_name: Human-readable folder name. Falls back to batch_id.

    Returns:
        Tuple of (batch_folder, list_of_documents).

    Raises:
        FileNotFoundError: If the images/ subdirectory does not exist.
        ValueError: If UPLOAD_FOLDER is not configured.
    """
    folder_name = batch_name or batch_id
    output_dir = Path(batch_output_dir)
    images_dir = output_dir / "images"
    thumbnails_dir = output_dir / "thumbnails"

    if not images_dir.exists():
        raise FileNotFoundError(f"Images directory not found: {images_dir}")

    # --- DB folders ---
    root_folder = _ensure_images_root_folder()
    batch_folder = _ensure_batch_folder(folder_name, root_folder)

    # --- Physical directories ---
    upload_base = _get_upload_base()
    dest_dir = upload_base / IMAGES_ROOT_FOLDER_NAME / folder_name
    dest_thumbs_dir = dest_dir / "thumbnails"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_thumbs_dir.mkdir(parents=True, exist_ok=True)

    # --- Load batch metadata for per-image info ---
    batch_meta = {}
    meta_file = output_dir / "batch_metadata.json"
    if meta_file.exists():
        try:
            batch_meta = json.loads(meta_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Could not read batch metadata: {e}")

    # Build a lookup from image filename -> result metadata
    result_lookup = {}
    for result in batch_meta.get("results", []):
        img_path = result.get("image_path", "")
        img_filename = Path(img_path).name if img_path else ""
        if img_filename:
            result_lookup[img_filename] = result

    # --- Process each image ---
    documents: List[DBDocument] = []
    image_files = sorted(images_dir.glob("*"))

    for image_path in image_files:
        if not image_path.is_file():
            continue

        filename = image_path.name
        dest_path = dest_dir / filename
        # Document.path is relative to UPLOAD_FOLDER, no leading slash
        relative_path = f"{IMAGES_ROOT_FOLDER_NAME}/{folder_name}/{filename}"

        # Idempotency: skip if document already registered
        existing = DBDocument.query.filter_by(path=relative_path).first()
        if existing:
            documents.append(existing)
            continue

        # Copy image to uploads
        shutil.copy2(str(image_path), str(dest_path))

        # Copy thumbnail if it exists (handles .jpg, .png, etc.)
        thumb_src = _find_thumbnail(thumbnails_dir, image_path.stem)
        has_thumbnail = False
        if thumb_src is not None:
            dest_thumb_path = dest_thumbs_dir / thumb_src.name
            shutil.copy2(str(thumb_src), str(dest_thumb_path))
            has_thumbnail = True

        file_size = dest_path.stat().st_size
        file_ext = image_path.suffix.lower()

        # Pull per-image metadata from batch results
        result_meta = result_lookup.get(filename, {})
        prompt_text = (result_meta.get("metadata") or {}).get("original_prompt", "")

        doc = DBDocument(
            filename=filename,
            path=relative_path,
            type=file_ext,
            folder_id=batch_folder.id,
            size=file_size,
            index_status="NOT_INDEXED",
            is_code_file=False,
            file_metadata=json.dumps({
                "source": "batch_generation",
                "batch_id": batch_id,
                "has_thumbnail": has_thumbnail,
                "prompt": prompt_text,
            }),
            uploaded_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db.session.add(doc)
        documents.append(doc)

    db.session.commit()
    logger.info(
        f"Registered {len(documents)} images from batch {batch_id} "
        f"into {batch_folder.path}"
    )

    return batch_folder, documents
