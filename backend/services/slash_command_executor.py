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
    "video": "generate_video",
    "music-video": "generate_music_video",
    "film-crew": "start_film_crew",
    "remember": "save_memory",
    "gpu": "inspect_gpu",
    "logs": "read_logs",
    "sysmap": "map_codebase",
    "swarm": "swarm_status",
    "removebg": "remove_background",
    "outpaint": "outpaint_image",
    "identity": "generate_identity",
    "inpaint": "inpaint_image",
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

    slash = (options.get("slash_command") or "").strip().lstrip("/").lower()
    # The browser names the slash command's tool but leaves its arguments in
    # slash_args; parse them here rather than run the tool without them.
    if tool and not (slash and not params and SLASH_COMMAND_TOOL_MAP.get(slash) == tool
                     and (options.get("slash_args") or options.get("slash_prompt"))):
        return tool, dict(params)

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
    if slash == "music-video":
        from backend.tools.video_pipeline_tools import parse_music_video_nl
        parsed = parse_music_video_nl(args)
        song = params.get("song") or parsed.get("song")
        style = params.get("style_prompt") or parsed.get("style_prompt")
        if not song or not style:
            return None, {}
        out = {"song": song, "style_prompt": style}
        if params.get("name"):
            out["name"] = params["name"]
        if params.get("i2v_model"):
            out["i2v_model"] = params["i2v_model"]
        return mapped, out
    if slash == "film-crew":
        from backend.tools.video_pipeline_tools import parse_film_crew_nl
        parsed = parse_film_crew_nl(args)
        script = params.get("script_text") or parsed.get("script_text")
        if not script:
            return None, {}
        out = {"script_text": script}
        if params.get("name"):
            out["name"] = params["name"]
        if params.get("video_model"):
            out["video_model"] = params["video_model"]
        return mapped, out
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
        # /sysmap and /sysmap refresh report the map; anything else is a
        # question for the query tool: /sysmap module backend/app.py,
        # /sysmap what calls run_chat_turn, /sysmap where is the seed box.
        if not args or args.lower() in ("refresh", "--refresh", "1", "true"):
            return mapped, {"refresh": bool(args)}
        return "system_map_query", {"q": args}
    if slash == "swarm":
        return mapped, {"swarm_id": args} if args else {}
    if slash == "removebg":
        return mapped, dict(params)
    if slash == "outpaint":
        out = dict(params)
        if args and not out.get("instruction"):
            out["instruction"] = args
        return mapped, out
    if slash == "inpaint":
        instruction = params.get("instruction") or args
        if not instruction:
            return None, {}
        out = {"instruction": instruction}
        if params.get("image"):
            out["image"] = params["image"]
        return mapped, out
    if slash == "identity":
        prompt = params.get("prompt") or args
        if not prompt:
            return None, {}
        # No consent flag here: the engine shows the consent card and records
        # the answer for the reference image before the tool runs.
        out = {"prompt": prompt}
        if params.get("image"):
            out["image"] = params["image"]
        return mapped, out

    return mapped, dict(params)


def build_slash_user_message(slash_command: str, args: str) -> str:
    """Display message stored in chat history for a slash invocation."""
    cmd = slash_command.strip()
    if not cmd.startswith("/"):
        cmd = f"/{cmd}"
    return f"{cmd} {args}".strip() if args else cmd


def run_direct_tool_turn(
    options: Optional[Dict[str, Any]],
    message: str,
    session_id: str,
    emit_fn,
    app,
    project_id: Optional[int] = None,
    log_tag: str = "chat",
    image_data: Any = None,
) -> bool:
    """Run a slash command or direct_tool as the whole turn, before any routing.

    The one pre-router bypass for POST /api/chat/unified and Socket.IO
    chat:send: the named registry tool runs through the engine's direct path
    (approval card, tool events, history) with no model involved. ``image_data``
    is the turn's attached picture, which photo tools (/removebg, /outpaint,
    /inpaint, /identity) work on. Returns False when the options name no direct
    tool, True once the turn has run. Errors propagate to the caller.
    """
    tool_name, params = resolve_slash_direct_tool(options)
    if not tool_name:
        return False
    import uuid

    from backend.services.unified_chat_engine import UnifiedChatEngine
    from backend.tools.tool_registry_init import initialize_all_tools
    from backend.utils.llm_service import get_llm_for_startup

    logger.info(f"[{log_tag}] Direct slash/direct intercept for {tool_name} before router")
    registry = initialize_all_tools()
    llm = None
    try:
        llm = get_llm_for_startup()
    except Exception:
        pass  # the direct path does not call the model
    engine = UnifiedChatEngine(tool_registry=registry, llm_instance=llm or object())
    engine.app = app
    engine._project_id = project_id
    engine._image_data = image_data
    request_id = str(uuid.uuid4())
    display = message or f"/{options.get('slash_command', tool_name)}"
    try:
        from backend.services.turn_router import direct_turn
        with app.app_context():
            direct_turn(display, tool_name, surface=options.get("surface"), request_id=request_id)
    except Exception as turn_err:
        logger.debug(f"direct turn not recorded: {turn_err}")
    with app.app_context():
        engine._run_direct_tool_execution(tool_name, params, session_id, emit_fn, request_id, display, options)
    return True
