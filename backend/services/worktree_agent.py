"""Guaardvark's code agent, working in one folder on a local Ollama model.

The Swarm gives each task a git worktree. This runs the built-in agent loop
(AgentExecutor) there with tools that stop at the worktree's edge: list, read
and search files, write and edit them, run the tests, commit. Nothing here
talks to anything but the local Ollama server, and the tests run with no
network and no write access outside the worktree (code_sandbox.wrap_confined)
wherever bubblewrap can provide that; elsewhere they run as the swarm's other
agents run theirs, in the worktree without a sandbox.

A small local model does the work, so the run is checked from outside the
loop: after each round the tests that cover the changed files are run, a
failure goes back to the model as the next round's task, and the run counts
as done only once those tests pass and the work is committed.

Run it as a program on one task (the swarm's guaardvark backend does):

    python -m backend.services.worktree_agent --root <worktree> "<task>"
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.services.agent_executor import AgentExecutor, FactsRegistry
from backend.services.agent_tools import BaseTool, ToolParameter, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

# Rounds of the agent loop per task, and steps per round. Measured 2026-11-25
# with gemma4:12b on three small tasks in a scratch repository (a new module
# with its test, a second module that imports the first, an edit to the first
# with new tests), four runs each: every run finished in one round, in 5, 7
# and 9 steps. 24 leaves room for a failing test or a mistyped edit; the
# second and third rounds are for the outside check sending work back.
MAX_ROUNDS = 3
MAX_STEPS_PER_ROUND = 24
# An identical call is allowed this many times between two changes to the
# worktree; a write or an edit starts the count again, so the tests can be
# run after every fix. At 8 with no reset, qwen3.5:9b spent a whole round
# reading the same two files (2026-11-25).
MAX_SAME_CALL = 3
MAX_FAILURES_PER_TOOL = 4
TEST_TIMEOUT_S = 300
TEST_OUTPUT_CHARS = 6000
MAX_WRITE_BYTES = 1024 * 1024
# What the model is given with each step has to fit its context window beside
# the instructions and tool list (about 1,500 tokens, measured on the first
# call of each run) and the reply (1,500 kept free). The rest, at about 3.4
# characters a token, is split one third for a read_file page and two thirds
# for the earlier steps: about 5,900 and 11,800 characters in the 8,192-token window
# the chat models are loaded with on a 16 GB card.
PROMPT_OVERHEAD_TOKENS = 3000
CHARS_PER_TOKEN = 3.4
MIN_STEP_CHARS = 3000
STEP_RESULT_CHARS = 6000
LLM_TIMEOUT_S = 600.0
# Low, for code: the same task should produce the same file.
TEMPERATURE = 0.2

DEFAULT_TEST_COMMAND = "python3 -m pytest"
# Left in a worktree by running code, never part of the work.
CACHE_DIRS = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")

AGENT_PROMPT = """You are a coding agent working alone in one git worktree. Do the task with your tools; nobody will answer questions.

- Look first: list_files shows the folder, read_file reads a file, search_code finds text.
- New file: write_file with the whole file. Change to an existing file: edit_file with the exact text to replace, or write_file with the whole new file.
- When the task asks for tests, write them, then call run_tests. If a test fails, read the output, fix the code and run the tests again.
- When the work is finished and the tests pass, call commit with a short message.
- Then answer in one or two sentences saying what you changed.

