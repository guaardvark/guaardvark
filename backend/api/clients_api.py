# backend/api/clients_api.py
# Version 1.4: Changed POST route to '/' to handle potential trailing slash from frontend.
# Based on v1.3

import logging
import os
import uuid

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.utils import secure_filename

from backend.api.response_models import ClientResponse, ProjectResponse

try:
    from backend.models import Client, Project, db
except ImportError as e:
    logging.critical(
        f"CRITICAL - Failed to import DB/Client/Project model for clients_api.",
        exc_info=True,
    )
    db = None
    Client = None
    Project = None

clients_bp = Blueprint(
    "clients_api", __name__, url_prefix="/api/clients"
)  # Prefix ends without slash
logger = logging.getLogger(__name__)


# Import centralized serialization utilities
from backend.utils.serialization_utils import serialize_client as utils_serialize_client, serialize_project as utils_serialize_project

def serialize_client(client_obj, overrides=None):
    """Serialize client using centralized utility with Pydantic validation"""
    if not client_obj:
        return None

    # Use centralized serialization (pass overrides to avoid N+1 queries)
    data = utils_serialize_client(client_obj, overrides=overrides)
    if not data:
        return None

    # Validate and normalize using Pydantic
    validated = ClientResponse.model_validate(data)
    return validated.model_dump()


def serialize_project(project_obj):  # Kept for get_projects_for_client
    if not project_obj:
        return None

    if hasattr(project_obj, "to_dict") and callable(project_obj.to_dict):
        data = project_obj.to_dict()
    else:
        data = {
            "id": project_obj.id,
            "name": project_obj.name,
            "description": project_obj.description,
            "client_id": project_obj.client_id,
            "client": None,
            "website_count": 0,
            "document_count": 0,
            "task_count": 0,
            "rule_count": 0,
            "primary_rule_count": 0,
            "created_at": (
                project_obj.created_at.isoformat() if project_obj.created_at else None
            ),
            "updated_at": (
                project_obj.updated_at.isoformat() if project_obj.updated_at else None
            ),
        }

    validated = ProjectResponse.model_validate(data)
    return validated.model_dump()


@clients_bp.route("", methods=["GET"])
def get_clients():
    logger.info("API: Received GET /api/clients request")
    if not db or not Client:
        logger.error("DB or Client model not available")
        return jsonify({"error": "Server configuration error."}), 500
    try:
        clients = Client.query.order_by(Client.name).all()
        logger.info(f"Found {len(clients)} clients in DB, serializing...")

        # Batch count projects per client — 1 query instead of N
        project_counts = dict(
            db.session.query(Project.client_id, func.count(Project.id))
            .filter(Project.client_id.isnot(None))
            .group_by(Project.client_id).all()
        )

        clients_list = []
        for c in clients:
            try:
                overrides = {"project_count": project_counts.get(c.id, 0)}
                serialized = serialize_client(c, overrides=overrides)
                clients_list.append(serialized)
            except Exception as serialize_error:
                logger.error(f"Error serializing client {c.id} ({c.name}): {serialize_error}", exc_info=True)
                # Continue with other clients instead of failing completely
                continue
        logger.info(f"Successfully serialized {len(clients_list)} clients from DB.")
        return jsonify(clients_list)
    except Exception as e:
        logger.error(f"Error fetching clients: {e}", exc_info=True)
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return (
            jsonify({"error": "An unexpected error occurred while fetching clients"}),
            500,
        )


