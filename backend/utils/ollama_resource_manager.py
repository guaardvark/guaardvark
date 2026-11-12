"""
Adaptive Ollama Resource Manager

Provides resource-aware model loading with dynamic context window sizing.
Prevents OOM situations by estimating model memory requirements against
available system resources before loading.
"""

import logging
import os
import warnings
import re
import threading
import time
import weakref
from typing import Dict, NamedTuple, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Name rules for when Ollama cannot answer. Declared, with what each one cost
# or protects against, in backend/services/model_capability_data.py.
from backend.services.model_capability_data import (  # noqa: E402
    NON_TEXT_NAME_PATTERNS as NON_TEXT_MODEL_PATTERNS,
    THINKING_NAME_PATTERNS as THINKING_MODEL_PATTERNS,
    VISION_NAME_PATTERNS as VISION_MODEL_PATTERNS,
)

# Memory reserves (MB)
GPU_RESERVE_MB = 2048   # 2GB for embedding model + display + system
# RAM kept back for the system and other processes: a quarter of the machine,
# at least RAM_RESERVE_MIN_MB, at most RAM_RESERVE_MB (see ram_reserve_mb). A
# flat 10 GB left nothing on an 8 GB machine, so chat always got the floor.
RAM_RESERVE_MB = 10240
RAM_RESERVE_MIN_MB = 1536
RAM_RESERVE_SHARE = 0.25

# Context window limits
# The web UI's chat prompt (system prompt and tool list before the question)
# measured 2,131-2,431 tokens with qwen3.5:4b on 3.4.2 (2026-11-08); at 2,048
# every reply was cut to one token. 4,096 costs that model 69 MB more than 2,048.
MIN_NUM_CTX = 4096
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
    """Per-architecture memory model: ``fixed_mb_per_b`` + ``mb_per_b_per_ctx * num_ctx``, both scaled by billions of params.

    ``min_mb_per_ctx`` is a floor on the per-token cost, for families whose
    small sizes keep a cache that does not shrink with the parameter count.
    """

    mb_per_b_per_ctx: float
    fixed_mb_per_b: float
    min_mb_per_ctx: float = 0.0

    def mb_per_ctx_token(self, params_b: float) -> float:
        return max(params_b * self.mb_per_b_per_ctx, self.min_mb_per_ctx)


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
#
# qwen35 (Qwen 3.5): full attention on every fourth layer, linear attention on
# the rest. Measured 2026-11-08 on Ollama 0.40.15 with an f16 KV cache, memory
# growth per context token between num_ctx 2,048 and 32,768:
#   * 16 GiB NVIDIA card, flash attention on, nvidia-smi above idle:
#     0.8b 0.0186 MB/token, 2b 0.0154, 4b 0.0357, 9b 0.0367.
#   * CPU only (i5-7400, 7.7 GiB, flash attention off), Ollama's own size:
#     4b 3,004 MB @2048, 3,210 @8192, 3,528 @16384, i.e. 0.0366 MB/token.
# The slope is 4b's per billion params (the highest among 2b, 4b and 9b); the
# floor covers 0.8b and 2b, whose cache does not shrink with their size. The
# /api/tags file size charged as weights covered every CPU total; on the card
# the ~0.6 GB above it is the runner's CUDA context, inside GPU_RESERVE_MB.
OVERHEAD_PROFILES: Dict[str, OverheadProfile] = {
    "gemma4": OverheadProfile(mb_per_b_per_ctx=0.0015, fixed_mb_per_b=110.0),
    "qwen35": OverheadProfile(mb_per_b_per_ctx=0.0087, fixed_mb_per_b=0.0, min_mb_per_ctx=0.019),
}

