#!/usr/bin/env python3
"""
LlamaIndex Code Manipulation Tools for LLM Self-Improvement
Provides FunctionTools compatible with ReActAgent for code reading, searching, and editing.

These tools enable an LLM agent to:
1. Read source code files
2. Search for patterns across the codebase
3. Edit code files with automatic backups
4. List project structure
5. Run tests to verify changes

Milestone Goal: Enable LLM to remove "Snibbly Nips" button from SettingsPage.jsx
"""

import hashlib
import os
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
import logging
import subprocess
import time
from pathlib import Path
from typing import List, Optional
import re

# The third-party engine runs search_code's caller-supplied patterns: unlike re
# it takes a per-call timeout and can release the GIL while it matches.
import regex

from backend.services.guarded_code_service import (
    GuardedCodeError,
    apply_exact_replacement,
    FALLBACK_PRIVATE_PREFIXES,
    forbidden_path_reason,
    private_path_reason,
    private_relative_paths,
    read_repo_file,
    resolve_repo_path,
)
from backend.utils.file_kinds import BUILD_DIR_NAMES, in_build_dir
from backend.utils.path_safety import is_sensitive

logger = logging.getLogger(__name__)

# Guaardvark's own checkout, 2 levels up from backend/tools. Relative paths are
# read from here unless the caller roots the call at a project's folder.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_turn_root: ContextVar[Optional[Path]] = ContextVar("code_tools_turn_root", default=None)


def _root() -> Path:
    """The folder relative paths resolve against for this call."""
    return _turn_root.get() or PROJECT_ROOT


@contextmanager
def rooted_at(folder):
    """Resolve relative paths against ``folder`` (a project's folder) inside the block."""
    token = _turn_root.set(Path(folder).expanduser().resolve())
    try:
        yield
    finally:
        _turn_root.reset(token)


@lru_cache(maxsize=64)
def _is_own_git_checkout(root: Path) -> bool:
    """True when ``root`` is the top of a git work tree. A folder inside another
    checkout (a Library folder under Guaardvark's ignored data) is not: git would
    answer for the outer checkout and call every file in it ignored."""
    try:
        proc = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and Path(proc.stdout.strip()).resolve() == root.resolve()


def _private_reason(relative_path: str, root: Path) -> Optional[str]:
    """Why a root-relative path is not source: git-ignored in a checkout, or a
    hidden, dependency or build folder in any other folder."""
    if _is_own_git_checkout(root):
        return private_path_reason(relative_path, root)
    parts = relative_path.replace("\\", "/").strip("/").split("/")
    if any(p.startswith(".") for p in parts) or in_build_dir(relative_path + "/x"):
        return f"'{relative_path}' is a hidden, dependency or build folder, not source code"
    return None


def _read_source_file(filepath: str, allow_external: bool = True) -> dict:
    """read_repo_file plus the code tools' own refusals: credential and key files
    anywhere, and git-ignored local data inside the checkout. Every refusal is
    decided from the path before the file is touched, so a refusal never tells the
    caller whether the file exists."""
    root = _root()
    resolved, relative_path = resolve_repo_path(filepath, root, allow_external=allow_external)
    if is_sensitive(str(resolved)):
        raise GuardedCodeError("Credential and key files are not read by the code tools", "SENSITIVE_PATH", 403)
    if not Path(relative_path).is_absolute():
        reason = _private_reason(relative_path, root)
        if reason:
            raise GuardedCodeError(reason, "PRIVATE_PATH", 403)
    return read_repo_file(filepath, repo_root=root, allow_external=allow_external)


