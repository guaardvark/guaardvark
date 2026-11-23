# backend/api/file_operations_api.py
# File operations API for CodeEditorPage

import os
import json
import logging
from pathlib import Path
from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename
from backend.services.guarded_code_service import (
    GuardedCodeError,
    apply_exact_replacement,
    default_repo_root,
    is_codebase_locked,
    protected_file_reason,
    resolve_repo_path,
)

logger = logging.getLogger(__name__)

file_ops_bp = Blueprint("file_operations", __name__, url_prefix="/api/files")

# Allowed file extensions for security
ALLOWED_EXTENSIONS = {
    '.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.css', '.scss', '.sass',
    '.json', '.xml', '.yaml', '.yml', '.md', '.txt', '.csv', '.sql',
    '.java', '.cpp', '.c', '.h', '.hpp', '.go', '.rs', '.php', '.rb',
    '.sh', '.bash', '.zsh', '.fish', '.ps1', '.bat', '.cmd'
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
        file_path.relative_to(base_path)
        return True
    except (OSError, ValueError):
        return False

def get_project_root():
    """Get the project root directory"""
    return str(default_repo_root())

def _guard_mutation_path(path):
    """Shared guard for manual CodeEditorPage filesystem writes."""
    if is_codebase_locked():
        raise GuardedCodeError("Codebase is locked. Unlock it before saving code.", "CODEBASE_LOCKED", 423)
    resolved, relative_path = resolve_repo_path(path)
    reason = protected_file_reason(relative_path)
    if reason:
        raise GuardedCodeError(reason, "PROTECTED_FILE", 403)
    return resolved, relative_path

def _inbound_refusal(changes, subject, payload):
    """Ask the inbound guard about a code-editor file operation.

    Returns (refusal_response, verdict). The editor is a person typing, so only
    a block refuses here; holds are recorded for the review list. A blocked
    operation is kept with its payload, so approving it there carries it out.
    """
    from backend.services import inbound_guard_service as guard

    if not guard.is_on():
        return None, None
    try:
        verdict = guard.check_and_gate(changes, source="code_editor", subject=subject, payload=payload)
    except guard.InboundRefused as refused:
        body = {"error": str(refused), "code": refused.code, "scan_id": refused.scan_id}
        return (jsonify(body), 409 if refused.held else 403), None
    return None, verdict


def _inbound_landed(paths, subject, verdict):
    if verdict is not None:
        from backend.services import inbound_guard_service as guard

        guard.landed(paths, source="code_editor", subject=subject, verdict=verdict)


@file_ops_bp.route("/read", methods=["POST"])
def read_file():
    """Read file content"""
    try:
        data = request.get_json()
        if not data or 'filePath' not in data:
            return jsonify({"error": "filePath is required"}), 400

        try:
            file_path, _relative_path = resolve_repo_path(data['filePath'])
        except GuardedCodeError as e:
            return jsonify({"error": str(e), "code": e.code}), e.status_code

        if not os.path.exists(file_path):
            return jsonify({"error": "File not found"}), 404

        if not is_allowed_file(file_path):
            return jsonify({"error": "File type not allowed"}), 403

        # Check file size
        file_size = os.path.getsize(file_path)
        if file_size > MAX_FILE_SIZE:
            return jsonify({"error": "File too large"}), 413

        # Read file content
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Determine language from extension
        ext = Path(file_path).suffix.lower()
        language_map = {
            '.py': 'python',
            '.js': 'javascript',
            '.jsx': 'javascript',
            '.ts': 'typescript',
            '.tsx': 'typescript',
            '.html': 'html',
            '.css': 'css',
            '.scss': 'scss',
            '.sass': 'sass',
            '.json': 'json',
            '.xml': 'xml',
            '.yaml': 'yaml',
            '.yml': 'yaml',
            '.md': 'markdown',
            '.txt': 'text',
            '.csv': 'csv',
            '.sql': 'sql',
            '.java': 'java',
            '.cpp': 'cpp',
            '.c': 'c',
            '.h': 'c',
            '.hpp': 'cpp',
            '.go': 'go',
            '.rs': 'rust',
            '.php': 'php',
            '.rb': 'ruby',
            '.sh': 'shell',
            '.bash': 'shell',
            '.zsh': 'shell',
            '.fish': 'shell',
            '.ps1': 'powershell',
            '.bat': 'batch',
            '.cmd': 'batch'
        }
        language = language_map.get(ext, 'text')

        return jsonify({
            "success": True,
            "content": content,
            "filePath": file_path,
            "size": file_size,
            "lastModified": os.path.getmtime(file_path),
            "language": language
        })

    except Exception as e:
        logger.error(f"Error reading file: {e}")
        return jsonify({"error": str(e)}), 500

@file_ops_bp.route("/write", methods=["POST"])
def write_file():
    """Write file content"""
    try:
        data = request.get_json()
        if not data or 'filePath' not in data or 'content' not in data:
            return jsonify({"error": "filePath and content are required"}), 400

        file_path = data['filePath']
        content = data['content']

        if not is_allowed_file(file_path):
            return jsonify({"error": "File type not allowed"}), 403

        # Check content size
        if len(content) > MAX_FILE_SIZE:
            return jsonify({"error": "Content too large"}), 413

        try:
            resolved_path, _relative_path = _guard_mutation_path(file_path)
            if not resolved_path.exists():
                return jsonify({"error": "File not found"}), 404
            old_content = resolved_path.read_text(encoding='utf-8')
            apply_exact_replacement(str(resolved_path), old_content, content, origin="code_editor",
                                    allow_reindent=False)
        except GuardedCodeError as e:
            return jsonify({"error": str(e), "code": e.code}), e.status_code

        return jsonify({
            "success": True,
            "filePath": str(resolved_path),
            "size": len(content),
            "message": "File saved successfully"
        })

    except Exception as e:
        logger.error(f"Error writing file: {e}")
        return jsonify({"error": str(e)}), 500

@file_ops_bp.route("/create", methods=["POST"])
def create_file():
    """Create new file"""
    try:
        data = request.get_json()
        if not data or 'filePath' not in data:
            return jsonify({"error": "filePath is required"}), 400

        file_path = data['filePath']
        content = data.get('content', '')

        if not is_allowed_file(file_path):
            return jsonify({"error": "File type not allowed"}), 403

        try:
            resolved_path, _relative_path = _guard_mutation_path(file_path)
        except GuardedCodeError as e:
            return jsonify({"error": str(e), "code": e.code}), e.status_code

        if resolved_path.exists():
            return jsonify({"error": "File already exists"}), 409

        from backend.services import inbound_guard_service as guard

        subject = f"create {_relative_path}"
        refusal, verdict = (None, None)
        if guard.is_on():
            refusal, verdict = _inbound_refusal(
                [guard.change_for_file(resolved_path, None, content)], subject,
                {"kind": "write_file", "path": _relative_path, "content": content},
            )
        if refusal:
            return refusal

        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(resolved_path), exist_ok=True)

        # Create file
        with open(resolved_path, 'w', encoding='utf-8') as f:
            f.write(content)
        _inbound_landed([str(resolved_path)], subject, verdict)

        return jsonify({
            "success": True,
            "filePath": str(resolved_path),
            "message": "File created successfully"
        })

    except Exception as e:
        logger.error(f"Error creating file: {e}")
        return jsonify({"error": str(e)}), 500

