# backend/api/entity_indexing_api.py
# API endpoints for managing entity indexing in the LLM context system
# Version 1.0: Initial implementation

import logging
from flask import Blueprint, request, jsonify, current_app
from backend.services.entity_indexing_service import get_entity_indexing_service
from backend.models import db

logger = logging.getLogger(__name__)

entity_indexing_bp = Blueprint(
    "entity_indexing", __name__, url_prefix="/api/entity-indexing"
)


@entity_indexing_bp.route("/index-all", methods=["POST"])
def index_all_entities():
    """Index all entities in the database"""
    try:
        service = get_entity_indexing_service()
        results = service.index_all_entities()

        return (
            jsonify(
                {
                    "success": True,
                    "message": "Entity indexing completed",
                    "results": results,
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Error in index_all_entities: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@entity_indexing_bp.route("/index-entity", methods=["POST"])
def index_single_entity():
    """Index a single entity by type and ID"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request must be JSON"}), 400

        entity_type = data.get("entity_type")
        entity_id = data.get("entity_id")

        if not entity_type or not entity_id:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "entity_type and entity_id are required",
                    }
                ),
                400,
            )

        if entity_type not in ["client", "project", "website", "task"]:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "entity_type must be one of: client, project, website, task",
                    }
                ),
                400,
            )

        try:
            entity_id = int(entity_id)
        except ValueError:
            return (
                jsonify(
                    {"success": False, "error": "entity_id must be a valid integer"}
                ),
                400,
            )

        service = get_entity_indexing_service()
        success = service.update_entity_index(entity_type, entity_id)

        if success:
            return (
                jsonify(
                    {
                        "success": True,
                        "message": f"Successfully indexed {entity_type} {entity_id}",
                    }
                ),
                200,
            )
        else:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"Failed to index {entity_type} {entity_id}",
                    }
                ),
                500,
            )

    except Exception as e:
        logger.error(f"Error in index_single_entity: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@entity_indexing_bp.route("/status", methods=["GET"])
def get_indexing_status():
    """Get the current status of entity indexing"""
    try:
        from backend.models import Client, Project, Website, Task

        # Count entities
        client_count = db.session.query(Client).count()
        project_count = db.session.query(Project).count()
        website_count = db.session.query(Website).count()
        task_count = db.session.query(Task).count()

        return (
            jsonify(
                {
                    "success": True,
                    "entity_counts": {
                        "clients": client_count,
                        "projects": project_count,
                        "websites": website_count,
                        "tasks": task_count,
                    },
                    "message": "Entity indexing status retrieved successfully",
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Error in get_indexing_status: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@entity_indexing_bp.route("/reindex-entity-type", methods=["POST"])
def reindex_entity_type():
    """Reindex all entities of a specific type"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request must be JSON"}), 400

        entity_type = data.get("entity_type")

        if not entity_type:
            return jsonify({"success": False, "error": "entity_type is required"}), 400

        if entity_type not in ["client", "project", "website", "task"]:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "entity_type must be one of: client, project, website, task",
                    }
                ),
                400,
            )

        service = get_entity_indexing_service()

        # Index entities of specified type
        success_count = 0
        error_count = 0

        if entity_type == "client":
            from backend.models import Client

            clients = db.session.query(Client).all()
            for client in clients:
                if service.index_client(client):
                    success_count += 1
                else:
                    error_count += 1

        elif entity_type == "project":
            from backend.models import Project

            projects = db.session.query(Project).all()
            for project in projects:
                if service.index_project(project):
                    success_count += 1
                else:
                    error_count += 1

        elif entity_type == "website":
            from backend.models import Website

            websites = db.session.query(Website).all()
            for website in websites:
                if service.index_website(website):
                    success_count += 1
                else:
                    error_count += 1

        elif entity_type == "task":
            from backend.models import Task

            tasks = db.session.query(Task).all()
            for task in tasks:
                if service.index_task(task):
                    success_count += 1
                else:
                    error_count += 1

        # Persist changes
        if service.storage_context:
            service.storage_context.persist()

        return (
            jsonify(
                {
                    "success": True,
                    "message": f"Reindexed {entity_type} entities",
                    "results": {
                        "success_count": success_count,
                        "error_count": error_count,
                        "total_processed": success_count + error_count,
                    },
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Error in reindex_entity_type: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500
