# backend/api/rag_debug_api.py
# RAG Debugging and Performance Monitoring API
# Provides detailed insights into retrieval performance, context quality, and system metrics

import logging
import time
import json
from functools import wraps
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone, timedelta
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from flask import Blueprint, request, jsonify, current_app

from backend.utils.settings_utils import get_setting

from backend.utils.unified_index_manager import get_global_index_manager
from backend.utils.enhanced_rag_chunking import EnhancedRAGChunker
from backend.utils.context_manager import ContextManager
from backend.utils.response_utils import success_response, error_response
from backend.models import db, LLMMessage, LLMSession

# LlamaIndex imports
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.query_engine import BaseQueryEngine

logger = logging.getLogger(__name__)

rag_debug_bp = Blueprint("rag_debug", __name__, url_prefix="/api/rag-debug")

@dataclass
class RetrievalMetrics:
    """Metrics for a single retrieval operation"""
    query: str
    retrieved_nodes: int
    retrieval_time: float
    avg_similarity_score: float
    top_score: float
    bottom_score: float
    content_types: Dict[str, int]
    sources: List[str]
    timestamp: datetime

@dataclass
class ContextQualityMetrics:
    """Metrics for context quality assessment"""
    session_id: str
    total_chunks: int
    unique_sources: int
    avg_chunk_length: int
    compression_ratio: float
    redundancy_score: float
    relevance_score: float
    freshness_score: float
    timestamp: datetime

