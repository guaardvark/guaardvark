#!/usr/bin/env python3
"""
BrainState — Singleton holding all pre-computed agent state.

Initialized once at backend startup.  Refreshed only when the active model
changes, a plugin starts/stops, or an explicit refresh is requested.

Every field that used to be rebuilt per-request in the old pipeline lives
here instead: tool schemas, system prompts, model capabilities, and the
compiled reflex table.
"""

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from .step_budget import StepBudget, TierTelemetry

logger = logging.getLogger(__name__)

# Re-export for backwards compatibility
__all__ = [
    "BrainState",
    "BrainHealth",
    "ModelCapabilities",
    "ReflexAction",
    "ReflexResult",
    "StepBudget",
    "TierTelemetry",
    "WarmUpStatus",
    "_build_default_reflexes",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class WarmUpStatus(Enum):
    PENDING = "pending"
    WARMING = "warming"
    READY = "ready"
    FAILED = "failed"


@dataclass
class ModelCapabilities:
    """Detected once per model change, cached."""
    name: str = ""
    supports_native_tools: bool = False
    is_thinking_model: bool = False
    is_vision_model: bool = False
    context_window: int = 8192


@dataclass
class BrainHealth:
    """Tracks what components are available for graceful degradation."""
    llm_available: bool = False
    tools_available: bool = False
    reflexes_loaded: bool = False
    warm_up_status: WarmUpStatus = WarmUpStatus.PENDING
    degradation_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "llm_available": self.llm_available,
            "tools_available": self.tools_available,
            "reflexes_loaded": self.reflexes_loaded,
            "warm_up_status": self.warm_up_status.value,
            "degradation_reason": self.degradation_reason,
        }


@dataclass
class ReflexResult:
    """Result from a Tier 1 reflex execution."""
    response: str
    tool_called: Optional[str] = None
    tool_params: Optional[Dict[str, Any]] = None
    success: bool = True
    emit_events: Optional[List[Dict]] = None


@dataclass
class ReflexAction:
    """A compiled pattern -> action mapping.  Zero LLM involvement."""
    name: str
    patterns: List["re.Pattern[str]"]
    handler: Callable[..., ReflexResult]
    priority: int = 100  # lower = checked first


# ---------------------------------------------------------------------------
# Reflex pattern helpers
# ---------------------------------------------------------------------------

def _anchor_pattern(pattern: str, flags: int = re.IGNORECASE) -> "re.Pattern[str]":
    """Compile a full-message anchored pattern for deterministic Tier-1 matching."""
    p = pattern.strip()
    if p.startswith("(?i)"):
        p = p[4:]
    if not p.startswith("^"):
        p = "^" + p
    if not p.endswith("$"):
        p = p + r"[\s?!.,]*$"
    return re.compile(p, flags)


# ---------------------------------------------------------------------------
# Default reflex table (action reflexes only — no social/chat placebo)
# ---------------------------------------------------------------------------

