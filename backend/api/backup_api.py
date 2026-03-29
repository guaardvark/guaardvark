# backend/api/backup_api.py
"""API endpoints for creating and restoring backups."""

from __future__ import annotations

import os

from flask import Blueprint, jsonify, request, send_file

from backend import config
from backend.services import backup_service

backup_bp = Blueprint("backup_bp", __name__, url_prefix="/api/backups")


@backup_bp.route("/create", methods=["POST"])
def create_backup_endpoint():
    payload = request.get_json() or {}
    backup_type = payload.get("type", "full")
    components = payload.get("components")
    name = payload.get("name")  # Extract custom backup name
    include_plugins = payload.get("include_plugins", False)

    try:
        if backup_type == "data":
            zip_path = backup_service.create_data_backup(components=components, name=name, include_plugins=include_plugins)
        elif backup_type in ("full", "full_system"):
            zip_path = backup_service.create_full_backup(name=name)
        elif backup_type in ("code_release", "code_only"):
            zip_path = backup_service.create_code_release(name=name)
        else:
            # Backward compat: route old types through wrapper
            zip_path = backup_service.create_backup(backup_type, components, name=name)
    except Exception as e:  # pragma: no cover - unexpected errors
        return jsonify({"error": str(e)}), 500
    return jsonify({"file": os.path.basename(zip_path)})


@backup_bp.route("/restore", methods=["POST"])
def restore_backup_endpoint():
    if "file" in request.files:
        file_obj = request.files["file"]
        
        # SECURITY FIX: Validate uploaded file
        if not file_obj.filename or not file_obj.filename.endswith('.zip'):
            return jsonify({"error": "Only ZIP files are allowed"}), 400
        
        tmp_path = os.path.join(config.BACKUP_DIR, "_upload.zip")
        
        # SECURITY FIX: Ensure backup directory exists and is secure
        backup_dir_real = os.path.realpath(config.BACKUP_DIR)
        tmp_path_real = os.path.realpath(tmp_path)
        
        if not tmp_path_real.startswith(backup_dir_real + os.sep):
            return jsonify({"error": "Invalid backup path"}), 400
        
        os.makedirs(config.BACKUP_DIR, exist_ok=True)
        file_obj.save(tmp_path)
        path = tmp_path
    else:
        data = request.get_json(silent=True) or {}
        name = data.get("filename")
        if not name:
            return jsonify({"error": "file required"}), 400
        
        # SECURITY FIX: Validate filename to prevent directory traversal
        if not isinstance(name, str) or '..' in name or '/' in name or '\\' in name:
            return jsonify({"error": "Invalid filename"}), 400
        
        # Only allow .zip files
        if not name.endswith('.zip'):
            return jsonify({"error": "Only ZIP files are allowed"}), 400
        
        path = os.path.join(config.BACKUP_DIR, name)
        
        # SECURITY FIX: Validate final path is within backup directory
        backup_dir_real = os.path.realpath(config.BACKUP_DIR)
        path_real = os.path.realpath(path)
        
        if not path_real.startswith(backup_dir_real + os.sep):
            return jsonify({"error": "Invalid backup file path"}), 400
        
        # Check if file exists
        if not os.path.exists(path):
            return jsonify({"error": "Backup file not found"}), 404
    
    try:
        summary = backup_service.restore_backup(path)
    except Exception as e:  # pragma: no cover
        return jsonify({"error": str(e)}), 500
    finally:
        if "file_obj" in locals():
            try:
                os.remove(path)
            except OSError:
                pass
    return jsonify(summary)


@backup_bp.route("", methods=["GET"])
def list_backups_endpoint():
    return jsonify({"backups": backup_service.list_backups()})


@backup_bp.route("/<filename>/download", methods=["GET"])
def download_backup_endpoint(filename: str):
    if not filename or '..' in filename or '/' in filename or '\\' in filename:
        return jsonify({"error": "Invalid filename"}), 400
    if not filename.endswith('.zip'):
        return jsonify({"error": "Only ZIP files can be downloaded"}), 400
    path = os.path.join(config.BACKUP_DIR, filename)
    backup_dir_real = os.path.realpath(config.BACKUP_DIR)
    path_real = os.path.realpath(path)
    if not path_real.startswith(backup_dir_real + os.sep) and path_real != backup_dir_real:
        return jsonify({"error": "Invalid backup file path"}), 400
    if not os.path.exists(path):
        return jsonify({"error": "Backup file not found"}), 404
    return send_file(path, as_attachment=True, download_name=filename)


@backup_bp.route("/<filename>", methods=["DELETE"])
def delete_backup_endpoint(filename: str):
    if backup_service.delete_backup(filename):
        return jsonify({"deleted": filename})
    return jsonify({"error": "not found"}), 404
