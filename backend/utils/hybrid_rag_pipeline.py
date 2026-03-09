#!/usr/bin/env python3
"""
Hybrid RAG Pipeline
Combines dense (semantic) and sparse (keyword) retrieval with query expansion and reranking
"""

# Force local LlamaIndex configuration BEFORE any LlamaIndex imports
import backend.utils.llama_index_local_config

import logging
import re
from typing import List, Dict, Any, Optional, Tuple, Set
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict, Counter

# BM25 for sparse retrieval
from rank_bm25 import BM25Okapi

# LlamaIndex imports
from llama_index.core import VectorStoreIndex, Settings
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode

# Local imports
from backend.utils.unified_index_manager import UnifiedIndexManager

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """Result from hybrid retrieval"""
    node: TextNode
    score: float
    retrieval_method: str  # 'dense', 'sparse', 'expanded', 'reranked'
    original_rank: int
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HybridRetrievalConfig:
    """Configuration for hybrid retrieval"""
    # Number of results to retrieve
    dense_top_k: int = 10
    sparse_top_k: int = 10
    expanded_top_k: int = 5
    final_top_k: int = 10

    # Query expansion
    enable_query_expansion: bool = True
    max_expanded_queries: int = 3

    # Reranking
    enable_reranking: bool = True
    rerank_method: str = 'rrf'  # 'rrf' (Reciprocal Rank Fusion), 'score', 'weighted'

    # Weights for hybrid scoring
    dense_weight: float = 0.5
    sparse_weight: float = 0.3
    expanded_weight: float = 0.2

    # Deduplication
    enable_deduplication: bool = True
    similarity_threshold: float = 0.95  # For deduplication

    # BM25 settings
    bm25_k1: float = 1.5  # Term frequency saturation
    bm25_b: float = 0.75  # Length normalization


class BM25Index:
    """BM25 sparse retrieval index"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.bm25 = None
        self.documents = []
        self.tokenized_corpus = []
        self.node_mapping = {}

    def build_index(self, nodes: List[TextNode]) -> None:
        """Build BM25 index from nodes"""
        self.documents = []
        self.node_mapping = {}

        for idx, node in enumerate(nodes):
            text = node.get_content()
            self.documents.append(text)
            self.node_mapping[idx] = node

        # Tokenize corpus
        self.tokenized_corpus = [self._tokenize(doc) for doc in self.documents]

        # Build BM25 index
        self.bm25 = BM25Okapi(self.tokenized_corpus, k1=self.k1, b=self.b)

        logger.info(f"Built BM25 index with {len(self.documents)} documents")

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text for BM25"""
        # Simple tokenization - lowercase and split on non-alphanumeric
        text = text.lower()
        tokens = re.findall(r'\b\w+\b', text)
        return tokens

    def search(self, query: str, top_k: int = 10) -> List[Tuple[TextNode, float]]:
        """Search using BM25"""
        if self.bm25 is None:
            logger.warning("BM25 index not built")
            return []

        # Tokenize query
        tokenized_query = self._tokenize(query)

        # Get BM25 scores
        scores = self.bm25.get_scores(tokenized_query)

        # Get top-k results
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]

        results = []
        for idx in top_indices:
            if idx in self.node_mapping:
                results.append((self.node_mapping[idx], float(scores[idx])))

        return results


