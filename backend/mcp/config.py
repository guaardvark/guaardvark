"""
MCP server configuration.

Single source of truth: ``data/config/mcp.json`` (optional). Env vars override
anything in the file. Defaults are safe (default-deny on destructive tool
categories, outputs resource enabled read-only).

This is the server that other agents connect to. The backend is also an MCP
*client* of external servers, configured in ``backend/config.py``, and the
server process loads the same ``.env`` and profile. So the server's own
switches have their own names:

  * ``GUAARDVARK_MCP_SERVER_ENABLED`` turns this server off.
    ``GUAARDVARK_MCP_ENABLED`` is the client's switch and is not read here:
    the Creator profile sets it to false, which must not stop the server.
  * ``GUAARDVARK_MCP_SERVER_TIMEOUT`` is this server's per-call ceiling.
    ``GUAARDVARK_MCP_TIMEOUT``, which the client also reads (its default is
    30 s), is used only when the server's own variable is unset.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Tuple

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

# Tools chat gates behind its approval card that MCP clients still get. An MCP
# client asks its user before a call unless told not to, and both declare
# destructiveHint. Over MCP they act only on the fact, preference and note
# entries save_memory writes.
DEFAULT_ALLOW_TOOLS: List[str] = ["update_memory", "delete_memory"]

# Registered tools MCP clients are not offered. inpaint_image runs the same
# edit as edit_image under a second name, kept for chat's slash commands and
# turn router; a client choosing between two names for one operation gains
# nothing.
DEFAULT_DENY_TOOLS: List[str] = ["inpaint_image"]

class Toolset(NamedTuple):
    """A named slice of the exposed tools, for a client that wants one focused server."""
    summary: str
    tools: Tuple[str, ...]


# Toolsets narrow the server after the policy above has applied: a tool the
# policy hides stays hidden, so on a stock install ``desktop`` serves only the
# media player tools. "all" is everything the policy exposes. Each named set
# stays at or under MAX_TOOLSET_SIZE; a tool may sit in more than one.
TOOLSETS: Dict[str, Toolset] = {
    "media": Toolset(
        "images, video, songs, speech and Film Crew productions rendered on this machine",
        ("generate_image", "edit_image", "outpaint_image", "remove_background",
         "generate_animation", "generate_video", "generate_music_video", "analyze_video_style",
         "start_film_crew", "generate_music", "generate_speech", "get_generation_status",
         "schedule_job", "scheduled_jobs"),
    ),
    "knowledge": Toolset(
        "the user's indexed documents, Guaardvark's records and memory",
        ("search_knowledge_base", "list_documents", "get_document_outline", "read_document_section",
         "summarize_corpus", "index_document", "process_file", "find_records",
         "save_memory", "search_memory", "update_memory", "delete_memory"),
    ),
    "research": Toolset(
        "web search, page fetching and reading long sources part by part",
        ("web_search", "fetch_url", "analyze_website", "scan", "note", "read_result"),
    ),
    "code": Toolset(
        "code search, reading and analysis over indexed repositories, and the System Map",
        ("search_codebase", "search_code", "read_code", "list_code_files", "list_code_repositories",
         "read_ast_node", "get_repository_map", "get_dependency_graph", "analyze_code", "codegen",
         "verify_change", "check_inbound_change", "map_codebase", "system_map_query",
         "self_improvement_status"),
    ),
    "content": Toolset(
        "written content and data files, and supervised outreach drafts and posts",
        ("generate_wordpress_content", "generate_enhanced_wordpress_content", "generate_file",
         "generate_csv", "generate_bulk_csv", "outreach_status", "outreach_list_queue",
         "outreach_draft_post", "outreach_reject_draft", "request_publish"),
    ),
    "ops": Toolset(
        "GPU state, logs, render status, scheduled jobs, swarms and self-improvement status",
        ("inspect_gpu", "read_logs", "get_generation_status", "schedule_job", "scheduled_jobs",
         "swarm_status", "self_improvement_status"),
    ),
    "desktop": Toolset(
        "the media player and, where mcp.json allows them, desktop and app control",
        ("media_play", "media_control", "media_volume", "media_status", "gui_screenshot",
         "gui_click", "gui_type", "gui_hotkey", "gui_locate_image", "app_launch", "app_focus",
         "app_list"),
    ),
}
ALL_TOOLSET = "all"
MAX_TOOLSET_SIZE = 15
TOOLSET_ENV = "GUAARDVARK_MCP_TOOLSET"


class UnknownToolset(ValueError):
    """The selected toolset name is not one of TOOLSETS or ``all``."""


def toolset_names() -> List[str]:
    return [*TOOLSETS, ALL_TOOLSET]


def toolsets_with(tool_name: str) -> List[str]:
    """The named toolsets that carry ``tool_name``."""
    return [name for name, toolset in TOOLSETS.items() if tool_name in toolset.tools]


def toolset_members(policy: "ToolPolicy") -> frozenset[str] | None:
    """Tool names the active toolset keeps; None for ``all``.
    Raises ``UnknownToolset`` naming the valid choices and where the bad name came from."""
    name = policy.toolset
    if name == ALL_TOOLSET:
        return None
    if name not in TOOLSETS:
        source = f" (from {policy.toolset_source})" if policy.toolset_source else ""
        raise UnknownToolset(
            f"unknown toolset '{name}'{source}. Choose one of: {', '.join(toolset_names())}."
        )
    return frozenset(TOOLSETS[name].tools)


# Folders under the outputs root that MCP clients may list and read: the ones
# Guaardvark's generators write media and documents into (writer in the
# comment). An entry with a slash serves only that subtree. Nothing else under
# the root is a resource: chat-exports/ (conversation transcripts),
# screenshots/ (agent desktop captures), consent/ (likeness consent records),
# training/ (training-video work built on reference voices), edit_inputs/ and
# upscaling/input/ (files people supplied), tracking/ and other job state, and
# anything a script or an operator put there by hand.
DEFAULT_OUTPUT_FOLDERS: List[str] = [
    "generated_images",      # tools/image_tools.py, services/stills_pipeline.py
    "generated_animations",  # services/animation_generator.py
    "videos",                # music videos, text overlays, video_editor renders
    "audio",                 # Audio Foundry's default output dir (plugin.json)
    "narrations",            # api/voice_api.py
    "storyboards",           # Film Crew storyboard frames (tasks/production_swarm_tasks.py)
    "character_samples",     # Cast Library samples (tasks/character_generation_tasks.py)
    "csv",                   # generate_csv (tools/generation_tools.py)
    "files",                 # generate_file (tools/generation_tools.py)
    "code",                  # codegen (tools/code_tools.py)
    "upscaling/output",      # upscaled images and video (api/upscaling_api.py)
    "video_style",           # style analysis keyframes (services/video_style/jobs.py)
]


@dataclass
class ToolPolicy:
    """What tools this MCP server exposes."""
    # Categories to drop wholesale (name-based category lookup on the registry).
    deny_categories: List[str] = field(default_factory=lambda: list(DEFAULT_DENY_CATEGORIES))
    # Explicit allow/deny by tool name. ``allow`` is an additive override —
    # a name here bypasses deny_categories. ``deny`` wins over everything.
    # A list in mcp.json replaces the default list, as deny_categories does.
    allow: List[str] = field(default_factory=lambda: list(DEFAULT_ALLOW_TOOLS))
    deny: List[str] = field(default_factory=lambda: list(DEFAULT_DENY_TOOLS))
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
    # One of TOOLSETS, or "all". ``load_config`` picks it: --toolset, then
    # GUAARDVARK_MCP_TOOLSET, then server.tools.toolset in mcp.json.
    toolset: str = ALL_TOOLSET
    # The setting that chose it, for messages; empty for the default.
    toolset_source: str = ""


@dataclass
class ResourcePolicy:
    """What resources this MCP server exposes."""
    # Expose ``data/outputs/`` as ``guaardvark://outputs/...`` URIs.
    outputs_enabled: bool = True
    # Chroot for the outputs provider. Never serve files outside this.
    outputs_root: str = "data/outputs"
    # Folders under outputs_root that are served, whole subtree each. Listing
    # and reading both apply it; see DEFAULT_OUTPUT_FOLDERS.
    outputs_folders: List[str] = field(default_factory=lambda: list(DEFAULT_OUTPUT_FOLDERS))
    # Files directly in outputs_root. Bulk CSV generation, CSV task handlers,
    # the task executor and document generation write their results there.
    outputs_root_files: bool = True
    # Largest file resources/read embeds; a bigger one gets a download link instead.
    max_inline_bytes: int = 8 * 1024 * 1024


@dataclass
class MCPConfig:
    # False refuses to start the server (``server.build_server``). Set by
    # ``server.enabled`` in mcp.json or ``GUAARDVARK_MCP_SERVER_ENABLED``.
    enabled: bool = True
    # The setting that turned the server off, for the refusal message.
    disabled_by: str = ""
    server_name: str = "guaardvark"
    tools: ToolPolicy = field(default_factory=ToolPolicy)
    resources: ResourcePolicy = field(default_factory=ResourcePolicy)
    # Per-call timeout in seconds (``GUAARDVARK_MCP_SERVER_TIMEOUT``). Enforced by the
    # tools adapter: the tool keeps running in its worker thread, the caller gets
    # an error that says so. Generation tools queue by default (see
    # ``ToolPolicy.argument_defaults``), so this only has to cover synchronous
    # work such as file processing and retrieval. A call whose arguments carry
    # ``wait_for_result=true`` gets the longer ceiling ``tools_adapter._call_timeout``
    # works out from the two constants below.
    timeout_seconds: int = 120


# Ceiling for calls that asked to wait for a render (``wait_for_result=true``),
# unless the tool's own wait plus ``WAIT_HEADROOM_SECONDS`` is longer.
WAIT_TIMEOUT_SECONDS = 30 * 60

# What the tools adapter allows on top of a tool's own wait (the tool's
# ``MAX_WAIT_S``). A tool that waits answers for itself when its wait runs out
# ("still running (batch X)"), and that answer carries the id the client polls
# with. The tool's clock starts later than the adapter's: the hop to the
# backend, model preflight and queueing come first, and generate_video forwards
# with its wait plus 60 s as the HTTP read timeout. Without a margin the
# adapter's timeout fires first and the id is lost. 120 s covers that 60 s and
# the same again for the connection and the last poll; it is derived from the
# code path, not from a timed render.
WAIT_HEADROOM_SECONDS = 120


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


SERVER_ENABLED_ENV = "GUAARDVARK_MCP_SERVER_ENABLED"
SERVER_TIMEOUT_ENV = "GUAARDVARK_MCP_SERVER_TIMEOUT"
# Read by the backend's MCP client too; the server falls back to it.
SHARED_TIMEOUT_ENV = "GUAARDVARK_MCP_TIMEOUT"


def _as_bool(value: Any) -> bool | None:
    """True or False for a JSON boolean, 0 or 1, or a flag string; None for anything else."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        flag = value.strip().lower()
        if flag in ("true", "1", "yes", "on"):
            return True
        if flag in ("false", "0", "no", "off"):
            return False
    return None


