"""Where maps are kept, when one is stale, and who rebuilds it.

A map is stale when the tree it describes has changed, not when a timer runs
out. Each snapshot carries the key it was built for: the checkout's git HEAD,
a hash of the list of files the mapper surveys, the newest modification time
among them, and the mapper's own version. Computing the key lists the tree
and stats each file (milliseconds), so every read can tell whether the map
still describes the files on disk.

Reads never build. A stale or missing map is rebuilt by one background
thread per root (single flight); a reader gets the last map, marked stale,
and can wait on the job when it has to. Snapshots, the query index and the
liveness findings are written aside and renamed, so a reader never sees half
a file.

Files under <storage>/cache/system_map/, named after the root's last three
path segments:
  <slug>.json            snapshot (SystemMap.to_dict() + cache_key)
  <slug>.index.json.gz   query index (index.build) + cache_key
  <slug>.liveness.json   runtime-liveness findings from the 12-hour audit
  <slug>.dismissed.json  dismissed finding ids (actions.py)
  <slug>.dispatched.json finding ids handed to self-improvement (actions.py)
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import facts as facts_mod
from . import index as index_mod
from .core import _GIT_LOCATION_ENV, source_files

logger = logging.getLogger(__name__)

# Bumped when what the mapper produces changes, so maps built by older code
# are rebuilt rather than served.
MAPPER_VERSION = f"3.{facts_mod.FACTS_VERSION}.{index_mod.INDEX_VERSION}"

LIVENESS_KINDS = frozenset({"runtime-zombie", "contextual-discovery", "hot-path-spike"})


# ── Paths ───────────────────────────────────────────────────────────────────

def storage_dir() -> Path:
    env = os.environ.get("GUAARDVARK_STORAGE_DIR")
    if env:
        return Path(env)
    try:
        from backend.config import GUAARDVARK_ROOT
        return Path(GUAARDVARK_ROOT) / "data"
    except Exception:
        return Path(os.environ.get("GUAARDVARK_ROOT", ".")) / "data"


def cache_dir() -> Path:
    d = storage_dir() / "cache" / "system_map"
    d.mkdir(parents=True, exist_ok=True)
    return d


def slug(root: str | Path) -> str:
    parts = [p for p in Path(root).resolve().parts if p not in ("", "/", "\\")]
    return "_".join(parts[-3:]).replace("/", "_") or "default"


def snapshot_path(root: str | Path) -> Path:
    return cache_dir() / f"{slug(root)}.json"


def index_path(root: str | Path) -> Path:
    return cache_dir() / f"{slug(root)}.index.json.gz"


def liveness_path(root: str | Path) -> Path:
    return cache_dir() / f"{slug(root)}.liveness.json"


def write_json_atomic(path: Path, data: Any, compress: bool = False) -> None:
    """Write ``data`` as JSON beside ``path`` and rename it into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        raw = json.dumps(data).encode("utf-8")
        tmp.write_bytes(gzip.compress(raw, compresslevel=5) if compress else raw)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _read_json(path: Path, compressed: bool = False):
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        return json.loads(gzip.decompress(raw) if compressed else raw)
    except (OSError, ValueError) as exc:
        logger.warning("Unreadable system map file %s: %s", path, exc)
        return None


# ── Freshness ───────────────────────────────────────────────────────────────

def _git_head(root: Path) -> Optional[str]:
    env = {k: v for k, v in os.environ.items() if k not in _GIT_LOCATION_ENV}
    try:
        proc = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def current_key(root: str | Path) -> dict:
    """What a map of ``root`` built now would be built from."""
    root = Path(root).resolve()
    files = source_files(root)
    newest = 0.0
    for path in files:
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    listing = hashlib.sha1("\n".join(p.relative_to(root).as_posix() for p in files).encode()).hexdigest()
    return {"head": _git_head(root), "listing": listing, "newest_mtime": round(newest, 6),
            "files": len(files), "mapper": MAPPER_VERSION}


def is_current(payload: Optional[dict], key: dict) -> bool:
    if not payload:
        return False
    have = payload.get("cache_key") or {}
    return all(have.get(k) == key.get(k) for k in ("head", "listing", "newest_mtime", "mapper"))


# ── Liveness (written by the 12-hour audit, merged into every snapshot) ─────

def write_liveness(root: str | Path, findings: list[dict], stats: dict) -> None:
    write_json_atomic(liveness_path(root), {"generated_at": time.time(), "findings": findings, "stats": stats})


def merge_liveness(root: str | Path, payload: dict) -> dict:
    """Replace the snapshot's liveness findings with the audit's latest."""
    data = _read_json(liveness_path(root)) or {}
    kept = [f for f in payload.get("findings", []) if f.get("kind") not in LIVENESS_KINDS]
    payload["findings"] = kept + list(data.get("findings") or [])
    if data:
        payload.setdefault("stats", {})["liveness"] = {**(data.get("stats") or {}),
                                                      "generated_at": data.get("generated_at")}
    return payload


