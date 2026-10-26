#!/usr/bin/env python3
"""
System Tools
Safe, read-only system operations for agents.
"""

import fnmatch
import logging
import os
import re
import subprocess
import shlex
import time
from typing import List, Dict, Any, Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)

# Error-output redirects a model adds out of habit; see SystemCommandTool.execute.
_STDERR_REDIRECT_RE = re.compile(r"\s+2>\s*/dev/null\b|\s+2>&1")


class SystemCommandTool(BaseTool):
    """
    Executes safe, read-only system commands.
    """
    
    name = "system_command"
    read_only = True
    plan_research = False
    description = (
        "Inspect local project files and directories (ls, grep, cat, find). Only for filesystem "
        "operations, NOT for looking up general information — use web_search for that. Runs one "
        "command without a shell: pipes (|), ;, &&, ||, backticks, $( ) and redirects are refused, "
        "so make one call per command. Allowed: ls, grep, cat, find, wc, head, tail, pwd, whoami, "
        "date, echo. Relative paths start at the Guaardvark folder. To find a file by name, "
        "find_files is faster and skips build folders."
    )
    chat_summary = (
        "Run ONE read-only command on local files: ls, grep, cat, find, wc, head, tail. No pipes, "
        ";, &&, ||, $( ) or redirects; one command per call. To find files by name use find_files."
    )
    is_dangerous = False # Explicitly safe because of whitelist
    requires_approval = True
    # ls and find listings are the answer; 500 characters held a dozen paths.
    observation_chars = 2000
    
    # Whitelist of allowed commands
    ALLOWED_COMMANDS = [
        "ls", "grep", "cat", "find", "wc", "head", "tail", "pwd", "whoami", "date", "echo"
    ]
    
    parameters = {
        "command": ToolParameter(
            name="command",
            type="string",
            required=True,
            description="The command to execute (e.g., 'ls -la', 'grep pattern file.txt')"
        ),
        "cwd": ToolParameter(
            name="cwd",
            type="string",
            required=False,
            description="Working directory. Default: the chat's project folder when it has one, else Guaardvark's folder",
            default=None
        )
    }
    
    # find(1) expressions that execute programs, delete or write files.
    FIND_FORBIDDEN = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0",
                      "-fprintf", "-fls"}
    # Options whose next argument is a value, not a path.
    VALUE_OPTIONS = {
        "grep": {"-m", "--max-count", "-A", "-B", "-C", "--after-context", "--before-context",
                 "--context", "--label", "--color", "--colour", "-d", "-D"},
        "head": {"-n", "-c", "--lines", "--bytes"},
        "tail": {"-n", "-c", "--lines", "--bytes"},
        "ls": {"-I", "--ignore", "--hide", "-w", "--width", "-T", "--tabsize", "--sort", "--time-style"},
    }
    NO_PATH_COMMANDS = {"pwd", "whoami", "date", "echo"}

    @staticmethod
    def _allowed_roots(turn_root=None):
        """Where "Project folder only" lets tools go: Guaardvark's folder,
        GUAARDVARK_ALLOWED_PATHS, and the chat's project folder when it has one."""
        from backend import config

        roots = [str(config.GUAARDVARK_ROOT)] + list(getattr(config, "ALLOWED_AUTOMATION_PATHS", []))
        return roots + [turn_root] if turn_root else roots

    def _path_args(self, base_cmd, args):
        """The args that name files or directories (checked for credential files
        and, when paths are confined, for containment)."""
        paths, skip_next, pattern_seen = [], False, False
        value_opts = self.VALUE_OPTIONS.get(base_cmd, set())
        find_expr = False
        for i, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            if base_cmd == "find":
                if find_expr or arg.startswith("-") or arg in ("(", ")", "!", ","):
                    find_expr = True  # everything after the first expression token is expression
                    continue
                paths.append(arg)
                continue
            if base_cmd == "grep":
                if arg in ("-e", "--regexp"):
                    pattern_seen, skip_next = True, True
                    continue
                if arg in ("-f", "--file"):
                    pattern_seen = True
                    if i + 1 < len(args):
                        paths.append(args[i + 1])
                    skip_next = True
                    continue
            if arg in value_opts:
                skip_next = True
                continue
            if arg.startswith("-"):
                continue
            if base_cmd == "grep" and not pattern_seen:
                pattern_seen = True  # first positional is the pattern
                continue
            paths.append(arg)
        return paths

    def execute(self, **kwargs) -> ToolResult:
        """Execute the system command"""
        from backend.utils.path_safety import is_sensitive

        command_str = kwargs.get("command", "")
        cwd = kwargs.get("cwd")
        
        if not command_str:
            return ToolResult(success=False, error="Command is required")

        # There is no shell, so "2>/dev/null" would reach find or grep as a
        # path and fail the call. Error output already comes back with the
        # result, which is all these redirects are ever asked to do.
        command_str = _STDERR_REDIRECT_RE.sub("", command_str).strip()
            
        # Security check: Parse command and check against whitelist
        try:
            parts = shlex.split(command_str)
            if not parts:
                return ToolResult(success=False, error="Empty command")
            # No shell expands "~", and find_files reports paths that way, so a
            # path copied from its result named a folder literally called "~".
            parts = [os.path.expanduser(p) if p.startswith("~") else p for p in parts]
            if cwd and str(cwd).startswith("~"):
                cwd = os.path.expanduser(str(cwd))
                
            base_cmd = parts[0]
            if base_cmd not in self.ALLOWED_COMMANDS:
                return ToolResult(
                    success=False, 
                    error=f"Command '{base_cmd}' is not allowed. Allowed: {', '.join(self.ALLOWED_COMMANDS)}"
                )
                
            # Prevent chaining or piping which might bypass checks (simple check)
            if any(c in command_str for c in [";", "|", "&", "`", "$("]):
                 return ToolResult(
                    success=False, 
                    error=(
                        "Only one command per call: pipes (|), ;, &&, ||, backticks and $( ) are "
                        "refused. Make a separate system_command call for each command, e.g. "
                        "find ~/Documents -iname '*report*'. To find files by name, find_files is simpler."
                    ),
                )
            # Only a standalone operator is a redirect; "<div" or ">=" can be a grep pattern.
            if any(p in (">", ">>", "<", "1>", "2>", "&>") for p in parts[1:]):
                return ToolResult(
                    success=False,
                    error="Redirects are not supported: there is no shell, and the output comes back to you directly. Drop the redirect and run the command again.",
                )

            if base_cmd == "find":
                bad = [a for a in parts[1:] if a in self.FIND_FORBIDDEN or a.startswith("-fprint")]
                if bad:
                    return ToolResult(
                        success=False,
                        error=f"find actions that run programs or modify files are not allowed: {', '.join(bad)}",
                    )
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to parse command: {e}")

        from backend.services.project_root import turn_project_root

        turn_root = turn_project_root(self)
        if not cwd and turn_root:
            cwd = turn_root

        from backend.utils.settings_utils import get_confine_tool_paths

        if get_confine_tool_paths():
            # Settings → Agents → "Project folder only": the working directory
            # and every path argument stay inside the project and
            # GUAARDVARK_ALLOWED_PATHS.
            from backend import config
            from backend.utils.path_safety import is_within

            roots = self._allowed_roots(turn_root)
            cwd = cwd or str(config.GUAARDVARK_ROOT)
            if not is_within(cwd, roots):
                return ToolResult(success=False, error=f"cwd '{cwd}' is outside the allowed directories")
            if base_cmd not in self.NO_PATH_COMMANDS:
                for path in self._path_args(base_cmd, parts[1:]):
                    if not is_within(path, roots, base=cwd):
                        return ToolResult(
                            success=False,
                            error=(f"'{path}' is outside the project folder and GUAARDVARK_ALLOWED_PATHS "
                                   f"(Settings → Agents → Project folder only is on)"),
                        )

        if base_cmd not in self.NO_PATH_COMMANDS:
            for path in self._path_args(base_cmd, parts[1:]):
                if is_sensitive(path):
                    return ToolResult(success=False, error=f"'{path}' may contain credentials and cannot be read")

        if base_cmd == "grep":
            # Recursive greps must not print secrets from credential files.
            from backend.utils.path_safety import SENSITIVE_PATTERNS

            parts = parts[:1] + [f"--exclude={p}" for p in SENSITIVE_PATTERNS] + ["--exclude-dir=.git"] + parts[1:]

        try:
            # Execute
            result = subprocess.run(
                parts, 
                capture_output=True, 
                text=True, 
                cwd=cwd,
                timeout=10
            )
            
            return ToolResult(
                success=result.returncode == 0,
                output=result.stdout + result.stderr,
                metadata={
                    "returncode": result.returncode,
                    "command": command_str
                }
            )
            
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error="Command timed out")
        except Exception as e:
            logger.error(f"System command execution failed: {e}", exc_info=True)
            return ToolResult(success=False, error=str(e))


