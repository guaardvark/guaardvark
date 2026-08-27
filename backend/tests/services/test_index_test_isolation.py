"""Vector-table isolation between test runs and the real index.

The ORM gets its own database under test, so `Document.id` restarts at 1 while
the vector store resolves `DATABASE_URL` from `backend.config` directly. Since
`add_file_to_index` purges by document id before inserting, an unscoped test run
deletes the real documents 1, 2, 3 — measured once as 316 nodes down to 107.
"""
from __future__ import annotations

import backend.services.indexing_service as isvc


def test_prefix_applies_while_running_under_pytest():
    assert isvc._test_table_prefix() == "test_"


def test_table_name_is_scoped_away_from_production():
    name = isvc._pg_table_name()
    if name is None:
        return  # no embedding dimension resolvable in this environment
    assert name.startswith("guaardvark_test_"), name


def test_prefix_is_empty_without_either_signal(monkeypatch):
    """The prefix must not be unconditional.

    If it applied outside tests, production would quietly read and write a table
    that holds nothing — the same class of silent wrong-store failure this guard
    exists to prevent.
    """
    monkeypatch.delenv("GUAARDVARK_MODE", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert isvc._test_table_prefix() == ""


def test_mode_env_alone_is_enough(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("GUAARDVARK_MODE", "test")
    assert isvc._test_table_prefix() == "test_"
