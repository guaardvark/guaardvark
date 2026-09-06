"""
Adaptive Ollama Resource Manager

Provides resource-aware model loading with dynamic context window sizing.
Prevents OOM situations by estimating model memory requirements against
available system resources before loading.
"""

import logging
import re
import threading
import time
import weakref
from typing import Dict, NamedTuple, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Vision/multimodal model patterns — these need special handling
VISION_MODEL_PATTERNS = [
    r'vl\b', r'vision', r'llava', r'moondream', r'bakllava',
    r'minicpm-v', r'llama.*vision', r'granite.*vision', r'gemma.*vision',
    # Gemma 4 integrates vision natively — match even without "vision" suffix
    r'gemma[\-_]?4',
]

# Models that are vision-only (not suitable as default text LLM).
# Omits natively multimodal models (Gemma 4) that handle both text and vision.
NON_TEXT_MODEL_PATTERNS = [
    r'vl\b', r'vision', r'llava', r'moondream', r'bakllava',
    r'minicpm-v', r'llama.*vision', r'granite.*vision', r'gemma.*vision',
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

# When /api/show fails (Ollama unreachable, model not pulled, unparseable
# reply) it is not retried for this long. Short on purpose: a backend that
# boots before Ollama must pick up the real model info on the first chat turn
# after Ollama answers, not minutes later.
_unreachable_at: Dict[str, float] = {}
_unreachable_ttl = 15  # seconds

# LLM instances whose num_ctx is a placeholder chosen without model info.
# Keyed by id(); the weak value drops the entry when the instance is collected.
# Guarded by _provisional_lock because Flask handlers register and refresh
# instances from different threads.
_provisional_llms: "weakref.WeakValueDictionary[int, object]" = weakref.WeakValueDictionary()
_provisional_lock = threading.Lock()


class OverheadProfile(NamedTuple):
    """Per-architecture memory model: ``fixed_mb_per_b`` + ``mb_per_b_per_ctx * num_ctx``, both scaled by billions of params."""

    mb_per_b_per_ctx: float
    fixed_mb_per_b: float


# gemma4: measured on a 16 GiB card with Ollama's f16 KV cache and flash
# attention on (OLLAMA_KV_CACHE_TYPE=f16, OLLAMA_FLASH_ATTENTION=1,
# OLLAMA_NUM_PARALLEL=1); a q8_0/q4_0 KV cache would only lower the slope.
#   * 11.9B dense Q4_K_M, weights 7206 MiB: nvidia-smi memory.used above idle
#     8655 MiB @8192, 8799 @16384, 9087 @32768. Slope 0.01758 MiB/token =
#     0.00148 MB/B/token; intercept 1305 MiB = 110 MB/B.
#   * e4b (8.0B, Q4_K_M, 9163 MiB file of which 3.4 GB is resident for text):
#     4643 MiB @8192, 4921 @32768. Slope 0.0113 MiB/token = 0.0014 MB/B/token.
#     Sizing charges the whole file (audio/vision towers included), so the
#     estimate overshoots real use ~2x for e2b/e4b; that errs towards less
#     context, never towards OOM. At 32768 e4b sat at 6826 MiB of 16376.
# Sliding-window attention on most layers keeps the slope ~50x below the fallback.
OVERHEAD_PROFILES: Dict[str, OverheadProfile] = {
    "gemma4": OverheadProfile(mb_per_b_per_ctx=0.0015, fixed_mb_per_b=110.0),
}

# Name patterns for families whose /api/show "family" may be missing or generic.
_FAMILY_NAME_PATTERNS = {
    "gemma4": r"gemma[\-_]?4",
}

# Unmeasured families: 0.08 MB per billion params per context token, the top of
# the 0.019-0.081 range seen across 8B-14B dense models at 32K-262K context.
# Overestimating costs a little context; underestimating is an OOM.
FALLBACK_OVERHEAD = OverheadProfile(mb_per_b_per_ctx=0.08, fixed_mb_per_b=0.0)


class NumCtxDecision(NamedTuple):
    """``num_ctx`` to request, and whether it was sized from real model info.

    ``resolved=False`` means Ollama could not describe the model and ``num_ctx``
    is a placeholder; callers should re-resolve once Ollama answers.
    """

    num_ctx: int
    resolved: bool


def get_ollama_base_url() -> str:
    """Get Ollama base URL from config or default."""
    try:
        from backend.config import OLLAMA_BASE_URL
        return OLLAMA_BASE_URL
    except ImportError:
        return "http://127.0.0.1:11434"


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
    if (time.time() - _unreachable_at.get(cache_key, 0)) < _unreachable_ttl:
        return None

    base_url = get_ollama_base_url()

    try:
        resp = requests.post(
            f"{base_url}/api/show",
            json={"name": model_name},
            timeout=10,
        )
        if not resp.ok:
            logger.debug("Ollama /api/show returned %d for '%s'", resp.status_code, model_name)
            _unreachable_at[cache_key] = time.time()
            return None

        data = resp.json()
        model_info_raw = data.get("model_info", {})
        details = data.get("details", {})

        # Extract parameter count from model_info keys. The native context is
        # "<arch>.context_length"; rope keys such as
        # "<arch>.rope.scaling.original_context_length" also contain the phrase
        # and are only used when no exact key exists.
        parameter_count = 0
        native_context = 0
        loose_context = 0
        for key, value in model_info_raw.items():
            if key.endswith(".context_length"):
                native_context = int(value)
            elif "context_length" in key:
                loose_context = int(value)
            if "parameter_count" in key.lower():
                parameter_count = int(value)
        if native_context == 0:
            native_context = loose_context

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

        capabilities = data.get("capabilities", [])

        info = {
            "size_mb": size_bytes / (1024 * 1024) if size_bytes else 0,
            "parameter_count": parameter_count,
            "native_context": native_context,
            "architecture": details.get("family", "unknown"),
            "families": details.get("families", []),
            "quantization": details.get("quantization_level", "unknown"),
            "is_vision": is_vision_model(model_name) or "clip" in str(details.get("families", [])).lower(),
            "capabilities": capabilities,
            "_cached_at": time.time(),
        }

        _model_info_cache[cache_key] = info
        _unreachable_at.pop(cache_key, None)

    except requests.RequestException as e:
        logger.warning("Failed to get model info for '%s': %s", model_name, e)
        _unreachable_at[cache_key] = time.time()
        return None
    except Exception as e:
        logger.warning("Error parsing model info for '%s': %s", model_name, e)
        _unreachable_at[cache_key] = time.time()
        return None

    _refresh_provisional_instances(model_name)
    return info


def model_info_available(model_name: str) -> bool:
    """Whether Ollama can currently describe ``model_name`` (cached once it can)."""
    return get_model_info(model_name) is not None


def model_supports_tools(model_name: str) -> bool:
    """Check if a model supports native function calling via Ollama's capabilities API."""
    info = get_model_info(model_name)
    if not info:
        return False
    return "tools" in info.get("capabilities", [])


def overhead_profile(model_name: str, model_info: Optional[dict] = None) -> OverheadProfile:
    """The measured memory profile for a model's family, or ``FALLBACK_OVERHEAD``.

    Matches ``/api/show``'s ``family`` first, then the model name, so a custom
    tag of a measured architecture still gets its numbers.
    """
    arch = str((model_info or {}).get("architecture", "")).lower()
    if arch in OVERHEAD_PROFILES:
        return OVERHEAD_PROFILES[arch]
    lower = (model_name or "").lower()
    for family, pattern in _FAMILY_NAME_PATTERNS.items():
        if family in OVERHEAD_PROFILES and re.search(pattern, lower):
            return OVERHEAD_PROFILES[family]
    return FALLBACK_OVERHEAD


def _estimate_total_overhead_mb(
    parameter_count: int, num_ctx: int, profile: OverheadProfile = FALLBACK_OVERHEAD,
) -> float:
    """Memory (MB) beyond the weights — KV cache, compute graph, runner — at ``num_ctx``."""
    if parameter_count == 0:
        parameter_count = 7_000_000_000

    params_b = parameter_count / 1e9
    return params_b * (profile.fixed_mb_per_b + num_ctx * profile.mb_per_b_per_ctx)


def _fallback_num_ctx(model_name: str) -> int:
    """Placeholder num_ctx when Ollama cannot describe the model.

    Only vision-only models get the smaller default; natively multimodal chat
    models (Gemma 4) are the primary text LLM and take the text default.
    """
    return DEFAULT_TEXT_NUM_CTX if is_text_chat_model(model_name) else DEFAULT_VISION_NUM_CTX


def compute_optimal_num_ctx(model_name: str) -> int:
    """The num_ctx from :func:`decide_num_ctx`, for callers that only need the number."""
    return decide_num_ctx(model_name).num_ctx


def decide_num_ctx(model_name: str) -> NumCtxDecision:
    """
    Compute the optimal num_ctx for a model based on available system resources.

    Strategy:
    1. If model weights fit entirely in GPU → allow up to MAX_NUM_CTX (GPU-fast inference)
    2. If model needs CPU offloading → cap at 8192 (CPU KV lookups are slow,
       more context = more memory = more offloading = slower inference)
    3. In all cases, verify total estimated memory fits in available budget

    Without model info the result is a placeholder (``resolved=False``); see
    :func:`refresh_context_window` for picking up the real value later.
    """
    model_info = get_model_info(model_name)
    if not model_info:
        default = _fallback_num_ctx(model_name)
        logger.info("No model info for '%s', using default num_ctx=%d", model_name, default)
        return NumCtxDecision(default, resolved=False)

    resources = get_system_resources()
    return NumCtxDecision(_size_num_ctx(model_name, model_info, resources), resolved=True)


def _size_num_ctx(model_name: str, model_info: dict, resources: Dict[str, float]) -> int:
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
    profile = overhead_profile(model_name, model_info)

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
        return model_weight_mb + _estimate_total_overhead_mb(param_count, ctx, profile)

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
    remaining_mb = total_budget_mb - model_weight_mb - params_b * profile.fixed_mb_per_b
    mb_per_ctx_token = params_b * profile.mb_per_b_per_ctx
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
    if not model_info:
        return True, "Model info unavailable — proceeding with defaults", _fallback_num_ctx(model_name)

    resources = get_system_resources()
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
        model_info["parameter_count"], MIN_NUM_CTX, overhead_profile(model_name, model_info),
    )
    if model_weight_mb + overhead_at_min > total_available_mb:
        return (
            False,
            f"Model '{model_name}' needs {model_weight_mb + overhead_at_min:.0f}MB even at "
            f"minimum context ({MIN_NUM_CTX}), but only {total_available_mb:.0f}MB available",
            MIN_NUM_CTX,
        )

    return True, f"OK — recommended num_ctx={recommended_ctx}", recommended_ctx


