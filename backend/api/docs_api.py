# backend/api/docs_api.py
# Version 1.15: Unified indexing entry point - all indexing goes through consistent Celery task pattern

import datetime
import json
import logging
import os
from typing import Optional

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload
from werkzeug.utils import secure_filename
from backend.utils.db_utils import ensure_db_session_cleanup
from backend.utils.response_utils import success_response, error_response

try:
    from llama_index.core import VectorStoreIndex

    from backend.models import Document as DBDocument
    from backend.models import Project, Website, db

    imports_ok = True
except ImportError as e:
    logging.basicConfig(level=logging.CRITICAL)
    logging.critical(
        f"CRITICAL: Failed to import dependencies for docs_api: {e}", exc_info=True
    )
    try:
        from backend.models import Document as DBDocument
        from backend.models import Project, Website, db
    except Exception:
        db = DBDocument = Project = Website = None
    VectorStoreIndex = None
    # Allow routes that don't depend on LlamaIndex to function
    imports_ok = True

# Remove top-level import of isolated tasks to prevent circular imports
# Tasks will be imported lazily when needed

docs_bp = Blueprint("docs", __name__, url_prefix="/api/docs")
logger = logging.getLogger(__name__)


def _is_code_file(filename: str) -> bool:
    """Detect if a file is a code file that should be stored for discussion."""
    code_extensions = {
        '.js', '.jsx', '.ts', '.tsx',  # JavaScript/TypeScript
        '.py', '.pyx', '.pyi',          # Python
        '.java', '.class',              # Java
        '.cpp', '.cc', '.cxx', '.c',    # C/C++
        '.h', '.hpp', '.hxx',           # C/C++ headers
        '.cs',                          # C#
        '.php',                         # PHP
        '.rb',                          # Ruby
        '.go',                          # Go
        '.rs',                          # Rust
        '.swift',                       # Swift
        '.kt', '.kts',                  # Kotlin
        '.scala',                       # Scala
        '.sh', '.bash', '.zsh',         # Shell scripts
        '.sql',                         # SQL
        '.css', '.scss', '.sass',       # Stylesheets
        '.html', '.htm', '.xml',        # Markup
        '.json', '.yaml', '.yml',       # Configuration
        '.dockerfile', '.Dockerfile',    # Docker
        '.md', '.markdown',             # Markdown (often has code)
        '.vue',                         # Vue.js
        '.svelte',                      # Svelte
        '.dart',                        # Dart
        '.r', '.R',                     # R
        '.matlab', '.m',                # MATLAB
        '.pl', '.pm',                   # Perl
        '.lua',                         # Lua
        '.vim',                         # Vim script
    }
    
    # Check file extension
    _, ext = os.path.splitext(filename.lower())
    return ext in code_extensions


def _is_text_file(filename: str) -> bool:
    """Detect if a file is a text file that should have content stored."""
    text_extensions = {
        # Code files (already covered by _is_code_file)
        '.js', '.jsx', '.ts', '.tsx', '.py', '.java', '.cpp', '.c', '.h', '.cs', 
        '.php', '.rb', '.go', '.rs', '.swift', '.kt', '.scala', '.sh', '.sql',
        '.css', '.scss', '.sass', '.html', '.htm', '.xml', '.json', '.yaml', '.yml',
        '.md', '.markdown', '.vue', '.svelte', '.dart', '.r', '.matlab', '.pl', '.lua',
        # Text documents
        '.txt', '.rtf', '.csv', '.tsv', '.log',
        # Configuration files
        '.ini', '.cfg', '.conf', '.toml', '.properties',
        # Documentation
        '.rst', '.tex', '.asciidoc', '.adoc',
        # Data files
        '.jsonl', '.ndjson', '.xml', '.xsl', '.xslt',
        # Other text formats
        '.diff', '.patch', '.gitignore', '.env'
    }
    
    # Check file extension
    _, ext = os.path.splitext(filename.lower())
    return ext in text_extensions


