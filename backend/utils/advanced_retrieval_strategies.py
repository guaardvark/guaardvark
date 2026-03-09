#!/usr/bin/env python3
"""
Advanced Retrieval Strategies
Implements time-weighted, authority-weighted, diversity-aware, and negative sampling retrieval
"""

import logging
import numpy as np
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict
import math

from llama_index.core.schema import TextNode

logger = logging.getLogger(__name__)


@dataclass
class RetrievalStrategy:
    """Configuration for retrieval strategies"""
    # Time-based weighting
    enable_time_weighting: bool = True
    time_decay_factor: float = 0.1  # Exponential decay rate
    recent_boost_days: int = 7  # Boost documents within this many days
    recent_boost_multiplier: float = 1.5  # Multiplier for recent docs

    # Authority weighting
    enable_authority_weighting: bool = True
    authority_sources: List[str] = field(default_factory=list)  # Trusted sources
    authority_boost: float = 1.3  # Multiplier for authoritative sources
    citation_weight: float = 0.2  # Weight for citation count

    # Diversity
    enable_diversity: bool = True
    diversity_threshold: float = 0.85  # Similarity threshold for diversity
    max_similar_results: int = 3  # Max results from same cluster
    mmr_lambda: float = 0.5  # MMR lambda parameter (0=diversity, 1=relevance)

    # Negative sampling
    enable_negative_sampling: bool = False
    negative_examples: List[str] = field(default_factory=list)
    negative_weight: float = -0.5  # Penalty for negative similarity


class TimeWeightedRetriever:
    """Retriever with time-based weighting for recency"""

    def __init__(self, strategy: RetrievalStrategy):
        self.strategy = strategy

    def apply_time_weighting(
        self,
        results: List[Tuple[TextNode, float]],
        current_time: Optional[datetime] = None
    ) -> List[Tuple[TextNode, float]]:
        """
        Apply time-based weighting to results

        Args:
            results: List of (node, score) tuples
            current_time: Current timestamp (defaults to now)

        Returns:
            List of (node, score) tuples with time weighting applied
        """
        if not self.strategy.enable_time_weighting:
            return results

        current_time = current_time or datetime.now()
        weighted_results = []

        for node, score in results:
            # Get timestamp from metadata
            timestamp = self._extract_timestamp(node)

            if timestamp:
                # Calculate time weight
                time_weight = self._calculate_time_weight(timestamp, current_time)
                weighted_score = score * time_weight
            else:
                # No timestamp, use original score
                weighted_score = score

            weighted_results.append((node, weighted_score))

        # Re-sort by weighted score
        weighted_results.sort(key=lambda x: x[1], reverse=True)

        logger.debug(f"Applied time weighting to {len(results)} results")
        return weighted_results

    def _extract_timestamp(self, node: TextNode) -> Optional[datetime]:
        """Extract timestamp from node metadata"""
        metadata = node.metadata if hasattr(node, 'metadata') else {}

        # Try various timestamp fields
        timestamp_fields = ['timestamp', 'created_at', 'date', 'published_date', 'last_modified']

        for field in timestamp_fields:
            if field in metadata:
                ts = metadata[field]

                # Handle different timestamp formats
                if isinstance(ts, datetime):
                    return ts
                elif isinstance(ts, str):
                    try:
                        # Try ISO format
                        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    except (ValueError, TypeError):
                        pass
                elif isinstance(ts, (int, float)):
                    try:
                        # Try Unix timestamp
                        return datetime.fromtimestamp(ts)
                    except (ValueError, OSError):
                        pass

        return None

    def _calculate_time_weight(self, timestamp: datetime, current_time: datetime) -> float:
        """
        Calculate time-based weight

        Args:
            timestamp: Document timestamp
            current_time: Current time

        Returns:
            Time weight multiplier (>= 1.0 for recent, < 1.0 for old)
        """
        # Calculate age in days
        age_days = (current_time - timestamp).total_seconds() / 86400

        # Apply recent boost
        if age_days <= self.strategy.recent_boost_days:
            return self.strategy.recent_boost_multiplier

        # Apply exponential decay for older documents
        decay = math.exp(-self.strategy.time_decay_factor * age_days / 365.0)

        # Ensure minimum weight of 0.5
        return max(0.5, decay)


