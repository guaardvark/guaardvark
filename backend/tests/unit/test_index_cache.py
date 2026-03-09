import types

import pytest
from tests.helpers import make_mock_llm

from backend.utils import index_manager as im


class DummyIndex:
    @classmethod
    def from_documents(cls, docs, storage_context=None):
        return cls()


class DummyStorageContext:
    def __init__(self, persist_dir=None):
        self.persist_dir = persist_dir

    @classmethod
    def from_defaults(cls, docstore=None, index_store=None, persist_dir=None):
        import os

        os.makedirs(persist_dir, exist_ok=True)
        return cls(persist_dir)

    def persist(self, persist_dir=None):
        import os

        if persist_dir:
            os.makedirs(persist_dir, exist_ok=True)
            self.persist_dir = persist_dir


def dummy_load(ctx):
    return DummyIndex()


@pytest.fixture
def stub_index_manager(monkeypatch):
    monkeypatch.setattr(im, "VectorStoreIndex", DummyIndex, raising=False)
    monkeypatch.setattr(im, "StorageContext", DummyStorageContext, raising=False)
    monkeypatch.setattr(im, "load_index_from_storage", dummy_load, raising=False)
    monkeypatch.setattr(
        im,
        "Settings",
        types.SimpleNamespace(llm=object(), embed_model=object()),
        raising=False,
    )
    im._index_cache = {}
    yield
    im._index_cache = {}


@pytest.mark.indexing
def test_separate_indexes(tmp_path, stub_index_manager, monkeypatch):
    monkeypatch.setattr(im, "INDEX_ROOT", str(tmp_path / "project_index"))
    monkeypatch.setattr(im, "PROJECT_INDEX_MODE", "per_project")
    idx1 = im.get_or_create_index("p1")
    idx2 = im.get_or_create_index("p2")
    assert idx1 is not idx2
    path1 = str(tmp_path / "project_index" / "p1")
    path2 = str(tmp_path / "project_index" / "p2")
    assert (tmp_path / "project_index" / "p1").exists()
    assert (tmp_path / "project_index" / "p2").exists()
    assert set(im._index_cache.keys()) == {path1, path2}


@pytest.mark.indexing
def test_cache_reuse(tmp_path, stub_index_manager, monkeypatch):
    monkeypatch.setattr(im, "INDEX_ROOT", str(tmp_path / "project_index"))
    monkeypatch.setattr(im, "PROJECT_INDEX_MODE", "per_project")
    idx1 = im.get_or_create_index("p1")
    idx2 = im.get_or_create_index("p1")
    assert idx1 is idx2
    expected_path = str(tmp_path / "project_index" / "p1")
    assert list(im._index_cache.keys()) == [expected_path]
