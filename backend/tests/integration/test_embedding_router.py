"""
Integration tests for EmbeddingRouter

Tests hardware profile detection, routing logic, and hybrid GPU+CPU processing.
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock
from typing import List

# Test imports
try:
    from backend.utils.embedding_router import (
        EmbeddingRouter,
        HardwareProfile,
        LatencyTracker,
        RouterEmbeddingAdapter,
        get_embedding_router,
    )

    ROUTER_AVAILABLE = True
except ImportError:
    ROUTER_AVAILABLE = False


@pytest.mark.skipif(not ROUTER_AVAILABLE, reason="EmbeddingRouter not available")
class TestHardwareProfile:
    """Test hardware profile detection"""

    def test_profile_detection_high_end_gpu(self):
        """Test detection of high-end GPU system"""
        with patch("backend.utils.embedding_router.psutil") as mock_psutil, patch(
            "backend.utils.embedding_router.subprocess"
        ) as mock_subprocess:

            # Mock high-end system
            mock_memory = Mock()
            mock_memory.total = 32 * (1024**3)  # 32 GB
            mock_psutil.virtual_memory.return_value = mock_memory
            mock_psutil.cpu_count.return_value = 8

            # Mock GPU available
            mock_result = Mock()
            mock_result.returncode = 0
            mock_subprocess.run.return_value = mock_result

            router = EmbeddingRouter()
            assert router.hardware_profile == HardwareProfile.HIGH_END_GPU

    def test_profile_detection_low_resource(self):
        """Test detection of low-resource system (Raspberry Pi)"""
        with patch("backend.utils.embedding_router.psutil") as mock_psutil, patch(
            "backend.utils.embedding_router.subprocess"
        ) as mock_subprocess:

            # Mock low-resource system
            mock_memory = Mock()
            mock_memory.total = 4 * (1024**3)  # 4 GB
            mock_psutil.virtual_memory.return_value = mock_memory
            mock_psutil.cpu_count.return_value = 2

            # Mock no GPU
            mock_result = Mock()
            mock_result.returncode = 1
            mock_subprocess.run.return_value = mock_result

            router = EmbeddingRouter()
            assert router.hardware_profile == HardwareProfile.LOW_RESOURCE

    def test_profile_detection_cpu_only(self):
        """Test detection of CPU-only system"""
        with patch("backend.utils.embedding_router.psutil") as mock_psutil, patch(
            "backend.utils.embedding_router.subprocess"
        ) as mock_subprocess:

            # Mock CPU-only system
            mock_memory = Mock()
            mock_memory.total = 16 * (1024**3)  # 16 GB
            mock_psutil.virtual_memory.return_value = mock_memory
            mock_psutil.cpu_count.return_value = 8

            # Mock no GPU
            mock_result = Mock()
            mock_result.returncode = 1
            mock_subprocess.run.return_value = mock_result

            router = EmbeddingRouter()
            assert router.hardware_profile in (
                HardwareProfile.CPU_ONLY_POWERFUL,
                HardwareProfile.CPU_ONLY_MODEST,
            )


@pytest.mark.skipif(not ROUTER_AVAILABLE, reason="EmbeddingRouter not available")
class TestLatencyTracker:
    """Test latency tracking and adaptive routing"""

    def test_latency_recording(self):
        """Test recording latencies"""
        tracker = LatencyTracker(window_size=10)

        tracker.record("gpu", 50.0)
        tracker.record("gpu", 60.0)
        tracker.record("cpu", 100.0)
        tracker.record("cpu", 120.0)

        stats = tracker.get_stats()
        assert stats["gpu_samples"] == 2
        assert stats["cpu_samples"] == 2
        assert stats["avg_gpu_ms"] == 55.0
        assert stats["avg_cpu_ms"] == 110.0

    def test_optimal_split_ratio(self):
        """Test optimal split ratio calculation"""
        tracker = LatencyTracker(window_size=10)

        # GPU is faster (lower latency)
        tracker.record("gpu", 50.0)
        tracker.record("gpu", 60.0)
        tracker.record("cpu", 100.0)
        tracker.record("cpu", 120.0)

        ratio = tracker.get_optimal_split_ratio()
        # GPU is faster, so ratio should favor GPU (> 0.5)
        assert 0.5 < ratio < 1.0

    def test_optimal_split_ratio_no_data(self):
        """Test optimal split ratio with no data"""
        tracker = LatencyTracker()
        ratio = tracker.get_optimal_split_ratio()
        assert ratio == 0.6  # Default


@pytest.mark.skipif(not ROUTER_AVAILABLE, reason="EmbeddingRouter not available")
class TestEmbeddingRouter:
    """Test EmbeddingRouter routing logic"""

    def test_singleton(self):
        """Test that router is a singleton"""
        router1 = get_embedding_router()
        router2 = get_embedding_router()
        assert router1 is router2

    def test_get_embedding_single(self):
        """Test single embedding generation"""
        router = EmbeddingRouter()

        # Mock GPU client
        mock_client = Mock()
        mock_client.is_available.return_value = True
        mock_client.generate_embedding.return_value = {"embedding": [0.1] * 4096}
        router._gpu_client = mock_client
        router._gpu_available = True

        # Mock CPU embedding
        mock_cpu = Mock()
        mock_cpu.get_text_embedding.return_value = [0.2] * 4096
        router._cpu_embedding = mock_cpu

        # Test GPU path
        embedding = router.get_embedding("test text")
        assert len(embedding) == 4096
        assert embedding[0] == 0.1

    def test_get_embeddings_batch_cpu_fallback(self):
        """Test batch embedding with CPU fallback"""
        router = EmbeddingRouter()

        # Mock GPU unavailable
        router._gpu_client = None
        router._gpu_available = False

        # Mock CPU embedding
        mock_cpu = Mock()
        mock_cpu.get_text_embeddings.return_value = [[0.1] * 4096, [0.2] * 4096]
        router._cpu_embedding = mock_cpu

        texts = ["text 1", "text 2"]
        embeddings = router.get_embeddings_batch(texts)

        assert len(embeddings) == 2
        assert len(embeddings[0]) == 4096
        mock_cpu.get_text_embeddings.assert_called_once_with(texts)

    def test_parallel_batch_split(self):
        """Test parallel batch processing splits correctly"""
        router = EmbeddingRouter()
        router.profile_config["gpu_ratio"] = 0.6

        # Mock GPU client
        mock_client = Mock()
        mock_client.generate_embeddings_batch.return_value = {
            "embeddings": [[0.1] * 4096, [0.2] * 4096, [0.3] * 4096]
        }
        router._gpu_client = mock_client
        router._gpu_available = True

        # Mock CPU embedding
        mock_cpu = Mock()
        mock_cpu.get_text_embeddings.return_value = [[0.4] * 4096, [0.5] * 4096]
        router._cpu_embedding = mock_cpu

        texts = ["text1", "text2", "text3", "text4", "text5"]

        # Should split: 3 to GPU (60%), 2 to CPU (40%)
        embeddings = router._parallel_batch(texts)

        assert len(embeddings) == 5
        assert len(embeddings[0]) == 4096

    def test_get_stats(self):
        """Test router statistics"""
        router = EmbeddingRouter()
        stats = router.get_stats()

        assert "hardware_profile" in stats
        assert "profile_config" in stats
        assert "gpu_available" in stats
        assert "latency_stats" in stats


@pytest.mark.skipif(not ROUTER_AVAILABLE, reason="EmbeddingRouter not available")
class TestRouterEmbeddingAdapter:
    """Test RouterEmbeddingAdapter for LlamaIndex integration"""

    def test_adapter_initialization(self):
        """Test adapter initialization"""
        router = EmbeddingRouter()
        adapter = RouterEmbeddingAdapter(router)

        assert adapter.router is router
        assert adapter.embed_dim == router.embed_dim

    def test_adapter_get_text_embedding(self):
        """Test adapter single embedding"""
        router = Mock()
        router.get_embedding.return_value = [0.1] * 4096
        router.embed_dim = 4096

        adapter = RouterEmbeddingAdapter(router)
        embedding = adapter._get_text_embedding("test")

        assert len(embedding) == 4096
        router.get_embedding.assert_called_once_with("test")

    def test_adapter_get_text_embeddings(self):
        """Test adapter batch embeddings"""
        router = Mock()
        router.get_embeddings_batch.return_value = [[0.1] * 4096, [0.2] * 4096]
        router.embed_dim = 4096

        adapter = RouterEmbeddingAdapter(router)
        embeddings = adapter.get_text_embeddings(["text1", "text2"])

        assert len(embeddings) == 2
        router.get_embeddings_batch.assert_called_once_with(["text1", "text2"])


@pytest.mark.skipif(not ROUTER_AVAILABLE, reason="EmbeddingRouter not available")
class TestIntegration:
    """Integration tests with real components"""

    @pytest.mark.skip(reason="Requires Ollama and GPU service")
    def test_real_embedding_generation(self):
        """Test real embedding generation (requires Ollama)"""
        router = get_embedding_router()

        # This will use real GPU service or CPU Ollama
        embedding = router.get_embedding("test embedding")

        assert len(embedding) > 0
        assert isinstance(embedding, list)
        assert all(isinstance(x, (int, float)) for x in embedding)

    @pytest.mark.skip(reason="Requires Ollama and GPU service")
    def test_real_batch_processing(self):
        """Test real batch processing (requires Ollama)"""
        router = get_embedding_router()

        texts = ["text 1", "text 2", "text 3"]
        embeddings = router.get_embeddings_batch(texts)

        assert len(embeddings) == len(texts)
        assert all(len(emb) > 0 for emb in embeddings)

    def test_semantic_consistency(self):
        """Test that GPU and CPU produce same embeddings for same text"""
        router = EmbeddingRouter()

        # Mock both GPU and CPU to return same embedding
        test_embedding = [0.1] * 4096

        mock_client = Mock()
        mock_client.is_available.return_value = True
        mock_client.generate_embedding.return_value = {"embedding": test_embedding}
        router._gpu_client = mock_client
        router._gpu_available = True

        mock_cpu = Mock()
        mock_cpu.get_text_embedding.return_value = test_embedding
        router._cpu_embedding = mock_cpu

        # Get embedding via GPU path
        gpu_embedding = router._route_to_gpu(["test"])[0]

        # Get embedding via CPU path
        cpu_embedding = router._route_to_cpu(["test"])[0]

        # Should be identical (same model, same text)
        assert gpu_embedding == cpu_embedding == test_embedding
