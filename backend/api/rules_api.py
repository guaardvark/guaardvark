# backend/api/rules_api.py
# Version 1.06 — Bulletproof command_label UNIQUE handling

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.models import Project, Rule, db
from backend.utils.response_utils import success_response, error_response

rules_bp = Blueprint("rules_api", __name__, url_prefix="/api/rules")


@rules_bp.route("", methods=["GET"])
def get_rules():
    try:
        project_id_filter = request.args.get("project_id", type=int)
        query = db.session.query(Rule)
        if project_id_filter is not None:
            query = query.join(Rule.linked_projects).filter(
                Project.id == project_id_filter
            )

        # Optimize query with pagination and eager loading
        page = request.args.get("page", 1, type=int)
        per_page = min(
            request.args.get("per_page", 50, type=int), 100
        )  # Max 100 per page

        rules = query.order_by(Rule.updated_at.desc().nullslast()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        items = [rule.to_dict() for rule in rules.items]
        return jsonify(items), 200
    except SQLAlchemyError as e:
        db.session.rollback()
        return error_response(str(e), 500, "DATABASE_ERROR")


@rules_bp.route("/<int:rule_id>", methods=["GET"])
def get_rule(rule_id):
    rule = db.session.get(Rule, rule_id)
    if not rule:
        return error_response("Rule not found", 404, "NOT_FOUND")
    return success_response("Rule retrieved", rule.to_dict())


@rules_bp.route("", methods=["POST"])
def create_rule():
    # Input validation
    if not request.is_json:
        return error_response("Request must be JSON", 400, "INVALID_REQUEST")

    data = request.get_json()
    if not data:
        return error_response("Empty JSON data", 400, "EMPTY_DATA")

    # Validate required fields
    name = data.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        return error_response("Missing or invalid 'name' field", 400, "INVALID_NAME")

    rule_text = data.get("rule_text")
    if not rule_text or not isinstance(rule_text, str):
        return error_response(
            "Missing or invalid 'rule_text' field", 400, "INVALID_RULE_TEXT"
        )

    # Validate optional fields
    level = data.get("level", "PROMPT")
    if level not in [
        "SYSTEM",
        "PROJECT",
        "CLIENT",
        "USER_GLOBAL",
        "USER_SPECIFIC",
        "PROMPT",
        "LEARNED",
    ]:
        return error_response("Invalid 'level' value", 400, "INVALID_LEVEL")

    # --- PATCH: Normalize blank/empty/whitespace command_label to None/NULL ---
    command_label = data.get("command_label")
    if command_label is None or str(command_label).strip() == "":
        command_label = None
    try:
        rule = Rule(
            name=name.strip(),
            level=level,
            type=data.get("type", "PROMPT_TEMPLATE"),
            command_label=command_label,
            reference_id=data.get("reference_id"),
            rule_text=rule_text.strip(),
            description=(
                data.get("description", "").strip() if data.get("description") else None
            ),
            target_models=data.get("target_models", '["__ALL__"]'),
            is_active=bool(data.get("is_active", True)),
            project_id=data.get("project_id"),
        )
        db.session.add(rule)
        db.session.commit()
        return success_response("Rule created", {"id": rule.id}, 201)
    except IntegrityError as e:
        db.session.rollback()
        return error_response(
            "Duplicate command_label. Each rule must have a unique command_label.",
            409,
            "DUPLICATE_COMMAND_LABEL",
        )
    except SQLAlchemyError as e:
        db.session.rollback()
        return error_response(
            "Failed to create rule due to database error", 500, "DATABASE_ERROR"
        )
    except Exception as e:
        db.session.rollback()
        return error_response("Failed to create rule", 500, "CREATE_FAILED")


@rules_bp.route("/<int:rule_id>", methods=["PUT"])
def update_rule(rule_id):
    data = request.get_json()
    rule = db.session.get(Rule, rule_id)
    if not rule:
        return error_response("Rule not found", 404)
    # --- PATCH: Normalize blank/empty/whitespace command_label to None/NULL ---
    if "command_label" in data and (
        data["command_label"] is None or str(data["command_label"]).strip() == ""
    ):
        data["command_label"] = None

    # Check for duplicate command_label (excluding current rule)
    if "command_label" in data and data["command_label"] is not None:
        existing_rule = Rule.query.filter(
            Rule.command_label == data["command_label"], Rule.id != rule_id
        ).first()
        if existing_rule:
            return error_response("Duplicate command_label. Must be unique.", 409)

    try:
        for field in [
            "name",
            "level",
            "type",
            "command_label",
            "reference_id",
            "rule_text",
            "description",
            "target_models",
            "is_active",
            "project_id",
        ]:
            if field in data:
                setattr(rule, field, data[field])
        db.session.commit()
        return success_response(message="Rule updated.")
    except IntegrityError as e:
        db.session.rollback()
        return error_response(
            "Duplicate command_label. Each rule must have a unique command_label.", 409
        )
    except SQLAlchemyError as e:
        db.session.rollback()
        return error_response("Failed to update rule", 500)


@rules_bp.route("/<int:rule_id>", methods=["DELETE"])
def delete_rule(rule_id):
    rule = db.session.get(Rule, rule_id)
    if not rule:
        return error_response("Rule not found", 404)
    try:
        db.session.delete(rule)
        db.session.commit()
        return success_response(message="Rule deleted.")
    except SQLAlchemyError as e:
        db.session.rollback()
        return error_response("Failed to delete rule", 500)


@rules_bp.route("/learned", methods=["DELETE"])
def purge_learned_rules():
    """Delete all rules marked as LEARNED."""
    try:
        count = db.session.query(Rule).filter(Rule.level == "LEARNED").delete()
        db.session.commit()
        return success_response(message=f"Deleted {count} learned rules.")
    except SQLAlchemyError as e:
        db.session.rollback()
        return error_response("Failed to purge learned rules", 500)


# Optional: More endpoints (e.g., linking) can go here as needed.
