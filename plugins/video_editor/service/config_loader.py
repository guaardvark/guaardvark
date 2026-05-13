"""Load + merge plugin.json (manifest) and config.yaml (runtime overrides)."""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def project_root() -> Path:
    """Honor GUAARDVARK_ROOT (set by start.sh) — fall back to two-up from this file."""
    env = os.environ.get("GUAARDVARK_ROOT")
    if env:
        return Path(env).resolve()
    return _PLUGIN_ROOT.parent.parent


def load_config() -> dict[str, Any]:
    """Return the merged runtime config."""
    manifest = _read_json(_PLUGIN_ROOT / "plugin.json")
    runtime = _read_yaml(_PLUGIN_ROOT / "config.yaml")

    melt_path = _resolve_melt_path(runtime.get("melt", {}).get("path", ""))
    runtime.setdefault("melt", {})["resolved_path"] = str(melt_path) if melt_path else ""

    return {
        "manifest": manifest,
        "runtime": runtime,
        "paths": {
            "plugin_root": str(_PLUGIN_ROOT),
            "project_root": str(project_root()),
            "mlt_projects": str(_abs(runtime.get("output", {}).get("mlt_projects_dir", "data/outputs/videos/mlt-projects"))),
            "renders": str(_abs(runtime.get("output", {}).get("renders_dir", "data/outputs/videos/editor-renders"))),
        },
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def _abs(p: str) -> Path:
    """Resolve a project-relative path against the project root."""
    pp = Path(p)
    if pp.is_absolute():
        return pp
    return (project_root() / pp).resolve()


def _resolve_melt_path(configured: str) -> Path | None:
    """Find an executable melt — accept the configured path or fall back to PATH."""
    if configured:
        p = Path(configured)
        if p.is_file():
            return p.resolve()
    found = shutil.which("melt")
    return Path(found).resolve() if found else None
