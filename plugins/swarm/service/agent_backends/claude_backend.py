"""
Claude Code backend — spawns Claude Code as a subprocess.

Each task gets its own background process running in the task's worktree.
Output goes to a log file for dashboard viewing. No tmux needed.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..models import AgentStatus, SwarmTask
from .base_backend import AgentProcess, BaseBackend

logger = logging.getLogger("swarm.backend.claude")

COMPLETION_MARKER = "completion.md"
LOG_FILE = ".swarm-agent.log"

WRAPPER_SCRIPT = """#!/bin/bash
cd "{worktree_path}"

# Claude Code 2.x writes its response to stdout in --print mode. Route
# stdout into completion.md (so estimate_cost and the dashboard have a
# preview of the response), stderr into the log file (so crashes are
# captured), and mirror both to the log for debugging.
{claude_command} > "{completion_file}" 2>> "{log_file}"
EXIT_CODE=$?

# Append the response to the log too so the dashboard tails it.
if [ -s "{completion_file}" ]; then
    cat "{completion_file}" >> "{log_file}"
fi

if [ $EXIT_CODE -eq 0 ]; then
    echo "SWARM_AGENT_DONE" > .swarm-status
else
    echo "SWARM_AGENT_FAILED:$EXIT_CODE" > .swarm-status
fi
"""


class ClaudeBackend(BaseBackend):
    """Runs Claude Code as the AI agent. Needs internet (Anthropic API)."""

    name = "claude"
    requires_internet = True

    def spawn(self, worktree_path: str, task: SwarmTask, config: dict[str, Any]) -> AgentProcess:
        wt = Path(worktree_path)
        log_file = wt / LOG_FILE
        completion_file = wt / COMPLETION_MARKER

        command = config.get("command", "claude")
        # Claude Code 2.x does not have --output-file. It writes to stdout in
        # --print mode and the wrapper script captures that into completion.md.
        # --bare skips hooks, CLAUDE.md auto-discovery, keychain reads, and
        # auto-memory, which is what we want for a sandboxed worktree agent
        # that should stand on its own without parent-session pollution.
        # --dangerously-skip-permissions is required for non-interactive
        # execution — without it Claude refuses to use Bash/Edit/Write in
        # "don't ask mode" and the agent returns a text-only refusal instead
        # of actually creating files. The worktree is the sandbox; that's
        # exactly the case this flag is designed for.
        args = config.get("args", ["--print", "--bare", "--dangerously-skip-permissions"])

        prompt = self._build_prompt(task)

        claude_parts = [command] + args + [prompt]
        claude_cmd = " ".join(_shell_quote(p) for p in claude_parts)

        # write wrapper script
        wrapper_path = wt / ".swarm-run.sh"
        wrapper_path.write_text(
            WRAPPER_SCRIPT.format(
                worktree_path=worktree_path,
                claude_command=claude_cmd,
                log_file=str(log_file),
                completion_file=str(completion_file),
            )
        )
        wrapper_path.chmod(0o755)

        # launch as a background subprocess
        proc = subprocess.Popen(
            ["bash", str(wrapper_path)],
            cwd=worktree_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # detach from our process group
        )

        logger.info(f"Launched Claude agent for '{task.id}' (pid={proc.pid})")

        return AgentProcess(
            task_id=task.id,
            backend_name=self.name,
            pid=proc.pid,
            worktree_path=worktree_path,
            status=AgentStatus.RUNNING,
            metadata={"popen": proc},
        )

    def check_status(self, process: AgentProcess) -> AgentStatus:
        wt = Path(process.worktree_path)

        # check the status marker file
        status_file = wt / ".swarm-status"
        if status_file.exists():
            content = status_file.read_text().strip()
            if content == "SWARM_AGENT_DONE":
                process.status = AgentStatus.FINISHED
                return AgentStatus.FINISHED
            if content.startswith("SWARM_AGENT_FAILED"):
                process.status = AgentStatus.CRASHED
                return AgentStatus.CRASHED

        # Check if the completion marker has content. The wrapper script
        # redirects stdout to completion.md via shell `>` which creates the
        # file at fork time — so existence alone doesn't mean anything. Only
        # a non-empty file indicates Claude actually produced a response.
        completion_file = wt / COMPLETION_MARKER
        if completion_file.exists() and completion_file.stat().st_size > 0:
            process.status = AgentStatus.FINISHED
            return AgentStatus.FINISHED

        # check if the process is still alive
        if process.pid:
            try:
                os.kill(process.pid, 0)  # signal 0 = just check if alive
            except ProcessLookupError:
                # process is gone but no completion marker — it crashed
                process.status = AgentStatus.CRASHED
                return AgentStatus.CRASHED
            except PermissionError:
                pass  # process exists but we can't signal it — still running

        process.status = AgentStatus.RUNNING
        return AgentStatus.RUNNING

    def get_logs(self, process: AgentProcess, lines: int = 50) -> str:
        wt = Path(process.worktree_path)
        log_file = wt / LOG_FILE

        if not log_file.exists():
            return "(no output yet — agent still starting)"

        # read last N lines from the log file
        try:
            all_lines = log_file.read_text().split("\n")
            tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
            return "\n".join(tail)
        except Exception as e:
            return f"(could not read logs: {e})"

    def kill(self, process: AgentProcess) -> bool:
        if process.pid:
            try:
                # kill the whole process group (wrapper + child)
                os.killpg(os.getpgid(process.pid), 9)
                process.status = AgentStatus.KILLED
                return True
            except (ProcessLookupError, PermissionError):
                pass

            try:
                os.kill(process.pid, 9)
                process.status = AgentStatus.KILLED
                return True
            except (ProcessLookupError, PermissionError):
                pass

        return False

    def estimate_cost(self, process: AgentProcess) -> tuple[int, float]:
        wt = Path(process.worktree_path)
        completion = wt / COMPLETION_MARKER
        if not completion.exists():
            return (0, 0.0)

        content = completion.read_text()
        estimated_tokens = len(content) // 4
        cost = estimated_tokens * 0.075 / 1000
        return (estimated_tokens, cost)

    def is_available(self) -> bool:
        return shutil.which("claude") is not None

    def _build_prompt(self, task: SwarmTask) -> str:
        parts = [
            f"You are working on task: {task.title}",
            "",
            task.description,
            "",
            "IMPORTANT: You are working in a git worktree. Commit your changes when done.",
            "Work only on the files relevant to this task.",
        ]

        if task.file_scope:
            parts.append(f"\nFocus on these files: {', '.join(task.file_scope)}")

        return "\n".join(parts)


def _shell_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)
