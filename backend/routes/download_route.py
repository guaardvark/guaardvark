# download_route.py   Version 1.000

import os

from flask import Blueprint, abort, current_app, send_from_directory

download_bp = Blueprint("outputs_api", __name__)


@download_bp.route("/outputs/<path:filename>", methods=["GET"])
def download_output(filename):
    outputs_dir = os.path.abspath(current_app.config["OUTPUT_DIR"])
    safe_path = os.path.normpath(os.path.abspath(os.path.join(outputs_dir, filename)))
    # Prevent directory traversal - path must be within outputs_dir and not the directory itself
    if not safe_path.startswith(outputs_dir + os.sep):
        abort(403, description="Invalid file path.")

    if not os.path.isfile(safe_path):
        abort(404, description="File not found.")

    rel_path = os.path.relpath(safe_path, outputs_dir)
    return send_from_directory(outputs_dir, rel_path, as_attachment=True)
