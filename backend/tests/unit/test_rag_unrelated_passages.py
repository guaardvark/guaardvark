"""Passages unrelated to the message stay out of chat when no reranker scored them.

Without the cross-encoder a search always returns its top passages, so with one
note indexed that note reached every chat turn, a haiku request included. The
search now marks each passage against the embedding model's floor and
drop_unrelated removes the ones marked unrelated.
"""

import pytest
from llama_index.core.schema import NodeWithScore, TextNode

from backend import config
from backend.services import indexing_service as ix
from backend.utils import reranker


def _hit(source, vector_score=None, node_id=None, **extra):
    r = {"text": f"Text of {source}.", "metadata": {"source_filename": source},
         "node_id": node_id or source, "vector_score": vector_score}
    r.update(extra)
    return r


@pytest.fixture(autouse=True)
def _no_overrides(monkeypatch):
    monkeypatch.delenv("GUAARDVARK_RAG_MIN_VECTOR_SCORE", raising=False)
    monkeypatch.delenv("GUAARDVARK_RAG_MIN_RERANK_SCORE", raising=False)


@pytest.fixture
def nomic(monkeypatch):
    monkeypatch.setattr(config, "get_active_embedding_model", lambda: "nomic-embed-text:latest")


@pytest.fixture
def shared_words(monkeypatch):
    """Stand in for the index: which node ids share a rare word with the query."""
    holder = {"ids": set(), "stored": {}}
    monkeypatch.setattr(ix, "_passages_sharing_rare_words",
                        lambda table, query, ids: holder["ids"] & set(ids)
                        if holder["ids"] is not None else None)
    monkeypatch.setattr(ix, "_stored_similarity",
                        lambda table, query, ids: {i: s for i, s in holder["stored"].items() if i in ids})
    return holder


class TestFloorPerModel:
    @pytest.mark.parametrize("name,floor", [
        ("nomic-embed-text", 0.60),
        ("nomic-embed-text:latest", 0.60),
        ("embeddinggemma:latest", 0.30),
        ("embeddinggemma-2:latest", 0.62),
        ("qwen3-embedding:4b-q4_K_M", None),
        ("", None),
    ])
    def test_measured_models_have_a_floor_and_others_none(self, name, floor):
        assert config.get_vector_relevance_floor(name) == floor

    def test_the_environment_overrides_and_zero_turns_it_off(self, monkeypatch):
        monkeypatch.setenv("GUAARDVARK_RAG_MIN_VECTOR_SCORE", "0.5")
        assert config.get_vector_relevance_floor("qwen3-embedding:4b") == 0.5
        monkeypatch.setenv("GUAARDVARK_RAG_MIN_VECTOR_SCORE", "0")
        assert config.get_vector_relevance_floor("nomic-embed-text") is None


class TestVectorScores:
    def test_keeps_each_nodes_own_score_and_passes_nodes_through(self):
        nodes = [NodeWithScore(node=TextNode(id_="a", text="x"), score=0.55),
                 NodeWithScore(node=TextNode(id_="b", text="y"), score=0.81)]

        class Leg:
            def retrieve(self, query):
                return nodes

        leg = ix._VectorScores(Leg())
        assert leg.retrieve("q") is nodes
        nodes[0].score = 1.0  # fusion rescales in place afterwards
        assert leg.scores == {"a": 0.55, "b": 0.81}

    def test_a_second_retrieval_keeps_the_higher_score(self):
        batches = iter([[NodeWithScore(node=TextNode(id_="a", text="x"), score=0.4)],
                        [NodeWithScore(node=TextNode(id_="a", text="x"), score=0.7)]])

        class Leg:
            def retrieve(self, query):
                return next(batches)

        leg = ix._VectorScores(Leg())
        leg.retrieve("q")
        leg.retrieve("q again")
        assert leg.scores == {"a": 0.7}


