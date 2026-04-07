"""
Swarm Orchestrator — the brain that runs the whole show.

Takes a plan, creates worktrees, spawns agents, monitors progress,
handles completions, triggers merges, and tracks costs. This is the
one file that ties everything together.

Usage:
    orch = SwarmOrchestrator("/path/to/repo", config)
    result = orch.launch("plan.md")
    # or for non-blocking:
    orch.launch_async("plan.md")
    while orch.is_running():
        status = orch.get_status()
        time.sleep(5)
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .config import SwarmConfig, check_internet
from .merge_manager import MergeManager
from .models import (
    AgentStatus,
    ConflictWarning,
    SwarmResult,
    SwarmStatus,
    SwarmTask,
    TimelineEvent,
    generate_swarm_id,
)
from .plan_parser import auto_serialize_conflicts, parse_plan, predict_conflicts
from .worktree_manager import WorktreeManager

logger = logging.getLogger("swarm.orchestrator")

# how often we check on running agents (seconds)
POLL_INTERVAL = 5


class SwarmOrchestrator:
    """
    Orchestrates a swarm of AI coding agents.

    Create one per swarm run. It owns the lifecycle from plan parsing
    through merge completion.
    """

    def __init__(self, repo_path: str | Path, config: SwarmConfig):
        self.repo_path = Path(repo_path).resolve()
        self.config = config

        # state — populated during launch
        self.swarm_id: str | None = None
        self.result: SwarmResult | None = None
        self.worktree_mgr: WorktreeManager | None = None
        self.merge_mgr: MergeManager | None = None

        # backend instances, keyed by name
        self._backends: dict[str, Any] = {}
        self._init_backends()

        # running agent processes, keyed by task_id
        self._processes: dict[str, Any] = {}

        # threading for async operation
        self._thread: threading.Thread | None = None
        self._cancel_event = threading.Event()

        # event callbacks — the future UI hooks into these
        self._on_event: Callable[[TimelineEvent], None] | None = None

    def launch(
        self,
        plan_path: str | Path,
        flight_mode: bool | None = None,
        max_agents: int | None = None,
        auto_merge: bool | None = None,
        dry_run: bool = False,
    ) -> SwarmResult:
        """
        Launch a swarm synchronously. Blocks until all agents complete.

        For non-blocking operation, use launch_async().
        """
        plan_path = Path(plan_path)
        fm = flight_mode if flight_mode is not None else self.config.flight_mode
        max_a = max_agents if max_agents is not None else self.config.max_concurrent_agents
        do_merge = auto_merge if auto_merge is not None else self.config.auto_merge

        # generate swarm identity
        self.swarm_id = generate_swarm_id()
        logger.info(f"Starting swarm {self.swarm_id} from {plan_path}")

        # parse the plan
        tasks = parse_plan(plan_path)
        if not tasks:
            raise ValueError("Plan produced no tasks — nothing to do")

        logger.info(f"Parsed {len(tasks)} tasks from plan")

        # check connectivity
        online = not fm and check_internet(
            self.config.offline_ping_target,
            self.config.offline_ping_timeout,
        )

        mode_str = "FLIGHT MODE (offline)" if (fm or not online) else "online"
        logger.info(f"Operating in {mode_str}")

        if not online and not fm and self.config.auto_fallback:
            logger.info("No internet detected — auto-falling back to offline backends")
            fm = True

        # conflict prediction
        warnings = predict_conflicts(tasks)
        if warnings:
            if fm:
                # flight mode: auto-serialize conflicts, no questions asked
                tasks = auto_serialize_conflicts(tasks, warnings)
                logger.info(f"Auto-serialized {len(warnings)} potential conflicts for Flight Mode")
            else:
                # log warnings — interactive resolution happens at CLI/UI level
                for w in warnings:
                    logger.warning(
                        f"Potential conflict: {w.task_a_id} <-> {w.task_b_id} "
                        f"on files: {', '.join(w.overlapping_files)} "
                        f"(recommendation: {w.recommendation})"
                    )

        # initialize result tracking
        self.result = SwarmResult(
            swarm_id=self.swarm_id,
            plan_path=str(plan_path),
            tasks=tasks,
            started_at=time.time(),
            flight_mode=fm or not online,
        )

        if dry_run:
            logger.info("Dry run — not launching agents")
            self._emit_event("dry_run", "swarm", {"tasks": len(tasks), "warnings": len(warnings)})
            return self.result

        # set up worktree manager
        self.worktree_mgr = WorktreeManager(
            self.repo_path, self.swarm_id, self.config.worktree_base,
        )

        # set up merge manager
        self.merge_mgr = MergeManager(
            self.repo_path, self.worktree_mgr.base_branch,
        )

        self._emit_event("swarm_started", "swarm", {
            "swarm_id": self.swarm_id,
            "task_count": len(tasks),
            "flight_mode": fm or not online,
            "max_agents": max_a,
        })

        # mark tasks with unmet deps as blocked
        self._update_blocked_status(tasks)

        try:
            # main orchestration loop
            self._run_loop(tasks, online=(not fm and online), max_agents=max_a)
        except Exception as e:
            logger.error(f"Swarm failed: {e}", exc_info=True)
            self._emit_event("swarm_error", "swarm", {"error": str(e)})
        finally:
            self.result.completed_at = time.time()

        # merge phase
        if do_merge and self.merge_mgr:
            self._run_merge_phase(tasks)

        # final summary
        logger.info(self.result.summary())
        self._emit_event("swarm_completed", "swarm", {
            "summary": self.result.summary(),
            "cost_usd": self.result.total_cost_usd,
            "tokens": self.result.total_tokens,
        })

        # save the result to disk
        self._save_result()

        return self.result

    def launch_async(self, plan_path: str | Path, **kwargs) -> str:
        """
        Launch a swarm in a background thread. Returns the swarm ID immediately.

        Validates the plan synchronously first — if it can't parse, fail fast
        instead of leaving a broken swarm in the active list.
        """
        self.swarm_id = generate_swarm_id()

        # validate the plan before spawning the thread — fail fast
        plan_path = Path(plan_path)
        tasks = parse_plan(plan_path)  # raises ValueError if no tasks found
        logger.info(f"Plan validated: {len(tasks)} tasks from {plan_path}")

        self._error: str | None = None

        def _run():
            try:
                self.launch(plan_path, **kwargs)
            except Exception as e:
                self._error = str(e)
                logger.error(f"Async swarm failed: {e}", exc_info=True)

        self._thread = threading.Thread(target=_run, name=f"swarm-{self.swarm_id}", daemon=True)
        self._thread.start()
        return self.swarm_id

    def cancel(self) -> None:
        """Cancel a running swarm. Kills all agents and cleans up."""
        logger.info(f"Cancelling swarm {self.swarm_id}")
        self._cancel_event.set()

        # kill all running agents
        for task_id, process in list(self._processes.items()):
            backend = self._backends.get(process.backend_name)
            if backend:
                backend.kill(process)
            task = self._find_task(task_id)
            if task:
                task.status = SwarmStatus.CANCELLED

        self._emit_event("swarm_cancelled", "swarm", {"swarm_id": self.swarm_id})

    def is_running(self) -> bool:
        """Is the swarm still running?"""
        if self._thread:
            return self._thread.is_alive()
        return False

    def get_status(self) -> dict[str, Any]:
        """
        Get current swarm status — the thing dashboards will poll.

        Returns a dict with everything the UI needs to render the state.
        """
        if not self.result:
            return {
                "swarm_id": self.swarm_id,
                "status": "failed" if getattr(self, "_error", None) else "not_started",
                "error": getattr(self, "_error", None),
                "tasks": [],
                "tasks_by_status": {},
                "running_count": 0,
                "total_cost_usd": 0,
                "total_tokens": 0,
                "elapsed_seconds": 0,
                "disk_usage_mb": 0,
                "flight_mode": False,
            }

        running = [t for t in self.result.tasks if t.status == SwarmStatus.RUNNING]
        disk_mb = self.worktree_mgr.disk_usage_mb() if self.worktree_mgr else 0

        return {
            "swarm_id": self.swarm_id,
            "status": "running" if self.is_running() else "completed",
            "flight_mode": self.result.flight_mode,
            "tasks": [t.to_dict() for t in self.result.tasks],
            "tasks_by_status": self.result.tasks_by_status,
            "running_count": len(running),
            "total_cost_usd": self.result.total_cost_usd,
            "total_tokens": self.result.total_tokens,
            "elapsed_seconds": (time.time() - self.result.started_at) if self.result.started_at else 0,
            "disk_usage_mb": round(disk_mb, 1),
        }

    def get_task_logs(self, task_id: str, lines: int = 50) -> str:
        """Get logs for a specific agent."""
        process = self._processes.get(task_id)
        if not process:
            return f"(no running process for task {task_id})"

        backend = self._backends.get(process.backend_name)
        if not backend:
            return "(backend not found)"

        return backend.get_logs(process, lines)

    def on_event(self, callback: Callable[[TimelineEvent], None]) -> None:
        """Register a callback for swarm events. The UI wires in here."""
        self._on_event = callback

    # -------------------------------------------------------------------
    # Main orchestration loop
    # -------------------------------------------------------------------

    def _run_loop(self, tasks: list[SwarmTask], online: bool, max_agents: int) -> None:
        """
        The core loop. Launches tasks as slots open up, monitors running
        agents, handles completions.
        """
        while not self._cancel_event.is_set():
            # check on running agents
            self._poll_running_agents()

            # find tasks ready to launch (deps met, not already running)
            ready = [
                t for t in tasks
                if t.status in (SwarmStatus.PENDING, SwarmStatus.QUEUED)
                and self._deps_met(t, tasks)
            ]

            # how many slots do we have?
            running_count = sum(1 for t in tasks if t.status == SwarmStatus.RUNNING)
            available_slots = max_agents - running_count

            # launch tasks to fill available slots
            for task in ready[:available_slots]:
                try:
                    self._launch_task(task, online)
                except Exception as e:
                    logger.error(f"Failed to launch task {task.id}: {e}")
                    task.status = SwarmStatus.FAILED
                    task.error = str(e)
                    self._emit_event("task_failed", task.id, {"error": str(e)})

            # are we done?
            all_terminal = all(
                t.status in (SwarmStatus.DONE, SwarmStatus.FAILED, SwarmStatus.MERGED,
                             SwarmStatus.NEEDS_REVIEW, SwarmStatus.CANCELLED)
                for t in tasks
            )
            if all_terminal:
                logger.info("All tasks have reached terminal state")
                break

            # check for deadlock — everything is blocked but nothing is running
            all_blocked_or_terminal = all(
                t.status in (SwarmStatus.BLOCKED, SwarmStatus.DONE, SwarmStatus.FAILED,
                             SwarmStatus.MERGED, SwarmStatus.NEEDS_REVIEW, SwarmStatus.CANCELLED)
                for t in tasks
            )
            if all_blocked_or_terminal and running_count == 0:
                logger.error("Deadlock detected — all remaining tasks are blocked with nothing running")
                for t in tasks:
                    if t.status == SwarmStatus.BLOCKED:
                        t.status = SwarmStatus.FAILED
                        t.error = "Deadlocked — dependency never completed"
                break

            time.sleep(POLL_INTERVAL)

    def _launch_task(self, task: SwarmTask, online: bool) -> None:
        """Create a worktree and spawn an agent for a single task."""
        # select backend
        backend_config = self.config.select_backend(task.preferred_backend, online=online)
        if not backend_config:
            configured = list(self.config.backends.keys())
            import shutil
            installed = [n for n in configured if shutil.which(self.config.backends[n].command)]
            raise RuntimeError(
                f"No backend available for {'offline' if not online else 'online'} mode. "
                f"Configured: {configured}. Installed: {installed or 'none'}. "
                f"{'Install cline/openclaw for offline mode, or disable flight mode.' if not online else ''}"
            )

        backend = self._backends.get(backend_config.name)
        if not backend:
            raise RuntimeError(f"Backend '{backend_config.name}' not initialized")

        # create worktree
        wt_info = self.worktree_mgr.create(task.id)
        task.branch_name = wt_info.branch_name
        task.worktree_path = wt_info.worktree_path
        task.backend_name = backend_config.name

        # spawn the agent
        config_dict = {
            "command": backend_config.command,
            "args": backend_config.args,
            "model": backend_config.model,
        }
        process = backend.spawn(wt_info.worktree_path, task, config_dict)

        task.status = SwarmStatus.RUNNING
        task.started_at = time.time()
        task.agent_pid = process.pid
        self._processes[task.id] = process

        logger.info(f"Launched task '{task.id}' on {backend_config.name} (branch: {wt_info.branch_name})")
        self._emit_event("task_spawned", task.id, {
            "backend": backend_config.name,
            "branch": wt_info.branch_name,
            "worktree": wt_info.worktree_path,
        })

    def _poll_running_agents(self) -> None:
        """Check on all running agents and update their status."""
        for task_id, process in list(self._processes.items()):
            backend = self._backends.get(process.backend_name)
            if not backend:
                continue

            prev_status = process.status
            new_status = backend.check_status(process)

            if new_status == prev_status:
                continue

            task = self._find_task(task_id)
            if not task:
                continue

            if new_status == AgentStatus.FINISHED:
                task.status = SwarmStatus.DONE
                task.completed_at = time.time()

                # grab cost/token estimates
                tokens, cost = backend.estimate_cost(process)
                task.token_count = tokens
                task.estimated_cost_usd = cost

                logger.info(
                    f"Task '{task_id}' completed in {task.elapsed_human} "
                    f"(tokens={tokens:,}, cost=${cost:.2f})"
                )
                self._emit_event("task_completed", task_id, {
                    "elapsed": task.elapsed_human,
                    "tokens": tokens,
                    "cost_usd": cost,
                })

            elif new_status == AgentStatus.CRASHED:
                task.status = SwarmStatus.FAILED
                task.completed_at = time.time()
                task.error = "Agent process crashed"

                logger.warning(f"Task '{task_id}' crashed after {task.elapsed_human}")
                self._emit_event("task_failed", task_id, {
                    "error": "Agent process crashed",
                    "elapsed": task.elapsed_human,
                })

    def _run_merge_phase(self, tasks: list[SwarmTask]) -> None:
        """Merge completed branches in dependency order."""
        merge_queue = self.merge_mgr.merge_queue(tasks)

        if not merge_queue:
            logger.info("No tasks ready for merge")
            return

        logger.info(f"Starting merge phase: {len(merge_queue)} branches to merge")

        for task in merge_queue:
            self._emit_event("merge_attempted", task.id, {"branch": task.branch_name})

            merge_result = self.merge_mgr.attempt_merge(
                task,
                run_tests=self.config.run_tests_before_merge,
                test_command=self.config.test_command,
            )

            self.result.merge_results[task.id] = merge_result

            if merge_result.success:
                logger.info(f"Merged {task.id}")
                self._emit_event("merge_succeeded", task.id, {})
            else:
                logger.warning(
                    f"Merge failed for {task.id}: {merge_result.error} "
                    f"(conflicts: {', '.join(merge_result.conflict_files)})"
                )
                self._emit_event("merge_failed", task.id, {
                    "conflict_files": merge_result.conflict_files,
                    "error": merge_result.error,
                })

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _init_backends(self) -> None:
        """Initialize backend instances from config."""
        from .agent_backends.claude_backend import ClaudeBackend
        from .agent_backends.cline_backend import ClineBackend

        backend_map = {
            "claude": ClaudeBackend,
            "cline": ClineBackend,
        }

        for name in self.config.backends:
            cls = backend_map.get(name)
            if cls:
                self._backends[name] = cls()
                logger.debug(f"Initialized backend: {name}")
            else:
                logger.warning(f"Unknown backend '{name}' in config — skipping")

    def _find_task(self, task_id: str) -> SwarmTask | None:
        if not self.result:
            return None
        return next((t for t in self.result.tasks if t.id == task_id), None)

    def _deps_met(self, task: SwarmTask, all_tasks: list[SwarmTask]) -> bool:
        """Check if all dependencies for a task have completed."""
        if not task.dependencies:
            return True

        for dep_id in task.dependencies:
            dep_task = next((t for t in all_tasks if t.id == dep_id), None)
            if not dep_task:
                continue  # unknown dep — don't block on it
            if dep_task.status not in (SwarmStatus.DONE, SwarmStatus.MERGED):
                return False
        return True

    def _update_blocked_status(self, tasks: list[SwarmTask]) -> None:
        """Mark tasks with unmet deps as BLOCKED."""
        for task in tasks:
            if task.dependencies and not self._deps_met(task, tasks):
                task.status = SwarmStatus.BLOCKED

    def _emit_event(self, event_type: str, task_id: str, data: dict[str, Any]) -> None:
        """Record a timeline event and notify any listeners."""
        event = TimelineEvent(
            timestamp=time.time(),
            task_id=task_id,
            event_type=event_type,
            data=data,
        )

        if self.result:
            self.result.timeline.append(event)

        if self._on_event:
            try:
                self._on_event(event)
            except Exception as e:
                logger.warning(f"Event callback error: {e}")

    def _save_result(self) -> None:
        """Save the swarm result to disk for later inspection/replay."""
        if not self.result or not self.swarm_id:
            return

        result_dir = self.repo_path / self.config.worktree_base / self.swarm_id
        result_dir.mkdir(parents=True, exist_ok=True)

        result_path = result_dir / "result.json"
        with open(result_path, "w") as f:
            json.dump(self.result.to_dict(), f, indent=2)

        logger.info(f"Saved swarm result to {result_path}")