class AuthorityWeightedRetriever:
    """Retriever with authority-based weighting for trusted sources"""

    def __init__(self, strategy: RetrievalStrategy):
        self.strategy = strategy

        # Normalize authority sources to lowercase
        self.authority_sources = set(
            src.lower() for src in strategy.authority_sources
        )

    def apply_authority_weighting(
        self,
        results: List[Tuple[TextNode, float]]
    ) -> List[Tuple[TextNode, float]]:
        """
        Apply authority-based weighting to results

        Args:
            results: List of (node, score) tuples

        Returns:
            List of (node, score) tuples with authority weighting applied
        """
        if not self.strategy.enable_authority_weighting:
            return results

        weighted_results = []

        for node, score in results:
            # Calculate authority weight
            authority_weight = self._calculate_authority_weight(node)
            weighted_score = score * authority_weight

            weighted_results.append((node, weighted_score))

        # Re-sort by weighted score
        weighted_results.sort(key=lambda x: x[1], reverse=True)

        logger.debug(f"Applied authority weighting to {len(results)} results")
        return weighted_results

    def _calculate_authority_weight(self, node: TextNode) -> float:
        """
        Calculate authority weight for a node

        Args:
            node: TextNode to evaluate

        Returns:
            Authority weight multiplier
        """
        metadata = node.metadata if hasattr(node, 'metadata') else {}
        weight = 1.0

        # Check if source is authoritative
        source = metadata.get('source', '').lower()
        if any(auth_src in source for auth_src in self.authority_sources):
            weight *= self.strategy.authority_boost
            logger.debug(f"Applied authority boost to source: {source}")

        # Check for explicit authority score
        if 'authority_score' in metadata:
            try:
                auth_score = float(metadata['authority_score'])
                weight *= (1.0 + auth_score * 0.5)  # Max 50% boost from authority score
            except (ValueError, TypeError):
                pass

        # Check citation count (if available)
        if 'citation_count' in metadata:
            try:
                citations = int(metadata['citation_count'])
                # Logarithmic scaling for citations
                citation_bonus = math.log(1 + citations) * self.strategy.citation_weight
                weight *= (1.0 + citation_bonus)
            except (ValueError, TypeError):
                pass

        # Check for verified or trusted flag
        if metadata.get('verified') or metadata.get('trusted'):
            weight *= 1.2

        return weight

    def add_authority_source(self, source: str) -> None:
        """Add an authority source to the trusted list"""
        self.authority_sources.add(source.lower())
        logger.info(f"Added authority source: {source}")

    def remove_authority_source(self, source: str) -> None:
        """Remove an authority source from the trusted list"""
        self.authority_sources.discard(source.lower())
        logger.info(f"Removed authority source: {source}")


