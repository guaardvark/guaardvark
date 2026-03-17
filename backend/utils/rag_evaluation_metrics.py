#!/usr/bin/env python3
"""
RAG Evaluation Metrics
Provides comprehensive evaluation framework for RAG systems including:
- Retrieval precision/recall tracking
- Answer relevance scoring
- User feedback integration
- A/B testing framework
"""

import logging
import json
from typing import List, Dict, Any, Optional, Tuple, Set
from dataclasses import dataclass, field, asdict
from datetime import datetime
from collections import defaultdict
from pathlib import Path
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class RetrievalMetrics:
    """Metrics for retrieval performance"""

    precision: float = 0.0
    recall: float = 0.0
    f1_score: float = 0.0
    mrr: float = 0.0  # Mean Reciprocal Rank
    ndcg: float = 0.0  # Normalized Discounted Cumulative Gain
    map_score: float = 0.0  # Mean Average Precision


@dataclass
class AnswerRelevanceMetrics:
    """Metrics for answer quality"""

    relevance_score: float = 0.0
    coherence_score: float = 0.0
    completeness_score: float = 0.0
    factual_consistency: float = 0.0
    citation_quality: float = 0.0


@dataclass
class UserFeedback:
    """User feedback on a retrieval/answer"""

    feedback_id: str
    query: str
    result_id: str
    rating: int  # 1-5 stars
    is_relevant: Optional[bool] = None
    is_helpful: Optional[bool] = None
    comment: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    user_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ABTestResult:
    """Result from A/B test comparison"""

    test_id: str
    variant_a: str
    variant_b: str
    queries_tested: int
    variant_a_wins: int
    variant_b_wins: int
    ties: int
    statistical_significance: float
    preferred_variant: Optional[str] = None
    metrics_a: Dict[str, float] = field(default_factory=dict)
    metrics_b: Dict[str, float] = field(default_factory=dict)