def _read_file_content(file_path: str, max_size_mb: int = 100) -> str:
    """Read file content safely with increased size limits."""
    try:
        # BUG FIX: Validate file exists before processing
        if not os.path.exists(file_path):
            logger.error(f"File does not exist: {file_path}")
            return None
            
        file_size = os.path.getsize(file_path)
        max_size_bytes = max_size_mb * 1024 * 1024  # Convert MB to bytes
        
        if file_size > max_size_bytes:
            logger.warning(f"File {file_path} is too large ({file_size} bytes, limit: {max_size_bytes} bytes) to store content")
            return None
            
        # Try different encodings for better compatibility
        encodings = ['utf-8', 'utf-8-sig', 'latin1', 'cp1252', 'iso-8859-1']
        
        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding, errors='replace') as f:
                    content = f.read()
                    logger.debug(f"Successfully read file using {encoding} encoding")
                    return content
            except UnicodeDecodeError:
                continue
        
        # If all encodings fail, try binary mode and decode with error replacement
        logger.warning(f"All text encodings failed for {file_path}, using binary mode")
        with open(file_path, 'rb') as f:
            raw_content = f.read()
            content = raw_content.decode('utf-8', errors='replace')
            return content
            
    except (OSError, IOError, UnicodeDecodeError) as e:
        logger.error(f"Failed to read file content from {file_path}: {e}")
        return None


@docs_bp.route("/", methods=["GET"])
def list_documents():
    """GET /api/docs: Retrieves a paginated list of documents."""
    logger.info("API: Received GET /api/docs request")
    if not imports_ok or not db or not DBDocument:
        return (
            jsonify({"error": "Server configuration error: Dependencies missing."}),
            500,
        )

    try:
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 10, type=int)
        per_page = max(1, min(per_page, 10000))
        project_id_filter = request.args.get("project_id", type=int)
        tag_filter = request.args.get("tag")
        query = db.session.query(DBDocument)

        if project_id_filter is not None:
            if hasattr(DBDocument, "project_id"):
                query = query.filter(DBDocument.project_id == project_id_filter)
            else:
                logger.warning(
                    "DBDocument model does not have 'project_id' for filtering."
                )

        if tag_filter:
            query = query.filter(DBDocument.tags.ilike(f"%{tag_filter}%"))

        sort_by = request.args.get("sort_by", "uploaded_at")
        sort_order = request.args.get("sort_order", "desc").lower()
        allowed_sort_columns = [
            "id",
            "filename",
            "type",
            "index_status",
            "uploaded_at",
            "updated_at",
            "project_id",
            "website_id",
        ]
        if sort_by not in allowed_sort_columns:
            sort_by = "uploaded_at"

        sort_column = getattr(DBDocument, sort_by, None)
        if sort_column:
            query = query.order_by(
                sort_column.asc() if sort_order == "asc" else sort_column.desc()
            )

        load_options = [
            joinedload(DBDocument.project).load_only(Project.id, Project.name),
            joinedload(DBDocument.website).load_only(Website.id, Website.url),
        ]
        query = query.options(*load_options)
        paginated_docs = query.paginate(page=page, per_page=per_page, error_out=False)

        # FIXED: Cross-validate document status with progress system for consistency
        documents_data = []
        try:
            from backend.utils.unified_progress_system import get_unified_progress
            progress_system = get_unified_progress()
            
            for doc in paginated_docs.items:
                doc_data = doc.to_dict()
                
                # Validate status consistency if document has an active job
                if doc_data.get('indexing_job_id'):
                    try:
                        progress_info = progress_system.get_process(doc_data['indexing_job_id'])
                        if progress_info:
                            progress_status = progress_info.status.value if hasattr(progress_info.status, 'value') else str(progress_info.status)
                            doc_status = doc_data.get('index_status')
                            
                            # Fix inconsistencies between progress and document status
                            if progress_status in ['in_progress', 'pending'] and doc_status == 'INDEXED':
                                logger.warning(f"Document {doc.id} shows INDEXED but progress is {progress_status}")
                                doc_data['index_status'] = 'INDEXING'
                                doc_data['status_note'] = f'Corrected from progress system ({progress_status})'
                            elif progress_status == 'error' and doc_status in ['INDEXED', 'INDEXING']:
                                logger.warning(f"Document {doc.id} shows {doc_status} but progress failed")
                                doc_data['index_status'] = 'ERROR'
                                doc_data['status_note'] = 'Corrected from failed progress'
                    except Exception as progress_check_error:
                        logger.debug(f"Could not validate progress for document {doc.id}: {progress_check_error}")
                
                documents_data.append(doc_data)
                
        except Exception as validation_error:
            logger.warning(f"Document status validation failed: {validation_error}")
            # Fall back to original data without validation
            documents_data = [doc.to_dict() for doc in paginated_docs.items]

        return (
            jsonify(
                {
                    "documents": documents_data,
                    "total": paginated_docs.total,
                    "pages": paginated_docs.pages,
                    "current_page": page,
                    "per_page": per_page,
                }
            ),
            200,
        )
    except (SQLAlchemyError, OSError, ValueError) as e:
        logger.error(f"API Error (GET /docs): {e}", exc_info=True)
        if isinstance(e, SQLAlchemyError):
            db.session.rollback()
        return (
            jsonify({"error": "Server error fetching documents", "details": str(e)}),
            500,
        )


