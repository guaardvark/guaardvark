#!/usr/bin/env python3
"""
Query Cache for Enhanced Chat API
Provides specialized caching for expensive chat operations to reduce response time from 3-5s to <2s.

Uses the existing CacheManager infrastructure with query-specific optimizations:
- Intent classification caching (1 hour TTL)
- File metadata caching (15 min TTL)
- File scoring caching (30 min TTL)
- Web search results caching (2 hour TTL)
- Pattern matching caching (30 min TTL)
"""

import re
import hashlib
import logging
from typing import Any, Optional, Dict, List, Tuple
from backend.utils.cache_manager import cache_manager, cached, invalidate_cache_pattern

logger = logging.getLogger(__name__)


class QueryCache:
    """
    Multi-tiered cache for chat API operations.

    Leverages existing CacheManager infrastructure to cache expensive operations
    in the enhanced_chat_api.py workflow.
    """

    def __init__(self):
        """Initialize query cache using global cache manager."""
        self.cache_manager = cache_manager
        logger.info("QueryCache initialized")

    # ========== Helper Methods ==========

    @staticmethod
    def normalize_query(query: str) -> str:
        """
        Normalize query for consistent cache keys.

        Args:
            query: Raw user query string

        Returns:
            Normalized query (lowercase, trimmed, punctuation removed)
        """
        # Convert to lowercase and trim
        normalized = query.lower().strip()

        # Remove punctuation but keep spaces
        normalized = re.sub(r'[^\w\s]', '', normalized)

        # Collapse multiple spaces
        normalized = re.sub(r'\s+', ' ', normalized)

        return normalized

    @staticmethod
    def hash_query(query: str) -> str:
        """
        Generate MD5 hash of normalized query for cache key.

        Args:
            query: Query string (will be normalized)

        Returns:
            MD5 hash hex string
        """
        normalized = QueryCache.normalize_query(query)
        return hashlib.md5(normalized.encode('utf-8')).hexdigest()

    # ========== Intent Caching ==========

    def get_cached_intent(self, query: str) -> Optional[str]:
        """
        Get cached intent classification result.

        Args:
            query: User query

        Returns:
            Cached intent string or None if not cached
        """
        query_hash = self.hash_query(query)
        cache_key = f"intent:{query_hash}"
        return self.cache_manager.get(cache_key, cache_type='query')

    def set_cached_intent(self, query: str, intent: str, ttl: int = 3600) -> None:
        """
        Cache intent classification result.

        Args:
            query: User query
            intent: Detected intent
            ttl: Time to live in seconds (default: 1 hour)
        """
        query_hash = self.hash_query(query)
        cache_key = f"intent:{query_hash}"
        self.cache_manager.set(cache_key, intent, ttl=ttl, cache_type='query')
        logger.debug(f"Cached intent for query hash {query_hash[:8]}: {intent}")

    # ========== File Metadata Caching ==========

    def get_cached_file_metadata(self) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached file metadata list.

        Returns:
            List of file metadata dicts or None if not cached
        """
        cache_key = "file_metadata:all"
        return self.cache_manager.get(cache_key, cache_type='query')

    def set_cached_file_metadata(self, metadata: List[Dict[str, Any]], ttl: int = 900) -> None:
        """
        Cache file metadata list.

        Args:
            metadata: List of file metadata dicts
            ttl: Time to live in seconds (default: 15 minutes)
        """
        cache_key = "file_metadata:all"
        self.cache_manager.set(cache_key, metadata, ttl=ttl, cache_type='query')
        logger.debug(f"Cached metadata for {len(metadata)} files")

    def invalidate_file_metadata(self) -> None:
        """Invalidate file metadata cache (call after file upload/deletion)."""
        invalidated = invalidate_cache_pattern("file_metadata:", cache_type='query')
        invalidated += invalidate_cache_pattern("file_scores:", cache_type='query')
        logger.info(f"Invalidated {invalidated} file-related cache entries")

    # ========== File Scoring Caching ==========

    def get_cached_file_scores(self, query: str, file_ids: List[int]) -> Optional[Dict[int, float]]:
        """
        Get cached file matching scores.

        Args:
            query: User query
            file_ids: List of file IDs to score

        Returns:
            Dict mapping file_id -> score or None if not cached
        """
        query_hash = self.hash_query(query)
        # Include file_ids in key for invalidation
        file_ids_hash = hashlib.md5(str(sorted(file_ids)).encode()).hexdigest()[:8]
        cache_key = f"file_scores:{query_hash}:{file_ids_hash}"
        return self.cache_manager.get(cache_key, cache_type='query')

    def set_cached_file_scores(
        self,
        query: str,
        file_ids: List[int],
        scores: Dict[int, float],
        ttl: int = 1800
    ) -> None:
        """
        Cache file matching scores.

        Args:
            query: User query
            file_ids: List of file IDs scored
            scores: Dict mapping file_id -> score
            ttl: Time to live in seconds (default: 30 minutes)
        """
        query_hash = self.hash_query(query)
        file_ids_hash = hashlib.md5(str(sorted(file_ids)).encode()).hexdigest()[:8]
        cache_key = f"file_scores:{query_hash}:{file_ids_hash}"
        self.cache_manager.set(cache_key, scores, ttl=ttl, cache_type='query')
        logger.debug(f"Cached scores for {len(scores)} files (query: {query_hash[:8]})")

    # ========== Web Search Caching ==========

    def get_cached_web_search(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Get cached web search results.

        Args:
            query: Search query

        Returns:
            Web search results dict or None if not cached
        """
        query_hash = self.hash_query(query)
        cache_key = f"web_search:{query_hash}"
        return self.cache_manager.get(cache_key, cache_type='api')

    def set_cached_web_search(self, query: str, results: Dict[str, Any], ttl: int = 7200) -> None:
        """
        Cache web search results.

        Args:
            query: Search query
            results: Web search results dict
            ttl: Time to live in seconds (default: 2 hours)
        """
        query_hash = self.hash_query(query)
        cache_key = f"web_search:{query_hash}"
        self.cache_manager.set(cache_key, results, ttl=ttl, cache_type='api')
        logger.debug(f"Cached web search results for query: {query[:50]}...")

    # ========== Pattern Matching Caching ==========

    def get_cached_pattern_result(self, query: str, pattern_type: str) -> Optional[bool]:
        """
        Get cached pattern matching result.

        Args:
            query: User query
            pattern_type: Type of pattern ('web_search', 'upload_notification', 'time_query', etc.)

        Returns:
            Boolean pattern match result or None if not cached
        """
        query_hash = self.hash_query(query)
        cache_key = f"pattern:{pattern_type}:{query_hash}"
        return self.cache_manager.get(cache_key, cache_type='query')

    def set_cached_pattern_result(
        self,
        query: str,
        pattern_type: str,
        result: bool,
        ttl: int = 1800
    ) -> None:
        """
        Cache pattern matching result.

        Args:
            query: User query
            pattern_type: Type of pattern
            result: Boolean pattern match result
            ttl: Time to live in seconds (default: 30 minutes)
        """
        query_hash = self.hash_query(query)
        cache_key = f"pattern:{pattern_type}:{query_hash}"
        self.cache_manager.set(cache_key, result, ttl=ttl, cache_type='query')
        logger.debug(f"Cached pattern result {pattern_type} for query: {query_hash[:8]}")

    # ========== Entity Context Caching ==========

    def get_cached_entity_context(self, query: str) -> Optional[str]:
        """
        Get cached entity context retrieval result.

        Args:
            query: User query

        Returns:
            Entity context string or None if not cached
        """
        query_hash = self.hash_query(query)
        cache_key = f"entity_context:{query_hash}"
        return self.cache_manager.get(cache_key, cache_type='query')

    def set_cached_entity_context(self, query: str, context: str, ttl: int = 1800) -> None:
        """
        Cache entity context retrieval result.

        Args:
            query: User query
            context: Retrieved entity context
            ttl: Time to live in seconds (default: 30 minutes)
        """
        query_hash = self.hash_query(query)
        cache_key = f"entity_context:{query_hash}"
        self.cache_manager.set(cache_key, context, ttl=ttl, cache_type='query')
        logger.debug(f"Cached entity context ({len(context)} chars) for query: {query_hash[:8]}")

    # ========== Statistics & Management ==========

    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dict with cache hit rate, sizes, and counts
        """
        return self.cache_manager.get_stats()

    def cleanup_expired(self) -> Dict[str, int]:
        """
        Clean up expired cache entries.

        Returns:
            Dict with count of expired entries per cache type
        """
        return self.cache_manager.cleanup_expired()

    def clear_all(self) -> None:
        """Clear all caches (use with caution)."""
        self.cache_manager.clear_all()
        logger.warning("All caches cleared")


# Global query cache instance
query_cache = QueryCache()


# ========== Decorator for Query Caching ==========

def cache_query_result(ttl: int = 1800, key_prefix: Optional[str] = None):
    """
    Decorator for caching query-related function results.

    Args:
        ttl: Time to live in seconds (default: 30 minutes)
        key_prefix: Optional prefix for cache key

    Example:
        @cache_query_result(ttl=3600, key_prefix='my_func')
        def expensive_query_operation(query: str):
            # ... expensive operation ...
            return result
    """
    return cached(cache_type='query', ttl=ttl, key_prefix=key_prefix)


__all__ = [
    'QueryCache',
    'query_cache',
    'cache_query_result'
]
