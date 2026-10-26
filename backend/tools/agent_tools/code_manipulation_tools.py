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
    rooted_at,
    search_code,
    list_files,
    verify_change
)
from backend.models import db, Folder
from backend.utils.backend_http import is_mcp_transport
from backend.utils.display_paths import display_text
import json
import ast
from contextlib import contextmanager
from datetime import datetime
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


# The most file text one read_code call returns. MCP clients cap the size of a
# tool result: the Claude Code CLI defaults to 25,000 tokens. Measured with the
# cl100k_base tokenizer, this repository's twelve largest source files run 3.6
# to 5.3 characters per token, so 60,000 characters is at most about 17,000
# tokens. 43 of its 1,964 tracked source files are longer and come back in pages.
READ_CODE_PAGE_CHARS = 60_000
# What a read_code page in Guaardvark's own chat leaves for everything around
# the file text: the header (path, counts, SHA-256, mtime), the "Showing
# lines" line, the next-page footer and the chat's observation wrapper.
READ_CODE_CHAT_RESERVE = 1_500

_CONTENT_START = "========== FILE CONTENT START ==========\n"
_CONTENT_END = "\n========== FILE CONTENT END =========="


def _line_number(value, name: str) -> int | None:
    """start_line or end_line as an integer of 1 or more; None when not given."""
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a whole number, got {value!r}")
    if number < 1:
        raise ValueError(f"{name} must be 1 or more (lines are numbered from 1), got {number}")
    return number


def _page_of_file(result: str, filepath: str, start_line: int | None, end_line: int | None,
                  max_chars: int | None = None) -> tuple[str, dict]:
    """Cut read_code()'s whole-file result down to the lines asked for and to
    one page of text. Returns (result, facts about the page).

    Lines are split on "\n", the numbering search_code reports. A whole file
    that fits in a page, asked for without a range, is returned unchanged. A
    page ends on a line boundary; one line longer than a page is cut. Raises
    ValueError for a range outside the file."""
    max_chars = READ_CODE_PAGE_CHARS if max_chars is None else max_chars
    head, marker, rest = result.partition(_CONTENT_START)
    if not marker or not rest.endswith(_CONTENT_END):
        return result, {}
    content = rest[:-len(_CONTENT_END)]
    lines = content.split("\n")
    if content.endswith("\n"):
        lines.pop()
    total = len(lines) if content else 0

    if start_line is None and end_line is None and len(content) <= max_chars:
        return result, {"total_lines": total, "complete": True}

    first = start_line or 1
    if end_line is not None and end_line < first:
        raise ValueError(f"end_line ({end_line}) is before start_line ({first})")
    if first > max(total, 1):
        raise ValueError(f"start_line {first} is past the end of '{filepath}', which has {total} lines")
    if total == 0:
        return result, {"total_lines": 0, "complete": True}
    wanted_last = min(end_line or total, total)

    taken: list[str] = []
    used = 0
    cut_line = False
    for line in lines[first - 1:wanted_last]:
        cost = len(line) + 1
        if taken and used + cost > max_chars:
            break
        if not taken and len(line) > max_chars:
            line, cut_line = line[:max_chars], True
        taken.append(line)
        used += cost
        if cut_line:
            break
    last = first + len(taken) - 1 if taken else first - 1
    text = "\n".join(taken)
    whole_file = first == 1 and last == total and not cut_line
    if whole_file and content.endswith("\n"):
        text += "\n"

    showing = f"Showing lines {first}-{last} of {total} ({len(text):,} characters)."
    footer = ""
    if cut_line:
        showing += (
            f" Line {first} is {len(lines[first - 1]):,} characters long and is cut at {max_chars:,};"
            " read_code pages by line, so the rest of that line is not available through it."
        )
        if last < total:
            showing += f" The next line is start_line={last + 1}."
    elif last < wanted_last:
        more = f"start_line={last + 1}" + (f", end_line={end_line}" if end_line is not None else "")
        showing += (
            f" One call returns at most {max_chars:,} characters; continue with {more}."
        )
        footer = f"\n[Lines {first}-{last} of {total}. Next page: read_code(filepath='{filepath}', {more})]"
    elif last < total:
        showing += f" The file continues at start_line={last + 1}."
    if not whole_file:
        showing += " The header above describes the whole file."

    page = f"{head.rstrip()}\n{showing}\n\n{_CONTENT_START}{text}{_CONTENT_END}{footer}"
    return page, {
        "total_lines": total,
        "start_line": first,
        "end_line": last,
        "complete": whole_file,
        "next_start_line": last + 1 if last < total else None,
    }


IN_PROJECT_FOLDER = (
    " In a chat inside a project that has a folder, relative paths are relative to that "
    "folder instead of the checkout."
)


