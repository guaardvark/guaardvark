"""Unit tests for the RAG/embedding refactor (Phases 1-4).

All mock-based — no running Ollama required. Every behavior is paired with its negative
case (zero-placebo rule): the guards must actually fall back when their inputs are bad.
"""
import os
from unittest.mock import MagicMock, patch

import pytest


# ---------- Phase 1: dedup uses the active model, batched, with safe fallbacks ----------

def test_dedup_drops_duplicate_with_single_batched_call():
    from backend.services import indexing_service as ix

    # Two identical chunks (→ cosine 1.0) + one distinct → expect the dup collapsed.
    chunks = [
        {"text": "alpha beta", "score": 0.9},
        {"text": "alpha beta", "score": 0.5},   # duplicate of #0, lower score → dropped
        {"text": "totally different", "score": 0.8},
    ]
    fake_router = MagicMock()
    fake_router.get_embeddings_batch.return_value = [
        [1.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],   # identical vector → duplicate
        [0.0, 1.0, 0.0],
    ]
    with patch("backend.utils.embedding_router.get_embedding_router", return_value=fake_router), \
         patch("backend.config.get_active_embedding_model", return_value="test-model"):
        out = ix.deduplicate_chunks(chunks)

    assert len(out) == 2                     # one duplicate removed
    assert fake_router.get_embeddings_batch.call_count == 1   # ONE batched call, not O(N)
    kept_scores = {c["score"] for c in out}
    assert 0.5 not in kept_scores            # the lower-scored duplicate was the one dropped


def test_dedup_returns_unchanged_on_router_error():
    from backend.services import indexing_service as ix
    chunks = [{"text": "a", "score": 1}, {"text": "b", "score": 1}]
    fake_router = MagicMock()
    fake_router.get_embeddings_batch.side_effect = RuntimeError("ollama down")
    with patch("backend.utils.embedding_router.get_embedding_router", return_value=fake_router), \
         patch("backend.config.get_active_embedding_model", return_value="m"):
        out = ix.deduplicate_chunks(chunks)
    assert out == chunks                     # negative case: never drop results on error


def test_dedup_returns_unchanged_on_shape_mismatch():
    from backend.services import indexing_service as ix
    chunks = [{"text": "a", "score": 1}, {"text": "b", "score": 1}, {"text": "c", "score": 1}]
    fake_router = MagicMock()
    fake_router.get_embeddings_batch.return_value = [[1.0, 0.0]]  # wrong length (1 != 3)
    with patch("backend.utils.embedding_router.get_embedding_router", return_value=fake_router), \
         patch("backend.config.get_active_embedding_model", return_value="m"):
        out = ix.deduplicate_chunks(chunks)
    assert out == chunks                     # negative case: malformed embeddings → skip dedup


# ---------- Phase 2: adaptive alpha bands ----------

def test_adaptive_alpha_keyword_leans_bm25():
    from backend.services.indexing_service import _adaptive_alpha
    assert _adaptive_alpha("get_active_embedding_model", 0.3) <= 0.25   # snake_case identifier
    assert _adaptive_alpha('"exact phrase"', 0.3) <= 0.25               # quoted
    assert _adaptive_alpha("auth", 0.3) <= 0.25                          # short


def test_adaptive_alpha_prose_leans_vector():
    from backend.services.indexing_service import _adaptive_alpha
    prose = "how does the retrieval pipeline decide which chunks are most relevant to the user question"
    assert _adaptive_alpha(prose, 0.3) >= 0.55


def test_adaptive_alpha_medium_uses_base():
    from backend.services.indexing_service import _adaptive_alpha
    assert _adaptive_alpha("explain the caching behavior here", 0.4) == 0.4


# ---------- Phase 2: MMR reranker ----------

def test_mmr_passthrough_for_tiny_lists():
    from backend.services.indexing_service import _mmr_rerank
    one = [{"text": "x", "score": 1.0}]
    assert _mmr_rerank(one) == one


def test_mmr_reorders_and_preserves_membership():
    from backend.services.indexing_service import _mmr_rerank
    results = [
        {"text": "apple apple apple", "score": 0.90},
        {"text": "apple apple apple", "score": 0.70},   # near-dup of #0, lower relevance
        {"text": "orange banana kiwi", "score": 0.85},  # diverse, comparable relevance
    ]
    out = _mmr_rerank(results)
    assert len(out) == 3                                  # nothing lost
    assert {id(r) for r in out} == {id(r) for r in results}
    # the comparably-relevant diverse doc is promoted above the near-duplicate
    assert out[1]["text"] == "orange banana kiwi"


# ---------- Phase 2: BM25 retriever cache ----------

