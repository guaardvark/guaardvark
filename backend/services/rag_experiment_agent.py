"""RAG Experiment Agent — LLM-driven hypothesis engine.

Reads experiment history and data/rag_research_program.md, proposes the next
parameter change to try. Order: TPE-lite over scored history, then LLM, then
random if both fail.
"""
import json
import os
import random
import logging
from typing import Optional

from backend.config import (
    AUTORESEARCH_DEFAULT_PARAMS,
    AUTORESEARCH_PHASE_PLATEAU_THRESHOLD,
    AUTORESEARCH_TPE_MIN_HISTORY,
    PROTECTED_RAG_PARAMS,
)

logger = logging.getLogger(__name__)

# Phase -> parameter names. Only phases whose parameters are ACTUALLY consumed
# by retrieval may appear here — tuning a knob nothing reads just measures
# LLM-judge noise and promotes false positives.
#   Phase 2 (index-time chunking: chunk_size, chunk_overlap, semantic/
#   hierarchical splitting, entities, structure) returns when per-experiment
#   eval-subset re-indexing lands — until then those params never reach the
#   chunker at query time.
#   Phase 3 (embedding_model) is excluded: swapping embeddings means re-indexing
#   the corpus per experiment, far outside a per-iteration budget.
PHASE_PARAMS = {
    1: ["top_k", "dedup_threshold", "context_window_chunks",
        "reranking_enabled", "query_expansion", "hybrid_search_alpha"],
}
MAX_PHASE = max(PHASE_PARAMS)

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

# Same path the research-run engine snapshots. The old research_program.md
# filename never received the human's directives.
RESEARCH_PROGRAM_RELPATH = os.path.join("data", "rag_research_program.md")

# Discretization for TPE-lite. Matches PARAM_RANGES for Phase 1.
_TPE_GRID = {
    "top_k": list(range(1, 21)),
    "dedup_threshold": [round(0.50 + i * 0.02, 2) for i in range(25)],
    "context_window_chunks": list(range(1, 11)),
    "reranking_enabled": [False, True],
    "query_expansion": [False, True],
    "hybrid_search_alpha": [round(i * 0.1, 1) for i in range(11)],
}