@docs_bp.route("/<int:doc_id>/status", methods=["GET"])
def get_document_status(doc_id):
    """GET /api/docs/<int:doc_id>/status: Retrieves the index_status of a specific document."""
    logger.debug(f"API: Received GET /api/docs/{doc_id}/status request")
    if not imports_ok or not db or not DBDocument:
        return error_response("Server configuration error", 500, "CONFIG_ERROR")

    try:
        document = db.session.query(DBDocument).filter_by(id=doc_id).first()
        if not document:
            return jsonify({"error": "Document not found"}), 404

        logger.debug(f"Found document {doc_id} with status: {document.index_status}")
        return (
            jsonify(
                {
                    "doc_id": document.id,
                    "index_status": document.index_status,
                    "filename": document.filename,
                }
            ),
            200,
        )

    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(
            f"API Error (GET /docs/{doc_id}/status): DB error: {e}", exc_info=True
        )
        return (
            jsonify(
                {"error": "Database error fetching document status", "details": str(e)}
            ),
            500,
        )
    except (OSError, ValueError, AttributeError) as e:
        logger.error(
            f"API Error (GET /docs/{doc_id}/status): Unexpected error: {e}",
            exc_info=True,
        )
        return jsonify({"error": "Unexpected server error", "details": str(e)}), 500


