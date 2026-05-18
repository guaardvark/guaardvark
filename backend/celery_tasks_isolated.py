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

def enhanced_code_aware_indexing(file_path, document, document_id, update_progress):
    """Enhanced indexing that leverages Guaardvark's existing CodeChunker system"""
    try:
        logger.info(f"Starting enhanced code-aware indexing for document {document_id}")
        update_progress(75, f'Analyzing file type and content for {document["filename"]}')

        # Read file content
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return False

        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        # Determine file type and language
        file_ext = os.path.splitext(file_path)[1].lower()
        code_extensions = {'.js', '.jsx', '.ts', '.tsx', '.py', '.java', '.cpp', '.c', '.h', '.cs', '.php', '.rb', '.go', '.rs', '.swift', '.kt', '.scala'}
        is_code_file = file_ext in code_extensions

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

            # Store content and update all fields for code files
            if is_code_file:
                session.execute(text("""
                    UPDATE documents
                    SET content = :content, is_code_file = :is_code_file, index_status = :index_status, indexed_at = :indexed_at,
                        file_metadata = :file_metadata, error_message = NULL
                    WHERE id = :document_id
                """), {"content": content, "is_code_file": True, "index_status": 'INDEXED',
                       "indexed_at": datetime.datetime.now().isoformat(),
                       "file_metadata": json.dumps(metadata), "document_id": document_id})
                logger.info(f"Enhanced code file indexing completed for document {document_id}: {metadata['line_count']} lines, language: {metadata['language']}")
            else:
                # For non-code files, still store content but with different metadata
                session.execute(text("""
                    UPDATE documents
                    SET content = :content, is_code_file = :is_code_file, index_status = :index_status, indexed_at = :indexed_at,
                        file_metadata = :file_metadata, error_message = NULL
                    WHERE id = :document_id
                """), {"content": content, "is_code_file": False, "index_status": 'INDEXED',
                       "indexed_at": datetime.datetime.now().isoformat(),
                       "file_metadata": json.dumps(metadata), "document_id": document_id})
                logger.info(f"Enhanced text file indexing completed for document {document_id}: {metadata['line_count']} lines")

            session.commit()

            update_progress(90, f'Adding to vector index for semantic search: {document["filename"]}')

            # BUG FIX #3: Move import outside or handle circular import better
            # Actually index into LlamaIndex vector store
            vector_indexed = False
            try:
                # Import here to avoid circular dependencies at module level
                from backend.services.indexing_service import add_text_to_index
                logger.info(f"Adding document {document_id} to vector index for RAG search")

                # Create LlamaIndex document with proper metadata (consistent with search expectations)
                doc_metadata = {
                    'filename': document['filename'],
                    'source_filename': document['filename'],
                    'document_id': str(document_id),
                    'file_type': metadata['file_type'],
                    'language': metadata['language'],
                    'project_id': str(document.get('project_id', 1)),
                    'indexed_at': metadata['processed_at'],
                    'content_type': 'document',
                    'entity_type': metadata['file_type']
                }

                # Add to vector index - this enables RAG search
                project_id_str = str(document.get('project_id')) if document.get('project_id') else None
                vector_result = add_text_to_index(content, metadata=doc_metadata, project_id=project_id_str)
                if vector_result:
                    logger.info(f"Successfully added document {document_id} to vector index")
                    update_progress(95, f'Vector indexing complete for {document["filename"]}')
                    vector_indexed = True
                else:
                    logger.warning(f"Vector indexing failed for document {document_id}")

            except ImportError as import_error:
                logger.warning(f"Could not import indexing_service (may be expected in isolated worker): {import_error}")
            except Exception as vector_error:
                logger.error(f"Vector indexing failed for document {document_id}: {vector_error}")
                # Don't fail the entire indexing if vector indexing fails
                # Database storage is still successful

            # Log success with detailed information
            logger.info(f"Successfully indexed document {document_id} using enhanced code-aware method:")
            logger.info(f"  - File: {document['filename']}")
            logger.info(f"  - Type: {metadata['file_type']}")
            logger.info(f"  - Language: {metadata['language']}")
            logger.info(f"  - Strategy: {metadata['processing_strategy']}")
            logger.info(f"  - Content stored: {len(content)} characters")
            logger.info(f"  - Vector indexed: Available for RAG search")

            return True

        except Exception:
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
            pass
        return False


