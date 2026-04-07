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
    run_tests_before_merge: bool = True
    test_command: str = "python3 -m pytest"
    flight_mode: bool = False

    backends: dict[str, BackendConfig] = field(default_factory=dict)

    # offline detection
    offline_ping_target: str = "api.anthropic.com"
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

    def select_backend(self, preferred: str | None, online: bool = True) -> BackendConfig | None:
        """Pick the best available backend for a task."""
        import shutil

        # explicit preference wins if it's installed and connectivity matches
        if preferred and preferred in self.backends:
            b = self.backends[preferred]
            if (not b.requires_internet or online) and shutil.which(b.command):
                return b
            # they asked for something unavailable — fall through

        candidates = self.get_backend_priority_list(online=online)
        return candidates[0] if candidates else None


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
        run_tests_before_merge=defaults.get("run_tests_before_merge", True),
        test_command=defaults.get("test_command", "python3 -m pytest"),
        flight_mode=(env_flight == "1") if env_flight else defaults.get("flight_mode", False),
        offline_ping_target=offline_raw.get("target", "api.anthropic.com"),
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


def check_internet(target: str = "api.anthropic.com", timeout: int = 2) -> bool:
    """Quick connectivity check. Returns False if we can't reach the target."""
    import subprocess

    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), target],
            capture_output=True,
            timeout=timeout + 2,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
