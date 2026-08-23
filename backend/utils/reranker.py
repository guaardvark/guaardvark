"""Cross-encoder reranking for retrieval results.

The fused retriever scores a query and a passage independently (bi-encoder plus
BM25); a cross-encoder reads the pair together and is markedly better at deciding
relevance. It is applied to the candidate pool AFTER filtering and dedup, and
BEFORE MMR -- rerank decides what is relevant, MMR decides what is diverse.

The model competes for VRAM with image and video generation, so loading is
admitted against free VRAM and falls back to CPU rather than failing a query.
Every outcome is reported back to the caller for the retrieval trace: a rerank
that did not happen must never look like one that did.
"""

import logging
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"

_lock = threading.Lock()
_model = None
_model_device: Optional[str] = None
_load_failed_reason: Optional[str] = None


def is_enabled() -> bool:
    return os.environ.get("GUAARDVARK_RERANK_CROSS_ENCODER", "true").lower() == "true"


def model_name() -> str:
    return os.environ.get("GUAARDVARK_RERANK_MODEL", DEFAULT_MODEL)


def _pick_device() -> str:
    """GPU only when there is comfortable headroom; otherwise CPU.

    The floor is deliberately above the model's own footprint: admitting it into
    the last free gigabyte would just move an OOM onto whichever generation job
    starts next.
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


def _get_model():
    """Load the cross-encoder once. Returns None if unavailable (never raises)."""
    global _model, _model_device, _load_failed_reason
    if _model is not None or _load_failed_reason is not None:
        return _model
    with _lock:
        if _model is not None or _load_failed_reason is not None:
            return _model
        try:
            from sentence_transformers import CrossEncoder
            device = _pick_device()
            name = model_name()
            logger.info("Reranker: loading %s on %s", name, device)
            _model = CrossEncoder(name, device=device, max_length=512)
            _model_device = device
            logger.info("Reranker: ready (%s on %s)", name, device)
        except Exception as e:
            _load_failed_reason = f"{e.__class__.__name__}: {str(e)[:160]}"
            logger.warning("Reranker unavailable — %s", _load_failed_reason)
            _model = None
    return _model


def rerank(query: str, results: List[Dict[str, Any]],
           top_n: Optional[int] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Reorder `results` by cross-encoder relevance.

    Returns (results, info). `results` is returned untouched whenever reranking
    did not run, and `info` always says why.
    """
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
