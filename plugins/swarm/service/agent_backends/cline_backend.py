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
from .base_backend import AgentProcess, BaseBackend, agent_env

logger = logging.getLogger("swarm.backend.cline")

DONE_MARKER = ".swarm-status"
LOG_FILE = ".swarm-agent.log"

# Candidate CLI commands, in preference order, for the offline agent backend.
_CLI_CANDIDATES = ["openclaw", "cline", "cline-cli"]

# Cache of probed CLI profiles, keyed by resolved command path/name.
_PROBE_CACHE: dict[str, dict[str, Any]] = {}


def probe_cli(command: str) -> dict[str, Any]:
    """Probe a CLI's `--help` to learn which flags it accepts.

    Runs `[command, '--help']` (PROBE ONLY — never invokes a model) and scans the
    output text for the flags we care about. Returns a profile dict:
        {
            "message_flag": "--message" | "--prompt" | "-p" | None,
            "model_flag":   "--model" | None,
            "ok":           bool,   # probe ran and produced help text
        }
    Result is cached per command for the process lifetime.
    """
    if command in _PROBE_CACHE:
        return _PROBE_CACHE[command]

    profile: dict[str, Any] = {"message_flag": None, "model_flag": None, "ok": False}
    try:
        res = subprocess.run(
            [command, "--help"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        help_text = (res.stdout or "") + "\n" + (res.stderr or "")
        if help_text.strip():
            profile["ok"] = True
            # Message flag preference: --message, then --prompt, then -p.
            if "--message" in help_text:
                profile["message_flag"] = "--message"
            elif "--prompt" in help_text:
                profile["message_flag"] = "--prompt"
            elif "-p" in help_text:
                profile["message_flag"] = "-p"
            if "--model" in help_text:
                profile["model_flag"] = "--model"
    except Exception as e:
        logger.debug(f"probe_cli({command!r}) failed: {e}")

    _PROBE_CACHE[command] = profile
    return profile


def resolve_cli_command(config: dict[str, Any]) -> str | None:
    """Pick the first installed CLI from the config command + known candidates."""
    candidates: list[str] = []
    configured = config.get("command")
    if configured:
        candidates.append(configured)
    for c in _CLI_CANDIDATES:
        if c not in candidates:
            candidates.append(c)
    for c in candidates:
        if shutil.which(c):
            return c
    return None

WRAPPER_SCRIPT = """#!/bin/bash
cd "{worktree_path}"

# A status left by an earlier attempt in this worktree would be read as this
# run's result before it has started.
rm -f .swarm-status

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

    def command_prefix(self, config: dict[str, Any]) -> list[str]:
        model = config.get("model", "ollama/gemma4:e4b")
        extra_args = config.get("args", [])

        # Resolve which CLI is actually installed, then probe its flags so we
        # build an argv the binary understands (openclaw uses different flags
        # from cline). Never invokes a model — probe is `<cmd> --help` only.
        command = resolve_cli_command(config)
        if command:
            profile = probe_cli(command)
        else:
            # Nothing installed — fall back to the configured shape and let the
            # wrapper surface the failure in the log.
            command = config.get("command", "cline")
            profile = {"message_flag": None, "model_flag": None, "ok": False}

        parts = [command]
        if profile.get("ok"):
            model_flag = profile.get("model_flag")
            if model and model_flag:
                parts.extend([model_flag, model])
            parts.extend(extra_args)
            parts.append(profile.get("message_flag") or "--message")
        else:
            # Probe failed — fall back to the original command shape and log it.
            logger.warning(
                f"CLI probe failed for {command!r}; falling back to default 'cline' argv shape"
            )
            if model:
                parts.extend(["--model", model])
            parts.extend(extra_args)
            parts.append("--message")
        return parts

    def spawn(self, worktree_path: str, task: SwarmTask, config: dict[str, Any]) -> AgentProcess:
        wt = Path(worktree_path)
        log_file = wt / LOG_FILE

        # A retry reuses the worktree; the previous attempt's status file would
        # be read as this attempt's result on the next poll.
        try:
            (wt / DONE_MARKER).unlink()
        except FileNotFoundError:
            pass

        model = config.get("model", "ollama/gemma4:e4b")

        prompt = self._build_prompt(task)

        cline_parts = self.command_prefix(config) + [prompt]

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
            env=agent_env(),
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
        """Check if any supported offline CLI is installed."""
        for cmd in _CLI_CANDIDATES:
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
