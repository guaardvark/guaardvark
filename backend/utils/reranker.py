"""Cross-encoder reranking for retrieval results.

The fused retriever scores a query and a passage independently (bi-encoder plus
BM25); a cross-encoder reads the pair together and is markedly better at deciding
relevance. It is applied to the candidate pool AFTER filtering and dedup, and
BEFORE MMR -- rerank decides what is relevant, MMR decides what is diverse.

The model competes for VRAM with image and video generation, so loading is
admitted against free VRAM and falls back to CPU rather than failing a query.
Every outcome is reported back to the caller for the retrieval trace: a rerank
that did not happen must never look like one that did.

Residency is reclaimable. `unload()` releases the weights and the orchestrator
calls it -- both from its periodic registry (via `status()`) and directly when a
render needs the card. Without that, a query that happened to run while the card
was quiet would take ~1.1 GB of VRAM for the life of the process and starve every
image batch behind it; that is exactly what F-RAG-10 was.
"""

import gc
import logging
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"

# Working set a predict() pass needs on top of the weights. Measured at ~40 MB for
# a 2-pair batch; a full candidate pool at max_length is larger, so the slot is
# priced with room rather than with the idle figure.
_ACTIVATION_HEADROOM_MB = 128

_lock = threading.Lock()
_model = None
_model_device: Optional[str] = None
_load_failed_reason: Optional[str] = None
_model_vram_mb: int = 0     # weights + headroom, 0 when not GPU-resident
_inflight: int = 0          # predict() passes in progress; blocks unload


def is_enabled() -> bool:
    return os.environ.get("GUAARDVARK_RERANK_CROSS_ENCODER", "true").lower() == "true"


def model_name() -> str:
    return os.environ.get("GUAARDVARK_RERANK_MODEL", DEFAULT_MODEL)


def _pick_device() -> str:
    """GPU only when there is comfortable headroom; otherwise CPU.

    The floor is deliberately above the model's own footprint: admitting it into
    the last free gigabyte would just move an OOM onto whichever generation job
    starts next. This choice is made per load, not once per process -- an unload
    releases it, and the next query re-decides against the card as it is then.
    """
    floor_mb = int(os.environ.get("GUAARDVARK_RERANK_MIN_VRAM_MB", "3000"))
    try:
        from backend.services.gpu_resource_coordinator import has_gpu, get_available_vram
        if not has_gpu():
            return "cpu"
        info = get_available_vram()
        if info.get("success") and info.get("available_mb", 0) >= floor_mb:
            return "cuda"
        logger.info(
            "Reranker: %s MB free < %s MB floor — loading on CPU",
            info.get("available_mb"), floor_mb,
        )
        return "cpu"
    except Exception as e:
        logger.debug("Reranker device probe failed (%s); using CPU", e)
        return "cpu"


