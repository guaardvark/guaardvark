# backend/api/files_api.py
# Version 1.0: True file manager API with folder and file operations
# Handles physical file system operations AND database records

import datetime
import json
import logging
import os
import re
import shutil
from typing import Optional
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.utils import secure_filename

from sqlalchemy import func as sa_func

from backend.models import Folder, Document as DBDocument, Client, Project, Website, db
from backend.utils.db_utils import ensure_db_session_cleanup
from backend.utils.response_utils import success_response, error_response

files_bp = Blueprint("files", __name__, url_prefix="/api/files")
logger = logging.getLogger(__name__)


def get_upload_base_path() -> Path:
    """Get the base upload directory path"""
    upload_folder = current_app.config.get("UPLOAD_FOLDER")
    if not upload_folder:
        raise ValueError("UPLOAD_FOLDER not configured")
    return Path(upload_folder)


def ensure_path_is_safe(path: str) -> bool:
    """Ensure path doesn't contain directory traversal attacks
    
    Note: This function validates database folder paths (virtual paths like '/002')
    which may start with '/' but are not filesystem absolute paths.
    """
    if not path or path == "/":
        return True

    # BUG FIX #2: Comprehensive path traversal prevention
    import urllib.parse

    # Decode URL encoding to catch %2e%2e and similar attacks
    decoded_path = urllib.parse.unquote(path)

    # Reject any form of parent directory reference
    if ".." in decoded_path or ".." in path:
        logger.warning(f"Rejected path with parent reference: {path}")
        return False

    # For database paths (virtual folder paths), paths starting with '/' are valid
    # They represent root-level folders in the database, not filesystem absolute paths
    # Examples: '/002', '/My Projects', '/folder/subfolder'
    
    # If path starts with '/', it's a database path - validate differently
    if path.startswith('/') and path != '/':
        # Database path validation: check for dangerous characters
        # Allow alphanumeric, spaces, hyphens, underscores, forward slashes, and dots
        # Reject control characters and filesystem-unsafe characters
        if re.search(r'[<>:"|?*\x00-\x1f]', decoded_path):
            logger.warning(f"Rejected path with invalid characters: {path}")
            return False
        
        # Additional check: ensure it doesn't look like an absolute filesystem path
        # Real filesystem paths on Unix start with /home, /usr, /var, /tmp, /opt, etc.
        # Or on Windows: C:\, D:\, etc.
        path_after_slash = decoded_path.lstrip('/')
        if path_after_slash:
            # Check for Windows drive letters
            if ':' in path_after_slash and len(path_after_slash) > 1 and path_after_slash[1] == ':':
                logger.warning(f"Rejected Windows absolute path: {path}")
                return False
            
            # Check for common Unix filesystem roots (but allow short names like '002')
            # Only reject if it's clearly a filesystem path
            unix_roots = ['home', 'usr', 'var', 'tmp', 'opt', 'etc', 'bin', 'sbin', 'root', 'sys', 'proc', 'dev']
            first_segment = path_after_slash.split('/')[0].lower()
            if first_segment in unix_roots and len(path_after_slash) > len(first_segment) + 1:
                logger.warning(f"Rejected Unix absolute path: {path}")
                return False
        
        # Database path is valid
        return True
    
    # For paths that don't start with '/', validate as filesystem relative paths
    # Remove leading '/' for filesystem validation if present
    path_for_validation = path.lstrip('/')
    decoded_for_validation = decoded_path.lstrip('/')
    
    # Reject actual absolute filesystem paths
    if decoded_for_validation and (os.path.isabs(decoded_for_validation) or os.path.isabs(path_for_validation)):
        logger.warning(f"Rejected absolute filesystem path: {path}")
        return False

    # Validate the final resolved path stays within base
    try:
        base = get_upload_base_path()
        final_path = os.path.normpath(os.path.join(str(base), path_for_validation))
        base_str = str(base.resolve())
        final_str = str(Path(final_path).resolve())

        if not final_str.startswith(base_str):
            logger.warning(f"Rejected path outside base: {path} -> {final_str} not in {base_str}")
            return False

        return True
    except Exception as e:
        logger.error(f"Path validation error for {path}: {e}")
        return False


def get_physical_path(relative_path: str) -> Path:
    """Convert relative path to absolute physical path"""
    base = get_upload_base_path()
    if not relative_path or relative_path == "/":
        return base
    return base / relative_path.lstrip("/")


# ============================================================================
# FOLDER OPERATIONS
# ============================================================================

def _batch_subfolder_counts(parent_ids):
    """Get subfolder counts for multiple parent folders in a single query"""
    if not parent_ids:
        return {}
    rows = db.session.query(
        Folder.parent_id, sa_func.count(Folder.id)
    ).filter(Folder.parent_id.in_(parent_ids)).group_by(Folder.parent_id).all()
    return {pid: cnt for pid, cnt in rows}


def _batch_document_counts(folder_ids):
    """Get document counts for multiple folders in a single query"""
    if not folder_ids:
        return {}
    rows = db.session.query(
        DBDocument.folder_id, sa_func.count(DBDocument.id)
    ).filter(DBDocument.folder_id.in_(folder_ids)).group_by(DBDocument.folder_id).all()
    return {fid: cnt for fid, cnt in rows}


def _batch_indexed_document_counts(folder_ids):
    """Get count of INDEXED documents per folder in a single query"""
    if not folder_ids:
        return {}
    rows = db.session.query(
        DBDocument.folder_id, sa_func.count(DBDocument.id)
    ).filter(
        DBDocument.folder_id.in_(folder_ids),
        DBDocument.index_status == 'INDEXED'
    ).group_by(DBDocument.folder_id).all()
    return {fid: cnt for fid, cnt in rows}


# Sort column mapping for server-side sorting
_FOLDER_SORT_COLS = {
    "name": Folder.name,
    "date": Folder.updated_at,
}
_DOC_SORT_COLS = {
    "name": DBDocument.filename,
    "date": DBDocument.uploaded_at,
    "size": DBDocument.size,
}