# Name patterns for families whose /api/show "family" may be missing or generic.
_FAMILY_NAME_PATTERNS = {
    "gemma4": r"gemma[\-_]?4",
    "qwen35": r"qwen[\-_]?3[\.\-_]?5(?!\d)",
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


_MISSING_MODEL_RE = re.compile(r"model ['\"]([^'\"]+)['\"] not found")


def missing_model_user_message(error_str: str) -> Optional[str]:
    """Plain-language reply when Ollama says a model is not installed, else None.

    Ollama answers a request for an absent model with
    "model 'NAME' not found (status code: 404)". Passed through as is, that
    reads like a crash, and nothing says how to get the model.
    """
    match = _MISSING_MODEL_RE.search(error_str or "")
    if not match:
        return None
    name = match.group(1)
    if os.environ.get("GUAARDVARK_DOCKER") == "1":
        how = f"Run: docker compose exec ollama ollama pull {name}"
    else:
        how = f"Run ./start.sh again to download the models this machine needs, or run: ollama pull {name}"
    return f"The model {name} is not installed in Ollama yet, so this can't be answered. {how}"


def is_vision_model(model_name: str) -> bool:
    """Whether this model can accept images. Delegates to the capability resolver.

    Kept as a function because callers import it by name; the answer no longer
    comes from VISION_MODEL_PATTERNS below. Pattern matching got this wrong in
    both directions on real installed models — calling a vision-less
    "gemma4"-named build multimodal, and calling genuinely multimodal Mistral
    and Qwen builds blind. The patterns survive as the resolver's offline
    fallback for when Ollama cannot be reached.
    """
    if not model_name:
        return False
    try:
        from backend.services.model_capability_resolver import sees_natively
        return sees_natively(model_name)
    except Exception:  # resolver unavailable (import cycle at boot, etc.)
        lower = model_name.lower()
        return any(re.search(p, lower) for p in VISION_MODEL_PATTERNS)


def is_text_chat_model(model_name: str) -> bool:
    """Check if a model is suitable as a default text chat LLM.

    The name rule leaves out vision-only and embedding models; when Ollama can
    describe the model, one it lists as embedding or decision is left out too,
    whatever its name (bge-m3 has no "embed" in it; nimble and tev1 answer
    with a label, not a reply). When it cannot, DECISION_NAME_PATTERNS stands
    in for the decision capability.
    """
    if not model_name:
        return False
    lower = model_name.lower()
    if any(re.search(p, lower) for p in NON_TEXT_MODEL_PATTERNS):
        return False
    from backend.services.model_capabilities import capabilities_for, decides_by_name
    rec = capabilities_for(model_name, with_vision=False)
    if rec.exists and rec.capabilities:
        return not (rec.embedding or rec.decision)
    return not (rec.exists and rec.embedding) and not decides_by_name(model_name)


def known_not_to_chat(model_name: str) -> bool:
    """True when Ollama describes the model and it cannot answer a chat
    request: no "completion", or an embedding or decision model.

    For a model a person chose. is_text_chat_model is the rule for picking a
    default and its name rule also leaves out vision-named models (qwen3-vl,
    llava) that chat fine. A model Ollama cannot describe is not known either way.
    """
    if not model_name:
        return False
    from backend.services.model_capabilities import capabilities_for
    rec = capabilities_for(model_name, with_vision=False)
    if not (rec.exists and rec.capabilities):
        return False
    return not rec.completion or rec.embedding or rec.decision


def _gpu_memory_mb() -> Optional[tuple]:
    """(free_mb, total_mb) of the first visible GPU, or None."""
    index = 0
    visible = (os.environ.get("CUDA_VISIBLE_DEVICES") or "").split(",")[0].strip()
    if visible.isdigit():
        index = int(visible)
    try:
        with warnings.catch_warnings():
            # The pynvml wheel warns at import that it is deprecated in favour of
            # nvidia-ml-py, which ships the same module; here it is only noise.
            warnings.simplefilter("ignore", FutureWarning)
            import pynvml
        pynvml.nvmlInit()
        try:
            info = pynvml.nvmlDeviceGetMemoryInfo(pynvml.nvmlDeviceGetHandleByIndex(index))
            return info.free / (1024 * 1024), info.total / (1024 * 1024)
        finally:
            pynvml.nvmlShutdown()
    except Exception as e:
        logger.debug("Could not query GPU memory via NVML: %s", e)
    try:
        import subprocess
        out = subprocess.check_output(
            ["nvidia-smi", f"--id={index}", "--query-gpu=memory.free,memory.total",
             "--format=csv,nounits,noheader"],
            timeout=5, text=True,
        )
        free, total = (float(x) for x in out.strip().split(","))
        return free, total
    except Exception:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            mem_free, mem_total = torch.cuda.mem_get_info(0)
            return mem_free / (1024 * 1024), mem_total / (1024 * 1024)
    except Exception as e:
        logger.debug("Could not query GPU memory via torch: %s", e)
    return None


# Ollama places a model when it loads it. One loaded while the GPU was short
# (an image pipeline holding VRAM) runs partly or wholly on the CPU until it
# unloads, and every chat renews its keep-alive, so it can stay there long
# after the room came back.
_PLACEMENT_CHECK_EVERY_S = 60.0
_PLACEMENT_MARGIN_MB = 1024
_PLACEMENT_RELOAD_WINDOW_S = 600.0
_PLACEMENT_BACKOFF_S = 1800.0
_placement_checked: Dict[str, float] = {}
_placement_reloaded: Dict[str, float] = {}
_placement_lock = threading.Lock()


def reload_onto_gpu_if_room(model_name: str) -> bool:
    """Unload a model that sits partly on the CPU when the GPU now has room for
    all of it, so the next request loads it whole onto the GPU.

    Checks at most once a minute per model. A model still off the GPU at the
    first check after such a reload, with room to spare, is not a placement
    Ollama made under pressure (wholly on the CPU: Ollama is not using the
    GPU); that is logged and the check backs off for half an hour instead of
    reloading on every chat. True when the model was unloaded.
    """
    if not model_name:
        return False
    now = time.time()
    with _placement_lock:
        if now - _placement_checked.get(model_name, 0.0) < _PLACEMENT_CHECK_EVERY_S:
            return False
        _placement_checked[model_name] = now
    base = get_ollama_base_url()
    try:
        models = requests.get(f"{base}/api/ps", timeout=3).json().get("models") or []
    except Exception:
        return False
    entry = next((m for m in models if model_name in (m.get("name"), m.get("model"))), None)
    if not entry:
        return False
    size, on_gpu = int(entry.get("size") or 0), int(entry.get("size_vram") or 0)
    if size <= 0 or on_gpu >= size:
        return False
    on_cpu_mb = (size - on_gpu) / (1024 * 1024)
    mem = _gpu_memory_mb()
    if not mem:
        return False
    free_mb = mem[0]
    share = round(100 * (size - on_gpu) / size)
    if free_mb < on_cpu_mb + _PLACEMENT_MARGIN_MB:
        logger.info(
            "%s runs %d%% on the CPU; the GPU has %.0f MB free, %.0f MB short of moving it",
            model_name, share, free_mb, on_cpu_mb + _PLACEMENT_MARGIN_MB - free_mb,
        )
        return False
    with _placement_lock:
        if now - _placement_reloaded.pop(model_name, 0.0) < _PLACEMENT_RELOAD_WINDOW_S:
            cause = ("Ollama is not using the GPU; restart Ollama from Plugins" if on_gpu == 0
                     else "Ollama keeps it there despite the free memory")
            logger.warning(
                "%s came back %d%% on the CPU after a reload although the GPU has %.0f MB "
                "free: %s. Not reloading it again for %d minutes.",
                model_name, share, free_mb, cause, _PLACEMENT_BACKOFF_S // 60,
            )
            _placement_checked[model_name] = now + _PLACEMENT_BACKOFF_S
            return False
        _placement_reloaded[model_name] = now
    try:
        requests.post(f"{base}/api/generate", json={"model": model_name, "keep_alive": 0}, timeout=30)
    except Exception as e:
        logger.warning("Could not unload %s to move it onto the GPU: %s", model_name, e)
        return False
    logger.warning(
        "%s was %d%% on the CPU (%.0f MB) while the GPU has %.0f MB free; unloaded so "
        "the next request loads it onto the GPU",
        model_name, share, on_cpu_mb, free_mb,
    )
    return True


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

    # GPU memory. NVML and nvidia-smi read it without touching CUDA; asking torch
    # first created a ~200 MB CUDA context in every process that imported the
    # indexing service (each MCP server, scripts) just to read two numbers.
    gpu = _gpu_memory_mb()
    if gpu:
        result["gpu_free_mb"], result["gpu_total_mb"] = gpu

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
        embedding_length = 0
        for key, value in model_info_raw.items():
            # "<arch>.embedding_length" only: multimodal models also report
            # "<arch>.vision.embedding_length" and "<arch>.audio.embedding_length".
            if key.endswith(".embedding_length") and key.count(".") == 1:
                embedding_length = int(value)
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
            "embedding_length": embedding_length,
            "architecture": details.get("family", "unknown"),
            "families": details.get("families", []),
            "quantization": details.get("quantization_level", "unknown"),
            # Read from the payload we just fetched, never from a name pattern.
            # This used to call is_vision_model(), which now asks the capability
            # resolver, which asks this function — a cycle. It was also simply
            # wrong: Ollama has already told us the answer two lines up.
            "is_vision": ("vision" in capabilities
                          or "clip" in str(details.get("families", [])).lower()),
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


# Ollama's manifest digests are compared on this many leading hex characters,
# the length `ollama list` prints.
DIGEST_PREFIX_LEN = 12
_LLAMACPP_ALIAS = re.compile(r"^llamacpp:([0-9a-f]{12,64})$")
_HEX_DIGEST = re.compile(r"^[0-9a-f]{12,64}$")


def _short_digest(value) -> str:
    digest = str(value or "").strip().lower()
    if digest.startswith("sha256:"):
        digest = digest[len("sha256:"):]
    return digest[:DIGEST_PREFIX_LEN] if _HEX_DIGEST.match(digest) else ""


def _full_model_name(model_name: str) -> str:
    name = (model_name or "").strip().lower()
    return name if not name or ":" in name else f"{name}:latest"


def _ollama_tags() -> list:
    """Ollama's /api/tags "models" entries; [] when it cannot be read."""
    try:
        resp = requests.get(f"{get_ollama_base_url()}/api/tags", timeout=5)
        if resp.ok:
            return list(resp.json().get("models") or [])
    except Exception as e:  # noqa: BLE001
        logger.debug("Could not list Ollama models: %s", e)
    return []


def model_digests(model_name: str, tags: Optional[list] = None) -> frozenset:
    """Short manifest digests Ollama lists under ``model_name``.

    One name is not one build, and one build is not one name: Ollama 0.40
    lists a pulled tag under two manifests (a ggml and a llamacpp build of the
    same pull, so gemma4:e2b appears twice in /api/tags) and adds a
    ``llamacpp:<digest>`` alias for the llamacpp one. The digests say which
    build a name is. A name without a tag is read as ``:latest``. ``tags`` is
    an /api/tags "models" list the caller already has; empty when Ollama
    cannot be reached and the name carries no digest of its own.
    """
    name = _full_model_name(model_name)
    if not name:
        return frozenset()
    if tags is None:
        tags = _ollama_tags()
    found = {
        _short_digest(entry.get("digest"))
        for entry in tags
        if name in (str(entry.get("name") or "").lower(), str(entry.get("model") or "").lower())
    }
    alias = _LLAMACPP_ALIAS.match(name)
    if alias:
        found.add(alias.group(1)[:DIGEST_PREFIX_LEN])
    found.discard("")
    return frozenset(found)


def installed_name_for(model_name: str, tags: Optional[list] = None) -> Optional[str]:
    """The name /api/tags lists for ``model_name``, or None when it is not installed.

    The same name wins; a name without a tag matches ``:latest``. A
    ``llamacpp:<digest>`` alias resolves to the listed name of that build:
    Ollama 0.40 listed such aliases and a saved chat model can still be one,
    but later versions answer to the alias without listing it, so a lookup by
    name alone calls the model missing. ``tags`` is an /api/tags "models" list
    the caller already has.
    """
    full = _full_model_name(model_name)
    if not full:
        return None
    if tags is None:
        tags = _ollama_tags()
    names = [str(entry.get("name") or "") for entry in tags if entry.get("name")]
    for name in names:
        if _full_model_name(name) == full:
            return name
    alias = _LLAMACPP_ALIAS.match(full)
    if not alias:
        return None
    prefix = alias.group(1)[:DIGEST_PREFIX_LEN]
    same_build = sorted(
        str(entry.get("name") or "")
        for entry in tags
        if entry.get("name") and _short_digest(entry.get("digest")) == prefix
    )
    return same_build[0] if same_build else None


def is_llamacpp_alias(model_name: str) -> bool:
    """Whether ``model_name`` is a ``llamacpp:<digest>`` build alias."""
    return bool(_LLAMACPP_ALIAS.match((model_name or "").strip().lower()))


def listed_name_for_alias(model_name: str, tags: Optional[list] = None) -> str:
    """``model_name``, or for a ``llamacpp:<digest>`` alias the plain name Ollama
    lists for the same build, when it lists one.

    Ollama 0.40.1 listed these aliases in /api/tags beside the plain name, so a
    model picker could offer and save one. Chat under the alias works, but every
    reader that matches by name (Keep ready, the GPU orchestrator, the startup
    check) misses it. Only an alias costs a /api/tags call.
    """
    alias = _LLAMACPP_ALIAS.match((model_name or "").strip().lower())
    if not alias:
        return model_name
    if tags is None:
        tags = _ollama_tags()
    prefix = alias.group(1)[:DIGEST_PREFIX_LEN]
    plain = sorted(
        str(entry.get("name") or "")
        for entry in tags
        if entry.get("name") and not is_llamacpp_alias(str(entry.get("name")))
        and _short_digest(entry.get("digest")) == prefix
    )
    return plain[0] if plain else model_name


def model_families(model_name: str, tags: Optional[list] = None) -> frozenset:
    """The model families Ollama reports for ``model_name`` (``details.family``).

    A set, because one pull listed under two manifests can report two families
    (embeddinggemma's appear as gemma3 and gemma-embedding). Manifests are
    matched by name and by the digests :func:`model_digests` finds. Empty when
    Ollama cannot be reached or the name is not installed.
    """
    name = _full_model_name(model_name)
    if not name:
        return frozenset()
    if tags is None:
        tags = _ollama_tags()
    digests = model_digests(name, tags)
    found = set()
    for entry in tags:
        names = {str(entry.get("name") or "").lower(), str(entry.get("model") or "").lower()}
        if name in names or _short_digest(entry.get("digest")) in digests:
            family = str((entry.get("details") or {}).get("family") or "").strip().lower()
            if family:
                found.add(family)
    return frozenset(found)


def same_model(a: str, b: str, tags: Optional[list] = None) -> Optional[bool]:
    """Whether two Ollama model names are the same build.

    True when they are the same name or share a manifest digest, False when
    both resolve to digests and share none, None when either cannot be
    resolved (not installed, Ollama unreachable)."""
    name_a, name_b = _full_model_name(a), _full_model_name(b)
    if not name_a or not name_b:
        return None
    if name_a == name_b:
        return True
    if tags is None:
        tags = _ollama_tags()
    digests_a, digests_b = model_digests(name_a, tags), model_digests(name_b, tags)
    if not digests_a or not digests_b:
        return None
    return bool(digests_a & digests_b)


# /api/ps is read at most this often; a decision asks several questions per turn.
RESIDENT_TTL_S = 2.0
# A model Ollama drops within this many seconds counts as not resident: a
# question sent to it now could arrive after it is gone and load it again.
RESIDENT_EXPIRY_MARGIN_S = 5.0
_RESIDENT_TIMEOUT_S = 1.0
_resident_cache: Dict[str, object] = {"at": 0.0, "models": []}
_resident_lock = threading.Lock()
_FRACTION = re.compile(r"(\.\d{6})\d+")


def resident_models(ttl_s: float = RESIDENT_TTL_S) -> list:
    """Ollama's /api/ps "models" entries, read at most once per ``ttl_s``.

    [] when Ollama cannot be reached; a failed read is cached like a good one,
    so a stopped Ollama costs one connection attempt per ``ttl_s``.
    """
    now = time.monotonic()
    with _resident_lock:
        if now - float(_resident_cache["at"]) < ttl_s:
            return list(_resident_cache["models"])
    models: list = []
    try:
        resp = requests.get(f"{get_ollama_base_url()}/api/ps", timeout=_RESIDENT_TIMEOUT_S)
        if resp.ok:
            models = list(resp.json().get("models") or [])
    except Exception as e:  # noqa: BLE001
        logger.debug("Could not read Ollama's loaded models: %s", e)
    with _resident_lock:
        _resident_cache.update(at=time.monotonic(), models=models)
    return list(models)


def _expires_in_s(entry: dict) -> Optional[float]:
    """Seconds until Ollama drops ``entry``, or None when it does not say."""
    from datetime import datetime, timezone

    raw = str(entry.get("expires_at") or "").strip()
    if not raw:
        return None
    try:
        # Ollama writes nanoseconds; older Pythons parse at most microseconds.
        when = datetime.fromisoformat(_FRACTION.sub(r"\1", raw.replace("Z", "+00:00")))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (when - datetime.now(timezone.utc)).total_seconds()


def loaded_entry(model_name: str, tags: Optional[list] = None) -> Optional[dict]:
    """The /api/ps entry for ``model_name`` when Ollama has it loaded anywhere
    (GPU, CPU or split), else None.

    Ollama 0.40 can list a loaded alias under a name nobody asked for
    (``sha256-<digest>:latest`` for a ``llamacpp:<digest>`` model), so an entry
    matches when its name is the same name, or when ``model_name`` resolves to
    exactly one manifest digest and the entry is that build. A name listed
    under two builds (gemma4:e2b on 0.40) matches only by name: a request by
    that name may load the other build. Another size or tag of the same family
    never matches (gemma4:e2b is not gemma4:12b). ``tags`` is an /api/tags list
    the caller already has.
    """
    name = _full_model_name(model_name)
    if not name:
        return None
    running = resident_models()
    for entry in running:
        names = {_full_model_name(str(entry.get(k) or "")) for k in ("name", "model")}
        if name in names:
            return dict(entry)
    if running:
        digests = model_digests(name, tags)
        if len(digests) == 1:
            for entry in running:
                if _short_digest(entry.get("digest")) in digests:
                    return dict(entry)
    return None


def resident_entry(model_name: str, tags: Optional[list] = None) -> Optional[dict]:
    """The /api/ps entry for ``model_name`` when it is loaded and staying loaded, else None.

    Loaded means: matched by :func:`loaded_entry`, on the GPU (``size_vram`` > 0)
    and not dropped within RESIDENT_EXPIRY_MARGIN_S.
    """
    match = loaded_entry(model_name, tags)
    if match is None:
        return None
    try:
        if int(match.get("size_vram") or 0) <= 0:
            return None
    except (TypeError, ValueError):
        return None
    remaining = _expires_in_s(match)
    if remaining is not None and remaining <= RESIDENT_EXPIRY_MARGIN_S:
        return None
    return match


def model_supports_tools(model_name: str) -> bool:
    """Check if a model supports native function calling via Ollama's capabilities API
    (or the model's declared row; see model_capabilities)."""
    from backend.services.model_capabilities import capabilities_for
    return capabilities_for(model_name, with_vision=False).tools


def model_supports_thinking(model_name: str) -> bool:
    """Whether ``model_name`` reasons in Ollama's ``thinking`` channel.

    A name match against :data:`THINKING_MODEL_PATTERNS` answers without any
    I/O; otherwise Ollama's capabilities list decides. An unreachable server
    or an unknown model is ``False``: the ``think`` request field is only sent
    to models known to accept it.
    """
    if not model_name:
        return False
    from backend.services.model_capabilities import capabilities_for, thinks_by_name
    if thinks_by_name(model_name):
        return True
    return capabilities_for(model_name, with_vision=False).thinking


def think_off_value(model_name: str):
    """What to send as ``think`` when thinking is meant to be off.

    ``False``, or for an architecture whose thinking cannot be turned off
    (:data:`model_capability_data.THINK_OFF_LEVEL_BY_ARCH`) the least level it
    takes. A level means the model still reasons, so the caller leaves room for
    it in num_predict.
    """
    from backend.services.model_capability_data import THINK_OFF_LEVEL_BY_ARCH
    if not model_name:
        return False
    try:
        from backend.services.model_capabilities import capabilities_for
        arch = getattr(capabilities_for(model_name, with_vision=False), "architecture", "")
    except Exception as e:  # noqa: BLE001 - a failed lookup must not break a chat call
        logger.debug("architecture lookup failed for %r: %s", model_name, e)
        return False
    return THINK_OFF_LEVEL_BY_ARCH.get(str(arch or "").lower(), False)


def thinking_kwargs(model_name: str) -> dict:
    """Constructor kwargs that turn thinking off for a llama-index ``Ollama``.

    ``{"thinking": False}`` for a thinking-capable model, ``{}`` for any other,
    so ``Ollama(model=name, **thinking_kwargs(name))`` never sends a flag a
    model does not accept. Spread it last; a caller that wants the model's own
    default passes ``thinking=None`` explicitly instead of using this.
    """
    return {"thinking": False} if model_supports_thinking(model_name) else {}


def think_payload(model_name: str) -> dict:
    """The raw-API twin of :func:`thinking_kwargs`: ``{"think": False}`` or ``{}``.

    For ``ollama.chat(**kw)``, ``ollama.Client().generate(**kw)`` and hand-built
    ``/api/chat`` or ``/api/generate`` request bodies.
    """
    return {"think": False} if model_supports_thinking(model_name) else {}


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
    return params_b * profile.fixed_mb_per_b + num_ctx * profile.mb_per_ctx_token(params_b)


def _fallback_num_ctx(model_name: str) -> int:
    """Placeholder num_ctx when Ollama cannot describe the model.

    Only vision-only models get the smaller default; natively multimodal chat
    models (Gemma 4) are the primary text LLM and take the text default.
    """
    return DEFAULT_TEXT_NUM_CTX if is_text_chat_model(model_name) else DEFAULT_VISION_NUM_CTX


def request_options(model_name: str, **overrides) -> dict:
    """``options`` for an Ollama request, with ``num_ctx`` always set.

    A request that omits ``num_ctx`` runs at whatever the Modelfile baked:
    Ollama's 4,096 default on library tags (long prompts are silently cut to
    the last half of that), or a community tag's 262,144 (a 12B model then
    offloads its KV cache to RAM). Either way the runner reloads when the next
    request asks for a different size. Every caller that is not the chat
    engine's sized path builds its options here so no request leaves unsized.
    An explicit ``num_ctx`` in ``overrides`` wins.
    """
    opts = {k: v for k, v in overrides.items() if v is not None}
    if not opts.get("num_ctx"):
        opts["num_ctx"] = compute_optimal_num_ctx(model_name)
    return opts


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

    A size set for the model in GUAARDVARK_NUM_CTX replaces all of this.
    """
    override = num_ctx_override(model_name)
    if override:
        logger.info("Context for '%s': num_ctx=%d from %s, not sized", model_name, override, NUM_CTX_ENV)
        return NumCtxDecision(override, resolved=True)

    model_info = get_model_info(model_name)
    if not model_info:
        default = _fallback_num_ctx(model_name)
        logger.info("No model info for '%s', using default num_ctx=%d", model_name, default)
        return NumCtxDecision(default, resolved=False)

    entry = loaded_entry(model_name)
    own_ram_mb = _own_ram_mb(entry)
    resources = _sizing_resources(model_name)
    num_ctx = _size_num_ctx(model_name, model_info, resources, log=own_ram_mb <= 0)
    if own_ram_mb > 0:
        # A loaded model's RAM share is its own, not "used": a machine without
        # a GPU keeps the chat model loaded, and sized against what was left
        # beside it, qwen3.5:4b on 7.7 GiB got the floor. Counted back, it only
        # keeps the model at the window it is loaded with: Linux still reports
        # most of a loaded model's memory as available, so growing on it would
        # disagree with the size picked before the model loaded, and every
        # change of size reloads the model (qwen3.5:4b on 8 GB: 7,168 unloaded,
        # 8,192 loaded; a reload there costs 30-45 s; measured 2026-11-08).
        with_own = _size_num_ctx(
            model_name, model_info,
            dict(resources, ram_free_mb=resources["ram_free_mb"] + own_ram_mb), log=False,
        )
        kept = min(int(entry.get("context_length") or 0), with_own)
        logger.info(
            "Adaptive context for loaded '%s': num_ctx=%d (loaded at %s, %d without its "
            "own %.0fMB, %d with it)",
            model_name, max(num_ctx, kept), entry.get("context_length"), num_ctx, own_ram_mb, with_own,
        )
        num_ctx = max(num_ctx, kept)
    return NumCtxDecision(num_ctx, resolved=True)


def _own_ram_mb(entry: Optional[dict]) -> float:
    """RAM (MB) a loaded model holds outside the GPU, from its /api/ps entry."""
    if not entry:
        return 0.0
    try:
        size_mb = int(entry.get("size") or 0) / (1024 * 1024)
        vram_mb = int(entry.get("size_vram") or 0) / (1024 * 1024)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, size_mb - vram_mb)


def _sizing_resources(model_name: str, own_ram_mb: float = 0.0) -> Dict[str, float]:
    """:func:`get_system_resources` with ``own_ram_mb`` added to free RAM, and
    the model's VRAM when the resident hook is on."""
    resources = get_system_resources()
    if own_ram_mb > 0:
        resources = dict(resources, ram_free_mb=resources["ram_free_mb"] + own_ram_mb)
    if _count_resident_model():
        entry = resident_entry(model_name)
        if entry:
            # The model's own weights and cache are part of "used": sized
            # against what was left, a loaded gemma4:12b looked like it needed
            # CPU offload and got 8,192. On a 16 GB card it takes 7.80 GiB at
            # 8,192 and 7.84 GiB at 32,768, all on the GPU (measured 2026-11-05).
            resources = dict(resources, gpu_free_mb=resources["gpu_free_mb"]
                             + int(entry.get("size_vram") or 0) / (1024 * 1024))
    return resources


def _count_resident_model() -> bool:
    """Hook, off by default: size a loaded model counting its VRAM as free
    too (GUAARDVARK_CTX_COUNT_RESIDENT=1). Its RAM share always keeps it at
    its loaded window (see decide_num_ctx). Off, a model on the GPU is sized
    against what is left beside it."""
    return os.environ.get("GUAARDVARK_CTX_COUNT_RESIDENT", "").strip().lower() in ("1", "true", "yes", "on")


# Hook, off by default: a fixed num_ctx for the models named in
# GUAARDVARK_NUM_CTX, as comma-separated name=size pairs:
#   GUAARDVARK_NUM_CTX=qwen3.8:27b=32768,gemma4:12b=65536
# A name without a tag means :latest. The size is sent as given, above
# MAX_NUM_CTX too: sizing estimates from a per-family profile, and on a card
# with room to spare the estimate sets the limit, not the memory.
NUM_CTX_ENV = "GUAARDVARK_NUM_CTX"
_num_ctx_overrides_parsed: Dict[str, Dict[str, int]] = {}


def _num_ctx_overrides() -> Dict[str, int]:
    raw = os.environ.get(NUM_CTX_ENV, "").strip()
    if not raw:
        return {}
    parsed = _num_ctx_overrides_parsed.get(raw)
    if parsed is not None:
        return parsed
    parsed = {}
    for item in filter(None, (part.strip() for part in raw.split(","))):
        name, sep, value = item.rpartition("=")
        try:
            size = int(value.strip())
        except ValueError:
            size = 0
        if not sep or not name.strip() or size <= 0:
            logger.warning("%s: ignoring %r, expected name=size such as qwen3.5:9b=16384", NUM_CTX_ENV, item)
            continue
        parsed[_full_model_name(name)] = size
    _num_ctx_overrides_parsed[raw] = parsed
    return parsed


def num_ctx_override(model_name: str) -> Optional[int]:
    """The num_ctx GUAARDVARK_NUM_CTX sets for ``model_name``, or None."""
    overrides = _num_ctx_overrides()
    return overrides.get(_full_model_name(model_name)) if overrides else None


def ram_reserve_mb(ram_total_mb: float) -> float:
    """RAM to keep back for the system and other processes on a machine with
    ``ram_total_mb``; the full RAM_RESERVE_MB when the total is unknown."""
    if not ram_total_mb or ram_total_mb <= 0:
        return float(RAM_RESERVE_MB)
    return min(float(RAM_RESERVE_MB), max(float(RAM_RESERVE_MIN_MB), RAM_RESERVE_SHARE * ram_total_mb))


def _min_num_ctx(model_info: dict) -> int:
    """MIN_NUM_CTX, or the model's own window when it was trained on a shorter one."""
    native_ctx = model_info.get("native_context", 0)
    return min(MIN_NUM_CTX, native_ctx) if native_ctx > 0 else MIN_NUM_CTX


def _size_num_ctx(model_name: str, model_info: dict, resources: Dict[str, float], log: bool = True) -> int:
    gpu_free_mb = resources["gpu_free_mb"]
    gpu_budget_mb = max(0, gpu_free_mb - GPU_RESERVE_MB)
    ram_reserve = ram_reserve_mb(resources.get("ram_total_mb", 0.0))
    ram_budget_mb = max(0, resources["ram_free_mb"] - ram_reserve)
    total_budget_mb = gpu_budget_mb + ram_budget_mb

    native_ctx = model_info.get("native_context", 0)
    floor = _min_num_ctx(model_info)

    if total_budget_mb <= 0:
        if log:
            logger.warning(
                "Very low available memory (GPU free: %.0fMB, RAM free: %.0fMB). Using minimum context.",
                gpu_free_mb, resources["ram_free_mb"],
            )
        return floor

    model_weight_mb = model_info["size_mb"]
    param_count = model_info["parameter_count"]
    params_b = param_count / 1e9 if param_count > 0 else 7.0
    profile = overhead_profile(model_name, model_info)

    if model_weight_mb > total_budget_mb:
        if log:
            logger.warning(
                "Model '%s' weights (%.0fMB) exceed available budget (%.0fMB). Using minimum context.",
                model_name, model_weight_mb, total_budget_mb,
            )
        return floor

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
    mb_per_ctx_token = profile.mb_per_ctx_token(params_b)
    if mb_per_ctx_token > 0:
        max_ctx_by_memory = int(remaining_mb / mb_per_ctx_token)
    else:
        max_ctx_by_memory = ceiling

    # Apply floor, ceiling, memory limit, and round to nearest 1024
    optimal = min(max_ctx_by_memory, ceiling)
    optimal = max(optimal, floor)
    optimal = (optimal // 1024) * 1024
    optimal = max(optimal, floor)

    if not log:
        return optimal
    est_total = _total_at_ctx(optimal)
    logger.info(
        "Adaptive context for '%s': num_ctx=%d (native=%d, ceiling=%d, "
        "model=%.0fMB, est_total=%.0fMB, gpu_only=%s, gpu_free=%.0fMB, ram_free=%.0fMB, "
        "ram_reserve=%.0fMB)",
        model_name, optimal, native_ctx, ceiling,
        model_weight_mb, est_total, fits_in_gpu,
        gpu_free_mb, resources["ram_free_mb"], ram_reserve,
    )

    return optimal


def validate_model_before_load(model_name: str) -> Tuple[bool, str, int]:
    """
    Validate that a model can be safely loaded with available resources.

    Returns:
        (safe_to_load, reason, recommended_num_ctx)

    A size set in GUAARDVARK_NUM_CTX is the recommendation in every case;
    ``safe_to_load`` and ``reason`` still report the memory check.
    """
    override = num_ctx_override(model_name)
    model_info = get_model_info(model_name)
    if not model_info:
        return True, "Model info unavailable — proceeding with defaults", override or _fallback_num_ctx(model_name)

    resources = _sizing_resources(model_name, _own_ram_mb(loaded_entry(model_name)))
    total_available_mb = (
        max(0, resources["gpu_free_mb"] - GPU_RESERVE_MB) +
        max(0, resources["ram_free_mb"] - ram_reserve_mb(resources.get("ram_total_mb", 0.0)))
    )
    floor = _min_num_ctx(model_info)

    model_weight_mb = model_info["size_mb"]

    # Check if model weights alone exceed budget
    if model_weight_mb > total_available_mb:
        return (
            False,
            f"Model weights ({model_weight_mb:.0f}MB) exceed available memory "
            f"({total_available_mb:.0f}MB = {resources['gpu_free_mb']:.0f}MB GPU + "
            f"{resources['ram_free_mb']:.0f}MB RAM - reserves)",
            override or floor,
        )

    recommended_ctx = compute_optimal_num_ctx(model_name)

    # Check if we can give at least minimum context
    overhead_at_min = _estimate_total_overhead_mb(
        model_info["parameter_count"], floor, overhead_profile(model_name, model_info),
    )
    if model_weight_mb + overhead_at_min > total_available_mb:
        return (
            False,
            f"Model '{model_name}' needs {model_weight_mb + overhead_at_min:.0f}MB even at "
            f"minimum context ({floor}), but only {total_available_mb:.0f}MB available",
            override or floor,
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

    ``thinking`` defaults to off for thinking-capable models (see
    :data:`THINKING_MODEL_PATTERNS` for what leaving it on cost). A caller that
    passes ``thinking`` explicitly, ``None`` included, is left alone: the chat
    page's per-chat toggle and any deliberate choice still win.
    """
    from llama_index.llms.ollama import Ollama

    if "thinking" not in kwargs:
        kwargs.update(thinking_kwargs(model_name))
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
    """Clear the model info cache, the unreachable-Ollama backoff and the /api/ps cache."""
    _model_info_cache.clear()
    _unreachable_at.clear()
    with _resident_lock:
        _resident_cache.update(at=0.0, models=[])
