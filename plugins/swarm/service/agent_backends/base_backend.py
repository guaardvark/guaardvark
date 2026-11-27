"""
Base backend — the interface all agent backends implement.

Each backend knows how to spawn a specific AI coding tool (Claude Code,
Cline, etc.) in a worktree and monitor its progress. The orchestrator
doesn't care which backend it's talking to — it just calls these methods.
"""

from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..models import AgentStatus, SwarmTask

logger = logging.getLogger("swarm.backend")

# An agent CLI runs with its permission prompts off, in a worktree. It gets a
# working shell environment and nothing that unlocks the rest of the install:
# no database URL, no app keys, no service tokens, no SSH agent.
_SECRET_NAME = re.compile(
    r"(?:^|_)(?:KEY|KEYS|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|DSN)(?:_|$)|"
    r"^(?:DATABASE_URL|REDIS_URL|CELERY_BROKER_URL|CELERY_RESULT_BACKEND|SSH_AUTH_SOCK|SSH_AGENT_PID|"
    r"GPG_AGENT_INFO|PGPASSWORD|PGPASSFILE)$",
    re.IGNORECASE,
)


# Files the agent backends write into a worktree for their own bookkeeping
# (wrapper script, log, exit status, the agent's report, captured reply); not
# the agent's work, and never committed.
SWARM_OWN_FILES = frozenset({
    ".swarm-run.sh", ".swarm-agent.log", ".swarm-status", ".swarm-result.json", "completion.md"})


def pid_running(pid: int) -> bool:
    """True while ``pid`` runs. Signal 0 alone also succeeds for a zombie (an
    agent that exited but was never collected), which would show as running
    forever; an agent this service started is collected here instead."""
    try:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except OSError:
        pass  # not our child
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            stat = f.read().decode("ascii", "replace")
        return stat[stat.rfind(")") + 1:].split()[0] not in ("Z", "X")
    except (OSError, IndexError):
        return True


def agent_env(keep: tuple[str, ...] = ()) -> dict[str, str]:
    """The environment an agent CLI is started with: this process's, minus
    anything that names a secret. ``keep`` lists the names a backend needs
    for its own sign-in (its API key)."""
    return {name: value for name, value in os.environ.items()
            if name in keep or not _SECRET_NAME.search(name)}


@dataclass
class AgentProcess:
    """Handle to a running agent — the backend creates these, the orchestrator tracks them."""

    task_id: str
    backend_name: str
    pid: int | None = None
    worktree_path: str = ""
    status: AgentStatus = AgentStatus.STARTING
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseBackend(ABC):
    """
    Interface for agent backends.

    Implement this to add support for a new AI coding tool.
    The orchestrator will call these methods in order:
      1. spawn()   — start the agent
      2. check_status()  — poll until done (called in a loop)
      3. get_logs()  — grab output for display
      4. estimate_cost()  — how much did that cost?
      5. kill()   — if we need to cancel
    """

    name: str = "base"
    requires_internet: bool = True

    @abstractmethod
    def spawn(self, worktree_path: str, task: SwarmTask, config: dict[str, Any]) -> AgentProcess:
        """
        Launch an agent in the given worktree.

        The agent should:
          1. cd into worktree_path
          2. execute the task described in task.description
          3. commit its changes to the worktree's branch

        Returns an AgentProcess handle for tracking.
        """
        ...

    @abstractmethod
    def check_status(self, process: AgentProcess) -> AgentStatus:
        """
        Check if the agent is still running, finished, or crashed.

        Called periodically by the orchestrator's monitor loop.
        """
        ...

    @abstractmethod
    def get_logs(self, process: AgentProcess, lines: int = 50) -> str:
        """Get recent log output from the agent."""
        ...

    @abstractmethod
    def kill(self, process: AgentProcess) -> bool:
        """
        Force-kill the agent process.

        Returns True if successfully killed.
        """
        ...

    def command_prefix(self, config: dict[str, Any]) -> list[str]:
        """The argv that runs this backend's CLI on one prompt, minus the prompt.

        spawn() appends the task prompt; a follow-up pass on the same backend
        (the diagnostic agent) appends its own.
        """
        raise NotImplementedError(f"{self.name} backend cannot run a one-off prompt")

    def run_model(self, config: dict[str, Any]) -> str | None:
        """The model a task launched now runs on. The configured one, unless
        the backend chooses among installed models itself."""
        return config.get("model")

    def unavailable_reason(self, model: str | None = None) -> str:
        """Why this backend cannot run a task on this machine now, or ""."""
        return "" if self.is_available() else f"{self.name} is not installed"

    def estimate_cost(self, process: AgentProcess) -> tuple[int, float]:
        """
        Tokens used and cost in USD, as the agent CLI reported them.

        Returns (token_count, cost_usd). Override this only with figures the
        CLI measured: both are shown to the person as they are. The default
        returns zeros, and nothing is shown.
        """
        return (0, 0.0)

    def is_available(self) -> bool:
        """
        Check if this backend is usable right now.

        Override to check if the command exists, model is pulled, etc.
        """
        import shutil
        return shutil.which(self.name) is not None