class RetrievalEvaluator:
    """Evaluates retrieval performance"""

    def __init__(self):
        self.evaluation_history = []

    def evaluate_retrieval(
        self,
        retrieved_docs: List[Any],
        relevant_docs: List[Any],
        retrieved_ids: Optional[List[str]] = None,
        relevant_ids: Optional[List[str]] = None,
        k: int = 10,
    ) -> RetrievalMetrics:
        """
        Evaluate retrieval performance

        Args:
            retrieved_docs: Retrieved documents
            relevant_docs: Ground truth relevant documents
            retrieved_ids: IDs of retrieved docs (if available)
            relevant_ids: IDs of relevant docs (if available)
            k: Number of top results to consider

        Returns:
            RetrievalMetrics object
        """
        # Extract IDs if not provided
        if retrieved_ids is None:
            retrieved_ids = [self._extract_id(doc) for doc in retrieved_docs[:k]]
        if relevant_ids is None:
            relevant_ids = [self._extract_id(doc) for doc in relevant_docs]

        # Convert to sets for intersection
        retrieved_set = set(retrieved_ids[:k])
        relevant_set = set(relevant_ids)

        # Calculate metrics
        metrics = RetrievalMetrics()

        # Precision and Recall
        if retrieved_set:
            true_positives = len(retrieved_set & relevant_set)
            metrics.precision = true_positives / len(retrieved_set)

            if relevant_set:
                metrics.recall = true_positives / len(relevant_set)

                # F1 Score
                if metrics.precision + metrics.recall > 0:
                    metrics.f1_score = (
                        2
                        * (metrics.precision * metrics.recall)
                        / (metrics.precision + metrics.recall)
                    )

        # Mean Reciprocal Rank (MRR)
        metrics.mrr = self._calculate_mrr(retrieved_ids, relevant_set)

        # Normalized Discounted Cumulative Gain (NDCG)
        metrics.ndcg = self._calculate_ndcg(retrieved_ids, relevant_set, k)

        # Mean Average Precision (MAP)
        metrics.map_score = self._calculate_map(retrieved_ids, relevant_set, k)

        # Store in history
        self.evaluation_history.append(
            {
                "timestamp": datetime.now().isoformat(),
                "metrics": asdict(metrics),
                "k": k,
            }
        )

        logger.info(
            f"Retrieval evaluation: P={metrics.precision:.3f}, "
            f"R={metrics.recall:.3f}, F1={metrics.f1_score:.3f}"
        )

        return metrics

    def _extract_id(self, doc: Any) -> str:
        """Extract ID from document"""
        if isinstance(doc, dict):
            return doc.get("id") or doc.get("doc_id") or str(doc)
        elif hasattr(doc, "id_"):
            return doc.id_
        elif hasattr(doc, "node_id"):
            return doc.node_id
        else:
            return str(doc)

    def _calculate_mrr(self, retrieved_ids: List[str], relevant_set: Set[str]) -> float:
        """Calculate Mean Reciprocal Rank"""
        for i, doc_id in enumerate(retrieved_ids, 1):
            if doc_id in relevant_set:
                return 1.0 / i
        return 0.0

    def _calculate_ndcg(
        self, retrieved_ids: List[str], relevant_set: Set[str], k: int
    ) -> float:
        """Calculate Normalized Discounted Cumulative Gain"""
        # DCG
        dcg = 0.0
        for i, doc_id in enumerate(retrieved_ids[:k], 1):
            if doc_id in relevant_set:
                dcg += 1.0 / np.log2(i + 1)

        # IDCG (ideal DCG)
        idcg = 0.0
        for i in range(1, min(len(relevant_set), k) + 1):
            idcg += 1.0 / np.log2(i + 1)

        return dcg / idcg if idcg > 0 else 0.0

    def _calculate_map(
        self, retrieved_ids: List[str], relevant_set: Set[str], k: int
    ) -> float:
        """Calculate Mean Average Precision"""
        if not relevant_set:
            return 0.0

        average_precision = 0.0
        num_relevant = 0

        for i, doc_id in enumerate(retrieved_ids[:k], 1):
            if doc_id in relevant_set:
                num_relevant += 1
                precision_at_i = num_relevant / i
                average_precision += precision_at_i

        return average_precision / len(relevant_set) if relevant_set else 0.0

    def get_aggregate_metrics(self) -> Dict[str, float]:
        """Get aggregate metrics across all evaluations"""
        if not self.evaluation_history:
            return {}

        metrics_list = [entry["metrics"] for entry in self.evaluation_history]

        aggregate = {}
        for key in metrics_list[0].keys():
            values = [m[key] for m in metrics_list]
            aggregate[f"avg_{key}"] = np.mean(values)
            aggregate[f"std_{key}"] = np.std(values)
            aggregate[f"min_{key}"] = np.min(values)
            aggregate[f"max_{key}"] = np.max(values)

        return aggregate


