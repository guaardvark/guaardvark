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


@pytest.fixture
def slow_plugin(world, monkeypatch):
    """A fake clock and a plugin whose embed calls time out after the timeout given."""
    calls, plugin, switch = world
    clock = {"t": 1000.0, "stalled": 0.0, "timeouts": []}
    monkeypatch.setattr(gp.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(ie.time, "monotonic", lambda: clock["t"])
    fast_post = requests.post

    def post(url, json=None, timeout=None, **k):
        if plugin.get("slow"):
            clock["t"] += timeout
            clock["stalled"] += timeout
            clock["timeouts"].append(timeout)
            raise requests.Timeout("read timed out")
        return fast_post(url, json=json, timeout=timeout, **k)
    monkeypatch.setattr(requests, "post", post)

    def ollama_embed(self, texts):
        calls["ollama"].extend(texts)
        clock["t"] += 0.5
        return [_vec(t) for t in texts]
    monkeypatch.setattr(OllamaEmbedding, "get_general_text_embeddings", ollama_embed)
    return calls, plugin, clock


def test_slow_plugin_backs_off_instead_of_stalling_indexing(slow_plugin):
    calls, plugin, clock = slow_plugin
    client = _client()
    client.get_text_embedding_batch([f"warm {i}" for i in range(10)])
    plugin["slow"] = True
    start, batches = clock["t"], 0
    while clock["t"] - start < 300:
        texts = [f"p{batches}-{i}" for i in range(10)]
        assert client.get_text_embedding_batch(texts) == [_vec(client._format_text(t)) for t in texts]
        batches += 1
    elapsed = clock["t"] - start
    # Backoff 30, 60, 120 s: three abandoned calls in 300 s, at most 10 s each.
    assert max(clock["timeouts"]) <= gp.CALL_TIMEOUT_S
    assert len(clock["timeouts"]) <= 4
    assert clock["stalled"] / elapsed < 0.15
    assert "retrying in" in er.get_embedding_router().plugin_gate().status()["reason"]


def test_failed_probe_is_not_retried_at_once(slow_plugin):
    calls, plugin, clock = slow_plugin
    plugin["slow"] = True
    client = _client()
    client.get_text_embedding_batch(["a"])
    assert clock["timeouts"] == [gp.PROBE_TIMEOUT_S]
    client.get_text_embedding_batch(["b"])
    client.get_text_embedding_batch(["c"])
    assert len(clock["timeouts"]) == 1  # skipped while backing off
    clock["t"] += gp.BACKOFF_BASE_S
    client.get_text_embedding_batch(["d"])
    assert len(clock["timeouts"]) == 2
    assert "retrying in 60 s" in er.get_embedding_router().plugin_gate().status()["reason"]


def test_recovers_after_the_backoff_and_resets_it(slow_plugin):
    calls, plugin, clock = slow_plugin
    plugin["slow"] = True
    client = _client()
    client.get_text_embedding_batch(["a"])
    plugin["slow"] = False
    clock["t"] += gp.BACKOFF_BASE_S
    client.get_text_embedding_batch(["b"])
    gate = er.get_embedding_router().plugin_gate()
    assert gate.status()["in_use"] is True and gate._failures == 0
    assert calls["plugin"][-1] == client._format_text("b")
