"""Config loader for Upscaling plugin.

Reads plugin.json config section and provides a typed dataclass.
"""
import json
import logging
import os
from dataclasses import dataclass
from typing import Sequence

logger = logging.getLogger("upscaling.config")


@dataclass
class UpscalingConfig:
    # Service
    port: int = 8202
    service_url: str = "http://localhost:8202"
    timeout: int = 30
    fallback_enabled: bool = False
    # Model
    default_model: str = "RealESRGAN_x4plus"
    precision: str = "bf16"
    compile_model: bool = True
    max_tile_size: str = "auto"
    batch_size: str = "auto"
    # Jobs
    job_timeout_minutes: int = 30
    # Watch folder
    watch_folder_enabled: bool = False
    watch_input_dir: str = ""
    watch_output_dir: str = ""
    target_width: int = 3840
    # Callback
    callback_url: str = ""


def resolve_default_model(configured: str, installed: Sequence[str]) -> str:
    """The model a request that names none runs on: the configured default when
    it is installed, else the first installed model, else the configured
    default, so the refusal names what to install."""
    if configured in installed or not installed:
        return configured
    return installed[0]


def load_config(plugin_root: str) -> UpscalingConfig:
    """Load config from plugin.json config section."""
    config = UpscalingConfig()
    plugin_json_path = os.path.join(plugin_root, "plugin.json")

    if os.path.exists(plugin_json_path):
        try:
            with open(plugin_json_path) as f:
                manifest = json.load(f)
            for key, value in manifest.get("config", {}).items():
                if hasattr(config, key):
                    setattr(config, key, value)
        except Exception as e:
            logger.warning(f"Failed to load plugin.json: {e}")

    # Resolve watch dirs relative to GUAARDVARK_ROOT (or plugin_root as fallback)
    project_root = os.environ.get("GUAARDVARK_ROOT", os.path.dirname(os.path.dirname(plugin_root)))
    for attr in ("watch_input_dir", "watch_output_dir"):
        path = getattr(config, attr, "")
        if path and not os.path.isabs(path):
            setattr(config, attr, os.path.join(project_root, path))

    return config
