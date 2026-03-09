# backend/api/indexing_api.py
# Version Updated: Use Celery task for indexing instead of Flask-Executor

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

        dispatched = 0
        for doc_id in all_doc_ids:
            doc = db.session.get(DBDocument, doc_id)
            if not doc:
                continue
            update_document_status(doc.id, "INDEXING")
            index_document_task.apply_async((doc.id, job_id), queue='indexing')
            dispatched += 1

        logger.info(f"Bulk indexing: dispatched {dispatched} tasks under job {job_id}")

        return jsonify({
            "message": f"Bulk indexing started for {dispatched} documents.",
            "job_id": job_id,
            "total_documents": dispatched,
        }), 202

    except Exception as e:
        logger.error(f"API Bulk Indexing: Unexpected error: {e}", exc_info=True)
        return jsonify({"error": f"Failed to trigger bulk indexing: {e}"}), 500
