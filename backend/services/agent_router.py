#!/usr/bin/env python3
"""
Agent Router Service
Intelligent routing of user messages to appropriate tools and agents.
Replaces hardcoded detection patterns in ChatPage with dynamic tool-based routing.
"""

import logging
import re
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class RouteType(Enum):
    """Types of routing destinations"""
    TOOL_DIRECT = "tool_direct"      # Direct tool execution
    AGENT_LOOP = "agent_loop"        # Full agent reasoning loop
    CHAT_ONLY = "chat_only"          # Standard chat (no tools)
    FILE_GENERATION = "file_gen"     # File generation flow
    ORCHESTRATOR = "orchestrator"    # High-level task orchestration



@dataclass
class RouteDecision:
    """Result of routing decision"""
    route_type: RouteType
    tool_name: Optional[str] = None
    tool_params: Optional[Dict[str, Any]] = None
    confidence: float = 0.0
    reasoning: str = ""
    suggested_mode: Optional[str] = None


@dataclass
class IntentPattern:
    """Pattern for detecting user intent"""
    name: str
    patterns: List[str]
    route_type: RouteType
    tool_name: Optional[str] = None
    param_extractors: Optional[Dict[str, str]] = None  # param_name -> regex


# A file name typed in the request: "notes.md", "src/app.py". The extension
# starts with a letter, so "1.5" is not one, and a word inside a longer dotted
# name ("www.example.com") is not taken.
_FILENAME_RE = re.compile(r"(?<![\w./-])((?:[\w-]+/)*[\w-]+\.[A-Za-z][A-Za-z0-9]{0,7})(?!\.?[\w/])")

# Languages a file request may name, and the extension each one means. A
# language counts only as the kind of file ("a python file", "a bash script")
# or what it is written in ("in python"); "a file about python" names none.
_LANGUAGE_EXTENSIONS = {
    "python": "py", "javascript": "js", "typescript": "ts", "react": "jsx",
    "jsx": "jsx", "tsx": "tsx", "html": "html", "css": "css", "json": "json",
    "yaml": "yaml", "markdown": "md", "bash": "sh", "shell": "sh", "sql": "sql",
    "java": "java", "golang": "go", "rust": "rs", "ruby": "rb", "php": "php",
    "c++": "cpp", "xml": "xml", "toml": "toml", "text": "txt", "plain text": "txt",
}
_LANGUAGE_WORDS = "|".join(
    re.escape(word) for word in sorted(_LANGUAGE_EXTENSIONS, key=len, reverse=True)
)
_LANGUAGE_RE = re.compile(
    rf"(?<![\w+])({_LANGUAGE_WORDS})(?:\s+(?:code\s+)?(?:file|script|module|program|component|page|stylesheet|document|config))\b"
    rf"|\b(?:in|written\s+in|using)\s+({_LANGUAGE_WORDS})(?![\w+])",
    re.IGNORECASE,
)


def requested_file(message: str) -> Dict[str, str]:
    """The file a "create a file" request names: {"filename", "extension"}.

    A typed file name gives both; otherwise a named language gives the
    extension alone. Empty when the request names neither, and the caller
    picks its own default.
    """
    text = message or ""
    name = _FILENAME_RE.search(text)
    if name:
        filename = name.group(1)
        return {"filename": filename, "extension": filename.rsplit(".", 1)[1].lower()}
    language = _LANGUAGE_RE.search(text)
    if language:
        word = " ".join((language.group(1) or language.group(2)).lower().split())
        return {"extension": _LANGUAGE_EXTENSIONS[word]}
    return {}


# Room between a request's verb and its object: the same sentence, at most 60
# characters. An unbounded ".*" across a pasted paragraph read "to create depth
# ... typical of classic film noir" in an image prompt as "create ... class".
_GAP = r"(?:(?![.!?](?:\s|$))[^\n]){0,60}?"


def _near(verbs: str, objects: str) -> str:
    """A whole-word verb with a whole-word object later in the same sentence."""
    return rf"\b(?:{verbs})\b{_GAP}\b(?:{objects})\b"


# "make" asks for a file only as the request itself, opening the message or a
# sentence: "make me a csv of 10 cities", "can you make a python file that ...".
# "make sure the csv has headers" and "how do I make a csv in excel?" are not.
_MAKE_A = (
    r"(?:^|[.!?]\s+)(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?"
    r"make\s+(?:me\s+|us\s+)?an?\s+(?:[\w-]+\s+){0,3}?"
)


