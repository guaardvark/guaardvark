"""
Embedding Router - Adaptive GPU+CPU Embedding System

Routes embedding requests to GPU and/or CPU backends based on:
- Hardware profile detection (GPU available, RAM, cores)
- Latency-based adaptive split ratios
- Concurrent batch processing for large indexing jobs

Uses Ollama as the sole backend with two execution modes:
- GPU mode: Default Ollama (model loaded in VRAM, ~7x faster)
- CPU mode: Ollama with num_gpu=0 (model in RAM only, 0 VRAM)

Both modes produce identical vectors for semantic consistency.
"""

import logging
import time
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import List, Optional, Dict, Any
from statistics import mean

logger = logging.getLogger(__name__)

try:
    from llama_index.core.embeddings import BaseEmbedding
    LLAMAINDEX_AVAILABLE = True
except ImportError:
    LLAMAINDEX_AVAILABLE = False
    BaseEmbedding = object
    logger.warning("LlamaIndex not available - RouterEmbeddingAdapter will have limited functionality")


class HardwareProfile(Enum):
    HIGH_END_GPU = "high_end_gpu"
    MID_RANGE_GPU = "mid_range_gpu"
    CPU_ONLY_POWERFUL = "cpu_powerful"
    CPU_ONLY_MODEST = "cpu_modest"
    LOW_RESOURCE = "low_resource"


class LatencyTracker:
    """Tracks per-backend latency for adaptive split ratio computation."""

    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self.gpu_latencies = deque(maxlen=window_size)
        self.cpu_latencies = deque(maxlen=window_size)
        self.lock = threading.Lock()

    def record(self, backend: str, latency_ms: float):
        with self.lock:
            if backend == "gpu":
                self.gpu_latencies.append(latency_ms)
            elif backend == "cpu":
                self.cpu_latencies.append(latency_ms)

    def get_optimal_split_ratio(self) -> float:
        """Return GPU ratio (0.0–1.0) based on measured latencies.

        Higher ratio means more work to GPU.  Adapts automatically as
        latency measurements accumulate.
        """
        with self.lock:
            if not self.gpu_latencies and not self.cpu_latencies:
                return 0.7  # Default: favour GPU

            if not self.gpu_latencies:
                return 0.0
            if not self.cpu_latencies:
                return 1.0

            avg_gpu = mean(self.gpu_latencies)
            avg_cpu = mean(self.cpu_latencies)

            total = avg_gpu + avg_cpu
            if total == 0:
                return 0.7

            # Inverse: faster backend gets higher ratio
            gpu_ratio = avg_cpu / total
            return max(0.1, min(0.9, gpu_ratio))

    def get_stats(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "gpu_samples": len(self.gpu_latencies),
                "cpu_samples": len(self.cpu_latencies),
                "avg_gpu_ms": round(mean(self.gpu_latencies), 1) if self.gpu_latencies else None,
                "avg_cpu_ms": round(mean(self.cpu_latencies), 1) if self.cpu_latencies else None,
                "optimal_gpu_ratio": round(self.get_optimal_split_ratio(), 2),
            }


