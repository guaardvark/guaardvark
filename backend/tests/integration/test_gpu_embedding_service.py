#!/usr/bin/env python3
"""
Integration tests for the GPU Embedding Service.

Tests the GPU embedding service endpoints and client:
- Health check endpoint
- Embedding generation endpoints
- Client communication
- Error handling and fallback

IMPORTANT: These tests can run with mocked responses or with the actual service running.
Set GPU_EMBEDDING_SERVICE_RUNNING=true environment variable to test against real service.
"""

import os
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, Mock
import requests

# Check if we should test against real service
TEST_REAL_SERVICE = os.environ.get("GPU_EMBEDDING_SERVICE_RUNNING", "false").lower() == "true"

# Skip if dependencies not available
try:
    from plugins.gpu_embedding.client.gpu_embedding_client import GPUEmbeddingClient, create_client
    from plugins.gpu_embedding.client.embedding_adapter import GPUEmbeddingAdapter
except ImportError as e:
    pytest.skip(f"GPU embedding client modules not available: {e}", allow_module_level=True)


class TestGPUEmbeddingClient:
    """Tests for the GPU Embedding HTTP Client."""
    
    @pytest.fixture
    def client(self):
        """Create a GPU embedding client."""
        return GPUEmbeddingClient(
            service_url="http://localhost:5002",
            timeout=5,
            max_retries=1
        )
    
    @patch('plugins.gpu_embedding.client.gpu_embedding_client.requests.Session.get')
    def test_health_check_success(self, mock_get, client):
        """Test successful health check."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "healthy",
            "model_loaded": True,
            "model_name": "nomic-embed-text",
            "gpu_available": True
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        health = client.health_check()
        
        assert health["status"] == "healthy"
        assert health["model_loaded"] == True
    
    @patch('plugins.gpu_embedding.client.gpu_embedding_client.requests.Session.get')
    def test_health_check_service_unavailable(self, mock_get, client):
        """Test health check when service is unavailable."""
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")
        
        health = client.health_check()
        
        assert health["status"] == "unavailable"
        assert health.get("available") == False
    
    @patch('plugins.gpu_embedding.client.gpu_embedding_client.requests.Session.get')
    def test_is_available(self, mock_get, client):
        """Test availability check."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "healthy",
            "model_loaded": True
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        assert client.is_available() == True
    
    @patch('plugins.gpu_embedding.client.gpu_embedding_client.requests.Session.post')
    def test_generate_embedding_success(self, mock_post, client):
        """Test successful embedding generation."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "embedding": [0.1, 0.2, 0.3] * 256,  # 768 dimensions
            "model": "nomic-embed-text",
            "dimension": 768,
            "processing_time_ms": 45.2
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        
        result = client.generate_embedding("Test text for embedding")
        
        assert "embedding" in result
        assert len(result["embedding"]) == 768
        assert result["dimension"] == 768
    
    @patch('plugins.gpu_embedding.client.gpu_embedding_client.requests.Session.post')
    def test_generate_embedding_batch_success(self, mock_post, client):
        """Test successful batch embedding generation."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "embeddings": [[0.1, 0.2, 0.3] * 256, [0.4, 0.5, 0.6] * 256],
            "model": "nomic-embed-text",
            "dimension": 768,
            "count": 2,
            "processing_time_ms": 85.5
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        
        result = client.generate_embeddings_batch(["Text 1", "Text 2"])
        
        assert "embeddings" in result
        assert len(result["embeddings"]) == 2
        assert result["count"] == 2
    
    @patch('plugins.gpu_embedding.client.gpu_embedding_client.requests.Session.post')
    def test_generate_embedding_timeout(self, mock_post, client):
        """Test embedding generation timeout handling."""
        mock_post.side_effect = requests.exceptions.Timeout("Request timed out")
        
        with pytest.raises(requests.exceptions.Timeout):
            client.generate_embedding("Test text")
    
    @patch('plugins.gpu_embedding.client.gpu_embedding_client.requests.Session.get')
    def test_get_models(self, mock_get, client):
        """Test getting model information."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "current_model": "nomic-embed-text",
            "loaded": True,
            "embed_dim": 768
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        models = client.get_models()
        
        assert models["current_model"] == "nomic-embed-text"
        assert models["loaded"] == True


class TestGPUEmbeddingAdapter:
    """Tests for the LlamaIndex embedding adapter."""
    
    @patch('plugins.gpu_embedding.client.embedding_adapter.create_client')
    def test_adapter_initialization(self, mock_create_client):
        """Test adapter initialization."""
        mock_client = MagicMock()
        mock_client.is_available.return_value = True
        mock_client.get_models.return_value = {
            "current_model": "nomic-embed-text",
            "embed_dim": 768
        }
        mock_create_client.return_value = mock_client
        
        adapter = GPUEmbeddingAdapter(
            service_url="http://localhost:5002",
            timeout=30
        )
        
        assert adapter.client is not None
    
    @patch('plugins.gpu_embedding.client.embedding_adapter.create_client')
    def test_get_text_embedding(self, mock_create_client):
        """Test getting text embedding via adapter."""
        mock_client = MagicMock()
        mock_client.is_available.return_value = True
        mock_client.generate_embedding.return_value = {
            "embedding": [0.1] * 768
        }
        mock_client.get_models.return_value = {
            "embed_dim": 768
        }
        mock_create_client.return_value = mock_client
        
        adapter = GPUEmbeddingAdapter()
        embedding = adapter._get_text_embedding("Test text")
        
        assert len(embedding) == 768
        mock_client.generate_embedding.assert_called_once_with("Test text")
    
    @patch('plugins.gpu_embedding.client.embedding_adapter.create_client')
    def test_get_text_embeddings_batch(self, mock_create_client):
        """Test getting batch embeddings via adapter."""
        mock_client = MagicMock()
        mock_client.is_available.return_value = True
        mock_client.generate_embeddings_batch.return_value = {
            "embeddings": [[0.1] * 768, [0.2] * 768]
        }
        mock_client.get_models.return_value = {
            "embed_dim": 768
        }
        mock_create_client.return_value = mock_client
        
        adapter = GPUEmbeddingAdapter()
        embeddings = adapter.get_text_embeddings(["Text 1", "Text 2"])
        
        assert len(embeddings) == 2
        assert len(embeddings[0]) == 768
    
    @patch('plugins.gpu_embedding.client.embedding_adapter.create_client')
    def test_fallback_when_service_unavailable(self, mock_create_client):
        """Test fallback to CPU embedding when GPU service is unavailable."""
        mock_client = MagicMock()
        mock_client.is_available.return_value = False
        mock_client.get_models.return_value = {}
        mock_create_client.return_value = mock_client
        
        # Create mock fallback embedding
        mock_fallback = MagicMock()
        mock_fallback._get_text_embedding.return_value = [0.5] * 384
        mock_fallback.embed_dim = 384
        
        adapter = GPUEmbeddingAdapter(
            fallback_embedding=mock_fallback,
            fallback_enabled=True
        )
        
        embedding = adapter._get_text_embedding("Test text")
        
        # Should use fallback since GPU service is unavailable
        assert len(embedding) == 384
        mock_fallback._get_text_embedding.assert_called_once()
    
    @patch('plugins.gpu_embedding.client.embedding_adapter.create_client')
    def test_is_gpu_available(self, mock_create_client):
        """Test GPU availability check via adapter."""
        mock_client = MagicMock()
        mock_client.is_available.return_value = True
        mock_client.get_models.return_value = {}
        mock_create_client.return_value = mock_client
        
        adapter = GPUEmbeddingAdapter()
        
        assert adapter.is_gpu_available() == True
    
    @patch('plugins.gpu_embedding.client.embedding_adapter.create_client')
    def test_get_service_info(self, mock_create_client):
        """Test getting service info via adapter."""
        mock_client = MagicMock()
        mock_client.service_url = "http://localhost:5002"
        mock_client.is_available.return_value = True
        mock_client.health_check.return_value = {
            "status": "healthy",
            "model_name": "nomic-embed-text",
            "gpu_available": True,
            "available": True
        }
        mock_client.get_models.return_value = {}
        mock_create_client.return_value = mock_client
        
        adapter = GPUEmbeddingAdapter()
        info = adapter.get_service_info()
        
        assert info["service_url"] == "http://localhost:5002"
        assert info["status"] == "healthy"


@pytest.mark.skipif(not TEST_REAL_SERVICE, reason="GPU embedding service not running")
class TestGPUEmbeddingServiceReal:
    """
    Integration tests against the real GPU embedding service.
    Only run when GPU_EMBEDDING_SERVICE_RUNNING=true.
    """
    
    @pytest.fixture
    def client(self):
        """Create client for real service."""
        return GPUEmbeddingClient(
            service_url=os.environ.get("GPU_EMBEDDING_SERVICE_URL", "http://localhost:5002"),
            timeout=30
        )
    
    def test_real_health_check(self, client):
        """Test health check against real service."""
        health = client.health_check()
        
        assert health["status"] in ("healthy", "degraded")
        print(f"Service health: {health}")
    
    def test_real_embedding_generation(self, client):
        """Test embedding generation against real service."""
        if not client.is_available():
            pytest.skip("GPU embedding service not available")
        
        result = client.generate_embedding(
            "This is a test document for GPU embedding generation."
        )
        
        assert "embedding" in result
        assert len(result["embedding"]) > 0
        print(f"Generated embedding with {len(result['embedding'])} dimensions in {result.get('processing_time_ms', 'N/A')}ms")
    
    def test_real_batch_embedding(self, client):
        """Test batch embedding against real service."""
        if not client.is_available():
            pytest.skip("GPU embedding service not available")
        
        texts = [
            "First test document for batch processing.",
            "Second test document with different content.",
            "Third document about machine learning."
        ]
        
        result = client.generate_embeddings_batch(texts)
        
        assert "embeddings" in result
        assert len(result["embeddings"]) == 3
        print(f"Generated {result['count']} embeddings in {result.get('processing_time_ms', 'N/A')}ms")
    
    def test_real_embedding_quality(self, client):
        """Test embedding quality by comparing similar and dissimilar texts."""
        if not client.is_available():
            pytest.skip("GPU embedding service not available")
        
        import math
        
        def cosine_similarity(a, b):
            dot_product = sum(x * y for x, y in zip(a, b))
            norm_a = math.sqrt(sum(x * x for x in a))
            norm_b = math.sqrt(sum(x * x for x in b))
            return dot_product / (norm_a * norm_b)
        
        # Similar texts
        text1 = "Python is a programming language for data science."
        text2 = "Python programming is used in data analysis."
        
        # Dissimilar text
        text3 = "The weather is sunny today in California."
        
        result1 = client.generate_embedding(text1)
        result2 = client.generate_embedding(text2)
        result3 = client.generate_embedding(text3)
        
        sim_1_2 = cosine_similarity(result1["embedding"], result2["embedding"])
        sim_1_3 = cosine_similarity(result1["embedding"], result3["embedding"])
        
        print(f"Similarity (similar texts): {sim_1_2:.4f}")
        print(f"Similarity (dissimilar texts): {sim_1_3:.4f}")
        
        # Similar texts should have higher similarity
        assert sim_1_2 > sim_1_3, "Similar texts should have higher cosine similarity"


class TestCreateClientFactory:
    """Tests for the client factory function."""
    
    def test_create_client_with_defaults(self, tmp_path):
        """Test creating client with default configuration."""
        # Create a mock plugin.json
        plugin_dir = tmp_path / "plugins" / "gpu_embedding"
        plugin_dir.mkdir(parents=True)
        
        plugin_json = {
            "config": {
                "service_url": "http://localhost:5002",
                "timeout": 30
            }
        }
        
        with open(plugin_dir / "plugin.json", "w") as f:
            json.dump(plugin_json, f)
        
        # Patch the file path
        with patch('plugins.gpu_embedding.client.gpu_embedding_client.Path') as mock_path:
            mock_path.return_value.parent.parent = plugin_dir
            
            client = create_client()
            
            assert client is not None
            assert client.service_url == "http://localhost:5002"
    
    def test_create_client_with_overrides(self):
        """Test creating client with explicit overrides."""
        client = create_client(
            service_url="http://custom:9999",
            timeout=60
        )
        
        assert client.service_url == "http://custom:9999"
        assert client.timeout == 60


if __name__ == "__main__":
    pytest.main([__file__, "-vv", "--tb=short"])
