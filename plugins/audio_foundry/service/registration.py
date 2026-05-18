"""Register generated audio files with the main Guaardvark backend.

The backend exposes POST /api/outputs/register (backend/api/outputs_api.py) which
calls backend.services.output_registration.register_file() inside the Flask app
context — something the plugin service can't do itself because it runs in a
separate Python process.

Failure is non-fatal: the file is already on disk either way. We log and move on.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from backends.base import GenerationResult

logger = logging.getLogger(__name__)

# Module-level defaults; overridden per-call by bootstrap-injected config.
_DEFAULT_BACKEND_URL = "http://localhost:5002"
_DEFAULT_FOLDER = "Audio"


def register_output(
    result: GenerationResult,
    backend_url: str = _DEFAULT_BACKEND_URL,
    folder: str = _DEFAULT_FOLDER,
    timeout_s: float = 5.0,
) -> dict[str, Any] | None:
    """POST the file path + metadata to the backend, return the Document dict or None.

    The backend uses the file's actual location on disk — we don't move it here.
    `folder` must match one of the DEFAULT_FOLDERS the backend knows about.
    """
    payload = {
        "physical_path": str(Path(result.path).resolve()),
        "folder_name": folder,
        "file_metadata": result.meta,
    }
    try:
        response = httpx.post(
            f"{backend_url.rstrip('/')}/api/outputs/register",
            json=payload,
            timeout=timeout_s,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning(
            "Registration POST failed (non-fatal, file remains at %s): %s",
            result.path, e,
        )
        return None

    body = response.json()
    # The backend wraps data in success_response format: {success, message, data}
    return body.get("data")