def read_code(filepath: str, allow_external: bool = True) -> str:
    """
    Read the complete contents of a source code file.

    Args:
        filepath: Relative path from project root (e.g., "frontend/src/pages/SettingsPage.jsx")
        allow_external: Also accept an absolute path outside the checkout. The chat
            passes True (the user named the file); the MCP transport passes False.

    Returns:
        The file contents with metadata, or error message

    Example:
        content = read_code("frontend/src/pages/SettingsPage.jsx")
    """
    try:
        try:
            file_data = _read_source_file(filepath, allow_external)
        except GuardedCodeError as e:
            # Maybe the user just uploaded this to chat — those land under
            # data/uploads/, not PROJECT_ROOT, and have a Document row.
            # Only a bare file name is looked up this way, and only a file inside the
            # uploads is read, so a missing path never falls through to another
            # document that happens to share its name.
            if e.code == "FILE_NOT_FOUND" and "/" not in filepath.replace("\\", "/"):
                from backend import config
                from backend.utils.path_safety import is_within
                from backend.utils.uploaded_file_resolver import find_uploaded_file
                uploaded = find_uploaded_file(filepath)
                upload_dir = getattr(config, "UPLOAD_DIR", "")
                if uploaded and uploaded[1] and not (upload_dir and is_within(uploaded[1], [upload_dir])):
                    uploaded = None
                if uploaded:
                    content, on_disk = uploaded
                    if content is None and on_disk:
                        if is_sensitive(on_disk):
                            return f"ERROR reading '{filepath}': Credential and key files are not read by the code tools"
                        with open(on_disk, 'r', encoding='utf-8') as f:
                            content = f.read()
                    if content is not None:
                        line_count = len(content.splitlines())
                        char_count = len(content)
                        logger.info(f"Read {line_count} lines from uploaded file {filepath}")
                        return f"""✓ Successfully read uploaded file: {filepath}
Lines: {line_count} | Characters: {char_count}

========== FILE CONTENT START ==========
{content}
========== FILE CONTENT END =========="""
            return f"ERROR reading '{filepath}': {e}"

        content = file_data["content"]
        line_count = len(content.splitlines())
        char_count = len(content)
        display_path = file_data.get("relative_path") or filepath

        # Hash and mtime let a later edit detect that the file changed since this read
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        try:
            mtime = file_data.get("on_disk_path") or filepath
            # resolve the actual path used
            from backend.services.guarded_code_service import resolve_repo_path
            actual_path, _ = resolve_repo_path(filepath, _root(), allow_external=True)
            file_mtime = actual_path.stat().st_mtime if actual_path.exists() else None
        except Exception:
            file_mtime = None

        result = f"""✓ Successfully read: {display_path}
Lines: {line_count} | Characters: {char_count}
SHA-256: {content_hash}
Mtime: {file_mtime}

========== FILE CONTENT START ==========
{content}
========== FILE CONTENT END =========="""

        logger.info(f"Read {line_count} lines from {display_path}")
        return result

    except Exception as e:
        error_msg = f"ERROR reading '{filepath}': {str(e)}"
        logger.error(error_msg)
        return error_msg


# Folders never searched or listed, whether or not git knows about them.
SKIP_DIR_NAMES = {
    'venv', 'node_modules', '.git', '__pycache__', 'dist', '.pytest_cache',
    'htmlcov', '.coverage', 'build', 'logs',
}
MAX_SEARCH_HITS = 100
MAX_MATCH_LINE_CHARS = 300
MAX_LISTED_ENTRIES = 1500
DEFAULT_SEARCH_GLOB = "**/*.{py,jsx,js,tsx,ts}"

# Budgets for search_code's caller-supplied pattern. A pattern with nested
# repeats such as '(\w+\s*)+' backtracks exponentially on lines it does not
# match, and stdlib re offers no way to stop it. Ordinary patterns take about a
# microsecond per line: the default glob over this checkout (~1,900 files,
# ~550,000 lines) scans in about half a second. So one line past
# LINE_MATCH_TIMEOUT_S, or a scan past SEARCH_DEADLINE_S, means the pattern is
# the problem. The deadline sits well under the MCP server's 120 s call
# timeout so the caller gets this reason rather than a generic timeout.
LINE_MATCH_TIMEOUT_S = 1.0
SEARCH_DEADLINE_S = 20.0