@files_bp.route("/browse", methods=["GET"])
@ensure_db_session_cleanup
def browse_folder():
    """
    GET /api/files/browse?path=/My Projects&fields=light&offset=0&limit=200&sort_by=name&sort_dir=asc
    List contents of a folder (both subfolders and files)
    """
    logger.info("API: Browse folder request")

    try:
        folder_path = request.args.get("path", "").strip()
        fields = request.args.get("fields", "full")
        offset = max(0, int(request.args.get("offset", 0)))
        limit = max(0, int(request.args.get("limit", 0)))  # 0 = unlimited (backward compat)
        sort_by = request.args.get("sort_by", "name")
        sort_dir = request.args.get("sort_dir", "asc")
        use_light = fields == "light"

        # Safety check
        if not ensure_path_is_safe(folder_path):
            return error_response("Invalid path", 400, "INVALID_PATH")

        # Resolve folder queries
        if not folder_path or folder_path == "/":
            folder_q = Folder.query.filter_by(parent_id=None)
            doc_q = DBDocument.query.filter_by(folder_id=None)
            parent_id = None
            folder_id_val = None
            breadcrumbs = [{"name": "Root", "path": "/"}]
            resp_path = "/"
        else:
            # Try exact path first, then without leading slash (folder paths are stored without it)
            folder = Folder.query.filter_by(path=folder_path).first()
            if not folder and folder_path.startswith("/"):
                folder = Folder.query.filter_by(path=folder_path.lstrip("/")).first()
            if not folder:
                return error_response("Folder not found", 404, "FOLDER_NOT_FOUND")
            folder_q = folder.subfolders
            doc_q = folder.documents
            parent_id = folder.parent_id
            folder_id_val = folder.id
            # Build breadcrumbs
            breadcrumbs = []
            current = folder
            while current:
                breadcrumbs.insert(0, {"name": current.name, "path": current.path})
                current = current.parent
            breadcrumbs.insert(0, {"name": "Root", "path": "/"})
            resp_path = folder.path

        # Get total counts before pagination
        total_folders = folder_q.count()
        total_documents = doc_q.count()

        # Apply server-side sorting
        folder_sort_col = _FOLDER_SORT_COLS.get(sort_by, Folder.name)
        doc_sort_col = _DOC_SORT_COLS.get(sort_by, DBDocument.filename)

        if sort_dir == "desc":
            folder_q = folder_q.order_by(folder_sort_col.desc())
            doc_q = doc_q.order_by(doc_sort_col.desc())
        else:
            folder_q = folder_q.order_by(folder_sort_col.asc())
            doc_q = doc_q.order_by(doc_sort_col.asc())

        # Combined pagination: folders come first, then documents
        if limit > 0:
            if offset < total_folders:
                # Start within folders
                folders = folder_q.offset(offset).limit(limit).all()
                remaining = limit - len(folders)
                documents = doc_q.limit(remaining).all() if remaining > 0 else []
            else:
                # Start within documents
                folders = []
                doc_offset = offset - total_folders
                documents = doc_q.offset(doc_offset).limit(limit).all()
            has_more = (offset + limit) < (total_folders + total_documents)
        else:
            # No limit - return all (backward compat)
            folders = folder_q.all()
            documents = doc_q.all()
            has_more = False

        # Serialize
        if use_light:
            folder_dicts = [f.to_dict_light() for f in folders]
            doc_dicts = [d.to_dict_light() for d in documents]
            # Inject batch counts
            folder_ids = [f.id for f in folders]
            sub_counts = _batch_subfolder_counts(folder_ids)
            doc_counts = _batch_document_counts(folder_ids)
            indexed_doc_counts = _batch_indexed_document_counts(folder_ids)
            for fd, f in zip(folder_dicts, folders):
                fd["subfolder_count"] = sub_counts.get(f.id, 0)
                fd["document_count"] = doc_counts.get(f.id, 0)
                fd["indexed_document_count"] = indexed_doc_counts.get(f.id, 0)
        else:
            folder_dicts = [f.to_dict() for f in folders]
            doc_dicts = [d.to_dict() for d in documents]

        result = {
            "path": resp_path,
            "folders": folder_dicts,
            "documents": doc_dicts,
            "parent_id": parent_id,
            "breadcrumbs": breadcrumbs,
            "total_folders": total_folders,
            "total_documents": total_documents,
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        }
        if folder_id_val is not None:
            result["folder_id"] = folder_id_val
            # Include current folder's full properties for inheritance checks
            result["current_folder"] = folder.to_dict_light() if use_light else folder.to_dict()

        return success_response(result)

    except Exception as e:
        logger.error(f"Error browsing folder: {e}", exc_info=True)
        return error_response("Failed to browse folder", 500, "BROWSE_ERROR")


@files_bp.route("/folder", methods=["POST"])
@ensure_db_session_cleanup
def create_folder():
    """POST /api/files/folder - Create new folder"""
    logger.info("API: Create folder request")
    try:
        data = request.get_json()
        if not data or "name" not in data:
            return error_response("Folder name required", 400, "MISSING_NAME")
        folder_name = secure_filename(data["name"].strip())
        if not folder_name:
            return error_response("Invalid folder name", 400, "INVALID_NAME")
        parent_path = data.get("parent_path", "").strip()
        if not ensure_path_is_safe(parent_path):
            return error_response("Invalid parent path", 400, "INVALID_PATH")
        parent_folder = None
        parent_id = None
        if parent_path and parent_path != "/":
            parent_folder = Folder.query.filter_by(path=parent_path).first()
            if not parent_folder:
                return error_response("Parent folder not found", 404, "PARENT_NOT_FOUND")
            parent_id = parent_folder.id
            new_path = f"{parent_path}/{folder_name}".lstrip("/")
        else:
            new_path = folder_name
        existing = Folder.query.filter_by(path=new_path).first()
        if existing:
            return error_response("Folder already exists", 409, "FOLDER_EXISTS")
        physical_path = get_physical_path(new_path)
        os.makedirs(physical_path, exist_ok=True)
        logger.info(f"Created physical folder: {physical_path}")
        new_folder = Folder(name=folder_name, path=new_path, parent_id=parent_id)
        db.session.add(new_folder)
        db.session.commit()
        logger.info(f"Created folder: {new_folder.name} at {new_folder.path}")
        return success_response(new_folder.to_dict(), 201)
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error creating folder: {e}", exc_info=True)
        return error_response("Database error", 500, "DB_ERROR")
    except Exception as e:
        logger.error(f"Error creating folder: {e}", exc_info=True)
        return error_response("Failed to create folder", 500, "CREATE_ERROR")


def _update_child_paths(folder: Folder, old_base: str, new_base: str):
    """Batch update paths for all descendants when parent is renamed/moved.
    Uses bulk SQL UPDATE instead of recursive Python iteration."""
    # Update all descendant folders in one query
    Folder.query.filter(Folder.path.like(f"{old_base}/%")).update(
        {Folder.path: sa_func.replace(Folder.path, old_base, new_base)},
        synchronize_session='fetch'
    )
    # Update all descendant documents in one query
    DBDocument.query.filter(DBDocument.path.like(f"{old_base}/%")).update(
        {DBDocument.path: sa_func.replace(DBDocument.path, old_base, new_base)},
        synchronize_session='fetch'
    )


@files_bp.route("/folder/<int:folder_id>", methods=["PUT"])
@ensure_db_session_cleanup
def rename_folder(folder_id):
    """PUT /api/files/folder/:id - Rename folder"""
    logger.info(f"API: Rename folder {folder_id}")
    try:
        folder = db.session.get(Folder, folder_id)
        if not folder:
            return error_response("Folder not found", 404, "FOLDER_NOT_FOUND")
        data = request.get_json()
        if not data or "name" not in data:
            return error_response("New name required", 400, "MISSING_NAME")
        new_name = data["name"].strip()
        if not new_name or new_name == folder.name:
            return success_response(folder.to_dict())
        
        # Basic validation for dangerous characters
        invalid_chars = '<>:"/\\|?*\x00-\x1f'
        if any(char in invalid_chars for char in new_name):
            return error_response("Folder name contains invalid characters", 400, "INVALID_FILENAME")
        
        if len(new_name) > 255:
            return error_response("Folder name too long", 400, "INVALID_FILENAME")
        old_path = folder.path
        old_physical_path = get_physical_path(old_path)
        if folder.parent_id:
            parent = db.session.get(Folder, folder.parent_id)
            new_path = f"{parent.path}/{new_name}"
        else:
            new_path = new_name
        new_physical_path = get_physical_path(new_path)
        existing = Folder.query.filter_by(path=new_path).first()
        if existing:
            return error_response("Folder with this name already exists", 409, "FOLDER_EXISTS")
        if old_physical_path.exists():
            shutil.move(str(old_physical_path), str(new_physical_path))
            logger.info(f"Renamed physical folder: {old_physical_path} -> {new_physical_path}")
        folder.name = new_name
        folder.path = new_path
        _update_child_paths(folder, old_path, new_path)
        db.session.commit()
        logger.info(f"Renamed folder {folder_id}: {old_path} -> {new_path}")
        return success_response(folder.to_dict())
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error renaming folder: {e}", exc_info=True)
        return error_response("Database error", 500, "DB_ERROR")
    except Exception as e:
        logger.error(f"Error renaming folder: {e}", exc_info=True)
        return error_response("Failed to rename folder", 500, "RENAME_ERROR")