@file_ops_bp.route("/delete", methods=["POST"])
def delete_file():
    """Delete file"""
    try:
        data = request.get_json()
        if not data or 'filePath' not in data:
            return jsonify({"error": "filePath is required"}), 400

        file_path = data['filePath']
        try:
            resolved_path, _relative_path = _guard_mutation_path(file_path)
        except GuardedCodeError as e:
            return jsonify({"error": str(e), "code": e.code}), e.status_code

        if not os.path.exists(resolved_path):
            return jsonify({"error": "File not found"}), 404

        from backend.services import inbound_guard_service as guard

        subject = f"delete {_relative_path}"
        refusal, verdict = (None, None)
        if guard.is_on():
            old_text = None
            if is_allowed_file(resolved_path) and os.path.getsize(resolved_path) <= MAX_FILE_SIZE:
                old_text = Path(resolved_path).read_text(encoding="utf-8", errors="replace")
            refusal, verdict = _inbound_refusal(
                [guard.change_for_file(resolved_path, old_text or "", None)], subject,
                {"kind": "delete_file", "path": _relative_path},
            )
        if refusal:
            return refusal

        # Delete file
        os.remove(resolved_path)
        _inbound_landed([str(resolved_path)], subject, verdict)

        return jsonify({
            "success": True,
            "message": "File deleted successfully"
        })

    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        return jsonify({"error": str(e)}), 500