class RAGDebugManager:
    """Manages RAG debugging and performance monitoring"""
    
    def __init__(self):
        self.index_manager = get_global_index_manager() if get_global_index_manager else None
        self.rag_chunker = EnhancedRAGChunker() if EnhancedRAGChunker else None
        
        # Performance tracking
        self.retrieval_metrics = deque(maxlen=1000)  # Keep last 1000 retrievals
        self.context_quality_metrics = deque(maxlen=500)  # Keep last 500 assessments
        
        # System metrics
        self.system_metrics = {
            'total_queries': 0,
            'total_retrievals': 0,
            'avg_retrieval_time': 0.0,
            'cache_hit_rate': 0.0,
            'error_rate': 0.0,
            'last_reset': datetime.now()
        }
        
        # Query performance tracking
        self.query_performance = defaultdict(list)
        
        # Error tracking
        self.error_log = deque(maxlen=100)
    
    def _calculate_similarity_stats(self, nodes: List[Any]) -> Dict[str, float]:
        """Calculate similarity statistics for retrieved nodes"""
        if not nodes:
            return {'avg': 0.0, 'top': 0.0, 'bottom': 0.0}
        
        scores = [getattr(node, 'score', 0.0) for node in nodes]
        return {
            'avg': sum(scores) / len(scores),
            'top': max(scores),
            'bottom': min(scores)
        }
    
    def _analyze_content_types(self, nodes: List[Any]) -> Dict[str, int]:
        """Analyze content types in retrieved nodes"""
        content_types = defaultdict(int)
        
        for node in nodes:
            metadata = getattr(node, 'metadata', {})
            content_type = metadata.get('content_type', 'unknown')
            content_types[content_type] += 1
        
        return dict(content_types)
    
    def _extract_sources(self, nodes: List[Any]) -> List[str]:
        """Extract unique sources from retrieved nodes"""
        sources = set()
        
        for node in nodes:
            metadata = getattr(node, 'metadata', {})
            source = metadata.get('source_document', 'unknown')
            sources.add(source)
        
        return list(sources)
    
    def _calculate_redundancy_score(self, chunks: List[str]) -> float:
        """Calculate redundancy score for context chunks"""
        if not chunks or len(chunks) < 2:
            return 0.0
        
        # Simple redundancy calculation based on word overlap
        total_comparisons = 0
        total_overlap = 0
        
        for i in range(len(chunks)):
            for j in range(i + 1, len(chunks)):
                words_i = set(chunks[i].lower().split())
                words_j = set(chunks[j].lower().split())
                
                if words_i and words_j:
                    overlap = len(words_i & words_j) / len(words_i | words_j)
                    total_overlap += overlap
                    total_comparisons += 1
        
        return total_overlap / total_comparisons if total_comparisons > 0 else 0.0
    
    def _calculate_relevance_score(self, query: str, chunks: List[str]) -> float:
        """Calculate relevance score between query and context chunks"""
        if not chunks:
            return 0.0
        
        query_words = set(query.lower().split())
        relevance_scores = []
        
        for chunk in chunks:
            chunk_words = set(chunk.lower().split())
            if chunk_words:
                relevance = len(query_words & chunk_words) / len(query_words | chunk_words)
                relevance_scores.append(relevance)
        
        return sum(relevance_scores) / len(relevance_scores) if relevance_scores else 0.0
    
    def _calculate_freshness_score(self, nodes: List[Any]) -> float:
        """Calculate freshness score based on node timestamps"""
        if not nodes:
            return 0.0
        
        now = datetime.now()
        freshness_scores = []
        
        for node in nodes:
            metadata = getattr(node, 'metadata', {})
            created_at = metadata.get('created_at')
            
            if created_at:
                try:
                    if isinstance(created_at, str):
                        created_time = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    else:
                        created_time = created_at
                    
                    age_days = (now - created_time).days
                    # Freshness decreases over time, max 30 days
                    freshness = max(0, 1 - age_days / 30)
                    freshness_scores.append(freshness)
                except Exception:
                    freshness_scores.append(0.5)  # Default score
            else:
                freshness_scores.append(0.5)  # Default score
        
        return sum(freshness_scores) / len(freshness_scores) if freshness_scores else 0.5
    
    def debug_retrieval(self, query: str, project_id: Optional[str] = None, 
                       top_k: int = 5) -> Dict[str, Any]:
        """Debug a single retrieval operation"""
        try:
            start_time = time.time()
            
            # Check if index manager is available
            if not self.index_manager:
                return {
                    'error': 'Index manager not available',
                    'metrics': {
                        'query': query,
                        'retrieved_nodes': 0,
                        'retrieval_time': 0.0,
                        'avg_similarity_score': 0.0,
                        'top_score': 0.0,
                        'bottom_score': 0.0,
                        'content_types': {},
                        'sources': [],
                        'timestamp': datetime.now().isoformat()
                    },
                    'detailed_nodes': [],
                    'query_analysis': {
                        'query_length': len(query),
                        'query_words': len(query.split()),
                        'query_complexity': self._assess_query_complexity(query)
                    }
                }
            
            # Get retriever
            try:
                retriever = self.index_manager.get_retriever(
                    project_id=project_id,
                    similarity_top_k=top_k
                )
            except Exception as e:
                logger.warning(f"Failed to get retriever: {e}")
                return {
                    'error': f'Failed to get retriever: {str(e)}',
                    'metrics': {
                        'query': query,
                        'retrieved_nodes': 0,
                        'retrieval_time': 0.0,
                        'avg_similarity_score': 0.0,
                        'top_score': 0.0,
                        'bottom_score': 0.0,
                        'content_types': {},
                        'sources': [],
                        'timestamp': datetime.now().isoformat()
                    },
                    'detailed_nodes': [],
                    'query_analysis': {
                        'query_length': len(query),
                        'query_words': len(query.split()),
                        'query_complexity': self._assess_query_complexity(query)
                    }
                }
            
            # Perform retrieval with error handling for LlamaIndex AssertionError
            try:
                nodes = retriever.retrieve(query)
                # Add validation before LlamaIndex processes results
                if not nodes or len(nodes) == 0:
                    logger.warning(f"Retriever returned empty nodes for query: {query}")
                    nodes = []  # Ensure we have a list, not None
            except AssertionError as e:
                logger.error(f"BUG FIX 2: LlamaIndex AssertionError caught during retrieval: {e}")
                nodes = []  # Fallback to empty results
                self.error_log.append({
                    'error': f'Vector store assertion failed - index may need rebuild: {str(e)}',
                    'query': query,
                    'timestamp': datetime.now().isoformat(),
                    'error_type': 'assertion_error'
                })
                
                # Try to recover by rebuilding index
                try:
                    logger.info("BUG FIX 2: Attempting index recovery...")
                    from backend.services.indexing_service import get_or_create_index
                    get_or_create_index(project_id)
                    logger.info("BUG FIX 2: Index recovery completed")
                except Exception as recovery_error:
                    logger.error(f"BUG FIX 2: Index recovery failed: {recovery_error}")
                
                # Return error result immediately
                return {
                    'error': f'Vector store retrieval failed: {str(e)}. Index may need to be rebuilt.',
                    'metrics': {
                        'query': query,
                        'retrieved_nodes': 0,
                        'retrieval_time': time.time() - start_time,
                        'avg_similarity_score': 0.0,
                        'top_score': 0.0,
                        'bottom_score': 0.0,
                        'content_types': {},
                        'sources': [],
                        'timestamp': datetime.now().isoformat(),
                        'error_type': 'vector_store_assertion'
                    },
                    'detailed_nodes': [],
                    'query_analysis': {
                        'query_length': len(query),
                        'query_words': len(query.split()),
                        'query_complexity': self._assess_query_complexity(query)
                    }
                }
            
            retrieval_time = time.time() - start_time
            
            # Calculate metrics
            similarity_stats = self._calculate_similarity_stats(nodes)
            content_types = self._analyze_content_types(nodes)
            sources = self._extract_sources(nodes)
            
            # Create metrics object
            metrics = RetrievalMetrics(
                query=query,
                retrieved_nodes=len(nodes),
                retrieval_time=retrieval_time,
                avg_similarity_score=similarity_stats['avg'],
                top_score=similarity_stats['top'],
                bottom_score=similarity_stats['bottom'],
                content_types=content_types,
                sources=sources,
                timestamp=datetime.now()
            )
            
            # Store metrics
            self.retrieval_metrics.append(metrics)
            
            # Update system metrics
            self.system_metrics['total_retrievals'] += 1
            self.system_metrics['avg_retrieval_time'] = (
                (self.system_metrics['avg_retrieval_time'] * (self.system_metrics['total_retrievals'] - 1) + 
                 retrieval_time) / self.system_metrics['total_retrievals']
            )
            
            # Prepare detailed response
            detailed_nodes = []
            for i, node in enumerate(nodes):
                node_info = {
                    'rank': i + 1,
                    'score': getattr(node, 'score', 0.0),
                    'content_preview': node.get_content()[:200] + "..." if len(node.get_content()) > 200 else node.get_content(),
                    'content_length': len(node.get_content()),
                    'metadata': getattr(node, 'metadata', {}),
                    'node_id': getattr(node, 'node_id', f'node_{i}')
                }
                detailed_nodes.append(node_info)
            
            return {
                'metrics': asdict(metrics),
                'detailed_nodes': detailed_nodes,
                'query_analysis': {
                    'query_length': len(query),
                    'query_words': len(query.split()),
                    'query_complexity': self._assess_query_complexity(query)
                }
            }
            
        except Exception as e:
            error_msg = str(e) if str(e) else f"Unknown error of type {type(e).__name__}"
            logger.error(f"Error in debug_retrieval: {error_msg}", exc_info=True)
            self.error_log.append({
                'error': error_msg,
                'query': query,
                'timestamp': datetime.now().isoformat()
            })
            return {
                'error': f'Retrieval failed: {error_msg}',
                'metrics': {
                    'query': query,
                    'retrieved_nodes': 0,
                    'retrieval_time': 0.0,
                    'avg_similarity_score': 0.0,
                    'top_score': 0.0,
                    'bottom_score': 0.0,
                    'content_types': {},
                    'sources': [],
                    'timestamp': datetime.now().isoformat()
                },
                'detailed_nodes': [],
                'query_analysis': {
                    'query_length': len(query),
                    'query_words': len(query.split()),
                    'query_complexity': self._assess_query_complexity(query)
                }
            }
    
    def _assess_query_complexity(self, query: str) -> str:
        """Assess query complexity"""
        word_count = len(query.split())
        
        if word_count <= 3:
            return 'simple'
        elif word_count <= 10:
            return 'medium'
        else:
            return 'complex'
    
    def analyze_context_quality(self, session_id: str, context_chunks: List[str], 
                               query: str) -> Dict[str, Any]:
        """Analyze the quality of context for a session"""
        try:
            # Calculate various quality metrics
            unique_sources = len(set(chunk.split('\n')[0] for chunk in context_chunks if chunk))
            avg_chunk_length = sum(len(chunk) for chunk in context_chunks) / len(context_chunks) if context_chunks else 0
            
            redundancy_score = self._calculate_redundancy_score(context_chunks)
            relevance_score = self._calculate_relevance_score(query, context_chunks)
            
            # For freshness, we need to get the actual nodes (simplified here)
            freshness_score = 0.7  # Default score
            
            # Compression ratio (simplified)
            total_length = sum(len(chunk) for chunk in context_chunks)
            compression_ratio = min(total_length / 10000, 1.0)  # Normalize to 10k chars
            
            # Create metrics object
            metrics = ContextQualityMetrics(
                session_id=session_id,
                total_chunks=len(context_chunks),
                unique_sources=unique_sources,
                avg_chunk_length=int(avg_chunk_length),
                compression_ratio=compression_ratio,
                redundancy_score=redundancy_score,
                relevance_score=relevance_score,
                freshness_score=freshness_score,
                timestamp=datetime.now()
            )
            
            # Store metrics
            self.context_quality_metrics.append(metrics)
            
            # Calculate overall quality score
            overall_score = (
                (1 - redundancy_score) * 0.3 +  # Lower redundancy is better
                relevance_score * 0.4 +         # Higher relevance is better
                freshness_score * 0.2 +         # Fresher is better
                min(unique_sources / 5, 1.0) * 0.1  # Diversity is good
            )
            
            return {
                'metrics': asdict(metrics),
                'overall_quality_score': overall_score,
                'quality_assessment': self._assess_quality_level(overall_score),
                'recommendations': self._generate_quality_recommendations(metrics)
            }
            
        except Exception as e:
            logger.error(f"Error in analyze_context_quality: {e}")
            raise
    
    def _assess_quality_level(self, score: float) -> str:
        """Assess quality level based on score"""
        if score >= 0.8:
            return 'excellent'
        elif score >= 0.6:
            return 'good'
        elif score >= 0.4:
            return 'fair'
        else:
            return 'poor'
    
    def _generate_quality_recommendations(self, metrics: ContextQualityMetrics) -> List[str]:
        """Generate recommendations based on quality metrics"""
        recommendations = []
        
        if metrics.redundancy_score > 0.7:
            recommendations.append("High redundancy detected. Consider improving chunk diversity.")
        
        if metrics.relevance_score < 0.3:
            recommendations.append("Low relevance score. Consider refining search query or improving indexing.")
        
        if metrics.unique_sources < 2:
            recommendations.append("Limited source diversity. Consider expanding knowledge base.")
        
        if metrics.avg_chunk_length < 100:
            recommendations.append("Chunks may be too short. Consider adjusting chunking strategy.")
        
        if metrics.freshness_score < 0.3:
            recommendations.append("Context may be outdated. Consider refreshing knowledge base.")
        
        return recommendations
    
    def get_performance_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get performance summary for the last N hours"""
        try:
            cutoff_time = datetime.now() - timedelta(hours=hours)
            
            # Filter recent metrics
            recent_retrievals = [m for m in self.retrieval_metrics if m.timestamp >= cutoff_time]
            recent_context_quality = [m for m in self.context_quality_metrics if m.timestamp >= cutoff_time]
            
            # Calculate summary statistics
            summary = {
                'time_period': f'Last {hours} hours',
                'retrieval_stats': {
                    'total_retrievals': len(recent_retrievals),
                    'avg_retrieval_time': sum(m.retrieval_time for m in recent_retrievals) / len(recent_retrievals) if recent_retrievals else 0,
                    'avg_nodes_retrieved': sum(m.retrieved_nodes for m in recent_retrievals) / len(recent_retrievals) if recent_retrievals else 0,
                    'avg_similarity_score': sum(m.avg_similarity_score for m in recent_retrievals) / len(recent_retrievals) if recent_retrievals else 0
                },
                'context_quality_stats': {
                    'total_assessments': len(recent_context_quality),
                    'avg_redundancy_score': sum(m.redundancy_score for m in recent_context_quality) / len(recent_context_quality) if recent_context_quality else 0,
                    'avg_relevance_score': sum(m.relevance_score for m in recent_context_quality) / len(recent_context_quality) if recent_context_quality else 0,
                    'avg_freshness_score': sum(m.freshness_score for m in recent_context_quality) / len(recent_context_quality) if recent_context_quality else 0
                },
                'system_metrics': self.system_metrics,
                'index_stats': self.index_manager.get_cache_stats(),
                'recent_errors': list(self.error_log)[-10:]  # Last 10 errors
            }
            
            return summary
            
        except Exception as e:
            logger.error(f"Error in get_performance_summary: {e}")
            raise
    
    def get_query_patterns(self) -> Dict[str, Any]:
        """Analyze query patterns and performance"""
        try:
            # Group queries by similarity
            query_patterns = defaultdict(list)
            
            for metrics in self.retrieval_metrics:
                # Simple pattern grouping by first word
                first_word = metrics.query.split()[0].lower() if metrics.query.split() else 'unknown'
                query_patterns[first_word].append(metrics)
            
            # Calculate pattern statistics
            pattern_stats = {}
            for pattern, queries in query_patterns.items():
                pattern_stats[pattern] = {
                    'count': len(queries),
                    'avg_retrieval_time': sum(q.retrieval_time for q in queries) / len(queries),
                    'avg_nodes_retrieved': sum(q.retrieved_nodes for q in queries) / len(queries),
                    'avg_similarity_score': sum(q.avg_similarity_score for q in queries) / len(queries)
                }
            
            return {
                'total_patterns': len(pattern_stats),
                'pattern_stats': pattern_stats,
                'most_common_patterns': sorted(pattern_stats.items(), key=lambda x: x[1]['count'], reverse=True)[:10]
            }
            
        except Exception as e:
            logger.error(f"Error in get_query_patterns: {e}")
            raise

# Global debug manager
debug_manager = RAGDebugManager()


def require_rag_debug(f):
    """Gate endpoint behind rag_debug_enabled setting."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not get_setting("rag_debug_enabled", default=True, cast=bool):
            return jsonify({"error": "RAG Debug is disabled. Enable it in Settings."}), 403
        return f(*args, **kwargs)
    return wrapper