class DiversityAwareRetriever:
    """Retriever with diversity-aware filtering using MMR"""

    def __init__(self, strategy: RetrievalStrategy):
        self.strategy = strategy

    def apply_diversity_filtering(
        self,
        results: List[Tuple[TextNode, float]],
        top_k: int
    ) -> List[Tuple[TextNode, float]]:
        """
        Apply Maximal Marginal Relevance (MMR) for diversity

        Args:
            results: List of (node, score) tuples
            top_k: Number of diverse results to return

        Returns:
            Diversified list of (node, score) tuples
        """
        if not self.strategy.enable_diversity or len(results) <= top_k:
            return results[:top_k]

        # Extract nodes and scores
        nodes = [node for node, _ in results]
        scores = [score for _, score in results]

        # Apply MMR
        selected_indices = self._mmr_selection(
            nodes, scores, top_k, self.strategy.mmr_lambda
        )

        # Build diverse results
        diverse_results = [
            (nodes[i], scores[i]) for i in selected_indices
        ]

        logger.debug(f"Applied diversity filtering: {len(results)} -> {len(diverse_results)}")
        return diverse_results

    def _mmr_selection(
        self,
        nodes: List[TextNode],
        scores: List[float],
        k: int,
        lambda_param: float
    ) -> List[int]:
        """
        Maximal Marginal Relevance selection

        Args:
            nodes: List of nodes
            scores: Relevance scores
            k: Number of results to select
            lambda_param: Trade-off parameter (0=diversity, 1=relevance)

        Returns:
            List of selected indices
        """
        selected = []
        remaining = list(range(len(nodes)))

        # Start with highest scoring document
        if remaining:
            best_idx = max(remaining, key=lambda i: scores[i])
            selected.append(best_idx)
            remaining.remove(best_idx)

        # Select remaining documents
        while len(selected) < k and remaining:
            best_score = float('-inf')
            best_idx = None

            for idx in remaining:
                # Relevance score
                relevance = scores[idx]

                # Maximum similarity to already selected documents
                max_similarity = 0.0
                for sel_idx in selected:
                    similarity = self._calculate_similarity(
                        nodes[idx], nodes[sel_idx]
                    )
                    max_similarity = max(max_similarity, similarity)

                # MMR score
                mmr_score = lambda_param * relevance - (1 - lambda_param) * max_similarity

                if mmr_score > best_score:
                    best_score = mmr_score
                    best_idx = idx

            if best_idx is not None:
                selected.append(best_idx)
                remaining.remove(best_idx)
            else:
                break

        return selected

    def _calculate_similarity(self, node1: TextNode, node2: TextNode) -> float:
        """
        Calculate similarity between two nodes

        For now uses simple text overlap. Could be enhanced with embeddings.
        """
        text1 = set(node1.get_content().lower().split())
        text2 = set(node2.get_content().lower().split())

        if not text1 or not text2:
            return 0.0

        # Jaccard similarity
        intersection = len(text1 & text2)
        union = len(text1 | text2)

        return intersection / union if union > 0 else 0.0

    def remove_redundant_results(
        self,
        results: List[Tuple[TextNode, float]]
    ) -> List[Tuple[TextNode, float]]:
        """
        Remove highly similar/redundant results

        Args:
            results: List of (node, score) tuples

        Returns:
            Filtered list without redundant results
        """
        if not self.strategy.enable_diversity:
            return results

        filtered = []
        seen_content = []

        for node, score in results:
            # Check if too similar to existing results
            is_redundant = False

            for seen_node in seen_content:
                similarity = self._calculate_similarity(node, seen_node)
                if similarity >= self.strategy.diversity_threshold:
                    is_redundant = True
                    break

            if not is_redundant:
                filtered.append((node, score))
                seen_content.append(node)

                # Limit results per cluster
                if len(filtered) >= len(results):
                    break

        logger.debug(f"Removed {len(results) - len(filtered)} redundant results")
        return filtered