@file_ops_bp.route("/list", methods=["POST"])
def list_directory():
    """List directory contents"""
    try:
        data = request.get_json()
        if not data or 'dirPath' not in data:
            return jsonify({"error": "dirPath is required"}), 400

        try:
            dir_path, _relative_path = resolve_repo_path(data['dirPath'])
        except GuardedCodeError as e:
            return jsonify({"error": str(e), "code": e.code}), e.status_code

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
                    files.append({
                        "name": item,
                        "path": item_path,
                        "size": os.path.getsize(item_path),
                        "lastModified": os.path.getmtime(item_path)
                    })
            elif os.path.isdir(item_path):
                directories.append({
                    "name": item,
                    "path": item_path,
                    "lastModified": os.path.getmtime(item_path)
                })

        return jsonify({
            "success": True,
            "files": files,
            "directories": directories,
            "path": dir_path
        })

    except Exception as e:
        logger.error(f"Error listing directory: {e}")
        return jsonify({"error": str(e)}), 500

@file_ops_bp.route("/mkdir", methods=["POST"])
def create_directory():
    """Create directory"""
    try:
        data = request.get_json()
        if not data or 'dirPath' not in data:
            return jsonify({"error": "dirPath is required"}), 400

        dir_path = data['dirPath']
        try:
            resolved_path, _relative_path = _guard_mutation_path(dir_path)
        except GuardedCodeError as e:
            return jsonify({"error": str(e), "code": e.code}), e.status_code

        if os.path.exists(resolved_path):
            return jsonify({"error": "Directory already exists"}), 409

        # Create directory
        os.makedirs(resolved_path, exist_ok=True)

        return jsonify({
            "success": True,
            "message": "Directory created successfully"
        })

    except Exception as e:
        logger.error(f"Error creating directory: {e}")
        return jsonify({"error": str(e)}), 500

@file_ops_bp.route("/rename", methods=["POST"])
def rename_file():
    """Rename file or directory"""
    try:
        data = request.get_json()
        if not data or 'oldPath' not in data or 'newPath' not in data:
            return jsonify({"error": "oldPath and newPath are required"}), 400

        old_path = data['oldPath']
        new_path = data['newPath']
        try:
            resolved_old, _old_rel = _guard_mutation_path(old_path)
            resolved_new, _new_rel = _guard_mutation_path(new_path)
        except GuardedCodeError as e:
            return jsonify({"error": str(e), "code": e.code}), e.status_code

        if not os.path.exists(resolved_old):
            return jsonify({"error": "File or directory not found"}), 404

        if os.path.exists(resolved_new):
            return jsonify({"error": "Target already exists"}), 409

        from backend.services import inbound_guard_service as guard

        subject = f"rename {_old_rel} -> {_new_rel}"
        refusal, verdict = (None, None)
        if guard.is_on():
            moved = guard.engine().Change(path=_new_rel, status="R", old_path=_old_rel, new_mode="100644")
            refusal, verdict = _inbound_refusal(
                [moved], subject, {"kind": "rename_file", "path": _old_rel, "new_path": _new_rel},
            )
        if refusal:
            return refusal

        # Rename file or directory
        os.rename(resolved_old, resolved_new)
        _inbound_landed([str(resolved_old), str(resolved_new)], subject, verdict)

        return jsonify({
            "success": True,
            "oldPath": str(resolved_old),
            "newPath": str(resolved_new),
            "message": "File renamed successfully"
        })

    except Exception as e:
        logger.error(f"Error renaming file: {e}")
        return jsonify({"error": str(e)}), 500
