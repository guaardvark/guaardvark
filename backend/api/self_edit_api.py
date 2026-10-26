"""Changes Guaardvark made to its own code: list them and revert them.

Reverting writes code into the checkout, so, like applying a staged fix, it
is refused from other hosts on the network unless they carry the API key
(MUTATION_PROTECTED_PREFIXES); reading the list stays open.
"""

from flask import Blueprint, jsonify, request

self_edit_bp = Blueprint("self_edits", __name__, url_prefix="/api/self-edits")


@self_edit_bp.route("", methods=["GET"])
def list_self_edits():
    from backend.services.self_edit import list_edits
    limit = max(1, min(int(request.args.get("limit", 50)), 500))
    return jsonify({"success": True, "edits": list_edits(limit=limit)})


@self_edit_bp.route("/<edit_id>", methods=["GET"])
def get_self_edit(edit_id):
    from backend.services.self_edit import load
    record = load(edit_id)
    if record is None:
        return jsonify({"success": False, "error": "No such change."}), 404
    return jsonify({"success": True, "edit": record})


@self_edit_bp.route("/<edit_or_group>/revert", methods=["POST"])
def revert_self_edit(edit_or_group):
    """Body: {force?: bool}. Puts back the files of one change, or of every
    change in a group (a plan step or a chat turn), newest first."""
    from backend.services.self_edit import revert
    force = bool((request.get_json(silent=True) or {}).get("force"))
    result = revert(edit_or_group, force=force)
    if result.get("conflicts"):
        return jsonify({"success": False, **result}), 409
    if not result.get("reverted"):
        return jsonify({"success": False, **result}), 404
    return jsonify({"success": True, **result})