class QueryExpander:
    """Expands queries for better retrieval"""

    def __init__(self, llm=None):
        self.llm = llm or Settings.llm
        self.expansion_cache = {}

    def expand_query(self, query: str, max_expansions: int = 3) -> List[str]:
        """Expand query into multiple variations"""
        # Check cache
        cache_key = f"{query}_{max_expansions}"
        if cache_key in self.expansion_cache:
            return self.expansion_cache[cache_key]

        expanded_queries = [query]  # Always include original

        # Method 1: Synonym expansion (simple approach)
        synonym_query = self._expand_with_synonyms(query)
        if synonym_query and synonym_query != query:
            expanded_queries.append(synonym_query)

        # Method 2: Question reformulation
        reformulated = self._reformulate_query(query)
        if reformulated and reformulated != query:
            expanded_queries.append(reformulated)

        # Method 3: Entity-based expansion
        entity_query = self._expand_with_entities(query)
        if entity_query and entity_query != query:
            expanded_queries.append(entity_query)

        # Limit to max_expansions
        expanded_queries = expanded_queries[:max_expansions]

        # Cache results
        self.expansion_cache[cache_key] = expanded_queries

        logger.debug(f"Expanded query '{query}' to {len(expanded_queries)} variations")
        return expanded_queries

    def _expand_with_synonyms(self, query: str) -> str:
        """Expand query with synonyms (simple keyword approach)"""
        # Simple synonym mapping
        synonyms = {
            'error': 'problem issue bug failure',
            'fix': 'repair resolve solve correct',
            'create': 'make generate build develop',
            'delete': 'remove erase destroy',
            'update': 'modify change edit revise',
            'find': 'search locate discover',
            'how': 'what method way process',
            'why': 'reason cause explanation',
        }

        words = query.lower().split()
        expanded_words = []

        for word in words:
            expanded_words.append(word)
            if word in synonyms:
                # Add first synonym
                first_synonym = synonyms[word].split()[0]
                expanded_words.append(first_synonym)

        return ' '.join(expanded_words)

    def _reformulate_query(self, query: str) -> str:
        """Reformulate query as a different question type"""
        query_lower = query.lower()

        # Convert "how to X" to "X methods"
        if query_lower.startswith('how to '):
            base = query[7:]
            return f"{base} methods and approaches"

        # Convert "what is X" to "X definition"
        if query_lower.startswith('what is '):
            base = query[8:]
            return f"{base} definition and explanation"

        # Convert "why X" to "X reasons"
        if query_lower.startswith('why '):
            base = query[4:]
            return f"{base} causes and reasons"

        # Add context words
        return f"{query} explanation examples"

    def _expand_with_entities(self, query: str) -> str:
        """Expand query with extracted entities"""
        # Simple entity extraction - capitalized words
        entities = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', query)

        if entities:
            # Add entity context
            entity_terms = ' '.join(entities)
            return f"{query} {entity_terms} related"

        return query


