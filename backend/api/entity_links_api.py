# backend/api/entity_links_api.py
# Comprehensive Entity Linking API for Many-to-Many Relationships
# Handles linking between Documents, Tasks, Clients, and Projects

import logging
from flask import Blueprint, request, jsonify, current_app
from sqlalchemy.exc import SQLAlchemyError
from backend.models import db, Document, Task, Client, Project

logger = logging.getLogger(__name__)

entity_links_bp = Blueprint("entity_links", __name__, url_prefix="/api/entity-links")

# Entity type mapping for validation and querying
ENTITY_MODELS = {
    "document": Document,
    "task": Task,
    "client": Client,
    "project": Project,
}


def get_entity_by_type_and_id(entity_type, entity_id):
    """Get an entity by type and ID with validation."""
    if entity_type not in ENTITY_MODELS:
        return None, f"Invalid entity type: {entity_type}"

    model = ENTITY_MODELS[entity_type]
    entity = db.session.get(model, entity_id)
    if not entity:
        return None, f"{entity_type.capitalize()} with ID {entity_id} not found"

    return entity, None


def serialize_entity(entity, entity_type):
    """Serialize an entity for API response."""
    if entity_type == "document":
        return {
            "id": entity.id,
            "filename": entity.filename,
            "type": entity.type,
            "index_status": entity.index_status,
        }
    elif entity_type == "task":
        return {
            "id": entity.id,
            "name": entity.name,
            "status": entity.status,
            "type": entity.type,
        }
    elif entity_type == "client":
        return {"id": entity.id, "name": entity.name, "email": entity.email}
    elif entity_type == "project":
        return {"id": entity.id, "name": entity.name, "description": entity.description}
    return {"id": entity.id}


