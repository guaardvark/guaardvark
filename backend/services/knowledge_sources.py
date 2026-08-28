"""Supplementary knowledge sources for the unified chat engine's RAG step.

The engine retrieves from its own pgvector index. A distribution that owns a
second corpus — a product catalogue, a code-book, a price list — registers a
retriever here and its hits are appended to the same RAG block, titled so the
model can cite them.

A retriever is ``fn(query, top_k) -> list[{"title", "snippet", "score"}]``. It
runs on the chat request path, so it must be fast and bounded; keep snippets
short. A retriever that raises is skipped rather than failing the turn.

    from backend.services.knowledge_sources import register_knowledge_source

    register_knowledge_source("catalogue", search_catalogue, min_score=0.6)
"""

import logging
import threading
from typing import Any, Callable, Dict, List, NamedTuple, Optional

logger = logging.getLogger(__name__)

KnowledgeRetriever = Callable[[str, int], Optional[List[Dict[str, Any]]]]


class _Source(NamedTuple):
    retrieve: KnowledgeRetriever
    min_score: Optional[float]


# Insertion-ordered: sources are queried in the order they were registered.
_sources: Dict[str, _Source] = {}
_lock = threading.Lock()


def register_knowledge_source(
    name: str, retrieve_fn: KnowledgeRetriever, *, min_score: Optional[float] = None
) -> None:
    """Register ``retrieve_fn`` under ``name``, replacing any source of the same name.

    Hits scoring below ``min_score`` are dropped before the engine sees them;
    ``None`` keeps everything the retriever returns.
    """
    if not name or not isinstance(name, str):
        raise ValueError("knowledge source name must be a non-empty string")
    if not callable(retrieve_fn):
        raise ValueError(f"knowledge source {name!r} must be callable")
    with _lock:
        _sources[name] = _Source(retrieve_fn, min_score)


def unregister_knowledge_source(name: str) -> bool:
    """Remove the source registered under ``name``. Returns whether one existed."""
    with _lock:
        return _sources.pop(name, None) is not None


def list_knowledge_sources() -> List[str]:
    """Return registered source names in query order."""
    with _lock:
        return list(_sources)


def retrieve_from_sources(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """Query every registered source and return the surviving hits in source order.

    ``top_k`` is a per-source limit, passed straight through to each retriever;
    the combined list can therefore hold up to ``top_k`` hits per source.
    """
    with _lock:
        sources = list(_sources.items())

    hits: List[Dict[str, Any]] = []
    for name, source in sources:
        try:
            raw = source.retrieve(query, top_k)
        except Exception as e:
            logger.warning(f"[KNOWLEDGE_SOURCES] source {name!r} failed: {e}")
            continue
        if not raw:
            continue
        for item in raw:
            hit = _normalize_hit(name, item)
            if hit is None:
                continue
            if source.min_score is not None and hit["score"] < source.min_score:
                continue
            hits.append(hit)
    return hits


def _normalize_hit(name: str, item: Any) -> Optional[Dict[str, Any]]:
    """Coerce one retriever result to ``{"title", "snippet", "score"}``, or None."""
    if not isinstance(item, dict):
        logger.debug(f"[KNOWLEDGE_SOURCES] source {name!r} returned a non-dict hit")
        return None

    snippet = item.get("snippet")
    if not isinstance(snippet, str) or not snippet.strip():
        return None

    title = item.get("title")
    title = title.strip() if isinstance(title, str) and title.strip() else name

    try:
        score = float(item.get("score", 0.0))
    except (TypeError, ValueError):
        score = 0.0

    return {"title": title, "snippet": snippet.strip(), "score": score}
