import os

import pytest

from backend.utils import index_manager


@pytest.mark.indexing
def test_per_project_index_creation_and_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_INDEX_ROOT", str(tmp_path / "indices"))
    monkeypatch.setenv("GUAARDVARK_PROJECT_INDEX_MODE", "per_project")
    index_manager.clear_indexes()

    idx_a = index_manager.get_or_create_index("A")
    idx_b = index_manager.get_or_create_index("B")

    assert os.path.isdir(tmp_path / "indices" / "A")
    assert os.path.isdir(tmp_path / "indices" / "B")

    idx_a.add_document("doc1", "alpha text")
    idx_b.add_document("doc2", "beta text")

    assert "doc1" in idx_a.search("alpha")
    assert "doc1" not in idx_b.search("alpha")


def test_global_index_shared(tmp_path, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_INDEX_ROOT", str(tmp_path / "indices"))
    monkeypatch.setenv("GUAARDVARK_PROJECT_INDEX_MODE", "global")
    index_manager.clear_indexes()

    idx_a = index_manager.get_or_create_index("A")
    idx_b = index_manager.get_or_create_index("B")

    expected_path = tmp_path / "indices"
    assert os.path.isdir(expected_path)
    assert idx_a is idx_b

    idx_a.add_document("doc1", "shared text")
    assert "doc1" in idx_b.search("shared")
