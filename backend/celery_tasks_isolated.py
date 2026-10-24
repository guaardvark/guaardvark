#!/usr/bin/env python3
"""
Truly isolated Celery tasks that don't import any Flask-dependent modules
This prevents the worker from hanging during startup
"""

import os
import logging
import json
import time
import datetime
import hashlib
from pathlib import Path
from celery import current_task
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from backend.utils import file_kinds
from backend.services.code_retrieval import STORED_ON_DEMAND

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Storage directory for filesystem paths (uploads, outputs, etc.)
STORAGE_DIR = os.environ.get('GUAARDVARK_STORAGE_DIR', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data'))

def _get_database_url():
    """Get DATABASE_URL from environment (set by start_postgres.sh in .env)."""
    url = os.environ.get('DATABASE_URL')
    if url:
        return url
    return "postgresql://guaardvark:guaardvark@localhost:5432/guaardvark"

_engine = None
_SessionFactory = None

def get_db_session():
    """Get a SQLAlchemy session for database operations without Flask."""
    global _engine, _SessionFactory
    if _engine is None:
        _engine = create_engine(_get_database_url(), pool_pre_ping=True)
        _SessionFactory = sessionmaker(bind=_engine)
    return _SessionFactory()

def get_document_by_id(document_id):
    """Get document from database without Flask"""
    session = get_db_session()
    try:
        result = session.execute(text("""
            SELECT id, filename, path, project_id, index_status, uploaded_at
            FROM documents
            WHERE id = :document_id
        """), {"document_id": document_id})
        row = result.fetchone()
        if row:
            return {
                'id': row[0],
                'filename': row[1],
                'file_path': row[2],  # Keep the key name for compatibility
                'project_id': row[3],
                'index_status': row[4],
                'uploaded_at': row[5]
            }
        return None
    finally:
        session.close()

def update_document_status(document_id, status, error_message=None):
    """Update document status without Flask"""
    session = get_db_session()
    try:
        if error_message:
            session.execute(text("""
                UPDATE documents
                SET index_status = :status, indexed_at = :indexed_at, error_message = :error_message
                WHERE id = :document_id
            """), {"status": status, "indexed_at": datetime.datetime.now().isoformat(),
                   "error_message": error_message, "document_id": document_id})
        else:
            session.execute(text("""
                UPDATE documents
                SET index_status = :status, indexed_at = :indexed_at
                WHERE id = :document_id
            """), {"status": status, "indexed_at": datetime.datetime.now().isoformat(),
                   "document_id": document_id})
        session.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to update document status: {e}")
        session.rollback()
        return False
    finally:
        session.close()

# Returned by enhanced_code_aware_indexing when the document was left PENDING
# for a later attempt: not a success, and not the document's fault.
DEFERRED = "deferred"
# Audio and video: kept and shown, with nothing to search.
STORED_ONLY = "stored_only"
# Compiled or binary: the row is already marked ERROR with the reason.
UNREADABLE = "unreadable"


def _file_sha256(file_path):
    digest = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _index_without_text(file_path, document, document_id, update_progress):
    """Media, build output and binary files have no text to store. Images still go
    to the vector index, whose image reader describes them; audio and video are
    stored only; compiled and binary files are refused with the reason."""
    kind = file_kinds.kind_of(file_path)
    if kind not in (file_kinds.MEDIA, file_kinds.BUILD_ARTIFACT):
        kind = 'binary'
    metadata = {
        'file_type': kind,
        'language': 'unknown',
        'content_hash': _file_sha256(file_path),
        'processed_at': datetime.datetime.now().isoformat(),
        'indexing_method': 'no_text',
    }
    session = get_db_session()
    try:
        session.execute(text("""
            UPDATE documents
            SET content = NULL, is_code_file = FALSE, file_metadata = :file_metadata, error_message = NULL
            WHERE id = :document_id
        """), {"file_metadata": json.dumps(metadata), "document_id": document_id})
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    if file_kinds.is_image(file_path):
        update_progress(90, f'Describing image for search: {document["filename"]}')
        from backend.services.indexing_service import add_file_to_index_by_id, vector_store_fallback_reason
        if add_file_to_index_by_id(file_path, document_id):
            return True
        reason = vector_store_fallback_reason()
        if reason:
            update_document_status(document_id, "PENDING", f"Waiting for the vector store: {reason}")
            return DEFERRED
        return False
    if kind == file_kinds.MEDIA:
        logger.info(f"Stored media document {document_id} ('{document['filename']}'): nothing to read as text")
        return STORED_ONLY
    update_document_status(document_id, "ERROR", "Compiled or binary file: there is no text to read")
    return UNREADABLE


def enhanced_code_aware_indexing(file_path, document, document_id, update_progress):
    """Enhanced indexing that leverages Guaardvark's existing CodeChunker system"""
    try:
        logger.info(f"Starting enhanced code-aware indexing for document {document_id}")
        update_progress(75, f'Analyzing file type and content for {document["filename"]}')

        # Read file content
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return False

        if file_kinds.never_read_as_text(file_path):
            return _index_without_text(file_path, document, document_id, update_progress)

        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        # Postgres text columns reject NUL (0x00) bytes ("A string literal cannot
        # contain NUL characters"), which silently failed the whole index for files
        # carrying stray nulls. Strip them at ingestion so the document still indexes.
        if "\x00" in content:
            null_count = content.count("\x00")
            content = content.replace("\x00", "")
            logger.warning(f"Stripped {null_count} NUL byte(s) from document {document_id} before indexing")

        # Determine file type and language
        file_ext = os.path.splitext(file_path)[1].lower()
        is_code_file = file_kinds.is_code(file_path)

        update_progress(80, f'Processing content using CodeChunker strategy for {document["filename"]}')

        # Update database with enhanced content and metadata
        session = get_db_session()
        try:
            # Create rich metadata that aligns with Guaardvark's CodeChunker system
            metadata = {
                'file_type': 'code' if is_code_file else 'text',
                'language': file_ext[1:] if file_ext else 'unknown',
                'line_count': len(content.splitlines()),
                'char_count': len(content),
                'content_hash': hashlib.sha256(content.encode('utf-8', errors='replace')).hexdigest(),
                'processing_strategy': 'code_preserving' if is_code_file else 'standard',
                'chunking_method': 'complete_file' if len(content) <= 50000 else 'logical_sections',
                'processed_at': datetime.datetime.now().isoformat(),
                'indexing_method': 'enhanced_code_aware',
                'preservation_mode': 'complete_file' if is_code_file and len(content) <= 50000 else 'semantic_chunks'
            }

            # Store content and metadata. The row stays INDEXING: index_document_task
            # marks it INDEXED once this returns success, i.e. once it is searchable.
            if is_code_file:
                session.execute(text("""
                    UPDATE documents
                    SET content = :content, is_code_file = :is_code_file, index_status = :index_status, indexed_at = :indexed_at,
                        file_metadata = :file_metadata, error_message = NULL
                    WHERE id = :document_id
                """), {"content": content, "is_code_file": True, "index_status": 'INDEXING',
                       "indexed_at": None,
                       "file_metadata": json.dumps(metadata), "document_id": document_id})
                logger.info(f"Enhanced code file indexing completed for document {document_id}: {metadata['line_count']} lines, language: {metadata['language']}")
            else:
                # For non-code files, still store content but with different metadata
                session.execute(text("""
                    UPDATE documents
                    SET content = :content, is_code_file = :is_code_file, index_status = :index_status, indexed_at = :indexed_at,
                        file_metadata = :file_metadata, error_message = NULL
                    WHERE id = :document_id
                """), {"content": content, "is_code_file": False, "index_status": 'INDEXING',
                       "indexed_at": None,
                       "file_metadata": json.dumps(metadata), "document_id": document_id})
                logger.info(f"Enhanced text file indexing completed for document {document_id}: {metadata['line_count']} lines")

            session.commit()

            update_progress(90, f'Adding to vector index for semantic search: {document["filename"]}')

            # BUG FIX #3: Move import outside or handle circular import better
            # Actually index into LlamaIndex vector store
            # vector_outcome: indexed | empty | failed | skipped
            vector_outcome = "skipped"
            try:
                # Import here to avoid circular dependencies at module level
                from backend.services.indexing_service import add_file_to_index_by_id
                logger.info(f"Adding document {document_id} to vector index for RAG search")

                # Index the FILE, not the text read above. Reading a PDF or DOCX as
                # UTF-8 yields mojibake; the file path lets Docling parse it properly,
                # lets markdown be sectioned, and purges the previous vectors so a
                # re-index replaces rather than duplicates.
                #
                # An empty document is still distinguished from a failure, because the
                # caller marks failures ERROR (retryable) and a file with nothing in it
                # is not going to index better next time. The pipeline returns a plain
                # boolean, so the emptiness check happens here against the content
                # already read above.
                if not (content or "").strip():
                    vector_outcome = "empty"
                    logger.info(f"Document {document_id} stored but had no content — nothing to vector-index")
                elif (added := add_file_to_index_by_id(file_path, document_id)) == STORED_ON_DEMAND:
                    vector_outcome = "on_demand"
                    logger.info(f"Document {document_id} is code: stored for reading in place, not embedded")
                elif added:
                    vector_outcome = "indexed"
                    logger.info(f"Successfully added document {document_id} to vector index")
                    update_progress(95, f'Vector indexing complete for {document["filename"]}')
                else:
                    from backend.services.indexing_service import vector_store_fallback_reason
                    if vector_store_fallback_reason():
                        vector_outcome = "deferred"
                    else:
                        vector_outcome = "failed"
                        logger.warning(f"Vector indexing returned a failure for document {document_id}")

            except ImportError as import_error:
                vector_outcome = "skipped"
                logger.warning(f"Could not import indexing_service (may be expected in isolated worker): {import_error}")
            except Exception as vector_error:
                vector_outcome = "failed"
                logger.error(f"Vector indexing failed for document {document_id}: {vector_error}")

            # Honest outcome. A document that is stored but NOT searchable because the
            # vector index genuinely FAILED must not be reported as a clean success —
            # return False so index_document_task marks it ERROR (retryable). Empty
            # content and the expected isolated-worker skip are NOT failures: the row
            # is stored, there is just nothing (more) to search.
            if vector_outcome == "failed":
                logger.error(f"Document {document_id}: content stored but vector indexing FAILED — not searchable")
                return False
            if vector_outcome == "on_demand":
                return STORED_ONLY
            if vector_outcome == "deferred":
                # Nothing is wrong with the document: the persisted vector store is
                # not in use in this worker (embedding backend unreachable at start).
                # PENDING puts it back in the queue the resume task works from.
                from backend.services.indexing_service import vector_store_fallback_reason
                reason = vector_store_fallback_reason() or "vector store unavailable"
                logger.warning(f"Document {document_id}: left PENDING, vector store not in use ({reason})")
                update_document_status(document_id, "PENDING", f"Waiting for the vector store: {reason}")
                return DEFERRED

            logger.info(
                f"Indexed document {document_id} ('{document['filename']}', "
                f"{metadata['language']}, {len(content)} chars) — vector: {vector_outcome}"
            )
            return True

        except Exception:
            logger.warning("Rolling back session on error (non-fatal for audit)", exc_info=True)  # noqa: BLE001 - cleanup must not leave inconsistent state
            session.rollback()
            raise
        finally:
            session.close()

    except Exception as e:
        logger.error(f"Error in enhanced code-aware indexing for document {document_id}: {e}")
        # Update error status
        try:
            update_document_status(document_id, "ERROR", f"Enhanced indexing failed: {str(e)}")
        except Exception:
            logger.warning(f"Failed to mark document {document_id} ERROR (non-fatal)", exc_info=True)  # noqa: BLE001 - status update must not mask original error
        return False


def simple_index_document(file_path, document_id):
    """Record a code file's metadata without LlamaIndex. It adds nothing to the
    vector index, so it does not mark the document INDEXED; True means the
    metadata was written, not that the document is searchable."""
    try:
        logger.info(f"Starting enhanced indexing for document {document_id}")

        # Check if file exists
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return False

        # Get file size
        file_size = os.path.getsize(file_path)
        logger.info(f"File size: {file_size} bytes")

        # Read and process the file content for code files
        try:
            # Determine if this is a code file based on extension
            file_ext = os.path.splitext(file_path)[1].lower()
            is_code_file = file_kinds.is_code(file_path)

            if is_code_file:
                logger.info(f"Processing as code file: {file_path}")

                # Read the file content
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()

                # Update the database with enhanced metadata for code files
                session = get_db_session()
                try:
                    # Store additional metadata about the code file
                    metadata = {
                        'file_type': 'code',
                        'language': file_ext[1:],  # extension without dot
                        'line_count': len(content.splitlines()),
                        'char_count': len(content),
                        'processed_at': datetime.datetime.now().isoformat(),
                        'indexing_method': 'code_aware'
                    }

                    # Update the document with enhanced status
                    session.execute(text("""
                        UPDATE documents
                        SET file_metadata = :file_metadata
                        WHERE id = :document_id
                    """), {"file_metadata": json.dumps(metadata), "document_id": document_id})

                    session.commit()
                    logger.info(f"Enhanced code file indexing completed for document {document_id}: {metadata['line_count']} lines, {metadata['char_count']} characters")

                except Exception:
                    logger.warning("Rolling back on bulk import error (non-fatal)", exc_info=True)  # noqa: BLE001
                    session.rollback()
                    raise
                finally:
                    session.close()
            else:
                # Nothing to record for other files, and nothing was indexed.
                logger.info(f"Fallback indexing does nothing for a non-code file: {file_path}")
                return False

            return True

        except UnicodeDecodeError as e:
            logger.warning(f"Text encoding issue for {file_path}: {e}")
            return False
        except Exception as e:
            logger.error(f"Error processing file content for {document_id}: {e}")
            return False

    except Exception as e:
        logger.error(f"Error indexing document {document_id}: {e}")
        return False

# These tasks will be imported by the main Celery app
# They need to be decorated with @celery.task when imported

def ping():
    """Simple ping task for health checking"""
    logger.info("Ping task received")
    return "pong"

def index_one_document(document_id, update_progress=None) -> dict:
    """Index one document and leave its row in its final state.

    The routine every indexing path shares: content, content hash and the
    code-file flag are stored, media and binaries are kept without text, code is
    read in place under code_retrieval=on_demand, and the rest is embedded.
    Returns {"status", "message", "filename"}, status one of indexed, stored,
    deferred (left PENDING for the vector store), unreadable or error.
    """
    update_progress = update_progress or (lambda *_args, **_kwargs: None)
    document = get_document_by_id(document_id)
    if not document:
        return {"status": "error", "message": f"Document {document_id} not found", "filename": None}
    filename = document["filename"]
    update_progress(65, f'Document found: {filename}, starting indexing...')
    upload_dir = os.environ.get('GUAARDVARK_UPLOAD_DIR', os.path.join(STORAGE_DIR, 'uploads'))
    # document paths are uploads-relative; tolerate a stray leading slash,
    # which os.path.join would otherwise treat as an absolute path
    full_file_path = os.path.join(upload_dir, document['file_path'].lstrip('/'))

    update_progress(70, f'Processing file content for {filename}')
    fallback_reason = None
    try:
        outcome = enhanced_code_aware_indexing(full_file_path, document, document_id, update_progress)
    except Exception as e:
        logger.error(f"Enhanced indexing failed: {e}, falling back to simple indexing")
        # The fallback records file metadata; it adds nothing to the vector
        # index, so it never makes the document searchable.
        simple_index_document(full_file_path, document_id)
        outcome = False
        fallback_reason = f"Indexing failed for {filename}: {e}"

    if outcome == STORED_ONLY:
        update_document_status(document_id, "STORED")
        return {"status": "stored", "message": f"Stored {filename}", "filename": filename}
    if outcome == UNREADABLE:
        return {"status": "unreadable",
                "message": f"{filename} is compiled or binary: there is no text to read",
                "filename": filename}
    if outcome == DEFERRED:
        return {"status": "deferred",
                "message": f"Indexing deferred for {filename}: vector store not in use; left PENDING",
                "filename": filename}
    if outcome:
        update_progress(90, f'Finalizing indexing for {filename}')
        update_document_status(document_id, "INDEXED")
        return {"status": "indexed", "message": f"Indexed {filename}", "filename": filename}

    try:
        from backend.services.indexing_service import no_content_reason
        reason = no_content_reason(document_id)
    except Exception:
        reason = None
    error_msg = reason or fallback_reason or f"Indexing failed for document {document_id}"
    update_document_status(document_id, "ERROR", error_msg)
    return {"status": "error", "message": error_msg, "filename": filename, "no_content": bool(reason)}


def index_document_task(document_id, process_id=None):
    """Index a document without any Flask dependencies"""

    if process_id is None:
        process_id = f"indexing_{document_id}"

    # Respect global pause toggle from Settings (prevents heavy embedding work when user wants machine relief)
    try:
        from backend.services.indexing_service import is_indexing_paused  # will add below if missing
    except Exception:
        def is_indexing_paused(): return False
    if is_indexing_paused():
        logger.info(f"Indexing is paused (per Settings); skipping document {document_id}")
        try:
            # update_document_status is defined in this module
            if 'update_document_status' in globals():
                globals()['update_document_status'](document_id, "PENDING", "Skipped while indexing paused")
        except Exception:
            pass
        return {"skipped": True, "reason": "indexing_paused", "document_id": document_id}

    # Job order on: a document is the safe point; hold here while a chat or
    # voice turn runs (job_arbiter.yield_point; a no-op with the setting off).
    from backend.services import job_arbiter
    job_arbiter.yield_point("indexing", document_id, label=f"Indexing document {document_id}")

    logger.info(f"Starting isolated indexing task for document {document_id} with process ID: {process_id}")

    # Get progress system for updates
    progress_system = None
    try:
        from backend.utils.unified_progress_system import get_unified_progress, ProcessType
        progress_system = get_unified_progress()

        # BUG FIX #2: Ensure the process exists before any updates with safer approach
        # This prevents "Process not found for update" errors in cross-process communication
        if progress_system and process_id:
            try:
                # Check if process exists using correct method name
                existing_process = progress_system.get_process(process_id)

                if not existing_process:
                    # Process doesn't exist in this worker, create it manually
                    logger.info(f"Process {process_id} not found in worker, creating new process")

                    # Use the proper API to create the process
                    try:
                        # Under the upload's id: a new id left the upload's job at
                        # "start" forever, while every update below targeted an id
                        # this worker did not have.
                        created_id = progress_system.create_process(
                            ProcessType.INDEXING,
                            f"Indexing document {document_id}",
                            {"document_id": document_id, "process_id": process_id},
                            process_id=process_id,
                        )
                        logger.info(f"Successfully created process {process_id} in worker (created_id: {created_id})")
                    except Exception as create_error:
                        logger.warning(f"Could not create process via API: {create_error}, will use updates directly")
                else:
                    logger.info(f"Successfully found existing process {process_id}")
            except Exception as check_error:
                logger.warning(f"Could not check process existence: {check_error}, will proceed with updates")

    except Exception as e:
        logger.warning(f"Could not get progress system: {e}")

    def update_progress(progress, message, status="processing"):
        """BUG FIX #4: Update progress in the unified system - removed invalid status parameter"""
        if progress_system:
            try:
                # update_process method signature: (process_id, progress, message)
                # Status is managed separately through complete_process/error_process
                progress_system.update_process(process_id, progress, message)
                logger.info(f"Progress update: {progress}% - {message}")
            except Exception as e:
                logger.warning(f"Failed to update progress: {e}")

    try:
        update_progress(55, f'Starting indexing for document {document_id}')
        outcome = index_one_document(document_id, update_progress)
        filename = outcome.get("filename") or f"document {document_id}"
        status = outcome["status"]

        if status == "stored":
            if progress_system:
                progress_system.complete_process(process_id, f'Stored {filename} (nothing to search)')
            return {'process_id': process_id, 'document_id': document_id,
                    'filename': filename, 'status': 'stored'}

        if status == "deferred":
            logger.warning(outcome["message"])
            if progress_system:
                progress_system.error_process(process_id, outcome["message"])
            return {'process_id': process_id, 'document_id': document_id,
                    'filename': filename, 'status': 'deferred'}

        if status == "indexed":
            update_progress(100, f'Indexing completed for {filename}', "complete")
            if progress_system:
                progress_system.complete_process(process_id, f'Successfully indexed {filename}')
            logger.info(f'Successfully indexed {filename}')
            return {'process_id': process_id, 'document_id': document_id,
                    'filename': filename, 'status': 'completed'}

        error_msg = outcome["message"]
        logger.error(f'Indexing failed: {error_msg}')
        if progress_system:
            progress_system.error_process(process_id, error_msg)
        return {'error': error_msg}

    except Exception as e:
        error_msg = f"Unexpected error during indexing: {str(e)}"
        logger.error(error_msg, exc_info=True)

        # Update document status
        update_document_status(document_id, "ERROR", error_msg)

        # Complete with error
        if progress_system:
            progress_system.error_process(process_id, error_msg)

        logger.error(f'Indexing error: {error_msg}')

        return {'error': error_msg}

def generate_bulk_csv_v2_task(rule_id, project_id, target_site, source_site, target_pages, process_id=None):
    """Generate bulk CSV without any Flask dependencies"""
    
    if process_id is None:
        process_id = f"csv_gen_{int(time.time())}"
    
    logger.info(f"Starting isolated CSV generation task with process ID: {process_id}")
    
    try:
        # Log progress
        logger.info('Starting CSV generation...')
        
        # BUG FIX #8: Validate output directory before creating CSV
        output_dir = os.environ.get('GUAARDVARK_OUTPUT_DIR', os.path.join(STORAGE_DIR, 'outputs'))

        # Ensure directory exists and is writable
        try:
            os.makedirs(output_dir, exist_ok=True)
            # Test if directory is writable
            test_file = os.path.join(output_dir, '.write_test')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
        except (OSError, IOError) as e:
            raise Exception(f"Output directory not writable: {output_dir} - {e}")

        output_file = os.path.join(output_dir, f"generated_{process_id}.csv")

        # Create a simple CSV file with proper error handling
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write("ID,Topic,Client,Project,Website\n")
                f.write(f"1,Test Topic,Test Client,Test Project,{target_site}\n")
                f.flush()  # Ensure data is written to disk

            # Verify file was created and has content
            if not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
                raise Exception("CSV file was not created properly")

        except IOError as io_error:
            raise Exception(f"Failed to write CSV file: {io_error}")
        
        logger.info(f"CSV generation completed for {process_id}")
        
        # Log completion
        logger.info(f'CSV generation completed: {os.path.basename(output_file)}')
        
        return {
            'process_id': process_id,
            'output_file': output_file,
            'status': 'completed'
        }
        
    except Exception as e:
        error_msg = f"Unexpected error during CSV generation: {str(e)}"
        logger.error(error_msg, exc_info=True)
        
        logger.error(f'CSV generation failed: {error_msg}')
        
        return {'error': error_msg}


def _guaardvark_storage_dirs():
    """(storage_dir, upload_dir, backup_dir) resolved the way backend.config
    resolves them (relative values against GUAARDVARK_ROOT, same defaults),
    taken at call time: this module stays free of backend.config, and a worker
    or a test sees the current values rather than import-time ones."""
    root = (os.environ.get('GUAARDVARK_ROOT')
            or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def resolve(var, default):
        path = os.environ.get(var, default)
        return path if os.path.isabs(path) else os.path.join(root, path)

    return (resolve('GUAARDVARK_STORAGE_DIR', 'data'),
            resolve('GUAARDVARK_UPLOAD_DIR', 'data/uploads'),
            resolve('GUAARDVARK_BACKUP_DIR', 'backups'))


def scan_import_source(source_path, supported_extensions, storage_dir, upload_dir, target_dir=None,
                       backup_dir=None):
    """Walk ``source_path`` for files a bulk import may take.

    Returns ``(files, skipped)`` where ``files`` is a list of
    ``(full_path, rel_path, filename)`` and ``skipped`` counts what the walk
    left alone. The rules, in order:

    - hidden directories (dot-prefixed) are pruned, so os.walk never enters
      them: .git, .ssh, .aws, .gnupg, .cache, ... (skipped["hidden_dirs"]);
    - symlinked directories are pruned and symlinked files skipped, so the
      walk never leaves the source (skipped["symlinks"]);
    - Guaardvark's own data folder (``storage_dir``, minus ``upload_dir``) and
      the import's target folder are pruned when they lie under the source,
      so docstore.json, index_store.json, config/*.json and the internal
      secret can never be imported or moved (skipped["internal_dirs"]);
    - Guaardvark's backups folder (``backup_dir``: full install archives) is
      pruned the same way when an import of a parent folder reaches it;
    - hidden files are skipped: .env, .netrc, .gitignore, .bashrc, ...
      (skipped["hidden_files"]);
    - credential and secret files named by path_safety.SENSITIVE_PATTERNS are
      skipped wherever they sit: id_rsa, credentials, *.pem, *.key, ...
      (skipped["sensitive_files"]);
    - the extension filter is unchanged, and a plain file with no extension
      (README, LICENSE, Makefile) is still taken.
    """
    from backend.utils.path_safety import is_sensitive, is_within

    skipped = {"hidden_dirs": 0, "hidden_files": 0, "sensitive_files": 0,
               "symlinks": 0, "internal_dirs": 0}
    storage_real = os.path.realpath(storage_dir) if storage_dir else None
    upload_real = os.path.realpath(upload_dir) if upload_dir else None
    target_real = os.path.realpath(target_dir) if target_dir else None
    backup_real = os.path.realpath(backup_dir) if backup_dir else None

    def is_internal(path):
        real = os.path.realpath(path)
        if target_real and real == target_real:
            return True
        if backup_real and is_within(real, [backup_real]):
            return True
        if not storage_real or not is_within(real, [storage_real]):
            return False
        return not (upload_real and is_within(real, [upload_real]))

    files_found = []
    for root, dirs, files in os.walk(source_path):
        kept = []
        for d in sorted(dirs):
            full = os.path.join(root, d)
            if d.startswith('.'):
                skipped["hidden_dirs"] += 1
            elif os.path.islink(full):
                skipped["symlinks"] += 1
            elif is_internal(full):
                skipped["internal_dirs"] += 1
            else:
                kept.append(d)
        dirs[:] = kept  # in place: this is what os.walk descends into
        for file in sorted(files):
            full_path = os.path.join(root, file)
            if file.startswith('.'):
                skipped["hidden_files"] += 1
                continue
            if is_sensitive(file):
                skipped["sensitive_files"] += 1
                continue
            if os.path.islink(full_path):
                skipped["symlinks"] += 1
                continue
            file_ext = os.path.splitext(file)[1].lower()
            if file_ext in supported_extensions or not file_ext:  # Include files without extensions
                rel_path = os.path.relpath(full_path, source_path)
                files_found.append((full_path, rel_path, file))
    return files_found, skipped


def _skipped_summary(skipped):
    """One clause for the status message, or '' when nothing was skipped."""
    files = skipped["hidden_files"] + skipped["sensitive_files"] + skipped["symlinks"]
    dirs = skipped["hidden_dirs"] + skipped["internal_dirs"]
    parts = []
    if dirs:
        parts.append(f"{dirs} hidden or internal folder{'s' if dirs != 1 else ''}")
    if files:
        parts.append(f"{files} hidden or private file{'s' if files != 1 else ''}")
    return f"; skipped {' and '.join(parts)}" if parts else ""


def bulk_import_documents_task(job_id, source_path, target_folder, project_id=None, 
                               client_id=None, website_id=None, reindex_missing=True, 
                               force_copy=True, dry_run=False, target_folder_id=None):
    """
    Bulk import documents from a source directory without Flask dependencies.
    
    Args:
        job_id: Unique job identifier
        source_path: Source directory path to import from
        target_folder: Target folder name in uploads directory
        project_id: Optional project ID to associate documents with
        client_id: Optional client ID
        website_id: Optional website ID
        reindex_missing: Whether to reindex documents that aren't indexed
        force_copy: Copy files (default) rather than move them out of the source
        dry_run: If True, don't actually import, just report what would be done
        target_folder_id: The exact folder to import into; when given,
            target_folder is only its path and is not looked up by name
    
    Returns:
        Dict with job status and statistics
    """
    logger.info(f"Starting bulk import task {job_id} from {source_path}")
    
    # Update job status helper
    def update_job_status(status, message, progress=None, stats=None):
        """Update job status via Redis or in-memory storage"""
        try:
            import redis
            redis_client = redis.Redis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0'), decode_responses=True)
            job_data = {
                "job_id": job_id,
                "status": status,
                "message": message,
                "progress": progress if progress is not None else 0,
                "stats": stats or {},
                "updated_at": datetime.datetime.now().isoformat()
            }
            redis_client.setex(f"bulk_import_job:{job_id}", 3600, json.dumps(job_data))
        except Exception as e:
            logger.warning(f"Could not update job status via Redis: {e}")
    
    try:
        update_job_status("processing", f"Scanning source directory: {source_path}", 10)
        
        # Validate source path
        if not os.path.exists(source_path):
            error_msg = f"Source path does not exist: {source_path}"
            logger.error(error_msg)
            update_job_status("error", error_msg, 0)
            return {'error': error_msg, 'job_id': job_id}
        
        if not os.path.isdir(source_path):
            error_msg = f"Source path is not a directory: {source_path}"
            logger.error(error_msg)
            update_job_status("error", error_msg, 0)
            return {'error': error_msg, 'job_id': job_id}

        # Guaardvark's own data folder is never a source (docstore, index
        # store, config, the internal secret); its uploads folder is the one
        # exception, so already-imported files can be reorganised.
        storage_dir, upload_dir, backup_dir = _guaardvark_storage_dirs()
        from backend.utils.path_safety import is_within
        if (is_within(source_path, [storage_dir]) and not is_within(source_path, [upload_dir])):
            error_msg = ("Source path is inside Guaardvark's own data folder; "
                         "only its uploads folder can be imported from")
            logger.error(f"{error_msg}: {source_path}")
            update_job_status("error", error_msg, 0)
            return {'error': error_msg, 'job_id': job_id}
        if is_within(source_path, [backup_dir]):
            error_msg = "Source path is inside Guaardvark's backups folder"
            logger.error(f"{error_msg}: {source_path}")
            update_job_status("error", error_msg, 0)
            return {'error': error_msg, 'job_id': job_id}
        
        # Find or create Folder record in database
        folder_id = None
        folder_path_for_db = None
        if target_folder_id:
            session = get_db_session()
            try:
                folder_row = session.execute(text("SELECT id, path FROM folders WHERE id = :id"),
                                             {"id": target_folder_id}).fetchone()
            finally:
                session.close()
            if not folder_row:
                error_msg = "The folder to import into no longer exists"
                logger.error(f"{error_msg}: id {target_folder_id}")
                update_job_status("error", error_msg, 0)
                return {'error': error_msg, 'job_id': job_id}
            folder_id, folder_path_for_db = folder_row
        elif target_folder:
            # Normalize folder path (ensure it starts with /)
            folder_path_for_db = target_folder if target_folder.startswith('/') else f"/{target_folder}"
            
            session = get_db_session()
            try:
                # Try to find existing folder by path or name
                result = session.execute(text("SELECT id, path FROM folders WHERE path = :folder_path OR name = :folder_name"),
                                        {"folder_path": folder_path_for_db, "folder_name": target_folder})
                folder_row = result.fetchone()

                if folder_row:
                    folder_id = folder_row[0]
                    folder_path_for_db = folder_row[1]  # Use actual path from DB
                    logger.info(f"Found existing folder: {target_folder} (ID: {folder_id}, path: {folder_path_for_db})")
                elif not dry_run:
                    # Create new folder record
                    now_iso = datetime.datetime.now().isoformat()
                    insert_result = session.execute(text("""
                        INSERT INTO folders (name, path, is_repository, created_at, updated_at)
                        VALUES (:name, :path, false, :created_at, :updated_at)
                        RETURNING id
                    """), {"name": target_folder, "path": folder_path_for_db,
                           "created_at": now_iso, "updated_at": now_iso})
                    folder_id = insert_result.fetchone()[0]
                    session.commit()
                    logger.info(f"Created new folder: {target_folder} (ID: {folder_id}, path: {folder_path_for_db})")
                else:
                    # Dry run - just use the path
                    logger.info(f"Dry run: Would create folder: {target_folder} at {folder_path_for_db}")
            except Exception as e:
                logger.error(f"Error getting/creating folder: {e}", exc_info=True)
                session.rollback()
                # Continue without folder_id - files will still be imported
            finally:
                session.close()
        
        # Determine physical target directory
        if folder_path_for_db:
            # Use folder path from database (strip leading / for filesystem)
            target_dir = os.path.join(upload_dir, folder_path_for_db.lstrip('/'))
        else:
            # Fallback to target_folder name
            target_dir = os.path.join(upload_dir, target_folder or "Imports")
        
        # Create target directory if needed
        if not dry_run:
            os.makedirs(target_dir, exist_ok=True)
        
        # Scan for files
        update_job_status("processing", "Scanning for files...", 20)
        text_extensions = {'.txt', '.md', '.csv', '.json',
                           '.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.css', '.xml'}
        # What is *indexable* is whatever the loaders can actually parse, asked
        # of the loaders rather than restated here. The old hardcoded answer put
        # .pdf/.docx in `media_extensions` on the grounds that "this pipeline
        # reads bytes raw, so PDFs/DOCX would index as mojibake garbage" — true
        # once, but get_documents_from_file() has run Docling ahead of the raw
        # read since 4277256, and docling_loader declares .pdf/.docx/.pptx. The
        # comment outlived its code and stranded every PDF and DOCX at STORED.
        # `.doc` (legacy binary) has no loader and correctly stays out.
        from backend.tasks.index_resume_tasks import _document_extensions
        indexable_extensions = _document_extensions() | text_extensions
        # Media still imports as browsable/viewable only: registered, never queued.
        media_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.webp',
                            '.wav', '.mp3', '.ogg', '.flac', '.m4a',
                            '.mp4', '.webm', '.mov', '.mkv',
                            '.pdf', '.doc', '.docx'}
        supported_extensions = text_extensions | media_extensions | indexable_extensions
        
        files_to_import, skipped = scan_import_source(
            source_path, supported_extensions, storage_dir, upload_dir, target_dir, backup_dir)
        skipped_note = _skipped_summary(skipped)

        total_files = len(files_to_import)
        logger.info(f"Found {total_files} files to import{skipped_note}")
        update_job_status("processing", f"Found {total_files} files to import{skipped_note}", 30)

        if dry_run:
            dry_stats = {"total_files": total_files, "files": [f[2] for f in files_to_import],
                         "skipped_private": skipped}
            update_job_status("complete", f"Dry run: Would import {total_files} files{skipped_note}", 100,
                              dry_stats)
            return {'job_id': job_id, 'status': 'complete', 'dry_run': True,
                    'total_files': total_files, 'files': [f[2] for f in files_to_import],
                    'skipped_private': skipped}
        
        # Process files
        imported_count = 0
        skipped_count = 0
        error_count = 0
        indexed_count = 0
        
        session = get_db_session()

        # Folder rows per nested source directory — the imported tree keeps
        # its shape instead of flattening into the target folder.
        folder_cache = {}
        if folder_id is not None and folder_path_for_db:
            folder_cache[folder_path_for_db] = folder_id

        def ensure_folder_chain(rel_dir):
            """Return (folder_id, folder_path) for target/rel_dir, creating rows."""
            if not folder_path_for_db:
                return None, None
            cur_path = folder_path_for_db
            cur_id = folder_cache.get(cur_path)
            if rel_dir in ("", "."):
                return cur_id, cur_path
            for part in rel_dir.replace(os.sep, "/").split("/"):
                nxt = f"{cur_path}/{part}"
                if nxt in folder_cache:
                    cur_id, cur_path = folder_cache[nxt], nxt
                    continue
                row = session.execute(
                    text("SELECT id FROM folders WHERE path = :p"), {"p": nxt}
                ).fetchone()
                if row:
                    folder_cache[nxt] = row[0]
                    cur_id, cur_path = row[0], nxt
                    continue
                now_iso = datetime.datetime.now().isoformat()
                new_row = session.execute(text("""
                    INSERT INTO folders (name, path, parent_id, is_repository,
                                         created_at, updated_at)
                    VALUES (:name, :path, :parent, false, :now, :now)
                    RETURNING id
                """), {"name": part, "path": nxt, "parent": cur_id,
                       "now": now_iso}).fetchone()
                session.commit()
                folder_cache[nxt] = new_row[0]
                cur_id, cur_path = new_row[0], nxt
            return cur_id, cur_path

        import shutil

        def db_path_for(file_folder_path, name):
            # document.path is relative to the uploads dir (no leading slash);
            # only folder.path carries one.
            if file_folder_path:
                return f"{file_folder_path}/{name}".lstrip('/')
            return os.path.join(target_folder or "Imports", name)

        def queue_indexing(doc_id):
            try:
                from backend.celery_tasks_isolated import index_document_task
                index_document_task.apply_async((doc_id,), queue='indexing')
                return True
            except Exception as e:
                logger.warning(f"Could not queue indexing for document {doc_id}: {e}")
                return False

        try:
            for idx, (source_file, rel_path, filename) in enumerate(files_to_import):
                progress = 30 + int((idx / total_files) * 60)
                update_job_status("processing", f"Importing {filename} ({idx+1}/{total_files})", progress)

                copied_file = None
                try:
                    rel_dir = os.path.dirname(rel_path)
                    file_folder_id, file_folder_path = ensure_folder_chain(rel_dir)
                    dest_dir = os.path.join(target_dir, rel_dir) if rel_dir else target_dir

                    indexable = (os.path.splitext(filename)[1].lower()
                                 in indexable_extensions
                                 or not os.path.splitext(filename)[1])

                    # A row for this file from an earlier run is looked up by the
                    # path it would get, before any on-disk rename, so a re-run
                    # skips it rather than importing a name_1.ext duplicate. The
                    # source file is not touched.
                    existing = session.execute(
                        text("SELECT id, index_status FROM documents WHERE path = :db_path"),
                        {"db_path": db_path_for(file_folder_path, filename)}).fetchone()
                    if existing:
                        doc_id, index_status = existing
                        if reindex_missing and index_status != 'INDEXED' and indexable:
                            if queue_indexing(doc_id):
                                indexed_count += 1
                        skipped_count += 1
                        logger.info(f"Skipped already imported document: {filename}")
                        continue

                    os.makedirs(dest_dir, exist_ok=True)
                    target_file = os.path.join(dest_dir, filename)

                    # A file on disk without a row (an earlier run that failed
                    # after copying) keeps its name; this one gets a suffix.
                    counter = 1
                    base_name, ext = os.path.splitext(filename)
                    while os.path.exists(target_file):
                        target_file = os.path.join(dest_dir, f"{base_name}_{counter}{ext}")
                        counter += 1
                    db_path = db_path_for(file_folder_path, os.path.basename(target_file))

                    # Copy first, in both modes. The source leaves the user's
                    # folder only once its row is committed; a failure below
                    # removes the copy and leaves the source where it was.
                    shutil.copy2(source_file, target_file)
                    copied_file = target_file

                    file_size = os.path.getsize(target_file)
                    file_ext = os.path.splitext(filename)[1]

                    insert_result = session.execute(text("""
                        INSERT INTO documents (filename, path, type, size, project_id, client_id, website_id, folder_id, index_status, uploaded_at)
                        VALUES (:filename, :path, :type, :size, :project_id, :client_id, :website_id, :folder_id, :index_status, :uploaded_at)
                        RETURNING id
                    """), {"filename": filename, "path": db_path, "type": file_ext, "size": file_size,
                           "project_id": project_id, "client_id": client_id, "website_id": website_id,
                           "folder_id": file_folder_id,
                           "index_status": 'PENDING' if indexable else 'STORED',
                           "uploaded_at": datetime.datetime.now().isoformat()})
                    doc_id = insert_result.fetchone()[0]
                    session.commit()
                    copied_file = None  # the copy is now the imported file

                    imported_count += 1
                    logger.info(f"Created document record {doc_id}: {filename}")

                    if not force_copy:
                        try:
                            os.remove(source_file)
                        except OSError as e:
                            logger.warning(f"Imported {filename} but could not remove the source {source_file}: {e}")

                    if indexable and queue_indexing(doc_id):
                        indexed_count += 1

                except Exception as e:
                    session.rollback()
                    error_count += 1
                    if copied_file and os.path.exists(copied_file):
                        try:
                            os.remove(copied_file)
                        except OSError as cleanup_err:
                            logger.warning(f"Could not remove partial copy {copied_file}: {cleanup_err}")
                    logger.error(f"Error importing {filename}: {e}", exc_info=True)

        finally:
            session.close()
        
        # Final status
        stats = {
            "total_files": total_files,
            "imported": imported_count,
            "skipped": skipped_count,
            "errors": error_count,
            "queued_for_indexing": indexed_count,
            "skipped_private": skipped,
        }

        update_job_status("complete",
                          f"Import complete: {imported_count} imported, {skipped_count} skipped, "
                          f"{error_count} errors{skipped_note}",
                          100, stats)
        
        logger.info(f"Bulk import {job_id} completed: {stats}")
        return {
            'job_id': job_id,
            'status': 'complete',
            'stats': stats
        }
        
    except Exception as e:
        error_msg = f"Bulk import failed: {str(e)}"
        logger.error(error_msg, exc_info=True)
        update_job_status("error", error_msg, 0)
        return {'error': error_msg, 'job_id': job_id}