class NegativeSamplingRetriever:
    """Retriever with negative sampling for contrast learning"""

    def __init__(self, strategy: RetrievalStrategy):
        self.strategy = strategy

    def apply_negative_filtering(
        self,
        results: List[Tuple[TextNode, float]],
        negative_examples: Optional[List[str]] = None
    ) -> List[Tuple[TextNode, float]]:
        """
        Apply negative sampling to filter out unwanted results

        Args:
            results: List of (node, score) tuples
            negative_examples: List of negative example texts

        Returns:
            Filtered list of (node, score) tuples
        """
        if not self.strategy.enable_negative_sampling:
            return results

        negative_examples = negative_examples or self.strategy.negative_examples
        if not negative_examples:
            return results

        # Convert negative examples to word sets
        negative_sets = [
            set(example.lower().split()) for example in negative_examples
        ]

        filtered_results = []

        for node, score in results:
            # Calculate negative similarity
            negative_score = self._calculate_negative_similarity(
                node, negative_sets
            )

            # Apply negative weight
            adjusted_score = score + (negative_score * self.strategy.negative_weight)

            # Only include if adjusted score is still positive
            if adjusted_score > 0:
                filtered_results.append((node, adjusted_score))

        # Re-sort by adjusted score
        filtered_results.sort(key=lambda x: x[1], reverse=True)

        logger.debug(f"Applied negative filtering: {len(results)} -> {len(filtered_results)}")
        return filtered_results

    def _calculate_negative_similarity(
        self,
        node: TextNode,
        negative_sets: List[Set[str]]
    ) -> float:
        """
        Calculate maximum similarity to negative examples

        Args:
            node: Node to evaluate
            negative_sets: List of word sets from negative examples

        Returns:
            Maximum negative similarity (0-1)
        """
        text = set(node.get_content().lower().split())

        max_similarity = 0.0
        for neg_set in negative_sets:
            if not neg_set:
                continue

            # Jaccard similarity
            intersection = len(text & neg_set)
            union = len(text | neg_set)
            similarity = intersection / union if union > 0 else 0.0

            max_similarity = max(max_similarity, similarity)

        return max_similarity


class AdvancedRetrievalPipeline:
    """
    Pipeline combining all advanced retrieval strategies
    """

    def __init__(self, strategy: Optional[RetrievalStrategy] = None):
        """
        Initialize advanced retrieval pipeline

        Args:
            strategy: RetrievalStrategy configuration
        """
        self.strategy = strategy or RetrievalStrategy()

        # Initialize retrievers
        self.time_retriever = TimeWeightedRetriever(self.strategy)
        self.authority_retriever = AuthorityWeightedRetriever(self.strategy)
        self.diversity_retriever = DiversityAwareRetriever(self.strategy)
        self.negative_retriever = NegativeSamplingRetriever(self.strategy)

        logger.info("Initialized AdvancedRetrievalPipeline")

    def apply_all_strategies(
        self,
        results: List[Tuple[TextNode, float]],
        top_k: int,
        current_time: Optional[datetime] = None,
        negative_examples: Optional[List[str]] = None
    ) -> List[Tuple[TextNode, float]]:
        """
        Apply all retrieval strategies in sequence

        Args:
            results: Initial retrieval results
            top_k: Number of final results
            current_time: Current timestamp for time weighting
            negative_examples: Negative examples for filtering

        Returns:
            Processed and ranked results
        """
        # 1. Apply time weighting
        if self.strategy.enable_time_weighting:
            results = self.time_retriever.apply_time_weighting(results, current_time)

        # 2. Apply authority weighting
        if self.strategy.enable_authority_weighting:
            results = self.authority_retriever.apply_authority_weighting(results)

        # 3. Apply negative filtering
        if self.strategy.enable_negative_sampling:
            results = self.negative_retriever.apply_negative_filtering(
                results, negative_examples
            )

        # 4. Apply diversity filtering
        if self.strategy.enable_diversity:
            results = self.diversity_retriever.apply_diversity_filtering(results, top_k * 2)

        # 5. Remove redundant results
        if self.strategy.enable_diversity:
            results = self.diversity_retriever.remove_redundant_results(results)

        # 6. Return top-k
        return results[:top_k]

    def add_authority_source(self, source: str) -> None:
        """Add an authority source"""
        self.authority_retriever.add_authority_source(source)

    def remove_authority_source(self, source: str) -> None:
        """Remove an authority source"""
        self.authority_retriever.remove_authority_source(source)


def create_advanced_pipeline(
    strategy: Optional[RetrievalStrategy] = None
) -> AdvancedRetrievalPipeline:
    """
    Convenience function to create an AdvancedRetrievalPipeline

    Args:
        strategy: Optional retrieval strategy configuration

    Returns:
        AdvancedRetrievalPipeline instance
    """
    return AdvancedRetrievalPipeline(strategy)
