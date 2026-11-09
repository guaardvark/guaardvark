"""The GPU Embedding plugin embeds for the index only when its vectors match the router's own."""

import json

import pytest

import backend.utils.gpu_embedding_plugin as gp


@pytest.fixture
def state(tmp_path, monkeypatch):
    path = tmp_path / "plugin_state.json"
    monkeypatch.setattr(gp, "_STATE_FILE", path)
    monkeypatch.setattr(gp, "ENABLED_TTL_S", 0)

    def set_enabled(on):
        path.write_text(json.dumps({"version": 2, "user_enabled": {"gpu_embedding": on}, "running": []}))
    return set_enabled


def test_identical_vectors_match():
    v = [0.1, 0.2, 0.3]
    assert gp.compare_vectors(v, list(v)) is None


@pytest.mark.parametrize("other, word", [
    ([0.1, 0.2], "dimension"),
    ([0.2, 0.4, 0.6], "norm"),
    ([0.3, 0.2, 0.1], "cosine"),
])
def test_different_vectors_are_refused(other, word):
    assert word in gp.compare_vectors(other, [0.1, 0.2, 0.3])


def test_off_by_default_without_a_toggle(state, monkeypatch):
    monkeypatch.setattr(gp, "_STATE_FILE", gp._STATE_FILE.with_name("missing.json"))
    assert gp.PluginEmbeddingGate().enabled() is False


def test_off_never_contacts_the_plugin(state, monkeypatch):
    state(False)
    called = []
    monkeypatch.setattr("requests.get", lambda *a, **k: called.append(a))
    gate = gp.PluginEmbeddingGate()
    assert gate.usable("m:latest", lambda t: [[1.0]], str) is False
    assert called == []


def test_model_mismatch_falls_back_with_reason(state, monkeypatch):
    state(True)

    class R:
        ok = True

        def json(self):
            return {"current_model": "other:latest", "loaded": True}
    monkeypatch.setattr("requests.get", lambda *a, **k: R())
    monkeypatch.setattr(gp, "_same_model", lambda a, b: a == b)
    gate = gp.PluginEmbeddingGate()
    assert gate.usable("mine:latest", lambda t: [[1.0]], str) is False
    assert "other:latest" in gate.status()["reason"]