# MODIFIED: Changed route from '' to '/' to handle trailing slash from frontend
@clients_bp.route("/", methods=["POST"])
def create_client():
    logger.info(
        "API: Received POST /api/clients/ request"
    )  # Log updated to reflect trailing slash
    if not db or not Client:
        return jsonify({"error": "Server configuration error."}), 500
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    name = data.get("name")
    notes = data.get("notes")
    email = data.get("email")
    phone = data.get("phone")
    location = data.get("location")
    logo_path = data.get("logo_path")

    # RAG Enhancement fields (now supporting arrays)
    industry = data.get("industry", [])
    target_audience = data.get("target_audience", [])
    unique_selling_points = data.get("unique_selling_points", [])
    competitor_urls = data.get("competitor_urls", [])
    brand_voice_examples = data.get("brand_voice_examples")
    keywords = data.get("keywords", [])
    content_goals = data.get("content_goals", [])
    regulatory_constraints = data.get("regulatory_constraints")
    geographic_coverage = data.get("geographic_coverage", [])

    if not name or not name.strip():
        return jsonify({"error": "Missing or empty 'name' field"}), 400

    try:
        import json
        new_client_db = Client(
            name=name.strip(),
            email=email.strip() if email else None,
            phone=phone.strip() if phone else None,
            location=location.strip() if location else None,
            logo_path=logo_path,
            notes=notes,
            # RAG Enhancement fields (storing arrays as JSON)
            industry=json.dumps(industry) if industry else None,
            target_audience=json.dumps(target_audience) if target_audience else None,
            unique_selling_points=json.dumps(unique_selling_points) if unique_selling_points else None,
            competitor_urls=json.dumps(competitor_urls) if competitor_urls else None,
            brand_voice_examples=brand_voice_examples,
            keywords=json.dumps(keywords) if keywords else None,
            content_goals=json.dumps(content_goals) if content_goals else None,
            regulatory_constraints=regulatory_constraints,
            geographic_coverage=json.dumps(geographic_coverage) if geographic_coverage else None,
        )
        db.session.add(new_client_db)
        db.session.commit()
        logger.info(f"Created client '{new_client_db.name}' with ID {new_client_db.id}")
        return jsonify(serialize_client(new_client_db)), 201
    except IntegrityError as e:
        db.session.rollback()
        logger.warning(
            f"Integrity error creating client (name '{name}', email '{email}'): {e}"
        )
        error_detail = f"Client creation failed. A client with this name or email might already exist."
        if hasattr(e, "orig") and e.orig:
            if "clients.name" in str(e.orig).lower():
                error_detail = f"A client with the name '{name}' already exists."
            elif "clients.email" in str(e.orig).lower():
                error_detail = f"A client with the email '{email}' already exists."
        return jsonify({"error": error_detail}), 409  # Conflict
    except Exception as e:
        db.session.rollback()
        logger.error(f"Unexpected error creating client: {e}", exc_info=True)
        return jsonify({"error": "An unexpected server error occurred"}), 500


@clients_bp.route("/<int:client_id>", methods=["GET"])
def get_client(client_id):
    logger.info(f"API: Received GET /api/clients/{client_id} request")
    if not db or not Client:
        return jsonify({"error": "Server configuration error."}), 500
    try:
        client_obj = db.session.get(Client, client_id)
        if client_obj:
            return jsonify(serialize_client(client_obj))
        else:
            return jsonify({"error": "Client not found"}), 404
    except Exception as e:
        logger.error(f"Error fetching client {client_id}: {e}", exc_info=True)
        return jsonify({"error": "An unexpected error occurred"}), 500


@clients_bp.route("/<int:client_id>/projects", methods=["GET"])
def get_projects_for_client(client_id):
    logger.info(f"API: Received GET /api/clients/{client_id}/projects request")
    if not db or not Client or not Project:
        return jsonify({"error": "Server configuration error."}), 500
    client_obj = db.session.get(Client, client_id)
    if not client_obj:
        return jsonify({"error": "Client not found"}), 404
    try:
        projects_query = client_obj.projects.order_by(Project.name)
        projects_list = [serialize_project(p) for p in projects_query.all()]
        logger.info(
            f"Retrieved {len(projects_list)} projects for client ID {client_id}."
        )
        return jsonify(projects_list)
    except Exception as e:
        logger.error(
            f"Error fetching projects for client {client_id}: {e}", exc_info=True
        )
        return jsonify({"error": "An unexpected error occurred"}), 500


