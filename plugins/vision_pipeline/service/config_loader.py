"""Config loader for Vision Pipeline plugin.

Merge order (highest wins):
1. Guaardvark Settings DB (via HTTP to main backend)
2. config.yaml (plugin-local)
3. plugin.json config section (manifest defaults)
"""
import json
import os
import yaml
import requests
import logging
from dataclasses import dataclass, field
from typing import Optional, List

logger = logging.getLogger("vision_pipeline.config")

# Maps nested YAML keys to flat canonical keys (used by plugin.json and Settings DB)
YAML_TO_CANONICAL = {
    "capture.max_fps": "max_fps",
    "capture.min_fps": "min_fps",
    "capture.quality": "frame_quality",
    "capture.width": "frame_width",
    "capture.format": "frame_format",
    "models.monitor": "monitor_model",
    "models.escalation": "escalation_model",
    "models.auto_select": "auto_select",
    "models.fallback_order": "fallback_order",
    "models.max_parallel": "max_parallel",
    "analysis.change_threshold": "change_threshold",
    "analysis.periodic_refresh_seconds": "periodic_refresh_seconds",
    "analysis.monitor_prompt": "monitor_prompt",
    "analysis.escalation_prompt": "escalation_prompt",
    "analysis.change_detected_prompt": "change_detected_prompt",
    "context.window_seconds": "context_window_seconds",
    "context.max_entries": "max_entries",
    "context.compression_interval": "compression_interval",
    "context.max_context_tokens": "max_context_tokens",
    "gpu.utilization_pause_threshold": "utilization_pause_threshold",
    "gpu.utilization_throttle_threshold": "utilization_throttle_threshold",
    "gpu.contention_behavior": "contention_behavior",
    "streams.max_concurrent": "max_concurrent_streams",
    "streams.stale_timeout_seconds": "stale_timeout_seconds",
}

# Settings DB key → canonical key
SETTINGS_DB_KEYS = {
    "vision_pipeline_max_fps": "max_fps",
    "vision_pipeline_quality": "frame_quality",
    "vision_pipeline_resolution": "frame_width",
    "vision_pipeline_monitor_model": "monitor_model",
    "vision_pipeline_escalation_model": "escalation_model",
    "vision_pipeline_auto_select": "auto_select",
}


@dataclass
class PipelineConfig:
    # Capture
    max_fps: float = 2.0
    min_fps: float = 0.25
    frame_quality: int = 70
    frame_width: int = 512
    frame_format: str = "jpeg"
    # Models
    monitor_model: str = "moondream"
    escalation_model: str = "llava:13b"
    auto_select: bool = True
    fallback_order: List[str] = field(default_factory=lambda: ["moondream", "llava:7b", "llava:latest", "bakllava"])
    max_parallel: int = 1
    # Analysis
    change_threshold: float = 0.3
    periodic_refresh_seconds: int = 10
    monitor_prompt: str = "Describe what you see in one brief sentence."
    escalation_prompt: str = "Describe this image in detail including objects, text, people, actions, and anything notable."
    change_detected_prompt: str = "Describe what changed compared to the previous scene."
    # Context
    context_window_seconds: int = 30
    max_entries: int = 60
    compression_interval: int = 15
    max_context_tokens: int = 500
    # GPU
    utilization_pause_threshold: int = 90
    utilization_throttle_threshold: int = 75
    contention_behavior: str = "min_fps"
    # Streams
    max_concurrent_streams: int = 2
    stale_timeout_seconds: int = 60
    # Service
    service_url: str = "http://localhost:8201"
    ollama_url: str = "http://localhost:11434"


def _flatten_yaml(data: dict, prefix: str = "") -> dict:
    """Flatten nested YAML dict to dot-notation keys."""
    flat = {}
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten_yaml(value, full_key))
        else:
            flat[full_key] = value
    return flat


def load_config(plugin_root: str, backend_url: str = "http://localhost:5002") -> PipelineConfig:
    """Load config with three-tier merge: plugin.json → config.yaml → Settings DB."""
    config = PipelineConfig()

    # Layer 1: plugin.json defaults
    plugin_json_path = os.path.join(plugin_root, "plugin.json")
    if os.path.exists(plugin_json_path):
        with open(plugin_json_path) as f:
            manifest = json.load(f)
        for key, value in manifest.get("config", {}).items():
            if hasattr(config, key):
                setattr(config, key, value)

    # Layer 2: config.yaml overrides
    config_yaml_path = os.path.join(plugin_root, "config.yaml")
    if os.path.exists(config_yaml_path):
        with open(config_yaml_path) as f:
            yaml_data = yaml.safe_load(f) or {}
        flat = _flatten_yaml(yaml_data)
        for yaml_key, canonical_key in YAML_TO_CANONICAL.items():
            if yaml_key in flat and hasattr(config, canonical_key):
                setattr(config, canonical_key, flat[yaml_key])

    # Layer 3: Settings DB overrides (best-effort)
    try:
        resp = requests.get(f"{backend_url}/api/settings/rag-features", timeout=2)
        if resp.status_code == 200:
            settings = resp.json().get("data", {})
            for db_key, canonical_key in SETTINGS_DB_KEYS.items():
                if db_key in settings and hasattr(config, canonical_key):
                    value = settings[db_key]
                    # Type coerce to match dataclass field type
                    current = getattr(config, canonical_key)
                    if isinstance(current, float):
                        value = float(value)
                    elif isinstance(current, int):
                        value = int(value)
                    elif isinstance(current, bool):
                        value = str(value).lower() in ("true", "1", "yes")
                    setattr(config, canonical_key, value)
    except Exception:
        logger.debug("Could not reach Settings DB — using file-based config only")

    return config
