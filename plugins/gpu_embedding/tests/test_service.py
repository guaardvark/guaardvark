"""
Tests for GPU Embedding Service
"""

import unittest
from unittest.mock import Mock, patch, MagicMock


class TestModelLoader(unittest.TestCase):
    """Tests for model loader"""
    
    @patch('plugins.gpu_embedding.service.model_loader.OllamaEmbedding')
    def test_initialize_model_success(self, mock_ollama):
        """Test successful model initialization"""
        from plugins.gpu_embedding.service.model_loader import initialize_model
        
        mock_embedding = Mock()
        mock_ollama.return_value = mock_embedding
        
        result = initialize_model("nomic-embed-text", "http://localhost:11434")
        
        self.assertTrue(result)
        mock_ollama.assert_called_once()
    
    @patch('plugins.gpu_embedding.service.model_loader.OllamaEmbedding')
    def test_initialize_model_failure(self, mock_ollama):
        """Test model initialization failure"""
        from plugins.gpu_embedding.service.model_loader import initialize_model
        
        mock_ollama.side_effect = ImportError("Ollama not available")
        
        result = initialize_model("nomic-embed-text", "http://localhost:11434")
        
        self.assertFalse(result)


class TestHealthCheck(unittest.TestCase):
    """Tests for health check"""
    
    def test_get_health_status(self):
        """Test health status retrieval"""
        from plugins.gpu_embedding.service.health import get_health_status
        
        health = get_health_status()
        
        self.assertIn("status", health)
        self.assertIn("uptime_seconds", health)
        self.assertIsInstance(health["uptime_seconds"], (int, float))


if __name__ == '__main__':
    unittest.main()

