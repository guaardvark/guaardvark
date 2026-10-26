#!/usr/bin/env python3
"""
File Operation Tools
Tools for reading, listing, and processing files
"""

import logging
import os
from pathlib import Path
from typing import Optional, List

from backend.services.agent_tools import BaseTool, ToolResult, ToolParameter

logger = logging.getLogger(__name__)


class ReadFileTool(BaseTool):
    """Read file contents with optional line range"""
    
    name = "read_file"
    read_only = True
    description = "Read the contents of a file, optionally specifying a line range"
    parameters = {
        "file_path": ToolParameter(
            name="file_path",
            type="string",
            required=True,
            description="Path to the file to read"
        ),
        "offset": ToolParameter(
            name="offset",
            type="int",
            required=False,
            description="Starting line number (1-indexed, optional)",
            default=None
        ),
        "limit": ToolParameter(
            name="limit",
            type="int",
            required=False,
            description="Number of lines to read (optional)",
            default=None
        )
    }
    
    def execute(self, file_path: str, offset: Optional[int] = None, limit: Optional[int] = None) -> ToolResult:
        """
        Read file contents
        
        Args:
            file_path: Path to file
            offset: Starting line number (1-indexed)
            limit: Number of lines to read
            
        Returns:
            ToolResult with file contents
        """
        try:
            # Resolve and validate path
            path = Path(file_path).resolve()
            
            if not path.exists():
                return ToolResult(
                    success=False,
                    error=f"File not found: {file_path}"
                )
            
            if not path.is_file():
                return ToolResult(
                    success=False,
                    error=f"Path is not a file: {file_path}"
                )
            
            # Read file
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    if offset is not None:
                        # Skip to offset
                        for _ in range(offset - 1):
                            f.readline()
                    
                    if limit is not None:
                        # Read limited lines
                        lines = [f.readline() for _ in range(limit)]
                        content = ''.join(lines)
                    else:
                        content = f.read()
                
                # Get file metadata
                file_size = path.stat().st_size
                line_count = content.count('\n') + 1 if content else 0
                
                return ToolResult(
                    success=True,
                    output=content,
                    metadata={
                        'file_path': str(path),
                        'file_size': file_size,
                        'line_count': line_count,
                        'offset': offset,
                        'limit': limit
                    }
                )
                
            except UnicodeDecodeError:
                return ToolResult(
                    success=False,
                    error=f"File is not a text file or has encoding issues: {file_path}"
                )
                
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Failed to read file: {str(e)}"
            )


class ListFilesTool(BaseTool):
    """List files and directories in a given path"""
    
    name = "list_files"
    read_only = True
    description = "List files and directories in a specified path"
    parameters = {
        "directory": ToolParameter(
            name="directory",
            type="string",
            required=True,
            description="Directory path to list"
        ),
        "recursive": ToolParameter(
            name="recursive",
            type="bool",
            required=False,
            description="List files recursively (default: False)",
            default=False
        ),
        "pattern": ToolParameter(
            name="pattern",
            type="string",
            required=False,
            description="File pattern to match (e.g., '*.py', optional)",
            default=None
        )
    }
    
    def execute(self, directory: str, recursive: bool = False, pattern: Optional[str] = None) -> ToolResult:
        """
        List files in directory
        
        Args:
            directory: Directory path
            recursive: List recursively
            pattern: Optional file pattern
            
        Returns:
            ToolResult with file list
        """
        try:
            path = Path(directory).resolve()
            
            if not path.exists():
                return ToolResult(
                    success=False,
                    error=f"Directory not found: {directory}"
                )
            
            if not path.is_dir():
                return ToolResult(
                    success=False,
                    error=f"Path is not a directory: {directory}"
                )
            
            # List files
            files = []
            dirs = []
            
            if recursive:
                if pattern:
                    file_list = path.rglob(pattern)
                else:
                    file_list = path.rglob('*')
            else:
                if pattern:
                    file_list = path.glob(pattern)
                else:
                    file_list = path.glob('*')
            
            for item in file_list:
                rel_path = str(item.relative_to(path))
                if item.is_file():
                    files.append({
                        'path': rel_path,
                        'size': item.stat().st_size,
                        'type': 'file'
                    })
                elif item.is_dir():
                    dirs.append({
                        'path': rel_path,
                        'type': 'directory'
                    })
            
            # Format output
            output = []
            if dirs:
                output.append("Directories:")
                for d in sorted(dirs, key=lambda x: x['path']):
                    output.append(f"  📁 {d['path']}/")
            
            if files:
                output.append("\nFiles:")
                for f in sorted(files, key=lambda x: x['path']):
                    size_kb = f['size'] / 1024
                    output.append(f"  📄 {f['path']} ({size_kb:.1f} KB)")
            
            output_text = "\n".join(output) if output else "Empty directory"
            
            return ToolResult(
                success=True,
                output=output_text,
                metadata={
                    'directory': str(path),
                    'file_count': len(files),
                    'dir_count': len(dirs),
                    'recursive': recursive,
                    'pattern': pattern,
                    'files': files,
                    'directories': dirs
                }
            )
            
        except Exception as e:
            logger.error(f"Error listing directory {directory}: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Failed to list directory: {str(e)}"
            )


