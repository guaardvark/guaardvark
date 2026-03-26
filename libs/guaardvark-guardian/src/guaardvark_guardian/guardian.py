"""
Guardian — Three-tier Claude supervision for autonomous AI systems.

Extracted from the Guaardvark platform's ClaudeAdvisorService.
Designed to be used standalone by any Python application that runs
autonomous AI agents and needs cloud-based supervision.
"""

import json
import logging
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from guaardvark_guardian.types import (
    AdvisoryResult,
    Directive,
    EscalationResult,
    ReviewResult,
    RiskLevel,
)

logger = logging.getLogger(__name__)

VALID_DIRECTIVES = [d.value for d in Directive]


class Guardian:
    """Three-tier Claude supervision for autonomous AI systems.

    Tier 1 — Escalation:
        Route hard problems to Claude when local models are insufficient.

    Tier 2 — Code Guardian:
        Review autonomous code changes before they are applied.
        Returns structured approval/rejection with risk levels and directives
        that can halt autonomous operations fleet-wide.

    Tier 3 — System Advisor:
        Analyze system state and recommend improvements.

    All tiers fail safely: if the Claude API is unavailable or the token
    budget is exceeded, operations continue with "proceed_with_caution"
    rather than blocking.

    Args:
        api_key: Anthropic API key. If None, all tiers return offline defaults.
        model: Claude model ID (default: claude-sonnet-4-20250514).
        max_output_tokens: Maximum response tokens (default: 4096).
        monthly_budget: Token budget per calendar month (default: 1_000_000).
        system_prompt: Custom system prompt. If None, uses a sensible default.
        on_directive: Callback invoked when a guardian directive is issued.
            Signature: (directive: Directive, review: ReviewResult) -> None.
            Use this to implement kill switches, fleet halts, etc.
        on_usage: Callback for persisting token usage.
            Signature: (usage: dict) -> None.
            Called after every API request with cumulative usage data.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-20250514",
        max_output_tokens: int = 4096,
        monthly_budget: int = 1_000_000,
        system_prompt: Optional[str] = None,
        on_directive: Optional[Callable] = None,
        on_usage: Optional[Callable] = None,
    ):
        self._api_key = api_key.strip() if api_key else None
        self._client = None
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._monthly_budget = monthly_budget
        self._system_prompt = system_prompt or self._default_system_prompt()
        self._on_directive = on_directive
        self._on_usage = on_usage

        self._usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self._usage_reset_date = datetime.now().replace(day=1, hour=0, minute=0, second=0)
        self._usage_lock = threading.Lock()

        if self._api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self._api_key)
                logger.info("Guardian initialized with API key")
            except ImportError:
                logger.warning("anthropic package not installed — pip install anthropic")
            except Exception as e:
                logger.error(f"Failed to initialize Anthropic client: {e}")

    @staticmethod
    def _default_system_prompt() -> str:
        return (
            "You are a code guardian for an autonomous AI system. "
            "Your role is to review code changes for safety, correctness, and quality. "
            "You also provide guidance when local AI models are insufficient "
            "and recommend system improvements.\n\n"
            "You are NOT a controller. The human operator always has final authority. "
            "Your directives apply only to autonomous agent behavior.\n\n"
            "When reviewing code changes, be precise and actionable. "
            "Only use emergency directives (halt_self_improvement, lock_codebase, halt_family) "
            "when a change is genuinely dangerous."
        )

    @property
    def available(self) -> bool:
        """Whether the Claude API is configured and reachable."""
        return self._api_key is not None and self._client is not None

    @property
    def usage(self) -> Dict[str, Any]:
        """Current token usage for the billing period."""
        return {
            "input_tokens": self._usage["input_tokens"],
            "output_tokens": self._usage["output_tokens"],
            "total_tokens": self._usage["total_tokens"],
            "monthly_budget": self._monthly_budget,
            "budget_remaining": max(0, self._monthly_budget - self._usage["total_tokens"]),
            "budget_used_percent": round(
                (self._usage["total_tokens"] / self._monthly_budget) * 100, 1
            ) if self._monthly_budget > 0 else 0,
        }

    def _check_budget(self) -> bool:
        with self._usage_lock:
            now = datetime.now()
            if now.month != self._usage_reset_date.month or now.year != self._usage_reset_date.year:
                self._usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                self._usage_reset_date = now.replace(day=1, hour=0, minute=0, second=0)
            return self._usage["total_tokens"] < self._monthly_budget

    def _track_usage(self, input_tokens: int = 0, output_tokens: int = 0):
        with self._usage_lock:
            self._usage["input_tokens"] += input_tokens
            self._usage["output_tokens"] += output_tokens
            self._usage["total_tokens"] += input_tokens + output_tokens
        if self._on_usage:
            try:
                self._on_usage(self.usage)
            except Exception as e:
                logger.warning(f"on_usage callback failed: {e}")

    def _parse_json_response(self, text: str) -> dict:
        """Extract JSON from a Claude response, handling markdown code blocks."""
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)

    # ── Tier 1: Escalation ──────────────────────────────────────────────

    def escalate(
        self,
        message: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        system_context: str = "",
    ) -> EscalationResult:
        """Escalate a question or problem to Claude.

        Use when local AI models are insufficient for a task.
        Maintains conversation context for follow-up questions.

        Args:
            message: The question or problem to escalate.
            conversation_history: Prior messages for context (last 10 used).
            system_context: Additional context about the current system state.

        Returns:
            EscalationResult with the response or unavailability reason.
        """
        if not self.available:
            return EscalationResult(available=False, reason="Claude API not configured")
        if not self._check_budget():
            return EscalationResult(available=False, reason="Monthly token budget exceeded")

        try:
            messages = []
            if conversation_history:
                for msg in conversation_history[-10:]:
                    role = msg.get("role", "user")
                    if role == "system":
                        continue
                    messages.append({"role": role, "content": msg.get("content", "")})
            messages.append({"role": "user", "content": message})

            system_prompt = self._system_prompt
            if system_context:
                system_prompt += f"\n\nCurrent system context:\n{system_context}"

            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_output_tokens,
                system=system_prompt,
                messages=messages,
            )

            self._track_usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

            return EscalationResult(
                available=True,
                response=response.content[0].text,
                model=self._model,
                usage={
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
            )
        except Exception as e:
            logger.error(f"Escalation failed: {e}", exc_info=True)
            return EscalationResult(available=False, reason=f"API error: {str(e)}")

    # ── Tier 2: Guardian ────────────────────────────────────────────────

    def review_change(
        self,
        file_path: str,
        current_content: str,
        proposed_diff: str,
        reasoning: str,
    ) -> ReviewResult:
        """Review an autonomous code change for safety and correctness.

        Claude analyzes the change and returns a structured verdict with:
        - approved: Whether the change should be applied
        - directive: Action instruction (proceed, reject, or emergency halt)
        - risk_level: Assessed risk (low → critical)
        - suggestions: Specific improvement recommendations

        If an emergency directive is issued, the on_directive callback is
        invoked so the host application can implement kill switches.

        Args:
            file_path: Path of the file being modified.
            current_content: Current file content (truncated to 3000 chars).
            proposed_diff: The unified diff of the proposed change.
            reasoning: The autonomous agent's stated reason for the change.

        Returns:
            ReviewResult with approval status and directive.
        """
        if not self.available:
            return ReviewResult.offline_default("Guardian unavailable — proceeding with caution")
        if not self._check_budget():
            return ReviewResult.offline_default("Token budget exceeded — proceeding with caution")

        try:
            review_prompt = (
                f"Review this autonomous code change for safety and correctness.\n\n"
                f"**File:** {file_path}\n\n"
                f"**Agent's reasoning:** {reasoning}\n\n"
                f"**Current file content:**\n```\n{current_content[:3000]}\n```\n\n"
                f"**Proposed diff:**\n```diff\n{proposed_diff}\n```\n\n"
                f"Respond with ONLY a JSON object:\n"
                f'{{"approved": bool, "suggestions": ["..."], '
                f'"risk_level": "low"|"medium"|"high"|"critical", '
                f'"directive": "proceed"|"proceed_with_caution"|"reject"|'
                f'"halt_self_improvement"|"lock_codebase"|"halt_family", '
                f'"reason": "..."}}\n\n'
                f"Use halt_self_improvement or higher ONLY if the change is dangerous "
                f"(modifying security infrastructure, disabling safety checks, "
                f"recursive self-modification of protected files)."
            )

            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=self._system_prompt,
                messages=[{"role": "user", "content": review_prompt}],
            )

            self._track_usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

            result_dict = self._parse_json_response(response.content[0].text)
            result = ReviewResult.from_dict(result_dict)

            # Fire directive callback for emergency directives
            if result.directive.is_emergency and self._on_directive:
                try:
                    self._on_directive(result.directive, result)
                except Exception as e:
                    logger.error(f"on_directive callback failed: {e}")

            return result

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse guardian response: {e}")
            return ReviewResult.offline_default(f"Could not parse guardian response: {e}")
        except Exception as e:
            logger.error(f"Guardian review failed: {e}", exc_info=True)
            return ReviewResult.offline_default(f"Guardian error: {str(e)}")

    # ── Tier 3: System Advisor ──────────────────────────────────────────

    def advise(self, system_state: Dict[str, Any]) -> AdvisoryResult:
        """Request system health advice based on current state.

        Claude analyzes GPU usage, disk space, model configuration,
        and other metrics to recommend improvements.

        Args:
            system_state: Dict of system metrics (GPU usage, disk, models, etc.)

        Returns:
            AdvisoryResult with categorized recommendations.
        """
        if not self.available:
            return AdvisoryResult(available=False, reason="Claude API not configured")
        if not self._check_budget():
            return AdvisoryResult(available=False, reason="Monthly token budget exceeded")

        try:
            advice_prompt = (
                "Analyze this system's current state and provide recommendations.\n\n"
                f"**System State:**\n```json\n{json.dumps(system_state, indent=2)}\n```\n\n"
                "Respond with a JSON object:\n"
                '{"recommendations": [{"category": "model"|"security"|"performance"|"config", '
                '"priority": "low"|"medium"|"high", "title": "...", "description": "...", '
                '"action": "..."}], "overall_health": "good"|"warning"|"critical"}'
            )

            response = self._client.messages.create(
                model=self._model,
                max_tokens=2048,
                system=self._system_prompt,
                messages=[{"role": "user", "content": advice_prompt}],
            )

            self._track_usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

            result_dict = self._parse_json_response(response.content[0].text)
            return AdvisoryResult(
                available=True,
                recommendations=result_dict.get("recommendations", []),
                overall_health=result_dict.get("overall_health", "unknown"),
            )

        except Exception as e:
            logger.error(f"Advisory failed: {e}", exc_info=True)
            return AdvisoryResult(available=False, reason=f"API error: {str(e)}")
