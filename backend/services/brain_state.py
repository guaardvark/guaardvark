#!/usr/bin/env python3
"""
BrainState — Singleton holding all pre-computed agent state.

Initialized once at backend startup.  Refreshed only when the active model
changes, a plugin starts/stops, or an explicit refresh is requested.

Every field that used to be rebuilt per-request in the old pipeline lives
here instead: tool schemas, system prompts, model capabilities, and the
compiled reflex table.
"""

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


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


@dataclass
class TierTelemetry:
    """Captured per interaction for analytics and future auto-reflex promotion."""
    tier: int
    latency_ms: int
    tools_called: List[str] = field(default_factory=list)
    tool_params: List[Dict] = field(default_factory=list)
    escalated_from: Optional[int] = None
    escalation_reason: Optional[str] = None
    message_hash: str = ""
    success: bool = True
    model: str = ""
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "latency_ms": self.latency_ms,
            "tools_called": self.tools_called,
            "tool_params": self.tool_params,
            "escalated_from": self.escalated_from,
            "escalation_reason": self.escalation_reason,
            "message_hash": self.message_hash,
            "success": self.success,
            "model": self.model,
            "timestamp": self.timestamp,
        }

    @staticmethod
    def hash_message(message: str) -> str:
        """One-way hash so telemetry never stores raw user messages."""
        normalized = message.strip().lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Greeting response pool (personality-aware, rotated)
# ---------------------------------------------------------------------------

_GREETING_POOL = [
    "Hey! What can I do for you?",
    "Hey there! What are we working on?",
    "What's up? Ready when you are.",
    "Hi! What do you need?",
    "Hey! Let's get to it.",
]

_FAREWELL_POOL = [
    "Later! Hit me up anytime.",
    "See you around!",
    "Catch you later!",
    "Peace! I'll be here.",
]

_THANKS_POOL = [
    "You got it!",
    "Anytime!",
    "No problem!",
    "Happy to help!",
]

_pool_counters: Dict[str, int] = {"greeting": 0, "farewell": 0, "thanks": 0}
_pool_lock = threading.Lock()


def _rotate_response(pool: List[str], pool_name: str) -> str:
    """Return the next response from the pool, rotating through them."""
    with _pool_lock:
        idx = _pool_counters.get(pool_name, 0) % len(pool)
        _pool_counters[pool_name] = idx + 1
        return pool[idx]


