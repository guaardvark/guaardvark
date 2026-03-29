# backend/services/backup_service.py
"""Backup and restore utilities for guaardvark.

This module provides a central place to create and restore backup
archives for the application. Both full and granular backups are
supported. Backup archives are standard ZIP files containing a
``guaardvark_backup.json`` manifest file and any referenced files.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict, List
from urllib.parse import urlparse
from zipfile import ZIP_DEFLATED, ZipFile

from flask import Flask, current_app, has_app_context
from sqlalchemy import text

from backend import config, models

logger = logging.getLogger(__name__)


# Standard patterns to ignore in all backups (junk, temporary, or generated files)
GLOBAL_IGNORE_PATTERNS = [
    '__pycache__', '*.pyc', '*.pyo', 'node_modules', 'dist', 'build',
    '.git', '.gitignore', 'venv', 'env', '.DS_Store', 'Thumbs.db',
    '.pytest_cache', '.coverage', '*.egg-info',
    '.claud', '.claude', 'claude.*', 'gemini.*', '*.code-workspace',
    '.cursorignore', '*__review__*', '*__tests__*', '*.zip',
    'compare-folders-tmp', '*.db', '*.sqlite', '*.sqlite3',
    'whisper.cpp', 'piper', 'piper-models', 'whisper-models',
    # AI model files — never include in backups (re-downloadable)
    '*.safetensors', '*.ckpt', '*.pt', '*.pth', '*.bin', '*.onnx',
    '*.gguf', '*.ggml', 'models', 'checkpoints', 'ComfyUI',
]

# Same as GLOBAL_IGNORE_PATTERNS but WITHOUT database file exclusions (*.db, *.sqlite, *.sqlite3)
# Used when copying data directories.
DATA_IGNORE_PATTERNS = [
    '__pycache__', '*.pyc', '*.pyo', 'node_modules', 'dist', 'build',
    '.git', '.gitignore', 'venv', 'env', '.DS_Store', 'Thumbs.db',
    '.pytest_cache', '.coverage', '*.egg-info',
    '.claud', '.claude', 'claude.*', 'gemini.*', '*.code-workspace',
    '.cursorignore', '*__review__*', '*__tests__*', '*.zip',
    'compare-folders-tmp',
    # AI model files — never include in backups
    '*.safetensors', '*.ckpt', '*.pt', '*.pth', '*.bin', '*.onnx',
    '*.gguf', '*.ggml',
]


_ALL_COMPONENTS = [
    "clients",
    "documents",
    "projects",
    "tasks",
    "websites",
    "chats",
    "rules",
    "system_settings",
]


def _create_app() -> Flask:
    """Return a minimal Flask app with DB configured."""
    app = Flask("backup_service")
    app.config.from_object(config)
    app.config.setdefault("SQLALCHEMY_DATABASE_URI", config.DATABASE_URL)
    models.db.init_app(app)
    return app


def _ensure_document_folder_column() -> None:
    """Ensure the documents table has folder_id (legacy DB compatibility)."""
    try:
        result = models.db.session.execute(
            text("SELECT column_name FROM information_schema.columns "
                 "WHERE table_name = 'documents' AND column_name = 'folder_id'")
        )
        if result.fetchone() is None:
            logger.warning("Adding missing documents.folder_id column (legacy DB)")
            models.db.session.execute(text("ALTER TABLE documents ADD COLUMN folder_id INTEGER"))
            models.db.session.execute(
                text("CREATE INDEX IF NOT EXISTS idx_documents_folder_id ON documents(folder_id)")
            )
            models.db.session.commit()
    except Exception as exc:
        logger.warning("Failed to ensure documents.folder_id column: %s", exc)


def _rel_path(path: str, base: str) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            rel = p.relative_to(base)
        except ValueError:
            rel = p.name
    else:
        rel = Path(path)
    return str(rel).replace(os.sep, "/")


def _rel_to_root(path: Path) -> str:
    """Return a POSIX path relative to GUAARDVARK_ROOT, or basename fallback."""
    try:
        return path.resolve().relative_to(Path(config.GUAARDVARK_ROOT).resolve()).as_posix()
    except Exception:
        return path.name


def _gather_clients(session) -> tuple[list, dict[str, str]]:
    clients = []
    files: Dict[str, str] = {}
    base = Path(config.UPLOAD_FOLDER)
    for c in session.query(models.Client).all():
        data = c.to_dict()
        logo = data.get("logo_path")
        if logo:
            abs_logo = base / logo if not os.path.isabs(logo) else Path(logo)
            rel = _rel_to_root(abs_logo)
            data["logo_path"] = rel
            if abs_logo.is_file():
                files[rel] = str(abs_logo)
            else:
                logger.warning("Missing logo for client %s: %s", c.name, abs_logo)
        clients.append(data)
    return clients, files


def _gather_documents(session) -> tuple[list, dict[str, str]]:
    docs = []
    files: Dict[str, str] = {}
    base = Path(config.UPLOAD_FOLDER)
    for d in session.query(models.Document).all():
        data = d.to_dict()
        path = data.get("path")
        if path:
            abs_file = Path(path)
            if not abs_file.is_absolute():
                abs_file = base / path
            rel = _rel_to_root(abs_file)
            data["path"] = rel
            if abs_file.is_file():
                files[rel] = str(abs_file)
            else:
                logger.warning("Missing document file %s", abs_file)
        docs.append(data)
    return docs, files


def _gather_chats(session) -> list:
    sessions = []
    for s in session.query(models.LLMSession).all():
        sessions.append(
            {
                "id": s.id,
                "user": s.user,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "messages": [
                    m.to_dict()
                    for m in s.messages.order_by(models.LLMMessage.timestamp).all()
                ],
            }
        )
    return sessions


def _gather_system_settings(session) -> tuple[list, dict[str, str]]:
    settings = []
    files: Dict[str, str] = {}
    base = Path(config.UPLOAD_FOLDER)
    for s in session.query(models.SystemSetting).all():
        data = s.to_dict()
        if s.key == "logo_path" and s.value:
            logo_path = Path(s.value)
            if not logo_path.is_absolute():
                logo_path = base / s.value
            rel = _rel_to_root(logo_path)
            data["value"] = rel
            if logo_path.is_file():
                files[rel] = str(logo_path)
        settings.append(data)
    return settings, files


def _create_plugin_ignore_function():
    """Create an ignore function for plugins directory that excludes large data/training directories."""
    standard_ignore = shutil.ignore_patterns(
        *GLOBAL_IGNORE_PATTERNS,
        '*.bin',
        '*.onnx',
        '*.so',
        '*.so.*',
        '*.a',
        '*.o',
        'whisper-cli',
        'libwhisper*',
    )
    
    def ignore_plugins(dirname, names):
        """Ignore large plugin subdirectories (data/training) that shouldn't be in backups."""
        ignored = set()
        
        # Determine path relative to plugins root
        rel_dir = os.path.basename(dirname)
        
        # For the training plugin, ignore heavy data directories but KEEP scripts/ and documentation
        if rel_dir == 'training' or dirname.endswith('plugins/training'):
            for name in names:
                if name in ['datasets', 'processed', 'raw_transcripts', 'output', 'batch_input']:
                    ignored.add(name)
        
        # General plugin level exclusions
        if rel_dir == 'plugins' or dirname.endswith('plugins'):
            for name in names:
                if name in ['output', 'batch_input']:
                    ignored.add(name)
        
        # Apply standard ignore patterns
        standard_ignored = standard_ignore(dirname, names)
        
        return ignored | set(standard_ignored)
    
    return ignore_plugins


