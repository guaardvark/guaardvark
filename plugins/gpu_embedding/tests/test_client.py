"""
Tests for GPU Embedding Client
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import requests


class TestGPUEmbeddingClient(unittest.TestCase):
    """Tests for GPU Embedding Client"""
    
    def setUp(self):
        """Set up test fixtures"""
        from plugins.gpu_embedding.client.gpu_embedding_client import GPUEmbeddingClient
        self.client = GPUEmbeddingClient(
            service_url="http://localhost:5002",
            timeout=5
        )
    
    @patch('plugins.gpu_embedding.client.gpu_embedding_client.requests.Session.get')
    def test_health_check_success(self, mock_get):
        """Test successful health check"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "status": "healthy",
            "model_loaded": True
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        health = self.client.health_check()
        
        self.assertEqual(health["status"], "healthy")
        self.assertTrue(health.get("model_loaded"))
    
    @patch('plugins.gpu_embedding.client.gpu_embedding_client.requests.Session.get')
    def test_health_check_failure(self, mock_get):
        """Test health check failure"""
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")
        
        health = self.client.health_check()
        
        self.assertEqual(health["status"], "unavailable")
        self.assertFalse(health.get("available", True))
    
    @patch('plugins.gpu_embedding.client.gpu_embedding_client.requests.Session.post')
    def test_generate_embedding_success(self, mock_post):
        """Test successful embedding generation"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "embedding": [0.1, 0.2, 0.3],
            "dimension": 768,
            "processing_time_ms": 45.2
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        
        result = self.client.generate_embedding("test text")
        
        self.assertIn("embedding", result)
        self.assertEqual(len(result["embedding"]), 3)
        mock_post.assert_called_once()


if __name__ == '__main__':
    unittest.main()

