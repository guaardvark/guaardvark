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
        # Reentrant: get_stats() holds this and calls get_optimal_split_ratio(),
        # which takes it again.
        self.lock = threading.RLock()

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
        # Clients for a prompt pair other than the model's declared one (the
        # index's recorded pair), keyed ("gpu" | "cpu", query_prompt, document_prompt).
        self._prompted_clients: Dict[tuple, Any] = {}
        self._active_model_name = None
        self._embed_dim = None
        self._choice_checked_at = 0.0
        self._plugin_gate = None
        self._paths = threading.local()

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
        # No GPU+CPU split on a GPU box (parallel_threshold 0). Ollama keeps one
        # copy of a model: a num_gpu=0 call reloads it onto the CPU and every
        # call after it, GPU path included, runs on that CPU copy until it
        # unloads. Measured on qwen3-embedding:4b-q4_K_M, 12 texts (RTX 4070 Ti
        # SUPER, Ollama 0.40): 0.13 s on the GPU; the first num_gpu=0 call 7.98 s
        # (6.78 s reload); each later call 1.20 s, still on the CPU.
        configs = {
            HardwareProfile.HIGH_END_GPU: {
                "gpu_enabled": True,
                "batch_size": 64,
                "parallel_threshold": 0,
                "gpu_ratio": 0.8,
                "max_workers": 4,
                "latency_window": 100,
            },
            HardwareProfile.MID_RANGE_GPU: {
                "gpu_enabled": True,
                "batch_size": 32,
                "parallel_threshold": 0,
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
            return "http://127.0.0.1:11434"

    def use_model(self, model_name: str, embed_dim: Optional[int] = None) -> None:
        """Embed with ``model_name`` from now on.

        Both clients are rebuilt on next use, so the CPU one keeps num_gpu=0
        and the model's query/text prefixes. Replacing one client and leaving
        the other cached kept the old model in use until a restart.
        """
        with self.lock:
            if model_name != self._active_model_name:
                logger.info(f"EmbeddingRouter: {self._active_model_name} -> {model_name}")
            self._gpu_embedding = None
            self._cpu_embedding = None
            self._prompted_clients = {}
            self._active_model_name = model_name
            self._embed_dim = embed_dim
            self.latency_tracker = LatencyTracker(
                window_size=self.profile_config.get("latency_window", 100)
            )

    def _follow_saved_choice(self) -> None:
        """Switch to the model saved in Settings when another process changed it.

        Checked at most every 10 s. Only an explicit choice counts: the
        automatic pick depends on free VRAM and may differ from call to call,
        and following it would mix vector widths in one index.
        """
        now = time.time()
        if now - self._choice_checked_at < 10.0:
            return
        self._choice_checked_at = now
        try:
            from backend.config import get_saved_embedding_model
            saved = get_saved_embedding_model()
        except Exception:
            return
        if saved and self._active_model_name and saved != self._active_model_name:
            self.use_model(saved)

    def _model_name(self) -> str:
        if not self._active_model_name:
            from backend.config import get_active_embedding_model
            self._active_model_name = get_active_embedding_model()
        return self._active_model_name

    def _declared_prompts(self) -> tuple:
        from backend.utils.llama_index_local_config import get_embedding_instructions
        return tuple(get_embedding_instructions(self._model_name()))

    def _prompted_client(self, prompts: Optional[tuple], cpu: bool):
        """The client for a prompt pair, or None for the model's declared pair
        (the cached _gpu_embedding / _cpu_embedding)."""
        if prompts is None:
            return None
        prompts = tuple(p or None for p in prompts)
        if prompts == self._declared_prompts():
            return None
        key = ("cpu" if cpu else "gpu",) + prompts
        client = self._prompted_clients.get(key)
        if client is None:
            from backend.config import get_embedding_keep_alive
            from llama_index.embeddings.ollama import OllamaEmbedding
            model_name = self._model_name()
            kwargs = {
                "model_name": model_name,
                "base_url": self._get_ollama_base_url(),
                "keep_alive": get_embedding_keep_alive(),
                "query_instruction": prompts[0],
                "text_instruction": prompts[1],
            }
            if cpu:
                kwargs["ollama_additional_kwargs"] = {"num_gpu": 0}
            client = OllamaEmbedding(**kwargs)
            client.model_name = model_name
            self._prompted_clients[key] = client
        return client

    def _get_gpu_embedding(self, prompts: Optional[tuple] = None):
        """Get or create GPU embedding client (default Ollama, uses VRAM).

        ``prompts`` (query, document) picks a client for that pair; None means
        the model's declared prompts.
        """
        prompted = self._prompted_client(prompts, cpu=False)
        if prompted is not None:
            return prompted
        if self._gpu_embedding is None:
            try:
                from backend.config import get_embedding_keep_alive
                from llama_index.embeddings.ollama import OllamaEmbedding
                from backend.utils.llama_index_local_config import get_embedding_instructions

                model_name = self._model_name()

                query_inst, text_inst = get_embedding_instructions(model_name)
                ollama_kwargs = {
                    "model_name": model_name,
                    "base_url": self._get_ollama_base_url(),
                    "keep_alive": get_embedding_keep_alive(),  # orchestrator owns eviction; see config
                }
                if query_inst:
                    ollama_kwargs["query_instruction"] = query_inst
                if text_inst:
                    ollama_kwargs["text_instruction"] = text_inst

                self._gpu_embedding = OllamaEmbedding(**ollama_kwargs)
                self._gpu_embedding.model_name = model_name

                if hasattr(self._gpu_embedding, 'embed_dim'):
                    self._embed_dim = self._gpu_embedding.embed_dim

                logger.info(f"GPU embedding client initialized: {model_name} (asymmetric prefixes: {bool(query_inst)})")
            except Exception as e:
                logger.error(f"Failed to initialize GPU embedding: {e}")
                raise
        return self._gpu_embedding

    def _get_cpu_embedding(self, prompts: Optional[tuple] = None):
        """Get or create CPU embedding client (Ollama with num_gpu=0, zero VRAM)."""
        prompted = self._prompted_client(prompts, cpu=True)
        if prompted is not None:
            return prompted
        if self._cpu_embedding is None:
            try:
                from backend.config import get_embedding_keep_alive
                from llama_index.embeddings.ollama import OllamaEmbedding
                from backend.utils.llama_index_local_config import get_embedding_instructions

                model_name = self._model_name()

                query_inst, text_inst = get_embedding_instructions(model_name)
                ollama_kwargs = {
                    "model_name": model_name,
                    "base_url": self._get_ollama_base_url(),
                    "ollama_additional_kwargs": {"num_gpu": 0},
                    "keep_alive": get_embedding_keep_alive(),  # orchestrator owns eviction; see config
                }
                if query_inst:
                    ollama_kwargs["query_instruction"] = query_inst
                if text_inst:
                    ollama_kwargs["text_instruction"] = text_inst

                self._cpu_embedding = OllamaEmbedding(**ollama_kwargs)
                self._cpu_embedding.model_name = model_name

                logger.info(f"CPU embedding client initialized: {model_name} (num_gpu=0, asymmetric: {bool(query_inst)})")
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

            # What Ollama reports for the model (<arch>.embedding_length),
            # for one the table above does not list (mxbai-embed-large: 1024).
            if self._embed_dim is None:
                try:
                    from backend.config import get_active_embedding_model
                    from backend.services.model_capabilities import capabilities_for
                    dim = capabilities_for(get_active_embedding_model(), with_vision=False).embedding_dim
                    if dim > 0:
                        self._embed_dim = dim
                except Exception:
                    pass

            if self._embed_dim is None:
                self._embed_dim = 4096
        return self._embed_dim

    def _route_to_gpu(self, texts: List[str], prompts: Optional[tuple] = None,
                      query: bool = False) -> List[List[float]]:
        """Embed via GPU path (default Ollama). ``query`` embeds one search query."""
        model = self._get_gpu_embedding(prompts)
        # A CPU fallback after a GPU error leaves the model on the CPU for
        # every later call; move it back once the GPU has room.
        try:
            from backend.utils.ollama_resource_manager import reload_onto_gpu_if_room
            reload_onto_gpu_if_room(self._model_name())
        except Exception:
            pass
        start = time.time()
        try:
            if query:
                result = [model.get_query_embedding(texts[0])]
            elif len(texts) == 1:
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

    def _route_to_cpu(self, texts: List[str], prompts: Optional[tuple] = None,
                      query: bool = False) -> List[List[float]]:
        """Embed via CPU path (Ollama with num_gpu=0, zero VRAM). ``query`` as for GPU."""
        model = self._get_cpu_embedding(prompts)
        start = time.time()
        try:
            if query:
                result = [model.get_query_embedding(texts[0])]
            elif len(texts) == 1:
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

    def _parallel_batch(self, texts: List[str], prompts: Optional[tuple] = None) -> List[List[float]]:
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
            return self._route_to_cpu(cpu_texts, prompts)
        if not cpu_texts:
            return self._route_to_gpu(gpu_texts, prompts)

        logger.debug(
            f"Parallel batch: {len(gpu_texts)} GPU + {len(cpu_texts)} CPU "
            f"(ratio={gpu_ratio:.2f})"
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            gpu_future = executor.submit(self._route_to_gpu, gpu_texts, prompts)
            cpu_future = executor.submit(self._route_to_cpu, cpu_texts, prompts)

            try:
                gpu_results = gpu_future.result(timeout=300)
                cpu_results = cpu_future.result(timeout=300)
                return gpu_results + cpu_results
            except Exception as e:
                logger.warning(f"Parallel batch failed: {e}, falling back to GPU-only")
                try:
                    return self._route_to_gpu(texts, prompts)
                except Exception:
                    return self._route_to_cpu(texts, prompts)

    def _note_path(self, path: str, n: int) -> None:
        counts = getattr(self._paths, "counts", None)
        if counts is None:
            counts = self._paths.counts = {}
        counts[path] = counts.get(path, 0) + n

    def path_counts(self) -> Dict[str, int]:
        """Texts this thread has embedded so far, per path."""
        return dict(getattr(self._paths, "counts", None) or {})

    def plugin_gate(self):
        if self._plugin_gate is None:
            from backend.utils.gpu_embedding_plugin import PluginEmbeddingGate
            self._plugin_gate = PluginEmbeddingGate()
        return self._plugin_gate

    def _own_path(self, texts: List[str], prompts: Optional[tuple] = None) -> List[List[float]]:
        if self.profile_config["gpu_enabled"]:
            try:
                return self._route_to_gpu(texts, prompts)
            except Exception as e:
                logger.warning(f"GPU failed, falling back to CPU: {e}")
        return self._route_to_cpu(texts, prompts)

    def _own_client(self, prompts: Optional[tuple] = None):
        return (self._get_gpu_embedding(prompts) if self.profile_config["gpu_enabled"]
                else self._get_cpu_embedding(prompts))

    def _via_plugin_check(self) -> bool:
        """Whether the plugin would embed the next call (runs the gate's check)."""
        return self.plugin_gate().usable(self._model_name(), self._own_path, self._own_client()._format_text)

    def _via_plugin(self, texts: List[str], prompts: Optional[tuple] = None) -> Optional[List[List[float]]]:
        """Vectors from the GPU Embedding plugin, or None to use the router's own path.

        The plugin embeds text as given, so the client for ``prompts`` formats it.
        """
        gate = self.plugin_gate()
        if not gate.enabled():
            return None
        client = self._own_client(prompts)
        vectors = None
        if gate.usable(self._model_name(), lambda t: self._own_path(t, prompts), client._format_text):
            try:
                vectors = gate.embed(texts, client._format_text)
            except Exception as e:
                logger.warning(f"GPU Embedding plugin failed, using the indexer's own path: {e}")
        if vectors is None:
            gate.count(len(texts), via_plugin=False)
            return None
        gate.count(len(texts), via_plugin=True)
        self._note_path("gpu_embedding_plugin", len(texts))
        return vectors

    def get_embedding(self, text: str, *, prompts: Optional[tuple] = None) -> List[float]:
        """Get embedding for a single text. Uses GPU if available, else CPU.

        ``prompts`` (query, document) embeds with that document prompt, as an
        index records it; None uses the model's declared prompt.
        """
        with self.lock:
            self._follow_saved_choice()
            via_plugin = self._via_plugin([text], prompts)
            if via_plugin is not None:
                return via_plugin[0]
            if self.profile_config["gpu_enabled"]:
                try:
                    out = self._route_to_gpu([text], prompts)[0]
                    self._note_path("ollama_gpu", 1)
                    return out
                except Exception as e:
                    logger.warning(f"GPU failed, falling back to CPU: {e}")
            out = self._route_to_cpu([text], prompts)[0]
            self._note_path("ollama_cpu", 1)
            return out

    def get_query_embedding(self, query: str, *, prompts: Optional[tuple] = None) -> List[float]:
        """Embed a search query with the query prompt of ``prompts`` (None: the
        model's declared one).

        Routed like get_embedding but never through the GPU Embedding plugin:
        one short text, and the plugin path is checked for passages.
        """
        with self.lock:
            self._follow_saved_choice()
            if self.profile_config["gpu_enabled"]:
                try:
                    out = self._route_to_gpu([query], prompts, query=True)[0]
                    self._note_path("ollama_gpu", 1)
                    return out
                except Exception as e:
                    logger.warning(f"GPU failed, falling back to CPU: {e}")
            out = self._route_to_cpu([query], prompts, query=True)[0]
            self._note_path("ollama_cpu", 1)
            return out

    def get_embeddings_batch(self, texts: List[str], *, prompts: Optional[tuple] = None) -> List[List[float]]:
        """Get embeddings for multiple texts.

        For large batches (>= parallel_threshold), splits work across GPU and
        CPU concurrently.  For small batches, uses GPU-only (faster).
        ``prompts`` as for get_embedding.
        """
        with self.lock:
            if not texts:
                return []
            self._follow_saved_choice()

            via_plugin = self._via_plugin(texts, prompts)
            if via_plugin is not None:
                return via_plugin

            threshold = self.profile_config["parallel_threshold"]

            # Large batch: parallel GPU+CPU
            if (self.profile_config["gpu_enabled"]
                    and threshold > 0
                    and len(texts) >= threshold):
                try:
                    out = self._parallel_batch(texts, prompts)
                    self._note_path("ollama_gpu_cpu_split", len(texts))
                    return out
                except Exception as e:
                    logger.warning(f"Parallel batch failed: {e}, falling back")

            # Small batch or CPU-only: single path
            if self.profile_config["gpu_enabled"]:
                try:
                    out = self._route_to_gpu(texts, prompts)
                    self._note_path("ollama_gpu", len(texts))
                    return out
                except Exception as e:
                    logger.warning(f"GPU batch failed, CPU fallback: {e}")

            out = self._route_to_cpu(texts, prompts)
            self._note_path("ollama_cpu", len(texts))
            return out

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
            **({"gpu_embedding_plugin": self._plugin_gate.status()}
               if self._plugin_gate is not None and self._plugin_gate.enabled() else {}),
        }


class RouterEmbeddingAdapter(BaseEmbedding if LLAMAINDEX_AVAILABLE else object):
    """LlamaIndex-compatible adapter wrapping EmbeddingRouter."""

    # Pydantic V2 model config — allow arbitrary attributes for _router etc.
    model_config = {"arbitrary_types_allowed": True} if LLAMAINDEX_AVAILABLE else {}

    # Use PrivateAttr for Pydantic V2 compatibility (BaseEmbedding is a Pydantic model)
    if LLAMAINDEX_AVAILABLE:
        from pydantic import PrivateAttr
        _router: Any = PrivateAttr(default=None)
        _cached_embed_dim: Optional[int] = PrivateAttr(default=None)
        _cached_model_name: Optional[str] = PrivateAttr(default=None)
    else:
        _router = None
        _cached_embed_dim = None
        _cached_model_name = None

    def __init__(self, router: EmbeddingRouter):
        model_name = router._active_model_name or "mxbai-embed-large"
        if LLAMAINDEX_AVAILABLE:
            super().__init__(model_name=model_name)
        self._router = router
        self._cached_embed_dim = router.embed_dim
        self._cached_model_name = model_name

    @property
    def embed_dim(self) -> int:
        return self._cached_embed_dim or 4096

    def _index_prompts(self) -> tuple:
        """The (query, document) prompts recorded on the index, read per call so
        a re-index started anywhere takes effect without rebuilding this adapter."""
        from backend.services.embedding_prompts import index_prompts
        return tuple(index_prompts(self._router._model_name()))

    # Read by the indexing service to tell whether this client matches the index.
    @property
    def query_instruction(self) -> Optional[str]:
        return self._index_prompts()[0]

    @property
    def text_instruction(self) -> Optional[str]:
        return self._index_prompts()[1]

    def _get_query_embedding(self, query: str) -> List[float]:
        return self._router.get_query_embedding(query, prompts=self._index_prompts())

    def _get_text_embedding(self, text: str) -> List[float]:
        return self._router.get_embedding(text, prompts=self._index_prompts())

    async def _aget_query_embedding(self, query: str) -> List[float]:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_query_embedding, query)

    async def _aget_text_embedding(self, text: str) -> List[float]:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_text_embedding, text)

    def get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        return self._router.get_embeddings_batch(texts, prompts=self._index_prompts())


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