@files_bp.route("/folder/<int:folder_id>/move", methods=["POST"])
@ensure_db_session_cleanup
def move_folder(folder_id):
    """POST /api/files/folder/:id/move - Move folder to a different parent"""
    logger.info(f"API: Move folder {folder_id}")
    try:
        folder = db.session.get(Folder, folder_id)
        if not folder:
            return error_response("Folder not found", 404, "FOLDER_NOT_FOUND")

        data = request.get_json()
        if not data or "destination_path" not in data:
            return error_response("Destination path required", 400, "MISSING_DESTINATION")

        dest_path = data["destination_path"].strip()
        if not ensure_path_is_safe(dest_path):
            return error_response("Invalid destination path", 400, "INVALID_PATH")

        # Prevent moving folder into itself
        if dest_path == folder.path:
            return error_response("Cannot move folder into itself", 400, "INVALID_MOVE")

        # Prevent moving folder into its own descendant
        if dest_path.startswith(folder.path + "/"):
            return error_response("Cannot move folder into its own subfolder", 400, "INVALID_MOVE")

        # Resolve destination parent
        dest_folder_id = None
        if dest_path and dest_path != "/":
            dest_folder = Folder.query.filter_by(path=dest_path).first()
            if not dest_folder:
                return error_response("Destination folder not found", 404, "DEST_NOT_FOUND")
            dest_folder_id = dest_folder.id

        # Build new path
        old_path = folder.path
        if dest_path and dest_path != "/":
            new_path = f"{dest_path}/{folder.name}"
        else:
            new_path = folder.name

        # Check for name collision at destination
        existing = Folder.query.filter_by(path=new_path).first()
        if existing and existing.id != folder.id:
            return error_response("A folder with this name already exists at the destination", 409, "FOLDER_EXISTS")

        # Move physical directory
        old_physical_path = get_physical_path(old_path)
        new_physical_path = get_physical_path(new_path)
        if old_physical_path.exists():
            new_physical_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_physical_path), str(new_physical_path))
            logger.info(f"Moved folder: {old_physical_path} -> {new_physical_path}")

        # Update database records
        folder.parent_id = dest_folder_id
        folder.path = new_path
        _update_child_paths(folder, old_path, new_path)
        db.session.commit()

        logger.info(f"Moved folder {folder_id}: {old_path} -> {new_path}")
        return success_response(folder.to_dict())
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error moving folder: {e}", exc_info=True)
        return error_response("Database error", 500, "DB_ERROR")
    except Exception as e:
        logger.error(f"Error moving folder: {e}", exc_info=True)
        return error_response("Failed to move folder", 500, "MOVE_ERROR")


def _cascade_properties_to_folder(folder: Folder, properties: dict, stats: dict):
    """Recursively apply properties to all files and subfolders within a folder"""
    # Update all documents in this folder
    documents = folder.documents.all()
    for doc in documents:
        updated = False
        if 'client_id' in properties:
            doc.client_id = properties['client_id']
            updated = True
        if 'project_id' in properties:
            doc.project_id = properties['project_id']
            updated = True
        if 'website_id' in properties:
            doc.website_id = properties['website_id']
            updated = True
        if 'tags' in properties:
            doc.tags = properties['tags']
            updated = True
        if 'notes' in properties:
            doc.notes = properties['notes']
            updated = True
        if updated:
            doc.updated_at = datetime.datetime.now()
            db.session.add(doc)
            stats['files_updated'] += 1

    # Recursively process subfolders — update subfolder properties too
    subfolders = folder.subfolders.all()
    for subfolder in subfolders:
        stats['folders_processed'] += 1
        for key in ('client_id', 'project_id', 'website_id', 'tags', 'notes', 'is_repository'):
            if key in properties and hasattr(subfolder, key):
                setattr(subfolder, key, properties[key])
        subfolder.updated_at = datetime.datetime.now()
        db.session.add(subfolder)
        _cascade_properties_to_folder(subfolder, properties, stats)


@files_bp.route("/folder/<int:folder_id>/link", methods=["PUT"])
@ensure_db_session_cleanup
def link_folder_to_entities(folder_id):
    """PUT /api/files/folder/:id/link - Link folder and cascade properties to all children"""
    logger.info(f"API: Link folder {folder_id} to entities with cascading")
    try:
        folder = db.session.get(Folder, folder_id)
        if not folder:
            return error_response("Folder not found", 404, "FOLDER_NOT_FOUND")
        
        data = request.get_json()
        if not data:
            return error_response("No data provided", 400, "NO_DATA")
        
        # Build properties dict from request
        properties = {}
        if "client_id" in data:
            client_id = data["client_id"]
            if client_id and not db.session.get(Client, client_id):
                return error_response("Client not found", 404, "CLIENT_NOT_FOUND")
            properties['client_id'] = client_id
        
        if "project_id" in data:
            project_id = data["project_id"]
            if project_id and not db.session.get(Project, project_id):
                return error_response("Project not found", 404, "PROJECT_NOT_FOUND")
            properties['project_id'] = project_id
        
        if "website_id" in data:
            website_id = data["website_id"]
            if website_id and not db.session.get(Website, website_id):
                return error_response("Website not found", 404, "WEBSITE_NOT_FOUND")
            properties['website_id'] = website_id
        
        if "tags" in data:
            # Convert list to JSON string for storage
            tags = data["tags"]
            if isinstance(tags, list):
                properties['tags'] = json.dumps(tags) if tags else None
            elif isinstance(tags, str):
                properties['tags'] = tags if tags else None
            else:
                properties['tags'] = None
        
        if "notes" in data:
            properties['notes'] = data["notes"] if data["notes"] else None

        if "is_repository" in data:
            properties['is_repository'] = bool(data["is_repository"])

        # Cascade properties to all children
        stats = {
            'files_updated': 0,
            'folders_processed': 0,
        }
        
        # Save properties on the folder itself
        for key, value in properties.items():
            if hasattr(folder, key):
                setattr(folder, key, value)

        cascade = data.get("cascade", True)  # Default to True for folder properties
        if cascade:
            _cascade_properties_to_folder(folder, properties, stats)

        # Update folder's updated_at timestamp
        folder.updated_at = datetime.datetime.now()
        
        db.session.commit()
        
        logger.info(f"Updated entity links for folder {folder_id} and cascaded to {stats['files_updated']} files in {stats['folders_processed']} subfolders")
        
        return success_response({
            **folder.to_dict(),
            "cascade_stats": stats
        })
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error linking folder: {e}", exc_info=True)
        return error_response("Database error", 500, "DB_ERROR")
    except Exception as e:
        logger.error(f"Error linking folder: {e}", exc_info=True)
        return error_response("Failed to link folder", 500, "LINK_ERROR")