Paths are relative to the worktree. You have no internet access and cannot touch files outside the worktree."""


def _git(root: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=timeout)


def _is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


@dataclass
class Worktree:
    """The folder one run works in, and what the run has done there."""

    root: Path
    test_command: str = DEFAULT_TEST_COMMAND
    # Paths (relative to root) that belong to whoever started the run, such as
    # the swarm's log and status files. They are never committed.
    not_work: tuple = ()
    base: str = ""
    # None until the tests have run; then whether the last run passed.
    tests_passed: Optional[bool] = None
    sandboxed: Optional[bool] = None
    # Characters of one read_file page and of the earlier steps repeated to
    # the model; set from the model's context window (step_budget).
    page_chars: int = 5900
    earlier_chars: int = 11800
    log: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        if not self.base:
            self.base = self.head()

    def head(self) -> str:
        return _git(self.root, "rev-parse", "HEAD").stdout.strip()

    def _is_work(self, path: str) -> bool:
        parts = path.split("/")
        return not (path in self.not_work or path.endswith((".pyc", ".backup"))
                    or any(p in CACHE_DIRS for p in parts))

    def uncommitted(self) -> List[str]:
        """Changed and new files not yet committed, the run's bookkeeping left out."""
        out = _git(self.root, "status", "--porcelain", "--untracked-files=all").stdout
        paths = [line[3:].split(" -> ")[-1].strip().strip('"') for line in out.splitlines() if len(line) > 3]
        return [p for p in paths if self._is_work(p)]

    def changed_since_base(self) -> List[str]:
        committed = _git(self.root, "diff", "--name-only", self.base, "HEAD").stdout.split()
        return sorted(set(committed) | set(self.uncommitted()))

    def test_targets(self) -> List[str]:
        """Test files for what the run changed: the changed test files, and
        test_<name>.py / <name>_test.py for each changed Python file."""
        changed = self.changed_since_base()
        tracked = _git(self.root, "ls-files", "--cached", "--others", "--exclude-standard").stdout.split()
        by_name: Dict[str, List[str]] = {}
        for path in tracked:
            by_name.setdefault(path.rsplit("/", 1)[-1], []).append(path)
        targets: List[str] = []
        for path in changed:
            if not (self.root / path).is_file():
                continue
            if _is_test_file(path):
                targets.append(path)
            elif path.endswith(".py"):
                stem = path.rsplit("/", 1)[-1][:-3]
                targets += by_name.get(f"test_{stem}.py", []) + by_name.get(f"{stem}_test.py", [])
        return sorted(set(targets))

    def run_tests(self, targets: List[str]) -> tuple[Optional[bool], str]:
        """(passed, output). ``passed`` is None when the tests could not be run."""
        from backend.services import code_sandbox

        command = self.test_command.split() + ["-q", "-p", "no:cacheprovider", *targets]
        confined, why = code_sandbox.wrap_confined(command, self.root)
        self.sandboxed = confined is not None
        if confined is None:
            logger.info("Tests run without a sandbox: %s", why)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        try:
            proc = subprocess.run(confined or command, cwd=str(self.root), capture_output=True, text=True,
                                  timeout=TEST_TIMEOUT_S, env=env)
        except subprocess.TimeoutExpired:
            self.tests_passed = False
            return False, f"The tests did not finish in {TEST_TIMEOUT_S} seconds."
        except OSError as e:
            return None, f"The test command could not be started: {e}"
        # A test runner reports on stdout. Only an error and no report means
        # the command did not start, such as an interpreter without pytest.
        if proc.returncode != 0 and not proc.stdout.strip() and proc.stderr.strip():
            return None, f"The tests could not run: {proc.stderr.strip().splitlines()[-1][:300]}"
        output = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr.strip() else "")).strip()
        if len(output) > TEST_OUTPUT_CHARS:
            output = "...\n" + output[-TEST_OUTPUT_CHARS:]
        # pytest: 0 all passed, 1 some failed, 2 interrupted (an import or
        # syntax error while collecting), 5 nothing collected.
        self.tests_passed = proc.returncode == 0
        return self.tests_passed, output or f"(no output, exit code {proc.returncode})"

    def commit(self, message: str) -> tuple[bool, str]:
        pending = self.uncommitted()
        if not pending:
            return False, "There is nothing to commit: no file has changed since the last commit."
        excluded = [f":(exclude){p}" for p in self.not_work] + [
            f":(exclude,glob)**/{d}/**" for d in CACHE_DIRS] + [":(exclude,glob)**/*.backup"]
        added = _git(self.root, "add", "-A", "--", ".", *excluded)
        if added.returncode != 0:
            return False, f"git add failed: {added.stderr.strip()[:500]}"
        identity: List[str] = []
        if not _git(self.root, "config", "user.email").stdout.strip():
            identity = ["-c", "user.name=Guaardvark agent", "-c", "user.email=agent@localhost"]
        done = _git(self.root, *identity, "commit", "-m", message.strip() or "Agent work")
        if done.returncode != 0:
            return False, f"git commit failed: {(done.stderr or done.stdout).strip()[:800]}"
        return True, f"Committed {len(pending)} file(s): {', '.join(pending[:10])}"


class _WorktreeTool(BaseTool):
    """A tool bound to one worktree. Nothing in it asks for approval: the
    worktree is the task's own copy, and merging it back is a separate step."""

    read_only = False
    observation_chars = 8000

    def __init__(self, worktree: Worktree):
        super().__init__()
        self.wt = worktree

    def _resolve(self, filepath: str) -> tuple[Path, str]:
        """An existing-or-new path inside the worktree, or GuardedCodeError."""
        from backend.services.guarded_code_service import GuardedCodeError, resolve_repo_path
        from backend.utils.path_safety import is_sensitive

        resolved, relative = resolve_repo_path(str(filepath or ""), self.wt.root, allow_external=False)
        if is_sensitive(str(resolved)):
            raise GuardedCodeError("Credential and key files are not read or written by the agent",
                                   "SENSITIVE_PATH", 403)
        return resolved, relative


