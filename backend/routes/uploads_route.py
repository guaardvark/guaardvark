import os
import logging

from flask import Blueprint, abort, current_app, send_from_directory, Response

uploads_bp = Blueprint("uploads_api", __name__)
logger = logging.getLogger(__name__)


@uploads_bp.route("/api/uploads/<path:filename>", methods=["GET"])
def get_upload(filename):
    # Check if this image should be proxied from master server
    try:
        from backend.utils.interconnector_image_utils import (
            should_use_master_image_repository,
            proxy_image_from_master,
        )

        image_path = f"uploads/{filename}"
        if should_use_master_image_repository():
            # Try to proxy from master
            success, image_data, error = proxy_image_from_master(image_path)

            if success and image_data:
                # Determine content type from filename
                from werkzeug.utils import secure_filename
                from mimetypes import guess_type

                content_type, _ = guess_type(filename)
                if not content_type:
                    content_type = "application/octet-stream"

                logger.info(f"Proxied image from master: {filename}")
                return Response(
                    image_data,
                    mimetype=content_type,
                    headers={"Cache-Control": "public, max-age=3600"},
                )
            elif error:
                logger.warning(
                    f"Failed to proxy image from master: {error}, falling back to local"
                )
                # Fall through to local storage
    except Exception as e:
        logger.error(f"Error checking master image repository: {e}")
        # Fall through to local storage

    # Local storage fallback
    uploads_dir = os.path.abspath(current_app.config["UPLOAD_FOLDER"])
    safe_path = os.path.normpath(os.path.abspath(os.path.join(uploads_dir, filename)))
    # Prevent directory traversal - path must be within uploads_dir and not the directory itself
    if not safe_path.startswith(uploads_dir + os.sep):
        abort(403, description="Invalid file path.")
    if not os.path.isfile(safe_path):
        abort(404, description="File not found.")
    rel_path = os.path.relpath(safe_path, uploads_dir)
    return send_from_directory(uploads_dir, rel_path)