def _delete_folder_recursive(folder: Folder, deleted_folders: list, deleted_documents: list):
    """Recursively delete a folder and all its subfolders and documents"""
    # First, recursively delete all subfolders
    subfolders = folder.subfolders.all()
    for subfolder in subfolders:
        _delete_folder_recursive(subfolder, deleted_folders, deleted_documents)
    
    # Delete all documents in this folder
    documents = folder.documents.all()
    doc_ids_to_deindex = []
    for document in documents:
        doc_ids_to_deindex.append(str(document.id))
        doc_path = document.path
        physical_doc_path = get_physical_path(doc_path)
        if physical_doc_path.exists():
            try:
                physical_doc_path.unlink()
                logger.info(f"Deleted physical document: {physical_doc_path}")
            except Exception as e:
                logger.warning(f"Failed to delete physical document {physical_doc_path}: {e}")
        db.session.delete(document)
        deleted_documents.append(document.filename)

    # Batch deindex from vector store
    index_instance = current_app.config.get("LLAMA_INDEX_INDEX")
    storage_dir = current_app.config.get("STORAGE_DIR")
    if doc_ids_to_deindex and index_instance and storage_dir and hasattr(index_instance, "delete_ref_doc"):
        for ref_doc_id in doc_ids_to_deindex:
            try:
                index_instance.delete_ref_doc(ref_doc_id, delete_from_docstore=True)
                logger.info(f"Removed document {ref_doc_id} from vector index")
            except Exception:
                logger.warning(f"Failed to remove doc {ref_doc_id} from index (continuing)")
        try:
            index_instance.storage_context.persist(persist_dir=storage_dir)
        except Exception as e:
            logger.warning(f"Failed to persist index after folder deletion: {e}")

    # Delete the physical folder structure
    folder_path = folder.path
    physical_path = get_physical_path(folder_path)
    if physical_path.exists():
        try:
            shutil.rmtree(physical_path)
            logger.info(f"Deleted physical folder: {physical_path}")
        except Exception as e:
            logger.warning(f"Failed to delete physical folder {physical_path}: {e}")
    
    # Delete the folder from database
    folder_name = folder.name
    db.session.delete(folder)
    deleted_folders.append(folder_name)
    logger.info(f"Deleted folder {folder.id}: {folder_name}")


@files_bp.route("/folder/<int:folder_id>", methods=["DELETE"])
@ensure_db_session_cleanup
def delete_folder(folder_id):
    """DELETE /api/files/folder/:id - Delete folder and contents recursively"""
    logger.info(f"API: Delete folder {folder_id} (recursive)")
    try:
        folder = db.session.get(Folder, folder_id)
        if not folder:
            return error_response("Folder not found", 404, "FOLDER_NOT_FOUND")
        
        folder_name = folder.name
        deleted_folders = []
        deleted_documents = []
        
        # Recursively delete folder and all its contents
        _delete_folder_recursive(folder, deleted_folders, deleted_documents)
        
        # Commit all deletions
        db.session.commit()
        
        logger.info(f"Successfully deleted folder {folder_id}: {folder_name} with {len(deleted_folders)} subfolders and {len(deleted_documents)} documents")
        return success_response({
            "message": "Folder deleted successfully",
            "folder_id": folder_id,
            "name": folder_name,
            "deleted_subfolders": len(deleted_folders),
            "deleted_documents": len(deleted_documents)
        })
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error deleting folder: {e}", exc_info=True)
        return error_response("Database error", 500, "DB_ERROR")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting folder: {e}", exc_info=True)
        return error_response("Failed to delete folder", 500, "DELETE_ERROR")
@files_bp.route("/folder/<int:folder_id>/toggle-repo", methods=["PUT", "POST"])
@ensure_db_session_cleanup
def toggle_repo_status(folder_id):
    """PUT /api/files/folder/:id/toggle-repo - Toggle folder repository status with cascading"""
    logger.info(f"API: Toggle repository status for folder {folder_id}")
    try:
        folder = db.session.get(Folder, folder_id)
        if not folder:
            return error_response("Folder not found", 404, "FOLDER_NOT_FOUND")

        data = request.get_json(silent=True)
        if data and "is_repository" in data:
            folder.is_repository = bool(data["is_repository"])
        else:
            # Toggle if not specified
            folder.is_repository = not folder.is_repository

        # Cascade is_repository to all child folders
        stats = {'files_updated': 0, 'folders_processed': 0}
        _cascade_properties_to_folder(folder, {'is_repository': folder.is_repository}, stats)

        db.session.commit()

        status_msg = "marked as repository" if folder.is_repository else "unmarked as repository"
        logger.info(f"Folder {folder_id} {status_msg} (cascaded to {stats['folders_processed']} subfolders)")

        # Trigger analysis if marked as repository (non-blocking)
        if folder.is_repository:
            try:
                from backend.tasks.repo_analysis_tasks import analyze_repository_task
                analyze_repository_task.delay(folder_id)
            except Exception as task_err:
                logger.warning(f"Could not dispatch repo analysis task: {task_err}")

        return success_response({
            **folder.to_dict(),
            "cascade_stats": stats
        })
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error toggling repo status: {e}", exc_info=True)
        return error_response("Database error", 500, "DB_ERROR")
    except Exception as e:
        logger.error(f"Error toggling repo status: {e}", exc_info=True)
        return error_response(f"Failed to toggle repository status: {e}", 500, "TOGGLE_ERROR")

# ============================================================================
# FILE OPERATIONS
# ============================================================================

@files_bp.route("/upload", methods=["POST"])
@ensure_db_session_cleanup
def upload_file():
    """POST /api/files/upload - Unified upload endpoint supporting folders and projects"""
    logger.info("API: Upload file request")
    try:
        if "file" not in request.files:
            return error_response("No file provided", 400, "NO_FILE")
        file = request.files["file"]
        if file.filename == "":
            return error_response("No file selected", 400, "NO_FILE")
        
        # Get parameters (support both folder-based and project-based uploads)
        folder_path = request.form.get("folder_path", "").strip()
        project_id = request.form.get("project_id", type=int)
        client_id = request.form.get("client_id", type=int)
        website_id = request.form.get("website_id", type=int)
        tags = request.form.get("tags", "")
        metadata_str = request.form.get("metadata", "{}")
        auto_index = request.form.get("auto_index", "true").lower() not in ("false", "0", "no")
        
        # Validate folder_path if provided
        if folder_path and not ensure_path_is_safe(folder_path):
            return error_response("Invalid folder path", 400, "INVALID_PATH")
        
        # Parse metadata
        try:
            metadata = json.loads(metadata_str) if metadata_str else {}
        except json.JSONDecodeError:
            return error_response("Invalid metadata JSON format", 400, "INVALID_METADATA")
        
        # Use unified upload service
        from backend.services.unified_upload_service import UnifiedUploadService
        
        document, job_id = UnifiedUploadService.upload_file(
            file=file,
            folder_path=folder_path if folder_path else None,
            project_id=project_id,
            client_id=client_id,
            website_id=website_id,
            tags=tags if tags else None,
            metadata=metadata,  # Pass metadata dict (can be empty)
            store_content=True,  # Store content for text/code files
            auto_index=auto_index,
        )
        
        response_data = document.to_dict()
        if job_id:
            response_data['job_id'] = job_id
        
        logger.info(f"Successfully uploaded file {document.filename} (ID: {document.id})")
        return success_response(response_data, 201)
        
    except ValueError as e:
        logger.warning(f"Validation error during upload: {e}")
        return error_response(str(e), 400, "VALIDATION_ERROR")
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error uploading file: {e}", exc_info=True)
        return error_response("Database error", 500, "DB_ERROR")
    except Exception as e:
        logger.error(f"Error uploading file: {e}", exc_info=True)
        return error_response("Failed to upload file", 500, "UPLOAD_ERROR")


