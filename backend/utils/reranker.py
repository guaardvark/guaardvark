"""Cross-encoder reranking for retrieval results.

The fused retriever scores a query and a passage independently (bi-encoder plus
BM25); a cross-encoder reads the pair together and is markedly better at deciding
relevance. It is applied to the candidate pool AFTER filtering and dedup, and its
order is final: MMR runs only when the cross-encoder did not score the pool.

The model competes for VRAM with image and video generation, so loading is
admitted against free VRAM and falls back to CPU rather than failing a query.
Every outcome is reported back to the caller for the retrieval trace: a rerank
that did not happen must never look like one that did.

Residency is reclaimable. `unload()` releases the weights and the orchestrator
calls it -- both from its periodic registry (via `status()`) and directly when a
render needs the card. Without that, a query that happened to run while the card
was quiet would take ~1.1 GB of VRAM for the life of the process and starve every
image batch behind it; that is exactly what F-RAG-10 was.

A Celery worker cannot see the GPU, and on the CPU this model is slow (about 27 s
for the 20 candidates of a default retrieval). A worker therefore sends its pairs
to the backend (POST /api/meta/reranker/score), which scores them with the model
chat uses, on the GPU, through the same code: the scores are chat's. The backend
scores only on the GPU; when it cannot, or does not answer, the worker loads the
model on its own CPU as before (see ``_scores_from_backend``).
"""

import contextlib
import gc
import logging
import math
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.utils.backend_http import in_mcp_process

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


# The weights are fetched only by Install (Settings > Knowledge), never by a
# search: a first query on a fresh install used to download 2.2 GB unannounced.
# Install takes the files CrossEncoder reads, not the repo's other formats.
_INSTALL_PATTERNS = ["*.json", "*.safetensors", "*.model", "*.txt"]
INSTALL_SIZE_GB = {"BAAI/bge-reranker-v2-m3": 2.3}

_install_lock = threading.Lock()
_install: Dict[str, Any] = {"state": "idle", "model": None, "progress": None,
                            "downloaded_gb": 0.0, "error": None}


def _local_snapshot(name: str) -> Optional[str]:
    """The model's folder in the Hugging Face cache, or None. Never touches the network."""
    try:
        from huggingface_hub import snapshot_download
        path = snapshot_download(repo_id=name, local_files_only=True,
                                 allow_patterns=_INSTALL_PATTERNS, token=False)
    except Exception:
        return None
    folder = os.fspath(path)
    has_config = os.path.isfile(os.path.join(folder, "config.json"))
    has_weights = any(f.endswith(".safetensors") for f in os.listdir(folder))
    return folder if has_config and has_weights else None


def is_installed() -> bool:
    return _local_snapshot(model_name()) is not None


def not_installed_reason() -> str:
    name = model_name()
    size = INSTALL_SIZE_GB.get(name)
    about = f", about {size} GB" if size else ""
    return (f"the reranker model ({name}{about}) is not installed; install it in "
            f"Settings > Knowledge. Searches run without reranking until then.")


# Below this rerank score a passage is unrelated to the question, per model: the
# scale belongs to the model, so a model not listed here gets no floor.
#
# bge-reranker-v2-m3, measured 2026-09-27 through search_with_llamaindex on two
# indexes (1,145 chunks of product docs and extrusion manuals; two short notes):
# for 12 questions the documents answer, the best passage scored 0.76-0.998 and
# every passage from an answering document scored 0.62 or more; 12 general
# questions they do not answer (capital of Australia, coffee beans, the 1928
# World Series, ...) peaked at 0.0996 on either index. 0.30 is the midpoint of
# that gap on the logit scale.
RELEVANCE_FLOOR = {
    "BAAI/bge-reranker-v2-m3": 0.30,
}