def _source_files(subdir: str = "") -> List[str]:
    """Repo-relative paths of the checkout's source under subdir: tracked files plus
    untracked ones git does not ignore. Falls back to a pruned walk when this is not
    a git checkout."""
    subdir = subdir.strip("/")
    root = _root()
    proc = None
    if _is_own_git_checkout(root):
        try:
            proc = subprocess.run(
                ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z",
                 "--", subdir or "."],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            proc = None
    if proc is not None and proc.returncode == 0:
        files = [p for p in proc.stdout.split("\0") if p]
        # --cached also lists tracked files deleted from the working tree; a
        # trailing "/" marks a nested repository; a symlink can point at ignored
        # or outside data. None of those is source here, and neither are .env or
        # credential files git happens not to ignore.
        return [
            p for p in files
            if not p.endswith("/")
            and not SKIP_DIR_NAMES.intersection(p.split("/")[:-1])
            and not _refused_source_name(p)
            and not (root / p).is_symlink()
            and (root / p).exists()
        ]

    files = []
    own_checkout = root == PROJECT_ROOT
    for dirpath, dirnames, filenames in os.walk(root / subdir):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIR_NAMES and d not in BUILD_DIR_NAMES and not d.startswith(".")
            and not (own_checkout and (rel_dir + d + "/").startswith(FALLBACK_PRIVATE_PREFIXES))
            and not os.path.islink(os.path.join(dirpath, d))
        ]
        files.extend(
            rel_dir + name for name in filenames
            if not _refused_source_name(rel_dir + name) and not os.path.islink(os.path.join(dirpath, name))
        )
    return files


def _refused_source_name(rel: str) -> bool:
    """True for paths the code tools never open: .env and credential or key
    files, and anything read_code's folder rules refuse."""
    return is_sensitive(rel) or forbidden_path_reason(rel) is not None


def _literal_prefix(glob: str) -> str:
    """The leading folders of a glob that contain no wildcard, e.g. 'backend/api'
    for 'backend/api/**/*.py'."""
    parts = glob.split("/")[:-1]
    literal = []
    for part in parts:
        if any(c in part for c in "*?[{"):
            break
        literal.append(part)
    return "/".join(literal)


def _expand_braces(pattern: str) -> List[str]:
    """Expand every {a,b} group in a glob, e.g. 'src/**/*.{js,jsx}'."""
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    head, tail = pattern[:match.start()], pattern[match.end():]
    out = []
    for option in match.group(1).split(","):
        out.extend(_expand_braces(head + option + tail))
    return out