@files_bp.route("/document/<int:doc_id>", methods=["PUT"])
@ensure_db_session_cleanup
def rename_document(doc_id):
    """PUT /api/files/document/:id - Rename document"""
    logger.info(f"API: Rename document {doc_id}")
    try:
        document = db.session.get(DBDocument, doc_id)
        if not document:
            return error_response("Document not found", 404, "DOCUMENT_NOT_FOUND")
        data = request.get_json()
        if not data or "filename" not in data:
            return error_response("New filename required", 400, "MISSING_FILENAME")
        new_filename = data["filename"].strip()
        if not new_filename:
            return error_response("Invalid filename", 400, "INVALID_FILENAME")
        
        # Basic validation for dangerous characters but preserve file extensions
        invalid_chars = '<>:"/\\|?*\x00-\x1f'
        if any(char in invalid_chars for char in new_filename):
            return error_response("Filename contains invalid characters", 400, "INVALID_FILENAME")
        
        if len(new_filename) > 255:
            return error_response("Filename too long", 400, "INVALID_FILENAME")
        document.filename = new_filename
        document.updated_at = datetime.datetime.now()
        db.session.commit()
        logger.info(f"Renamed document {doc_id} to: {new_filename}")
        return success_response(document.to_dict())
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error renaming document: {e}", exc_info=True)
        return error_response("Database error", 500, "DB_ERROR")
    except Exception as e:
        logger.error(f"Error renaming document: {e}", exc_info=True)
        return error_response("Failed to rename document", 500, "RENAME_ERROR")


@files_bp.route("/document/<int:doc_id>/move", methods=["POST"])
@ensure_db_session_cleanup
def move_document(doc_id):
    """POST /api/files/document/:id/move - Move document to different folder"""
    logger.info(f"API: Move document {doc_id}")
    try:
        document = db.session.get(DBDocument, doc_id)
        if not document:
            return error_response("Document not found", 404, "DOCUMENT_NOT_FOUND")
        data = request.get_json()
        if not data or "destination_path" not in data:
            return error_response("Destination path required", 400, "MISSING_DESTINATION")
        dest_path = data["destination_path"].strip()
        if not ensure_path_is_safe(dest_path):
            return error_response("Invalid destination path", 400, "INVALID_PATH")
        dest_folder_id = None
        if dest_path and dest_path != "/":
            dest_folder = Folder.query.filter_by(path=dest_path).first()
            if not dest_folder:
                return error_response("Destination folder not found", 404, "DEST_NOT_FOUND")
            dest_folder_id = dest_folder.id
        old_path = document.path
        old_physical_path = get_physical_path(old_path)
        # Apply the resolver against the destination folder — was a silent
        # overwrite before, which is the fastest way to lose a file.
        # Files-app convention: 'name (2).ext' if a sibling already holds
        # the name. exclude_id=document.id so we don't see ourselves as
        # a collision (no-op move into the same folder is fine).
        from backend.utils.filename_resolver import resolve_filename
        resolved_filename = resolve_filename(
            dest_folder_id,
            document.filename,
            db.session,
            DBDocument,
            exclude_id=document.id,
        )
        if dest_path and dest_path != "/":
            new_path = f"{dest_path}/{resolved_filename}"
        else:
            new_path = resolved_filename
        new_physical_path = get_physical_path(new_path)
        if old_physical_path.exists():
            new_physical_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_physical_path), str(new_physical_path))
            logger.info(f"Moved file: {old_physical_path} -> {new_physical_path}")
        document.filename = resolved_filename
        document.path = new_path
        document.folder_id = dest_folder_id
        document.updated_at = datetime.datetime.now()
        db.session.commit()
        logger.info(f"Moved document {doc_id} to: {new_path}")
        return success_response(document.to_dict())
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error moving document: {e}", exc_info=True)
        return error_response("Database error", 500, "DB_ERROR")
    except Exception as e:
        logger.error(f"Error moving document: {e}", exc_info=True)
        return error_response("Failed to move document", 500, "MOVE_ERROR")


@files_bp.route("/document/<int:doc_id>/copy", methods=["POST"])
@ensure_db_session_cleanup
def copy_document(doc_id):
    """POST /api/files/document/:id/copy - Copy document to a different folder"""
    logger.info(f"API: Copy document {doc_id}")
    try:
        document = db.session.get(DBDocument, doc_id)
        if not document:
            return error_response("Document not found", 404, "DOCUMENT_NOT_FOUND")
        data = request.get_json()
        if not data or "destination_path" not in data:
            return error_response("Destination path required", 400, "MISSING_DESTINATION")
        dest_path = data["destination_path"].strip()
        if not ensure_path_is_safe(dest_path):
            return error_response("Invalid destination path", 400, "INVALID_PATH")

        # Resolve destination folder
        dest_folder_id = None
        if dest_path and dest_path != "/":
            dest_folder = Folder.query.filter_by(path=dest_path).first()
            if not dest_folder:
                return error_response("Destination folder not found", 404, "DEST_NOT_FOUND")
            dest_folder_id = dest_folder.id

        # Physical copy
        old_physical = get_physical_path(document.path)
        physical_filename = Path(document.path).name
        if dest_path and dest_path != "/":
            new_rel_path = f"{dest_path.lstrip('/')}/{physical_filename}"
        else:
            new_rel_path = physical_filename

        # Handle name collision
        new_physical = get_physical_path(new_rel_path)
        if new_physical.exists():
            stem = old_physical.stem
            ext = old_physical.suffix
            new_filename = f"{stem} (Copy){ext}"
            if dest_path and dest_path != "/":
                new_rel_path = f"{dest_path.lstrip('/')}/{new_filename}"
            else:
                new_rel_path = new_filename
            new_physical = get_physical_path(new_rel_path)

        new_physical.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(old_physical), str(new_physical))

        # Also copy thumbnail if it exists
        thumb_dir = old_physical.parent / "thumbnails"
        thumb_name = old_physical.stem + ".jpg"
        thumb_src = thumb_dir / thumb_name
        if thumb_src.exists():
            dest_thumb_dir = new_physical.parent / "thumbnails"
            dest_thumb_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(thumb_src), str(dest_thumb_dir / thumb_name))

        # Create new document record
        new_doc = DBDocument(
            filename=Path(new_rel_path).name,
            path=new_rel_path,
            type=document.type,
            folder_id=dest_folder_id,
            size=document.size,
            index_status="NOT_INDEXED",
            is_code_file=document.is_code_file,
            file_metadata=document.file_metadata,
            uploaded_at=datetime.datetime.now(),
            updated_at=datetime.datetime.now(),
        )
        db.session.add(new_doc)
        db.session.commit()
        logger.info(f"Copied document {doc_id} to {new_rel_path} (new id={new_doc.id})")
        return success_response(new_doc.to_dict(), 201)
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error copying document: {e}", exc_info=True)
        return error_response("Database error", 500, "DB_ERROR")
    except Exception as e:
        logger.error(f"Error copying document: {e}", exc_info=True)
        return error_response("Failed to copy document", 500, "COPY_ERROR")


@files_bp.route("/document/<int:doc_id>", methods=["DELETE"])
@ensure_db_session_cleanup
def delete_document(doc_id):
    """DELETE /api/files/document/:id - Delete document"""
    logger.info(f"API: Delete document {doc_id}")
    try:
        document = db.session.get(DBDocument, doc_id)
        if not document:
            return error_response("Document not found", 404, "DOCUMENT_NOT_FOUND")
        doc_filename = document.filename
        doc_path = document.path
        physical_path = get_physical_path(doc_path)
        if physical_path.exists():
            physical_path.unlink()
            logger.info(f"Deleted physical file: {physical_path}")
        # Remove from vector index if it was indexed
        index_instance = current_app.config.get("LLAMA_INDEX_INDEX")
        storage_dir = current_app.config.get("STORAGE_DIR")
        if (index_instance and storage_dir and
                hasattr(index_instance, "delete_ref_doc")):
            try:
                ref_doc_id = str(document.id)
                index_instance.delete_ref_doc(ref_doc_id, delete_from_docstore=True)
                logger.info(f"Removed document {doc_id} from vector index")
                try:
                    index_instance.storage_context.persist(persist_dir=storage_dir)
                except Exception as persist_err:
                    logger.warning(f"Failed to persist index after deletion: {persist_err}")
            except Exception as index_err:
                logger.warning(f"Failed to remove doc {doc_id} from index (continuing): {index_err}")
        db.session.delete(document)
        db.session.commit()
        logger.info(f"Deleted document {doc_id}: {doc_filename}")
        return success_response({"message": "Document deleted successfully", "document_id": doc_id, "filename": doc_filename})
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error deleting document: {e}", exc_info=True)
        return error_response("Database error", 500, "DB_ERROR")
    except Exception as e:
        logger.error(f"Error deleting document: {e}", exc_info=True)
        return error_response("Failed to delete document", 500, "DELETE_ERROR")


