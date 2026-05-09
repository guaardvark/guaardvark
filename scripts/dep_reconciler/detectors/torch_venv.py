"""Detect-only: warn when isolated-venv plugins haven't been bootstrapped."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class TorchVenvDetector:
    id = "torch_venv_detector"
    name = "Isolated-venv plugin readiness check"

    def __init__(self, repo_root: Path):
        self.root = repo_root

    def detect(self) -> list[str]:
        """Return a list of warning strings; also log them at WARNING level."""
        warnings: list[str] = []
        plugins_dir = self.root / "plugins"
        if not plugins_dir.is_dir():
            return warnings
        for plugin in sorted(plugins_dir.iterdir()):
            if not plugin.is_dir():
                continue
            setup = plugin / "scripts" / "setup_venv.sh"
            if not setup.is_file():
                continue
            # Find any venv-* directories the plugin uses.
            venv_dirs = [d for d in plugin.iterdir() if d.is_dir() and d.name.startswith("venv-")]
            if not venv_dirs:
                msg = (
                    f"{plugin.name} torch venv missing — run "
                    f"plugins/{plugin.name}/scripts/setup_venv.sh"
                )
                logger.warning(msg)
                warnings.append(msg)
                continue
            # Each venv-* must have bin/python
            for v in venv_dirs:
                if not (v / "bin" / "python").is_file():
                    msg = (
                        f"{plugin.name}/{v.name} is incomplete (no bin/python) — "
                        f"re-run plugins/{plugin.name}/scripts/setup_venv.sh"
                    )
                    logger.warning(msg)
                    warnings.append(msg)
        return warnings
