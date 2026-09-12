#!/usr/bin/env python3
"""
Code Manipulation Tools for Agent System
Wraps llama_code_tools functions as BaseTool instances for use in the ReACT agent loop.

These tools enable Claude Code-like behavior:
- Read source code files
- Search across the codebase
- Edit files with automatic backups
- List project structure
- Verify changes
"""

import logging
import os
from pathlib import Path
from typing import Dict, Any

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult, register_tool
from backend.tools.llama_code_tools import (
    read_code,
    search_code,
    list_files,
    verify_change
)
from backend.models import db, Folder
import json
import ast
from backend.services.guarded_code_service import (
    GuardedCodeError,
    apply_exact_replacement,
    stage_pending_fix,
    is_codebase_locked,
)

logger = logging.getLogger(__name__)

# Directories and files that EditCodeTool must not modify
EDIT_CODE_FORBIDDEN_SEGMENTS = (
    ".git",
    "node_modules",
    "venv",
    "__pycache__",
    "dist",
    ".env",
)


def _is_protected_file(filepath: str) -> tuple[bool, str | None]:
    """Check if file is protected from autonomous modification."""
    from backend.config import PROTECTED_FILES
    normalized = filepath.replace("\\", "/")
    for protected in PROTECTED_FILES:
        if normalized.endswith(protected) or protected in normalized:
            return True, (
                f"BLOCKED: '{protected}' is protected by the kill switch architecture "
                f"and cannot be modified by autonomous processes. "
                f"Request a human to make this change."
            )
    return False, None




def _self_improvement_apply_blocked() -> bool:
    """Self-improvement-specific apply gate (default-on block).

    Defaults to True (blocked). Checked only when `_self_improvement_context`
    is set on the tool call, so user-initiated chat-driven edits are
    unaffected. To enable autonomous edits from the self-improvement loop,
    set `self_improvement_apply_enabled=true` in system_setting.
    """
    try:
        from backend.models import db, SystemSetting
        setting = db.session.query(SystemSetting).filter_by(
            key="self_improvement_apply_enabled"
        ).first()
        if setting is None:
            return True  # default: block self-improvement-driven apply
        return setting.value.lower() != "true"
    except Exception:
        return True  # DB unreachable → fail closed


def _handle_uncle_directive(directive: str, reason: str):
    """Execute Uncle Claude's kill switch directive."""
    logger.critical(f"Uncle Claude directive: {directive} — {reason}")
    from backend.models import db, SystemSetting

    if directive in ("halt_self_improvement", "lock_codebase", "halt_family"):
        setting = db.session.query(SystemSetting).filter_by(key="self_improvement_enabled").first()
        if setting:
            setting.value = "false"
        else:
            db.session.add(SystemSetting(key="self_improvement_enabled", value="false"))

    if directive in ("lock_codebase", "halt_family"):
        setting = db.session.query(SystemSetting).filter_by(key="codebase_locked").first()
        if setting:
            setting.value = "true"
        else:
            db.session.add(SystemSetting(key="codebase_locked", value="true"))
        import os
        from datetime import datetime
        lock_file = os.path.join(os.environ.get("GUAARDVARK_ROOT", "."), "data", ".codebase_lock")
        os.makedirs(os.path.dirname(lock_file), exist_ok=True)
        with open(lock_file, "w") as f:
            f.write(f"UNCLE_DIRECTIVE={directive}\nREASON={reason}\nTIMESTAMP={datetime.now().isoformat()}\n")

    db.session.commit()

    if directive == "halt_family":
        try:
            from backend.services.interconnector_sync_service import InterconnectorSyncService
            sync_service = InterconnectorSyncService()
            sync_service.broadcast_directive("halt_family", reason)
        except Exception as e:
            logger.error(f"Failed to broadcast halt_family directive: {e}")


def _is_edit_forbidden(filepath: str) -> tuple[bool, str | None]:
    """Return (True, reason) if filepath is in a forbidden location, else (False, None)."""
    if not filepath or not filepath.strip():
        return True, "Empty or missing filepath"
    normalized = filepath.replace("\\", "/").strip("/")
    parts = normalized.split("/")
    for segment in EDIT_CODE_FORBIDDEN_SEGMENTS:
        if segment in parts:
            return True, f"Edits are not allowed inside '{segment}/'"
        if normalized == segment or normalized.endswith("/" + segment):
            return True, f"Edits are not allowed for '{segment}'"
    if parts and parts[-1].strip() == ".env":
        return True, "Edits are not allowed for .env files"
    return False, None