def _measure_vram_mb(model) -> int:
    """Weights actually on the GPU, plus an activation allowance. 0 if not resident."""
    try:
        import torch
        params = getattr(model, "model", None)
        if params is None:
            return 0
        total = sum(
            p.numel() * p.element_size()
            for p in params.parameters()
            if p.device.type == "cuda"
        )
        if total <= 0:
            return 0
        return int(total // (1024 * 1024)) + _ACTIVATION_HEADROOM_MB
    except Exception:
        return 0


def _get_model():
    """Load the cross-encoder once. Returns None if unavailable (never raises)."""
    global _model, _model_device, _load_failed_reason, _model_vram_mb
    if _model is not None or _load_failed_reason is not None:
        return _model
    with _lock:
        if _model is not None or _load_failed_reason is not None:
            return _model
        try:
            from sentence_transformers import CrossEncoder
            device = _pick_device()
            name = model_name()
            kwargs: Dict[str, Any] = {"device": device, "max_length": 512}
            if device == "cuda":
                # fp16 halves the card cost (2424 MB -> 1334 MB measured on
                # bge-reranker-v2-m3) and reranking is a ranking decision, not an
                # arithmetic one. CPU stays fp32: half precision there is slower,
                # not faster, and costs no VRAM to avoid.
                import torch
                kwargs["model_kwargs"] = {"torch_dtype": torch.float16}
            logger.info("Reranker: loading %s on %s", name, device)
            _model = CrossEncoder(name, **kwargs)
            _model_device = device
            _model_vram_mb = _measure_vram_mb(_model) if device == "cuda" else 0
            logger.info(
                "Reranker: ready (%s on %s%s)", name, device,
                f", ~{_model_vram_mb}MB VRAM" if _model_vram_mb else "",
            )
        except Exception as e:
            _load_failed_reason = f"{e.__class__.__name__}: {str(e)[:160]}"
            logger.warning("Reranker unavailable — %s", _load_failed_reason)
            _model = None
    return _model


def status() -> Dict[str, Any]:
    """What the reranker currently holds. Cheap; safe to poll."""
    return {
        "loaded": _model is not None,
        "device": _model_device,
        "vram_mb": _model_vram_mb,
        "in_use": _inflight,
        "model": model_name(),
    }


def unload() -> Dict[str, Any]:
    """Release the model. Returns what happened; never raises.

    ``unloaded`` is True whenever nothing is resident afterwards -- including the
    case where nothing was loaded to begin with -- so a caller dropping a stale
    registry slot can trust it. It is False only when a predict() pass is in
    flight: dropping the module reference then would not free the memory anyway
    (the running call holds its own reference) and would misreport what was freed.
    """
    global _model, _model_device, _load_failed_reason, _model_vram_mb
    with _lock:
        if _model is None:
            return {"unloaded": True, "freed_mb": 0, "reason": "not loaded"}
        if _inflight > 0:
            return {"unloaded": False, "freed_mb": 0, "reason": f"in use ({_inflight} in flight)"}

        freed = _model_vram_mb
        was_device = _model_device
        _model = None
        _model_device = None
        _model_vram_mb = 0
        # A failed load is remembered so we don't retry it every query; an unload
        # is not a failure, so the next query is free to load again.
        _load_failed_reason = None

    gc.collect()
    if was_device == "cuda":
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
        except Exception as e:
            logger.debug("Reranker unload: empty_cache failed (%s)", e)

    logger.info("Reranker: unloaded from %s (~%sMB freed)", was_device, freed)
    return {"unloaded": True, "freed_mb": freed, "reason": None}


def rerank(query: str, results: List[Dict[str, Any]],
           top_n: Optional[int] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Reorder `results` by cross-encoder relevance.

    Returns (results, info). `results` is returned untouched whenever reranking
    did not run, and `info` always says why.
    """
    global _inflight

    info: Dict[str, Any] = {"applied": False, "reason": None, "model": None, "device": None}

    if not is_enabled():
        info["reason"] = "disabled by GUAARDVARK_RERANK_CROSS_ENCODER"
        return results, info
    if len(results) < 2:
        info["reason"] = "fewer than 2 candidates"
        return results, info

    model = _get_model()
    if model is None:
        info["reason"] = _load_failed_reason or "model unavailable"
        return results, info

    # Pin across predict() so an eviction racing this call is refused rather than
    # reporting VRAM it did not free.
    with _lock:
        _inflight += 1
    try:
        pairs = [(query, (r.get("text") or "")[:4000]) for r in results]
        scores = model.predict(pairs, show_progress_bar=False)
        for r, s in zip(results, scores):
            r["rerank_score"] = float(s)
        ordered = sorted(results, key=lambda r: r.get("rerank_score", 0.0), reverse=True)
        if top_n:
            ordered = ordered[:top_n]
        info.update({
            "applied": True,
            "model": model_name(),
            "device": _model_device,
            "scored": len(pairs),
        })
        return ordered, info
    except Exception as e:
        info["reason"] = f"rerank failed: {e.__class__.__name__}: {str(e)[:120]}"
        logger.warning("Reranker: %s", info["reason"])
        return results, info
    finally:
        with _lock:
            _inflight = max(0, _inflight - 1)