class AnswerRelevanceEvaluator:
    """Evaluates answer quality and relevance"""

    def __init__(self, llm=None):
        self.llm = llm
        self.evaluation_history = []

    def evaluate_answer(
        self,
        query: str,
        answer: str,
        retrieved_context: List[str],
        ground_truth: Optional[str] = None,
    ) -> AnswerRelevanceMetrics:
        """
        Evaluate answer quality

        Args:
            query: Original query
            answer: Generated answer
            retrieved_context: Retrieved context used
            ground_truth: Optional ground truth answer

        Returns:
            AnswerRelevanceMetrics object
        """
        metrics = AnswerRelevanceMetrics()

        # Relevance score
        metrics.relevance_score = self._evaluate_relevance(query, answer)

        # Coherence score
        metrics.coherence_score = self._evaluate_coherence(answer)

        # Completeness score
        metrics.completeness_score = self._evaluate_completeness(query, answer)

        # Factual consistency with context
        metrics.factual_consistency = self._evaluate_factual_consistency(
            answer, retrieved_context
        )

        # Citation quality
        metrics.citation_quality = self._evaluate_citation_quality(
            answer, retrieved_context
        )

        # Store in history
        self.evaluation_history.append(
            {
                "timestamp": datetime.now().isoformat(),
                "query": query,
                "metrics": asdict(metrics),
            }
        )

        logger.info(
            f"Answer evaluation: Relevance={metrics.relevance_score:.3f}, "
            f"Coherence={metrics.coherence_score:.3f}"
        )

        return metrics

    def _evaluate_relevance(self, query: str, answer: str) -> float:
        """Evaluate query-answer relevance"""
        # Simple keyword overlap approach
        query_words = set(query.lower().split())
        answer_words = set(answer.lower().split())

        # Remove common stop words
        stop_words = {
            "the",
            "a",
            "an",
            "and",
            "or",
            "but",
            "in",
            "on",
            "at",
            "to",
            "for",
        }
        query_words -= stop_words
        answer_words -= stop_words

        if not query_words:
            return 0.5

        # Calculate overlap
        overlap = len(query_words & answer_words)
        relevance = overlap / len(query_words)

        return min(1.0, relevance)

    def _evaluate_coherence(self, answer: str) -> float:
        """Evaluate answer coherence"""
        # Simple heuristics
        score = 0.5  # Base score

        # Check length (not too short, not too long)
        if 50 <= len(answer) <= 1000:
            score += 0.2

        # Check for complete sentences
        sentences = answer.split(".")
        if len(sentences) >= 2:
            score += 0.1

        # Check for proper capitalization
        if answer and answer[0].isupper():
            score += 0.1

        # Check for conclusion words
        conclusion_words = ["therefore", "thus", "in conclusion", "finally", "overall"]
        if any(word in answer.lower() for word in conclusion_words):
            score += 0.1

        return min(1.0, score)

    def _evaluate_completeness(self, query: str, answer: str) -> float:
        """Evaluate answer completeness"""
        # Check if answer addresses query aspects
        score = 0.5

        # Extract question words
        question_words = {"what", "where", "when", "who", "why", "how", "which"}
        query_lower = query.lower()

        # Check if question type is addressed
        for qword in question_words:
            if qword in query_lower:
                # Check if answer provides appropriate response
                if qword == "why" and (
                    "because" in answer.lower() or "reason" in answer.lower()
                ):
                    score += 0.2
                elif qword == "how" and (
                    "by" in answer.lower() or "through" in answer.lower()
                ):
                    score += 0.2
                elif qword in ["what", "which", "who"] and len(answer) > 20:
                    score += 0.2

        # Check answer length proportional to query complexity
        if len(answer.split()) >= len(query.split()) * 3:
            score += 0.1

        return min(1.0, score)

    def _evaluate_factual_consistency(self, answer: str, context: List[str]) -> float:
        """Evaluate factual consistency with retrieved context"""
        if not context:
            return 0.5

        # Extract key facts from answer (simple noun phrases)
        answer_words = set(answer.lower().split())

        # Check overlap with context
        context_text = " ".join(context).lower()
        context_words = set(context_text.split())

        # Calculate overlap
        overlap = len(answer_words & context_words)
        consistency = overlap / len(answer_words) if answer_words else 0.5

        return min(1.0, consistency)

    def _evaluate_citation_quality(self, answer: str, context: List[str]) -> float:
        """Evaluate quality of citations"""
        # Check for citation patterns
        citation_patterns = [
            r"\[\d+\]",  # [1], [2], etc.
            r"\(\d+\)",  # (1), (2), etc.
            r"according to",
            r"based on",
            r"as stated in",
        ]

        score = 0.0
        import re

        for pattern in citation_patterns:
            if re.search(pattern, answer.lower()):
                score += 0.2

        return min(1.0, score)


