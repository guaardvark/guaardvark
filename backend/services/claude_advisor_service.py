"""
ClaudeAdvisorService — Uncle Claude mentor integration.

Three tiers:
  1. Escalation: route hard problems to Claude API
  2. Guardian: review self-improvement code changes
  3. Update Advisor: system health recommendations
"""
import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.utils.settings_utils import get_setting, save_setting

logger = logging.getLogger(__name__)

VALID_DIRECTIVES = [
    "proceed", "proceed_with_caution", "reject",
    "halt_self_improvement", "lock_codebase", "halt_family",
]


class ClaudeAdvisorService:
    """Singleton service for Claude API mentor integration."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip() or None
        self._client = None
        self._model = os.environ.get("GUAARDVARK_CLAUDE_MODEL", "claude-sonnet-4-20250514")
        self._max_output_tokens = int(os.environ.get("GUAARDVARK_CLAUDE_MAX_TOKENS", "4096"))
        self._monthly_budget = int(os.environ.get("GUAARDVARK_CLAUDE_TOKEN_BUDGET", "1000000"))
        self._escalation_mode = get_setting("claude_escalation_mode", default="manual")

        self._usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self._usage_reset_date = datetime.now().replace(day=1, hour=0, minute=0, second=0)
        self._usage_lock = threading.Lock()

        if self._api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self._api_key)
                logger.info("ClaudeAdvisorService initialized with API key")
            except ImportError:
                logger.warning("anthropic package not installed — pip install anthropic")
                self._client = None
            except Exception as e:
                logger.error(f"Failed to initialize Anthropic client: {e}")
                self._client = None
        else:
            logger.info("ClaudeAdvisorService initialized without API key (offline mode)")

        self._load_persisted_usage()

    def _load_persisted_usage(self):
        """Load token usage from DB. Safe to call with corrupt/missing data."""
        saved = get_setting("claude_token_usage", default=None)
        if saved:
            try:
                data = json.loads(saved)
                self._usage = data["usage"]
                self._usage_reset_date = datetime.fromisoformat(data["reset_date"])
            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                logger.warning(f"Corrupt claude_token_usage in DB, resetting: {e}")
                self._usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                self._usage_reset_date = datetime.now().replace(day=1, hour=0, minute=0, second=0)

    def is_available(self) -> bool:
        return self._api_key is not None and self._client is not None

    def _check_budget(self) -> bool:
        with self._usage_lock:
            now = datetime.now()
            if now.month != self._usage_reset_date.month or now.year != self._usage_reset_date.year:
                self._usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                self._usage_reset_date = now.replace(day=1, hour=0, minute=0, second=0)
                save_setting("claude_token_usage", json.dumps({
                    "usage": self._usage,
                    "reset_date": self._usage_reset_date.isoformat()
                }))
            return self._usage["total_tokens"] < self._monthly_budget

    def _track_usage(self, input_tokens: int = 0, output_tokens: int = 0):
        with self._usage_lock:
            self._usage["input_tokens"] += input_tokens
            self._usage["output_tokens"] += output_tokens
            self._usage["total_tokens"] += (input_tokens + output_tokens)
            save_setting("claude_token_usage", json.dumps({
                "usage": self._usage,
                "reset_date": self._usage_reset_date.isoformat()
            }))

    def get_usage(self) -> Dict[str, Any]:
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

    def _build_system_context(self) -> str:
        return (
            "You are the Guaardvark AI assistant — built into Guaardvark, "
            "a self-improving, offline-first AI platform that runs locally on user hardware using Ollama LLMs. "
            "You provide guidance, review code changes made by the autonomous "
            "self-improvement system, and help when local models are insufficient.\n\n"
            "Your role:\n"
            "- Guardian: Review code changes for safety, correctness, and quality\n"
            "- Mentor: Provide reasoning the local models cannot\n"
            "- Advisor: Recommend system improvements and updates\n\n"
            "You are NOT a controller. The user always has final authority. "
            "Your directives apply only to autonomous agent behavior.\n\n"
            "Do not mention Anthropic, Claude, or any underlying AI provider. "
            "If asked what model or AI you are, say you are Guaardvark's built-in AI assistant."
        )

    # ── Tier 1: Escalation ──────────────────────────────────────────────

    def escalate(
        self,
        message: str,
        conversation_history: List[Dict[str, str]],
        system_context: str = "",
    ) -> Dict[str, Any]:
        if not self.is_available():
            return {"available": False, "reason": "Claude API not configured"}

        if not self._check_budget():
            return {"available": False, "reason": "Monthly token budget exceeded"}

        try:
            messages = []
            for msg in conversation_history[-10:]:
                role = msg.get("role", "user")
                if role == "system":
                    continue
                messages.append({"role": role, "content": msg.get("content", "")})
            messages.append({"role": "user", "content": message})

            system_prompt = self._build_system_context()
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

            return {
                "available": True,
                "response": response.content[0].text,
                "model": self._model,
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
            }
        except Exception as e:
            logger.error(f"Claude escalation failed: {e}", exc_info=True)
            return {"available": False, "reason": f"API error: {str(e)}"}

    def escalate_streaming(
        self,
        message: str,
        conversation_history: List[Dict[str, str]],
        emit_fn=None,
        session_id: str = "",
        system_context: str = "",
    ):
        """Streaming escalation — yields tokens for Socket.IO emission."""
        if not self.is_available() or not self._check_budget():
            return None

        try:
            messages = []
            for msg in conversation_history[-10:]:
                role = msg.get("role", "user")
                if role == "system":
                    continue
                messages.append({"role": role, "content": msg.get("content", "")})
            messages.append({"role": "user", "content": message})

            system_prompt = self._build_system_context()
            if system_context:
                system_prompt += f"\n\nCurrent system context:\n{system_context}"

            full_response = ""
            input_tokens = 0
            output_tokens = 0

            with self._client.messages.stream(
                model=self._model,
                max_tokens=self._max_output_tokens,
                system=system_prompt,
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    full_response += text
                    if emit_fn:
                        emit_fn("chat:token", {
                            "content": text,
                            "session_id": session_id,
                            "source": "uncle_claude",
                        })

                final_message = stream.get_final_message()
                input_tokens = final_message.usage.input_tokens
                output_tokens = final_message.usage.output_tokens

            self._track_usage(input_tokens=input_tokens, output_tokens=output_tokens)
            return full_response
        except Exception as e:
            logger.error(f"Claude streaming escalation failed: {e}", exc_info=True)
            return None

    # ── Tier 2: Guardian ────────────────────────────────────────────────

    def review_change(
        self,
        file_path: str,
        current_content: str,
        proposed_diff: str,
        reasoning: str,
    ) -> Dict[str, Any]:
        if not self.is_available():
            return {
                "approved": True,
                "suggestions": [],
                "risk_level": "unknown",
                "directive": "proceed_with_caution",
                "reason": "Uncle Claude unavailable — proceeding with caution",
                "offline_fallback": True,
            }

        if not self._check_budget():
            return {
                "approved": True,
                "suggestions": [],
                "risk_level": "unknown",
                "directive": "proceed_with_caution",
                "reason": "Token budget exceeded — proceeding with caution",
                "offline_fallback": True,
            }

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
                system=self._build_system_context(),
                messages=[{"role": "user", "content": review_prompt}],
            )

            self._track_usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

            response_text = response.content[0].text.strip()
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]

            result = json.loads(response_text)

            if result.get("directive") not in VALID_DIRECTIVES:
                result["directive"] = "proceed_with_caution"

            result["offline_fallback"] = False
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse Claude guardian response: {e}")
            return {
                "approved": True,
                "suggestions": [],
                "risk_level": "unknown",
                "directive": "proceed_with_caution",
                "reason": f"Could not parse guardian response: {e}",
                "offline_fallback": True,
            }
        except Exception as e:
            logger.error(f"Claude guardian review failed: {e}", exc_info=True)
            return {
                "approved": True,
                "suggestions": [],
                "risk_level": "unknown",
                "directive": "proceed_with_caution",
                "reason": f"Guardian error: {str(e)}",
                "offline_fallback": True,
            }

    # ── Tier 3: Update Advisor ──────────────────────────────────────────

    def advise(self, system_state: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_available():
            return {"available": False, "reason": "Claude API not configured"}

        if not self._check_budget():
            return {"available": False, "reason": "Monthly token budget exceeded"}

        try:
            advice_prompt = (
                "Analyze this Guaardvark node's current state and provide recommendations.\n\n"
                f"**System State:**\n```json\n{json.dumps(system_state, indent=2)}\n```\n\n"
                "Respond with a JSON object:\n"
                '{"recommendations": [{"category": "model"|"security"|"performance"|"config", '
                '"priority": "low"|"medium"|"high", "title": "...", "description": "...", '
                '"action": "..."}], "overall_health": "good"|"warning"|"critical"}'
            )

            response = self._client.messages.create(
                model=self._model,
                max_tokens=2048,
                system=self._build_system_context(),
                messages=[{"role": "user", "content": advice_prompt}],
            )

            self._track_usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

            response_text = response.content[0].text.strip()
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]

            result = json.loads(response_text)
            result["available"] = True
            return result

        except Exception as e:
            logger.error(f"Claude advisor failed: {e}", exc_info=True)
            return {"available": False, "reason": f"API error: {str(e)}"}


def get_claude_advisor() -> ClaudeAdvisorService:
    """Get or create the singleton ClaudeAdvisorService instance."""
    return ClaudeAdvisorService()
