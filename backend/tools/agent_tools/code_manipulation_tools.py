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
from typing import Dict, Any

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult, register_tool
from backend.tools.llama_code_tools import (
    read_code,
    search_code,
    edit_code,
    list_files,
    verify_change
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


def _is_codebase_locked() -> bool:
    """Check if codebase is locked by user or Uncle Claude directive."""
    import os
    lock_file = os.path.join(os.environ.get("GUAARDVARK_ROOT", "."), "data", ".codebase_lock")
    if os.path.exists(lock_file):
        return True
    try:
        from backend.models import db, SystemSetting
        setting = db.session.query(SystemSetting).filter_by(key="codebase_locked").first()
        return setting and setting.value.lower() == "true"
    except Exception:
        return False


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
        "Use this to understand existing code before making modifications."
    )
    parameters = {
        "filepath": ToolParameter(
            name="filepath",
            type="string",
            required=True,
            description="Relative path from project root (e.g., 'frontend/src/pages/MyPage.jsx')"
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
    description = (
        "Edit a source code file by replacing exact text. Creates automatic backup. "
        "The old_text MUST be unique in the file or the edit will fail. "
        "Use read_code first to get the exact text to replace. "
        "Use empty string for new_text to delete code."
    )
    parameters = {
        "filepath": ToolParameter(
            name="filepath",
            type="string",
            required=True,
            description="Relative path from project root"
        ),
        "old_text": ToolParameter(
            name="old_text",
            type="string",
            required=True,
            description="The EXACT text to replace (must be unique in file)"
        ),
        "new_text": ToolParameter(
            name="new_text",
            type="string",
            required=True,
            description="The new text to insert (can be empty string for deletion)"
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        filepath = kwargs.get("filepath")
        old_text = kwargs.get("old_text")
        new_text = kwargs.get("new_text", "")

        if not filepath:
            return ToolResult(
                success=False,
                error="Missing required parameter: filepath"
            )
        if old_text is None:
            return ToolResult(
                success=False,
                error="Missing required parameter: old_text"
            )

        # Kill switch: block all edits when codebase is locked
        if _is_codebase_locked():
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
                metadata={"filepath": filepath}
            )

        # Guardian review (Uncle Claude) — only during self-improvement
        if kwargs.get("_self_improvement_context"):
            try:
                from backend.services.claude_advisor_service import get_claude_advisor
                advisor = get_claude_advisor()
                if advisor.is_available():
                    import os
                    review = advisor.review_change(
                        file_path=filepath,
                        current_content=open(filepath).read()[:3000] if os.path.exists(filepath) else "",
                        proposed_diff=f"- {old_text[:500]}\n+ {new_text[:500]}",
                        reasoning=kwargs.get("_reasoning", "Autonomous code change"),
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

        try:
            result = edit_code(filepath, old_text, new_text)

            # Check if result indicates an error
            if result.startswith("ERROR"):
                error_msg = result
                # Improve "exact match not found" errors with read_code suggestion
                if "Could not find the exact text" in result or "could not find" in result.lower():
                    error_msg = (
                        result.rstrip()
                        + "\n\nSUGGESTION: Use read_code(filepath) first to get the exact text including whitespace, then retry with that exact string."
                    )
                return ToolResult(
                    success=False,
                    error=error_msg,
                    metadata={
                        "filepath": filepath,
                        "old_text_preview": (old_text[:100] + "..." if len(old_text or "") > 100 else (old_text or "")),
                        "new_text_preview": (new_text[:100] + "..." if len(new_text or "") > 100 else (new_text or "")),
                    }
                )

            return ToolResult(
                success=True,
                output=result,
                metadata={
                    "filepath": filepath,
                    "operation": "deleted" if not new_text else "replaced"
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
            default=2,
            description="Maximum directory depth to show (default: 2)"
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        directory = kwargs.get("directory", "frontend/src")
        max_depth = kwargs.get("max_depth", 2)

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
        "Set should_exist=False to verify that text was successfully removed."
    )
    parameters = {
        "filepath": ToolParameter(
            name="filepath",
            type="string",
            required=True,
            description="Relative path from project root"
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


# Tool instances for registration
CODE_MANIPULATION_TOOLS = [
    ReadCodeTool(),
    SearchCodeTool(),
    EditCodeTool(),
    ListCodeFilesTool(),
    VerifyChangeTool(),
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
    'CODE_MANIPULATION_TOOLS',
    'register_code_manipulation_tools',
]