class HybridReranker:
    """Reranks combined results from multiple retrieval methods"""

    def __init__(self, method: str = 'rrf'):
        self.method = method

    def rerank(
        self,
        dense_results: List[Tuple[TextNode, float]],
        sparse_results: List[Tuple[TextNode, float]],
        expanded_results: List[List[Tuple[TextNode, float]]],
        config: HybridRetrievalConfig
    ) -> List[RetrievalResult]:
        """Rerank all results"""

        if self.method == 'rrf':
            return self._reciprocal_rank_fusion(
                dense_results, sparse_results, expanded_results, config
            )
        elif self.method == 'score':
            return self._score_based_ranking(
                dense_results, sparse_results, expanded_results, config
            )
        elif self.method == 'weighted':
            return self._weighted_ranking(
                dense_results, sparse_results, expanded_results, config
            )
        else:
            logger.warning(f"Unknown rerank method: {self.method}, using RRF")
            return self._reciprocal_rank_fusion(
                dense_results, sparse_results, expanded_results, config
            )

    def _reciprocal_rank_fusion(
        self,
        dense_results: List[Tuple[TextNode, float]],
        sparse_results: List[Tuple[TextNode, float]],
        expanded_results: List[List[Tuple[TextNode, float]]],
        config: HybridRetrievalConfig
    ) -> List[RetrievalResult]:
        """Reciprocal Rank Fusion (RRF) reranking"""
        k = 60  # RRF constant
        scores = defaultdict(float)
        node_info = {}

        # Process dense results
        for rank, (node, score) in enumerate(dense_results, 1):
            node_id = node.node_id or node.id_
            scores[node_id] += config.dense_weight / (k + rank)
            if node_id not in node_info:
                node_info[node_id] = {
                    'node': node,
                    'methods': [],
                    'original_rank': rank
                }
            node_info[node_id]['methods'].append('dense')

        # Process sparse results
        for rank, (node, score) in enumerate(sparse_results, 1):
            node_id = node.node_id or node.id_
            scores[node_id] += config.sparse_weight / (k + rank)
            if node_id not in node_info:
                node_info[node_id] = {
                    'node': node,
                    'methods': [],
                    'original_rank': rank
                }
            node_info[node_id]['methods'].append('sparse')

        # Process expanded results
        for exp_results in expanded_results:
            for rank, (node, score) in enumerate(exp_results, 1):
                node_id = node.node_id or node.id_
                scores[node_id] += config.expanded_weight / (k + rank)
                if node_id not in node_info:
                    node_info[node_id] = {
                        'node': node,
                        'methods': [],
                        'original_rank': rank
                    }
                node_info[node_id]['methods'].append('expanded')

        # Sort by RRF score
        sorted_node_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

        # Create results
        results = []
        for idx, node_id in enumerate(sorted_node_ids):
            info = node_info[node_id]
            result = RetrievalResult(
                node=info['node'],
                score=scores[node_id],
                retrieval_method=','.join(set(info['methods'])),
                original_rank=info['original_rank'],
                metadata={'rrf_score': scores[node_id], 'methods': info['methods']}
            )
            results.append(result)

        return results

    def _score_based_ranking(
        self,
        dense_results: List[Tuple[TextNode, float]],
        sparse_results: List[Tuple[TextNode, float]],
        expanded_results: List[List[Tuple[TextNode, float]]],
        config: HybridRetrievalConfig
    ) -> List[RetrievalResult]:
        """Score-based ranking (normalize and combine scores)"""
        scores = defaultdict(float)
        node_info = {}

        # Normalize scores
        dense_max = max([s for _, s in dense_results], default=1.0)
        sparse_max = max([s for _, s in sparse_results], default=1.0)

        # Process dense results
        for rank, (node, score) in enumerate(dense_results, 1):
            node_id = node.node_id or node.id_
            normalized_score = (score / dense_max) if dense_max > 0 else 0
            scores[node_id] += config.dense_weight * normalized_score
            if node_id not in node_info:
                node_info[node_id] = {
                    'node': node,
                    'methods': [],
                    'original_rank': rank
                }
            node_info[node_id]['methods'].append('dense')

        # Process sparse results
        for rank, (node, score) in enumerate(sparse_results, 1):
            node_id = node.node_id or node.id_
            normalized_score = (score / sparse_max) if sparse_max > 0 else 0
            scores[node_id] += config.sparse_weight * normalized_score
            if node_id not in node_info:
                node_info[node_id] = {
                    'node': node,
                    'methods': [],
                    'original_rank': rank
                }
            node_info[node_id]['methods'].append('sparse')

        # Process expanded results
        for exp_results in expanded_results:
            exp_max = max([s for _, s in exp_results], default=1.0)
            for rank, (node, score) in enumerate(exp_results, 1):
                node_id = node.node_id or node.id_
                normalized_score = (score / exp_max) if exp_max > 0 else 0
                scores[node_id] += config.expanded_weight * normalized_score
                if node_id not in node_info:
                    node_info[node_id] = {
                        'node': node,
                        'methods': [],
                        'original_rank': rank
                    }
                node_info[node_id]['methods'].append('expanded')

        # Sort by combined score
        sorted_node_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

        # Create results
        results = []
        for idx, node_id in enumerate(sorted_node_ids):
            info = node_info[node_id]
            result = RetrievalResult(
                node=info['node'],
                score=scores[node_id],
                retrieval_method=','.join(set(info['methods'])),
                original_rank=info['original_rank'],
                metadata={'combined_score': scores[node_id], 'methods': info['methods']}
            )
            results.append(result)

        return results

    def _weighted_ranking(
        self,
        dense_results: List[Tuple[TextNode, float]],
        sparse_results: List[Tuple[TextNode, float]],
        expanded_results: List[List[Tuple[TextNode, float]]],
        config: HybridRetrievalConfig
    ) -> List[RetrievalResult]:
        """Weighted ranking based on position"""
        scores = defaultdict(float)
        node_info = {}

        # Process dense results - higher weight for top results
        for rank, (node, score) in enumerate(dense_results, 1):
            node_id = node.node_id or node.id_
            position_weight = 1.0 / rank
            scores[node_id] += config.dense_weight * position_weight
            if node_id not in node_info:
                node_info[node_id] = {
                    'node': node,
                    'methods': [],
                    'original_rank': rank
                }
            node_info[node_id]['methods'].append('dense')

        # Process sparse results
        for rank, (node, score) in enumerate(sparse_results, 1):
            node_id = node.node_id or node.id_
            position_weight = 1.0 / rank
            scores[node_id] += config.sparse_weight * position_weight
            if node_id not in node_info:
                node_info[node_id] = {
                    'node': node,
                    'methods': [],
                    'original_rank': rank
                }
            node_info[node_id]['methods'].append('sparse')

        # Process expanded results
        for exp_results in expanded_results:
            for rank, (node, score) in enumerate(exp_results, 1):
                node_id = node.node_id or node.id_
                position_weight = 1.0 / rank
                scores[node_id] += config.expanded_weight * position_weight
                if node_id not in node_info:
                    node_info[node_id] = {
                        'node': node,
                        'methods': [],
                        'original_rank': rank
                    }
                node_info[node_id]['methods'].append('expanded')

        # Sort by weighted score
        sorted_node_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

        # Create results
        results = []
        for idx, node_id in enumerate(sorted_node_ids):
            info = node_info[node_id]
            result = RetrievalResult(
                node=info['node'],
                score=scores[node_id],
                retrieval_method=','.join(set(info['methods'])),
                original_rank=info['original_rank'],
                metadata={'weighted_score': scores[node_id], 'methods': info['methods']}
            )
            results.append(result)

        return results


