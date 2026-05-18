"""
Adapter: ``data/outputs/`` → MCP ``Resource``.

Exposes generated images / videos / audio / code / docs under the
``guaardvark://outputs/{relative_path}`` URI scheme. Read-only. Chrooted:
any URI that resolves outside the configured outputs root is denied.

Phase 1 keeps it dumb — we enumerate the filesystem on each list request
rather than maintaining an index. ``data/outputs/`` is not huge (hundreds
of files at most) so a stat walk is fine. A cached index can land later
if we measure it's a problem.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

import mcp.types as mcp_types
from pydantic import AnyUrl

from backend.mcp.audit import audit_call
from backend.mcp.config import MCPConfig

logger = logging.getLogger(__name__)

URI_SCHEME = "guaardvark"
URI_PREFIX = f"{URI_SCHEME}://outputs/"

# Directories and file patterns we never surface (runtime state, not artifacts).
_EXCLUDE_DIRS = {".progress_jobs", "__pycache__", ".git"}
_EXCLUDE_FILES = {".DS_Store", "Thumbs.db"}
# Bounded walk — don't blow out a listing on a runaway directory.
_MAX_FILES_LISTED = 500


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _outputs_root(config: MCPConfig) -> Path:
    root = Path(config.resources.outputs_root)
    if not root.is_absolute():
        root = _project_root() / root
    return root.resolve()


def _uri_for(path: Path, root: Path) -> str:
    rel = path.relative_to(root).as_posix()
    # Percent-encode each segment; keep the slash separators intact.
    return URI_PREFIX + "/".join(quote(seg, safe="") for seg in rel.split("/"))


def _path_for_uri(uri: str, root: Path) -> Path | None:
    """Resolve a guaardvark://outputs/... URI to a file path, or None if bad."""
    if not uri.startswith(URI_PREFIX):
        return None
    parsed = urlparse(uri)
    if parsed.scheme != URI_SCHEME:
        return None
    rel = unquote(parsed.path.lstrip("/"))
    # urlparse on guaardvark://outputs/foo.png gives netloc='outputs', path='/foo.png'.
    # Re-join the netloc if present so we land on the right subtree.
    if parsed.netloc and not rel.startswith(parsed.netloc):
        rel = f"{parsed.netloc}/{rel}".lstrip("/")
    if rel.startswith("outputs/"):
        rel = rel[len("outputs/"):]

    candidate = (root / rel).resolve()
    # Chroot check — the resolved path must be under root. No .. escapes.
    try:
        candidate.relative_to(root)
    except ValueError:
        logger.warning("MCP: rejected out-of-chroot URI %s", uri)
        return None
    return candidate


def _mime_for(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    return mime or "application/octet-stream"


def _walk_outputs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if len(files) >= _MAX_FILES_LISTED:
            break
        if not path.is_file():
            continue
        if any(part in _EXCLUDE_DIRS for part in path.relative_to(root).parts):
            continue
        if path.name in _EXCLUDE_FILES:
            continue
        files.append(path)
    return files


def register_resources(server: Any, config: MCPConfig) -> int:
    """Wire list_resources + read_resource handlers. Returns count at registration time."""
    if not config.resources.outputs_enabled:
        logger.info("MCP: outputs resource provider disabled by config")
        return 0

    root = _outputs_root(config)
    if not root.exists():
        logger.info("MCP: outputs root %s does not exist yet — serving empty list", root)

    @server.list_resources()
    async def _list_resources() -> list[mcp_types.Resource]:
        with audit_call(method="resources/list", target=str(root)) as rec:
            files = _walk_outputs(root)
            rec["bytes_out"] = sum(p.stat().st_size for p in files) if files else 0
            return [
                mcp_types.Resource(
                    uri=AnyUrl(_uri_for(path, root)),
                    name=path.name,
                    description=f"Generated output: {path.relative_to(root).as_posix()}",
                    mimeType=_mime_for(path),
                )
                for path in files
            ]

    @server.read_resource()
    async def _read_resource(uri: AnyUrl):
        uri_str = str(uri)
        with audit_call(method="resources/read", target=uri_str) as rec:
            path = _path_for_uri(uri_str, root)
            if path is None or not path.is_file():
                rec["outcome"] = "error"
                rec["error_code"] = "not_found"
                raise FileNotFoundError(f"Unknown or out-of-scope resource: {uri_str}")

            mime = _mime_for(path)
            rec["bytes_out"] = path.stat().st_size

            # Text vs binary split. Text MIME types go as UTF-8 text; everything
            # else goes as base64 blob via the SDK's ReadResourceContents helper.
            if mime.startswith("text/") or mime in {"application/json", "application/xml"}:
                return path.read_text(encoding="utf-8", errors="replace")
            return path.read_bytes()

    # Count at registration time for the startup banner; the real list is
    # re-walked on every request so new files show up without restart.
    return len(_walk_outputs(root))