def _build_default_reflexes(tool_registry=None) -> List[ReflexAction]:
    """Build the default reflex table.

    Reflexes are context-free: they fire only when the pattern match alone
    is unambiguous regardless of conversation history.
    """
    reflexes: List[ReflexAction] = []

    # -- Media reflexes (only if tools are available) --
    if tool_registry:
        def _media_reflex(tool_name: str, extract_fn=None):
            """Create a handler that calls a media tool directly."""
            def handler(message: str, match: "re.Match", ctx: Dict) -> ReflexResult:
                params = {}
                if extract_fn:
                    params = extract_fn(message, match)
                try:
                    result = tool_registry.execute_tool(tool_name, **params)
                    if result.success and result.output:
                        output = result.output
                        if isinstance(output, dict):
                            # Format dict output as readable text
                            parts = [f"{k}: {v}" for k, v in output.items()
                                     if v and k != "metadata"]
                            response = "\n".join(parts) if parts else str(output)
                        else:
                            response = str(output)
                        return ReflexResult(
                            response=response,
                            tool_called=tool_name,
                            tool_params=params,
                            success=True,
                        )
                    # Tool reported failure -- fall through to Tier 2
                    return ReflexResult(
                        response="",
                        tool_called=tool_name,
                        tool_params=params,
                        success=False,
                    )
                except Exception as e:
                    logger.warning(f"Reflex {tool_name} failed: {e}")
                    return ReflexResult(response="", success=False)
            return handler

        def _extract_media_action(message: str, match: "re.Match") -> Dict:
            action = match.group(1).lower()
            action_map = {"skip": "next", "prev": "previous", "resume": "play"}
            return {"action": action_map.get(action, action)}

        def _extract_play_query(message: str, match: "re.Match") -> Dict:
            # Everything after "play " is the query
            query = re.sub(r"(?i)^play\s+", "", message).strip()
            return {"query": query} if query else {}

        def _extract_volume(message: str, match: "re.Match") -> Dict:
            # ASCII digits only: \d also matches other scripts' digits.
            vol_match = re.search(r"([0-9]+)", message)
            if vol_match:
                return {"level": int(vol_match.group(1))}
            # Whole words, so "unmute" is not read as "mute" nor "setup" as "up".
            for word, val in [("unmute", "unmute"), ("mute", "mute"),
                              ("up", "+10"), ("louder", "+10"),
                              ("down", "-10"), ("quieter", "-10"), ("softer", "-10")]:
                if re.search(rf"\b{word}\b", message.lower()):
                    return {"level": val}
            return {}

        # Only add media reflexes if the tools exist
        if tool_registry.get_tool("media_play"):
            reflexes.append(ReflexAction(
                name="media_play",
                patterns=[_anchor_pattern(r"play\s+.+")],
                handler=_media_reflex("media_play", _extract_play_query),
                priority=10,
            ))

        if tool_registry.get_tool("media_control"):
            reflexes.append(ReflexAction(
                name="media_control",
                patterns=[
                    _anchor_pattern(
                        r"(pause|stop|resume|next|skip|previous|prev)"
                        r"(?:\s+(?:the\s+)?(?:music|song|track|playback|player))?"
                    ),
                ],
                handler=_media_reflex("media_control", _extract_media_action),
                priority=10,
            ))

        if tool_registry.get_tool("media_volume"):
            reflexes.append(ReflexAction(
                name="media_volume",
                patterns=[
                    _anchor_pattern(r"volume\s+(?:up|down|[0-9]+)"),
                    _anchor_pattern(r"(?:turn|set)\s+(?:the\s+)?volume(?:\s+[0-9]+)?"),
                    _anchor_pattern(r"(?:louder|quieter|softer)"),
                    _anchor_pattern(r"(?:mute|unmute)"),
                ],
                handler=_media_reflex("media_volume", _extract_volume),
                priority=10,
            ))

        if tool_registry.get_tool("media_status"):
            reflexes.append(ReflexAction(
                name="media_status",
                patterns=[
                    _anchor_pattern(
                        r"(?:what'?s|what\s+is)\s+(?:this\s+)?(?:playing|this\s+song)"
                    ),
                    _anchor_pattern(r"(?:current|now)\s+(?:playing|song|track)"),
                ],
                handler=_media_reflex("media_status"),
                priority=10,
            ))

    # Sort by priority (lower = first)
    reflexes.sort(key=lambda r: r.priority)
    return reflexes


# ---------------------------------------------------------------------------
# BrainState singleton
# ---------------------------------------------------------------------------

