# backend/api/indexing_api.py
# Version Updated: Use Celery task for indexing instead of Flask-Executor

import json
import logging
import os

from flask import Blueprint, current_app, jsonify, request

from backend.utils.unified_progress_system import get_unified_progress, ProcessType
from backend.utils.db_utils import ensure_db_session_cleanup

# --- Corrected Import ---
try:
    # Import the actual function name from services.indexing_service
    # Also import the status updater function
    # Import the Document model for fetching
    from backend.models import Document as DBDocument, Folder
    from backend.models import db
    from backend.services.indexing_service import (add_file_to_index,
                                                   update_document_status)
except ImportError as e:
    logging.getLogger(__name__).critical(
        f"CRITICAL Failed to import dependencies for indexing_api: {e}", exc_info=True
    )
    add_file_to_index = None
    update_document_status = None  # Add fallback
    db = None
    DBDocument = None
    Folder = None
# --- End Corrected Import ---

indexing_bp = Blueprint("indexing", __name__, url_prefix="/api/index")


# --- Pause/Resume Indexing Support (defined early for use in triggers) ---

def _get_indexing_paused_setting():
    """Helper to read the pause flag from SystemSetting."""
    try:
        from backend.models import SystemSetting, db
        row = db.session.get(SystemSetting, "indexing_paused")
        if row:
            val = str(row.value or "").strip().lower()
            return val in ("true", "1", "yes", "on")
        return False
    except Exception as e:
        logging.getLogger(__name__).warning(f"Could not read indexing_paused setting: {e}")
        return False

def _set_indexing_paused(paused: bool):
    """Helper to write the pause flag."""
    try:
        from backend.models import SystemSetting, db
        key = "indexing_paused"
        row = db.session.get(SystemSetting, key)
        if not row:
            row = SystemSetting(key=key)
            db.session.add(row)
        row.value = "true" if paused else "false"
        db.session.commit()
        logging.getLogger(__name__).info(f"Set indexing_paused={paused}")
        return True
    except Exception as e:
        logging.getLogger(__name__).error(f"Failed to set indexing_paused: {e}")
        try:
            db.session.rollback()
        except:
            pass
        return False


