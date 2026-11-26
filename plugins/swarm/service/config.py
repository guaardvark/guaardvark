"""
Configuration loader for the swarm plugin.

Reads config.yaml, merges CLI overrides, validates the result.
Nothing fancy — just a dict with some convenience methods.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("swarm.config")

# where the plugin lives — everything resolves from here
PLUGIN_DIR = Path(__file__).parent.parent
DEFAULT_CONFIG_PATH = PLUGIN_DIR / "config.yaml"

# Tag values that mean "no preference", as the plan's "Assign to:" line reads them.
NO_PREFERENCE = ("", "any", "auto", "none")


@dataclass
class BackendConfig:
    """Settings for one agent backend (claude, cline, etc.)."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    model: str | None = None
    requires_internet: bool = True
    priority: int = 99
    cost_per_1k_input_tokens: float = 0.0
    cost_per_1k_output_tokens: float = 0.0

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> BackendConfig:
        return cls(
            name=name,
            command=data.get("command", name),
            args=data.get("args", []),
            model=data.get("model"),
            requires_internet=data.get("requires_internet", True),
            priority=data.get("priority", 99),
            cost_per_1k_input_tokens=data.get("cost_per_1k_input_tokens", 0.0),
            cost_per_1k_output_tokens=data.get("cost_per_1k_output_tokens", 0.0),
        )


@dataclass
class SwarmConfig:
    """The whole config, parsed and ready to use."""

    max_concurrent_agents: int = 5
    worktree_base: str = ".swarm-worktrees"
    auto_merge: bool = False
    # The merger agent writes resolved conflict content to source. Default OFF:
    # autonomous conflict resolution must be opted into, and self-repo writes are
    # routed through the guarded_code_service chokepoint (see merger_agent.py).
    enable_merger_agent: bool = False
    # A last pass after a task's retries run out. It runs on the backend that
    # ran the task, so a local task is diagnosed locally, and a backend that
    # needs internet is never used in Flight Mode.
    enable_diagnostic_agent: bool = True
    run_tests_before_merge: bool = True
    test_command: str = "python3 -m pytest"
    flight_mode: bool = False
    # Longest one agent may run before the swarm stops it and marks the task
    # timed_out; 0 turns the limit off. The CLIs have no timeout of their own,
    # so a hung agent otherwise holds its slot (and the swarm) forever. 120 is
    # not measured (no swarm task durations were on record when it was set): a
    # task is one focused change in one worktree, so two hours is a ceiling for
    # a hung CLI rather than a budget for work. Revise it from the elapsed
    # times tasks now report.
    agent_timeout_minutes: float = 120

    backends: dict[str, BackendConfig] = field(default_factory=dict)

    # offline detection. Empty means "don't probe": with no target configured
    # the swarm assumes it is online and lets a backend fail on its own. The
    # previous default pinged api.anthropic.com from every /health poll —
    # a third party learning the machine is up, from a local-first product.
    offline_ping_target: str = ""
    offline_ping_timeout: int = 2
    auto_fallback: bool = True

    # cost
    cost_tracking_enabled: bool = True

    def get_backend_priority_list(self, online: bool = True, check_available: bool = True) -> list[BackendConfig]:
        """Backends sorted by priority, filtered by connectivity and availability."""
        candidates = list(self.backends.values())
        if self.flight_mode or not online:
            candidates = [b for b in candidates if not b.requires_internet]
        if check_available:
            import shutil
            candidates = [b for b in candidates if shutil.which(b.command)]
        candidates.sort(key=lambda b: b.priority)
        return candidates

    def select_backend(self, preferred: str | None, online: bool = True) -> tuple[BackendConfig | None, str]:
        """Pick the backend for a task: (backend, "") or (None, reason).

        A named backend is used or refused, never swapped for another. A task
        that names none runs on the first installed local backend: a hosted
        backend receives the task and the repository's code, so it runs only
        when a task or the launch names it.
        """
        import shutil

        if preferred:
            b = self.backends.get(preferred)
            if b is None:
                configured = ", ".join(self.backends) or "none"
                return None, f"unknown backend (configured: {configured})"
            if b.requires_internet and (self.flight_mode or not online):
                return None, "it needs internet and the swarm is offline"
            if not shutil.which(b.command):
                return None, f"'{b.command}' is not installed"
            return b, ""

        candidates = self.get_backend_priority_list(online=online)
        local = [b for b in candidates if not b.requires_internet]
        if local:
            return local[0], ""
        hosted = [b.name for b in candidates if b.requires_internet]
        if hosted:
            return None, (
                f"no local agent CLI is installed, and a hosted backend ({', '.join(hosted)}) "
                f"runs only when the task or the launch names it, for example [Backend: {hosted[0]}]"
            )
        return None, "no configured backend is installed for this mode"

    def backend_for_task(
        self, task, online: bool = True, default: str | None = None,
    ) -> tuple[BackendConfig | None, str, bool]:
        """The backend a task would run on: (backend, reason it has none, whether
        one was named).

        [Model: ...] and [Backend: ...] tags are read with keys in any letter
        case. A model tag wins over a backend tag, and a tag wins over the
        plan's backend field. ``default`` is the backend the launch named for
        tasks that name none. Whatever is named is used or refused, never
        swapped for another backend.
        """
        tags = {str(k).strip().lower(): str(v).strip() for k, v in task.tags.items()}
        model_tag = tags.get("model", "")
        backend_tag = tags.get("backend", "")
        if model_tag.lower() in NO_PREFERENCE:
            model_tag = ""
        if backend_tag.lower() in NO_PREFERENCE:
            backend_tag = ""

        if model_tag:
            backend, reason = self.select_backend_for_model(model_tag, online=online)
            if not backend:
                reason = f"requested model {model_tag} not available: {reason}"
            return backend, reason, True

        preferred = backend_tag.lower() if backend_tag else task.preferred_backend
        if not preferred and (default or "").strip().lower() not in NO_PREFERENCE:
            preferred = default.strip().lower()
        backend, reason = self.select_backend(preferred, online=online)
        if not backend and preferred:
            reason = f"requested backend {preferred} not available: {reason}"
        return backend, reason, bool(preferred)

    def plan_cannot_run(self, tasks, online: bool = True, default: str | None = None) -> str:
        """Why no task of a plan has a backend to run on, or "" when one does.

        Asked before a launch so a plan that can only fail is refused once,
        instead of every task failing in turn.
        """
        reasons: list[str] = []
        for task in tasks:
            backend, reason, _named = self.backend_for_task(task, online=online, default=default)
            if backend:
                return ""
            if reason not in reasons:
                reasons.append(reason)
        if not reasons:
            return ""
        return f"No task in this plan can run on this machine: {'; '.join(reasons)}."

    def select_backend_for_model(self, model: str, online: bool = True) -> tuple[BackendConfig | None, str]:
        """Pick the backend for a [Model: ...] tag: (backend, "") or (None, reason).

        The tag names a backend, or a model a backend is configured with. Only
        those backends are candidates, tried in priority order; like a named
        backend, the tag is never swapped for the general priority list.
        """
        wanted = model.strip().lower()
        if wanted in self.backends:
            return self.select_backend(wanted, online=online)

        matches = sorted(
            (b for b in self.backends.values() if b.model and wanted in b.model.lower()),
            key=lambda b: b.priority,
        )
        if not matches:
            models = ", ".join(sorted({b.model for b in self.backends.values() if b.model})) or "none"
            return None, f"no configured backend uses it (configured models: {models})"

        reasons = []
        for b in matches:
            chosen, reason = self.select_backend(b.name, online=online)
            if chosen:
                return chosen, ""
            reasons.append(f"{b.name}: {reason}")
        return None, "; ".join(reasons)


