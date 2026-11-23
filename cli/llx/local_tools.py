"""Local tools for the Guaardvark CLI REPL.

Direct, safe filesystem + exec operations that work in lite mode and full,
modeled after the Grok tool surface (read_file, search_replace style, run)
and peer CLIs (Claude Code, Cline/OpenClaw, Cursor agent).

Security model mirrors:
- utils.py blocked roots/segments/sensitive files
- backend/services/guarded_code_service.py protected files + backups + verify
All writes are explicit-approval, backed up, and verified on read-after-write.
"""

from __future__ import annotations

import difflib
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

# ── Safety constants (source of truth in cli/llx/utils.py; duplicated here for
# standalone CLI package when backend is not importable).
try:
    from llx.utils import (
        BLOCKED_EXTERNAL_ROOTS,
        BLOCKED_PATH_SEGMENTS,
        SENSITIVE_SUFFIXES,
        SENSITIVE_FILENAMES,
        make_unified_diff as _make_unified_diff_from_utils,
    )
except Exception:
    # Fallbacks (must stay in sync)
    BLOCKED_EXTERNAL_ROOTS = ("/boot", "/dev", "/etc", "/proc", "/root", "/run", "/sys")
    BLOCKED_PATH_SEGMENTS = {".aws", ".docker", ".gnupg", ".kube", ".ssh", ".swarm-worktrees"}
    SENSITIVE_SUFFIXES = {".key", ".kdbx", ".p12", ".pem", ".pfx"}
    SENSITIVE_FILENAMES = {"id_dsa", "id_ecdsa", "id_ed25519", "id_rsa"}
    _make_unified_diff_from_utils = None

MAX_READ_BYTES = 2 * 1024 * 1024
MAX_RUN_OUTPUT = 64 * 1024

# Conservative protected + skip sets
PROTECTED_BASENAMES: set[str] = {
    "config.py", "killswitch.sh", "start.sh", "stop.sh",
    ".env", "CLAUDE.md", "GROK.md", "AGENTS.md", "GUAARDVARK.md",
}
SKIP_DIRS = {
    ".git", ".svn", ".hg", "__pycache__", "node_modules", "venv", ".venv",
    "dist", "build", ".pytest_cache", ".swarm-worktrees", "logs", "data/cache",
}


def make_unified_diff(original: str, updated: str, path: str) -> str:
    if _make_unified_diff_from_utils:
        return _make_unified_diff_from_utils(original, updated, path)
    import difflib
    a = (original or "").splitlines(keepends=True)
    b = (updated or "").splitlines(keepends=True)
    return "".join(difflib.unified_diff(a, b, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))


def _resolve_path(target: str | Path, cwd: Path | None = None) -> Path:
    """Resolve relative to provided cwd (or process cwd). Never escape to blocked roots."""
    p = Path(target).expanduser()
    if not p.is_absolute():
        base = cwd or Path.cwd()
        p = (base / p).resolve()
    else:
        p = p.resolve()
    return p


def _blocked_reason(path: Path) -> str | None:
    """Return human reason if path is unsafe to read/write, else None."""
    try:
        for root in BLOCKED_EXTERNAL_ROOTS:
            path.relative_to(root)
            return f"blocked: inside system location '{root}'"
    except ValueError:
        pass

    parts = set(path.parts)
    for seg in BLOCKED_PATH_SEGMENTS:
        if seg in parts:
            return f"blocked: inside sensitive directory '{seg}'"

    name = path.name
    if name.startswith(".env"):
        return "blocked: environment files are not editable via CLI tools"
    if name in SENSITIVE_FILENAMES or (path.suffix.lower() in SENSITIVE_SUFFIXES):
        return "blocked: sensitive credential or key file"
    return None


def _is_protected(path: Path) -> tuple[bool, str | None]:
    """Check against built-in + (best-effort) backend PROTECTED_FILES."""
    # Built-in basenames
    if path.name in PROTECTED_BASENAMES:
        return True, f"'{path.name}' is protected (kill-switch / critical config)"

    # Try to load the real list from the installed backend (graceful)
    try:
        from backend.config import PROTECTED_FILES  # type: ignore

        rel = str(path)
        normalized = rel.replace("\\", "/").strip("/")
        basename = normalized.rsplit("/", 1)[-1]
        for prot in PROTECTED_FILES:
            pn = prot.replace("\\", "/").strip("/")
            if normalized == pn or normalized.endswith(f"/{pn}") or basename == pn:
                return True, f"'{prot}' is protected by the kill switch architecture"
    except Exception:
        pass
    return False, None


