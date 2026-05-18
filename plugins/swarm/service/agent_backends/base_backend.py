"""
Base backend — the interface all agent backends implement.

Each backend knows how to spawn a specific AI coding tool (Claude Code,
Cline, etc.) in a worktree and monitor its progress. The orchestrator
doesn't care which backend it's talking to — it just calls these methods.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..models import AgentStatus, SwarmTask

logger = logging.getLogger("swarm.backend")


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

    def estimate_cost(self, process: AgentProcess) -> tuple[int, float]:
        """
        Estimate tokens used and cost in USD.

        Returns (token_count, cost_usd).
        Override this if your backend can track actual token usage.
        Default returns zeros — honest about what we don't know.
        """
        return (0, 0.0)

    def is_available(self) -> bool:
        """
        Check if this backend is usable right now.

        Override to check if the command exists, model is pulled, etc.
        """
        import shutil
        return shutil.which(self.name) is not None