class HybridRAGPipeline:
    """
    Hybrid RAG Pipeline that combines dense and sparse retrieval with query expansion and reranking
    """

    def __init__(
        self,
        index_manager: Optional[UnifiedIndexManager] = None,
        config: Optional[HybridRetrievalConfig] = None,
        project_id: Optional[str] = None
    ):
        """
        Initialize Hybrid RAG Pipeline

        Args:
            index_manager: UnifiedIndexManager instance (uses global if not provided)
            config: HybridRetrievalConfig (uses defaults if not provided)
            project_id: Project ID for retrieving index
        """
        self.index_manager = index_manager
        self.config = config or HybridRetrievalConfig()
        self.project_id = project_id

        # Initialize components
        self.bm25_index = None
        self.query_expander = QueryExpander()
        self.reranker = HybridReranker(method=self.config.rerank_method)

        # Statistics
        self.stats = {
            'total_queries': 0,
            'dense_retrievals': 0,
            'sparse_retrievals': 0,
            'expanded_queries': 0,
            'reranked_results': 0
        }

        logger.info("Initialized HybridRAGPipeline")

    def build_bm25_index(self, force_rebuild: bool = False) -> None:
        """Build BM25 index from vector store documents"""
        if self.bm25_index is not None and not force_rebuild:
            logger.info("BM25 index already built")
            return

        # Get index from manager
        if self.index_manager is None:
            from backend.utils.unified_index_manager import get_global_index_manager
            self.index_manager = get_global_index_manager()

        index, _ = self.index_manager.get_index(project_id=self.project_id)

        # Get all nodes from index
        nodes = []
        if hasattr(index, 'docstore') and hasattr(index.docstore, 'docs'):
            for doc_id, doc in index.docstore.docs.items():
                if hasattr(doc, 'get_content'):
                    # Create TextNode from document
                    node = TextNode(
                        text=doc.get_content(),
                        id_=doc_id,
                        metadata=doc.metadata if hasattr(doc, 'metadata') else {}
                    )
                    nodes.append(node)

        # Build BM25 index
        self.bm25_index = BM25Index(k1=self.config.bm25_k1, b=self.config.bm25_b)
        self.bm25_index.build_index(nodes)

        logger.info(f"Built BM25 index with {len(nodes)} nodes")

    def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None
    ) -> List[RetrievalResult]:
        """
        Retrieve documents using hybrid approach

        Args:
            query: Search query
            top_k: Number of results to return (uses config if not specified)

        Returns:
            List of RetrievalResult objects
        """
        k = top_k or self.config.final_top_k
        self.stats['total_queries'] += 1

        # Ensure BM25 index is built
        if self.bm25_index is None:
            self.build_bm25_index()

        # 1. Dense retrieval (semantic/vector search)
        dense_results = self._dense_retrieval(query, self.config.dense_top_k)
        self.stats['dense_retrievals'] += 1

        # 2. Sparse retrieval (BM25/keyword search)
        sparse_results = self._sparse_retrieval(query, self.config.sparse_top_k)
        self.stats['sparse_retrievals'] += 1

        # 3. Query expansion
        expanded_results = []
        if self.config.enable_query_expansion:
            expanded_results = self._expanded_retrieval(query, self.config.expanded_top_k)
            self.stats['expanded_queries'] += len(expanded_results)

        # 4. Combine and rerank
        if self.config.enable_reranking:
            reranked = self.reranker.rerank(
                dense_results, sparse_results, expanded_results, self.config
            )
            self.stats['reranked_results'] += 1
        else:
            # Simple concatenation without reranking
            reranked = self._simple_combine(dense_results, sparse_results, expanded_results)

        # 5. Deduplication
        if self.config.enable_deduplication:
            reranked = self._deduplicate(reranked)

        # 6. Return top-k
        return reranked[:k]

    def _dense_retrieval(self, query: str, top_k: int) -> List[Tuple[TextNode, float]]:
        """Dense (vector/semantic) retrieval"""
        if self.index_manager is None:
            from backend.utils.unified_index_manager import get_global_index_manager
            self.index_manager = get_global_index_manager()

        # Get retriever from index manager
        retriever = self.index_manager.get_retriever(
            project_id=self.project_id,
            similarity_top_k=top_k
        )

        # Retrieve nodes
        nodes_with_scores = retriever.retrieve(query)

        # Convert to tuple format
        results = []
        for node_with_score in nodes_with_scores:
            results.append((node_with_score.node, node_with_score.score or 0.0))

        logger.debug(f"Dense retrieval found {len(results)} results")
        return results

    def _sparse_retrieval(self, query: str, top_k: int) -> List[Tuple[TextNode, float]]:
        """Sparse (BM25/keyword) retrieval"""
        if self.bm25_index is None:
            logger.warning("BM25 index not built, returning empty results")
            return []

        results = self.bm25_index.search(query, top_k=top_k)
        logger.debug(f"Sparse retrieval found {len(results)} results")
        return results

    def _expanded_retrieval(self, query: str, top_k: int) -> List[List[Tuple[TextNode, float]]]:
        """Query expansion and retrieval"""
        # Expand query
        expanded_queries = self.query_expander.expand_query(
            query, max_expansions=self.config.max_expanded_queries
        )

        # Skip original query (already retrieved in dense)
        expanded_queries = [q for q in expanded_queries if q != query]

        # Retrieve for each expanded query
        expanded_results = []
        for exp_query in expanded_queries:
            results = self._dense_retrieval(exp_query, top_k)
            if results:
                expanded_results.append(results)

        logger.debug(f"Expanded retrieval with {len(expanded_queries)} queries")
        return expanded_results

    def _simple_combine(
        self,
        dense_results: List[Tuple[TextNode, float]],
        sparse_results: List[Tuple[TextNode, float]],
        expanded_results: List[List[Tuple[TextNode, float]]]
    ) -> List[RetrievalResult]:
        """Simple combination without reranking"""
        results = []
        seen_ids = set()

        # Add dense results
        for rank, (node, score) in enumerate(dense_results, 1):
            node_id = node.node_id or node.id_
            if node_id not in seen_ids:
                results.append(RetrievalResult(
                    node=node,
                    score=score,
                    retrieval_method='dense',
                    original_rank=rank
                ))
                seen_ids.add(node_id)

        # Add sparse results
        for rank, (node, score) in enumerate(sparse_results, 1):
            node_id = node.node_id or node.id_
            if node_id not in seen_ids:
                results.append(RetrievalResult(
                    node=node,
                    score=score,
                    retrieval_method='sparse',
                    original_rank=rank
                ))
                seen_ids.add(node_id)

        # Add expanded results
        for exp_results in expanded_results:
            for rank, (node, score) in enumerate(exp_results, 1):
                node_id = node.node_id or node.id_
                if node_id not in seen_ids:
                    results.append(RetrievalResult(
                        node=node,
                        score=score,
                        retrieval_method='expanded',
                        original_rank=rank
                    ))
                    seen_ids.add(node_id)

        return results

    def _deduplicate(self, results: List[RetrievalResult]) -> List[RetrievalResult]:
        """Remove duplicate or very similar results"""
        if not results:
            return results

        unique_results = []
        seen_ids = set()
        seen_texts = {}

        for result in results:
            node_id = result.node.node_id or result.node.id_

            # Check for exact duplicate by ID
            if node_id in seen_ids:
                continue

            # Check for text similarity
            text = result.node.get_content()
            text_hash = hash(text)

            # Simple deduplication - could be enhanced with semantic similarity
            if text_hash in seen_texts:
                continue

            unique_results.append(result)
            seen_ids.add(node_id)
            seen_texts[text_hash] = True

        logger.debug(f"Deduplication: {len(results)} -> {len(unique_results)} results")
        return unique_results

    def get_stats(self) -> Dict[str, Any]:
        """Get pipeline statistics"""
        return {
            **self.stats,
            'timestamp': datetime.now().isoformat(),
            'config': {
                'dense_top_k': self.config.dense_top_k,
                'sparse_top_k': self.config.sparse_top_k,
                'final_top_k': self.config.final_top_k,
                'rerank_method': self.config.rerank_method,
                'query_expansion_enabled': self.config.enable_query_expansion,
                'reranking_enabled': self.config.enable_reranking
            }
        }

    def reset_stats(self) -> None:
        """Reset statistics"""
        self.stats = {
            'total_queries': 0,
            'dense_retrievals': 0,
            'sparse_retrievals': 0,
            'expanded_queries': 0,
            'reranked_results': 0
        }


# Convenience function
def create_hybrid_pipeline(
    project_id: Optional[str] = None,
    config: Optional[HybridRetrievalConfig] = None
) -> HybridRAGPipeline:
    """
    Create a HybridRAGPipeline instance

    Args:
        project_id: Project ID for retrieving index
        config: Configuration (uses defaults if not provided)

    Returns:
        HybridRAGPipeline instance
    """
    return HybridRAGPipeline(project_id=project_id, config=config)