# ── Build and read ──────────────────────────────────────────────────────────

_index_cache: dict[str, tuple[dict, dict]] = {}   # root -> (cache_key, index)
_index_lock = threading.Lock()


def build(root: str | Path) -> dict:
    """Map ``root`` now, store the snapshot and the index, and return the
    snapshot with its ``_cache`` note. Raises when the map cannot be built."""
    from .core import codebase_map

    root = Path(root).resolve()
    key = current_key(root)  # taken first: an edit during the build leaves the map stale
    t0 = time.time()
    smap = codebase_map(root, with_index=True)
    elapsed = time.time() - t0
    payload = smap.to_dict()
    payload["cache_key"] = key
    payload["built_in_seconds"] = round(elapsed, 2)
    merge_liveness(root, payload)
    index = smap.index or {}
    index["cache_key"] = key
    try:
        write_json_atomic(index_path(root), index, compress=True)
    except Exception as exc:
        logger.warning("Could not store the system map index for %s: %s", root, exc)
    try:
        write_json_atomic(snapshot_path(root), payload)
    except Exception as exc:
        logger.warning("Could not store the system map for %s: %s", root, exc)
    with _index_lock:
        _index_cache[str(root)] = (key, index)
    logger.info("Mapped %s in %.2fs (%d files, %d findings)", root, elapsed, smap.file_count, len(smap.findings))
    out = dict(payload)
    out["_cache"] = {"hit": False, "stale": False, "computed_in_seconds": round(elapsed, 2)}
    return out


def read_snapshot(root: str | Path) -> tuple[Optional[dict], Optional[float]]:
    """(snapshot, age in seconds) as last stored, or (None, None)."""
    path = snapshot_path(root)
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return None, None
    data = _read_json(path)
    return (data, age) if data is not None else (None, None)


def load_index(root: str | Path, payload: Optional[dict] = None) -> Optional[dict]:
    """The query index that belongs to ``payload`` (the stored snapshot when
    not given), from memory or disk; None when there is none for it."""
    root = Path(root).resolve()
    if payload is None:
        payload, _ = read_snapshot(root)
    want = (payload or {}).get("cache_key")
    if not want:
        return None
    with _index_lock:
        hit = _index_cache.get(str(root))
        if hit and hit[0] == want:
            return hit[1]
    data = _read_json(index_path(root), compressed=True)
    if not data or data.get("cache_key") != want:
        return None
    with _index_lock:
        _index_cache[str(root)] = (want, data)
    return data


# ── Single flight ───────────────────────────────────────────────────────────

class Job:
    """One background build for one root."""

    def __init__(self) -> None:
        self.started = time.time()
        self.done = threading.Event()
        self.payload: Optional[dict] = None
        self.error: Optional[str] = None


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def _run(root: Path, job: Job) -> None:
    try:
        job.payload = build(root)
    except Exception as exc:
        logger.exception("System map of %s failed", root)
        job.error = str(exc) or type(exc).__name__
    finally:
        job.done.set()


def running_job(root: str | Path) -> Optional[Job]:
    with _jobs_lock:
        job = _jobs.get(str(Path(root).resolve()))
    return job if job is not None and not job.done.is_set() else None


def start(root: str | Path) -> Job:
    """The build running for ``root``, or a new one started in the
    background. A root never has two at once, so a second caller joins the
    first."""
    root = Path(root).resolve()
    key = str(root)
    with _jobs_lock:
        job = _jobs.get(key)
        if job is not None and not job.done.is_set():
            return job
        job = Job()
        _jobs[key] = job
    threading.Thread(target=_run, args=(root, job), name="system-map", daemon=True).start()
    return job


def state(root: str | Path, refresh_if_stale: bool = True) -> tuple[Optional[dict], dict]:
    """(stored snapshot or None, cache note) without building in this thread.

    The note says how old the map is, whether it still describes the tree
    (``stale``), and whether a rebuild is running (``refreshing``). With
    ``refresh_if_stale`` a stale or missing map starts the one background
    rebuild for the root."""
    root = Path(root).resolve()
    payload, age = read_snapshot(root)
    key = current_key(root)
    current = is_current(payload, key)
    job = running_job(root)
    if not current and refresh_if_stale and job is None:
        job = start(root)
    note = {
        "hit": payload is not None,
        "age_seconds": int(age) if age is not None else None,
        "stale": not current,
        "refreshing": job is not None and not job.done.is_set(),
    }
    if job is not None and not job.done.is_set():
        note["refresh_running_seconds"] = int(time.time() - job.started)
    return payload, note
