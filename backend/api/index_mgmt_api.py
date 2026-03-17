# backend/api/index_mgmt_api.py
# Extracted from meta_api for index management endpoints

import json
import logging
import os
import shutil

from flask import Blueprint, current_app, jsonify, request

try:
    from llama_index.core import Settings
    from llama_index.core.storage.docstore import SimpleDocumentStore
    from llama_index.core.storage.index_store import SimpleIndexStore

    llama_index_available = True
except Exception:
    Settings = SimpleDocumentStore = SimpleIndexStore = None
    llama_index_available = False

try:
    from backend.models import Document, db
    from backend.services.indexing_service import (
        get_or_create_index as initialize_llama_index_from_service,
    )
except Exception:
    db = Document = initialize_llama_index_from_service = None

index_mgmt_bp = Blueprint("index_mgmt_api", __name__, url_prefix="/api/meta")
logger = logging.getLogger(__name__)


@index_mgmt_bp.route("/index-info", methods=["GET"])
def get_index_info():
    """Return basic statistics about the current vector index."""
    logger.info("API: Received GET /api/meta/index-info request")

    if not llama_index_available:
        return jsonify({"error": "LlamaIndex unavailable"}), 503

    storage_dir = current_app.config.get("STORAGE_DIR")
    if not storage_dir:
        return jsonify({"error": "STORAGE_DIR not configured"}), 500

    docstore_path = os.path.join(storage_dir, "docstore.json")
    chunk_count = 0
    last_rebuilt = None

    if os.path.exists(docstore_path):
        try:
            with open(docstore_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                docs_obj = data.get("docs")
                if not docs_obj and isinstance(data.get("docstore"), dict):
                    docs_obj = data["docstore"].get("docs")
                if isinstance(docs_obj, dict):
                    chunk_count = len(docs_obj)
            last_rebuilt = os.path.getmtime(docstore_path)
        except Exception as e:
            logger.error(f"Index info: Error reading docstore.json: {e}", exc_info=True)

    # ==========================================================================
    # PROTECTED CODE - DO NOT MODIFY WITHOUT EXPLICIT PERMISSION
    # --------------------------------------------------------------------------
    # This embedding model detection logic supports RouterEmbeddingAdapter and
    # OllamaEmbedding. It displays the actual Ollama model name on SettingsPage.
    # Do NOT change this to show SimpleTextEmbedding or similar fallbacks.
    # Changes require direct permission from the project owner.
    #
    # Last verified working: 2026-01-31
    # ==========================================================================

    # Try to get embedding model from app config first, then Settings
    embed_model = None
    try:
        embed_model = current_app.config.get("LLAMA_INDEX_EMBED_MODEL")
    except Exception:
        pass

    if not embed_model:
        try:
            from llama_index.core import Settings as LISettings

            embed_model = getattr(LISettings, "embed_model", None)
        except Exception as e:
            logger.warning(f"Could not access Settings.embed_model: {e}")

    model_name = "Unknown"
    if embed_model is not None:
        # Try multiple ways to get the model name from OllamaEmbedding
        try:
            class_name = type(embed_model).__name__
            logger.debug(f"Attempting to extract model name from {class_name}")

            # OllamaEmbedding stores model_name as a direct attribute
            model_name = getattr(embed_model, "model_name", None)

            # If not found, try private attribute or instance variables
            if not model_name:
                # Check all attributes that might contain the model name
                attrs_to_check = ["_model_name", "model", "_model", "base_model"]
                for attr in attrs_to_check:
                    value = getattr(embed_model, attr, None)
                    if value and isinstance(value, str):
                        model_name = value
                        logger.debug(
                            f"Found model name in attribute '{attr}': {model_name}"
                        )
                        break

            # If still not found, inspect __dict__ for model-related keys
            if not model_name and hasattr(embed_model, "__dict__"):
                for key, value in embed_model.__dict__.items():
                    if (
                        "model" in key.lower()
                        and value
                        and isinstance(value, str)
                        and key != "__class__"
                        and not key.startswith("__")
                    ):
                        model_name = value
                        logger.debug(
                            f"Found model name in __dict__['{key}']: {model_name}"
                        )
                        break

            # If still not found and it's OllamaEmbedding or RouterEmbeddingAdapter, use VRAM-aware selection
            embedding_adapter_classes = ["OllamaEmbedding", "RouterEmbeddingAdapter"]
            if (
                not model_name
                or model_name
                in [
                    "OllamaEmbedding",
                    "SimpleTextEmbedding",
                    "FallbackEmbedding",
                    "RouterEmbeddingAdapter",
                ]
            ) and class_name in embedding_adapter_classes:
                try:
                    from backend.config import get_active_embedding_model

                    model_name = get_active_embedding_model()
                    logger.info(
                        f"Detected embedding model via VRAM-aware selection: {model_name}"
                    )
                except Exception as api_error:
                    logger.debug(f"Could not determine embedding model: {api_error}")

            # Final fallback
            if not model_name or model_name in [
                "OllamaEmbedding",
                "SimpleTextEmbedding",
                "FallbackEmbedding",
                "RouterEmbeddingAdapter",
            ]:
                if class_name in ["OllamaEmbedding", "RouterEmbeddingAdapter"]:
                    # Try to get from config as last resort
                    try:
                        from backend.config import get_active_embedding_model

                        model_name = get_active_embedding_model()
                    except Exception:
                        model_name = f"{class_name} (model name unavailable)"
                else:
                    model_name = class_name

            logger.info(f"Final embedding model name: {model_name} (from {class_name})")
        except Exception as e:
            logger.warning(f"Error extracting embedding model name: {e}", exc_info=True)
            model_name = type(embed_model).__name__ if embed_model else "Unknown"
    else:
        logger.warning("Embedding model not found in app config or Settings")

    return (
        jsonify(
            {
                "chunk_count": chunk_count,
                "embedding_model": model_name,
                "last_rebuilt": last_rebuilt,
            }
        ),
        200,
    )


@index_mgmt_bp.route("/reset-index", methods=["POST"])
def reset_index():
    logger.info("API: Received POST /api/meta/reset-index request")
    if not (
        llama_index_available
        and SimpleDocumentStore
        and SimpleIndexStore
        and initialize_llama_index_from_service
    ):
        logger.error("Core components unavailable for index reset.")
        return jsonify({"error": "Core components unavailable for index reset."}), 503

    storage_dir = current_app.config.get("STORAGE_DIR")
    if not storage_dir:
        logger.error("STORAGE_DIR not configured.")
        return jsonify({"error": "STORAGE_DIR not configured."}), 500

    abs_storage_dir = os.path.abspath(storage_dir)
    logger.warning(
        f"Reset Index: Attempting to clear only index files from: {abs_storage_dir}"
    )

    # Define index-related files to delete (preserve database, uploads, outputs, cache, etc.)
    index_files_to_delete = [
        "docstore.json",
        "index_store.json",
        "graph_store.json",
        "default__vector_store.json",
    ]

    deleted_files = []
    deleted_dirs = []

    try:
        # Delete index files in the main storage directory
        for index_file in index_files_to_delete:
            file_path = os.path.join(abs_storage_dir, index_file)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    deleted_files.append(index_file)
                    logger.info(f"Reset Index: Deleted {index_file}")
                except OSError as e:
                    logger.error(f"Reset Index: Failed to delete {index_file}: {e}")

        # Delete per-project index directories if they exist
        projects_dir = os.path.join(abs_storage_dir, "projects")
        if os.path.isdir(projects_dir):
            import shutil

            for project_subdir in os.listdir(projects_dir):
                project_path = os.path.join(projects_dir, project_subdir)
                if os.path.isdir(project_path):
                    # Delete index files in each project subdirectory
                    for index_file in index_files_to_delete:
                        proj_file_path = os.path.join(project_path, index_file)
                        if os.path.exists(proj_file_path):
                            try:
                                os.remove(proj_file_path)
                                deleted_files.append(
                                    f"projects/{project_subdir}/{index_file}"
                                )
                                logger.info(
                                    f"Reset Index: Deleted projects/{project_subdir}/{index_file}"
                                )
                            except OSError as e:
                                logger.error(
                                    f"Reset Index: Failed to delete {proj_file_path}: {e}"
                                )
                    # If project directory is now empty (only had index files), remove it
                    try:
                        if not os.listdir(project_path):
                            os.rmdir(project_path)
                            deleted_dirs.append(f"projects/{project_subdir}")
                            logger.info(
                                f"Reset Index: Removed empty project directory: projects/{project_subdir}"
                            )
                    except OSError:
                        pass  # Directory not empty or other error, leave it

        logger.info(
            f"Reset Index: Deleted {len(deleted_files)} index files and {len(deleted_dirs)} empty directories. "
            f"Preserved database, uploads, outputs, cache, context, and other data."
        )
        logger.info(
            "Reset Index: Calling indexing_service.get_or_create_index to set up new empty index..."
        )
        initialize_llama_index_from_service()
        from backend.services.indexing_service import index as new_global_index
        from backend.services.indexing_service import (
            storage_context as new_global_storage_context,
        )

        if new_global_index is None or new_global_storage_context is None:
            logger.error(
                "Reset Index: get_or_create_index failed to set up a new index/storage_context."
            )
            return (
                jsonify(
                    {"error": "Failed to re-initialize index after clearing storage."}
                ),
                500,
            )

        current_app.config["LLAMA_INDEX_INDEX"] = new_global_index
        current_app.config["LLAMA_INDEX_STORAGE_CONTEXT"] = new_global_storage_context

        # Clear both cache systems to prevent stale index references
        try:
            # Clear indexing_service cache
            current_app.config.pop("INDEX_CACHE", None)
            logger.info("Reset Index: Cleared INDEX_CACHE from Flask app config")

            # Clear index_manager cache
            from backend.utils.index_manager import clear_indexes

            clear_indexes()
            logger.info("Reset Index: Cleared _index_cache from index_manager")
        except Exception as cache_error:
            logger.warning(f"Reset Index: Cache clearing failed: {cache_error}")

        logger.info(
            "Reset Index: LlamaIndex components re-initialized in app.config with an empty index."
        )

        if db and Document:
            try:
                logger.info(
                    "Reset Index: Resetting document statuses in database to 'INDEXING'..."
                )
                updated_count = db.session.query(Document).update(
                    {
                        "index_status": "INDEXING",
                        "indexed_at": None,
                        "error_message": None,
                    }
                )
                db.session.commit()
                logger.info(
                    f"Reset Index: Updated status for {updated_count} documents."
                )
            except Exception as e:
                db.session.rollback()
                logger.error(
                    f"Reset Index: Failed to update document statuses: {e}",
                    exc_info=True,
                )
        else:
            logger.warning(
                "Reset Index: DB or Document model not available, skipping status reset."
            )
        return (
            jsonify(
                {
                    "message": "Index storage cleared and re-initialized successfully. Document statuses reset."
                }
            ),
            200,
        )
    except Exception as e:
        logger.error(f"Error during index reset operation: {e}", exc_info=True)
        return (
            jsonify({"error": f"An error occurred during index reset: {str(e)}"}),
            500,
        )


@index_mgmt_bp.route("/purge-index", methods=["POST"])
def purge_index():
    """Purge selected portions of the index based on provided options."""
    logger.info("API: Received POST /api/meta/purge-index request")
    options = request.get_json(silent=True) or {}
    logger.info(f"Purge Index options received: {options}")
    return (
        jsonify(
            {"message": "Purge request acknowledged.", "received_options": options}
        ),
        200,
    )


@index_mgmt_bp.route("/rebuild-index", methods=["POST"])
def rebuild_index():
    logger.info("API: Received POST /api/meta/rebuild-index request")
    try:
        from backend.services.indexing_service import add_file_to_index
    except ImportError as e:
        logger.critical(f"Failed to import add_file_to_index: {e}", exc_info=True)
        return jsonify({"error": "Indexing service function not found."}), 500

    if not (llama_index_available and db and Document):
        return jsonify({"error": "Core components unavailable for index rebuild."}), 503

    logger.info("Rebuild Index: Performing a full index reset first...")
    reset_response_obj = reset_index()
    if reset_response_obj.status_code != 200:
        reset_data = (
            json.loads(reset_response_obj.get_data(as_text=True))
            if reset_response_obj.is_json
            else {"error": "Unknown error during reset"}
        )
        logger.error(
            f"Rebuild Index: Failed reset phase. Error: {reset_data.get('error')}"
        )
        return (
            jsonify(
                {"error": f"Rebuild failed during reset: {reset_data.get('error')}"}
            ),
            reset_response_obj.status_code,
        )

    logger.info("Rebuild Index: Initial reset successful. Re-indexing documents.")
    if not current_app.config.get("LLAMA_INDEX_INDEX"):
        return jsonify({"error": "LlamaIndex index unavailable after reset."}), 500

    try:
        docs_to_reindex = Document.query.filter(
            Document.index_status != "INDEXING"
        ).all()
        logger.info(
            f"Rebuild Index: Found {len(docs_to_reindex)} documents to re-index."
        )
        indexed_count, error_count = 0, 0
        upload_folder_abs = current_app.config.get("UPLOAD_FOLDER")
        if not upload_folder_abs:
            return jsonify({"error": "Upload folder not set."}), 500

        for db_doc in docs_to_reindex:
            full_doc_path = (
                db_doc.path
                if os.path.isabs(db_doc.path)
                else os.path.join(upload_folder_abs, db_doc.path)
            )
            if not db_doc.path or not os.path.exists(full_doc_path):
                logger.warning(
                    f"Rebuild Index: Skipping doc ID {db_doc.id} ('{db_doc.filename}'), path invalid: {full_doc_path}"
                )
                db_doc.index_status, db_doc.error_message = (
                    "ERROR",
                    f"File not found: {full_doc_path}",
                )
                error_count += 1
                continue
            db_doc.index_status = "PENDING"
            db.session.commit()
            if add_file_to_index(full_doc_path, db_doc, db_doc.project_id):
                indexed_count += 1
            else:
                error_count += 1
        db.session.commit()
        msg = f"Index rebuild attempt: {len(docs_to_reindex)}. Indexed: {indexed_count}, Errors/Skipped: {error_count}."
        logger.info(f"Rebuild Index: {msg}")
        return jsonify({"message": msg}), 200
    except Exception as e:
        db.session.rollback()
        logger.error(
            f"Rebuild Index: Error during document processing: {e}", exc_info=True
        )
        return jsonify({"error": f"Error during indexing: {e}"}), 500


@index_mgmt_bp.route("/optimize-index", methods=["POST"])
def optimize_index():
    """Remove orphaned vector entries whose source documents no longer exist in DB."""
    logger.info("API: Received POST /api/meta/optimize-index request")

    index_instance = current_app.config.get("LLAMA_INDEX_INDEX")
    storage_dir = current_app.config.get("STORAGE_DIR")

    # Fallback: try to load the index on-demand if not in app config
    if not index_instance or not hasattr(index_instance, "storage_context"):
        logger.warning(
            "Optimize Index: LLAMA_INDEX_INDEX not in app config, attempting on-demand load"
        )
        if initialize_llama_index_from_service:
            try:
                result = initialize_llama_index_from_service()
                if isinstance(result, tuple):
                    index_instance = result[0]
                else:
                    index_instance = result
                if index_instance and hasattr(index_instance, "storage_context"):
                    current_app.config["LLAMA_INDEX_INDEX"] = index_instance
                    logger.info("Optimize Index: Successfully loaded index on-demand")
            except Exception as e:
                logger.error(f"Optimize Index: On-demand index load failed: {e}")

    if not index_instance or not hasattr(index_instance, "storage_context"):
        return (
            jsonify(
                {
                    "error": "No index available. Index some documents first, then try again."
                }
            ),
            503,
        )

    if not (db and Document):
        return jsonify({"error": "Database not available"}), 503

    try:
        docstore = index_instance.storage_context.docstore

        # Collect all unique ref_doc_ids from the docstore
        all_ref_doc_ids = set()
        for node_id, node in docstore.docs.items():
            ref_doc_id = getattr(node, "ref_doc_id", None)
            if not ref_doc_id:
                metadata = getattr(node, "metadata", {}) or {}
                ref_doc_id = metadata.get("document_id")
            if ref_doc_id:
                all_ref_doc_ids.add(ref_doc_id)

        logger.info(
            f"Optimize Index: Found {len(all_ref_doc_ids)} unique ref_doc_ids in docstore"
        )

        # Check which ones still exist in DB
        orphaned = []
        for ref_id in all_ref_doc_ids:
            try:
                doc = db.session.get(Document, int(ref_id))
                if not doc:
                    orphaned.append(ref_id)
            except (ValueError, TypeError):
                orphaned.append(ref_id)

        logger.info(f"Optimize Index: Found {len(orphaned)} orphaned entries to remove")

        # Remove orphaned entries
        removed = 0
        for ref_id in orphaned:
            try:
                index_instance.delete_ref_doc(ref_id, delete_from_docstore=True)
                removed += 1
                logger.info(f"Optimize Index: Removed orphaned ref_doc_id={ref_id}")
            except Exception as e:
                logger.warning(
                    f"Optimize Index: Failed to remove ref_doc_id={ref_id}: {e}"
                )

        # Persist changes
        if removed > 0:
            try:
                index_instance.storage_context.persist(persist_dir=storage_dir)
                logger.info(
                    f"Optimize Index: Persisted index after removing {removed} orphans"
                )
            except Exception as e:
                logger.warning(f"Optimize Index: Failed to persist after cleanup: {e}")

        msg = f"Optimization complete. Removed {removed} orphaned entries."
        logger.info(f"Optimize Index: {msg}")
        return (
            jsonify(
                {
                    "message": msg,
                    "orphaned_removed": removed,
                    "total_checked": len(all_ref_doc_ids),
                    "remaining": len(all_ref_doc_ids) - removed,
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Optimize Index: Error during optimization: {e}", exc_info=True)
        return jsonify({"error": f"Optimization failed: {e}"}), 500