def resolve_num_ctx(model_name: str, explicit: Optional[int] = None) -> int:
    """The context window to request from Ollama for ``model_name``.

    An explicit value a caller passed through wins untouched — the bound below
    exists to stop a silent default choosing something unusable, not to overrule
    a deliberate choice.

    Why this is not optional: llama-index-llms-ollama defaults ``context_window``
    to ``-1``, and ``-1`` means "ask the model for its native context length and
    send that as ``num_ctx`` on every call". Measured on this project's own
    workstation (a 16,376 MiB NVIDIA card), ``qwen3.5:9b`` advertises a
    native 262,144:

      * unbounded — 16 GB resident, split 25%/75% CPU/GPU, 2.6 tok/s generation,
        39-60 s per summariser call
      * bounded at 8,192 — 5.9 GB resident, 100% GPU, 3.24 s cold / 0.58 s warm

    So constructing an ``Ollama`` without a context window is never a neutral
    default; it is a ~100x slowdown of every LLM feature in the product, and it
    reports nothing when it happens.
    """
    if explicit is not None:
        return int(explicit)
    return resolve_num_ctx_decision(model_name).num_ctx


def resolve_num_ctx_decision(model_name: str) -> NumCtxDecision:
    """:func:`decide_num_ctx` that never raises; any failure is an unresolved text default."""
    try:
        return decide_num_ctx(model_name)
    except Exception as e:
        logger.warning(
            "Could not size num_ctx for %r (%s); falling back to %d",
            model_name, e, DEFAULT_TEXT_NUM_CTX,
        )
        return NumCtxDecision(DEFAULT_TEXT_NUM_CTX, resolved=False)


