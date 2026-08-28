import pytest

from backend.services import knowledge_sources as ks


@pytest.fixture(autouse=True)
def restore_registry():
    """Leave the process-wide registry exactly as the test found it."""
    snapshot = dict(ks._sources)
    yield
    ks._sources.clear()
    ks._sources.update(snapshot)


def hit(title, score=1.0, snippet=None):
    return {"title": title, "snippet": snippet or f"{title} body", "score": score}


def test_registry_starts_empty():
    assert ks.list_knowledge_sources() == []
    assert ks.retrieve_from_sources("anything") == []


def test_register_and_unregister():
    ks.register_knowledge_source("catalogue", lambda q, k: [hit("Shingles")])

    assert ks.list_knowledge_sources() == ["catalogue"]
    assert ks.retrieve_from_sources("roof")[0]["title"] == "Shingles"

    assert ks.unregister_knowledge_source("catalogue") is True
    assert ks.list_knowledge_sources() == []
    assert ks.unregister_knowledge_source("catalogue") is False


def test_register_rejects_bad_arguments():
    with pytest.raises(ValueError):
        ks.register_knowledge_source("", lambda q, k: [])
    with pytest.raises(ValueError):
        ks.register_knowledge_source("nope", "not callable")


def test_sources_are_queried_in_registration_order():
    ks.register_knowledge_source("first", lambda q, k: [hit("A")])
    ks.register_knowledge_source("second", lambda q, k: [hit("B"), hit("C")])

    assert [h["title"] for h in ks.retrieve_from_sources("q")] == ["A", "B", "C"]


def test_query_and_top_k_reach_the_retriever():
    seen = {}

    def spy(query, top_k):
        seen["query"] = query
        seen["top_k"] = top_k
        return []

    ks.register_knowledge_source("spy", spy)
    ks.retrieve_from_sources("gutter pitch", top_k=3)

    assert seen == {"query": "gutter pitch", "top_k": 3}


def test_min_score_drops_weak_hits():
    ks.register_knowledge_source(
        "scored",
        lambda q, k: [hit("Strong", 0.9), hit("Weak", 0.4), hit("Edge", 0.6)],
        min_score=0.6,
    )

    assert [h["title"] for h in ks.retrieve_from_sources("q")] == ["Strong", "Edge"]


def test_without_min_score_every_hit_survives():
    ks.register_knowledge_source("scored", lambda q, k: [hit("Strong", 0.9), hit("Weak", 0.0)])

    assert [h["title"] for h in ks.retrieve_from_sources("q")] == ["Strong", "Weak"]


def test_a_failing_source_does_not_break_the_others():
    def boom(query, top_k):
        raise RuntimeError("source exploded")

    ks.register_knowledge_source("before", lambda q, k: [hit("A")])
    ks.register_knowledge_source("boom", boom)
    ks.register_knowledge_source("after", lambda q, k: [hit("B")])

    assert [h["title"] for h in ks.retrieve_from_sources("q")] == ["A", "B"]


def test_malformed_hits_are_dropped_and_missing_fields_defaulted():
    ks.register_knowledge_source(
        "sloppy",
        lambda q, k: [
            "not a dict",
            {"title": "No snippet"},
            {"snippet": "   "},
            {"snippet": "  untitled body  "},
            {"title": "Bad score", "snippet": "body", "score": "high"},
        ],
    )

    hits = ks.retrieve_from_sources("q")
    assert hits == [
        {"title": "sloppy", "snippet": "untitled body", "score": 0.0},
        {"title": "Bad score", "snippet": "body", "score": 0.0},
    ]


def test_a_source_returning_nothing_is_harmless():
    ks.register_knowledge_source("empty_list", lambda q, k: [])
    ks.register_knowledge_source("none", lambda q, k: None)

    assert ks.retrieve_from_sources("q") == []


@pytest.fixture
def engine():
    from backend.services.unified_chat_engine import UnifiedChatEngine

    return UnifiedChatEngine(tool_registry=None, llm_instance=None)


@pytest.fixture
def pgvector_results(monkeypatch):
    """Stub the pgvector search so no index or database is touched."""
    from backend.services import indexing_service

    results = []
    monkeypatch.setattr(
        indexing_service, "search_with_llamaindex", lambda query, **kw: list(results)
    )
    return results


def test_rag_output_is_unchanged_when_no_sources_are_registered(engine, pgvector_results):
    pgvector_results.append({"text": "shingle spec", "metadata": {"source_filename": "spec.pdf"}})

    assert engine._retrieve_rag_context("q") == "[Source: spec.pdf]\nshingle spec"


def test_rag_returns_empty_string_when_nothing_matches(engine, pgvector_results):
    assert engine._retrieve_rag_context("q") == ""


def test_rag_appends_source_hits_after_pgvector_chunks(engine, pgvector_results):
    pgvector_results.append({"text": "shingle spec", "metadata": {"source_filename": "spec.pdf"}})
    ks.register_knowledge_source("catalogue", lambda q, k: [hit("Ridge Vent")])

    assert engine._retrieve_rag_context("q") == (
        "[Source: spec.pdf]\nshingle spec\n\n"
        "[Source: Ridge Vent]\nRidge Vent body"
    )


def test_rag_returns_source_hits_even_when_pgvector_is_empty(engine, pgvector_results):
    ks.register_knowledge_source("catalogue", lambda q, k: [hit("Ridge Vent")])

    assert engine._retrieve_rag_context("q") == "[Source: Ridge Vent]\nRidge Vent body"


def test_rag_clips_source_snippets_like_pgvector_chunks(engine, pgvector_results):
    ks.register_knowledge_source("verbose", lambda q, k: [hit("Long", snippet="x" * 900)])

    assert engine._retrieve_rag_context("q") == "[Source: Long]\n" + "x" * 500
