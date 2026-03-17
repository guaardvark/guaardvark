#!/usr/bin/env python3
"""
Agent Executor
Implements ReACT (Reasoning, Action, Observation) agent loop
"""

import logging
import threading
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
import re

from backend.services.agent_tools import ToolRegistry, ToolResult
from backend.utils.agent_output_parser import (
    parse_tool_calls_structured,
    format_tool_result_for_llm,
    ToolCallResponse,
)

from backend.utils.llm_debug_logger import (
    log_system_prompt,
    log_user_message,
    log_llm_response,
    log_tool_call,
    log_tool_result,
    log_guard_event,
    log_decision,
)

logger = logging.getLogger(__name__)


def _safe_content(message) -> str:
    """Extract content from a LlamaIndex ChatMessage, handling multi-block (thinking) models."""
    if not message:
        return ""
    try:
        return str(message.content)
    except (ValueError, AttributeError):
        blocks = getattr(message, "blocks", [])
        for block in blocks:
            text = getattr(block, "text", str(block) if block else "")
            if text:
                return text
        if blocks:
            return str(blocks[0])
        thinking = getattr(message, "thinking", None)
        if thinking:
            return str(thinking)
        return ""


@dataclass
class ExtractedFact:
    """A fact extracted from tool observations"""

    content: str
    source_tool: str
    confidence: float
    iteration: int
    raw_evidence: str
    fact_id: int = 0  # Sequential ID for citation


@dataclass
class AgentStep:
    """Single step in agent execution"""

    iteration: int
    thoughts: Optional[str] = None
    tool_calls: List[Any] = field(default_factory=list)
    observations: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: str = ""


@dataclass
class AgentResult:
    """Final result from agent execution"""

    final_answer: str
    steps: List[AgentStep] = field(default_factory=list)
    iterations: int = 0
    success: bool = True
    error: Optional[str] = None