def _glob_regex(pattern: str) -> "re.Pattern":
    """A glob as a regex over repo-relative POSIX paths. '**/' spans any number of
    folders (including none), '*' and '?' stay inside one path segment."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z")


def _effective_glob(glob: str) -> str:
    """The glob a file_glob stands for. A wildcard-free name of a folder in the
    checkout means every file under it ('backend/utils' -> 'backend/utils/**')."""
    folder = glob.rstrip("/")
    if folder and not any(c in glob for c in "*?[{") and (_root() / folder).is_dir():
        return folder + "/**"
    return glob


def _anchored_globs(glob: str) -> List[str]:
    """The expanded globs to match against repo-relative paths. One with no '/'
    names files in any folder, as ripgrep's -g and gitignore patterns do:
    '*.py' matches backend/app.py as well as setup.py."""
    return [g if "/" in g else "**/" + g for g in _expand_braces(glob)]


def _too_expensive(pattern: str, why: str) -> str:
    return (
        f"ERROR: the pattern '{pattern}' is too expensive to run: {why}. Patterns with nested "
        "repeats such as '(\\w+\\s*)+' backtrack exponentially on lines they do not match. "
        "Write it without the nested repeat (for example '\\w+\\s*\\(' instead of "
        "'(\\w+\\s*)+\\('), or narrow file_glob."
    )


def search_code(pattern: str, file_glob: str = DEFAULT_SEARCH_GLOB, max_hits: int = MAX_SEARCH_HITS) -> str:
    """
    Search the checkout's source files for a case-insensitive regex, line by line.

    Args:
        pattern: Python regular expression (e.g., "Snibbly Nips", "Button.*onClick")
        file_glob: Glob relative to the checkout root; {a,b} groups are expanded, a
            glob with no '/' matches file names in any folder, and a folder name
            searches every file under it
        max_hits: How many hits to list; the total is always reported

    Returns:
        Up to max_hits "path:line" hits with the line text, or an ERROR string.
        A pattern that runs past LINE_MATCH_TIMEOUT_S on one line or
        SEARCH_DEADLINE_S in all is an ERROR, not a partial result.

    Example:
        results = search_code("Snibbly Nips")
        results = search_code("Button", "frontend/**/*.jsx")
    """
    try:
        # re decides what is valid, so the accepted syntax and its error
        # messages stay Python's; the regex engine, compiled as VERSION0 (its
        # re-compatible mode, simple case folding), does the matching.
        re.compile(pattern, re.IGNORECASE)
        line_re = regex.compile(pattern, regex.IGNORECASE | regex.VERSION0)
    except (re.error, regex.error) as e:
        return f"ERROR: '{pattern}' is not a valid regular expression: {e}"
    glob = (file_glob or DEFAULT_SEARCH_GLOB).strip()
    while glob.startswith("./"):
        glob = glob[2:]
    if glob.startswith(("/", "~")) or ".." in glob.split("/"):
        return f"ERROR: file_glob '{file_glob}' must be relative to the checkout root, without '..'"
    max_hits = max(1, int(max_hits))

    try:
        effective = _effective_glob(glob)
        matchers = [_glob_regex(g) for g in _anchored_globs(effective)]
        files = [p for p in _source_files(_literal_prefix(effective)) if any(m.match(p) for m in matchers)]

        deadline = time.monotonic() + SEARCH_DEADLINE_S
        matches = []
        for scanned, rel in enumerate(files):
            path = _root() / rel
            try:
                with open(path, 'rb') as f:
                    if b"\0" in f.read(8192):
                        continue  # binary file
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line_num, line in enumerate(f, start=1):
                        timeout = min(LINE_MATCH_TIMEOUT_S, deadline - time.monotonic())
                        try:
                            if timeout <= 0:
                                raise TimeoutError
                            # concurrent=True releases the GIL while matching, so the
                            # MCP server's event loop and its call timeout keep running.
                            hit = line_re.search(line, timeout=timeout, concurrent=True)
                        except TimeoutError:
                            if timeout < LINE_MATCH_TIMEOUT_S:
                                why = (f"the search had not finished after {SEARCH_DEADLINE_S:g} s "
                                       f"(stopped at {rel}:{line_num}, file {scanned + 1} of {len(files)})")
                            else:
                                why = (f"matching one line ({rel}:{line_num}) took longer than "
                                       f"{LINE_MATCH_TIMEOUT_S:g} s")
                            return _too_expensive(pattern, why)
                        if hit:
                            text = line.rstrip()
                            if len(text) > MAX_MATCH_LINE_CHARS:
                                text = text[:MAX_MATCH_LINE_CHARS] + " …"
                            matches.append((rel, line_num, text))
            except OSError as e:
                logger.debug(f"Skipping {rel}: {e}")

        if not matches:
            if effective != glob:
                note = f" (every file under {effective[:-3]})"
            elif any("/" not in g for g in _expand_braces(effective)):
                note = " (file names in any folder)"
            else:
                note = ""
            return f"No matches found for pattern '{pattern}' in {file_glob}{note}"

        result = f"✓ Found {len(matches)} matches for '{pattern}':\n\n"
        for i, (rel, line_num, text) in enumerate(matches[:max_hits], 1):
            result += f"{i}. {rel}:{line_num}\n   {text}\n\n"
        if len(matches) > max_hits:
            result += f"... and {len(matches) - max_hits} more matches (showing first {max_hits})\n"

        logger.info(f"Search for '{pattern}' found {len(matches)} matches")
        return result

    except Exception as e:
        error_msg = f"ERROR searching for '{pattern}': {str(e)}"
        logger.error(error_msg)
        return error_msg


def edit_code(filepath: str, old_text: str, new_text: str, expected_hash: str = None, expected_mtime: float = None) -> str:
    """
    Edit a source code file by replacing exact text. CRITICAL: Creates automatic backup.

    Args:
        filepath: Relative path from project root
        old_text: The EXACT text to replace (must be unique in file)
        new_text: The new text to insert (can be empty string for deletion)
        expected_hash: Optional sha256 of content at read time (enables drift guard)
        expected_mtime: Optional mtime at read time (helps drift guard)

    Returns:
        Success message with backup info, or error message

    Security:
        - Only edits files within project root
        - Creates .backup file before any changes
        - Verifies exact match before editing
        - Rolls back on verification failure
        - Drift guard prevents editing stale reads (important for agents/swarm)

    Example:
        # To remove the Snibbly Nips button:
        result = edit_code(
            "frontend/src/pages/SettingsPage.jsx",
            "      <Button variant=\"contained\" color=\"primary\">\n        Snibbly Nips\n      </Button>",
            ""
        )
    """
    try:
        edit_result = apply_exact_replacement(
            filepath,
            old_text,
            new_text,
            repo_root=_root(),
            allow_external=True,
            expected_hash=expected_hash,
            expected_mtime=expected_mtime,
        )
        old_lines = len(old_text.split('\n'))
        new_lines = len(new_text.split('\n'))
        lines_diff = new_lines - old_lines

        result = f"""✓ Successfully edited '{edit_result.relative_path}'

