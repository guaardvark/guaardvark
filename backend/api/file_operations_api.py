# backend/api/file_operations_api.py
# File operations API for CodeEditorPage

import os
import json
import logging
from pathlib import Path
from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

file_ops_bp = Blueprint("file_operations", __name__, url_prefix="/api/files")

# Allowed file extensions for security
ALLOWED_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".html",
    ".css",
    ".scss",
    ".sass",
    ".json",
    ".xml",
    ".yaml",
    ".yml",
    ".md",
    ".txt",
    ".csv",
    ".sql",
    ".java",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".go",
    ".rs",
    ".php",
    ".rb",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".ps1",
    ".bat",
    ".cmd",
}

# Maximum file size (10MB)
MAX_FILE_SIZE = 10 * 1024 * 1024


def is_allowed_file(filename):
    """Check if file extension is allowed"""
    if not filename:
        return False
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def is_safe_path(base_path, file_path):
    """Check if file path is safe (no directory traversal)"""
    try:
        base_path = Path(base_path).resolve()
        file_path = Path(file_path).resolve()
        return base_path in file_path.parents or base_path == file_path.parent
    except (OSError, ValueError):
        return False


def get_project_root():
    """Get the project root directory"""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".."
    )


@file_ops_bp.route("/read", methods=["POST"])
def read_file():
    """Read file content"""
    try:
        data = request.get_json()
        if not data or "filePath" not in data:
            return jsonify({"error": "filePath is required"}), 400

        file_path = data["filePath"]

        # Security check
        if not is_safe_path(get_project_root(), file_path):
            return jsonify({"error": "Access denied: Invalid file path"}), 403

        if not os.path.exists(file_path):
            return jsonify({"error": "File not found"}), 404

        if not is_allowed_file(file_path):
            return jsonify({"error": "File type not allowed"}), 403

        # Check file size
        file_size = os.path.getsize(file_path)
        if file_size > MAX_FILE_SIZE:
            return jsonify({"error": "File too large"}), 413

        # Read file content
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Determine language from extension
        ext = Path(file_path).suffix.lower()
        language_map = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".html": "html",
            ".css": "css",
            ".scss": "scss",
            ".sass": "sass",
            ".json": "json",
            ".xml": "xml",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".md": "markdown",
            ".txt": "text",
            ".csv": "csv",
            ".sql": "sql",
            ".java": "java",
            ".cpp": "cpp",
            ".c": "c",
            ".h": "c",
            ".hpp": "cpp",
            ".go": "go",
            ".rs": "rust",
            ".php": "php",
            ".rb": "ruby",
            ".sh": "shell",
            ".bash": "shell",
            ".zsh": "shell",
            ".fish": "shell",
            ".ps1": "powershell",
            ".bat": "batch",
            ".cmd": "batch",
        }
        language = language_map.get(ext, "text")

        return jsonify(
            {
                "success": True,
                "content": content,
                "filePath": file_path,
                "size": file_size,
                "lastModified": os.path.getmtime(file_path),
                "language": language,
            }
        )

    except Exception as e:
        logger.error(f"Error reading file: {e}")
        return jsonify({"error": str(e)}), 500


@file_ops_bp.route("/write", methods=["POST"])
def write_file():
    """Write file content"""
    try:
        data = request.get_json()
        if not data or "filePath" not in data or "content" not in data:
            return jsonify({"error": "filePath and content are required"}), 400

        file_path = data["filePath"]
        content = data["content"]

        # Security check
        if not is_safe_path(get_project_root(), file_path):
            return jsonify({"error": "Access denied: Invalid file path"}), 403

        if not is_allowed_file(file_path):
            return jsonify({"error": "File type not allowed"}), 403

        # Check content size
        if len(content) > MAX_FILE_SIZE:
            return jsonify({"error": "Content too large"}), 413

        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # Write file content
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return jsonify(
            {
                "success": True,
                "filePath": file_path,
                "size": len(content),
                "message": "File saved successfully",
            }
        )

    except Exception as e:
        logger.error(f"Error writing file: {e}")
        return jsonify({"error": str(e)}), 500


