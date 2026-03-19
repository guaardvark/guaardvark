"""Pre-sync code backup service for Interconnector."""
import logging
import os
import tarfile
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BACKUP_DIR = "backups/sync"
INCLUDE_DIRS = ["backend", "frontend/src", "scripts", "cli"]
EXCLUDE_PATTERNS = [
    "__pycache__", "node_modules", ".git", "venv",
    "data", "logs", ".pyc", ".pyo",
    ".gguf", ".safetensors", ".bin", ".pt",
]


def create_pre_sync_backup() -> str:
    """Create a tarball of code files before sync.

    Returns: path to the backup file
    Raises: RuntimeError if backup fails
    """
    root = os.environ.get("GUAARDVARK_ROOT", ".")
    backup_dir = os.path.join(root, BACKUP_DIR)
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"pre_sync_{timestamp}.tar.gz")

    def _should_exclude(tarinfo):
        name = tarinfo.name
        parts = name.split(os.sep)
        for pattern in EXCLUDE_PATTERNS:
            if pattern.startswith("."):
                if name.endswith(pattern):
                    return None
            elif pattern in parts:
                return None
        return tarinfo

    try:
        with tarfile.open(backup_path, "w:gz") as tar:
            for include_dir in INCLUDE_DIRS:
                full_path = os.path.join(root, include_dir)
                if os.path.exists(full_path):
                    tar.add(full_path, arcname=include_dir, filter=_should_exclude)

            # Add root-level shell scripts and python files
            for entry in os.scandir(root):
                if entry.is_file():
                    if entry.name.endswith(".sh") or entry.name.endswith(".py"):
                        tar.add(entry.path, arcname=entry.name)

        size_mb = os.path.getsize(backup_path) / (1024 * 1024)
        logger.info(f"Pre-sync backup created: {backup_path} ({size_mb:.1f} MB)")
        return backup_path

    except Exception as e:
        logger.error(f"Failed to create pre-sync backup: {e}")
        if os.path.exists(backup_path):
            os.remove(backup_path)
        raise RuntimeError(f"Pre-sync backup failed: {e}")


def list_backups() -> list:
    """List all pre-sync backups."""
    root = os.environ.get("GUAARDVARK_ROOT", ".")
    backup_dir = os.path.join(root, BACKUP_DIR)
    if not os.path.exists(backup_dir):
        return []

    backups = []
    for f in sorted(Path(backup_dir).glob("pre_sync_*.tar.gz"), reverse=True):
        backups.append({
            "filename": f.name,
            "path": str(f),
            "size_bytes": f.stat().st_size,
            "size_mb": round(f.stat().st_size / (1024 * 1024), 1),
            "created_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })
    return backups