# Folders that are never what a person means by "my files", and that turn a
# name search into a crawl: dependency trees, caches and version control.
_FIND_SKIP_DIRS = {
    "node_modules", "venv", ".venv", "env", "__pycache__", "site-packages", ".git", ".hg",
    ".svn", ".cache", ".npm", ".cargo", ".rustup", "snap", ".local", ".mozilla", ".config",
}
_FIND_TIME_BUDGET_S = 8.0
_SEPARATORS = re.compile(r"[\s_\-.]+")


def _loose(text: str) -> str:
    """Lowercase with runs of spaces, underscores, dashes and dots made one space."""
    return _SEPARATORS.sub(" ", text.lower()).strip()


def _home_relative(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


class FindFilesTool(BaseTool):
    """Find files and folders by name, without a shell."""

    name = "find_files"
    read_only = True
    idempotent = True
    # Searching outside Guaardvark's own folders reads the names of the
    # person's files, so it asks first, as system_command does. Inside them
    # it does not: see needs_approval.
    requires_approval = True
    observation_chars = 3000
    chat_summary = (
        "Find files or folders on this computer whose name contains some text. Searches Guaardvark's "
        "uploads and outputs unless path is given; path='~' searches the home folder (asks the user first)."
    )
    description = (
        "Find files and folders whose name contains some text, case-insensitive, with spaces, "
        "underscores, dashes and dots treated alike (so 'drug commercial' finds "
        "Drug_Commercial_v2.mp4); * and ? work as wildcards. Searches Guaardvark's uploads and "
        "outputs folders unless path names another folder ('~' is the home folder, a relative path "
        "starts there). Searching outside Guaardvark's folders asks the user first. Skips hidden "
        "folders, dependency and cache folders, and other drives mounted inside the path; stops "
        "after 8 seconds. Reports each match with its size and date. For Guaardvark's own projects, "
        "clients and documents use find_records."
    )
    parameters = {
        "name": ToolParameter(
            name="name", type="string", required=True,
            description="Text the file or folder name contains, e.g. 'drug commercial' or '*.mp4'.",
        ),
        "path": ToolParameter(
            name="path", type="string", required=False,
            description="Folder to search: '~' for home, or a path. Default: Guaardvark's uploads and outputs.",
        ),
        "kind": ToolParameter(
            name="kind", type="string", required=False, default="any", enum=["any", "files", "folders"],
            description="any (default), files or folders.",
        ),
        "limit": ToolParameter(
            name="limit", type="int", required=False, default=50, minimum=1, maximum=200,
            description="Most matches to report, 1-200 (default 50).",
        ),
    }

    @staticmethod
    def _own_roots() -> List[str]:
        from backend import config
        return [str(config.UPLOAD_DIR), str(config.OUTPUT_DIR)]

    def _roots(self, path: Optional[str]) -> List[str]:
        if not path or not str(path).strip():
            from backend.services.project_root import turn_project_root
            turn_root = turn_project_root(self)
            return ([turn_root] if turn_root else []) + self._own_roots()
        raw = os.path.expanduser(str(path).strip())
        if not os.path.isabs(raw):
            raw = os.path.join(os.path.expanduser("~"), raw)
        return [os.path.realpath(raw)]

    def needs_approval(self, params: Dict[str, Any]) -> bool:
        """Ask only when the search reaches past Guaardvark's own folders."""
        from backend.utils.path_safety import is_within

        from backend.services.project_root import turn_project_root

        own = self._own_roots()
        turn_root = turn_project_root(self)
        if turn_root:
            own = own + [turn_root]
        return not all(is_within(root, own) for root in self._roots(params.get("path")))

    def execute(self, name: str = None, path: str = None, kind: str = None, limit: int = None,
                **_ignored) -> ToolResult:
        from backend.utils.path_safety import is_sensitive, is_within
        from backend.utils.settings_utils import get_confine_tool_paths

        needle = (name or "").strip()
        if not needle:
            return ToolResult(success=False, error="name is required: the text the file or folder name contains.")
        kind = (kind or "any").strip().lower()
        if kind not in ("any", "files", "folders"):
            kind = "any"
        try:
            limit = max(1, min(int(limit or 50), 200))
        except (TypeError, ValueError):
            limit = 50

        roots = self._roots(path)
        missing = [r for r in roots if not os.path.isdir(r)]
        if path and missing:
            return ToolResult(success=False, error=f"No folder at {_home_relative(missing[0])}.")
        roots = [r for r in roots if os.path.isdir(r)]
        if get_confine_tool_paths():
            from backend.services.project_root import turn_project_root
            allowed = SystemCommandTool._allowed_roots(turn_project_root(self))
            outside = [r for r in roots if not is_within(r, allowed)]
            if outside:
                return ToolResult(success=False, error=(
                    f"{_home_relative(outside[0])} is outside the project folder and "
                    "GUAARDVARK_ALLOWED_PATHS (Settings → File access → Project folder only is on)."
                ))

        wildcard = any(c in needle for c in "*?")
        pattern = needle.lower()
        loose = _loose(needle)

        def matches(entry: str) -> bool:
            if wildcard:
                return fnmatch.fnmatchcase(entry.lower(), pattern)
            return loose in _loose(entry)

        started = time.monotonic()
        found, folders_seen, timed_out = [], 0, False
        for root in roots:
            root_dev = os.stat(root).st_dev
            for current, dirs, files in os.walk(root):
                folders_seen += 1
                if time.monotonic() - started > _FIND_TIME_BUDGET_S:
                    timed_out = True
                    break
                kept = []
                for d in dirs:
                    full = os.path.join(current, d)
                    if d.startswith(".") or d in _FIND_SKIP_DIRS:
                        continue
                    try:
                        if os.stat(full).st_dev != root_dev:
                            continue  # another drive mounted inside the path
                    except OSError:
                        continue
                    kept.append(d)
                    if kind != "files" and matches(d):
                        found.append((full, True))
                dirs[:] = kept
                if kind != "folders":
                    for f in files:
                        if not f.startswith(".") and not is_sensitive(f) and matches(f):
                            found.append((os.path.join(current, f), False))
                if len(found) >= limit:
                    break
            if timed_out or len(found) >= limit:
                break

        elapsed = time.monotonic() - started
        where = ", ".join(_home_relative(r) for r in roots) or "(no folder to search)"
        if not found:
            note = " Stopped after 8 seconds; name a smaller folder with path." if timed_out else ""
            return ToolResult(success=True, output=(
                f"No file or folder named like '{needle}' under {where} "
                f"({folders_seen} folders searched in {elapsed:.1f}s).{note}"
            ), metadata={"count": 0})

        lines = [f"FILES named like '{needle}' under {where} — {len(found)} found"
                 + (" (stopped at the limit)" if len(found) >= limit else "")
                 + (" (stopped after 8 seconds; more may exist)" if timed_out else "")]
        for full, is_dir in found[:limit]:
            try:
                st = os.stat(full)
                stamp = time.strftime("%Y-%m-%d", time.localtime(st.st_mtime))
                size = "folder" if is_dir else _human_size(st.st_size)
            except OSError:
                stamp, size = "", "folder" if is_dir else "?"
            lines.append(f"  - {_home_relative(full)} · {size}" + (f" · {stamp}" if stamp else ""))
        return ToolResult(success=True, output="\n".join(lines), metadata={"count": len(found[:limit])})


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
