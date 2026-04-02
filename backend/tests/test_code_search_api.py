import pytest
import json


class TestParseSymbolsFromMetadata:
    """Test helper functions without needing Flask/DB."""

    def test_parse_symbols_from_metadata(self):
        from backend.api.code_search_api import _parse_symbols_from_metadata
        metadata_json = json.dumps({
            "symbols": [
                {"name": "foo", "type": "function", "line": 1},
                {"name": "Bar", "type": "class", "line": 10},
            ],
            "language": "python",
        })
        symbols = _parse_symbols_from_metadata(metadata_json, "foo")
        assert len(symbols) == 1
        assert symbols[0]["name"] == "foo"

    def test_parse_symbols_case_insensitive(self):
        from backend.api.code_search_api import _parse_symbols_from_metadata
        metadata_json = json.dumps({
            "symbols": [{"name": "HandleUpload", "type": "function", "line": 5}],
        })
        symbols = _parse_symbols_from_metadata(metadata_json, "handleupload")
        assert len(symbols) == 1

    def test_parse_symbols_returns_empty_for_none(self):
        from backend.api.code_search_api import _parse_symbols_from_metadata
        assert _parse_symbols_from_metadata(None, "foo") == []

    def test_parse_symbols_returns_empty_for_invalid_json(self):
        from backend.api.code_search_api import _parse_symbols_from_metadata
        assert _parse_symbols_from_metadata("not json", "foo") == []

    def test_parse_symbols_partial_match(self):
        from backend.api.code_search_api import _parse_symbols_from_metadata
        metadata_json = json.dumps({
            "symbols": [
                {"name": "handleFileUpload", "type": "function", "line": 10},
                {"name": "handleClick", "type": "function", "line": 20},
            ],
        })
        symbols = _parse_symbols_from_metadata(metadata_json, "handle")
        assert len(symbols) == 2


class TestGetSubfolderIds:

    def test_function_exists(self):
        from backend.api.code_search_api import _get_subfolder_ids
        assert callable(_get_subfolder_ids)
