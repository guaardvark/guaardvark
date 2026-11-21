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

from backend.services.agent_tools import ToolRegistry, ToolResult, tool_needs_approval_card
from backend.services.step_budget import StepBudget
from backend.utils.agent_output_parser import (
    parse_tool_calls_structured,
    format_tool_result_for_llm,
    ToolCallResponse
)

from backend.utils.llm_debug_logger import (
    log_system_prompt, log_user_message, log_llm_response,
    log_tool_call, log_tool_result, log_guard_event, log_decision,
)

logger = logging.getLogger(__name__)


def _safe_content(message) -> str:
    """Extract content from a LlamaIndex ChatMessage, handling multi-block (thinking) models."""
    if not message:
        return ""
    try:
        return str(message.content)
    except (ValueError, AttributeError):
        blocks = getattr(message, 'blocks', [])
        for block in blocks:
            text = getattr(block, 'text', str(block) if block else "")
            if text:
                return text
        if blocks:
            return str(blocks[0])
        thinking = getattr(message, 'thinking', None)
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
    # Facts check on final_answer: True when every checkable claim is in the
    # facts, False when it is not (the answer is still returned), None when
    # no tool produced facts to check against.
    verified: Optional[bool] = None


class FactsRegistry:
    """Registry to track and manage extracted facts from tool observations"""
    
    def __init__(self):
        self.facts: List[ExtractedFact] = []
        self._next_fact_id = 1
        self._lock = threading.Lock()
    
    def extract_facts_from_observation(self, tool_name: str, result: ToolResult, iteration: int) -> List[ExtractedFact]:
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
            extracted.extend(self._extract_facts_from_website_analysis(result, iteration))
        elif tool_name == "edit_code":
            # Per agentic audit (FactsRegistry pollution): skip code-edit diffs
            # (hunks, paths, line numbers) to reduce noise in SI/agent loops.
            # See code_manipulation_tools.py "Diff:" literal and rec R2.
            pass
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
    
    def _extract_facts_from_web_search(self, result: ToolResult, iteration: int) -> List[ExtractedFact]:
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
                            facts.append(ExtractedFact(
                                content=phrase,
                                source_tool="web_search",
                                confidence=0.8 if idx < 2 else 0.6,  # Higher confidence for top results
                                iteration=iteration,
                                raw_evidence=f"{title}: {snippet[:200]}",
                                fact_id=0  # Will be set by caller
                            ))
            
            # Extract from summary if available
            if summary:
                summary_facts = self._extract_key_phrases(summary)
                for phrase in summary_facts:
                    if len(phrase) > 15:
                        facts.append(ExtractedFact(
                            content=phrase,
                            source_tool="web_search",
                            confidence=0.7,
                            iteration=iteration,
                            raw_evidence=summary[:300],
                            fact_id=0
                        ))
        
        return facts
    
    def _extract_facts_from_website_analysis(self, result: ToolResult, iteration: int) -> List[ExtractedFact]:
        """Extract facts from website analysis"""
        facts = []
        output = result.output
        
        if isinstance(output, dict):
            title = output.get("title", "")
            description = output.get("description", "")
            content_preview = output.get("content_preview", "")
            
            # Extract key information
            if title:
                facts.append(ExtractedFact(
                    content=f"Website title: {title}",
                    source_tool="analyze_website",
                    confidence=0.9,
                    iteration=iteration,
                    raw_evidence=title,
                    fact_id=0
                ))
            
            if description:
                facts.append(ExtractedFact(
                    content=description,
                    source_tool="analyze_website",
                    confidence=0.8,
                    iteration=iteration,
                    raw_evidence=description,
                    fact_id=0
                ))
        
        return facts
    
    def _extract_facts_generic(self, tool_name: str, result: ToolResult, iteration: int) -> List[ExtractedFact]:
        """Generic fact extraction for unknown tools"""
        facts = []
        output = result.output
        
        if isinstance(output, str):
            # Extract key phrases from string output
            phrases = self._extract_key_phrases(output)
            for phrase in phrases:
                if len(phrase) > 20:
                    facts.append(ExtractedFact(
                        content=phrase,
                        source_tool=tool_name,
                        confidence=0.6,
                        iteration=iteration,
                        raw_evidence=output[:300],
                        fact_id=0
                    ))
        elif isinstance(output, dict):
            # Extract from dictionary values
            for key, value in output.items():
                if isinstance(value, str) and len(value) > 20:
                    facts.append(ExtractedFact(
                        content=f"{key}: {value[:200]}",
                        source_tool=tool_name,
                        confidence=0.6,
                        iteration=iteration,
                        raw_evidence=str(value)[:300],
                        fact_id=0
                    ))
        
        return facts
    
    def _extract_key_phrases(self, text: str) -> List[str]:
        """Extract key phrases from text"""
        phrases = []
        
        # Split by sentences
        sentences = re.split(r'[.!?]+', text)
        
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) > 20:
                # Look for factual statements (contain numbers, dates, locations, etc.)
                if any(pattern in sentence.lower() for pattern in [
                    'was', 'is', 'are', 'won', 'sold', 'located', 'in', 'at', 'on',
                    r'\d+',  # Contains numbers
                ]):
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
                lines.append(f"  Source: {fact.source_tool} (iteration {fact.iteration}, confidence: {fact.confidence:.1f})")
                lines.append(f"  Evidence: {fact.raw_evidence[:150]}...")
                lines.append("")

            return "\n".join(lines)

    def get_facts_by_confidence(self, min_confidence: float = 0.5) -> List[ExtractedFact]:
        """Get facts above a confidence threshold"""
        with self._lock:
            return [f for f in self.facts if f.confidence >= min_confidence]

    def clear(self):
        """Clear all facts"""
        with self._lock:
            self.facts = []
            self._next_fact_id = 1