class TestJudge:
    def test_one_note_reaches_its_questions_and_not_a_haiku(self, nomic, shared_words):
        # Cosines measured on a one-note index with nomic-embed-text (2026-11-08).
        haiku = [_hit("orchard-notes.md", 0.555)]
        ix._judge_by_vector_score(haiku, "Write a haiku about an old computer.", "t", {})
        asked = [_hit("orchard-notes.md", 0.853)]
        ix._judge_by_vector_score(asked, "Who planted the oldest tree in the Harlow Orchard?", "t", {})

        assert haiku[0]["vector_related"] is False
        assert asked[0]["vector_related"] is True

    def test_a_rare_word_holds_a_passage_below_the_floor(self, nomic, shared_words):
        shared_words["ids"] = {"orchard-notes.md"}
        hits = [_hit("orchard-notes.md", 0.466)]
        trace = {}

        ix._judge_by_vector_score(hits, "What colour is the ledger Tobias Wren uses?", "t", trace)

        assert hits[0]["vector_related"] is True
        assert trace["vector_floor"] == 0.60 and trace["vector_unrelated"] == 0

    def test_a_keyword_only_hit_is_judged_by_its_stored_vector(self, nomic, shared_words):
        shared_words["stored"] = {"close": 0.79, "far": 0.41}
        hits = [_hit("a.pdf", None, "close"), _hit("b.pdf", None, "far")]

        ix._judge_by_vector_score(hits, "q", "t", {})

        assert hits[0]["vector_score"] == 0.79 and hits[0]["vector_related"] is True
        assert hits[1]["vector_related"] is False

    def test_nothing_is_marked_when_the_words_cannot_be_checked(self, nomic, shared_words):
        shared_words["ids"] = None
        hits = [_hit("a.md", 0.3), _hit("b.md", 0.9)]

        ix._judge_by_vector_score(hits, "q", "t", {})

        assert all("vector_related" not in h for h in hits)

    @pytest.mark.parametrize("model,table", [("qwen3-embedding:4b", "t"), ("nomic-embed-text", None)])
    def test_nothing_is_marked_without_a_floor_or_a_table(self, monkeypatch, shared_words, model, table):
        monkeypatch.setattr(config, "get_active_embedding_model", lambda: model)
        hits = [_hit("a.md", 0.1)]

        ix._judge_by_vector_score(hits, "q", table, {})

        assert "vector_related" not in hits[0]

    def test_a_search_without_the_vector_leg_is_not_judged(self, nomic, shared_words):
        hits = [{"text": "t", "metadata": {"source_filename": "a.md"}, "node_id": "a"}]

        ix._judge_by_vector_score(hits, "q", "t", {})

        assert "vector_related" not in hits[0]


class TestDropUnrelated:
    def test_drops_passages_marked_unrelated(self):
        hits = [_hit("a.md", vector_related=True), _hit("b.md", vector_related=False)]

        kept, dropped = reranker.drop_unrelated(hits)

        assert [h["metadata"]["source_filename"] for h in kept] == ["a.md"] and dropped == 1

    def test_an_unrated_chunk_stays_only_beside_a_kept_passage_of_its_source(self):
        kept_src = _hit("a.md", vector_related=True)
        beside = {"text": "dep", "metadata": {"source_filename": "a.md"}, "node_id": "a-dep"}
        alone = {"text": "dep", "metadata": {"source_filename": "b.md"}, "node_id": "b-dep"}

        kept, dropped = reranker.drop_unrelated([kept_src, _hit("b.md", vector_related=False), beside, alone])

        assert kept == [kept_src, beside] and dropped == 2

    def test_unmarked_results_are_untouched(self):
        hits = [_hit("a.md", 0.2), _hit("b.md", 0.9)]
        assert reranker.drop_unrelated(hits) == (hits, 0)

    def test_a_reranker_score_wins_over_the_vector_mark(self, monkeypatch):
        monkeypatch.setattr(reranker, "model_name", lambda: "BAAI/bge-reranker-v2-m3")
        hits = [_hit("a.md", vector_related=False, rerank_score=0.9),
                _hit("b.md", vector_related=True, rerank_score=0.01)]

        kept, dropped = reranker.drop_unrelated(hits)

        assert [h["metadata"]["source_filename"] for h in kept] == ["a.md"] and dropped == 1

    def test_a_reranker_without_a_floor_keeps_everything(self, monkeypatch):
        monkeypatch.setattr(reranker, "model_name", lambda: "some/other-reranker")
        hits = [_hit("a.md", vector_related=False, rerank_score=0.01)]

        assert reranker.drop_unrelated(hits) == (hits, 0)
