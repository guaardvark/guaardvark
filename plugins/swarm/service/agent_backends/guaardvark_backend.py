"""
Guaardvark backend: the built-in code agent on a local Ollama model.

No outside CLI. Each task runs Guaardvark's own agent loop
(backend/services/worktree_agent.py) as a subprocess in the task's worktree:
it reads, searches, writes and edits files there, runs the tests with no
network, and commits. The only thing it talks to is the local Ollama server,
so it is the backend Flight Mode runs on.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

from ..models import AgentStatus, SwarmTask
from .base_backend import SWARM_OWN_FILES, AgentProcess, BaseBackend, agent_env

logger = logging.getLogger("swarm.backend.guaardvark")

DONE_MARKER = ".swarm-status"
LOG_FILE = ".swarm-agent.log"
RESULT_FILE = ".swarm-result.json"
LAUNCHER = Path(__file__).with_name("guaardvark_agent_main.py")
_OLLAMA_PREFIX = "ollama/"

# How long one answer about the model is reused. The launch dialog and every
# task launch ask; Ollama's model list does not change that fast.
_PROBE_TTL_S = 20.0
_probe_cache: dict[str, tuple[float, str | None, str]] = {}

WRAPPER_SCRIPT = """#!/bin/bash
cd "{worktree_path}"

# A status left by an earlier attempt in this worktree would be read as this
# run's result before it has started.
rm -f .swarm-status

{agent_command} >> "{log_file}" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "SWARM_AGENT_DONE" > .swarm-status
else
    echo "SWARM_AGENT_FAILED:$EXIT_CODE" > .swarm-status
