# backend/api/projects_api.py
# Version 1.7: Changed POST route to '/' to handle trailing slash from frontend.
# Based on v1.6

import logging

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload

from backend.api.response_models import ProjectResponse, SimpleClientInfo

try:
    from backend.models import Client, Document, Project, Rule, Task, Website, db
    from backend.utils.db_utils import DatabaseConnectionManager

    logging.getLogger(__name__).info(
        "Successfully imported db, Project, Client, Website, Document, Rule, Task using 'backend.models'"
    )
except ImportError as e:
    logging.getLogger(__name__).critical(
        f"CRITICAL: Failed to import models for projects_api: {e}", exc_info=True
    )
    db, Project, Client, Website, Document, Rule, Task = None, None, None, None, None, None, None

projects_bp = Blueprint(
    "projects_api", __name__, url_prefix="/api/projects"
)  # Prefix ends without slash
logger = logging.getLogger(__name__)


def project_to_dict(project, counts=None):
    from backend.utils.serialization_utils import format_logo_path
    if not project:
        return None

    if project.client_ref:
        client_info = {
            "id": project.client_ref.id,
            "name": project.client_ref.name,
            "logo_path": format_logo_path(getattr(project.client_ref, "logo_path", None)),
        }
    else:
        client_info = None

    if counts:
        # Use pre-computed batch counts (avoids N+1 queries)
        website_count = counts.get("websites", {}).get(project.id, 0)
        document_count = counts.get("documents", {}).get(project.id, 0)
        task_count = counts.get("tasks", {}).get(project.id, 0)
        rule_count = counts.get("linked_rules", {}).get(project.id, 0)
        primary_rule_count = counts.get("primary_rules", {}).get(project.id, 0)
    else:
        # Fallback for single-project endpoints (detail view)
        try:
            result = db.session.execute(
                text("SELECT COUNT(id) FROM websites WHERE project_id = :pid"),
                {"pid": project.id},
            )
            website_count = result.scalar() or 0
        except Exception as e:
            logger.warning(
                f"Could not count websites for project {project.id}: {e}",
                exc_info=True,
            )
            website_count = 0

        document_count = (
            project.documents.count() if hasattr(project.documents, "count") else 0
        )
        task_count = (
            project.tasks.count()
            if hasattr(project, "tasks") and hasattr(project.tasks, "count")
            else 0
        )
        rule_count = (
            project.linked_rules.count()
            if hasattr(project, "linked_rules") and hasattr(project.linked_rules, "count")
            else 0
        )
        primary_rule_count = (
            project.primary_rules.count() if hasattr(project, "primary_rules") else 0
        )

    data = {
        "id": project.id,
        "client_id": project.client_id,
        "name": project.name,
        "description": project.description,
        "client": client_info,
        "website_count": website_count,
        "document_count": document_count,
        "task_count": task_count,
        "rule_count": rule_count,
        "primary_rule_count": primary_rule_count,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }

    validated = ProjectResponse.model_validate(data)
    return validated.model_dump()


@projects_bp.route("", methods=["GET"])
@projects_bp.route("/", methods=["GET"])
def get_projects_route():
    logger.info("API: Received GET /api/projects request")
    if not db or not Project or not Client:
        return jsonify({"error": "Server configuration error."}), 500
    try:
        projects = (
            db.session.query(Project)
            .options(joinedload(Project.client_ref))
            .order_by(Project.name)
            .all()
        )

        # Batch count queries — 5 queries total instead of 5 * N
        counts = {}
        counts["websites"] = dict(
            db.session.query(Website.project_id, func.count(Website.id))
            .group_by(Website.project_id).all()
        )
        counts["documents"] = dict(
            db.session.query(Document.project_id, func.count(Document.id))
            .group_by(Document.project_id).all()
        )
        counts["tasks"] = dict(
            db.session.query(Task.project_id, func.count(Task.id))
            .group_by(Task.project_id).all()
        )
        # linked_rules via association table
        linked_rules_rows = db.session.execute(
            text("SELECT project_id, COUNT(rule_id) FROM project_rules_association GROUP BY project_id")
        ).fetchall()
        counts["linked_rules"] = dict(linked_rules_rows)
        # primary_rules — rules with project_id set directly
        counts["primary_rules"] = dict(
            db.session.query(Rule.project_id, func.count(Rule.id))
            .filter(Rule.project_id.isnot(None))
            .group_by(Rule.project_id).all()
        )

        projects_list = [project_to_dict(project, counts=counts) for project in projects]
        logger.info(f"API: Found {len(projects_list)} projects.")
        return jsonify(projects_list), 200
    except Exception as e:
        logger.error(f"API Error (GET /projects): {e}", exc_info=True)
        db.session.rollback()
        return jsonify({"error": "Server error fetching projects"}), 500


