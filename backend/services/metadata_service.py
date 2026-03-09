# backend/services/metadata_service.py   Version 1.1
# Updated META_PATH to point at the actual storage/docstore.json metadata file

import json
import logging
import os

logger = logging.getLogger("backend.services.metadata_service")

# Project root (backend/) -> use storage folder one level up
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# Point at the real metadata JSON in storage using config
from backend.config import STORAGE_DIR
META_PATH = os.path.join(STORAGE_DIR, "docstore.json")

# In-memory metadata map
metadata_map = {}


def load_metadata():
    """Load metadata from JSON file into memory."""
    global metadata_map
    try:
        with open(META_PATH, "r") as f:
            data = json.load(f)
        metadata_map = data
        logger.info(f"Loaded metadata from {META_PATH}, {len(metadata_map)} items.")
    except FileNotFoundError:
        logger.warning(
            f"Metadata file not found: {META_PATH}. Creating empty metadata file."
        )
        try:
            os.makedirs(os.path.dirname(META_PATH), exist_ok=True)
            with open(META_PATH, "w", encoding="utf-8") as f:
                json.dump({}, f)
            metadata_map = {}
            logger.info(f"Created new metadata file at {META_PATH}")
        except Exception as create_err:
            logger.error(
                f"Failed to create metadata file {META_PATH}: {create_err}",
                exc_info=True,
            )
            metadata_map = {}
    except Exception as e:
        logger.error(f"Error loading metadata from {META_PATH}: {e}", exc_info=True)


def get_metadata(doc_id: int) -> dict:
    """Get metadata for a specific document ID."""
    return metadata_map.get(str(doc_id), {})


def get_docs_by_tag(tag: str) -> list[int]:
    """Return list of document IDs matching a tag."""
    matches = []
    for doc_id, meta in metadata_map.items():
        tags = meta.get("tags", [])
        if tags and tag.lower() in [t.lower() for t in tags]:
            matches.append(int(doc_id))
    return matches


def get_docs_by_project(project_id: str) -> list[int]:
    """Return list of document IDs for a given project ID."""
    matches = []
    for doc_id, meta in metadata_map.items():
        if meta.get("project_id") == project_id:
            matches.append(int(doc_id))
    return matches


# Initialize metadata on import
load_metadata()