def mark_provisional(llm, resolved: bool = False) -> None:
    """Record that ``llm``'s num_ctx is a placeholder (no-op when ``resolved``).

    Objects that cannot be weakly referenced cannot be tracked and keep their
    placeholder; that only happens for stand-ins, never for an Ollama instance.
    """
    if resolved:
        return
    try:
        with _provisional_lock:
            _provisional_llms[id(llm)] = llm
    except TypeError:
        logger.debug("Cannot track %r for num_ctx refresh (no weakref support)", type(llm))


def is_provisional(llm) -> bool:
    """Whether ``llm`` still carries a placeholder num_ctx."""
    with _provisional_lock:
        return _provisional_llms.get(id(llm)) is llm


def refresh_context_window(llm) -> int:
    """The context window ``llm`` should be used with, re-resolved if it was a placeholder.

    Cheap on the hot path: instances sized from real model info return their
    ``context_window`` untouched. A provisional instance re-runs the sizing;
    once Ollama describes the model, ``context_window`` and the mirrored
    ``additional_kwargs["num_ctx"]`` are updated in place and the instance
    stops being provisional. Until then the placeholder stays in force.

    Callers that read ``context_window`` per request (the chat engine) should
    read it through this function. Independently of that, the first successful
    ``/api/show`` for a model refreshes every provisional instance of it, so a
    boot-time placeholder is corrected as soon as anything asks Ollama about
    the model.
    """
    current = int(getattr(llm, "context_window", 0) or 0)
    if not is_provisional(llm):
        return current
    try:
        decision = resolve_num_ctx_decision(getattr(llm, "model", "") or "")
    except Exception:
        return current
    if not decision.resolved:
        return current
    llm.context_window = decision.num_ctx
    extra = getattr(llm, "additional_kwargs", None)
    if isinstance(extra, dict):
        extra["num_ctx"] = decision.num_ctx
    with _provisional_lock:
        _provisional_llms.pop(id(llm), None)
    logger.info(
        "Re-resolved num_ctx for '%s': %d (placeholder was %d)",
        getattr(llm, "model", ""), decision.num_ctx, current,
    )
    return decision.num_ctx


