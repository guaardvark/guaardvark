"""Build the active reconciler list. Stdlib-only top imports."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal


def classify_plugin_venv_mode(plugin_dir: Path) -> Literal["isolated", "shared"]:
    """A plugin is isolated if it has setup_venv.sh OR a venv-*/ directory.

    Isolated plugins are detected-only by TorchVenvDetector and never
    have their requirements installed into the main venv.
    """
    if (plugin_dir / "scripts" / "setup_venv.sh").is_file():
        return "isolated"
    for child in plugin_dir.iterdir() if plugin_dir.is_dir() else []:
        if child.is_dir() and child.name.startswith("venv-"):
            return "isolated"
    return "shared"


def enabled_plugin_ids(plugin_state_path: Path) -> list[str]:
    """Read data/plugin_state.json. Missing/corrupt → empty list."""
    if not plugin_state_path.is_file():
        return []
    try:
        data = json.loads(plugin_state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    return [pid for pid, cfg in data.items()
            if isinstance(cfg, dict) and cfg.get("user_enabled") is True]


def build_active_reconcilers(repo_root: Path) -> list:
    """Return [BackendVenv, Alembic, PluginBundle, Frontend, CliVenv, TorchVenvDetector] in run order.

    Lazy-imports the concrete reconciler classes — they live in submodules
    and we want to keep registry.py importable without dragging them in until
    needed (matters for unit tests that monkeypatch one reconciler).
    """
    # Import here, not at top, to keep registry.py independently testable.
    from scripts.dep_reconciler.reconcilers.backend_venv import BackendVenv
    from scripts.dep_reconciler.reconcilers.alembic import Alembic
    from scripts.dep_reconciler.reconcilers.plugin_bundle import PluginBundle
    from scripts.dep_reconciler.reconcilers.frontend import Frontend
    from scripts.dep_reconciler.reconcilers.cli_venv import CliVenv
    from scripts.dep_reconciler.detectors.torch_venv import TorchVenvDetector

    plugin_state = repo_root / "data" / "plugin_state.json"
    plugins_dir = repo_root / "plugins"

    enabled = enabled_plugin_ids(plugin_state)
    shared_plugins = [
        pid for pid in enabled
        if (plugins_dir / pid).is_dir()
        and classify_plugin_venv_mode(plugins_dir / pid) == "shared"
        and any((plugins_dir / pid).glob("requirements*.txt"))
    ]

    return [
        BackendVenv(repo_root),
        Alembic(repo_root),
        PluginBundle(repo_root, shared_plugins),
        Frontend(repo_root),
        CliVenv(repo_root),
        TorchVenvDetector(repo_root),
    ]