def test_bm25_cache_builds_once_then_rebuilds_on_change():
    from backend.services import indexing_service as ix
    ix._bm25_cache.clear()
    docstore = MagicMock()
    docstore.docs = {"a": 1, "b": 2}

    sentinel = object()
    with patch("llama_index.retrievers.bm25.BM25Retriever.from_defaults", return_value=sentinel) as mk:
        r1 = ix._get_cached_bm25_retriever(docstore, 5)
        r2 = ix._get_cached_bm25_retriever(docstore, 5)   # same docstore + count → cache hit
        assert r1 is sentinel and r2 is sentinel
        assert mk.call_count == 1
        docstore.docs = {"a": 1, "b": 2, "c": 3}          # in-place add → count changed
        ix._get_cached_bm25_retriever(docstore, 5)
        assert mk.call_count == 2                          # rebuilt


# ---------- Phase 3: hardware-aware keep_alive ----------

def test_keep_alive_resident_without_gpu(monkeypatch):
    monkeypatch.delenv("GUAARDVARK_EMBED_KEEP_ALIVE_CPU", raising=False)
    with patch("backend.services.gpu_resource_coordinator.has_gpu", return_value=False):
        from backend.config import get_embedding_keep_alive
        assert get_embedding_keep_alive() == -1            # resident on CPU-only


def test_keep_alive_short_ttl_with_gpu(monkeypatch):
    monkeypatch.delenv("GUAARDVARK_EMBED_KEEP_ALIVE_GPU", raising=False)
    with patch("backend.services.gpu_resource_coordinator.has_gpu", return_value=True):
        from backend.config import get_embedding_keep_alive
        assert get_embedding_keep_alive() == "5m"          # frees VRAM after idle


def test_default_advanced_rag_follows_gpu(monkeypatch):
    monkeypatch.delenv("GUAARDVARK_ADVANCED_RAG", raising=False)
    from backend import config
    with patch("backend.services.gpu_resource_coordinator.has_gpu", return_value=False):
        assert config.default_advanced_rag() is False
    with patch("backend.services.gpu_resource_coordinator.has_gpu", return_value=True):
        assert config.default_advanced_rag() is True


# ---------- Phase 4: dimension-lock ----------

def test_dimension_lock_blocks_on_model_mismatch(tmp_path, monkeypatch):
    import json
    from backend.services import indexing_service as ix
    monkeypatch.setenv("GUAARDVARK_INDEX_ROOT", str(tmp_path))
    monkeypatch.setenv("GUAARDVARK_PROJECT_INDEX_MODE", "single")
    (tmp_path / "embedding_meta.json").write_text(json.dumps({"embedding_model": "old-model"}))
    with patch("backend.config.get_active_embedding_model", return_value="new-model"):
        assert ix._check_index_embedding_model() is False     # mismatch → blocked
    with patch("backend.config.get_active_embedding_model", return_value="old-model"):
        assert ix._check_index_embedding_model() is True      # match → allowed


@pytest.mark.parametrize("stored,active", [("nomic-embed-text:latest", "nomic-embed-text"),
                                           ("nomic-embed-text", "nomic-embed-text:latest")])
def test_dimension_lock_takes_the_latest_tag_as_the_same_model(tmp_path, monkeypatch, stored, active):
    import json
    from backend.services import indexing_service as ix
    from backend.utils import ollama_resource_manager as orm
    monkeypatch.setenv("GUAARDVARK_INDEX_ROOT", str(tmp_path))
    monkeypatch.setenv("GUAARDVARK_PROJECT_INDEX_MODE", "single")
    monkeypatch.setattr(orm, "_ollama_tags", lambda: pytest.fail("the names alone settle it"))
    (tmp_path / "embedding_meta.json").write_text(json.dumps({"embedding_model": stored}))
    with patch("backend.config.get_active_embedding_model", return_value=active):
        assert ix._check_index_embedding_model() is True


def test_dimension_lock_backfills_when_missing(tmp_path, monkeypatch):
    from backend.services import indexing_service as ix
    monkeypatch.setenv("GUAARDVARK_INDEX_ROOT", str(tmp_path))
    monkeypatch.setenv("GUAARDVARK_PROJECT_INDEX_MODE", "single")
    with patch("backend.config.get_active_embedding_model", return_value="m"):
        assert ix._check_index_embedding_model() is True      # missing → backfill, allow
        assert (tmp_path / "embedding_meta.json").exists()    # sidecar written


# ---------- Phase 4: query-embedding cache ----------

def test_query_embed_cache_hits_second_call(monkeypatch):
    from backend.services import indexing_service as ix
    ix._query_embed_cache.clear()
    fake_settings = MagicMock()
    fake_settings.embed_model.get_query_embedding.return_value = [0.1, 0.2, 0.3]
    with patch.dict("sys.modules", {"llama_index.core": fake_settings}), \
         patch("backend.config.get_active_embedding_model", return_value="m"):
        # `from llama_index.core import Settings` → Settings is attr on the module mock
        fake_settings.Settings = fake_settings
        v1 = ix._get_cached_query_embedding("repeat me")
        v2 = ix._get_cached_query_embedding("repeat me")
    assert v1 == [0.1, 0.2, 0.3] and v2 == v1
    assert fake_settings.embed_model.get_query_embedding.call_count == 1   # 2nd call cached
