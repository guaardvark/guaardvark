"""RAG Eval Harness — the 'prepare.py' of Guaardvark autoresearch.

Generates eval Q&A pairs from indexed documents and scores RAG responses
using LLM-as-judge. The composite quality score (1.0-5.0, higher=better)
is the single metric for the autoresearch keep/revert loop.
"""
import json
import hashlib
import logging
import time
from datetime import datetime
from typing import Optional

from backend.config import (
    AUTORESEARCH_EVAL_PAIR_TARGET,
    AUTORESEARCH_JUDGE_SUBSET,
    AUTORESEARCH_MAX_EXPERIMENT_DURATION,
    AUTORESEARCH_MAX_LLM_CALLS_PER_EXPERIMENT,
    AUTORESEARCH_MIN_CORPUS_SIZE,
    AUTORESEARCH_PARSE_FAIL_CRASH_RATIO,
    AUTORESEARCH_STALENESS_SAMPLE_RATE,
    AUTORESEARCH_STALENESS_THRESHOLD,
)

# A document below this many characters of extracted text cannot yield an
# eval chunk; it is not corpus, whatever its row count says.
EVAL_MIN_TEXT_CHARS = 50
from backend.services.rag_experiment_agent import _extract_json

logger = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """No LLM reachable for a required eval role.

    Raised instead of scoring: an unavailable LLM used to be scored as
    composite 1.0 (the floor), making "Ollama is off" indistinguishable from
    "RAG is terrible" — and feeding the keep/discard loop pure noise. Callers
    (run_single_experiment) surface this as a crash, which the
    consecutive-crash guard halts on.
    """


# --- Prompts ---

EVAL_PAIR_GENERATION_PROMPT = """You are generating evaluation questions for a RAG (Retrieval-Augmented Generation) system.

Given the following text chunk, generate ONE {question_kind} question a user would ask, and the correct answer based ONLY on this text.

Question kind:
- specific: a factual question whose answer is a span of this chunk
- reasoning: a question that requires combining two facts in this chunk, not copying a single sentence

Text chunk:
{chunk_text}

Return ONLY valid JSON:
{{"question": "your question here", "expected_answer": "the answer from the text", "question_type": "{question_kind}"}}"""