def relevance_floor() -> Optional[float]:
    """The score a passage needs to count as related, or None when unknown.

    GUAARDVARK_RAG_MIN_RERANK_SCORE overrides the measured value; 0 turns the
    floor off.
    """
    raw = os.environ.get("GUAARDVARK_RAG_MIN_RERANK_SCORE", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            logger.warning("GUAARDVARK_RAG_MIN_RERANK_SCORE=%r is not a number; ignoring", raw)
    return RELEVANCE_FLOOR.get(model_name())


def drop_unrelated(results: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """Keep the retrieved passages rated related to the question.

    When this reranker scored the retrieval, its floor decides, if its model
    has one. Otherwise the search's ``vector_related`` mark decides
    (indexing_service._judge_by_vector_score), where the embedding model has a
    floor. A hit that was not rated, such as a dependency-expansion chunk, stays
    only beside a surviving rated hit from the same source. Returns (kept,
    number dropped).
    """
    if any(r.get("rerank_score") is not None for r in results):
        floor = relevance_floor()
        if not floor:
            return results, 0

        def _rated(r):
            return r.get("rerank_score") is not None

        def _related(r):
            return r["rerank_score"] >= floor
    else:
        def _rated(r):
            return r.get("vector_related") is not None

        def _related(r):
            return r["vector_related"]

    rated = [r for r in results if _rated(r)]
    if not rated:
        return results, 0

    def _source(r):
        return (r.get("metadata") or {}).get("source_filename")

    related_sources = {_source(r) for r in rated if _related(r)}
    kept = [r for r in results if (_related(r) if _rated(r) else _source(r) in related_sources)]
    return kept, len(results) - len(kept)


def _cuda_unusable_reason() -> Optional[str]:
    """Why this process cannot load the model on CUDA, or None if it can.

    NVML (has_gpu) sees the card whatever this process may use. Celery workers
    run with CUDA_VISIBLE_DEVICES empty, which counts even if torch saw the
    card before it was blanked.
    """
    visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    if visible is not None and not visible.strip():
        return "CUDA_VISIBLE_DEVICES is empty"
    try:
        import torch
        if torch.cuda.is_available():
            return None
        return "torch sees no CUDA device"
    except Exception as e:  # noqa: BLE001 - a broken CUDA build counts as no CUDA
        return f"CUDA check failed ({e.__class__.__name__})"


def _why_not_cuda() -> Optional[str]:
    """Why the model should not load on the GPU here now, or None when it may.

    GPU only when this process can use one with comfortable headroom. The floor
    is deliberately above the model's own footprint: admitting it into the last
    free gigabyte would just move an OOM onto whichever generation job starts
    next. This is decided per load, not once per process -- an unload releases
    the card, and the next query re-decides against the card as it is then.
    """
    # Memory held by the MCP process is invisible to the backend's GPU admission.
    if in_mcp_process():
        return "the MCP process keeps no GPU memory"
    unusable = _cuda_unusable_reason()
    if unusable:
        return f"no usable GPU in this process ({unusable})"
    try:
        floor_mb = int(os.environ.get("GUAARDVARK_RERANK_MIN_VRAM_MB", "3000"))
        from backend.services.gpu_resource_coordinator import has_gpu, get_available_vram
        if not has_gpu():
            return "no GPU"
        info = get_available_vram()
        if info.get("success") and info.get("available_mb", 0) >= floor_mb:
            return None
        return f"{info.get('available_mb')} MB free < {floor_mb} MB floor"
    except Exception as e:
        return f"device probe failed ({e})"


def _pick_device() -> str:
    """"cuda" when ``_why_not_cuda`` allows it, otherwise "cpu", saying why.

    A Celery worker, where the GPU is hidden, gets here only when the backend
    could not score for it (``_scores_from_backend``), and then reranks on the
    CPU with the same model chat uses.
    """
    why = _why_not_cuda()
    if why is None:
        return "cuda"
    logger.info("Reranker: %s — loading on CPU", why)
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
    """Load the cross-encoder once. Returns None if unavailable (never raises).

    Loads only from the local cache. A model that is not installed is not a
    failure to remember: the next query checks again, so an Install takes
    effect without a restart.
    """
    if _model is not None or _load_failed_reason is not None:
        return _model
    if not is_installed():
        return None
    with _lock:
        if _model is None and _load_failed_reason is None:
            _load_locked(_pick_device())
    return _model


def _gpu_model() -> Tuple[Any, Optional[str]]:
    """The model if it is on the GPU, loading it there when the card has room.

    Never loads it on the CPU: the caller (the backend scoring for a worker)
    then tells the worker to score on its own CPU, rather than leaving a CPU
    copy here that chat would keep using after the card frees up. Returns
    (model, None), or (None, why not).
    """
    if _model is None and _load_failed_reason is None and not is_installed():
        return None, not_installed_reason()
    with _lock:
        if _model is None and _load_failed_reason is None:
            why = _why_not_cuda()
            if why is not None:
                return None, why
            _load_locked("cuda")
        if _model is None:
            return None, _load_failed_reason or "model unavailable"
        if _model_device != "cuda":
            return None, f"the reranker is loaded on the {_model_device}, not the GPU"
        return _model, None


def _load_locked(device: str) -> None:
    """Load the model onto ``device``. The caller holds ``_lock``.

    A failure is remembered in ``_load_failed_reason`` instead of raised.
    """
    global _model, _model_device, _load_failed_reason, _model_vram_mb
    try:
        from sentence_transformers import CrossEncoder
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
        _model = CrossEncoder(name, local_files_only=True, **kwargs)
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


def status() -> Dict[str, Any]:
    """What the reranker currently holds. Cheap; safe to poll."""
    return {
        "loaded": _model is not None,
        "device": _model_device,
        "vram_mb": _model_vram_mb,
        "in_use": _inflight,
        "model": model_name(),
    }


def install_status() -> Dict[str, Any]:
    """For Settings > Knowledge: on or off, installed or not, and any install in progress."""
    name = model_name()
    with _install_lock:
        job = dict(_install)
    return {
        "enabled": is_enabled(),
        "model": name,
        "installed": is_installed(),
        "size_gb": INSTALL_SIZE_GB.get(name),
        "load_error": _load_failed_reason,
        "install": job,
    }


def start_install() -> Dict[str, Any]:
    """Download the reranker's weights in the background. The only path that fetches them."""
    name = model_name()
    if is_installed():
        return install_status()
    with _install_lock:
        # install_status() takes _install_lock too, so it is called only
        # after the lock is released: a second Install used to hang here.
        already_running = _install["state"] == "running"
        if not already_running:
            _install.update({"state": "running", "model": name,
                             "progress": 0 if INSTALL_SIZE_GB.get(name) else None,
                             "downloaded_gb": 0.0, "error": None, "started_at": time.time()})
    if not already_running:
        threading.Thread(target=_run_install, args=(name,), daemon=True,
                         name="reranker-install").start()
    return install_status()


def _run_install(name: str) -> None:
    global _load_failed_reason
    from huggingface_hub import snapshot_download
    from backend.services.local_weights import hf_repo_cache_dir

    cache = hf_repo_cache_dir(name)
    stop = threading.Event()
    total = INSTALL_SIZE_GB.get(name)

    def _bytes() -> int:
        n = 0
        for root, _dirs, files in os.walk(cache):
            for f in files:
                try:
                    n += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return n

    baseline = _bytes()

    def _watch() -> None:
        while not stop.wait(1.0):
            done = max(0, _bytes() - baseline) / 1024 ** 3
            with _install_lock:
                if _install["state"] != "running":
                    return
                _install["downloaded_gb"] = round(done, 2)
                if total:
                    _install["progress"] = min(99, int(100 * done / total))

    threading.Thread(target=_watch, daemon=True, name="reranker-install-progress").start()
    try:
        logger.info("Reranker: installing %s", name)
        snapshot_download(repo_id=name, allow_patterns=_INSTALL_PATTERNS, token=False)
        if not _local_snapshot(name):
            raise RuntimeError("the download finished without config.json and a .safetensors file")
        with _lock:
            _load_failed_reason = None
        with _install_lock:
            _install.update({"state": "done", "progress": 100, "finished_at": time.time(),
                             "downloaded_gb": round(max(0, _bytes() - baseline) / 1024 ** 3, 2)})
        logger.info("Reranker: %s installed", name)
    except Exception as e:
        logger.error("Reranker install failed: %s", e)
        with _install_lock:
            _install.update({"state": "failed", "error": f"{e.__class__.__name__}: {str(e)[:200]}",
                             "finished_at": time.time()})
    finally:
        stop.set()


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


# Characters of each passage the model reads; the tokenizer cuts the pair at
# 512 tokens anyway, this only bounds the work of getting there.
PASSAGE_CHARS = 4000


@contextlib.contextmanager
def _pinned():
    """Pin the model across predict() so an eviction racing the call is refused
    rather than reporting VRAM it did not free."""
    global _inflight
    with _lock:
        _inflight += 1
    try:
        yield
    finally:
        with _lock:
            _inflight = max(0, _inflight - 1)


def _predict(model, query: str, texts: List[str]) -> List[float]:
    """One score per text, in order. Chat and the backend's score route both
    score through here, so the same inputs give the same scores."""
    pairs = [(query, t[:PASSAGE_CHARS]) for t in texts]
    with _pinned():
        scores = model.predict(pairs, show_progress_bar=False)
    return [float(s) for s in scores]


# --- Scoring for a worker on the backend's GPU ----------------------------------

SCORE_PATH = "/api/meta/reranker/score"
# A retrieval reranks at most 50 candidates (indexing_service caps the pool).
MAX_REMOTE_PASSAGES = 200
MAX_REMOTE_QUERY_CHARS = 100_000
# Measured on a 16 GB desktop card with 4,000-character passages: with the
# model resident the backend scores 20 candidates in 0.10 s and 50 in 0.24 s;
# loading it first takes 1.3 s, or 4.6 s in a process that has not used CUDA
# yet. A worker's CPU (two torch threads) takes 27 s for 20 and 67 s for 50.
# 20 s is four times the slowest backend answer and still below the CPU's time
# for a default pool, so a backend busy past it is left for the CPU.
REMOTE_CONNECT_TIMEOUT_S = 2.0
REMOTE_READ_TIMEOUT_S = 20.0
# After the backend could not score, this process scores on its own CPU for
# this long before asking again, so a stuck or GPU-less backend costs one
# wait per minute, not one per retrieval.
REMOTE_RETRY_S = 60.0

_remote: Dict[str, Any] = {"retry_at": 0.0, "reason": None, "noted": set(), "working": False}
_clock: Callable[[], float] = time.monotonic


def score_on_gpu(query: str, passages: List[str], model: Optional[str] = None) -> Dict[str, Any]:
    """Score ``passages`` against ``query`` here, on the GPU only.

    What POST /api/meta/reranker/score answers for a worker. Returns
    {"applied": True, "scores": [...], "model", "device"} with one score per
    passage in order, or {"applied": False, "reason": ...} when this process
    cannot score on the GPU, or reranks with a model other than ``model``; the
    worker then scores on its own CPU. Never raises.
    """
    info: Dict[str, Any] = {"applied": False, "reason": None, "model": model_name(), "device": None}
    if not is_enabled():
        info["reason"] = "disabled by GUAARDVARK_RERANK_CROSS_ENCODER"
        return info
    if model and model != model_name():
        info["reason"] = f"the backend reranks with {model_name()}, not {model}"
        return info
    if not passages:
        info.update({"applied": True, "scores": [], "scored": 0})
        return info
    m, why = _gpu_model()
    if m is None:
        info["reason"] = why
        return info
    try:
        scores = _predict(m, query, passages)
    except Exception as e:
        info["reason"] = f"rerank failed: {e.__class__.__name__}: {str(e)[:120]}"
        logger.warning("Reranker: %s", info["reason"])
        return info
    info.update({"applied": True, "scores": scores, "device": "cuda", "scored": len(scores)})
    return info


def _worker_without_gpu() -> Optional[str]:
    """Why this process sends its scoring to the backend, or None to score here.

    Only a Celery worker that cannot use the GPU: the backend never asks itself
    (on a box without a usable GPU it hides CUDA too), and a worker that can see
    the card loads the model there as the backend would.
    """
    if os.environ.get("CELERY_WORKER_MODE", "").strip().lower() != "true":
        return None
    return _cuda_unusable_reason()


def _note_remote_once(key: str, level: int, message: str, *args) -> None:
    if key in _remote["noted"]:
        logger.debug(message, *args)
        return
    _remote["noted"].add(key)
    logger.log(level, message, *args)


def _scores_from_backend(query: str, texts: List[str]) -> Tuple[Optional[List[float]], Dict[str, Any]]:
    """Scores from the backend's GPU reranker, or (None, {"reason": why not}).

    Goes only to this checkout's backend on this machine, with its API key.
    """
    now = _clock()
    if now < _remote["retry_at"]:
        return None, {"reason": _remote["reason"]}
    if len(texts) > MAX_REMOTE_PASSAGES or len(query) > MAX_REMOTE_QUERY_CHARS:
        return None, {"reason": "too large to send to the backend"}

    from backend.utils.backend_http import BackendError, request_json

    try:
        reply = request_json(
            "POST", SCORE_PATH,
            payload={"query": query, "passages": texts, "model": model_name()},
            connect_timeout=REMOTE_CONNECT_TIMEOUT_S,
            read_timeout=REMOTE_READ_TIMEOUT_S,
            own_backend=True,
        )
        data = reply.data if isinstance(reply.data, dict) else {}
        scores = data.get("scores") if data.get("applied") else None
        if not (isinstance(scores, list) and len(scores) == len(texts)
                and all(isinstance(s, (int, float)) and math.isfinite(s) for s in scores)):
            # A refusal is the expected answer from a backend without a usable
            # card (a Mac, a CPU-only box) or with a full one.
            key, level = "refused", logging.INFO
            why = data.get("reason") or "it answered without one score per passage"
            reason = f"the backend could not score on its GPU ({why})"
            scores = None
    except BackendError as e:
        key, level = e.kind, logging.WARNING
        reason = f"the backend did not answer ({e.kind}: {str(e)[:160]})"
        scores = None

    if scores is None:
        _remote.update({"retry_at": now + REMOTE_RETRY_S, "reason": reason, "working": False})
        _note_remote_once(key, level, "Reranker: %s; reranking on this worker's CPU, asking again "
                          "in %.0f s", reason, REMOTE_RETRY_S)
        return None, {"reason": reason}

    if not _remote["working"]:
        _remote["working"] = True
        logger.info("Reranker: scoring on the backend's %s (%s)", data.get("device"), data.get("model"))
    _remote.update({"retry_at": 0.0, "reason": None})
    return [float(s) for s in scores], data


def rerank(query: str, results: List[Dict[str, Any]],
           top_n: Optional[int] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Reorder `results` by cross-encoder relevance.

    Returns (results, info). `results` is returned untouched whenever reranking
    did not run, and `info` always says why. In a worker without the GPU the
    backend scores (info["via"] == "backend") when it can.
    """
    info: Dict[str, Any] = {"applied": False, "reason": None, "model": None, "device": None}

    if not is_enabled():
        info["reason"] = "disabled by GUAARDVARK_RERANK_CROSS_ENCODER"
        return results, info
    if len(results) < 2:
        info["reason"] = "fewer than 2 candidates"
        return results, info

    texts = [(r.get("text") or "")[:PASSAGE_CHARS] for r in results]
    scores: Optional[List[float]] = None

    if _worker_without_gpu():
        scores, remote = _scores_from_backend(query, texts)
        if scores is not None:
            info.update({"model": remote.get("model") or model_name(),
                         "device": remote.get("device"), "via": "backend"})
        else:
            info["backend_unavailable"] = remote.get("reason")

    if scores is None:
        model = _get_model()
        if model is None:
            info["reason"] = _load_failed_reason or (
                "model unavailable" if is_installed() else not_installed_reason())
            return results, info
        try:
            scores = _predict(model, query, texts)
        except Exception as e:
            info["reason"] = f"rerank failed: {e.__class__.__name__}: {str(e)[:120]}"
            logger.warning("Reranker: %s", info["reason"])
            return results, info
        info.update({"model": model_name(), "device": _model_device})

    for r, s in zip(results, scores):
        r["rerank_score"] = s
    ordered = sorted(results, key=lambda r: r.get("rerank_score", 0.0), reverse=True)
    if top_n:
        ordered = ordered[:top_n]
    info.update({"applied": True, "scored": len(texts)})
    return ordered, info
