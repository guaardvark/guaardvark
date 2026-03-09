"""
Adaptive Ollama Resource Manager

Provides resource-aware model loading with dynamic context window sizing.
Prevents OOM situations by estimating model memory requirements against
available system resources before loading.
"""

import logging
import re
import time
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Vision/multimodal model patterns — these need special handling
VISION_MODEL_PATTERNS = [
    r'vl\b', r'vision', r'llava', r'moondream', r'bakllava',
    r'minicpm-v', r'llama.*vision', r'granite.*vision', r'gemma.*vision',
]

# Models that should never be auto-selected as default text LLM
NON_TEXT_MODEL_PATTERNS = VISION_MODEL_PATTERNS + [
    r'embed', r'retrieval', r'minilm',
]

# Memory reserves (MB)
GPU_RESERVE_MB = 2048   # 2GB for embedding model + display + system
RAM_RESERVE_MB = 10240  # 10GB for system + other processes

# Context window limits
MIN_NUM_CTX = 2048
MAX_NUM_CTX = 32768
DEFAULT_TEXT_NUM_CTX = 8192
DEFAULT_VISION_NUM_CTX = 4096
FALLBACK_NUM_CTX = 8192

# Cache for model info to avoid repeated API calls
_model_info_cache: Dict[str, dict] = {}
_cache_ttl = 300  # 5 minutes


def get_ollama_base_url() -> str:
    """Get Ollama base URL from config or default."""
    try:
        from backend.config import OLLAMA_BASE_URL
        return OLLAMA_BASE_URL
    except ImportError:
        return "http://localhost:11434"


def is_vision_model(model_name: str) -> bool:
    """Check if a model is a vision/multimodal model by name pattern."""
    if not model_name:
        return False
    lower = model_name.lower()
    return any(re.search(p, lower) for p in VISION_MODEL_PATTERNS)


def is_text_chat_model(model_name: str) -> bool:
    """Check if a model is suitable as a default text chat LLM."""
    if not model_name:
        return False
    lower = model_name.lower()
    return not any(re.search(p, lower) for p in NON_TEXT_MODEL_PATTERNS)


def get_system_resources() -> Dict[str, float]:
    """
    Get available system memory resources in MB.

    Returns dict with: gpu_free_mb, gpu_total_mb, ram_free_mb, ram_total_mb
    """
    result = {
        "gpu_free_mb": 0.0,
        "gpu_total_mb": 0.0,
        "ram_free_mb": 0.0,
        "ram_total_mb": 0.0,
    }

    # GPU memory via PyTorch/pynvml
    try:
        import torch
        if torch.cuda.is_available():
            mem_free, mem_total = torch.cuda.mem_get_info(0)
            result["gpu_free_mb"] = mem_free / (1024 * 1024)
            result["gpu_total_mb"] = mem_total / (1024 * 1024)
    except Exception as e:
        logger.debug("Could not query GPU memory via torch: %s", e)
        # Fallback: try nvidia-smi
        try:
            import subprocess
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=memory.free,memory.total",
                 "--format=csv,nounits,noheader"],
                timeout=5, text=True,
            )
            parts = out.strip().split(",")
            if len(parts) == 2:
                result["gpu_free_mb"] = float(parts[0].strip())
                result["gpu_total_mb"] = float(parts[1].strip())
        except Exception:
            pass

    # System RAM via psutil
    try:
        import psutil
        vm = psutil.virtual_memory()
        result["ram_free_mb"] = vm.available / (1024 * 1024)
        result["ram_total_mb"] = vm.total / (1024 * 1024)
    except ImportError:
        # Fallback: read /proc/meminfo
        try:
            with open("/proc/meminfo", "r") as f:
                meminfo = {}
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = parts[1].strip().split()[0]
                        meminfo[key] = int(val)  # kB
                result["ram_free_mb"] = meminfo.get("MemAvailable", 0) / 1024
                result["ram_total_mb"] = meminfo.get("MemTotal", 0) / 1024
        except Exception:
            pass

    return result


