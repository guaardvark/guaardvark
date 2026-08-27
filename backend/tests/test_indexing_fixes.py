"""Regression tests for the 2026-06-03 indexing-pipeline fixes.

Covers: the scoped_session.in_transaction() guard (the blocker that aborted all
HTTP indexing), the add_text_to_index empty-vs-failure contract, and the Purge
endpoint no-longer-a-placebo input validation. Each guard exercises its negative case.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from backend.services import indexing_service as ix


# --------------------------------------------------------------------------
# #1 — _session_in_transaction() must return a bool and NEVER raise, even when
# the scoped_session doesn't expose in_transaction (SQLAlchemy 2.0).
# --------------------------------------------------------------------------
def test_session_in_transaction_true_via_real_session(monkeypatch):
    real_session = SimpleNamespace(in_transaction=lambda: True)
    fake_db = SimpleNamespace(session=lambda: real_session)  # scoped_session is callable
    monkeypatch.setattr(ix, "db", fake_db)
    assert ix._session_in_transaction() is True


def test_session_in_transaction_false_via_real_session(monkeypatch):
    fake_db = SimpleNamespace(session=lambda: SimpleNamespace(in_transaction=lambda: False))
    monkeypatch.setattr(ix, "db", fake_db)
    assert ix._session_in_transaction() is False


def test_session_in_transaction_never_raises_on_broken_session(monkeypatch):
    # The exact production failure: scoped_session has no in_transaction and calling
    # it blows up. The guard must swallow and return False, not AttributeError.
    def _boom():
        raise AttributeError("'scoped_session' object has no attribute 'in_transaction'")
    fake_db = SimpleNamespace(session=_boom)
    monkeypatch.setattr(ix, "db", fake_db)
    assert ix._session_in_transaction() is False


# --------------------------------------------------------------------------
# #5 — add_text_to_index distinguishes empty (None) from failure (False).
# --------------------------------------------------------------------------
def _fake_chunker(nodes):
    """Stand in for the process-wide chunker.

    Patches `get_shared_chunker`, not `EnhancedRAGChunker`. The class is only
    constructed on the first call and cached in a module global, so patching it
    does nothing once any earlier test has warmed the singleton — these two
    tests passed alone and failed after `test_rag_rearchitecture.py`, which is
    an order dependency, not a real result. `get_shared_chunker` is the seam
    `add_text_to_index` actually calls.
    """
    inst = MagicMock()
    inst.chunk_documents.return_value = nodes
    return lambda: inst


def test_add_text_to_index_returns_none_when_no_nodes(monkeypatch):
    monkeypatch.setattr(ix, "index", MagicMock())
    monkeypatch.setattr(ix, "storage_context", MagicMock())
    monkeypatch.setattr(ix, "_lazy_load_llamaindex", lambda: None)
    monkeypatch.setattr(ix, "LlamaDocument", lambda **kw: object())
    with patch("backend.utils.enhanced_rag_chunking.get_shared_chunker", _fake_chunker([])):
        assert ix.add_text_to_index("anything", metadata={}) is None  # empty, not False


def test_add_text_to_index_returns_none_when_all_nodes_empty(monkeypatch):
    monkeypatch.setattr(ix, "index", MagicMock())
    monkeypatch.setattr(ix, "storage_context", MagicMock())
    monkeypatch.setattr(ix, "_lazy_load_llamaindex", lambda: None)
    monkeypatch.setattr(ix, "LlamaDocument", lambda **kw: object())
    empty_node = SimpleNamespace(text="", metadata={})  # has attrs but empty text -> invalid
    with patch("backend.utils.enhanced_rag_chunking.get_shared_chunker", _fake_chunker([empty_node])):
        assert ix.add_text_to_index("anything", metadata={}) is None


def test_add_text_to_index_returns_false_when_no_index(monkeypatch):
    monkeypatch.setattr(ix, "index", None)
    monkeypatch.setattr(ix, "get_or_create_index", lambda *a, **k: None)  # stays None
    assert ix.add_text_to_index("x", metadata={}) is False  # real failure


# --------------------------------------------------------------------------
# #6 — Purge is no longer a placebo: it rejects an empty selection up front.
# --------------------------------------------------------------------------
@pytest.fixture()
def purge_client():
    from backend.api.index_mgmt_api import index_mgmt_bp
    app = Flask(__name__)
    app.register_blueprint(index_mgmt_bp, url_prefix="/api/meta")
    return app.test_client()


def test_purge_requires_a_selection(purge_client):
    resp = purge_client.post("/api/meta/purge-index", json={})
    assert resp.status_code == 400
    assert "Select at least one" in resp.get_json()["error"]


def test_purge_no_longer_returns_placebo_acknowledged(purge_client, monkeypatch):
    # The old placebo returned 200 "acknowledged" for any input. With a real
    # selection it must do real work (or fail honestly), never the placebo 200.
    #
    # The endpoint falls back to initialize_llama_index_from_service() whenever the
    # app config has no index -- which is exactly this bare test app. Unpatched, that
    # loads the REAL index and purges it: this test used to delete live indexed data
    # as a side effect (measured: 417 -> 63 vectors). Hand it a stand-in so the
    # endpoint's contract is exercised against nothing real.
    from backend.api import index_mgmt_api as mod

    fake_index = MagicMock()
    fake_index.storage_context = MagicMock()
    fake_index.storage_context.docstore.docs = {}
    monkeypatch.setattr(mod, "initialize_llama_index_from_service", lambda: fake_index)

    resp = purge_client.post("/api/meta/purge-index", json={"purgeDocuments": True})
    body = resp.get_json()
    assert not (resp.status_code == 200 and body.get("message", "").startswith("Purge request acknowledged"))
