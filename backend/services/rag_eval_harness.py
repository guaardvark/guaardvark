"""RAG Eval Harness — the 'prepare.py' of Guaardvark autoresearch.

Generates eval Q&A pairs from indexed documents and scores RAG responses
using LLM-as-judge. The composite quality score (1.0-5.0, higher=better)
is the single metric for the autoresearch keep/revert loop.
"""

import json
import hashlib
import logging
from datetime import datetime
from typing import Optional

from backend.config import (
    AUTORESEARCH_EVAL_PAIR_TARGET,
    AUTORESEARCH_MIN_CORPUS_SIZE,
    AUTORESEARCH_STALENESS_SAMPLE_RATE,
    AUTORESEARCH_STALENESS_THRESHOLD,
)

logger = logging.getLogger(__name__)

# --- Prompts ---

EVAL_PAIR_GENERATION_PROMPT = """You are generating evaluation questions for a RAG (Retrieval-Augmented Generation) system.

Given the following text chunk, generate ONE factual question that a user would ask, and the correct answer based ONLY on this text.

Text chunk:
{chunk_text}

Return ONLY valid JSON:
{{"question": "your question here", "expected_answer": "the answer from the text"}}"""

JUDGE_PROMPT = """You are evaluating the quality of a RAG system's response.

Question: {question}
Expected Answer: {expected_answer}
Actual Response: {actual_response}
Retrieved Context Chunks:
{chunks_text}

Score each dimension from 1-5 (5=best):
- relevance: Are the retrieved chunks relevant to the question?
- grounding: Is the response supported by the retrieved chunks (not hallucinated)?
- completeness: Does the response fully address the question?

Return ONLY valid JSON:
{{"relevance": N, "grounding": N, "completeness": N}}"""