# The smallest per-call timeout accepted. Zero or less makes every call report
# a timeout at once (``asyncio.wait_for`` does not wait at all) while the tool
# runs on, and there is no "no limit" value, so such a setting is refused.
MIN_TIMEOUT_SECONDS = 1


def _flag(value: Any, where: str) -> bool | None:
    """``value`` as a boolean; None, with a warning, when it is not one."""
    flag = _as_bool(value)
    if flag is None:
        logger.warning("%s=%r is not true or false; ignored", where, value)
    return flag


def _timeout(value: Any, where: str, current: int) -> int | None:
    """``value`` as a per-call timeout in seconds; None, with a warning, when it
    is not a whole number or is below ``MIN_TIMEOUT_SECONDS``."""
    try:
        if isinstance(value, bool):
            raise ValueError
        seconds = int(value)
    except (TypeError, ValueError):
        logger.warning("%s=%r is not a whole number of seconds; keeping %d s", where, value, current)
        return None
    if seconds < MIN_TIMEOUT_SECONDS:
        logger.warning(
            "%s=%r is below the %d s minimum, and there is no 'no limit' value; keeping %d s",
            where, value, MIN_TIMEOUT_SECONDS, current,
        )
        return None
    return seconds


def _table(parent: dict, key: str, where: str) -> dict:
    """``parent[key]`` when it is an object. Null, a list or a scalar there is
    reported and read as nothing set."""
    if key not in parent:
        return {}
    value = parent[key]
    if isinstance(value, dict):
        return value
    logger.warning("%s in mcp.json is %s, not an object; using defaults for it",
                   where, "null" if value is None else f"a {type(value).__name__}")
    return {}


