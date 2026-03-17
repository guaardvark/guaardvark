import logging
from flask import Blueprint, jsonify, request
from backend.utils.cache_manager import cache_manager, invalidate_cache_pattern
from backend.utils.response_utils import success_response, error_response


class CacheStatsBlueprint(Blueprint):
    """Blueprint for cache statistics API endpoints."""

    def __init__(self, name: str, import_name: str, url_prefix: str = "/api/cache"):
        super().__init__(name, import_name, url_prefix=url_prefix)


cache_stats_bp = CacheStatsBlueprint("cache_stats", __name__)

logger = logging.getLogger(__name__)


@cache_stats_bp.route("/stats", methods=["GET"])
def get_cache_stats() -> dict:
    """Return cache statistics."""

    try:
        stats = cache_manager.get_stats()
        return success_response(stats)
    except Exception as e:
        logger.error(f"Failed to get cache stats: {e}")
        return error_response(str(e), 500)


@cache_stats_bp.route("/clear", methods=["POST"])
def clear_cache() -> dict:
    """Clear cache by type or all."""

    try:
        data = request.get_json() or {}
        cache_type = data.get("cache_type", "all")

        if cache_type == "all":
            cache_manager.clear_all()
            message = "All caches cleared successfully"
        else:
            cache = cache_manager._get_cache(cache_type)
            if cache:
                cache.clear()
                message = f"Cache '{cache_type}' cleared successfully"
            else:
                return error_response(f"Invalid cache type: {cache_type}", 400)

        return success_response({"message": message})
    except Exception as e:
        logger.error(f"Failed to clear cache: {e}")
        return error_response(str(e), 500)


@cache_stats_bp.route("/cleanup", methods=["POST"])
def cleanup_expired() -> dict:
    """Clean up expired cache entries."""

    try:
        expired_counts = cache_manager.cleanup_expired()
        total_expired = sum(expired_counts.values())

        return success_response(
            {
                "message": f"Cleaned up {total_expired} expired entries",
                "expired_by_cache": expired_counts,
                "total_expired": total_expired,
            }
        )
    except Exception as e:
        logger.error(f"Failed to cleanup expired cache entries: {e}")
        return error_response(str(e), 500)


@cache_stats_bp.route("/invalidate", methods=["POST"])
def invalidate_pattern() -> dict:
    """Invalidate cache pattern."""

    try:
        data = request.get_json()
        if not data or "pattern" not in data:
            return error_response("Pattern is required", 400)

        pattern = data["pattern"]
        cache_type = data.get("cache_type", "api")

        if not pattern:
            return error_response("Pattern cannot be empty", 400)

        invalidated_count = invalidate_cache_pattern(pattern, cache_type)

        return success_response(
            {
                "message": f"Invalidated {invalidated_count} cache entries",
                "pattern": pattern,
                "cache_type": cache_type,
                "invalidated_count": invalidated_count,
            }
        )
    except Exception as e:
        logger.error(f"Failed to invalidate cache pattern: {e}")
        return error_response(str(e), 500)


@cache_stats_bp.route("/health", methods=["GET"])
def cache_health() -> dict:
    """Return cache health status."""

    try:
        stats = cache_manager.get_stats()
        health_issues = []

        if stats["hit_rate"] < 0.3 and stats["total_requests"] > 100:
            health_issues.append("Low cache hit rate - consider cache optimization")

        if any(size > 900 for size in stats["cache_sizes"].values()):
            health_issues.append("Cache approaching capacity limits")

        health_status = "healthy" if not health_issues else "warning"

        return success_response(
            {
                "status": health_status,
                "issues": health_issues,
                "statistics": stats,
                "recommendations": _get_cache_recommendations(stats),
            }
        )
    except Exception as e:
        logger.error(f"Failed to get cache health: {e}")
        return error_response(str(e), 500)


def _get_cache_recommendations(stats: dict) -> list:
    """Return cache recommendations based on statistics."""

    recommendations = []

    if stats["hit_rate"] < 0.5:
        recommendations.append(
            "Consider increasing cache TTL for frequently accessed data"
        )

    if stats["total_requests"] > 1000 and stats["hit_rate"] > 0.8:
        recommendations.append(
            "Cache is performing well - consider increasing cache sizes"
        )

    if any(size > 800 for size in stats["cache_sizes"].values()):
        recommendations.append("Consider implementing cache eviction policies")

    return recommendations
