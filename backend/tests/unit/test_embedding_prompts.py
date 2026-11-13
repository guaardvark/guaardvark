"""Which prompts an index embeds with: the declared table and the per-index record."""

import json

import pytest

import backend.config as cfg
import backend.services.embedding_prompts as ep


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_INDEX_ROOT", str(tmp_path))
    monkeypatch.setattr(ep, "_cache", {"key": None, "value": ep.NO_PROMPTS, "recorded": False, "at": 0.0})
    return tmp_path


def _rows(monkeypatch, value):
    monkeypatch.setattr(ep, "_vector_rows_exist", lambda: value)


@pytest.mark.parametrize("model,expected", [
    ("embeddinggemma-2:latest", ("task: search result | query:", "title: none | text:")),
    ("nomic-embed-text:latest", ("search_query:", "search_document:")),
    ("bge-m3", (None, None)),
    ("bge-large-en-v1.5", ("Represent this sentence for searching relevant passages:", None)),
    ("snowflake-arctic-embed2", ("query:", None)),
    ("all-minilm", (None, None)),
])
def test_declared_prompts_match_on_the_longest_key(model, expected):
    assert cfg.get_embedding_prompts(model) == expected


def test_a_new_index_uses_prompts_only_where_measured():
    assert cfg.get_embedding_prompts("embeddinggemma:latest", for_index=True)[1] == "title: none | text:"
    assert cfg.get_embedding_prompts("nomic-embed-text", for_index=True) == (None, None)
    assert cfg.get_embedding_prompts("unknown-model", for_index=True) == (None, None)


def test_an_old_index_without_a_record_stays_unprompted(root, monkeypatch):
    _rows(monkeypatch, True)
    assert ep.index_prompts("embeddinggemma-2:latest") == (None, None)
    assert not (root / "embedding_meta.json").exists()


def test_an_index_that_cannot_be_read_stays_unprompted(root, monkeypatch):
    _rows(monkeypatch, None)
    assert ep.index_prompts("embeddinggemma-2:latest") == (None, None)


def test_a_new_index_records_the_model_prompts(root, monkeypatch):
    _rows(monkeypatch, False)
    assert ep.index_prompts("embeddinggemma-2:latest") == ("task: search result | query:", "title: none | text:")
    rec = json.loads((root / "embedding_meta.json").read_text())["prompts"]
    assert rec["model"] == "embeddinggemma-2:latest" and rec["document"] == "title: none | text:"


def test_the_record_wins_over_the_tables(root, monkeypatch):
    ep.write_meta({"prompts": {"model": "embeddinggemma-2:latest", "query": "q:", "document": "d:"}})
    _rows(monkeypatch, True)
    assert ep.index_prompts("embeddinggemma-2:latest") == ("q:", "d:")


def test_a_record_for_another_model_is_not_used(root, monkeypatch):
    ep.write_meta({"prompts": {"model": "nomic-embed-text:latest", "query": "search_query:",
                               "document": "search_document:"}})
    _rows(monkeypatch, True)
    monkeypatch.setattr("backend.utils.ollama_resource_manager.same_model", lambda a, b, tags=None: False)
    assert ep.recorded_prompts("embeddinggemma-2:latest") is None
    assert ep.index_prompts("embeddinggemma-2:latest") == (None, None)


def test_stamping_the_model_keeps_the_prompt_record(root, monkeypatch):
    from backend.services import indexing_service as ix
    monkeypatch.setenv("GUAARDVARK_PROJECT_INDEX_MODE", "single")
    ep.write_meta({"prompts": {"model": "m", "query": "q:", "document": "d:"}})
    monkeypatch.setattr(cfg, "get_active_embedding_model", lambda: "m")
    ix._record_index_embedding_model()
    meta = json.loads((root / "embedding_meta.json").read_text())
    assert meta["embedding_model"] == "m" and meta["prompts"]["document"] == "d:"


def test_reindex_is_suggested_only_for_a_missing_document_prompt(root, monkeypatch):
    _rows(monkeypatch, True)
    assert ep.prompt_status("embeddinggemma-2:latest")["reindex_suggested"] is True
    assert ep.prompt_status("nomic-embed-text:latest")["reindex_suggested"] is False
    ep.record_declared_prompts("embeddinggemma-2:latest")
    status = ep.prompt_status("embeddinggemma-2:latest")
    assert status["reindex_suggested"] is False and status["index"]["recorded"] is True
