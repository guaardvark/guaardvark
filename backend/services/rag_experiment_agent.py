"""RAG Experiment Agent — LLM-driven hypothesis engine.

Reads experiment history and research_program.md, proposes the next
parameter change to try. Falls back to random search if LLM output
is unparseable.
"""
import json
import os
import random
import logging
from typing import Optional

from backend.config import (
    AUTORESEARCH_DEFAULT_PARAMS,
    AUTORESEARCH_PHASE_PLATEAU_THRESHOLD,
    PROTECTED_RAG_PARAMS,
)

logger = logging.getLogger(__name__)

# Phase -> parameter names
PHASE_PARAMS = {
    1: ["top_k", "dedup_threshold", "context_window_chunks",
        "reranking_enabled", "query_expansion", "hybrid_search_alpha"],
    2: ["chunk_size", "chunk_overlap", "use_semantic_splitting",
        "use_hierarchical_splitting", "extract_entities", "preserve_structure"],
    3: ["embedding_model"],
}

# Parameter ranges for random fallback
PARAM_RANGES = {
    "top_k": (1, 20, "int"),
    "dedup_threshold": (0.5, 0.98, "float"),
    "context_window_chunks": (1, 10, "int"),
    "reranking_enabled": (False, True, "bool"),
    "query_expansion": (False, True, "bool"),
    "hybrid_search_alpha": (0.0, 1.0, "float"),
    "chunk_size": (200, 3000, "int"),
    "chunk_overlap": (0, 500, "int"),
    "use_semantic_splitting": (False, True, "bool"),
    "use_hierarchical_splitting": (False, True, "bool"),
    "extract_entities": (False, True, "bool"),
    "preserve_structure": (False, True, "bool"),
}

AGENT_PROMPT = """You are a RAG optimization researcher. Based on the experiment history
and research program, propose ONE parameter change.

Current config: {current_config}
Phase {phase} parameters you can change: {available_params}
Research program directives:
{research_program}

Last {history_count} experiments:
{history_table}

Propose exactly ONE change. Return ONLY valid JSON:
{{"parameter": "param_name", "new_value": <value>, "hypothesis": "your reasoning"}}"""


class RAGExperimentAgent:
    """Proposes experiments using LLM reasoning + random fallback."""

    def __init__(self):
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            try:
                from flask import current_app
                self._llm = current_app.config.get("LLAMA_INDEX_LLM")
            except RuntimeError:
                pass
            if self._llm is None:
                try:
                    from backend.services.llm_service import get_llm
                    self._llm = get_llm()
                except Exception:
                    pass
        return self._llm

    def _call_llm(self, prompt: str) -> str:
        llm = self._get_llm()
        if llm is None:
            return ""
        try:
            response = llm.complete(prompt, temperature=0.7)
            return str(response).strip()
        except Exception as e:
            logger.warning(f"Agent LLM call failed: {e}")
            return ""

    def _load_research_program(self) -> str:
        program_path = os.path.join(
            os.environ.get("GUAARDVARK_ROOT", ""), "data", "research_program.md"
        )
        try:
            with open(program_path, "r") as f:
                return f.read()
        except FileNotFoundError:
            return "(no research program found)"

    def _format_history(self, history: list, max_rows: int = 20) -> str:
        recent = history[-max_rows:] if len(history) > max_rows else history
        if not recent:
            return "(no experiments yet — this is the first run)"
        lines = ["#  | param | change | delta | status"]
        for i, exp in enumerate(recent, 1):
            param = exp.get("parameter_changed", "?")
            old = exp.get("old_value", "?")
            new = exp.get("new_value", "?")
            delta = exp.get("delta", 0)
            status = exp.get("status", "?")
            delta_str = f"+{delta:.3f}" if delta and delta > 0 else f"{delta:.3f}" if delta else "?"
            lines.append(f"{i}  | {param} | {old}->{new} | {delta_str} | {status}")
        return "\n".join(lines)

    def propose_experiment(
        self, history: list, current_config: dict, phase: int = 1
    ) -> dict:
        """Propose next experiment. Falls back to random if LLM fails."""
        available = [
            p for p in PHASE_PARAMS.get(phase, [])
            if p not in PROTECTED_RAG_PARAMS
        ]
        if not available:
            return self._random_proposal(available or ["top_k"], current_config)

        # Try LLM-driven proposal
        research_program = self._load_research_program()
        history_table = self._format_history(history)
        prompt = AGENT_PROMPT.format(
            current_config=json.dumps(current_config, indent=2),
            phase=phase,
            available_params=", ".join(available),
            research_program=research_program[:2000],
            history_count=min(len(history), 20),
            history_table=history_table,
        )

        response = self._call_llm(prompt)
        try:
            parsed = json.loads(response)
            param = parsed.get("parameter")
            new_value = parsed.get("new_value")
            hypothesis = parsed.get("hypothesis", "LLM-proposed experiment")

            if param in available and new_value is not None:
                # Validate the value is different from current
                if str(new_value) != str(current_config.get(param)):
                    return {
                        "parameter": param,
                        "new_value": new_value,
                        "hypothesis": hypothesis,
                    }
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

        # Fallback: random proposal
        logger.info("Agent LLM failed to produce valid proposal, falling back to random")
        return self._random_proposal(available, current_config)

    def _random_proposal(self, available: list, current_config: dict) -> dict:
        """Random parameter change as fallback."""
        param = random.choice(available)
        prange = PARAM_RANGES.get(param)
        if not prange:
            return {
                "parameter": param,
                "new_value": not current_config.get(param, False),
                "hypothesis": "Random fallback: toggle boolean",
            }

        low, high, ptype = prange
        current_val = current_config.get(param, low)

        if ptype == "bool":
            new_val = not current_val
        elif ptype == "int":
            new_val = random.randint(int(low), int(high))
            while new_val == current_val and low != high:
                new_val = random.randint(int(low), int(high))
        elif ptype == "float":
            new_val = round(random.uniform(float(low), float(high)), 2)
            while abs(new_val - current_val) < 0.01:
                new_val = round(random.uniform(float(low), float(high)), 2)
        else:
            new_val = current_val

        return {
            "parameter": param,
            "new_value": new_val,
            "hypothesis": f"Random exploration: try {param}={new_val}",
        }

    def should_advance_phase(self, history: list) -> bool:
        """Check if current phase is plateaued."""
        if len(history) < AUTORESEARCH_PHASE_PLATEAU_THRESHOLD:
            return False
        recent = history[-AUTORESEARCH_PHASE_PLATEAU_THRESHOLD:]
        return all(exp.get("status") == "discard" for exp in recent)