def _parse_database_url(db_url: str) -> dict:
    """Parse a PostgreSQL DATABASE_URL into connection components."""
    parsed = urlparse(db_url)
    return {
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "user": parsed.username or "guaardvark",
        "password": parsed.password or "",
        "dbname": parsed.path.lstrip("/") or "guaardvark",
    }


def _create_pg_dump(dest_path: Path) -> bool:
    """Create a PostgreSQL dump file using pg_dump.

    Args:
        dest_path: Where to write the SQL dump file.

    Returns:
        True if the dump was created successfully, False otherwise.
    """
    db_url = config.DATABASE_URL
    if not db_url or not db_url.startswith("postgresql"):
        logger.warning("DATABASE_URL is not PostgreSQL, skipping pg_dump")
        return False

    params = _parse_database_url(db_url)
    env = os.environ.copy()
    env["PGPASSWORD"] = params["password"]

    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "pg_dump",
                "-h", params["host"],
                "-p", params["port"],
                "-U", params["user"],
                "--no-owner",
                "--no-acl",
                "-F", "c",  # Custom format (compressed, supports pg_restore)
                "-f", str(dest_path),
                params["dbname"],
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            size_mb = dest_path.stat().st_size / (1024 * 1024)
            logger.info("PostgreSQL dump created: %s (%.2f MB)", dest_path, size_mb)
            return True
        else:
            logger.error("pg_dump failed (rc=%d): %s", result.returncode, result.stderr)
            return False
    except FileNotFoundError:
        logger.error("pg_dump command not found. Install postgresql-client.")
        return False
    except subprocess.TimeoutExpired:
        logger.error("pg_dump timed out after 300 seconds")
        return False
    except Exception as e:
        logger.error("pg_dump failed: %s", e)
        return False