@entity_links_bp.route("/<entity_type>/<int:entity_id>/links", methods=["GET"])
def get_entity_links(entity_type, entity_id):
    """Get all linked entities for a specific entity."""
    logger.info(f"API: Getting links for {entity_type} {entity_id}")

    try:
        entity, error = get_entity_by_type_and_id(entity_type, entity_id)
        if error:
            return jsonify({"error": error}), 404

        links = {}

        # Get linked entities based on entity type
        if entity_type == "document":
            # Documents can link to projects (direct relationship)
            if entity.project:
                links["projects"] = [serialize_entity(entity.project, "project")]
            else:
                links["projects"] = []

            # TODO: Add document-task, document-client many-to-many when implemented
            links["tasks"] = []
            links["clients"] = []

        elif entity_type == "task":
            # Tasks can link to projects (direct relationship)
            if entity.project:
                links["projects"] = [serialize_entity(entity.project, "project")]
            else:
                links["projects"] = []

            # TODO: Add task-document, task-client many-to-many when implemented
            links["documents"] = []
            links["clients"] = []

        elif entity_type == "client":
            # Clients can link to projects (one-to-many)
            links["projects"] = [
                serialize_entity(proj, "project") for proj in entity.projects
            ]

            # TODO: Add client-document, client-task many-to-many when implemented
            links["documents"] = []
            links["tasks"] = []

        elif entity_type == "project":
            # Projects have multiple relationship types
            links["documents"] = [
                serialize_entity(doc, "document") for doc in entity.documents
            ]
            links["tasks"] = [serialize_entity(task, "task") for task in entity.tasks]
            links["rules"] = [
                serialize_entity(rule, "rule") for rule in entity.linked_rules
            ]
            if entity.client_ref:
                links["clients"] = [serialize_entity(entity.client_ref, "client")]
            else:
                links["clients"] = []

        return (
            jsonify(
                {
                    "success": True,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "links": links,
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(
            f"Error getting links for {entity_type} {entity_id}: {e}", exc_info=True
        )
        return jsonify({"error": "Failed to get entity links"}), 500


@entity_links_bp.route(
    "/<entity_type>/<int:entity_id>/links/<link_type>", methods=["PUT"]
)
def update_entity_links(entity_type, entity_id, link_type):
    """Update links between entities."""
    logger.info(f"API: Updating {link_type} links for {entity_type} {entity_id}")

    try:
        entity, error = get_entity_by_type_and_id(entity_type, entity_id)
        if error:
            return jsonify({"error": error}), 404

        data = request.get_json()
        if not data or "linked_ids" not in data:
            return jsonify({"error": "linked_ids array is required"}), 400

        linked_ids = data["linked_ids"]

        # Handle different link types
        if entity_type == "project" and link_type == "documents":
            # Update project documents
            documents = (
                db.session.query(Document).filter(Document.id.in_(linked_ids)).all()
            )
            for doc in documents:
                doc.project_id = entity_id
            # Clear documents not in the list
            db.session.query(Document).filter(
                Document.project_id == entity_id, ~Document.id.in_(linked_ids)
            ).update({"project_id": None})

        elif entity_type == "project" and link_type == "tasks":
            # Update project tasks
            tasks = db.session.query(Task).filter(Task.id.in_(linked_ids)).all()
            for task in tasks:
                task.project_id = entity_id
            # Clear tasks not in the list
            db.session.query(Task).filter(
                Task.project_id == entity_id, ~Task.id.in_(linked_ids)
            ).update({"project_id": None})

        elif entity_type == "document" and link_type == "projects":
            # Documents can only link to one project
            if len(linked_ids) > 1:
                return (
                    jsonify({"error": "Documents can only be linked to one project"}),
                    400,
                )
            entity.project_id = linked_ids[0] if linked_ids else None

        elif entity_type == "task" and link_type == "projects":
            # Tasks can only link to one project
            if len(linked_ids) > 1:
                return (
                    jsonify({"error": "Tasks can only be linked to one project"}),
                    400,
                )
            entity.project_id = linked_ids[0] if linked_ids else None

        elif entity_type == "client" and link_type == "projects":
            # Update client projects
            projects = (
                db.session.query(Project).filter(Project.id.in_(linked_ids)).all()
            )
            for project in projects:
                project.client_id = entity_id
            # Clear projects not in the list
            db.session.query(Project).filter(
                Project.client_id == entity_id, ~Project.id.in_(linked_ids)
            ).update({"client_id": None})

        else:
            return (
                jsonify(
                    {"error": f"Link type {link_type} not supported for {entity_type}"}
                ),
                400,
            )

        db.session.commit()

        return (
            jsonify(
                {
                    "success": True,
                    "message": f"Successfully updated {link_type} links for {entity_type} {entity_id}",
                    "linked_count": len(linked_ids),
                }
            ),
            200,
        )

    except Exception as e:
        db.session.rollback()
        logger.error(
            f"Error updating links for {entity_type} {entity_id}: {e}", exc_info=True
        )
        return jsonify({"error": "Failed to update entity links"}), 500


@entity_links_bp.route("/linkable/<entity_type>", methods=["GET"])
def get_linkable_entities(entity_type):
    """Get all entities of a specific type that can be linked."""
    logger.info(f"API: Getting linkable entities of type {entity_type}")

    try:
        if entity_type not in ENTITY_MODELS:
            return jsonify({"error": f"Invalid entity type: {entity_type}"}), 400

        model = ENTITY_MODELS[entity_type]

        # Apply filters if provided
        search_term = request.args.get("search", "").strip()
        query = db.session.query(model)

        if search_term:
            if entity_type == "document":
                query = query.filter(model.filename.ilike(f"%{search_term}%"))
            elif entity_type in ["task", "project"]:
                query = query.filter(model.name.ilike(f"%{search_term}%"))
            elif entity_type == "client":
                query = query.filter(model.name.ilike(f"%{search_term}%"))

        entities = query.limit(100).all()  # Limit results for performance

        return (
            jsonify(
                {
                    "success": True,
                    "entity_type": entity_type,
                    "entities": [
                        serialize_entity(entity, entity_type) for entity in entities
                    ],
                    "count": len(entities),
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(
            f"Error getting linkable entities of type {entity_type}: {e}", exc_info=True
        )
        return jsonify({"error": f"Failed to get linkable {entity_type} entities"}), 500