class ListFilesTool(_WorktreeTool):
    name = "list_files"
    read_only = True
    description = "List the source files in the worktree as a tree. directory is relative to the worktree; '.' is all of it."
    parameters = {
        "directory": ToolParameter(name="directory", type="string", required=False, default=".",
                                   description="Folder to list, relative to the worktree. Default '.'"),
    }

    def execute(self, directory: str = ".", **_: Any) -> ToolResult:
        from backend.tools.llama_code_tools import list_files, rooted_at

        with rooted_at(self.wt.root):
            out = list_files(directory or ".", max_depth=6)
        if out.startswith("ERROR"):
            return ToolResult(success=False, error=out)
        return ToolResult(success=True, output=out)


class ReadFileTool(_WorktreeTool):
    name = "read_file"
    read_only = True
    description = ("Read one text file in the worktree. Returns its content between START and END markers. "
                   "A long file comes in pages; each page says which start_line reads the next one.")
    parameters = {
        "filepath": ToolParameter(name="filepath", type="string", required=True,
                                  description="File to read, relative to the worktree, e.g. 'src/app.py'"),
        "start_line": ToolParameter(name="start_line", type="int", required=False,
                                    description="First line to return, counting from 1. Default: the first line"),
        "end_line": ToolParameter(name="end_line", type="int", required=False,
                                  description="Last line to return. Default: as far as one page reaches"),
    }

    def execute(self, filepath: str = "", start_line: Any = None, end_line: Any = None, **_: Any) -> ToolResult:
        from backend.tools.agent_tools.code_manipulation_tools import _line_number, _page_of_file
        from backend.tools.llama_code_tools import read_code, rooted_at

        with rooted_at(self.wt.root):
            out = read_code(filepath, allow_external=False)
        if out.startswith("ERROR"):
            return ToolResult(success=False, error=out)
        try:
            out, page = _page_of_file(out, filepath, _line_number(start_line, "start_line"),
                                      _line_number(end_line, "end_line"), max_chars=self.wt.page_chars)
        except ValueError as e:
            return ToolResult(success=False, error=f"ERROR reading '{filepath}': {e}")
        return ToolResult(success=True, output=out.replace("read_code", "read_file"),
                          metadata={"filepath": filepath, **page})


class SearchCodeTool(_WorktreeTool):
    name = "search_code"
    read_only = True
    description = ("Find lines matching a regular expression in the worktree's source files. "
                   "Returns path:line hits with the line text.")
    parameters = {
        "pattern": ToolParameter(name="pattern", type="string", required=True,
                                 description="Regular expression to look for (case-insensitive)"),
        "file_glob": ToolParameter(name="file_glob", type="string", required=False,
                                   description="Which files to search, e.g. '**/*.py' or 'src'. Default: all code files"),
    }

    def execute(self, pattern: str = "", file_glob: Optional[str] = None, **_: Any) -> ToolResult:
        from backend.tools.llama_code_tools import DEFAULT_SEARCH_GLOB, rooted_at, search_code

        with rooted_at(self.wt.root):
            out = search_code(pattern, file_glob or DEFAULT_SEARCH_GLOB)
        if out.startswith("ERROR"):
            return ToolResult(success=False, error=out)
        return ToolResult(success=True, output=out)


class WriteFileTool(_WorktreeTool):
    name = "write_file"
    description = ("Create a file in the worktree, or replace an existing one, with the content given. "
                   "Give the whole file. Folders are created as needed. A file whose syntax is broken is not written.")
    parameters = {
        "filepath": ToolParameter(name="filepath", type="string", required=True,
                                  description="File to write, relative to the worktree, e.g. 'pkg/util.py'"),
        "content": ToolParameter(name="content", type="string", required=True,
                                 description="The complete text of the file"),
    }

    def execute(self, filepath: str = "", content: Optional[str] = None, **_: Any) -> ToolResult:
        from backend.services.guarded_code_service import GuardedCodeError, protected_file_reason, syntax_check

        if content is None:
            return ToolResult(success=False, error="Missing required parameter: content")
        try:
            resolved, relative = self._resolve(filepath)
            reason = protected_file_reason(relative)
            if reason:
                raise GuardedCodeError(reason, "PROTECTED_FILE", 403)
        except GuardedCodeError as e:
            return ToolResult(success=False, error=f"'{filepath}' was not written: {e}")
        if resolved.is_dir():
            return ToolResult(success=False, error=f"'{relative}' is a folder")
        data = content.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            return ToolResult(success=False, error=f"'{relative}' was not written: over {MAX_WRITE_BYTES} bytes")
        checked, problem = syntax_check(resolved, content)
        if problem:
            return ToolResult(success=False,
                              error=f"'{relative}' was not written: syntax error ({problem}). Fix it and write the file again.")
        existed = resolved.exists()
        if existed and resolved.read_bytes() == data:
            return ToolResult(success=True, metadata={"filepath": relative, "unchanged": True},
                              output=f"No change: {relative} already has exactly this content. "
                                     "Go on to the next thing the task asks for.")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(data)
        self.wt.tests_passed = None
        return ToolResult(
            success=True,
            output=(f"{'Replaced' if existed else 'Created'} {relative} ({len(content.splitlines())} lines"
                    f"{', syntax checked' if checked else ''})."),
            metadata={"filepath": relative},
        )