@clients_bp.route("/<int:client_id>", methods=["PUT"])
def update_client(client_id):
    logger.info(f"API: Received PUT /api/clients/{client_id} request")
    if not db or not Client:
        return jsonify({"error": "Server configuration error."}), 500
    client_obj = db.session.get(Client, client_id)
    if not client_obj:
        return jsonify({"error": "Client not found"}), 404
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json()
    updated_fields = []
    try:
        if "name" in data:
            new_name = data["name"].strip() if data["name"] else ""
            if not new_name:
                return jsonify({"error": "Client name cannot be empty."}), 400
            if client_obj.name != new_name:
                client_obj.name = new_name
                updated_fields.append("name")
        if "email" in data:
            new_email = data["email"].strip() if data.get("email") else None
            if client_obj.email != new_email:
                client_obj.email = new_email
                updated_fields.append("email")
        if "phone" in data:
            new_phone = data["phone"].strip() if data.get("phone") else None
            if client_obj.phone != new_phone:
                client_obj.phone = new_phone
                updated_fields.append("phone")
        if "location" in data:
            new_location = data["location"].strip() if data.get("location") else None
            if client_obj.location != new_location:
                client_obj.location = new_location
                updated_fields.append("location")
        if "logo_path" in data:
            new_logo_path = data.get("logo_path")
            if client_obj.logo_path != new_logo_path:
                client_obj.logo_path = new_logo_path
                updated_fields.append("logo_path")
        if "notes" in data:
            if client_obj.notes != data.get("notes"):
                client_obj.notes = data.get("notes")
                updated_fields.append("notes")

        # RAG Enhancement fields (now supporting arrays stored as JSON)
        import json
        if "industry" in data:
            new_industry = json.dumps(data.get("industry", [])) if data.get("industry") else None
            if client_obj.industry != new_industry:
                client_obj.industry = new_industry
                updated_fields.append("industry")
        if "target_audience" in data:
            new_target_audience = json.dumps(data.get("target_audience", [])) if data.get("target_audience") else None
            if client_obj.target_audience != new_target_audience:
                client_obj.target_audience = new_target_audience
                updated_fields.append("target_audience")
        if "unique_selling_points" in data:
            new_usps = json.dumps(data.get("unique_selling_points", [])) if data.get("unique_selling_points") else None
            if client_obj.unique_selling_points != new_usps:
                client_obj.unique_selling_points = new_usps
                updated_fields.append("unique_selling_points")
        if "competitor_urls" in data:
            new_competitor_urls = json.dumps(data.get("competitor_urls", [])) if data.get("competitor_urls") else None
            if client_obj.competitor_urls != new_competitor_urls:
                client_obj.competitor_urls = new_competitor_urls
                updated_fields.append("competitor_urls")
        if "brand_voice_examples" in data:
            if client_obj.brand_voice_examples != data.get("brand_voice_examples"):
                client_obj.brand_voice_examples = data.get("brand_voice_examples")
                updated_fields.append("brand_voice_examples")
        if "keywords" in data:
            new_keywords = json.dumps(data.get("keywords", [])) if data.get("keywords") else None
            if client_obj.keywords != new_keywords:
                client_obj.keywords = new_keywords
                updated_fields.append("keywords")
        if "content_goals" in data:
            new_content_goals = json.dumps(data.get("content_goals", [])) if data.get("content_goals") else None
            if client_obj.content_goals != new_content_goals:
                client_obj.content_goals = new_content_goals
                updated_fields.append("content_goals")
        if "regulatory_constraints" in data:
            if client_obj.regulatory_constraints != data.get("regulatory_constraints"):
                client_obj.regulatory_constraints = data.get("regulatory_constraints")
                updated_fields.append("regulatory_constraints")
        if "geographic_coverage" in data:
            new_geographic_coverage = json.dumps(data.get("geographic_coverage", [])) if data.get("geographic_coverage") else None
            if client_obj.geographic_coverage != new_geographic_coverage:
                client_obj.geographic_coverage = new_geographic_coverage
                updated_fields.append("geographic_coverage")

        if not updated_fields:
            logger.info(f"No actual changes for client {client_id}.")
            return jsonify(serialize_client(client_obj)), 200
        db.session.commit()
        logger.info(f"Updated client {client_id}. Fields: {', '.join(updated_fields)}")
        return jsonify(serialize_client(client_obj))
    except IntegrityError as e:
        db.session.rollback()
        logger.warning(f"Integrity error updating client {client_id}: {e}")
        error_detail = (
            f"Update failed. Name or email may already exist for another client."
        )
        if hasattr(e, "orig") and e.orig:
            if "clients.name" in str(e.orig).lower():
                error_detail = (
                    f"A client with the name '{data.get('name')}' already exists."
                )
            elif "clients.email" in str(e.orig).lower():
                error_detail = (
                    f"A client with the email '{data.get('email')}' already exists."
                )
        return jsonify({"error": error_detail}), 409
    except Exception as e:
        db.session.rollback()
        logger.error(
            f"Unexpected error updating client {client_id}: {e}", exc_info=True
        )
        return jsonify({"error": "An unexpected server error occurred"}), 500


