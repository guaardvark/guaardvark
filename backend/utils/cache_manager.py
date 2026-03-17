import time
import json
import hashlib
import logging
from typing import Any, Optional, Dict, Callable
from functools import wraps
from collections import OrderedDict
from threading import Lock, RLock

logger = logging.getLogger(__name__)


class LRUCache:

    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self.cache = OrderedDict()
        self.lock = RLock()

    def get(self, key: str) -> Optional[Any]:
        with self.lock:
            if key in self.cache:
                value = self.cache.pop(key)
                self.cache[key] = value
                return value
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        with self.lock:
            if len(self.cache) >= self.max_size and key not in self.cache:
                self.cache.popitem(last=False)

            if ttl:
                value = {"data": value, "expires_at": time.time() + ttl}

            self.cache[key] = value

    def delete(self, key: str) -> bool:
        with self.lock:
            return self.cache.pop(key, None) is not None

    def clear(self) -> None:
        with self.lock:
            self.cache.clear()

    def cleanup_expired(self) -> int:
        current_time = time.time()
        expired_keys = []

        with self.lock:
            for key, value in self.cache.items():
                if isinstance(value, dict) and "expires_at" in value:
                    if current_time > value["expires_at"]:
                        expired_keys.append(key)

            for key in expired_keys:
                del self.cache[key]

        return len(expired_keys)


class CacheManager:

    def __init__(self):
        self.api_cache = LRUCache(max_size=500)
        self.query_cache = LRUCache(max_size=200)
        self.model_cache = LRUCache(max_size=100)
        self.metadata_cache = LRUCache(max_size=1000)

        self._stats = {"hits": 0, "misses": 0, "sets": 0, "deletes": 0}
        self._stats_lock = Lock()

    def _get_cache(self, cache_type: str) -> LRUCache:
        cache_map = {
            "api": self.api_cache,
            "query": self.query_cache,
            "model": self.model_cache,
            "metadata": self.metadata_cache,
        }
        return cache_map.get(cache_type, self.api_cache)

    def _make_key(self, prefix: str, *args, **kwargs) -> str:
        key_data = {
            "prefix": prefix,
            "args": args,
            "kwargs": sorted(kwargs.items()) if kwargs else {},
        }
        key_str = json.dumps(key_data, sort_keys=True, default=str)
        return hashlib.md5(key_str.encode()).hexdigest()

    def get(self, key: str, cache_type: str = "api") -> Optional[Any]:
        cache = self._get_cache(cache_type)
        value = cache.get(key)

        with self._stats_lock:
            if value is not None:
                if isinstance(value, dict) and "expires_at" in value:
                    if time.time() > value["expires_at"]:
                        cache.delete(key)
                        self._stats["misses"] += 1
                        return None
                    value = value["data"]

                self._stats["hits"] += 1
                return value
            else:
                self._stats["misses"] += 1
                return None

    def set(
        self, key: str, value: Any, ttl: Optional[int] = None, cache_type: str = "api"
    ) -> None:
        cache = self._get_cache(cache_type)
        cache.set(key, value, ttl)

        with self._stats_lock:
            self._stats["sets"] += 1

    def delete(self, key: str, cache_type: str = "api") -> bool:
        cache = self._get_cache(cache_type)
        deleted = cache.delete(key)

        if deleted:
            with self._stats_lock:
                self._stats["deletes"] += 1

        return deleted

    def clear_all(self) -> None:
        self.api_cache.clear()
        self.query_cache.clear()
        self.model_cache.clear()
        self.metadata_cache.clear()

        with self._stats_lock:
            self._stats = {"hits": 0, "misses": 0, "sets": 0, "deletes": 0}

    def cleanup_expired(self) -> Dict[str, int]:
        return {
            "api": self.api_cache.cleanup_expired(),
            "query": self.query_cache.cleanup_expired(),
            "model": self.model_cache.cleanup_expired(),
            "metadata": self.metadata_cache.cleanup_expired(),
        }

    def get_stats(self) -> Dict[str, Any]:
        with self._stats_lock:
            stats = self._stats.copy()

        total_requests = stats["hits"] + stats["misses"]
        hit_rate = stats["hits"] / total_requests if total_requests > 0 else 0

        return {
            **stats,
            "total_requests": total_requests,
            "hit_rate": hit_rate,
            "cache_sizes": {
                "api": len(self.api_cache.cache),
                "query": len(self.query_cache.cache),
                "model": len(self.model_cache.cache),
                "metadata": len(self.metadata_cache.cache),
            },
        }


cache_manager = CacheManager()


def cached(
    cache_type: str = "api", ttl: Optional[int] = None, key_prefix: Optional[str] = None
):
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            prefix = key_prefix or f"{func.__module__}.{func.__name__}"
            cache_key = cache_manager._make_key(prefix, *args, **kwargs)

            cached_result = cache_manager.get(cache_key, cache_type)
            if cached_result is not None:
                logger.debug(f"Cache hit for {prefix}: {cache_key[:16]}...")
                return cached_result

            try:
                result = func(*args, **kwargs)
                cache_manager.set(cache_key, result, ttl, cache_type)
                logger.debug(f"Cache set for {prefix}: {cache_key[:16]}...")
                return result
            except Exception as e:
                logger.warning(f"Function {prefix} failed, not caching: {e}")
                raise

        return wrapper

    return decorator


def invalidate_cache_pattern(pattern: str, cache_type: str = "api") -> int:
    cache = cache_manager._get_cache(cache_type)
    keys_to_delete = []

    with cache.lock:
        for key in cache.cache.keys():
            if pattern in key:
                keys_to_delete.append(key)

    for key in keys_to_delete:
        cache.delete(key)

    return len(keys_to_delete)
