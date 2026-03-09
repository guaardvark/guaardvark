import os

import pytest

try:
    from backend.utils import csv_chunker
except Exception:
    pytest.skip("Required modules not available", allow_module_level=True)


class DummyDoc:
    def __init__(self, id_, text, metadata):
        self.id_ = id_
        self.text = text
        self.metadata = metadata


def test_parse_csv_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(csv_chunker, "Document", DummyDoc, raising=False)
    csv_file = tmp_path / "example.csv"
    csv_file.write_text("A,B\n1,2\n3,4\n", encoding="utf-8")

    docs = csv_chunker.parse_csv_rows(str(csv_file))
    assert len(docs) == 3
    # First doc is a summary/statistics node
    assert "Filename:" in docs[0].text and "Sample rows:" in docs[0].text
    # Next docs are row chunks
    assert docs[1].text.startswith("A: 1") or docs[2].text.startswith("A: 1")