def _restore_pg_dump(dump_path: Path) -> bool:
    """Restore a PostgreSQL dump file using pg_restore.

    Args:
        dump_path: Path to the .pgdump file.

    Returns:
        True if restore succeeded, False otherwise.
    """
    db_url = config.DATABASE_URL
    if not db_url or not db_url.startswith("postgresql"):
        logger.warning("DATABASE_URL is not PostgreSQL, skipping pg_restore")
        return False

    params = _parse_database_url(db_url)
    env = os.environ.copy()
    env["PGPASSWORD"] = params["password"]

    try:
        result = subprocess.run(
            [
                "pg_restore",
                "-h", params["host"],
                "-p", params["port"],
                "-U", params["user"],
                "-d", params["dbname"],
                "--no-owner",
                "--no-acl",
                "--clean",
                "--if-exists",
                str(dump_path),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        # pg_restore returns non-zero on warnings (e.g. "relation already exists")
        # so we only fail on actual error output
        if result.returncode == 0:
            logger.info("PostgreSQL restore completed successfully")
            return True
        elif "error" in result.stderr.lower() and "warning" not in result.stderr.lower():
            logger.error("pg_restore failed (rc=%d): %s", result.returncode, result.stderr)
            return False
        else:
            logger.warning("pg_restore completed with warnings: %s", result.stderr[:500])
            return True
    except FileNotFoundError:
        logger.error("pg_restore command not found. Install postgresql-client.")
        return False
    except subprocess.TimeoutExpired:
        logger.error("pg_restore timed out after 300 seconds")
        return False
    except Exception as e:
        logger.error("pg_restore failed: %s", e)
        return False


def _generate_backup_filename(backup_type: str, name: str | None = None) -> str:
    """Generate a backup filename with optional custom name.

    Args:
        backup_type: Type of backup (data, full, code_release)
        name: Optional custom name for the backup

    Returns:
        Filename string without extension
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if name:
        # Sanitize name: remove invalid filesystem characters
        safe_name = re.sub(r'[<>:"/\\|?*]', '_', name.strip())
        # Remove any leading/trailing dots and spaces
        safe_name = safe_name.strip('. ')
        # Limit length to avoid filesystem issues
        if len(safe_name) > 100:
            safe_name = safe_name[:100]
        backup_name = f"{safe_name}_{timestamp}"
    else:
        # Default naming based on backup type
        type_map = {
            "data": "guaardvark_data_backup",
            "full": "guaardvark_full_backup",
            "code_release": "guaardvark_code_release",
        }
        base_name = type_map.get(backup_type, "guaardvark_backup")
        backup_name = f"{base_name}_{timestamp}"

    return backup_name


def create_data_backup(components: List[str] | None = None, name: str | None = None, include_plugins: bool = False) -> str:
    """Create a data backup ZIP file and return its path.

    Backs up database components, uploaded files, state files, and context data.
    If components is None, all components are included. If a list is provided,
    only those components are backed up (granular mode).

    Args:
        components: Optional list of components to include (None = all)
        name: Optional custom name for the backup file
        include_plugins: If True, include the plugins/ directory (excluding models, datasets, etc.)
    """
    if components is None:
        components = list(_ALL_COMPONENTS)
    else:
        components = [c for c in components if c in _ALL_COMPONENTS]

    app_created = False
    if not has_app_context():
        app = _create_app()
        ctx = app.app_context()
        ctx.push()
        app_created = True
    try:
        models.db.create_all()
        session = models.db.session
        data: Dict[str, any] = {
            "version": "1.0",
            "backup_type": "data",
            "components": components,
            "backup_date": datetime.now().isoformat(),
            "system_info": {
                "python_version": str(sys.version),
                "platform": os.name,
                "project_root": str(config.GUAARDVARK_ROOT),
            },
        }
        file_map: Dict[str, str] = {}

        if "clients" in components:
            clients, files = _gather_clients(session)
            data["clients"] = clients
            file_map.update(files)
        if "projects" in components:
            data["projects"] = [
                p.to_dict() for p in session.query(models.Project).all()
            ]
        if "websites" in components:
            data["websites"] = [
                w.to_dict() for w in session.query(models.Website).all()
            ]
        if "tasks" in components:
            data["tasks"] = [t.to_dict() for t in session.query(models.Task).all()]
        if "documents" in components:
            docs, files = _gather_documents(session)
            data["documents"] = docs
            file_map.update(files)
        if "rules" in components:
            data["rules"] = [r.to_dict() for r in session.query(models.Rule).all()]
        if "chats" in components:
            data["chats"] = _gather_chats(session)
        if "system_settings" in components:
            settings, files = _gather_system_settings(session)
            data["system_settings"] = settings
            file_map.update(files)

        os.makedirs(config.BACKUP_DIR, exist_ok=True)
        backup_name = _generate_backup_filename("data", name)
        zip_path = Path(config.BACKUP_DIR) / f"{backup_name}.zip"

        # Get project root for state files
        project_root = Path(__file__).parent.parent.parent

        # State JSON files always included in data backups
        state_json_files = [
            "data/folder_state.json",
            "data/dashboard_state.json",
            "data/code_editor_state.json",
            "data/code_editor_session.json",
            "data/documents_windows_v2_state.json",
            "data/default__vector_store.json",
            "data/graph_store.json",
            "data/index_store.json",
            "data/docstore.json",
        ]

        # Data directories always included
        data_directories = [
            "data/context/",
            "data/conversations/",
        ]

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            json_path = tmp / "guaardvark_backup.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            for rel, src in file_map.items():
                dest = tmp / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(src, dest)
                except Exception as e:
                    logger.warning("Failed to copy %s: %s", src, e)

            # Copy state JSON files
            for json_file in state_json_files:
                src_path = project_root / json_file
                if src_path.exists() and src_path.is_file():
                    dest_path = tmp / json_file
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(src_path, dest_path)
                    except Exception as e:
                        logger.warning("Failed to copy state file %s: %s", json_file, e)

            # Copy data directories
            for data_dir in data_directories:
                src_path = project_root / data_dir
                if src_path.exists() and src_path.is_dir():
                    dest_dir = tmp / data_dir
                    try:
                        shutil.copytree(
                            src_path, dest_dir,
                            ignore=shutil.ignore_patterns(*DATA_IGNORE_PATTERNS, '*.tmp'),
                        )
                    except Exception as e:
                        logger.warning("Failed to copy data directory %s: %s", data_dir, e)

            # Copy plugins directory if requested
            if include_plugins:
                plugins_src = project_root / "plugins"
                if plugins_src.exists() and plugins_src.is_dir():
                    plugins_dest = tmp / "plugins"
                    try:
                        shutil.copytree(
                            plugins_src, plugins_dest,
                            ignore=_create_plugin_ignore_function(),
                        )
                        data["plugins_included"] = True
                        logger.info("Plugins directory included in data backup")
                    except Exception as e:
                        logger.warning("Failed to copy plugins directory: %s", e)

            # Create PostgreSQL database dump
            pg_dump_path = tmp / "data" / "database" / "guaardvark.pgdump"
            if _create_pg_dump(pg_dump_path):
                data["pg_dump_included"] = True
            else:
                logger.warning("PostgreSQL dump not included in data backup")

            # Re-write manifest with pg_dump flag
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            with ZipFile(zip_path, "w", ZIP_DEFLATED) as zf:
                for root, _dirs, filenames in os.walk(tmp):
                    for fn in filenames:
                        file_path = Path(root) / fn
                        zf.write(file_path, file_path.relative_to(tmp))

        logger.info("Data backup created: %s", zip_path)
        return str(zip_path)
    finally:
        if app_created:
            try:
                models.db.session.remove()
            except Exception:
                pass
            ctx.pop()


# Backward compatibility alias
def create_backup(backup_type: str = "full", components: List[str] | None = None, name: str | None = None) -> str:
    """Backward-compatible wrapper. Maps old types to new functions."""
    if backup_type in ("full_system",):
        return create_full_backup(name=name)
    if backup_type in ("code_only",):
        return create_code_release(name=name)
    # "full", "granular", "system" all map to data backup
    if backup_type == "granular":
        return create_data_backup(components=components, name=name)
    return create_data_backup(components=None, name=name)


def create_full_backup(name: str | None = None) -> str:
    """Create a complete backup including ALL files needed to install and run the system.

    This includes:
    - All application data (database, files, settings)
    - All source code (backend, frontend)
    - All configuration files (requirements.txt, package.json, etc.)
    - All startup scripts (start.sh, stop.sh, etc.)
    - All documentation and guides
    - All system configuration files

    Extract and run ./start.sh to deploy on a new machine.
    
    This backup can be extracted to a new machine and run immediately.
    
    Args:
        name: Optional custom name for the backup file
    """
    logger.info("Creating complete full system backup")
    
    app_created = False
    if not has_app_context():
        app = _create_app()
        ctx = app.app_context()
        ctx.push()
        app_created = True
    
    try:
        models.db.create_all()
        session = models.db.session
        
        # Get project root directory
        project_root = Path(__file__).parent.parent.parent
        logger.info("Project root: %s", project_root)
        
        # Collect all data
        data = {
            "version": "1.0",
            "backup_type": "full",
            "timestamp": int(datetime.now().timestamp()),
            "description": "Complete system backup including all files needed to install and run on a new machine",
            "backup_date": datetime.now().isoformat(),
            "system_info": {
                "database_path": config.DATABASE_URL,
                "upload_folder": config.UPLOAD_FOLDER,
                "backup_folder": config.BACKUP_DIR,
                "python_version": str(sys.version),
                "platform": os.name,
                "project_root": str(project_root)
            }
        }
        
        # Gather all database data
        clients, client_files = _gather_clients(session)
        data["clients"] = clients
        
        data["projects"] = [p.to_dict() for p in session.query(models.Project).all()]
        data["websites"] = [w.to_dict() for w in session.query(models.Website).all()]
        data["tasks"] = [t.to_dict() for t in session.query(models.Task).all()]
        
        docs, doc_files = _gather_documents(session)
        data["documents"] = docs

        data["rules"] = [r.to_dict() for r in session.query(models.Rule).all()]
        data["chats"] = _gather_chats(session)
        
        settings, setting_files = _gather_system_settings(session)
        data["system_settings"] = settings
        
        # Combine all file maps
        file_map: Dict[str, str] = {}
        file_map.update(client_files)
        file_map.update(doc_files)
        file_map.update(setting_files)
        
        # Create backup filename with optional custom name
        backup_name = _generate_backup_filename("full", name)
        zip_path = Path(config.BACKUP_DIR) / f"{backup_name}.zip"
        
        # Extract timestamp for installation instructions
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        os.makedirs(config.BACKUP_DIR, exist_ok=True)
        
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            
            # Create JSON manifest
            json_path = tmp / "guaardvark_backup.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            
            # Copy all referenced files to backup structure
            for rel, src in file_map.items():
                dest = tmp / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(src, dest)
                except Exception as e:
                    logger.warning("Failed to copy %s: %s", src, e)
            
            # Copy ALL critical system files
            critical_files = [
                # Startup and management scripts
                "start.sh",
                "stop.sh",
                "restart.sh",
                "start_redis.sh",
                "start_celery.sh",
                "start_postgres.sh",
                "manager",

                # Configuration files
                "backend/requirements.txt",
                "backend/requirements-base.txt",
                "backend/requirements-llm.txt",
                "backend/requirements-test.txt",
                "frontend/package.json",
                "frontend/package-lock.json",
                "pytest.ini",

                # Documentation directory and root docs
                "docs/",
                "LICENSE",
                "README.md",
                "INSTALL.md",
                "CLAUDE.md",
                "GEMINI.md",
                "FILE_GENERATION_GUIDE.md",
                "GPU_SETUP_README.md",
                "AUTOMATION_QUICKSTART.md",

                # Python configuration
                "sitecustomize.py",
                "run_tests.py",
                "test_runner.py",
                "task_manager.py",
                "first_run_diagnostic.py",
                "cleanup_system_rules.py",
                "load_memory.py",
                "memory_bridge.py",

                # Environment files (if they exist)
                ".env",
                ".env.example",
                ".env.automation.example",

                # Scripts directory
                "scripts/",

                # Core backend files
                "backend/__init__.py",
                "backend/app.py",
                "backend/config.py",
                "backend/models.py",
                "backend/celery_app.py",
                "backend/celery_tasks_isolated.py",
                "backend/socketio_events.py",
                "backend/socketio_instance.py",
                "backend/rule_utils.py",
                "backend/schema.sql",
                "backend/seed_data.py",
                "backend/seed_models.py",
                "backend/seed_rules.json",
                "backend/cuda_config.py",

                # Backend directories
                "backend/api/",
                "backend/services/",
                "backend/utils/",
                "backend/routes/",
                "backend/tools/",
                "backend/tests/",
                "backend/tasks/",
                "backend/migrations/",
                "backend/handlers/",
                "backend/agents/",
                "backend/plugins/",

                # Root level plugins directory
                "plugins/",

                # CLI tool (llx command)
                "cli/",

                # Frontend source code (excluding node_modules and dist)
                "frontend/src/",
                "frontend/public/",
                "frontend/index.html",
                "frontend/vite.config.js",
                "frontend/.eslintrc.cjs",
                "frontend/.eslintrc.json",
            ]

            # Data directory - selective inclusion (exclude runtime/temp data and models)
            data_includes = [
                "data/database/",      # Database files
                "data/logos/",         # Client/system logos
                "data/system/",        # System configuration files
                "data/uploads/",       # User uploaded files
                "data/context/",       # Conversation context JSON files
                "data/conversations/", # Conversation session JSON files
                # NOTE: data/models/ excluded - models can be downloaded on new machine
            ]
            
            # State JSON files in data root (explicit list to avoid temporary files)
            state_json_files = [
                "data/folder_state.json",
                "data/dashboard_state.json",
                "data/code_editor_state.json",
                "data/code_editor_session.json",
                "data/documents_windows_v2_state.json",
                "data/default__vector_store.json",
                "data/graph_store.json",
                "data/index_store.json",
                "data/docstore.json",
            ]

            # Empty directories to create (for runtime use on new machine)
            empty_dirs = [
                "logs",        # Empty logs directory (will be populated at runtime)
                "pids",        # Empty pids directory (will be populated at runtime)
                "data/outputs", # Generated outputs
                "data/models",  # AI models
                "backend/tools/voice/whisper.cpp",
                "backend/tools/voice/piper"
            ]

            # Copy critical files and directories
            for file_path in critical_files:
                src_path = project_root / file_path
                if src_path.exists():
                    if src_path.is_file():
                        # Copy single file
                        dest_path = tmp / file_path
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            shutil.copy2(src_path, dest_path)
                            logger.info("Copied file: %s", file_path)
                        except Exception as e:
                            logger.warning("Failed to copy file %s: %s", file_path, e)
                    elif src_path.is_dir():
                        # Copy directory (excluding certain patterns)
                        dest_dir = tmp / file_path
                        try:
                            # Use custom ignore function for plugins directory
                            if file_path == "plugins/":
                                ignore_func = _create_plugin_ignore_function()
                            else:
                                # Standard ignore function for other directories
                                ignore_func = shutil.ignore_patterns(
                                    *GLOBAL_IGNORE_PATTERNS,
                                    '*.bin',
                                    '*.onnx',
                                    '*.so',
                                    '*.so.*',
                                    '*.a',
                                    '*.o',
                                    'whisper-cli',
                                    'libwhisper*',
                                    'piper-models',
                                    'whisper-models'
                                )
                            
                            shutil.copytree(
                                src_path,
                                dest_dir,
                                ignore=ignore_func
                            )
                            logger.info("Copied directory: %s", file_path)
                        except Exception as e:
                            logger.warning("Failed to copy directory %s: %s", file_path, e)
                else:
                    logger.info("File/directory not found (skipping): %s", file_path)

            # Create PostgreSQL database dump
            pg_dump_path = tmp / "data" / "database" / "guaardvark.pgdump"
            if _create_pg_dump(pg_dump_path):
                data["pg_dump_included"] = True
                # Re-write manifest with pg_dump flag
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            else:
                logger.warning("PostgreSQL dump not included in full backup")

            # Copy selective data directories (exclude runtime/temporary data)
            for data_path in data_includes:
                src_path = project_root / data_path
                if src_path.exists() and src_path.is_dir():
                    dest_dir = tmp / data_path
                    try:
                        shutil.copytree(
                            src_path,
                            dest_dir,
                            ignore=shutil.ignore_patterns(
                                *DATA_IGNORE_PATTERNS,
                                '*.tmp'
                            )
                        )
                        logger.info("Copied data directory: %s", data_path)
                    except Exception as e:
                        logger.warning("Failed to copy data directory %s: %s", data_path, e)
                else:
                    # Create empty directory structure for missing data folders
                    dest_dir = tmp / data_path
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    logger.info("Created empty data directory: %s", data_path)
            
            # Copy state JSON files from data root
            for json_file in state_json_files:
                src_path = project_root / json_file
                if src_path.exists() and src_path.is_file():
                    dest_path = tmp / json_file
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(src_path, dest_path)
                        logger.info("Copied state file: %s", json_file)
                    except Exception as e:
                        logger.warning("Failed to copy state file %s: %s", json_file, e)
                else:
                    logger.debug("State file not found (skipping): %s", json_file)
            
            # Create installation instructions
            install_instructions = f"""# Guaardvark Full System Backup

## Backup Information
- **Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- **Type:** Full System Backup (code + data)

## Install

1. **Extract:**
   ```bash
   unzip {backup_name}.zip
   cd guaardvark
   ```

2. **Start:**
   ```bash
   ./start.sh
   ```

The startup script handles everything: dependencies, database, frontend build, and all services.

| Service | URL |
|---------|-----|
| Web UI | http://localhost:5173 |
| API | http://localhost:5000 |
| Health Check | http://localhost:5000/api/health |

## Troubleshooting

- Permission issues: `chmod +x *.sh`
- Health diagnostics: `./start.sh --test`
- Check logs in `logs/`
"""
            
            # Write installation instructions
            install_path = tmp / "INSTALL.md"
            with open(install_path, "w", encoding="utf-8") as f:
                f.write(install_instructions)

            # Create empty system directories for new installations
            for dir_name in empty_dirs:
                dir_path = tmp / dir_name
                dir_path.mkdir(parents=True, exist_ok=True)
                # Create a README file to preserve empty directory structure and explain purpose
                readme_path = dir_path / "README.md"
                readme_content = {
                    "logs": "# Logs Directory\n\nThis directory will contain runtime logs when the system is running.\nIt is intentionally empty in the backup to reduce file size.\n",
                    "pids": "# PIDs Directory\n\nThis directory will contain process ID files when the system is running.\nIt is intentionally empty in the backup to reduce file size.\n",
                    "data/outputs": "# Outputs Directory\n\nThis directory will contain generated outputs (images, videos, etc.).\nIt is intentionally empty in the backup to reduce file size.\n",
                    "data/models": "# Models Directory\n\nThis directory is for AI models.\nIt is intentionally empty in the backup as models can be downloaded or generated.\n",
                    "backend/tools/voice/whisper.cpp": "# Whisper.cpp Directory\n\nThis directory is for the Whisper.cpp tool.\nIt is intentionally empty in the backup following the 3rd-party software policy.\n",
                    "backend/tools/voice/piper": "# Piper Directory\n\nThis directory is for the Piper TTS tool.\nIt is intentionally empty in the backup following the 3rd-party software policy.\n"
                }
                with open(readme_path, "w") as f:
                    f.write(readme_content.get(dir_name, f"# {dir_name.title()} Directory\n\nRuntime directory.\n"))
                logger.info("Created empty directory with README: %s", dir_name)
            
            # Create ZIP archive
            with ZipFile(zip_path, "w", ZIP_DEFLATED) as zf:
                for root, _dirs, filenames in os.walk(tmp):
                    for name in filenames:
                        file_path = Path(root) / name
                        arcname = file_path.relative_to(tmp)
                        zf.write(file_path, arcname)
            
            logger.info("Full system backup created successfully: %s", zip_path)
            logger.info("Backup size: %.2f MB", zip_path.stat().st_size / (1024 * 1024))
            
        return str(zip_path)
        
    finally:
        if app_created:
            try:
                models.db.session.remove()
            except Exception:
                pass
            ctx.pop()


def create_code_release(name: str | None = None) -> str:
    """Create a code release backup - source code and configuration only, zero data.

    This backup includes:
    - All source code (backend, frontend)
    - All configuration files (requirements.txt, package.json, etc.)
    - All startup scripts (start.sh, stop.sh, etc.)
    - All documentation and guides
    - System configuration files (.env.example, etc.)

    This backup EXCLUDES:
    - Database files
    - User uploaded files
    - Client logos
    - Chat history
    - All runtime data
    - State JSON files

    Use this for distributing the codebase to new machines or open-source releases.
    Recipients run ./start.sh and get a fresh, clean installation.

    Args:
        name: Optional custom name for the backup file
    """
    logger.info("Creating code release backup (excluding all data)")
    
    app_created = False
    if not has_app_context():
        app = _create_app()
        ctx = app.app_context()
        ctx.push()
        app_created = True
    
    try:
        # Get project root directory
        project_root = Path(__file__).parent.parent.parent
        logger.info("Project root: %s", project_root)
        
        # Create backup filename with optional custom name
        backup_name = _generate_backup_filename("code_release", name)
        zip_path = Path(config.BACKUP_DIR) / f"{backup_name}.zip"
        
        # Extract timestamp for installation instructions
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        os.makedirs(config.BACKUP_DIR, exist_ok=True)
        
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            
            # Metadata about the backup (no data included)
            metadata = {
                "version": "1.0",
                "backup_type": "code_release",
                "timestamp": int(datetime.now().timestamp()),
                "description": "Code and configuration backup excluding all data",
                "backup_date": datetime.now().isoformat(),
                "system_info": {
                    "python_version": str(sys.version),
                    "platform": os.name,
                    "project_root": str(project_root)
                },
                "note": "This backup contains source code and configuration files only. No database, uploads, or user data is included.",
                "symlinks": {
                    "manager": "scripts/system-manager/system-manager"
                },
                "post_restore": "Run ./start.sh to install dependencies, provision databases, and start services."
            }
            
            # Write metadata JSON
            meta_path = tmp / "guaardvark_backup.json"
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)
            
            # Critical files and directories to include (code/config only)
            critical_files = [
                # Root configuration and startup files
                "README.md",
                "INSTALL.md",
                "CLAUDE.md",
                "GEMINI.md",
                "AUTOMATION_QUICKSTART.md",
                "start.sh",
                "stop.sh",
                "restart.sh",
                "start_celery.sh",
                "start_redis.sh",
                "start_postgres.sh",
                # NOTE: "manager" is a symlink to scripts/system-manager/system-manager
                # which is already included via "scripts/" below. start.sh recreates
                # the symlink on fresh machines, so we don't need to copy it here.
                "run_tests.py",
                ".env.example",
                ".env.automation.example",
                # NOTE: .env is handled separately below (sanitized for portability)
                
                # Scripts directory (contains install_pytorch.sh and other setup scripts)
                "scripts/",
                
                # Core backend files
                "backend/__init__.py",
                "backend/app.py",
                "backend/config.py",
                "backend/models.py",
                "backend/celery_app.py",
                "backend/celery_tasks_isolated.py",
                "backend/socketio_events.py",
                "backend/socketio_instance.py",
                "backend/rule_utils.py",
                "backend/schema.sql",
                "backend/seed_data.py",
                "backend/seed_models.py",
                "backend/seed_rules.json",
                "backend/cuda_config.py",
                "backend/requirements.txt",
                "backend/requirements-base.txt",
                "backend/requirements-llm.txt",
                
                # Backend directories (code only)
                "backend/api/",
                "backend/services/",
                "backend/utils/",
                "backend/routes/",
                "backend/tests/",
                "backend/tasks/",
                "backend/migrations/",
                "backend/handlers/",
                "backend/agents/",
                "backend/plugins/",
                
                # Backend tools directory (voice binaries excluded via ignore_patterns)
                "backend/tools/",
                
                # NOTE: plugins/ root directory excluded from code-only backups
                # It contains large user-generated data (training, outputs, etc.)
                # Only include in full_system backups if needed
                
                # Frontend source code (excluding node_modules and dist)
                "frontend/src/",
                "frontend/public/",
                "frontend/index.html",
                "frontend/vite.config.js",
                "frontend/.eslintrc.cjs",
                "frontend/.eslintrc.json",
                "frontend/package.json",
                "frontend/package-lock.json",

                # Root level scripts and config
                "pytest.ini",
                "test_runner.py",
                "task_manager.py",
                "first_run_diagnostic.py",
                "cleanup_system_rules.py",
                "load_memory.py",
                "memory_bridge.py",
                "LICENSE",

                # Root level plugins directory (code only)
                "plugins/",

                # CLI tool (llx command)
                "cli/",
            ]
            
            # Empty directories to create (for runtime use on new machine)
            empty_dirs = [
                "logs",        # Empty logs directory (will be populated at runtime)
                "pids",        # Empty pids directory (will be populated at runtime)
                "docs",        # Documentation (empty in code-only)
                "data/outputs", # Generated outputs
                "data/models",  # AI models
                "data/context", # Context files
                "data/conversations", # Conversation files
                "backend/tools/voice/whisper.cpp",
                "backend/tools/voice/piper"
            ]
            
            # Copy critical files and directories
            for file_path in critical_files:
                src_path = project_root / file_path
                if src_path.exists():
                    if src_path.is_file():
                        # Copy single file
                        dest_path = tmp / file_path
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            shutil.copy2(src_path, dest_path)
                            logger.info("Copied file: %s", file_path)
                        except Exception as e:
                            logger.warning("Failed to copy file %s: %s", file_path, e)
                    elif src_path.is_dir():
                        # Copy directory (excluding certain patterns)
                        dest_dir = tmp / file_path
                        try:
                            def ignore_func(dirname, names):
                                # Use smarter ignore for plugins
                                if 'plugins' in dirname:
                                    # Reuse the plugin ignore logic but adapted for this context
                                    ignored = set(shutil.ignore_patterns(
                                        *GLOBAL_IGNORE_PATTERNS,
                                        '*.bin', '*.onnx', '*.so', '*.so.*', '*.a', '*.o',
                                        'piper-models', 'whisper-models'
                                    )(dirname, names))
                                    
                                    # If we are in the training directory, ignore the data subdirs
                                    rel_dir = os.path.basename(dirname)
                                    if rel_dir == 'training' or dirname.endswith('plugins/training'):
                                        for name in names:
                                            if name in ['datasets', 'processed', 'raw_transcripts', 'output', 'batch_input']:
                                                ignored.add(name)
                                    return ignored

                                ignored = set(shutil.ignore_patterns(
                                    *GLOBAL_IGNORE_PATTERNS,
                                    '*.bin',
                                    '*.onnx',
                                    '*.so',
                                    '*.so.*',
                                    '*.a',
                                    '*.o',
                                    'whisper-cli',
                                    'libwhisper*',
                                    'piper-models',
                                    'whisper-models'
                                )(dirname, names))
                                return ignored
                            
                            shutil.copytree(
                                src_path,
                                dest_dir,
                                ignore=ignore_func
                            )
                            logger.info("Copied directory: %s", file_path)
                        except Exception as e:
                            logger.warning("Failed to copy directory %s: %s", file_path, e)
                else:
                    logger.info("File/directory not found (skipping): %s", file_path)

            # Sanitize .env for portability: strip machine-specific values that
            # start_postgres.sh and start.sh auto-generate on first launch.
            env_src = project_root / ".env"
            if env_src.exists():
                # Keys that are auto-generated per-machine and must not be carried over
                _machine_specific_keys = {"DATABASE_URL", "GUAARDVARK_ROOT"}
                # Paths that indicate machine-specific values
                _machine_paths = [str(project_root), os.path.expanduser("~")]
                sanitized_lines = [
                    "# Sanitized for code release — machine-specific values removed.",
                    "# DATABASE_URL is auto-generated by start_postgres.sh on first launch.",
                    "",
                ]
                with open(env_src, "r", encoding="utf-8") as f:
                    for line in f:
                        stripped = line.strip()
                        if not stripped or stripped.startswith("#"):
                            sanitized_lines.append(stripped)
                            continue
                        key = stripped.split("=", 1)[0] if "=" in stripped else ""
                        if key in _machine_specific_keys:
                            sanitized_lines.append(f"# {key}=  # auto-generated on first launch")
                            continue
                        # Strip any value containing machine-specific paths
                        if "=" in stripped and any(p in stripped.split("=", 1)[1] for p in _machine_paths):
                            sanitized_lines.append(f"# {key}=  # contained machine-specific path")
                            continue
                        sanitized_lines.append(stripped)
                env_dest = tmp / ".env"
                with open(env_dest, "w", encoding="utf-8") as f:
                    f.write("\n".join(sanitized_lines) + "\n")
                logger.info("Copied sanitized .env (stripped machine-specific values)")

            # Create empty data directory structure (without copying actual data)
            data_dirs = [
                "data/database/",
                "data/logos/",
                "data/system/",
                "data/uploads/",
            ]
            for data_dir in data_dirs:
                dest_dir = tmp / data_dir
                dest_dir.mkdir(parents=True, exist_ok=True)
                # Create README explaining the directory is intentionally empty
                readme_path = dest_dir / "README.md"
                readme_content = f"""# {data_dir} Directory

This directory is intentionally empty in this code-only backup.

This backup contains source code and configuration files only. No data files
(database, uploads, logos, etc.) are included.

To restore data, use a separate data backup or start with a fresh installation.
"""
                with open(readme_path, "w", encoding="utf-8") as f:
                    f.write(readme_content)
                logger.info("Created empty data directory: %s", data_dir)
            
            # Create installation instructions
            install_instructions = f"""# Guaardvark Code Release

## Backup Information
- **Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- **Type:** Code Release (no data — database and files are created fresh on first run)

## Install

1. **Extract:**
   ```bash
   unzip {backup_name}.zip
   cd guaardvark
   ```

2. **Start:**
   ```bash
   ./start.sh
   ```

The startup script handles everything: dependencies, database, frontend build, and all services.

| Service | URL |
|---------|-----|
| Web UI | http://localhost:5173 |
| API | http://localhost:5000 |
| Health Check | http://localhost:5000/api/health |

## Troubleshooting

- Permission issues: `chmod +x *.sh`
- Health diagnostics: `./start.sh --test`
- Check logs in `logs/`

## Data

To restore existing data, use a separate Guaardvark data backup.
"""
            
            # Write installation instructions
            install_path = tmp / "INSTALL.md"
            with open(install_path, "w", encoding="utf-8") as f:
                f.write(install_instructions)
            
            # Create empty system directories for new installations
            for dir_name in empty_dirs:
                dir_path = tmp / dir_name
                dir_path.mkdir(parents=True, exist_ok=True)
                # Create a README file to preserve empty directory structure
                readme_path = dir_path / "README.md"
                readme_content = {
                    "logs": "# Logs Directory\n\nThis directory will contain runtime logs when the system is running.\nIt is intentionally empty in the backup to reduce file size.\n",
                    "pids": "# PIDs Directory\n\nThis directory will contain process ID files when the system is running.\nIt is intentionally empty in the backup to reduce file size.\n",
                    "docs": "# Docs Directory\n\nThis directory is for documentation.\nIt is intentionally empty in this code-only backup.\n",
                    "data/outputs": "# Outputs Directory\n\nThis directory will contain generated outputs.\nIt is intentionally empty in this code-only backup.\n",
                    "data/models": "# Models Directory\n\nThis directory is for AI models.\nIt is intentionally empty in this code-only backup.\n",
                    "data/context": "# Context Directory\n\nThis directory is for conversation context files.\nIt is intentionally empty in this code-only backup.\n",
                    "data/conversations": "# Conversations Directory\n\nThis directory is for conversation session files.\nIt is intentionally empty in this code-only backup.\n",
                    "backend/tools/voice/whisper.cpp": "# Whisper.cpp Directory\n\nThis directory is for the Whisper.cpp tool.\nIt is intentionally empty in this code-only backup following the 3rd-party software policy.\n",
                    "backend/tools/voice/piper": "# Piper Directory\n\nThis directory is for the Piper TTS tool.\nIt is intentionally empty in this code-only backup following the 3rd-party software policy.\n"
                }
                with open(readme_path, "w", encoding="utf-8") as f:
                    f.write(readme_content.get(dir_name, f"# {dir_name.title()} Directory\n\nRuntime directory.\n"))
                logger.info("Created empty directory with README: %s", dir_name)
            
            # Create ZIP archive
            with ZipFile(zip_path, "w", ZIP_DEFLATED) as zf:
                for root, _dirs, filenames in os.walk(tmp):
                    for name in filenames:
                        file_path = Path(root) / name
                        arcname = file_path.relative_to(tmp)
                        zf.write(file_path, arcname)
            
            logger.info("Code release backup created successfully: %s", zip_path)
            logger.info("Backup size: %.2f MB", zip_path.stat().st_size / (1024 * 1024))
            
        return str(zip_path)
        
    finally:
        if app_created:
            try:
                models.db.session.remove()
            except Exception:
                pass
            ctx.pop()


def _safe_extract(zf: ZipFile, member: str, dest_root: Path) -> Path | None:
    dest = dest_root / member
    dest = dest.resolve()
    if not str(dest).startswith(str(dest_root.resolve())):
        logger.warning("Skipping suspicious path %s", member)
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(member) as src, open(dest, "wb") as dst:
        dst.write(src.read())
    return dest


def _load_json(p: Path) -> dict:
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _restore_clients(data: list[dict]) -> int:
    count = 0
    upload_base = Path(config.UPLOAD_FOLDER)

    for c in data:
        name = c.get("name")
        if not name:
            continue
        existing = models.Client.query.filter_by(name=name).first()
        if existing:
            client = existing
        else:
            client = models.Client(name=name)
            models.db.session.add(client)

        # Restore all client fields
        client.email = c.get("email")
        client.phone = c.get("phone")
        client.description = c.get("description")
        client.notes = c.get("notes")
        client.contact_url = c.get("contact_url")
        client.location = c.get("location")
        client.primary_service = c.get("primary_service")
        client.secondary_service = c.get("secondary_service")
        client.brand_tone = c.get("brand_tone")
        client.business_hours = c.get("business_hours")

        # Handle JSON array fields - store as JSON strings in database
        social_links = c.get("social_links")
        if isinstance(social_links, list):
            client.social_links = json.dumps(social_links)
        else:
            client.social_links = social_links

        # Handle JSON array fields - store as JSON strings in database
        industry = c.get("industry")
        if isinstance(industry, list):
            client.industry = json.dumps(industry)
        else:
            client.industry = industry

        target_audience = c.get("target_audience")
        if isinstance(target_audience, list):
            client.target_audience = json.dumps(target_audience)
        else:
            client.target_audience = target_audience

        unique_selling_points = c.get("unique_selling_points")
        if isinstance(unique_selling_points, list):
            client.unique_selling_points = json.dumps(unique_selling_points)
        else:
            client.unique_selling_points = unique_selling_points

        competitor_urls = c.get("competitor_urls")
        if isinstance(competitor_urls, list):
            client.competitor_urls = json.dumps(competitor_urls)
        else:
            client.competitor_urls = competitor_urls

        client.brand_voice_examples = c.get("brand_voice_examples")

        keywords = c.get("keywords")
        if isinstance(keywords, list):
            client.keywords = json.dumps(keywords)
        else:
            client.keywords = keywords

        content_goals = c.get("content_goals")
        if isinstance(content_goals, list):
            client.content_goals = json.dumps(content_goals)
        else:
            client.content_goals = content_goals

        client.regulatory_constraints = c.get("regulatory_constraints")

        geographic_coverage = c.get("geographic_coverage")
        if isinstance(geographic_coverage, list):
            client.geographic_coverage = json.dumps(geographic_coverage)
        else:
            client.geographic_coverage = geographic_coverage

        # Handle logo path: copy from extracted location to proper UPLOAD_FOLDER location
        logo_path = c.get("logo_path")
        if logo_path:
            # Logo was extracted to project_root/data/logos/client_X_filename.png
            project_root = Path(config.GUAARDVARK_ROOT)
            extracted_logo = project_root / logo_path

        if extracted_logo.is_file():
            try:
                if extracted_logo.resolve().is_relative_to(upload_base.resolve()):
                    rel_path = extracted_logo.resolve().relative_to(upload_base.resolve())
                    client.logo_path = rel_path.as_posix()
                else:
                    # Legacy backup path – place under logos/ using original name
                    rel_path = Path("logos") / extracted_logo.name
                    dest_file = upload_base / rel_path
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(extracted_logo, dest_file)
                    client.logo_path = rel_path.as_posix()
                logger.info("Restored logo for client %s: %s", name, client.logo_path)
            except Exception as e:
                logger.warning("Failed to restore logo for client %s: %s", name, e)
                client.logo_path = None
            else:
                logger.warning("Logo file not found for client %s: %s", name, extracted_logo)
                client.logo_path = None
        else:
            client.logo_path = None

        count += 1
    models.db.session.commit()
    return count


def _restore_projects(data: list[dict]) -> int:
    count = 0
    for p in data:
        name = p.get("name")
        if not name:
            continue
        proj = models.Project.query.filter_by(name=name).first() or models.Project(
            name=name
        )
        proj.description = p.get("description")
        proj.client_id = p.get("client_id")
        models.db.session.add(proj)
        count += 1
    models.db.session.commit()
    return count


def _restore_websites(data: list[dict]) -> int:
    count = 0
    for w in data:
        url = w.get("url")
        if not url:
            continue
        site = models.Website.query.filter_by(url=url).first() or models.Website(
            url=url
        )
        site.sitemap = w.get("sitemap")
        site.status = w.get("status")
        site.project_id = w.get("project_id")
        site.client_id = w.get("client_id")
        models.db.session.add(site)
        count += 1
    models.db.session.commit()
    return count


def _restore_tasks(data: list[dict]) -> int:
    count = 0
    for t in data:
        name = t.get("name")
        if not name:
            continue
        task = models.Task(name=name)
        task.status = t.get("status")
        task.priority = t.get("priority")
        task.type = t.get("type")
        task.description = t.get("description")
        task.project_id = t.get("project_id")
        models.db.session.add(task)
        count += 1
    models.db.session.commit()
    return count


def _restore_documents(data: list[dict]) -> int:
    count = 0
    upload_base = Path(config.UPLOAD_FOLDER)
    project_root = Path(config.GUAARDVARK_ROOT)

    for d in data:
        backup_path = d.get("path")
        if not backup_path:
            continue

        # Document files are extracted to project_root/files/doc_X_filename
        extracted_file = (project_root / backup_path).resolve()
        relative_path = None

        if extracted_file.is_file():
            try:
                if extracted_file.is_relative_to(upload_base.resolve()):
                    rel = extracted_file.relative_to(upload_base.resolve())
                    relative_path = rel.as_posix()
                    dest_file = extracted_file  # already in place
                else:
                    # Legacy layout (e.g., files/doc_*). Place under uploads root.
                    rel = Path(backup_path).name
                    relative_path = rel if isinstance(rel, str) else rel.as_posix()
                    dest_file = upload_base / relative_path
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(extracted_file, dest_file)
                logger.info("Restored document file: %s", relative_path)
            except Exception as e:
                logger.warning("Failed to restore document file %s: %s", backup_path, e)
                relative_path = None
        else:
            logger.warning("Document file not found: %s", extracted_file)
            relative_path = None

        if not relative_path:
            continue

        # Check if document already exists by path
        doc = models.Document.query.filter_by(path=relative_path).first()
        if not doc:
            doc = models.Document(
                filename=d.get("filename", os.path.basename(relative_path)),
                path=relative_path
            )
            models.db.session.add(doc)

        doc.type = d.get("type")
        doc.project_id = d.get("project_id")
        doc.website_id = d.get("website_id")
        tags = d.get("tags")
        if isinstance(tags, list):
            tags = json.dumps(tags)
        doc.tags = tags
        count += 1

    models.db.session.commit()
    return count


def _restore_rules(data: list[dict]) -> int:
    count = 0
    for r in data:
        name = r.get("name")
        level = r.get("level")
        if not name or not level:
            continue
        rule = models.Rule.query.filter_by(
            name=name, level=level
        ).first() or models.Rule(name=name, level=level)
        rule.type = r.get("type")
        rule.command_label = r.get("command_label")
        rule.rule_text = r.get("rule_text")
        rule.description = r.get("description")
        rule.project_id = r.get("project_id")
        rule.target_models_json = json.dumps(r.get("target_models", ["__ALL__"]))
        rule.is_active = r.get("is_active", True)
        models.db.session.add(rule)
        count += 1
    models.db.session.commit()
    return count


def _restore_system_settings(data: list[dict]) -> int:
    count = 0
    upload_base = Path(config.UPLOAD_FOLDER)
    project_root = Path(config.GUAARDVARK_ROOT)

    for s in data:
        key = s.get("key")
        if not key:
            continue

        row = models.SystemSetting.query.get(key) or models.SystemSetting(key=key)
        value = s.get("value")

        # Handle logo_path specially - copy from extracted location to UPLOAD_FOLDER
        if key == "logo_path" and value:
            extracted_logo = (project_root / value).resolve()

            if extracted_logo.is_file():
                try:
                    if extracted_logo.is_relative_to(upload_base.resolve()):
                        rel_path = extracted_logo.relative_to(upload_base.resolve()).as_posix()
                        row.value = rel_path
                        logger.info("Restored system logo: %s", row.value)
                    else:
                        rel_path = Path("system") / extracted_logo.name
                        dest_file = upload_base / rel_path
                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(extracted_logo, dest_file)
                        row.value = rel_path.as_posix()
                        logger.info("Restored system logo (legacy path): %s", row.value)
                except Exception as e:
                    logger.warning("Failed to restore system logo: %s", e)
                    row.value = value
            else:
                logger.warning("System logo file not found: %s", extracted_logo)
                row.value = value
        else:
            row.value = value

        models.db.session.add(row)
        count += 1

    models.db.session.commit()
    return count


def restore_backup(zip_file: str) -> Dict[str, int]:
    """Restore a backup from ``zip_file``.

    Extracts files and restores database records from the backup archive.
    After restore completes, run ``./start.sh`` to install Python/Node
    dependencies, provision databases, set up the CLI tool, and start
    services. The restore itself does NOT install dependencies.
    """
    app_created = False
    if not has_app_context():
        app = _create_app()
        ctx = app.app_context()
        ctx.push()
        app_created = True
    summary = {}
    try:
        _ensure_document_folder_column()
        with ZipFile(zip_file, "r") as zf:
            tmpdir = TemporaryDirectory()
            tmp_path = Path(tmpdir.name)
            meta_path = None

            # First extract the manifest
            for m in zf.namelist():
                if m.endswith("/"):
                    continue
                if m == "guaardvark_backup.json":
                    meta_path = _safe_extract(zf, m, tmp_path)
                    break
            if not meta_path:
                raise ValueError("No backup manifest found in ZIP file")
            data = _load_json(meta_path)

            # Extract all files to the appropriate locations
            project_root = Path(config.GUAARDVARK_ROOT)
            for member in zf.namelist():
                if member.endswith("/") or member == "guaardvark_backup.json":
                    continue

                # Extract to temporary location first
                temp_file = _safe_extract(zf, member, tmp_path)
                if temp_file:
                    # Determine the final destination, ensuring it stays under project_root
                    dest_path = (project_root / member).resolve()
                    if not str(dest_path).startswith(str(project_root.resolve())):
                        logger.warning("Skipping suspicious restore path: %s", member)
                        continue
                    dest_path.parent.mkdir(parents=True, exist_ok=True)

                    try:
                        shutil.copy2(temp_file, dest_path)
                        logger.info("Restored file: %s", dest_path)
                    except Exception as e:
                        logger.warning("Failed to restore file %s: %s", member, e)
            # Recreate symlinks recorded in the manifest
            symlinks = data.get("symlinks", {})
            for link_name, link_target in symlinks.items():
                link_path = project_root / link_name
                target_path = project_root / link_target
                if target_path.exists():
                    try:
                        if link_path.exists() or link_path.is_symlink():
                            link_path.unlink()
                        os.symlink(link_target, link_path)
                        logger.info("Recreated symlink: %s -> %s", link_name, link_target)
                        summary.setdefault("symlinks_restored", 0)
                        summary["symlinks_restored"] += 1
                    except Exception as e:
                        logger.warning("Failed to create symlink %s -> %s: %s", link_name, link_target, e)
                else:
                    logger.warning("Symlink target not found: %s -> %s", link_name, link_target)

            # Restore PostgreSQL dump if present
            if data.get("pg_dump_included"):
                pg_dump_file = project_root / "data" / "database" / "guaardvark.pgdump"
                if pg_dump_file.is_file():
                    if _restore_pg_dump(pg_dump_file):
                        summary["pg_restore"] = "success"
                        logger.info("PostgreSQL database restored from dump")
                    else:
                        summary["pg_restore"] = "failed"
                        logger.error("PostgreSQL restore failed, falling back to JSON data")
                else:
                    summary["pg_restore"] = "dump_missing"
                    logger.warning("pg_dump_included flag set but dump file not found")

            # Restore from JSON data (fallback or supplement to pg_dump)
            if data.get("clients"):
                summary["clients"] = _restore_clients(data["clients"])
            if data.get("projects"):
                summary["projects"] = _restore_projects(data["projects"])
            if data.get("websites"):
                summary["websites"] = _restore_websites(data["websites"])
            if data.get("tasks"):
                summary["tasks"] = _restore_tasks(data["tasks"])
            if data.get("documents"):
                summary["documents"] = _restore_documents(data["documents"])
            if data.get("rules"):
                summary["rules"] = _restore_rules(data["rules"])
            if data.get("system_settings"):
                summary["system_settings"] = _restore_system_settings(
                    data["system_settings"]
                )
    finally:
        if app_created:
            try:
                models.db.session.remove()
            except Exception:
                pass
            ctx.pop()
    return summary


def list_backups() -> list:
    """Return list of backup entries with metadata (name, size, type, date)."""
    if not os.path.isdir(config.BACKUP_DIR):
        return []
    entries = []
    for f in sorted(os.listdir(config.BACKUP_DIR)):
        if not f.endswith(".zip"):
            continue
        path = os.path.join(config.BACKUP_DIR, f)
        try:
            stat = os.stat(path)
            size = stat.st_size
            mtime = stat.st_mtime
        except OSError:
            size = 0
            mtime = 0

        # Detect backup type from filename
        if "full_backup" in f or "system_backup" in f:
            btype = "full"
        elif "code_release" in f:
            btype = "code"
        elif "auto_daily" in f:
            btype = "auto"
        else:
            btype = "data"

        entries.append({
            "name": f,
            "size": size,
            "type": btype,
            "modified": mtime,
        })
    # Sort newest first
    entries.sort(key=lambda e: e["modified"], reverse=True)
    return entries


def delete_backup(filename: str) -> bool:
    """Delete a backup file."""
    path = os.path.join(config.BACKUP_DIR, filename)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False