class ReadCodeTool(BaseTool):
    """Tool to read source code files"""

    name = "read_code"
    description = (
        "Read the complete contents of a source code file. "
        "Returns file content with line count and character count. "
        "Use this to understand existing code before making modifications. "
        "Accepts paths relative to the project root and explicit absolute paths for user-referenced external files."
    )
    parameters = {
        "filepath": ToolParameter(
            name="filepath",
            type="string",
            required=True,
            description="Path relative to project root, or an explicit absolute path for an external text file"
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        filepath = kwargs.get("filepath")

        if not filepath:
            return ToolResult(
                success=False,
                error="Missing required parameter: filepath"
            )

        try:
            result = read_code(filepath)

            # Check if result indicates an error
            if result.startswith("ERROR"):
                return ToolResult(
                    success=False,
                    error=result,
                    metadata={"filepath": filepath}
                )

            return ToolResult(
                success=True,
                output=result,
                metadata={"filepath": filepath}
            )
        except Exception as e:
            logger.error(f"ReadCodeTool failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Failed to read file: {str(e)}",
                metadata={"filepath": filepath}
            )


class SearchCodeTool(BaseTool):
    """Tool to search for patterns across the codebase"""

    name = "search_code"
    description = (
        "Search for code patterns across the project using case-insensitive regex. "
        "Returns all matches with file paths, line numbers, and matched content. "
        "Use this to find where code patterns exist before making changes."
    )
    parameters = {
        "pattern": ToolParameter(
            name="pattern",
            type="string",
            required=True,
            description="Text or regex pattern to search for (e.g., 'handleClick', 'Button.*onClick')"
        ),
        "file_glob": ToolParameter(
            name="file_glob",
            type="string",
            required=False,
            default="**/*.{py,jsx,js,tsx,ts}",
            description="Glob pattern for files to search (default: '**/*.{py,jsx,js,tsx,ts}')"
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        pattern = kwargs.get("pattern")
        file_glob = kwargs.get("file_glob", "**/*.{py,jsx,js,tsx,ts}")

        if not pattern:
            return ToolResult(
                success=False,
                error="Missing required parameter: pattern"
            )

        try:
            result = search_code(pattern, file_glob)

            # Check for no matches (not necessarily an error)
            is_no_match = "No matches found" in result

            return ToolResult(
                success=True,
                output=result,
                metadata={
                    "pattern": pattern,
                    "file_glob": file_glob,
                    "has_matches": not is_no_match
                }
            )
        except Exception as e:
            logger.error(f"SearchCodeTool failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Search failed: {str(e)}",
                metadata={"pattern": pattern}
            )


class EditCodeTool(BaseTool):
    """Tool to edit source code files by text replacement"""

    name = "edit_code"
    is_dangerous = True
    requires_approval = True
    description = (
        "Edit a source code file by replacing exact text. Creates automatic backup. "
        "The old_text MUST be unique in the file or the edit will fail. "
        "Use read_code first to get the exact text to replace. "
        "Use empty string for new_text to delete code. "
        "Accepts paths relative to the project root and explicit absolute paths for user-referenced external files."
    )
    parameters = {
        "filepath": ToolParameter(
            name="filepath",
            type="string",
            required=True,
            description="Path relative to project root, or an explicit absolute path for an external text file"
        ),
        "old_text": ToolParameter(
            name="old_text",
            type="string",
            required=True,
            description="The EXACT text to replace (must be unique in file). For best results with the drift guard, obtain this via a recent read_code call."
        ),
        "new_text": ToolParameter(
            name="new_text",
            type="string",
            required=True,
            description="The new text to insert (can be empty string for deletion)"
        ),
        "dry_run": ToolParameter(
            name="dry_run",
            type="bool",
            required=False,
            default=False,
            description="If True, verifies the edit matches and compiles without writing changes to disk (default: False)"
        ),
        "expected_hash": ToolParameter(
            name="expected_hash",
            type="string",
            required=False,
            description="Optional sha256 hex digest of the file content at the time it was read (enables drift detection)."
        ),
        "expected_mtime": ToolParameter(
            name="expected_mtime",
            type="number",
            required=False,
            description="Optional file mtime (float from os.stat) at read time (helps drift detection)."
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        filepath = kwargs.get("filepath")
        old_text = kwargs.get("old_text")
        new_text = kwargs.get("new_text", "")
        dry_run = kwargs.get("dry_run", False)
        expected_hash = kwargs.get("expected_hash")
        expected_mtime = kwargs.get("expected_mtime")

        if not filepath:
            return ToolResult(
                success=False,
                error="Missing required parameter: filepath"
            )
        # Resolve relative paths against GUAARDVARK_ROOT for Celery worker compatibility
        if not os.path.isabs(filepath):
            root = os.environ.get("GUAARDVARK_ROOT", "")
            if root:
                filepath = os.path.join(root, filepath)
        if old_text is None:
            return ToolResult(
                success=False,
                error="Missing required parameter: old_text"
            )

        # Kill switch: block all edits when codebase is locked
        if is_codebase_locked():
            return ToolResult(
                success=False,
                error="BLOCKED: Codebase is locked. A user must unlock it before autonomous edits can proceed.",
                metadata={"blocked_by": "kill_switch"}
            )

        # Kill switch: block edits to protected files
        is_protected, protection_msg = _is_protected_file(filepath)
        if is_protected:
            return ToolResult(
                success=False,
                error=protection_msg,
                metadata={"blocked_by": "protected_files"}
            )

        # Safety: block edits to restricted directories and sensitive files
        forbidden, reason = _is_edit_forbidden(filepath)
        if forbidden:
            return ToolResult(
                success=False,
                error=f"ERROR: {reason}",
                metadata={"filepath": filepath, "blocked_by": "FORBIDDEN_PATH"}
            )

        # Extract agent context (set by AgentExecutor.set_tool_context)
        ctx = kwargs.pop("_agent_context", {})

        # Guardian review (Uncle Claude) — only during self-improvement (skip if dry_run)
        if ctx.get("_self_improvement_context") and not dry_run:
            try:
                from backend.services.claude_advisor_service import get_claude_advisor
                advisor = get_claude_advisor()
                if advisor.is_available():
                    review = advisor.review_change(
                        file_path=filepath,
                        current_content=open(filepath).read()[:3000] if os.path.exists(filepath) else "",
                        proposed_diff=f"- {old_text[:500]}\n+ {new_text[:500]}",
                        reasoning=ctx.get("_reasoning", "Autonomous code change"),
                    )
                    if not review.get("approved", True):
                        directive = review.get("directive", "reject")
                        if directive in ("halt_self_improvement", "lock_codebase", "halt_family"):
                            _handle_uncle_directive(directive, review.get("reason", ""))
                        return ToolResult(
                            success=False,
                            error=f"Uncle Claude rejected this change: {review.get('reason', 'No reason given')}. "
                                  f"Suggestions: {', '.join(review.get('suggestions', []))}",
                            metadata={"guardian_review": review}
                        )
            except Exception as e:
                logger.warning(f"Guardian review failed, proceeding with caution: {e}")

            # Stage diff to pending_fixes instead of applying directly.
            try:
                pending_id = stage_pending_fix(
                    filepath,
                    old_text,
                    new_text,
                    ctx.get("_reasoning", "Autonomous fix"),
                    run_id=ctx.get("_run_id"),
                )
                return ToolResult(
                    success=True,
                    output=f"Fix staged for review (pending_fix #{pending_id}). "
                           f"File: {filepath}. "
                           f"The change will be applied after approval.",
                    metadata={"staged": True, "pending_fix_id": pending_id, "filepath": filepath}
                )
            except GuardedCodeError as e:
                return ToolResult(
                    success=False,
                    error=f"Failed to stage fix for review: {e}",
                    metadata={"staging_failed": True, "blocked_by": e.code}
                )
            except Exception as e:
                logger.error(f"Failed to stage fix: {e}", exc_info=True)
                # FAIL HARD — never silently switch from reviewed to unreviewed
                return ToolResult(
                    success=False,
                    error=f"Failed to stage fix for review: {e}. Fix NOT applied.",
                    metadata={"staging_failed": True}
                )

        try:
            edit_result = apply_exact_replacement(
                filepath,
                old_text,
                new_text,
                dry_run=dry_run,
                allow_external=True,
                expected_hash=expected_hash,
                expected_mtime=expected_mtime,
            )
            diff = edit_result.diff
            if len(diff) > 4000:
                diff = diff[:4000] + "\n... diff truncated ..."
            action = "Dry run succeeded for" if dry_run else "Successfully edited"
            return ToolResult(
                success=True,
                output=(
                    f"{action} '{edit_result.relative_path}'. "
                    f"Backup: {edit_result.backup_path}. "
                    f"Verification: {edit_result.verification['output_summary']}"
                    f"\n\nDiff:\n{diff}"
                ),
                metadata={
                    "filepath": edit_result.file_path,
                    "relative_path": edit_result.relative_path,
                    "backup_path": edit_result.backup_path,
                    "diff": edit_result.diff,
                    "verification": edit_result.verification,
                    "operation": "dry_run" if dry_run else ("deleted" if not new_text else "replaced"),
                }
            )
        except GuardedCodeError as e:
            error_msg = str(e)
            if e.code in {"TEXT_NOT_FOUND", "TEXT_NOT_UNIQUE"}:
                error_msg += (
                    "\n\nSUGGESTION: Use read_code(filepath) first to get the exact text including whitespace, "
                    "then retry with enough surrounding context for one unique match."
                )
            return ToolResult(
                success=False,
                error=error_msg,
                metadata={
                    "filepath": filepath,
                    "blocked_by": e.code,
                    "old_text_preview": (old_text[:100] + "..." if len(old_text or "") > 100 else (old_text or "")),
                    "new_text_preview": (new_text[:100] + "..." if len(new_text or "") > 100 else (new_text or "")),
                }
            )
        except Exception as e:
            logger.error(f"EditCodeTool failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Edit failed: {str(e)}",
                metadata={"filepath": filepath}
            )


class ListCodeFilesTool(BaseTool):
    """Tool to list project directory structure (code-exploration)."""

    name = "list_code_files"
    description = (
        "List files and directories to understand project structure. "
        "Returns a formatted tree view of the directory contents. "
        "Use this to explore the codebase and find relevant files."
    )
    parameters = {
        "directory": ToolParameter(
            name="directory",
            type="string",
            required=False,
            default="frontend/src",
            description="Relative path from project root (default: 'frontend/src')"
        ),
        "max_depth": ToolParameter(
            name="max_depth",
            type="int",
            required=False,
            default=5,
            description="Maximum directory depth to show (default: 5)"
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        directory = kwargs.get("directory", "frontend/src")
        max_depth = kwargs.get("max_depth", 5)

        # Ensure max_depth is an integer
        if isinstance(max_depth, str):
            try:
                max_depth = int(max_depth)
            except ValueError:
                max_depth = 2

        try:
            result = list_files(directory, max_depth)

            # Check if result indicates an error
            if result.startswith("ERROR"):
                return ToolResult(
                    success=False,
                    error=result,
                    metadata={"directory": directory}
                )

            return ToolResult(
                success=True,
                output=result,
                metadata={
                    "directory": directory,
                    "max_depth": max_depth
                }
            )
        except Exception as e:
            logger.error(f"ListCodeFilesTool failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"List files failed: {str(e)}",
                metadata={"directory": directory}
            )


class VerifyChangeTool(BaseTool):
    """Tool to verify code changes were applied correctly"""

    name = "verify_change"
    description = (
        "Verify that a code change was successful by checking if text exists in file. "
        "Use after edit_code to confirm changes were applied correctly. "
        "Set should_exist=False to verify that text was successfully removed. "
        "Accepts paths relative to the project root and explicit absolute paths for user-referenced external files."
    )
    parameters = {
        "filepath": ToolParameter(
            name="filepath",
            type="string",
            required=True,
            description="Path relative to project root, or an explicit absolute path for an external text file"
        ),
        "expected_text": ToolParameter(
            name="expected_text",
            type="string",
            required=True,
            description="Text to check for in the file"
        ),
        "should_exist": ToolParameter(
            name="should_exist",
            type="bool",
            required=False,
            default=True,
            description="True if text should exist, False to verify deletion (default: True)"
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        filepath = kwargs.get("filepath")
        expected_text = kwargs.get("expected_text")
        should_exist = kwargs.get("should_exist", True)

        if not filepath:
            return ToolResult(
                success=False,
                error="Missing required parameter: filepath"
            )
        if not expected_text:
            return ToolResult(
                success=False,
                error="Missing required parameter: expected_text"
            )

        # Handle string boolean values
        if isinstance(should_exist, str):
            should_exist = should_exist.lower() in ('true', '1', 'yes')

        try:
            result = verify_change(filepath, expected_text, should_exist)

            # Check if verification passed or failed
            verification_passed = "✓ VERIFIED" in result

            return ToolResult(
                success=verification_passed,
                output=result,
                error=None if verification_passed else result,
                metadata={
                    "filepath": filepath,
                    "expected_text_preview": expected_text[:50] if expected_text else "",
                    "should_exist": should_exist,
                    "verification_passed": verification_passed
                }
            )
        except Exception as e:
            logger.error(f"VerifyChangeTool failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Verification failed: {str(e)}",
                metadata={"filepath": filepath}
            )


def _repository_folder(tool: BaseTool, folder_id, with_physical_path: bool = False) -> tuple[dict | None, str | None]:
    """What the repository tools need to know about a folder: is_repository,
    parsed metadata and, when asked, its root on disk. Returns (info, None) or
    (None, error). The MCP server has no Flask app, so there it asks the backend."""
    from backend.utils.backend_http import BackendError, is_mcp_transport, request_json

    if is_mcp_transport(tool):
        try:
            return request_json("GET", f"/api/files/folder/{int(folder_id)}/repository").data, None
        except BackendError as e:
            if e.status == 404:
                return None, f"Folder {folder_id} not found."
            return None, str(e)

    folder = db.session.get(Folder, folder_id)
    if not folder:
        return None, f"Folder {folder_id} not found."
    info = {
        "id": folder.id,
        "path": folder.path,
        "is_repository": bool(folder.is_repository),
        "metadata": json.loads(folder.repo_metadata) if folder.repo_metadata else None,
    }
    if with_physical_path:
        from backend.api.files_api import get_physical_path
        info["physical_path"] = str(get_physical_path(folder.path).resolve())
    return info, None


class GetRepositoryMapTool(BaseTool):
    """Tool to get the PageRank-based repository map of a Code Repository folder."""

    name = "get_repository_map"
    description = (
        "Retrieve the PageRank-based architectural repository map for a given folder ID. "
        "This map shows the most important functions and classes in the codebase and their relationships. "
        "Use this tool to get a high-level understanding of a Code Repository."
    )
    parameters = {
        "folder_id": ToolParameter(
            name="folder_id",
            type="int",
            required=True,
            description="The integer ID of the Code Repository folder."
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        folder_id = kwargs.get("folder_id")
        if not folder_id:
            return ToolResult(success=False, error="Missing required parameter: folder_id")

        try:
            folder, err = _repository_folder(self, folder_id)
            if err:
                return ToolResult(success=False, error=err)

            if not folder["is_repository"]:
                return ToolResult(success=False, error=f"Folder {folder_id} is not marked as a Code Repository.")

            if not folder["metadata"]:
                return ToolResult(success=False, error=f"Folder {folder_id} has no repository metadata generated yet.")

            repo_map = folder["metadata"].get("repository_map")
            
            if not repo_map:
                return ToolResult(success=False, error="No repository map found in the metadata. It may still be generating.")

            return ToolResult(
                success=True,
                output=repo_map,
                metadata={"folder_id": folder_id}
            )
        except Exception as e:
            logger.error(f"GetRepositoryMapTool failed: {e}", exc_info=True)
            return ToolResult(success=False, error=f"Failed to get repository map: {str(e)}")


class GetDependencyGraphTool(BaseTool):
    """Tool to get the import dependency graph of a Code Repository folder."""

    name = "get_dependency_graph"
    description = (
        "Retrieve the file-level import dependency graph for a given folder ID. "
        "This returns a JSON string mapping files to the files they import. "
        "Use this tool to trace dependencies and understand how files interact."
    )
    parameters = {
        "folder_id": ToolParameter(
            name="folder_id",
            type="int",
            required=True,
            description="The integer ID of the Code Repository folder."
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        folder_id = kwargs.get("folder_id")
        if not folder_id:
            return ToolResult(success=False, error="Missing required parameter: folder_id")

        try:
            folder, err = _repository_folder(self, folder_id)
            if err:
                return ToolResult(success=False, error=err)

            if not folder["is_repository"]:
                return ToolResult(success=False, error=f"Folder {folder_id} is not marked as a Code Repository.")

            if not folder["metadata"]:
                return ToolResult(success=False, error=f"Folder {folder_id} has no repository metadata generated yet.")

            dep_graph = folder["metadata"].get("dependency_graph")
            
            if not dep_graph:
                return ToolResult(success=False, error="No dependency graph found in the metadata.")

            return ToolResult(
                success=True,
                output=json.dumps(dep_graph, indent=2),
                metadata={"folder_id": folder_id, "node_count": len(dep_graph)}
            )
        except Exception as e:
            logger.error(f"GetDependencyGraphTool failed: {e}", exc_info=True)
            return ToolResult(success=False, error=f"Failed to get dependency graph: {str(e)}")


class ReadASTNodeTool(BaseTool):
    """Tool to precisely extract a class or function from a Python file using AST."""

    name = "read_ast_node"
    description = (
        "Read the exact source code of a specific class or function from a Python file in a Code Repository folder. "
        "This is more precise and token-efficient than reading the entire file. "
        "Only supports Python (.py) files currently and requires a repository-relative filepath."
    )
    parameters = {
        "folder_id": ToolParameter(
            name="folder_id",
            type="int",
            required=True,
            description="The integer ID of the Code Repository folder."
        ),
        "filepath": ToolParameter(
            name="filepath",
            type="string",
            required=True,
            description="Path to the Python file, relative to the Code Repository folder."
        ),
        "node_name": ToolParameter(
            name="node_name",
            type="string",
            required=True,
            description="The name of the class or function to extract (e.g., 'MyClass' or 'my_function')."
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        folder_id = kwargs.get("folder_id")
        filepath = kwargs.get("filepath")
        node_name = kwargs.get("node_name")

        if not folder_id or not filepath or not node_name:
            return ToolResult(success=False, error="Missing required parameter: folder_id, filepath, or node_name")

        if not filepath.endswith(".py"):
            return ToolResult(success=False, error="read_ast_node currently only supports Python (.py) files.")

        try:
            if Path(filepath).is_absolute():
                return ToolResult(success=False, error="filepath must be relative to the Code Repository folder.")

            folder, err = _repository_folder(self, folder_id, with_physical_path=True)
            if err:
                return ToolResult(success=False, error=err)

            if not folder["is_repository"]:
                return ToolResult(success=False, error=f"Folder {folder_id} is not marked as a Code Repository.")

            if not folder.get("physical_path"):
                return ToolResult(success=False, error=f"Folder {folder_id} has no location on disk.")

            repo_root = Path(folder["physical_path"]).resolve()
            full_path = (repo_root / filepath).resolve()
            try:
                full_path.relative_to(repo_root)
            except ValueError:
                return ToolResult(success=False, error="filepath resolves outside the Code Repository folder.")

            if not full_path.exists():
                return ToolResult(success=False, error=f"File not found: {filepath}")

            source = full_path.read_text(encoding="utf-8")

            try:
                tree = ast.parse(source)
            except SyntaxError as e:
                return ToolResult(success=False, error=f"Syntax error in file, cannot parse AST: {e}")

            matches = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name == node_name:
                        lines = source.splitlines()
                        start_line = node.lineno - 1
                        end_line = node.end_lineno
                        if node.decorator_list:
                            start_line = node.decorator_list[0].lineno - 1

                        matches.append({
                            "type": type(node).__name__,
                            "start_line": start_line + 1,
                            "end_line": end_line,
                            "source": "\n".join(lines[start_line:end_line]),
                        })

            if matches:
                if len(matches) == 1:
                    output = matches[0]["source"]
                else:
                    output = "\n\n".join(
                        f"# Match {idx}: {match['type']} lines {match['start_line']}-{match['end_line']}\n{match['source']}"
                        for idx, match in enumerate(matches, start=1)
                    )
                return ToolResult(
                    success=True,
                    output=output,
                    metadata={
                        "folder_id": folder_id,
                        "filepath": filepath,
                        "node_name": node_name,
                        "match_count": len(matches),
                        "matches": [
                            {k: v for k, v in match.items() if k != "source"}
                            for match in matches
                        ],
                    }
                )
            return ToolResult(success=False, error=f"Node '{node_name}' not found in {filepath}.")
            
        except Exception as e:
            logger.error(f"ReadASTNodeTool failed: {e}", exc_info=True)
            return ToolResult(success=False, error=f"Failed to read AST node: {str(e)}")


class ListCodeRepositoriesTool(BaseTool):
    """Tool to list all folders marked as Code Repositories (for discovery in NL flows)."""

    name = "list_code_repositories"
    description = (
        "List all Code Repository folders that have been marked as such (is_repository=True) and analyzed. "
        "Returns id, name, path, and whether repo_metadata is available. "
        "Use this first when the user refers to 'the uploaded code', 'guaardvark upload folder', 'the code repo in data/uploads/Code', "
        "or similar to discover the folder_id(s) needed for get_repository_map, get_dependency_graph, read_ast_node, etc. "
        "This helps the agent get a full picture of available code repositories before analyzing or editing."
    )
    parameters = {}

    def execute(self, **kwargs) -> ToolResult:
        try:
            from backend.utils.backend_http import is_mcp_transport, request_json

            if is_mcp_transport(self):
                result = list((request_json("GET", "/api/files/repositories").data or {}).get("repositories") or [])
            else:
                repos = Folder.query.filter_by(is_repository=True).all()
                result = []
                for f in repos:
                    result.append({
                        "id": f.id,
                        "name": f.name,
                        "path": f.path,
                        "has_metadata": bool(f.repo_metadata),
                        "description": (f.description or "")[:200] if f.description else ""
                    })

            # Always surface the live main source root (GUAARDVARK_ROOT) even if not a DB-indexed Code Repo.
            # Agent can use read_code / search_code / list_code_files with absolute or relative paths from here.
            # For full map/graph, the folder should be marked is_repository and analyzed.
            try:
                root = os.environ.get("GUAARDVARK_ROOT") or os.getcwd()
                result.append({
                    "id": "live",
                    "name": "Live main codebase (GUAARDVARK_ROOT)",
                    "path": root,
                    "has_metadata": False,
                    "description": "The running Guaardvark source root. Use file tools (read_code etc.) directly with this path for exploration. Mark as Code Repo in Documents for map tools."
                })
            except Exception:
                pass

            return ToolResult(
                success=True,
                output=result,
                metadata={"count": len(result)}
            )
        except Exception as e:
            logger.error(f"ListCodeRepositoriesTool failed: {e}", exc_info=True)
            return ToolResult(success=False, error=f"Failed to list code repos: {str(e)}")


# Tool instances for registration
CODE_MANIPULATION_TOOLS = [
    ReadCodeTool(),
    SearchCodeTool(),
    EditCodeTool(),
    ListCodeFilesTool(),
    VerifyChangeTool(),
    GetRepositoryMapTool(),
    GetDependencyGraphTool(),
    ReadASTNodeTool(),
    ListCodeRepositoriesTool(),
]


def register_code_manipulation_tools():
    """Register all code manipulation tools in the global registry"""
    for tool in CODE_MANIPULATION_TOOLS:
        try:
            register_tool(tool)
            logger.info(f"Registered code manipulation tool: {tool.name}")
        except Exception as e:
            logger.error(f"Failed to register tool {tool.name}: {e}")

    logger.info(f"Registered {len(CODE_MANIPULATION_TOOLS)} code manipulation tools")


# Export
__all__ = [
    'ReadCodeTool',
    'SearchCodeTool',
    'EditCodeTool',
    'ListCodeFilesTool',
    'VerifyChangeTool',
    'ListCodeRepositoriesTool',
    'CODE_MANIPULATION_TOOLS',
    'register_code_manipulation_tools',
]
