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
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

import psutil
import requests

from .backend_url import backend_api_url
from .config import SwarmConfig, check_internet
from .gpu_hold import GpuHoldClient, ollama_model_of
from .merge_manager import MergeManager
from .models import (
    AgentStatus,
    ConflictWarning,
    SwarmResult,
    SwarmStatus,
    SwarmTask,
    TERMINAL_STATUSES,
    TimelineEvent,
    generate_swarm_id,
)
from .plan_parser import auto_serialize_conflicts, parse_plan, predict_conflicts
from .worktree_manager import WorktreeManager
from .resource_monitor import get_resource_monitor

logger = logging.getLogger("swarm.orchestrator")

# how often we check on running agents (seconds)
POLL_INTERVAL = 5

def _backend_classes() -> dict:
    """Backend name in config.yaml -> the class that runs it."""
    from .agent_backends.claude_backend import ClaudeBackend
    from .agent_backends.cline_backend import ClineBackend

    return {"claude": ClaudeBackend, "cline": ClineBackend}


def backend_installed(name: str, backend_config) -> bool:
    """Whether a configured backend's CLI is on this machine.

    The configured command on PATH, or else the backend's own check (cline
    accepts several CLI names). Never runs a model.
    """
    import shutil

    command = getattr(backend_config, "command", "") or ""
    if command and shutil.which(command):
        return True
    if os.sep in command:
        # A configured path is what the wrapper runs; another CLI of the same
        # name elsewhere on PATH does not stand in for it.
        return False
    cls = _backend_classes().get(name)
    if cls is None:
        return False
    try:
        return bool(cls().is_available())
    except Exception as e:  # noqa: BLE001 - a broken probe means not usable
        logger.debug(f"is_available() failed for backend {name}: {e}")
        return False


# Files the agent backends write into a worktree for their own bookkeeping
# (wrapper script, log, exit status, captured reply); not the agent's work.
_SWARM_OWN_FILES = frozenset({
    ".swarm-run.sh", ".swarm-agent.log", ".swarm-status", ".swarm-result.json", "completion.md"})
# Caches an agent leaves by running its code or tests. In a repository whose
# .gitignore does not list them they show as untracked, and a task that had
# committed its work was sent to review for "uncommitted changes".
_CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})


def _not_the_agents_work(path: str) -> bool:
    """An untracked path that is bookkeeping or a cache, not something the
    agent was asked to write."""
    return (path in _SWARM_OWN_FILES or path.endswith(".pyc")
            or any(part in _CACHE_DIRS for part in path.split("/")))

# Tag values that mean "no preference", as the plan's "Assign to:" line reads them.
_NO_PREFERENCE = ("", "any", "auto", "none")

# Freeze-guard thresholds for the shared 60GB box. Each spawned agent is a
# `claude`/`cline` subprocess that can balloon RAM (and carry "shadow RAM"
# before psutil reflects it). Spawning into < this much free RAM, or any swap
# pressure, is how the box gets driven into the swap-death freeze that locked
# the operator's PC. This is intentionally conservative and CHEAP (pure psutil,
# no GPU/subprocess probes) so it can run on every spawn tick.
SPAWN_MIN_RAM_AVAIL_GB = 6.0
# The same swap floor as the backend's load gate (system_load_gate.py,
# SWAP_HARD_MAX_GB), with the same override. Swap in use is not pressure by
# itself: pages swapped out stay there after the pressure has passed. It was
# 1 GB here, and a machine with 30 GB of RAM available and 8 GB of old swap
# in use never started an agent. The RAM floor above is the working limit.
SPAWN_MAX_SWAP_USED_GB = 8.0
# With nothing running, how long a swarm waits on the guards before its
# waiting tasks fail with the reason, instead of showing "running" for good.
SPAWN_WAIT_LIMIT_S = 600
_GB = 1024 ** 3