# MODIFIED: Changed route from '' to '/' to handle trailing slash from frontend
@projects_bp.route("/", methods=["POST"])
def create_project_route():
    logger.info("API: Received POST /api/projects/ request")  # Path updated in log
    if not db or not Project or not Client:
        return jsonify({"error": "Server configuration error."}), 500
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json()
    logger.debug(f"API: Request data: {data}")

    name = data.get("name")
    client_id_str = data.get("client_id")
    description = data.get("description", None)

    if not name or not name.strip():
        return jsonify({"error": "Project name cannot be empty."}), 400
    
    # SECURITY FIX: Add length validation
    if len(name.strip()) > 255:
        return jsonify({"error": "Project name too long. Maximum 255 characters."}), 400
    
    if description and len(description) > 1000:
        return jsonify({"error": "Project description too long. Maximum 1000 characters."}), 400
    if client_id_str is None:
        return jsonify({"error": "Missing required field: client_id"}), 400

    try:
        client_id = int(client_id_str)
        # SECURITY FIX: Add bounds checking for integer values
        if client_id < 1 or client_id > 2147483647:  # 32-bit signed int max
            return jsonify({"error": "Invalid client_id value"}), 400
        
        client_exists = (
            db.session.query(Client.id).filter_by(id=client_id).scalar() is not None
        )
        if not client_exists:
            logger.warning(
                f"Client with ID {client_id} not found during project creation."
            )
            return jsonify({"error": f"Client with ID {client_id} not found."}), 404
    except (ValueError, TypeError):
        logger.warning(f"Invalid client_id format: {client_id_str}")
        return jsonify({"error": "Invalid client_id format."}), 400
    except SQLAlchemyError as e:
        logger.error(
            f"API Error (POST /projects/): DB error checking client ID {client_id_str} - {e}",
            exc_info=True,
        )
        return jsonify({"error": "Database error validating client ID."}), 500

    try:
        with DatabaseConnectionManager():
            new_project = Project(
                name=name.strip(), description=description, client_id=client_id
            )
            db.session.add(new_project)
            db.session.commit()
            project_id_val = new_project.id
        logger.info(
            f"API: Created project '{new_project.name}' ID: {project_id_val} for Client ID: {client_id}"
        )

        created_project_with_client = (
            db.session.query(Project)
            .options(joinedload(Project.client_ref))
            .filter_by(id=project_id_val)
            .first()
        )
        return jsonify(project_to_dict(created_project_with_client)), 201
    except IntegrityError as e:
        db.session.rollback()
        logger.warning(f"Integrity error creating project '{name}': {e}")
        if "projects.name" in str(e.orig).lower():
            return (
                jsonify({"error": f"A project with the name '{name}' already exists."}),
                409,
            )
        return (
            jsonify(
                {
                    "error": "Failed to create project due to data conflict (e.g., name already exists)."
                }
            ),
            409,
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"API Error (POST /projects/): {e}", exc_info=True)
        return jsonify({"error": "Failed to create project."}), 500


@projects_bp.route("/<int:project_id>", methods=["GET"])
def get_project_route(project_id):
    logger.info(f"API: Received GET /api/projects/{project_id} request")
    if not db or not Project:
        return jsonify({"error": "Server configuration error."}), 500
    try:
        project = (
            db.session.query(Project)
            .options(joinedload(Project.client_ref))
            .filter_by(id=project_id)
            .first()
        )
        if project is None:
            return jsonify({"error": "Project not found"}), 404
        logger.info(f"API: Found project ID: {project_id} (Name: '{project.name}')")
        return jsonify(project_to_dict(project)), 200
    except Exception as e:
        logger.error(f"API Error (GET /projects/{project_id}): {e}", exc_info=True)
        db.session.rollback()
        return jsonify({"error": "Server error fetching project"}), 500


@projects_bp.route("/<int:project_id>", methods=["PUT"])
def update_project_route(project_id):
    logger.info(f"API: Received PUT /api/projects/{project_id} request")
    if not db or not Project or not Client:
        return jsonify({"error": "Server configuration error."}), 500
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    project = db.session.get(Project, project_id)
    if project is None:
        return jsonify({"error": "Project not found"}), 404

    data = request.get_json()
    logger.debug(f"API: Request data for update: {data}")
    updated_fields = []
    try:
        if "name" in data:
            new_name = data["name"].strip()
            if not new_name:
                return jsonify({"error": "Project name cannot be empty."}), 400
            if project.name != new_name:
                project.name = new_name
                updated_fields.append("name")

        if "description" in data:
            if project.description != data.get("description"):
                project.description = data.get("description")
                updated_fields.append("description")

        if "client_id" in data:
            new_client_id_str = data.get("client_id")
            if new_client_id_str is None:
                if project.client_id is not None:
                    project.client_id = None
                    updated_fields.append("client_id (unset)")
            else:
                try:
                    new_client_id = int(new_client_id_str)
                    if project.client_id != new_client_id:
                        new_client_exists = (
                            db.session.query(Client.id)
                            .filter_by(id=new_client_id)
                            .scalar()
                            is not None
                        )
                        if not new_client_exists:
                            return (
                                jsonify(
                                    {
                                        "error": f"Client with ID {new_client_id} not found."
                                    }
                                ),
                                404,
                            )
                        project.client_id = new_client_id
                        updated_fields.append("client_id")
                except (ValueError, TypeError):
                    return jsonify({"error": "Invalid client_id format."}), 400

        if not updated_fields:
            logger.info(f"No actual changes for project {project_id}")
            return jsonify(project_to_dict(project)), 200

        db.session.commit()
        logger.info(
            f"API: Updated project ID: {project_id}. Fields: {', '.join(updated_fields)}"
        )
        updated_project_with_client = (
            db.session.query(Project)
            .options(joinedload(Project.client_ref))
            .get(project_id)
        )
        return jsonify(project_to_dict(updated_project_with_client)), 200
    except IntegrityError as e:
        db.session.rollback()
        logger.warning(f"Integrity error updating project {project_id}: {e}")
        if "projects.name" in str(e.orig).lower():
            return (
                jsonify(
                    {
                        "error": f"A project with the name '{data.get('name')}' already exists."
                    }
                ),
                409,
            )
        return jsonify({"error": "Failed to update project due to data conflict."}), 409
    except Exception as e:
        db.session.rollback()
        logger.error(f"API Error (PUT /projects/{project_id}): {e}", exc_info=True)
        return jsonify({"error": "Failed to update project."}), 500


