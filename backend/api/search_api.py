# backend/api/search_api.py   Version 1.1 (Enhanced debug logging + null source handling)

import logging
import os

from flask import Blueprint, current_app, jsonify, request
from llama_index.core import (StorageContext, VectorStoreIndex,
                              load_index_from_storage)
from llama_index.core.query_engine import RetrieverQueryEngine

from backend.config import STORAGE_DIR
from backend.models import Document, db
from backend.services.metadata_service import (get_docs_by_project,
                                               get_docs_by_tag)

# Use centralized query engine utilities
from backend.utils.query_engine_wrapper import get_query_engine

search_bp = Blueprint("search", __name__, url_prefix="/api/search")
logger = logging.getLogger("backend.api.search_api")

# Configuration - use the imported STORAGE_DIR from config
# STORAGE_DIR is imported from backend.config (removed hardcoded override)

# Removed duplicate get_query_engine function - using centralized wrapper instead

@search_bp.route("/semantic", methods=["POST"])
def search_semantic():
    data = request.get_json()
    query = data.get("query")
    if not query:
        return jsonify({"error": "Query is required"}), 400
    try:
        # Use centralized query engine from utils
        storage_context = StorageContext.from_defaults(persist_dir=STORAGE_DIR)
        index = load_index_from_storage(storage_context)
        query_engine = get_query_engine(index)
        
        if not query_engine:
            logger.error("Failed to create query engine from centralized wrapper")
            return jsonify({"error": "Query engine initialization failed"}), 500
        
        result = query_engine.query(query)

        if not result or not result.source_nodes:
            logger.warning("Semantic query returned no source nodes.")
            return (
                jsonify(
                    {
                        "answer": str(result.response) if result else None,
                        "sources": [],
                        "debug": "No source documents matched this query.",
                    }
                ),
                200,
            )

        return (
            jsonify(
                {
                    "answer": str(result.response),
                    "sources": [n.metadata for n in result.source_nodes],
                }
            ),
            200,
        )

    except Exception as e:
        err_msg = str(e)
        # Friendly message when vector index was built with different embedding dimensions (e.g. 384 vs 4096)
        if "not aligned" in err_msg or "dim 0" in err_msg or ("4096" in err_msg and "384" in err_msg):
            logger.warning(
                "Semantic search failed due to embedding dimension mismatch. "
                "Suggest user reset/rebuild index: %s",
                err_msg[:200],
            )
            return (
                jsonify(
                    {
                        "error": "Vector index was built with a different embedding model. "
                        "Please use Settings to reset/rebuild the index and re-upload documents.",
                        "details": err_msg[:500],
                    }
                ),
                503,
            )
        logger.error(f"Semantic search failed: {e}", exc_info=True)
        return jsonify({"error": "Semantic search failed.", "details": err_msg}), 500


@search_bp.route("/by-tag/<tag>", methods=["GET"])
def search_by_tag(tag):
    try:
        doc_ids = get_docs_by_tag(tag)
        docs = db.session.query(Document).filter(Document.id.in_(doc_ids)).all()
        return (
            jsonify(
                [
                    {
                        "id": d.id,
                        "filename": d.filename,
                        "type": d.type,
                        "indexed_at": d.indexed_at,
                        "path": d.path,
                    }
                    for d in docs
                ]
            ),
            200,
        )
    except Exception as e:
        logger.error(f"Tag search failed: {e}", exc_info=True)
        return jsonify({"error": "Tag search failed."}), 500


@search_bp.route("/by-project/<project_id>", methods=["GET"])
def search_by_project(project_id):
    try:
        doc_ids = get_docs_by_project(project_id)
        docs = db.session.query(Document).filter(Document.id.in_(doc_ids)).all()
        return (
            jsonify(
                [
                    {
                        "id": d.id,
                        "filename": d.filename,
                        "type": d.type,
                        "indexed_at": d.indexed_at,
                        "path": d.path,
                    }
                    for d in docs
                ]
            ),
            200,
        )
    except Exception as e:
        logger.error(f"Project search failed: {e}", exc_info=True)
        return jsonify({"error": "Project search failed."}), 500