@clients_bp.route("/<int:client_id>/logo", methods=["POST"])
def upload_client_logo(client_id):
    logger.info(f"API: Received POST /api/clients/{client_id}/logo request")
    if not db or not Client:
        return jsonify({"error": "Server configuration error."}), 500
    client_obj = db.session.get(Client, client_id)
    if not client_obj:
        return jsonify({"error": "Client not found"}), 404
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "Logo file missing. Use form field 'file'."}), 400
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    # Use CLIENT_LOGO_FOLDER which points to data/logos/
    upload_base = current_app.config.get("CLIENT_LOGO_FOLDER") or os.path.join(
        current_app.config["UPLOAD_FOLDER"], "logos"
    )
    os.makedirs(upload_base, exist_ok=True)

    # Preserve original filename - no renaming
    filename = secure_filename(file.filename)

    save_path = os.path.join(upload_base, filename)

    file.save(save_path)

    # Store just the filename (not the full path) since we know it's in logos/
    client_obj.logo_path = filename
    db.session.commit()
    logger.info(f"Saved logo for client {client_id}: {filename}")
    return jsonify({"logo_path": client_obj.logo_path}), 200


@clients_bp.route("/<int:client_id>", methods=["DELETE"])
def delete_client(client_id):
    logger.info(f"API: Received DELETE /api/clients/{client_id} request")
    if not db or not Client:
        return jsonify({"error": "Server configuration error."}), 500
    client_obj = db.session.get(Client, client_id)
    if not client_obj:
        return jsonify({"error": "Client not found"}), 404
    try:
        client_name = client_obj.name
        db.session.delete(client_obj)
        db.session.commit()
        logger.info(f"Deleted client {client_id} ('{client_name}')")
        return jsonify({"message": "Client deleted successfully"}), 200
    except IntegrityError as e:
        db.session.rollback()
        logger.error(f"Integrity error deleting client {client_id}: {e}", exc_info=True)
        # More specific error message if possible for FK constraint
        if (
            "FOREIGN KEY constraint failed" in str(e.orig).lower()
            or "violates foreign key constraint" in str(e.orig).lower()
        ):
            return (
                jsonify(
                    {
                        "error": "Cannot delete client. It is still associated with projects. Please reassign or delete associated projects first.",
                        "details": str(e.orig if hasattr(e, "orig") else e),
                    }
                ),
                409,
            )
        return (
            jsonify(
                {
                    "error": "Cannot delete client due to existing associations.",
                    "details": str(e.orig if hasattr(e, "orig") else e),
                }
            ),
            409,
        )
    except Exception as e:
        db.session.rollback()
        logger.error(
            f"Unexpected error deleting client {client_id}: {e}", exc_info=True
        )
        return jsonify({"error": "An unexpected error occurred"}), 500