@projects_bp.route("/<int:project_id>", methods=["DELETE"])
def delete_project_route(project_id):
    logger.info(f"API: Received DELETE /api/projects/{project_id} request")
    if not db or not Project:
        return jsonify({"error": "Server configuration error."}), 500
    project = db.session.get(Project, project_id)
    if project is None:
        return jsonify({"error": "Project not found"}), 404
    try:
        project_name = project.name
        logger.info(
            f"Staging project {project_id} ('{project_name}') for deletion. Associated items will be handled by DB cascade/SET NULL rules."
        )
        db.session.delete(project)
        db.session.commit()
        logger.info(f"API: Deleted project ID: {project_id} (Name: '{project_name}')")
        return (
            jsonify(
                {
                    "message": f"Project '{project_name}' (ID: {project_id}) deleted successfully."
                }
            ),
            200,
        )
    except IntegrityError as e:
        db.session.rollback()
        logger.error(
            f"Integrity error deleting project {project_id}: {e}", exc_info=True
        )
        return (
            jsonify(
                {
                    "error": "Cannot delete project. It might be referenced by other items that were not automatically disassociated or deleted.",
                    "details": str(e.orig if hasattr(e, "orig") else e),
                }
            ),
            409,
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"API Error (DELETE /projects/{project_id}): {e}", exc_info=True)
        return jsonify({"error": "Failed to delete project."}), 500


@projects_bp.route("/<int:project_id>/rules/<int:rule_id>/link", methods=["POST"])
def link_rule_to_project_route(project_id, rule_id):
    logger.info(
        f"API: Received POST /api/projects/{project_id}/rules/{rule_id}/link request"
    )
    if not db or not Rule or not Project:
        return jsonify({"error": "DB/Model unavailable."}), 500
    try:
        project = db.session.get(Project, project_id)
        rule = db.session.get(Rule, rule_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404
        if not rule:
            return jsonify({"error": "Rule/Prompt not found"}), 404
        link_exists = project.linked_rules.filter(Rule.id == rule_id).count() > 0
        if link_exists:
            logger.warning(f"Rule {rule_id} already linked to project {project_id}.")
            return jsonify({"message": "Rule already linked to project"}), 200
        project.linked_rules.append(rule)
        db.session.commit()
        logger.info(f"Successfully linked Rule {rule_id} to Project {project_id}")
        return jsonify({"message": "Rule linked successfully"}), 201
    except Exception as e:
        db.session.rollback()
        logger.error(
            f"Error linking rule {rule_id} to project {project_id}: {e}", exc_info=True
        )
        return jsonify({"error": "Failed to link rule to project"}), 500


@projects_bp.route("/<int:project_id>/rules/<int:rule_id>/unlink", methods=["DELETE"])
def unlink_rule_from_project_route(project_id, rule_id):
    logger.info(
        f"API: Received DELETE /api/projects/{project_id}/rules/{rule_id}/unlink request"
    )
    if not db or not Rule or not Project:
        return jsonify({"error": "DB/Model unavailable."}), 500
    try:
        project = db.session.get(Project, project_id)
        rule = db.session.get(Rule, rule_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404
        if not rule:
            return jsonify({"error": "Rule/Prompt not found"}), 404
        link_exists = project.linked_rules.filter(Rule.id == rule_id).count() > 0
        if not link_exists:
            logger.warning(f"Rule {rule_id} was not linked to project {project_id}.")
            return jsonify({"error": "Rule not linked to this project"}), 404
        project.linked_rules.remove(rule)
        db.session.commit()
        logger.info(f"Successfully unlinked Rule {rule_id} from Project {project_id}")
        return jsonify({"message": "Rule unlinked successfully"}), 200
    except Exception as e:
        db.session.rollback()
        logger.error(
            f"Error unlinking rule {rule_id} from project {project_id}: {e}",
            exc_info=True,
        )
        return jsonify({"error": "Failed to unlink rule from project"}), 500
