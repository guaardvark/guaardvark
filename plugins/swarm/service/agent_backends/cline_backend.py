"""
Cline backend — spawns Cline CLI pointed at local Ollama.

This is the offline workhorse. No API keys, no internet, just your
GPU and a local model doing its thing. Perfect for Flight Mode.
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

logger = logging.getLogger("swarm.backend.cline")

DONE_MARKER = ".swarm-status"
LOG_FILE = ".swarm-agent.log"

WRAPPER_SCRIPT = """#!/bin/bash
cd "{worktree_path}"

# the actual cline invocation — stdout/stderr go to the log file
{cline_command} >> "{log_file}" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "SWARM_AGENT_DONE" > .swarm-status
else
    echo "SWARM_AGENT_FAILED:$EXIT_CODE" > .swarm-status
fi
"""


class ClineBackend(BaseBackend):
    """
    Runs Cline CLI as the AI agent. Works fully offline with Ollama.

    Cost: $0.00 — your electricity bill is between you and your power company.
    """

    name = "cline"
    requires_internet = False

    def spawn(self, worktree_path: str, task: SwarmTask, config: dict[str, Any]) -> AgentProcess:
        wt = Path(worktree_path)
        log_file = wt / LOG_FILE

        command = config.get("command", "cline")
        model = config.get("model", "ollama/qwen2.5-coder:32b")
        extra_args = config.get("args", [])

        prompt = self._build_prompt(task)

        cline_parts = [command]
        if model:
            cline_parts.extend(["--model", model])
        cline_parts.extend(extra_args)
        cline_parts.extend(["--message", prompt])

        cline_cmd = " ".join(_shell_quote(p) for p in cline_parts)

        # write wrapper script
        wrapper_path = wt / ".swarm-run.sh"
        wrapper_path.write_text(
            WRAPPER_SCRIPT.format(
                worktree_path=worktree_path,
                cline_command=cline_cmd,
                log_file=str(log_file),
            )
        )
        wrapper_path.chmod(0o755)

        # launch as a background subprocess
        proc = subprocess.Popen(
            ["bash", str(wrapper_path)],
            cwd=worktree_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        logger.info(f"Launched Cline agent for '{task.id}' (model={model}, pid={proc.pid})")

        return AgentProcess(
            task_id=task.id,
            backend_name=self.name,
            pid=proc.pid,
            worktree_path=worktree_path,
            status=AgentStatus.RUNNING,
            metadata={"model": model, "popen": proc},
        )

    def check_status(self, process: AgentProcess) -> AgentStatus:
        wt = Path(process.worktree_path)

        # check our done marker
        status_file = wt / DONE_MARKER
        if status_file.exists():
            content = status_file.read_text().strip()
            if content == "SWARM_AGENT_DONE":
                process.status = AgentStatus.FINISHED
                return AgentStatus.FINISHED
            if content.startswith("SWARM_AGENT_FAILED"):
                process.status = AgentStatus.CRASHED
                return AgentStatus.CRASHED

        # check if the process is still alive
        if process.pid:
            try:
                os.kill(process.pid, 0)
            except ProcessLookupError:
                process.status = AgentStatus.CRASHED
                return AgentStatus.CRASHED
            except PermissionError:
                pass

        process.status = AgentStatus.RUNNING
        return AgentStatus.RUNNING

    def get_logs(self, process: AgentProcess, lines: int = 50) -> str:
        wt = Path(process.worktree_path)
        log_file = wt / LOG_FILE

        if not log_file.exists():
            return "(no output yet — agent still starting)"

        try:
            all_lines = log_file.read_text().split("\n")
            tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
            return "\n".join(tail)
        except Exception as e:
            return f"(could not read logs: {e})"

    def kill(self, process: AgentProcess) -> bool:
        if process.pid:
            try:
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
        return (0, 0.0)

    def is_available(self) -> bool:
        """Check if cline CLI is installed."""
        for cmd in ["cline", "openclaw", "cline-cli"]:
            if shutil.which(cmd):
                return True
        return False

    def _build_prompt(self, task: SwarmTask) -> str:
        parts = [
            f"Task: {task.title}",
            "",
            task.description,
            "",
            "You are working in a git worktree. Commit your changes when done.",
            "Work only on the files relevant to this task.",
        ]

        if task.file_scope:
            parts.append(f"\nFocus on these files: {', '.join(task.file_scope)}")

        return "\n".join(parts)


def _shell_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)
