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

    TOOL_DIRECT = "tool_direct"  # Direct tool execution
    AGENT_LOOP = "agent_loop"  # Full agent reasoning loop
    CHAT_ONLY = "chat_only"  # Standard chat (no tools)
    FILE_GENERATION = "file_gen"  # File generation flow
    ORCHESTRATOR = "orchestrator"  # High-level task orchestration


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
                    r"generate.*wordpress",
                    r"create.*csv.*wordpress",
                    r"wordpress.*csv",
                    r"/wordpress\b",
                ],
                route_type=RouteType.TOOL_DIRECT,
                tool_name="generate_wordpress_content",
            ),
            # Bulk CSV Generation
            IntentPattern(
                name="bulk_csv",
                patterns=[
                    r"generate\s+(\d+)\s+(?:pages?|rows?|entries)",
                    r"bulk.*csv",
                    r"batch.*generate",
                    r"/batchcsv\b",
                    r"create\s+(\d+).*csv",
                ],
                route_type=RouteType.TOOL_DIRECT,
                tool_name="generate_bulk_csv",
                param_extractors={"quantity": r"(\d+)\s+(?:pages?|rows?|entries)"},
            ),
            # File Generation
            IntentPattern(
                name="file_generation",
                patterns=[
                    r"create.*file",
                    r"generate.*file",
                    r"write.*to\s+(\w+\.\w+)",
                    r"/createfile\b",
                ],
                route_type=RouteType.FILE_GENERATION,
                tool_name="generate_file",
            ),
            # CSV Generation (single)
            IntentPattern(
                name="csv_generation",
                patterns=[
                    r"create.*csv",
                    r"generate.*csv",
                    r"/createcsv\b",
                ],
                route_type=RouteType.TOOL_DIRECT,
                tool_name="generate_csv",
            ),
            # Code Generation
            IntentPattern(
                name="code_generation",
                patterns=[
                    r"generate.*code",
                    r"write.*(?:python|javascript|java|code)",
                    r"create.*(?:function|class|module)",
                    r"/codegen\b",
                    r"modify.*(?:file|code)",
                ],
                route_type=RouteType.TOOL_DIRECT,
                tool_name="codegen",
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
                tool_name="analyze_code",
            ),
            # Browser Automation
            IntentPattern(
                name="browser_automation",
                patterns=[
                    r"(?i)take\s+(?:a\s+)?screenshot",
                    r"(?i)screenshot\s+(?:of\s+)?(?:https?://|\w+\.)",
                    r"(?i)(?:scrape|extract)\s+(?:the\s+)?(?:data|text|links|content)\s+from",
                    r"(?i)navigate\s+to\s+(?:https?://|\w+\.)",
                    r"(?i)open\s+(?:https?://|\w+\.)",
                    r"(?i)fill\s+(?:out|in)\s+(?:the\s+)?form",
                    r"(?i)click\s+(?:the\s+)?(?:button|link|element)",
                    r"(?i)browser\s+automat",
                    r"(?i)web\s+scrap",
                    r"(?i)get\s+(?:the\s+)?html\s+(?:of|from)",
                    r"(?i)execute\s+javascript\s+on",
                    r"(?i)wait\s+for\s+(?:the\s+)?(?:element|page|button)",
                ],
                route_type=RouteType.AGENT_LOOP,
                tool_name=None,
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
                tool_name=None,
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
                tool_name="media_play",
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
                param_extractors={
                    "action": r"(?i)(pause|stop|resume|next|skip|previous|prev)"
                },
            ),
            IntentPattern(
                name="media_status",
                patterns=[
                    r"(?i)(?:what'?s|what\s+is)\s+(?:this\s+)?(?:playing|this\s+song)",
                    r"(?i)(?:current|now)\s+(?:playing|song|track)",
                ],
                route_type=RouteType.TOOL_DIRECT,
                tool_name="media_status",
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
                param_extractors={
                    "level": r"(?i)(?:volume\s+(?:to\s+)?|set\s+(?:the\s+)?volume\s+(?:to\s+)?)(\d+)|(?:volume\s+)(up|down)|^(mute|unmute)$|(louder|quieter|softer)"
                },
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
                tool_name=None,
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
                tool_name=None,  # Agent decides which tools to use
            ),
            IntentPattern(
                name="multi_step_generation",
                patterns=[
                    r"(?:first|step\s*1).*(?:then|next|step\s*2)",
                    r"create.*based\s+on.*(?:analysis|research|data)",
                    r"generate.*(?:using|from).*(?:template|existing|previous)",
                ],
                route_type=RouteType.AGENT_LOOP,
                tool_name=None,
            ),
            IntentPattern(
                name="intelligent_assistant",
                patterns=[
                    r"help\s+me\s+(?:figure\s+out|understand|decide)",
                    r"what.*(?:best|optimal|recommended).*(?:approach|way|method)",
                    r"how\s+(?:should|can|would)\s+(?:i|we).*(?:implement|build|create)",
                ],
                route_type=RouteType.AGENT_LOOP,
                tool_name=None,
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
                tool_name=None,
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
                param_extractors={
                    "tool_name": r"(?:use|run|execute)\s+(?:tool\s+)?(\w+)"
                },
            ),
        ]

    def route(
        self, message: str, context: Optional[Dict[str, Any]] = None
    ) -> RouteDecision:
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

        # Check intent patterns
        for pattern in self._intent_patterns:
            match_result = self._match_pattern(message_lower, pattern)
            if match_result:
                return match_result

        # Check for file-related context that suggests generation
        if self._suggests_file_generation(message, context):
            return RouteDecision(
                route_type=RouteType.FILE_GENERATION,
                confidence=0.6,
                reasoning="Message suggests file generation based on context",
            )

        # Default to chat-only
        return RouteDecision(
            route_type=RouteType.CHAT_ONLY,
            confidence=1.0,
            reasoning="No specific tool pattern detected, routing to standard chat",
        )

    def _handle_command(self, message: str, context: Dict[str, Any]) -> RouteDecision:
        """Handle explicit slash commands"""
        parts = message.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        command_tool_map = {
            "/wordpress": ("generate_wordpress_content", RouteType.TOOL_DIRECT),
            "/wordpress_enhanced": (
                "generate_enhanced_wordpress_content",
                RouteType.TOOL_DIRECT,
            ),
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
            return RouteDecision(
                route_type=route_type,
                tool_name=tool_name,
                tool_params={"args": args} if args else None,
                confidence=1.0,
                reasoning=f"Explicit command: {command}",
            )

        return RouteDecision(
            route_type=RouteType.CHAT_ONLY,
            confidence=0.5,
            reasoning=f"Unknown command: {command}",
        )

    def _match_pattern(
        self, message: str, intent: IntentPattern
    ) -> Optional[RouteDecision]:
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
                    reasoning=f"Matched pattern: {intent.name}",
                )

        return None

    def _suggests_file_generation(self, message: str, context: Dict[str, Any]) -> bool:
        """Check if message suggests file generation based on patterns"""
        file_indicators = [
            r"\.csv\b",
            r"\.py\b",
            r"\.js\b",
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

    def execute_route(
        self,
        decision: RouteDecision,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
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
            return {"type": "chat", "requires_llm": True, "message": message}

        if decision.route_type == RouteType.TOOL_DIRECT:
            return self._execute_tool(decision, message, context)

        if decision.route_type == RouteType.FILE_GENERATION:
            return self._handle_file_generation(decision, message, context)

        if decision.route_type == RouteType.AGENT_LOOP:
            return self._execute_agent_loop(decision, message, context)

        if decision.route_type == RouteType.ORCHESTRATOR:
            return self._execute_orchestrator(decision, message, context)

        return {"type": "error", "error": f"Unknown route type: {decision.route_type}"}

    def _execute_tool(
        self, decision: RouteDecision, message: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a direct tool call"""
        registry = self._get_tool_registry()
        if not registry:
            return {"type": "error", "error": "Tool registry not available"}

        tool_name = decision.tool_name
        if not tool_name:
            return {"type": "error", "error": "No tool specified"}

        tool = registry.get_tool(tool_name)
        if not tool:
            return {"type": "error", "error": f"Tool '{tool_name}' not found"}

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
                "message": f"Tool '{tool_name}' needs more information: {', '.join(missing)}",
            }

        # Execute the tool
        try:
            result = registry.execute_tool(tool_name, **params)
            return {
                "type": "tool_result",
                "tool_name": tool_name,
                "result": result.to_dict(),
            }
        except Exception as e:
            logger.error(f"Tool execution failed: {e}", exc_info=True)
            return {"type": "error", "error": f"Tool execution failed: {str(e)}"}

    def _handle_file_generation(
        self, decision: RouteDecision, message: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle file generation requests"""
        # Extract filename if present
        filename_match = re.search(r"(\w+\.\w+)", message)
        filename = filename_match.group(1) if filename_match else None

        return {
            "type": "file_generation",
            "tool_name": decision.tool_name or "generate_file",
            "suggested_filename": filename,
            "message": message,
            "show_file_dialog": True,
        }

    def _execute_agent_loop(
        self, decision: RouteDecision, message: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute full agent reasoning loop using agent config system"""
        try:
            from backend.services.agent_executor import AgentExecutor
            from backend.services.agent_config import get_agent_config_manager

            # Get agent config manager to find the right agent
            manager = get_agent_config_manager()
            agent = manager.get_agent_for_message(message)

            if not agent:
                # Fallback to generic executor if no agent matches
                logger.warning("No matching agent found, using generic executor")
                return self._execute_generic_agent_loop(decision, message, context)

            if not agent.enabled:
                return {"type": "error", "error": f"Agent '{agent.id}' is disabled"}

            # Redirect to Orchestrator service if applicable
            from backend.services.agent_config import AgentType

            if agent.agent_type == AgentType.ORCHESTRATOR:
                logger.info(f"Redirecting agent '{agent.id}' to OrchestratorService")
                # Wrap as a decision to reuse _execute_orchestrator signature
                return self._execute_orchestrator(decision, message, context)

            # Get tool registry
            registry = self._get_tool_registry()
            llm = self._get_llm()

            if not registry or not llm:
                return {"type": "error", "error": "Agent system not available"}

            # Filter tool registry to only agent's assigned tools (like /api/agents/execute does)
            from backend.services.agent_tools import ToolRegistry

            agent_tool_registry = ToolRegistry()
            for tool_name in agent.tools:
                tool = registry.get_tool(tool_name)
                if tool:
                    agent_tool_registry.register(tool)
                else:
                    logger.warning(
                        f"Agent '{agent.id}' references tool '{tool_name}' which is not available"
                    )

            if len(agent_tool_registry) == 0:
                return {
                    "type": "error",
                    "error": f"Agent '{agent.id}' has no available tools",
                }

            # Use args from tool_params if available (for /agent command)
            # Otherwise use the full message
            query = message
            if decision.tool_params and decision.tool_params.get("args"):
                query = decision.tool_params["args"]

            # Create executor with agent's max_iterations
            executor = AgentExecutor(
                agent_tool_registry, llm, max_iterations=agent.max_iterations
            )

            # Build session context with agent's system prompt (like /api/agents/execute does)
            session_context = f"""Agent: {agent.name}
System: {agent.system_prompt}

User Context: {str(context)}"""

            result = executor.execute(query, session_context=session_context)

            return {
                "type": "agent_result",
                "agent_used": agent.id,
                "final_answer": result.final_answer,
                "steps": [
                    {
                        "iteration": s.iteration,
                        "thoughts": s.thoughts,
                        "tool_calls": s.tool_calls,
                        "observations": s.observations,
                    }
                    for s in result.steps
                ],
                "iterations": result.iterations,
                "success": result.success,
            }

        except Exception as e:
            logger.error(f"Agent execution failed: {e}", exc_info=True)
            return {"type": "error", "error": f"Agent execution failed: {str(e)}"}

    def _execute_generic_agent_loop(
        self, decision: RouteDecision, message: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Fallback: Execute generic agent loop without specific agent config"""
        try:
            from backend.services.agent_executor import AgentExecutor

            registry = self._get_tool_registry()
            llm = self._get_llm()

            if not registry or not llm:
                return {"type": "error", "error": "Agent system not available"}

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
                "steps": [
                    {
                        "iteration": s.iteration,
                        "thoughts": s.thoughts,
                        "tool_calls": s.tool_calls,
                        "observations": s.observations,
                    }
                    for s in result.steps
                ],
                "iterations": result.iterations,
                "success": result.success,
            }

        except Exception as e:
            logger.error(f"Generic agent execution failed: {e}", exc_info=True)
            return {"type": "error", "error": f"Agent execution failed: {str(e)}"}

    def _execute_orchestrator(
        self, decision: RouteDecision, message: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
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
                "error": result.get("error"),
            }

        except Exception as e:
            logger.error(f"Orchestrator execution failed: {e}", exc_info=True)
            return {
                "type": "error",
                "error": f"Orchestrator execution failed: {str(e)}",
            }


# Global router instance
_router: Optional[AgentRouter] = None


def get_agent_router() -> AgentRouter:
    """Get or create the global agent router"""
    global _router
    if _router is None:
        _router = AgentRouter()
    return _router


def route_message(
    message: str, context: Optional[Dict[str, Any]] = None
) -> RouteDecision:
    """Convenience function to route a message"""
    router = get_agent_router()
    return router.route(message, context)


def execute_routed_message(
    message: str, context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Convenience function to route and execute a message"""
    router = get_agent_router()
    decision = router.route(message, context)
    return router.execute_route(decision, message, context)
