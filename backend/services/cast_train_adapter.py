"""The training adapter a Cast train base declares: where it is, and Install.

A distilled base (Z-Image Turbo) needs a de-distillation adapter fused in while
a LoRA trains on it. The adapter is declared on the base's entry in
media_model_registry (``train_adapter``). Training uses it whenever it is on
this machine and never fetches it; the download happens only from Install on
the Cast page.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_installs: Dict[str, Dict[str, Any]] = {}


def declared(base_model_id: str | None) -> Optional[dict[str, Any]]:
    """The base's ``train_adapter`` entry, or None when it needs no adapter."""
    from backend.services.media_model_registry import get_profile
    return (get_profile(base_model_id) or {}).get("train_adapter") or None


def adapter_path(base_model_id: str | None) -> Optional[str]:
    """The adapter file on this machine, or None. Never touches the network.

    A path set in the entry's environment variable wins over the installed copy.
    """
    spec = declared(base_model_id)
    if not spec:
        return None
    explicit = os.environ.get(spec.get("env") or "", "").strip()
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    from backend.services.local_weights import cached_path
    return cached_path(spec["hf_repo"], spec["filename"])


def status(base_model_id: str | None) -> dict[str, Any]:
    """For the Cast page: whether the base needs an adapter, has it, or is fetching it."""
    spec = declared(base_model_id)
    if not spec:
        return {"needed": False, "installed": False, "base_model_id": base_model_id}
    with _lock:
        job = dict(_installs.get(spec["hf_repo"]) or {"state": "idle"})
    return {
        "needed": True,
        "installed": adapter_path(base_model_id) is not None,
        "base_model_id": base_model_id,
        "name": spec.get("name"),
        "source": spec["hf_repo"],
        "size_mb": spec.get("size_mb"),
        "license": spec.get("license"),
        "install": job,
    }


def start_install(base_model_id: str | None) -> dict[str, Any]:
    """Download the adapter in the background. The only path that fetches it."""
    spec = declared(base_model_id)
    if not spec or adapter_path(base_model_id):
        return status(base_model_id)
    repo = spec["hf_repo"]
    with _lock:
        running = (_installs.get(repo) or {}).get("state") == "running"
        if not running:
            _installs[repo] = {"state": "running", "progress": 0, "error": None,
                               "started_at": time.time()}
    if not running:
        threading.Thread(target=_run_install, args=(dict(spec),), daemon=True,
                         name="cast-train-adapter-install").start()
    return status(base_model_id)


def _run_install(spec: dict[str, Any]) -> None:
    from huggingface_hub import hf_hub_download
    from backend.services.local_weights import cached_path, hf_repo_cache_dir

    repo, filename = spec["hf_repo"], spec["filename"]
    cache = hf_repo_cache_dir(repo)
    total = int(spec.get("size_mb") or 0) * 1000 ** 2
    stop = threading.Event()

    def _bytes() -> int:
        n = 0
        for root, _dirs, files in os.walk(cache):
            for f in files:
                path = os.path.join(root, f)
                try:
                    if not os.path.islink(path):
                        n += os.path.getsize(path)
                except OSError:
                    pass
        return n

    baseline = _bytes()

    def _watch() -> None:
        while not stop.wait(1.0):
            with _lock:
                job = _installs.get(repo)
                if not job or job.get("state") != "running":
                    return
                if total:
                    job["progress"] = min(99, int(100 * max(0, _bytes() - baseline) / total))

    threading.Thread(target=_watch, daemon=True, name="cast-train-adapter-progress").start()
    try:
        logger.info("Cast training adapter: installing %s/%s", repo, filename)
        hf_hub_download(repo_id=repo, filename=filename, token=False)
        if not cached_path(repo, filename):
            raise RuntimeError("the download finished without the adapter file")
        with _lock:
            _installs[repo] = {"state": "done", "progress": 100, "error": None,
                               "finished_at": time.time()}
        logger.info("Cast training adapter: %s installed", filename)
    except Exception as e:  # noqa: BLE001 — shown on the Cast page
        logger.error("Cast training adapter install failed: %s", e)
        with _lock:
            _installs[repo] = {"state": "failed", "progress": None,
                               "error": f"{e.__class__.__name__}: {str(e)[:200]}",
                               "finished_at": time.time()}
    finally:
        stop.set()