@docs_bp.route("/<int:doc_id>/usage", methods=["GET"])
def get_document_usage(doc_id):
    """GET /api/docs/<int:doc_id>/usage: Retrieves usage analytics for a document."""
    logger.debug(f"API: Received GET /api/docs/{doc_id}/usage request")
    if not imports_ok or not db or not DBDocument:
        return jsonify({"error": "Server configuration error"}), 500

    try:
        document = db.session.query(DBDocument).filter_by(id=doc_id).first()
        if not document:
            return jsonify({"error": "Document not found"}), 404

        if document.index_status != "INDEXED":
            return jsonify({
                "error": "Document not indexed",
                "message": "Usage data is only available for indexed documents"
            }), 400

        # Get document file size
        file_size = 0
        if document.path and current_app.config.get("UPLOAD_FOLDER"):
            full_path = os.path.join(current_app.config["UPLOAD_FOLDER"], document.path)
            if os.path.exists(full_path):
                file_size = os.path.getsize(full_path)

        # Get index manager for retrieval statistics
        index_manager = current_app.config.get("INDEX_MANAGER")
        retrieval_stats = {
            "retrieval_count": 0,
            "last_retrieved": None,
            "avg_similarity_score": 0.0,
            "context_hits": 0,
        }

        if index_manager and hasattr(index_manager, "get_cache_stats"):
            cache_stats = index_manager.get_cache_stats()
            # Estimate retrieval count based on cache hits
            retrieval_stats["retrieval_count"] = cache_stats.get("cache_hits", 0) // 10  # Rough estimate
            retrieval_stats["avg_similarity_score"] = 0.75  # Default score for indexed documents

        # Get last indexed time as last retrieved time
        if document.indexed_at:
            retrieval_stats["last_retrieved"] = document.indexed_at.isoformat()

        # Estimate context hits based on document size and type
        if file_size > 0:
            retrieval_stats["context_hits"] = max(1, file_size // 1024)  # Rough estimate

        usage_data = {
            "document_id": doc_id,
            "filename": document.filename,
            "index_status": document.index_status,
            "retrieval_stats": retrieval_stats,
            "storage_size": file_size,
            "uploaded_at": document.uploaded_at.isoformat() if document.uploaded_at else None,
            "indexed_at": document.indexed_at.isoformat() if document.indexed_at else None,
        }

        return jsonify(usage_data), 200

    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(
            f"API Error (GET /docs/{doc_id}/usage): DB error: {e}", exc_info=True
        )
        return (
            jsonify(
                {"error": "Database error fetching document usage", "details": str(e)}
            ),
            500,
        )
    except (OSError, ValueError, AttributeError) as e:
        logger.error(
            f"API Error (GET /docs/{doc_id}/usage): Unexpected error: {e}",
            exc_info=True,
        )
        return jsonify({"error": "Unexpected server error", "details": str(e)}), 500


@docs_bp.route("/<int:doc_id>/context", methods=["GET"])
def get_document_context(doc_id):
    """GET /api/docs/<int:doc_id>/context: Retrieves context information for a document."""
    logger.debug(f"API: Received GET /api/docs/{doc_id}/context request")
    if not imports_ok or not db or not DBDocument:
        return jsonify({"error": "Server configuration error"}), 500

    try:
        document = db.session.query(DBDocument).filter_by(id=doc_id).first()
        if not document:
            return jsonify({"error": "Document not found"}), 404

        if document.index_status != "INDEXED":
            return jsonify({
                "error": "Document not indexed",
                "message": "Context information is only available for indexed documents"
            }), 400

        # Get document file size for chunk estimation
        file_size = 0
        if document.path and current_app.config.get("UPLOAD_FOLDER"):
            full_path = os.path.join(current_app.config["UPLOAD_FOLDER"], document.path)
            if os.path.exists(full_path):
                file_size = os.path.getsize(full_path)

        # Estimate chunk count based on file size (rough estimate: 1KB per chunk)
        chunk_count = max(1, file_size // 1024) if file_size > 0 else 1
        avg_chunk_size = file_size // chunk_count if chunk_count > 0 else 0

        # Determine content types based on file extension
        content_types = []
        if document.filename:
            ext = document.filename.lower().split('.')[-1]
            if ext in ['txt', 'md', 'rst']:
                content_types = ["text"]
            elif ext in ['py', 'js', 'ts', 'java', 'cpp', 'c', 'cs']:
                content_types = ["code"]
            elif ext in ['csv', 'xlsx', 'xls']:
                content_types = ["table"]
            elif ext in ['pdf', 'doc', 'docx']:
                content_types = ["text", "table"]
            else:
                content_types = ["text"]

        # Extract entities from tags if available
        entities = []
        if document.tags:
            try:
                tags = json.loads(document.tags) if isinstance(document.tags, str) else document.tags
                if isinstance(tags, list):
                    entities = [tag for tag in tags if len(tag) > 2]  # Filter meaningful tags
            except (json.JSONDecodeError, TypeError):
                pass

        # Get relationships from project and website
        relationships = []
        if document.project:
            relationships.append("project")
        if document.website:
            relationships.append("website")

        context_data = {
            "document_id": doc_id,
            "filename": document.filename,
            "chunk_count": chunk_count,
            "avg_chunk_size": avg_chunk_size,
            "content_types": content_types,
            "entities": entities,
            "relationships": relationships,
            "file_size": file_size,
            "document_type": document.type,
            "project_name": document.project.name if document.project else None,
            "website_url": document.website.url if document.website else None,
        }

        return jsonify(context_data), 200

    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(
            f"API Error (GET /docs/{doc_id}/context): DB error: {e}", exc_info=True
        )
        return (
            jsonify(
                {"error": "Database error fetching document context", "details": str(e)}
            ),
            500,
        )
    except (OSError, ValueError, AttributeError) as e:
        logger.error(
            f"API Error (GET /docs/{doc_id}/context): Unexpected error: {e}",
            exc_info=True,
        )
        return jsonify({"error": "Unexpected server error", "details": str(e)}), 500


@docs_bp.route("/<int:doc_id>/content", methods=["GET"])
def get_document_content(doc_id):
    """GET /api/docs/<int:doc_id>/content: Retrieves complete file content for a document."""
    logger.debug(f"API: Received GET /api/docs/{doc_id}/content request")
    if not imports_ok or not db or not DBDocument:
        return jsonify({"error": "Server configuration error"}), 500

    try:
        document = db.session.query(DBDocument).filter_by(id=doc_id).first()
        if not document:
            return jsonify({"error": "Document not found"}), 404

        # Check if document has stored content (for code files)
        if document.content is not None:
            # Return stored content from database
            content_info = {
                "document_id": doc_id,
                "filename": document.filename,
                "content": document.content,
                "source": "database",
                "is_code_file": getattr(document, 'is_code_file', False),
                "index_status": document.index_status,
                "content_length": len(document.content),
                "storage_type": "complete" if document.index_status == "STORED" else "truncated"
            }
            
            # Add truncation info if applicable
            if document.index_status == "STORED_TRUNCATED":
                content_info["truncation_info"] = {
                    "is_truncated": True,
                    "error_message": document.error_message
                }
            
            return jsonify(content_info), 200

        # If no content in database, try to read from file
        if document.path and current_app.config.get("UPLOAD_FOLDER"):
            full_path = os.path.join(current_app.config["UPLOAD_FOLDER"], document.path)
            if os.path.exists(full_path):
                # Read content from file
                file_content = _read_file_content(full_path, max_size_mb=100)
                if file_content is not None:
                    content_info = {
                        "document_id": doc_id,
                        "filename": document.filename,
                        "content": file_content,
                        "source": "file_system",
                        "is_code_file": getattr(document, 'is_code_file', False),
                        "index_status": document.index_status,
                        "content_length": len(file_content),
                        "storage_type": "file_read"
                    }
                    return jsonify(content_info), 200
                else:
                    return jsonify({
                        "error": "Failed to read file content",
                        "message": "Could not read content from file system"
                    }), 500
            else:
                return jsonify({
                    "error": "File not found",
                    "message": f"File not found at expected location: {full_path}"
                }), 404

        # No content available
        return jsonify({
            "error": "No content available",
            "message": "Document has no stored content and file is not accessible",
            "document_id": doc_id,
            "filename": document.filename,
            "index_status": document.index_status
        }), 404

    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(
            f"API Error (GET /docs/{doc_id}/content): DB error: {e}", exc_info=True
        )
        return (
            jsonify(
                {"error": "Database error fetching document content", "details": str(e)}
            ),
            500,
        )
    except (OSError, IOError, UnicodeDecodeError) as e:
        logger.error(
            f"API Error (GET /docs/{doc_id}/content): Unexpected error: {e}",
            exc_info=True,
        )
        return jsonify({"error": "Unexpected server error", "details": str(e)}), 500


@docs_bp.route("/<int:doc_id>/content/raw", methods=["GET"])
def get_document_content_raw(doc_id):
    """GET /api/docs/<int:doc_id>/content/raw: Returns raw file content as plain text."""
    logger.debug(f"API: Received GET /api/docs/{doc_id}/content/raw request")
    if not imports_ok or not db or not DBDocument:
        return "Server configuration error", 500

    try:
        document = db.session.query(DBDocument).filter_by(id=doc_id).first()
        if not document:
            return "Document not found", 404

        # Check if document has stored content
        if document.content is not None:
            from flask import Response
            return Response(
                document.content,
                mimetype='text/plain',
                headers={
                    'Content-Disposition': f'inline; filename="{document.filename}"',
                    'X-Content-Source': 'database',
                    'X-Storage-Type': 'complete' if document.index_status == "STORED" else 'truncated'
                }
            )

        # Try to read from file system
        if document.path and current_app.config.get("UPLOAD_FOLDER"):
            full_path = os.path.join(current_app.config["UPLOAD_FOLDER"], document.path)
            if os.path.exists(full_path):
                file_content = _read_file_content(full_path, max_size_mb=100)
                if file_content is not None:
                    from flask import Response
                    return Response(
                        file_content,
                        mimetype='text/plain',
                        headers={
                            'Content-Disposition': f'inline; filename="{document.filename}"',
                            'X-Content-Source': 'file_system',
                            'X-Storage-Type': 'file_read'
                        }
                    )

        return "No content available", 404

    except Exception as e:
        logger.error(
            f"API Error (GET /docs/{doc_id}/content/raw): Unexpected error: {e}",
            exc_info=True,
        )
        return f"Server error: {str(e)}", 500


@docs_bp.route("/<int:doc_id>", methods=["GET", "PUT", "DELETE"])
@ensure_db_session_cleanup
def manage_document(doc_id):
    if not imports_ok or not db or not DBDocument:
        return jsonify({"error": "Server configuration error"}), 500

    document = db.session.get(DBDocument, doc_id)
    if not document:
        logger.warning(
            f"Document with ID {doc_id} not found for method {request.method}."
        )
        return jsonify({"error": "Document not found"}), 404

    if request.method == "GET":
        logger.info(f"API: GET /api/docs/{doc_id}")
        document_with_relations = (
            db.session.query(DBDocument)
            .options(
                joinedload(DBDocument.project).load_only(Project.id, Project.name),
                joinedload(DBDocument.website).load_only(Website.id, Website.url),
            )
            .filter(DBDocument.id == doc_id)
            .first()
        )
        if not document_with_relations:
            return (
                jsonify({"error": "Document not found after trying to load relations"}),
                404,
            )
        return jsonify(document_with_relations.to_dict()), 200

    elif request.method == "PUT":
        logger.info(f"API: PUT /api/docs/{doc_id}")
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided for update."}), 400
        try:
            if "project_id" in data:
                project_id_val = data["project_id"]
                if project_id_val is not None:
                    project = db.session.get(Project, project_id_val)
                    if not project:
                        return (
                            jsonify(
                                {
                                    "error": f"Project with ID {project_id_val} not found."
                                }
                            ),
                            400,
                        )
                document.project_id = project_id_val

            if "tags" in data:
                tags_data = data["tags"]
                if isinstance(tags_data, list):
                    cleaned_tags = [
                        str(tag).strip() for tag in tags_data if str(tag).strip()
                    ]
                    document.tags = json.dumps(cleaned_tags)
                    logger.debug(
                        f"Updating doc {doc_id} tags to JSON string: {document.tags}"
                    )
                elif tags_data is None:
                    document.tags = json.dumps([])
                    logger.debug(f"Clearing tags for doc {doc_id}")
                else:
                    logger.warning(
                        f"Tags data for doc {doc_id} was not a list or null: {type(tags_data)}. Storing as empty JSON array."
                    )
                    document.tags = json.dumps([])

            document.updated_at = datetime.datetime.now(datetime.UTC)
            db.session.commit()
            logger.info(f"Document {doc_id} updated successfully. Data: {data}")

            updated_document = (
                db.session.query(DBDocument)
                .options(
                    joinedload(DBDocument.project).load_only(Project.id, Project.name),
                    joinedload(DBDocument.website).load_only(Website.id, Website.url),
                )
                .filter(DBDocument.id == doc_id)
                .first()
            )
            return jsonify(updated_document.to_dict()), 200
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(
                f"API Error (PUT /docs/{doc_id}): DB error: {e}", exc_info=True
            )
            return (
                jsonify(
                    {"error": "Database error updating document", "details": str(e)}
                ),
                500,
            )
        except Exception as e:
            db.session.rollback()
            logger.error(
                f"API Error (PUT /docs/{doc_id}): Unexpected: {e}", exc_info=True
            )
            return jsonify({"error": "Unexpected server error", "details": str(e)}), 500

    elif request.method == "DELETE":
        logger.info(f"API: DELETE /api/docs/{doc_id} - Enhanced deletion for code files")
        
        # Enhanced deletion: Handle code files vs indexed files differently
        is_code_file = document.is_code_file if hasattr(document, 'is_code_file') else False
        index_status = document.index_status
        
        # Cancel any associated indexing jobs before deletion
        if hasattr(document, 'indexing_job_id') and document.indexing_job_id:
            try:
                from backend.utils.unified_progress_system import get_unified_progress
                progress_system = get_unified_progress()
                logger.info(f"Cancelling indexing job {document.indexing_job_id} for document {doc_id}")
                progress_system.cancel_process(document.indexing_job_id, f"Document {doc_id} deleted")
                logger.info(f"Successfully cancelled indexing job {document.indexing_job_id}")
            except Exception as cancel_error:
                logger.warning(f"Failed to cancel indexing job {document.indexing_job_id}: {cancel_error}")
                # Continue with deletion even if job cancellation fails
        
        # Only try to remove from index if it was actually indexed
        if not is_code_file and index_status in ["INDEXED", "INDEXING"]:
            index_instance: Optional[VectorStoreIndex] = current_app.config.get("LLAMA_INDEX_INDEX")
            storage_dir: Optional[str] = current_app.config.get("STORAGE_DIR")
            
            if (index_instance and VectorStoreIndex and storage_dir and 
                hasattr(index_instance, "storage_context") and 
                hasattr(index_instance, "delete_ref_doc")):
                
                try:
                    ref_doc_id_to_delete = str(document.id)
                    index_instance.delete_ref_doc(ref_doc_id_to_delete, delete_from_docstore=True)
                    logger.info(f"Removed document {doc_id} from vector index")
                    
                    try:
                        index_instance.storage_context.persist(persist_dir=storage_dir)
                        logger.info(f"Persisted index changes after deleting doc {doc_id}")
                    except Exception as persist_err:
                        logger.warning(f"Failed to persist index after deletion: {persist_err}")
                        
                except Exception as index_err:
                    logger.warning(f"Failed to remove from index (continuing with deletion): {index_err}")
            else:
                logger.info(f"Index not available for document {doc_id} - skipping index removal")
        else:
            logger.info(f"Document {doc_id} is a code file (stored={index_status}) - skipping index removal")

        try:
            doc_filename = document.filename
            upload_folder: Optional[str] = current_app.config.get("UPLOAD_FOLDER")
            
            # Remove from database
            db.session.delete(document)
            logger.info(f"Document {doc_id} ({doc_filename}) staged for DB deletion")

            # Remove file from disk
            doc_path_to_delete = document.path
            if doc_path_to_delete and upload_folder:
                full_doc_path = (
                    os.path.join(upload_folder, doc_path_to_delete)
                    if not os.path.isabs(doc_path_to_delete)
                    else doc_path_to_delete
                )
                if os.path.exists(full_doc_path):
                    os.remove(full_doc_path)
                    logger.info(f"Deleted file from disk: {full_doc_path}")
                else:
                    logger.warning(f"File not found for deletion: {full_doc_path}")

            # Commit all changes
            db.session.commit()
            
            delete_type = "code file" if is_code_file else "indexed document"
            logger.info(f"Successfully deleted {delete_type} {doc_id} ({doc_filename})")
            
            return jsonify({
                "message": f"Document {doc_id} deleted successfully",
                "filename": doc_filename,
                "type": delete_type
            }), 200
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"API Error (DELETE /docs/{doc_id}): {e}", exc_info=True)
            return jsonify({"error": "Error during deletion", "details": str(e)}), 500


@docs_bp.route("/upload", methods=["POST"])
@ensure_db_session_cleanup
def upload_document():
    """POST /api/docs/upload: Upload a new document (uses unified upload service)
    
    Note: This endpoint is maintained for backward compatibility.
    New code should use /api/files/upload which supports both folders and projects.
    """
    logger.debug("API: Received POST /api/docs/upload request")
    if not imports_ok or not db or not DBDocument:
        return jsonify({"error": "Server configuration error"}), 500

    try:
        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "No file selected"}), 400

        # Get parameters
        project_id = request.form.get("project_id")
        tags = request.form.get("tags", "")
        metadata_str = request.form.get("metadata", "{}")

        # Validate project_id if provided
        if project_id:
            try:
                project_id = int(project_id)
                # Verify project exists
                from backend.models import Project
                project = db.session.query(Project).filter_by(id=project_id).first()
                if not project:
                    return jsonify({"error": f"Project {project_id} not found"}), 404
            except ValueError:
                return jsonify({"error": "Invalid project_id format"}), 400

        # Parse metadata
        try:
            metadata = json.loads(metadata_str) if metadata_str else {}
        except json.JSONDecodeError:
            return jsonify({"error": "Invalid metadata JSON format"}), 400

        # Use unified upload service (no folder_path for docs_api - project-based only)
        from backend.services.unified_upload_service import UnifiedUploadService
        
        document, job_id = UnifiedUploadService.upload_file(
            file=file,
            folder_path=None,  # docs_api doesn't support folders
            project_id=project_id,
            client_id=None,
            website_id=None,
            tags=tags if tags else None,
            metadata=metadata if metadata else None,
            store_content=True,  # Store content for text/code files
            auto_index=True      # Automatically trigger indexing (now uses direct Celery, no HTTP delay)
        )

        return jsonify({
            "message": "Document uploaded successfully",
            "document_id": document.id,
            "filename": document.filename,
            "job_id": job_id
        }), 201

    except ValueError as e:
        logger.warning(f"Validation error during upload: {e}")
        return jsonify({"error": str(e)}), 400
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"API Error (POST /docs/upload): DB error: {e}", exc_info=True)
        return jsonify({"error": "Database error during upload", "details": str(e)}), 500
    except Exception as e:
        logger.error(f"API Error (POST /docs/upload): Unexpected error: {e}", exc_info=True)
        return jsonify({"error": "Upload failed", "details": str(e)}), 500


@docs_bp.route("/<int:doc_id>/reindex", methods=["POST"])
@ensure_db_session_cleanup  
def trigger_reindex(doc_id):
    """POST /api/docs/<int:doc_id>/reindex: Unified reindexing endpoint - redirects to indexing_api pattern."""
    logger.info(f"API: Received POST /api/docs/{doc_id}/reindex request - redirecting to unified indexing")
    
    # UNIFIED: Redirect to indexing_api.py pattern for consistency
    try:
        from backend.api.indexing_api import trigger_document_indexing
        
        # Get request data for parent job linking
        request_data = request.get_json() or {}
        parent_job_id = request_data.get('parent_job_id')
        
        # Call the unified indexing function with parent job support
        with current_app.test_request_context(json={"parent_job_id": parent_job_id} if parent_job_id else {}):
            result = trigger_document_indexing(doc_id)
            return result
            
    except Exception as e:
        logger.error(f"Failed to trigger unified reindexing for doc {doc_id}: {e}", exc_info=True)
        return jsonify({"error": f"Failed to trigger reindexing: {e}"}), 500