@files_bp.route("/document/<int:doc_id>/link", methods=["PUT"])
@ensure_db_session_cleanup
def link_document_to_entity(doc_id):
    """PUT /api/files/document/:id/link - Link document to entities"""
    logger.info(f"API: Link document {doc_id} to entities")
    try:
        document = db.session.get(DBDocument, doc_id)
        if not document:
            return error_response("Document not found", 404, "DOCUMENT_NOT_FOUND")
        data = request.get_json()
        if not data:
            return error_response("No data provided", 400, "NO_DATA")
        if "client_id" in data:
            client_id = data["client_id"]
            if client_id and not db.session.get(Client, client_id):
                return error_response("Client not found", 404, "CLIENT_NOT_FOUND")
            document.client_id = client_id
        if "project_id" in data:
            project_id = data["project_id"]
            if project_id and not db.session.get(Project, project_id):
                return error_response("Project not found", 404, "PROJECT_NOT_FOUND")
            document.project_id = project_id
        if "website_id" in data:
            website_id = data["website_id"]
            if website_id and not db.session.get(Website, website_id):
                return error_response("Website not found", 404, "WEBSITE_NOT_FOUND")
            document.website_id = website_id
        if "tags" in data:
            tags = data["tags"]
            if isinstance(tags, list):
                document.tags = json.dumps(tags) if tags else None
            else:
                document.tags = tags if tags else None
        if "notes" in data:
            document.notes = data["notes"] if data["notes"] else None
        document.updated_at = datetime.datetime.now()
        db.session.commit()
        logger.info(f"Updated entity links for document {doc_id}")
        return success_response(document.to_dict())
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error linking document: {e}", exc_info=True)
        return error_response("Database error", 500, "DB_ERROR")
    except Exception as e:
        logger.error(f"Error linking document: {e}", exc_info=True)
        return error_response("Failed to link document", 500, "LINK_ERROR")


@files_bp.route("/document/<int:doc_id>", methods=["GET"])
def get_document(doc_id):
    """GET /api/files/document/:id - Return one document's full record (incl. parsed metadata).

    Folder listings use to_dict_light for speed; consumers that need the full
    record (audio player modal, file properties, etc.) hit this endpoint to
    pull the heavier fields on demand.
    """
    document = db.session.get(DBDocument, doc_id)
    if not document:
        return error_response("Document not found", 404, "DOCUMENT_NOT_FOUND")
    return success_response(document.to_dict())


@files_bp.route("/document/<int:doc_id>/download", methods=["GET"])
def download_document(doc_id):
    """GET /api/files/document/:id/download - Download file"""
    logger.info(f"API: Download document {doc_id}")
    try:
        document = db.session.get(DBDocument, doc_id)
        if not document:
            return error_response("Document not found", 404, "DOCUMENT_NOT_FOUND")
        physical_path = get_physical_path(document.path)
        if not physical_path.exists():
            return error_response("File not found on disk", 404, "FILE_NOT_FOUND")
        # Serve inline for media files and PDFs so <video>, <img>, <audio>, and <iframe>
        # tags can play/display them; fall back to attachment for everything else.
        media_exts = {
            '.mp4', '.webm', '.avi', '.mov', '.mkv',                  # video
            '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg',          # image
            '.wav', '.mp3', '.ogg', '.flac', '.m4a', '.aac', '.opus',  # audio
            '.pdf',
        }
        as_attachment = physical_path.suffix.lower() not in media_exts
        response = send_file(physical_path, as_attachment=as_attachment, download_name=document.filename)
        # Disable long-term caching so edited images are served fresh
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response
    except Exception as e:
        logger.error(f"Error downloading document: {e}", exc_info=True)
        return error_response("Failed to download document", 500, "DOWNLOAD_ERROR")


@files_bp.route("/thumbnail", methods=["GET"])
def get_thumbnail():
    """GET /api/files/thumbnail?path=<document_path> - Get thumbnail for an image document.
    Looks for a thumbnail in a thumbnails/ subdirectory next to the image.
    Falls back to serving the original image if no thumbnail exists."""
    doc_path = request.args.get("path", "").strip()
    if not doc_path:
        return error_response("path parameter required", 400, "NO_PATH")

    if not ensure_path_is_safe(doc_path):
        return error_response("Invalid path", 400, "INVALID_PATH")

    base = get_upload_base_path()
    doc_physical = base / doc_path.lstrip("/")

    if not doc_physical.exists():
        return error_response("File not found", 404, "FILE_NOT_FOUND")

    # Look for thumbnail in thumbnails/ subdirectory
    parent_dir = doc_physical.parent
    thumb_dir = parent_dir / "thumbnails"
    thumb_name = doc_physical.stem + ".jpg"
    thumb_path = thumb_dir / thumb_name

    if thumb_path.exists():
        return send_file(str(thumb_path), mimetype="image/jpeg")

    # Try .png thumbnail as fallback
    thumb_png = thumb_dir / (doc_physical.stem + ".png")
    if thumb_png.exists():
        return send_file(str(thumb_png), mimetype="image/png")

    # For video files, try to generate a thumbnail on the fly via ffmpeg
    video_exts = {'.mp4', '.webm', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.m4v'}
    if doc_physical.suffix.lower() in video_exts:
        try:
            import subprocess
            thumb_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["ffmpeg", "-i", str(doc_physical), "-vf", "select=eq(n\\,0)",
                 "-frames:v", "1", "-q:v", "2", "-y", str(thumb_path)],
                capture_output=True, timeout=15,
            )
            if thumb_path.exists() and thumb_path.stat().st_size > 0:
                return send_file(str(thumb_path), mimetype="image/jpeg")
        except Exception as e:
            logger.warning(f"Failed to generate video thumbnail: {e}")

    # Fallback: serve the original file (works for images, not ideal for video)
    return send_file(str(doc_physical))


# ============================================================================
# IMAGE EDITING
# ============================================================================

