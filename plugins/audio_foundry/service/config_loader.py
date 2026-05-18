"""Load plugin.json + config.yaml and hand back a merged view.

Plugin.json carries the plugin manifest (port, vram, endpoints, top-level toggles).
config.yaml carries runtime-tunable details (backend params, timeouts, queue names).

config.yaml wins when both define the same key — operators edit that file, not the
manifest, to change behavior.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict[str, Any]:
    """Read plugin.json + config.yaml from the plugin root, merged.

    Returns a dict shaped like:
        {
          "manifest": {... plugin.json ...},
          "runtime":  {... config.yaml ...},
        }

    Neither file is required to exist; missing files produce empty dicts.
    """
    manifest: dict[str, Any] = {}
    runtime: dict[str, Any] = {}

    manifest_path = PLUGIN_ROOT / "plugin.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except json.JSONDecodeError as e:
            logger.error("plugin.json is not valid JSON: %s", e)

    runtime_path = PLUGIN_ROOT / "config.yaml"
    if runtime_path.exists():
        try:
            runtime = yaml.safe_load(runtime_path.read_text()) or {}
        except yaml.YAMLError as e:
            logger.error("config.yaml is not valid YAML: %s", e)

    return {"manifest": manifest, "runtime": runtime}
