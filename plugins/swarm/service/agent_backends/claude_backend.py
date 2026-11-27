"""
Claude Code backend — spawns Claude Code as a subprocess.

Each task gets its own background process running in the task's worktree.
Output goes to a log file for dashboard viewing. No tmux needed.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..backend_url import backend_api_url
from ..models import AgentStatus, SwarmTask
from .base_backend import AgentProcess, BaseBackend, agent_env, pid_running

logger = logging.getLogger("swarm.backend.claude")

COMPLETION_MARKER = "completion.md"
LOG_FILE = ".swarm-agent.log"
# The CLI's own JSON report of the run: its reply, token usage and cost.
RESULT_FILE = ".swarm-result.json"

WRAPPER_SCRIPT = """#!/bin/bash
cd "{worktree_path}"

# Clear any leftover state from a previous attempt in this worktree before
# we start writing fresh output. If we don't, a stale SWARM_AGENT_FAILED
# from the prior run gets read by the orchestrator's next poll and the new
# agent is declared crashed before it has a chance to do anything.
rm -f .swarm-status

{claude_command} > "{result_file}" 2>> "{log_file}"
EXIT_CODE=$?

# The reply is the "result" field of the report; output that is not such a
# report (a CLI that printed plain text) is kept as it is.
{python} -c 'import json, sys; sys.stdout.write(json.load(open(sys.argv[1]))["result"])' \
    "{result_file}" > "{completion_file}" 2>/dev/null || cp "{result_file}" "{completion_file}"

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

    def command_prefix(self, config: dict[str, Any]) -> list[str]:
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
        args = list(config.get("args", ["--print", "--bare", "--dangerously-skip-permissions"]))
        if "--bare" in args and not os.environ.get("ANTHROPIC_API_KEY"):
            # --bare signs in with ANTHROPIC_API_KEY only; without one it
            # answers "Not logged in". A signed-in CLI runs instead with the
            # user's own settings, hooks and MCP servers left out.
            args = [a for a in args if a != "--bare"] + [
                "--setting-sources", "project,local", "--strict-mcp-config", "--no-session-persistence"]
        return [command] + args

    def spawn(self, worktree_path: str, task: SwarmTask, config: dict[str, Any]) -> AgentProcess:
        wt = Path(worktree_path)
        log_file = wt / LOG_FILE
        completion_file = wt / COMPLETION_MARKER

        # Belt-and-suspenders cleanup of state files from a prior attempt.
        # The wrapper script also does this, but we do it here too so the
        # window between spawn() returning and the wrapper actually executing
        # can't be observed by check_status with stale files in place.
        result_file = wt / RESULT_FILE
        for stale in (wt / ".swarm-status", completion_file, result_file):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass

        prompt = self._build_prompt(task)

        # --output-format json makes the CLI report the tokens the run used.
        claude_parts = self.command_prefix(config) + ["--output-format", "json", prompt]
        claude_cmd = " ".join(_shell_quote(p) for p in claude_parts)

        # write wrapper script
        wrapper_path = wt / ".swarm-run.sh"
        wrapper_path.write_text(
            WRAPPER_SCRIPT.format(
                worktree_path=worktree_path,
                claude_command=claude_cmd,
                log_file=str(log_file),
                completion_file=str(completion_file),
                result_file=str(result_file),
                python=_shell_quote(sys.executable or "python3"),
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
            env=agent_env(keep=("ANTHROPIC_API_KEY",)),
        )

        logger.info(f"Launched Claude agent for '{task.id}' (pid={proc.pid})")

        return AgentProcess(
            task_id=task.id,
            backend_name=self.name,
            pid=proc.pid,
            worktree_path=worktree_path,
            status=AgentStatus.RUNNING,
            # A run signed in with an API key is billed per token; a signed-in
            # subscription is not, so the CLI's dollar figure is not its cost.
            metadata={"popen": proc, "billed_per_token": bool(os.environ.get("ANTHROPIC_API_KEY"))},
        )

    def check_status(self, process: AgentProcess) -> AgentStatus:
        wt = Path(process.worktree_path)

        # The .swarm-status file is the only authoritative termination signal.
        # The wrapper script writes it after Claude exits, with the exit code
        # encoded as DONE or FAILED:N. Reading completion.md size or polling
        # the pid before this file appears is unreliable — `claude --print`
        # streams to stdout, so completion.md grows mid-run, and a finished
        # wrapper process may briefly linger as a zombie.
        status_file = wt / ".swarm-status"
        if status_file.exists():
            content = status_file.read_text().strip()
            if content == "SWARM_AGENT_DONE":
                process.status = AgentStatus.FINISHED
                return AgentStatus.FINISHED
            if content.startswith("SWARM_AGENT_FAILED"):
                process.status = AgentStatus.CRASHED
                return AgentStatus.CRASHED

        # No status file yet — check whether the wrapper process is still
        # alive. Prefer Popen.poll() because it reaps zombies; fall back to
        # signal 0 only if we lost the Popen handle (e.g. after a reload).
        popen = process.metadata.get("popen") if isinstance(process.metadata, dict) else None
        if popen is not None:
            rc = popen.poll()
            if rc is None:
                process.status = AgentStatus.RUNNING
                return AgentStatus.RUNNING
            # Wrapper exited without writing .swarm-status — that means it
            # crashed before reaching the trailing `echo` (e.g. killed, OOM,
            # disk full). Treat as crashed.
            process.status = AgentStatus.CRASHED
            return AgentStatus.CRASHED

        if process.pid and not pid_running(process.pid):
            process.status = AgentStatus.CRASHED
            return AgentStatus.CRASHED

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
        """Tokens and cost as the CLI reported them for this run; zeros when
        it reported none. Tokens are everything the run sent and received,
        cached input included. The cost is passed on only for a run billed per
        token."""
        try:
            report = json.loads((Path(process.worktree_path) / RESULT_FILE).read_text())
            usage = report.get("usage") or {}
            tokens = sum(int(usage.get(k) or 0) for k in (
                "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
            cost = float(report.get("total_cost_usd") or 0.0)
        except (OSError, ValueError, TypeError, AttributeError):
            return (0, 0.0)
        billed = isinstance(process.metadata, dict) and process.metadata.get("billed_per_token")
        return (tokens, cost if billed else 0.0)

    def is_available(self) -> bool:
        return shutil.which("claude") is not None

    def _build_prompt(self, task: SwarmTask) -> str:
        swarm_api = f"{backend_api_url()}/swarm/{task.swarm_id if hasattr(task, 'swarm_id') else 'active'}"
        
        parts = [
            f"You are working on task: {task.title}",
            "",
            task.description,
            "",
            "CONTEXT:",
            f"Current swarm ID: {task.swarm_id if hasattr(task, 'swarm_id') else 'active'}",
            f"Your Task ID: {task.id}",
            "",
            "INTER-AGENT COMMUNICATION:",
            "You can communicate with other agents in this swarm using these endpoints:",
            f"- GET  {swarm_api}/bus/state : Get shared architecture decisions or global state.",
            f"- POST {swarm_api}/bus/state : Update shared state (e.g., 'key': 'api_contract', 'value': '...').",
            f"- POST {swarm_api}/bus/broadcast : Send an event (e.g., 'event_type': 'schema_ready').",
            "Use these to coordinate if you are modifying shared dependencies.",
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
