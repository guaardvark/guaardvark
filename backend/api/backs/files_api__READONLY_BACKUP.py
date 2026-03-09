# backend/api/files_api.py
# Version: Read-only file system access for LLM testing

import json
import logging
import os
import pathlib
from typing import Dict, List, Optional, Union
from urllib.parse import unquote

from flask import Blueprint, current_app, request
from werkzeug.exceptions import BadRequest, NotFound

from backend.utils.response_utils import error_response, success_response

files_bp = Blueprint("files", __name__, url_prefix="/api/files")
logger = logging.getLogger(__name__)

# Get project root dynamically
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _validate_path(relative_path: str) -> str:
    """
    Validate and sanitize a relative path to prevent directory traversal.
    
    Args:
        relative_path: The relative path from project root
        
    Returns:
        Absolute path within project root
        
    Raises:
        BadRequest: If path is invalid or attempts directory traversal
    """
    if not relative_path:
        return PROJECT_ROOT
    
    # Normalize the path
    normalized_path = os.path.normpath(relative_path)
    
    # Prevent directory traversal
    if normalized_path.startswith("..") or ".." in normalized_path:
        raise BadRequest("Directory traversal not allowed")
    
    # Construct absolute path
    absolute_path = os.path.abspath(os.path.join(PROJECT_ROOT, normalized_path))
    
    # Ensure path is within project root
    if not absolute_path.startswith(PROJECT_ROOT):
        raise BadRequest("Path outside project root not allowed")
    
    return absolute_path


def _get_file_info(file_path: str) -> Dict:
    """Get file information for API response."""
    stat = os.stat(file_path)
    return {
        "name": os.path.basename(file_path),
        "path": os.path.relpath(file_path, PROJECT_ROOT),
        "is_directory": os.path.isdir(file_path),
        "size": stat.st_size if os.path.isfile(file_path) else None,
        "modified": stat.st_mtime,
        "permissions": oct(stat.st_mode)[-3:],
    }


@files_bp.route("/browse", methods=["GET"])
def browse_directory():
    """Browse directory contents."""
    try:
        relative_path = request.args.get("path", "")
        absolute_path = _validate_path(relative_path)
        
        if not os.path.exists(absolute_path):
            raise NotFound(f"Path not found: {relative_path}")
        
        if not os.path.isdir(absolute_path):
            raise BadRequest(f"Path is not a directory: {relative_path}")
        
        # Get directory contents
        items = []
        try:
            for item in os.listdir(absolute_path):
                item_path = os.path.join(absolute_path, item)
                if os.path.exists(item_path):  # Handle broken symlinks
                    items.append(_get_file_info(item_path))
        except PermissionError:
            raise BadRequest(f"Permission denied accessing: {relative_path}")
        
        # Sort: directories first, then files alphabetically
        items.sort(key=lambda x: (not x["is_directory"], x["name"].lower()))
        
        response_data = {
            "current_path": relative_path,
            "absolute_path": absolute_path,
            "project_root": PROJECT_ROOT,
            "items": items,
            "total_items": len(items),
        }
        
        logger.info(f"Browsed directory: {relative_path} ({len(items)} items)")
        return success_response(response_data)
        
    except (BadRequest, NotFound) as e:
        logger.warning(f"Browse error: {e}")
        return error_response(str(e), status_code=400)
    except Exception as e:
        logger.error(f"Unexpected error browsing directory: {e}", exc_info=True)
        return error_response("Internal server error", status_code=500)