def _names(value: Any, where: str) -> List[str] | None:
    """``value`` as a list of names; None, with a warning, when it is not one.
    A single string is one name, never its characters."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    logger.warning("%s in mcp.json is %s, not a list; keeping the default",
                   where, "null" if value is None else f"a {type(value).__name__}")
    return None


def _set_toolset(cfg: MCPConfig, value: Any, source: str) -> None:
    """Select a toolset by name. An empty value leaves the earlier choice; an
    unknown name is kept so ``toolset_members`` refuses it rather than serving more."""
    name = str(value).strip().lower() if value is not None else ""
    if name:
        cfg.tools.toolset = name
        cfg.tools.toolset_source = source


def _apply_file(cfg: MCPConfig, raw: Any) -> None:
    """Write the settings of a parsed mcp.json over ``cfg``. A value of the
    wrong type is reported and skipped; the rest of the file still applies."""
    if not isinstance(raw, dict):
        logger.warning("mcp.json is a %s, not an object; using defaults", type(raw).__name__)
        return
    server = _table(raw, "server", "server")
    if "enabled" in server:
        flag = _flag(server["enabled"], "server.enabled in mcp.json")
        if flag is not None:
            cfg.enabled = flag
            cfg.disabled_by = "" if flag else "server.enabled in data/config/mcp.json"
    if "name" in server:
        cfg.server_name = str(server["name"])
    if "timeout_seconds" in server:
        seconds = _timeout(server["timeout_seconds"], "server.timeout_seconds in mcp.json",
                           cfg.timeout_seconds)
        if seconds is not None:
            cfg.timeout_seconds = seconds

    tools = _table(server, "tools", "server.tools")
    # Per tool, the file's values go over the built-in ones. A file that sets a
    # default for one tool leaves every other tool's defaults in place.
    for tool, values in _table(tools, "argument_defaults", "server.tools.argument_defaults").items():
        if isinstance(values, dict):
            cfg.tools.argument_defaults[str(tool)] = {
                **cfg.tools.argument_defaults.get(str(tool), {}), **values}
        else:
            logger.warning("server.tools.argument_defaults.%s in mcp.json is not an object; ignored", tool)
    for key in ("deny_categories", "allow", "deny"):
        if key in tools:
            names = _names(tools[key], f"server.tools.{key}")
            if names is not None:
                setattr(cfg.tools, key, names)
    for key in ("hide_dangerous", "hide_approval_required"):
        if key in tools:
            flag = _flag(tools[key], f"server.tools.{key} in mcp.json")
            if flag is not None:
                setattr(cfg.tools, key, flag)
    if "toolset" in tools:
        _set_toolset(cfg, tools["toolset"], "server.tools.toolset in data/config/mcp.json")

    resources = _table(server, "resources", "server.resources")
    for key in ("outputs_enabled", "outputs_root_files"):
        if key in resources:
            flag = _flag(resources[key], f"server.resources.{key} in mcp.json")
            if flag is not None:
                setattr(cfg.resources, key, flag)
    if "outputs_root" in resources:
        if isinstance(resources["outputs_root"], str) and resources["outputs_root"].strip():
            cfg.resources.outputs_root = resources["outputs_root"]
        else:
            logger.warning("server.resources.outputs_root in mcp.json is not a path; keeping the default")
    if "outputs_folders" in resources:
        folders = _names(resources["outputs_folders"], "server.resources.outputs_folders")
        if folders is not None:
            cfg.resources.outputs_folders = folders
    if "max_inline_bytes" in resources:
        limit = resources["max_inline_bytes"]
        try:
            if isinstance(limit, bool) or int(limit) < 0:
                raise ValueError
            cfg.resources.max_inline_bytes = int(limit)
        except (TypeError, ValueError):
            logger.warning("server.resources.max_inline_bytes=%r in mcp.json is not a byte count; "
                           "keeping the default", limit)


def load_config(toolset: str | None = None) -> MCPConfig:
    """
    Load MCP config. Precedence (lowest → highest):
      1. Built-in defaults
      2. ``data/config/mcp.json`` (if it exists)
      3. Env vars: ``GUAARDVARK_MCP_SERVER_ENABLED``, ``GUAARDVARK_MCP_TOOLSET``,
         and ``GUAARDVARK_MCP_SERVER_TIMEOUT`` or, when that is unset,
         ``GUAARDVARK_MCP_TIMEOUT``.
      4. ``toolset``, the command line's ``--toolset``.
    Missing keys are fine; we fill with defaults.
    """
    cfg = MCPConfig()
    path = _config_path()

    if path.exists():
        # A broken config file must not stop the server: whatever cannot be
        # read falls back to its default, with a warning that names it.
        try:
            with path.open("r") as fh:
                raw = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not parse %s (%s); using defaults", path, exc)
        else:
            try:
                _apply_file(cfg, raw)
                logger.info("Loaded MCP config from %s", path)
            except Exception as exc:  # noqa: BLE001 - a half-applied file is worse than none
                logger.warning("Could not apply %s (%s: %s); using defaults",
                               path, exc.__class__.__name__, exc)
                cfg = MCPConfig()

    env_enabled = os.environ.get(SERVER_ENABLED_ENV)
    if env_enabled is not None:
        flag = _flag(env_enabled, SERVER_ENABLED_ENV)
        if flag is not None:
            cfg.enabled = flag
            cfg.disabled_by = "" if flag else SERVER_ENABLED_ENV
    for name in (SERVER_TIMEOUT_ENV, SHARED_TIMEOUT_ENV):
        env_timeout = os.environ.get(name)
        if env_timeout is None:
            continue
        seconds = _timeout(env_timeout, name, cfg.timeout_seconds)
        if seconds is None:
            continue
        cfg.timeout_seconds = seconds
        break

    _set_toolset(cfg, os.environ.get(TOOLSET_ENV), TOOLSET_ENV)
    _set_toolset(cfg, toolset, "--toolset")
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
