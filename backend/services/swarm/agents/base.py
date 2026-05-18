"""Base contract for swarm role-agents.

Each agent has a fixed system prompt, a Pydantic output model, and an
`invoke()` that returns an AgentInvocation. Failures don't raise: they
return an AgentInvocation with status='parse_error', 'timeout', or 'error'.
The caller persists the AgentInvocation as a SwarmMessage row.
"""
from __future__ import annotations
from dataclasses import dataclass
from time import time
from typing import Generic, TypeVar, Callable
import json
import re
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


# Matches the OPENING fence (with optional language tag) and the LAST closing
# fence in the string. Greedy on the inner capture so JSON strings containing
# triple-backticks (e.g. dialogue with code references) don't get prematurely
# truncated. We assume the LLM wraps a single block; surrounding prose is fine.
_FENCE_RE = re.compile(r"```(?:[a-zA-Z0-9]+)?\s*\n?(.*)\n?\s*```", re.DOTALL)


def _strip_markdown_fences(raw: str) -> str:
    """Extract JSON from a markdown fence if present, else return as-is.

    LLMs (especially Ollama-served Gemma) often wrap structured output
    in ```json ... ``` blocks despite explicit prompt instructions otherwise.
    """
    match = _FENCE_RE.search(raw)
    if match:
        return match.group(1).strip()
    return raw.strip()


@dataclass
class AgentInvocation(Generic[T]):
    agent_name: str
    input_data: object
    output: T | None
    status: str  # ok | parse_error | timeout | error
    error_text: str | None
    latency_ms: int
    model: str
    raw_response: str | None


class BaseSwarmAgent(Generic[T]):
    """Subclass and set: name, output_model, system_prompt. Override build_user_prompt."""

    name: str = "base"
    output_model: type[T] = None  # type: ignore[assignment]
    system_prompt: str = ""
    model: str = "gemma4:e4b"

    def __init__(self, llm: Callable[..., str]):
        """`llm` is a callable accepting kwargs system, user, model and returning the raw string."""
        self.llm = llm

    def build_user_prompt(self, input_data) -> str:
        raise NotImplementedError

    def invoke(self, input_data) -> AgentInvocation[T]:
        t0 = time()
        try:
            user = self.build_user_prompt(input_data)
            raw = self.llm(system=self.system_prompt, user=user, model=self.model)
        except Exception as e:
            latency_ms = int((time() - t0) * 1000)
            return AgentInvocation(
                agent_name=self.name, input_data=input_data, output=None,
                status="error", error_text=str(e),
                latency_ms=latency_ms, model=self.model, raw_response=None,
            )

        latency_ms = int((time() - t0) * 1000)
        try:
            cleaned = _strip_markdown_fences(raw)
            parsed = self.output_model.model_validate(json.loads(cleaned))
        except (ValidationError, json.JSONDecodeError) as e:
            return AgentInvocation(
                agent_name=self.name, input_data=input_data, output=None,
                status="parse_error", error_text=str(e),
                latency_ms=latency_ms, model=self.model, raw_response=raw,
            )

        return AgentInvocation(
            agent_name=self.name, input_data=input_data, output=parsed,
            status="ok", error_text=None,
            latency_ms=latency_ms, model=self.model, raw_response=raw,
        )