@files_bp.route("/image/edit", methods=["POST"])
def edit_image():
    """POST /api/files/image/edit - Apply edits to an image and save.

    JSON body:
      document_id:  int (required) - document to edit
      operations:   list of operations to apply in order, each is a dict:
        {"type": "rotate", "angle": 90}          - rotate CW by angle degrees
        {"type": "crop", "x": 0, "y": 0, "width": 100, "height": 100, "unit": "px"|"%"}
        {"type": "resize", "width": 800, "height": 600}  - resize (aspect preserved if one is 0)
        {"type": "flip", "direction": "horizontal"|"vertical"}
      save_mode:    "overwrite" | "copy" (default "copy")
      format:       "png" | "jpeg" | "webp" | null (keep original)
      quality:      int 1-100 (for jpeg/webp, default 90)
    """
    try:
        from PIL import Image as PILImage, ImageOps

        data = request.get_json()
        if not data:
            return error_response("JSON body required", 400, "NO_DATA")

        doc_id = data.get("document_id")
        if not doc_id:
            return error_response("document_id required", 400, "NO_DOC_ID")

        document = db.session.get(DBDocument, doc_id)
        if not document:
            return error_response("Document not found", 404, "DOCUMENT_NOT_FOUND")

        physical_path = get_physical_path(document.path)
        if not physical_path.exists():
            return error_response("File not found on disk", 404, "FILE_NOT_FOUND")

        operations = data.get("operations", [])
        save_mode = data.get("save_mode", "copy")
        out_format = data.get("format")  # None = keep original
        quality = min(max(data.get("quality", 90), 1), 100)

        # Open image
        img = PILImage.open(str(physical_path))
        img = ImageOps.exif_transpose(img)  # Fix orientation from EXIF

        # Apply operations in order
        for op in operations:
            op_type = op.get("type")
            if op_type == "rotate":
                angle = op.get("angle", 0)
                img = img.rotate(-angle, expand=True, resample=PILImage.BICUBIC)
            elif op_type == "crop":
                unit = op.get("unit", "px")
                cx, cy = op.get("x", 0), op.get("y", 0)
                cw, ch = op.get("width", img.width), op.get("height", img.height)
                if unit == "%":
                    cx = int(cx / 100 * img.width)
                    cy = int(cy / 100 * img.height)
                    cw = int(cw / 100 * img.width)
                    ch = int(ch / 100 * img.height)
                else:
                    cx, cy, cw, ch = int(cx), int(cy), int(cw), int(ch)
                # Clamp to image bounds
                cx = max(0, min(cx, img.width - 1))
                cy = max(0, min(cy, img.height - 1))
                cw = max(1, min(cw, img.width - cx))
                ch = max(1, min(ch, img.height - cy))
                img = img.crop((cx, cy, cx + cw, cy + ch))
            elif op_type == "resize":
                rw = op.get("width", 0)
                rh = op.get("height", 0)
                if rw and rh:
                    img = img.resize((int(rw), int(rh)), PILImage.LANCZOS)
                elif rw:
                    ratio = int(rw) / img.width
                    img = img.resize((int(rw), int(img.height * ratio)), PILImage.LANCZOS)
                elif rh:
                    ratio = int(rh) / img.height
                    img = img.resize((int(img.width * ratio), int(rh)), PILImage.LANCZOS)
            elif op_type == "flip":
                direction = op.get("direction", "horizontal")
                if direction == "horizontal":
                    img = img.transpose(PILImage.FLIP_LEFT_RIGHT)
                else:
                    img = img.transpose(PILImage.FLIP_TOP_BOTTOM)

        # Determine output format
        orig_ext = physical_path.suffix.lower().lstrip(".")
        format_map = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp", "gif": "png"}
        target_format = out_format or format_map.get(orig_ext, "png")
        target_ext = {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}.get(target_format, ".png")

        # Convert RGBA to RGB for JPEG
        if target_format == "jpeg" and img.mode in ("RGBA", "LA", "P"):
            bg = PILImage.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            img = bg

        # Determine save path
        if save_mode == "overwrite":
            out_path = physical_path.with_suffix(target_ext)
            out_doc = document
            if out_path != physical_path:
                # Format changed — update document record
                out_doc.filename = physical_path.stem + target_ext
                out_doc.path = str(Path(document.path).with_suffix(target_ext))
                # Remove old file after save
                old_path = physical_path
            else:
                old_path = None
        else:
            # Save as copy with _edited suffix
            stem = physical_path.stem
            counter = 1
            while True:
                new_name = f"{stem}_edited{f'_{counter}' if counter > 1 else ''}{target_ext}"
                out_path = physical_path.parent / new_name
                if not out_path.exists():
                    break
                counter += 1

            # Create new document record
            new_doc_path = str(Path(document.path).parent / new_name)
            out_doc = DBDocument(
                filename=new_name,
                path=new_doc_path,
                folder_id=document.folder_id,
                file_size=0,
                file_type=f"image/{target_format}",
                uploaded_at=datetime.datetime.utcnow(),
            )
            db.session.add(out_doc)

        # Save
        save_kwargs = {}
        if target_format in ("jpeg", "webp"):
            save_kwargs["quality"] = quality
        if target_format == "png":
            save_kwargs["optimize"] = True
        img.save(str(out_path), format=target_format.upper(), **save_kwargs)

        # Update file size
        out_doc.file_size = out_path.stat().st_size

        # Remove old file if format changed on overwrite
        if save_mode == "overwrite" and old_path and old_path != out_path and old_path.exists():
            old_path.unlink()

        db.session.commit()

        logger.info(f"Image edited: doc={doc_id}, mode={save_mode}, format={target_format}, path={out_path}")
        return success_response({
            "document_id": out_doc.id,
            "filename": out_doc.filename,
            "path": out_doc.path,
            "file_size": out_doc.file_size,
            "width": img.width,
            "height": img.height,
        }, "Image saved successfully")

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error editing image: {e}", exc_info=True)
        return error_response(f"Failed to edit image: {str(e)}", 500, "EDIT_ERROR")


@files_bp.route("/image/info/<int:doc_id>", methods=["GET"])
def get_image_info(doc_id):
    """GET /api/files/image/info/:id - Get image dimensions and metadata"""
    try:
        from PIL import Image as PILImage

        document = db.session.get(DBDocument, doc_id)
        if not document:
            return error_response("Document not found", 404, "DOCUMENT_NOT_FOUND")

        physical_path = get_physical_path(document.path)
        if not physical_path.exists():
            return error_response("File not found on disk", 404, "FILE_NOT_FOUND")

        with PILImage.open(str(physical_path)) as img:
            width, height = img.size
            mode = img.mode
            fmt = img.format or physical_path.suffix.lstrip(".")

        return success_response({
            "document_id": doc_id,
            "filename": document.filename,
            "width": width,
            "height": height,
            "mode": mode,
            "format": fmt,
            "file_size": document.file_size,
        })
    except Exception as e:
        logger.error(f"Error getting image info: {e}", exc_info=True)
        return error_response("Failed to get image info", 500, "INFO_ERROR")


# ============================================================================
# SEARCH & UTILITIES
# ============================================================================

@files_bp.route("/search", methods=["GET"])
def search_files():
    """GET /api/files/search?q=invoice - Search files and folders"""
    logger.info("API: Search files request")
    try:
        query = request.args.get("q", "").strip()
        limit = min(request.args.get("limit", 50, type=int), 100)  # Cap at 100
        if not query:
            return error_response("Search query required", 400, "NO_QUERY")

        # BUG FIX #18: Sanitize search query to prevent SQL injection
        # Escape SQL wildcards and limit length
        query = query.replace('%', '\\%').replace('_', '\\_')[:100]

        folders = Folder.query.filter(Folder.name.ilike(f"%{query}%", escape='\\')).limit(limit).all()
        documents = DBDocument.query.filter(DBDocument.filename.ilike(f"%{query}%", escape='\\')).limit(limit).all()
        return success_response({
            "query": query,
            "folders": [f.to_dict() for f in folders],
            "documents": [d.to_dict() for d in documents],
            "total_results": len(folders) + len(documents)
        })
    except Exception as e:
        logger.error(f"Error searching files: {e}", exc_info=True)
        return error_response("Search failed", 500, "SEARCH_ERROR")


