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
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
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
    from backend.services.inbound_guard_service import guard_file_reason

    guard_reason = guard_file_reason(normalized)
    if guard_reason:
        return guard_reason
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
# / syntax / backup / rollback): it refuses to mutate files that are backup
# artifacts or archived (.BACK / .BACKUP / _BACK / __BACKUP / /backs/ /
# /_archive/). It is a pure string check on the path, the same one the System
# Mapper uses to classify a module as "archived", so it needs no map, no I/O,
# and cannot crash. (A second tier used to build a whole system map to look up
# the same classification; it could only ever agree with this check.)

def readonly_lifecycle_reason(relative_path: str) -> str | None:
    """Return a block reason if the path is a backup artifact or archived module.

    Dormant and unrouted modules are NOT blocked: they are legitimately edited
    to wire them up."""
    normalized = relative_path.replace("\\", "/").strip("/")
    if not normalized:
        return None

    from backend.services.system_mapper.dependency_graph import _is_backup_artifact

    if _is_backup_artifact(normalized):
        return (
            f"'{normalized}' is a backup/archived artifact (read-only) and "
            "cannot be modified by self-code operations."
        )
    return None


def forbidden_path_reason(relative_path: str) -> str | None:
    # Compared case-folded: on a case-insensitive filesystem ".GIT/config" is
    # the same file as ".git/config".
    normalized = relative_path.replace("\\", "/").strip("/").lower()
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


# Git-ignored paths are local data, not source, so the code-reading tools refuse
# them. Uploads and outputs are the exception: Guaardvark already serves those
# through its document and output tools, and uploaded code repositories live there.
READABLE_IGNORED_PREFIXES = ("data/uploads/", "data/outputs/")
# Used when the install is not a git checkout and check-ignore cannot answer.
FALLBACK_PRIVATE_PREFIXES = ("docs/local-workspace-only/", "data/")


def private_relative_paths(relative_paths: list[str], repo_root: str | Path | None = None) -> set[str]:
    """Return the repo-relative paths that are local data rather than source.

    A path is private when git ignores it (``.gitignore`` or ``.git/info/exclude``)
    and it is not under READABLE_IGNORED_PREFIXES. Each path is also asked about
    as a folder ("p/"), so a directory-only pattern such as "backups/" gives the
    same answer whether or not the folder exists.
    """
    root = Path(repo_root).expanduser().resolve() if repo_root else default_repo_root()
    candidates = [p.replace("\\", "/").lstrip("/") for p in relative_paths if p]
    candidates = [p for p in candidates if not p.startswith(READABLE_IGNORED_PREFIXES)]
    if not candidates:
        return set()
    queries = candidates + [p + "/" for p in candidates if not p.endswith("/")]
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--stdin", "-z"],
            input="\0".join(queries) + "\0",
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        proc = None
    if proc is not None and proc.returncode in (0, 1):
        ignored = {p for p in proc.stdout.split("\0") if p}
        return {p for p in candidates if p in ignored or p + "/" in ignored}
    return {p for p in candidates if (p + "/").startswith(FALLBACK_PRIVATE_PREFIXES)}


def private_path_reason(relative_path: str, repo_root: str | Path | None = None) -> str | None:
    """Block reason for one repo-relative path that is git-ignored local data."""
    if private_relative_paths([relative_path], repo_root):
        return f"'{relative_path}' is git-ignored local data, not source code"
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


def _read_for_edit(file_path: Path) -> tuple[bytes, str, str]:
    """(the file's bytes, its text with LF line endings, its line ending).

    The line ending is "\r\n" for a file written with CRLF throughout and
    "\n" otherwise, so an edit gives the file back as it was found.
    """
    raw = file_path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise GuardedCodeError(f"File is not valid UTF-8 text: {e}", "FILE_NOT_TEXT", 415)
    if "\x00" in text:
        raise GuardedCodeError("File appears to be binary, not text", "FILE_NOT_TEXT", 415)
    crlf = text.count("\r\n")
    newline = "\r\n" if crlf and crlf == text.count("\n") else "\n"
    if newline == "\r\n":
        text = text.replace("\r\n", "\n")
    return raw, text, newline


