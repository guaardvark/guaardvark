# backend/services/unified_upload_service.py
# Unified Upload Service
# Handles file uploads with support for both folder-based and project-based uploads

import datetime
import json
import logging
import os
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from werkzeug.utils import secure_filename
from flask import current_app

from backend.models import Folder, Document as DBDocument, Client, Project, Website, db
from backend.utils.unified_progress_system import get_unified_progress, ProcessType

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
    
    _, ext = os.path.splitext(filename.lower())
    return ext in text_extensions


def _read_file_content(file_path: str, max_size_mb: int = 100) -> Optional[str]:
    """Read file content safely with increased size limits."""
    try:
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


class UnifiedUploadService:
    """Unified service for handling file uploads with all features"""
    
    @staticmethod
    def upload_file(
        file,
        folder_path: Optional[str] = None,
        project_id: Optional[int] = None,
        client_id: Optional[int] = None,
        website_id: Optional[int] = None,
        tags: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        store_content: bool = True,  # Whether to store content in DB for text/code files
        auto_index: bool = True,     # Whether to automatically trigger indexing
    ) -> Tuple[DBDocument, Optional[str]]:
        """
        Upload a file with unified handling
        
        Args:
            file: File object from request
            folder_path: Optional folder path (database path like '/002')
            project_id: Optional project ID
            client_id: Optional client ID
            website_id: Optional website ID
            tags: Optional tags string
            metadata: Optional metadata dict
            store_content: Whether to store file content in database (for text/code files)
            auto_index: Whether to automatically trigger indexing
            
        Returns:
            Tuple of (document, job_id) where job_id is None if auto_index is False
        """
        if not file or not file.filename:
            raise ValueError("No file provided")
        
        # Get upload folder configuration
        upload_folder = current_app.config.get("UPLOAD_FOLDER")
        if not upload_folder:
            raise ValueError("Upload folder not configured")
        
        # Ensure upload directory exists
        os.makedirs(upload_folder, exist_ok=True)

        # Resolve folder + entities FIRST — the filename resolver needs folder_id
        # to know which siblings to compare against, and the entity validation
        # should fail-fast before we touch disk.
        folder_id = None
        if folder_path and folder_path != "/":
            folder = Folder.query.filter_by(path=folder_path).first()
            if not folder:
                raise ValueError(f"Folder not found: {folder_path}")
            folder_id = folder.id

        if client_id and not db.session.get(Client, client_id):
            raise ValueError(f"Client not found: {client_id}")
        if project_id and not db.session.get(Project, project_id):
            raise ValueError(f"Project not found: {project_id}")
        if website_id and not db.session.get(Website, website_id):
            raise ValueError(f"Website not found: {website_id}")

        # Keep the user's filename verbatim; let the collision resolver pick
        # a Files-app-style suffix if a sibling already holds the name. The
        # legacy YYYYMMDD_HHMMSS_ prefix is gone — it created divergence
        # between Document.filename (clean) and basename(Document.path)
        # (timestamped) that broke path resolution for any consumer assuming
        # the two agreed.
        from backend.utils.filename_resolver import resolve_filename
        original_filename = secure_filename(file.filename)
        chosen_filename = resolve_filename(
            folder_id, original_filename, db.session, DBDocument,
        )

        # Determine storage path (filesystem path under UPLOAD_FOLDER)
        if folder_path and folder_path != "/":
            # For database paths like '/002', remove leading slash for filesystem
            folder_path_clean = folder_path.lstrip('/')
            storage_path = f"{folder_path_clean}/{chosen_filename}"
        else:
            storage_path = chosen_filename

        # Construct full file path
        file_path = os.path.join(upload_folder, storage_path)

        # Create directory if needed
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)

        # Save file first
        file.save(file_path)
        file_size = os.path.getsize(file_path)
        logger.info(f"Saved file to: {file_path} ({file_size} bytes)")

        # Get file size for progress tracking (use actual saved size)
        total_size = file_size

        # Detect file types — chosen_filename keeps the original's extension
        # since the resolver only adds a ' (N)' suffix before the extension.
        is_code = _is_code_file(chosen_filename)
        is_text = _is_text_file(chosen_filename)
        file_ext = '.' + chosen_filename.split('.')[-1].lower() if '.' in chosen_filename else ''
        
        # Create progress tracking
        progress_system = get_unified_progress()
        job_id = None
        
        if auto_index and progress_system:
            job_id = progress_system.create_process(
                ProcessType.INDEXING,
                f"Processing {chosen_filename}",
                {"filename": chosen_filename, "size": total_size, "document_id": None}
            )
            if progress_system and job_id:
                progress_system.update_process(job_id, 10, f"Uploading {chosen_filename}")
        
        # Create document record
        document = DBDocument(
            filename=chosen_filename,
            path=storage_path,
            type=file_ext,
            folder_id=folder_id,
            client_id=client_id,
            project_id=project_id,
            website_id=website_id,
            tags=tags,
            size=file_size,
            index_status="PENDING",
            file_metadata=json.dumps(metadata) if metadata is not None else None,
            is_code_file=is_code,
            indexing_job_id=job_id
        )
        
        db.session.add(document)
        db.session.flush()  # Get ID without committing
        
        # Update progress with document ID
        if progress_system and job_id:
            progress_system.update_process(job_id, 20, f"Uploading {chosen_filename}")
        
        # Store content in database if requested and file is text/code
        if store_content and (is_text or is_code):
            try:
                file_content = _read_file_content(file_path)
                if file_content:
                    max_content_size = 200 * 1024 * 1024  # 200MB limit
                    if len(file_content) > max_content_size:
                        truncated_content = file_content[:max_content_size] + f"\n\n[CONTENT TRUNCATED - Original size: {len(file_content)} bytes, showing first {max_content_size} bytes]"
                        document.content = truncated_content
                        document.index_status = "STORED_TRUNCATED"
                        document.error_message = f"File truncated due to size: {len(file_content)} bytes (stored {max_content_size} bytes)"
                        logger.warning(f"File {chosen_filename} truncated for storage")
                    else:
                        document.content = file_content
                        document.index_status = "STORED"
                        logger.info(f"Stored complete content for {'code' if is_code else 'text'} file: {chosen_filename}")
                else:
                    document.index_status = "STORAGE_FAILED"
                    document.error_message = "Failed to read file content"
                    logger.warning(f"Failed to store content for {chosen_filename}")
            except Exception as e:
                document.index_status = "STORAGE_FAILED"
                document.error_message = f"Content storage error: {str(e)}"
                logger.error(f"Error storing content for {chosen_filename}: {e}")
        
        # Commit document
        db.session.commit()
        logger.info(f"Document {document.id} ({chosen_filename}) successfully committed to database")
        
        # Update progress
        if progress_system and job_id:
            progress_system.update_process(job_id, 50, f"Upload complete: {chosen_filename}, starting indexing...")
        
        # Trigger indexing if requested
        if auto_index:
            try:
                from backend.celery_tasks_isolated import index_document_task
                
                # Submit indexing task to Celery directly (no HTTP delay)
                task = index_document_task.apply_async((document.id, job_id), queue='indexing')
                logger.info(f"Submitted indexing task for document {document.id}: {task.id}")
            except Exception as index_err:
                logger.error(f"Failed to trigger indexing for document {document.id}: {index_err}")
                # Don't fail the upload if indexing fails - user can manually reindex
                if progress_system and job_id:
                    progress_system.error_process(job_id, f"Indexing trigger failed: {index_err}")
        
        return document, job_id