def _should_skip_dir(d: Path) -> bool:
    name = d.name
    if name in SKIP_DIRS:
        return True
    if name.startswith(".") and name not in {".github", ".grok"}:
        # skip most dot-dirs except a couple useful ones
        return True
    return False


# ── Public API ────────────────────────────────────────────────────────────

def list_dir(path: str | Path = ".", cwd: Path | None = None, max_entries: int = 200) -> dict[str, Any]:
    """List files and dirs. Returns {'path': , 'folders': [...], 'files': [...]}"""
    target = _resolve_path(path, cwd)
    if not target.exists():
        return {"path": str(target), "error": "not found"}
    if not target.is_dir():
        return {"path": str(target), "error": "not a directory"}

    folders: list[str] = []
    files: list[dict] = []
    try:
        entries = sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        for e in entries:
            if len(folders) + len(files) >= max_entries:
                break
            if e.is_dir():
                if _should_skip_dir(e):
                    continue
                folders.append(e.name + "/")
            else:
                try:
                    size = e.stat().st_size
                except Exception:
                    size = 0
                files.append({"name": e.name, "size": size})
    except PermissionError as ex:
        return {"path": str(target), "error": f"permission denied: {ex}"}

    return {"path": str(target), "folders": folders, "files": files}


def read_file(
    path: str | Path,
    offset: int | None = None,
    limit: int | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Read text file (UTF-8). Supports 1-based offset/limit like backend tools."""
    target = _resolve_path(path, cwd)
    blocked = _blocked_reason(target)
    if blocked:
        return {"path": str(target), "error": blocked, "read_status": "blocked"}
    if not target.exists():
        return {"path": str(target), "error": "file not found", "read_status": "missing"}
    if not target.is_file():
        return {"path": str(target), "error": "not a file", "read_status": "not_file"}

    try:
        size = target.stat().st_size
        if size > MAX_READ_BYTES:
            return {"path": str(target), "error": f"file too large ({size} bytes)", "read_status": "too_large"}

        text = target.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines(keepends=True)

        start = 0 if offset is None else max(0, offset - 1)
        if limit is not None:
            end = start + max(0, limit)
            content_lines = lines[start:end]
        else:
            content_lines = lines[start:]

        content = "".join(content_lines)
        return {
            "path": str(target),
            "content": content,
            "offset": offset or 1,
            "limit": limit,
            "total_lines": len(lines),
            "read_status": "ok",
            "size": size,
        }
    except Exception as ex:
        return {"path": str(target), "error": str(ex), "read_status": "error"}


def grep(
    pattern: str,
    path: str | Path = ".",
    cwd: Path | None = None,
    glob: str = "**/*",
    max_matches: int = 200,
    case_sensitive: bool = False,
) -> dict[str, Any]:
    """Recursive grep. Pure Python (no external rg dep). Returns matches list."""
    target = _resolve_path(path, cwd)
    if not target.exists():
        return {"pattern": pattern, "path": str(target), "error": "path not found", "matches": []}

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        cre = re.compile(pattern, flags)
    except re.error as ex:
        return {"pattern": pattern, "error": f"bad regex: {ex}", "matches": []}

    matches: list[dict] = []
    root = target if target.is_dir() else target.parent
    search_root = target if target.is_dir() else target

    try:
        it: Iterable[Path]
        if search_root.is_file():
            it = [search_root]
        else:
            it = (p for p in search_root.rglob(glob) if p.is_file())

        for p in it:
            if len(matches) >= max_matches:
                break
            if _should_skip_dir(p.parent):
                continue
            br = _blocked_reason(p)
            if br:
                continue
            try:
                if p.stat().st_size > MAX_READ_BYTES:
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(text.splitlines(), 1):
                    if cre.search(line):
                        matches.append({
                            "file": str(p),
                            "line": i,
                            "text": line[:300],
                        })
                        if len(matches) >= max_matches:
                            break
            except Exception:
                continue
    except Exception as ex:
        return {"pattern": pattern, "error": str(ex), "matches": []}

    return {"pattern": pattern, "path": str(target), "matches": matches, "count": len(matches)}


def run_command(
    cmd: str,
    cwd: Path | None = None,
    timeout: float = 60.0,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run a shell command. Captures output (truncated). Never uses shell=True for safety."""
    base = cwd or Path.cwd()
    try:
        import shlex
        # Use shlex for proper quoting (user can still pass bash -c '...')
        if isinstance(cmd, (list, tuple)):
            parts = list(cmd)
        else:
            parts = shlex.split(cmd)
        full_env = os.environ.copy()
        if env:
            full_env.update(env)

        proc = subprocess.run(
            parts,
            cwd=str(base),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=full_env,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if len(out) > MAX_RUN_OUTPUT:
            out = out[: MAX_RUN_OUTPUT - 100] + "\n... [truncated]"
        return {
            "cmd": cmd,
            "cwd": str(base),
            "exit_code": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "output": out,
            "success": proc.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"cmd": cmd, "error": f"timeout after {timeout}s", "exit_code": 124, "success": False}
    except FileNotFoundError:
        return {"cmd": cmd, "error": "command not found", "exit_code": 127, "success": False}
    except Exception as ex:
        return {"cmd": cmd, "error": str(ex), "success": False}


def make_unified_diff(original: str, updated: str, path: str) -> str:
    """Return a unified diff string."""
    a = original.splitlines(keepends=True)
    b = updated.splitlines(keepends=True)
    diff = difflib.unified_diff(
        a, b, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""
    )
    return "".join(diff)


def apply_search_replace(
    path: str | Path,
    old_text: str,
    new_text: str,
    cwd: Path | None = None,
    replace_all: bool = False,
) -> dict[str, Any]:
    """Exact-match search/replace with backup + verify.

    Mirrors the spirit (and many mechanics) of guarded_code_service.apply_exact_replacement.
    """
    target = _resolve_path(path, cwd)
    blocked = _blocked_reason(target)
    if blocked:
        return {"success": False, "path": str(target), "error": blocked}

    prot, reason = _is_protected(target)
    if prot:
        return {"success": False, "path": str(target), "error": reason or "protected file"}

    if not target.exists():
        # Allow creation for new files when old_text == ""
        if old_text != "":
            return {"success": False, "path": str(target), "error": "file does not exist (use empty old_text to create)"}
        original = ""
    else:
        if not target.is_file():
            return {"success": False, "path": str(target), "error": "not a regular file"}
        try:
            original = target.read_text(encoding="utf-8")
        except Exception as ex:
            return {"success": False, "path": str(target), "error": f"read failed: {ex}"}

    if old_text and old_text not in original:
        return {"success": False, "path": str(target), "error": "old_text not found in file (exact match required)"}
    if old_text and not replace_all and original.count(old_text) > 1:
        return {"success": False, "path": str(target), "error": (
            f"old_text occurs {original.count(old_text)} times; quote more of the surrounding text so it "
            "matches once")}

    # Create backup (even for new files, the "original" is empty)
    bak_path = target.with_name(target.name + f".bak-{int(time.time())}")
    try:
        if target.exists():
            bak_path.write_text(original, encoding="utf-8")
        else:
            bak_path.write_text("", encoding="utf-8")

        if replace_all:
            updated = original.replace(old_text, new_text)
        else:
            updated = original.replace(old_text, new_text, 1)

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(updated, encoding="utf-8")

        # Verify
        after = target.read_text(encoding="utf-8")
        if after != updated:
            # rollback attempt
            target.write_text(original, encoding="utf-8")
            return {"success": False, "path": str(target), "error": "post-write verification failed; rolled back"}

        diff = make_unified_diff(original, updated, str(target))
        return {
            "success": True,
            "path": str(target),
            "backup": str(bak_path),
            "diff": diff,
            "bytes_written": len(updated.encode("utf-8")),
        }
    except Exception as ex:
        # best effort rollback
        try:
            if target.exists() and 'original' in locals():
                target.write_text(original, encoding="utf-8")
        except Exception:
            pass
        return {"success": False, "path": str(target), "error": str(ex)}


def get_git_info(cwd: Path | None = None) -> dict[str, Any]:
    """Best-effort git branch + dirty flag. Silent on failure."""
    base = cwd or Path.cwd()
    info = {"branch": "", "dirty": False, "sha": ""}
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(base), text=True, stderr=subprocess.DEVNULL, timeout=2
        ).strip()
        info["branch"] = branch or ""
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(base), text=True, stderr=subprocess.DEVNULL, timeout=2
        ).strip()
        info["sha"] = sha or ""
        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=str(base), text=True, stderr=subprocess.DEVNULL, timeout=2
        ).strip()
        info["dirty"] = bool(status)
    except Exception:
        pass
    return info


def create_file(path: str | Path, content: str, cwd: Path | None = None) -> dict[str, Any]:
    """Convenience wrapper around apply for brand new files."""
    return apply_search_replace(path, "", content, cwd=cwd)
