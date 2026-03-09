"""Simple hierarchy API placeholder."""

from flask import Blueprint
from backend.utils.response_utils import error_response

hierarchy_bp = Blueprint("hierarchy_api", __name__, url_prefix="/api/hierarchy")


@hierarchy_bp.route("/", methods=["GET"])
def hierarchy_root():
    """Return a not implemented message."""
    return error_response("Hierarchy API not implemented", 501, "NOT_IMPLEMENTED")
