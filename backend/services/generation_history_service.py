"""Bulk deletion of generation history: batch images, batch videos, audio.

Settings → Maintenance → "Delete History". Removes the on-disk batch
directories and audio outputs together with the database rows that mirror
them, which the per-batch delete routes never did (they `rmtree` and leave
`documents`, `folders` and `job_history` rows orphaned).

Scope is deliberately narrow and structural, not by folder prefix:

- image/video history = subdirectories of ``UPLOAD_DIR/Images`` and
  ``UPLOAD_DIR/Videos`` that contain ``batch_metadata.json``. The same
  folders also hold ``Editor Renders``, ``Text Overlay`` and standalone
  ComfyUI output, none of which is batch history;
- audio history = files in ``UPLOAD_DIR/Audio`` named ``<uuid4 hex>.wav``,
  ``.mp3`` or ``_input_params.json`` (the audio_foundry backends name every
  generation ``uuid.uuid4().hex``) plus the sidecar's ``.jobs/*.json``
  records. Hand-named files in the same folder are user assets and stay;
- ComfyUI scratch = everything in ``COMFYUI_DIR/output`` and
  ``COMFYUI_DIR/input``. Every render is fetched over ``/view`` into a batch
  directory and every reference frame is pushed over ``/upload/image``, so
  what those two folders hold is a second copy of history the batch
  directories already own (no ``documents`` row points into them). The one
  exception is ``input/example.png``, which ComfyUI's own checkout tracks.
  Both folders are left alone while ComfyUI has a prompt running or queued:
  it reads ``input/`` and writes ``output/`` on the fly.

Never touched: Film Crew productions, video-editor projects, the cast
library and ``data/training/loras`` (the live LoRA registry ComfyUI loads),
chat history, scheduler tasks, music videos, upscaling, voice reference clips.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

BATCH_METADATA = "batch_metadata.json"
AUDIO_GENERATED_RE = re.compile(r"^[0-9a-f]{32}(\.wav|\.mp3|_input_params\.json)$")
# A batch in one of these states is still owned by a generator and is skipped.
ACTIVE_STATUSES = frozenset({"queued", "pending", "running", "processing"})
SIDECAR_TIMEOUT_S = 10
_DEFAULT_SIDECAR_URL = "http://127.0.0.1:8206"
COMFYUI_TIMEOUT_S = 5
COMFYUI_SCRATCH_DIRS = ("output", "input")
# Files ComfyUI's own checkout ships in these folders (its .gitignore exempts
# them); removing them dirties the plugin tree and breaks the stock workflow.
COMFYUI_KEEP = {"input": frozenset({"example.png"})}


# ─── filesystem ──────────────────────────────────────────────────────────────

def _upload_dir(upload_dir: Optional[str | Path] = None) -> Path:
    # Resolved lazily: backend.config rewrites UPLOAD_DIR in test mode.
    if upload_dir is not None:
        return Path(upload_dir)
    from backend.config import UPLOAD_DIR
    return Path(UPLOAD_DIR)


def _tree_stats(path: Path) -> tuple[int, int]:
    """(file count, bytes) under ``path``; tolerant of files vanishing mid-walk."""
    files = 0
    size = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    files += 1
                    size += p.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return files, size


def _discover_batches(root: Path) -> list[dict[str, Any]]:
    """Batch directories under ``root``: only those carrying batch_metadata.json."""
    batches: list[dict[str, Any]] = []
    if not root.is_dir():
        return batches
    for d in sorted(root.iterdir()):
        meta = d / BATCH_METADATA
        if not d.is_dir() or not meta.is_file():
            continue
        status = None
        try:
            status = json.loads(meta.read_text(encoding="utf-8")).get("status")
        except Exception:
            status = None  # unreadable metadata is still a batch; it just cannot be running
        files, size = _tree_stats(d)
        batches.append({"batch_id": d.name, "dir": d, "status": status, "files": files, "bytes": size})
    return batches


def _audio_root(upload: Path) -> Path:
    return upload / "Audio"


def _audio_generated_files(audio_root: Path) -> list[Path]:
    if not audio_root.is_dir():
        return []
    return sorted(p for p in audio_root.iterdir() if p.is_file() and AUDIO_GENERATED_RE.match(p.name))


def _audio_job_files(audio_root: Path) -> list[Path]:
    jobs_dir = audio_root / ".jobs"
    if not jobs_dir.is_dir():
        return []
    return sorted(jobs_dir.glob("*.json"))


# ─── ComfyUI scratch ─────────────────────────────────────────────────────────

def _comfyui_dir(comfyui_dir: Optional[str | Path] = None) -> Path:
    if comfyui_dir is not None:
        return Path(comfyui_dir)
    from backend.config import COMFYUI_DIR
    return Path(COMFYUI_DIR)


def _comfyui_url() -> str:
    from backend.config import COMFYUI_URL
    return COMFYUI_URL


def _comfyui_scratch(comfy: Path, sub: str) -> list[dict[str, Any]]:
    """Top-level entries of ``COMFYUI_DIR/<sub>`` a purge removes, with sizes.

    Subfolders (``3d``, ``clipspace``, SaveImage prefixes with a slash) go
    whole; ComfyUI recreates them on demand.
    """
    root = comfy / sub
    keep = COMFYUI_KEEP.get(sub, frozenset())
    entries: list[dict[str, Any]] = []
    if not root.is_dir():
        return entries
    for p in sorted(root.iterdir()):
        if p.name in keep:
            continue
        try:
            if p.is_symlink():
                files, size = 1, 0  # the link goes, its target stays
            elif p.is_dir():
                files, size = _tree_stats(p)
            elif p.is_file():
                files, size = 1, p.stat().st_size
            else:
                continue
        except OSError:
            continue
        entries.append({"path": p, "files": files, "bytes": size})
    return entries


def _remove_scratch_entry(p: Path) -> None:
    if p.is_symlink() or not p.is_dir():
        p.unlink()
    else:
        shutil.rmtree(p)


def _comfyui_in_flight() -> list[str]:
    """Prompt ids ComfyUI is running or has queued.

    An unreachable ComfyUI has nothing in flight and returns ``[]``. Any other
    failure raises: the caller cannot tell whether the folders are in use.
    """
    import requests
    try:
        resp = requests.get(f"{_comfyui_url()}/queue", timeout=COMFYUI_TIMEOUT_S)
    except requests.ConnectionError:
        return []
    resp.raise_for_status()
    queue = resp.json()
    ids: list[str] = []
    # Each queue entry is [number, prompt_id, prompt, extra_data, outputs].
    for item in list(queue.get("queue_running") or []) + list(queue.get("queue_pending") or []):
        if isinstance(item, (list, tuple)) and len(item) > 1:
            ids.append(str(item[1]))
        elif isinstance(item, dict) and item.get("prompt_id"):
            ids.append(str(item["prompt_id"]))
    return ids


# ─── live generator state ────────────────────────────────────────────────────

def _generator_instance(kind: str):
    """The already-constructed generator, or None. Never constructs one:
    ``get_batch_image_generator()`` builds the whole image pipeline."""
    try:
        if kind == "images":
            from backend.services import batch_image_generator as mod
            return getattr(mod, "_batch_generator_instance", None)
        from backend.services import batch_video_generator as mod
        return getattr(mod, "_batch_video_generator_instance", None)
    except Exception as e:  # pragma: no cover - import failure means nothing is live
        logger.debug("generator instance for %s unavailable: %s", kind, e)
        return None


def _active_batch_ids(instance) -> set[str]:
    if instance is None:
        return set()
    lock = getattr(instance, "batch_lock", None) or threading.Lock()
    with lock:
        return {
            bid for bid, status in dict(getattr(instance, "active_batches", {})).items()
            if getattr(status, "status", None) in ACTIVE_STATUSES
        }


def _forget_batches(instance, batch_ids: Iterable[str]) -> None:
    if instance is None:
        return
    ids = set(batch_ids)
    lock = getattr(instance, "batch_lock", None) or threading.Lock()
    with lock:
        for bid in ids:
            getattr(instance, "active_batches", {}).pop(bid, None)
            getattr(instance, "cancel_events", {}).pop(bid, None)
            order = getattr(instance, "queue_order", None)
            if isinstance(order, list) and bid in order:
                order.remove(bid)


# ─── audio sidecar ───────────────────────────────────────────────────────────

def _sidecar_url() -> str:
    try:
        from backend.api.audio_foundry_api import AUDIO_FOUNDRY_URL
        return AUDIO_FOUNDRY_URL
    except Exception:
        return _DEFAULT_SIDECAR_URL


def _sidecar_active_ids() -> Optional[set[str]]:
    """Ids of queued/running audio jobs, or None when the sidecar is down."""
    import requests
    try:
        resp = requests.get(f"{_sidecar_url()}/jobs", timeout=SIDECAR_TIMEOUT_S)
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
    except requests.ConnectionError:
        return None
    except Exception as e:
        logger.warning("audio sidecar job list failed: %s", e)
        return None
    return {j["id"] for j in jobs if j.get("status") in ("queued", "running")}


def _sidecar_clear() -> Optional[dict[str, Any]]:
    """Ask the sidecar to drop its finished jobs (memory + disk).

    Returns its response, or None when the sidecar is not running — in which
    case every job file on disk is stale by definition and the caller may
    remove them directly. Any other failure raises so it is reported.
    """
    import requests
    try:
        resp = requests.delete(f"{_sidecar_url()}/jobs", timeout=SIDECAR_TIMEOUT_S)
    except requests.ConnectionError:
        return None
    resp.raise_for_status()
    return resp.json()


# ─── database mirrors ────────────────────────────────────────────────────────

def _batch_folder_paths(image_ids: Iterable[str], video_ids: Iterable[str]) -> list[str]:
    return [f"Images/{b}" for b in image_ids] + [f"Videos/{b}" for b in video_ids]


def _db_targets(image_ids: Iterable[str], video_ids: Iterable[str]) -> tuple[list[int], list[int]]:
    """(folder ids, document ids) that mirror the given batches and every
    generated audio file. Root folders are never included."""
    from backend.models import db, Document, Folder

    folder_ids: list[int] = []
    doc_ids: set[int] = set()
    paths = _batch_folder_paths(image_ids, video_ids)
    if paths:
        folder_ids = [fid for (fid,) in db.session.query(Folder.id).filter(Folder.path.in_(paths))]
    if folder_ids:
        doc_ids.update(did for (did,) in db.session.query(Document.id).filter(Document.folder_id.in_(folder_ids)))
    audio_folder_id = db.session.query(Folder.id).filter(Folder.path == "Audio").scalar()
    if audio_folder_id is not None:
        for did, path in db.session.query(Document.id, Document.path).filter(Document.folder_id == audio_folder_id):
            if AUDIO_GENERATED_RE.match(Path(path or "").name):
                doc_ids.add(did)
    return folder_ids, sorted(doc_ids)


def _job_history_ids(image_ids: Iterable[str], keep: Iterable[str] = ()) -> list[str]:
    """job_history rows for video batches (their own kind) and image batches.

    Image batches share the ``unified`` kind with indexing and other
    processes, so they are picked by ``native_id`` or by the
    ``process_type`` the image generator stamps — never by kind alone.
    ``keep`` names batches that are still running: their rows stay with them.
    """
    from backend.models import db, JobHistory

    keep_set = set(keep)
    ids = [
        jid for (jid, native_id) in db.session.query(JobHistory.id, JobHistory.native_id)
        .filter(JobHistory.kind == "video_gen")
        if native_id not in keep_set
    ]
    image_set = set(image_ids)
    for jid, native_id, meta in db.session.query(
        JobHistory.id, JobHistory.native_id, JobHistory.job_metadata
    ).filter(JobHistory.kind == "unified"):
        if native_id in keep_set:
            continue
        process_type = (meta or {}).get("process_type") if isinstance(meta, dict) else None
        if native_id in image_set or process_type == "image_generation":
            ids.append(jid)
    return ids


def _delete_db_rows(folder_ids: list[int], doc_ids: list[int], job_history_ids: list[str]) -> dict[str, int]:
    from backend.models import db, Document, EvalPair, Folder, JobHistory

    deleted = {"documents": 0, "folders": 0, "job_history": 0}
    try:
        if doc_ids:
            # eval_pairs.source_doc_id has no ondelete; null it or the delete raises.
            db.session.query(EvalPair).filter(EvalPair.source_doc_id.in_(doc_ids)).update(
                {"source_doc_id": None}, synchronize_session=False
            )
            try:
                from backend.services.indexing_service import purge_document_vectors
                for did in doc_ids:
                    purge_document_vectors(did)
            except Exception as e:  # vectors are best-effort; rows still go
                logger.warning("vector purge skipped: %s", e)
            deleted["documents"] = db.session.query(Document).filter(Document.id.in_(doc_ids)).delete(
                synchronize_session=False
            )
        if folder_ids:
            deleted["folders"] = db.session.query(Folder).filter(Folder.id.in_(folder_ids)).delete(
                synchronize_session=False
            )
        if job_history_ids:
            deleted["job_history"] = db.session.query(JobHistory).filter(JobHistory.id.in_(job_history_ids)).delete(
                synchronize_session=False
            )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return deleted


# ─── public API ──────────────────────────────────────────────────────────────

def count_generation_history(
    upload_dir: Optional[str | Path] = None, comfyui_dir: Optional[str | Path] = None
) -> dict[str, Any]:
    """What "Delete History" would remove, for the confirmation dialog."""
    upload = _upload_dir(upload_dir)
    images = _discover_batches(upload / "Images")
    videos = _discover_batches(upload / "Videos")
    audio_root = _audio_root(upload)
    audio_files = _audio_generated_files(audio_root)
    audio_jobs = _audio_job_files(audio_root)
    comfy = _comfyui_dir(comfyui_dir)
    scratch = {sub: _comfyui_scratch(comfy, sub) for sub in COMFYUI_SCRATCH_DIRS}

    running_images = sorted(_active_batch_ids(_generator_instance("images")))
    running_videos = sorted(_active_batch_ids(_generator_instance("videos")))
    running_audio = sorted(_sidecar_active_ids() or set())
    try:
        running_comfy = _comfyui_in_flight()
    except Exception as e:
        logger.warning("ComfyUI queue check failed: %s", e)
        running_comfy = []

    audio_bytes = 0
    for p in audio_files:
        try:
            audio_bytes += p.stat().st_size
        except OSError:
            pass

    db_counts = {"documents": 0, "folders": 0, "job_history": 0}
    try:
        folder_ids, doc_ids = _db_targets([b["batch_id"] for b in images], [b["batch_id"] for b in videos])
        db_counts = {
            "documents": len(doc_ids),
            "folders": len(folder_ids),
            "job_history": len(_job_history_ids(
                [b["batch_id"] for b in images], keep=running_images + running_videos
            )),
        }
    except Exception as e:
        logger.warning("generation history DB count failed: %s", e)

    comfy_counts = {
        sub: {"files": sum(e["files"] for e in scratch[sub]), "bytes": sum(e["bytes"] for e in scratch[sub])}
        for sub in COMFYUI_SCRATCH_DIRS
    }
    comfy_bytes = sum(c["bytes"] for c in comfy_counts.values())
    total_bytes = sum(b["bytes"] for b in images) + sum(b["bytes"] for b in videos) + audio_bytes + comfy_bytes
    return {
        "images": {
            "batches": len(images),
            "files": sum(b["files"] for b in images),
            "bytes": sum(b["bytes"] for b in images),
            "running": running_images,
        },
        "videos": {
            "batches": len(videos),
            "files": sum(b["files"] for b in videos),
            "bytes": sum(b["bytes"] for b in videos),
            "running": running_videos,
        },
        "audio": {
            "jobs": len(audio_jobs),
            "files": len(audio_files),
            "bytes": audio_bytes,
            "running": running_audio,
        },
        "comfyui": {
            **comfy_counts,
            "files": sum(c["files"] for c in comfy_counts.values()),
            "bytes": comfy_bytes,
            "running": running_comfy,
        },
        "db": db_counts,
        "total_bytes": total_bytes,
    }


def delete_generation_history(
    *,
    upload_dir: Optional[str | Path] = None,
    comfyui_dir: Optional[str | Path] = None,
    triggered_by: str = "settings_ui",
) -> dict[str, Any]:
    """Delete all batch-image, batch-video and audio generation history,
    plus ComfyUI's output/ and input/ scratch.

    Each stage is isolated: a failure is recorded in ``errors`` and the
    remaining stages still run, so a bad sidecar cannot leave batch
    directories behind and a bad directory cannot leave DB rows behind.
    Batches a generator is still working on are skipped and reported.
    """
    upload = _upload_dir(upload_dir)
    errors: list[str] = []
    skipped: dict[str, list[str]] = {"images": [], "videos": [], "audio": [], "comfyui": []}
    deleted: dict[str, Any] = {
        "images": {"batches": 0, "files": 0, "bytes": 0},
        "videos": {"batches": 0, "files": 0, "bytes": 0},
        "audio": {"jobs": 0, "files": 0, "bytes": 0},
        "comfyui": {sub: {"files": 0, "bytes": 0} for sub in COMFYUI_SCRATCH_DIRS},
        "documents": 0,
        "folders": 0,
        "job_history": 0,
    }
    removed_ids: dict[str, list[str]] = {"images": [], "videos": []}

    # 1. Batch directories. A batch whose metadata still says "running" but
    #    that no live generator owns was interrupted; it is history now.
    generators = {"images": _generator_instance("images"), "videos": _generator_instance("videos")}
    for kind in ("images", "videos"):
        root = upload / ("Images" if kind == "images" else "Videos")
        live = _active_batch_ids(generators[kind])
        for batch in _discover_batches(root):
            bid = batch["batch_id"]
            if bid in live:
                skipped[kind].append(bid)
                continue
            try:
                shutil.rmtree(batch["dir"])
            except Exception as e:
                errors.append(f"{kind}/{bid}: {e}")
                continue
            removed_ids[kind].append(bid)
            deleted[kind]["batches"] += 1
            deleted[kind]["files"] += batch["files"]
            deleted[kind]["bytes"] += batch["bytes"]
        _forget_batches(generators[kind], removed_ids[kind])

    # 1b. ComfyUI's own output/ and input/. A prompt still executing may read
    #     input/ or write output/ next, so a non-empty queue defers both.
    comfy = _comfyui_dir(comfyui_dir)
    purge_comfy = True
    try:
        skipped["comfyui"] = _comfyui_in_flight()
    except Exception as e:
        errors.append(f"comfyui queue: {e}")
        purge_comfy = False
    if skipped["comfyui"]:
        purge_comfy = False
    if purge_comfy:
        for sub in COMFYUI_SCRATCH_DIRS:
            for entry in _comfyui_scratch(comfy, sub):
                try:
                    _remove_scratch_entry(entry["path"])
                except Exception as e:
                    errors.append(f"comfyui/{sub}/{entry['path'].name}: {e}")
                    continue
                deleted["comfyui"][sub]["files"] += entry["files"]
                deleted["comfyui"][sub]["bytes"] += entry["bytes"]

    # 2. Audio: let the sidecar drop its finished jobs so its memory and the
    #    .jobs files agree; if it is down, every job file is stale.
    audio_root = _audio_root(upload)
    sidecar_available = True
    active_audio: set[str] = set()
    clear_jobs = True
    try:
        result = _sidecar_clear()
        if result is None:
            sidecar_available = False
        else:
            active_audio = set(result.get("active_ids") or [])
            deleted["audio"]["jobs"] += int(result.get("removed") or 0)
    except Exception as e:
        errors.append(f"audio sidecar: {e}")
        clear_jobs = False  # unknown which jobs are live; leave their records alone
    skipped["audio"] = sorted(active_audio)

    if clear_jobs:
        for p in _audio_job_files(audio_root):
            if p.stem in active_audio:
                continue
            try:
                p.unlink()
                if not sidecar_available:
                    deleted["audio"]["jobs"] += 1
            except FileNotFoundError:
                pass  # the sidecar removed it first
            except Exception as e:
                errors.append(f"audio job {p.name}: {e}")
    # Outputs are written only when a job finishes, so nothing here belongs
    # to a job that is still running.
    for p in _audio_generated_files(audio_root):
        try:
            size = p.stat().st_size
            p.unlink()
            deleted["audio"]["files"] += 1
            deleted["audio"]["bytes"] += size
        except Exception as e:
            errors.append(f"audio {p.name}: {e}")

    # 3. Database mirrors of what is now gone from disk.
    try:
        folder_ids, doc_ids = _db_targets(removed_ids["images"], removed_ids["videos"])
        jh_ids = _job_history_ids(removed_ids["images"], keep=skipped["images"] + skipped["videos"])
        deleted.update(_delete_db_rows(folder_ids, doc_ids, jh_ids))
    except Exception as e:
        errors.append(f"database: {e}")

    # 4. Retention audit — append-only, one row per category that lost something.
    try:
        from backend.services.retention_audit_service import record_deletion
        for kind in ("images", "videos"):
            if deleted[kind]["batches"]:
                record_deletion(
                    actor="user",
                    kind=f"generation_history_{kind}",
                    operation="bulk_delete",
                    item_count=deleted[kind]["batches"],
                    bytes_freed=deleted[kind]["bytes"],
                    parameters={"batch_ids": removed_ids[kind], "skipped": skipped[kind]},
                    triggered_by=triggered_by,
                )
        if deleted["audio"]["files"] or deleted["audio"]["jobs"]:
            record_deletion(
                actor="user",
                kind="generation_history_audio",
                operation="bulk_delete",
                item_count=deleted["audio"]["files"] + deleted["audio"]["jobs"],
                bytes_freed=deleted["audio"]["bytes"],
                parameters={"skipped": skipped["audio"], "sidecar_available": sidecar_available},
                triggered_by=triggered_by,
            )
        comfy_files = sum(c["files"] for c in deleted["comfyui"].values())
        if comfy_files:
            record_deletion(
                actor="user",
                kind="generation_history_comfyui",
                operation="bulk_delete",
                item_count=comfy_files,
                bytes_freed=sum(c["bytes"] for c in deleted["comfyui"].values()),
                parameters={"dir": str(comfy), **deleted["comfyui"]},
                triggered_by=triggered_by,
            )
        if deleted["documents"] or deleted["folders"] or deleted["job_history"]:
            record_deletion(
                actor="user",
                kind="generation_history_db",
                operation="bulk_delete",
                item_count=deleted["documents"] + deleted["folders"] + deleted["job_history"],
                parameters={k: deleted[k] for k in ("documents", "folders", "job_history")},
                triggered_by=triggered_by,
            )
    except Exception as e:
        errors.append(f"retention audit: {e}")

    bytes_freed = (
        deleted["images"]["bytes"] + deleted["videos"]["bytes"] + deleted["audio"]["bytes"]
        + sum(c["bytes"] for c in deleted["comfyui"].values())
    )
    return {
        "success": not errors,
        "deleted": deleted,
        "skipped": skipped,
        "errors": errors,
        "sidecar_available": sidecar_available,
        "bytes_freed": bytes_freed,
    }