@rag_debug_bp.route("/retrieve", methods=["POST"])
@require_rag_debug
def debug_retrieve():
    """Debug a retrieval operation"""
    try:
        logger.info("RAG debug retrieve endpoint called")
        
        if not request.is_json:
            logger.warning("Request is not JSON")
            return error_response("Request must be JSON", 400)
        
        data = request.get_json()
        logger.info(f"Request data: {data}")
        
        query = data.get('query')
        project_id = data.get('project_id')
        top_k = data.get('top_k', 5)
        
        if not query:
            logger.warning("Missing query parameter")
            return error_response("Missing query parameter", 400)
        
        logger.info(f"Calling debug_manager.debug_retrieval with query: {query}")
        
        # Check if debug_manager is properly initialized
        if not debug_manager:
            logger.error("Debug manager is not initialized")
            return error_response("Debug manager not available", 500)
        
        # Check if index manager is available
        if not debug_manager.index_manager:
            logger.error("Index manager is not available in debug manager")
            return error_response("Index manager not available", 500)
        
        result = debug_manager.debug_retrieval(query, project_id, top_k)
        logger.info(f"debug_manager.debug_retrieval returned: {result}")
        
        # Check if the result contains an error
        if 'error' in result:
            logger.warning(f"RAG debug retrieve returned error: {result['error']}")
            return success_response(result)  # Return the error as part of the response
        
        return success_response(result)
        
    except Exception as e:
        logger.error(f"Debug retrieve error: {e}", exc_info=True)
        return error_response(f"RAG debug retrieve failed: {str(e)}", 500)