class EditFileTool(_WorktreeTool):
    name = "edit_file"
    description = ("Change part of an existing file: replace old_text, which must occur exactly once in the file, "
                   "with new_text. Read the file first and copy old_text from it, whitespace included.")
    parameters = {
        "filepath": ToolParameter(name="filepath", type="string", required=True,
                                  description="File to edit, relative to the worktree"),
        "old_text": ToolParameter(name="old_text", type="string", required=True,
                                  description="The exact text to replace; it must occur once in the file"),
        "new_text": ToolParameter(name="new_text", type="string", required=True,
                                  description="The text to put in its place (empty to delete old_text)"),
    }

    def execute(self, filepath: str = "", old_text: Optional[str] = None, new_text: Optional[str] = None,
                **_: Any) -> ToolResult:
        from backend.services.guarded_code_service import GuardedCodeError, apply_exact_replacement

        try:
            self._resolve(filepath)
            done = apply_exact_replacement(
                filepath, old_text, new_text if new_text is not None else "",
                repo_root=self.wt.root, require_unlocked=False, allow_external=False, origin="worktree_agent",
            )
        except GuardedCodeError as e:
            hint = ""
            if e.code in ("TEXT_NOT_FOUND", "TEXT_NOT_UNIQUE"):
                hint = " Read the file again and copy the text exactly, or write the whole file with write_file."
            return ToolResult(success=False, error=f"'{filepath}' was not edited: {e}{hint}")
        except Exception as e:  # noqa: BLE001 - the model gets the reason and can write the file whole
            logger.warning("edit_file failed on %s: %s", filepath, e)
            return ToolResult(success=False, error=f"'{filepath}' was not edited: {e}")
        # The edit's own backup copy; in a worktree git already keeps the original.
        if done.backup_path:
            try:
                Path(done.backup_path).unlink()
            except OSError:
                pass
        self.wt.tests_passed = None
        diff = done.diff if len(done.diff) <= 3000 else done.diff[:3000] + "\n... diff cut ..."
        return ToolResult(success=True, output=f"Edited {done.relative_path}.\n\n{diff}",
                          metadata={"filepath": done.relative_path})


class RunTestsTool(_WorktreeTool):
    name = "run_tests"
    read_only = True
    description = ("Run the tests in the worktree and return the test runner's output. "
                   "With no target, the tests for the files you changed are run.")
    parameters = {
        "target": ToolParameter(name="target", type="string", required=False,
                                description="A test file or folder relative to the worktree, e.g. 'tests/test_app.py'. Default: the tests for your changes"),
    }

    def execute(self, target: Optional[str] = None, **_: Any) -> ToolResult:
        from backend.services.guarded_code_service import GuardedCodeError

        targets: List[str] = []
        if target and str(target).strip() not in (".", "./"):
            try:
                resolved, relative = self._resolve(str(target).strip())
            except GuardedCodeError as e:
                return ToolResult(success=False, error=f"'{target}' cannot be tested: {e}")
            if not resolved.exists():
                return ToolResult(success=False, error=f"'{relative}' does not exist; write it first")
            targets = [relative]
        else:
            targets = self.wt.test_targets()
            if not targets:
                return ToolResult(success=False,
                                  error="No test file covers what you changed. Write the tests first, "
                                        "or name a test file as target.")
        passed, output = self.wt.run_tests(targets)
        if passed is None:
            return ToolResult(success=False, error=output)
        verdict = "TESTS PASSED" if passed else "TESTS FAILED: fix the code or the tests, then run them again"
        return ToolResult(success=True, output=f"{verdict} ({' '.join(targets)})\n\n{output}",
                          metadata={"passed": passed, "targets": targets})


class CommitTool(_WorktreeTool):
    name = "commit"
    description = "Commit all your changes in the worktree with a short message. Call it once the work is finished and the tests pass."
    parameters = {
        "message": ToolParameter(name="message", type="string", required=True,
                                 description="One short line saying what changed, e.g. 'Add slugify helper with tests'"),
    }

    def execute(self, message: str = "", **_: Any) -> ToolResult:
        # Files changed since the tests last ran: run the tests that cover them first.
        if self.wt.tests_passed is None and self.wt.uncommitted():
            targets = self.wt.test_targets()
            if targets:
                passed, output = self.wt.run_tests(targets)
                if passed is False:
                    return ToolResult(success=False,
                                      error=f"Not committed: the tests fail ({' '.join(targets)}). Fix them first.\n\n"
                                            f"{output[-STEP_RESULT_CHARS:]}")
        if self.wt.tests_passed is False:
            return ToolResult(success=False,
                              error="Not committed: the last test run failed. Fix it and run the tests again first.")
        ok, detail = self.wt.commit(message)
        return ToolResult(success=True, output=detail) if ok else ToolResult(success=False, error=detail)


