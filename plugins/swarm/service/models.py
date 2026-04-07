"""
Data models for the swarm plugin.

Everything the orchestrator passes around lives here — tasks, results,
timeline events, the whole state machine. No business logic, just shapes.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SwarmStatus(str, Enum):
    """Lifecycle of a single task within a swarm."""
    PENDING = "pending"          # parsed, waiting for deps
    BLOCKED = "blocked"          # has unmet dependencies
    QUEUED = "queued"            # deps met, waiting for an agent slot
    RUNNING = "running"          # agent is working on it
    DONE = "done"                # agent finished, ready for merge
    FAILED = "failed"            # agent crashed or gave up
    NEEDS_REVIEW = "needs_review"  # merge conflict or test failure
    MERGED = "merged"            # branch merged into base
    CANCELLED = "cancelled"      # user killed it


class AgentStatus(str, Enum):
    """What the backend process is doing right now."""
    STARTING = "starting"
    RUNNING = "running"
    FINISHED = "finished"
    CRASHED = "crashed"
    KILLED = "killed"


@dataclass
class SwarmTask:
    """One unit of work in a swarm — maps to one agent, one worktree, one branch."""

    id: str
    title: str
    description: str
    file_scope: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    preferred_backend: str | None = None
    status: SwarmStatus = SwarmStatus.PENDING

    # set when the task is launched
    branch_name: str | None = None
    worktree_path: str | None = None
    backend_name: str | None = None
    agent_pid: int | None = None
    # timing
    started_at: float | None = None
    completed_at: float | None = None

    # cost tracking — because people love knowing what they spent
    token_count: int = 0
    estimated_cost_usd: float = 0.0

    # error info
    error: str | None = None

    @property
    def elapsed_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.completed_at or time.time()
        return end - self.started_at

    @property
    def elapsed_human(self) -> str:
        """Returns something like '3m 42s' instead of a pile of decimals."""
        secs = self.elapsed_seconds
        if secs is None:
            return "-"
        mins, secs = divmod(int(secs), 60)
        if mins > 0:
            return f"{mins}m {secs}s"
        return f"{secs}s"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "file_scope": self.file_scope,
            "dependencies": self.dependencies,
            "preferred_backend": self.preferred_backend,
            "status": self.status.value,
            "branch_name": self.branch_name,
            "worktree_path": self.worktree_path,
            "backend_name": self.backend_name,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed": self.elapsed_human,
            "token_count": self.token_count,
            "estimated_cost_usd": self.estimated_cost_usd,
            "error": self.error,
        }


@dataclass
class TimelineEvent:
    """Single event in the swarm timeline — the replay system reads these."""

    timestamp: float
    task_id: str
    event_type: str   # spawned, status_change, file_modified, completed, merge_attempted, ...
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "task_id": self.task_id,
            "event_type": self.event_type,
            "data": self.data,
        }


@dataclass
class ConflictWarning:
    """Pre-launch heads-up: these tasks might step on each other."""

    task_a_id: str
    task_b_id: str
    overlapping_files: list[str]
    recommendation: str  # "serialize", "merge_scope", or "proceed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_a": self.task_a_id,
            "task_b": self.task_b_id,
            "overlapping_files": self.overlapping_files,
            "recommendation": self.recommendation,
        }


@dataclass
class MergeResult:
    """Outcome of trying to merge a completed task branch."""

    task_id: str
    success: bool
    conflict_files: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "success": self.success,
            "conflict_files": self.conflict_files,
            "error": self.error,
        }


@dataclass
class SwarmResult:
    """Final report card for a completed swarm run."""

    swarm_id: str
    plan_path: str
    tasks: list[SwarmTask]
    started_at: float
    completed_at: float | None = None
    flight_mode: bool = False
    timeline: list[TimelineEvent] = field(default_factory=list)
    merge_results: dict[str, MergeResult] = field(default_factory=dict)

    @property
    def total_cost_usd(self) -> float:
        return sum(t.estimated_cost_usd for t in self.tasks)

    @property
    def total_tokens(self) -> int:
        return sum(t.token_count for t in self.tasks)

    @property
    def total_elapsed_seconds(self) -> float | None:
        if self.completed_at is None:
            return None
        return self.completed_at - self.started_at

    @property
    def tasks_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for t in self.tasks:
            counts[t.status.value] = counts.get(t.status.value, 0) + 1
        return counts

    def summary(self) -> str:
        """The thing you actually want to read when a swarm finishes."""
        total = len(self.tasks)
        merged = sum(1 for t in self.tasks if t.status == SwarmStatus.MERGED)
        done = sum(1 for t in self.tasks if t.status == SwarmStatus.DONE)
        failed = sum(1 for t in self.tasks if t.status == SwarmStatus.FAILED)
        review = sum(1 for t in self.tasks if t.status == SwarmStatus.NEEDS_REVIEW)

        elapsed = self.total_elapsed_seconds
        time_str = "-"
        if elapsed is not None:
            mins, secs = divmod(int(elapsed), 60)
            time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

        longest = max(
            (t for t in self.tasks if t.elapsed_seconds is not None),
            key=lambda t: t.elapsed_seconds or 0,
            default=None,
        )
        longest_str = (
            f"{longest.elapsed_human} ({longest.id})" if longest else "-"
        )

        mode = " [FLIGHT MODE]" if self.flight_mode else ""
        cost = f"${self.total_cost_usd:.2f}" if self.total_cost_usd > 0 else "free (local)"

        lines = [
            f"Swarm complete{mode}: {total} tasks — {merged} merged, {done} done, {failed} failed, {review} needs review",
            f"Total time: {time_str} | Longest task: {longest_str}",
            f"Total cost: {cost} | Tokens: {self.total_tokens:,}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "swarm_id": self.swarm_id,
            "plan_path": self.plan_path,
            "tasks": [t.to_dict() for t in self.tasks],
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_cost_usd": self.total_cost_usd,
            "total_tokens": self.total_tokens,
            "flight_mode": self.flight_mode,
            "timeline": [e.to_dict() for e in self.timeline],
            "merge_results": {k: v.to_dict() for k, v in self.merge_results.items()},
            "summary": self.summary(),
        }


def generate_swarm_id() -> str:
    """Short, readable swarm ID. Timestamp prefix so they sort nicely."""
    ts = time.strftime("%Y%m%d-%H%M%S")
    short = uuid.uuid4().hex[:6]
    return f"swarm-{ts}-{short}"