class BrainState:
    """
    Singleton holding all pre-computed agent state.

    Initialized once at startup.  Call refresh() when the active model or
    tool registry changes.
    """

    _instance: Optional["BrainState"] = None
    _lock = threading.Lock()

    def __init__(self):
        # Tier 1
        self.reflexes: List[ReflexAction] = []

        # Tier 2 / Tier 3 shared
        self.tool_registry = None
        self.tool_schemas_json: str = ""
        self.tool_schemas_native: List[Any] = []
        self.system_prompts: Dict[str, str] = {}

        # Model
        self.active_model: str = ""
        self.model_caps = ModelCapabilities()
        self.llm: Any = None

        # Config
        self.max_agent_iterations: int = 10
        self.lite_mode: bool = False

        # Health
        self.health = BrainHealth()

        # Internal
        self._initialized = False
        self._warm_up_thread: Optional[threading.Thread] = None
        # Flask app handle for live DB reads from worker threads that
        # don't inherit a request context. Captured during initialize().
        self._app = None

    @classmethod
    def get_instance(cls) -> "BrainState":
        """Get or create the singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def existing(cls) -> Optional["BrainState"]:
        """The singleton when something has created it, without creating one."""
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        with cls._lock:
            cls._instance = None

    # -- Initialization -----------------------------------------------------

    def initialize(self, lite_mode: bool = False):
        """
        Initialize all pre-computed state.  Called once during create_app().

        Each step is wrapped in try/except for graceful degradation --
        partial initialization is valid.
        """
        self.lite_mode = lite_mode
        logger.info(f"BrainState initializing (lite_mode={lite_mode})")
        start = time.monotonic()

        # Capture the live Flask app so worker threads can push their own
        # app context when they need DB access (memory lookups, etc.).
        try:
            from flask import current_app
            self._app = current_app._get_current_object()
        except Exception:
            self._app = None

        # Step 1: Tool registry
        try:
            if not lite_mode:
                from backend.tools.tool_registry_init import initialize_all_tools
                self.tool_registry = initialize_all_tools()
                self.health.tools_available = True
                logger.info(f"Tool registry loaded: {len(self.tool_registry.list_tools())} tools")
            else:
                self.health.tools_available = False
                logger.info("Lite mode: tool registry skipped")
        except Exception as e:
            logger.error(f"Tool registry failed: {e}")
            self.health.tools_available = False
            self.health.degradation_reason = f"Tool registry unavailable: {e}"

        # Step 2: Serialize tool schemas (once)
        try:
            if self.tool_registry:
                self.tool_schemas_json = self.tool_registry.get_tool_schemas(
                    format="json_prompt"
                )
                logger.info(f"Tool schemas serialized ({len(self.tool_schemas_json)} chars)")
        except Exception as e:
            logger.error(f"Tool schema serialization failed: {e}")
            self.tool_schemas_json = ""

        # Step 3: Build LlamaIndex FunctionTool objects (once)
        try:
            if self.tool_registry:
                self.tool_schemas_native = self.tool_registry.as_llama_index_tools()
                logger.info(f"Native tool objects built: {len(self.tool_schemas_native)}")
        except Exception as e:
            logger.warning(f"Native tool build failed (non-critical): {e}")
            self.tool_schemas_native = []

        # Step 4: Detect model capabilities
        try:
            if not lite_mode:
                self._detect_model_capabilities()
            else:
                self._detect_model_capabilities_lite()
            self.health.llm_available = self.llm is not None
        except Exception as e:
            logger.error(f"Model detection failed: {e}")
            self.health.llm_available = False
            if not self.health.degradation_reason:
                self.health.degradation_reason = f"LLM unavailable: {e}"

        # Step 5: Pre-render system prompts
        try:
            self._build_system_prompts()
            logger.info(f"System prompts pre-rendered: {list(self.system_prompts.keys())}")
        except Exception as e:
            logger.error(f"System prompt rendering failed: {e}")

        # Step 6: Compile reflex table
        try:
            self.reflexes = _build_default_reflexes(
                self.tool_registry if self.health.tools_available else None
            )
            self.health.reflexes_loaded = True
            logger.info(f"Reflex table compiled: {len(self.reflexes)} reflexes")
        except Exception as e:
            logger.error(f"Reflex compilation failed: {e}")
            self.reflexes = []
            self.health.reflexes_loaded = False

        # Step 7: Warm-up ping (background thread). Skipped when the user has
        # disabled the Ollama plugin in /plugins — same logic as app.py's
        # [LLM-Init] step 5. Without this gate, Ollama would still load the
        # model into VRAM at boot even with the plugin toggled off, because
        # this warmup runs independently of [LLM-Init].
        if self.health.llm_available and self._ollama_user_enabled():
            self._start_warmup()
        elif self.health.llm_available:
            logger.info(
                "BrainState: skipping warm-up — Ollama plugin is disabled in user prefs. "
                "First chat call will load the model on demand."
            )
            self.health.warm_up_status = WarmUpStatus.READY

        elapsed = (time.monotonic() - start) * 1000
        self._initialized = True
        logger.info(f"BrainState initialized in {elapsed:.0f}ms | health={self.health.to_dict()}")

    def _detect_model_capabilities(self):
        """Detect model capabilities from Ollama (full mode)."""
        from backend.utils.llm_service import get_default_llm
        from backend.utils.ollama_resource_manager import (
            is_vision_model,
            model_supports_thinking,
            model_supports_tools,
        )

        self.llm = get_default_llm()
        model_name = getattr(self.llm, "model", "unknown")
        self.active_model = model_name

        self.model_caps = ModelCapabilities(
            name=model_name,
            supports_native_tools=model_supports_tools(model_name),
            is_thinking_model=model_supports_thinking(model_name),
            is_vision_model=is_vision_model(model_name),
            context_window=getattr(self.llm, "context_window", 8192),
        )
        logger.info(f"Model capabilities: {self.model_caps}")

    def _detect_model_capabilities_lite(self):
        """Detect model capabilities for lite mode (minimal deps)."""
        try:
            from backend.utils.llm_service import get_default_llm
            self.llm = get_default_llm()
            model_name = getattr(self.llm, "model", "unknown")
            self.active_model = model_name
            self.model_caps = ModelCapabilities(
                name=model_name,
                context_window=getattr(self.llm, "context_window", 8192),
            )
        except Exception as e:
            logger.warning(f"Lite mode LLM detection failed: {e}")
            self.model_caps = ModelCapabilities()

    def _build_system_prompts(self):
        """Pre-render system prompt prefix templates (live blocks filled at request time).

        The persona and the rules block come from the Rules page and are both
        gated by the Settings "Rules" switch; ``refresh_prompts`` re-renders
        them when a rule changes.
        """

        persona = ""
        rules_block = ""
        self.persona_rule_id = None
        try:
            from backend.utils.settings_utils import get_rules_enabled
            if get_rules_enabled():
                from backend import rule_utils
                from backend.utils import chat_utils
                persona = chat_utils.get_active_system_prompt() or ""
                self.persona_rule_id = chat_utils.LAST_PERSONA_RULE_ID if persona else None
                rules_block = rule_utils.get_chat_rules_block(self.active_model or None)
        except Exception:
            pass

        honesty = ""
        try:
            from backend.services.honesty_steering import HonestySteering
            steering = HonestySteering()
            honesty = steering.get_steering_prompt(
                intent="general", intensity="standard"
            ) or ""
        except Exception:
            pass

        prefix = ""
        if honesty:
            prefix = honesty + "\n\n"
        if persona:
            prefix += persona + "\n\n"
        if rules_block:
            prefix += rules_block.strip() + "\n\n"
        prefix += "{MEMORY_BLOCK}{DESKTOP_STATE}"

        # Prefix-only templates; role tails assembled in get_system_prompt()
        self.system_prompts["chat"] = prefix
        self.system_prompts["vision"] = prefix
        self.system_prompts["agent"] = prefix

    _VOICE_INSTRUCTION = (
        "\n\nIMPORTANT — VOICE MODE: The user is speaking via voice. "
        "Respond with ONLY what should be spoken — natural, concise, conversational."
    )

    def get_system_prompt(
        self,
        role: str = "chat",
        query: str = "",
        session_id: str = None,
        project_id=None,
        cli_working_memory: Optional[Dict[str, Any]] = None,
        budget: Optional[StepBudget] = None,
        facts_registry=None,
        skip_tools: bool = False,
        tool_list: str = "",
        is_voice_message: bool = False,
        context: str = None,
        native: bool = False,
        agent_name: str = "",
        agent_prompt: str = "",
        screen: bool = False,
    ) -> str:
        """Canonical system prompt builder for all tiers.

        Args:
            role: "chat" | "agent" | "vision". "agent" is the AgentExecutor
                loop; "vision" is the Tier 2 chat path for images and the
                watched agent screen.
            context: deprecated alias for role (back-compat)
            tool_list: chat role, the XML tool list (from UCE concise builder);
                agent role, the executor's json_prompt list of the tools that
                run can call
            native: agent role, the model takes tools through the API's tools
                parameter
            agent_name, agent_prompt: agent role, a configured agent's name
                and instructions
            screen: agent role, the run works the agent's virtual screen
        """
        if context and role == "chat":
            role = context

        prefix = self.system_prompts.get(role, self.system_prompts.get("chat", ""))

        memory_text = ""
        try:
            from backend.api.memory_api import get_memories_for_context
            if self._app is not None:
                with self._app.app_context():
                    memory_text = get_memories_for_context(
                        limit=20,
                        max_tokens=500,
                        query=query,
                        session_id=session_id,
                        project_id=project_id,
                        cli_working_memory=cli_working_memory,
                    ) or ""
            else:
                memory_text = get_memories_for_context(
                    limit=20,
                    max_tokens=500,
                    query=query,
                    session_id=session_id,
                    project_id=project_id,
                    cli_working_memory=cli_working_memory,
                ) or ""
        except Exception:
            memory_text = ""
        memory_block = f"{memory_text}\n\n" if memory_text else ""

        desktop_block = ""
        try:
            from backend.utils.platform import screen_agent_available

            # Off Linux there is no agent screen; querying it only puts
            # "query failed" in the prompt, which small models repeat back.
            if screen_agent_available():
                from backend.services.agent_control_service import AgentControlService
                desktop = AgentControlService._get_desktop_state()
                if desktop:
                    desktop_block = f"Agent virtual screen state:\n{desktop}\n\n"
        except Exception:
            pass

        budget_block = ""
        if budget is not None:
            budget_block = (
                budget.to_llm_summary()
                + "\n\nUse budget status to plan efficient paths.\n\n"
            )

        facts_block = ""
        if getattr(facts_registry, "facts", None) and hasattr(facts_registry, "format_facts_for_prompt"):
            try:
                fb = facts_registry.format_facts_for_prompt()
                if fb:
                    facts_block = fb + "\n\nPrior extracted facts (use for synthesis).\n\n"
            except Exception:
                pass

        voice_suffix = self._VOICE_INSTRUCTION if is_voice_message else ""

        filled_prefix = (
            prefix
            .replace("{MEMORY_BLOCK}", memory_block)
            .replace("{DESKTOP_STATE}", desktop_block)
            + budget_block
            + facts_block
        )

        if role == "vision":
            vision_tools = ""
            if self.tool_registry:
                try:
                    vision_tools = self.tool_registry.get_tool_schemas(
                        format="json_prompt", tool_filter="vision"
                    )
                except Exception:
                    pass
            return (
                f"{filled_prefix}You are controlling a virtual screen (DISPLAY=:99) "
                f"with Firefox and a desktop environment.\n\n"
                f"Available Tools:\n{vision_tools}\n\n"
                "RULES:\n"
                "- Use agent_mode_start first, then agent_task_execute for screen tasks\n"
                "- Use agent_screen_capture to see what is on screen\n"
                "- NEVER fabricate information. Only state facts found in tool results\n"
                f"{voice_suffix}"
            )

        if role == "agent":
            from backend.services.agent_prompt_blocks import build_agent_prompt_tail
            return filled_prefix + build_agent_prompt_tail(
                tool_schemas=tool_list,
                native=native,
                screen=screen,
                agent_name=agent_name,
                agent_prompt=agent_prompt,
            ) + voice_suffix

        # chat role (Tier 2 / UCE)
        if skip_tools:
            return (
                f"{filled_prefix}Respond directly and conversationally. "
                "Be helpful, concise, and natural.\n"
                "You are a private, local AI assistant running on the user's own hardware."
                f"{voice_suffix}"
            )

        if not tool_list.strip() and self.tool_registry and query:
            try:
                from backend.services.unified_chat_engine import (
                    build_concise_tool_list,
                    select_tools_for_context,
                )
                fallback_names = select_tools_for_context(
                    query, self.tool_registry.list_tools()
                )
                tool_list = build_concise_tool_list(self.tool_registry, fallback_names)
            except Exception:
                pass

        if tool_list.strip():
            from backend.services.chat_prompt_blocks import build_chat_tools_prompt_tail
            return f"{filled_prefix}{build_chat_tools_prompt_tail(tool_list, voice_suffix)}"

        # Prefix-only when no tools selected (tests call without query; UCE always passes query=message)
        return f"{filled_prefix}{voice_suffix}"

    @staticmethod
    def _ollama_user_enabled() -> bool:
        """Return True if the user has Ollama enabled, False if disabled.

        Checks data/plugin_state.json's user_enabled overlay first; falls back
        to plugins/ollama/plugin.json's config.enabled if no override. Defaults
        fail-open (True) so a corrupt/missing state file doesn't break chat
        for users who never touched the plugin toggle.
        """
        try:
            import json as _json
            from pathlib import Path
            root = Path(__file__).resolve().parent.parent.parent
            state_file = root / "data" / "plugin_state.json"
            if state_file.exists():
                state = _json.loads(state_file.read_text()) or {}
                user_enabled = state.get("user_enabled", {})
                if "ollama" in user_enabled:
                    return bool(user_enabled["ollama"])
            manifest = root / "plugins" / "ollama" / "plugin.json"
            if manifest.exists():
                cfg = (_json.loads(manifest.read_text()) or {}).get("config", {})
                return bool(cfg.get("enabled", True))
        except Exception:
            pass
        return True

    def _start_warmup(self):
        """Send a throwaway prompt to force model into VRAM."""
        if os.environ.get("CELERY_WORKER_MODE", "false").lower() == "true":
            # A Celery worker never serves chat, and it builds the app inside
            # its first task, often a render that has just evicted Ollama.
            logger.info("BrainState: skipping warm-up in the Celery worker")
            self.health.warm_up_status = WarmUpStatus.READY
            return
        self.health.warm_up_status = WarmUpStatus.WARMING

        def _ping():
            try:
                import requests
                from backend.config import OLLAMA_BASE_URL
                from backend.utils.ollama_resource_manager import request_options
                # Use Ollama's canonical warmup primitive instead of llama_index
                # chat(): the latter's httpx stack silently timed out on slow
                # hardware even though Ollama itself was making progress. 15-min
                # read deadline covers cold-load on pre-AVX2 CPUs with HDDs.
                resp = requests.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": self.active_model,
                        "prompt": "ok",
                        "stream": False,
                        # Sized like the chat that follows, so this load is the one that serves it.
                        "options": request_options(self.active_model, num_predict=1),
                        "keep_alive": "30m",
                    },
                    timeout=(10.0, 900.0),
                )
                resp.raise_for_status()
                self.health.warm_up_status = WarmUpStatus.READY
                logger.info(f"Model warm-up complete: {self.active_model} is hot")
            except Exception as e:
                self.health.warm_up_status = WarmUpStatus.FAILED
                logger.warning(f"Model warm-up failed (non-blocking): {e}")

        self._warm_up_thread = threading.Thread(
            target=_ping, daemon=True, name="brain-warmup"
        )
        self._warm_up_thread.start()

    # -- Refresh ------------------------------------------------------------

    def refresh(self):
        """
        Refresh pre-computed state after a config change.

        Much faster than full initialize() -- rebuilds cached strings,
        doesn't re-import modules.
        """
        logger.info("BrainState refreshing...")
        start = time.monotonic()

        # Re-detect model (may have changed in Settings)
        try:
            if not self.lite_mode:
                self._detect_model_capabilities()
            else:
                self._detect_model_capabilities_lite()
            self.health.llm_available = self.llm is not None
        except Exception as e:
            logger.error(f"Model refresh failed: {e}")

        # Re-serialize tool schemas (tools may have changed)
        try:
            if self.tool_registry:
                self.tool_schemas_json = self.tool_registry.get_tool_schemas(
                    format="json_prompt"
                )
                self.tool_schemas_native = self.tool_registry.as_llama_index_tools()
        except Exception as e:
            logger.error(f"Tool schema refresh failed: {e}")

        self.refresh_prompts()

        # Rebuild reflexes (tools may have changed)
        try:
            self.reflexes = _build_default_reflexes(
                self.tool_registry if self.health.tools_available else None
            )
        except Exception as e:
            logger.error(f"Reflex refresh failed: {e}")

        # Warm up new model (respect Ollama plugin toggle)
        if self.health.llm_available and self._ollama_user_enabled():
            self._start_warmup()
        elif self.health.llm_available:
            self.health.warm_up_status = WarmUpStatus.READY

        elapsed = (time.monotonic() - start) * 1000
        logger.info(f"BrainState refreshed in {elapsed:.0f}ms")

    def refresh_prompts(self):
        """Re-render the system prompt prefixes only.

        The persona and rules block are baked into the prefix, so a Rules page
        save or a flip of the Rules switch calls this; the full ``refresh``
        also re-detects the model and warms it up, which a text edit does not
        warrant. Runs under the captured app context when none is active.
        """
        try:
            from flask import has_app_context
            needs_ctx = self._app is not None and not has_app_context()
        except Exception:
            needs_ctx = False
        try:
            if needs_ctx:
                with self._app.app_context():
                    self._build_system_prompts()
            else:
                self._build_system_prompts()
        except Exception as e:
            logger.error(f"System prompt refresh failed: {e}")

    # -- Reflex matching ----------------------------------------------------

    def match_reflex(self, message: str) -> Optional[Tuple[ReflexAction, "re.Match"]]:
        """
        Check message against the reflex table.  Returns (action, match) or None.

        Reflexes are context-free: they match on the current message alone.
        If the match is ambiguous without conversation history, it should
        not be in the reflex table.
        """
        stripped = message.strip()
        if not stripped:
            return None

        for reflex in self.reflexes:
            for pattern in reflex.patterns:
                m = pattern.fullmatch(stripped)
                if m:
                    return (reflex, m)
        return None

    # -- Convenience --------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        """True if at least reflexes are loaded (minimum viable state)."""
        return self._initialized and self.health.reflexes_loaded


def refresh_chat_prompts() -> bool:
    """Make a rule, prompt or Rules-switch change reach the next chat turn.

    Clears the rule lookup caches, then re-renders the brain's prompt prefix
    when the running app has an initialized BrainState. Returns True when the
    prefix was rebuilt. Never raises: a failed refresh must not fail the save
    that triggered it.
    """
    try:
        from backend import rule_utils
        rule_utils.clear_rule_caches()
    except Exception as e:
        logger.warning(f"Rule cache clear failed: {e}")
    try:
        from flask import current_app
        brain_state = getattr(current_app, "brain_state", None)
    except Exception:
        brain_state = None
    if brain_state is None or not getattr(brain_state, "_initialized", False):
        return False
    try:
        brain_state.refresh_prompts()
        return True
    except Exception as e:
        logger.warning(f"Chat prompt refresh failed: {e}")
        return False
