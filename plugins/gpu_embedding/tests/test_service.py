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


class TestGpuVisibility(unittest.TestCase):
    """The GPU is a fact in the health body, not a condition for answering 200"""

    def _status(self, loaded, gpu):
        from plugins.gpu_embedding.service import health
        with patch.object(health, "check_gpu_available", return_value=gpu), \
             patch.object(health, "is_model_loaded", return_value=loaded), \
             patch.object(health, "get_model_name", return_value="embeddinggemma:latest"), \
             patch.object(health, "get_model_info", return_value={"embed_dim": 768}):
            return health.get_health_status()

    def test_loaded_model_without_a_visible_gpu_is_healthy(self):
        result = self._status(True, {"available": False, "error": "no tool"})
        self.assertEqual(result["status"], "healthy")
        self.assertFalse(result["gpu_available"])
        self.assertTrue(result["issues"])

    def test_model_not_loaded_is_still_unhealthy(self):
        result = self._status(False, {"available": True, "gpus": []})
        self.assertEqual(result["status"], "unhealthy")

    def test_rocm_smi_is_the_fallback(self):
        from plugins.gpu_embedding.service import health
        with patch.object(health, "_check_nvidia_smi", return_value={"available": False, "error": "nvidia-smi not found"}), \
             patch("backend.services.hardware_detector.probe_amd_vram_bytes", return_value=(25753026560, 539684864)):
            result = health.check_gpu_available()
        self.assertTrue(result["available"])
        self.assertEqual(result["gpus"][0]["memory_total_mb"], 24560)

    def test_nvidia_smi_comes_first(self):
        from plugins.gpu_embedding.service import health
        nvidia = {"available": True, "gpus": [{"name": "x", "memory_total_mb": 1, "memory_used_mb": 0}], "count": 1}
        with patch.object(health, "_check_nvidia_smi", return_value=nvidia), \
             patch("backend.services.hardware_detector.probe_amd_vram_bytes") as amd:
            self.assertEqual(health.check_gpu_available(), nvidia)
        amd.assert_not_called()

    def test_no_tool_means_no_gpu(self):
        from plugins.gpu_embedding.service import health
        with patch.object(health, "_check_nvidia_smi", return_value={"available": False, "error": "nvidia-smi not found"}), \
             patch("backend.services.hardware_detector.probe_amd_vram_bytes", return_value=None):
            self.assertFalse(health.check_gpu_available()["available"])


if __name__ == '__main__':
    unittest.main()

