"""
Output Registration Service
Routes generated files into data/uploads/ with Bates-stamp naming
and creates Folder + Document DB records so they appear in DocumentsPage.

Naming convention:
  Files:   ImageGen_04-02-2026_001.png, VideoGen_04-02-2026_001.mp4
  Batches: ImageBatch_04-02-2026_001/, VideoBatch_04-02-2026_001/
  Sequence resets daily, increments per type.
"""

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from backend.config import UPLOAD_DIR
from backend.models import Document as DBDocument, Folder, db

logger = logging.getLogger(__name__)

# The holy trinity of default folders — like Documents/Pictures/Videos on any OS
DEFAULT_FOLDERS = ["Images", "Videos", "Code"]

# Bates prefix per content type
BATES_PREFIXES = {
    "image": "ImageGen",
    "image_batch": "ImageBatch",
    "video": "VideoGen",
    "video_batch": "VideoBatch",
    "code": "CodeGen",
    "data": "DataGen",
}


def _today_stamp() -> str:
    """MM-DD-YYYY format for the Bates stamp."""
    return datetime.now().strftime("%m-%d-%Y")


def _next_sequence(directory: Path, prefix: str, date_stamp: str) -> int:
    """Scan directory to find the next available sequence number for today."""
    pattern = re.compile(rf"^{re.escape(prefix)}_{re.escape(date_stamp)}_(\d{{3}})")
    max_seq = 0
    if directory.exists():
        for entry in directory.iterdir():
            match = pattern.match(entry.name)
            if match:
                max_seq = max(max_seq, int(match.group(1)))
    return max_seq + 1


def bates_name(content_type: str, extension: str = "", parent_dir: Optional[Path] = None) -> str:
    """
    Generate a Bates-stamped filename.

    Args:
        content_type: One of the BATES_PREFIXES keys (image, video, code, etc.)
        extension: File extension including dot (e.g. ".png", ".mp4")
        parent_dir: Directory to scan for existing files to determine sequence number.
                    If None, uses the default folder for this content type.

    Returns:
        Something like "ImageGen_04-02-2026_001.png"
    """
    prefix = BATES_PREFIXES.get(content_type, content_type)
    date_stamp = _today_stamp()

    if parent_dir is None:
        # Guess the folder from content type
        folder_map = {"image": "Images", "image_batch": "Images", "video": "Videos",
                      "video_batch": "Videos", "code": "Code", "data": "Code"}
        folder_name = folder_map.get(content_type, "")
        parent_dir = Path(UPLOAD_DIR) / folder_name

    seq = _next_sequence(parent_dir, prefix, date_stamp)
    return f"{prefix}_{date_stamp}_{seq:03d}{extension}"


def ensure_default_folders():
    """
    Create the default OS-style folders on disk and in the database.
    Safe to call multiple times — skips anything that already exists.
    """
    upload_base = Path(UPLOAD_DIR)
    upload_base.mkdir(parents=True, exist_ok=True)

    for folder_name in DEFAULT_FOLDERS:
        folder_path = upload_base / folder_name
        folder_path.mkdir(parents=True, exist_ok=True)

        # Check if DB record exists
        existing = Folder.query.filter_by(path=folder_name).first()
        if not existing:
            new_folder = Folder(name=folder_name, path=folder_name, parent_id=None)
            db.session.add(new_folder)
            logger.info(f"Created default folder: {folder_name}")

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Could not create default folders in DB (may already exist): {e}")


def ensure_subfolder(parent_folder_name: str, subfolder_name: str) -> Folder:
    """
    Ensure a subfolder exists under a parent folder (e.g. Images/ImageBatch_04-02-2026_001).
    Creates both the physical directory and DB record.
    Returns the Folder object.
    """
    parent_path = parent_folder_name
    child_path = f"{parent_path}/{subfolder_name}"

    # Ensure physical dir
    physical = Path(UPLOAD_DIR) / child_path
    physical.mkdir(parents=True, exist_ok=True)

    # Check if DB record exists
    existing = Folder.query.filter_by(path=child_path).first()
    if existing:
        return existing

    # Get parent folder ID
    parent = Folder.query.filter_by(path=parent_path).first()
    parent_id = parent.id if parent else None

    new_folder = Folder(name=subfolder_name, path=child_path, parent_id=parent_id)
    db.session.add(new_folder)
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Could not create subfolder record for {child_path}: {e}")
        # Re-query in case of race condition
        return Folder.query.filter_by(path=child_path).first()

    logger.info(f"Created subfolder: {child_path}")
    return new_folder


def register_file(
    physical_path: str,
    folder_name: str,
    filename: Optional[str] = None,
    subfolder_name: Optional[str] = None,
    file_type: Optional[str] = None,
    file_metadata: Optional[dict] = None,
) -> Optional[DBDocument]:
    """
    Register a generated file in the database so DocumentsPage can see it.

    Args:
        physical_path: Absolute path to the file on disk (must already exist)
        folder_name: Top-level folder name (e.g. "Images", "Videos", "Code")
        filename: Display name for the file. If None, uses the file's actual name.
        subfolder_name: Optional subfolder (e.g. "ImageBatch_04-02-2026_001")
        file_type: File extension (e.g. ".png"). Auto-detected if None.
        file_metadata: Optional JSON metadata dict.

    Returns:
        The created Document record, or None on failure.
    """
    p = Path(physical_path)
    if not p.exists():
        logger.warning(f"Cannot register non-existent file: {physical_path}")
        return None

    if filename is None:
        filename = p.name

    if file_type is None:
        file_type = p.suffix.lower()

    # Derive the DB path from the actual physical location relative to UPLOAD_DIR
    # so the download endpoint can find the file where it really lives
    upload_base = Path(UPLOAD_DIR)
    try:
        db_path = str(p.relative_to(upload_base))
    except ValueError:
        # File is outside UPLOAD_DIR — fall back to constructed path
        if subfolder_name:
            db_path = f"{folder_name}/{subfolder_name}/{filename}"
        else:
            db_path = f"{folder_name}/{filename}"

    # Ensure folder record exists
    if subfolder_name:
        folder = ensure_subfolder(folder_name, subfolder_name)
        folder_id = folder.id if folder else None
    else:
        parent = Folder.query.filter_by(path=folder_name).first()
        folder_id = parent.id if parent else None

    # Check for duplicate path
    existing = DBDocument.query.filter_by(path=db_path).first()
    if existing:
        logger.debug(f"Document already registered: {db_path}")
        return existing

    # File size
    try:
        file_size = p.stat().st_size
    except OSError:
        file_size = None

    import json
    metadata_str = json.dumps(file_metadata) if file_metadata else None

    doc = DBDocument(
        filename=filename,
        path=db_path,
        type=file_type,
        folder_id=folder_id,
        size=file_size,
        index_status="STORED",
        file_metadata=metadata_str,
        uploaded_at=datetime.now(),
    )
    db.session.add(doc)
    try:
        db.session.commit()
        logger.info(f"Registered output: {db_path} (doc id={doc.id})")
        return doc
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to register file {db_path}: {e}")
        return None


def get_output_dir(folder_name: str, subfolder_name: Optional[str] = None) -> Path:
    """
    Get the physical output directory path under data/uploads/.
    Creates the directory if it doesn't exist.
    """
    if subfolder_name:
        path = Path(UPLOAD_DIR) / folder_name / subfolder_name
    else:
        path = Path(UPLOAD_DIR) / folder_name
    path.mkdir(parents=True, exist_ok=True)
    return path
