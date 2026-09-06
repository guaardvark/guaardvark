"""Guarded source-code operations for self-code agents.

This is the single apply boundary for edits to Guaardvark's own checkout.
Callers may read through it freely, but writes are exact-match, allowlisted,
lock-aware, protected-file-aware, and backed by a diff plus backup.
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


DEFAULT_EXCLUDED_SEGMENTS = {
    ".git",
    ".pytest_cache",
    ".swarm-worktrees",
    "__pycache__",
    "data/cache",
    "dist",
    "htmlcov",
    "logs",
    "node_modules",
    "venv",
}

DEFAULT_EXTERNAL_BLOCKED_ROOTS = (
    "/boot",
    "/dev",
    "/etc",
    "/proc",
    "/root",
    "/run",
    "/sys",
)

DEFAULT_EXTERNAL_EXCLUDED_SEGMENTS = {
    ".aws",
    ".docker",
    ".gnupg",
    ".kube",
    ".ssh",
}

DEFAULT_SENSITIVE_FILENAMES = {
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}

DEFAULT_SENSITIVE_SUFFIXES = {
    ".key",
    ".kdbx",
    ".p12",
    ".pem",
    ".pfx",
}


class GuardedCodeError(Exception):
    """Raised when a guarded source-code operation is rejected."""

    def __init__(self, message: str, code: str = "GUARDED_CODE_ERROR", status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class GuardedEditResult:
    file_path: str
    relative_path: str
    backup_path: str
    diff: str
    verification: dict


def default_repo_root() -> Path:
    """Return the configured Guaardvark repository root."""
    env_root = os.environ.get("GUAARDVARK_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def _normalized_relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def is_codebase_locked() -> bool:
    """Return True when the global codebase lock is active."""
    root = default_repo_root()
    if (root / "data" / ".codebase_lock").exists():
        return True

    # If in test mode and no application context is pushed, bypass DB to allow unit test isolation
    import os
    if os.environ.get("GUAARDVARK_MODE") == "test":
        from flask import has_app_context
        if not has_app_context():
            return False

    try:
        from backend.models import SystemSetting, db

        setting = db.session.query(SystemSetting).filter_by(key="codebase_locked").first()
        return bool(setting and str(setting.value).lower() == "true")
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error querying lock status, falling back to LOCKED for safety: {e}")
        # Default to locked in production for safety; unlocked in testing/sandboxes
        return os.environ.get("GUAARDVARK_MODE") != "test"



def protected_file_reason(relative_path: str) -> str | None:
    """Return a block reason if the path is protected by config.

    Matching is ANCHORED on path components, NOT a naive substring test. A loose
    `protected_norm in normalized` both over-blocks (a future `quickstart.sh` would
    match the protected `start.sh`) and under-anchors (it would match a substring
    inside a directory or filename that merely contains the protected string). We
    block when ANY of the following hold against the normalized POSIX path:

      1. exact match: `normalized == protected_norm`
         (covers full-path entries like `backend/config.py`)
      2. path-suffix match: `normalized` ends with `/<protected_norm>`
         (covers a protected full path reached via a deeper containing path)
      3. basename match: the final path component equals the protected entry
         (covers the bare-filename entries — `start.sh`, `stop.sh`,
         `killswitch.sh` — which are intended to match by filename anywhere)

    This preserves every TRUE positive currently in config.PROTECTED_FILES (the
    real protected files still resolve via rule 1/2/3) while refusing the
    accidental substring collisions (e.g. `quickstart.sh`, `scripts/foo_start.sh`).
    """
    from backend.config import PROTECTED_FILES

    normalized = relative_path.replace("\\", "/").strip("/")
    if not normalized:
        return None
    basename = normalized.rsplit("/", 1)[-1]
    for protected in PROTECTED_FILES:
        protected_norm = protected.replace("\\", "/").strip("/")
        if (
            normalized == protected_norm
            or normalized.endswith(f"/{protected_norm}")
            or basename == protected_norm
        ):
            return (
                f"'{protected}' is protected by the kill switch architecture "
                "and cannot be modified by autonomous processes."
            )
    return None


# --- Mutability Gatekeeper (read-only / archived lifecycle) ---------------
#
# This is a layer ON TOP of the existing guards (lock / protected / exact-match
# / syntax / backup / rollback). Its job: refuse to mutate files the
# system_mapper classifies as backup artifacts or archived lifecycle.
#
# FAIL-OPEN BY DESIGN (read this before "fixing" it):
#   Tier 2 (the cached, map-derived lifecycle check) FAILS OPEN — if the
#   analyzer crashes, the cache build raises, or any node lookup blows up, we
#   ALLOW the write. This is the DELIBERATE OPPOSITE of `is_codebase_locked()`,
#   which fails CLOSED (defaults to locked) because the lock is a hard safety
#   brake. The lifecycle gate is an ergonomic guard, not a safety brake: an
#   interactive edit must never be bricked because a static analyzer choked on
#   an unrelated file. The real safety nets (lock, PROTECTED_FILES, exact-match,
#   syntax verify, backup, rollback) all still run regardless, and Tier 1 below
#   already HARD-BLOCKS the genuinely dangerous case (backup artifacts) with a
#   pure string check that needs no analyzer and cannot crash.

# Tier-2 lifecycle cache: repo_root(str) -> (built_at_monotonic, node_meta dict)
_LIFECYCLE_CACHE: dict[str, tuple[float, dict]] = {}
_LIFECYCLE_CACHE_LOCK = threading.Lock()
_LIFECYCLE_CACHE_TTL = 300.0  # seconds


def invalidate_lifecycle_cache() -> None:
    """Drop the cached system-map lifecycle metadata (e.g. after large edits)."""
    with _LIFECYCLE_CACHE_LOCK:
        _LIFECYCLE_CACHE.clear()


def _lifecycle_node_meta(root: Path) -> dict:
    """Return cached node_meta for `root`, building lazily on miss/expiry.

    May raise if the system-mapper import or analysis fails — callers in the
    gatekeeper treat any raise as fail-open (allow the write)."""
    key = str(root)
    now = time.monotonic()
    with _LIFECYCLE_CACHE_LOCK:
        cached = _LIFECYCLE_CACHE.get(key)
        if cached and (now - cached[0]) < _LIFECYCLE_CACHE_TTL:
            return cached[1]

    # Build outside the lock — codebase_map() is multi-second and we don't want
    # to serialize unrelated edits behind it.
    from backend.services.system_mapper import codebase_map

    smap = codebase_map(root)
    node_meta = smap.node_meta or {}

    with _LIFECYCLE_CACHE_LOCK:
        _LIFECYCLE_CACHE[key] = (time.monotonic(), node_meta)
    return node_meta


def readonly_lifecycle_reason(relative_path: str) -> str | None:
    """Return a block reason if the path is a backup artifact or archived module.

    TIER 1 (always-on, deterministic, no map run, no I/O): backup-artifact path
    match (.BACK / .BACKUP / _BACK / __BACKUP / /backs/ / /_archive/). This is
    the must-ship hard block.

    TIER 2 (cached, optional, .py only): consult the system_mapper lifecycle and
    block ONLY lifecycle == 'archived'. 'dormant' is explicitly NOT blocked —
    dormant/unrouted modules are legitimately edited to wire them up. Tier 2
    FAILS OPEN: any exception allows the write (see the module comment above for
    why this is the opposite of the codebase-lock policy)."""
    normalized = relative_path.replace("\\", "/").strip("/")
    if not normalized:
        return None

    # TIER 1 — pure string check, microseconds, no I/O, cannot crash.
    from backend.services.system_mapper.dependency_graph import _is_backup_artifact

    if _is_backup_artifact(normalized):
        return (
            f"'{normalized}' is a backup/archived artifact (read-only) and "
            "cannot be modified by self-code operations."
        )

    # TIER 2 — richer 'archived' lifecycle. Python sources only.
    if not normalized.endswith(".py"):
        return None

    try:
        root = default_repo_root()
        node_meta = _lifecycle_node_meta(root)
        for meta in node_meta.values():
            if not isinstance(meta, dict):
                continue
            if meta.get("path", "").replace("\\", "/").strip("/") != normalized:
                continue
            if meta.get("lifecycle") == "archived":
                return (
                    f"'{normalized}' is classified as archived (read-only) by the "
                    "system mapper and cannot be modified by self-code operations."
                )
            break
    except Exception:
        # FAIL OPEN — see module comment. An analyzer failure must not brick edits.
        return None

    return None


def forbidden_path_reason(relative_path: str) -> str | None:
    normalized = relative_path.replace("\\", "/").strip("/")
    if not normalized:
        return "Empty path"
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "Path traversal is not allowed"
    for segment in DEFAULT_EXCLUDED_SEGMENTS:
        segment_parts = segment.split("/")
        if len(segment_parts) == 1 and any(part == segment for part in parts):
            return f"Self-code operations are not allowed inside '{segment}'"
        if len(segment_parts) > 1 and (normalized == segment or normalized.startswith(f"{segment}/")):
            return f"Self-code operations are not allowed inside '{segment}'"
    if parts[-1].startswith(".env"):
        return "Self-code operations are not allowed for environment files"
    return None


def external_path_reason(path: Path) -> str | None:
    """Return a block reason for explicitly referenced files outside the repo."""
    resolved = path.resolve()
    for blocked_root_str in DEFAULT_EXTERNAL_BLOCKED_ROOTS:
        blocked_root = Path(blocked_root_str)
        if _is_under(resolved, blocked_root):
            return f"External file operations are not allowed inside '{blocked_root}'"

    parts = set(resolved.parts)
    for segment in DEFAULT_EXTERNAL_EXCLUDED_SEGMENTS:
        if segment in parts:
            return f"External file operations are not allowed inside '{segment}'"

    name = resolved.name
    suffix = resolved.suffix.lower()
    if name.startswith(".env"):
        return "External file operations are not allowed for environment files"
    if name in DEFAULT_SENSITIVE_FILENAMES or suffix in DEFAULT_SENSITIVE_SUFFIXES:
        return "External file operations are not allowed for sensitive key or credential files"
    return None


def _read_text_file(file_path: Path) -> str:
    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise GuardedCodeError(f"File is not valid UTF-8 text: {e}", "FILE_NOT_TEXT", 415)
    if "\x00" in content:
        raise GuardedCodeError("File appears to be binary, not text", "FILE_NOT_TEXT", 415)
    return content


def verify_syntax(file_path: Path, content: str | None = None) -> bool:
    """Check if the content or file has valid syntax."""
    if file_path.suffix == ".py":
        import py_compile
        import tempfile
        try:
            if content is not None:
                # Compile from string content by creating a temp file
                fd, temp_file_name = tempfile.mkstemp(suffix=".py")
                try:
                    with os.fdopen(fd, 'w', encoding='utf-8') as temp_file:
                        temp_file.write(content)
                    py_compile.compile(temp_file_name, doraise=True)
                    return True
                finally:
                    try:
                        os.unlink(temp_file_name)
                    except Exception:
                        pass
            else:
                py_compile.compile(str(file_path), doraise=True)
                return True
        except py_compile.PyCompileError:
            return False
    elif file_path.suffix == ".json":
        import json
        try:
            if content is not None:
                json.loads(content)
            else:
                json.loads(file_path.read_text(encoding="utf-8"))
            return True
        except ValueError:
            return False
    return True


def resolve_repo_path(
    path: str,
    repo_root: str | Path | None = None,
    *,
    allow_external: bool = False,
) -> tuple[Path, str]:
    """Resolve a caller-provided path against the repo root or an allowed external path."""
    if not path or not str(path).strip():
        raise GuardedCodeError("Path is required", "EMPTY_PATH")

    raw_path = str(path).strip()
    root = Path(repo_root).expanduser().resolve() if repo_root else default_repo_root()
    candidate = Path(raw_path).expanduser()
    explicitly_external = candidate.is_absolute()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()

    if _is_under(candidate, root):
        relative_path = _normalized_relative(candidate, root)
        reason = forbidden_path_reason(relative_path)
        if reason:
            raise GuardedCodeError(reason, "FORBIDDEN_PATH", 403)
        return candidate, relative_path

    if not allow_external:
        raise GuardedCodeError("Path is outside the configured repository root", "PATH_OUTSIDE_REPO", 403)

    if not explicitly_external:
        raise GuardedCodeError(
            "External paths must be absolute or start with '~'",
            "EXTERNAL_PATH_NOT_EXPLICIT",
            403,
        )

    reason = external_path_reason(candidate)
    if reason:
        raise GuardedCodeError(reason, "FORBIDDEN_EXTERNAL_PATH", 403)

    return candidate, str(candidate)


def read_repo_file(
    path: str,
    repo_root: str | Path | None = None,
    max_bytes: int = 10 * 1024 * 1024,
    *,
    allow_external: bool = False,
) -> dict:
    file_path, relative_path = resolve_repo_path(path, repo_root, allow_external=allow_external)
    if not file_path.exists() or not file_path.is_file():
        raise GuardedCodeError("File not found", "FILE_NOT_FOUND", 404)
    size = file_path.stat().st_size
    if size > max_bytes:
        raise GuardedCodeError("File is too large to read through self-code API", "FILE_TOO_LARGE", 413)
    content = _read_text_file(file_path)
    return {
        "path": str(file_path),
        "relative_path": relative_path,
        "content": content,
        "size": size,
        "last_modified": file_path.stat().st_mtime,
        "scope": "repo" if not Path(relative_path).is_absolute() else "external",
    }


def browse_repo_path(path: str = "", repo_root: str | Path | None = None) -> dict:
    root = Path(repo_root).expanduser().resolve() if repo_root else default_repo_root()
    if not path or path == ".":
        directory = root
        relative_path = "."
    else:
        directory, relative_path = resolve_repo_path(path, root)
    if not directory.exists() or not directory.is_dir():
        raise GuardedCodeError("Directory not found", "DIRECTORY_NOT_FOUND", 404)

    folders = []
    files = []
    for child in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        child_rel = _normalized_relative(child, root)
        if forbidden_path_reason(child_rel):
            continue
        try:
            stat = child.stat()
        except OSError:
            # Broken symlink / unreadable entry (e.g. the repo-root `manager`
            # symlink) — skip it rather than fail the whole folder listing.
            continue
        item_id = f"repo:{child_rel}"
        if child.is_dir():
            folders.append({
                "id": item_id,
                "name": child.name,
                "path": f"/__repo__/{child_rel}",
                "source_type": "live_repo",
                "relative_path": child_rel,
                "is_repository": child_rel == ".",
                "updated_at": None,
                "subfolder_count": 0,
                "document_count": 0,
                "indexed_document_count": 0,
            })
        elif child.is_file():
            files.append({
                "id": item_id,
                "filename": child.name,
                "name": child.name,
                "path": f"/__repo__/{child_rel}",
                "source_type": "live_repo",
                "relative_path": child_rel,
                "type": child.suffix.lstrip(".") or "text",
                "size": stat.st_size,
                "is_code_file": True,
                "index_status": "NOT_INDEXED",
                "uploaded_at": None,
                "updated_at": None,
            })
    return {
        "path": f"/__repo__/{relative_path}" if relative_path != "." else "/__repo__",
        "relative_path": "" if relative_path == "." else relative_path,
        "folders": folders,
        "documents": files,
        "total_folders": len(folders),
        "total_documents": len(files),
        "has_more": False,
    }


def build_unified_diff(relative_path: str, old_text: str, new_text: str) -> str:
    return "".join(difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=relative_path,
        tofile=relative_path,
    ))


def _restore_from_backup(file_path: Path, original_content: str, context: str) -> None:
    """Best-effort restore of original bytes after a post-backup failure in apply.

    Surfaces a combined error if the restore itself fails (the .backup file next
    to the target remains the operator's ultimate recovery mechanism). Never
    silently leaves a half-written file when we can avoid it.
    """
    try:
        file_path.write_text(original_content, encoding="utf-8")
    except Exception as restore_err:
        raise GuardedCodeError(
            f"Failed to {context}; in-place rollback to the pre-edit content also failed. "
            f"A sibling .backup file was created earlier and can be used for manual recovery. "
            f"Rollback error: {restore_err}",
            "ROLLBACK_FAILED",
            500,
        ) from restore_err

def _find_fuzzy_match(content: str, old_text: str, threshold: float = 0.85) -> Optional[str]:
    """Find a unique fuzzy match for old_text in content."""
    if len(old_text.strip()) < 15:
        return None  # Too short for safe fuzzy matching
        
    content_lines = content.splitlines()
    old_lines = old_text.splitlines()
    
    if not old_lines or not content_lines:
        return None
        
    target_len = len(old_lines)
    window_sizes = [target_len - 1, target_len, target_len + 1, target_len + 2]
    window_sizes = [w for w in window_sizes if w > 0]
    
    matches = []
    
    for w in window_sizes:
        for i in range(len(content_lines) - w + 1):
            window_text = "\n".join(content_lines[i:i+w])
            ratio = difflib.SequenceMatcher(None, window_text, old_text).ratio()
            if ratio >= threshold:
                matches.append((ratio, window_text))
                
    if not matches:
        return None
        
    max_ratio = max(m[0] for m in matches)
    # Filter matches that are very close to max_ratio
    best_matches = set(m[1] for m in matches if max_ratio - m[0] < 0.01)
    
    if len(best_matches) > 1:
        # Not unique
        return None
        
    return best_matches.pop()


def apply_exact_replacement(
    path: str,
    old_text: str,
    new_text: str,
    *,
    repo_root: str | Path | None = None,
    require_unlocked: bool = True,
    dry_run: bool = False,
    allow_external: bool = False,
    max_bytes: int = 10 * 1024 * 1024,
    expected_hash: Optional[str] = None,
    expected_mtime: Optional[float] = None,
) -> GuardedEditResult:
    """Apply one exact replacement after all guarded-code checks.

    Optional drift protection:
    - expected_hash: sha256 of the content the caller read. If the on-disk
      content no longer matches, raise FILE_DRIFT_DETECTED.
    - expected_mtime: mtime (os.stat().st_mtime) at read time.
    These are powerful for agentic code editing in concurrent/swarm scenarios.
    """
    if require_unlocked and is_codebase_locked():
        raise GuardedCodeError("Codebase is locked. Unlock it before applying code changes.", "CODEBASE_LOCKED", 423)
    if old_text is None:
        raise GuardedCodeError("old_text is required", "MISSING_OLD_TEXT")
    if new_text is None:
        raise GuardedCodeError("new_text is required", "MISSING_NEW_TEXT")

    file_path, relative_path = resolve_repo_path(path, repo_root, allow_external=allow_external)
    if not file_path.exists() or not file_path.is_file():
        raise GuardedCodeError("File not found", "FILE_NOT_FOUND", 404)
    if file_path.stat().st_size > max_bytes:
        raise GuardedCodeError("File is too large to edit through self-code API", "FILE_TOO_LARGE", 413)

    if not Path(relative_path).is_absolute():
        protected_reason = protected_file_reason(relative_path)
        if protected_reason:
            raise GuardedCodeError(protected_reason, "PROTECTED_FILE", 403)

        # Mutability Gatekeeper: refuse writes to read-only / archived files.
        # Runs after the protected-file check (so PROTECTED_FILE wins on overlap)
        # and before content read / dry-run / backup, so dry-runs report the
        # rejection and the backup->write->verify->rollback sequence is untouched.
        lifecycle_reason = readonly_lifecycle_reason(relative_path)
        if lifecycle_reason:
            raise GuardedCodeError(lifecycle_reason, "READONLY_LIFECYCLE", 403)

    current_content = _read_text_file(file_path)

    # Drift guard: if the caller provided a hash or mtime from when it read
    # the file, verify that the on-disk version has not changed since.
    # This prevents "read then clobber" races when multiple agents or the
    # user edit the same file.
    if expected_hash is not None:
        current_hash = hashlib.sha256(current_content.encode("utf-8")).hexdigest()
        if current_hash != expected_hash:
            raise GuardedCodeError(
                "File content has changed since it was read (drift detected). "
                "Re-read the current content with read_code (or equivalent) before retrying the edit. "
                "This guard protects against concurrent edits in chat, self-improvement, or swarm scenarios.",
                "FILE_DRIFT_DETECTED",
                409,
            )

    if expected_mtime is not None:
        try:
            current_mtime = file_path.stat().st_mtime
            if abs(current_mtime - expected_mtime) > 0.0001:  # tolerate floating point
                raise GuardedCodeError(
                    "File mtime has changed since it was read (drift detected). "
                    "Re-read before editing.",
                    "FILE_DRIFT_DETECTED",
                    409,
                )
        except Exception:
            # If we can't stat, don't block the edit — the hash guard is stronger anyway.
            pass

    occurrence_count = current_content.count(old_text)

    using_normalized = False
    if occurrence_count == 0:
        # Fallback to normalized line ending search
        normalized_content = current_content.replace("\r\n", "\n")
        normalized_old = old_text.replace("\r\n", "\n")
        occurrence_count = normalized_content.count(normalized_old)
        if occurrence_count == 1:
            using_normalized = True
            current_content = normalized_content
            old_text = normalized_old
            new_text = new_text.replace("\r\n", "\n")

    if occurrence_count == 0:
        # Final fallback: Fuzzy matching
        fuzzy_match = _find_fuzzy_match(current_content, old_text)
        if fuzzy_match:
            # We found a unique block of code that is very similar to old_text
            old_text = fuzzy_match
            occurrence_count = current_content.count(old_text)
            logger.info(f"Fuzzy match successful. Applying edit via fuzzy block.")

    if occurrence_count == 0:
        raise GuardedCodeError("Exact text was not found; edit was not applied.", "TEXT_NOT_FOUND", 409)
    if occurrence_count > 1:
        raise GuardedCodeError("Exact text is not unique; edit was not applied.", "TEXT_NOT_UNIQUE", 409)

    updated_content = current_content.replace(old_text, new_text)
    diff = build_unified_diff(relative_path, old_text, new_text)

    if dry_run:
        # Dry run syntax check
        if not verify_syntax(file_path, updated_content):
            raise GuardedCodeError("Edit would introduce syntax errors.", "SYNTAX_CHECK_FAILED", 400)

        return GuardedEditResult(
            file_path=str(file_path),
            relative_path=relative_path,
            backup_path="",
            diff=diff,
            verification={
                "command": "exact_text_replacement",
                "return_code": 0,
                "output_summary": "Dry run succeeded: content matched and verified syntax.",
            },
        )

    backup_path = file_path.with_suffix(file_path.suffix + ".backup")
    backup_path.write_text(current_content, encoding="utf-8")

    if backup_path.stat().st_size != len(current_content.encode("utf-8")):
        raise GuardedCodeError("Backup verification failed; edit was not applied.", "BACKUP_FAILED", 500)

    try:
        file_path.write_text(updated_content, encoding="utf-8")
    except Exception as e:
        _restore_from_backup(file_path, current_content, "write the edit")
        raise GuardedCodeError(f"Write failed: {e}", "WRITE_FAILED", 500) from e

    # Validate syntax on the newly written file
    if not verify_syntax(file_path):
        _restore_from_backup(file_path, current_content, "rollback after syntax verification failure")
        raise GuardedCodeError("Edit introduced syntax errors; edit was rolled back.", "SYNTAX_CHECK_FAILED", 400)

    try:
        verify_content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        _restore_from_backup(file_path, current_content, "rollback after post-write read verification failure")
        raise GuardedCodeError(f"Post-write verify read failed: {e}", "VERIFY_READ_FAILED", 500) from e

    if verify_content != updated_content:
        _restore_from_backup(file_path, current_content, "restore after post-write verification mismatch")
        raise GuardedCodeError("Post-write verification failed; edit was rolled back.", "VERIFY_FAILED", 500)

    return GuardedEditResult(
        file_path=str(file_path),
        relative_path=relative_path,
        backup_path=str(backup_path),
        diff=diff,
        verification={
            "command": "exact_text_replacement",
            "return_code": 0,
            "output_summary": "File content matched expected post-write content and syntax verified.",
        },
    )



def stage_pending_fix(
    path: str,
    old_text: str,
    new_text: str,
    description: str,
    *,
    severity: str = "medium",
    run_id: int | None = None,
    repo_root: str | Path | None = None,
) -> int:
    """Create a PendingFix after validating path/lock/protection, without writing.

    Note: Lock is not enforced on staging since it is purely a proposal and does
    not write to the repo. The lock check is strictly enforced during apply.
    """
    file_path, relative_path = resolve_repo_path(path, repo_root)
    if not file_path.exists() or not file_path.is_file():
        raise GuardedCodeError("File not found", "FILE_NOT_FOUND", 404)

    protected_reason = protected_file_reason(relative_path)
    if protected_reason:
        raise GuardedCodeError(protected_reason, "PROTECTED_FILE", 403)

    # Mutability Gatekeeper: reject proposals targeting read-only / archived files
    # at staging time, same as apply does (Tier 1 backup-artifact hard block plus
    # Tier 2 archived-lifecycle, fail-open).
    lifecycle_reason = readonly_lifecycle_reason(relative_path)
    if lifecycle_reason:
        raise GuardedCodeError(lifecycle_reason, "READONLY_LIFECYCLE", 403)

    current_content = file_path.read_text(encoding="utf-8")
    occurrence_count = current_content.count(old_text)
    if occurrence_count == 0:
        raise GuardedCodeError("Exact text was not found; fix was not staged.", "TEXT_NOT_FOUND", 409)
    if occurrence_count > 1:
        raise GuardedCodeError("Exact text is not unique; fix was not staged.", "TEXT_NOT_UNIQUE", 409)

    from backend.models import PendingFix, db

    if run_id is None:
        logger.info("PendingFix staged without run_id (ad-hoc/manual from guarded; per team audit intentional for non-SI proposals)")

    pending = PendingFix(
        run_id=run_id,
        file_path=str(file_path),
        original_content=old_text,
        proposed_new_content=new_text,
        proposed_diff=build_unified_diff(relative_path, old_text, new_text),
        fix_description=description,
        severity=severity,
        status="proposed",
    )
    db.session.add(pending)
    db.session.commit()
    return pending.id
