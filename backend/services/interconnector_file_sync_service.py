import json
import logging
import os
import re
import hashlib
import shutil
import fnmatch
import gzip
import base64
from itertools import islice
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from flask import current_app

logger = logging.getLogger(__name__)

MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 30 * 1024 * 1024
MAX_FILE_COUNT = 1500


class InterconnectorFileSyncService:

    def __init__(self):
        self.default_sync_paths = [
            "backend/api/",
            "backend/services/",
            "backend/utils/",
            "backend/routes/",
            "backend/handlers/",
            "backend/plugins/",
            "backend/tools/",
            "backend/tasks/",
            "backend/migrations/",
            "backend/tests/",
            "backend/app.py",
            "backend/celery_app.py",
            "backend/celery_tasks_isolated.py",
            "backend/config.py",
            "backend/cuda_config.py",
            "backend/__init__.py",
            "backend/models.py",
            "backend/rule_utils.py",
            "backend/seed_data.py",
            "backend/seed_models.py",
            "backend/socketio_events.py",
            "backend/socketio_instance.py",
            "backend/requirements.txt",
            "backend/requirements-base.txt",
            "frontend/src/",
            "frontend/package.json",
            "frontend/package-lock.json",
            "scripts/",
            "start.sh",
            "stop.sh",
            "start_redis.sh",
            "start_celery.sh",
            "start_postgres.sh",
            "run_tests.py",
            "CLAUDE.md",
            "cli/",
            "manager",
        ]

        self.exclude_patterns = [
            "__pycache__",
            ".pyc",
            ".pyo",
            ".pyd",
            "node_modules",
            ".git",
            ".env",
            "venv/",
            "env/",
            "*.log",
            "*.db",
            "*.pid",
            "dist/",
            "build/",
            "data/",
            "logs/",
            "pids/",
            "backups/",
            "*.zip",
            ".safetensors",
            ".safetensors.index.json",
            "backend/venv/",
            "backend/__pycache__/",
            "backend/data/",
            "backend/logs/",
            "backend/pids/",
            "frontend/node_modules/",
            "frontend/dist/",
            "frontend/.env",
            "backend/tools/voice/whisper.cpp/",
            "backend/tools/voice/piper-models/",
            "backend/tools/voice/piper/",
            ".egg-info",
        ]

    def get_file_hash(self, file_path: str) -> Optional[str]:
        try:
            with open(file_path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception as e:
            logger.error(f"Error calculating hash for {file_path}: {e}")
            return None

    def should_exclude_file(
        self, file_path: str, patterns: Optional[List[str]] = None
    ) -> bool:
        file_path_lower = file_path.lower()
        file_name = Path(file_path).name.lower()
        active_patterns = patterns or self.exclude_patterns

        for pattern in active_patterns:
            pattern_lower = pattern.lower()

            if pattern_lower.startswith("*."):
                extension = pattern_lower[1:]  # Remove "*"
                if file_path_lower.endswith(extension):
                    logger.debug(
                        f"[FILE_SYNC] File excluded by pattern '{pattern}': {file_path}"
                    )
                    return True

            elif pattern_lower.endswith("/"):
                if pattern_lower.rstrip("/") in file_path_lower:
                    logger.debug(
                        f"[FILE_SYNC] File excluded by pattern '{pattern}': {file_path}"
                    )
                    return True

            else:
                if pattern_lower in file_path_lower:
                    logger.debug(
                        f"[FILE_SYNC] File excluded by pattern '{pattern}': {file_path}"
                    )
                    return True
                if pattern_lower in file_name:
                    logger.debug(
                        f"[FILE_SYNC] File excluded by pattern '{pattern}' (filename match): {file_path}"
                    )
                    return True

        return False

    def get_project_root(self) -> Path:
        logger.debug("[FILE_SYNC] Detecting project root...")

        llamax_root = os.environ.get("GUAARDVARK_ROOT")
        if llamax_root:
            root_path = Path(llamax_root).resolve()
            logger.info(
                f"[FILE_SYNC] Project root from GUAARDVARK_ROOT env: {root_path}"
            )
            return root_path

        try:
            if current_app:
                upload_folder = current_app.config.get("UPLOAD_FOLDER", "")
                if upload_folder:
                    upload_path = Path(upload_folder)
                    if (
                        upload_path.name == "uploads"
                        and upload_path.parent.name == "data"
                    ):
                        root_path = upload_path.parent.parent
                        logger.info(
                            f"[FILE_SYNC] Project root inferred from UPLOAD_FOLDER: {root_path}"
                        )
                        return root_path
        except Exception as e:
            logger.debug(f"[FILE_SYNC] Error getting root from current_app: {e}")

        cwd = Path.cwd()
        logger.debug(f"[FILE_SYNC] Current working directory: {cwd}")
        if (cwd / "backend").exists() and (cwd / "frontend").exists():
            logger.info(f"[FILE_SYNC] Project root detected from CWD: {cwd}")
            return cwd

        current_file = Path(__file__)
        logger.debug(f"[FILE_SYNC] Current file location: {current_file}")
        potential_root = current_file.parent.parent.parent
        logger.debug(f"[FILE_SYNC] Potential root from file location: {potential_root}")
        if (potential_root / "backend").exists() and (
            potential_root / "frontend"
        ).exists():
            logger.info(
                f"[FILE_SYNC] Project root detected from file location: {potential_root}"
            )
            return potential_root

        logger.warning(f"[FILE_SYNC] Using CWD as fallback project root: {cwd}")
        return cwd

    def scan_files(
        self,
        sync_paths: Optional[List[str]] = None,
        since: Optional[datetime] = None,
        include_content: bool = True,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ) -> List[Dict]:
        project_root = self.get_project_root()
        logger.info(f"[FILE_SYNC] Scanning files from project root: {project_root}")

        if not sync_paths:
            sync_paths = self.default_sync_paths
            logger.info(f"[FILE_SYNC] Using default sync paths: {sync_paths}")
        else:
            logger.info(f"[FILE_SYNC] Using custom sync paths: {sync_paths}")

        if since:
            logger.info(f"[FILE_SYNC] Filtering files modified after: {since}")

        files_list = []
        excluded_count = 0
        total_bytes = 0
        patterns_exclude = (exclude_patterns or []) + self.exclude_patterns
        patterns_include = include_patterns or []

        for sync_path in sync_paths:
            if os.path.isabs(sync_path):
                full_path = Path(sync_path).resolve()
                try:
                    full_path.relative_to(project_root)
                except ValueError:
                    logger.warning(
                        f"[FILE_SYNC] Skipping absolute path outside project root: {full_path}"
                    )
                    continue
            else:
                full_path = (project_root / sync_path).resolve()

            logger.debug(f"[FILE_SYNC] Scanning path: {sync_path} -> {full_path}")

            if not full_path.exists():
                logger.warning(f"[FILE_SYNC] Sync path does not exist: {full_path}")
                continue

            if full_path.is_file():
                scanned = self._scan_file(
                    full_path,
                    project_root,
                    since,
                    include_content,
                    patterns_include,
                    patterns_exclude,
                )
                files_list.extend(scanned)
                if len(scanned) == 0 and not self.should_exclude_file(
                    str(full_path), patterns_exclude
                ):
                    excluded_count += 1
            elif full_path.is_dir():
                scanned = self._scan_directory(
                    full_path,
                    project_root,
                    since,
                    include_content,
                    patterns_include,
                    patterns_exclude,
                )
                files_list.extend(scanned)
                logger.debug(
                    f"[FILE_SYNC] Scanned directory {sync_path}: found {len(scanned)} files"
                )

            if len(files_list) > MAX_FILE_COUNT:
                logger.warning(
                    f"[FILE_SYNC] Reached max file count limit ({MAX_FILE_COUNT}), truncating results."
                )
                files_list = list(islice(files_list, MAX_FILE_COUNT))
                break

            total_bytes = sum(f.get("size", 0) for f in files_list)
            if total_bytes > MAX_TOTAL_BYTES:
                logger.warning(
                    f"[FILE_SYNC] Reached max total size limit ({MAX_TOTAL_BYTES} bytes), truncating results."
                )
                trimmed = []
                running = 0
                for f in files_list:
                    if running + f.get("size", 0) > MAX_TOTAL_BYTES:
                        break
                    trimmed.append(f)
                    running += f.get("size", 0)
                files_list = trimmed
                break

        logger.info(
            f"[FILE_SYNC] File scan complete: {len(files_list)} files found, "
            f"{excluded_count} files excluded"
        )
        return files_list

    def _scan_file(
        self,
        file_path: Path,
        project_root: Path,
        since: Optional[datetime] = None,
        include_content: bool = True,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ) -> List[Dict]:
        files_list = []

        file_str = str(file_path)
        if self.should_exclude_file(file_str, exclude_patterns):
            logger.debug(f"[FILE_SYNC] File excluded: {file_path}")
            return files_list

        if include_patterns:
            matched = any(fnmatch.fnmatch(file_str, pat) for pat in include_patterns)
            if not matched:
                return files_list

        try:
            stat = file_path.stat()
            modified_time = datetime.fromtimestamp(stat.st_mtime)

            if since and modified_time < since:
                logger.debug(
                    f"[FILE_SYNC] File skipped (too old): {file_path} (modified: {modified_time})"
                )
                return files_list

            try:
                rel_path = file_path.relative_to(project_root)
            except ValueError:
                logger.warning(
                    f"[FILE_SYNC] File outside project root, skipping: {file_path}"
                )
                return files_list

            if stat.st_size > MAX_FILE_SIZE_BYTES:
                logger.warning(
                    f"[FILE_SYNC] Skipping large file (> {MAX_FILE_SIZE_BYTES} bytes): {file_path}"
                )
                return files_list

            file_hash = self.get_file_hash(str(file_path))
            if not file_hash:
                logger.warning(
                    f"[FILE_SYNC] Could not calculate hash for file: {file_path}"
                )
                return files_list

            if include_content:
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    if not content:
                        logger.warning(
                            f"[FILE_SYNC] File content is empty: {file_path}"
                        )
                        return files_list

                    compressed = gzip.compress(content.encode("utf-8"))
                    files_list.append(
                        {
                            "path": str(rel_path),
                            "hash": file_hash,
                            "size": stat.st_size,
                            "modified_at": modified_time.isoformat(),
                            "content_compressed": base64.b64encode(compressed).decode(
                                "ascii"
                            ),
                            "compression": "gzip",
                            "original_size": len(content.encode("utf-8")),
                            "compressed_size": len(compressed),
                        }
                    )
                except (UnicodeDecodeError, TypeError):
                    # Binary file — encode with base64 instead of skipping
                    try:
                        with open(file_path, "rb") as f:
                            raw = f.read()
                        compressed = gzip.compress(raw)
                        files_list.append(
                            {
                                "path": str(rel_path),
                                "hash": file_hash,
                                "size": stat.st_size,
                                "modified_at": modified_time.isoformat(),
                                "content_compressed": base64.b64encode(
                                    compressed
                                ).decode("ascii"),
                                "compression": "gzip",
                                "content_type": "binary",
                                "original_size": len(raw),
                                "compressed_size": len(compressed),
                            }
                        )
                        logger.info(
                            f"[FILE_SYNC] Binary file encoded: {file_path} ({len(raw)} bytes)"
                        )
                    except Exception as e:
                        logger.warning(
                            f"[FILE_SYNC] Could not read binary file {file_path}: {e}"
                        )
                    return files_list
                except Exception as e:
                    logger.warning(f"[FILE_SYNC] Could not read file {file_path}: {e}")
                    return files_list
            else:
                files_list.append(
                    {
                        "path": str(rel_path),
                        "hash": file_hash,
                        "size": stat.st_size,
                        "modified_at": modified_time.isoformat(),
                    }
                )

        except Exception as e:
            logger.error(
                f"[FILE_SYNC] Error scanning file {file_path}: {e}", exc_info=True
            )

        return files_list

    def _scan_directory(
        self,
        dir_path: Path,
        project_root: Path,
        since: Optional[datetime] = None,
        include_content: bool = True,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ) -> List[Dict]:
        files_list = []

        try:
            for item in dir_path.rglob("*"):
                if item.is_file():
                    files_list.extend(
                        self._scan_file(
                            item,
                            project_root,
                            since,
                            include_content,
                            include_patterns,
                            exclude_patterns,
                        )
                    )
        except Exception as e:
            logger.error(f"Error scanning directory {dir_path}: {e}")

        return files_list

    def validate_files_batch(
        self, files_list: List[Dict]
    ) -> Tuple[List[Dict], List[Dict]]:
        valid_files = []
        invalid_files = []

        for f in files_list:
            path = f.get("path", "unknown")
            has_content = f.get("content") is not None or bool(
                f.get("content_compressed")
            )

            if has_content:
                valid_files.append(f)
            else:
                invalid_files.append(f)
                logger.warning(
                    f"[FILE_SYNC] File missing content, excluding from batch: {path}"
                )

        if invalid_files:
            logger.warning(
                f"[FILE_SYNC] Batch validation: {len(valid_files)} valid, "
                f"{len(invalid_files)} invalid (missing content)"
            )

        return valid_files, invalid_files

    def check_import_dependencies(
        self, files_list: List[Dict], frontend_root: str = "frontend/src"
    ) -> List[Dict[str, Any]]:
        missing_deps = []
        path_lookup = {f.get("path"): f for f in files_list if f.get("path")}
        path_variants = set()
        for p in path_lookup:
            path_variants.add(p)
            path_variants.add(p.replace("/", "\\"))
            base, ext = os.path.splitext(p)
            if not ext or ext in (".js", ".jsx", ".ts", ".tsx"):
                for e in [".js", ".jsx", ".ts", ".tsx"]:
                    path_variants.add(base + e)

        import_re = re.compile(
            r"""(?:import\s+(?:\{[^}]*\}|\*+\s+as\s+\w+|\w+)\s+from\s+|import\s+)['"]([^'"]+)['"]"""
        )
        require_re = re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""")

        for file_data in files_list:
            path = file_data.get("path", "")
            if not path or not path.startswith("frontend/"):
                continue
            content = None
            if file_data.get("content"):
                content = file_data["content"]
            elif (
                file_data.get("content_compressed")
                and file_data.get("compression") == "gzip"
            ):
                try:
                    compressed = base64.b64decode(file_data["content_compressed"])
                    content = gzip.decompress(compressed).decode("utf-8")
                except Exception:
                    continue
            if not content:
                continue

            project_root = self.get_project_root()
            dir_path = project_root / Path(path).parent
            for match in import_re.finditer(content):
                imp = match.group(1)
                if imp.startswith("."):
                    try:
                        resolved = str(
                            (dir_path / imp).resolve().relative_to(project_root)
                        ).replace("\\", "/")
                    except ValueError:
                        continue
                else:
                    continue
                found = False
                for variant in [
                    resolved,
                    resolved + ".js",
                    resolved + ".jsx",
                    resolved + ".ts",
                    resolved + ".tsx",
                ]:
                    if variant in path_variants or variant in path_lookup:
                        found = True
                        break
                if not found:
                    missing_deps.append(
                        {
                            "file": path,
                            "missing": imp,
                            "resolved_path": resolved,
                        }
                    )
            for match in require_re.finditer(content):
                imp = match.group(1)
                if imp.startswith("."):
                    try:
                        resolved = str(
                            (dir_path / imp).resolve().relative_to(project_root)
                        ).replace("\\", "/")
                    except ValueError:
                        continue
                else:
                    continue
                found = False
                for variant in [
                    resolved,
                    resolved + ".js",
                    resolved + ".jsx",
                    resolved + ".ts",
                    resolved + ".tsx",
                ]:
                    if variant in path_variants or variant in path_lookup:
                        found = True
                        break
                if not found:
                    already_reported = any(
                        m["file"] == path and m["missing"] == imp for m in missing_deps
                    )
                    if not already_reported:
                        missing_deps.append(
                            {
                                "file": path,
                                "missing": imp,
                                "resolved_path": resolved,
                            }
                        )

        if missing_deps:
            logger.warning(
                f"[FILE_SYNC] Import dependency check: {len(missing_deps)} missing dependencies found"
            )
            for m in missing_deps[:5]:
                logger.warning(
                    f"[FILE_SYNC]   {m['file']} imports {m['missing']} (not in batch)"
                )

        return missing_deps

    def apply_file(
        self,
        file_data: Dict,
        conflict_strategy: str = "last_write_wins",
        create_backup: bool = True,
    ) -> Tuple[bool, Optional[str], Dict]:
        stats = {
            "created": False,
            "updated": False,
            "skipped": False,
            "backed_up": False,
        }
        conflict_id = None

        project_root = self.get_project_root()
        relative_path = file_data.get("path", "")
        file_path = (project_root / relative_path).resolve()

        try:
            file_path.relative_to(project_root)
        except ValueError:
            logger.error(
                f"[FILE_SYNC] Refusing to write outside project root: {file_path}"
            )
            raise ValueError(f"Invalid file path outside project root: {relative_path}")

        logger.info(f"[FILE_SYNC] Applying file: {relative_path}")
        logger.debug(
            f"[FILE_SYNC] Project root: {project_root}, Full path: {file_path}"
        )

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            logger.debug(
                f"[FILE_SYNC] Ensured parent directory exists: {file_path.parent}"
            )
        except Exception as e:
            logger.error(
                f"[FILE_SYNC] Failed to create parent directory for {file_path}: {e}",
                exc_info=True,
            )
            raise

        if file_path.exists():
            logger.debug(
                f"[FILE_SYNC] File exists, checking for conflicts: {file_path}"
            )

            local_stat = file_path.stat()
            local_modified = datetime.fromtimestamp(local_stat.st_mtime)
            logger.debug(
                f"[FILE_SYNC] Local file modified: {local_modified} (size: {local_stat.st_size} bytes)"
            )

            local_hash = self.get_file_hash(str(file_path))
            remote_hash = file_data.get("hash")

            if local_hash == remote_hash:
                logger.info(
                    f"[FILE_SYNC] File unchanged (hash match), skipping: {relative_path}"
                )
                stats["skipped"] = True
                return True, None, stats

            logger.debug(
                f"[FILE_SYNC] File differs: local_hash={local_hash[:8]}..., remote_hash={remote_hash[:8] if remote_hash else 'None'}..."
            )

            logger.warning(
                f"[FILE_SYNC] File differs from expected state - possible manual modification detected: {relative_path}"
            )
            logger.warning(
                f"[FILE_SYNC] Local file: modified={local_modified}, hash={local_hash[:16]}..."
            )

            if conflict_strategy == "last_write_wins":
                remote_modified_str = file_data.get("modified_at", "")
                try:
                    remote_modified = (
                        datetime.fromisoformat(remote_modified_str)
                        if remote_modified_str
                        else datetime.now()
                    )
                except Exception as e:
                    logger.warning(
                        f"[FILE_SYNC] Could not parse remote modified_at '{remote_modified_str}': {e}"
                    )
                    remote_modified = datetime.now()

                logger.debug(
                    f"[FILE_SYNC] Timestamp comparison: local={local_modified}, remote={remote_modified}"
                )

                if local_modified > remote_modified:
                    time_diff = (local_modified - remote_modified).total_seconds()
                    logger.info(
                        f"[FILE_SYNC] Local file is newer by {time_diff:.0f} seconds - "
                        f"may have been manually modified or copied: {relative_path}"
                    )

                if remote_modified > local_modified:
                    logger.info(
                        f"[FILE_SYNC] Remote file is newer, updating: {relative_path}"
                    )
                    if create_backup:
                        logger.info(
                            f"[FILE_SYNC] Creating backup before overwriting manually modified file: {relative_path}"
                        )
                        self._create_backup(file_path)
                        stats["backed_up"] = True

                    self._write_file(file_path, self._get_file_content(file_data))
                    stats["updated"] = True
                    return True, None, stats
                else:
                    logger.info(
                        f"[FILE_SYNC] Local file is newer, skipping (preserving manual changes): {relative_path}"
                    )
                    stats["skipped"] = True
                    return True, None, stats
            else:
                logger.warning(
                    f"[FILE_SYNC] Conflict detected, creating conflict record: {relative_path}"
                )
                conflict_id = self._create_file_conflict(file_path, file_data)
                return False, conflict_id, stats
        else:
            logger.info(f"[FILE_SYNC] Creating new file: {relative_path}")
            self._write_file(file_path, self._get_file_content(file_data))
            stats["created"] = True
            return True, None, stats

    def apply_files_atomic(
        self,
        files_list: List[Dict],
        conflict_strategy: str = "last_write_wins",
        create_backup: bool = True,
    ) -> Tuple[bool, Dict[str, Any]]:
        result = {
            "summary": {
                "total_processed": 0,
                "total_created": 0,
                "total_updated": 0,
                "total_skipped": 0,
                "total_conflicts": 0,
                "total_errors": 0,
                "total_backed_up": 0,
                "rolled_back": False,
            },
            "details": [],
            "invalid_files": [],
        }

        valid_files, invalid_files = self.validate_files_batch(files_list)
        result["invalid_files"] = [f.get("path", "?") for f in invalid_files]

        if invalid_files:
            logger.warning(
                f"[FILE_SYNC] Atomic apply: Excluding {len(invalid_files)} files "
                f"without content from batch"
            )

        if not valid_files:
            result["summary"]["total_errors"] = (
                len(invalid_files) if invalid_files else 1
            )
            return False, result

        project_root = self.get_project_root()
        backups: List[Tuple[Path, Path]] = []
        created_files: List[Path] = []
        applied_count = 0

        try:
            for file_data in valid_files:
                relative_path = file_data.get("path", "")
                file_path = (project_root / relative_path).resolve()

                try:
                    file_path.relative_to(project_root)
                except ValueError:
                    raise ValueError(
                        f"Invalid file path outside project root: {relative_path}"
                    )

                if file_path.exists():
                    local_hash = self.get_file_hash(str(file_path))
                    remote_hash = file_data.get("hash")
                    if local_hash != remote_hash:
                        if conflict_strategy == "last_write_wins":
                            remote_modified_str = file_data.get("modified_at", "")
                            try:
                                remote_modified = (
                                    datetime.fromisoformat(remote_modified_str)
                                    if remote_modified_str
                                    else datetime.now()
                                )
                            except Exception:
                                remote_modified = datetime.now()
                            local_modified = datetime.fromtimestamp(
                                file_path.stat().st_mtime
                            )

                            if remote_modified > local_modified and create_backup:
                                backup_path = self._create_backup_return_path(file_path)
                                if backup_path:
                                    backups.append((file_path, backup_path))
                                    result["summary"]["total_backed_up"] += 1
                else:
                    file_path.parent.mkdir(parents=True, exist_ok=True)

            for file_data in valid_files:
                relative_path = file_data.get("path", "unknown")
                file_path = (project_root / relative_path).resolve()

                try:
                    success, conflict_id, stats = self.apply_file(
                        file_data, conflict_strategy, create_backup=False
                    )
                    result["summary"]["total_processed"] += 1

                    if stats.get("created"):
                        result["summary"]["total_created"] += 1
                        created_files.append(file_path)
                    elif stats.get("updated"):
                        result["summary"]["total_updated"] += 1
                    elif stats.get("skipped"):
                        result["summary"]["total_skipped"] += 1

                    if conflict_id:
                        result["summary"]["total_conflicts"] += 1

                    result["details"].append(
                        {
                            "path": relative_path,
                            "status": "success" if success else "conflict",
                            "created": stats.get("created", False),
                            "updated": stats.get("updated", False),
                            "skipped": stats.get("skipped", False),
                        }
                    )

                    if not success:
                        raise RuntimeError(f"Conflict for file: {relative_path}")

                except Exception as e:
                    result["summary"]["total_errors"] += 1
                    result["details"].append(
                        {
                            "path": relative_path,
                            "status": "error",
                            "error": str(e),
                        }
                    )
                    logger.error(
                        f"[FILE_SYNC] Atomic apply failed at {relative_path}: {e}",
                        exc_info=True,
                    )
                    self._rollback_atomic(backups, created_files)
                    result["summary"]["rolled_back"] = True
                    return False, result

            # Post-sync: clear __pycache__ for any synced Python files to prevent stale bytecode
            synced_py = any(
                d.get("path", "").endswith(".py")
                for d in valid_files
                if not d.get("path", "").startswith("venv/")
            )
            if synced_py:
                self._clear_pycache(project_root)
                logger.info(
                    "[FILE_SYNC] Cleared __pycache__ after syncing Python files"
                )

            return True, result

        except Exception as e:
            logger.error(f"[FILE_SYNC] Atomic apply failed: {e}", exc_info=True)
            self._rollback_atomic(backups, created_files)
            result["summary"]["rolled_back"] = True
            result["summary"]["total_errors"] += 1
            return False, result

    def _create_backup_return_path(self, file_path: Path) -> Optional[Path]:
        try:
            backup_dir = self.get_project_root() / "backups" / "file_sync"
            backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            try:
                rel = file_path.relative_to(self.get_project_root())
                safe_name = str(rel).replace("/", "_").replace("\\", "_")
            except ValueError:
                safe_name = file_path.name
            backup_name = f"{safe_name}.{timestamp}.bak"
            backup_path = backup_dir / backup_name
            shutil.copy2(file_path, backup_path)
            logger.info(
                f"[FILE_SYNC] Created backup for atomic rollback: {backup_path}"
            )
            return backup_path
        except Exception as e:
            logger.warning(f"[FILE_SYNC] Failed to create backup for {file_path}: {e}")
            return None

    def _rollback_atomic(
        self, backups: List[Tuple[Path, Path]], created_files: List[Path]
    ) -> None:
        logger.warning("[FILE_SYNC] Rolling back atomic file apply")
        for original_path, backup_path in reversed(backups):
            try:
                if backup_path.exists():
                    shutil.copy2(backup_path, original_path)
                    logger.info(
                        f"[FILE_SYNC] Rollback: Restored {original_path} from backup"
                    )
            except Exception as e:
                logger.error(f"[FILE_SYNC] Rollback failed for {original_path}: {e}")
        for created_path in reversed(created_files):
            try:
                if created_path.exists():
                    created_path.unlink()
                    logger.info(
                        f"[FILE_SYNC] Rollback: Deleted newly created {created_path}"
                    )
            except Exception as e:
                logger.error(
                    f"[FILE_SYNC] Rollback failed to delete {created_path}: {e}"
                )

    def _clear_pycache(self, root: Path) -> None:
        """Remove __pycache__ directories under root (excluding venv) to prevent stale bytecode."""
        try:
            count = 0
            for pycache_dir in root.rglob("__pycache__"):
                # Skip virtual environments
                if "venv" in pycache_dir.parts:
                    continue
                try:
                    shutil.rmtree(pycache_dir)
                    count += 1
                except Exception as e:
                    logger.debug(f"[FILE_SYNC] Could not remove {pycache_dir}: {e}")
            if count > 0:
                logger.info(f"[FILE_SYNC] Removed {count} __pycache__ directories")
        except Exception as e:
            logger.warning(f"[FILE_SYNC] Error clearing pycache: {e}")

    def _write_file(self, file_path: Path, content):
        try:
            logger.debug(
                f"[FILE_SYNC] Writing file: {file_path} ({len(content)} bytes)"
            )
            if isinstance(content, bytes):
                with open(file_path, "wb") as f:
                    f.write(content)
            else:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
            logger.info(f"[FILE_SYNC] Successfully wrote file: {file_path}")
        except Exception as e:
            logger.error(
                f"[FILE_SYNC] Error writing file {file_path}: {e}", exc_info=True
            )
            raise

    def _create_backup(self, file_path: Path):
        try:
            backup_dir = self.get_project_root() / "backups" / "file_sync"
            backup_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{file_path.name}.{timestamp}.bak"
            backup_path = backup_dir / backup_name

            shutil.copy2(file_path, backup_path)
            logger.info(f"Created backup: {backup_path}")
        except Exception as e:
            logger.warning(f"Failed to create backup for {file_path}: {e}")

    def _get_file_content(self, file_data: Dict):
        """Get file content. Returns str for text files, bytes for binary files."""
        if "content" in file_data and file_data.get("content") is not None:
            return file_data["content"]
        if file_data.get("compression") == "gzip" and file_data.get(
            "content_compressed"
        ):
            try:
                compressed = base64.b64decode(file_data["content_compressed"])
                raw = gzip.decompress(compressed)
                # Binary files: return bytes directly
                if file_data.get("content_type") == "binary":
                    return raw
                return raw.decode("utf-8")
            except Exception as e:
                logger.error(
                    f"[FILE_SYNC] Failed to decompress content for {file_data.get('path')}: {e}"
                )
                raise
        raise ValueError(f"No content provided for file {file_data.get('path')}")

    def verify_file_integrity(
        self,
        relative_path: str,
        expected_hash: Optional[str] = None,
        expected_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        project_root = self.get_project_root()
        file_path = project_root / relative_path

        result = {
            "path": relative_path,
            "exists": False,
            "matches": False,
            "actual_hash": None,
            "expected_hash": expected_hash,
            "actual_size": None,
            "expected_size": expected_size,
            "modified_at": None,
            "errors": [],
        }

        try:
            if not file_path.exists():
                logger.warning(f"[FILE_SYNC VERIFY] File missing: {relative_path}")
                result["errors"].append("File does not exist")
                return result

            result["exists"] = True

            stat = file_path.stat()
            result["actual_size"] = stat.st_size
            result["modified_at"] = datetime.fromtimestamp(stat.st_mtime).isoformat()

            if expected_size is not None:
                if result["actual_size"] != expected_size:
                    logger.warning(
                        f"[FILE_SYNC VERIFY] Size mismatch for {relative_path}: "
                        f"expected={expected_size}, actual={result['actual_size']}"
                    )
                    result["errors"].append(
                        f"Size mismatch: expected {expected_size}, got {result['actual_size']}"
                    )
                else:
                    logger.debug(
                        f"[FILE_SYNC VERIFY] Size matches for {relative_path}: {result['actual_size']}"
                    )

            if expected_hash:
                actual_hash = self.get_file_hash(str(file_path))
                result["actual_hash"] = actual_hash

                if actual_hash == expected_hash:
                    result["matches"] = True
                    logger.info(
                        f"[FILE_SYNC VERIFY] File verified: {relative_path} (hash match)"
                    )
                else:
                    logger.warning(
                        f"[FILE_SYNC VERIFY] Hash mismatch for {relative_path}: "
                        f"expected={expected_hash[:16]}..., actual={actual_hash[:16]}..."
                    )
                    result["errors"].append(
                        f"Hash mismatch: expected {expected_hash[:16]}..., got {actual_hash[:16]}..."
                    )
            else:
                result["matches"] = True
                logger.debug(
                    f"[FILE_SYNC VERIFY] File exists (no hash check): {relative_path}"
                )

        except Exception as e:
            logger.error(
                f"[FILE_SYNC VERIFY] Error verifying file {relative_path}: {e}",
                exc_info=True,
            )
            result["errors"].append(str(e))

        return result

    def verify_files_batch(self, file_checks: List[Dict[str, Any]]) -> Dict[str, Any]:
        logger.info(
            f"[FILE_SYNC VERIFY] Starting batch verification of {len(file_checks)} files"
        )

        results = {
            "total": len(file_checks),
            "verified": 0,
            "matches": 0,
            "mismatches": 0,
            "missing": 0,
            "errors": 0,
            "files": [],
        }

        for file_check in file_checks:
            file_path = file_check.get("path")
            expected_hash = file_check.get("hash")
            expected_size = file_check.get("size")

            if not file_path:
                logger.warning("[FILE_SYNC VERIFY] Skipping file check with no path")
                continue

            file_result = self.verify_file_integrity(
                file_path, expected_hash, expected_size
            )
            results["files"].append(file_result)
            results["verified"] += 1

            if file_result["exists"]:
                if file_result["matches"]:
                    results["matches"] += 1
                else:
                    results["mismatches"] += 1
            else:
                results["missing"] += 1

            if file_result["errors"]:
                results["errors"] += len(file_result["errors"])

        logger.info(
            f"[FILE_SYNC VERIFY] Batch verification complete: "
            f"{results['matches']} matches, {results['mismatches']} mismatches, "
            f"{results['missing']} missing, {results['errors']} errors"
        )

        return results

    def _create_file_conflict(
        self, local_path: Path, remote_data: Dict
    ) -> Optional[str]:
        try:
            with open(local_path, "r", encoding="utf-8", errors="ignore") as f:
                local_content = f.read()

            conflict_data = {
                "local_path": str(local_path),
                "local_content": local_content,
                "local_hash": self.get_file_hash(str(local_path)),
                "remote_content": remote_data.get("content")
                or remote_data.get("content_compressed"),
                "remote_hash": remote_data.get("hash"),
                "remote_modified": remote_data.get("modified_at"),
                "created_at": datetime.now().isoformat(),
            }

            conflict_dir = self.get_project_root() / "backups" / "file_conflicts"
            conflict_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = local_path.name.replace("/", "_").replace("\\", "_")
            conflict_file = conflict_dir / f"{safe_name}.{timestamp}.conflict.json"

            with open(conflict_file, "w", encoding="utf-8") as f:
                json.dump(conflict_data, f, indent=2)

            logger.info(f"Created conflict record: {conflict_file}")
            return str(conflict_file)

        except Exception as e:
            logger.error(f"Error creating conflict record: {e}")
            return None


_file_sync_service = None


def get_file_sync_service() -> InterconnectorFileSyncService:
    global _file_sync_service
    if _file_sync_service is None:
        _file_sync_service = InterconnectorFileSyncService()
    return _file_sync_service
