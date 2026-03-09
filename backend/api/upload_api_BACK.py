# backend/api/upload_api.py
# Version 1.3: Align with Document model v1.4 (path, index_status, uploaded_at)

import logging
import os
from datetime import datetime, timezone
import json # Added for json.loads

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.utils import secure_filename
from backend.utils.response_utils import success_response, error_response

# Use absolute imports
try:
    # Import Project model as well to potentially validate project_id if needed
    # Import Document model using alias to avoid conflict with LlamaIndex Document
    from backend.models import Document as DBDocument
    from backend.models import Project, db
except ImportError:
    logging.getLogger(__name__).critical(
        "Failed to import db/Document/Project model.", exc_info=True
    )
    db = None
    DBDocument = None
    Project = None  # Add fallback for Project

# --- Blueprint Definition ---
upload_bp = Blueprint("upload", __name__, url_prefix="/api/upload")

# Allowed file extensions - expanded to support development files
ALLOWED_EXTENSIONS = {
    # Text and documentation
    "txt", "md", "rst", "rtf",
    # Data formats
    "csv", "json", "xml", "yaml", "yml", "toml",
    # Documents
    "pdf", "docx", "doc", "odt",
    # Web technologies
    "html", "htm", "css", "scss", "sass", "less",
    "js", "jsx", "ts", "tsx", "vue", "svelte",
    # Programming languages
    "py", "pyw", "pyi", "rb", "php", "java", "kt", "scala",
    "c", "cpp", "cc", "cxx", "h", "hpp", "hxx",
    "cs", "fs", "vb", "go", "rs", "swift", "dart",
    "r", "m", "mm", "pl", "sh", "bash", "zsh", "fish",
    "sql", "lua", "nim", "zig", "julia", "elm",
    # Configuration and scripts
    "ini", "conf", "config", "cfg", "env",
    "dockerfile", "makefile", "cmake", "gradle",
    "gitignore", "gitattributes", "editorconfig",
    # Markup and templating
    "svg", "mxml", "xaml", "jsp", "asp", "aspx",
    "handlebars", "hbs", "mustache", "twig", "jinja2",
    # Images
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "tif",
    # Shell and batch
    "bat", "cmd", "ps1", "psm1",
    # Other common formats
    "log", "diff", "patch", "properties", "plist"
}