@file_ops_bp.route("/create", methods=["POST"])
def create_file():
    """Create new file"""
    try:
        data = request.get_json()
        if not data or "filePath" not in data:
            return jsonify({"error": "filePath is required"}), 400

        file_path = data["filePath"]
        content = data.get("content", "")

        # Security check
        if not is_safe_path(get_project_root(), file_path):
            return jsonify({"error": "Access denied: Invalid file path"}), 403

        if not is_allowed_file(file_path):
            return jsonify({"error": "File type not allowed"}), 403

        if os.path.exists(file_path):
            return jsonify({"error": "File already exists"}), 409

        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # Create file
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return jsonify(
            {
                "success": True,
                "filePath": file_path,
                "message": "File created successfully",
            }
        )

    except Exception as e:
        logger.error(f"Error creating file: {e}")
        return jsonify({"error": str(e)}), 500


@file_ops_bp.route("/delete", methods=["POST"])
def delete_file():
    """Delete file"""
    try:
        data = request.get_json()
        if not data or "filePath" not in data:
            return jsonify({"error": "filePath is required"}), 400

        file_path = data["filePath"]

        # Security check
        if not is_safe_path(get_project_root(), file_path):
            return jsonify({"error": "Access denied: Invalid file path"}), 403

        if not os.path.exists(file_path):
            return jsonify({"error": "File not found"}), 404

        # Delete file
        os.remove(file_path)

        return jsonify({"success": True, "message": "File deleted successfully"})

    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        return jsonify({"error": str(e)}), 500


@file_ops_bp.route("/list", methods=["POST"])
def list_directory():
    """List directory contents"""
    try:
        data = request.get_json()
        if not data or "dirPath" not in data:
            return jsonify({"error": "dirPath is required"}), 400

        dir_path = data["dirPath"]

        # Security check
        if not is_safe_path(get_project_root(), dir_path):
            return jsonify({"error": "Access denied: Invalid directory path"}), 403

        if not os.path.exists(dir_path) or not os.path.isdir(dir_path):
            return jsonify({"error": "Directory not found"}), 404

        # List directory contents
        items = os.listdir(dir_path)
        files = []
        directories = []

        for item in items:
            item_path = os.path.join(dir_path, item)
            if os.path.isfile(item_path):
                if is_allowed_file(item):
                    files.append(
                        {
                            "name": item,
                            "path": item_path,
                            "size": os.path.getsize(item_path),
                            "lastModified": os.path.getmtime(item_path),
                        }
                    )
            elif os.path.isdir(item_path):
                directories.append(
                    {
                        "name": item,
                        "path": item_path,
                        "lastModified": os.path.getmtime(item_path),
                    }
                )

        return jsonify(
            {
                "success": True,
                "files": files,
                "directories": directories,
                "path": dir_path,
            }
        )

    except Exception as e:
        logger.error(f"Error listing directory: {e}")
        return jsonify({"error": str(e)}), 500


@file_ops_bp.route("/mkdir", methods=["POST"])
def create_directory():
    """Create directory"""
    try:
        data = request.get_json()
        if not data or "dirPath" not in data:
            return jsonify({"error": "dirPath is required"}), 400

        dir_path = data["dirPath"]

        # Security check
        if not is_safe_path(get_project_root(), dir_path):
            return jsonify({"error": "Access denied: Invalid directory path"}), 403

        if os.path.exists(dir_path):
            return jsonify({"error": "Directory already exists"}), 409

        # Create directory
        os.makedirs(dir_path, exist_ok=True)

        return jsonify({"success": True, "message": "Directory created successfully"})

    except Exception as e:
        logger.error(f"Error creating directory: {e}")
        return jsonify({"error": str(e)}), 500


@file_ops_bp.route("/rename", methods=["POST"])
def rename_file():
    """Rename file or directory"""
    try:
        data = request.get_json()
        if not data or "oldPath" not in data or "newPath" not in data:
            return jsonify({"error": "oldPath and newPath are required"}), 400

        old_path = data["oldPath"]
        new_path = data["newPath"]

        # Security check
        if not is_safe_path(get_project_root(), old_path):
            return jsonify({"error": "Access denied: Invalid old path"}), 403
        if not is_safe_path(get_project_root(), new_path):
            return jsonify({"error": "Access denied: Invalid new path"}), 403

        if not os.path.exists(old_path):
            return jsonify({"error": "File or directory not found"}), 404

        if os.path.exists(new_path):
            return jsonify({"error": "Target already exists"}), 409

        # Rename file or directory
        os.rename(old_path, new_path)

        return jsonify(
            {
                "success": True,
                "oldPath": old_path,
                "newPath": new_path,
                "message": "File renamed successfully",
            }
        )

    except Exception as e:
        logger.error(f"Error renaming file: {e}")
        return jsonify({"error": str(e)}), 500
