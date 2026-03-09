# doc_query_api.py   Version 2.001

import os

from flask import Blueprint, jsonify, request
from llama_index.core import (StorageContext, VectorStoreIndex,
                              load_index_from_storage)
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.vector_stores.types import ExactMatchFilter, MetadataFilters

doc_query_bp = Blueprint("doc_query", __name__, url_prefix="/api/docs/query")


@doc_query_bp.route("", methods=["POST"])
def query_documents():
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        data = request.get_json()
        question = data.get("question")
        metadata = data.get("metadata", {})

        if not question:
            return jsonify({"error": "Missing 'question' in request"}), 400

        # Load the vector index from storage
        project_id = data.get("project_id")
        # Use direct config import instead of current_app to avoid context issues
        from backend.config import STORAGE_DIR
        base_dir = STORAGE_DIR
            
        if not base_dir:
            return jsonify({"error": "Server misconfigured"}), 500
            
        from backend.services.indexing_service import get_index_for_project

        index, storage_context = get_index_for_project(project_id, base_dir)
        if index is None or storage_context is None:
            return jsonify({"error": "Index not available"}), 500

        # Handle optional metadata filters
        filters = []
        for key, value in metadata.items():
            filters.append(ExactMatchFilter(key=key, value=value))

        metadata_filter = MetadataFilters(filters=filters) if filters else None

        # Create retriever with optional metadata
        retriever = index.as_retriever(similarity_top_k=5, filters=metadata_filter)
        query_engine = RetrieverQueryEngine(retriever=retriever)

        response = query_engine.query(question)

        sources = [
            {
                "doc_id": node.node_id,
                "score": node.score,
                "text": node.text[:500],
                "metadata": node.metadata,
            }
            for node in response.source_nodes
        ]

        return jsonify({"response": str(response), "sources": sources})

    except Exception as e:
        logger.error(f"Document query error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
