# backend/utils/query_engine_wrapper.py
"""Utilities for building RetrieverQueryEngine instances.

This module centralizes creation of ``RetrieverQueryEngine`` objects to
avoid passing duplicate ``retriever`` arguments which can lead to
``TypeError``. It also exposes :func:`get_query_engine` for the common
pattern of creating a retriever from an index and then building a query
engine from it.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

try:
    from llama_index.core.indices.base import BaseIndex
    from llama_index.core.query_engine import RetrieverQueryEngine
    from llama_index.core.retrievers import BaseRetriever
    from llama_index.core.retrievers.auto_merging_retriever import \
        AutoMergingRetriever
except Exception:  # pragma: no cover - missing optional deps
    RetrieverQueryEngine = None  # type: ignore
    BaseRetriever = Any  # type: ignore
    BaseIndex = Any  # type: ignore
    AutoMergingRetriever = Any  # type: ignore

logger = logging.getLogger(__name__)


def _build_query_engine_from_retriever(
    retriever: BaseRetriever, **kwargs: Any
) -> Optional[RetrieverQueryEngine]:
    """Return a ``RetrieverQueryEngine`` from ``retriever``.

    If ``retriever`` is also provided in ``kwargs`` the kwarg value
    takes precedence and a warning is emitted. Any errors during
    engine creation are logged and ``None`` is returned.
    """

    if RetrieverQueryEngine is None:
        logger.error(
            "RetrieverQueryEngine class unavailable; LlamaIndex not installed?"
        )
        return None

    if retriever is None:
        logger.error("_build_query_engine_from_retriever called with None retriever")
        return None

    if "retriever" in kwargs:
        logger.warning(
            "Duplicate 'retriever' argument provided to RetrieverQueryEngine.from_args"
        )
        if kwargs["retriever"] is not retriever:
            retriever = kwargs["retriever"]
        kwargs.pop("retriever")

    try:
        return RetrieverQueryEngine.from_args(retriever, **kwargs)
    except TypeError as te:
        logger.error(
            "TypeError while building RetrieverQueryEngine; check duplicate arguments: %s",
            te,
        )
    except Exception as exc:  # pragma: no cover - unexpected failures
        logger.error("Failed to build RetrieverQueryEngine: %s", exc, exc_info=True)
    return None


def get_query_engine(index: BaseIndex, **kwargs: Any) -> Optional[RetrieverQueryEngine]:
    """Create a ``RetrieverQueryEngine`` for ``index``.

    This obtains a retriever via ``index.as_retriever`` and delegates to
    :func:`_build_query_engine_from_retriever`. Errors are logged and
    ``None`` is returned on failure.
    """

    if index is None or not hasattr(index, "as_retriever"):
        logger.error("Invalid index passed to get_query_engine; missing as_retriever()")
        return None

    try:
        base_ret = index.as_retriever(similarity_top_k=10)
        retriever = AutoMergingRetriever(base_ret, index.storage_context)
    except Exception as exc:
        logger.error("Failed to create retriever from index: %s", exc, exc_info=True)
        return None

    engine = _build_query_engine_from_retriever(retriever, **kwargs)
    if engine is None:
        logger.error("Failed to create query engine from retriever")
    return engine