def get_model_info(model_name: str) -> Optional[dict]:
    """
    Get model metadata from Ollama /api/show.

    Returns dict with: size_mb, parameter_count, native_context, architecture, families
    Returns None if model not found or Ollama unavailable.
    """
    # Check cache
    cache_key = model_name
    cached = _model_info_cache.get(cache_key)
    if cached and (time.time() - cached.get("_cached_at", 0)) < _cache_ttl:
        return cached

    base_url = get_ollama_base_url()

    try:
        resp = requests.post(
            f"{base_url}/api/show",
            json={"name": model_name},
            timeout=10,
        )
        if not resp.ok:
            logger.debug("Ollama /api/show returned %d for '%s'", resp.status_code, model_name)
            return None

        data = resp.json()
        model_info_raw = data.get("model_info", {})
        details = data.get("details", {})

        # Extract parameter count from model_info keys
        parameter_count = 0
        native_context = 0
        for key, value in model_info_raw.items():
            if "context_length" in key:
                native_context = int(value)
            if "block_count" in key or "num_hidden_layers" in key:
                pass  # Architecture info
            if "parameter_count" in key.lower():
                parameter_count = int(value)

        # Estimate parameter count from details.parameter_size if not found
        if parameter_count == 0:
            param_size_str = details.get("parameter_size", "")
            if param_size_str:
                # Parse strings like "8.0B", "14B", "3.8B"
                match = re.match(r"([\d.]+)\s*([BbMm])", param_size_str)
                if match:
                    num = float(match.group(1))
                    unit = match.group(2).upper()
                    if unit == "B":
                        parameter_count = int(num * 1e9)
                    elif unit == "M":
                        parameter_count = int(num * 1e6)

        # Get model file size — /api/show doesn't include 'size', so check /api/tags
        size_bytes = 0
        try:
            tags_resp = requests.get(f"{base_url}/api/tags", timeout=5)
            if tags_resp.ok:
                for m in tags_resp.json().get("models", []):
                    if m.get("name", "").lower() == model_name.lower():
                        size_bytes = m.get("size", 0)
                        break
        except Exception:
            pass
        # Fallback: estimate from parameter count and quantization
        if size_bytes == 0 and parameter_count > 0:
            # Q4 ≈ 0.5 bytes per param, Q8 ≈ 1 byte per param
            bpp = 0.5 if "q4" in (details.get("quantization_level", "")).lower() else 0.75
            size_bytes = int(parameter_count * bpp)

        info = {
            "size_mb": size_bytes / (1024 * 1024) if size_bytes else 0,
            "parameter_count": parameter_count,
            "native_context": native_context,
            "architecture": details.get("family", "unknown"),
            "families": details.get("families", []),
            "quantization": details.get("quantization_level", "unknown"),
            "is_vision": is_vision_model(model_name) or "clip" in str(details.get("families", [])).lower(),
            "_cached_at": time.time(),
        }

        _model_info_cache[cache_key] = info
        return info

    except requests.RequestException as e:
        logger.warning("Failed to get model info for '%s': %s", model_name, e)
        return None
    except Exception as e:
        logger.warning("Error parsing model info for '%s': %s", model_name, e)
        return None


def _estimate_total_overhead_mb(parameter_count: int, num_ctx: int) -> float:
    """
    Estimate total memory overhead (KV cache + compute graph) in MB for a context size.

    Empirically calibrated from real Ollama measurements:
      qwen2.5:14b at 32K ctx: 48GB total - 8.5GB weights = 39.5GB overhead
        → 39500 / (14.8 * 32768) = 0.081 MB per billion params per ctx token
      qwen3-vl:8b at 262K ctx: 49GB total - 5.8GB weights = 43.2GB overhead
        → 43200 / (8.8 * 262144) = 0.019 MB per billion params per ctx token

    The variance comes from different GQA ratios across architectures.
    We use 0.08 (the higher measured value) to be safe — underestimating
    causes OOM, overestimating just means slightly less context.
    """
    if parameter_count == 0:
        parameter_count = 7_000_000_000

    params_b = parameter_count / 1e9

    # Conservative empirical estimate: 0.08 MB per billion params per context token.
    # Includes KV cache, compute graphs, and runner overhead.
    mb_per_b_per_ctx = 0.08

    return params_b * num_ctx * mb_per_b_per_ctx