@rag_debug_bp.route("/context-quality", methods=["POST"])
@require_rag_debug
def analyze_context():
    """Analyze context quality for a session"""
    try:
        if not request.is_json:
            return error_response("Request must be JSON", 400)
        
        data = request.get_json()
        session_id = data.get('session_id')
        context_chunks = data.get('context_chunks', [])
        query = data.get('query', '')
        
        if not session_id:
            return error_response("Missing session_id parameter", 400)
        
        result = debug_manager.analyze_context_quality(session_id, context_chunks, query)
        return success_response(result)
        
    except Exception as e:
        logger.error(f"Context quality analysis error: {e}")
        return error_response(str(e), 500)

@rag_debug_bp.route("/performance", methods=["GET"])
@require_rag_debug
def get_performance():
    """Get performance summary"""
    try:
        hours = request.args.get('hours', 24, type=int)
        result = debug_manager.get_performance_summary(hours)
        return success_response(result)
        
    except Exception as e:
        logger.error(f"Performance summary error: {e}")
        return error_response(str(e), 500)

@rag_debug_bp.route("/query-patterns", methods=["GET"])
@require_rag_debug
def get_patterns():
    """Get query patterns analysis"""
    try:
        result = debug_manager.get_query_patterns()
        return success_response(result)
        
    except Exception as e:
        logger.error(f"Query patterns error: {e}")
        return error_response(str(e), 500)

