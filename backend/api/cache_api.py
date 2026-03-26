
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from flask import Blueprint, current_app, jsonify, request

cache_bp = Blueprint("cache_api", __name__, url_prefix="/api/meta")
logger = logging.getLogger(__name__)


def get_project_root() -> Path:
    try:
        from backend.config import GUAARDVARK_ROOT
        return Path(GUAARDVARK_ROOT)
    except ImportError:
        backend_dir = Path(__file__).resolve().parent.parent
        return backend_dir.parent


def validate_path_is_in_project(path: Path, project_root: Path) -> bool:
    try:
        resolved_path = path.resolve()
        resolved_root = project_root.resolve()
        return str(resolved_path).startswith(str(resolved_root))
    except (OSError, ValueError):
        return False


def get_directory_size(path: Path) -> int:
    total_size = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            for filename in filenames:
                filepath = Path(dirpath) / filename
                try:
                    total_size += filepath.stat().st_size
                except (OSError, FileNotFoundError):
                    pass
    except (OSError, PermissionError):
        pass
    return total_size


def count_pyc_files(path: Path) -> int:
    count = 0
    try:
        for filepath in path.rglob("*.pyc"):
            count += 1
    except (OSError, PermissionError):
        pass
    return count


def clean_pycache_directory(
    pycache_dir: Path, project_root: Path
) -> Tuple[bool, Dict, str]:
    stats = {
        "size_bytes": 0,
        "pyc_files": 0,
    }

    if not validate_path_is_in_project(pycache_dir, project_root):
        return False, stats, f"Path outside project root: {pycache_dir}"

    if not pycache_dir.exists():
        return True, stats, ""  # Not an error, just doesn't exist

    if not pycache_dir.is_dir():
        return False, stats, f"Path is not a directory: {pycache_dir}"

    try:
        stats["size_bytes"] = get_directory_size(pycache_dir)
        stats["pyc_files"] = count_pyc_files(pycache_dir)

        shutil.rmtree(pycache_dir)
        return True, stats, ""
    except PermissionError as e:
        return False, stats, f"Permission denied: {e}"
    except OSError as e:
        return False, stats, f"OS error: {e}"
    except Exception as e:
        return False, stats, f"Unexpected error: {e}"


def find_pycache_directories(root: Path) -> List[Path]:
    pycache_dirs = []
    try:
        for path in root.rglob("__pycache__"):
            if path.is_dir():
                pycache_dirs.append(path)
    except (OSError, PermissionError) as e:
        logger.warning(f"Error searching for __pycache__ directories in {root}: {e}")
    return pycache_dirs


def purge_backend_modules() -> List[str]:
    modules_to_purge = [
        m for m in list(sys.modules.keys()) if m.startswith("backend.")
    ]
    purged = []
    for mod_name in modules_to_purge:
        try:
            sys.modules.pop(mod_name, None)
            purged.append(mod_name)
        except Exception as e:
            logger.warning(f"Failed to purge module {mod_name}: {e}")
    return purged


@cache_bp.route("/clear-pycache", methods=["POST"])
def clear_pycache_endpoint():
    try:
        logger.info("clear_pycache_endpoint: Starting cleanup")
        project_root = get_project_root()
        logger.info(f"clear_pycache_endpoint: Project root: {project_root}")
        backend_dir = project_root / "backend"
        plugins_dir = project_root / "plugins"
        logger.info(f"clear_pycache_endpoint: Backend dir: {backend_dir}, exists: {backend_dir.exists()}")
        logger.info(f"clear_pycache_endpoint: Plugins dir: {plugins_dir}, exists: {plugins_dir.exists()}")

        total_dirs_found = 0
        total_dirs_cleaned = 0
        total_pyc_files = 0
        total_size_bytes = 0
        locations_cleaned = []
        errors = []

        search_dirs = [backend_dir]
        if plugins_dir.exists() and plugins_dir.is_dir():
            search_dirs.append(plugins_dir)

        for search_dir in search_dirs:
            if not search_dir.exists():
                continue

            logger.info(f"Searching for __pycache__ directories in {search_dir}")
            pycache_dirs = find_pycache_directories(search_dir)
            total_dirs_found += len(pycache_dirs)

            for pycache_dir in pycache_dirs:
                success, stats, error_msg = clean_pycache_directory(
                    pycache_dir, project_root
                )

                if success:
                    total_dirs_cleaned += 1
                    total_pyc_files += stats["pyc_files"]
                    total_size_bytes += stats["size_bytes"]
                    try:
                        rel_path = pycache_dir.relative_to(project_root)
                        locations_cleaned.append(str(rel_path))
                    except (ValueError, TypeError):
                        try:
                            locations_cleaned.append(str(pycache_dir))
                        except Exception:
                            locations_cleaned.append(pycache_dir.name)
                    logger.info(
                        f"Cleaned {pycache_dir}: {stats['pyc_files']} files, "
                        f"{stats['size_bytes']} bytes"
                    )
                else:
                    errors.append(f"{pycache_dir}: {error_msg}")
                    logger.warning(f"Failed to clean {pycache_dir}: {error_msg}")

        # NOTE: purge_backend_modules() was removed — it destroyed the
        # initialized SQLAlchemy db instance by wiping backend.models from
        # sys.modules, causing "Flask app not registered" 500s on every
        # endpoint that uses deferred imports.  Clearing __pycache__ files
        # is sufficient; hot-reloading modules requires a process restart.
        logger.info("Skipping module purge (destroys live Flask/SQLAlchemy state)")


        def format_size(size_bytes: int) -> str:
            if size_bytes == 0:
                return "0 B"
            size = float(size_bytes)
            for unit in ["B", "KB", "MB", "GB"]:
                if size < 1024.0:
                    return f"{size:.2f} {unit}"
                size /= 1024.0
            return f"{size:.2f} TB"

        success = len(errors) == 0 or total_dirs_cleaned > 0
        message_parts = []

        if total_dirs_cleaned > 0:
            message_parts.append(
                f"Cleaned {total_dirs_cleaned} __pycache__ directory(ies) "
                f"({total_pyc_files} .pyc files, {format_size(total_size_bytes)})"
            )
        else:
            message_parts.append("No __pycache__ directories found to clean")

        # Module purging disabled (destroys SQLAlchemy in running Flask)
        purged_modules = []

        if errors:
            message_parts.append(f"{len(errors)} error(s) encountered")

        response_data = {
            "success": success,
            "message": ". ".join(message_parts) + ".",
            "statistics": {
                "directories_found": total_dirs_found,
                "directories_cleaned": total_dirs_cleaned,
                "pyc_files_deleted": total_pyc_files,
                "size_bytes": total_size_bytes,
                "size_formatted": format_size(total_size_bytes),
            },
            "locations_cleaned": locations_cleaned,
            "modules_purged_count": len(purged_modules),
            "modules_purged": purged_modules[:20],
        }

        if errors:
            response_data["errors"] = errors

        status_code = 200 if success else 207
        return jsonify(response_data), status_code

    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        logger.error(f"Error in clear_pycache_endpoint: {e}\n{error_traceback}")
        return (
            jsonify(
                {
                    "success": False,
                    "error": str(e),
                    "message": f"Failed to clear pycache: {str(e)}",
                }
            ),
            500,
        )
