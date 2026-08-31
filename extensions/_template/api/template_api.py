"""A blueprint the loader discovers like anything in backend/api.

Keep the blueprint name unique across extensions (Flask refuses duplicates)
and the url_prefix listed in extension.json so the mounted check covers it.
"""
from flask import Blueprint

from backend.utils.response_utils import success_response

template_bp = Blueprint("template_extension", __name__, url_prefix="/api/template")


@template_bp.route("/ping", methods=["GET"])
def ping():
    return success_response({"pong": True})
