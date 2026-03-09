import importlib
import json
import os

import pytest

try:
    from backend.services import metadata_service
except Exception:
    pytest.skip("backend services not available", allow_module_level=True)


def test_metadata_created_when_missing(tmp_path, monkeypatch):
    meta_file = tmp_path / "storage" / "docstore.json"
    monkeypatch.setattr(metadata_service, "META_PATH", str(meta_file), raising=False)
    # ensure starting state
    if meta_file.exists():
        meta_file.unlink()
    metadata_service.metadata_map = {}

    metadata_service.load_metadata()

    assert meta_file.exists()
    with open(meta_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data == {}
    assert metadata_service.metadata_map == {}
