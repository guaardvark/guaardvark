"""
MCP server configuration.

Single source of truth: ``data/config/mcp.json`` (optional). Env vars override
anything in the file. Defaults are safe (default-deny on destructive tool
categories, outputs resource enabled read-only).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Categories kept behind an explicit allow-list. These can touch the user's
# machine, spawn processes, or drive the virtual desktop — not things we let
# a random external MCP client call on sight.
DEFAULT_DENY_CATEGORIES: List[str] = [
    "desktop",         # gui_click, gui_type, app_launch, clipboard_*
    "agent_control",   # agent_task_execute, agent_screen_capture
    "system",          # system_command (shell)
    "test_execution",  # execute_python (arbitrary python)
    "browser",         # puppeteer-style browser driving
    "mcp",             # meta-tools; exposing them creates recursion loops
    "mcp_native",      # native proxies for external MCP servers (postgres, redis, fs, etc.)
                       # must be explicitly allowed; prevents silent bypass of default-deny
]


@dataclass
class ToolPolicy:
    """What tools this MCP server exposes."""
    # Categories to drop wholesale (name-based category lookup on the registry).
    deny_categories: List[str] = field(default_factory=lambda: list(DEFAULT_DENY_CATEGORIES))
    # Explicit allow/deny by tool name. ``allow`` is an additive override —
    # a name here bypasses deny_categories. ``deny`` wins over everything.
    allow: List[str] = field(default_factory=list)
    deny: List[str] = field(default_factory=list)
    # If True, tools with ``is_dangerous=True`` are hidden.
    hide_dangerous: bool = True
    # If True, tools with ``requires_approval=True`` are hidden.
    hide_approval_required: bool = True
    # Argument values applied when an MCP caller omits the key. An MCP client is
    # a remote agent with its own request timeout, so a render that the chat
    # surface waits on inline is queued here and polled with
    # ``get_generation_status`` instead. A caller that passes the key wins.
    argument_defaults: Dict[str, Dict[str, Any]] = field(
        default_factory=lambda: {"generate_image": {"wait_for_result": False}}
    )


@dataclass
class ResourcePolicy:
    """What resources this MCP server exposes."""
    # Expose ``data/outputs/`` as ``guaardvark://outputs/...`` URIs.
    outputs_enabled: bool = True
    # Chroot for the outputs provider. Never serve files outside this.
    outputs_root: str = "data/outputs"


@dataclass
class MCPConfig:
    enabled: bool = True
    server_name: str = "guaardvark"
    tools: ToolPolicy = field(default_factory=ToolPolicy)
    resources: ResourcePolicy = field(default_factory=ResourcePolicy)
    # Per-call timeout in seconds (``GUAARDVARK_MCP_TIMEOUT``). Enforced by the
    # tools adapter: the tool keeps running in its worker thread, the caller gets
    # an error that says so. Generation tools queue by default (see
    # ``ToolPolicy.argument_defaults``), so this only has to cover synchronous
    # work such as file processing and retrieval. A call whose arguments carry
    # ``wait_for_result=true`` is allowed ``WAIT_TIMEOUT_SECONDS`` instead.
    timeout_seconds: int = 120


# Ceiling for calls that asked to wait for a render (``wait_for_result=true``).
WAIT_TIMEOUT_SECONDS = 30 * 60


def _merge(base: dict, override: dict) -> dict:
    """Shallow-merge override into base. Nested dicts get merged one level deep."""
    out = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **val}
        else:
            out[key] = val
    return out


def _config_path() -> Path:
    """Config file lives under the project root's ``data/config/``."""
    return Path(__file__).resolve().parent.parent.parent / "data" / "config" / "mcp.json"


def load_config() -> MCPConfig:
    """
    Load MCP config. Precedence (lowest → highest):
      1. Built-in defaults
      2. ``data/config/mcp.json`` (if it exists)
      3. Env vars: ``GUAARDVARK_MCP_ENABLED``, ``GUAARDVARK_MCP_TIMEOUT``.
    Missing keys are fine; we fill with defaults.
    """
    cfg = MCPConfig()
    path = _config_path()

    if path.exists():
        try:
            with path.open("r") as fh:
                raw = json.load(fh)
            server = raw.get("server", {})
            if "enabled" in server:
                cfg.enabled = bool(server["enabled"])
            if "name" in server:
                cfg.server_name = str(server["name"])
            if "timeout_seconds" in server:
                cfg.timeout_seconds = int(server["timeout_seconds"])

            tools = server.get("tools", {})
            if isinstance(tools.get("argument_defaults"), dict):
                cfg.tools.argument_defaults = {
                    str(k): dict(v) for k, v in tools["argument_defaults"].items() if isinstance(v, dict)
                }
            if "deny_categories" in tools:
                cfg.tools.deny_categories = list(tools["deny_categories"])
            if "allow" in tools:
                cfg.tools.allow = list(tools["allow"])
            if "deny" in tools:
                cfg.tools.deny = list(tools["deny"])
            if "hide_dangerous" in tools:
                cfg.tools.hide_dangerous = bool(tools["hide_dangerous"])
            if "hide_approval_required" in tools:
                cfg.tools.hide_approval_required = bool(tools["hide_approval_required"])

            resources = server.get("resources", {})
            if "outputs_enabled" in resources:
                cfg.resources.outputs_enabled = bool(resources["outputs_enabled"])
            if "outputs_root" in resources:
                cfg.resources.outputs_root = str(resources["outputs_root"])

            logger.info("Loaded MCP config from %s", path)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            # Don't let a broken config file kill the whole server; fall back
            # to defaults and tell whoever's listening.
            logger.warning("Could not parse %s (%s); using defaults", path, exc)

    # Env overrides — these come from backend.config for compatibility.
    env_enabled = os.environ.get("GUAARDVARK_MCP_ENABLED")
    if env_enabled is not None:
        cfg.enabled = env_enabled.lower() == "true"
    env_timeout = os.environ.get("GUAARDVARK_MCP_TIMEOUT")
    if env_timeout is not None:
        try:
            cfg.timeout_seconds = int(env_timeout)
        except ValueError:
            pass

    return cfg


def tool_is_exposed(
    tool_name: str,
    category: str | None,
    is_dangerous: bool,
    requires_approval: bool,
    policy: ToolPolicy,
) -> tuple[bool, str]:
    """
    Policy gate for a single tool. Returns (allowed, reason-if-denied).

    Order of checks matches the principle of least privilege:
      1. Hard ``deny`` list → no.
      2. Explicit ``allow`` list → yes (bypasses category + flag gates).
      3. Safety flags (dangerous / approval).
      4. Category deny-list.
    """
    if tool_name in policy.deny:
        return False, f"tool '{tool_name}' is in deny list"
    if tool_name in policy.allow:
        return True, ""
    if is_dangerous and policy.hide_dangerous:
        return False, f"tool '{tool_name}' is marked dangerous"
    if requires_approval and policy.hide_approval_required:
        return False, f"tool '{tool_name}' requires approval"
    if category and category in policy.deny_categories:
        return False, f"category '{category}' is in deny list"
    return True, ""