def _swap_limit_gb() -> float:
    raw = os.environ.get("GUAARDVARK_SWAP_HARD_MAX_GB", "").strip()
    try:
        return float(raw) if raw else SPAWN_MAX_SWAP_USED_GB
    except ValueError:
        return SPAWN_MAX_SWAP_USED_GB


def _spawn_freeze_guard_block_reason() -> str | None:
    """Return a reason to withhold spawning a new agent subprocess, or None.

    Pure psutil so it never blocks on a missing nvidia-smi or an unreachable
    backend. Mirrors GlobalLoadGate's RAM/swap hard floors
    (backend/services/system_load_gate.py) but local to the swarm plugin, which
    can't assume the backend package is importable from its venv.
    """
    try:
        ram_avail_gb = psutil.virtual_memory().available / _GB
        swap_used_gb = psutil.swap_memory().used / _GB
    except Exception:
        # If we can't read load, don't be the thing that blocks all work.
        return None
    if ram_avail_gb < SPAWN_MIN_RAM_AVAIL_GB:
        return f"RAM available {ram_avail_gb:.1f} GB < {SPAWN_MIN_RAM_AVAIL_GB:.0f} GB floor"
    if swap_used_gb > _swap_limit_gb():
        return f"swap in use {swap_used_gb:.1f} GB > {_swap_limit_gb():.0f} GB"
    return None


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

        # retry tracking
        self._retries: dict[str, int] = {}
        self.max_retries = 2

        # main backend API: merges, GPU holds, event pushes and the
        # diagnostic agent all reach the backend here
        self._backend_url = backend_api_url()

        # GPU holds on the backend's orchestrator for agents running a local
        # Ollama model: task_id -> slot_id, released when the agent exits
        self._gpu: GpuHoldClient | None = None
        self._gpu_holds: dict[str, str] = {}

        # threading for async operation
        self._thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._waiting_on: str | None = None
        self._waiting_since: float | None = None

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

        # Swarm identity. launch_async has already issued one and handed it to
        # the caller, which tracks the swarm under it; keep that id.
        self.swarm_id = self.swarm_id or generate_swarm_id()
        logger.info(f"Starting swarm {self.swarm_id} from {plan_path}")

        # parse the plan
        tasks = parse_plan(plan_path)
        if not tasks:
            raise ValueError("Plan produced no tasks — nothing to do")
        
        for t in tasks:
            t.swarm_id = self.swarm_id

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
            self.repo_path, 
            self.worktree_mgr.base_branch,
            enable_merger_agent=self.config.enable_merger_agent,
            backend_url=self._backend_url
        )

        # Set up inter-agent communication bus
        from .communication_bus import CommunicationBus
        self.comm_bus = CommunicationBus()

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
            # nothing polls the agents past this point, so no hold may outlive it
            for task_id in list(self._gpu_holds):
                self._release_gpu_hold(task_id)

        # merge phase
        if do_merge and self.merge_mgr and not self._cancel_event.is_set():
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
            self._release_gpu_hold(task_id)
            # _processes keeps finished agents too; only a task still running
            # was cancelled.
            task = self._find_task(task_id)
            if task and task.status == SwarmStatus.RUNNING:
                task.status = SwarmStatus.CANCELLED

        self._emit_event("swarm_cancelled", "swarm", {"swarm_id": self.swarm_id})

    def _overall_status(self) -> str:
        """running, cancelled, failed or completed. "completed" means the run
        ended by itself and at least one task finished its work; a run that
        was cancelled, crashed, or in which no task got anywhere is not
        reported as completed."""
        if self.is_running():
            return "running"
        if self._cancel_event.is_set():
            return "cancelled"
        if getattr(self, "_error", None):
            return "failed"
        tasks = self.result.tasks if self.result else []
        finished = {SwarmStatus.DONE, SwarmStatus.MERGED, SwarmStatus.NEEDS_REVIEW, SwarmStatus.NO_CHANGES}
        if tasks and not any(t.status in finished for t in tasks):
            return "failed"
        return "completed"

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
                "repo_path": str(self.repo_path),
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
            "repo_path": str(self.repo_path),
            "status": self._overall_status(),
            "error": getattr(self, "_error", None),
            # Why no new agent is being started right now, when that is so.
            "waiting_on": getattr(self, "_waiting_on", None),
            "flight_mode": self.result.flight_mode,
            "tasks": [t.to_dict() for t in self.result.tasks],
            "tasks_by_status": self.result.tasks_by_status,
            "running_count": len(running),
            "total_cost_usd": self.result.total_cost_usd,
            "total_tokens": self.result.total_tokens,
            "usage_reported": True,
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

    def get_task_diff(self, task_id: str) -> str:
        """The task branch's changes against the commit it started from:
        committed work and uncommitted edits together, plus new untracked files.

        `git diff HEAD` showed only uncommitted edits, so a task whose agent
        committed (as it is told to) always read "no changes".
        """
        if not self.worktree_mgr:
            return "(worktree manager not initialized)"

        info = self.worktree_mgr.manifest.worktrees.get(task_id)
        if not info or not info.worktree_path:
            return f"(no worktree found for task {task_id})"

        import subprocess

        def git(*args):
            return subprocess.run(
                ["git", *args], cwd=info.worktree_path, capture_output=True, text=True, timeout=10,
            )

        try:
            task = self._find_task(task_id)
            base = (task.tags.get("base_head") if task else None) or ""
            if not base:
                # Older runs did not record it: the branch point from the base branch.
                mb = git("merge-base", "HEAD", self.worktree_mgr.manifest.base_branch)
                base = mb.stdout.strip() if mb.returncode == 0 else "HEAD"
            # Working tree against the base commit: commits since then and
            # uncommitted edits to tracked files, in one diff.
            res = git("diff", base)
            if res.returncode != 0:
                return f"Error: git diff failed: {res.stderr.strip()}"
            out = res.stdout
            untracked = git("ls-files", "--others", "--exclude-standard").stdout.split()
            untracked = [p for p in untracked if not _not_the_agents_work(p)]
            if untracked:
                out += ("\n" if out else "") + "# Untracked files (not in the diff):\n" + "\n".join(
                    f"#   {p}" for p in untracked
                ) + "\n"
            if not out.strip():
                return "(no changes in worktree)"
            return out
        except Exception as e:
            logger.warning(f"Failed to get diff for task {task_id}: {e}")
            return f"Error: {e}"

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
            # check on running agents — this is where DONE / FAILED transitions happen
            self._poll_running_agents()

            # Re-evaluate blocked status now that running agents may have
            # completed. Without this call, BLOCKED tasks never unblock even
            # after their parents finish, and the deadlock detector below
            # sweeps them as failed. This is the fix for the "Deadlocked —
            # dependency never completed" bug that bit a 4-task refactor plan
            # where tasks 3 and 4 depended on tasks 1 and 2 — 1 and 2 both
            # completed successfully, but 3 and 4 stayed BLOCKED forever.
            self._update_blocked_status(tasks)

            # find tasks ready to launch (deps met, not already running)
            ready = [
                t for t in tasks
                if t.status in (SwarmStatus.PENDING, SwarmStatus.QUEUED)
                and self._deps_met(t, tasks)
            ]

            # how many slots do we have?
            running_count = sum(1 for t in tasks if t.status == SwarmStatus.RUNNING)
            available_slots = max_agents - running_count

            # Check system health before launching new agents
            monitor = get_resource_monitor()
            held_by = None
            if available_slots > 0 and not monitor.is_healthy():
                logger.warning(f"Swarm throttling: skipping spawn due to high system load")
                available_slots = 0 # force throttle this tick
                held_by = "system load is high (CPU, RAM or free VRAM)"
                self._emit_event("swarm_throttled", "swarm", monitor.get_system_stats())

            # launch tasks to fill available slots
            for task in ready[:available_slots]:
                # Freeze-guard: re-check RAM/swap before EACH spawn (not just
                # once per tick) — earlier spawns this tick may have eaten the
                # headroom. If the box is memory-pressured, throttle: stop
                # spawning this tick and wait for the next poll rather than
                # piling subprocesses on until the machine swaps to a freeze.
                block_reason = _spawn_freeze_guard_block_reason()
                if block_reason is not None:
                    logger.warning(
                        "Swarm freeze-guard: withholding spawn (%s) — throttling", block_reason
                    )
                    self._emit_event("swarm_throttled", "swarm", {"freeze_guard": block_reason})
                    held_by = block_reason
                    break  # try again next poll tick
                try:
                    self._launch_task(task, online)
                except Exception as e:
                    logger.error(f"Failed to launch task {task.id}: {e}")
                    task.status = SwarmStatus.FAILED
                    task.error = str(e)
                    self._emit_event("task_failed", task.id, {"error": str(e)})

            # What the swarm is waiting on, for the status the page shows; a
            # swarm with nothing running does not wait on it for good.
            if held_by and ready:
                self._waiting_since = self._waiting_since or time.time()
                self._waiting_on = held_by
                if running_count == 0 and time.time() - self._waiting_since > SPAWN_WAIT_LIMIT_S:
                    for t in ready:
                        t.status = SwarmStatus.FAILED
                        t.error = (f"Not started: the machine stayed too busy for "
                                   f"{SPAWN_WAIT_LIMIT_S // 60} minutes ({held_by}).")
                        self._emit_event("task_failed", t.id, {"error": t.error})
            else:
                self._waiting_since = None
                self._waiting_on = None

            # are we done?
            all_terminal = all(t.status in TERMINAL_STATUSES for t in tasks)
            if all_terminal:
                logger.info("All tasks have reached terminal state")
                break

            # check for deadlock — everything is blocked but nothing is running
            all_blocked_or_terminal = all(
                t.status == SwarmStatus.BLOCKED or t.status in TERMINAL_STATUSES for t in tasks
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
        preferred = task.preferred_backend
        
        # [Model: ...] and [Backend: ...] tags, keys in any letter case. A model
        # tag wins over a backend tag; either one that names something
        # unavailable fails the task rather than falling back.
        tags = {str(k).strip().lower(): str(v).strip() for k, v in task.tags.items()}
        model_tag = tags.get("model", "")
        backend_tag = tags.get("backend", "")
        if model_tag.lower() in _NO_PREFERENCE:
            model_tag = ""
        if backend_tag.lower() in _NO_PREFERENCE:
            backend_tag = ""

        if model_tag:
            backend_config, reason = self.config.select_backend_for_model(model_tag, online=online)
            if not backend_config:
                raise RuntimeError(f"requested model {model_tag} not available: {reason}")
        else:
            if backend_tag:
                preferred = backend_tag.lower()
            backend_config, reason = self.config.select_backend(preferred, online=online)
            if not backend_config and preferred:
                raise RuntimeError(f"requested backend {preferred} not available: {reason}")
        if not backend_config:
            configured = list(self.config.backends.keys())
            import shutil
            installed = [n for n in configured if shutil.which(self.config.backends[n].command)]
            raise RuntimeError(
                f"No backend available for {'offline' if not online else 'online'} mode: {reason}. "
                f"Configured: {configured}. Installed: {installed or 'none'}. "
                f"{'Install cline/openclaw for offline mode, or disable flight mode.' if not online else ''}"
            )

        backend = self._backends.get(backend_config.name)
        if not backend:
            raise RuntimeError(f"Backend '{backend_config.name}' not initialized")

        # create worktree
        # A task that depends on others starts from their branches, not from
        # the base branch: their work is not merged until the swarm ends.
        dependency_branches = [
            dep.branch_name for dep in (self._find_task(d) for d in (task.dependencies or []))
            if dep is not None and dep.branch_name and dep.status in (SwarmStatus.DONE, SwarmStatus.MERGED)
        ]
        if dependency_branches:
            wt_info = self.worktree_mgr.create(task.id, start_points=dependency_branches)
        else:
            wt_info = self.worktree_mgr.create(task.id)
        left_out = getattr(self.worktree_mgr, "dependency_problems", None)
        if left_out:
            task.tags["dependency_note"] = (
                "Started without the work of " + ", ".join(left_out)
                + ": it did not merge cleanly with the other dependencies.")
        task.branch_name = wt_info.branch_name
        task.worktree_path = wt_info.worktree_path
        task.backend_name = backend_config.name
        try:
            import subprocess
            base_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=wt_info.worktree_path,
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout.strip()
            task.tags["base_head"] = base_head
        except Exception as e:
            logger.debug(f"Could not record base HEAD for {task.id}: {e}")

        # spawn the agent, holding its local model on the GPU while it runs
        config_dict = self._backend_call_config(backend_config)
        self._hold_gpu(task, backend_config)
        try:
            process = backend.spawn(wt_info.worktree_path, task, config_dict)
        except Exception:
            self._release_gpu_hold(task.id)
            raise

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

            if self._stop_if_timed_out(task_id, process, backend):
                continue

            prev_status = process.status
            new_status = backend.check_status(process)

            if new_status == prev_status:
                continue

            task = self._find_task(task_id)
            if not task:
                continue

            if new_status == AgentStatus.FINISHED:
                self._release_gpu_hold(task_id)
                completion_state = self._completion_state(task)
                if completion_state.get("has_uncommitted_diff"):
                    task.status = SwarmStatus.NEEDS_REVIEW
                    task.error = "Agent finished with uncommitted worktree changes"
                elif (
                    completion_state.get("base_head")
                    and "completion_check_error" not in completion_state
                    and not completion_state.get("has_commit")
                ):
                    # A clean exit that left the branch where it started: the
                    # agent produced nothing to merge, so the task did not get done.
                    task.status = SwarmStatus.NO_CHANGES
                    task.error = "Agent exited without committing or changing anything"
                else:
                    task.status = SwarmStatus.DONE
                task.completed_at = time.time()

                # tokens and cost, when the agent CLI reported them
                tokens, cost = backend.estimate_cost(process)
                task.token_count = tokens
                task.estimated_cost_usd = cost

                reported = (f" (tokens={tokens:,}" + (f", cost=${cost:.2f}" if cost > 0 else "") + ")"
                            if tokens > 0 else "")
                logger.info(f"Task '{task_id}' completed in {task.elapsed_human}{reported}")
                self._emit_event("task_completed", task_id, {
                    "elapsed": task.elapsed_human,
                    "tokens": tokens,
                    "cost_usd": cost,
                    **completion_state,
                })

            elif new_status == AgentStatus.CRASHED:
                # Check for retries
                retry_count = self._retries.get(task_id, 0)
                if retry_count < self.max_retries:
                    self._retries[task_id] = retry_count + 1
                    task.status = SwarmStatus.PENDING
                    logger.warning(
                        f"Task '{task_id}' crashed — retrying ({retry_count + 1}/{self.max_retries})"
                    )
                    self._emit_event("task_retrying", task_id, {
                        "attempt": retry_count + 1,
                        "max_attempts": self.max_retries,
                        "reason": "Agent process crashed"
                    })
                else:
                    # Retries exhausted — try one last-ditch diagnosis if enabled
                    diagnostic_agent = self._diagnostic_agent_for(task) if task.worktree_path else None
                    if diagnostic_agent:
                        logger.warning(f"Task '{task_id}' retries exhausted — invoking DiagnosticAgent...")
                        self._emit_event("task_diagnostic_start", task_id, {"reason": "retries_exhausted"})

                        logs = self.get_task_logs(task_id, lines=200)
                        fixed = diagnostic_agent.run_diagnosis(
                            task.worktree_path,
                            task.title,
                            task.description,
                            logs
                        )
                        
                        if fixed:
                            logger.info(f"DiagnosticAgent claims to have fixed '{task_id}'. Resetting status to DONE.")
                            task.status = SwarmStatus.DONE
                            task.completed_at = time.time()
                            self._emit_event("task_diagnostic_success", task_id, {"message": "Agent fixed the issue autonomously"})
                            self._release_gpu_hold(task_id)
                            self._processes.pop(task_id, None)
                            continue

                    task.status = SwarmStatus.FAILED
                    task.completed_at = time.time()
                    task.error = "Agent process crashed (all retries exhausted)"
                    logger.error(f"Task '{task_id}' failed after {retry_count} retries")
                    self._emit_event("task_failed", task_id, {
                        "error": "Agent process crashed (all retries exhausted)",
                        "elapsed": task.elapsed_human,
                    })

                # Cleanup the crashed process record. The hold is kept through
                # the diagnosis above, which runs the same model; a retry takes a new one.
                self._release_gpu_hold(task_id)
                self._processes.pop(task_id, None)

    def _stop_if_timed_out(self, task_id: str, process, backend) -> bool:
        """Stop an agent past agent_timeout_minutes; True when it was stopped."""
        limit_min = float(getattr(self.config, "agent_timeout_minutes", 0) or 0)
        task = self._find_task(task_id)
        if limit_min <= 0 or not task or not task.started_at or task.status != SwarmStatus.RUNNING:
            return False
        elapsed = time.time() - task.started_at
        if elapsed <= limit_min * 60:
            return False
        stopped = False
        try:
            stopped = bool(backend.kill(process))
        except Exception as e:  # noqa: BLE001 - still end the task; say the kill failed
            logger.error(f"Could not stop timed-out agent for '{task_id}': {e}")
        task.status = SwarmStatus.TIMED_OUT
        task.completed_at = time.time()
        task.error = (
            f"Agent ran longer than the {limit_min:g}-minute limit (agent_timeout_minutes) "
            + ("and was stopped" if stopped else "; stopping its process failed")
        )
        logger.warning(f"Task '{task_id}' timed out after {int(elapsed)}s")
        self._emit_event("task_timed_out", task_id, {
            "elapsed": task.elapsed_human,
            "limit_minutes": limit_min,
            "process_stopped": stopped,
        })
        self._release_gpu_hold(task_id)
        self._processes.pop(task_id, None)
        return True

    def _completion_state(self, task: SwarmTask) -> dict[str, object]:
        """Summarize whether a completed task produced committed or uncommitted changes."""
        if not task.worktree_path:
            return {"has_commit": False, "has_uncommitted_diff": False}
        import subprocess
        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=task.worktree_path,
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout.strip()
            diff = subprocess.run(
                ["git", "diff", "--quiet", "HEAD"],
                cwd=task.worktree_path,
                timeout=5,
            )
            base_head = task.tags.get("base_head")
            # New files the agent left uncommitted are work too; the swarm's
            # own files in the worktree (wrapper, log, status) are not.
            untracked = [
                p for p in subprocess.run(
                    ["git", "ls-files", "--others", "--exclude-standard"],
                    cwd=task.worktree_path, capture_output=True, text=True, timeout=5,
                ).stdout.split()
                if not _not_the_agents_work(p)
            ]
            return {
                "base_head": base_head,
                "head": head,
                "has_commit": bool(base_head and head != base_head),
                "has_uncommitted_diff": diff.returncode == 1 or bool(untracked),
                "untracked_files": untracked[:20],
            }
        except Exception as e:
            logger.warning(f"Could not verify completion state for {task.id}: {e}")
            return {"has_commit": False, "has_uncommitted_diff": False, "completion_check_error": str(e)}

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
        backend_map = _backend_classes()

        for name in self.config.backends:
            cls = backend_map.get(name)
            if cls:
                self._backends[name] = cls()
                logger.debug(f"Initialized backend: {name}")
            else:
                logger.warning(f"Unknown backend '{name}' in config — skipping")

    @staticmethod
    def _backend_call_config(backend_config) -> dict[str, Any]:
        """The per-call settings a backend's spawn() and command_prefix() read."""
        return {
            "command": backend_config.command,
            "args": backend_config.args,
            "model": backend_config.model,
        }

    def _gpu_client(self) -> GpuHoldClient:
        if self._gpu is None:
            self._gpu = GpuHoldClient(self._backend_url)
        return self._gpu

    def _hold_gpu(self, task: SwarmTask, backend_config) -> None:
        """Hold the Ollama model this task's agent runs, if it runs one."""
        model = ollama_model_of(backend_config.model)
        if not model or task.id in self._gpu_holds:
            return
        slot_id = self._gpu_client().hold(model)
        if slot_id:
            self._gpu_holds[task.id] = slot_id

    def _release_gpu_hold(self, task_id: str) -> None:
        slot_id = self._gpu_holds.pop(task_id, None)
        if slot_id:
            self._gpu_client().release(slot_id)

    def _diagnostic_agent_for(self, task: SwarmTask):
        """A DiagnosticAgent on the backend that ran this task, or None.

        None when enable_diagnostic_agent is off. It never switches backend,
        so a task that ran locally is diagnosed locally, and a backend that
        needs internet is never used while the swarm is in Flight Mode.
        """
        if not self.config.enable_diagnostic_agent or not task.backend_name:
            return None
        backend_config = self.config.backends.get(task.backend_name)
        backend = self._backends.get(task.backend_name)
        if backend_config is None or backend is None:
            return None
        flight_mode = self.config.flight_mode or bool(self.result and self.result.flight_mode)
        if flight_mode and (backend_config.requires_internet or backend.requires_internet):
            logger.info(
                f"No diagnosis for '{task.id}': {task.backend_name} needs internet "
                f"and the swarm is in Flight Mode"
            )
            return None
        try:
            command = backend.command_prefix(self._backend_call_config(backend_config))
        except NotImplementedError:
            return None
        from .diagnostic_agent import DiagnosticAgent
        return DiagnosticAgent(self._backend_url, command)

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
        """Re-evaluate PENDING/BLOCKED status against current dependency state.

        Bidirectional: a PENDING task whose deps are unmet transitions to
        BLOCKED, and a BLOCKED task whose deps have since completed
        transitions back to PENDING so the main loop's ready filter can
        pick it up. Tasks in RUNNING, DONE, FAILED, MERGED, NEEDS_REVIEW,
        or CANCELLED are left alone — we only touch the two states that
        can legitimately flip based on dependency progress.

        This MUST be called at the top of every _run_loop iteration.
        Calling it only once at startup (the original bug) meant tasks
        were stamped BLOCKED at t=0 and never unblocked even after their
        parents completed successfully. The deadlock detector then swept
        them as failed with "dependency never completed" even though the
        dependency very much had.
        """
        for task in tasks:
            if task.status not in (SwarmStatus.PENDING, SwarmStatus.BLOCKED):
                continue
            deps_ok = self._deps_met(task, tasks)
            if deps_ok and task.status == SwarmStatus.BLOCKED:
                task.status = SwarmStatus.PENDING
            elif not deps_ok and task.status == SwarmStatus.PENDING:
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

        # 1. Internal callback (CLI/local)
        if self._on_event:
            try:
                self._on_event(event)
            except Exception as e:
                logger.warning(f"Event callback error: {e}")

        # 2. Push to main backend for WebSocket broadcast
        try:
            url = f"{self._backend_url}/swarm/event"
            requests.post(url, json={
                "event_type": event_type,
                "task_id": task_id,
                "data": data,
            }, timeout=1)
        except Exception as e:
            # Don't let broadcast failure kill the swarm
            logger.debug(f"Failed to push swarm event to main backend: {e}")

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