def load_config(
    config_path: Path | str | None = None,
    overrides: dict[str, Any] | None = None,
) -> SwarmConfig:
    """
    Load config from YAML, merge overrides from CLI flags / env vars.

    Priority: CLI overrides > env vars > config.yaml > defaults
    """
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH

    raw: dict[str, Any] = {}
    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        logger.info(f"Loaded config from {path}")
    else:
        logger.warning(f"Config not found at {path}, using defaults")

    defaults = raw.get("defaults", {})
    backends_raw = raw.get("backends", {})
    offline_raw = raw.get("offline_detection", {})

    # env var overrides — SWARM_MAX_AGENTS, SWARM_FLIGHT_MODE, etc.
    env_flight = os.environ.get("SWARM_FLIGHT_MODE")
    env_max = os.environ.get("SWARM_MAX_AGENTS")

    config = SwarmConfig(
        max_concurrent_agents=int(env_max) if env_max else defaults.get("max_concurrent_agents", 5),
        worktree_base=defaults.get("worktree_base", ".swarm-worktrees"),
        auto_merge=defaults.get("auto_merge", False),
        enable_merger_agent=defaults.get("enable_merger_agent", False),
        enable_diagnostic_agent=defaults.get("enable_diagnostic_agent", True),
        run_tests_before_merge=defaults.get("run_tests_before_merge", True),
        test_command=defaults.get("test_command", "python3 -m pytest"),
        flight_mode=(env_flight == "1") if env_flight else defaults.get("flight_mode", False),
        agent_timeout_minutes=float(defaults.get("agent_timeout_minutes", 120) or 0),
        offline_ping_target=offline_raw.get("target", ""),
        offline_ping_timeout=offline_raw.get("timeout_seconds", 2),
        auto_fallback=offline_raw.get("auto_fallback", True),
        cost_tracking_enabled=raw.get("cost_tracking", {}).get("enabled", True),
    )

    # parse backends
    for name, bdata in backends_raw.items():
        config.backends[name] = BackendConfig.from_dict(name, bdata)

    # CLI overrides — these come in as a flat dict
    if overrides:
        for key, val in overrides.items():
            if val is None:
                continue
            if hasattr(config, key):
                setattr(config, key, val)

    if not config.backends:
        logger.warning("No backends configured — swarm won't be able to launch agents")

    return config


def check_internet(target: str = "", timeout: int = 2) -> bool:
    """Quick connectivity check against a target the operator chose.

    Returns False if the target can't be reached. With no target there is no
    probe at all and the answer is True: nothing is contacted unless someone
    set `offline_detection.target` in the swarm plugin config."""
    import subprocess

    if not target:
        return True
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), target],
            capture_output=True,
            timeout=timeout + 2,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