# MIME type validation for additional security - expanded for code files
ALLOWED_MIME_TYPES = {
    # Text and documentation
    "txt": ["text/plain"],
    "md": ["text/plain", "text/markdown", "text/x-markdown"],
    "rst": ["text/plain", "text/x-rst"],
    "rtf": ["application/rtf", "text/rtf"],
    
    # Data formats
    "csv": ["text/csv", "application/csv", "text/plain"],
    "json": ["application/json", "text/json", "text/plain"],
    "xml": ["application/xml", "text/xml", "text/plain"],
    "yaml": ["application/x-yaml", "text/yaml", "text/plain"],
    "yml": ["application/x-yaml", "text/yaml", "text/plain"],
    "toml": ["application/toml", "text/plain"],
    
    # Documents
    "pdf": ["application/pdf"],
    "docx": ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
    "doc": ["application/msword"],
    "odt": ["application/vnd.oasis.opendocument.text"],
    
    # Web technologies
    "html": ["text/html", "text/plain"],
    "htm": ["text/html", "text/plain"],
    "css": ["text/css", "text/plain"],
    "scss": ["text/plain"],
    "sass": ["text/plain"],
    "less": ["text/plain"],
    "js": ["application/javascript", "text/javascript", "text/plain"],
    "jsx": ["text/plain", "application/javascript"],
    "ts": ["text/plain", "application/typescript"],
    "tsx": ["text/plain", "application/typescript"],
    "vue": ["text/plain"],
    "svelte": ["text/plain"],
    
    # Programming languages
    "py": ["text/plain", "text/x-python"],
    "pyw": ["text/plain", "text/x-python"],
    "pyi": ["text/plain", "text/x-python"],
    "rb": ["text/plain", "application/x-ruby"],
    "php": ["text/plain", "application/x-php"],
    "java": ["text/plain", "text/x-java-source"],
    "kt": ["text/plain"],
    "scala": ["text/plain"],
    "c": ["text/plain", "text/x-c"],
    "cpp": ["text/plain", "text/x-c++"],
    "cc": ["text/plain", "text/x-c++"],
    "cxx": ["text/plain", "text/x-c++"],
    "h": ["text/plain", "text/x-c"],
    "hpp": ["text/plain", "text/x-c++"],
    "hxx": ["text/plain", "text/x-c++"],
    "cs": ["text/plain"],
    "fs": ["text/plain"],
    "vb": ["text/plain"],
    "go": ["text/plain", "text/x-go"],
    "rs": ["text/plain"],
    "swift": ["text/plain"],
    "dart": ["text/plain"],
    "r": ["text/plain"],
    "m": ["text/plain"],
    "mm": ["text/plain"],
    "pl": ["text/plain", "text/x-perl"],
    "sh": ["text/plain", "application/x-sh"],
    "bash": ["text/plain", "application/x-sh"],
    "zsh": ["text/plain", "application/x-sh"],
    "fish": ["text/plain"],
    "sql": ["text/plain", "application/sql"],
    "lua": ["text/plain", "text/x-lua"],
    
    # Configuration and scripts
    "ini": ["text/plain"],
    "conf": ["text/plain"],
    "config": ["text/plain"],
    "cfg": ["text/plain"],
    "env": ["text/plain"],
    "dockerfile": ["text/plain"],
    "makefile": ["text/plain"],
    "cmake": ["text/plain"],
    "gradle": ["text/plain"],
    
    # Markup and templating
    "svg": ["image/svg+xml", "text/xml"],
    "jsp": ["text/plain"],
    "asp": ["text/plain"],
    "aspx": ["text/plain"],
    
    # Images
    "png": ["image/png"],
    "jpg": ["image/jpeg"],
    "jpeg": ["image/jpeg"],
    "gif": ["image/gif"],
    "webp": ["image/webp"],
    "bmp": ["image/bmp"],
    "tiff": ["image/tiff"],
    "tif": ["image/tiff"],
    
    # Shell and batch
    "bat": ["text/plain"],
    "cmd": ["text/plain"],
    "ps1": ["text/plain"],
    
    # Other formats
    "log": ["text/plain"],
    "diff": ["text/plain"],
    "patch": ["text/plain"],
    "properties": ["text/plain"],
}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def validate_file_content(file, filename):
    """
    Validate file content to ensure it matches the expected file type.
    
    Args:
        file: The uploaded file object
        filename: The filename
        
    Returns:
        bool: True if file is valid, False otherwise
    """
    if not filename or "." not in filename:
        return False
    
    file_ext = filename.rsplit(".", 1)[1].lower()
    
    # Check if extension is allowed
    if file_ext not in ALLOWED_EXTENSIONS:
        return False
    
    # Read a small portion of the file to check content
    try:
        file.seek(0)  # Reset file pointer
        content_start = file.read(8192)  # Read first 8KB for better validation
        file.seek(0)  # Reset file pointer again
        
        # Basic content validation based on file type
        if file_ext == "pdf":
            # PDF files should start with %PDF
            if not content_start.startswith(b'%PDF'):
                return False
        elif file_ext in ["docx", "doc", "odt"]:
            # Binary document formats - just check they're not empty and have binary content
            if len(content_start) == 0:
                return False
            # These are binary formats, so we don't validate text content
        elif file_ext in ["svg"]:
            # SVG files should contain XML content
            try:
                content_str = content_start.decode('utf-8', errors='ignore')
                if not ('<svg' in content_str.lower() or '<?xml' in content_str.lower()):
                    return False
            except Exception:
                return False
        elif file_ext in ["png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "tif"]:
            # Image files - just check they're not empty and have binary content
            if len(content_start) == 0:
                return False
            # Basic binary format validation
            if file_ext == "png" and not content_start.startswith(b'\x89PNG'):
                return False
            elif file_ext in ["jpg", "jpeg"] and not content_start.startswith(b'\xFF\xD8'):
                return False
            elif file_ext == "gif" and not content_start.startswith(b'GIF'):
                return False
            # For other image formats, just ensure they have binary content
        elif file_ext == "json":
            # JSON files should be valid JSON (for smaller files) or start with { or [
            try:
                content_str = content_start.decode('utf-8')
                # Try to parse as JSON first
                try:
                    json.loads(content_str)
                except json.JSONDecodeError:
                    # If that fails, check if it looks like JSON structure
                    content_str = content_str.strip()
                if not content_str.startswith(('{', '[')):
                        return False
            except UnicodeDecodeError:
                return False
        elif file_ext in ["xml", "mxml", "xaml"]:
            # XML files should start with <?xml or contain XML-like content
            try:
                content_str = content_start.decode('utf-8', errors='ignore')
                content_str_lower = content_str.lower()
                if not ('<?xml' in content_str_lower or '<' in content_str_lower):
                    return False
            except Exception:
                return False
        elif file_ext in ["html", "htm", "jsp", "asp", "aspx"]:
            # HTML/markup files should contain HTML-like content
            try:
                content_str = content_start.decode('utf-8', errors='ignore')
                content_str_lower = content_str.lower()
                html_indicators = ['<html', '<head', '<body', '<div', '<p', '<h1', '<h2', '<!doctype', '<script', '<style']
                if not any(indicator in content_str_lower for indicator in html_indicators):
                    # Allow empty HTML files or files that just have text content
                    if len(content_str.strip()) == 0 or '<' not in content_str:
                        pass  # Allow these cases
            except Exception:
                return False
        else:
            # For all other file types (text-based: code files, config files, etc.)
            # Just verify they can be decoded as text (UTF-8 with fallback to latin-1)
            try:
                # Try UTF-8 first
                content_start.decode('utf-8')
            except UnicodeDecodeError:
                try:
                    # Fallback to latin-1 (which can decode any byte sequence)
                    content_start.decode('latin-1')
                except UnicodeDecodeError:
                    # If even latin-1 fails, something is very wrong
                    return False
        
        return True
        
    except Exception as e:
        # Log the validation error for debugging
        logger = logging.getLogger(__name__)
        logger.warning(f"File validation error for {filename}: {e}")
        return False


@upload_bp.route("/", methods=["POST"])
def upload_file_endpoint():
    """
    DEPRECATED: This endpoint is deprecated. Use /api/docs/upload instead.
    Handles file uploads, saves file, and creates DB record including project_id and tags.
    """
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.warning("API: Received POST /api/upload request - DEPRECATED ENDPOINT. Use /api/docs/upload instead.")

    if not db or not DBDocument:
        logger.error("API Error (POST /upload): DB or Document model unavailable.")
        return (
            jsonify({"error": "Database connection or Document model unavailable."}),
            500,
        )

    # --- Check for file part ---
    if "file" not in request.files:
        logger.warning("API Error (POST /upload): No 'file' part in request.files.")
        return error_response("No file part in the request", 400, "MISSING_FILE")

    file = request.files["file"]
    if file.filename == "":
        logger.warning("API Error (POST /upload): No file selected.")
        return error_response("No selected file", 400, "EMPTY_FILE")

    # Implement streaming file processing for large files
    file.seek(0)  # Reset file pointer to beginning
    
    # Check file size for streaming processing and validation
    file.seek(0, 2)  # Seek to end
    file_size = file.tell()
    file.seek(0)  # Reset to beginning
    
    # File size validation
    MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB limit
    if file_size > MAX_FILE_SIZE:
        return error_response(
            f"File too large. Maximum file size is {MAX_FILE_SIZE // (1024*1024)}MB, got {file_size // (1024*1024)}MB",
            status_code=413
        )
    
    if file_size == 0:
        return error_response("Empty file not allowed", status_code=400)
    
    # Use streaming for files larger than 10MB
    use_streaming = file_size > 10 * 1024 * 1024  # 10MB threshold
    if use_streaming:
        logger.info(f"Large file detected ({file_size} bytes) - using streaming processing")

    # SECURITY FIX: Validate file content matches extension
    if not validate_file_content(file, file.filename):
        logger.warning(f"API Error (POST /upload): File content validation failed for: {file.filename}")
        return jsonify({"error": "File content does not match file type or contains invalid data"}), 400

    if file and allowed_file(file.filename):
        # Check if this is an image and should be forwarded to master
        file_ext = os.path.splitext(file.filename)[1].lower().lstrip(".")
        is_image = file_ext in ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg']
        
        if is_image:
            try:
                from backend.utils.interconnector_image_utils import should_use_master_image_repository, forward_image_to_master
                
                if should_use_master_image_repository():
                    # Forward image to master server
                    file.seek(0)  # Reset to beginning
                    file_data = file.read()
                    success, master_path, error = forward_image_to_master(file_data, file.filename, "/api/uploads/")
                    
                    if success:
                        # Create local DB record pointing to master path
                        filename = secure_filename(file.filename)
                        db_relative_path = master_path if master_path else filename
                        
                        # Create or update document record
                        existing_doc = DBDocument.query.filter_by(path=db_relative_path).first()
                        if existing_doc:
                            existing_doc.filename = filename
                            existing_doc.type = file_ext
                            existing_doc.uploaded_at = datetime.now()
                            existing_doc.size = file_size
                            if project_id:
                                existing_doc.project_id = project_id
                            if tags_str:
                                existing_doc.tags = tags_str
                        else:
                            new_doc = DBDocument(
                                filename=filename,
                                path=db_relative_path,
                                type=file_ext,
                                size=file_size,
                                project_id=project_id,
                                tags=tags_str
                            )
                            db.session.add(new_doc)
                        
                        db.session.commit()
                        
                        result_doc = existing_doc if existing_doc else new_doc
                        logger.info(f"Image forwarded to master server: {master_path}")
                        return jsonify({
                            "success": True,
                            "message": "File uploaded to master server",
                            "data": result_doc.to_dict()
                        }), 201
                    else:
                        logger.warning(f"Failed to forward image to master: {error}")
                        # Fall through to local storage
            except Exception as e:
                logger.error(f"Error checking master image repository: {e}")
                # Fall through to local storage
        
        # Sanitize filename
        filename = secure_filename(file.filename)
        
        # SECURITY FIX: Additional path traversal protection
        if not filename or filename.startswith('.') or '/' in filename or '\\' in filename:
            logger.warning(f"API Error (POST /upload): Invalid filename after sanitization: {filename}")
            return jsonify({"error": "Invalid filename"}), 400
            
        # --- MODIFIED: Get file extension for 'type' field ---
        file_ext = (
            os.path.splitext(filename)[1].lower().lstrip(".")
        )  # Get extension without dot
        # --- END MODIFICATION ---

        # --- Get project_id and tags from form data ---
        project_id_str = request.form.get("project_id")
        tags_str = request.form.get(
            "tags"
        )  # Expecting comma-separated string or similar
        logger.debug(
            f"Upload form data - project_id: {project_id_str}, tags: {tags_str}"
        )

        project_id = None
        if project_id_str:
            try:
                project_id = int(project_id_str)
                # SECURITY FIX: Add bounds checking for project_id
                if project_id < 1 or project_id > 2147483647:  # 32-bit signed int max
                    logger.warning(f"API Warning (POST /upload): Project ID out of valid range: {project_id}")
                    project_id = None
                # Optional: Validate if project_id actually exists in the Project table
                elif Project and not db.session.get(Project, project_id):
                    logger.warning(
                        f"API Warning (POST /upload): Project ID {project_id} not found in database."
                    )
                    project_id = None  # Set back to None if validation fails
            except ValueError:
                logger.warning(
                    f"API Warning (POST /upload): Invalid project_id '{project_id_str}'. Must be an integer."
                )
                project_id = None
            except SQLAlchemyError as e:
                logger.error(
                    f"Database error validating project ID {project_id_str}: {e}",
                    exc_info=True,
                )
                project_id = None
        # --- End Get project_id and tags ---

        try:
            upload_folder_path = current_app.config.get("UPLOAD_FOLDER")
            if not upload_folder_path:
                logger.error("CRITICAL: UPLOAD_FOLDER not found in Flask app config!")
                return (
                    jsonify(
                        {"error": "Server configuration error: Upload folder not set."}
                    ),
                    500,
                )

            os.makedirs(upload_folder_path, exist_ok=True)
            # --- MODIFIED: Store relative path in DB for portability ---
            # Save file with original secure filename
            save_path_abs = os.path.join(upload_folder_path, filename)
            
            # SECURITY FIX: Validate that the final path is within upload directory
            upload_folder_real = os.path.realpath(upload_folder_path)
            save_path_real = os.path.realpath(save_path_abs)
            if not save_path_real.startswith(upload_folder_real + os.sep):
                logger.error(f"SECURITY: Path traversal attempt detected: {save_path_real}")
                return jsonify({"error": "Invalid file path"}), 400
            
            # Store the filename itself as the relative path within the upload folder
            db_relative_path = filename
            # --- END MODIFICATION ---
            logger.info(f"DEBUG: Calculated absolute save_path: {save_path_abs}")
            logger.info(f"DEBUG: Storing relative path in DB: {db_relative_path}")

            # Save the file
            file.save(save_path_abs)
            logger.info(f"File saved to: {save_path_abs}")

            # --- Create or Update Database Record ---
            # Check if document with same path already exists
            existing_doc = DBDocument.query.filter_by(path=db_relative_path).first()
            
            if existing_doc:
                # Update existing document record instead of creating new one
                logger.info(f"Updating existing document record for path: {db_relative_path}")
                existing_doc.filename = filename
                existing_doc.type = file_ext
                existing_doc.uploaded_at = datetime.now()
                existing_doc.index_status = "INDEXING"  # Reset to INDEXING
                existing_doc.indexed_at = None  # Clear previous indexing timestamp
                existing_doc.error_message = None  # Clear any previous errors
                existing_doc.project_id = project_id
                existing_doc.tags = tags_str
                existing_doc.size = file_size  # Store file size for existing documents
                
                # RAG FIX: Update content for existing document too
                try:
                    if os.path.exists(save_path_abs) and os.path.getsize(save_path_abs) < 50 * 1024 * 1024:  # 50MB limit
                        with open(save_path_abs, 'r', encoding='utf-8', errors='ignore') as f:
                            file_content = f.read()
                        existing_doc.content = file_content
                        existing_doc.is_code_file = file_ext in ['.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.cpp', '.c', '.h']
                        logger.info(f"Updated file content for RAG access: {filename} ({len(file_content)} chars)")
                except Exception as content_error:
                    logger.warning(f"Could not update content for {filename}: {content_error}")
                
                new_doc = existing_doc  # For consistency with return response
            else:
                # Create new document record
                logger.info(f"Creating new document record for path: {db_relative_path}")
                new_doc = DBDocument(
                    filename=filename,
                    path=db_relative_path,  # Store relative path
                    type=file_ext,  # Store file extension type
                    uploaded_at=datetime.now(),  # Use the correct field name
                    index_status="INDEXING",  # Set to INDEXING instead of UPLOADED
                    project_id=project_id,
                    tags=tags_str,
                    size=file_size,  # Store file size for new documents
                    # indexed_at and error_message will be set later
                )
                db.session.add(new_doc)
            
            # RAG FIX: Store file content for immediate LLM access
            try:
                if os.path.exists(save_path_abs) and os.path.getsize(save_path_abs) < 50 * 1024 * 1024:  # 50MB limit
                    with open(save_path_abs, 'r', encoding='utf-8', errors='ignore') as f:
                        file_content = f.read()
                    new_doc.content = file_content
                    new_doc.is_code_file = file_ext in ['.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.cpp', '.c', '.h']
                    logger.info(f"Stored file content for RAG access: {filename} ({len(file_content)} chars)")
            except Exception as content_error:
                logger.warning(f"Could not store content for {filename}: {content_error}")
            
            db.session.commit()
            logger.info(
                f"DEBUG: Saved Document to DB: ID={new_doc.id}, Filename='{new_doc.filename}', Path='{new_doc.path}', ProjectID={new_doc.project_id}, Tags='{new_doc.tags}', Status='{new_doc.index_status}'"
            )

            # RAG FIX: Trigger auto-indexing for immediate LLM access
            try:
                from backend.api.indexing_api import trigger_document_indexing
                logger.info(f"Triggering auto-indexing for document {new_doc.id} after upload.")
                with current_app.test_request_context():
                    resp = trigger_document_indexing(new_doc.id)
                    logger.info(f"Auto-indexing response for doc {new_doc.id}: {resp[0].get_json() if hasattr(resp[0], 'get_json') else resp}")
            except Exception as e:
                logger.error(f"Failed to auto-trigger indexing for doc {new_doc.id}: {e}", exc_info=True)

            # Return success response including the new document ID and filename
            return (
                jsonify(
                    {
                        "message": "File uploaded successfully. Ready for indexing.",
                        "filename": filename,
                        "document_id": new_doc.id,
                        "project_id": new_doc.project_id,
                        "tags": new_doc.tags,
                    }
                ),
                200,
            )

        except SQLAlchemyError as e:
            # Rollback database transaction
            try:
                db.session.rollback()
            except Exception as rollback_err:
                logger.error(f"Failed to rollback transaction: {rollback_err}")
            
            # Clean up uploaded file on database failure
            try:
                if "save_path_abs" in locals() and os.path.exists(save_path_abs):
                    os.remove(save_path_abs)
                    logger.info(f"Cleaned up file {save_path_abs} after database error")
            except OSError as cleanup_err:
                logger.warning(f"Failed to clean up file {save_path_abs}: {cleanup_err}")
            
            logger.error(
                f"Database error saving document record for '{filename}': {e}",
                exc_info=True,
            )
            return (
                jsonify(
                    {"error": "Database error saving file record.", "details": str(e)}
                ),
                500,
            )
        except Exception as e:
            logger.error(
                f"Error saving file '{filename}' or creating DB record: {e}",
                exc_info=True,
            )
            try:
                db.session.rollback()
            except SQLAlchemyError as rollback_err:
                logger.error(f"Failed to rollback transaction: {rollback_err}")
                pass
            # Attempt cleanup if save path was determined
            try:
                if "save_path_abs" in locals() and os.path.exists(save_path_abs):
                    os.remove(save_path_abs)
            except OSError:
                pass
            return (
                jsonify(
                    {
                        "error": "Failed to save file or create record.",
                        "details": str(e),
                    }
                ),
                500,
            )

    elif file and not allowed_file(file.filename):
        logger.warning(
            f"API Error (POST /upload): File type not allowed: {file.filename}"
        )
        return jsonify({"error": "File type not allowed"}), 400
    else:
        logger.error("API Error (POST /upload): Unknown file upload error.")
        return jsonify({"error": "Unknown upload error"}), 500