@files_bp.route("/recent", methods=["GET"])
def get_recent_files():
    """GET /api/files/recent?limit=20 - Get recently uploaded files"""
    logger.info("API: Get recent files request")
    try:
        limit = request.args.get("limit", 20, type=int)
        documents = DBDocument.query.order_by(DBDocument.uploaded_at.desc()).limit(limit).all()
        return success_response({"documents": [d.to_dict() for d in documents]})
    except Exception as e:
        logger.error(f"Error getting recent files: {e}", exc_info=True)
        return error_response("Failed to get recent files", 500, "RECENT_ERROR")


# ============================================================================
# BULK IMPORT OPERATIONS
# ============================================================================

# Use Redis for job status storage (shared between Flask and Celery)
import redis
import json as json_lib

try:
    _redis_client = redis.Redis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0'), decode_responses=True)
    _redis_client.ping()
    logger.info("Connected to Redis for bulk import job storage")
    USE_REDIS = True
except Exception as e:
    logger.warning(f"Redis not available, using in-memory storage: {e}")
    USE_REDIS = False
    _bulk_import_jobs = {}

def get_job_status(job_id):
    """Get job status from Redis or in-memory storage"""
    if USE_REDIS:
        data = _redis_client.get(f"bulk_import_job:{job_id}")
        return json_lib.loads(data) if data else None
    else:
        return _bulk_import_jobs.get(job_id)

def set_job_status(job_id, status_data):
    """Set job status in Redis or in-memory storage"""
    if USE_REDIS:
        _redis_client.setex(
            f"bulk_import_job:{job_id}",
            3600,  # Expire after 1 hour
            json_lib.dumps(status_data)
        )
    else:
        _bulk_import_jobs[job_id] = status_data


@files_bp.route("/bulk-import", methods=["POST"])
@ensure_db_session_cleanup
def start_bulk_import():
    """POST /api/files/bulk-import - Start a bulk import job"""
    logger.info("API: Start bulk import request")
    try:
        data = request.get_json()
        if not data or "source_path" not in data:
            return error_response("source_path is required", 400, "MISSING_SOURCE_PATH")

        source_path = data.get("source_path", "").strip()
        if not source_path:
            return error_response("source_path cannot be empty", 400, "INVALID_SOURCE_PATH")

        # Generate a job ID
        import uuid
        job_id = str(uuid.uuid4())

        # Store job info
        job_data = {
            "job_id": job_id,
            "status": "queued",
            "message": "Bulk import job queued",
            "progress": 0,
            "stats": {},
            "created_at": datetime.datetime.now().isoformat(),
        }
        set_job_status(job_id, job_data)

        # Extract parameters
        target_folder = data.get("target_folder") or ""
        if target_folder:
            target_folder = target_folder.strip() or "Imports"
        else:
            target_folder = "Imports"
        project_id = data.get("project_id")
        client_id = data.get("client_id")
        website_id = data.get("website_id")
        reindex_missing = data.get("reindex_missing", True)
        force_copy = data.get("force_copy", False)
        dry_run = data.get("dry_run", False)

        # Submit to background task via Celery
        try:
            from backend.celery_tasks_isolated import bulk_import_documents_task

            # Update status to processing
            job_data = get_job_status(job_id)
            job_data["status"] = "processing"
            job_data["message"] = "Starting bulk import..."
            set_job_status(job_id, job_data)

            # Submit task
            task = bulk_import_documents_task.apply_async(
                args=(
                    job_id,
                    source_path,
                    target_folder,
                    project_id,
                    client_id,
                    website_id,
                    reindex_missing,
                    force_copy,
                    dry_run,
                ),
                queue="indexing"
            )

            job_data["celery_task_id"] = task.id
            set_job_status(job_id, job_data)
            logger.info(f"Submitted bulk import task {task.id} for job {job_id}")

        except ImportError:
            # Celery not available, mark as error
            job_data = get_job_status(job_id)
            job_data["status"] = "error"
            job_data["message"] = "Celery worker not available"
            set_job_status(job_id, job_data)
            logger.error("Cannot import bulk_import_documents_task - Celery task not defined")
            return error_response(
                "Bulk import task not available - Celery worker may not be configured",
                503,
                "CELERY_NOT_AVAILABLE"
            )

        # Return response in format frontend expects (unwrapped)
        job_data = get_job_status(job_id)
        return jsonify({
            "job_id": job_id,
            "status": job_data["status"],
            "message": "Bulk import started"
        }), 202

    except Exception as e:
        logger.error(f"Error starting bulk import: {e}", exc_info=True)
        return error_response("Failed to start bulk import", 500, "BULK_IMPORT_ERROR")


@files_bp.route("/bulk-import/<job_id>/status", methods=["GET"])
def get_bulk_import_status_endpoint(job_id):
    """GET /api/files/bulk-import/:job_id/status - Get bulk import job status"""
    logger.info(f"API: Get bulk import status for job {job_id}")
    try:
        job_info = get_job_status(job_id)
        if not job_info:
            return jsonify({"error": "Job not found"}), 404

        # Return unwrapped response for frontend compatibility
        return jsonify(job_info), 200

    except Exception as e:
        logger.error(f"Error getting bulk import status: {e}", exc_info=True)
        return jsonify({"error": "Failed to get job status"}), 500


@files_bp.route("/browse-server", methods=["GET"])
def browse_server_directory():
    """GET /api/files/browse-server?path=/some/path&include_files=true - Browse server directories for bulk import"""
    logger.info("API: Browse server directory")
    try:
        requested_path = request.args.get("path", "/")
        include_files = request.args.get("include_files", "false").lower() == "true"
        show_hidden = request.args.get("show_hidden", "false").lower() == "true"

        # Security: Prevent directory traversal attacks
        # Only allow absolute paths and validate they exist
        import os

        if not os.path.isabs(requested_path):
            # If relative, assume it's relative to user's home or a safe base
            requested_path = os.path.abspath(os.path.expanduser(requested_path))

        # Resolve the path to prevent .. tricks
        requested_path = os.path.realpath(requested_path)

        # Check if path exists and is a directory
        if not os.path.exists(requested_path):
            return jsonify({"error": "Path does not exist"}), 404

        if not os.path.isdir(requested_path):
            return jsonify({"error": "Path is not a directory"}), 400

        # List contents of the directory
        try:
            entries = os.listdir(requested_path)
            directories = []
            files = []

            for entry in sorted(entries):
                # Skip hidden entries unless show_hidden is true
                if entry.startswith('.') and not show_hidden:
                    continue

                entry_path = os.path.join(requested_path, entry)
                try:
                    stat_info = os.stat(entry_path)
                    if os.path.isdir(entry_path):
                        # Count items in directory for preview
                        try:
                            item_count = len([e for e in os.listdir(entry_path) if not e.startswith('.')])
                        except (PermissionError, OSError):
                            item_count = -1  # Permission denied or error

                        directories.append({
                            "name": entry,
                            "item_count": item_count,
                            "modified": stat_info.st_mtime
                        })
                    elif include_files:
                        # Get file extension for type identification
                        _, ext = os.path.splitext(entry)
                        files.append({
                            "name": entry,
                            "size": stat_info.st_size,
                            "modified": stat_info.st_mtime,
                            "extension": ext.lower().lstrip('.')
                        })
                except (PermissionError, OSError):
                    # Skip entries we can't access
                    continue

            response_data = {
                "path": requested_path,
                "directories": directories,
                "parent_path": os.path.dirname(requested_path) if requested_path != "/" else None
            }

            if include_files:
                response_data["files"] = files
                response_data["total_files"] = len(files)
                response_data["total_directories"] = len(directories)

            return jsonify(response_data), 200

        except PermissionError:
            return jsonify({"error": "Permission denied to read directory"}), 403

    except Exception as e:
        logger.error(f"Error browsing server directory: {e}", exc_info=True)
        return jsonify({"error": "Failed to browse directory"}), 500