@indexing_bp.route("/<int:document_id>", methods=["POST"])
@ensure_db_session_cleanup
def trigger_document_indexing(document_id):
    """
    Triggers the indexing process for a specific document ID.
    Finds the document path and calls the indexing service function,
    passing the document object itself for metadata retrieval.
    Updates document status based on the indexing result.
    """
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info(f"API: Received POST /api/index/{document_id} request")

    if add_file_to_index is None or update_document_status is None:
        logger.error("API Indexing: Indexing service functions not available.")
        return (
            jsonify({"error": "Indexing service unavailable due to import error."}),
            500,
        )

    if db is None or DBDocument is None:
        logger.error("API Indexing: DB or Document model not available.")
        return jsonify({"error": "Database unavailable for indexing."}), 500

    document = None  # Initialize document variable
    try:
        # Get request data for parent job linking
        request_data = request.get_json() or {}
        parent_job_id = request_data.get('parent_job_id')
        
        # 1. Find the document in the database
        document = db.session.get(DBDocument, document_id)
        if not document:
            logger.warning(f"API Indexing: Document with ID {document_id} not found.")
            return jsonify({"error": f"Document with ID {document_id} not found."}), 404

        if _get_indexing_paused_setting():
            logger.info(f"Indexing paused; refusing trigger for doc {document_id}")
            return jsonify({"error": "Indexing is paused. Use the pause toggle in Settings to resume.", "paused": True}), 423

        file_path = document.path
        filename = document.filename or os.path.basename(file_path)  # Fallback filename

        # Construct the full path robustly
        upload_folder_abs = current_app.config.get("UPLOAD_FOLDER")
        if not upload_folder_abs:
            logger.error("API Indexing: UPLOAD_FOLDER not configured in Flask app.")
            # Update status before returning error
            update_document_status(
                document.id, "ERROR", "Server config missing: UPLOAD_FOLDER"
            )
            return jsonify({"error": "Upload folder configuration missing."}), 500

        full_file_path = (
            file_path
            if os.path.isabs(file_path)
            else os.path.join(upload_folder_abs, file_path)
        )

        if not os.path.exists(full_file_path):
            logger.error(
                f"API Indexing: File for document {document_id} not found at path: {full_file_path}"
            )
            # Update status using the helper function
            update_document_status(
                document.id, "ERROR", f"File not found at path: {full_file_path}"
            )
            return (
                jsonify(
                    {
                        "error": f"File path not found for document {document_id}: {full_file_path}"
                    }
                ),
                400,
            )

        # 2. Update status to 'INDEXING' before starting
        update_document_status(document.id, "INDEXING")
        logger.info(
            f"API Indexing: Queuing background task for doc {document_id} ({filename})"
        )

        job_id = None
        progress_system = get_unified_progress()
        
        if parent_job_id:
            # Try to continue from upload job if it exists
            try:
                existing_process = progress_system.get_process(parent_job_id)
                if existing_process and existing_process.status.value != 'complete':
                    job_id = parent_job_id
                    progress_system.update_process(job_id, 50, f"Starting indexing for {filename}")
                    logger.info(f"Continuing upload job {parent_job_id} for indexing document {document_id}")
                else:
                    parent_job_id = None  # Create new job if parent is complete
            except Exception as e:
                logger.warning(f"Could not continue parent job {parent_job_id}: {e}")
                parent_job_id = None
        
        if not job_id:
            # Create new indexing job
            job_id = progress_system.create_process(
                ProcessType.INDEXING,
                f"Indexing document {document_id}: {filename}"
            )
            progress_system.update_process(job_id, 0, "Starting indexing process")
        
        # Store the job_id in the document for future reference
        document.indexing_job_id = job_id
        db.session.commit()

        # Use Celery task instead of Flask-Executor
        try:
            from backend.celery_tasks_isolated import index_document_task
            # Submit to Celery with explicit queue specification
            task = index_document_task.apply_async((document.id, job_id), queue='indexing')
            logger.info(f"Submitted indexing task to Celery queue 'indexing': {task.id}")
        except ImportError as e:
            logger.error(f"Failed to import Celery task: {e}")
            update_document_status(document.id, "ERROR", "Indexing service unavailable")
            return jsonify({"error": "Indexing service unavailable"}), 500

        return (
            jsonify(
                {
                    "message": "Indexing started.",
                    "job_id": job_id,
                    "document_id": document.id,
                }
            ),
            202,
        )

    except Exception as e:
        # Catch any unexpected errors during the process
        logger.error(
            f"API Indexing: Unexpected error triggering indexing for doc {document_id}: {e}",
            exc_info=True,
        )
        # Attempt to mark as ERROR if we have the document object
        if document and document.id:
            try:
                # Use the helper function to set error status
                update_document_status(
                    document.id, "ERROR", f"Unexpected error in API: {e}"
                )
            except Exception as db_err:
                # Log error during status update but proceed with main error response
                logger.error(
                    f"API Indexing: Failed to update status to ERROR for {document_id} after exception: {db_err}"
                )
        return (
            jsonify(
                {"error": f"Failed to trigger indexing for document {document_id}: {e}"}
            ),
            500,
        )


def _get_all_document_ids_recursive(folder_id):
    """Get all document IDs from a folder and all its subfolders recursively."""
    doc_ids = [d.id for d in DBDocument.query.filter_by(folder_id=folder_id).all()]
    subfolders = Folder.query.filter_by(parent_id=folder_id).all()
    for sub in subfolders:
        doc_ids.extend(_get_all_document_ids_recursive(sub.id))
    return doc_ids