class UserFeedbackIntegration:
    """Manages user feedback for RAG system"""

    def __init__(self, storage_path: Optional[str] = None):
        """
        Initialize feedback integration

        Args:
            storage_path: Path to store feedback data
        """
        if storage_path:
            self.storage_path = Path(storage_path)
            self.storage_path.mkdir(parents=True, exist_ok=True)
        else:
            from backend.config import STORAGE_DIR

            self.storage_path = Path(STORAGE_DIR) / "feedback"
            self.storage_path.mkdir(parents=True, exist_ok=True)

        self.feedback_db = []
        self._load_feedback()

    def _load_feedback(self):
        """Load feedback from storage"""
        feedback_file = self.storage_path / "user_feedback.json"
        if feedback_file.exists():
            try:
                with open(feedback_file, "r") as f:
                    data = json.load(f)
                    self.feedback_db = data
                logger.info(f"Loaded {len(self.feedback_db)} feedback entries")
            except Exception as e:
                logger.error(f"Failed to load feedback: {e}")

    def _save_feedback(self):
        """Save feedback to storage"""
        feedback_file = self.storage_path / "user_feedback.json"
        try:
            with open(feedback_file, "w") as f:
                json.dump(self.feedback_db, f, indent=2, default=str)
            logger.debug("Saved feedback to storage")
        except Exception as e:
            logger.error(f"Failed to save feedback: {e}")

    def add_feedback(self, feedback: UserFeedback) -> None:
        """
        Add user feedback

        Args:
            feedback: UserFeedback object
        """
        feedback_dict = asdict(feedback)
        feedback_dict["timestamp"] = feedback.timestamp.isoformat()
        self.feedback_db.append(feedback_dict)
        self._save_feedback()

        logger.info(
            f"Added feedback: {feedback.feedback_id} - Rating: {feedback.rating}"
        )

    def get_feedback_for_query(self, query: str) -> List[UserFeedback]:
        """Get all feedback for a specific query"""
        results = []
        for fb in self.feedback_db:
            if fb["query"] == query:
                results.append(UserFeedback(**fb))
        return results

    def get_average_rating(self, query: Optional[str] = None) -> float:
        """Get average rating (optionally for specific query)"""
        if query:
            feedbacks = [fb for fb in self.feedback_db if fb["query"] == query]
        else:
            feedbacks = self.feedback_db

        if not feedbacks:
            return 0.0

        ratings = [fb["rating"] for fb in feedbacks]
        return np.mean(ratings)

    def get_relevance_rate(self) -> float:
        """Get percentage of results marked as relevant"""
        relevant_count = sum(
            1 for fb in self.feedback_db if fb.get("is_relevant") is True
        )
        total = len(self.feedback_db)
        return relevant_count / total if total > 0 else 0.0

    def get_feedback_stats(self) -> Dict[str, Any]:
        """Get aggregate feedback statistics"""
        if not self.feedback_db:
            return {}

        return {
            "total_feedback": len(self.feedback_db),
            "average_rating": self.get_average_rating(),
            "relevance_rate": self.get_relevance_rate(),
            "rating_distribution": self._get_rating_distribution(),
            "latest_feedback": (
                self.feedback_db[-5:]
                if len(self.feedback_db) >= 5
                else self.feedback_db
            ),
        }

    def _get_rating_distribution(self) -> Dict[int, int]:
        """Get distribution of ratings"""
        distribution = defaultdict(int)
        for fb in self.feedback_db:
            distribution[fb["rating"]] += 1
        return dict(distribution)