Backup: {edit_result.backup_path}
Changes: {"Removed" if lines_diff < 0 else "Added" if lines_diff > 0 else "Modified"} {abs(lines_diff)} lines
Old text length: {len(old_text)} chars
New text length: {len(new_text)} chars

The file has been updated. You can verify by reading it with read_code().
"""

        logger.info(f"Successfully edited {edit_result.relative_path}: {lines_diff:+d} lines")
        return result
    except GuardedCodeError as e:
        error_msg = f"ERROR editing '{filepath}': {e}"
        if e.code in {"TEXT_NOT_FOUND", "TEXT_NOT_UNIQUE"}:
            error_msg += (
                "\n\nTIPS:\n"
                "1. Use read_code() first to get the exact text including whitespace\n"
                "2. Include enough surrounding context to make the match unique\n"
                f"\nSEARCHED FOR:\n{old_text[:200]}..."
            )
        logger.error(error_msg)
        return error_msg

    except Exception as e:
        error_msg = f"ERROR editing '{filepath}': {str(e)}"
        logger.error(error_msg, exc_info=True)
        return error_msg


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size:,}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


def list_files(directory: str = "frontend/src/pages", max_depth: int = 5) -> str:
    """
    List the checkout's source files under a folder as a tree.

    Args:
        directory: Folder relative to the checkout root; "." for the whole checkout
        max_depth: 0 lists only the folder's direct entries; each step adds a level

    Returns:
        Formatted directory tree (at most MAX_LISTED_ENTRIES entries), or an ERROR string

    Example:
        structure = list_files("frontend/src/pages")
        structure = list_files("backend/api", max_depth=1)
    """
    try:
        root = _root()
        full_path = (root / (directory or ".")).resolve()
        if not full_path.is_relative_to(root):
            return f"ERROR: Path '{directory}' is outside project root"

        # Decided from the path alone, before the folder is looked at: the same
        # answer for a private folder that exists and one that does not.
        rel_dir = full_path.relative_to(root).as_posix()
        prefix = "" if rel_dir == "." else rel_dir + "/"
        reason = _private_reason(prefix.rstrip("/"), root) if prefix else None
        if reason:
            return f"ERROR: {reason}"

        if not full_path.exists():
            return f"ERROR: Path '{directory}' does not exist"
        if not full_path.is_dir():
            return f"ERROR: '{directory}' is a file, not a folder; open it with read_code"

        # Build the tree from the source inventory, so ignored data never shows.
        tree: dict = {}
        for rel in _source_files(prefix):
            if not rel.startswith(prefix):
                continue
            parts = rel[len(prefix):].split("/")
            node = tree
            for part in parts[:-1]:
                node = node.setdefault(part + "/", {})
            node[parts[-1]] = None

        lines: List[str] = []
        omitted = 0

        def render(node: dict, base: Path, indent: str, depth: int) -> None:
            nonlocal omitted
            entries = sorted(node.items(), key=lambda kv: (not kv[0].endswith("/"), kv[0].lower()))
            for i, (name, child) in enumerate(entries):
                if len(lines) >= MAX_LISTED_ENTRIES:
                    omitted += 1
                    if child is not None and depth < max_depth:
                        render(child, base / name.rstrip("/"), indent, depth + 1)
                    continue
                last = i == len(entries) - 1
                branch = "└── " if last else "├── "
                if child is not None:
                    lines.append(f"{indent}{branch}{name}")
                    if depth < max_depth:
                        render(child, base / name.rstrip("/"), indent + ("    " if last else "│   "), depth + 1)
                else:
                    try:
                        size = _format_size((base / name).lstat().st_size)
                    except OSError:
                        size = "?"
                    lines.append(f"{indent}{branch}{name} ({size})")

        render(tree, full_path, "", 0)
        result = f"✓ Directory structure: {directory}\n\n" + "\n".join(lines) + ("\n" if lines else "(no source files)\n")
        if omitted:
            result += f"\n... {omitted} more entries not shown (limit {MAX_LISTED_ENTRIES}); lower max_depth or list a subfolder\n"
        logger.info(f"Listed structure for '{directory}' (depth: {max_depth})")
        return result

    except Exception as e:
        error_msg = f"ERROR listing '{directory}': {str(e)}"
        logger.error(error_msg)
        return error_msg


def verify_change(filepath: str, expected_text: str, should_exist: bool = True, allow_external: bool = True) -> str:
    """
    Verify that a code change was successful by checking if text exists in file.

    Args:
        filepath: Relative path from project root
        expected_text: Text that should (or shouldn't) exist after the edit
        should_exist: True if text should exist, False if it should be gone
        allow_external: As in read_code.

    Returns:
        Verification result message

    Example:
        # After removing Snibbly Nips, verify it's gone:
        result = verify_change("frontend/src/pages/SettingsPage.jsx", "Snibbly Nips", should_exist=False)
    """
    try:
        file_data = _read_source_file(filepath, allow_external)
        content = file_data["content"]
        display_path = file_data.get("relative_path") or filepath

        text_found = expected_text in content

        if should_exist:
            if text_found:
                return f"✓ VERIFIED: Text '{expected_text[:50]}...' exists in '{display_path}'"
            else:
                return f"✗ VERIFICATION FAILED: Expected text not found in '{display_path}'"
        else:
            if not text_found:
                return f"✓ VERIFIED: Text '{expected_text[:50]}...' successfully removed from '{display_path}'"
            else:
                return f"✗ VERIFICATION FAILED: Text still exists in '{display_path}' (should have been removed)"
    except GuardedCodeError as e:
        return f"ERROR during verification: {e}"

    except Exception as e:
        return f"ERROR during verification: {str(e)}"


# Export functions for LlamaIndex FunctionTool creation
__all__ = [
    'read_code',
    'search_code',
    'edit_code',
    'list_files',
    'verify_change'
]


if __name__ == "__main__":
    # Test the tools
    print("=== Testing Code Tools for LLM Self-Improvement ===\n")

    # Test 1: Search for Snibbly Nips
    print("1. Searching for 'Snibbly Nips':")
    result = search_code("Snibbly.*Nips")
    print(result[:500])
    print()

    # Test 2: List Settings pages
    print("2. Frontend pages:")
    result = list_files("frontend/src/pages", max_depth=1)
    print(result[:500])
    print()

    print("Code tools ready for ReActAgent integration")