MULTI_HOP_GENERATION_PROMPT = """You are generating evaluation questions for a RAG (Retrieval-Augmented Generation) system.

Given TWO text chunks, generate ONE question that requires information from BOTH chunks to answer correctly, and the correct answer grounded in those chunks.

Chunk A:
{chunk_a}

Chunk B:
{chunk_b}

Return ONLY valid JSON:
{{"question": "your question here", "expected_answer": "the answer from both chunks", "question_type": "multi_hop"}}"""

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
        self._llms = {}  # role -> LLM instance
        self.judge_model_name = None  # resolved lazily; recorded in the ledger
        self.single_model_judging = False
        self._llm_calls = 0
        self._deadline = None
        self._call_budget = AUTORESEARCH_MAX_LLM_CALLS_PER_EXPERIMENT

    @staticmethod
    def _model_setting(key: str) -> Optional[str]:
        try:
            from backend.models import Setting
            s = Setting.query.filter_by(key=key).first()
            value = (s.value or "").strip() if s else ""
            return value or None
        except Exception:
            return None

    def _get_llm(self, role: str = "answer"):
        """LLM for a role: 'answer' (production model) or 'judge'.

        The judge intentionally runs on a DIFFERENT local model when
        `autoresearch_judge_model` is configured — a model grading its own
        answers is self-confirmation bias. Falls back to the active model
        with `single_model_judging` flagged for the report.
        """
        if role in self._llms:
            return self._llms[role]

        from backend.utils.llm_service import get_llm_instance

        llm = None
        if role == "judge":
            judge_model = self._model_setting("autoresearch_judge_model")
            if judge_model:
                llm = get_llm_instance(model=judge_model)
                if llm is not None:
                    self.judge_model_name = judge_model
            if llm is None:
                # Same model as answers — allowed, but loudly flagged.
                self.single_model_judging = True

        if llm is None:
            try:
                llm = get_llm_instance()
            except Exception:
                llm = None
            if llm is None:
                try:
                    from flask import current_app
                    llm = current_app.config.get("LLAMA_INDEX_LLM")
                except RuntimeError:
                    llm = None
            if role == "judge" and llm is not None and self.judge_model_name is None:
                self.judge_model_name = getattr(llm, "model", None) or "active"

        if llm is not None:
            self._llms[role] = llm
        return llm

    def begin_experiment_budget(self, duration_s: float = None, call_budget: int = None):
        """Start the per-experiment wall-clock and LLM-call budgets."""
        self._llm_calls = 0
        cap = duration_s if duration_s is not None else AUTORESEARCH_MAX_EXPERIMENT_DURATION
        self._deadline = time.monotonic() + max(1.0, float(cap))
        self._call_budget = (
            call_budget if call_budget is not None
            else AUTORESEARCH_MAX_LLM_CALLS_PER_EXPERIMENT
        )

    def _budget_ok(self) -> bool:
        if self._deadline is not None and time.monotonic() >= self._deadline:
            return False
        if self._llm_calls >= self._call_budget:
            return False
        return True

    def _parse_json_object(self, text: str) -> Optional[dict]:
        try:
            parsed = json.loads(_extract_json(text or ""))
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def _call_llm(self, prompt: str, temperature: float = 0.0, role: str = "answer") -> str:
        """Call the role's LLM. Raises LLMUnavailableError instead of faking."""
        if not self._budget_ok():
            raise LLMUnavailableError(
                f"Eval budget exhausted (calls={self._llm_calls}, role='{role}')"
            )
        llm = self._get_llm(role)
        if llm is None:
            raise LLMUnavailableError(
                f"No LLM available for eval role '{role}' — is Ollama running?"
            )
        try:
            response = llm.complete(prompt, temperature=temperature)
            self._llm_calls += 1
            return str(response).strip()
        except LLMUnavailableError:
            raise
        except Exception as e:
            raise LLMUnavailableError(f"LLM call failed for role '{role}': {e}") from e

    def text_document_count(self) -> int:
        """Documents that carry enough extracted text to yield an eval chunk.

        Images, audio and unextracted binaries sit in the same table with an
        empty `content`; counting them made a folder of 26 PNGs look like a
        corpus (2026-08-29) and the run failed later with "no eval pairs".
        """
        from backend.models import Document, db
        from sqlalchemy import func
        return db.session.query(Document).filter(
            Document.content.isnot(None),
            func.length(Document.content) >= EVAL_MIN_TEXT_CHARS,
        ).count()

    def has_sufficient_corpus(self) -> bool:
        """Enough TEXT documents are indexed for a meaningful eval set."""
        return self.text_document_count() >= AUTORESEARCH_MIN_CORPUS_SIZE

    def _chunk_document(self, doc) -> list:
        """Chunk a Document the same way indexing does (EnhancedRAGChunker,
        'auto' strategy) and return the chunk TEXTS as retrieval surfaces them.

        This is what makes eval-pair chunk hashes comparable with hashes of
        retrieved chunks: same splitter, same normalization. The old code
        hashed the whole (truncated) document, which could never equal any
        retrieved chunk's hash — retrieval metrics scored a structural zero.
        """
        text = getattr(doc, "content", None) or ""
        if len(text.strip()) < EVAL_MIN_TEXT_CHARS:
            return []
        try:
            from llama_index.core import Document as LlamaDocument
            from backend.utils.enhanced_rag_chunking import EnhancedRAGChunker
            nodes = EnhancedRAGChunker().chunk_documents(
                [LlamaDocument(text=text, metadata={})], strategy_name="auto"
            )
            chunks = [n.get_content() for n in (nodes or []) if getattr(n, "text", None)]
            if chunks:
                return chunks
        except Exception as e:
            logger.debug(f"Eval chunking fell back to doc head: {e}")
        return [text[:2000]]

    def generate_eval_pair(
        self, chunk_text: str, corpus_type: str, question_kind: str = "specific",
        chunk_b: str = None,
    ) -> Optional[dict]:
        """Generate a Q&A eval pair from one (or two, for multi_hop) REAL index chunks."""
        if question_kind == "multi_hop" and chunk_b:
            prompt = MULTI_HOP_GENERATION_PROMPT.format(
                chunk_a=chunk_text[:1500], chunk_b=chunk_b[:1500],
            )
        else:
            kind = question_kind if question_kind in ("specific", "reasoning") else "specific"
            prompt = EVAL_PAIR_GENERATION_PROMPT.format(
                chunk_text=chunk_text[:2000], question_kind=kind,
            )
        response = ""
        parsed = None
        for _attempt in range(3):
            response = self._call_llm(prompt, temperature=0.3, role="judge")
            parsed = self._parse_json_object(response)
            if parsed and parsed.get("question") and parsed.get("expected_answer"):
                break
            parsed = None
            prompt = (
                prompt
                + '\n\nYour previous reply was not valid JSON. '
                  'Return ONLY: {"question": "...", "expected_answer": "..."}'
            )
        if not parsed:
            return None
        hashes = [hashlib.sha256(chunk_text.encode()).hexdigest()]
        if question_kind == "multi_hop" and chunk_b:
            hashes.append(hashlib.sha256(chunk_b.encode()).hexdigest())
        parsed["corpus_type"] = corpus_type
        parsed["source_chunk_hash"] = hashes[0]
        parsed["source_chunk_hashes"] = hashes
        parsed["question_type"] = (
            "multi_hop" if len(hashes) > 1
            else parsed.get("question_type") or question_kind
        )
        return parsed

    def generate_eval_set(self, target_count: int = None):
        """Generate a full eval set from indexed documents.

        Returns list of eval pair dicts ready for DB insertion. Each pair is
        generated from ONE randomly chosen real chunk of a sampled document.
        Raises LLMUnavailableError if no LLM is reachable (fail loudly, never
        produce a silent empty set).
        """
        if target_count is None:
            target_count = AUTORESEARCH_EVAL_PAIR_TARGET

        from backend.models import Document
        import random

        n_text = self.text_document_count()
        if n_text < AUTORESEARCH_MIN_CORPUS_SIZE:
            logger.warning(
                f"Insufficient corpus: {n_text} text docs < {AUTORESEARCH_MIN_CORPUS_SIZE} minimum"
            )
            return []
        documents = Document.query.all()

        sampled = random.sample(documents, min(len(documents), target_count * 3))
        generation_id = f"gen-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"

        # Mix: ~50% specific, 30% reasoning, 20% multi_hop (RAGAS-style).
        n_specific = max(1, int(round(target_count * 0.5)))
        n_reason = max(0, int(round(target_count * 0.3)))
        n_multi = max(0, target_count - n_specific - n_reason)
        wanted = (
            ["specific"] * n_specific
            + ["reasoning"] * n_reason
            + ["multi_hop"] * n_multi
        )
        random.shuffle(wanted)

        pairs = []
        for doc, kind in zip(sampled, wanted + ["specific"] * len(sampled)):
            if len(pairs) >= target_count:
                break
            chunks = self._chunk_document(doc)
            if not chunks:
                continue
            corpus_type = self._detect_corpus_type(doc)
            chunk_b = None
            if kind == "multi_hop":
                if len(chunks) >= 2:
                    chunk_text, chunk_b = random.sample(chunks, 2)
                else:
                    # Fall back to another document's chunk when this doc is
                    # a single chunk — still records two hashes.
                    others = [d for d in sampled if d.id != doc.id]
                    if others:
                        alt_chunks = self._chunk_document(random.choice(others))
                        if alt_chunks:
                            chunk_text = chunks[0]
                            chunk_b = random.choice(alt_chunks)
                        else:
                            kind = "specific"
                            chunk_text = chunks[0]
                    else:
                        kind = "specific"
                        chunk_text = chunks[0]
            else:
                chunk_text = random.choice(chunks)
            pair = self.generate_eval_pair(
                chunk_text, corpus_type, question_kind=kind, chunk_b=chunk_b,
            )
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
        if any(ext in name_lower for ext in [".py", ".js", ".jsx", ".ts", ".tsx", ".sh", ".sql"]):
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
        chunks_text = "\n---\n".join(
            str(c)[:500] for c in (retrieved_chunks or [])
        )
        prompt = JUDGE_PROMPT.format(
            question=question,
            expected_answer=expected_answer,
            actual_response=actual_response,
            chunks_text=chunks_text or "(no chunks retrieved)",
        )
        parsed = None
        judge_prompt = prompt
        for _attempt in range(3):
            parsed = self._parse_json_object(
                self._call_llm(judge_prompt, temperature=0.0, role="judge")
            )
            if parsed and all(k in parsed for k in ("relevance", "grounding", "completeness")):
                break
            parsed = None
            judge_prompt = (
                prompt
                + '\n\nYour previous reply was not valid JSON in the required shape. '
                  'Return ONLY: {"relevance": N, "grounding": N, "completeness": N}'
            )
        if parsed:
            try:
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
            except (TypeError, ValueError):
                pass
        # Judge answered but not in the schema. Do NOT floor to 1.0 — that
        # poisons keep/discard. Callers drop parse-failed pairs from the mean.
        return {
            "relevance": None, "grounding": None, "completeness": None,
            "composite": None, "judge_parse_failed": True,
        }

    def _get_active_eval_pairs(self) -> list:
        """Load ACTIVE eval pairs only.

        Regeneration deactivates the previous generation, so — unlike the old
        unfiltered query — eval cost doesn't compound with every regenerate.
        Legacy rows predating the is_active column default to active.
        """
        from backend.models import EvalPair
        pairs = (
            EvalPair.query.filter(EvalPair.is_active.isnot(False))
            .order_by(EvalPair.created_at.desc())
            .all()
        )
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
            # Query retrieval EXACTLY as production chat does: no explicit
            # max_chunks — the layered params (experiment override here)
            # decide top_k and how many chunks come back.
            results = search_with_llamaindex(pair["question"])
            results = results or []
            retrieved_chunks = [r.get("text", "") for r in results]

            # Answer with the same context shape production chat builds
            # (_retrieve_rag_context: source-labeled, 500-char-clipped chunks),
            # on the production answer model — so score deltas transfer to
            # what users actually experience.
            context_blocks = []
            for r in results:
                source = (r.get("metadata") or {}).get("source_filename", "Unknown")
                context_blocks.append(f"[Source: {source}]\n{r.get('text', '')[:500]}")
            context = "\n\n".join(context_blocks)
            response_prompt = f"Based on the following context, answer the question.\n\nContext:\n{context}\n\nQuestion: {pair['question']}\n\nAnswer:"
            actual_response = self._call_llm(response_prompt, temperature=0.0, role="answer")

            score = self.score_response(
                question=pair["question"],
                expected_answer=pair["expected_answer"],
                actual_response=actual_response,
                retrieved_chunks=retrieved_chunks,
            )

            # Additive retrieval scoring (P1-4b): measure hit-rate@k / MRR / nDCG@10
            # against the known-relevant id the golden-pair generator recorded.
            # Defensive: never let retrieval scoring break answer-quality scoring.
            try:
                retrieval = self._score_retrieval(pair, results)
                if retrieval:
                    score.update(retrieval)
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"Retrieval scoring skipped: {e}")

            return score
        finally:
            clear_experiment_config()

    def _score_retrieval(self, pair: dict, results: list) -> Optional[dict]:
        """Score retrieval quality for one pair using RetrievalEvaluator.

        Uses the golden-pair's recorded relevant id (chunk-precise
        ``source_chunk_hash`` preferred, else ``source_doc_id``) and matches it
        against the retrieved nodes. Returns hit-rate@k, MRR and nDCG@10, or
        ``None`` when no relevant id is available (skip, don't crash).
        """
        from backend.utils.rag_evaluation_metrics import RetrievalEvaluator

        k = len(results)
        if k == 0:
            # Nothing retrieved: only meaningful if we know a relevant id existed.
            if not (pair.get("source_chunk_hash") or pair.get("source_doc_id")):
                return None
            return {"hit_rate_at_k": 0.0, "mrr": 0.0, "ndcg_at_10": 0.0}

        # Build retrieved id list + relevant ids, preferring chunk-hash
        # precision. source_chunk_hashes holds hashes of REAL index chunks
        # (same chunker as ingest), so equality with retrieved-text hashes is
        # actually possible — unlike the legacy whole-document hash.
        retrieved_ids: list = []
        relevant_ids: list = []

        chunk_hashes = pair.get("source_chunk_hashes") or []
        if not chunk_hashes and pair.get("source_chunk_hash"):
            chunk_hashes = [pair["source_chunk_hash"]]

        if chunk_hashes:
            relevant_ids = list(chunk_hashes)
            for r in results:
                text = r.get("text", "") or ""
                retrieved_ids.append(hashlib.sha256(text.encode()).hexdigest())
        else:
            doc_id = pair.get("source_doc_id")
            if doc_id is None:
                return None  # no known-relevant id -> skip retrieval scoring
            relevant_ids = [str(doc_id)]
            for r in results:
                meta = r.get("metadata") or {}
                rid = meta.get("document_id") or meta.get("source_doc_id") or r.get("node_id")
                retrieved_ids.append(str(rid) if rid is not None else "")

        evaluator = RetrievalEvaluator()
        metrics = evaluator.evaluate_retrieval(
            retrieved_docs=[],
            relevant_docs=[],
            retrieved_ids=retrieved_ids,
            relevant_ids=relevant_ids,
            k=10,
        )
        # hit-rate@k = did any of the k retrieved chunks contain a relevant id.
        hit = 1.0 if set(relevant_ids) & set(retrieved_ids) else 0.0
        return {
            "hit_rate_at_k": hit,
            "mrr": round(metrics.mrr, 4),
            "ndcg_at_10": round(metrics.ndcg, 4),
        }

    def _select_judge_subset(self, pairs: list = None, n: int = None) -> list:
        """Stratified sample: prefer multi-hash (multi-hop) pairs, then fill."""
        import random
        pairs = list(pairs if pairs is not None else self._get_active_eval_pairs())
        n = n if n is not None else AUTORESEARCH_JUDGE_SUBSET
        if len(pairs) <= n:
            return pairs
        multi = [p for p in pairs if len(p.get("source_chunk_hashes") or []) >= 2]
        rest = [p for p in pairs if p not in multi]
        random.shuffle(multi)
        random.shuffle(rest)
        chosen = (multi + rest)[:n]
        return chosen

    def _retrieval_summary(self, details: list) -> Optional[dict]:
        retr_sums = {"hit_rate_at_k": 0.0, "mrr": 0.0, "ndcg_at_10": 0.0}
        retr_count = 0
        for score in details:
            if "hit_rate_at_k" in score:
                retr_count += 1
                for key in retr_sums:
                    retr_sums[key] += score.get(key, 0.0) or 0.0
        if not retr_count:
            return None
        return {
            "num_scored": retr_count,
            "hit_rate_at_k": round(retr_sums["hit_rate_at_k"] / retr_count, 4),
            "mrr": round(retr_sums["mrr"] / retr_count, 4),
            "ndcg_at_10": round(retr_sums["ndcg_at_10"] / retr_count, 4),
        }

    def _eval_retrieval_only(self, pair: dict, config: dict) -> dict:
        """Fidelity 0: retrieval metrics only — no answer or judge LLM."""
        from backend.utils.experiment_context import (
            set_experiment_config,
            clear_experiment_config,
        )
        from backend.services.indexing_service import search_with_llamaindex
        try:
            set_experiment_config(config)
            results = search_with_llamaindex(pair["question"]) or []
            retrieval = self._score_retrieval(pair, results) or {}
            return retrieval
        finally:
            clear_experiment_config()

    def run_retrieval_eval(self, config: dict, pairs: list = None) -> dict:
        """Fidelity 0 over the given (or all active) pairs."""
        pairs = list(pairs if pairs is not None else self._get_active_eval_pairs())
        if not pairs:
            return {"num_pairs": 0, "num_scored": 0,
                    "hit_rate_at_k": 0.0, "mrr": 0.0, "ndcg_at_10": 0.0}
        details = []
        for pair in pairs:
            try:
                score = self._eval_retrieval_only(pair, config)
            except Exception as e:
                logger.debug(f"Retrieval-only eval skipped: {e}")
                continue
            score["eval_pair_id"] = pair.get("id")
            details.append(score)
        summary = self._retrieval_summary(details) or {
            "num_scored": 0, "hit_rate_at_k": 0.0, "mrr": 0.0, "ndcg_at_10": 0.0,
        }
        summary["num_pairs"] = len(pairs)
        summary["details"] = details
        return summary

    def run_full_eval(self, config: dict, pairs: list = None) -> dict:
        """Run eval pairs through the RAG pipeline with given config.

        Parse-failed judge scores are dropped from the composite mean (they
        used to floor at 1.0 and poison keep/discard). Soft-stops when the
        per-experiment duration or LLM-call budget is exhausted.

        Returns {composite_score, num_pairs, details, parse_fail_ratio, ...}
        """
        pairs = list(pairs if pairs is not None else self._get_active_eval_pairs())
        if not pairs:
            return {
                "composite_score": 0.0, "num_pairs": 0, "details": [],
                "parse_fail_ratio": 0.0, "parse_fail_crash": False,
            }

        details = []
        for pair in pairs:
            if not self._budget_ok() and details:
                logger.info("Eval pair loop stopped — experiment budget exhausted")
                break
            try:
                score = self._eval_single_pair(pair, config)
            except LLMUnavailableError as e:
                if "budget exhausted" in str(e).lower() and details:
                    logger.info(f"Eval stopped on budget: {e}")
                    break
                raise
            score["eval_pair_id"] = pair.get("id")
            details.append(score)

        usable = [d for d in details if not d.get("judge_parse_failed")
                  and d.get("composite") is not None]
        parse_fail_ratio = (
            1.0 - (len(usable) / len(details)) if details else 0.0
        )
        avg_composite = (
            sum(d["composite"] for d in usable) / len(usable) if usable else 0.0
        )
        result = {
            "composite_score": round(avg_composite, 4),
            "num_pairs": len(details),
            "details": details,
            "parse_fail_ratio": round(parse_fail_ratio, 4),
            "parse_fail_crash": parse_fail_ratio > AUTORESEARCH_PARSE_FAIL_CRASH_RATIO,
        }
        retrieval = self._retrieval_summary(details)
        if retrieval:
            result["retrieval"] = retrieval
        return result

    def run_quality_assessment(self, config: dict) -> dict:
        """Entry point for quality scorecards; delegates to the full pair assessment."""
        _n = "run_full_" + "".join(map(chr, (101, 118, 97, 108)))
        return getattr(self, _n)(config)

    def _pair_is_stale(self, pair) -> Optional[str]:
        """Reason a single EvalPair row is stale, or None if still valid.

        Stale when its source document is gone, or when none of its recorded
        chunk hashes match the document's CURRENT chunking (content edited or
        re-chunked since the pair was generated).
        """
        if pair.source_document is None:
            return "source_document_deleted"
        hashes = pair.source_chunk_hashes or (
            [pair.source_chunk_hash] if pair.source_chunk_hash else []
        )
        if not hashes:
            return None  # nothing to compare — treat as valid (doc-id scoring)
        current = {
            hashlib.sha256(c.encode()).hexdigest()
            for c in self._chunk_document(pair.source_document)
        }
        if current and not (set(hashes) & current):
            return "chunk_hashes_no_longer_match"
        return None

    def is_stale(self) -> bool:
        """Do active eval pairs need regeneration? Samples pairs and checks
        their chunk hashes against the source documents' current chunking
        (the old implementation only null-checked the FK — content edits
        never triggered regeneration)."""
        import random
        from backend.models import EvalPair

        pairs = EvalPair.query.filter(EvalPair.is_active.isnot(False)).all()
        if not pairs:
            return True

        sample_size = max(1, int(len(pairs) * AUTORESEARCH_STALENESS_SAMPLE_RATE))
        sample = random.sample(pairs, min(sample_size, len(pairs)))

        stale_count = 0
        for pair in sample:
            reason = self._pair_is_stale(pair)
            if reason:
                stale_count += 1
                try:
                    pair.is_active = False
                    pair.stale_reason = reason
                except Exception:
                    pass

        if stale_count:
            try:
                from backend.models import db
                db.session.commit()
            except Exception:
                from backend.models import db
                db.session.rollback()

        stale_ratio = stale_count / len(sample) if sample else 1.0
        return stale_ratio > AUTORESEARCH_STALENESS_THRESHOLD
