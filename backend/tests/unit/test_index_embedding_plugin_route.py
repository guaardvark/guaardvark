"""The index's embedding client sends passages to the GPU Embedding plugin only
when the gate allows it, and otherwise embeds exactly as OllamaEmbedding does."""

import hashlib
import json

import pytest
import requests
from llama_index.core import VectorStoreIndex
from llama_index.core.schema import TextNode
from llama_index.embeddings.ollama import OllamaEmbedding

import backend.utils.embedding_router as er
import backend.utils.gpu_embedding_plugin as gp
import backend.utils.index_embedding as ie

MODEL = "fake-embed:latest"
DIM = 8


def _vec(text, salt=""):
    h = hashlib.sha256((salt + text).encode()).digest()
    return [b / 255.0 + 0.01 for b in h[:DIM]]


class _Resp:
    def __init__(self, body, ok=True, code=200):
        self._body, self.ok, self.status_code = body, ok, code

    def json(self):
        return self._body


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Stub Ollama and the plugin's endpoints; a fresh router and gate."""
    calls = {"ollama": [], "plugin": [], "gets": 0}
    plugin = {"model": MODEL, "salt": "", "dim": DIM, "fail": False}

    def ollama_embed(self, texts):
        calls["ollama"].extend(texts)
        return [_vec(t) for t in texts]
    monkeypatch.setattr(OllamaEmbedding, "get_general_text_embeddings", ollama_embed)
    monkeypatch.setattr(OllamaEmbedding, "get_general_text_embedding",
                        lambda self, t: ollama_embed(self, [t])[0])

    def fake_get(url, **k):
        assert url.startswith("http://127.0.0.1:8204/"), url
        calls["gets"] += 1
        return _Resp({"current_model": plugin["model"], "loaded": True, "embed_dim": plugin["dim"]})

    def fake_post(url, json=None, **k):
        assert url.startswith("http://127.0.0.1:8204/"), url
        if plugin["fail"]:
            return _Resp({"error": "boom"}, ok=False, code=500)
        texts = json["texts"] if "texts" in json else [json["text"]]
        calls["plugin"].extend(texts)
        vs = [_vec(t, plugin["salt"])[:plugin["dim"]] for t in texts]
        body = {"embeddings": vs} if "texts" in json else {"embedding": vs[0]}
        body["model"] = plugin["model"]
        return _Resp(body)
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)

    state = tmp_path / "plugin_state.json"
    monkeypatch.setattr(gp, "_STATE_FILE", state)
    monkeypatch.setattr(gp, "ENABLED_TTL_S", 0)
    monkeypatch.setattr(gp, "_same_model", lambda a, b: bool(a) and a == b)
    monkeypatch.setattr(ie, "_ENABLED_TTL_S", 0)
    monkeypatch.setattr(er.EmbeddingRouter, "_instance", None)
    monkeypatch.setattr(er, "_embedding_router", None)

    def switch(on):
        state.write_text(json.dumps({"version": 2, "user_enabled": {"gpu_embedding": on}, "running": []}))
    switch(True)
    return calls, plugin, switch


def _client():
    return ie.IndexOllamaEmbedding(model_name=MODEL, text_instruction="passage:", query_instruction="query:")


def _insert(client, n=5):
    index = VectorStoreIndex([], embed_model=client)
    nodes = [TextNode(text=f"passage {i} about rivers") for i in range(n)]
    index.insert_nodes(nodes)
    return [index.vector_store.get(x.node_id) for x in nodes], nodes


def _expected(client, nodes):
    return [_vec(client._format_text(x.text)) for x in nodes]


def test_off_never_contacts_the_plugin_or_builds_the_router(world, monkeypatch):
    calls, _, switch = world
    switch(False)
    monkeypatch.setattr(er, "get_embedding_router", lambda: pytest.fail("router built while off"))
    client = _client()
    stored, nodes = _insert(client)
    assert stored == _expected(client, nodes)
    assert calls["gets"] == 0 and calls["plugin"] == []


def test_on_and_matching_passages_go_through_the_plugin(world):
    calls, _, _ = world
    client = _client()
    stored, nodes = _insert(client)
    assert stored == _expected(client, nodes)
    # Only the one probe text went to Ollama; every passage went to the plugin.
    assert calls["ollama"] == [client._format_text(gp.PROBE_TEXT)]
    assert calls["plugin"][1:] == [client._format_text(x.text) for x in nodes]
    router = er.get_embedding_router()
    assert router.path_counts().get("gpu_embedding_plugin") == len(nodes)
    assert router.plugin_gate().status()["in_use"] is True


def test_queries_never_go_through_the_plugin(world):
    calls, _, _ = world
    client = _client()
    q = client.get_query_embedding("rivers")
    assert q == _vec(client._format_query("rivers"))
    assert calls["plugin"] == [] and calls["gets"] == 0


def test_other_model_falls_back_to_ollama(world):
    calls, plugin, _ = world
    plugin["model"] = "other-embed:latest"
    client = _client()
    stored, nodes = _insert(client)
    assert stored == _expected(client, nodes)
    assert calls["plugin"] == []
    assert "other-embed:latest" in er.get_embedding_router().plugin_gate().status()["reason"]


def test_same_name_different_vectors_falls_back(world):
    calls, plugin, _ = world
    plugin["salt"] = "another quantisation"
    client = _client()
    stored, nodes = _insert(client)
    assert stored == _expected(client, nodes)
    assert calls["plugin"] == [client._format_text(gp.PROBE_TEXT)]  # the probe only
    assert "differ" in er.get_embedding_router().plugin_gate().status()["reason"]


def test_plugin_error_mid_index_falls_back_for_that_batch(world):
    calls, plugin, _ = world
    client = _client()
    _insert(client, n=2)  # verified and in use
    plugin["fail"] = True
    stored, nodes = _insert(client, n=3)
    assert stored == _expected(client, nodes)
    assert er.get_embedding_router().path_counts().get("ollama") == 3
    assert er.get_embedding_router().plugin_gate().status()["in_use"] is False


def test_vectors_of_another_width_are_refused(world):
    calls, plugin, _ = world
    client = _client()
    _insert(client, n=1)
    plugin["dim"] = DIM - 2
    stored, nodes = _insert(client, n=2)
    assert stored == _expected(client, nodes)
    assert all(len(v) == DIM for v in stored)


@pytest.mark.asyncio
async def test_async_batch_takes_the_same_route(world):
    calls, _, _ = world
    client = _client()
    out = await client.aget_text_embedding_batch(["a river", "a lake"])
    assert out == [_vec(client._format_text(t)) for t in ["a river", "a lake"]]
    assert calls["plugin"][1:] == [client._format_text(t) for t in ["a river", "a lake"]]
