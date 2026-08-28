"""Where ComfyUI actually is.

The plugin manifest carries the default port, and an untracked
plugin.local.json next to it can override it (a Comfy Desktop install on
8000, say). Several backend clients had their own hardcoded ``:8188``, so the
override the docs recommend fixed some pages and not others. Every client
resolves through here instead.

Resolution order: ``GUAARDVARK_COMFYUI_URL`` (an explicit choice always wins),
the plugin's effective port, then the manifest default of 8188.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_COMFYUI_PORT = 8188


def comfyui_port() -> int:
    try:
        from backend.plugins.plugin_manager import get_plugin_manager

        info = get_plugin_manager().get_plugin_info("comfyui")
        port = int(info.get("port") or 0)
        if port > 0:
            return port
    except Exception as e:  # noqa: BLE001 - a missing plugin manager falls back to the default
        logger.debug("comfyui port lookup fell back to default: %s", e)
    return DEFAULT_COMFYUI_PORT


def get_comfyui_url() -> str:
    explicit = (os.environ.get("GUAARDVARK_COMFYUI_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    return f"http://127.0.0.1:{comfyui_port()}"