@files_bp.route("/read", methods=["GET"])
def read_file():
    """Read file contents safely."""
    try:
        relative_path = request.args.get("path", "")
        if not relative_path:
            raise BadRequest("Path parameter is required")
        
        absolute_path = _validate_path(relative_path)
        
        if not os.path.exists(absolute_path):
            raise NotFound(f"File not found: {relative_path}")
        
        if not os.path.isfile(absolute_path):
            raise BadRequest(f"Path is not a file: {relative_path}")
        
        # Check file size (limit to 1MB for safety)
        file_size = os.path.getsize(absolute_path)
        max_size = 1024 * 1024  # 1MB
        
        if file_size > max_size:
            raise BadRequest(f"File too large ({file_size} bytes). Maximum size: {max_size} bytes")
        
        # Determine file type and handle accordingly
        file_ext = os.path.splitext(absolute_path)[1].lower()
        
        # Text files
        text_extensions = {'.txt', '.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.css', 
                          '.json', '.xml', '.yaml', '.yml', '.md', '.rst', '.log', 
                          '.sh', '.bash', '.zsh', '.fish', '.ini', '.cfg', '.conf',
                          '.sql', '.sqlite', '.db', '.csv', '.tsv', '.toml', '.lock'}
        
        if file_ext in text_extensions or not file_ext:
            # Try to read as text
            try:
                with open(absolute_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                content_type = "text"
            except UnicodeDecodeError:
                # Try with different encoding
                try:
                    with open(absolute_path, 'r', encoding='latin-1') as f:
                        content = f.read()
                    content_type = "text"
                except Exception:
                    raise BadRequest(f"Cannot read file as text: {relative_path}")
        else:
            # Binary file
            content = f"[Binary file: {os.path.basename(absolute_path)}]"
            content_type = "binary"
        
        response_data = {
            "path": relative_path,
            "absolute_path": absolute_path,
            "content": content,
            "content_type": content_type,
            "size": file_size,
            "encoding": "utf-8" if content_type == "text" else None,
        }
        
        logger.info(f"Read file: {relative_path} ({file_size} bytes, {content_type})")
        return success_response(response_data)
        
    except (BadRequest, NotFound) as e:
        logger.warning(f"Read file error: {e}")
        return error_response(str(e), status_code=400)
    except Exception as e:
        logger.error(f"Unexpected error reading file: {e}", exc_info=True)
        return error_response("Internal server error", status_code=500)


@files_bp.route("/info", methods=["GET"])
def get_file_info():
    """Get detailed file information."""
    try:
        relative_path = request.args.get("path", "")
        if not relative_path:
            raise BadRequest("Path parameter is required")
        
        absolute_path = _validate_path(relative_path)
        
        if not os.path.exists(absolute_path):
            raise NotFound(f"Path not found: {relative_path}")
        
        file_info = _get_file_info(absolute_path)
        
        # Add additional information
        if os.path.isfile(absolute_path):
            file_info.update({
                "extension": os.path.splitext(absolute_path)[1],
                "mime_type": _guess_mime_type(absolute_path),
            })
        elif os.path.isdir(absolute_path):
            try:
                file_info["item_count"] = len(os.listdir(absolute_path))
            except PermissionError:
                file_info["item_count"] = "Permission denied"
        
        logger.info(f"File info: {relative_path}")
        return success_response(file_info)
        
    except (BadRequest, NotFound) as e:
        logger.warning(f"File info error: {e}")
        return error_response(str(e), status_code=400)
    except Exception as e:
        logger.error(f"Unexpected error getting file info: {e}", exc_info=True)
        return error_response("Internal server error", status_code=500)


def _guess_mime_type(file_path: str) -> str:
    """Guess MIME type based on file extension."""
    ext = os.path.splitext(file_path)[1].lower()
    
    mime_types = {
        '.py': 'text/x-python',
        '.js': 'application/javascript',
        '.jsx': 'text/jsx',
        '.ts': 'application/typescript',
        '.tsx': 'text/tsx',
        '.html': 'text/html',
        '.css': 'text/css',
        '.json': 'application/json',
        '.xml': 'application/xml',
        '.yaml': 'application/x-yaml',
        '.yml': 'application/x-yaml',
        '.md': 'text/markdown',
        '.txt': 'text/plain',
        '.log': 'text/plain',
        '.sh': 'application/x-sh',
        '.sql': 'application/sql',
        '.csv': 'text/csv',
        '.tsv': 'text/tab-separated-values',
    }
    
    return mime_types.get(ext, 'application/octet-stream')


@files_bp.route("/search", methods=["GET"])
def search_files():
    """Search for files by name pattern."""
    try:
        pattern = request.args.get("pattern", "")
        if not pattern:
            raise BadRequest("Pattern parameter is required")
        
        start_path = request.args.get("path", "")
        absolute_start_path = _validate_path(start_path)
        
        if not os.path.exists(absolute_start_path):
            raise NotFound(f"Start path not found: {start_path}")
        
        if not os.path.isdir(absolute_start_path):
            raise BadRequest(f"Start path is not a directory: {start_path}")
        
        # Simple pattern matching (can be enhanced)
        results = []
        max_results = 100  # Limit results
        
        for root, dirs, files in os.walk(absolute_start_path):
            if len(results) >= max_results:
                break
                
            # Check directories
            for dir_name in dirs:
                if pattern.lower() in dir_name.lower():
                    dir_path = os.path.join(root, dir_name)
                    results.append(_get_file_info(dir_path))
                    if len(results) >= max_results:
                        break
            
            # Check files
            for file_name in files:
                if len(results) >= max_results:
                    break
                if pattern.lower() in file_name.lower():
                    file_path = os.path.join(root, file_name)
                    results.append(_get_file_info(file_path))
        
        response_data = {
            "pattern": pattern,
            "start_path": start_path,
            "results": results,
            "total_found": len(results),
            "max_results": max_results,
        }
        
        logger.info(f"File search: '{pattern}' in '{start_path}' ({len(results)} results)")
        return success_response(response_data)
        
    except (BadRequest, NotFound) as e:
        logger.warning(f"File search error: {e}")
        return error_response(str(e), status_code=400)
    except Exception as e:
        logger.error(f"Unexpected error searching files: {e}", exc_info=True)
        return error_response("Internal server error", status_code=500)


@files_bp.route("/generate-revision", methods=["POST"])
def generate_file_revision():
    """Generate a revised version of a file using LLM."""
    try:
        data = request.get_json()
        if not data:
            raise BadRequest("Request body must be JSON")
        
        original_path = data.get("original_path")
        revision_instructions = data.get("instructions", "")
        llm_context = data.get("llm_context", "")
        
        if not original_path:
            raise BadRequest("original_path is required")
        
        if not revision_instructions:
            raise BadRequest("instructions are required")
        
        # Validate original file path
        original_absolute_path = _validate_path(original_path)
        
        if not os.path.exists(original_absolute_path):
            raise NotFound(f"Original file not found: {original_path}")
        
        if not os.path.isfile(original_absolute_path):
            raise BadRequest(f"Path is not a file: {original_path}")
        
        # Read original file content
        try:
            with open(original_absolute_path, 'r', encoding='utf-8') as f:
                original_content = f.read()
        except UnicodeDecodeError:
            raise BadRequest(f"Cannot read file as text: {original_path}")
        
        # Generate new filename with revision suffix
        original_name = os.path.basename(original_absolute_path)
        name_parts = os.path.splitext(original_name)
        revision_name = f"{name_parts[0]}-rev.{name_parts[1] if name_parts[1] else ''}"
        revision_path = os.path.join(os.path.dirname(original_absolute_path), revision_name)
        
        # Create prompt for LLM
        llm_prompt = f"""You are an expert code reviewer and developer. I have a file that needs to be revised based on the following instructions:

ORIGINAL FILE: {original_name}
ORIGINAL CONTENT:
```
{original_content}
```

INSTRUCTIONS FOR REVISION:
{revision_instructions}

ADDITIONAL CONTEXT:
{llm_context if llm_context else "No additional context provided."}

Please generate a revised version of this file that addresses the instructions while maintaining the original structure and functionality. The revision should be a complete, working version of the file.

REVISED FILE CONTENT:"""
        
        # Generate content using LLM directly
        try:
            from backend.utils import llm_service
            
            llm = current_app.config.get("LLAMA_INDEX_LLM")
            if not llm:
                return error_response("LLM not configured", status_code=503)
            
            # Generate the revised content
            generated_content = llm_service.run_llm_chat_prompt(
                llm_prompt,
                llm_instance=llm,
                messages=[
                    llm_service.ChatMessage(role=llm_service.MessageRole.SYSTEM, content="You are an expert code reviewer and developer. Generate complete, working code that addresses the user's requirements."),
                    llm_service.ChatMessage(role=llm_service.MessageRole.USER, content=llm_prompt),
                ]
            )
            
            if not generated_content or not generated_content.strip():
                return error_response("LLM failed to generate content", status_code=500)
            
            # Write the revised file directly to the same directory
            try:
                with open(revision_path, 'w', encoding='utf-8') as f:
                    f.write(generated_content)
                
                # Validate the file was written correctly
                if not os.path.exists(revision_path):
                    raise FileNotFoundError(f"Generated file was not created: {revision_path}")
                    
                file_size = os.path.getsize(revision_path)
                if file_size == 0:
                    raise ValueError("Generated file is empty")
                
                response_data = {
                    "message": "File revision generated successfully.",
                    "revision_path": os.path.relpath(revision_path, PROJECT_ROOT),
                    "revision_name": revision_name,
                    "file_size": file_size,
                    "original_file": original_path,
                    "content_length": len(generated_content)
                }
                
                logger.info(f"Generated file revision: {revision_path} ({file_size} bytes)")
                return success_response(response_data)
                
            except IOError as e:
                logger.error(f"File write error: {e}")
                return error_response(f"Failed to write revision file: {str(e)}", status_code=500)
                
        except Exception as e:
            logger.error(f"LLM generation error: {e}")
            return error_response(f"Failed to generate revision: {str(e)}", status_code=500)
        
    except (BadRequest, NotFound) as e:
        logger.warning(f"Generate revision error: {e}")
        return error_response(str(e), status_code=400)
    except Exception as e:
        logger.error(f"Unexpected error generating revision: {e}", exc_info=True)
        return error_response("Internal server error", status_code=500)


@files_bp.route("/health", methods=["GET"])
def files_health():
    """Health check for file system API."""
    try:
        # Test basic functionality
        test_path = _validate_path("")
        exists = os.path.exists(test_path)
        
        response_data = {
            "ready": exists,
            "project_root": PROJECT_ROOT,
            "project_root_exists": exists,
            "permissions": {
                "read": os.access(test_path, os.R_OK),
                "write": os.access(test_path, os.W_OK),
            }
        }
        
        return success_response(response_data)
        
    except Exception as e:
        logger.error(f"Files API health check failed: {e}", exc_info=True)
        return error_response("Files API unhealthy", status_code=500) 