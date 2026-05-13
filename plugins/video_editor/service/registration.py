"""POST finished outputs to the main Guaardvark backend as Document rows.

The contract matches the audio_foundry plugin: /api/outputs/register with
{physical_path, folder_name, file_metadata}. Failure is non-fatal — the file
is on disk either way; only the DocumentsPage indexing is lost.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


def register_output(
    physical_path: str | Path,
    *,
    backend_url: str = "http://localhost:5002",
    folder: str = "Videos",
    file_metadata: Optional[dict[str, Any]] = None,
    timeout_s: float = 5.0,
) -> Optional[dict[str, Any]]:
    """POST the file path + metadata to the backend; return the Document dict or None."""
    payload = {
        "physical_path": str(Path(physical_path).resolve()),
        "folder_name": folder,
        "file_metadata": file_metadata or {},
    }
    url = f"{backend_url.rstrip('/')}/api/outputs/register"
    try:
        response = httpx.post(url, json=payload, timeout=timeout_s)
        response.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("registration failed for %s (non-fatal): %s", physical_path, e)
        return None
    body = response.json()
    return body.get("data")