def build_tools(worktree: Worktree) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (ListFilesTool, ReadFileTool, SearchCodeTool, WriteFileTool, EditFileTool, RunTestsTool, CommitTool):
        registry.register(tool(worktree))
    return registry


class _NoFacts(FactsRegistry):
    """The loop's fact notes are for research answers. Sentences picked out of
    source files and test output are not facts, and they crowd the prompt."""

    def extract_facts_from_observation(self, tool_name, result, iteration):
        return []


CHANGING_TOOLS = ("write_file", "edit_file")


def _new_guard():
    from backend.services.tool_execution_guard import ToolExecutionGuard

    class _Guard(ToolExecutionGuard):
        """Counts repeats between changes: after a write or an edit, reading
        the file or running the tests again is a new question."""

        def record_result(self, tool_name, params, success, error, iteration):
            super().record_result(tool_name, params, success, error, iteration)
            if tool_name in CHANGING_TOOLS:
                self._changes.add(self._hash_call(tool_name, params))
                if success:
                    with self._lock:
                        # The changes themselves stay counted: writing the same file again is a repeat.
                        self._seen_hashes = {h: n for h, n in self._seen_hashes.items() if h in self._changes}

    guard = _Guard(max_failures_per_tool=MAX_FAILURES_PER_TOOL, max_duplicate_calls=MAX_SAME_CALL,
                   scope="for the rest of this round")
    guard._changes = set()
    return guard


def _short_args(tool: str, params: Dict[str, Any]) -> str:
    """A call's arguments on one line; file text is given by its size."""
    shown = []
    for key, value in (params or {}).items():
        text = str(value)
        if key in ("content", "old_text", "new_text") or len(text) > 80:
            text = f"<{len(text.splitlines()) or 1} lines>"
        shown.append(f"{key}={text!r}" if not text.startswith("<") else f"{key}={text}")
    return f"{tool}({', '.join(shown)})"