class FactsRegistry:
    """Registry to track and manage extracted facts from tool observations"""

    def __init__(self):
        self.facts: List[ExtractedFact] = []
        self._next_fact_id = 1
        self._lock = threading.Lock()

    def extract_facts_from_observation(
        self, tool_name: str, result: ToolResult, iteration: int
    ) -> List[ExtractedFact]:
        """
        Extract key facts from a tool observation

        Args:
            tool_name: Name of the tool that produced the result
            result: ToolResult containing the observation
            iteration: Current iteration number

        Returns:
            List of extracted facts
        """
        extracted = []

        if not result.success or not result.output:
            return extracted

        # Tool-specific fact extraction
        if tool_name == "web_search":
            extracted.extend(self._extract_facts_from_web_search(result, iteration))
        elif tool_name == "analyze_website":
            extracted.extend(
                self._extract_facts_from_website_analysis(result, iteration)
            )
        else:
            # Generic extraction for other tools
            extracted.extend(self._extract_facts_generic(tool_name, result, iteration))

        # Add to registry (thread-safe)
        with self._lock:
            for fact in extracted:
                fact.fact_id = self._next_fact_id
                self._next_fact_id += 1
                self.facts.append(fact)

        return extracted

    def _extract_facts_from_web_search(
        self, result: ToolResult, iteration: int
    ) -> List[ExtractedFact]:
        """Extract facts from web search results"""
        facts = []
        output = result.output

        if isinstance(output, dict):
            # Extract from search results
            results = output.get("results", [])
            summary = output.get("summary", "")
            query = output.get("query", "")

            # Extract facts from individual results
            for idx, res in enumerate(results[:5]):  # Limit to top 5 results
                snippet = res.get("snippet", "")
                title = res.get("title", "")
                url = res.get("url", "")

                if snippet:
                    # Extract key claims from snippet
                    key_phrases = self._extract_key_phrases(snippet)
                    for phrase in key_phrases:
                        if len(phrase) > 10:  # Filter out very short phrases
                            facts.append(
                                ExtractedFact(
                                    content=phrase,
                                    source_tool="web_search",
                                    confidence=(
                                        0.8 if idx < 2 else 0.6
                                    ),  # Higher confidence for top results
                                    iteration=iteration,
                                    raw_evidence=f"{title}: {snippet[:200]}",
                                    fact_id=0,  # Will be set by caller
                                )
                            )

            # Extract from summary if available
            if summary:
                summary_facts = self._extract_key_phrases(summary)
                for phrase in summary_facts:
                    if len(phrase) > 15:
                        facts.append(
                            ExtractedFact(
                                content=phrase,
                                source_tool="web_search",
                                confidence=0.7,
                                iteration=iteration,
                                raw_evidence=summary[:300],
                                fact_id=0,
                            )
                        )

        return facts

    def _extract_facts_from_website_analysis(
        self, result: ToolResult, iteration: int
    ) -> List[ExtractedFact]:
        """Extract facts from website analysis"""
        facts = []
        output = result.output

        if isinstance(output, dict):
            title = output.get("title", "")
            description = output.get("description", "")
            content_preview = output.get("content_preview", "")

            # Extract key information
            if title:
                facts.append(
                    ExtractedFact(
                        content=f"Website title: {title}",
                        source_tool="analyze_website",
                        confidence=0.9,
                        iteration=iteration,
                        raw_evidence=title,
                        fact_id=0,
                    )
                )

            if description:
                facts.append(
                    ExtractedFact(
                        content=description,
                        source_tool="analyze_website",
                        confidence=0.8,
                        iteration=iteration,
                        raw_evidence=description,
                        fact_id=0,
                    )
                )

        return facts

    def _extract_facts_generic(
        self, tool_name: str, result: ToolResult, iteration: int
    ) -> List[ExtractedFact]:
        """Generic fact extraction for unknown tools"""
        facts = []
        output = result.output

        if isinstance(output, str):
            # Extract key phrases from string output
            phrases = self._extract_key_phrases(output)
            for phrase in phrases:
                if len(phrase) > 20:
                    facts.append(
                        ExtractedFact(
                            content=phrase,
                            source_tool=tool_name,
                            confidence=0.6,
                            iteration=iteration,
                            raw_evidence=output[:300],
                            fact_id=0,
                        )
                    )
        elif isinstance(output, dict):
            # Extract from dictionary values
            for key, value in output.items():
                if isinstance(value, str) and len(value) > 20:
                    facts.append(
                        ExtractedFact(
                            content=f"{key}: {value[:200]}",
                            source_tool=tool_name,
                            confidence=0.6,
                            iteration=iteration,
                            raw_evidence=str(value)[:300],
                            fact_id=0,
                        )
                    )

        return facts

    def _extract_key_phrases(self, text: str) -> List[str]:
        """Extract key phrases from text"""
        phrases = []

        # Split by sentences
        sentences = re.split(r"[.!?]+", text)

        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) > 20:
                # Look for factual statements (contain numbers, dates, locations, etc.)
                if any(
                    pattern in sentence.lower()
                    for pattern in [
                        "was",
                        "is",
                        "are",
                        "won",
                        "sold",
                        "located",
                        "in",
                        "at",
                        "on",
                        r"\d+",  # Contains numbers
                    ]
                ):
                    phrases.append(sentence)

        return phrases

    def format_facts_for_prompt(self) -> str:
        """Format all facts for inclusion in LLM prompt"""
        with self._lock:
            if not self.facts:
                return "No facts extracted yet."

            lines = []
            for fact in self.facts:
                lines.append(f"[Fact {fact.fact_id}] {fact.content}")
                lines.append(
                    f"  Source: {fact.source_tool} (iteration {fact.iteration}, confidence: {fact.confidence:.1f})"
                )
                lines.append(f"  Evidence: {fact.raw_evidence[:150]}...")
                lines.append("")

            return "\n".join(lines)

    def get_facts_by_confidence(
        self, min_confidence: float = 0.5
    ) -> List[ExtractedFact]:
        """Get facts above a confidence threshold"""
        with self._lock:
            return [f for f in self.facts if f.confidence >= min_confidence]

    def clear(self):
        """Clear all facts"""
        with self._lock:
            self.facts = []
            self._next_fact_id = 1


