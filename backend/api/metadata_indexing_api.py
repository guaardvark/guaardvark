# backend/api/metadata_indexing_api.py
# API for triggering metadata indexing operations

import logging
from flask import Blueprint, request, jsonify, current_app

from backend.utils.db_utils import ensure_db_session_cleanup
from backend.services.metadata_indexing_service import metadata_indexing_service

metadata_indexing_bp = Blueprint("metadata_indexing", __name__, url_prefix="/api/metadata")
logger = logging.getLogger(__name__)

@metadata_indexing_bp.route("/index/client/<int:client_id>", methods=["POST"])
@ensure_db_session_cleanup
def index_client_metadata(client_id):
    """Index metadata for a specific client"""
    logger.info(f"API: Received POST /api/metadata/index/client/{client_id}")
    
    try:
        success = metadata_indexing_service.index_client_metadata(client_id)
        
        if success:
            return jsonify({
                "success": True,
                "message": f"Client {client_id} metadata indexed successfully"
            }), 200
        else:
            return jsonify({
                "success": False,
                "message": f"Failed to index client {client_id} metadata"
            }), 500
            
    except Exception as e:
        logger.error(f"Error indexing client {client_id} metadata: {e}")
        return jsonify({
            "success": False,
            "error": f"Failed to index client metadata: {str(e)}"
        }), 500

@metadata_indexing_bp.route("/index/project/<int:project_id>", methods=["POST"])
@ensure_db_session_cleanup
def index_project_metadata(project_id):
    """Index metadata for a specific project"""
    logger.info(f"API: Received POST /api/metadata/index/project/{project_id}")
    
    try:
        success = metadata_indexing_service.index_project_metadata(project_id)
        
        if success:
            return jsonify({
                "success": True,
                "message": f"Project {project_id} metadata indexed successfully"
            }), 200
        else:
            return jsonify({
                "success": False,
                "message": f"Failed to index project {project_id} metadata"
            }), 500
            
    except Exception as e:
        logger.error(f"Error indexing project {project_id} metadata: {e}")
        return jsonify({
            "success": False,
            "error": f"Failed to index project metadata: {str(e)}"
        }), 500

@metadata_indexing_bp.route("/index/job/<int:task_id>", methods=["POST"])
@ensure_db_session_cleanup
def index_job_metadata(task_id):
    """Index metadata for a specific job"""
    logger.info(f"API: Received POST /api/metadata/index/job/{task_id}")
    
    try:
        success = metadata_indexing_service.index_job_metadata(task_id)
        
        if success:
            return jsonify({
                "success": True,
                "message": f"Job {task_id} metadata indexed successfully"
            }), 200
        else:
            return jsonify({
                "success": False,
                "message": f"Failed to index job {task_id} metadata"
            }), 500
            
    except Exception as e:
        logger.error(f"Error indexing job {task_id} metadata: {e}")
        return jsonify({
            "success": False,
            "error": f"Failed to index job metadata: {str(e)}"
        }), 500

@metadata_indexing_bp.route("/index/all", methods=["POST"])
@ensure_db_session_cleanup
def reindex_all_metadata():
    """Reindex all metadata (clients, projects, jobs)"""
    logger.info("API: Received POST /api/metadata/index/all")
    
    try:
        results = metadata_indexing_service.reindex_all_metadata()
        
        return jsonify({
            "success": True,
            "message": "Metadata reindexing completed",
            "results": results
        }), 200
            
    except Exception as e:
        logger.error(f"Error during full metadata reindexing: {e}")
        return jsonify({
            "success": False,
            "error": f"Failed to reindex all metadata: {str(e)}"
        }), 500

@metadata_indexing_bp.route("/index/relationships", methods=["POST"])
@ensure_db_session_cleanup
def reindex_all_relationships():
    """Reindex all entity relationships for complete RAG context"""
    logger.info("API: Received POST /api/metadata/index/relationships")
    
    try:
        from backend.services.entity_relationship_indexer import entity_relationship_indexer
        
        results = entity_relationship_indexer.reindex_all_relationships()
        
        return jsonify({
            "success": True,
            "message": "Complete relationship reindexing completed",
            "results": results
        }), 200
            
    except Exception as e:
        logger.error(f"Error during relationship reindexing: {e}")
        return jsonify({
            "success": False,
            "error": f"Failed to reindex relationships: {str(e)}"
        }), 500

@metadata_indexing_bp.route("/status", methods=["GET"])
@ensure_db_session_cleanup
def get_metadata_indexing_status():
    """Get status of metadata indexing system"""
    logger.info("API: Received GET /api/metadata/status")
    
    try:
        from backend.utils.unified_job_metadata import unified_job_metadata
        from backend.utils.context_bridge import context_bridge
        
        # Get summary of active jobs
        summary = unified_job_metadata.get_active_jobs_summary()
        context_status = context_bridge.get_context_status()
        
        return jsonify({
            "success": True,
            "metadata_indexing_available": metadata_indexing_service.add_text_to_index is not None,
            "active_jobs_summary": summary,
            "context_bridge_status": context_status,
            "indexing_status": "ready"
        }), 200
            
    except Exception as e:
        logger.error(f"Error getting metadata indexing status: {e}")
        return jsonify({
            "success": False,
            "error": f"Failed to get metadata status: {str(e)}"
        }), 500