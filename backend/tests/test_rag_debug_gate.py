"""Tests for RAG debug endpoint gating."""
import pytest
from unittest.mock import patch, MagicMock


class TestRagDebugGate:
    """Test that RAG debug endpoints respect the toggle."""

    def test_debug_endpoint_returns_403_when_disabled(self):
        """When rag_debug_enabled is false, endpoints return 403."""
        from backend.api.rag_debug_api import require_rag_debug
        from flask import Flask, jsonify

        app = Flask(__name__)

        @app.route("/test")
        @require_rag_debug
        def test_endpoint():
            return jsonify({"data": "ok"})

        with app.test_client() as client:
            with patch("backend.api.rag_debug_api.get_setting", return_value=False):
                resp = client.get("/test")
                assert resp.status_code == 403

    def test_debug_endpoint_returns_200_when_enabled(self):
        """When rag_debug_enabled is true, endpoints work normally."""
        from backend.api.rag_debug_api import require_rag_debug
        from flask import Flask, jsonify

        app = Flask(__name__)

        @app.route("/test")
        @require_rag_debug
        def test_endpoint():
            return jsonify({"data": "ok"})

        with app.test_client() as client:
            with patch("backend.api.rag_debug_api.get_setting", return_value=True):
                resp = client.get("/test")
                assert resp.status_code == 200