class _CodeAgentExecutor(AgentExecutor):
    """The agent loop for code work in one worktree.

    AgentExecutor gives the model one step's results at a time, which suits a
    question answered from a search. An edit needs the file and its test in
    view together, so each new step here also gets the earlier steps: every
    call on a line, and the results as far as the worktree's earlier_chars reach,
    newest first. Without that, both gemma4:12b and qwen3.5:9b read the same
    two files in turn until the steps ran out (2026-11-25, the
    keep_underscores edit task: 1 of 2 and 1 of 3 runs finished).
    """

    def __init__(self, *args, worktree: Optional[Worktree] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.facts_registry = _NoFacts()
        self._worktree = worktree
        # (step, call line, succeeded, result text, file the call was about)
        self._records: List[tuple] = []

    def _memory_context(self, user_query, process_id) -> str:
        # The person's saved chat memories are not part of a coding task, and
        # looking them up would start the whole backend inside this process.
        return ""

    def _new_guard(self):
        return _new_guard()

    def _execute_iteration_native(self, prompt, system_prompt, iteration, process_id):
        return self._with_earlier_steps(
            super()._execute_iteration_native(prompt, system_prompt, iteration, process_id), iteration)

    def _execute_iteration(self, prompt, system_prompt, iteration, process_id):
        out = super()._execute_iteration(prompt, system_prompt, iteration, process_id)
        # The native path falls back to this one and wraps the result itself.
        return out if self._native_tools_supported else self._with_earlier_steps(out, iteration)

    def _with_earlier_steps(self, out: Dict[str, Any], iteration: int) -> Dict[str, Any]:
        step = out.get("step")
        if out.get("is_final") or step is None or getattr(step, "_carried", False):
            return out
        step._carried = True
        observations = step.observations or []
        for obs in observations:
            outcome = obs.get("result") or {}
            ok = bool(outcome.get("success"))
            text = str((outcome.get("output") if ok else outcome.get("error")) or "").strip()
            params = obs.get("parameters") or {}
            if ok and obs.get("tool") == "write_file" and isinstance(params.get("content"), str):
                # What was written, so the model sees the file as it now is.
                text = f"{text}\nThe file now contains:\n{params['content']}"
            self._records.append((iteration, _short_args(obs.get("tool", "?"), params), ok, text,
                                  str(params.get("filepath") or "")))
        if not observations:
            # A reply with no tool call: the loop's own reminder, plus what was done so far.
            earlier = self._render_steps()
            if earlier:
                out["next_prompt"] = f"{out.get('next_prompt', '')}\n\n{earlier}"
            return out
        out["next_prompt"] = "\n\n".join(part for part in (
            f"Task:\n{self.original_query}",
            self._render_steps(),
            _state_note(self._worktree) if self._worktree else "",
            "Next: " + self._next_hint(observations),
        ) if part)
        return out

    def _next_hint(self, observations: List[Dict[str, Any]]) -> str:
        """What the worktree's state says the next step is. The model decides
        how to write the code; whether it is tested and committed is a fact."""
        wt = self._worktree
        last_error = str(((observations[-1].get("result") or {}).get("error")) or "") if observations else ""
        repeat = " Do not repeat a call you have already made." if "[BLOCKED]" in last_error else ""
        if wt is None:
            return "call the next tool, or give your final answer." + repeat
        pending = wt.uncommitted()
        if not pending:
            if wt.head() != wt.base:
                return ("your work is committed. If the task is complete, answer in one or two sentences "
                        "and call no more tools; otherwise do what is still missing.")
            return "nothing has been changed yet. Write the code with write_file or edit_file." + repeat
        if wt.tests_passed is True:
            return "the tests pass. If the task is complete, call commit now; otherwise do what is still missing."
        if wt.tests_passed is False:
            return ("the tests fail. Read the test output above, fix the code or the tests with edit_file "
                    "or write_file, then call run_tests." + repeat)
        if wt.test_targets():
            return "you changed files since the tests last ran. Call run_tests now, or finish the code first." + repeat
        return ("if the task asks for tests, write them now with write_file; when everything the task asks "
                "for is written, call commit." + repeat)

    def _render_steps(self) -> str:
        """Every call so far, oldest first. Results are filled in from the
        newest back until the budget is used; a file's content is shown once,
        where it was last read or written."""
        if not self._records:
            return ""
        budget = self._worktree.earlier_chars if self._worktree else 11800
        bodies: Dict[int, str] = {}
        shown_files: set = set()
        changed_later: set = set()
        tests_seen = False
        for index in range(len(self._records) - 1, -1, -1):
            _step, call, ok, text, path = self._records[index]
            tool = call.split("(", 1)[0]
            first_line = text.splitlines()[0][:200] if text else ""
            note = ""
            if ok and tool in ("read_file", "write_file"):
                # A file's content is shown once, where it was last read or written.
                key = (path, call) if tool == "read_file" else (path, "")
                if path in changed_later and tool == "read_file":
                    note = "(the file has changed since; its newer content is below)"
                elif key in shown_files or (path, "") in shown_files:
                    note = first_line if tool == "write_file" else "(read again below)"
                shown_files.add(key)
            elif ok and tool == "run_tests":
                if tests_seen:
                    note = first_line
                tests_seen = True
            if ok and tool in CHANGING_TOOLS:
                changed_later.add(path)
            if not note:
                body = text if len(text) <= STEP_RESULT_CHARS else text[:STEP_RESULT_CHARS] + "\n... cut ..."
                if len(body) <= budget:
                    budget -= len(body)
                    note = body
                else:
                    note = first_line + " (result not repeated here)"
            bodies[index] = note
        lines = ["What you have done so far, oldest first:"]
        for index, (step_no, call, ok, _text, _path) in enumerate(self._records):
            lines.append(f"{index + 1}. {call} -> {'ok' if ok else 'FAILED'}")
            if bodies.get(index):
                lines.append(bodies[index])
        return "\n".join(lines)


class _CountingLLM:
    """Passes every call through to the Ollama client and adds up the tokens
    Ollama reports for each reply."""

    def __init__(self, llm):
        self._llm = llm
        self.prompt_tokens = 0
        self.reply_tokens = 0
        self.calls = 0

    def __getattr__(self, name):
        return getattr(self._llm, name)

    def _count(self, response) -> None:
        raw = getattr(response, "raw", None) or {}
        get = raw.get if hasattr(raw, "get") else (lambda k, d=None: getattr(raw, k, d))
        self.prompt_tokens += int(get("prompt_eval_count", 0) or 0)
        self.reply_tokens += int(get("eval_count", 0) or 0)
        self.calls += 1

    def chat(self, *args, **kwargs):
        response = self._llm.chat(*args, **kwargs)
        self._count(response)
        return response

    def chat_with_tools(self, *args, **kwargs):
        response = self._llm.chat_with_tools(*args, **kwargs)
        self._count(response)
        return response


def choose_model(preferred: Optional[str]) -> tuple[Optional[str], str]:
    """(installed model to run, "") or (None, why there is none).

    ``preferred`` is kept when Ollama has it; otherwise the choice falls to
    resolve_chat_model's order (same family, the chat model in use, the
    hardware policy's). A model that cannot call tools is not used.
    """
    from backend.services.ollama_chat_model import installed_chat_tags, resolve_chat_model
    from backend.utils.ollama_resource_manager import model_supports_tools

    tags = installed_chat_tags()
    if not tags:
        return None, "Ollama is not running, or it has no chat model installed"
    wanted = (preferred or "").strip()
    if wanted.lower().startswith("ollama/"):
        wanted = wanted[len("ollama/"):]
    model = resolve_chat_model(wanted, installed=tags) if wanted else resolve_chat_model("", installed=tags)
    if model not in tags:
        return None, f"the model {wanted or '(none set)'} is not installed in Ollama"
    if not model_supports_tools(model):
        return None, f"the installed model {model} cannot call tools"
    return model, ""


def step_budget(context_tokens: int) -> tuple[int, int]:
    """(read_file page, earlier steps) in characters for a context window."""
    room = max(int((int(context_tokens or 0) - PROMPT_OVERHEAD_TOKENS) * CHARS_PER_TOKEN), MIN_STEP_CHARS)
    return room // 3, room - room // 3


def _build_llm(model: str):
    from backend.utils.ollama_resource_manager import build_ollama

    kwargs: Dict[str, Any] = {"request_timeout": LLM_TIMEOUT_S, "temperature": TEMPERATURE}
    try:
        from backend.config import get_chat_keep_alive
        kwargs["keep_alive"] = get_chat_keep_alive()
    except Exception as e:  # noqa: BLE001 - Ollama's own keep-alive then applies
        logger.debug("chat keep-alive not available: %s", e)
    return build_ollama(model, **kwargs)


def _agent_config():
    from backend.services.agent_config import AgentConfig, AgentType

    return AgentConfig(
        id="worktree_code_agent",
        name="Worktree Code Agent",
        description="Does one coding task in a git worktree.",
        agent_type=AgentType.CODE_ASSISTANT,
        tools=["list_files", "read_file", "search_code", "write_file", "edit_file", "run_tests", "commit"],
        system_prompt=AGENT_PROMPT,
        max_iterations=MAX_STEPS_PER_ROUND,
    )


def _state_note(worktree: Worktree) -> str:
    changed = worktree.changed_since_base()
    if not changed:
        return ""
    pending = worktree.uncommitted()
    note = "Files changed so far in this worktree: " + ", ".join(changed[:30]) + "."
    note += (" Not yet committed: " + ", ".join(pending[:30]) + "." if pending else " All of it is committed.")
    if worktree.tests_passed is not None and pending:
        note += " The last test run " + ("passed." if worktree.tests_passed else "failed.")
    return note


def run_task(task: str, root: str | Path, *, model: Optional[str] = None, test_command: str = DEFAULT_TEST_COMMAND,
             not_work: tuple = (), base: str = "") -> Dict[str, Any]:
    """Do ``task`` in the worktree at ``root``. The report says whether it is
    done (tests for the changed files pass and the work is committed), with
    the model, rounds, steps, tokens and seconds it took."""
    started = time.time()
    report: Dict[str, Any] = {"done": False, "model": None, "rounds": 0, "steps": 0, "tool_calls": 0,
                              "prompt_tokens": 0, "reply_tokens": 0, "tests": None, "sandboxed_tests": None,
                              "commit": None, "reason": "", "answer": ""}
    worktree = Worktree(Path(root), test_command=test_command, not_work=tuple(not_work), base=base)
    if not worktree.base:
        report["reason"] = f"{worktree.root} is not a git checkout with a commit"
        return report

    chosen, why = choose_model(model)
    if not chosen:
        report["reason"] = f"No local model to run on: {why}"
        return report
    report["model"] = chosen
    llm = _CountingLLM(_build_llm(chosen))
    worktree.page_chars, worktree.earlier_chars = step_budget(getattr(llm, "context_window", 0))
    print(f"[agent] model {chosen}, context {getattr(llm, 'context_window', '?')} tokens, worktree {worktree.root}",
          flush=True)

    registry = build_tools(worktree)
    query = task
    for round_no in range(1, MAX_ROUNDS + 1):
        report["rounds"] = round_no
        executor = _CodeAgentExecutor(registry, llm, max_iterations=MAX_STEPS_PER_ROUND, agent=_agent_config(),
                                      worktree=worktree)
        result = executor.execute(query, session_context=_state_note(worktree))
        report["steps"] += result.iterations
        for step in result.steps:
            for obs in step.observations or []:
                report["tool_calls"] += 1
                outcome = obs.get("result") or {}
                text = str(outcome.get("output") if outcome.get("success") else outcome.get("error") or "")
                print(f"[round {round_no} step {step.iteration}] {obs.get('tool')} "
                      f"{'ok' if outcome.get('success') else 'FAILED'}: {text.strip().splitlines()[0][:160] if text.strip() else ''}",
                      flush=True)
        if not result.success:
            report["reason"] = f"The agent loop stopped: {result.error}"
            print(f"[agent] {report['reason']}", flush=True)
            break
        report["answer"] = (result.final_answer or "").strip()

        changed = worktree.changed_since_base()
        if not changed:
            report["reason"] = "The agent changed no file"
            query = (f"{task}\n\nNothing has been changed in the worktree yet. Do the task now with "
                     "write_file or edit_file, then run_tests if it has tests, then commit.")
            print(f"[round {round_no}] nothing changed; asking again", flush=True)
            continue

        targets = worktree.test_targets()
        if targets:
            passed, output = worktree.run_tests(targets)
            report["tests"] = {"targets": targets, "passed": passed}
            report["sandboxed_tests"] = worktree.sandboxed
            print(f"[round {round_no}] tests {' '.join(targets)}: "
                  f"{'passed' if passed else 'could not run' if passed is None else 'FAILED'}", flush=True)
            if not passed:
                report["reason"] = "The tests for the changed files do not pass"
                query = (f"{task}\n\nYour earlier work is in the worktree, but its tests do not pass. "
                         f"Read the files, fix the problem, run the tests again and commit.\n\n"
                         f"Test output:\n{output[-3000:]}")
                continue

        # A round that ends with the model still calling tools ran out of
        # steps. Its work counts only if the model had already committed it.
        finished = bool(result.steps) and not result.steps[-1].tool_calls
        pending = worktree.uncommitted()
        if not finished and (pending or worktree.head() == worktree.base):
            report["reason"] = f"The agent ran out of steps ({MAX_STEPS_PER_ROUND}) before finishing"
            query = (f"{task}\n\nYou ran out of steps before finishing. Do not read files again: write "
                     "what is still missing, run the tests, and commit.")
            print(f"[round {round_no}] out of steps with the work unfinished; asking again", flush=True)
            continue
        if pending:
            first_line = task.strip().splitlines()[0][:72] if task.strip() else "Agent work"
            ok, detail = worktree.commit(first_line)
            print(f"[round {round_no}] the agent left its work uncommitted; committing it: {detail}", flush=True)
            if not ok:
                report["reason"] = detail
                break
        report["done"] = True
        report["reason"] = ""
        break

    head = worktree.head()
    report["commit"] = head if head != worktree.base else None
    if report["done"] and not report["commit"]:
        report["done"] = False
        report["reason"] = "Nothing was committed"
    report["prompt_tokens"] = llm.prompt_tokens
    report["reply_tokens"] = llm.reply_tokens
    report["llm_calls"] = llm.calls
    report["seconds"] = round(time.time() - started, 1)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run Guaardvark's code agent on one task in a git worktree.")
    parser.add_argument("--root", default=".", help="the worktree (default: the current folder)")
    parser.add_argument("--model", default=None, help="Ollama model to prefer, e.g. qwen3.5:9b")
    parser.add_argument("--test-command", default=DEFAULT_TEST_COMMAND)
    parser.add_argument("--base", default="", help="commit the task started from (default: HEAD now)")
    parser.add_argument("--not-work", action="append", default=[],
                        help="a path in the worktree that is not the agent's work and is never committed")
    parser.add_argument("--result-file", default=None, help="write the run's report here as JSON")
    parser.add_argument("--resolve-model", action="store_true",
                        help="print the model a run would use as JSON and exit")
    parser.add_argument("task", nargs="?", default="")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)

    if args.resolve_model:
        model, why = choose_model(args.model)
        print(json.dumps({"model": model, "reason": why}))
        return 0 if model else 2
    if not args.task.strip():
        parser.error("a task is required")

    report = run_task(args.task, args.root, model=args.model, test_command=args.test_command,
                      not_work=tuple(args.not_work), base=args.base)
    if args.result_file:
        try:
            Path(args.result_file).write_text(json.dumps(report, indent=2))
        except OSError as e:
            logger.warning("Could not write %s: %s", args.result_file, e)
    tokens = report["prompt_tokens"] + report["reply_tokens"]
    if report["done"]:
        print(f"[agent] done in {report['seconds']} s on {report['model']}: {report['rounds']} round(s), "
              f"{report['steps']} steps, {tokens:,} tokens, commit {str(report['commit'])[:10]}", flush=True)
        if report["answer"]:
            print(report["answer"], flush=True)
        return 0
    print(f"[agent] not done after {report.get('seconds', 0)} s: {report['reason']}", flush=True)
    return 1 if report["model"] else 2


if __name__ == "__main__":
    sys.exit(main())