def _write_bytes_atomic(file_path: Path, data: bytes) -> None:
    """Replace the file in one step, keeping its permissions: a reader never
    sees half of an edit, and a crash mid-write leaves the old file."""
    tmp = file_path.with_name(f".{file_path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        try:
            os.chmod(tmp, file_path.stat().st_mode & 0o7777)
        except OSError:
            pass
        os.replace(tmp, file_path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def syntax_check(file_path: Path, content: str | None = None) -> tuple[bool, str | None]:
    """(checked, first error). ``checked`` is False for a file type nothing
    on this machine can parse; the error is None when the syntax is fine."""
    from backend.services.code_syntax_check import check_file_syntax

    if content is None:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    found = check_file_syntax(file_path.name, content)
    if not found.get("supported"):
        return False, None
    errors = found.get("errors") or []
    if errors:
        return True, f"line {errors[0].get('line')}: {errors[0].get('message')}"
    return True, None


def verify_syntax(file_path: Path, content: str | None = None) -> bool:
    """False only when the syntax was checked and is broken."""
    return syntax_check(file_path, content)[1] is None


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


def refuse_credential_file(file_path: Path) -> None:
    """Every read of a repository file, by a tool, the Code Editor or a
    route, refuses credential and key files, decided from the name before the
    file is touched."""
    from backend.utils.path_safety import is_sensitive

    if is_sensitive(str(file_path)):
        raise GuardedCodeError("Credential and key files are not read through the code tools or the Code Editor",
                               "SENSITIVE_PATH", 403)


def read_repo_file(
    path: str,
    repo_root: str | Path | None = None,
    max_bytes: int = 10 * 1024 * 1024,
    *,
    allow_external: bool = False,
) -> dict:
    file_path, relative_path = resolve_repo_path(path, repo_root, allow_external=allow_external)
    refuse_credential_file(file_path)
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
            # Broken symlinks and vanished entries must not fail the listing.
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


_ANALYSIS_CACHE: dict[str, tuple[float, dict]] = {}
_ANALYSIS_TTL_S = 900


def _repo_file_list(root: Path) -> list[str]:
    """Relative paths that count as repository content.

    Git's index is the authority where available — tracked files are the
    repository, so runtime data, venvs, and caches never pollute the stats.
    Non-git checkouts fall back to a walk under the same visibility rules
    as browsing.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True, text=True, check=True, timeout=15,
        )
        paths = [p for p in out.stdout.splitlines()
                 if p and not forbidden_path_reason(p)]
        if paths:
            return paths
    except (OSError, subprocess.SubprocessError):
        pass
    paths = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        kept = []
        for d in dirnames:
            rel = d if rel_dir == "." else f"{rel_dir}/{d}"
            if not forbidden_path_reason(rel):
                kept.append(d)
        dirnames[:] = kept
        for f in filenames:
            rel = f if rel_dir == "." else f"{rel_dir}/{f}"
            if not forbidden_path_reason(rel):
                paths.append(rel)
    return paths


def live_repo_analysis_summary(repo_root: str | Path | None = None) -> dict:
    """Language/framework summary for the live repo mount.

    The mount has no Folder row, so the DB-backed repository analysis can't
    describe it; this computes the same headline fields from paths alone and
    caches per root.
    """
    root = Path(repo_root).expanduser().resolve() if repo_root else default_repo_root()
    key = str(root)
    now = time.time()
    cached = _ANALYSIS_CACHE.get(key)
    if cached and now - cached[0] < _ANALYSIS_TTL_S:
        return cached[1]
    from backend.services.repository_analysis_service import RepositoryAnalysisService
    paths = _repo_file_list(root)
    summary = {
        "languages": RepositoryAnalysisService.get_language_breakdown(paths),
        "frameworks": RepositoryAnalysisService.detect_frameworks(paths),
        "file_count": len(paths),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }
    _ANALYSIS_CACHE[key] = (now, summary)
    return summary


def build_unified_diff(relative_path: str, old_text: str, new_text: str) -> str:
    return "".join(difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=relative_path,
        tofile=relative_path,
    ))


def _restore_from_backup(file_path: Path, original: bytes, context: str) -> None:
    """Put the pre-edit bytes back after a failure in apply.

    Raises a combined error if the restore itself fails (the .backup file next
    to the target then holds the original).
    """
    try:
        _write_bytes_atomic(file_path, original)
    except Exception as restore_err:
        raise GuardedCodeError(
            f"Failed to {context}; in-place rollback to the pre-edit content also failed. "
            f"A sibling .backup file was created earlier and can be used for manual recovery. "
            f"Rollback error: {restore_err}",
            "ROLLBACK_FAILED",
            500,
        ) from restore_err


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _find_reindented_match(content: str, old_text: str, new_text: str) -> Optional[tuple[str, str, str]]:
    """Locate ``old_text`` when it differs from the file only in whitespace
    at the ends of its lines.

    Models often quote a block with its indentation shifted or its trailing
    spaces dropped. That is the only difference tolerated: every line must
    carry the same code, the block must occur once, and the indentation must
    differ by one constant prefix, which is then applied to ``new_text`` too.
    Returns (text before the block, the block as it is in the file, the
    replacement re-indented), or None.
    """
    lead = len(old_text) - len(old_text.lstrip("\n"))
    trail = len(old_text) - len(old_text.rstrip("\n"))
    old_lines = old_text.strip("\n").split("\n")
    keys = [line.strip() for line in old_lines]
    if len("".join(keys)) < 15:
        return None  # too little text to place safely
    lines = content.split("\n")
    span = len(keys)
    hits = [n for n in range(len(lines) - span + 1)
            if all(lines[n + m].strip() == keys[m] for m in range(span))]
    if len(hits) != 1:
        return None
    start = hits[0]
    found = lines[start:start + span]

    add = drop = None
    for theirs, ours in zip(old_lines, found):
        if not ours.strip():
            continue
        a, b = _indent_of(theirs), _indent_of(ours)
        if b.endswith(a) and (add is None or add == b[: len(b) - len(a)]) and not drop:
            add = b[: len(b) - len(a)]
        elif a.endswith(b) and (drop is None or drop == a[: len(a) - len(b)]) and not add:
            drop = a[: len(a) - len(b)]
        else:
            return None  # indentation differs unevenly: not safe to carry over

    body = new_text
    for _ in range(lead):
        body = body[1:] if body.startswith("\n") else body
    for _ in range(trail):
        body = body[:-1] if body.endswith("\n") else body
    shifted = []
    for line in body.split("\n"):
        if not line.strip():
            shifted.append(line)
        elif add:
            shifted.append(add + line)
        elif drop:
            if not line.startswith(drop):
                return None
            shifted.append(line[len(drop):])
        else:
            shifted.append(line)
    before = "\n".join(lines[:start])
    if start:
        before += "\n"
    return before, "\n".join(found), "\n".join(shifted)


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
    origin: str = "self_code",
    human_approved: bool = False,
    allow_reindent: bool = True,
) -> GuardedEditResult:
    """Apply one exact replacement after all guarded-code checks.

    The text must occur exactly once. With ``allow_reindent`` (the default,
    for text a model quoted) a block that differs only in indentation or
    trailing spaces is still placed, and the result says so; a caller that
    holds the exact text a person approved passes False.

    The inbound guard reads the change last, once the text to replace is
    settled. ``origin`` names the path it came by in the guard's record;
    ``human_approved`` says a person already approved this edit (an approved
    pending fix), which answers a hold but not a block.

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

    original_bytes, current_content, newline = _read_for_edit(file_path)
    # Callers quote text they read with universal newlines; compare like with like.
    old_text = old_text.replace("\r\n", "\n")
    new_text = new_text.replace("\r\n", "\n")

    # Drift guard: if the caller provided a hash or mtime from when it read
    # the file, verify that the on-disk version has not changed since.
    # This prevents "read then clobber" races when multiple agents or the
    # user edit the same file.
    if expected_hash is not None:
        as_read = current_content.replace("\r\n", "\n").replace("\r", "\n")
        current_hash = hashlib.sha256(as_read.encode("utf-8")).hexdigest()
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
        except OSError:
            # Cannot stat: the hash guard, when given, has already answered.
            current_mtime = None
        if current_mtime is not None and abs(current_mtime - expected_mtime) > 0.0001:
            raise GuardedCodeError(
                "File mtime has changed since it was read (drift detected). "
                "Re-read before editing.",
                "FILE_DRIFT_DETECTED",
                409,
            )

    if old_text == "":
        raise GuardedCodeError("old_text is empty; quote the text to replace.", "MISSING_OLD_TEXT")

    match = "exact"
    occurrence_count = current_content.count(old_text)
    if occurrence_count > 1:
        raise GuardedCodeError("Exact text is not unique; edit was not applied.", "TEXT_NOT_UNIQUE", 409)
    if occurrence_count == 1:
        updated_content = current_content.replace(old_text, new_text)
    else:
        placed = _find_reindented_match(current_content, old_text, new_text) if allow_reindent else None
        if placed is None:
            raise GuardedCodeError("Exact text was not found; edit was not applied.", "TEXT_NOT_FOUND", 409)
        before, old_text, new_text = placed
        updated_content = before + new_text + current_content[len(before) + len(old_text):]
        match = "whitespace"
        logger.info("Edit to %s placed after ignoring indentation and trailing whitespace", relative_path)

    diff = build_unified_diff(relative_path, old_text, new_text)

    verdict = _inbound_gate(file_path, relative_path, current_content, old_text, new_text,
                            origin=origin, human_approved=human_approved, dry_run=dry_run)

    checked, syntax_error = syntax_check(file_path, updated_content)
    syntax_note = "syntax checked" if checked else "syntax not checked for this file type"
    match_note = "" if match == "exact" else " The quoted text differed from the file in indentation or trailing spaces only."

    if dry_run:
        if syntax_error:
            raise GuardedCodeError(f"Edit would introduce a syntax error ({syntax_error}).",
                                   "SYNTAX_CHECK_FAILED", 400)

        return GuardedEditResult(
            file_path=str(file_path),
            relative_path=relative_path,
            backup_path="",
            diff=diff,
            verification={
                "command": "exact_text_replacement",
                "return_code": 0,
                "match": match,
                "syntax_checked": checked,
                "output_summary": f"Dry run succeeded: the text was found once; {syntax_note}.{match_note}",
            },
        )

    if syntax_error:
        raise GuardedCodeError(f"Edit would introduce a syntax error ({syntax_error}); it was not applied.",
                               "SYNTAX_CHECK_FAILED", 400)

    backup_path = file_path.with_suffix(file_path.suffix + ".backup")
    backup_path.write_bytes(original_bytes)

    if backup_path.stat().st_size != len(original_bytes):
        raise GuardedCodeError("Backup verification failed; edit was not applied.", "BACKUP_FAILED", 500)

    updated_bytes = (updated_content.replace("\n", "\r\n") if newline == "\r\n" else updated_content).encode("utf-8")
    try:
        _write_bytes_atomic(file_path, updated_bytes)
    except Exception as e:
        raise GuardedCodeError(f"Write failed: {e}", "WRITE_FAILED", 500) from e

    try:
        written = file_path.read_bytes()
    except Exception as e:
        _restore_from_backup(file_path, original_bytes, "rollback after post-write read verification failure")
        raise GuardedCodeError(f"Post-write verify read failed: {e}", "VERIFY_READ_FAILED", 500) from e

    if written != updated_bytes:
        _restore_from_backup(file_path, original_bytes, "restore after post-write verification mismatch")
        raise GuardedCodeError("Post-write verification failed; edit was rolled back.", "VERIFY_FAILED", 500)

    if verdict is not None:
        from backend.services import inbound_guard_service

        inbound_guard_service.landed([str(file_path)], source=origin, subject=f"{origin}: {relative_path}",
                                     verdict=verdict)

    return GuardedEditResult(
        file_path=str(file_path),
        relative_path=relative_path,
        backup_path=str(backup_path),
        diff=diff,
        verification={
            "command": "exact_text_replacement",
            "return_code": 0,
            "match": match,
            "syntax_checked": checked,
            "output_summary": f"File content matched expected post-write content; {syntax_note}.{match_note}",
        },
    )



def _inbound_gate(file_path: Path, relative_path: str, current: str, old_text: str, new_text: str, *,
                  origin: str, human_approved: bool, dry_run: bool):
    """Ask the inbound guard about one replacement; None when the guard is off.

    While enforcing, a block is refused. A hold on a direct edit becomes a
    pending fix, so the change waits for a person instead of being lost.
    """
    from backend.services import inbound_guard_service as guard

    if not guard.is_on():
        return None
    try:
        change = guard.change_for_replacement(file_path, current, old_text, new_text)
        verdict = guard.check([change], source=origin, subject=f"{origin}: {relative_path}", keep=not dry_run)
        guard.gate(verdict, human_approved=human_approved)
        if human_approved and verdict is not None and verdict.verdict == "hold" and verdict.scan_id:
            guard.mark(verdict.scan_id, "approved", by="pending fix approval")
    except guard.InboundRefused as refused:
        if refused.held and not dry_run and refused.verdict is not None:
            fix_id = _stage_held_edit(file_path, relative_path, old_text, new_text, refused)
            guard.link_pending_fix(refused.scan_id, fix_id)
            raise GuardedCodeError(f"{refused} The edit is waiting as pending fix #{fix_id}.",
                                   refused.code, 409) from refused
        raise GuardedCodeError(str(refused), refused.code, 409 if refused.held else 403) from refused
    return verdict


def _stage_held_edit(file_path: Path, relative_path: str, old_text: str, new_text: str, refused) -> int:
    from backend.models import PendingFix, db

    worst = refused.verdict.findings[0] if refused.verdict.findings else None
    pending = PendingFix(
        file_path=str(file_path),
        original_content=old_text,
        proposed_new_content=new_text,
        proposed_diff=build_unified_diff(relative_path, old_text, new_text),
        fix_description="Held by the inbound guard"
        + (f": {worst.severity} {worst.rule} — {worst.why}" if worst else "."),
        severity=(worst.severity if worst and worst.severity in ("critical", "high", "medium", "low") else "medium"),
        status="proposed",
    )
    db.session.add(pending)
    db.session.commit()
    return pending.id


def stage_pending_fix(
    path: str,
    old_text: str,
    new_text: str,
    description: str,
    *,
    severity: str = "medium",
    run_id: int | None = None,
    repo_root: str | Path | None = None,
    reviewed_by: str | None = None,
    review_notes: str | None = None,
) -> int:
    """Create a PendingFix after validating path/lock/protection, without writing.

    reviewed_by/review_notes record a review that happened before staging
    (e.g. Uncle Claude's), or why there was none; a person still applies it.

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
    # at staging time, same as apply does.
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

    # A change already waiting for review, or one a person rejected, is the same
    # question asked again. An applied one is not: its old text is back in the
    # file only if the change was undone.
    filed = (
        PendingFix.query
        .filter(PendingFix.file_path == str(file_path))
        .filter(PendingFix.original_content == old_text)
        .filter(PendingFix.proposed_new_content == new_text)
        .filter(PendingFix.status.in_(("proposed", "triaged", "approved", "rejected")))
        .order_by(PendingFix.id)
        .first()
    )
    if filed is not None:
        raise GuardedCodeError(
            f"The same change is already filed as pending fix #{filed.id} ({filed.status}); "
            "it was not staged again.",
            "ALREADY_FILED", 409,
        )

    if run_id is None:
        logger.info("PendingFix staged without run_id (ad-hoc/manual from guarded; per team audit intentional for non-SI proposals)")

    # The inbound guard reads the proposal now, so its verdict sits beside the
    # diff when a person reviews it. A blocked proposal is not staged at all.
    from backend.services import inbound_guard_service as guard

    verdict = None
    if guard.is_on():
        change = guard.change_for_replacement(file_path, current_content, old_text, new_text)
        try:
            verdict = guard.check([change], source="proposal", subject=f"proposal: {relative_path}")
            if verdict is not None and verdict.enforced and verdict.verdict == "block":
                guard.gate(verdict)
        except guard.InboundRefused as refused:
            raise GuardedCodeError(str(refused), refused.code, 403) from refused

    pending = PendingFix(
        run_id=run_id,
        file_path=str(file_path),
        original_content=old_text,
        proposed_new_content=new_text,
        proposed_diff=build_unified_diff(relative_path, old_text, new_text),
        fix_description=description,
        severity=severity,
        status="proposed",
        reviewed_by=reviewed_by,
        review_notes=review_notes,
    )
    db.session.add(pending)
    db.session.commit()
    if verdict is not None:
        guard.link_pending_fix(getattr(verdict, "scan_id", None), pending.id)
    return pending.id