def compute_optimal_num_ctx(model_name: str) -> int:
    """
    Compute the optimal num_ctx for a model based on available system resources.

    Strategy:
    1. If model weights fit entirely in GPU → allow up to MAX_NUM_CTX (GPU-fast inference)
    2. If model needs CPU offloading → cap at 8192 (CPU KV lookups are slow,
       more context = more memory = more offloading = slower inference)
    3. In all cases, verify total estimated memory fits in available budget
    """
    model_info = get_model_info(model_name)
    resources = get_system_resources()

    if not model_info:
        default = DEFAULT_VISION_NUM_CTX if is_vision_model(model_name) else DEFAULT_TEXT_NUM_CTX
        logger.info("No model info for '%s', using default num_ctx=%d", model_name, default)
        return default

    gpu_free_mb = resources["gpu_free_mb"]
    gpu_budget_mb = max(0, gpu_free_mb - GPU_RESERVE_MB)
    ram_budget_mb = max(0, resources["ram_free_mb"] - RAM_RESERVE_MB)
    total_budget_mb = gpu_budget_mb + ram_budget_mb

    if total_budget_mb <= 0:
        logger.warning(
            "Very low available memory (GPU free: %.0fMB, RAM free: %.0fMB). Using minimum context.",
            gpu_free_mb, resources["ram_free_mb"],
        )
        return MIN_NUM_CTX

    model_weight_mb = model_info["size_mb"]
    param_count = model_info["parameter_count"]
    native_ctx = model_info.get("native_context", 0)
    params_b = param_count / 1e9 if param_count > 0 else 7.0

    if model_weight_mb > total_budget_mb:
        logger.warning(
            "Model '%s' weights (%.0fMB) exceed available budget (%.0fMB). Using minimum context.",
            model_name, model_weight_mb, total_budget_mb,
        )
        return MIN_NUM_CTX

    # Strategy: check if model + KV cache at DEFAULT context (8192) fits entirely
    # in GPU. If yes, allow up to MAX_NUM_CTX (GPU-fast inference). If not, the
    # model needs CPU offloading — cap at 8192 to keep inference responsive.
    # (CPU KV cache lookups are slow; more context = more offloading = slower.)
    def _total_at_ctx(ctx):
        return model_weight_mb + _estimate_total_overhead_mb(param_count, ctx)

    total_at_default = _total_at_ctx(DEFAULT_TEXT_NUM_CTX)
    fits_in_gpu = total_at_default <= gpu_budget_mb

    if fits_in_gpu:
        # Model + 8K context fits in GPU — small model, allow large context
        practical_ceiling = MAX_NUM_CTX
        logger.debug(
            "Model '%s' fits in GPU at 8K ctx (%.0fMB <= %.0fMB). Allowing up to %d.",
            model_name, total_at_default, gpu_budget_mb, practical_ceiling,
        )
    else:
        # Model needs CPU offloading. Cap at 8192 for responsive inference.
        practical_ceiling = DEFAULT_TEXT_NUM_CTX
        logger.debug(
            "Model '%s' needs CPU offload at 8K ctx (%.0fMB > %.0fMB GPU). Capping at %d.",
            model_name, total_at_default, gpu_budget_mb, practical_ceiling,
        )

    ceiling = min(native_ctx, practical_ceiling) if native_ctx > 0 else practical_ceiling

    # Also verify total fits in combined GPU+RAM budget
    remaining_mb = total_budget_mb - model_weight_mb
    mb_per_ctx_token = params_b * 0.08
    if mb_per_ctx_token > 0:
        max_ctx_by_memory = int(remaining_mb / mb_per_ctx_token)
    else:
        max_ctx_by_memory = ceiling

    # Apply floor, ceiling, memory limit, and round to nearest 1024
    optimal = min(max_ctx_by_memory, ceiling)
    optimal = max(optimal, MIN_NUM_CTX)
    optimal = (optimal // 1024) * 1024
    optimal = max(optimal, MIN_NUM_CTX)

    est_total = _total_at_ctx(optimal)
    logger.info(
        "Adaptive context for '%s': num_ctx=%d (native=%d, ceiling=%d, "
        "model=%.0fMB, est_total=%.0fMB, gpu_only=%s, gpu_free=%.0fMB, ram_free=%.0fMB)",
        model_name, optimal, native_ctx, ceiling,
        model_weight_mb, est_total, fits_in_gpu,
        gpu_free_mb, resources["ram_free_mb"],
    )

    return optimal


def validate_model_before_load(model_name: str) -> Tuple[bool, str, int]:
    """
    Validate that a model can be safely loaded with available resources.

    Returns:
        (safe_to_load, reason, recommended_num_ctx)
    """
    model_info = get_model_info(model_name)
    resources = get_system_resources()

    if not model_info:
        return True, "Model info unavailable — proceeding with defaults", FALLBACK_NUM_CTX

    total_available_mb = (
        max(0, resources["gpu_free_mb"] - GPU_RESERVE_MB) +
        max(0, resources["ram_free_mb"] - RAM_RESERVE_MB)
    )

    model_weight_mb = model_info["size_mb"]

    # Check if model weights alone exceed budget
    if model_weight_mb > total_available_mb:
        return (
            False,
            f"Model weights ({model_weight_mb:.0f}MB) exceed available memory "
            f"({total_available_mb:.0f}MB = {resources['gpu_free_mb']:.0f}MB GPU + "
            f"{resources['ram_free_mb']:.0f}MB RAM - reserves)",
            MIN_NUM_CTX,
        )

    recommended_ctx = compute_optimal_num_ctx(model_name)

    # Check if we can give at least minimum context
    overhead_at_min = _estimate_total_overhead_mb(
        model_info["parameter_count"], MIN_NUM_CTX,
    )
    if model_weight_mb + overhead_at_min > total_available_mb:
        return (
            False,
            f"Model '{model_name}' needs {model_weight_mb + overhead_at_min:.0f}MB even at "
            f"minimum context ({MIN_NUM_CTX}), but only {total_available_mb:.0f}MB available",
            MIN_NUM_CTX,
        )

    return True, f"OK — recommended num_ctx={recommended_ctx}", recommended_ctx


def clear_cache():
    """Clear the model info cache."""
    _model_info_cache.clear()