def _refresh_provisional_instances(model_name: str) -> None:
    """Re-size every provisional instance of ``model_name`` from freshly cached info.

    Called by :func:`get_model_info` after a successful fetch; the sizing it
    triggers hits the cache, so this never issues a second request.
    """
    with _provisional_lock:
        waiting = [llm for llm in _provisional_llms.values()
                   if getattr(llm, "model", None) == model_name]
    for llm in waiting:
        try:
            refresh_context_window(llm)
        except Exception as e:
            logger.debug("Could not refresh num_ctx for '%s': %s", model_name, e)


def build_ollama(model_name: str, **kwargs):
    """Build a llama-index ``Ollama`` whose context window is always bounded.

    Every knob is passed straight through except ``context_window``, which is
    filled in from :func:`resolve_num_ctx` when the caller did not set one. Use
    this instead of constructing ``Ollama`` directly: the library's own default
    is the failure described above, and it is invisible at the call site.

    ``num_ctx`` is mirrored into ``additional_kwargs`` because that dict is
    spread *after* the base kwargs in the library's ``_model_kwargs``, so a
    caller-supplied ``additional_kwargs`` would otherwise be able to silently
    reintroduce an unbounded value.
    """
    from llama_index.llms.ollama import Ollama

    explicit = kwargs.pop("context_window", None)
    if explicit is not None:
        decision = NumCtxDecision(int(explicit), resolved=True)
    else:
        decision = resolve_num_ctx_decision(model_name)
    num_ctx = decision.num_ctx
    additional = dict(kwargs.pop("additional_kwargs", None) or {})
    additional.setdefault("num_ctx", num_ctx)
    if "base_url" not in kwargs:
        from backend.config import OLLAMA_BASE_URL
        kwargs["base_url"] = OLLAMA_BASE_URL
    llm = Ollama(
        model=model_name,
        context_window=num_ctx,
        additional_kwargs=additional,
        **kwargs,
    )
    mark_provisional(llm, decision.resolved)
    return llm


def clear_cache():
    """Clear the model info cache and the unreachable-Ollama backoff."""
    _model_info_cache.clear()
    _unreachable_at.clear()