def _extract_json(text: str) -> str:
    """Pull the first {...} block out of a possibly chatty LLM reply."""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start:end + 1]
    return text


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
        self.proposer_model_name = None  # recorded in the experiment ledger

    def _get_llm(self):
        if self._llm is None:
            from backend.utils.llm_service import get_llm_instance
            try:
                from backend.models import Setting
                s = Setting.query.filter_by(key="autoresearch_proposer_model").first()
                configured = (s.value or "").strip() if s else ""
            except Exception:
                configured = ""
            if configured:
                self._llm = get_llm_instance(model=configured)
                if self._llm is not None:
                    self.proposer_model_name = configured
            if self._llm is None:
                try:
                    self._llm = get_llm_instance()
                except Exception:
                    self._llm = None
            if self._llm is None:
                try:
                    from flask import current_app
                    self._llm = current_app.config.get("LLAMA_INDEX_LLM")
                except RuntimeError:
                    pass
            if self._llm is None:
                # Research runs execute in a Celery worker whose app has no
                # LLAMA_INDEX_LLM; without this the proposer silently went
                # 100% random on the first live run (2026-08-30).
                try:
                    from backend.utils.llm_service import get_saved_active_model_name
                    active = get_saved_active_model_name()
                except Exception:
                    active = None
                if active:
                    self._llm = get_llm_instance(model=active)
            if self._llm is not None and self.proposer_model_name is None:
                self.proposer_model_name = getattr(self._llm, "model", None) or "active"
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
            os.environ.get("GUAARDVARK_ROOT", ""), RESEARCH_PROGRAM_RELPATH
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

        tpe = self._tpe_proposal(history, current_config, available)
        if tpe is not None:
            return tpe

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

        # Up to 3 attempts (1 + 2 repairs): small local models flub JSON often
        # enough that a single shot degraded to random.choice most of the time.
        attempt_prompt = prompt
        for attempt in range(3):
            response = self._call_llm(attempt_prompt)
            if not response:
                break  # LLM unavailable — retrying won't help
            try:
                parsed = json.loads(_extract_json(response))
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
                            "source": "llm",
                        }
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
            attempt_prompt = (
                prompt
                + "\n\nYour previous reply was not valid JSON in the required shape. "
                  'Return ONLY: {"parameter": "...", "new_value": ..., "hypothesis": "..."}'
            )

        # Fallback: random proposal — labeled, so reports can show the
        # LLM-vs-random proposal ratio instead of hiding degradation.
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
                "source": "random",
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
            "source": "random",
        }

    def _tpe_proposal(
        self, history: list, current_config: dict, available: list
    ) -> Optional[dict]:
        """Component-wise TPE-lite over scored history. None = not enough data.

        Splits observations into the top quartile (good) vs the rest (bad) by
        composite_score, then for each Phase-1 param picks the discrete value
        maximizing n_good / (n_bad + 1) that is not the current value and was
        not the most recently discarded value for that param. With p=0.3 a
        second param is also flipped (the caller still records one change as
        the primary; the second is applied via new_value_extra — actually we
        only return ONE change to stay compatible with the 1-param ledger).
        """
        scored = []
        for h in history or []:
            try:
                score = float(h.get("composite_score"))
            except (TypeError, ValueError):
                continue
            param = h.get("parameter_changed") or h.get("parameter")
            if not param:
                continue
            scored.append({
                "parameter": param,
                "value": h.get("new_value"),
                "score": score,
                "status": h.get("status"),
            })
        if len(scored) < AUTORESEARCH_TPE_MIN_HISTORY:
            return None

        scores = sorted(s["score"] for s in scored)
        cutoff = scores[max(0, len(scores) - max(1, len(scores) // 4))]
        good = [s for s in scored if s["score"] >= cutoff]
        bad = [s for s in scored if s["score"] < cutoff]
        if not good:
            return None

        recent_discard = {}
        for h in reversed(history or []):
            if h.get("status") == "discard":
                p = h.get("parameter_changed") or h.get("parameter")
                if p and p not in recent_discard:
                    recent_discard[p] = str(h.get("new_value"))

        def _coerce(param, raw):
            grid = _TPE_GRID.get(param)
            if not grid:
                return None
            ptype = PARAM_RANGES.get(param, (None, None, None))[2]
            try:
                if ptype == "bool":
                    if isinstance(raw, bool):
                        val = raw
                    else:
                        val = str(raw).strip().lower() in ("1", "true", "yes")
                elif ptype == "int":
                    val = int(float(raw))
                else:
                    val = float(raw)
            except (TypeError, ValueError):
                return None
            # Snap to nearest grid point.
            return min(grid, key=lambda g: abs(float(g) - float(val))
                       if not isinstance(g, bool) else (0 if g == val else 1))

        best = None
        best_ratio = -1.0
        for param in available:
            grid = _TPE_GRID.get(param)
            if not grid:
                continue
            current = current_config.get(param)
            for candidate in grid:
                if str(candidate) == str(current):
                    continue
                if recent_discard.get(param) == str(candidate):
                    continue
                n_good = sum(
                    1 for s in good
                    if s["parameter"] == param and _coerce(param, s["value"]) == candidate
                )
                n_bad = sum(
                    1 for s in bad
                    if s["parameter"] == param and _coerce(param, s["value"]) == candidate
                )
                # Unobserved values get a small exploration prior so TPE
                # still tries them instead of only repeating known keeps.
                ratio = (n_good + 0.15) / (n_bad + 1.0)
                if ratio > best_ratio:
                    best_ratio = ratio
                    best = (param, candidate, n_good, n_bad)

        if best is None:
            return None
        param, new_val, n_good, n_bad = best
        return {
            "parameter": param,
            "new_value": new_val,
            "hypothesis": (
                f"TPE-lite: {param}={new_val} "
                f"(good={n_good} bad={n_bad} among {len(scored)} scored trials)"
            ),
            "source": "tpe",
        }

    def should_advance_phase(self, history: list) -> bool:
        """Check if current phase is plateaued."""
        if len(history) < AUTORESEARCH_PHASE_PLATEAU_THRESHOLD:
            return False
        recent = history[-AUTORESEARCH_PHASE_PLATEAU_THRESHOLD:]
        return all(exp.get("status") == "discard" for exp in recent)
