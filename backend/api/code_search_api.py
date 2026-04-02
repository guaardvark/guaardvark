"""Code-specific search endpoints: symbol search, code semantic search, file pattern search."""

import json
import logging
from typing import Dict, List, Optional

from flask import Blueprint, jsonify, request

from backend.models import Document, Folder, db

logger = logging.getLogger(__name__)

code_search_bp = Blueprint("code_search", __name__, url_prefix="/api/search")


def _parse_symbols_from_metadata(
    metadata_json: Optional[str], query: str
) -> List[Dict]:
    """Extract matching symbols from a Document's file_metadata JSON."""
    if not metadata_json:
        return []
    try:
        data = json.loads(metadata_json)
    except (json.JSONDecodeError, TypeError):
        return []

    symbols = data.get("symbols", [])
    query_lower = query.lower()
    return [s for s in symbols if query_lower in s.get("name", "").lower()]


@code_search_bp.route("/symbols", methods=["GET"])
def search_symbols():
    """Search for code symbols (functions, classes, methods) by name."""
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify({"error": "Query 'q' must be at least 2 characters"}), 400

    language = request.args.get("language")
    symbol_type = request.args.get("type")
    folder_id = request.args.get("folder_id", type=int)

    try:
        # Query documents that are code files with matching metadata
        query = Document.query.filter(
            Document.is_code_file == True,
            Document.file_metadata.isnot(None),
        )

        if folder_id:
            query = query.filter(Document.folder_id == folder_id)

        # Get documents and search within their symbol lists
        docs = query.limit(200).all()
        results = []

        for doc in docs:
            matching = _parse_symbols_from_metadata(doc.file_metadata, q)
            if symbol_type:
                matching = [s for s in matching if s.get("type") == symbol_type]
            if language:
                try:
                    meta = json.loads(doc.file_metadata)
                    if meta.get("language") != language:
                        continue
                except (json.JSONDecodeError, TypeError):
                    continue

            for sym in matching:
                results.append({
                    "name": sym["name"],
                    "type": sym.get("type", "unknown"),
                    "line": sym.get("line"),
                    "file_path": doc.path,
                    "filename": doc.filename,
                    "document_id": doc.id,
                    "folder_id": doc.folder_id,
                    "language": json.loads(doc.file_metadata).get("language") if doc.file_metadata else None,
                })

        return jsonify({"symbols": results, "count": len(results)}), 200

    except Exception as e:
        logger.error(f"Symbol search failed: {e}", exc_info=True)
        return jsonify({"error": "Symbol search failed"}), 500


@code_search_bp.route("/code", methods=["POST"])
def search_code_semantic():
    """Semantic search scoped to code files only."""
    data = request.get_json()
    query_text = data.get("query", "").strip()
    if not query_text:
        return jsonify({"error": "Query is required"}), 400

    folder_id = data.get("folder_id")
    language = data.get("language")
    top_k = data.get("top_k", 10)

    try:
        from backend.services.indexing_service import search_with_llamaindex
        results = search_with_llamaindex(query_text, max_chunks=top_k, project_id=None)

        # Filter to code files only
        code_results = [
            r for r in results
            if r.get("metadata", {}).get("content_type") == "code"
            or r.get("metadata", {}).get("is_code_file")
            or r.get("metadata", {}).get("language")
        ]

        if language:
            code_results = [
                r for r in code_results
                if r.get("metadata", {}).get("language") == language
            ]

        return jsonify({
            "results": code_results[:top_k],
            "count": len(code_results),
        }), 200

    except Exception as e:
        logger.error(f"Code semantic search failed: {e}", exc_info=True)
        return jsonify({"error": "Code search failed"}), 500


@code_search_bp.route("/files", methods=["GET"])
def search_files():
    """Search for files by path pattern within a repository."""
    pattern = request.args.get("pattern", "").strip()
    folder_id = request.args.get("folder_id", type=int)
    language = request.args.get("language")

    if not pattern and not folder_id:
        return jsonify({"error": "Provide 'pattern' or 'folder_id'"}), 400

    try:
        query = Document.query

        if pattern:
            query = query.filter(Document.path.ilike(f"%{pattern}%"))

        if folder_id:
            folder_ids = _get_subfolder_ids(folder_id)
            query = query.filter(Document.folder_id.in_(folder_ids))

        if language:
            from backend.utils.code_chunker import CODE_LANGUAGE_MAP
            matching_exts = [
                ext for ext, lang in CODE_LANGUAGE_MAP.items() if lang == language
            ]
            if matching_exts:
                from sqlalchemy import or_
                conditions = [Document.filename.ilike(f"%{ext}") for ext in matching_exts]
                query = query.filter(or_(*conditions))

        docs = query.limit(100).all()
        return jsonify({
            "files": [
                {
                    "id": d.id,
                    "filename": d.filename,
                    "path": d.path,
                    "type": d.type,
                    "folder_id": d.folder_id,
                    "is_code_file": d.is_code_file,
                    "index_status": d.index_status,
                }
                for d in docs
            ],
            "count": len(docs),
        }), 200

    except Exception as e:
        logger.error(f"File search failed: {e}", exc_info=True)
        return jsonify({"error": "File search failed"}), 500


def _get_subfolder_ids(folder_id: int) -> List[int]:
    """Get all subfolder IDs recursively."""
    ids = [folder_id]
    subfolders = Folder.query.filter(Folder.parent_id == folder_id).all()
    for sf in subfolders:
        ids.extend(_get_subfolder_ids(sf.id))
    return ids
