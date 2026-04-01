# backend/api/rules_api.py
# Version 1.06 — Bulletproof command_label UNIQUE handling

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.models import Project, Rule, db
from backend.utils.response_utils import success_response, error_response

rules_bp = Blueprint("rules_api", __name__, url_prefix="/api/rules")


@rules_bp.route("", methods=["GET"])
def get_rules():
    """
    Retrieve a paginated list of rules with optional filtering.
    
    Query Parameters:
        project_id (int, optional): Filter rules linked to a specific project.
        type (str, optional): Filter by rule type (e.g., 'COMMAND_RULE').
        is_active (str, optional): Filter by active status ('true', '1', 'yes').
        page (int, optional): Page number for pagination (default: 1).
        per_page (int, optional): Items per page, max 100 (default: 50).
    
    Returns:
        JSON array of rule objects, each containing rule details.
    
    Errors:
        500 DATABASE_ERROR: Database query failed.
    """
    try:
        project_id_filter = request.args.get("project_id", type=int)
        query = db.session.query(Rule)
        if project_id_filter is not None:
            query = query.join(Rule.linked_projects).filter(
                Project.id == project_id_filter
            )

        # Filter by rule type (e.g., COMMAND_RULE)
        rule_type = request.args.get("type")
        if rule_type:
            query = query.filter(Rule.type == rule_type)

        # Filter by active status
        is_active = request.args.get("is_active")
        if is_active is not None:
            query = query.filter(Rule.is_active == (is_active.lower() in ("true", "1", "yes")))

        # Optimize query with pagination and eager loading
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 50, type=int), 100)  # Max 100 per page
        
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
    """
    Retrieve a single rule by its ID.
    
    Path Parameters:
        rule_id (int): The unique identifier of the rule.
    
    Returns:
        JSON response with rule details on success.
    
    Errors:
        404 NOT_FOUND: Rule with given ID does not exist.
    """
    rule = db.session.get(Rule, rule_id)
    if not rule:
        return error_response("Rule not found", 404, "NOT_FOUND")
    return success_response("Rule retrieved", rule.to_dict())


@rules_bp.route("", methods=["POST"])
def create_rule():
    """
    Create a new rule in the system.
    
    Request Body (JSON):
        name (str, required): Name of the rule.
        rule_text (str, required): The content/text of the rule.
        level (str, optional): Rule level - one of 'SYSTEM', 'PROJECT', 'CLIENT',
            'USER_GLOBAL', 'USER_SPECIFIC', 'PROMPT', 'LEARNED' (default: 'PROMPT').
        type (str, optional): Rule type (default: 'PROMPT_TEMPLATE').
        command_label (str, optional): Unique command label for the rule.
        reference_id (str, optional): Reference identifier.
        description (str, optional): Description of the rule.
        target_models (str, optional): JSON array of target models (default: '["__ALL__"]').
        is_active (bool, optional): Whether rule is active (default: True).
        project_id (int, optional): Associated project ID.
    
    Returns:
        JSON response with created rule ID on success (status 201).
    
    Errors:
        400 INVALID_REQUEST: Request body is not JSON.
        400 EMPTY_DATA: JSON body is empty.
        400 INVALID_NAME: Missing or invalid 'name' field.
        400 INVALID_RULE_TEXT: Missing or invalid 'rule_text' field.
        400 INVALID_LEVEL: Invalid 'level' value.
        409 DUPLICATE_COMMAND_LABEL: command_label must be unique.
        500 DATABASE_ERROR: Database operation failed.
        500 CREATE_FAILED: Unexpected error during creation.
    """
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
        return error_response("Missing or invalid 'rule_text' field", 400, "INVALID_RULE_TEXT")

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
            409, "DUPLICATE_COMMAND_LABEL"
        )
    except SQLAlchemyError as e:
        db.session.rollback()
        return error_response("Failed to create rule due to database error", 500, "DATABASE_ERROR")
    except Exception as e:
        db.session.rollback()
        return error_response("Failed to create rule", 500, "CREATE_FAILED")


@rules_bp.route("/<int:rule_id>", methods=["PUT"])
def update_rule(rule_id):
    """
    Update an existing rule by its ID.
    
    Path Parameters:
        rule_id (int): The unique identifier of the rule to update.
    
    Request Body (JSON):
        Any of the following optional fields:
        name (str): New name for the rule.
        level (str): New rule level.
        type (str): New rule type.
        command_label (str): New unique command label.
        reference_id (str): New reference identifier.
        rule_text (str): New rule content.
        description (str): New description.
        target_models (str): New JSON array of target models.
        is_active (bool): New active status.
        project_id (int): New associated project ID.
    
    Returns:
        JSON success message on update completion.
    
    Errors:
        404 NOT_FOUND: Rule with given ID does not exist.
        409 CONFLICT: Duplicate command_label (must be unique).
        500 DATABASE_ERROR: Database operation failed.
    """
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
        return error_response("Duplicate command_label. Each rule must have a unique command_label.", 409)
    except SQLAlchemyError as e:
        db.session.rollback()
        return error_response("Failed to update rule", 500)


@rules_bp.route("/<int:rule_id>", methods=["DELETE"])
def delete_rule(rule_id):
    """
    Delete a rule by its ID.
    
    Path Parameters:
        rule_id (int): The unique identifier of the rule to delete.
    
    Returns:
        JSON success message on deletion.
    
    Errors:
        404 NOT_FOUND: Rule with given ID does not exist.
        500 DATABASE_ERROR: Database operation failed.
    """
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