def _turn_project_root(tool):
    from backend.services.project_root import turn_project_root
    return turn_project_root(tool)


@contextmanager
def _in_turn_root(tool):
    root = _turn_project_root(tool)
    if root:
        with rooted_at(root):
            yield
    else:
        yield


class ReadCodeTool(BaseTool):
    """Tool to read source code files"""

    name = "read_code"
    read_only = True
    observation_chars = 8000  # the file text is the answer
    description = (
        "Read one UTF-8 text file (up to 10 MB) from this Guaardvark install's own checkout, whole or by line range. "
        "Returns a header (path, line and character counts, SHA-256 of the whole file's text, mtime) and the content "
        "between START/END markers, without line numbers. One call returns at most 60,000 characters of "
        "content, in whole lines: a longer file comes back as its first page with a 'Showing lines A-B of N' "
        "line naming the start_line of the next page. Pass start_line and end_line (1-based, inclusive, "
        "the line numbers search_code reports) to read part of a file; a range past the end of the file "
        "is an error. Paths are relative to the checkout root; an "
        "absolute path works when it lies inside the checkout. Refused: git-ignored local data (files "
        "under data/uploads and data/outputs stay readable), .env and credential or key files (*.pem, "
        "*.key, id_rsa, .netrc, credentials.* and similar), and anything under .git, venv, node_modules, "
        "dist, logs or __pycache__. In Guaardvark's own chat an absolute path outside the checkout also works, "
        "except system and key folders; over MCP it is refused. To find a file use search_code or "
        "search_codebase; for PDF or Office files process_file; for logs read_logs."
        + IN_PROJECT_FOLDER
    )
    parameters = {
        "filepath": ToolParameter(
            name="filepath",
            type="string",
            required=True,
            description="File to read, relative to the checkout root, e.g. 'backend/app.py' or 'frontend/src/App.jsx'. A relative path may not leave the checkout."
        ),
        "start_line": ToolParameter(
            name="start_line",
            type="int",
            required=False,
            minimum=1,
            description="First line to return, counting from 1. Default: the first line. Use the 'path:line' numbers from search_code, or the start_line a previous page named."
        ),
        "end_line": ToolParameter(
            name="end_line",
            type="int",
            required=False,
            minimum=1,
            description="Last line to return, inclusive. Default: as far as one call reaches (60,000 characters). A value past the end of the file reads to the end."
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
            try:
                start_line = _line_number(kwargs.get("start_line"), "start_line")
                end_line = _line_number(kwargs.get("end_line"), "end_line")
            except ValueError as e:
                return ToolResult(success=False, error=f"ERROR reading '{filepath}': {e}", metadata={"filepath": filepath})

            with _in_turn_root(self):
                result = read_code(filepath, allow_external=not is_mcp_transport(self))

            # Check if result indicates an error
            if result.startswith("ERROR"):
                return ToolResult(
                    success=False,
                    error=result,
                    metadata={"filepath": filepath}
                )

            # Guaardvark's own chat reads observation_chars of a result, so its
            # pages are cut to fit, with room for the header and the next-page
            # line; a page cut by the chat instead would lose that line.
            # MCP clients get the full page.
            page_chars = None if is_mcp_transport(self) else max(
                1000, self.observation_chars - READ_CODE_CHAT_RESERVE)
            try:
                result, page = _page_of_file(result, filepath, start_line, end_line, max_chars=page_chars)
            except ValueError as e:
                return ToolResult(success=False, error=f"ERROR reading '{filepath}': {e}", metadata={"filepath": filepath})

            return ToolResult(
                success=True,
                output=result,
                metadata={"filepath": filepath, **page}
            )
        except Exception as e:
            logger.error(f"ReadCodeTool failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=display_text(f"Failed to read file: {str(e)}"),
                metadata={"filepath": filepath}
            )


class SearchCodeTool(BaseTool):
    """Tool to search for patterns across the codebase"""

    name = "search_code"
    read_only = True
    observation_chars = 4000  # each hit is a path, a line number and the matching line
    description = (
        "Find lines matching a case-insensitive Python regular expression in this Guaardvark install's "
        "own source files: tracked files and untracked ones git does not ignore, skipping venv, "
        "node_modules, dist, build, logs and __pycache__ folders, binary files, symlinks, and .env or "
        "credential files. Returns the total, then up to 100 numbered 'path:line' hits with the line text "
        "(long lines cut at 300 characters). Matching is one line at a time. 'No matches found' is a "
        "normal result; an invalid pattern, or an absolute, ~ or '..' glob, is an error. A pattern that "
        "backtracks badly (nested repeats such as '(\\w+\\s*)+') is stopped with an error once one line "
        "takes over 1 s or the whole search over 20 s. Use it for exact names and patterns; "
        "search_codebase asks by meaning when the zvec_grep plugin is connected; read_code opens a hit "
        "that is UTF-8 text."
        + IN_PROJECT_FOLDER
    )
    parameters = {
        "pattern": ToolParameter(
            name="pattern",
            type="string",
            required=True,
            description="Python regular expression, matched case-insensitively against one line at a time, e.g. 'def\\s+load_config' or 'Button.*onClick'. Escape ( [ . and the like to match them literally."
        ),
        "file_glob": ToolParameter(
            name="file_glob",
            type="string",
            required=False,
            default="**/*.{py,jsx,js,tsx,ts}",
            description="Which files to search, relative to the checkout root, e.g. 'backend/**/*.py', 'frontend/**/*.{js,jsx}', '*.py' or 'backend/utils'. A glob without '/' matches file names in any folder ('*.py' is every Python file, as with ripgrep -g); a folder name with no wildcard searches every file under it. '**/' spans zero or more folders, '*' and '?' stay within one folder name, and {a,b} groups expand anywhere; [abc] classes are not supported. Default '**/*.{py,jsx,js,tsx,ts}'."
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
            with _in_turn_root(self):
                result = search_code(pattern, file_glob)
            if result.startswith("ERROR"):
                return ToolResult(success=False, error=result, metadata={"pattern": pattern})

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
    read_only = False
    is_dangerous = True
    requires_approval = True
    description = (
        "Edit a source code file by replacing exact text. Creates automatic backup. "
        "The old_text MUST be unique in the file or the edit will fail. "
        "Use read_code first to get the exact text to replace. "
        "Use empty string for new_text to delete code. "
        "Accepts paths relative to the project root and explicit absolute paths for user-referenced external files."
        + IN_PROJECT_FOLDER
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
        # Relative paths: the chat's project folder when it has one, so an edit
        # names the same file read_code read; else GUAARDVARK_ROOT, for Celery
        # workers and self-improvement.
        if not os.path.isabs(filepath):
            root = _turn_project_root(self) or os.environ.get("GUAARDVARK_ROOT", "")
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
        self_improvement = bool(ctx.get("_self_improvement_context"))

        # Guardian review (Uncle Claude) — only during self-improvement (skip if dry_run)
        if self_improvement and not dry_run:
            # Only approved=True counts as a review that passed. Anything else
            # (unavailable, over budget, unparseable, error) is recorded as
            # "not reviewed" on the staged fix; a person applies it either way.
            reviewed_by = None
            review_notes = None
            try:
                from backend.services.claude_advisor_service import (
                    get_claude_advisor, scheduled_sends_allowed,
                )
                if ctx.get("_trigger") in ("scheduled", "reactive") and not scheduled_sends_allowed():
                    # No person started a scheduled or reactive (heal) run, and the
                    # review sends this file to Anthropic: it waits for Uncle
                    # Claude's Scheduled sends.
                    review = {"approved": None,
                              "reason": "not sent: scheduled sends to Uncle Claude are off"}
                else:
                    advisor = get_claude_advisor()
                    review = advisor.review_change(
                        file_path=filepath,
                        current_content=open(filepath).read()[:3000] if os.path.exists(filepath) else "",
                        proposed_diff=f"- {old_text[:500]}\n+ {new_text[:500]}",
                        reasoning=ctx.get("_reasoning", "Autonomous code change"),
                    )
                approved = review.get("approved")
                if approved is False:
                    directive = review.get("directive", "reject")
                    if directive in ("halt_self_improvement", "lock_codebase", "halt_family"):
                        # A failure here must not turn the rejection into a staged fix.
                        try:
                            _handle_uncle_directive(directive, review.get("reason", ""))
                        except Exception as directive_error:
                            logger.error(f"Uncle Claude directive {directive} was not applied: "
                                         f"{directive_error}", exc_info=True)
                    return ToolResult(
                        success=False,
                        error=f"Uncle Claude rejected this change: {review.get('reason', 'No reason given')}. "
                              f"Suggestions: {', '.join(review.get('suggestions', []))}",
                        metadata={"guardian_review": review}
                    )
                if approved is True:
                    reviewed_by = "uncle_claude"
                    review_notes = review.get("reason") or None
                else:
                    review_notes = f"not reviewed: {review.get('reason') or 'no verdict returned'}"
            except Exception as e:
                logger.warning(f"Guardian review failed; staging as not reviewed: {e}")
                review_notes = f"not reviewed: {e}"

            # Self-improvement writes a file only once a person has set
            # self_improvement_apply_enabled=true. Until then, and whenever that
            # setting cannot be read, the change is staged for review instead.
            # A scheduled run always stages: nobody asked for it, so a person
            # reviews what it found.
            if _self_improvement_apply_blocked() or ctx.get("_trigger") == "scheduled":
                try:
                    pending_id = stage_pending_fix(
                        filepath,
                        old_text,
                        new_text,
                        ctx.get("_reasoning", "Autonomous fix"),
                        run_id=ctx.get("_run_id"),
                        reviewed_by=reviewed_by,
                        review_notes=review_notes,
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

        from backend.services import self_edit
        if not dry_run and not self_improvement and self_edit.is_own_code(filepath):
            return self._edit_own_code(filepath, old_text, new_text, ctx, expected_hash, expected_mtime)

        try:
            edit_result = apply_exact_replacement(
                filepath,
                old_text,
                new_text,
                dry_run=dry_run,
                # Self-improvement stays inside the checkout, as staging does.
                allow_external=not self_improvement,
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


    def _edit_own_code(self, filepath, old_text, new_text, ctx, expected_hash, expected_mtime) -> ToolResult:
        """An edit to Guaardvark's own code: a restore point first, then the
        exact write, then the checks; a failed check puts the file back.

        From a chat, such an edit runs only as a step of a plan the person
        approved. Otherwise the chat is put in plan mode and the model is
        told to plan the change, so the person sees it before it is made.
        """
        from backend.services import self_edit
        from backend.services.guarded_code_service import GuardedCodeError

        plan_step = ctx.get("plan_step") if isinstance(ctx, dict) else None
        if (ctx or {}).get("transport") == "chat" and not plan_step:
            from backend.services.plan_mode import switch_on
            switched = switch_on(ctx.get("chat_session_id"))
            return ToolResult(
                success=False,
                error=("Changes to Guaardvark's own code are planned first, so the person sees them before "
                       "they are made. " + ("Plan mode is now on for this chat: " if switched else "")
                       + "tell the person what you would change and ask them to approve the plan."),
                metadata={"blocked_by": "needs_plan", "filepath": filepath},
            )
        if plan_step:
            group = f"plan-{plan_step.get('plan_id')}-{plan_step.get('index')}"
        else:
            group = str((ctx or {}).get("request_id") or "") or f"edit-{os.path.basename(filepath)}"
        try:
            record = self_edit.apply_change(
                filepath, old_text, new_text, group=group,
                expected_hash=expected_hash, expected_mtime=expected_mtime,
            )
        except GuardedCodeError as e:
            error_msg = str(e)
            if e.code in {"TEXT_NOT_FOUND", "TEXT_NOT_UNIQUE"}:
                error_msg += ("\n\nSUGGESTION: Use read_code(filepath) first to get the exact text including "
                              "whitespace, then retry with enough surrounding context for one unique match.")
            return ToolResult(success=False, error=error_msg, metadata={"filepath": filepath, "blocked_by": e.code})
        rel = record["files"][0]["path"]
        checks = "; ".join(f"{c['check']}: {'ok' if c['ok'] else c['detail']}" for c in record["checks"])
        if record["status"] == "failed_checks":
            return ToolResult(
                success=False,
                error=f"The change to {rel} was made, checked and undone: {record.get('error')}. ({checks})",
                metadata={"self_edit": record, "filepath": filepath},
            )
        effect = ("It takes effect after a restart; the restart banner offers one."
                  if record.get("restart_needed") else "It is live now.")
        return ToolResult(
            success=True,
            output=(f"Changed {rel} (change {record['id']}). Checks: {checks}. {effect} "
                    "It can be reverted from the chat or the Self-improvement page."),
            metadata={"self_edit": record, "filepath": filepath, "relative_path": rel},
        )


class ListCodeFilesTool(BaseTool):
    """Tool to list project directory structure (code-exploration)."""

    name = "list_code_files"
    read_only = True
    observation_chars = 3000  # a path per line
    description = (
        "Show a folder of this Guaardvark install's own source as an indented tree: folders first, then "
        "files with sizes. It lists the files search_code searches plus binary files, so git-ignored data (uploads "
        "and outputs included), folders holding no source, venv, node_modules, dist, build, logs and "
        "__pycache__ folders, symlinks and .env or credential files are left out. max_depth 0 lists only "
        "the folder's direct entries and each step adds a level; output stops at 1,500 entries with a "
        "note of how many more there are. Use read_code to open a file, search_code or search_codebase "
        "to find one by content, and list_code_repositories for repositories indexed in the Library."
        + IN_PROJECT_FOLDER
    )
    parameters = {
        "directory": ToolParameter(
            name="directory",
            type="string",
            required=False,
            default="frontend/src",
            description="Folder relative to the checkout root, e.g. 'backend/api', or '.' for the whole checkout. Paths outside the checkout are refused. Default 'frontend/src'."
        ),
        "max_depth": ToolParameter(
            name="max_depth",
            type="int",
            required=False,
            default=5,
            minimum=0,
            description="Levels below directory to expand: 0 shows only its direct entries, 1 adds their contents, and so on. Default 5; use 1-2 on large folders such as '.'."
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
            with _in_turn_root(self):
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
                error=display_text(f"List files failed: {str(e)}"),
                metadata={"directory": directory}
            )


class VerifyChangeTool(BaseTool):
    """Tool to verify code changes were applied correctly"""

    name = "verify_change"
    read_only = True
    observation_chars = 2000
    description = (
        "Check whether a text file in this Guaardvark install's checkout contains an exact string: confirm "
        "an edit landed (should_exist=true) or that removed text is gone (should_exist=false). Plain case- "
        "and whitespace-sensitive substring (line endings read as \\n), not a regex. Reads only and needs "
        "no Guaardvark backend. "
        "Returns a line starting '✓ VERIFIED' or '✗ VERIFICATION FAILED'; 'ERROR during verification' "
        "(file missing, refused, not UTF-8, or over 10 MB) comes back as an error result. Same path rules "
        "as read_code. To see the file use read_code; to find text across files, search_code."
        + IN_PROJECT_FOLDER
    )
    parameters = {
        "filepath": ToolParameter(
            name="filepath",
            type="string",
            required=True,
            description="File to check, relative to the checkout root, e.g. 'backend/app.py'. Same rules as read_code."
        ),
        "expected_text": ToolParameter(
            name="expected_text",
            type="string",
            required=True,
            description="Exact text to look for: a case- and whitespace-sensitive substring, not a regex."
        ),
        "should_exist": ToolParameter(
            name="should_exist",
            type="bool",
            required=False,
            default=True,
            description="true (default): passes when the text is present. false: passes when it is absent from the file; a missing file is an error, not a pass."
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
            with _in_turn_root(self):
                result = verify_change(filepath, expected_text, should_exist, allow_external=not is_mcp_transport(self))

            # A completed check is a result, pass or fail; only an unreadable file is an error.
            if result.startswith("ERROR"):
                return ToolResult(success=False, error=result, metadata={"filepath": filepath})
            verification_passed = "✓ VERIFIED" in result

            return ToolResult(
                success=True,
                output=result,
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


def _analysis_stamp(metadata: dict) -> str:
    """When the stored analysis ran and how many files it covered, as one
    sentence for a result that reflects that analysis rather than the folder
    as it is now."""
    raw = str(metadata.get("analyzed_at") or "").strip()
    try:
        when = datetime.fromisoformat(raw).strftime("%Y-%m-%d %H:%M") + " (server local time)"
    except ValueError:
        when = raw
    count = metadata.get("file_count")
    files = f", {count} files" if isinstance(count, int) else ""
    if not when:
        return f"Analysis time not recorded{files}. Files added or changed since the analysis are not reflected."
    return f"Analysed {when}{files}. Files added or changed since then are not reflected."


PROJECT_FOLDER_MAP_NOTE = (
    " Leave folder_id empty for the folder the chat works in: a project's folder in a chat inside "
    "a project that has one, otherwise Guaardvark's own source. That folder is read from disk, "
    "with paths relative to it, and rebuilt when its source changes."
)


def _code_root(tool):
    """(root, own) for a code tool called without folder_id: the chat's project
    folder, or Guaardvark's own checkout (own=True)."""
    root = _turn_project_root(tool)
    if root:
        return root, False
    from backend.tools.llama_code_tools import PROJECT_ROOT
    return str(PROJECT_ROOT), True


def _project_code_map(tool):
    """(data, None) for the folder the turn works in (its project folder, or
    Guaardvark's own checkout), built from disk; (None, error) on failure."""
    root, own = _code_root(tool)
    import hashlib
    from backend.services.repository_analysis_service import project_code_map

    project_id = (getattr(tool, "_context", None) or {}).get("project_id")
    if own:
        key = "self"
    elif project_id:
        key = f"project-{int(project_id)}"
    else:
        key = "root-" + hashlib.sha1(root.encode()).hexdigest()[:12]
    try:
        data = project_code_map(key, root)
    except Exception as e:  # noqa: BLE001 - reported to the model, not raised
        logger.error(f"Mapping {root} failed: {e}", exc_info=True)
        return None, display_text(f"Could not map the folder: {e}")
    return {**data, "own_checkout": own}, None


def _folder_of_turn_project(tool, folder_id) -> bool:
    """True when folder_id is a Library folder of the chat's own project and the
    project has a folder on disk: the upload is a snapshot of that folder, so the
    folder itself answers."""
    if not folder_id or not _turn_project_root(tool):
        return False
    project_id = (getattr(tool, "_context", None) or {}).get("project_id")
    if not project_id:
        return False
    try:
        folder = db.session.get(Folder, int(folder_id))
    except (TypeError, ValueError):
        return False
    return bool(folder and folder.project_id and int(folder.project_id) == int(project_id))


def _fit_map(tool, repo_map: str) -> str:
    """The ranked map cut to what Guaardvark's own chat reads of a result, at a
    file boundary, saying how many lower-ranked files were left out. MCP
    clients get the whole map."""
    budget = tool.observation_chars - 400
    if is_mcp_transport(tool) or len(repo_map) <= budget:
        return repo_map
    sections = repo_map.split("\n## ")
    kept = sections[0]
    for section in sections[1:]:
        if len(kept) + len(section) + 4 > budget:
            break
        kept += "\n## " + section
    left = repo_map.count("\n## ") - kept.count("\n## ")
    return kept.rstrip() + f"\n\n... {left} more files of the ranked map not shown; read_code or get_dependency_graph(file=...) for any file."


def _project_map_stamp(data):
    where = ("Guaardvark's own source" if data.get("own_checkout")
             else f"the project folder {display_text(data['root'])}")
    return (f"Mapped from {where} at {data['analyzed_at']} "
            f"({data['file_count']} source files); paths are relative to it.")


class GetRepositoryMapTool(BaseTool):
    """Tool to get the PageRank-based repository map of a Code Repository folder."""

    name = "get_repository_map"
    read_only = True
    observation_chars = 6000  # the ranked map is the answer
    description = (
        "Return the stored architecture overview of an uploaded Code Repository folder: Markdown listing "
        "its files by PageRank, each with up to 10 top-ranked classes, functions and methods, cut off at "
        "about 4,096 tokens. Take folder_id from list_code_repositories, using an entry with "
        "has_metadata=true. Read-only; needs the Guaardvark backend running, and shows the folder as of "
        "its last analysis, not later edits: the first line says when that analysis ran (server local "
        "time) and how many files it covered. Fails with a message if the folder is not a Code Repository "
        "or not analysed yet, and says so when the analysis found no code symbols. For import edges use "
        "get_dependency_graph; for one Python symbol's source, read_ast_node."
        + PROJECT_FOLDER_MAP_NOTE
    )
    parameters = {
        "folder_id": ToolParameter(
            name="folder_id",
            type="int",
            required=False,
            description="Integer id of an analysed Code Repository folder (has_metadata=true in list_code_repositories). Subfolders of a marked folder are listed too but are usually not analysed on their own; use the top folder's id. Leave it empty for the chat's project folder, or for Guaardvark's own source when the chat has none."
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        folder_id = kwargs.get("folder_id")
        if not folder_id or _folder_of_turn_project(self, folder_id):
            data, err = _project_code_map(self)
            if err:
                return ToolResult(success=False, error=err)
            if not data["repository_map"]:
                return ToolResult(success=True, output="No classes or functions to map. " + _project_map_stamp(data),
                                  metadata={"root": data["root"]})
            return ToolResult(success=True, output=f"{_project_map_stamp(data)}\n\n{_fit_map(self, data['repository_map'])}",
                              metadata={"root": data["root"], "analyzed_at": data["analyzed_at"]})

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
                if "repository_map" in folder["metadata"]:
                    return ToolResult(
                        success=True,
                        output=(
                            "The analysis found no classes or functions to map in this folder. "
                            + _analysis_stamp(folder["metadata"])
                        ),
                        metadata={"folder_id": folder_id},
                    )
                return ToolResult(success=False, error="No repository map found in the metadata. It may still be generating.")

            return ToolResult(
                success=True,
                output=f"{_analysis_stamp(folder['metadata'])}\n\n{repo_map}",
                metadata={"folder_id": folder_id, "analyzed_at": folder["metadata"].get("analyzed_at")}
            )
        except Exception as e:
            logger.error(f"GetRepositoryMapTool failed: {e}", exc_info=True)
            return ToolResult(success=False, error=display_text(f"Failed to get repository map: {str(e)}"))


def _graph_result(head: dict, graph: dict, file, metadata: dict) -> ToolResult:
    """The whole graph, or with ``file`` that file's imports and importers. A file
    is matched by its listed path, or by the end of it ('firewall.py')."""
    if not file:
        return ToolResult(success=True, output=json.dumps({**head, "graph": graph}, indent=2), metadata=metadata)
    wanted = str(file).strip().lstrip("./")
    known = set(graph) | {t for targets in graph.values() for t in targets}
    matches = [p for p in known if p == wanted] or [p for p in known if p.endswith("/" + wanted)]
    if not matches:
        return ToolResult(success=True, output=json.dumps({
            **head, "file": wanted, "imports": [], "imported_by": [],
            "note": "This file neither imports nor is imported by any file in the folder."}, indent=2),
            metadata=metadata)
    target = sorted(matches)[0]
    return ToolResult(success=True, output=json.dumps({
        **head,
        "file": target,
        "imports": graph.get(target, []),
        "imported_by": sorted(src for src, targets in graph.items() if target in targets),
    }, indent=2), metadata=metadata)


class GetDependencyGraphTool(BaseTool):
    """Tool to get the import dependency graph of a Code Repository folder."""

    name = "get_dependency_graph"
    read_only = True
    observation_chars = 4000
    description = (
        "Return the file-level import graph of an uploaded Code Repository folder as JSON "
        "{analyzed_at, file_count, graph}, where graph is {file: [in-repository files it imports]} with "
        "paths starting with the folder's own path. Only "
        "Python and JavaScript/TypeScript imports are parsed; third-party imports and files that import "
        "nothing in the repository are left out, and a repository with no internal imports has an empty graph. "
        "Take folder_id from list_code_repositories. Read-only; needs the Guaardvark backend running, "
        "reflects the last analysis (analyzed_at is when it ran, in server local time; later edits are "
        "not in the graph), and fails with a message if the folder is not analysed. For a "
        "ranked symbol overview use get_repository_map; for import cycles in Guaardvark's own checkout, "
        "map_codebase. With file, returns {file, imports, imported_by} for that one file."
        + PROJECT_FOLDER_MAP_NOTE
    )
    parameters = {
        "folder_id": ToolParameter(
            name="folder_id",
            type="int",
            required=False,
            description="Integer id of an analysed Code Repository folder (has_metadata=true in list_code_repositories). Subfolders of a marked folder are listed too but are usually not analysed on their own; use the top folder's id. Leave it empty for the chat's project folder, or for Guaardvark's own source when the chat has none."
        ),
        "file": ToolParameter(
            name="file",
            type="string",
            required=False,
            description="Optional path of one file, as the graph lists it (e.g. 'service/firewall.py'). Returns only that file's imports and the files that import it, instead of the whole graph. Use it for 'what imports X' and 'what does X import'."
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        folder_id = kwargs.get("folder_id")
        if not folder_id or _folder_of_turn_project(self, folder_id):
            data, err = _project_code_map(self)
            if err:
                return ToolResult(success=False, error=err)
            head = {"root": display_text(data["root"]), "analyzed_at": data["analyzed_at"],
                    "file_count": data["file_count"]}
            return _graph_result(head, data["dependency_graph"], kwargs.get("file"),
                                 {"root": data["root"], "node_count": len(data["dependency_graph"])})

        try:
            folder, err = _repository_folder(self, folder_id)
            if err:
                return ToolResult(success=False, error=err)

            if not folder["is_repository"]:
                return ToolResult(success=False, error=f"Folder {folder_id} is not marked as a Code Repository.")

            if not folder["metadata"]:
                return ToolResult(success=False, error=f"Folder {folder_id} has no repository metadata generated yet.")

            dep_graph = folder["metadata"].get("dependency_graph")

            # An empty graph is an answer: nothing in the repository imports anything else in it.
            if dep_graph is None:
                return ToolResult(success=False, error="No dependency graph found in the metadata.")

            head = {"analyzed_at": folder["metadata"].get("analyzed_at"),
                    "file_count": folder["metadata"].get("file_count")}
            return _graph_result(head, dep_graph, kwargs.get("file"),
                                 {"folder_id": folder_id, "node_count": len(dep_graph)})
        except Exception as e:
            logger.error(f"GetDependencyGraphTool failed: {e}", exc_info=True)
            return ToolResult(success=False, error=display_text(f"Failed to get dependency graph: {str(e)}"))


class ReadASTNodeTool(BaseTool):
    """Tool to precisely extract a class or function from a Python file using AST."""

    name = "read_ast_node"
    read_only = True
    observation_chars = 6000  # one function or class, whole
    description = (
        "Return the source of one Python class or function, decorators included, from a file in an "
        "uploaded Code Repository folder, instead of the whole file. Matches the bare name at any depth "
        "(methods, nested functions); each definition comes back headed '# Match N: <type> lines A-B'. "
        "Reads the file as it is on disk now; needs the Guaardvark backend running to locate the folder. "
        "Fails with a message for non-.py or absolute paths, paths outside the folder, a missing file, "
        "syntax errors or a name not found. Find names with get_repository_map; for the whole file, "
        "read_code with 'data/uploads/' plus the path get_repository_map shows."
        " Leave folder_id empty to read from the chat's project folder, or from Guaardvark's own source "
        "when the chat has none, with the path relative to that folder."
    )
    parameters = {
        "folder_id": ToolParameter(
            name="folder_id",
            type="int",
            required=False,
            description="Integer id of the Code Repository folder, from list_code_repositories. It does not need to be analysed. Leave it empty for the chat's project folder, or for Guaardvark's own source when the chat has none."
        ),
        "filepath": ToolParameter(
            name="filepath",
            type="string",
            required=True,
            description="Path of a .py file relative to the folder's root, e.g. 'app/main.py'. Paths shown by get_repository_map and get_dependency_graph begin with the folder's own path (e.g. 'Repo/app/main.py'); drop that prefix."
        ),
        "node_name": ToolParameter(
            name="node_name",
            type="string",
            required=True,
            description="Exact, case-sensitive name of a class or function, e.g. 'Worker' or 'build_graph'. Use the bare name: 'Worker.run' does not match; 'run' does, along with any other definition named run."
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        folder_id = kwargs.get("folder_id")
        filepath = kwargs.get("filepath")
        node_name = kwargs.get("node_name")

        if not filepath or not node_name:
            return ToolResult(success=False, error="Missing required parameter: filepath or node_name")
        turn_root = _code_root(self)[0] if (not folder_id or _folder_of_turn_project(self, folder_id)) else None

        if not filepath.endswith(".py"):
            return ToolResult(success=False, error="read_ast_node currently only supports Python (.py) files.")

        try:
            if Path(filepath).is_absolute():
                return ToolResult(success=False, error="filepath must be relative to the Code Repository folder.")

            if turn_root:
                from backend.services.guarded_code_service import forbidden_path_reason
                from backend.tools.llama_code_tools import _private_reason
                repo_root = Path(turn_root).resolve()
                reason = forbidden_path_reason(filepath) or _private_reason(filepath, repo_root)
                if reason:
                    return ToolResult(success=False, error=reason)
            else:
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

            try:
                source = full_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return ToolResult(success=False, error=f"{filepath} is not UTF-8 text.")

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
                # Every match carries its line range: MCP clients see only this text.
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
            return ToolResult(success=False, error=display_text(f"Failed to read AST node: {str(e)}"))


# Guaardvark's own source is always listed, although it is not a Library folder:
# it has no folder id and cannot be marked as a Code Repository. The folder tools
# reach it by leaving folder_id empty. Its path is "." because read_code,
# search_code and list_code_files take paths relative to the checkout root;
# where the checkout sits on disk is not something a client needs.
LIVE_CHECKOUT_ENTRY = {
    "id": "live",
    "name": "Guaardvark's own source (live checkout)",
    "path": ".",
    "has_metadata": False,
    "description": (
        "The source of this Guaardvark install, not a Library folder: 'live' is not a folder_id. "
        "Call get_repository_map, get_dependency_graph and read_ast_node with folder_id left empty "
        "(outside a project that has its own folder) to map it, follow its imports or read one "
        "function. Explore it with search_code, search_codebase, list_code_files and read_code, "
        "using paths relative to the checkout root, or map_codebase for findings."
    ),
}


class ListCodeRepositoriesTool(BaseTool):
    """Tool to list all folders marked as Code Repositories (for discovery in NL flows)."""

    name = "list_code_repositories"
    read_only = True
    # The built-in 'live' entry alone is about 450 characters, and the count
    # comes after the list: at the 500 default neither reached the model.
    observation_chars = 4000
    description = (
        "List the folders marked as Code Repositories in Guaardvark, as a JSON array of {id, name, path, "
        "has_metadata, description}. Call it first to get the integer folder_id that get_repository_map, "
        "get_dependency_graph and read_ast_node take. Marking a folder on the Documents page also marks "
        "each subfolder, listed separately; only entries with has_metadata=true have been analysed (the "
        "map and graph are built at the end of that analysis), so use the top folder's id for those. The last entry, id 'live', is Guaardvark's own source root "
        "(path '.', the root that read_code and search_code paths are relative to), "
        "not a folder id: explore it with search_code, read_code or map_codebase. Read-only; needs the "
        "Guaardvark backend running. Folders are marked on the Documents page or by bulk indexing."
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

            result.append(dict(LIVE_CHECKOUT_ENTRY))

            return ToolResult(
                success=True,
                output=result,
                metadata={"count": len(result)}
            )
        except Exception as e:
            logger.error(f"ListCodeRepositoriesTool failed: {e}", exc_info=True)
            return ToolResult(success=False, error=display_text(f"Failed to list code repos: {str(e)}"))


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