@rag_debug_bp.route("/system-health", methods=["GET"])
@require_rag_debug
def get_system_health():
    """Get overall system health metrics"""
    try:
        # Get index stats from persisted index files on disk + in-memory INDEX_CACHE
        import os
        from backend.config import INDEX_ROOT

        # 1. Check persisted index files on disk (always accurate)
        persisted_indexes = {}
        total_memory = 0
        index_root = str(INDEX_ROOT)

        # Check for global index (docstore.json in INDEX_ROOT)
        docstore_path = os.path.join(index_root, "docstore.json")
        if os.path.exists(docstore_path):
            docstore_size = os.path.getsize(docstore_path)
            vector_store_path = os.path.join(index_root, "default__vector_store.json")
            vector_size = os.path.getsize(vector_store_path) if os.path.exists(vector_store_path) else 0
            total_memory = docstore_size + vector_size
            persisted_indexes["global"] = {"project_id": "global", "size": docstore_size + vector_size}

        # Check for per-project indexes (subdirectories with docstore.json)
        for entry in os.listdir(index_root):
            entry_path = os.path.join(index_root, entry)
            if os.path.isdir(entry_path):
                project_docstore = os.path.join(entry_path, "docstore.json")
                if os.path.exists(project_docstore):
                    size = os.path.getsize(project_docstore)
                    project_vector = os.path.join(entry_path, "default__vector_store.json")
                    if os.path.exists(project_vector):
                        size += os.path.getsize(project_vector)
                    total_memory += size
                    persisted_indexes[entry] = {"project_id": entry, "size": size}

        # 2. Also check in-memory INDEX_CACHE for live cached count
        flask_cache = current_app.config.get("INDEX_CACHE", {})
        cached_count = max(len(persisted_indexes), len(flask_cache))

        # Merge with UnifiedIndexManager stats for access counters
        uim_stats = debug_manager.index_manager.get_cache_stats()
        index_stats = {
            'cached_indexes': persisted_indexes,
            'total_cached': cached_count,
            'total_memory_usage': total_memory,
            'access_stats': uim_stats.get('access_stats', {
                'total_loads': cached_count,
                'cache_hits': 0,
                'cache_misses': 0,
                'index_creates': cached_count,
                'errors': 0
            })
        }
        chunker_stats = debug_manager.rag_chunker.get_chunking_stats()
        
        # Recent error rate
        recent_errors = len([e for e in debug_manager.error_log 
                           if datetime.fromisoformat(e['timestamp'].replace('Z', '+00:00')) >= 
                           datetime.now() - timedelta(hours=1)])
        
        health_score = 1.0
        health_issues = []
        
        # Check various health indicators
        if recent_errors > 10:
            health_score -= 0.3
            health_issues.append("High error rate in the last hour")
        
        if index_stats['access_stats']['errors'] > index_stats['access_stats']['total_loads'] * 0.1:
            health_score -= 0.2
            health_issues.append("High index error rate")
        
        if chunker_stats['chunking_errors'] > chunker_stats['total_documents'] * 0.05:
            health_score -= 0.2
            health_issues.append("High chunking error rate")
        
        health_level = 'excellent' if health_score >= 0.9 else 'good' if health_score >= 0.7 else 'fair' if health_score >= 0.5 else 'poor'
        
        return success_response({
            'health_score': health_score,
            'health_level': health_level,
            'health_issues': health_issues,
            'system_metrics': debug_manager.system_metrics,
            'index_stats': index_stats,
            'chunker_stats': chunker_stats,
            'recent_errors': recent_errors,
            'uptime': (datetime.now() - debug_manager.system_metrics['last_reset']).total_seconds()
        })
        
    except Exception as e:
        logger.error(f"System health error: {e}")
        return error_response(str(e), 500) 