class AgentExecutor:
    """
    ReACT-style agent executor
    Coordinates tool calling and LLM reasoning
    """

    def __init__(self, tool_registry: ToolRegistry, llm, max_iterations: int = 10):
        """
        Initialize agent executor

        Args:
            tool_registry: Registry of available tools
            llm: LLM instance for reasoning
            max_iterations: Maximum iterations before stopping
        """
        self.tool_registry = tool_registry
        self.llm = llm
        self.max_iterations = max_iterations
        self.facts_registry = FactsRegistry()
        self.original_query = ""  # Store original query for synthesis

        # Try to get system coordinator if available
        try:
            from backend.utils.system_coordinator import get_system_coordinator

            self.coordinator = get_system_coordinator()
        except ImportError:
            logger.warning("System coordinator not available - running without it")
            self.coordinator = None

        logger.info(f"Agent executor initialized with {len(tool_registry)} tools")

    def execute(
        self,
        user_query: str,
        session_context: str = "",
        process_id: Optional[str] = None,
    ) -> AgentResult:
        """
        Execute agent loop with tool calls

        Args:
            user_query: User's question or request
            session_context: Additional context from session
            process_id: Optional process ID from system coordinator

        Returns:
            AgentResult with final answer and execution steps
        """
        try:
            logger.info(f"Starting agent execution for query: {user_query[:100]}...")

            iteration = 0
            steps = []
            self.original_query = user_query  # Store for synthesis step
            self._tool_history = []  # Track tools called across iterations

            # Clear facts registry for new execution
            self.facts_registry.clear()

            # Tool execution guard: circuit breaker + duplicate detection
            from backend.services.tool_execution_guard import ToolExecutionGuard

            self._guard = ToolExecutionGuard(max_failures_per_tool=2)

            # Inject cross-session memory if available
            memory_context = ""
            try:
                from backend.utils.memory_manager import MemoryManager

                memory_mgr = MemoryManager()
                smart_context = memory_mgr.get_smart_context(
                    session_id=process_id or "self_improvement",
                    messages=[{"role": "user", "content": user_query}],
                    max_messages=5,
                )
                if smart_context:
                    memory_items = [
                        m.get("content", "")[:200]
                        for m in smart_context
                        if m.get("importance", 0) > 0.5
                    ]
                    if memory_items:
                        memory_context = (
                            "\n\nPrevious relevant learnings:\n"
                            + "\n".join(f"- {item}" for item in memory_items)
                        )
            except Exception as e:
                logger.debug(f"Memory context not available: {e}")

            if memory_context:
                session_context = (session_context or "") + memory_context

            # Build system prompt with available tools
            tool_schemas = self.tool_registry.get_tool_schemas(format="json_prompt")
            system_prompt = self._build_system_prompt(tool_schemas, session_context)

            # Apply honesty steering to prevent hallucinated fixes
            try:
                from backend.services.honesty_steering import HonestySteering

                steering = HonestySteering()
                honesty_prefix = steering.get_steering_prompt(
                    intent="general", intensity="standard"
                )
                if honesty_prefix:
                    system_prompt = honesty_prefix + "\n\n" + system_prompt
            except Exception as e:
                logger.debug(f"Honesty steering not available: {e}")

            # LLM Debug: log system prompt and user message
            log_system_prompt("agent_executor", system_prompt)
            log_user_message("agent_executor", user_query)

            # Build initial prompt
            current_prompt = self._build_initial_prompt(user_query, session_context)

            # Agent loop
            while iteration < self.max_iterations:
                iteration += 1
                logger.info(f"Agent iteration {iteration}/{self.max_iterations}")

                # Use error boundary if coordinator available
                if self.coordinator:
                    with self.coordinator.error_manager.error_boundary(
                        process_id, f"agent_iteration_{iteration}"
                    ):
                        step_result = self._execute_iteration(
                            current_prompt, system_prompt, iteration, process_id
                        )
                else:
                    step_result = self._execute_iteration(
                        current_prompt, system_prompt, iteration, process_id
                    )

                # Check result
                if step_result["is_final"]:
                    logger.info("Agent reached final answer")
                    final_answer = step_result["final_answer"]

                    # Synthesize and verify answer using facts
                    if self.facts_registry.facts:
                        logger.info(
                            f"Synthesizing answer from {len(self.facts_registry.facts)} extracted facts"
                        )
                        synthesized = self._synthesize_answer(
                            self.original_query, self.facts_registry.facts
                        )
                        is_valid, verified_answer = self._verify_answer(
                            synthesized, self.facts_registry.facts
                        )

                        if is_valid:
                            final_answer = verified_answer
                            logger.info("Answer verified against facts")
                        else:
                            logger.warning(
                                f"Answer verification failed: {verified_answer}"
                            )
                            # Use synthesized answer anyway, but log the issue
                            final_answer = synthesized

                    return AgentResult(
                        final_answer=final_answer,
                        steps=steps + [step_result["step"]],
                        iterations=iteration,
                        success=True,
                    )

                # Add step and continue
                steps.append(step_result["step"])
                current_prompt = step_result["next_prompt"]

            # Max iterations reached - synthesize from collected facts
            logger.warning(f"Agent reached max iterations ({self.max_iterations})")

            if self.facts_registry.facts:
                logger.info(
                    f"Synthesizing final answer from {len(self.facts_registry.facts)} collected facts"
                )
                synthesized = self._synthesize_answer(
                    self.original_query, self.facts_registry.facts
                )
                is_valid, verified_answer = self._verify_answer(
                    synthesized, self.facts_registry.facts
                )
                final_answer = verified_answer if is_valid else synthesized
            else:
                final_summary = self._summarize_steps(steps)
                final_answer = f"Reached maximum iterations. Here's what I found:\n\n{final_summary}"

            return AgentResult(
                final_answer=final_answer,
                steps=steps,
                iterations=iteration,
                success=True,
            )

        except Exception as e:
            logger.error(f"Agent execution failed: {e}", exc_info=True)
            return AgentResult(
                final_answer="",
                steps=steps if "steps" in locals() else [],
                iterations=iteration if "iteration" in locals() else 0,
                success=False,
                error=str(e),
            )

    def _execute_iteration(
        self, prompt: str, system_prompt: str, iteration: int, process_id: Optional[str]
    ) -> Dict[str, Any]:
        """Execute a single iteration of the agent loop"""
        from datetime import datetime

        # Get LLM response
        from backend.utils.llm_service import ChatMessage, MessageRole

        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=system_prompt),
            ChatMessage(role=MessageRole.USER, content=prompt),
        ]

        # Enforce JSON output from Ollama (constrained decoding).
        # Some models (vision models, thinking models) don't support JSON
        # constrained decoding — fall back to unconstrained output + parsing.
        try:
            llm_response = self.llm.chat(messages, format="json")
        except Exception as json_err:
            if "invalid character" in str(json_err):
                logger.warning(
                    f"JSON constrained decoding failed, retrying without format constraint: {json_err}"
                )
                llm_response = self.llm.chat(messages)
            else:
                raise
        response_text = _safe_content(llm_response.message)

        logger.info(
            f"LLM response (length: {len(response_text)}): {response_text[:200]}..."
        )
        log_llm_response("agent_executor", response_text, iteration=iteration)

        # Parse tool calls
        tool_call_response = parse_tool_calls_structured(response_text, self.llm)

        # If no tool calls, check if this is a final answer or just thinking
        if not tool_call_response.tool_calls:
            # With JSON structured output, the model may produce
            # {"tool_calls": [], "final_answer": null} meaning "I'm thinking."
            # Only treat as final if there's an actual final_answer.
            if tool_call_response.final_answer:
                log_decision(
                    "agent_executor",
                    "FINAL_ANSWER",
                    {
                        "iteration": iteration,
                        "answer_preview": tool_call_response.final_answer[:200],
                    },
                )
                return {
                    "is_final": True,
                    "final_answer": tool_call_response.final_answer,
                    "step": AgentStep(
                        iteration=iteration,
                        thoughts=tool_call_response.thoughts,
                        tool_calls=[],
                        observations=[],
                        timestamp=datetime.now().isoformat(),
                    ),
                }
            # No tool calls and no final answer — LLM is thinking but not acting.
            # Return as non-final so the loop prompts it to take action.
            logger.info(
                "No tool calls and no final answer — prompting LLM to take action"
            )

            all_facts_text = self.facts_registry.format_facts_for_prompt()
            nudge_prompt = f"""You responded with no tool calls and no final answer.

You MUST either:
1. Call a tool by setting "tool_calls" with at least one entry, OR
2. Provide your final answer by setting "final_answer"

CUMULATIVE FACTS REGISTRY:
{all_facts_text}

Original question: {self.original_query}

What tool do you need to call next?"""

            return {
                "is_final": False,
                "step": AgentStep(
                    iteration=iteration,
                    thoughts=tool_call_response.thoughts or response_text,
                    tool_calls=[],
                    observations=[],
                    timestamp=datetime.now().isoformat(),
                ),
                "next_prompt": nudge_prompt,
            }

        # Execute tool calls
        observations = []
        observation_texts = []

        for tool_call in tool_call_response.tool_calls:
            logger.info(f"Executing tool: {tool_call.tool_name}")

            # Normalize parameters - handle nested parameter/value format
            normalized_params = self._normalize_tool_parameters(tool_call.parameters)
            logger.debug(f"Tool {tool_call.tool_name} parameters: {normalized_params}")

            log_tool_call(
                "agent_executor",
                tool_call.tool_name,
                normalized_params,
                reasoning=tool_call_response.thoughts,
                iteration=iteration,
            )

            # Guard check: circuit breaker + duplicate detection
            allowed, block_reason = self._guard.check_call(
                tool_call.tool_name, normalized_params
            )
            if not allowed:
                logger.info(
                    f"Guard blocked tool call: {tool_call.tool_name} — {block_reason}"
                )
                log_guard_event(
                    "agent_executor",
                    "BLOCKED",
                    tool_call.tool_name,
                    details=block_reason,
                )
                result = ToolResult(success=False, error=block_reason)
                observations.append(
                    {
                        "tool": tool_call.tool_name,
                        "parameters": normalized_params,
                        "result": result.to_dict(),
                    }
                )
                observation_texts.append(
                    format_tool_result_for_llm(tool_call.tool_name, result)
                )
                continue

            # Security validation if coordinator available
            if self.coordinator and tool_call.tool_name == "execute_python":
                code = tool_call.parameters.get("code", "")
                if not self.coordinator.validate_security("llm_prompt", prompt=code):
                    result = ToolResult(
                        success=False,
                        error="Security validation failed for code execution",
                    )
                    observations.append(
                        {
                            "tool": tool_call.tool_name,
                            "parameters": normalized_params,
                            "result": result.to_dict(),
                        }
                    )
                    observation_texts.append(
                        format_tool_result_for_llm(tool_call.tool_name, result)
                    )
                    continue

            # Execute tool with normalized parameters
            result = self.tool_registry.execute_tool(
                tool_call.tool_name, **normalized_params
            )

            # Register result as resource if coordinator available
            if self.coordinator and process_id and result.success and result.output:
                from backend.utils.system_coordinator import ResourceType

                self.coordinator.register_resource(
                    result.output, ResourceType.MEMORY_BUFFER, process_id
                )

            # Record result with guard for circuit breaker tracking
            self._guard.record_result(
                tool_call.tool_name,
                normalized_params,
                result.success,
                result.error,
                iteration,
            )
            log_tool_result(
                "agent_executor",
                tool_call.tool_name,
                result.success,
                str(result.output) if result.success else (result.error or ""),
                iteration=iteration,
            )

            observations.append(
                {
                    "tool": tool_call.tool_name,
                    "parameters": normalized_params,
                    "result": result.to_dict(),
                }
            )

            observation_texts.append(
                format_tool_result_for_llm(tool_call.tool_name, result)
            )
            self._tool_history.append(
                f"{tool_call.tool_name}({', '.join(f'{k}={v!r}' for k, v in normalized_params.items())})"
            )

            # Extract facts from this observation
            extracted_facts = self.facts_registry.extract_facts_from_observation(
                tool_call.tool_name, result, iteration
            )
            if extracted_facts:
                logger.info(
                    f"Extracted {len(extracted_facts)} facts from {tool_call.tool_name}"
                )

        # Build next prompt with observations and facts
        observations_combined = "\n\n".join(observation_texts)

        # Get facts extracted in this iteration
        iteration_facts = [
            f for f in self.facts_registry.facts if f.iteration == iteration
        ]
        extracted_facts_text = ""
        if iteration_facts:
            extracted_facts_text = "\n".join(
                [f"[Fact {f.fact_id}] {f.content}" for f in iteration_facts]
            )

        # Get all cumulative facts
        all_facts_text = self.facts_registry.format_facts_for_prompt()

        history_text = "\n".join(
            f"  {i+1}. {h}" for i, h in enumerate(self._tool_history)
        )

        # Include blocked-tools summary if any tools have been circuit-broken
        guard_block = ""
        if hasattr(self, "_guard"):
            guard_summary = self._guard.get_blocked_tools_summary()
            if guard_summary:
                guard_block = f"\n{guard_summary}\n"

        next_prompt = f"""Latest tool result:
{observations_combined}

Tools already called:
{history_text}
{guard_block}
Original task: {self.original_query}

If the task is complete (all requested steps done), you MUST set "final_answer" with a summary.
Otherwise, call the next tool needed. Do NOT repeat a tool you already called with the same parameters."""

        return {
            "is_final": False,
            "step": AgentStep(
                iteration=iteration,
                thoughts=tool_call_response.thoughts,
                tool_calls=[tc.dict() for tc in tool_call_response.tool_calls],
                observations=observations,
                timestamp=datetime.now().isoformat(),
            ),
            "next_prompt": next_prompt,
        }

    def _build_system_prompt(self, tool_schemas: str, session_context: str = "") -> str:
        """Build system prompt with tool descriptions for JSON output"""
        base_prompt = f"""You are an AI assistant with access to tools. Help the user by using tools when needed.

Available Tools:
{tool_schemas}

RESPONSE FORMAT:
You MUST respond with a JSON object. Every response must have these three fields:
- "thoughts": your reasoning about what to do (string or null)
- "tool_calls": array of tool calls to execute (empty array if none needed)
- "final_answer": your final answer to the user (string or null)

Each tool call object has: "tool_name" (string), "parameters" (object), and optional "reasoning" (string).

EXAMPLE - Using a tool:
{{"thoughts": "I need to read the file first", "tool_calls": [{{"tool_name": "read_code", "parameters": {{"filepath": "config.py"}}, "reasoning": "Need to see current config"}}], "final_answer": null}}

EXAMPLE - Editing a file:
{{"thoughts": "I need to fix the bug", "tool_calls": [{{"tool_name": "edit_code", "parameters": {{"filepath": "config.py", "old_text": "DEBUG = True", "new_text": "DEBUG = False"}}}}], "final_answer": null}}

EXAMPLE - Final answer (no tools needed):
{{"thoughts": "I have all the information", "tool_calls": [], "final_answer": "The config file sets DEBUG to True on line 1."}}

RULES:
- Use exact parameter names from the tool descriptions
- Include ALL required parameters
- After tool results, use them to formulate your answer
- Only state facts found in tool results
- When you have enough information, set final_answer
- If a tool fails, try a DIFFERENT tool or different parameters. Never retry the same call.
- NEVER fabricate information. Only state facts found in tool results.
- If you cannot find the answer, say so honestly.
- If browser tools fail, use analyze_website or web_search as lighter alternatives."""

        # Append session context if it contains agent-specific instructions
        if session_context and "Agent:" in session_context:
            base_prompt += f"\n\n{session_context}"

        return base_prompt

    def _build_initial_prompt(self, user_query: str, session_context: str) -> str:
        """Build the initial prompt for the agent"""
        if session_context:
            return f"""Context:
{session_context}

User Query:
{user_query}

Think step-by-step about how to help with this request. What tools do you need?"""
        else:
            return f"""User Query:
{user_query}

Think step-by-step about how to help with this request. What tools do you need?"""

    def _synthesize_answer(self, query: str, facts: List[ExtractedFact]) -> str:
        """
        Synthesize an answer from collected facts

        Args:
            query: Original user query
            facts: List of extracted facts

        Returns:
            Synthesized answer
        """
        if not facts:
            return "I don't have enough information to answer this question."

        # Format facts for synthesis prompt
        facts_text = self.facts_registry.format_facts_for_prompt()

        synthesis_prompt = f"""Based ONLY on these verified facts, answer the question.

Question: {query}

Verified Facts:
{facts_text}

CRITICAL RULES:
1. ONLY use information from the facts above
2. Cite which fact supports each claim using [Fact N] notation
3. If facts are insufficient to fully answer, say so explicitly
4. NEVER add information not in the facts
5. If facts conflict, mention the discrepancy

Answer:"""

        try:
            from backend.utils.llm_service import ChatMessage, MessageRole

            messages = [
                ChatMessage(
                    role=MessageRole.SYSTEM,
                    content="You are a fact-checking assistant. You only state information that is explicitly supported by the provided facts.",
                ),
                ChatMessage(role=MessageRole.USER, content=synthesis_prompt),
            ]

            llm_response = self.llm.chat(messages)
            synthesized = _safe_content(llm_response.message)

            logger.info(f"Synthesized answer (length: {len(synthesized)})")
            return synthesized

        except Exception as e:
            logger.error(f"Synthesis failed: {e}", exc_info=True)
            # Fallback: format facts directly
            return f"Based on the collected facts:\n\n{facts_text}"

    def _verify_answer(
        self, answer: str, facts: List[ExtractedFact]
    ) -> Tuple[bool, str]:
        """
        Verify that an answer is grounded in facts

        Args:
            answer: The answer to verify
            facts: List of extracted facts

        Returns:
            Tuple of (is_valid, corrected_answer_or_reason)
        """
        if not facts:
            return (False, "No facts available to verify against")

        # Extract key claims from answer (simple keyword matching)
        answer_lower = answer.lower()

        # Check for unsupported claims (heuristic)
        # Look for common factual patterns that should be in facts
        location_patterns = [
            r"\b(in|at|near|located in|sold in|sold at)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
            r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(won|sold|located)",
        ]

        # Extract potential locations/entities from answer
        potential_claims = []
        for pattern in location_patterns:
            matches = re.findall(pattern, answer)
            for match in matches:
                if isinstance(match, tuple):
                    potential_claims.extend([m for m in match if len(m) > 2])
                else:
                    if len(match) > 2:
                        potential_claims.append(match)

        # Check if these claims appear in facts
        facts_text = " ".join(
            [f.content.lower() + " " + f.raw_evidence.lower() for f in facts]
        )

        unsupported = []
        for claim in set(potential_claims):
            claim_lower = claim.lower()
            if len(claim_lower) > 3:  # Only check substantial claims
                # Check if claim appears in facts
                if claim_lower not in facts_text:
                    # Check if it's a common word (false positive)
                    common_words = ["the", "and", "or", "but", "for", "with", "from"]
                    if claim_lower not in common_words:
                        unsupported.append(claim)

        if unsupported:
            logger.warning(f"Found potentially unsupported claims: {unsupported}")
            # Use LLM for more sophisticated verification
            return self._verify_with_llm(answer, facts, unsupported)

        return (True, answer)

    def _verify_with_llm(
        self, answer: str, facts: List[ExtractedFact], unsupported: List[str]
    ) -> Tuple[bool, str]:
        """Use LLM to verify answer against facts"""
        facts_text = self.facts_registry.format_facts_for_prompt()

        verification_prompt = f"""Verify if this answer is fully supported by the facts below.

Answer to verify:
{answer}

Available Facts:
{facts_text}

Potentially unsupported claims: {', '.join(unsupported)}

Check:
1. Does every factual claim in the answer appear in the facts?
2. Are there any locations, names, or numbers not in the facts?
3. If yes, rewrite the answer to only use information from the facts.

If the answer is valid, respond with: VALID: [original answer]
If the answer needs correction, respond with: CORRECTED: [corrected answer using only facts]"""

        try:
            from backend.utils.llm_service import ChatMessage, MessageRole

            messages = [
                ChatMessage(
                    role=MessageRole.SYSTEM,
                    content="You are a fact verification assistant.",
                ),
                ChatMessage(role=MessageRole.USER, content=verification_prompt),
            ]

            llm_response = self.llm.chat(messages)
            verification_result = _safe_content(llm_response.message)

            if verification_result.startswith("VALID:"):
                return (True, verification_result[6:].strip())
            elif verification_result.startswith("CORRECTED:"):
                return (True, verification_result[10:].strip())
            else:
                # Fallback: return original but mark as potentially invalid
                return (False, f"Verification inconclusive. Original answer: {answer}")

        except Exception as e:
            logger.error(f"LLM verification failed: {e}", exc_info=True)
            return (False, answer)

    def _normalize_tool_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize tool parameters to handle different LLM output formats.

        Handles:
        1. Direct format: {"query": "value"}
        2. Nested format: {"parameter": "query", "value": "value"}
        3. List format: [{"parameter": "query", "value": "value"}, ...]

        Args:
            params: Raw parameters from LLM

        Returns:
            Normalized parameters dict
        """
        if not params:
            return {}

        # Handle list of parameter/value pairs
        if isinstance(params, list):
            normalized = {}
            for item in params:
                if isinstance(item, dict) and "value" in item:
                    # Handle both "parameter" and "parameter_name" keys
                    param_name = item.get("parameter") or item.get("parameter_name")
                    param_value = item.get("value")
                    if param_name:
                        normalized[param_name] = param_value
            if normalized:
                logger.info(f"Normalized list format to: {normalized}")
                return normalized

        # Check if this is the nested parameter/value format (single pair)
        if isinstance(params, dict):
            # Handle both "parameter" and "parameter_name" keys
            p_key = (
                "parameter"
                if "parameter" in params
                else ("parameter_name" if "parameter_name" in params else None)
            )
            if p_key and "value" in params:
                # Convert nested format to direct format
                param_name = params.get(p_key)
                param_value = params.get("value")
                if param_name:
                    logger.info(
                        f"Normalizing nested parameter format: {param_name} = {param_value}"
                    )
                    return {param_name: param_value}

        # Already in direct format or unknown format
        result = params if isinstance(params, dict) else {}

        # Coerce string values to proper types (LLM outputs XML text as strings)
        coerced = {}
        for k, v in result.items():
            if isinstance(v, str):
                low = v.lower().strip()
                if low == "true":
                    coerced[k] = True
                elif low == "false":
                    coerced[k] = False
                elif low == "none" or low == "null":
                    coerced[k] = None
                else:
                    # Try int/float coercion
                    try:
                        coerced[k] = int(v)
                    except ValueError:
                        try:
                            coerced[k] = float(v)
                        except ValueError:
                            coerced[k] = v
            else:
                coerced[k] = v
        return coerced

    def _summarize_steps(self, steps: List[AgentStep]) -> str:
        """Summarize agent steps into a coherent response"""
        summary_lines = []

        for step in steps:
            if step.thoughts:
                summary_lines.append(f"Reasoning: {step.thoughts}")

            for tool_call in step.tool_calls:
                summary_lines.append(
                    f"- Used tool: {tool_call.get('tool_name', 'unknown')}"
                )

            for obs in step.observations:
                result = obs.get("result", {})
                if result.get("success"):
                    summary_lines.append(
                        f"  Result: {str(result.get('output', ''))[:100]}..."
                    )

        return "\n".join(summary_lines)


# Global executor instance (lazy initialization)
_global_agent_executor: Optional[AgentExecutor] = None


def get_agent_executor(
    tool_registry: Optional[ToolRegistry] = None, llm=None
) -> AgentExecutor:
    """Get or create global agent executor"""
    global _global_agent_executor

    if _global_agent_executor is None:
        if tool_registry is None or llm is None:
            raise ValueError(
                "Must provide tool_registry and llm for first initialization"
            )
        _global_agent_executor = AgentExecutor(tool_registry, llm)

    return _global_agent_executor
