"""External output registration API.

Thin HTTP wrapper around backend.services.output_registration.register_file so
external services (plugin processes running in their own Python interpreters —
audio_foundry, comfyui, etc.) can write a file to disk and then ask the backend
to add a Document row pointing at it, without needing the Flask app context
themselves.

The in-process callers (batch_video_generator, comfyui_video_generator) keep
calling register_file() directly — this endpoint exists specifically for
cross-process callers.
"""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, request

from backend.services.output_registration import register_file
from backend.utils.response_utils import error_response, success_response

logger = logging.getLogger(__name__)

outputs_bp = Blueprint("outputs_api", __name__, url_prefix="/api/outputs")


@outputs_bp.route("/register", methods=["POST"])
def register_output():
    """Register a file that already exists on disk as a Document row.

    Body (JSON):
        physical_path (required): absolute path to the file on disk
        folder_name   (required): top-level folder ("Audio", "Images", "Videos", ...)
        filename      (optional): display name; defaults to actual file name
        subfolder_name(optional): creates/uses a subfolder under folder_name
        file_type     (optional): extension like ".wav"; auto-detected if omitted
        file_metadata (optional): arbitrary dict stored as JSON on the row

    Returns the created/existing Document row as JSON.
    """
    payload = request.get_json(silent=True) or {}

    physical_path = payload.get("physical_path")
    folder_name = payload.get("folder_name")
    if not physical_path or not folder_name:
        return error_response(
            "physical_path and folder_name are required",
            status_code=400,
            error_code="MISSING_FIELDS",
        )

    if not Path(physical_path).is_file():
        return error_response(
            f"File does not exist: {physical_path}",
            status_code=404,
            error_code="FILE_NOT_FOUND",
        )

    try:
        doc = register_file(
            physical_path=physical_path,
            folder_name=folder_name,
            filename=payload.get("filename"),
            subfolder_name=payload.get("subfolder_name"),
            file_type=payload.get("file_type"),
            file_metadata=payload.get("file_metadata"),
        )
    except Exception as e:
        logger.exception("register_file failed for %s", physical_path)
        return error_response(
            f"Registration failed: {type(e).__name__}: {e}",
            status_code=500,
            error_code="REGISTRATION_ERROR",
        )

    if doc is None:
        return error_response(
            "Registration returned no Document — see backend logs",
            status_code=500,
            error_code="REGISTRATION_ERROR",
        )

    return success_response(
        data=doc.to_dict(),
        message="Output registered",
        status_code=201,
    )
