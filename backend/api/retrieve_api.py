# backend/api/retrieve_api.py
# Version updated to use global index/retriever

import logging

from flask import Blueprint, current_app, jsonify, request
# LlamaIndex imports
from llama_index.core import VectorStoreIndex  # For type hinting if needed
from llama_index.core.retrievers import BaseRetriever

# --- REMOVED Local LlamaIndex Initialization ---
# Initialization now happens centrally in app.py

# --- Blueprint Definition ---
retrieve_bp = Blueprint("retrieve_api", __name__, url_prefix="/api/retrieve")


# --- API Route ---
@retrieve_bp.route("", methods=["POST"])
def retrieve_from_documents():
    """
    Handles POST requests to /api/retrieve for retrieving nodes from the index.
    Expects JSON body with 'query' and optional 'top_k'.
    """
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received POST /api/retrieve request")

    # Determine target project index
    project_id = request.args.get("project_id", type=int)
    if project_id is None and request.is_json:
        body = request.get_json(silent=True) or {}
        project_id = body.get("project_id")
        try:
            if project_id is not None:
                project_id = int(project_id)
        except Exception:
            project_id = None

    from backend.services.indexing_service import get_or_create_index

    index_instance, _sc, _path = get_or_create_index(project_id)
    retriever = index_instance.as_retriever() if index_instance else None

    if retriever is None:
        logger.error(
            "API Error (POST /retrieve): Index/Retriever not found in application context."
        )
        return (
            jsonify(
                {"error": "Retrieval components not available. Check server logs."}
            ),
            503,
        )  # Service Unavailable

    if not request.is_json:
        logger.warning("API Error (POST /retrieve): Request body not JSON.")
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    query_text = data.get("query")
    top_k = data.get("top_k", 3)  # Default top_k
    if not query_text:
        logger.warning("API Error (POST /retrieve): Missing 'query'.")
        return jsonify({"error": "Missing 'query' in request body"}), 400

    logger.info(f"API Retrieve: Query='{query_text[:50]}...', Top K='{top_k}'")

    try:
        # Set retriever parameters if needed (e.g., similarity_top_k)
        retriever.similarity_top_k = top_k

        # --- Retrieve Nodes ---
        retrieved_nodes = retriever.retrieve(query_text)

        # --- Process Response ---
        nodes_list = []
        if retrieved_nodes:
            for node_with_score in retrieved_nodes:
                node = node_with_score.node
                nodes_list.append(
                    {
                        "text": node.get_text(),  # Consider truncating if needed
                        "score": (
                            node_with_score.score if node_with_score.score else "N/A"
                        ),
                        "metadata": node.metadata or {},
                        "node_id": node.node_id,
                    }
                )
        logger.info(f"API Retrieve: Retrieved {len(nodes_list)} nodes.")

        return (
            jsonify(
                {
                    "query": query_text,
                    "retrieved_nodes": nodes_list,
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(
            f"API Error (POST /retrieve): Error during retrieval - {e}", exc_info=True
        )
        return (
            jsonify(
                {"error": "Failed to process retrieval request", "details": str(e)}
            ),
            500,
        )
