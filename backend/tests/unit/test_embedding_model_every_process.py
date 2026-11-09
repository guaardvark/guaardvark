"""The embedding model chosen in Settings reaches every process.

Import-time configuration, Celery workers and scripts have no Flask app context.
They used to fall through to GUAARDVARK_EMBEDDING_MODEL or auto-selection, so a
worker indexed with a different model than the one chat searched with.
"""

import types

import pytest

import backend.config as cfg
import backend.services.indexing_service as isvc


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(cfg, "_saved_embed_cache", {"at": 0.0, "value": None})
    monkeypatch.setattr(isvc, "_embed_sync_gave_up", None)


def test_saved_choice_beats_env_outside_app(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_EMBEDDING_MODEL", "env-model")
    monkeypatch.setattr(cfg, "_saved_embedding_model_outside_app", lambda: "chosen-model")
    assert cfg.get_active_embedding_model() == "chosen-model"


def test_env_used_when_nothing_saved(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_EMBEDDING_MODEL", "env-model")
    monkeypatch.setattr(cfg, "_saved_embedding_model_outside_app", lambda: None)
    assert cfg.get_active_embedding_model() == "env-model"


def test_outside_app_read_is_cached(monkeypatch):
    calls = []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            calls.append(1)
            return types.SimpleNamespace(first=lambda: ("chosen-model",))

    monkeypatch.setattr(cfg, "_saved_embed_engine", types.SimpleNamespace(connect=lambda: _Conn()))
    assert cfg._saved_embedding_model_outside_app() == "chosen-model"
    assert cfg._saved_embedding_model_outside_app() == "chosen-model"
    assert len(calls) == 1


def _fake_settings(monkeypatch, name):
    import llama_index.core as core
    fake = types.SimpleNamespace(embed_model=types.SimpleNamespace(model_name=name))
    monkeypatch.setattr(core, "Settings", fake)
    return fake


def test_sync_rebuilds_a_stale_client(monkeypatch):
    fake = _fake_settings(monkeypatch, "old-model")
    new_client = types.SimpleNamespace(model_name="new-model")
    import backend.utils.llm_service as llm
    monkeypatch.setattr(llm, "get_default_embed_model", lambda: new_client)
    monkeypatch.setattr(isvc, "index", object())
    monkeypatch.setattr(isvc, "storage_context", object())
    isvc._sync_embed_model("new-model")
    assert fake.embed_model is new_client
    assert isvc.index is None and isvc.storage_context is None


def test_sync_is_a_no_op_when_current(monkeypatch):
    fake = _fake_settings(monkeypatch, "same-model")
    before = fake.embed_model
    import backend.utils.llm_service as llm
    import backend.services.embedding_prompts as ep
    monkeypatch.setattr(ep, "index_prompts", lambda model=None: (None, None))
    monkeypatch.setattr(llm, "get_default_embed_model", lambda: pytest.fail("must not rebuild"))
    isvc._sync_embed_model("same-model")
    assert fake.embed_model is before


def test_sync_rebuilds_when_the_index_prompts_changed(monkeypatch):
    """A re-index recorded the model's prompts; a worker holding an unprompted
    client must not go on embedding passages without them."""
    fake = _fake_settings(monkeypatch, "same-model")
    import backend.utils.llm_service as llm
    import backend.services.embedding_prompts as ep
    monkeypatch.setattr(ep, "index_prompts", lambda model=None: ("q:", "d:"))
    new_client = types.SimpleNamespace(model_name="same-model", query_instruction="q:", text_instruction="d:")
    monkeypatch.setattr(llm, "get_default_embed_model", lambda: new_client)
    monkeypatch.setattr(isvc, "index", object())
    monkeypatch.setattr(isvc, "storage_context", object())
    isvc._sync_embed_model("same-model")
    assert fake.embed_model is new_client
    assert isvc.index is None


def test_sync_gives_up_once_when_the_client_disagrees(monkeypatch):
    fake = _fake_settings(monkeypatch, "old-model")
    before = fake.embed_model
    built = []
    import backend.utils.llm_service as llm

    def _build():
        built.append(1)
        return types.SimpleNamespace(model_name="something-else")

    monkeypatch.setattr(llm, "get_default_embed_model", _build)
    isvc._sync_embed_model("new-model")
    isvc._sync_embed_model("new-model")
    assert fake.embed_model is before
    assert len(built) == 1