@indexing_bp.route("/bulk", methods=["POST"])
@ensure_db_session_cleanup
def trigger_bulk_indexing():
    """Index multiple folders (recursively) and/or individual files."""
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received POST /api/index/bulk request")

    if add_file_to_index is None or update_document_status is None:
        return jsonify({"error": "Indexing service unavailable due to import error."}), 500

    if db is None or DBDocument is None or Folder is None:
        return jsonify({"error": "Database unavailable for indexing."}), 500

    try:
        data = request.get_json() or {}
        folder_ids = data.get("folder_ids", [])
        document_ids = data.get("document_ids", [])

        if not folder_ids and not document_ids:
            return jsonify({"error": "No folder_ids or document_ids provided."}), 400

        if _get_indexing_paused_setting():
            logger.info("Indexing paused; refusing bulk indexing request")
            return jsonify({"error": "Indexing is paused. Resume in Settings before bulk operations.", "paused": True}), 423

        # Collect all document IDs from folders recursively
        all_doc_ids = set(document_ids)
        for fid in folder_ids:
            folder = db.session.get(Folder, fid)
            if folder:
                all_doc_ids.update(_get_all_document_ids_recursive(fid))
            else:
                logger.warning(f"Bulk indexing: Folder {fid} not found, skipping.")

        if not all_doc_ids:
            return jsonify({"error": "No documents found in the specified folders/documents."}), 404

        # Create a parent progress job
        progress_system = get_unified_progress()
        job_id = progress_system.create_process(
            ProcessType.INDEXING,
            f"Bulk indexing {len(all_doc_ids)} documents"
        )
        progress_system.update_process(job_id, 0, "Starting bulk indexing")

        # Dispatch Celery task for each document
        try:
            from backend.celery_tasks_isolated import index_document_task
        except ImportError as e:
            logger.error(f"Failed to import Celery task: {e}")
            return jsonify({"error": "Indexing service unavailable"}), 500

        import hashlib

        dispatched = 0
        skipped = 0
        for doc_id in all_doc_ids:
            doc = db.session.get(DBDocument, doc_id)
            if not doc:
                continue

            # Check content hash for incremental re-indexing
            if doc.index_status == "INDEXED" and doc.file_metadata:
                try:
                    existing_meta = json.loads(doc.file_metadata)
                    stored_hash = existing_meta.get("content_hash")
                    if stored_hash:
                        upload_dir = os.environ.get(
                            'GUAARDVARK_UPLOAD_DIR',
                            os.path.join(os.environ.get('GUAARDVARK_STORAGE_DIR', 'data'), 'uploads')
                        )
                        full_path = os.path.join(upload_dir, doc.path)
                        if os.path.exists(full_path):
                            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                                current_hash = hashlib.sha256(f.read().encode('utf-8', errors='replace')).hexdigest()
                            if current_hash == stored_hash:
                                skipped += 1
                                continue  # File unchanged, skip re-indexing
                except Exception as e:
                    logger.debug(f"Hash check failed for doc {doc_id}, will re-index: {e}")

            update_document_status(doc.id, "INDEXING")
            index_document_task.apply_async((doc.id, job_id), queue='indexing')
            dispatched += 1

        logger.info(f"Bulk indexing: dispatched {dispatched} tasks, skipped {skipped} unchanged, under job {job_id}")

        # Auto-trigger repository analysis for folders with enough code files
        from backend.utils.code_chunker import CODE_LANGUAGE_MAP

        min_code_files = int(os.environ.get("GUAARDVARK_REPO_ANALYSIS_MIN_FILES", "5"))

        for fid in folder_ids:
            try:
                folder = db.session.get(Folder, fid)
                if not folder:
                    continue
                all_docs = list(folder.documents.all())
                code_count = sum(
                    1 for d in all_docs
                    if os.path.splitext(d.filename)[1].lower() in CODE_LANGUAGE_MAP
                )
                if code_count >= min_code_files:
                    logger.info(f"Folder {folder.name} has {code_count} code files, triggering repo analysis")
                    from backend.services.repository_analysis_service import RepositoryAnalysisService
                    RepositoryAnalysisService.analyze_repository(fid)
            except Exception as e:
                logger.warning(f"Repository analysis failed for folder {fid}: {e}")

        return jsonify({
            "message": f"Bulk indexing started: {dispatched} dispatched, {skipped} unchanged (skipped).",
            "job_id": job_id,
            "total_documents": dispatched,
            "skipped": skipped,
        }), 202

    except Exception as e:
        logger.error(f"API Bulk Indexing: Unexpected error: {e}", exc_info=True)
        return jsonify({"error": f"Failed to trigger bulk indexing: {e}"}), 500


# (pause helpers defined at top of file)