# Words after which the rest of a request is its topic, not the kind of thing
# asked for: "a poem about javascript", "a blog post about clean code", "a
# tutorial on python decorators" ask for prose.
_TOPIC_WORDS = r"about|on|regarding|concerning|explaining|describing"
_GAP_BEFORE_TOPIC = rf"(?:(?!\b(?:{_TOPIC_WORDS})\b)(?![.!?](?:\s|$))[^\n]){{0,60}}?"


def _near_object(verbs: str, objects: str) -> str:
    """Like _near, with the object named before any topic word: "write a
    python script about cats" matches python, "write a poem about python" does not."""
    return rf"\b(?:{verbs})\b{_GAP_BEFORE_TOPIC}\b(?:{objects})\b"


# A person saying the turn needs no tools or files: "do not generate anything",
# "no tool calling", "without tools", "don't create a file".
_DECLINES_TOOLS_RE = re.compile(
    r"\b(?:do\s+not|don[’']?t|dont)\s+(?:generate|create|make)\s+"
    r"(?:anything|any\s+files?|a\s+file|files?)\b"
    r"|\b(?:do\s+not|don[’']?t|dont)\s+(?:use|call)\s+(?:any\s+)?tools?\b"
    r"|\bno\s+tool(?:s|\s+calls?|\s+calling)?\b"
    r"|\bwithout\s+(?:any\s+|using\s+(?:any\s+)?)?tools?\b",
    re.IGNORECASE,
)


def declines_tools(message: str) -> bool:
    """True when the message asks for no tools or generated files this turn."""
    return bool(_DECLINES_TOOLS_RE.search(message or ""))