# Closing line of an agent run's first message. Asking the model which tools it
# needs got a written plan back ("I should use fetch_url ..."), which native tool
# calling then took as the final answer; this asks it to act instead.
_ACT_NOW = (
    "Do it now: call the tool you need first, and give your final answer once "
    "you have its result. Answer directly only if no tool is needed."
)

class AgentExecutor:
    """
    ReACT-style agent executor
    Coordinates tool calling and LLM reasoning
    """
    
    def __init__(self, tool_registry: ToolRegistry, llm, max_iterations: int = 10, agent=None):
        """
        Initialize agent executor

        Args:
            tool_registry: Registry of available tools
            llm: LLM instance for reasoning
            max_iterations: Maximum iterations before stopping
            agent: optional AgentConfig; its name and instructions go into the
                system prompt, and its tools decide whether this is a screen task
        """
        self.tool_registry = tool_registry
        self.llm = llm if llm is not None else self._load_default_llm()
        self.max_iterations = max_iterations
        self.agent = agent
        self.facts_registry = FactsRegistry()
        self.original_query = ""  # Store original query for synthesis
        self._tool_context = {}
        self._native_tools_supported = False  # set in execute() after model check
        self._screen = False  # set in execute()
        self._tool_filter = None

        # Try to get system coordinator if available
        try:
            from backend.utils.system_coordinator import get_system_coordinator
            self.coordinator = get_system_coordinator()
        except ImportError:
            logger.warning("System coordinator not available - running without it")
            self.coordinator = None

        logger.info(f"Agent executor initialized with {len(tool_registry)} tools")

    @staticmethod
    def _load_default_llm():
        """Load the default chat LLM for callers that pass llm=None."""
        try:
            from backend.utils.llm_service import get_default_llm
            llm = get_default_llm()
            if llm is None:
                logger.error("Default LLM loader returned None")
            return llm
        except Exception as e:
            logger.error(f"Failed to initialize default LLM for AgentExecutor: {e}", exc_info=True)
            return None

    def _name_available_tools_on_miss(self, result) -> None:
        """When the model invents a tool name, tell it what exists. A bare
        "not found" left a local model retrying variations until the run
        ran out (directed run 24, 2026-09-05: tool_code_read_file)."""
        try:
            if result.success or "not found in registry" not in (result.error or ""):
                return
            names = sorted(self.tool_registry.list_tools())
            if names:
                result.error = (f"{result.error}. Use one of the tools that exist: "
                                f"{', '.join(names)}")
        except Exception:
            pass

    def set_tool_context(self, **kwargs):
        """Set extra kwargs that get forwarded to every tool execute() call."""
        self._tool_context.update(kwargs)

    def _refuse_unapproved(self, tool_name: str, params: Dict[str, Any]) -> Optional[ToolResult]:
        """A refusal for a call that needs a person's approval card, else None.

        This loop has no card to show, so such a call is not run. edit_code
        under self-improvement is the exception: there it only stages a
        PendingFix, and a person applies it.
        """
        tool = self.tool_registry.get_tool(tool_name)
        if tool is None:
            return None
        if tool_name == "edit_code" and self._tool_context.get("_self_improvement_context"):
            return None
        if not tool_needs_approval_card(tool, tool_name, params, set()):
            return None
        logger.info(f"Refused {tool_name}: needs an approval card, which this loop cannot show")
        return ToolResult(
            success=False,
            error=f"{tool_name} needs your approval in chat and was not run",
        )
    
    def execute(self, user_query: str, session_context: str = "", process_id: Optional[str] = None, max_steps: Optional[int] = None, budget: Optional["StepBudget"] = None) -> AgentResult:
        """
        Execute agent loop with tool calls
        
        Args:
            user_query: User's question or request
            session_context: Extra context for the first user message (never the system prompt)
            process_id: Optional process ID from system coordinator
            max_steps: legacy int form (still supported)
            budget: preferred explicit StepBudget from AgentBrain (carries history + awareness)
            
        Returns:
            AgentResult with final answer and execution steps
        """
        try:
            logger.info(f"Starting agent execution (query_len={len(user_query)})")
            
            iteration = 0
            steps = []
            self.original_query = user_query  # Store for synthesis step
            self._described_tool_nudged = False
            self._tool_history = []  # Track tools called across iterations

            effective_budget = budget
            if effective_budget is None and max_steps is not None:
                # Back-compat path
                from backend.services.step_budget import StepBudget
                effective_budget = StepBudget(total=max(1, int(max_steps)))

            if effective_budget is not None:
                self.max_iterations = min(self.max_iterations, max(1, effective_budget.remaining))
                # Charge the entry into this executor
                effective_budget.charge(1, 3, "entered AgentExecutor")
            self._budget = effective_budget  # store for prompt injection so agent can "see" its limits

            if self.llm is None or not hasattr(self.llm, "chat"):
                error = (
                    "Agent executor cannot run: no chat-capable LLM instance is configured. "
                    "Check Ollama/LlamaIndex startup and active model configuration."
                )
                logger.error(error)
                return AgentResult(
                    final_answer="",
                    steps=[],
                    iterations=0,
                    success=False,
                    error=error,
                )

            # Clear facts registry for new execution
            self.facts_registry.clear()

            # Tool execution guard: circuit breaker + duplicate detection
            from backend.services.tool_execution_guard import ToolExecutionGuard
            self._guard = ToolExecutionGuard(max_failures_per_tool=2, scope="for the rest of this task")

            # Durable AgentMemory rows that match the task, kept when
            # memory_match_score against the query is above 0.3, best first.
            # With BrainState the system prompt already carries its memory
            # block, so this lookup only runs without it.
            memory_context = ""
            if not self._brain_state_used():
                try:
                    from backend.api.memory_api import search_memories
                    mems = search_memories(
                        query=user_query,
                        limit=8,
                        min_importance=0.4,
                        match_text=user_query,
                        min_match=0.3,
                        session_id=process_id or "default",
                    )
                    learnings = [(m.get('content') or '')[:200] for m in mems[:5]]
                    if learnings:
                        memory_context = "Saved memories that match this task:\n" + "\n".join(f"- {l}" for l in learnings)
                except Exception as e:
                    logger.debug(f"Contract memory context not available (falling back empty): {e}")

            if memory_context:
                session_context = f"{session_context}\n\n{memory_context}" if session_context else memory_context

            # A configured agent is a screen agent when it owns agent_task_execute;
            # otherwise the task text decides. The agent's own instructions and
            # the session context never do: they name screens and browsers.
            if self.tool_registry.get_tool("agent_task_execute") is None:
                self._screen = False
            elif self.agent is not None:
                self._screen = "agent_task_execute" in (self.agent.tools or [])
            else:
                self._screen = self._is_vision_task(user_query)
            is_vision_task = self._screen
            # An agent's registry is already its own tool list; only an
            # unconfigured screen run narrows the full registry.
            tool_filter = 'vision' if (is_vision_task and self.agent is None) else None
            self._tool_filter = tool_filter

            # Check if active model supports native function calling (Gemma 4, etc.)
            self._native_tools_supported = False
            self._li_tools = []
            try:
                model_name = getattr(self.llm, 'model', '')
                if model_name:
                    from backend.utils.ollama_resource_manager import model_supports_tools
                    if model_supports_tools(model_name):
                        self._li_tools = self.tool_registry.as_llama_index_tools(tool_filter=tool_filter)
                        if self._li_tools:
                            self._native_tools_supported = True
                            logger.info(f"Native function calling enabled for {model_name} ({len(self._li_tools)} tools)")
            except Exception as e:
                logger.debug(f"Native tool detection failed, using prompt injection: {e}")

            tool_schemas = self.tool_registry.get_tool_schemas(format='json_prompt', tool_filter=tool_filter)
            system_prompt = self._resolve_system_prompt(tool_schemas)

            # Honesty steering already in BrainState prefix when available
            if not self._brain_state_used():
                try:
                    from backend.services.honesty_steering import HonestySteering
                    steering = HonestySteering()
                    honesty_prefix = steering.get_steering_prompt(intent="general", intensity="standard")
                    if honesty_prefix:
                        system_prompt = honesty_prefix + "\n\n" + system_prompt
                except Exception as e:
                    logger.debug(f"Honesty steering not available: {e}")

            # Build initial prompt
            current_prompt = self._build_initial_prompt(user_query, session_context)

            # LLM Debug: log system prompt and the first user message as sent
            log_system_prompt("agent_executor", system_prompt)
            log_user_message("agent_executor", current_prompt)
            
            # Agent loop
            from backend.services.agent_control_service import get_chat_stop_check
            while iteration < self.max_iterations:
                stop = get_chat_stop_check()
                if stop is not None and stop():
                    logger.info(f"Agent stopped by the user before iteration {iteration + 1}")
                    return AgentResult(final_answer="", steps=steps, iterations=iteration,
                                       success=False, error="Stopped.")
                iteration += 1
                logger.info(f"Agent iteration {iteration}/{self.max_iterations}")
                
                # Choose iteration method based on native tool support
                _iter_fn = self._execute_iteration_native if self._native_tools_supported else self._execute_iteration

                # Use error boundary if coordinator available
                if self.coordinator:
                    with self.coordinator.error_manager.error_boundary(
                        process_id, f"agent_iteration_{iteration}"
                    ):
                        step_result = _iter_fn(
                            current_prompt, system_prompt, iteration, process_id
                        )
                else:
                    step_result = _iter_fn(
                        current_prompt, system_prompt, iteration, process_id
                    )

                # Live-stream this iteration's reasoning so Tier-3 ReACT runs
                # show steps in realtime, not just persisted-for-refresh. Uses
                # the thread-local emit_fn (set on this run thread in
                # unified_chat_api) and mirrors the source="agent_loop" payload
                # shape that StreamingMessage consumes. Best-effort: never let an
                # emit failure abort the agent loop.
                try:
                    from backend.services.agent_control_service import get_chat_emit_fn
                    _live_emit = get_chat_emit_fn()
                    if _live_emit:
                        _sid = self._tool_context.get("session_id")
                        _step = step_result.get('step')
                        _reasoning = getattr(_step, 'thoughts', None) if _step else None
                        if _sid and _reasoning:
                            _live_emit("chat:thinking", {
                                "session_id": _sid,
                                "iteration": iteration,
                                "status": f"Step {iteration}",
                                "source": "agent_loop",
                                "reasoning": _reasoning,
                            })
                except Exception:
                    pass

                # Check result
                if step_result['is_final']:
                    logger.info("Agent reached final answer")
                    final_answer = step_result['final_answer']

                    # The model's own answer is returned; the facts check only
                    # labels it. Facts come from every tool run, related or
                    # not, so they never replace the answer.
                    verified = None
                    if self.facts_registry.facts:
                        verified, check_note = self._verify_answer(final_answer, self.facts_registry.facts)
                        if verified:
                            logger.info("Answer verified against facts")
                        else:
                            logger.warning(f"Answer not verified against facts: {check_note[:200]}")

                    return AgentResult(
                        final_answer=final_answer,
                        steps=steps + [step_result['step']],
                        iterations=iteration,
                        success=True,
                        verified=verified,
                    )
                
                # Add step and continue
                steps.append(step_result['step'])
                current_prompt = step_result['next_prompt']
            
            # Max iterations reached - synthesize from collected facts
            logger.warning(f"Agent reached max iterations ({self.max_iterations})")
            
            verified = None
            if self.facts_registry.facts:
                logger.info(f"Synthesizing final answer from {len(self.facts_registry.facts)} collected facts")
                synthesized = self._synthesize_answer(self.original_query, self.facts_registry.facts)
                verified, verified_answer = self._verify_answer(synthesized, self.facts_registry.facts)
                final_answer = verified_answer if verified else synthesized
            else:
                final_summary = self._summarize_steps(steps)
                final_answer = f"Reached maximum iterations. Here's what I found:\n\n{final_summary}"
            
            return AgentResult(
                final_answer=final_answer,
                steps=steps,
                iterations=iteration,
                success=True,
                verified=verified,
            )
            
        except Exception as e:
            logger.error(f"Agent execution failed: {e}", exc_info=True)
            return AgentResult(
                final_answer="",
                steps=steps if 'steps' in locals() else [],
                iterations=iteration if 'iteration' in locals() else 0,
                success=False,
                error=str(e)
            )
    
    def _described_tool(self, text: str) -> Optional[str]:
        """Name of one of this run's tools that `text` mentions, or None.

        Used only when a native first reply called no tool: a reply that talks
        about a tool it could have called is a plan, not an answer.
        """
        if not text:
            return None
        for tool in getattr(self, "_li_tools", None) or []:
            name = getattr(getattr(tool, "metadata", None), "name", None)
            if name and re.search(rf"\b{re.escape(name)}\b", text):
                return name
        return None

    def _execute_iteration(self, prompt: str, system_prompt: str, iteration: int, process_id: Optional[str]) -> Dict[str, Any]:
        """Execute a single iteration of the agent loop"""
        from datetime import datetime
        
        # Get LLM response
        from backend.utils.llm_service import ChatMessage, MessageRole
        
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=system_prompt),
            ChatMessage(role=MessageRole.USER, content=prompt)
        ]

        # Enforce JSON output from Ollama (constrained decoding).
        # Some models (vision models, thinking models) don't support JSON
        # constrained decoding — fall back to unconstrained output + parsing.
        try:
            llm_response = self.llm.chat(messages, format="json")
        except Exception as json_err:
            if "invalid character" in str(json_err):
                logger.warning(f"JSON constrained decoding failed, retrying without format constraint: {json_err}")
                llm_response = self.llm.chat(messages)
            else:
                raise
        response_text = _safe_content(llm_response.message)
        
        logger.info(f"LLM response (length: {len(response_text)}): {response_text[:200]}...")
        log_llm_response("agent_executor", response_text, iteration=iteration)

        # Parse tool calls
        tool_call_response = parse_tool_calls_structured(response_text, self.llm)
        
        # If no tool calls, check if this is a final answer or just thinking
        if not tool_call_response.tool_calls:
            # With JSON structured output, the model may produce
            # {"tool_calls": [], "final_answer": null} meaning "I'm thinking."
            # Only treat as final if there's an actual final_answer.
            if tool_call_response.final_answer:
                log_decision("agent_executor", "FINAL_ANSWER", {
                    "iteration": iteration,
                    "answer_preview": tool_call_response.final_answer[:200],
                })
                return {
                    'is_final': True,
                    'final_answer': tool_call_response.final_answer,
                    'step': AgentStep(
                        iteration=iteration,
                        thoughts=tool_call_response.thoughts,
                        tool_calls=[],
                        observations=[],
                        timestamp=datetime.now().isoformat()
                    )
                }
            # No tool calls and no final answer — LLM is thinking but not acting.
            # Return as non-final so the loop prompts it to take action.
            logger.info("No tool calls and no final answer — prompting LLM to take action")

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
                'is_final': False,
                'step': AgentStep(
                    iteration=iteration,
                    thoughts=tool_call_response.thoughts or response_text,
                    tool_calls=[],
                    observations=[],
                    timestamp=datetime.now().isoformat()
                ),
                'next_prompt': nudge_prompt
            }
        
        # Execute tool calls
        observations = []
        observation_texts = []
        
        for tool_call in tool_call_response.tool_calls:
            logger.info(f"Executing tool: {tool_call.tool_name}")

            # Normalize parameters - handle nested parameter/value format
            normalized_params = self._normalize_tool_parameters(tool_call.parameters, tool_call.tool_name)
            logger.debug(f"Tool {tool_call.tool_name} parameters: {normalized_params}")

            log_tool_call("agent_executor", tool_call.tool_name, normalized_params,
                          reasoning=tool_call_response.thoughts, iteration=iteration)

            # Guard check: circuit breaker + duplicate detection
            allowed, block_reason = self._guard.check_call(tool_call.tool_name, normalized_params)
            if not allowed:
                logger.info(f"Guard blocked tool call: {tool_call.tool_name} — {block_reason}")
                log_guard_event("agent_executor", "BLOCKED", tool_call.tool_name, details=block_reason)
                result = ToolResult(success=False, error=block_reason)
                observations.append({
                    'tool': tool_call.tool_name,
                    'parameters': normalized_params,
                    'result': result.to_dict()
                })
                observation_texts.append(format_tool_result_for_llm(tool_call.tool_name, result))
                continue

            refusal = self._refuse_unapproved(tool_call.tool_name, normalized_params)
            if refusal:
                self._guard.record_result(
                    tool_call.tool_name, normalized_params, False, refusal.error, iteration
                )
                log_tool_result("agent_executor", tool_call.tool_name, False, refusal.error,
                                iteration=iteration)
                observations.append({
                    'tool': tool_call.tool_name,
                    'parameters': normalized_params,
                    'result': refusal.to_dict()
                })
                observation_texts.append(format_tool_result_for_llm(tool_call.tool_name, refusal))
                continue

            # Security validation if coordinator available
            if self.coordinator and tool_call.tool_name == "execute_python":
                code = tool_call.parameters.get("code", "")
                if not self.coordinator.validate_security("llm_prompt", prompt=code):
                    result = ToolResult(
                        success=False,
                        error="Security validation failed for code execution"
                    )
                    observations.append({
                        'tool': tool_call.tool_name,
                        'parameters': normalized_params,
                        'result': result.to_dict()
                    })
                    observation_texts.append(format_tool_result_for_llm(tool_call.tool_name, result))
                    continue

            # Execute tool with normalized parameters
            if tool_call.tool_name in ("agent_task_execute", "agent_screen_capture"):
                try:
                    from backend.services.agent_control_service import get_chat_emit_fn
                    _emit_ctx = get_chat_emit_fn()
                    logger.debug(
                        f"[EMIT-HANDOFF][AGENT_EXECUTOR] about to exec {tool_call.tool_name} via registry; "
                        f"threadlocal emit available? { _emit_ctx is not None } (ACS will use for live chat:thinking source=agent_loop)"
                    )
                except Exception:
                    pass
            result = self.tool_registry.execute_tool(tool_call.tool_name, agent_context=self._tool_context, **normalized_params)
            self._name_available_tools_on_miss(result)

            # Register result as resource if coordinator available
            if self.coordinator and process_id and result.success and result.output:
                from backend.utils.system_coordinator import ResourceType
                self.coordinator.register_resource(
                    result.output,
                    ResourceType.MEMORY_BUFFER,
                    process_id
                )
            
            # Record result with guard for circuit breaker tracking
            self._guard.record_result(
                tool_call.tool_name, normalized_params,
                result.success, result.error, iteration
            )
            log_tool_result("agent_executor", tool_call.tool_name, result.success,
                            str(result.output) if result.success else (result.error or ""),
                            iteration=iteration)

            observations.append({
                'tool': tool_call.tool_name,
                'parameters': normalized_params,
                'result': result.to_dict()
            })

            observation_texts.append(format_tool_result_for_llm(tool_call.tool_name, result))
            self._tool_history.append(f"{tool_call.tool_name}({', '.join(f'{k}={v!r}' for k, v in normalized_params.items())})")

            # Extract facts from this observation
            extracted_facts = self.facts_registry.extract_facts_from_observation(
                tool_call.tool_name, result, iteration
            )
            if extracted_facts:
                logger.info(f"Extracted {len(extracted_facts)} facts from {tool_call.tool_name}")
                if getattr(self, '_budget', None):
                    self._budget.charge(1, 3, f"fact extracted from {tool_call.tool_name}")
        
        # Build next prompt with observations and facts
        observations_combined = "\n\n".join(observation_texts)
        
        # Get facts extracted in this iteration
        iteration_facts = [f for f in self.facts_registry.facts if f.iteration == iteration]
        extracted_facts_text = ""
        if iteration_facts:
            extracted_facts_text = "\n".join([f"[Fact {f.fact_id}] {f.content}" for f in iteration_facts])
        
        # Get all cumulative facts
        all_facts_text = self.facts_registry.format_facts_for_prompt()
        
        history_text = "\n".join(f"  {i+1}. {h}" for i, h in enumerate(self._tool_history))

        # Include blocked-tools summary if any tools have been circuit-broken
        guard_block = ""
        if hasattr(self, '_guard'):
            guard_summary = self._guard.get_blocked_tools_summary()
            if guard_summary:
                guard_block = f"\n{guard_summary}\n"

        budget_block = ""
        if getattr(self, '_budget', None):
            budget_block = self._budget.to_llm_summary() + "\n\n"

        next_prompt = f"""{budget_block}Latest tool result:
{observations_combined}

Tools already called:
{history_text}
{guard_block}
Original task: {self.original_query}

If the task is complete (all requested steps done), you MUST set "final_answer" with a summary.
Otherwise, call the next tool needed. Do NOT repeat a tool you already called with the same parameters."""
        
        return {
            'is_final': False,
            'step': AgentStep(
                iteration=iteration,
                thoughts=tool_call_response.thoughts,
                tool_calls=[tc.dict() for tc in tool_call_response.tool_calls],
                observations=observations,
                timestamp=datetime.now().isoformat()
            ),
            'next_prompt': next_prompt
        }
    
    @staticmethod
    def _is_vision_task(user_query: str, session_context: str = "") -> bool:
        """Detect if this is a vision/screen automation task.

        execute() passes the task text only; session_context is read when a
        caller passes it.
        """
        import re
        combined = (user_query + " " + (session_context or "")).lower()
        # Exact substring keywords
        vision_keywords = [
            'virtual screen', 'virtual display', 'agent mode',
            'open firefox', 'open browser', 'open chrome',
            'click on', 'type into', 'search youtube', 'search google',
            'navigate to', 'go to website', 'use the screen',
            'screen capture', 'screenshot', 'using your screen',
            'vnc', 'display :99', 'xdotool',
            'your screen', 'the screen', 'agent screen',
            'open a tab', 'open tab', 'new tab', 'close tab',
            'firefox tab', 'browser tab',
            'use your agent', 'agent tool',
        ]
        if any(kw in combined for kw in vision_keywords):
            return True
        # Regex patterns for flexible matching
        vision_patterns = [
            r'open\s+(?:a\s+)?(?:new\s+)?(?:firefox|browser|chrome|tab)',
            r'(?:use|launch|start|control)\s+(?:the\s+|your\s+)?(?:virtual|screen|firefox|browser|agent)',
            r'(?:on|from|using|via|through)\s+(?:the\s+)?(?:virtual|agent)',
            r'(?:click|type|scroll|drag|hover)\s+(?:on|in|into|at)',
            r'(?:go|visit|browse)\s+(?:to\s+)?(?:a\s+)?(?:website|webpage|url|page)',
            r'(?:show|tell|describe)\s+(?:me\s+)?what.{0,15}(?:on|see).{0,10}screen',
            r'agent\s+(?:vision|control|screen|virtual|execute|task)',
            r'/vision\b',
            r'/agent\b',
        ]
        return any(re.search(p, combined) for p in vision_patterns)

    def _brain_state_used(self) -> bool:
        try:
            from backend.services.brain_state import BrainState
            bs = BrainState.get_instance()
            return bool(getattr(bs, "_initialized", False))
        except Exception:
            return False

    def _agent_identity(self) -> Tuple[str, str]:
        """(name, instructions) of the configured agent, or two empty strings."""
        if self.agent is None:
            return "", ""
        return (getattr(self.agent, "name", "") or "", getattr(self.agent, "system_prompt", "") or "")

    def _resolve_system_prompt(self, tool_schemas: str, native: Optional[bool] = None) -> str:
        """Prefer the BrainState canonical prompt; fall back to the local builder.

        The agent's instructions appear here once. The session context goes
        only into the first user message (_build_initial_prompt).
        """
        if native is None:
            native = self._native_tools_supported
        agent_name, agent_prompt = self._agent_identity()
        try:
            from backend.services.brain_state import BrainState
            bs = BrainState.get_instance()
            if getattr(bs, "_initialized", False):
                return bs.get_system_prompt(
                    role="agent",
                    screen=self._screen,
                    query=getattr(self, "original_query", ""),
                    budget=getattr(self, "_budget", None),
                    facts_registry=self.facts_registry,
                    tool_list=tool_schemas,
                    native=native,
                    agent_name=agent_name,
                    agent_prompt=agent_prompt,
                )
        except Exception as e:
            logger.debug(f"BrainState system prompt unavailable, using local builder: {e}")

        if native:
            return self._build_system_prompt_native()
        return self._build_system_prompt(tool_schemas)

    def _budget_line(self) -> str:
        if getattr(self, '_budget', None):
            return self._budget.to_llm_summary() + " Use this information to plan efficiently across steps."
        return ""

    def _build_system_prompt(self, tool_schemas: str, native: bool = False) -> str:
        """Local system prompt, used when BrainState is not initialized."""
        from backend.services.agent_prompt_blocks import build_agent_prompt_tail
        agent_name, agent_prompt = self._agent_identity()
        return build_agent_prompt_tail(
            tool_schemas=tool_schemas,
            native=native,
            screen=self._screen,
            agent_name=agent_name,
            agent_prompt=agent_prompt,
            budget_line=self._budget_line(),
        )

    def _build_system_prompt_native(self) -> str:
        """Local system prompt for models that take tools through the API's tools parameter."""
        return self._build_system_prompt("", native=True)

    def _execute_iteration_native(self, prompt: str, system_prompt: str, iteration: int, process_id: Optional[str]) -> Dict[str, Any]:
        """Execute a single iteration using native function calling (Gemma 4, etc.).

        Instead of injecting tool schemas into the prompt and parsing JSON
        from raw text, this uses LlamaIndex's chat_with_tools() which passes
        tools via the Ollama API's tools parameter and returns structured
        ToolCallBlock objects.
        """
        from datetime import datetime
        from backend.utils.llm_service import ChatMessage, MessageRole

        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=system_prompt),
        ]
        # Attach conversation history for multi-turn (the prompt contains observations)
        messages.append(ChatMessage(role=MessageRole.USER, content=prompt))

        try:
            llm_response = self.llm.chat_with_tools(
                self._li_tools,
                chat_history=[messages[0]],
                user_msg=messages[1],
                allow_parallel_tool_calls=True,
            )
        except Exception as e:
            # Fall back to prompt-injection path on any failure
            logger.warning(f"Native tool calling failed, falling back to prompt injection: {e}")
            self._native_tools_supported = False
            # The prompt path needs the tool list and JSON format in the text.
            tool_schemas = self.tool_registry.get_tool_schemas(format='json_prompt', tool_filter=self._tool_filter)
            fallback_system = self._resolve_system_prompt(tool_schemas, native=False)
            return self._execute_iteration(prompt, fallback_system, iteration, process_id)

        response_text = _safe_content(llm_response.message)
        logger.info(f"Native LLM response (length: {len(response_text)}): {response_text[:200]}...")
        log_llm_response("agent_executor", response_text, iteration=iteration)

        # Extract tool calls from native response
        tool_selections = self.llm.get_tool_calls_from_response(llm_response, error_on_no_tool_call=False)

        if not tool_selections:
            # No tools called — treat as final answer or nudge
            described = self._described_tool(response_text)
            if described and iteration == 1 and not self._described_tool_nudged:
                # The model wrote about calling one of its tools instead of
                # calling it. Ask once; a second text reply stands as the answer.
                self._described_tool_nudged = True
                log_decision("agent_executor", "DESCRIBED_NOT_CALLED", {
                    "iteration": iteration,
                    "tool": described,
                    "mode": "native",
                })
                return {
                    'is_final': False,
                    'step': AgentStep(
                        iteration=iteration,
                        thoughts=response_text,
                        tool_calls=[],
                        observations=[],
                        timestamp=datetime.now().isoformat()
                    ),
                    'next_prompt': (
                        f"You described using {described} but did not call it. "
                        f"Call {described} now with its parameters; do not describe the plan.\n\n"
                        f"Original question: {self.original_query}"
                    ),
                }
            if response_text and response_text.strip():
                log_decision("agent_executor", "FINAL_ANSWER", {
                    "iteration": iteration,
                    "answer_preview": response_text[:200],
                    "mode": "native",
                })
                return {
                    'is_final': True,
                    'final_answer': response_text,
                    'step': AgentStep(
                        iteration=iteration,
                        thoughts=response_text,
                        tool_calls=[],
                        observations=[],
                        timestamp=datetime.now().isoformat()
                    )
                }
            # Empty response, nudge
            all_facts_text = self.facts_registry.format_facts_for_prompt()
            return {
                'is_final': False,
                'step': AgentStep(
                    iteration=iteration,
                    thoughts="(no response)",
                    tool_calls=[],
                    observations=[],
                    timestamp=datetime.now().isoformat()
                ),
                'next_prompt': (
                    f"You didn't call any tools or provide an answer.\n"
                    f"CUMULATIVE FACTS:\n{all_facts_text}\n\n"
                    f"Original question: {self.original_query}\n"
                    f"What tool do you need to call next, or what is your final answer?"
                ),
            }

        # Execute each tool call
        observations = []
        observation_texts = []
        executed_tool_calls = []

        for sel in tool_selections:
            logger.info(f"[native] Executing tool: {sel.tool_name}")
            normalized_params = self._normalize_tool_parameters(sel.tool_kwargs)
            logger.debug(f"[native] Tool {sel.tool_name} parameters: {normalized_params}")

            log_tool_call("agent_executor", sel.tool_name, normalized_params,
                          reasoning="(native function call)", iteration=iteration)

            executed_tool_calls.append(ToolCallResponse(
                thoughts=response_text,
                tool_calls=[],  # logged separately
            ))

            # Guard check
            allowed, block_reason = self._guard.check_call(sel.tool_name, normalized_params)
            if not allowed:
                logger.info(f"Guard blocked tool call: {sel.tool_name} — {block_reason}")
                log_guard_event("agent_executor", "BLOCKED", sel.tool_name, details=block_reason)
                result = ToolResult(success=False, error=block_reason)
                observations.append({'tool': sel.tool_name, 'parameters': normalized_params, 'result': result.to_dict()})
                observation_texts.append(format_tool_result_for_llm(sel.tool_name, result))
                continue

            refusal = self._refuse_unapproved(sel.tool_name, normalized_params)
            if refusal:
                self._guard.record_result(sel.tool_name, normalized_params, False, refusal.error, iteration)
                log_tool_result("agent_executor", sel.tool_name, False, refusal.error, iteration=iteration)
                observations.append({'tool': sel.tool_name, 'parameters': normalized_params, 'result': refusal.to_dict()})
                observation_texts.append(format_tool_result_for_llm(sel.tool_name, refusal))
                continue

            # Security validation
            if self.coordinator and sel.tool_name == "execute_python":
                code = normalized_params.get("code", "")
                if not self.coordinator.validate_security("llm_prompt", prompt=code):
                    result = ToolResult(success=False, error="Security validation failed for code execution")
                    observations.append({'tool': sel.tool_name, 'parameters': normalized_params, 'result': result.to_dict()})
                    observation_texts.append(format_tool_result_for_llm(sel.tool_name, result))
                    continue

            # Execute
            result = self.tool_registry.execute_tool(sel.tool_name, agent_context=self._tool_context, **normalized_params)
            self._name_available_tools_on_miss(result)

            if self.coordinator and process_id and result.success and result.output:
                from backend.utils.system_coordinator import ResourceType
                self.coordinator.register_resource(result.output, ResourceType.MEMORY_BUFFER, process_id)

            self._guard.record_result(sel.tool_name, normalized_params, result.success, result.error, iteration)
            log_tool_result("agent_executor", sel.tool_name, result.success,
                            str(result.output) if result.success else (result.error or ""),
                            iteration=iteration)

            observations.append({'tool': sel.tool_name, 'parameters': normalized_params, 'result': result.to_dict()})
            observation_texts.append(format_tool_result_for_llm(sel.tool_name, result))
            self._tool_history.append(f"{sel.tool_name}({', '.join(f'{k}={v!r}' for k, v in normalized_params.items())})")

            # Extract facts
            extracted_facts = self.facts_registry.extract_facts_from_observation(sel.tool_name, result, iteration)
            if extracted_facts:
                logger.info(f"Extracted {len(extracted_facts)} facts from {sel.tool_name}")
                if getattr(self, '_budget', None):
                    self._budget.charge(1, 3, f"fact extracted from {sel.tool_name}")

        # Build next prompt with observations
        observations_combined = "\n\n".join(observation_texts)
        iteration_facts = [f for f in self.facts_registry.facts if f.iteration == iteration]
        extracted_facts_text = ""
        if iteration_facts:
            extracted_facts_text = "\n".join([f"[Fact {f.fact_id}] {f.content}" for f in iteration_facts])

        all_facts_text = self.facts_registry.format_facts_for_prompt()

        # Build compact tool call representations for step recording
        tc_records = [
            ToolCallResponse(thoughts=response_text, tool_calls=[]).tool_calls
            for _ in tool_selections
        ]

        # Get guard blocked tools for next prompt
        blocked_tools = self._guard.blocked_tools
        blocked_msg = ""
        if blocked_tools:
            blocked_msg = f"\nBLOCKED TOOLS (do not retry): {', '.join(blocked_tools)}"

        next_prompt = f"""Tool Results:
{observations_combined}

{f"NEW FACTS EXTRACTED:{chr(10)}{extracted_facts_text}" if extracted_facts_text else ""}

CUMULATIVE FACTS REGISTRY:
{all_facts_text}
{blocked_msg}

Previous tools used: {', '.join(self._tool_history[-10:])}

Original question: {self.original_query}

Based on the tool results, either call another tool or provide your final answer."""

        return {
            'is_final': False,
            'step': AgentStep(
                iteration=iteration,
                thoughts=response_text,
                tool_calls=[{'tool_name': s.tool_name, 'parameters': s.tool_kwargs} for s in tool_selections],
                observations=observations,
                timestamp=datetime.now().isoformat()
            ),
            'next_prompt': next_prompt,
        }

    def _build_initial_prompt(self, user_query: str, session_context: str) -> str:
        """Build the initial prompt for the agent"""
        session_context = (session_context or "").strip()
        if session_context:
            return f"""Context:
{session_context}

User Query:
{user_query}

{_ACT_NOW}"""
        else:
            return f"""User Query:
{user_query}

{_ACT_NOW}"""
    
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
                ChatMessage(role=MessageRole.SYSTEM, content="You are a fact-checking assistant. You only state information that is explicitly supported by the provided facts."),
                ChatMessage(role=MessageRole.USER, content=synthesis_prompt)
            ]
            
            llm_response = self.llm.chat(messages)
            synthesized = _safe_content(llm_response.message)
            
            logger.info(f"Synthesized answer (length: {len(synthesized)})")
            return synthesized
            
        except Exception as e:
            logger.error(f"Synthesis failed: {e}", exc_info=True)
            # Fallback: format facts directly
            return f"Based on the collected facts:\n\n{facts_text}"
    
    def _verify_answer(self, answer: str, facts: List[ExtractedFact]) -> Tuple[bool, str]:
        """
        Verify that an answer is grounded in facts
        
        Args:
            answer: The answer to verify
            facts: List of extracted facts
            
        Returns:
            Tuple of (is_valid, answer_or_correction_or_reason). is_valid is
            True only when the answer as given is supported; a corrected
            rewrite comes back with False.
        """
        if not facts:
            return (False, "No facts available to verify against")
        
        # Extract key claims from answer (simple keyword matching)
        answer_lower = answer.lower()
        
        # Check for unsupported claims (heuristic)
        # Look for common factual patterns that should be in facts
        location_patterns = [
            r'\b(in|at|near|located in|sold in|sold at)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)',
            r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(won|sold|located)',
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
        facts_text = " ".join([f.content.lower() + " " + f.raw_evidence.lower() for f in facts])
        
        unsupported = []
        for claim in set(potential_claims):
            claim_lower = claim.lower()
            if len(claim_lower) > 3:  # Only check substantial claims
                # Check if claim appears in facts
                if claim_lower not in facts_text:
                    # Check if it's a common word (false positive)
                    common_words = ['the', 'and', 'or', 'but', 'for', 'with', 'from']
                    if claim_lower not in common_words:
                        unsupported.append(claim)
        
        if unsupported:
            logger.warning(f"Found potentially unsupported claims: {unsupported}")
            # Use LLM for more sophisticated verification
            return self._verify_with_llm(answer, facts, unsupported)
        
        return (True, answer)
    
    def _verify_with_llm(self, answer: str, facts: List[ExtractedFact], unsupported: List[str]) -> Tuple[bool, str]:
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
                ChatMessage(role=MessageRole.SYSTEM, content="You are a fact verification assistant."),
                ChatMessage(role=MessageRole.USER, content=verification_prompt)
            ]
            
            llm_response = self.llm.chat(messages)
            verification_result = _safe_content(llm_response.message)
            
            if verification_result.startswith("VALID:"):
                return (True, verification_result[6:].strip())
            elif verification_result.startswith("CORRECTED:"):
                return (False, verification_result[10:].strip())
            else:
                # Fallback: return original but mark as potentially invalid
                return (False, f"Verification inconclusive. Original answer: {answer}")
                
        except Exception as e:
            logger.error(f"LLM verification failed: {e}", exc_info=True)
            return (False, answer)
    
    def _normalize_tool_parameters(self, params: Dict[str, Any], tool_name: Optional[str] = None) -> Dict[str, Any]:
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
            p_key = "parameter" if "parameter" in params else ("parameter_name" if "parameter_name" in params else None)
            if p_key and "value" in params:
                # Convert nested format to direct format
                param_name = params.get(p_key)
                param_value = params.get("value")
                if param_name:
                    logger.info(f"Normalizing nested parameter format: {param_name} = {param_value}")
                    return {param_name: param_value}
        
        # Already in direct format or unknown format
        result = params if isinstance(params, dict) else {}

        # Coerce string values (text tool-call formats) using the tool's schema
        from backend.services.agent_tools import coerce_params_to_schema

        tool = self.tool_registry.get_tool(tool_name) if tool_name else None
        return coerce_params_to_schema(result, tool)
    
    def _summarize_steps(self, steps: List[AgentStep]) -> str:
        """Summarize agent steps into a coherent response"""
        summary_lines = []
        
        for step in steps:
            if step.thoughts:
                summary_lines.append(f"Reasoning: {step.thoughts}")
            
            for tool_call in step.tool_calls:
                summary_lines.append(f"- Used tool: {tool_call.get('tool_name', 'unknown')}")
            
            for obs in step.observations:
                result = obs.get('result', {})
                if result.get('success'):
                    summary_lines.append(f"  Result: {str(result.get('output', ''))[:100]}...")
        
        return "\n".join(summary_lines)


# Global executor instance (lazy initialization)
_global_agent_executor: Optional[AgentExecutor] = None


def get_agent_executor(tool_registry: Optional[ToolRegistry] = None, llm=None) -> AgentExecutor:
    """Get or create global agent executor"""
    global _global_agent_executor
    
    if _global_agent_executor is None:
        if tool_registry is None or llm is None:
            raise ValueError("Must provide tool_registry and llm for first initialization")
        _global_agent_executor = AgentExecutor(tool_registry, llm)
    
    return _global_agent_executor