class EmbeddingRouter:
    """
    Adaptive embedding router with real GPU/CPU load balancing.

    GPU path: Ollama default (model in VRAM, fast)
    CPU path: Ollama with num_gpu=0 (model in RAM, zero VRAM)

    Both paths use the SAME model and produce IDENTICAL vectors.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self.lock = threading.RLock()

        self.hardware_profile = self._detect_hardware_profile()
        logger.info(f"Detected hardware profile: {self.hardware_profile.value}")

        self.profile_config = self._configure_for_profile()

        self.latency_tracker = LatencyTracker(
            window_size=self.profile_config.get("latency_window", 100)
        )

        # Embedding clients (lazy init)
        self._gpu_embedding = None  # OllamaEmbedding (default, uses GPU)
        self._cpu_embedding = None  # OllamaEmbedding (num_gpu=0, CPU only)
        self._active_model_name = None
        self._embed_dim = None

        logger.info(
            f"EmbeddingRouter initialized: profile={self.hardware_profile.value}, "
            f"gpu_enabled={self.profile_config['gpu_enabled']}, "
            f"parallel_threshold={self.profile_config['parallel_threshold']}"
        )

    def _detect_hardware_profile(self) -> HardwareProfile:
        try:
            import psutil

            memory_gb = psutil.virtual_memory().total / (1024 ** 3)
            cpu_cores = psutil.cpu_count(logical=True) or 2

            gpu_available = False
            try:
                import subprocess
                result = subprocess.run(
                    ['which', 'nvidia-smi'],
                    capture_output=True, timeout=1
                )
                gpu_available = result.returncode == 0
            except Exception:
                pass

            if gpu_available:
                if memory_gb >= 32 and cpu_cores >= 8:
                    return HardwareProfile.HIGH_END_GPU
                elif memory_gb >= 16:
                    return HardwareProfile.MID_RANGE_GPU
                else:
                    return HardwareProfile.MID_RANGE_GPU
            else:
                if memory_gb >= 16 and cpu_cores >= 8:
                    return HardwareProfile.CPU_ONLY_POWERFUL
                elif memory_gb >= 8:
                    return HardwareProfile.CPU_ONLY_MODEST
                else:
                    return HardwareProfile.LOW_RESOURCE

        except ImportError:
            logger.warning("psutil not available, using LOW_RESOURCE profile")
            return HardwareProfile.LOW_RESOURCE
        except Exception as e:
            logger.warning(f"Error detecting hardware profile: {e}, using LOW_RESOURCE")
            return HardwareProfile.LOW_RESOURCE

    def _configure_for_profile(self) -> Dict[str, Any]:
        configs = {
            HardwareProfile.HIGH_END_GPU: {
                "gpu_enabled": True,
                "batch_size": 64,
                "parallel_threshold": 20,  # Split batches >= 20 across GPU+CPU
                "gpu_ratio": 0.8,
                "max_workers": 4,
                "latency_window": 100,
            },
            HardwareProfile.MID_RANGE_GPU: {
                "gpu_enabled": True,
                "batch_size": 32,
                "parallel_threshold": 10,
                "gpu_ratio": 0.7,
                "max_workers": 3,
                "latency_window": 100,
            },
            HardwareProfile.CPU_ONLY_POWERFUL: {
                "gpu_enabled": False,
                "batch_size": 32,
                "parallel_threshold": 0,
                "gpu_ratio": 0.0,
                "max_workers": 4,
                "latency_window": 50,
            },
            HardwareProfile.CPU_ONLY_MODEST: {
                "gpu_enabled": False,
                "batch_size": 16,
                "parallel_threshold": 0,
                "gpu_ratio": 0.0,
                "max_workers": 2,
                "latency_window": 50,
            },
            HardwareProfile.LOW_RESOURCE: {
                "gpu_enabled": False,
                "batch_size": 4,
                "parallel_threshold": 0,
                "gpu_ratio": 0.0,
                "max_workers": 1,
                "latency_window": 20,
            },
        }
        return configs.get(self.hardware_profile, configs[HardwareProfile.LOW_RESOURCE])

    def _get_ollama_base_url(self) -> str:
        try:
            from backend.config import OLLAMA_BASE_URL
            return OLLAMA_BASE_URL
        except ImportError:
            return "http://localhost:11434"

    def _get_gpu_embedding(self):
        """Get or create GPU embedding client (default Ollama, uses VRAM)."""
        if self._gpu_embedding is None:
            try:
                from backend.config import get_active_embedding_model
                from llama_index.embeddings.ollama import OllamaEmbedding

                model_name = get_active_embedding_model()
                self._active_model_name = model_name

                self._gpu_embedding = OllamaEmbedding(
                    model_name=model_name,
                    base_url=self._get_ollama_base_url(),
                )
                self._gpu_embedding.model_name = model_name

                if hasattr(self._gpu_embedding, 'embed_dim'):
                    self._embed_dim = self._gpu_embedding.embed_dim

                logger.info(f"GPU embedding client initialized: {model_name}")
            except Exception as e:
                logger.error(f"Failed to initialize GPU embedding: {e}")
                raise
        return self._gpu_embedding

    def _get_cpu_embedding(self):
        """Get or create CPU embedding client (Ollama with num_gpu=0, zero VRAM)."""
        if self._cpu_embedding is None:
            try:
                from backend.config import get_active_embedding_model
                from llama_index.embeddings.ollama import OllamaEmbedding

                model_name = get_active_embedding_model()
                self._active_model_name = model_name

                self._cpu_embedding = OllamaEmbedding(
                    model_name=model_name,
                    base_url=self._get_ollama_base_url(),
                    ollama_additional_kwargs={"num_gpu": 0},
                )
                self._cpu_embedding.model_name = model_name

                logger.info(f"CPU embedding client initialized: {model_name} (num_gpu=0)")
            except Exception as e:
                logger.error(f"Failed to initialize CPU embedding: {e}")
                raise
        return self._cpu_embedding

    @property
    def embed_dim(self) -> int:
        if self._embed_dim is None:
            # Check already-initialized clients (non-blocking)
            for client in (self._gpu_embedding, self._cpu_embedding):
                if client and hasattr(client, 'embed_dim') and client.embed_dim:
                    self._embed_dim = client.embed_dim
                    break

            # Estimate from config if clients not yet initialized
            if self._embed_dim is None:
                try:
                    from backend.config import get_embedding_vram_estimates, get_active_embedding_model
                    model = get_active_embedding_model()
                    estimates = get_embedding_vram_estimates()
                    for key, info in estimates.items():
                        if key in model or model in key:
                            self._embed_dim = info["dimensions"]
                            break
                except Exception:
                    pass

            if self._embed_dim is None:
                self._embed_dim = 4096
        return self._embed_dim

    def _route_to_gpu(self, texts: List[str]) -> List[List[float]]:
        """Embed via GPU path (default Ollama)."""
        model = self._get_gpu_embedding()
        start = time.time()
        try:
            if len(texts) == 1:
                result = [model.get_text_embedding(texts[0])]
            elif hasattr(model, 'get_text_embeddings'):
                result = model.get_text_embeddings(texts)
            else:
                result = [model.get_text_embedding(t) for t in texts]

            latency_ms = (time.time() - start) * 1000 / max(len(texts), 1)
            self.latency_tracker.record("gpu", latency_ms)
            return result
        except Exception as e:
            logger.warning(f"GPU embedding failed: {e}")
            raise

    def _route_to_cpu(self, texts: List[str]) -> List[List[float]]:
        """Embed via CPU path (Ollama with num_gpu=0, zero VRAM)."""
        model = self._get_cpu_embedding()
        start = time.time()
        try:
            if len(texts) == 1:
                result = [model.get_text_embedding(texts[0])]
            elif hasattr(model, 'get_text_embeddings'):
                result = model.get_text_embeddings(texts)
            else:
                result = [model.get_text_embedding(t) for t in texts]

            latency_ms = (time.time() - start) * 1000 / max(len(texts), 1)
            self.latency_tracker.record("cpu", latency_ms)
            return result
        except Exception as e:
            logger.error(f"CPU embedding failed: {e}")
            raise

    def _parallel_batch(self, texts: List[str]) -> List[List[float]]:
        """Split a large batch across GPU and CPU concurrently.

        Uses the adaptive split ratio: GPU gets the larger share when it's
        faster (which it usually is by ~7x), but the CPU path processes its
        chunk in parallel — yielding better throughput than GPU alone for
        large batches.
        """
        # Use adaptive ratio if we have measurements, else profile default
        if self.latency_tracker.gpu_latencies and self.latency_tracker.cpu_latencies:
            gpu_ratio = self.latency_tracker.get_optimal_split_ratio()
        else:
            gpu_ratio = self.profile_config["gpu_ratio"]

        split_idx = int(len(texts) * gpu_ratio)
        gpu_texts = texts[:split_idx]
        cpu_texts = texts[split_idx:]

        if not gpu_texts:
            return self._route_to_cpu(cpu_texts)
        if not cpu_texts:
            return self._route_to_gpu(gpu_texts)

        logger.debug(
            f"Parallel batch: {len(gpu_texts)} GPU + {len(cpu_texts)} CPU "
            f"(ratio={gpu_ratio:.2f})"
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            gpu_future = executor.submit(self._route_to_gpu, gpu_texts)
            cpu_future = executor.submit(self._route_to_cpu, cpu_texts)

            try:
                gpu_results = gpu_future.result(timeout=300)
                cpu_results = cpu_future.result(timeout=300)
                return gpu_results + cpu_results
            except Exception as e:
                logger.warning(f"Parallel batch failed: {e}, falling back to GPU-only")
                try:
                    return self._route_to_gpu(texts)
                except Exception:
                    return self._route_to_cpu(texts)

    def get_embedding(self, text: str) -> List[float]:
        """Get embedding for a single text. Uses GPU if available, else CPU."""
        with self.lock:
            if self.profile_config["gpu_enabled"]:
                try:
                    return self._route_to_gpu([text])[0]
                except Exception as e:
                    logger.warning(f"GPU failed, falling back to CPU: {e}")
            return self._route_to_cpu([text])[0]

    def get_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """Get embeddings for multiple texts.

        For large batches (>= parallel_threshold), splits work across GPU and
        CPU concurrently.  For small batches, uses GPU-only (faster).
        """
        with self.lock:
            if not texts:
                return []

            threshold = self.profile_config["parallel_threshold"]

            # Large batch: parallel GPU+CPU
            if (self.profile_config["gpu_enabled"]
                    and threshold > 0
                    and len(texts) >= threshold):
                try:
                    return self._parallel_batch(texts)
                except Exception as e:
                    logger.warning(f"Parallel batch failed: {e}, falling back")

            # Small batch or CPU-only: single path
            if self.profile_config["gpu_enabled"]:
                try:
                    return self._route_to_gpu(texts)
                except Exception as e:
                    logger.warning(f"GPU batch failed, CPU fallback: {e}")

            return self._route_to_cpu(texts)

    def get_stats(self) -> Dict[str, Any]:
        # Get model name without triggering lazy init
        model_name = self._active_model_name
        if not model_name:
            try:
                from backend.config import get_active_embedding_model
                model_name = get_active_embedding_model()
            except Exception:
                model_name = "Unknown"

        return {
            "hardware_profile": self.hardware_profile.value,
            "gpu_enabled": self.profile_config["gpu_enabled"],
            "parallel_threshold": self.profile_config["parallel_threshold"],
            "gpu_initialized": self._gpu_embedding is not None,
            "cpu_initialized": self._cpu_embedding is not None,
            "active_model": model_name,
            "embed_dim": self.embed_dim,
            "latency": self.latency_tracker.get_stats(),
        }


class RouterEmbeddingAdapter(BaseEmbedding if LLAMAINDEX_AVAILABLE else object):
    """LlamaIndex-compatible adapter wrapping EmbeddingRouter."""

    def __init__(self, router: EmbeddingRouter):
        if LLAMAINDEX_AVAILABLE:
            super().__init__()
        self._router = router
        self._embed_dim = router.embed_dim
        self._model_name = router._active_model_name

    @property
    def model_name(self) -> str:
        if self._model_name:
            return self._model_name
        if self._router and self._router._active_model_name:
            return self._router._active_model_name
        try:
            from backend.config import get_active_embedding_model
            return get_active_embedding_model()
        except Exception:
            return "Unknown"

    @property
    def embed_dim(self) -> int:
        return self._embed_dim

    def _get_query_embedding(self, query: str) -> List[float]:
        return self._router.get_embedding(query)

    def _get_text_embedding(self, text: str) -> List[float]:
        return self._router.get_embedding(text)

    async def _aget_query_embedding(self, query: str) -> List[float]:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._router.get_embedding, query)

    async def _aget_text_embedding(self, text: str) -> List[float]:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._router.get_embedding, text)

    def get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        return self._router.get_embeddings_batch(texts)


# Global singleton
_embedding_router: Optional[EmbeddingRouter] = None
_router_lock = threading.Lock()


def get_embedding_router() -> EmbeddingRouter:
    """Get the global EmbeddingRouter singleton instance."""
    global _embedding_router
    if _embedding_router is None:
        with _router_lock:
            if _embedding_router is None:
                _embedding_router = EmbeddingRouter()
    return _embedding_router