class ABTestingFramework:
    """A/B testing framework for comparing RAG variants"""

    def __init__(self, storage_path: Optional[str] = None):
        """
        Initialize A/B testing framework

        Args:
            storage_path: Path to store test results
        """
        if storage_path:
            self.storage_path = Path(storage_path)
        else:
            from backend.config import STORAGE_DIR

            self.storage_path = Path(STORAGE_DIR) / "ab_tests"

        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.active_tests = {}
        self.completed_tests = []

    def create_test(
        self, test_id: str, variant_a: str, variant_b: str, description: str = ""
    ) -> None:
        """
        Create a new A/B test

        Args:
            test_id: Unique test identifier
            variant_a: Name of variant A
            variant_b: Name of variant B
            description: Test description
        """
        self.active_tests[test_id] = {
            "test_id": test_id,
            "variant_a": variant_a,
            "variant_b": variant_b,
            "description": description,
            "results_a": [],
            "results_b": [],
            "queries_tested": 0,
            "created_at": datetime.now().isoformat(),
        }

        logger.info(f"Created A/B test: {test_id} ({variant_a} vs {variant_b})")

    def record_result(
        self, test_id: str, variant: str, query: str, metrics: Dict[str, float]
    ) -> None:
        """
        Record result for a variant

        Args:
            test_id: Test identifier
            variant: Variant name (must match variant_a or variant_b)
            query: Query tested
            metrics: Performance metrics
        """
        if test_id not in self.active_tests:
            logger.warning(f"Test {test_id} not found")
            return

        test = self.active_tests[test_id]

        result = {
            "query": query,
            "metrics": metrics,
            "timestamp": datetime.now().isoformat(),
        }

        if variant == test["variant_a"]:
            test["results_a"].append(result)
        elif variant == test["variant_b"]:
            test["results_b"].append(result)
        else:
            logger.warning(f"Unknown variant: {variant}")
            return

        test["queries_tested"] += 1

    def analyze_test(self, test_id: str) -> ABTestResult:
        """
        Analyze A/B test results

        Args:
            test_id: Test identifier

        Returns:
            ABTestResult with analysis
        """
        if test_id not in self.active_tests:
            raise ValueError(f"Test {test_id} not found")

        test = self.active_tests[test_id]

        # Calculate aggregate metrics
        metrics_a = self._aggregate_metrics(test["results_a"])
        metrics_b = self._aggregate_metrics(test["results_b"])

        # Compare variants
        wins_a, wins_b, ties = self._compare_variants(metrics_a, metrics_b)

        # Statistical significance (simplified)
        total_comparisons = wins_a + wins_b + ties
        significance = (
            abs(wins_a - wins_b) / total_comparisons if total_comparisons > 0 else 0.0
        )

        # Determine preferred variant
        preferred = None
        if significance > 0.1:  # 10% threshold
            preferred = test["variant_a"] if wins_a > wins_b else test["variant_b"]

        result = ABTestResult(
            test_id=test_id,
            variant_a=test["variant_a"],
            variant_b=test["variant_b"],
            queries_tested=test["queries_tested"],
            variant_a_wins=wins_a,
            variant_b_wins=wins_b,
            ties=ties,
            statistical_significance=significance,
            preferred_variant=preferred,
            metrics_a=metrics_a,
            metrics_b=metrics_b,
        )

        logger.info(
            f"A/B test analysis: {preferred or 'No clear winner'} "
            f"(sig={significance:.3f})"
        )

        return result

    def _aggregate_metrics(self, results: List[Dict]) -> Dict[str, float]:
        """Aggregate metrics across results"""
        if not results:
            return {}

        aggregated = defaultdict(list)
        for result in results:
            for key, value in result["metrics"].items():
                aggregated[key].append(value)

        return {key: np.mean(values) for key, values in aggregated.items()}

    def _compare_variants(
        self, metrics_a: Dict[str, float], metrics_b: Dict[str, float]
    ) -> Tuple[int, int, int]:
        """Compare metrics between variants"""
        wins_a = 0
        wins_b = 0
        ties = 0

        for key in metrics_a.keys():
            if key not in metrics_b:
                continue

            val_a = metrics_a[key]
            val_b = metrics_b[key]

            if abs(val_a - val_b) < 0.01:  # Tie threshold
                ties += 1
            elif val_a > val_b:
                wins_a += 1
            else:
                wins_b += 1

        return wins_a, wins_b, ties

    def complete_test(self, test_id: str) -> None:
        """Mark test as completed and archive results"""
        if test_id not in self.active_tests:
            logger.warning(f"Test {test_id} not found")
            return

        test = self.active_tests[test_id]
        result = self.analyze_test(test_id)

        # Archive test
        self.completed_tests.append(
            {
                "test": test,
                "result": asdict(result),
                "completed_at": datetime.now().isoformat(),
            }
        )

        # Save to file
        test_file = self.storage_path / f"{test_id}_results.json"
        with open(test_file, "w") as f:
            json.dump(self.completed_tests[-1], f, indent=2, default=str)

        # Remove from active tests
        del self.active_tests[test_id]

        logger.info(f"Completed A/B test: {test_id}")