fi
"""


def ollama_base_url() -> str:
    """Where Ollama listens: OLLAMA_BASE_URL, as the backend reads it, else the local default."""
    return (os.environ.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")


def _bare(model: str | None) -> str:
    """``ollama/gemma4:12b`` -> ``gemma4:12b``."""
    text = (model or "").strip()
    return text[len(_OLLAMA_PREFIX):] if text.lower().startswith(_OLLAMA_PREFIX) else text


def _installed_models() -> set[str] | None:
    """The models Ollama has, or None when it does not answer."""
    try:
        response = requests.get(f"{ollama_base_url()}/api/tags", timeout=3)
        response.raise_for_status()
        return {m.get("model") or m.get("name") for m in response.json().get("models", [])} - {None}
    except (requests.RequestException, ValueError):
        return None


def resolve_model(configured: str | None) -> tuple[str | None, str]:
    """(the Ollama model a task would run on, "") or (None, why none).

    The configured model when Ollama has it. Otherwise Guaardvark's own choice
    of an installed chat model that can call tools (worktree_agent
    --resolve-model: same family, the chat model in use, the hardware
    policy's). Cached for a few seconds.
    """
    key = configured or ""
    cached = _probe_cache.get(key)
    if cached and time.monotonic() - cached[0] < _PROBE_TTL_S:
        return cached[1], cached[2]

    wanted = _bare(configured)
    installed = _installed_models()
    if installed is None:
        model, reason = None, f"Ollama does not answer at {ollama_base_url()}"
    elif wanted and wanted in installed:
        model, reason = wanted, ""
    else:
        model, reason = _ask_agent_for_model(wanted)
    _probe_cache[key] = (time.monotonic(), model, reason)
    return model, reason


def _ask_agent_for_model(wanted: str) -> tuple[str | None, str]:
    command = [sys.executable, str(LAUNCHER), "--resolve-model"] + (["--model", wanted] if wanted else [])
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=60, env=agent_env())
        answer = json.loads(done.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as e:
        logger.warning(f"Could not ask the code agent for a model: {e}")
        return None, "Guaardvark's code agent could not be started to choose a model"
    if answer.get("model"):
        if wanted:
            logger.info(f"{wanted} is not installed in Ollama; the code agent runs on {answer['model']}")
        return answer["model"], ""
    return None, answer.get("reason") or "no installed Ollama model can run the code agent"


class GuaardvarkBackend(BaseBackend):
    """Guaardvark's own code agent on a local Ollama model. Offline."""

    name = "guaardvark"
    requires_internet = False

    def unavailable_reason(self, model: str | None = None) -> str:
        """Why this backend cannot run a task now, or ""."""
        return resolve_model(model)[1]

    def is_available(self) -> bool:
        return resolve_model(None)[0] is not None

    def run_model(self, config: dict[str, Any]) -> str | None:
        """The model a task launched now runs on, as ``ollama/<name>``."""
        model = resolve_model(config.get("model"))[0]
        return f"{_OLLAMA_PREFIX}{model}" if model else config.get("model")

    def command_prefix(self, config: dict[str, Any]) -> list[str]:
        parts = [sys.executable, str(LAUNCHER)]
        model = _bare(config.get("model"))
        if model:
            parts += ["--model", model]
        if config.get("test_command"):
            parts += ["--test-command", str(config["test_command"])]
        for name in sorted(SWARM_OWN_FILES):
            parts += ["--not-work", name]
        return parts

    def spawn(self, worktree_path: str, task: SwarmTask, config: dict[str, Any]) -> AgentProcess:
        wt = Path(worktree_path)
        log_file = wt / LOG_FILE
        for stale in (wt / DONE_MARKER, wt / RESULT_FILE):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass

        command = self.command_prefix(config) + ["--result-file", RESULT_FILE]
        base_head = (task.tags or {}).get("base_head")
        if base_head:
            command += ["--base", base_head]
        command.append(self._build_prompt(task))

        wrapper_path = wt / ".swarm-run.sh"
        wrapper_path.write_text(
            WRAPPER_SCRIPT.format(
                worktree_path=worktree_path,
                agent_command=" ".join(_shell_quote(p) for p in command),
                log_file=str(log_file),
            )
        )
        wrapper_path.chmod(0o755)

        proc = subprocess.Popen(
            ["bash", str(wrapper_path)],
            cwd=worktree_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=agent_env(),
        )
        model = _bare(config.get("model"))
        logger.info(f"Launched Guaardvark agent for '{task.id}' (model={model}, pid={proc.pid})")
        return AgentProcess(
            task_id=task.id,
            backend_name=self.name,
            pid=proc.pid,
            worktree_path=worktree_path,
            status=AgentStatus.RUNNING,
            metadata={"model": model, "popen": proc},
        )

    def check_status(self, process: AgentProcess) -> AgentStatus:
        status_file = Path(process.worktree_path) / DONE_MARKER
        if status_file.exists():
            content = status_file.read_text().strip()
            if content == "SWARM_AGENT_DONE":
                process.status = AgentStatus.FINISHED
                return AgentStatus.FINISHED
            if content.startswith("SWARM_AGENT_FAILED"):
                process.status = AgentStatus.CRASHED
                return AgentStatus.CRASHED

        popen = process.metadata.get("popen") if isinstance(process.metadata, dict) else None
        if popen is not None:
            if popen.poll() is None:
                process.status = AgentStatus.RUNNING
                return AgentStatus.RUNNING
            # The wrapper is gone without having written a status: it was killed.
            process.status = AgentStatus.CRASHED
            return AgentStatus.CRASHED

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
        log_file = Path(process.worktree_path) / LOG_FILE
        if not log_file.exists():
            return "(no output yet — agent still starting)"
        try:
            all_lines = log_file.read_text().split("\n")
            return "\n".join(all_lines[-lines:] if len(all_lines) > lines else all_lines)
        except Exception as e:
            return f"(could not read logs: {e})"

    def kill(self, process: AgentProcess) -> bool:
        if process.pid:
            for stop in (lambda: os.killpg(os.getpgid(process.pid), 9), lambda: os.kill(process.pid, 9)):
                try:
                    stop()
                    process.status = AgentStatus.KILLED
                    return True
                except (ProcessLookupError, PermissionError):
                    pass
        return False

    def estimate_cost(self, process: AgentProcess) -> tuple[int, float]:
        """The tokens Ollama counted for the run. A local model costs nothing per token."""
        try:
            report = json.loads((Path(process.worktree_path) / RESULT_FILE).read_text())
            return (int(report.get("prompt_tokens") or 0) + int(report.get("reply_tokens") or 0), 0.0)
        except (OSError, ValueError, TypeError, AttributeError):
            return (0, 0.0)

    def _build_prompt(self, task: SwarmTask) -> str:
        parts = [task.title, "", task.description]
        if task.file_scope:
            parts.append(f"\nWork on these files: {', '.join(task.file_scope)}")
        return "\n".join(parts)


def _shell_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)
