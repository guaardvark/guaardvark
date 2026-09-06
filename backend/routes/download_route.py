# download_route.py   Version 1.000

import os

from flask import Blueprint, abort, current_app, request, send_from_directory
from backend.utils.path_guard import PathEscapesRoot, contained, contained_path

download_bp = Blueprint("outputs_api", __name__)

_MARKUP_AS_TEXT = frozenset({".html", ".htm", ".xhtml", ".svg", ".xml"})


@download_bp.route("/outputs/<path:filename>", methods=["GET"])
def download_output(filename):
    outputs_dir = os.path.abspath(current_app.config["OUTPUT_DIR"])
    try:
        safe_path = contained_path(outputs_dir, filename)
    except PathEscapesRoot:
        abort(403, description="Invalid file path.")

    if not os.path.isfile(safe_path):
        abort(404, description="File not found.")

    rel_path = os.path.relpath(safe_path, outputs_dir)
    # ?inline=1 lets the browser display the file instead of saving it.
    inline = request.args.get("inline", "").strip().lower() in ("1", "true", "yes")
    # Generated markup is shown as text: an inline .html/.svg would otherwise run
    # its scripts on the API origin.
    mimetype = None
    if inline and os.path.splitext(safe_path)[1].lower() in _MARKUP_AS_TEXT:
        mimetype = "text/plain; charset=utf-8"
    return send_from_directory(
        outputs_dir, rel_path, as_attachment=not inline, mimetype=mimetype
    )