# ---------------------------------------------------------------------------
# Default reflex table
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
            action_map = {"skip": "next", "prev": "previous"}
            return {"action": action_map.get(action, action)}

        def _extract_play_query(message: str, match: "re.Match") -> Dict:
            # Everything after "play " is the query
            query = re.sub(r"(?i)^play\s+", "", message).strip()
            return {"query": query} if query else {}

        def _extract_volume(message: str, match: "re.Match") -> Dict:
            vol_match = re.search(r"(\d+)", message)
            if vol_match:
                return {"level": int(vol_match.group(1))}
            for word, val in [("up", "up"), ("down", "down"),
                              ("louder", "up"), ("quieter", "down"),
                              ("softer", "down"), ("mute", "mute"),
                              ("unmute", "unmute")]:
                if word in message.lower():
                    return {"level": val}
            return {}

        # Only add media reflexes if the tools exist
        if tool_registry.get_tool("media_play"):
            reflexes.append(ReflexAction(
                name="media_play",
                patterns=[
                    re.compile(r"(?i)^play\s+.+"),
                ],
                handler=_media_reflex("media_play", _extract_play_query),
                priority=10,
            ))

        if tool_registry.get_tool("media_control"):
            reflexes.append(ReflexAction(
                name="media_control",
                patterns=[
                    re.compile(r"(?i)^(pause|stop|resume|next|skip|previous|prev)(?:\s+(?:the\s+)?(?:music|song|track|playback|player))?[.!]?$"),
                ],
                handler=_media_reflex("media_control", _extract_media_action),
                priority=10,
            ))

        if tool_registry.get_tool("media_volume"):
            reflexes.append(ReflexAction(
                name="media_volume",
                patterns=[
                    re.compile(r"(?i)(?:volume\s+(?:up|down|\d+)|(?:turn|set)\s+(?:the\s+)?volume|(?:louder|quieter|softer)|^(?:mute|unmute)$)"),
                ],
                handler=_media_reflex("media_volume", _extract_volume),
                priority=10,
            ))

        if tool_registry.get_tool("media_status"):
            reflexes.append(ReflexAction(
                name="media_status",
                patterns=[
                    re.compile(r"(?i)(?:what'?s|what\s+is)\s+(?:this\s+)?(?:playing|this\s+song)|(?:current|now)\s+(?:playing|song|track)"),
                ],
                handler=_media_reflex("media_status"),
                priority=10,
            ))

    # -- Greeting reflexes (always available, even in lite mode) --

    reflexes.append(ReflexAction(
        name="greeting",
        patterns=[
            re.compile(r"(?i)^(h(ello|i|ey|owdy|ola)|yo|sup|what'?s up|good (morning|afternoon|evening|night)|how are you|how'?s it going|how do you do)[?!.,\s]*$"),
        ],
        handler=lambda msg, match, ctx: ReflexResult(
            response=_rotate_response(_GREETING_POOL, "greeting"),
            success=True,
        ),
        priority=90,
    ))

    reflexes.append(ReflexAction(
        name="farewell",
        patterns=[
            re.compile(r"(?i)^(bye|goodbye|see ya|later|good night|peace|peace out|cya|ttyl)[?!.,\s]*$"),
        ],
        handler=lambda msg, match, ctx: ReflexResult(
            response=_rotate_response(_FAREWELL_POOL, "farewell"),
            success=True,
        ),
        priority=90,
    ))

    reflexes.append(ReflexAction(
        name="thanks",
        patterns=[
            re.compile(r"(?i)^(thanks?( you)?|thank you( so much)?|ty|thx|appreciate it)[?!.,\s]*$"),
        ],
        handler=lambda msg, match, ctx: ReflexResult(
            response=_rotate_response(_THANKS_POOL, "thanks"),
            success=True,
        ),
        priority=90,
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

    @classmethod
    def get_instance(cls) -> "BrainState":
        """Get or create the singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
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

        # Step 7: Warm-up ping (background thread)
        if self.health.llm_available:
            self._start_warmup()

        elapsed = (time.monotonic() - start) * 1000
        self._initialized = True
        logger.info(f"BrainState initialized in {elapsed:.0f}ms | health={self.health.to_dict()}")

    def _detect_model_capabilities(self):
        """Detect model capabilities from Ollama (full mode)."""
        from backend.utils.llm_service import get_default_llm
        from backend.utils.ollama_resource_manager import (
            is_vision_model,
            model_supports_tools,
        )

        self.llm = get_default_llm()
        model_name = getattr(self.llm, "model", "unknown")
        self.active_model = model_name

        # Thinking model detection (matches unified_chat_engine.py patterns)
        thinking_patterns = ["qwen3", "deepseek-r1", "thinking", "gemma4", "gemma-4"]
        is_thinking = any(p in model_name.lower() for p in thinking_patterns)

        self.model_caps = ModelCapabilities(
            name=model_name,
            supports_native_tools=model_supports_tools(model_name),
            is_thinking_model=is_thinking,
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
        """Pre-render system prompts for each tier."""

        # Load rules persona (user's custom system prompt)
        persona = ""
        try:
            from backend.utils.chat_utils import get_active_system_prompt
            persona = get_active_system_prompt() or ""
        except Exception:
            pass

        # Honesty steering (baked in, not injected per-request)
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

        # Load saved memories into context
        memory_block = ""
        try:
            from backend.api.memory_api import get_memories_for_context
            memory_text = get_memories_for_context(limit=20, max_tokens=500)
            if memory_text:
                memory_block = memory_text + "\n\n"
        except Exception:
            pass  # Memory system unavailable — no impact on chat

        if memory_block:
            prefix += memory_block

        # Inject agent desktop state so the LLM knows what's on the
        # virtual screen before deciding what tools to call
        try:
            from backend.services.agent_control_service import AgentControlService
            desktop = AgentControlService._get_desktop_state()
            if desktop:
                prefix += f"Agent virtual screen state:\n{desktop}\n\n"
        except Exception:
            pass  # Agent display not running — no impact

        # -- Chat prompt (Tier 2) --
        tool_block = ""
        if self.tool_schemas_json:
            tool_block = f"""
You have access to tools. When you need to use a tool, respond with a JSON object:
{{"thoughts": "your reasoning", "tool_calls": [{{"tool_name": "name", "parameters": {{...}}}}], "final_answer": null}}

When you have the answer (no tools needed):
{{"thoughts": "reasoning", "tool_calls": [], "final_answer": "your answer"}}

Available Tools:
{self.tool_schemas_json}

"""

        self.system_prompts["chat"] = f"""{prefix}You are an AI assistant. Help the user by answering questions and using tools when needed.

{tool_block}RULES:
- Use exact parameter names from tool descriptions
- After tool results, use them to formulate your answer
- Only state facts found in tool results or your knowledge
- NEVER fabricate information
- If you cannot find the answer, say so honestly
- If a tool fails, try a DIFFERENT tool or different parameters"""

        # -- Vision prompt (Tier 2 vision) --
        vision_tools = ""
        if self.tool_registry:
            try:
                vision_tools = self.tool_registry.get_tool_schemas(
                    format="json_prompt", tool_filter="vision"
                )
            except Exception:
                pass

        self.system_prompts["vision"] = f"""{prefix}You are controlling a virtual screen (DISPLAY=:99) with Firefox and a desktop environment.

Available Tools:
{vision_tools}

RULES:
- Use agent_mode_start first, then agent_task_execute to perform screen tasks
- Use agent_screen_capture to see what is currently on screen
- Do NOT use browser_navigate, browser_execute_js, app_launch, or analyze_website
- Break complex tasks into small steps: first capture the screen, then one action at a time
- NEVER fabricate information. Only state facts found in tool results
- If you cannot complete the task, say so honestly"""

        # -- Agent prompt (Tier 3 ReACT) --
        self.system_prompts["agent"] = f"""{prefix}You are an AI assistant with access to tools. Help the user by using tools when needed.

Available Tools:
{self.tool_schemas_json}

RESPONSE FORMAT:
You MUST respond with a JSON object. Every response must have these three fields:
- "thoughts": your reasoning about what to do (string or null)
- "tool_calls": array of tool calls to execute (empty array if none needed)
- "final_answer": your final answer to the user (string or null)

Each tool call object has: "tool_name" (string), "parameters" (object), and optional "reasoning" (string).

RULES:
- Use exact parameter names from the tool descriptions
- Include ALL required parameters
- After tool results, use them to formulate your answer
- Only state facts found in tool results
- When you have enough information, set final_answer
- If a tool fails, try a DIFFERENT tool or different parameters. Never retry the same call.
- NEVER fabricate information. Only state facts found in tool results.
- If you cannot find the answer, say so honestly."""

    # -- Warm-up ping -------------------------------------------------------

    def _start_warmup(self):
        """Send a throwaway prompt to force model into VRAM."""
        self.health.warm_up_status = WarmUpStatus.WARMING

        def _ping():
            try:
                from backend.utils.llm_service import ChatMessage, MessageRole
                messages = [
                    ChatMessage(role=MessageRole.USER, content="ping"),
                ]
                self.llm.chat(messages)
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

        # Re-render system prompts
        try:
            self._build_system_prompts()
        except Exception as e:
            logger.error(f"System prompt refresh failed: {e}")

        # Rebuild reflexes (tools may have changed)
        try:
            self.reflexes = _build_default_reflexes(
                self.tool_registry if self.health.tools_available else None
            )
        except Exception as e:
            logger.error(f"Reflex refresh failed: {e}")

        # Warm up new model
        if self.health.llm_available:
            self._start_warmup()

        elapsed = (time.monotonic() - start) * 1000
        logger.info(f"BrainState refreshed in {elapsed:.0f}ms")

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
                m = pattern.search(stripped)
                if m:
                    return (reflex, m)
        return None

    # -- Convenience --------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        """True if at least reflexes are loaded (minimum viable state)."""
        return self._initialized and self.health.reflexes_loaded

    def get_system_prompt(self, context: str = "chat") -> str:
        """Get pre-rendered system prompt by context key."""
        return self.system_prompts.get(context, self.system_prompts.get("chat", ""))
