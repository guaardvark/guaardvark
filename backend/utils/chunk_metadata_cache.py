# backend/utils/chunk_metadata_cache.py
# Chunk Metadata Cache for Enhanced RAG Chunking
# Provides caching for chunk metadata to avoid recalculation

import json
import hashlib
import logging
import os
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Try to use Redis if available, fallback to in-memory cache
try:
    import redis
    _redis_client = None
    try:
        _redis_client = redis.Redis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0'), db=1, decode_responses=True)
        _redis_client.ping()
        USE_REDIS = True
        logger.info("Chunk metadata cache: Using Redis")
    except Exception as e:
        logger.warning(f"Chunk metadata cache: Redis not available, using in-memory cache: {e}")
        USE_REDIS = False
        _redis_client = None
except ImportError:
    logger.warning("Chunk metadata cache: Redis library not available, using in-memory cache")
    USE_REDIS = False
    _redis_client = None

# In-memory fallback cache
_in_memory_cache: Dict[str, Dict[str, Any]] = {}
_cache_stats = {
    'hits': 0,
    'misses': 0,
    'sets': 0,
    'invalidations': 0
}


def _make_cache_key(document_id: str, content_hash: str) -> str:
    """Generate cache key for chunk metadata"""
    return f"chunk_metadata:{document_id}:{content_hash}"


def _make_document_key(document_id: str) -> str:
    """Generate cache key pattern for all chunks of a document"""
    return f"chunk_metadata:{document_id}:*"


def get_cached_metadata(document_id: str, content_hash: str) -> Optional[Dict[str, Any]]:
    """
    Get cached metadata for a chunk
    
    Args:
        document_id: Document identifier
        content_hash: Hash of chunk content
        
    Returns:
        Cached metadata dict or None if not found
    """
    cache_key = _make_cache_key(document_id, content_hash)
    
    try:
        if USE_REDIS and _redis_client:
            cached_data = _redis_client.get(cache_key)
            if cached_data:
                _cache_stats['hits'] += 1
                return json.loads(cached_data)
            else:
                _cache_stats['misses'] += 1
                return None
        else:
            # In-memory cache
            if cache_key in _in_memory_cache:
                _cache_stats['hits'] += 1
                return _in_memory_cache[cache_key]
            else:
                _cache_stats['misses'] += 1
                return None
    except Exception as e:
        logger.warning(f"Error retrieving cached metadata: {e}")
        _cache_stats['misses'] += 1
        return None


def set_cached_metadata(document_id: str, content_hash: str, metadata: Dict[str, Any], ttl: int = 86400) -> bool:
    """
    Cache metadata for a chunk
    
    Args:
        document_id: Document identifier
        content_hash: Hash of chunk content
        metadata: Metadata to cache
        ttl: Time to live in seconds (default: 24 hours)
        
    Returns:
        True if cached successfully, False otherwise
    """
    cache_key = _make_cache_key(document_id, content_hash)
    
    try:
        # Add timestamp to metadata
        metadata['cached_at'] = datetime.now().isoformat()
        
        if USE_REDIS and _redis_client:
            _redis_client.setex(
                cache_key,
                ttl,
                json.dumps(metadata, default=str)
            )
            _cache_stats['sets'] += 1
            return True
        else:
            # In-memory cache (no TTL support, but we'll limit size)
            if len(_in_memory_cache) > 10000:  # Limit to 10k entries
                # Remove oldest entries (simple FIFO)
                oldest_key = next(iter(_in_memory_cache))
                del _in_memory_cache[oldest_key]
            
            _in_memory_cache[cache_key] = metadata
            _cache_stats['sets'] += 1
            return True
    except Exception as e:
        logger.warning(f"Error caching metadata: {e}")
        return False


def invalidate_document(document_id: str) -> int:
    """
    Invalidate all cached metadata for a document
    
    Args:
        document_id: Document identifier
        
    Returns:
        Number of entries invalidated
    """
    count = 0
    
    try:
        if USE_REDIS and _redis_client:
            # Use SCAN to find all keys matching pattern
            pattern = _make_document_key(document_id)
            cursor = 0
            while True:
                cursor, keys = _redis_client.scan(cursor, match=pattern, count=100)
                if keys:
                    _redis_client.delete(*keys)
                    count += len(keys)
                if cursor == 0:
                    break
        else:
            # In-memory cache
            keys_to_delete = [
                key for key in _in_memory_cache.keys()
                if key.startswith(f"chunk_metadata:{document_id}:")
            ]
            for key in keys_to_delete:
                del _in_memory_cache[key]
                count += 1
        
        _cache_stats['invalidations'] += count
        logger.info(f"Invalidated {count} cache entries for document {document_id}")
    except Exception as e:
        logger.warning(f"Error invalidating document cache: {e}")
    
    return count


def clear_cache() -> int:
    """
    Clear all cached metadata
    
    Returns:
        Number of entries cleared
    """
    count = 0
    
    try:
        if USE_REDIS and _redis_client:
            # Delete all chunk metadata keys
            cursor = 0
            while True:
                cursor, keys = _redis_client.scan(cursor, match="chunk_metadata:*", count=100)
                if keys:
                    _redis_client.delete(*keys)
                    count += len(keys)
                if cursor == 0:
                    break
        else:
            # In-memory cache
            count = len(_in_memory_cache)
            _in_memory_cache.clear()
        
        logger.info(f"Cleared {count} cache entries")
    except Exception as e:
        logger.warning(f"Error clearing cache: {e}")
    
    return count


def get_cache_stats() -> Dict[str, Any]:
    """
    Get cache statistics
    
    Returns:
        Dictionary with cache statistics
    """
    total_requests = _cache_stats['hits'] + _cache_stats['misses']
    hit_rate = (_cache_stats['hits'] / total_requests * 100) if total_requests > 0 else 0
    
    stats = {
        'hits': _cache_stats['hits'],
        'misses': _cache_stats['misses'],
        'sets': _cache_stats['sets'],
        'invalidations': _cache_stats['invalidations'],
        'total_requests': total_requests,
        'hit_rate': round(hit_rate, 2),
        'backend': 'redis' if USE_REDIS else 'memory',
        'cache_size': len(_in_memory_cache) if not USE_REDIS else None
    }
    
    return stats

