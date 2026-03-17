"""
Configuration for GPU Embedding Service
"""

import os
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Get plugin root directory (parent of this file's parent)
PLUGIN_ROOT = Path(__file__).parent.parent
PLUGIN_CONFIG_FILE = PLUGIN_ROOT / "plugin.json"


def load_plugin_config():
    """Load configuration from plugin.json"""
    try:
        with open(PLUGIN_CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config
    except Exception as e:
        logger.error(f"Failed to load plugin config: {e}")
        return {}


def get_config():
    """Get service configuration with environment variable overrides"""
    plugin_config = load_plugin_config()
    plugin_config_data = plugin_config.get("config", {})

    # Environment variable overrides (PLUGIN_GPU_EMBEDDING_*)
    config = {
        "port": int(
            os.environ.get("PLUGIN_GPU_EMBEDDING_PORT", plugin_config.get("port", 5002))
        ),
        "host": os.environ.get(
            "PLUGIN_GPU_EMBEDDING_HOST", "127.0.0.1"  # localhost only by default
        ),
        "model": os.environ.get(
            "PLUGIN_GPU_EMBEDDING_MODEL",
            plugin_config_data.get("model", "nomic-embed-text"),
        ),
        "use_system_model": os.environ.get(
            "PLUGIN_GPU_EMBEDDING_USE_SYSTEM_MODEL",
            str(plugin_config_data.get("use_system_model", False)),
        ).lower()
        == "true",
        "batch_size": int(
            os.environ.get(
                "PLUGIN_GPU_EMBEDDING_BATCH_SIZE",
                plugin_config_data.get("batch_size", 32),
            )
        ),
        "max_text_length": int(
            os.environ.get(
                "PLUGIN_GPU_EMBEDDING_MAX_TEXT_LENGTH",
                plugin_config_data.get("max_text_length", 8192),
            )
        ),
        "timeout": int(
            os.environ.get(
                "PLUGIN_GPU_EMBEDDING_TIMEOUT", plugin_config_data.get("timeout", 30)
            )
        ),
        "gpu_device": os.environ.get(
            "CUDA_VISIBLE_DEVICES", "0"  # Use first GPU by default
        ),
        "ollama_base_url": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        "debug": os.environ.get("PLUGIN_GPU_EMBEDDING_DEBUG", "false").lower()
        == "true",
    }

    return config


# Global config instance
_config = None


def get_service_config():
    """Get cached service configuration"""
    global _config
    if _config is None:
        _config = get_config()
    return _config