class AgentRouter:
    """
    Routes user messages to appropriate handling mechanisms.
    Uses pattern matching and optional LLM-based intent detection.
    """

    def __init__(self):
        self._tool_registry = None
        self._llm = None
        self._intent_patterns = self._build_intent_patterns()
        logger.info("AgentRouter initialized")

    def _get_tool_registry(self):
        """Lazy load tool registry"""
        if self._tool_registry is None:
            try:
                from backend.tools import initialize_all_tools
                self._tool_registry = initialize_all_tools()
            except Exception as e:
                logger.error(f"Failed to get tool registry: {e}")
        return self._tool_registry

    def _get_llm(self):
        """Lazy load LLM"""
        if self._llm is None:
            try:
                from backend.utils.llm_service import get_default_llm
                self._llm = get_default_llm()
            except Exception as e:
                logger.error(f"Failed to get LLM: {e}")
        return self._llm

    def _build_intent_patterns(self) -> List[IntentPattern]:
        """Build intent detection patterns from rules"""
        return [
            # WordPress/CSV Generation
            IntentPattern(
                name="wordpress_content",
                patterns=[
                    _near("generate", "wordpress"),
                    _near("create", "csv") + _GAP + r"\bwordpress\b",
                    _near("wordpress", "csv"),
                    r"/wordpress\b",
                ],
                route_type=RouteType.TOOL_DIRECT,
                tool_name="generate_wordpress_content"
            ),

            # Bulk CSV Generation
            IntentPattern(
                name="bulk_csv",
                patterns=[
                    r"\bgenerate\s+(\d+)\s+(?:pages?|rows?|entries)\b",
                    _near("bulk", "csv"),
                    _near("batch", "generate"),
                    r"/batchcsv\b",
                    r"\bcreate\s+(\d+)" + _GAP + r"\bcsv\b",
                ],
                route_type=RouteType.TOOL_DIRECT,
                tool_name="generate_bulk_csv",
                param_extractors={"quantity": r"(\d+)\s+(?:pages?|rows?|entries)"}
            ),

            # Existing source-code edits. Route these through the agent loop so
            # the model can read the file and prepare an exact edit_code patch.
            IntentPattern(
                name="source_code_edit",
                patterns=[
                    r"(?:modify|update|edit|change|fix|refactor).*"
                    r"(?:frontend/|backend/|src/|app/|plugins/|scripts/|tests/|"
                    r"[\w./-]+\.(?:py|js|jsx|ts|tsx|css|html|json|md|yml|yaml))",
                    r"(?:in|within)\s+[\w./-]+\.(?:py|js|jsx|ts|tsx|css|html|json|md|yml|yaml)",
                ],
                route_type=RouteType.AGENT_LOOP,
                tool_name="edit_code"
            ),

            # CSV Generation (single). Checked before the generic file pattern,
            # which also matches "create a CSV file" and would propose a .js name.
            IntentPattern(
                name="csv_generation",
                patterns=[
                    _near("create", "csv"),
                    _near("generate", "csv"),
                    _MAKE_A + r"csv\b",
                    r"/createcsv\b",
                ],
                route_type=RouteType.TOOL_DIRECT,
                tool_name="generate_csv"
            ),

            # File Generation
            IntentPattern(
                name="file_generation",
                patterns=[
                    _near("create", "files?"),
                    _near("generate", "files?"),
                    _MAKE_A + r"file\b",
                    r"\bwrite\b" + _GAP + r"\bto\s+(\w+\.\w+)",
                    r"/createfile\b",
                ],
                route_type=RouteType.FILE_GENERATION,
                tool_name="generate_file"
            ),

            # Code Generation
            IntentPattern(
                name="code_generation",
                patterns=[
                    _near_object("generate", "code"),
                    _near_object("write", "python|javascript|java|code"),
                    _near_object("create", "function|class|module"),
                    r"/codegen\b",
                    _near("modify", "file|code"),
                    # "improve/refactor/optimize/rewrite this file" — must be
                    # grounded in the real file, so route to codegen (reads
                    # input_file), never to generate_file (fabricates).
                    r"(?:improve|enhance|optimi[sz]e|refactor|rewrite|clean\s*up)\b"
                    r"[\w\s]*(?:file|code|\.[A-Za-z0-9]+)",
                ],
                route_type=RouteType.TOOL_DIRECT,
                tool_name="codegen"
            ),

            # Code Analysis
            IntentPattern(
                name="code_analysis",
                patterns=[
                    r"analyze.*code",
                    r"review.*code",
                    r"check.*(?:security|performance|style)",
                    r"what.*(?:does|is).*(?:this|the).*(?:code|file)",
                ],
                route_type=RouteType.TOOL_DIRECT,
                tool_name="analyze_code"
            ),

            # Browser Automation
            IntentPattern(
                name="browser_automation",
                patterns=[
                    r"(?i)take\s+(?:a\s+)?screenshot",
                    r"(?i)screenshot\s+(?:of\s+)?(?:https?://|[\w-]+\.[a-z]{2,})",
                    r"(?i)(?:scrape|extract)\s+(?:the\s+)?(?:data|text|links|content)\s+from",
                    r"(?i)navigate\s+to\s+(?:https?://|[\w-]+\.[a-z]{2,})",
                    # A site, not the end of a sentence ("an open doorway. High").
                    r"(?i)\bopen\s+(?:https?://|[\w-]+\.[a-z]{2,})",
                    r"(?i)fill\s+(?:out|in)\s+(?:the\s+)?form",
                    r"(?i)click\s+(?:the\s+)?(?:button|link|element)",
                    r"(?i)browser\s+automat",
                    r"(?i)web\s+scrap",
                    r"(?i)get\s+(?:the\s+)?html\s+(?:of|from)",
                    r"(?i)execute\s+javascript\s+on",
                    r"(?i)wait\s+for\s+(?:the\s+)?(?:element|page|button)",
                ],
                route_type=RouteType.AGENT_LOOP,
                tool_name=None
            ),

            # Desktop Automation
            IntentPattern(
                name="desktop_automation",
                patterns=[
                    r"(?i)watch\s+(?:the\s+|my\s+)?(?:folder|directory|file)",
                    r"(?i)(?:copy|move|delete)\s+(?:all\s+)?(?:files|pdfs|images)",
                    r"(?i)(?:bulk|batch)\s+(?:copy|move|delete|rename)",
                    r"(?i)launch\s+(?:the\s+)?\w+(?:\s+app)?",
                    r"(?i)open\s+(?:the\s+)?(?:app|application|program)\b",
                    r"(?i)(?:what'?s|get|read|copy)\s+(?:on\s+)?(?:my\s+)?clipboard",
                    r"(?i)(?:set|copy\s+to)\s+clipboard",
                    r"(?i)send\s+(?:a\s+|me\s+(?:a\s+)?)?notification",
                    r"(?i)(?:list|show)\s+(?:all\s+)?(?:running\s+)?(?:apps|applications|processes)",
                    r"(?i)focus\s+(?:the\s+)?\w+\s+window",
                    r"(?i)click\s+(?:at|on)\s+(?:the\s+)?screen",
                    r"(?i)type\s+(?:the\s+)?text",
                    r"(?i)press\s+(?:the\s+)?(?:hotkey|shortcut|key)",
                    r"(?i)desktop\s+automat",
                    r"(?i)gui\s+automat",
                ],
                route_type=RouteType.AGENT_LOOP,
                tool_name=None
            ),

            # Media Player Control — TOOL_DIRECT (no agent loop needed)
            IntentPattern(
                name="media_play",
                patterns=[
                    r"(?i)play\s+(?:some\s+|my\s+)?(?:music|song|songs|track|album|playlist)",
                    r"(?i)play\s+[\w\s]+(?:songs?|music|album|playlist|band|artist)",
                    r"(?i)^play\s+.+",
                ],
                route_type=RouteType.TOOL_DIRECT,
                tool_name="media_play"
            ),
            IntentPattern(
                name="media_control_action",
                patterns=[
                    r"(?i)(?:pause|stop|resume)\s+(?:the\s+)?(?:music|song|playback|player|audio)",
                    r"(?i)(?:next|skip|previous|prev)\s+(?:song|track)",
                    r"(?i)(?:skip|next)\s+(?:this\s+)?(?:song|track)",
                    r"(?i)^(?:pause|stop|resume)$",
                ],
                route_type=RouteType.TOOL_DIRECT,
                tool_name="media_control",
                param_extractors={"action": r"(?i)(pause|stop|resume|next|skip|previous|prev)"}
            ),
            IntentPattern(
                name="media_status",
                patterns=[
                    r"(?i)(?:what'?s|what\s+is)\s+(?:this\s+)?(?:playing|this\s+song)",
                    r"(?i)(?:current|now)\s+(?:playing|song|track)",
                ],
                route_type=RouteType.TOOL_DIRECT,
                tool_name="media_status"
            ),
            IntentPattern(
                name="media_volume",
                patterns=[
                    r"(?i)(?:turn|set)\s+(?:the\s+)?(?:volume|audio)\s+(?:up|down|to)",
                    r"(?i)(?:volume)\s+(?:up|down|\d+)",
                    r"(?i)(?:mute|unmute)\s+(?:the\s+)?(?:audio|sound|volume)",
                    r"(?i)(?:louder|quieter|softer)",
                    r"(?i)^(?:mute|unmute)$",
                ],
                route_type=RouteType.TOOL_DIRECT,
                tool_name="media_volume",
                param_extractors={"level": r"(?i)(?:volume\s+(?:to\s+)?|set\s+(?:the\s+)?volume\s+(?:to\s+)?)(\d+)|(?:volume\s+)(up|down)|^(mute|unmute)$|(louder|quieter|softer)"}
            ),

            # MCP Integration
            IntentPattern(
                name="mcp_automation",
                patterns=[
                    r"(?i)(?:list|show)\s+(?:available\s+)?mcp\s+server",
                    r"(?i)connect\s+(?:to\s+)?(?:the\s+)?(?:mcp|filesystem)\s+server",
                    r"(?i)disconnect\s+(?:from\s+)?(?:the\s+)?mcp",
                    r"(?i)(?:list|show)\s+mcp\s+tool",
                    r"(?i)(?:use|execute|run)\s+mcp",
                    r"(?i)mcp\s+(?:status|state)",
                ],
                route_type=RouteType.AGENT_LOOP,
                tool_name=None
            ),

            # Complex multi-step tasks requiring agent reasoning loop
            IntentPattern(
                name="complex_research",
                patterns=[
                    r"research\s+(?:and|then)\s+(?:create|generate|write)",
                    r"find\s+(?:information|data).*(?:and|then).*(?:create|generate)",
                    r"analyze.*(?:and|then).*(?:improve|optimize|refactor)",
                    r"compare.*(?:and|then).*(?:recommend|suggest)",
                ],
                route_type=RouteType.AGENT_LOOP,
                tool_name=None  # Agent decides which tools to use
            ),

            IntentPattern(
                name="multi_step_generation",
                patterns=[
                    r"(?:first|step\s*1).*(?:then|next|step\s*2)",
                    r"create.*based\s+on.*(?:analysis|research|data)",
                    r"generate.*(?:using|from).*(?:template|existing|previous)",
                ],
                route_type=RouteType.AGENT_LOOP,
                tool_name=None
            ),

            IntentPattern(
                name="intelligent_assistant",
                patterns=[
                    r"help\s+me\s+(?:figure\s+out|understand|decide)",
                    r"what.*(?:best|optimal|recommended).*(?:approach|way|method)",
                    r"how\s+(?:should|can|would)\s+(?:i|we).*(?:implement|build|create)",
                ],
                route_type=RouteType.AGENT_LOOP,
                tool_name=None
            ),

            IntentPattern(
                name="explicit_agent",
                patterns=[
                    r"/agent\b",
                    r"use\s+(?:the\s+)?agent",
                    r"agent\s+mode",
                    r"think\s+step\s*by\s*step",
                    r"reason\s+(?:through|about)",
                ],
                route_type=RouteType.AGENT_LOOP,
                tool_name=None
            ),

            # Explicit tool request
            IntentPattern(
                name="explicit_tool",
                patterns=[
                    r"use\s+tool\s+(\w+)",
                    r"run\s+tool\s+(\w+)",
                    r"execute\s+(\w+)\s+tool",
                ],
                route_type=RouteType.TOOL_DIRECT,
                tool_name=None,  # Extracted from pattern
                param_extractors={"tool_name": r"(?:use|run|execute)\s+(?:tool\s+)?(\w+)"}
            ),
        ]

    def route(self, message: str, context: Optional[Dict[str, Any]] = None) -> RouteDecision:
        """
        Analyze message and determine routing.

        Args:
            message: User's message
            context: Optional context (session info, project, etc.)

        Returns:
            RouteDecision with routing information
        """
        context = context or {}
        message_lower = message.lower().strip()

        # First, check for explicit command patterns (highest priority)
        if message_lower.startswith("/"):
            return self._handle_command(message, context)

        if declines_tools(message):
            return RouteDecision(
                route_type=RouteType.CHAT_ONLY,
                confidence=1.0,
                reasoning="The message asks for no tools or files",
            )

        # Check intent patterns
        for pattern in self._intent_patterns:
            match_result = self._match_pattern(message_lower, pattern)
            if match_result:
                if (match_result.route_type == RouteType.FILE_GENERATION
                        or match_result.tool_name == "codegen"):
                    # The name or language the person typed, case kept, so the
                    # proposed file is notes.md or a .py rather than a guess.
                    params = {**(match_result.tool_params or {}), **requested_file(message)}
                    match_result.tool_params = params or None
                return match_result

        # Check for file-related context that suggests generation
        if self._suggests_file_generation(message, context):
            return RouteDecision(
                route_type=RouteType.FILE_GENERATION,
                confidence=0.6,
                reasoning="Message suggests file generation based on context"
            )

        # Default to chat-only
        return RouteDecision(
            route_type=RouteType.CHAT_ONLY,
            confidence=1.0,
            reasoning="No specific tool pattern detected, routing to standard chat"
        )

    def _handle_command(self, message: str, context: Dict[str, Any]) -> RouteDecision:
        """Handle explicit slash commands"""
        parts = message.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        command_tool_map = {
            "/wordpress": ("generate_wordpress_content", RouteType.TOOL_DIRECT),
            "/wordpress_enhanced": ("generate_enhanced_wordpress_content", RouteType.TOOL_DIRECT),
            "/batchcsv": ("generate_bulk_csv", RouteType.TOOL_DIRECT),
            "/createfile": ("generate_file", RouteType.FILE_GENERATION),
            "/createcsv": ("generate_csv", RouteType.TOOL_DIRECT),
            "/codegen": ("codegen", RouteType.TOOL_DIRECT),
            "/agent": (None, RouteType.AGENT_LOOP),  # Full agent reasoning mode
            "/browser": (None, RouteType.AGENT_LOOP),  # Browser automation
            "/desktop": (None, RouteType.AGENT_LOOP),  # Desktop automation
            "/mcp": (None, RouteType.AGENT_LOOP),  # MCP integration
            "/media": ("media_play", RouteType.TOOL_DIRECT),  # Media player control
        }

        if command in command_tool_map:
            tool_name, route_type = command_tool_map[command]
            tool_params = {"args": args} if args else None
            if route_type == RouteType.FILE_GENERATION and args:
                tool_params.update(requested_file(args))
            return RouteDecision(
                route_type=route_type,
                tool_name=tool_name,
                tool_params=tool_params,
                confidence=1.0,
                reasoning=f"Explicit command: {command}"
            )

        return RouteDecision(
            route_type=RouteType.CHAT_ONLY,
            confidence=0.5,
            reasoning=f"Unknown command: {command}"
        )

    def _match_pattern(self, message: str, intent: IntentPattern) -> Optional[RouteDecision]:
        """Match message against intent pattern"""
        for pattern in intent.patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                # Extract parameters if configured
                params = {}
                if intent.param_extractors:
                    for param_name, extractor in intent.param_extractors.items():
                        param_match = re.search(extractor, message, re.IGNORECASE)
                        if param_match:
                            params[param_name] = param_match.group(1)

                # Handle dynamic tool name extraction
                tool_name = intent.tool_name
                if tool_name is None and "tool_name" in params:
                    tool_name = params.pop("tool_name")

                return RouteDecision(
                    route_type=intent.route_type,
                    tool_name=tool_name,
                    tool_params=params if params else None,
                    confidence=0.8,
                    reasoning=f"Matched pattern: {intent.name}"
                )

        return None

    def _suggests_file_generation(self, message: str, context: Dict[str, Any]) -> bool:
        """Check if message suggests file generation based on patterns"""
        file_indicators = [
            r"\.csv\b",
            r"\.py\b",
            r"\.js\b",
            r"\.jsx\b",
            r"\.ts\b",
            r"\.tsx\b",
            r"\.json\b",
            r"\.xml\b",
            r"\.html\b",
            r"\.md\b",
            r"save\s+(?:as|to)",
            r"download",
            r"export",
            r"output\s+file",
        ]

        message_lower = message.lower()
        return any(re.search(p, message_lower) for p in file_indicators)

    def execute_route(self, decision: RouteDecision, message: str,
                      context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Execute the routing decision.

        Args:
            decision: The routing decision
            message: Original user message
            context: Optional context

        Returns:
            Execution result
        """
        context = context or {}

        if decision.route_type == RouteType.CHAT_ONLY:
            return {
                "type": "chat",
                "requires_llm": True,
                "message": message
            }

        if decision.route_type == RouteType.TOOL_DIRECT:
            return self._execute_tool(decision, message, context)

        if decision.route_type == RouteType.FILE_GENERATION:
            return self._handle_file_generation(decision, message, context)

        if decision.route_type == RouteType.AGENT_LOOP:
            return self._execute_agent_loop(decision, message, context)

        if decision.route_type == RouteType.ORCHESTRATOR:
            return self._execute_orchestrator(decision, message, context)

        return {
            "type": "error",
            "error": f"Unknown route type: {decision.route_type}"
        }

    def _execute_tool(self, decision: RouteDecision, message: str,
                      context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a direct tool call"""
        registry = self._get_tool_registry()
        if not registry:
            return {
                "type": "error",
                "error": "Tool registry not available"
            }

        tool_name = decision.tool_name
        if not tool_name:
            return {
                "type": "error",
                "error": "No tool specified"
            }

        tool = registry.get_tool(tool_name)
        if not tool:
            return {
                "type": "error",
                "error": f"Tool '{tool_name}' not found"
            }

        # Build parameters from decision and context
        params = decision.tool_params or {}

        # Add context-based parameters
        if "client" in context and "client" not in params:
            params["client"] = context["client"]
        if "project_id" in context and "project_id" not in params:
            params["project_id"] = context["project_id"]

        # For tools that need the message as input
        if "content_description" not in params and "instructions" not in params:
            if tool_name in ["generate_file", "generate_csv"]:
                params["content_description"] = message
            elif tool_name == "codegen":
                params["instructions"] = message

        # Media tool parameter extraction from message
        if tool_name == "media_play":
            # Extract query: strip "play" prefix and common filler words
            query = re.sub(r"(?i)^(?:please\s+)?play\s+", "", message).strip()
            query = re.sub(r"(?i)\s+(?:for me|please)$", "", query).strip()
            params["query"] = query if query else "music"
        elif tool_name == "media_control":
            if "action" in params:
                # Normalize extracted action
                action = params["action"].lower()
                if action in ("skip",):
                    params["action"] = "next"
                elif action in ("resume",):
                    params["action"] = "toggle"
        elif tool_name == "media_volume":
            if "level" not in params or not params.get("level"):
                # Try to extract volume level from message
                vol_match = re.search(r"(\d+)", message)
                if vol_match:
                    params["level"] = vol_match.group(1)
                elif re.search(r"(?i)(?:up|louder)", message):
                    params["level"] = "+10"
                elif re.search(r"(?i)(?:down|quieter|softer)", message):
                    params["level"] = "-10"
                elif re.search(r"(?i)^mute", message):
                    params["level"] = "mute"
                elif re.search(r"(?i)^unmute", message):
                    params["level"] = "unmute"

        # Check for missing required parameters
        missing = []
        for param_name, param in tool.parameters.items():
            if param.required and param_name not in params:
                missing.append(param_name)

        if missing:
            return {
                "type": "needs_params",
                "tool_name": tool_name,
                "missing_params": missing,
                "tool_description": tool.description,
                "message": f"Tool '{tool_name}' needs more information: {', '.join(missing)}"
            }

        # Execute the tool
        try:
            result = registry.execute_tool(tool_name, **params)
            return {
                "type": "tool_result",
                "tool_name": tool_name,
                "result": result.to_dict()
            }
        except Exception as e:
            logger.error(f"Tool execution failed: {e}", exc_info=True)
            return {
                "type": "error",
                "error": f"Tool execution failed: {str(e)}"
            }

    def _handle_file_generation(self, decision: RouteDecision, message: str,
                                context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle file generation requests"""
        requested = requested_file(message)

        return {
            "type": "file_generation",
            "tool_name": decision.tool_name or "generate_file",
            "suggested_filename": requested.get("filename"),
            "extension": requested.get("extension"),
            "message": message,
            "show_file_dialog": True
        }

    def _execute_agent_loop(self, decision: RouteDecision, message: str,
                            context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute full agent reasoning loop using agent config system"""
        try:
            from backend.services.agent_executor import AgentExecutor
            from backend.services.agent_config import get_agent_config_manager

            # Get agent config manager to find the right agent
            manager = get_agent_config_manager()
            agent = manager.get_agent_for_message(message)
            
            if not agent:
                from backend.services.agent_config import NO_AGENT_MATCHES
                return {"type": "error", "error": NO_AGENT_MATCHES}
            
            if not agent.enabled:
                return {
                    "type": "error",
                    "error": f"Agent '{agent.id}' is disabled"
                }

            # Redirect to Orchestrator service if applicable
            from backend.services.agent_config import AgentType
            if agent.agent_type == AgentType.ORCHESTRATOR:
                logger.info(f"Redirecting agent '{agent.id}' to OrchestratorService")
                # Wrap as a decision to reuse _execute_orchestrator signature
                return self._execute_orchestrator(decision, message, context)

            from backend.services.agent_config import llm_for_agent
            registry = self._get_tool_registry()
            llm = llm_for_agent(agent) or self._get_llm()

            if not registry or not llm:
                return {
                    "type": "error",
                    "error": "Agent system not available"
                }

            agent_tool_registry, missing = registry.subset(agent.tools)
            if missing:
                logger.warning(f"Agent '{agent.id}' references tools that are not available: {missing}")

            if len(agent_tool_registry) == 0:
                return {
                    "type": "error",
                    "error": f"Agent '{agent.id}' has no available tools"
                }

            # Use args from tool_params if available (for /agent command)
            # Otherwise use the full message
            query = message
            if decision.tool_params and decision.tool_params.get("args"):
                query = decision.tool_params["args"]

            executor = AgentExecutor(
                agent_tool_registry, llm, max_iterations=agent.max_iterations, agent=agent
            )
            from backend.services.agent_prompt_blocks import user_context_line
            result = executor.execute(query, session_context=user_context_line(context))

            return {
                "type": "agent_result",
                "agent_used": agent.id,
                "final_answer": result.final_answer,
                "error": result.error,
                "steps": [
                    {
                        "iteration": s.iteration,
                        "thoughts": s.thoughts,
                        "tool_calls": s.tool_calls,
                        "observations": s.observations
                    }
                    for s in result.steps
                ],
                "iterations": result.iterations,
                "success": result.success,
                "verified": result.verified,
            }

        except Exception as e:
            logger.error(f"Agent execution failed: {e}", exc_info=True)
            return {
                "type": "error",
                "error": f"Agent execution failed: {str(e)}"
            }
    
    def _execute_generic_agent_loop(self, decision: RouteDecision, message: str,
                                     context: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback: Execute generic agent loop without specific agent config"""
        try:
            from backend.services.agent_executor import AgentExecutor

            registry = self._get_tool_registry()
            llm = self._get_llm()

            if not registry or not llm:
                return {
                    "type": "error",
                    "error": "Agent system not available"
                }

            # Use args from tool_params if available (for /agent command)
            # Otherwise use the full message
            query = message
            if decision.tool_params and decision.tool_params.get("args"):
                query = decision.tool_params["args"]

            executor = AgentExecutor(registry, llm, max_iterations=10)
            result = executor.execute(query, session_context=str(context))

            return {
                "type": "agent_result",
                "final_answer": result.final_answer,
                "error": result.error,
                "steps": [
                    {
                        "iteration": s.iteration,
                        "thoughts": s.thoughts,
                        "tool_calls": s.tool_calls,
                        "observations": s.observations
                    }
                    for s in result.steps
                ],
                "iterations": result.iterations,
                "success": result.success,
                "verified": result.verified,
            }

        except Exception as e:
            logger.error(f"Generic agent execution failed: {e}", exc_info=True)
            return {
                "type": "error",
                "error": f"Agent execution failed: {str(e)}"
            }


    def _execute_orchestrator(self, decision: RouteDecision, message: str,
                            context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the orchestrator service"""
        try:
            from backend.services.orchestrator_service import get_orchestrator
            orchestrator = get_orchestrator()
            
            result = orchestrator.process_request(message, context)
            
            return {
                "type": "orchestrator_result",
                "final_answer": result.get("final_answer"),
                "plan": result.get("plan"),
                "success": result.get("success", False),
                "error": result.get("error")
            }
            
        except Exception as e:
            logger.error(f"Orchestrator execution failed: {e}", exc_info=True)
            return {
                "type": "error",
                "error": f"Orchestrator execution failed: {str(e)}"
            }

# Global router instance
_router: Optional[AgentRouter] = None


def get_agent_router() -> AgentRouter:
    """Get or create the global agent router"""
    global _router
    if _router is None:
        _router = AgentRouter()
    return _router


def route_message(message: str, context: Optional[Dict[str, Any]] = None) -> RouteDecision:
    """Convenience function to route a message.
    DEPRECATED/BRIDGE: Prefer AgentBrain.process (via unified_chat_api or direct) + useAgentRouter hook.
    Legacy patterns kept for /tools/route compat during unification (per PHASE2 plan).
    """
    import warnings
    warnings.warn("agent_router.route_message is legacy/bridge; use AgentBrain + memory/STA contracts instead", DeprecationWarning, stacklevel=2)
    router = get_agent_router()
    return router.route(message, context)


# Requests that name the agent loop itself. Every other AGENT_LOOP match
# (intelligent_assistant, complex_research, ...) is an everyday question that
# unified chat answers with RAG, memory and approval cards.
EXPLICIT_AGENT_COMMANDS = frozenset({"/agent", "/browser", "/desktop", "/mcp"})
EXPLICIT_AGENT_PATTERNS = frozenset({"explicit_agent", "mcp_automation"})


def is_explicit_agent_request(message: str, decision: RouteDecision) -> bool:
    """True when the message is an agent slash command or matched a pattern
    that asks for the agent or MCP by name (reasoning from _match_pattern)."""
    words = (message or "").strip().split(maxsplit=1)
    if words and words[0].lower() in EXPLICIT_AGENT_COMMANDS:
        return True
    return any(
        decision.reasoning == f"Matched pattern: {name}"
        for name in EXPLICIT_AGENT_PATTERNS
    )


def execute_routed_message(message: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Convenience function to route and execute a message.
    DEPRECATED/BRIDGE: Prefer AgentBrain + unified paths (see PHASE2_TIGHTENED_PLAN).
    """
    import warnings
    warnings.warn("agent_router.execute_routed_message is legacy/bridge; route through AgentBrain.process for memory/lessons/STA awareness", DeprecationWarning, stacklevel=2)
    router = get_agent_router()
    decision = router.route(message, context)
    return router.execute_route(decision, message, context)
