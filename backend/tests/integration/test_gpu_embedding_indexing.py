#!/usr/bin/env python3
"""
Integration tests for GPU Embedding with Document Indexing.

Tests the complete flow:
- Plugin configuration check
- GPU embedding service integration
- Document indexing with GPU embeddings
- Search with GPU-generated embeddings

IMPORTANT: These tests require Ollama to be running.
For full GPU tests, also run the GPU embedding service.
"""

import os
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, Mock

# Skip if dependencies not available
try:
    from flask import Flask
    from backend.models import db, Document as DBDocument
    from backend.services import indexing_service
    from backend import config
    from backend.utils.llama_index_local_config import (
        force_local_llama_index_config,
    )

    # _try_gpu_embedding_plugin removed — GPU embedding plugin deprecated
    _try_gpu_embedding_plugin = None
except ImportError as e:
    pytest.skip(f"Backend modules not available: {e}", allow_module_level=True)


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Create Flask app with test configuration."""
    # Configure temporary paths
    monkeypatch.setenv("GUAARDVARK_INDEX_ROOT", str(tmp_path / "indices"))
    monkeypatch.setenv("GUAARDVARK_PROJECT_INDEX_MODE", "global")
    monkeypatch.setattr(config, "INDEX_ROOT", str(tmp_path / "indices"))
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path / "uploads"))

    # Create directories
    os.makedirs(tmp_path / "indices", exist_ok=True)
    os.makedirs(tmp_path / "storage", exist_ok=True)
    os.makedirs(tmp_path / "uploads", exist_ok=True)

    # Create Flask app
    app = Flask(__name__)
    app.config.update(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "INDEX_ROOT": str(tmp_path / "indices"),
            "STORAGE_DIR": str(tmp_path / "storage"),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
        }
    )

    # Initialize database
    db.init_app(app)

    with app.app_context():
        db.create_all()

        # Clear any cached indexes
        if hasattr(indexing_service, "_index_cache"):
            indexing_service._index_cache = {}

        yield app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


class TestGPUPluginConfiguration:
    """Tests for GPU plugin configuration checks."""

    def test_is_gpu_embedding_plugin_enabled_false(self, tmp_path, monkeypatch):
        """Test plugin detection when disabled."""
        # Create a mock plugins directory with disabled plugin
        plugins_dir = tmp_path / "plugins" / "gpu-embedding"
        plugins_dir.mkdir(parents=True)

        plugin_json = {"id": "gpu-embedding", "config": {"enabled": False}}

        with open(plugins_dir / "plugin.json", "w") as f:
            json.dump(plugin_json, f)

        monkeypatch.setattr(config, "GUAARDVARK_ROOT", tmp_path)

        result = config.is_gpu_embedding_plugin_enabled()

        assert result == False

    def test_is_gpu_embedding_plugin_enabled_true(self, tmp_path, monkeypatch):
        """Test plugin detection when enabled."""
        # Create a mock plugins directory with enabled plugin
        plugins_dir = tmp_path / "plugins" / "gpu-embedding"
        plugins_dir.mkdir(parents=True)

        plugin_json = {"id": "gpu-embedding", "config": {"enabled": True}}

        with open(plugins_dir / "plugin.json", "w") as f:
            json.dump(plugin_json, f)

        monkeypatch.setattr(config, "GUAARDVARK_ROOT", tmp_path)

        result = config.is_gpu_embedding_plugin_enabled()

        assert result == True

    def test_is_gpu_embedding_plugin_missing(self, tmp_path, monkeypatch):
        """Test plugin detection when plugin directory doesn't exist."""
        monkeypatch.setattr(config, "GUAARDVARK_ROOT", tmp_path)

        result = config.is_gpu_embedding_plugin_enabled()

        assert result == False

    @patch("backend.config.requests.get")
    def test_is_gpu_embedding_service_available(self, mock_get, tmp_path, monkeypatch):
        """Test service availability check."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "healthy", "model_loaded": True}
        mock_get.return_value = mock_response

        result = config.is_gpu_embedding_service_available()

        assert result == True

    @patch("backend.config.requests.get")
    def test_is_gpu_embedding_service_unavailable(self, mock_get):
        """Test service unavailability detection."""
        mock_get.side_effect = Exception("Connection refused")

        result = config.is_gpu_embedding_service_available()

        assert result == False


@pytest.mark.skip(
    reason="GPU embedding plugin deprecated — VRAM-aware selection in config.py"
)
class TestGPUEmbeddingPluginIntegration:
    """Tests for GPU embedding plugin integration with LlamaIndex config."""

    @patch("backend.config.is_gpu_embedding_plugin_enabled")
    def test_try_gpu_embedding_plugin_disabled(self, mock_enabled):
        """Test that plugin is not used when disabled."""
        mock_enabled.return_value = False

        result = _try_gpu_embedding_plugin()

        assert result is None

    @patch("backend.config.is_gpu_embedding_plugin_enabled")
    @patch("backend.config.is_gpu_embedding_service_available")
    def test_try_gpu_embedding_plugin_service_unavailable(
        self, mock_available, mock_enabled
    ):
        """Test fallback when service is enabled but not running."""
        mock_enabled.return_value = True
        mock_available.return_value = False

        result = _try_gpu_embedding_plugin()

        assert result is None

    @patch("backend.config.is_gpu_embedding_plugin_enabled")
    @patch("backend.config.is_gpu_embedding_service_available")
    @patch("plugins.gpu_embedding.client.embedding_adapter.GPUEmbeddingAdapter")
    def test_try_gpu_embedding_plugin_success(
        self, mock_adapter, mock_available, mock_enabled
    ):
        """Test successful GPU embedding plugin initialization."""
        mock_enabled.return_value = True
        mock_available.return_value = True

        mock_adapter_instance = MagicMock()
        mock_adapter_instance.is_gpu_available.return_value = True
        mock_adapter.return_value = mock_adapter_instance

        result = _try_gpu_embedding_plugin()

        assert result is not None


@pytest.mark.skip(
    reason="GPU embedding plugin deprecated — VRAM-aware selection in config.py"
)
class TestIndexingWithGPUEmbeddings:
    """Tests for document indexing with GPU embeddings."""

    @patch("backend.utils.llama_index_local_config._try_gpu_embedding_plugin")
    def test_indexing_with_gpu_plugin(self, mock_gpu_plugin, app, tmp_path):
        """Test document indexing when GPU plugin is available."""
        # Mock GPU embedding model
        mock_embed_model = MagicMock()
        mock_embed_model.get_text_embedding.return_value = [0.1] * 768
        mock_embed_model.embed_dim = 768
        mock_gpu_plugin.return_value = mock_embed_model

        with app.app_context():
            # Create test document
            test_content = "Test document for GPU embedding indexing."
            test_file = tmp_path / "gpu_test.txt"
            test_file.write_text(test_content)

            doc = DBDocument(
                filename="gpu_test.txt",
                path=str(test_file),
                type="txt",
                content=test_content,
                index_status="PENDING",
            )
            db.session.add(doc)
            db.session.commit()

            # This test verifies the integration point exists
            # Actual indexing would require full service setup
            assert doc.id is not None

    @patch("backend.utils.llama_index_local_config._try_gpu_embedding_plugin")
    def test_indexing_fallback_when_gpu_unavailable(
        self, mock_gpu_plugin, app, tmp_path
    ):
        """Test that indexing falls back when GPU plugin is unavailable."""
        mock_gpu_plugin.return_value = None  # GPU plugin not available

        with app.app_context():
            # Create test document
            test_content = "Test document for fallback indexing."
            test_file = tmp_path / "fallback_test.txt"
            test_file.write_text(test_content)

            doc = DBDocument(
                filename="fallback_test.txt",
                path=str(test_file),
                type="txt",
                content=test_content,
                index_status="PENDING",
            )
            db.session.add(doc)
            db.session.commit()

            # Verify document created (actual indexing requires Ollama)
            assert doc.id is not None
            assert doc.index_status == "PENDING"


@pytest.mark.skip(
    reason="GPU embedding plugin deprecated — VRAM-aware selection in config.py"
)
class TestEmbeddingPriorityOrder:
    """Tests for embedding model priority order."""

    @patch("backend.utils.llama_index_local_config._try_gpu_embedding_plugin")
    def test_gpu_plugin_priority(self, mock_gpu_plugin):
        """Test that GPU plugin is tried first."""
        mock_embed = MagicMock()
        mock_gpu_plugin.return_value = mock_embed

        # GPU plugin should be checked first in the priority order
        result = _try_gpu_embedding_plugin()

        mock_gpu_plugin.assert_called_once()

    def test_priority_order_documentation(self):
        """Verify the documented priority order is correct."""
        # Priority: GPU Plugin > Qwen3 > nomic-embed-text > all-minilm > simple hash
        # This test documents the expected behavior
        priority_order = [
            "GPU Plugin (if enabled and available)",
            "Qwen3 embedding models (hardware-aware)",
            "nomic-embed-text (768-dim, high quality)",
            "all-minilm (384-dim, fast)",
            "Simple hash-based embedding (fallback)",
        ]

        assert len(priority_order) == 5


class TestGPUEmbeddingPerformance:
    """Tests for GPU embedding performance characteristics."""

    @patch("plugins.gpu_embedding.client.gpu_embedding_client.GPUEmbeddingClient")
    def test_batch_vs_single_embedding(self, mock_client_class):
        """Test that batch processing is available."""
        mock_client = MagicMock()
        mock_client.generate_embedding.return_value = {"embedding": [0.1] * 768}
        mock_client.generate_embeddings_batch.return_value = {
            "embeddings": [[0.1] * 768] * 10
        }
        mock_client_class.return_value = mock_client

        # Single embedding
        single_result = mock_client.generate_embedding("text")
        assert "embedding" in single_result

        # Batch embedding
        batch_result = mock_client.generate_embeddings_batch(["text"] * 10)
        assert "embeddings" in batch_result
        assert len(batch_result["embeddings"]) == 10


@pytest.mark.skipif(
    os.environ.get("GPU_EMBEDDING_SERVICE_RUNNING", "false").lower() != "true",
    reason="GPU embedding service not running",
)
class TestRealGPUEmbeddingIndexing:
    """
    Integration tests with real GPU embedding service.
    Only run when GPU_EMBEDDING_SERVICE_RUNNING=true.
    """

    def test_real_document_indexing(self, app, tmp_path):
        """Test document indexing with real GPU service."""
        with app.app_context():
            # Create test document
            test_content = """
            This document tests GPU-accelerated embedding generation.
            It contains multiple sentences to test chunking and embedding.
            The GPU service should process this efficiently.
            """

            test_file = tmp_path / "real_gpu_test.txt"
            test_file.write_text(test_content)

            doc = DBDocument(
                filename="real_gpu_test.txt",
                path=str(test_file),
                type="txt",
                content=test_content,
                index_status="PENDING",
            )
            db.session.add(doc)
            db.session.commit()

            # Index document
            result = indexing_service.add_file_to_index(
                file_path=str(test_file), db_document=doc, progress_callback=None
            )

            # Verify indexing succeeded
            assert result is True

            # Check document status
            indexed_doc = db.session.get(DBDocument, doc.id)
            assert indexed_doc.index_status == "INDEXED"

    def test_real_search_with_gpu_embeddings(self, app, tmp_path):
        """Test semantic search using GPU-generated embeddings."""
        with app.app_context():
            # Create and index test documents
            docs = [
                "Python programming is excellent for machine learning.",
                "JavaScript is the language of the web browser.",
                "Deep learning uses neural networks for AI tasks.",
            ]

            for i, content in enumerate(docs):
                test_file = tmp_path / f"search_test_{i}.txt"
                test_file.write_text(content)

                doc = DBDocument(
                    filename=f"search_test_{i}.txt",
                    path=str(test_file),
                    type="txt",
                    content=content,
                    index_status="PENDING",
                )
                db.session.add(doc)
                db.session.commit()

                indexing_service.add_file_to_index(
                    file_path=str(test_file), db_document=doc, progress_callback=None
                )

            # Perform search
            results = indexing_service.search_with_llamaindex(
                "machine learning and AI", max_chunks=5
            )

            assert results is not None
            assert len(results) > 0

            # Print results for verification
            for r in results:
                print(f"Score: {r['score']:.4f} - {r['text'][:50]}...")


if __name__ == "__main__":
    pytest.main([__file__, "-vv", "--tb=short"])