# Returned as-is (UTF-8): formats EnhancedFileProcessor has no extractor for, and
# CSV, whose extractor re-joins fields and drops their quoting.
PLAIN_TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".json", ".yaml", ".yml", ".toml", ".ini",
    ".html", ".htm", ".log", ".csv",
}
MAX_PLAIN_TEXT_BYTES = 10 * 1024 * 1024


class ProcessFileTool(BaseTool):
    """Extract the text of a document or image the user points at."""

    name = "process_file"
    read_only = True
    description = (
        "Extract the text of one document or image, after a header line 'Text of <path> (<format>, "
        "[N pages,] M words)'; nothing is written or indexed. PDF: the text layer of every page, no OCR "
        "(a scanned PDF comes back empty). DOCX: body paragraphs, then table rows. XML: the text of its "
        "elements, tags dropped. SVG: its title, desc and text elements, one per line. CSV, .txt, .md, .rst, .json, .yaml, .toml, .ini, .log and .html: the "
        "file as-is (UTF-8, up to 10 MB). Excel (.xlsx, .xlsm): a workbook summary, then for each sheet "
        "with data its size, column names and first 20 rows (up to 50 sheets and 10,000 rows a sheet "
        "are read); .xls needs the xlrd package and .xlsb the pyxlsb package, which a stock install "
        "lacks. A file that is password-protected, damaged or in an old format fails with an error "
        "that says which. "
        "Image text (.jpg, .jpeg, .png, .gif, .bmp, .webp) is read by a vision model in the local "
        "Ollama, and fails with an error when none is available. For documents already indexed use "
        "search_knowledge_base or read_document_section; for source code, read_code."
    )
    parameters = {
        "file_path": ToolParameter(
            name="file_path",
            type="string",
            required=True,
            description=(
                "File to read: a path relative to the Guaardvark folder (tried first, e.g. "
                "'data/uploads/report.pdf'), else relative to its uploads folder (e.g. "
                "'reports/q3.pdf'), or an absolute path inside Guaardvark's uploads, outputs or install "
                "folder. Files named like keys or credentials (.env*, *.pem, *.key, id_rsa*, "
                "credentials*, .netrc and similar) are refused everywhere. Inside the install folder, "
                "uploads and outputs included, anything under a .git, venv, node_modules, dist, logs "
                "or similar folder is refused, and so is git-ignored data other than uploads and "
                "outputs. A relative and an absolute path to the same file get the same answer. In "
                "Guaardvark's own chat an absolute path elsewhere also works, except system folders "
                "and anything under a hidden folder or named with a leading '.', and not with "
                "Settings > Project folder only on; over MCP it is refused."
            ),
        )
    }

    def _refusal(self, path: Path, root: Path, uploads: Path, outputs: Path) -> Optional[str]:
        """Why this resolved path may not be read, decided without touching the
        file, so a refusal never tells the caller whether it exists."""
        from backend.services.guarded_code_service import (
            external_path_reason, forbidden_path_reason, private_path_reason,
        )
        from backend.utils.backend_http import is_mcp_caller
        from backend.utils.path_safety import is_sensitive

        if is_sensitive(str(path)):
            return "credential, key and .env files are not read"
        internal = "files under .git, venv, node_modules, dist, logs and similar folders are not read"
        for base in (uploads, outputs):
            if path.is_relative_to(base):
                # Uploads and outputs are read although git ignores them. A
                # repository's own folders are still not documents, wherever the
                # repository was uploaded to.
                rel = path.relative_to(base).as_posix()
                return internal if rel not in ("", ".") and forbidden_path_reason(rel) else None
        if path.is_relative_to(root):
            rel = path.relative_to(root).as_posix()
            if forbidden_path_reason(rel):
                return internal
            if private_path_reason(rel, root):
                return "git-ignored local data is not read"
            return None
        if is_mcp_caller(self):
            return "it is outside Guaardvark's uploads, outputs and install folder, which is all this tool reads over MCP"
        if external_path_reason(path):
            return "system folders and key folders (.ssh, .aws, .gnupg and similar) are not read"
        # Hidden folders hold application credentials under names no list can
        # cover (~/.config/gh, ~/.claude, gcloud's config), so none are read.
        if any(part.startswith(".") for part in path.parts):
            return "hidden folders and files (names starting with '.') outside the install are not read"
        from backend.tools.code_tools import _confine_candidates
        if not _confine_candidates([str(path)]):
            return "it is outside the project folder (Settings: Project folder only)"
        return None

    def _resolve(self, file_path: str) -> tuple[Optional[Path], Optional[str]]:
        """The file to read, or why it may not be read."""
        from backend import config

        root = Path(config.GUAARDVARK_ROOT).resolve()
        uploads = Path(config.UPLOAD_DIR).resolve()
        outputs = Path(config.OUTPUT_DIR).resolve()

        raw = str(file_path or "").strip()
        if not raw or "\x00" in raw:
            return None, "file_path is empty or not a valid path"
        try:
            candidate = Path(raw).expanduser()
        except RuntimeError as e:  # ~user with no such user
            return None, f"'{file_path}' is not a valid path: {e}"
        # A relative path is tried in the Guaardvark folder, then in its uploads.
        # A name the first place refuses ('server.log', which the install's
        # .gitignore covers) may still be a file in uploads, so a refusal there
        # is kept and given only if uploads has no such file either. That way a
        # relative and an absolute path to the same upload get the same answer.
        options = [(candidate, None)] if candidate.is_absolute() else [(root / candidate, root), (uploads / candidate, uploads)]
        refusal = None
        for option, base in options:
            try:
                path = option.resolve()
            except (RuntimeError, OSError) as e:  # a symlink loop, for one
                return None, f"'{file_path}' could not be resolved: {e}"
            if base is not None and not path.is_relative_to(base):
                return None, f"'{file_path}' was refused: a relative path may not leave the Guaardvark folder"
            reason = self._refusal(path, root, uploads, outputs)
            if reason:
                refusal = refusal or reason
                continue
            try:
                if path.is_file():
                    return path, None
            except OSError as e:
                return None, f"'{file_path}' could not be read: {e}"
        if refusal:
            return None, f"'{file_path}' was refused: {refusal}"
        where = "" if candidate.is_absolute() else " (a relative path is looked up in the Guaardvark folder, then in its uploads)"
        return None, f"File not found: {file_path}{where}"

    def execute(self, file_path: str) -> ToolResult:
        path, problem = self._resolve(file_path)
        if problem:
            return ToolResult(success=False, error=problem)

        from backend import config
        root = Path(config.GUAARDVARK_ROOT).resolve()
        shown = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)

        if path.suffix.lower() in PLAIN_TEXT_SUFFIXES:
            try:
                if path.stat().st_size > MAX_PLAIN_TEXT_BYTES:
                    return ToolResult(success=False, error=f"{shown} is over 10 MB")
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return ToolResult(success=False, error=f"{shown} is not UTF-8 text")
            except OSError as e:
                return ToolResult(success=False, error=f"{shown} could not be read: {e}")
            words = len(text.split())
            return ToolResult(
                success=True,
                output=f"Text of {shown} ({path.suffix.lstrip('.').lower()}, {words} words):\n\n{text}",
                metadata={"file_path": shown, "format": path.suffix.lstrip(".").lower(), "word_count": words},
            )

        try:
            from backend.utils.enhanced_file_processor import FileProcessingError, create_file_processor
        except ImportError as e:
            logger.error(f"Enhanced file processor not available: {e}")
            return ToolResult(success=False, error="File processing system not available")
        def reason_text(reason) -> str:
            # A reader's message may spell out the file's full path.
            return str(reason).replace(str(path), shown)

        try:
            result = create_file_processor().process_file(str(path), raise_errors=True)
        except FileProcessingError as e:
            return ToolResult(success=False, error=f"Could not read {shown}: {reason_text(e)}")
        except Exception as e:
            logger.error(f"Error processing file {file_path}: {e}", exc_info=True)
            return ToolResult(success=False, error=f"Failed to process {shown}: {reason_text(e)}")

        if not result:
            kind = f"'{path.suffix.lower()}' files" if path.suffix else "files without an extension"
            return ToolResult(
                success=False,
                error=f"Cannot read {shown}: {kind} are not a type this tool reads",
            )

        meta = result.metadata
        fmt = meta.format.value
        text = result.text_content or ""
        if fmt in ("xlsx", "xls", "xlsm", "xlsb"):
            extraction = result.extraction_results or {}
            if not extraction.get("success"):
                reason = extraction.get("error") or "the workbook could not be read"
                return ToolResult(success=False, error=f"Could not read {shown}: {reason_text(reason)}")
        if fmt in ("jpg", "jpeg", "png", "gif", "bmp", "webp"):
            extraction = result.extraction_results or {}
            if not extraction.get("success"):
                # An OCR call that raised leaves no extraction result; its
                # reason is on the metadata.
                reason = (extraction.get("error") or getattr(meta, "extraction_error", None)
                          or "the image could not be read")
                return ToolResult(success=False, error=f"Could not read text from {shown}: {reason}")
            if not extraction.get("text_content"):
                text = "(no text found in the image)"

        counts = [fmt]
        if meta.page_count:
            counts.append(f"{meta.page_count} pages")
        if meta.word_count is not None:
            counts.append(f"{meta.word_count} words")
        return ToolResult(
            success=True,
            output=f"Text of {shown} ({', '.join(counts)}):\n\n{text}",
            metadata={
                'file_path': shown,
                'format': fmt,
                'size_bytes': meta.size_bytes,
                'word_count': meta.word_count,
                'page_count': meta.page_count,
                'mime_type': meta.mime_type,
            },
        )