@indexing_bp.route("/build-corpus-summaries", methods=["POST"])
def build_corpus_summaries():
    """Kick off a RAPTOR build.

    Queued rather than run inline: it is one LLM call per cluster per level over
    the whole corpus, which is minutes of sustained GPU work and contends with
    image and video generation. Returns the task id so the caller can poll.
    """
    try:
        payload = request.get_json(silent=True) or {}
        project_id = payload.get("project_id")
        max_levels = int(payload.get("max_levels") or 2)
        replace = bool(payload.get("replace", True))

        from backend.celery_app import celery
        async_result = celery.send_task(
            "raptor.build_tree",
            kwargs={"project_id": project_id, "max_levels": max_levels, "replace": replace},
            queue="indexing",
        )
        logger.info("Queued RAPTOR build task %s (levels=%s replace=%s)",
                    async_result.id, max_levels, replace)
        return jsonify({
            "message": "Corpus summary build queued. It runs in the background and "
                       "competes with generation work for the GPU.",
            "task_id": async_result.id,
        }), 202
    except Exception as e:
        logger.error("API build-corpus-summaries: %s", e, exc_info=True)
        return jsonify({"error": f"Could not queue the summary build: {e}"}), 500


@indexing_bp.route("/resume-pending", methods=["POST"])
@ensure_db_session_cleanup
def resume_pending_indexing():
    """Resume/re-queue all documents that are PENDING or ERROR for indexing.
    Respects the pause flag (will error if still paused).
    Creates a bulk job and dispatches tasks.
    """
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    if _get_indexing_paused_setting():
        return jsonify({"error": "Indexing is still paused. Resume first.", "paused": True}), 423

    if add_file_to_index is None or update_document_status is None or db is None or DBDocument is None:
        return jsonify({"error": "Indexing service unavailable."}), 500

    try:
        # Find candidates
        candidates = DBDocument.query.filter(
            DBDocument.index_status.in_(["PENDING", "ERROR"])
        ).all()

        if not candidates:
            return jsonify({"message": "No pending or errored documents to resume.", "dispatched": 0}), 200

        doc_ids = [d.id for d in candidates]

        # Reuse bulk logic style: create parent job, dispatch
        progress_system = get_unified_progress()
        job_id = progress_system.create_process(
            ProcessType.INDEXING,
            f"Resume pending indexing for {len(doc_ids)} documents"
        )
        progress_system.update_process(job_id, 0, "Resuming pending indexing")

        try:
            from backend.celery_tasks_isolated import index_document_task
        except ImportError as e:
            logger.error(f"Failed to import Celery task: {e}")
            return jsonify({"error": "Indexing service unavailable"}), 500

        dispatched = 0
        for doc_id in doc_ids:
            doc = db.session.get(DBDocument, doc_id)
            if not doc:
                continue
            update_document_status(doc.id, "INDEXING")
            index_document_task.apply_async((doc.id, job_id), queue='indexing')
            dispatched += 1

        logger.info(f"Resume pending: dispatched {dispatched} indexing tasks under job {job_id}")

        return jsonify({
            "message": f"Resumed indexing for {dispatched} pending/error documents.",
            "job_id": job_id,
            "dispatched": dispatched,
            "total_candidates": len(doc_ids),
        }), 202

    except Exception as e:
        logger.error(f"API resume-pending: {e}", exc_info=True)
        return jsonify({"error": f"Failed to resume pending: {e}"}), 500


# --- Pause / Resume controls for indexing (to relieve system load during heavy embedding) ---

@indexing_bp.route("/pause", methods=["POST"])
@ensure_db_session_cleanup
def pause_indexing():
    """Pause future indexing work (new document indexing will be skipped or rejected)."""
    success = _set_indexing_paused(True)
    return jsonify({"success": success, "paused": True, "message": "Indexing paused." if success else "Failed."})

@indexing_bp.route("/resume", methods=["POST"])
@ensure_db_session_cleanup
def resume_indexing():
    """Resume indexing."""
    success = _set_indexing_paused(False)
    return jsonify({"success": success, "paused": False, "message": "Indexing resumed." if success else "Failed."})

@indexing_bp.route("/paused", methods=["GET"])
@ensure_db_session_cleanup
def get_indexing_paused():
    """Current pause state."""
    paused = _get_indexing_paused_setting()
    return jsonify({"paused": paused})
