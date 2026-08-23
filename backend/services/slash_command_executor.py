"""Slash command → direct tool execution map.

Slash commands (/imagine, /websearch, …) must call the pertaining registry tool
directly — not rewrite the user message into an LLM chat prompt.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Single source of truth: slash command name (no leading /) → registry tool name
SLASH_COMMAND_TOOL_MAP: Dict[str, str] = {
    "imagine": "generate_image",
    "websearch": "web_search",
    # CLI-only today; browser handler can be added later
    "video": "generate_animation",
    "remember": "save_memory",
    "gpu": "inspect_gpu",
    "logs": "read_logs",
    "sysmap": "map_codebase",
    "swarm": "swarm_status",
}


def resolve_slash_direct_tool(
    options: Optional[Dict[str, Any]],
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Return (tool_name, params) from request options, or (None, {})."""
    if not isinstance(options, dict):
        return None, {}

    tool = (options.get("direct_tool") or "").strip()
    params = options.get("direct_tool_params")
    if not isinstance(params, dict):
        params = {}

    if tool:
        return tool, dict(params)

    slash = (options.get("slash_command") or "").strip().lstrip("/").lower()
    if not slash:
        return None, {}

    mapped = SLASH_COMMAND_TOOL_MAP.get(slash)
    if not mapped:
        return None, {}

    args = (options.get("slash_args") or options.get("slash_prompt") or "").strip()
    if slash == "imagine":
        from backend.utils.settings_utils import get_chat_image_model
        from backend.services.image_prompt_sanitize import sanitize_image_prompt

        model = (
            params.get("model")
            or options.get("image_model")
            or get_chat_image_model()
        )
        prompt = sanitize_image_prompt(params.get("prompt") or args)
        if not prompt:
            return None, {}
        logger.info(f"Resolved /imagine direct: model={model} (from /imagemodel or options)")
        return mapped, {"prompt": prompt, "model": model}
    if slash == "websearch":
        query = params.get("query") or args
        if not query:
            return None, {}
        return mapped, {"query": query}
    if slash == "video":
        prompt = params.get("prompt") or args
        if not prompt:
            return None, {}
        return mapped, {"prompt": prompt}
    if slash == "remember":
        content = params.get("content") or args
        if not content:
            return None, {}
        return mapped, {"content": content}
    if slash == "gpu":
        return mapped, {}
    if slash == "logs":
        out = dict(params)
        bits = args.split(None, 1)
        if bits and bits[0].endswith(".log"):
            out["name"] = bits[0]
            if len(bits) > 1:
                out["query"] = bits[1]
        elif args:
            out["query"] = args
        return mapped, out
    if slash == "sysmap":
        return mapped, {"refresh": args.lower() in ("refresh", "--refresh", "1", "true")}
    if slash == "swarm":
        return mapped, {"swarm_id": args} if args else {}

    return mapped, dict(params)


def build_slash_user_message(slash_command: str, args: str) -> str:
    """Display message stored in chat history for a slash invocation."""
    cmd = slash_command.strip()
    if not cmd.startswith("/"):
        cmd = f"/{cmd}"
    return f"{cmd} {args}".strip() if args else cmd
