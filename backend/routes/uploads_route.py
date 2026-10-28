import os
import logging

from flask import Blueprint, abort, current_app, send_from_directory, Response
from backend.utils.path_guard import PathEscapesRoot, contained, contained_path

uploads_bp = Blueprint("uploads_api", __name__)
logger = logging.getLogger(__name__)


@uploads_bp.route("/api/uploads/<path:filename>", methods=["GET"])
def get_upload(filename):
    # This box's own copy first. The master is asked only on a client that opted
    # in to images_on_master and has no copy of its own.
    uploads_dir = os.path.abspath(current_app.config["UPLOAD_FOLDER"])
    try:
        safe_path = contained_path(uploads_dir, filename)
    except PathEscapesRoot:
        abort(403, description="Invalid file path.")
    if os.path.isfile(safe_path):
        rel_path = os.path.relpath(safe_path, uploads_dir)
        return send_from_directory(uploads_dir, rel_path)

    try:
        from backend.utils.interconnector_image_utils import should_use_master_image_repository, proxy_image_from_master

        if should_use_master_image_repository():
            success, image_data, error = proxy_image_from_master(f"uploads/{filename}")
            if success and image_data:
                from mimetypes import guess_type

                content_type, _ = guess_type(filename)
                logger.info(f"Proxied image from master: {filename}")
                return Response(
                    image_data,
                    mimetype=content_type or "application/octet-stream",
                    headers={'Cache-Control': 'public, max-age=3600'}
                )
            if error:
                logger.info(f"Image not on the master either ({error}): {filename}")
    except Exception as e:
        logger.error(f"Error checking master image repository: {e}")

    abort(404, description="File not found.")
