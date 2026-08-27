"""
Integration tests for EmbeddingRouter

Tests hardware profile detection, routing logic, and hybrid GPU+CPU processing.
"""

import pytest
from unittest.mock import Mock, patch

# Test imports
try:
    from backend.utils.embedding_router import (
        EmbeddingRouter,
        HardwareProfile,
        LatencyTracker,
        RouterEmbeddingAdapter,
        get_embedding_router
    )
    ROUTER_AVAILABLE = True
except ImportError:
    ROUTER_AVAILABLE = False


@pytest.fixture
def reset_router_singleton():
    """Detach the process-wide EmbeddingRouter singleton for the duration of a test.

    EmbeddingRouter.__new__ hands out one instance per process, so without this a
    test's mocked state (or its hardware-detection patches) leaks into whatever
    test runs next.
    """
    original = EmbeddingRouter._instance
    EmbeddingRouter._instance = None
    yield
    EmbeddingRouter._instance = original


@pytest.fixture
def router(reset_router_singleton):
    """A freshly constructed, singleton-detached EmbeddingRouter for routing tests."""
    return EmbeddingRouter()


@pytest.mark.skipif(not ROUTER_AVAILABLE, reason="EmbeddingRouter not available")
class TestHardwareProfile:
    """Test hardware profile detection"""

    def test_profile_detection_high_end_gpu(self, reset_router_singleton):
        """Test detection of high-end GPU system"""
        with patch('psutil.virtual_memory') as mock_virtual_memory, \
             patch('psutil.cpu_count') as mock_cpu_count, \
             patch('subprocess.run') as mock_run:

            mock_virtual_memory.return_value = Mock(total=32 * (1024 ** 3))  # 32 GB
            mock_cpu_count.return_value = 8
            mock_run.return_value = Mock(returncode=0)  # nvidia-smi found

            router = EmbeddingRouter()
            assert router.hardware_profile == HardwareProfile.HIGH_END_GPU

    def test_profile_detection_low_resource(self, reset_router_singleton):
        """Test detection of low-resource system (Raspberry Pi)"""
        with patch('psutil.virtual_memory') as mock_virtual_memory, \
             patch('psutil.cpu_count') as mock_cpu_count, \
             patch('subprocess.run') as mock_run:

            mock_virtual_memory.return_value = Mock(total=4 * (1024 ** 3))  # 4 GB
            mock_cpu_count.return_value = 2
            mock_run.return_value = Mock(returncode=1)  # no GPU

            router = EmbeddingRouter()
            assert router.hardware_profile == HardwareProfile.LOW_RESOURCE

    def test_profile_detection_cpu_only(self, reset_router_singleton):
        """Test detection of CPU-only system"""
        with patch('psutil.virtual_memory') as mock_virtual_memory, \
             patch('psutil.cpu_count') as mock_cpu_count, \
             patch('subprocess.run') as mock_run:

            mock_virtual_memory.return_value = Mock(total=16 * (1024 ** 3))  # 16 GB
            mock_cpu_count.return_value = 8
            mock_run.return_value = Mock(returncode=1)  # no GPU

            router = EmbeddingRouter()
            assert router.hardware_profile == HardwareProfile.CPU_ONLY_POWERFUL


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
        assert ratio == 0.7  # Default: favour GPU


@pytest.mark.skipif(not ROUTER_AVAILABLE, reason="EmbeddingRouter not available")
class TestEmbeddingRouter:
    """Test EmbeddingRouter routing logic"""

    def test_singleton(self):
        """Test that router is a singleton"""
        router1 = get_embedding_router()
        router2 = get_embedding_router()
        assert router1 is router2

    def test_get_embedding_single(self, router):
        """Test single embedding generation routes through the GPU client"""
        router.profile_config["gpu_enabled"] = True
        dim = router.embed_dim

        mock_gpu = Mock()
        mock_gpu.get_text_embedding.return_value = [0.1] * dim
        router._gpu_embedding = mock_gpu

        embedding = router.get_embedding("test text")

        assert len(embedding) == dim
        assert embedding[0] == 0.1
        mock_gpu.get_text_embedding.assert_called_once_with("test text")

    def test_get_embeddings_batch_cpu_fallback(self, router):
        """Test batch embedding uses the CPU client when GPU is disabled"""
        router.profile_config["gpu_enabled"] = False
        dim = router.embed_dim

        mock_cpu = Mock()
        mock_cpu.get_text_embeddings.return_value = [
            [0.1] * dim,
            [0.2] * dim
        ]
        router._cpu_embedding = mock_cpu

        texts = ["text 1", "text 2"]
        embeddings = router.get_embeddings_batch(texts)

        assert len(embeddings) == 2
        assert len(embeddings[0]) == dim
        mock_cpu.get_text_embeddings.assert_called_once_with(texts)

    def test_parallel_batch_split(self, router):
        """Test parallel batch processing splits correctly across GPU and CPU"""
        router.profile_config["gpu_ratio"] = 0.6
        dim = router.embed_dim

        mock_gpu = Mock()
        mock_gpu.get_text_embeddings.return_value = [[0.1] * dim, [0.2] * dim, [0.3] * dim]
        router._gpu_embedding = mock_gpu

        mock_cpu = Mock()
        mock_cpu.get_text_embeddings.return_value = [[0.4] * dim, [0.5] * dim]
        router._cpu_embedding = mock_cpu

        texts = ["text1", "text2", "text3", "text4", "text5"]

        # Should split: 3 to GPU (60%), 2 to CPU (40%)
        embeddings = router._parallel_batch(texts)

        assert len(embeddings) == 5
        assert len(embeddings[0]) == dim
        mock_gpu.get_text_embeddings.assert_called_once_with(texts[:3])
        mock_cpu.get_text_embeddings.assert_called_once_with(texts[3:])

    def test_get_stats(self, router):
        """Test router statistics"""
        stats = router.get_stats()

        assert "hardware_profile" in stats
        assert "gpu_enabled" in stats
        assert "parallel_threshold" in stats
        assert "latency" in stats
        assert stats["embed_dim"] == router.embed_dim


