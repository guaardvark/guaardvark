"""Turn routing: one TurnDecision per chat turn.

``route_turn`` looks at a turn once: the message, the request options and the
facts code can compute (attachments, screen state, the session's last picture,
the assistant's last offer, which tools are registered). It returns a frozen
:class:`TurnDecision` holding

* ``legacy_route``: the handler today's chain gives the turn, reproduced in
  the order ``AgentBrain.process`` and then ``UnifiedChatEngine._run_chat``
  apply, with the same matchers;
* ``triggers``: the cheap pattern families that fired, and ``candidates``,
  the routes those triggers allow (a route is never a candidate unless a
  trigger produced it; ``chat`` always is);
* ``flags``: per-turn settings the chat model's path reads (tools, retrieval,
  live data, memory capture).

Only legacy mode exists: ``route`` is ``legacy_route`` and ``route_source`` is
``"rule"``. Nothing dispatches on the decision yet. AgentBrain builds one per
turn, passes it to the engine as ``options["turn"]`` and writes it to the
decision telemetry (``decision_telemetry``). The characterization corpus in
``backend/tests/fixtures/turn_router/legacy_corpus.json`` pins ``legacy_route``
to what the chain does; a change to either side shows up there.

A route is chosen before anything runs. A handler that then fails at run time
(a media reflex whose tool reports failure, a screen task whose loop raises)
falls through exactly as before; the decision records the choice, not the
outcome.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, FrozenSet, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

ROUTER_VERSION = "legacy-1"


class Surface(str, Enum):
    """Where a turn came from."""

    WEB = "web"                      # the web UI's chat, floating chat and Code Editor chat
    VOICE = "voice"                  # a spoken turn sent through the web chat (is_voice_message)
    DISCORD = "discord"              # the Discord plugin (session ids start with "discord_")
    CLUSTER = "cluster"              # a chat:send forwarded by another machine's cluster bridge
    CLI_REPL = "cli_repl"
    ENHANCED_CHAT = "enhanced_chat"
    API = "api"                      # any other caller


class Route(str, Enum):
    """The handler that takes a turn."""

    CHAT = "chat"                    # the chat model answers (tools offered unless flags.skip_tools)
    SOCIAL = "social"                # greeting or acknowledgement: the chat model, no tools
    ACCEPTED_OFFER = "accepted_offer"  # a bare yes to the assistant's own offer: chat, tools chosen for the offer
    NEW_IMAGE = "new_image"
    IMAGE_RETRY = "image_retry"      # "try again" after a GPU-busy image render
    EDIT_IMAGE = "edit_image"
    EDIT_RETRY = "edit_retry"        # "try again" after a GPU-busy edit
    NAMED_IMAGE = "named_image"      # identity, background removal or outpaint on the picture at hand
    VIDEO = "video"
    MUSIC_VIDEO = "music_video"
    FILM_CREW = "film_crew"
    SCREEN_TASK = "screen_task"      # the agent screen loop
    MEDIA_CONTROL = "media_control"  # the music player
    DELIBERATE = "deliberate"        # Tier 3 multi-step agent loop
    WORKSTATION = "workstation"      # GPU, logs, system map, swarm or self-improvement status
    DIRECT_TOOL = "direct_tool"      # a slash command or an explicit direct_tool option
    FILE_GENERATION = "file_generation"  # the chat page's file card (decided in the browser)
    CLI_COMMAND = "cli_command"      # an llx REPL command
    UNAVAILABLE = "unavailable"      # no chat model loaded: the turn ends with an error


# The side-effect class of each route, one of decision_data.SIDE_EFFECTS.
# chat and its variants call tools only through the tool guard and approval
# cards, so the route itself is "none"; an accepted offer runs what was offered.
ROUTE_SIDE_EFFECT: Dict[Route, str] = {
    Route.CHAT: "none",
    Route.SOCIAL: "none",
    Route.ACCEPTED_OFFER: "file_db_write",
    Route.NEW_IMAGE: "gpu_job",
    Route.IMAGE_RETRY: "gpu_job",
    Route.EDIT_IMAGE: "gpu_job",
    Route.EDIT_RETRY: "gpu_job",
    Route.NAMED_IMAGE: "gpu_job",
    Route.VIDEO: "gpu_job",
    Route.MUSIC_VIDEO: "gpu_job",
    Route.FILM_CREW: "gpu_job",
    Route.SCREEN_TASK: "desktop",
    Route.MEDIA_CONTROL: "desktop",
    Route.DELIBERATE: "shell",       # Tier 3 runs tools without approval cards
    Route.WORKSTATION: "none",       # read-only status tools
    Route.DIRECT_TOOL: "shell",      # any registry tool; refined by DIRECT_TOOL_SIDE_EFFECT
    Route.FILE_GENERATION: "file_db_write",
    Route.CLI_COMMAND: "shell",
    Route.UNAVAILABLE: "none",
}

# Direct tools whose class is known; a tool missing here keeps the route's class.
DIRECT_TOOL_SIDE_EFFECT: Dict[str, str] = {
    "generate_image": "gpu_job",
    "edit_image": "gpu_job",
    "generate_video": "gpu_job",
    "generate_music_video": "gpu_job",
    "start_film_crew": "gpu_job",
    "remove_background": "gpu_job",
    "outpaint_image": "gpu_job",
    "inpaint_image": "gpu_job",
    "generate_identity": "gpu_job",
    "web_search": "web_egress",
    "save_memory": "file_db_write",
    "inspect_gpu": "none",
    "read_logs": "none",
    "map_codebase": "none",
    "system_map_query": "none",
    "swarm_status": "none",
    "self_improvement_status": "none",
}

# Trigger families and the routes each may add to the candidate set. Families
# with no routes only set flags or record that the turn is a question's
# precheck. cli_verb is reserved for the llx REPL surface.
TRIGGER_ROUTES: Dict[str, Tuple[Route, ...]] = {
    "slash": (Route.DIRECT_TOOL,),
    "workstation": (Route.WORKSTATION,),
    "screen": (Route.SCREEN_TASK,),
    "force_tier": (Route.DELIBERATE,),
    "media": (Route.MEDIA_CONTROL,),
    "image": (Route.NEW_IMAGE, Route.IMAGE_RETRY),
    "edit": (Route.EDIT_IMAGE, Route.EDIT_RETRY),
    "named_image": (Route.NAMED_IMAGE,),
    "video": (Route.VIDEO,),
    "music_video": (Route.MUSIC_VIDEO,),
    "film_crew": (Route.FILM_CREW,),
    "deliberation": (Route.DELIBERATE,),
    "social": (Route.SOCIAL,),
    "offer": (Route.ACCEPTED_OFFER,),
    "file_generation": (Route.FILE_GENERATION,),
    "health": (Route.UNAVAILABLE,),
    "cli_verb": (Route.CLI_COMMAND,),
    "realtime": (),
    "web_search_offer": (),
    "lookup": (),
    "memory": (),
    "camera": (),
    "declines_tools": (),
    "declines_media": (),
}

# Tools whose registration decides an intercept; checked per turn with get_tool.
ROUTING_TOOLS: Tuple[str, ...] = (
    "generate_image", "edit_image", "generate_identity", "remove_background",
    "outpaint_image", "generate_music_video", "start_film_crew", "generate_video",
    "media_play", "media_control", "media_volume", "media_status",
)

# The chat page opens its file card for these AgentRouter tools
# (useAgentRouter.routeToDetection) at confidence 0.6 or more.
_FILE_CARD_TOOLS = frozenset({
    "generate_csv", "generate_bulk_csv", "generate_wordpress_content",
    "generate_enhanced_wordpress_content", "codegen", "generate_file",
})
_FILE_CARD_MIN_CONFIDENCE = 0.6

# The brain reads the assistant's last offer only for a short reply.
_OFFER_REPLY_MAX_CHARS = 200
_MAX_URLS = 10

_SLASH_MUSIC_VIDEO_RE = re.compile(r"^\s*/music-video\b", re.IGNORECASE)
_SLASH_FILM_CREW_RE = re.compile(r"^\s*/film-crew\b", re.IGNORECASE)


@dataclass(frozen=True)
class TurnDecision:
    """Everything decided about one turn, computed once.

    The mappings are plain dicts so the decision pickles and copies with the
    request options; treat them as read-only.
    """

    request_id: str
    surface: Surface
    route: Route
    route_args: Dict[str, Any] = field(default_factory=dict)
    route_source: str = "rule"       # rule | llm | fallback
    legacy_route: Route = Route.CHAT
    candidates: Tuple[Route, ...] = (Route.CHAT,)
    triggers: Tuple[str, ...] = ()
    flags: Dict[str, Any] = field(default_factory=dict)
    facts: Dict[str, Any] = field(default_factory=dict)
    answers: Dict[str, Any] = field(default_factory=dict)
    side_effect: str = "none"
    router_version: str = ROUTER_VERSION


# ---------------------------------------------------------------------------
# L0 facts
# ---------------------------------------------------------------------------

def _registry(state) -> Any:
    return getattr(state, "tool_registry", None) if state is not None else None


def _available_tools(registry, extra: Tuple[Optional[str], ...] = ()) -> FrozenSet[str]:
    if registry is None:
        return frozenset()
    names = [n for n in (*ROUTING_TOOLS, *extra) if n]
    out = set()
    for name in names:
        try:
            if registry.get_tool(name):
                out.add(name)
        except Exception:
            pass
    return frozenset(out)


def _default_screen_drivable(state) -> bool:
    from backend.services.agent_brain import AgentBrain
    return AgentBrain(state=state)._screen_drivable()


def compute_facts(
    message: str,
    options: Optional[Mapping[str, Any]] = None,
    *,
    session_id: Optional[str] = None,
    image_data: Any = None,
    app=None,
    state=None,
    force_tier: Optional[int] = None,
    screen_drivable: Optional[Callable[[], bool]] = None,
    is_voice_message: bool = False,
) -> Dict[str, Any]:
    """The L0 facts of a turn: computed by code, never asked of a model.

    ``state`` is the BrainState (health, tool registry, reflex table);
    ``screen_drivable`` is the brain's own check, called only where the brain
    calls it (screen active, no forced tier, not a picture request).
    """
    from backend.services import agent_brain as ab
    from backend.services import unified_chat_engine as ue
    from backend.services.slash_command_executor import resolve_slash_direct_tool

    opts = options if isinstance(options, Mapping) else {}
    msg = message or ""
    stripped = msg.strip()
    sid = session_id or ""

    last_path = ue._SESSION_LAST_EDIT.get(sid)
    last_image = bool(last_path) and _exists(last_path)
    pending_edit = ue._SESSION_PENDING_IMAGE_EDIT.get(sid) or {}
    pending_edit_path = pending_edit.get("image") or last_path
    pending_edit_ready = bool(
        pending_edit
        and pending_edit_path and _exists(pending_edit_path)
        and (pending_edit.get("instruction") or "").strip()
    )
    image_focus = sid in ue._SESSION_IMAGE_FOCUS
    has_image = bool(image_data)
    attached = opts.get("attached_images")
    image_count = len(attached) if isinstance(attached, (list, tuple)) and attached else int(has_image)

    screen_active = bool(opts.get("agent_screen_active", False))
    drivable: Optional[bool] = None
    if not force_tier and screen_active and not ab.is_pure_image_request(msg, opts):
        try:
            drivable = bool(screen_drivable() if screen_drivable else _default_screen_drivable(state))
        except Exception:
            drivable = False

    offer = None
    if len(stripped) <= _OFFER_REPLY_MAX_CHARS:
        offer = ab.pending_offer(ab._last_assistant_text(app, sid))

    health = getattr(state, "health", None)
    reflexes_loaded = bool(getattr(health, "reflexes_loaded", False))
    reflex = None
    if reflexes_loaded and state is not None:
        hit = state.match_reflex(msg)
        reflex = hit[0].name if hit else None

    slash_tool, _params = resolve_slash_direct_tool(dict(opts))
    workstation = ue.match_workstation_direct(msg)
    workstation_tool = workstation[0] if workstation else None

    return {
        "chars": len(msg),
        "message_sha256": _text_sha(msg),
        "has_image": has_image,
        "image_count": image_count,
        "image_focus": image_focus,
        "last_image": last_image,
        "has_recent_image": has_image or (last_image and image_focus),
        "pending_image_prompt": bool(ue._SESSION_PENDING_IMAGE_PROMPT.get(sid)),
        "pending_image_edit": pending_edit_ready,
        "screen_active": screen_active,
        "screen_viewer_open": bool(opts.get("screen_viewer_open", False)),
        "agent_mode": bool(opts.get("agent_mode")),
        "screen_drivable": drivable,
        "force_tier": force_tier,
        "urls": tuple(ue._URL_OR_DOMAIN_PATTERN.findall(msg)[:_MAX_URLS]),
        "pending_offer": offer,
        "camera_frame": bool(opts.get("camera_frame")),
        "is_voice": bool(is_voice_message),
        "llm_available": bool(getattr(health, "llm_available", False)),
        "tools_available": bool(getattr(health, "tools_available", False)),
        "reflexes_loaded": reflexes_loaded,
        "reflex": reflex,
        "slash_tool": slash_tool,
        "workstation_tool": workstation_tool,
        "media_requires_command": bool(ue._media_requires_explicit_command()),
        "tools": _available_tools(_registry(state), (slash_tool, workstation_tool)),
    }


def _exists(path: str) -> bool:
    try:
        return os.path.exists(path)
    except Exception:
        return False


def _text_sha(text: str) -> str:
    from backend.services.decision_telemetry import text_sha256
    return text_sha256(text)


# ---------------------------------------------------------------------------
# Legacy route: today's chain, in its order
# ---------------------------------------------------------------------------

def legacy_route(message: str, options: Optional[Mapping[str, Any]], facts: Mapping[str, Any]
                 ) -> Tuple[Route, Dict[str, Any]]:
    """The route AgentBrain.process, then the engine's intercepts, give this turn.

    Mirrors AgentBrain.process: slash or direct tool, the screen-direct gate,
    force_tier 3, Tier 1 reflexes, the vision branch, a bare yes to a pending
    offer, social small talk, deliberation, else Tier 2. Every Tier 2 branch
    runs the engine's intercepts (see :func:`_engine`).
    """
    from backend.services import agent_brain as ab

    opts = options if isinstance(options, Mapping) else {}
    msg = message or ""
    stripped = msg.strip()
    tools = facts["tools"]
    force_tier = facts["force_tier"]

    if facts["slash_tool"] and facts["slash_tool"] in tools:
        return Route.DIRECT_TOOL, {"handler": "brain", "tool": facts["slash_tool"]}

    if (not force_tier and facts["screen_active"]
            and not ab.is_pure_image_request(msg, opts) and facts["screen_drivable"]):
        if not ab.NO_SCREEN_CONTEXT.match(stripped):
            return Route.SCREEN_TASK, {"handler": "brain", "tier": 0}

    if force_tier == 3:
        return _deliberate(msg, opts, facts, via="force_tier")

    if facts["reflexes_loaded"] and facts["reflex"]:
        return Route.MEDIA_CONTROL, {"handler": "reflex", "tier": 1, "tool": facts["reflex"]}

    has_image = facts["has_image"]
    if ((has_image or ab.VISION_PATTERNS.search(msg))
            and (has_image or facts["screen_active"] or facts["screen_viewer_open"])):
        return _engine(msg, opts, facts, Route.CHAT, image=has_image,
                       prompt_role=ab.vision_prompt_key(msg, has_image))

    if ab.AFFIRMATION_PATTERNS.fullmatch(stripped) and facts["pending_offer"]:
        return _engine(msg, opts, facts, Route.ACCEPTED_OFFER, image=False)

    if ab.SOCIAL_TIER2_PATTERNS.fullmatch(stripped):
        return _engine(msg, opts, facts, Route.SOCIAL, image=False)

    if any(p.search(msg) for p in ab.DELIBERATION_SIGNALS):
        return _deliberate(msg, opts, facts, via="deliberation")

    return _engine(msg, opts, facts, Route.CHAT, image=has_image)


def _deliberate(msg, opts, facts, *, via: str) -> Tuple[Route, Dict[str, Any]]:
    """AgentBrain._deliberate: Tier 3, or Tier 2 when no tools are registered."""
    if not facts["llm_available"]:
        return Route.UNAVAILABLE, {"handler": "brain", "tier": 3, "via": via}
    if not facts["tools_available"]:
        return _engine(msg, opts, facts, Route.CHAT, image=facts["has_image"],
                       via=f"{via}_without_tools")
    return Route.DELIBERATE, {"handler": "brain", "tier": 3, "via": via}


def _engine(msg, opts, facts, brain_route: Route, *, image: bool, prompt_role: str = "chat",
            via: Optional[str] = None) -> Tuple[Route, Dict[str, Any]]:
    """AgentBrain._instinct and then UnifiedChatEngine._run_chat.

    ``image`` is whether the brain hands the engine the turn's picture: the
    social and accepted-offer branches do not.
    """
    if not facts["llm_available"]:
        return Route.UNAVAILABLE, {"handler": "brain", "tier": 2, "brain_route": brain_route.value}
    base: Dict[str, Any] = {"handler": "engine", "tier": 2, "brain_route": brain_route.value,
                            "prompt_role": prompt_role, "image": bool(image)}
    if via:
        base["via"] = via
    if not opts.get("skip_direct_intercepts"):
        hit = _engine_intercept(msg, opts, facts, image=bool(image))
        if hit is not None:
            route, args = hit
            return route, {**base, **args}
    return brain_route, base


def _engine_intercept(msg: str, opts: Mapping[str, Any], facts: Mapping[str, Any], *, image: bool
                      ) -> Optional[Tuple[Route, Dict[str, Any]]]:
    """The first of UnifiedChatEngine._run_chat's shortcuts that takes the turn.

    Order as in _run_chat: workstation (folded into direct_tool, so a typed
    direct_tool option wins), direct tool, image retry, edit retry, media,
    named photo tools, edit, music video, Film Crew, video, image.
    """
    from backend.services import unified_chat_engine as ue
    from backend.tools.video_pipeline_tools import (
        parse_film_crew_nl, parse_music_video_nl, wants_film_crew, wants_music_video,
    )
    from backend.services.image_prompt_sanitize import sanitize_image_prompt

    tools = facts["tools"]

    workstation = facts["workstation_tool"] if not opts.get("direct_tool") else None
    if workstation:
        return Route.WORKSTATION, {"tool": workstation, "registered": workstation in tools}
    if facts["slash_tool"]:
        # Only an unregistered tool gets this far; the engine answers that it is not available.
        return Route.DIRECT_TOOL, {"tool": facts["slash_tool"], "registered": facts["slash_tool"] in tools}

    retry = ue._is_image_retry_message(msg)
    if retry and facts["pending_image_prompt"] and "generate_image" in tools:
        return Route.IMAGE_RETRY, {"tool": "generate_image"}
    if retry and facts["pending_image_edit"] and "edit_image" in tools:
        return Route.EDIT_RETRY, {"tool": "edit_image"}

    stripped = msg.strip()
    for pattern, tool_name, _params in ue.UnifiedChatEngine._MEDIA_PATTERNS:
        if pattern.match(stripped) and tool_name in tools:
            return Route.MEDIA_CONTROL, {"tool": tool_name}

    last_image = facts["last_image"]
    focus = facts["image_focus"]
    source = image or ((focus or bool(ue._NAMES_THE_IMAGE_RE.search(msg))) and last_image)
    if source:
        if ue.user_wants_identity_generate(msg) and "generate_identity" in tools:
            return Route.NAMED_IMAGE, {"tool": "generate_identity"}
        if ue.user_wants_background_remove(msg) and "remove_background" in tools:
            return Route.NAMED_IMAGE, {"tool": "remove_background"}
        if ue.user_wants_outpaint(msg) and "outpaint_image" in tools:
            return Route.NAMED_IMAGE, {"tool": "outpaint_image"}

    last = False if image else last_image
    if (ue.user_wants_image_edit(msg, has_recent_image=image or (last and focus),
                                 has_stale_image=last and not focus)
            and "edit_image" in tools and (image or last)):
        return Route.EDIT_IMAGE, {"tool": "edit_image", "source": "attached" if image else "last_image"}

    if stripped:
        slash_mv = bool(_SLASH_MUSIC_VIDEO_RE.match(msg))
        if ((slash_mv or wants_music_video(msg))
                and (slash_mv or not ue._media_requires_explicit_command())
                and "generate_music_video" in tools):
            parsed = parse_music_video_nl(msg)
            return Route.MUSIC_VIDEO, {"tool": "generate_music_video",
                                       "notice": not (parsed.get("song") and parsed.get("style_prompt"))}
        slash_fc = bool(_SLASH_FILM_CREW_RE.match(msg))
        if ((slash_fc or wants_film_crew(msg))
                and (slash_fc or not ue._media_requires_explicit_command())
                and "start_film_crew" in tools):
            return Route.FILM_CREW, {"tool": "start_film_crew",
                                     "notice": not parse_film_crew_nl(msg).get("script_text")}
        if ue.user_wants_video_generation(msg) and "generate_video" in tools:
            return Route.VIDEO, {"tool": "generate_video"}
        if (ue.user_wants_image_generation(msg) and "generate_image" in tools
                and sanitize_image_prompt(msg)
                and (image or ue.earlier_picture_reference(msg) != "changed")):
            return Route.NEW_IMAGE, {"tool": "generate_image"}
    return None


# ---------------------------------------------------------------------------
# Trigger families and candidates
# ---------------------------------------------------------------------------

def compute_triggers(message: str, options: Optional[Mapping[str, Any]], facts: Mapping[str, Any]
                     ) -> Tuple[Tuple[str, ...], Tuple[Route, ...]]:
    """The trigger families that fired and the candidate routes they allow.

    Triggers are broader than the rules that act today (a mention, not a
    whole request), so the legacy route is always among the candidates.
    """
    from backend.services import agent_brain as ab
    from backend.services import unified_chat_engine as ue
    from backend.services.agent_router import declines_tools
    from backend.services.memory_capture import will_capture
    from backend.tools.video_pipeline_tools import _FILM_CREW_RE, _MUSIC_VIDEO_RE
    from backend.utils.request_phrases import declines_media

    opts = options if isinstance(options, Mapping) else {}
    msg = message or ""
    lower = msg.lower()
    stripped = msg.strip()
    has_picture = facts["has_image"] or facts["last_image"]
    retry = ue._is_image_retry_message(msg)
    fired = []

    def check(family: str, hit) -> None:
        if hit:
            fired.append(family)

    check("health", not facts["llm_available"])
    check("slash", facts["slash_tool"])
    check("workstation", facts["workstation_tool"] or ue._mentions_keyword(lower, ue.WORKSTATION_KEYWORDS))
    check("screen", facts["screen_active"] or ab.VISION_PATTERNS.search(msg))
    check("force_tier", facts["force_tier"] == 3)
    check("media", facts["reflex"] or any(p.match(stripped) for p, _t, _f in ue.UnifiedChatEngine._MEDIA_PATTERNS))
    check("image", ue._has_explicit_image_gen_intent(lower, draw_anywhere=True)
          or ue._SLASH_IMAGE_RE.match(lower) or ab.is_pure_image_request(msg, opts)
          or (retry and facts["pending_image_prompt"]))
    check("edit", (has_picture and ue._IMAGE_EDIT_VERB_RE.search(msg))
          or (retry and facts["pending_image_edit"]))
    check("named_image", has_picture and (ue._IDENTITY_INTENT_RE.search(msg) or ue._BG_REMOVE_RE.search(msg)
                                          or ue._OUTPAINT_RE.search(msg)))
    check("video", ue._VIDEO_INTENT_RE.search(lower) or ue._VIDEO_REQUEST_RE.search(lower)
          or ue._SLASH_VIDEO_RE.match(lower) or lower.startswith("video of "))
    check("music_video", _MUSIC_VIDEO_RE.search(msg) or _SLASH_MUSIC_VIDEO_RE.match(msg))
    check("film_crew", _FILM_CREW_RE.search(msg) or _SLASH_FILM_CREW_RE.match(msg))
    check("deliberation", any(p.search(msg) for p in ab.DELIBERATION_SIGNALS))
    check("social", ab.SOCIAL_TIER2_PATTERNS.fullmatch(stripped))
    check("offer", facts["pending_offer"])
    check("file_generation", _file_card_match(msg))
    check("realtime", ue.UnifiedChatEngine._is_realtime_query(msg))
    check("web_search_offer", _asks_current_information(msg))
    check("lookup", ue._LOOKUP_REQUEST_RE.search(msg))
    check("memory", will_capture(msg))
    check("camera", facts["camera_frame"])
    check("declines_tools", declines_tools(msg))
    check("declines_media", declines_media(msg))

    candidates = [Route.CHAT]
    for family in fired:
        for route in TRIGGER_ROUTES[family]:
            if route is Route.IMAGE_RETRY and not (retry and facts["pending_image_prompt"]):
                continue
            if route is Route.EDIT_RETRY and not (retry and facts["pending_image_edit"]):
                continue
            if route is Route.SCREEN_TASK and not facts["screen_active"]:
                continue
            if route not in candidates:
                candidates.append(route)
    return tuple(fired), tuple(candidates)


def _file_card_match(message: str) -> bool:
    """AgentRouter's file-generation or code-generation match that opens the chat page's file card."""
    try:
        from backend.services.agent_router import RouteType, get_agent_router
        decision = get_agent_router().route(message)
    except Exception as e:
        logger.debug("file-card trigger skipped: %s", e)
        return False
    if decision.confidence < _FILE_CARD_MIN_CONFIDENCE:
        return False
    if decision.route_type == RouteType.FILE_GENERATION:
        return True
    return decision.route_type == RouteType.TOOL_DIRECT and decision.tool_name in _FILE_CARD_TOOLS


def _asks_current_information(message: str) -> bool:
    try:
        from backend.utils.intent_classifier import asks_current_information
        return asks_current_information(message)
    except Exception as e:
        logger.debug("web-search-offer trigger skipped: %s", e)
        return False


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------

_CHAT_MODEL_ROUTES = (Route.CHAT, Route.SOCIAL, Route.ACCEPTED_OFFER)


def compute_flags(message: str, options: Optional[Mapping[str, Any]], facts: Mapping[str, Any],
                  route: Route, route_args: Mapping[str, Any]) -> Dict[str, Any]:
    """Settings the chat model's turn reads, as the engine and brain decide them today.

    use_rag and skip_tools are what the engine's main path would use;
    memory_candidate is whether this turn's message would be filed as a fact.
    """
    from backend.services import unified_chat_engine as ue
    from backend.services.agent_router import declines_tools
    from backend.services.memory_capture import will_capture

    opts = options if isinstance(options, Mapping) else {}
    msg = message or ""
    engine_image = bool(route_args.get("image"))
    offer = facts["pending_offer"] if route is Route.ACCEPTED_OFFER else None
    selection_text = f"{offer}\n{msg}" if offer else msg

    persist = opts.get("persist", True) is not False and not opts.get("skip_memory_capture")
    if not persist:
        memory = False
    elif route in _CHAT_MODEL_ROUTES and route_args.get("handler") == "engine":
        memory = will_capture(msg)
    elif route in (Route.DELIBERATE, Route.SCREEN_TASK):
        memory = will_capture(msg, explicit_only=True)
    else:
        memory = False

    return {
        "needs_live_data": ue.UnifiedChatEngine._is_realtime_query(msg),
        "use_rag": bool(opts.get("use_rag", True) and not ue.is_conversational(msg)
                        and not engine_image and not ue.UnifiedChatEngine._should_skip_rag(msg)),
        "skip_tools": bool(route is Route.SOCIAL or opts.get("skip_tools") or declines_tools(msg)
                           or opts.get("file_generation") in ("offered", "started")),
        "vision_prompt": route_args.get("prompt_role") == "vision",
        "wants_lookup": bool(ue._LOOKUP_REQUEST_RE.search(selection_text)),
        "memory_candidate": bool(memory),
    }


# ---------------------------------------------------------------------------
# route_turn
# ---------------------------------------------------------------------------

def side_effect_for(route: Route, route_args: Mapping[str, Any]) -> str:
    if route is Route.DIRECT_TOOL:
        return DIRECT_TOOL_SIDE_EFFECT.get(route_args.get("tool") or "", ROUTE_SIDE_EFFECT[route])
    return ROUTE_SIDE_EFFECT[route]


def surface_of(value: Any) -> Surface:
    try:
        return Surface(str(value))
    except ValueError:
        return Surface.API


# The Discord plugin names its sessions discord_user_<id>, discord_channel_<id>
# and discord_voice_<guild> (plugins/discord/core); the web UI uses UUIDs.
_DISCORD_SESSION_PREFIX = "discord_"


def chat_surface(session_id: Optional[str], is_voice_message: bool = False) -> Surface:
    """The surface of a POST /api/chat/unified turn."""
    if str(session_id or "").startswith(_DISCORD_SESSION_PREFIX):
        return Surface.DISCORD
    return Surface.VOICE if is_voice_message else Surface.WEB


def route_turn(
    message: str,
    options: Optional[Mapping[str, Any]] = None,
    *,
    surface: Any = None,
    request_id: Optional[str] = None,
    session_id: Optional[str] = None,
    image_data: Any = None,
    app=None,
    state=None,
    force_tier: Optional[int] = None,
    screen_drivable: Optional[Callable[[], bool]] = None,
    is_voice_message: bool = False,
    dry: bool = False,
) -> TurnDecision:
    """Decide the turn once and, unless ``dry``, write its 'turn' telemetry row.

    ``dry`` computes the facts, triggers and legacy route only: no question
    is asked, no model is called and nothing is written. With ``app`` the
    work runs in its app context, so settings read from the database exactly
    as the engine reads them.
    """
    from flask import has_app_context

    kwargs = dict(surface=surface, request_id=request_id, session_id=session_id,
                  image_data=image_data, app=app, state=state, force_tier=force_tier,
                  screen_drivable=screen_drivable, is_voice_message=is_voice_message, dry=dry)
    if app is not None and not has_app_context():
        with app.app_context():
            return _route_turn(message, options, **kwargs)
    return _route_turn(message, options, **kwargs)


def _route_turn(message, options, *, surface, request_id, session_id, image_data, app, state,
                force_tier, screen_drivable, is_voice_message, dry) -> TurnDecision:
    facts = compute_facts(
        message, options, session_id=session_id, image_data=image_data, app=app, state=state,
        force_tier=force_tier, screen_drivable=screen_drivable, is_voice_message=is_voice_message,
    )
    route, route_args = legacy_route(message, options, facts)
    triggers, candidates = compute_triggers(message, options, facts)
    if route not in candidates:
        logger.debug("turn route %s came from no trigger (%s)", route.value, ",".join(triggers))
    turn = TurnDecision(
        request_id=str(request_id or uuid.uuid4()),
        surface=surface_of(surface),
        route=route,
        route_args=dict(route_args),
        route_source="rule",
        legacy_route=route,
        candidates=candidates,
        triggers=triggers,
        flags=compute_flags(message, options, facts, route, route_args),
        facts=facts,
        answers={},
        side_effect=side_effect_for(route, route_args),
    )
    if not dry:
        write_turn(turn, message)
    return turn


def direct_turn(message: str, tool_name: str, *, surface: Any = None, request_id: Optional[str] = None,
                ) -> Optional[TurnDecision]:
    """Record a turn an entry point sent straight to a direct tool (a slash
    command run before the brain). Never raises; None when nothing was built."""
    try:
        msg = message or ""
        turn = TurnDecision(
            request_id=str(request_id or uuid.uuid4()),
            surface=surface_of(surface),
            route=Route.DIRECT_TOOL,
            route_args={"handler": "entry", "tool": tool_name},
            legacy_route=Route.DIRECT_TOOL,
            candidates=(Route.CHAT, Route.DIRECT_TOOL),
            triggers=("slash",),
            facts={"chars": len(msg), "message_sha256": _text_sha(msg), "slash_tool": tool_name},
            side_effect=side_effect_for(Route.DIRECT_TOOL, {"tool": tool_name}),
        )
        write_turn(turn, msg)
        return turn
    except Exception as e:
        logger.debug("direct turn not recorded: %s", e)
        return None


# ---------------------------------------------------------------------------
# Telemetry row
# ---------------------------------------------------------------------------

def turn_row(turn: TurnDecision, message: Optional[str] = None, *, llm_debug: bool = False) -> Dict[str, Any]:
    """The 'turn' telemetry row: no message text, offer text or links unless llm_debug."""
    facts = dict(turn.facts)
    facts.pop("tools", None)
    offer = facts.get("pending_offer")
    urls = facts.get("urls") or ()
    facts["pending_offer"] = bool(offer)
    facts["urls"] = len(urls)
    row: Dict[str, Any] = {
        "kind": "turn",
        "ts": datetime.now(timezone.utc).isoformat(),
        "request_id": turn.request_id,
        "surface": turn.surface.value,
        "route": turn.route.value,
        "route_source": turn.route_source,
        "legacy_route": turn.legacy_route.value,
        "route_args": dict(turn.route_args),
        "candidates": [r.value for r in turn.candidates],
        "triggers": list(turn.triggers),
        "flags": dict(turn.flags),
        "facts": facts,
        "answers": {name: _answer_summary(a) for name, a in turn.answers.items()},
        "side_effect": turn.side_effect,
        "router_version": turn.router_version,
    }
    if turn.legacy_route not in turn.candidates:
        row["legacy_outside_candidates"] = True
    if llm_debug:
        row["message"] = message
        row["pending_offer_text"] = offer
        row["url_list"] = list(urls)
    return row


def _answer_summary(answer: Any) -> Any:
    """A DecisionResult as {answer, source, reason}; anything else as given."""
    if isinstance(answer, Mapping):
        return {k: answer.get(k) for k in ("answer", "source", "reason")}
    keys = ("answer", "source", "reason")
    if all(hasattr(answer, k) for k in keys):
        return {k: getattr(answer, k) for k in keys}
    return answer


def write_turn(turn: TurnDecision, message: Optional[str] = None) -> bool:
    """Append the turn's row to the decision telemetry. Never raises."""
    try:
        from backend.services.decision_telemetry import append_row, llm_debug_on, telemetry_enabled
        if not telemetry_enabled():
            return False
        return append_row(turn_row(turn, message, llm_debug=llm_debug_on()))
    except Exception as e:
        logger.debug("turn telemetry row skipped: %s", e)
        return False