class RAGEvalHarness:
    """Immutable eval harness for autoresearch experiments."""

    def __init__(self):
        self._llm = None

    def _get_llm(self):
        """Get the Ollama LLM instance (lazy-loaded)."""
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

    def _call_llm(self, prompt: str, temperature: float = 0.0) -> str:
        """Call the local LLM with the given prompt."""
        llm = self._get_llm()
        if llm is None:
            return ""
        try:
            response = llm.complete(prompt, temperature=temperature)
            return str(response).strip()
        except Exception as e:
            logger.warning(f"LLM call failed: {e}")
            return ""

    def has_sufficient_corpus(self) -> bool:
        """Check if enough documents are indexed for meaningful eval."""
        from backend.models import Document, db

        count = db.session.query(Document).count()
        return count >= AUTORESEARCH_MIN_CORPUS_SIZE

    def generate_eval_pair(self, chunk_text: str, corpus_type: str) -> Optional[dict]:
        """Generate a Q&A eval pair from a text chunk."""
        prompt = EVAL_PAIR_GENERATION_PROMPT.format(chunk_text=chunk_text[:2000])
        response = self._call_llm(prompt, temperature=0.3)
        try:
            parsed = json.loads(response)
            if "question" in parsed and "expected_answer" in parsed:
                parsed["corpus_type"] = corpus_type
                parsed["source_chunk_hash"] = hashlib.sha256(
                    chunk_text.encode()
                ).hexdigest()
                return parsed
        except (json.JSONDecodeError, KeyError):
            pass
        return None

    def generate_eval_set(self, target_count: int = None):
        """Generate a full eval set from indexed documents.

        Returns list of eval pair dicts ready for DB insertion.
        """
        if target_count is None:
            target_count = AUTORESEARCH_EVAL_PAIR_TARGET

        from backend.models import Document, db
        import random

        documents = Document.query.all()
        if len(documents) < AUTORESEARCH_MIN_CORPUS_SIZE:
            logger.warning(
                f"Insufficient corpus: {len(documents)} docs < {AUTORESEARCH_MIN_CORPUS_SIZE} minimum"
            )
            return []

        # Sample documents, stratified by any available type info
        sampled = random.sample(documents, min(len(documents), target_count * 2))
        generation_id = f"gen-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"

        pairs = []
        for doc in sampled:
            if len(pairs) >= target_count:
                break
            # Use document content or title as the chunk text
            chunk_text = (
                getattr(doc, "content", None) or getattr(doc, "title", "") or ""
            )
            if len(chunk_text) < 50:
                continue
            corpus_type = self._detect_corpus_type(doc)
            pair = self.generate_eval_pair(chunk_text, corpus_type)
            if pair:
                pair["eval_generation_id"] = generation_id
                pair["source_doc_id"] = doc.id
                pairs.append(pair)

        logger.info(f"Generated {len(pairs)} eval pairs (generation: {generation_id})")
        return pairs

    def _detect_corpus_type(self, document) -> str:
        """Detect corpus type from document metadata."""
        name = getattr(document, "title", "") or getattr(document, "name", "") or ""
        name_lower = name.lower()
        if any(
            ext in name_lower
            for ext in [".py", ".js", ".jsx", ".ts", ".tsx", ".sh", ".sql"]
        ):
            return "code"
        if any(kw in name_lower for kw in ["client", "project", "brief", "proposal"]):
            return "client"
        return "knowledge"

    def score_response(
        self,
        question: str,
        expected_answer: str,
        actual_response: str,
        retrieved_chunks: list,
    ) -> dict:
        """LLM-as-judge scoring. Returns {relevance, grounding, completeness, composite}."""
        chunks_text = "\n---\n".join(str(c)[:500] for c in (retrieved_chunks or []))
        prompt = JUDGE_PROMPT.format(
            question=question,
            expected_answer=expected_answer,
            actual_response=actual_response,
            chunks_text=chunks_text or "(no chunks retrieved)",
        )
        response = self._call_llm(prompt, temperature=0.0)
        try:
            parsed = json.loads(response)
            relevance = max(1, min(5, int(parsed.get("relevance", 1))))
            grounding = max(1, min(5, int(parsed.get("grounding", 1))))
            completeness = max(1, min(5, int(parsed.get("completeness", 1))))
            composite = (relevance + grounding + completeness) / 3.0
            return {
                "relevance": relevance,
                "grounding": grounding,
                "completeness": completeness,
                "composite": round(composite, 3),
            }
        except (json.JSONDecodeError, KeyError, ValueError):
            return {"relevance": 1, "grounding": 1, "completeness": 1, "composite": 1.0}

    def _get_active_eval_pairs(self) -> list:
        """Load active eval pairs from DB."""
        from backend.models import EvalPair

        pairs = EvalPair.query.order_by(EvalPair.created_at.desc()).all()
        return [p.to_dict() for p in pairs]

    def _eval_single_pair(self, pair: dict, config: dict) -> dict:
        """Run a single eval pair through the RAG pipeline and score it."""
        from backend.utils.experiment_context import (
            set_experiment_config,
            clear_experiment_config,
        )
        from backend.services.indexing_service import search_with_llamaindex

        try:
            set_experiment_config(config)
            # Query the real retrieval path
            results = search_with_llamaindex(
                pair["question"],
                max_chunks=config.get("context_window_chunks", 3),
            )
            retrieved_chunks = [r.get("text", "") for r in results] if results else []

            # Generate response using LLM with retrieved context
            context = "\n".join(retrieved_chunks)
            response_prompt = f"Based on the following context, answer the question.\n\nContext:\n{context}\n\nQuestion: {pair['question']}\n\nAnswer:"
            actual_response = self._call_llm(response_prompt, temperature=0.0)

            return self.score_response(
                question=pair["question"],
                expected_answer=pair["expected_answer"],
                actual_response=actual_response,
                retrieved_chunks=retrieved_chunks,
            )
        finally:
            clear_experiment_config()

    def run_full_eval(self, config: dict) -> dict:
        """Run all eval pairs through the RAG pipeline with given config.

        Returns {composite_score, num_pairs, details: [...]}
        """
        pairs = self._get_active_eval_pairs()
        if not pairs:
            return {"composite_score": 0.0, "num_pairs": 0, "details": []}

        details = []
        total_composite = 0.0
        for pair in pairs:
            score = self._eval_single_pair(pair, config)
            score["eval_pair_id"] = pair["id"]
            details.append(score)
            total_composite += score["composite"]

        avg_composite = total_composite / len(pairs) if pairs else 0.0
        return {
            "composite_score": round(avg_composite, 4),
            "num_pairs": len(pairs),
            "details": details,
        }

    def is_stale(self) -> bool:
        """Check if eval pairs need regeneration by sampling chunk hashes."""
        import random
        from backend.models import EvalPair

        pairs = EvalPair.query.filter(EvalPair.source_chunk_hash.isnot(None)).all()
        if not pairs:
            return True

        sample_size = max(1, int(len(pairs) * AUTORESEARCH_STALENESS_SAMPLE_RATE))
        sample = random.sample(pairs, min(sample_size, len(pairs)))

        stale_count = 0
        for pair in sample:
            if pair.source_document is None:
                stale_count += 1
                continue

        stale_ratio = stale_count / len(sample) if sample else 1.0
        return stale_ratio > AUTORESEARCH_STALENESS_THRESHOLD