@pytest.mark.skipif(not ROUTER_AVAILABLE, reason="EmbeddingRouter not available")
class TestRouterEmbeddingAdapter:
    """Test RouterEmbeddingAdapter for LlamaIndex integration"""

    def test_adapter_initialization(self, router):
        """Test adapter initialization"""
        adapter = RouterEmbeddingAdapter(router)

        assert adapter._router is router
        assert adapter.embed_dim == router.embed_dim

    def test_adapter_get_text_embedding(self):
        """Test adapter single embedding"""
        mock_router = Mock()
        mock_router._active_model_name = "qwen3-embedding:4b-q4_K_M"
        mock_router.embed_dim = 2560
        mock_router.get_embedding.return_value = [0.1] * 2560

        adapter = RouterEmbeddingAdapter(mock_router)
        embedding = adapter._get_text_embedding("test")

        assert len(embedding) == 2560
        mock_router.get_embedding.assert_called_once_with("test")

    def test_adapter_get_text_embeddings(self):
        """Test adapter batch embeddings"""
        mock_router = Mock()
        mock_router._active_model_name = "qwen3-embedding:4b-q4_K_M"
        mock_router.embed_dim = 2560
        mock_router.get_embeddings_batch.return_value = [
            [0.1] * 2560,
            [0.2] * 2560
        ]

        adapter = RouterEmbeddingAdapter(mock_router)
        embeddings = adapter.get_text_embeddings(["text1", "text2"])

        assert len(embeddings) == 2
        mock_router.get_embeddings_batch.assert_called_once_with(["text1", "text2"])


@pytest.mark.skipif(not ROUTER_AVAILABLE, reason="EmbeddingRouter not available")
class TestIntegration:
    """Integration tests with real components"""

    @pytest.mark.integration
    @pytest.mark.skip(reason="Requires Ollama and GPU service")
    def test_real_embedding_generation(self):
        """Test real embedding generation (requires Ollama)"""
        router = get_embedding_router()

        # This will use real GPU service or CPU Ollama
        embedding = router.get_embedding("test embedding")

        assert len(embedding) > 0
        assert isinstance(embedding, list)
        assert all(isinstance(x, (int, float)) for x in embedding)

    @pytest.mark.integration
    @pytest.mark.skip(reason="Requires Ollama and GPU service")
    def test_real_batch_processing(self):
        """Test real batch processing (requires Ollama)"""
        router = get_embedding_router()

        texts = ["text 1", "text 2", "text 3"]
        embeddings = router.get_embeddings_batch(texts)

        assert len(embeddings) == len(texts)
        assert all(len(emb) > 0 for emb in embeddings)

    def test_semantic_consistency(self, router):
        """Test that GPU and CPU paths return the same vector for the same text"""
        dim = router.embed_dim
        test_embedding = [0.1] * dim

        mock_gpu = Mock()
        mock_gpu.get_text_embedding.return_value = test_embedding
        router._gpu_embedding = mock_gpu

        mock_cpu = Mock()
        mock_cpu.get_text_embedding.return_value = test_embedding
        router._cpu_embedding = mock_cpu

        # Get embedding via GPU path
        gpu_embedding = router._route_to_gpu(["test"])[0]

        # Get embedding via CPU path
        cpu_embedding = router._route_to_cpu(["test"])[0]

        # Should be identical (same model, same text)
        assert gpu_embedding == cpu_embedding == test_embedding