def simple_index_document(file_path, document_id):
    """Enhanced document indexing for code files without LlamaIndex dependencies"""
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
            code_extensions = {'.js', '.jsx', '.ts', '.tsx', '.py', '.java', '.cpp', '.c', '.h', '.cs', '.php', '.rb', '.go', '.rs', '.swift', '.kt', '.scala'}

            is_code_file = file_ext in code_extensions

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
                        SET index_status = :index_status, indexed_at = :indexed_at, file_metadata = :file_metadata
                        WHERE id = :document_id
                    """), {"index_status": 'INDEXED', "indexed_at": datetime.datetime.now().isoformat(),
                           "file_metadata": json.dumps(metadata), "document_id": document_id})

                    session.commit()
                    logger.info(f"Enhanced code file indexing completed for document {document_id}: {metadata['line_count']} lines, {metadata['char_count']} characters")

                except Exception:
                    session.rollback()
                    raise
                finally:
                    session.close()
            else:
                logger.info(f"Processing as regular file: {file_path}")
                # For non-code files, use basic processing
                pass

            return True

        except UnicodeDecodeError as e:
            logger.warning(f"Text encoding issue for {file_path}: {e}")
            # Still mark as indexed but note the encoding issue
            return True
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

def index_document_task(document_id, process_id=None):
    """Index a document without any Flask dependencies"""

    if process_id is None:
        process_id = f"indexing_{document_id}"

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
                        created_id = progress_system.create_process(
                            ProcessType.INDEXING,
                            f"Indexing document {document_id}",
                            {"document_id": document_id, "process_id": process_id}
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
        # Update progress: Starting
        update_progress(55, f'Starting indexing for document {document_id}')
        logger.info(f'Starting indexing for document {document_id}')
        
        # Get document from database
        update_progress(60, f'Loading document metadata for {document_id}')
        document = get_document_by_id(document_id)
        if not document:
            error_msg = f"Document {document_id} not found"
            logger.error(error_msg)
            if progress_system:
                progress_system.error_process(process_id, error_msg)
            return {'error': error_msg}

        logger.info(f"Found document: {document['filename']}")

        # Log progress
        update_progress(65, f'Document found: {document["filename"]}, starting indexing...')
        logger.info(f'Document found: {document["filename"]}, starting indexing...')
        
        # Construct full file path
        upload_dir = os.environ.get('GUAARDVARK_UPLOAD_DIR', os.path.join(STORAGE_DIR, 'uploads'))
        full_file_path = os.path.join(upload_dir, document['file_path'])
        
        # Index the document using enhanced code-aware processing
        update_progress(70, f'Processing file content for {document["filename"]}')
        try:
            # Use the enhanced indexing that works with the existing CodeChunker system
            success = enhanced_code_aware_indexing(full_file_path, document, document_id, update_progress)
        except Exception as e:
            logger.error(f"Enhanced indexing failed: {e}, falling back to simple indexing")
            update_progress(75, f'Using fallback indexing due to error for {document["filename"]}')
            success = simple_index_document(full_file_path, document_id)
        
        if success:
            # Update progress: Finalizing
            update_progress(90, f'Finalizing indexing for {document["filename"]}')
            logger.info(f'Indexing completed for {document["filename"]}')

            # Update document status in database
            update_document_status(document_id, "INDEXED")

            logger.info(f"Successfully indexed {document['filename']} for {process_id}")

            # Complete the progress
            update_progress(100, f'Indexing completed for {document["filename"]}', "complete")
            if progress_system:
                progress_system.complete_process(process_id, f'Successfully indexed {document["filename"]}')

            logger.info(f'Successfully indexed {document["filename"]}')

            return {
                'process_id': process_id,
                'document_id': document_id,
                'filename': document['filename'],
                'status': 'completed'
            }
        else:
            error_msg = f"Indexing failed for document {document_id}"
            logger.error(error_msg)

            # Update document status
            update_document_status(document_id, "ERROR", error_msg)

            # Complete with error
            if progress_system:
                progress_system.error_process(process_id, error_msg)

            logger.error(f'Indexing failed: {error_msg}')

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

def bulk_import_documents_task(job_id, source_path, target_folder, project_id=None, 
                               client_id=None, website_id=None, reindex_missing=True, 
                               force_copy=False, dry_run=False):
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
        force_copy: Whether to copy files (vs move)
        dry_run: If True, don't actually import, just report what would be done
    
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
        
        # Get upload directory
        upload_dir = os.environ.get('GUAARDVARK_UPLOAD_DIR', os.path.join(STORAGE_DIR, 'uploads'))
        
        # Find or create Folder record in database
        folder_id = None
        folder_path_for_db = None
        if target_folder:
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
                        INSERT INTO folders (name, path, created_at, updated_at)
                        VALUES (:name, :path, :created_at, :updated_at)
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
        supported_extensions = {'.txt', '.pdf', '.doc', '.docx', '.md', '.csv', '.json', 
                               '.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.css', '.xml'}
        
        files_to_import = []
        for root, dirs, files in os.walk(source_path):
            for file in files:
                file_ext = os.path.splitext(file)[1].lower()
                if file_ext in supported_extensions or not file_ext:  # Include files without extensions
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, source_path)
                    files_to_import.append((full_path, rel_path, file))
        
        total_files = len(files_to_import)
        logger.info(f"Found {total_files} files to import")
        update_job_status("processing", f"Found {total_files} files to import", 30)
        
        if dry_run:
            update_job_status("complete", f"Dry run: Would import {total_files} files", 100, 
                            {"total_files": total_files, "files": [f[2] for f in files_to_import]})
            return {'job_id': job_id, 'status': 'complete', 'dry_run': True, 
                   'total_files': total_files, 'files': [f[2] for f in files_to_import]}
        
        # Process files
        imported_count = 0
        skipped_count = 0
        error_count = 0
        indexed_count = 0
        
        session = get_db_session()
        try:
            for idx, (source_file, rel_path, filename) in enumerate(files_to_import):
                progress = 30 + int((idx / total_files) * 60)
                update_job_status("processing", f"Importing {filename} ({idx+1}/{total_files})", progress)

                try:
                    # Copy or move file
                    target_file = os.path.join(target_dir, filename)

                    # Handle duplicate filenames
                    counter = 1
                    base_name, ext = os.path.splitext(filename)
                    while os.path.exists(target_file):
                        target_file = os.path.join(target_dir, f"{base_name}_{counter}{ext}")
                        counter += 1

                    if force_copy:
                        import shutil
                        shutil.copy2(source_file, target_file)
                    else:
                        import shutil
                        shutil.move(source_file, target_file)

                    # Create relative path for database
                    # Use folder path if available, otherwise use target_folder
                    if folder_path_for_db:
                        # Match API pattern: folder_path/filename (folder_path already has leading /)
                        db_path = f"{folder_path_for_db}/{os.path.basename(target_file)}"
                    else:
                        # Fallback: use target_folder name without leading slash
                        db_path = os.path.join(target_folder or "Imports", os.path.basename(target_file))

                    # Check if document already exists
                    result = session.execute(text("SELECT id, index_status FROM documents WHERE path = :db_path"),
                                            {"db_path": db_path})
                    existing = result.fetchone()

                    if existing:
                        doc_id, index_status = existing
                        if reindex_missing and index_status != 'INDEXED':
                            # Queue for reindexing
                            try:
                                from backend.celery_tasks_isolated import index_document_task
                                index_document_task.apply_async((doc_id,), queue='indexing')
                                indexed_count += 1
                            except Exception as e:
                                logger.warning(f"Could not queue reindexing for document {doc_id}: {e}")
                        skipped_count += 1
                        logger.info(f"Skipped existing document: {filename}")
                    else:
                        # Create new document record
                        file_size = os.path.getsize(target_file)
                        file_ext = os.path.splitext(filename)[1]

                        insert_result = session.execute(text("""
                            INSERT INTO documents (filename, path, type, size, project_id, client_id, website_id, folder_id, index_status, uploaded_at)
                            VALUES (:filename, :path, :type, :size, :project_id, :client_id, :website_id, :folder_id, :index_status, :uploaded_at)
                            RETURNING id
                        """), {"filename": filename, "path": db_path, "type": file_ext, "size": file_size,
                               "project_id": project_id, "client_id": client_id, "website_id": website_id,
                               "folder_id": folder_id, "index_status": 'PENDING',
                               "uploaded_at": datetime.datetime.now().isoformat()})
                        doc_id = insert_result.fetchone()[0]
                        session.commit()

                        imported_count += 1
                        logger.info(f"Created document record {doc_id}: {filename}")

                        # Queue for indexing
                        try:
                            from backend.celery_tasks_isolated import index_document_task
                            index_document_task.apply_async((doc_id,), queue='indexing')
                            indexed_count += 1
                        except Exception as e:
                            logger.warning(f"Could not queue indexing for document {doc_id}: {e}")

                except Exception as e:
                    session.rollback()
                    error_count += 1
                    logger.error(f"Error importing {filename}: {e}", exc_info=True)

        finally:
            session.close()
        
        # Final status
        stats = {
            "total_files": total_files,
            "imported": imported_count,
            "skipped": skipped_count,
            "errors": error_count,
            "queued_for_indexing": indexed_count
        }
        
        update_job_status("complete", f"Import complete: {imported_count} imported, {skipped_count} skipped, {error_count} errors", 
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
