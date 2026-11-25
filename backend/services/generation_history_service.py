"""Bulk deletion of generation history: batch images, batch videos, audio.

Settings → Maintenance → "Delete History". Removes the on-disk batch
directories and audio outputs together with the database rows that mirror
them, which the per-batch delete routes never did (they `rmtree` and leave
`documents`, `folders` and `job_history` rows orphaned).

Scope is deliberately narrow and structural, not by folder prefix:

- image/video history = every folder under ``UPLOAD_DIR`` that contains
  ``batch_metadata.json``, wherever it was moved to in the Library. The
  media folders also hold ``Editor Renders`` and ``Text Overlay``, which are
  not history;
- single renders = files directly in ``UPLOAD_DIR/Images`` and
  ``UPLOAD_DIR/Videos`` that carry a name only a generator gives: the Bates
  stamp (``ImageGen_04-02-2026_153000_001.png``) or ComfyUI's counter
  (``<prefix>_00224_.png``). A file dropped there by hand stays;
- infographics = files in ``UPLOAD_DIR/Images/Infographics`` named as the
  infographic generator names them;
- audio history = files in ``UPLOAD_DIR/Audio`` and its ``Voice`` subfolder
  named ``<uuid4 hex>.wav``, ``.mp3`` or ``_input_params.json`` (the
  audio_foundry backends name every generation ``uuid.uuid4().hex``) plus the
  sidecar's ``.jobs/*.json`` records. Hand-named files in the same folders are
  user assets and stay;
- ComfyUI scratch = everything in ``COMFYUI_DIR/output`` and
  ``COMFYUI_DIR/input``. Every render is fetched over ``/view`` into a batch
  directory and every reference frame is pushed over ``/upload/image``, so
  what those two folders hold is a second copy of history the batch
  directories already own (no ``documents`` row points into them). The one
  exception is ``input/example.png``, which ComfyUI's own checkout tracks.
  Both folders are left alone while ComfyUI has a prompt running or queued:
  it reads ``input/`` and writes ``output/`` on the fly. The same gate covers
  the reference clips staged for it under the cache folder and its in-memory
  prompt history;
- tool outputs = the files chat, the Image Edit page and the MCP tools write
  under the outputs folder (``OUTPUT_HISTORY`` below), matched by the names
  those writers give. A hand-named file in the same folder stays;
- upscales = everything under ``outputs/upscaling/input`` and ``output``,
  left alone while an upscale is queued or running;
- previews = the Library's thumbnail cache, which holds a small copy of every
  image it has shown.

Finished projects go only when the caller names them (``OPTIONAL_KINDS``):
Film Crew productions, music videos, and the Cast Library's generated samples
that have not become training data. One an agent is working on is skipped.

Never touched: video-editor projects, cast subjects with their reference and
training images, ``data/training/loras`` (the live LoRA registry ComfyUI
loads), chat history, scheduler tasks, voice reference clips, and the consent
records kept by content hash in ``outputs/consent``.
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
# audio_foundry names a generation uuid4().hex; the ComfyUI music graph saves
# ``minimax_music3_<8 hex>`` plus SaveAudio's counter.
AUDIO_GENERATED_RE = re.compile(
    r"^([0-9a-f]{32}(\.wav|\.mp3|_input_params\.json)|minimax_music3_[0-9a-f]{8}_\d{5}_?\.(flac|mp3|wav))$"
)
# A copy made in the Library keeps the name and gains " (2)".
_COPY = r"( \(\d+\))?"
# output_registration.bates_name for single renders, and ComfyUI's SaveImage /
# SaveVideo counter, which nothing but ComfyUI writes.
# "Upscale" in the Library writes ``<name>_upscaled`` beside its source.
LOOSE_GENERATED_RE = re.compile(
    r"^((ImageGen|VideoGen)_\d{2}-\d{2}-\d{4}(_\d+)+|.+_\d{5}_)(_upscaled)?" + _COPY + r"\.[A-Za-z0-9]+$"
)
INFOGRAPHIC_RE = re.compile(r"^infographic_\d{8}_\d{6}_[0-9a-f]{6}" + _COPY + r"\.png$")
INFOGRAPHICS_SUBDIR = ("Images", "Infographics")
MEDIA_ROOTS = ("Images", "Videos")
# Where audio_foundry files spoken clips (its config.yaml output.voice_subdir).
AUDIO_VOICE_SUBDIR = "Voice"
# A batch in one of these states is still owned by a generator and is skipped.
ACTIVE_STATUSES = frozenset({"queued", "pending", "running", "processing"})
SIDECAR_TIMEOUT_S = 10
_DEFAULT_SIDECAR_URL = "http://127.0.0.1:8206"
COMFYUI_TIMEOUT_S = 5
COMFYUI_SCRATCH_DIRS = ("output", "input")
# Files ComfyUI's own checkout ships in these folders (its .gitignore exempts
# them); removing them dirties the plugin tree and breaks the stock workflow.
COMFYUI_KEEP = {"input": frozenset({"example.png"})}
# Reference clips staged for a ComfyUI prompt, under the cache folder.
COMFYUI_STAGING_DIRS = (("generated_videos", "h3_guides"), ("generated_videos", "h3_refs"))

_STAMP = r"\d{8}_\d{6}"
# Generator files under the outputs folder that no batch owns:
# (result key, subfolder, the names its writer gives).
OUTPUT_HISTORY = (
    # stills_pipeline and image_tools: chat, Image Edit and MCP results.
    ("tool_outputs", "generated_images",
     re.compile(r"^(gen|edit|nobg|outpaint|identity|scene)_" + _STAMP + r"_[0-9a-f]{8}\.(png|jpe?g|webp)$")),
    ("tool_outputs", "generated_animations",
     re.compile(r"^anim_" + _STAMP + r"_[0-9a-f]{8}\.(gif|mp4)$")),
    # The source, likeness and mask images those edits were given, with the
    # consent sidecar written beside one.
    ("tool_outputs", "edit_inputs",
     re.compile(r"^(page_src|edit_src|identity_ref|identity_mask)_[0-9a-f]{12}\.[A-Za-z0-9]+(\.consent)?$")),
    ("tool_outputs", "identity_scenes",
     re.compile(r"^scene_" + _STAMP + r"_[0-9a-f]{8}\.[A-Za-z0-9]+\.json$")),
    ("narrations", "narrations", re.compile(r"^narration_" + _STAMP + r"\.(wav|mp3)$")),
)
OUTPUT_HISTORY_KEYS = ("tool_outputs", "narrations")
UPSCALING_SUBDIR = "upscaling"
UPSCALING_SCRATCH_DIRS = ("input", "output")
THUMBNAILS_SUBDIR = "thumbnails"
# Projects a person built up over several steps; deleted only when asked for.
OPTIONAL_KINDS = ("productions", "music_videos", "cast_samples")
PRODUCTION_SUBDIRS = ("productions", "storyboards")
PRODUCTION_FOLDER_RE = re.compile(r"^(project_\d+|orphan)/productions/(\d+)(/|$)")
MUSIC_VIDEO_DIR_RE = re.compile(r"^music_video_(\d+)$")
# job_history rows are a log of work done; these process types are generation.
HISTORY_PROCESS_TYPES = frozenset({"image_generation", "video_render", "upscale"})


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


def _batch_kind(rel: str, name: str) -> str:
    """A moved batch keeps its kind: by the media folder it sits under, else by its Bates prefix."""
    if rel.startswith("Videos/") or name.startswith("VideoBatch"):
        return "videos"
    return "images"


def _discover_all_batches(upload: Path) -> list[dict[str, Any]]:
    """Every batch folder under the uploads folder, at any depth.

    A batch dragged into another Library folder is still a batch. The walk
    does not descend into a batch or into dot-folders.
    """
    import os

    batches: list[dict[str, Any]] = []
    if not upload.is_dir():
        return batches
    for root, dirs, files in os.walk(upload):
        d = Path(root)
        if BATCH_METADATA in files and d != upload:
            dirs[:] = []
            status = None
            try:
                status = json.loads((d / BATCH_METADATA).read_text(encoding="utf-8")).get("status")
            except Exception:
                status = None  # unreadable metadata is still a batch; it just cannot be running
            n, size = _tree_stats(d)
            rel = d.relative_to(upload).as_posix()
            batches.append({
                "batch_id": d.name, "dir": d, "rel": rel, "kind": _batch_kind(rel, d.name),
                "status": status, "files": n, "bytes": size,
            })
            continue
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
    return sorted(batches, key=lambda b: b["rel"])


def _matching_files(folder: Path, pattern: "re.Pattern[str]") -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and pattern.match(p.name))


def _loose_generated_files(upload: Path) -> list[Path]:
    """Single renders sitting directly in Images/ and Videos/."""
    found: list[Path] = []
    for root in MEDIA_ROOTS:
        found.extend(_matching_files(upload / root, LOOSE_GENERATED_RE))
    return found


def _infographic_files(upload: Path) -> list[Path]:
    return _matching_files(upload.joinpath(*INFOGRAPHICS_SUBDIR), INFOGRAPHIC_RE)


def _files_size(paths: Iterable[Path]) -> int:
    total = 0
    for p in paths:
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def _output_roots(output_dir: Optional[str | Path] = None) -> list[Path]:
    """The outputs folder, plus ``data/outputs`` under the install: the voice
    and upscaling routes write there whatever ``OUTPUT_DIR`` is set to."""
    if output_dir is not None:
        return [Path(output_dir)]
    from backend.config import GUAARDVARK_ROOT, OUTPUT_DIR
    roots = [Path(OUTPUT_DIR)]
    fixed = Path(GUAARDVARK_ROOT) / "data" / "outputs"
    if fixed.resolve() != roots[0].resolve():
        roots.append(fixed)
    return roots


def _cache_dir(cache_dir: Optional[str | Path] = None) -> Path:
    if cache_dir is not None:
        return Path(cache_dir)
    from backend.config import CACHE_DIR
    return Path(CACHE_DIR)


def _output_history_files(roots: Iterable[Path]) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = {key: [] for key in OUTPUT_HISTORY_KEYS}
    for root in roots:
        for key, sub, pattern in OUTPUT_HISTORY:
            found[key].extend(_matching_files(root / sub, pattern))
    return found


def _files_under(folders: Iterable[Path]) -> list[Path]:
    found: list[Path] = []
    for folder in folders:
        if folder.is_dir():
            found.extend(p for p in folder.rglob("*") if p.is_file() or p.is_symlink())
    return sorted(found)


def _upscaling_files(roots: Iterable[Path]) -> list[Path]:
    return _files_under(
        root / UPSCALING_SUBDIR / sub for root in roots for sub in UPSCALING_SCRATCH_DIRS
    )


def _prune_empty_dirs(folders: Iterable[Path]) -> None:
    """Remove the empty subfolders left under each folder; the folder itself stays."""
    import os

    for folder in folders:
        if not folder.is_dir():
            continue
        for root, _dirs, _files in os.walk(folder, topdown=False):
            if Path(root) != folder:
                try:
                    os.rmdir(root)
                except OSError:
                    pass  # not empty


def _upscales_running() -> int:
    """Upscale jobs queued or running in this process. Never constructs the
    supervisor: one that was never built has no jobs."""
    try:
        from backend.services import upscale_supervisor as mod
        supervisor = getattr(mod, "_SUPERVISOR", None)
        return supervisor.active_count() if supervisor is not None else 0
    except Exception as e:  # pragma: no cover - import failure means nothing is live
        logger.debug("upscale supervisor unavailable: %s", e)
        return 0


def _unlink_all(paths: Iterable[Path], label: str, errors: list[str]) -> tuple[list[Path], int]:
    """Remove files; returns (the ones removed, bytes freed)."""
    removed: list[Path] = []
    freed = 0
    for p in paths:
        try:
            size = p.stat().st_size
            p.unlink()
        except Exception as e:
            errors.append(f"{label} {p.name}: {e}")
            continue
        removed.append(p)
        freed += size
    return removed, freed


def _audio_root(upload: Path) -> Path:
    return upload / "Audio"


def _audio_generated_files(audio_root: Path) -> list[Path]:
    found = []
    for folder in (audio_root, audio_root / AUDIO_VOICE_SUBDIR):
        if folder.is_dir():
            found.extend(p for p in folder.iterdir() if p.is_file() and AUDIO_GENERATED_RE.match(p.name))
    return sorted(found)


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


def _comfyui_staging_files(cache: Path) -> list[Path]:
    return _files_under(cache.joinpath(*parts) for parts in COMFYUI_STAGING_DIRS)


def _comfyui_clear_history() -> None:
    """Drop the finished prompts ComfyUI keeps in memory (text and settings of
    every render since it started). An unreachable ComfyUI holds none."""
    import requests
    try:
        resp = requests.post(f"{_comfyui_url()}/history", json={"clear": True}, timeout=COMFYUI_TIMEOUT_S)
    except requests.ConnectionError:
        return
    resp.raise_for_status()


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


def live_batch_dirs() -> list[tuple[str, Path]]:
    """(batch id, folder) of every image and video batch queued or rendering
    in this process. Their workers keep writing to the folder they were given."""
    found: list[tuple[str, Path]] = []
    for kind in ("images", "videos"):
        instance = _generator_instance(kind)
        if instance is None:
            continue
        lock = getattr(instance, "batch_lock", None) or threading.Lock()
        with lock:
            statuses = dict(getattr(instance, "active_batches", {}))
        for bid, status in statuses.items():
            out = getattr(status, "output_dir", None)
            if out and getattr(status, "status", None) in ACTIVE_STATUSES:
                found.append((bid, Path(out)))
    return found


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


def _batch_targets(
    image_ids: Iterable[str], video_ids: Iterable[str], extra_paths: Iterable[str] = ()
) -> tuple[list[int], set[int]]:
    """(folder ids, document ids) that mirror the given batch directories.
    ``extra_paths`` are folder paths relative to the uploads folder, for batch
    folders that were moved below Images/ or Videos/."""
    from backend.models import db, Document, Folder

    folder_ids: list[int] = []
    doc_ids: set[int] = set()
    paths = _batch_folder_paths(image_ids, video_ids) + list(extra_paths)
    if paths:
        folder_ids = [fid for (fid,) in db.session.query(Folder.id).filter(Folder.path.in_(paths))]
    if folder_ids:
        doc_ids.update(did for (did,) in db.session.query(Document.id).filter(Document.folder_id.in_(folder_ids)))
    return folder_ids, doc_ids


def _db_targets(batch_paths: Iterable[str], file_paths: Iterable[str] = ()) -> tuple[list[int], list[int]]:
    """(folder ids, document ids) that mirror the given batch folders, the
    given single files and every generated audio file. Paths are relative to
    the uploads folder. Root folders are never included."""
    from backend.models import db, Document, Folder

    batch_set = set(batch_paths)
    file_set = set(file_paths)

    def inside_batch(path: str) -> bool:
        parent = Path(path or "")
        return any(ancestor.as_posix() in batch_set for ancestor in parent.parents)

    folder_ids = [
        fid for fid, path in db.session.query(Folder.id, Folder.path)
        if path in batch_set or inside_batch(path)
    ] if batch_set else []
    folder_set = set(folder_ids)
    doc_ids: set[int] = set()
    if batch_set or file_set:
        for did, path, folder_id in db.session.query(Document.id, Document.path, Document.folder_id):
            if folder_id in folder_set or path in file_set or (batch_set and inside_batch(path)):
                doc_ids.add(did)
    audio_folder_ids = [
        fid for (fid,) in db.session.query(Folder.id).filter(
            Folder.path.in_(["Audio", f"Audio/{AUDIO_VOICE_SUBDIR}"])
        )
    ]
    if audio_folder_ids:
        for did, path in db.session.query(Document.id, Document.path).filter(
            Document.folder_id.in_(audio_folder_ids)
        ):
            if AUDIO_GENERATED_RE.match(Path(path or "").name):
                doc_ids.add(did)
    return folder_ids, sorted(doc_ids)


def _stale_targets(upload: Path) -> tuple[list[int], list[int]]:
    """(folder ids, document ids) of Library entries whose folder is gone.

    The Library lists from these rows, not from disk, so a generated folder
    removed any other way (an older delete, a file manager) keeps showing
    there. Only folders below the media roots count, and one is left alone
    while any document filed in it still resolves to a file.
    """
    from backend.models import db, Document, Folder

    roots = (*MEDIA_ROOTS, "Audio")
    stale: dict[int, str] = {}
    for fid, path in db.session.query(Folder.id, Folder.path):
        head, sep, _rest = (path or "").partition("/")
        if sep and head in roots and not (upload / path).is_dir():
            stale[fid] = path
    if not stale:
        return [], []
    docs: dict[int, list[int]] = {}
    kept: set[str] = set()
    for did, path, folder_id in db.session.query(Document.id, Document.path, Document.folder_id).filter(
        Document.folder_id.in_(list(stale))
    ):
        file = Path(path or "")
        if path and (file if file.is_absolute() else upload / file).exists():
            kept.add(stale[folder_id])
        else:
            docs.setdefault(folder_id, []).append(did)

    def holds_a_kept_folder(path: str) -> bool:
        return any(k == path or k.startswith(path + "/") for k in kept)

    folder_ids = sorted(fid for fid, path in stale.items() if not holds_a_kept_folder(path))
    return folder_ids, sorted(did for fid in folder_ids for did in docs.get(fid, []))


def _stale_render_docs(upload: Path) -> list[int]:
    """Library entries a ComfyUI render registered whose file no longer exists
    (the clip was moved into its project, or removed by hand)."""
    from backend.models import db, Document, Folder

    media = {
        fid for fid, path in db.session.query(Folder.id, Folder.path)
        if (path or "").partition("/")[0] in MEDIA_ROOTS
    }
    if not media:
        return []
    ids = []
    for did, path, meta in db.session.query(Document.id, Document.path, Document.file_metadata).filter(
        Document.folder_id.in_(list(media))
    ):
        if isinstance(meta, str):  # the column holds JSON as text
            try:
                meta = json.loads(meta)
            except ValueError:
                continue
        if not path or not isinstance(meta, dict) or meta.get("source") != "comfyui":
            continue
        file = Path(path)
        if not (file if file.is_absolute() else upload / file).exists():
            ids.append(did)
    return sorted(ids)


# ─── optional kinds ──────────────────────────────────────────────────────────

def _project_busy(row, stage_to_agent: dict) -> bool:
    """True while an agent owns the project's current stage. A stage waiting
    on a person, a finished one and a failed or cancelled one are not busy."""
    status = row.status or ""
    if status.startswith(("failed", "cancelled")):
        return False
    return bool(stage_to_agent.get(row.current_stage or ""))


def _docs_inside(dirs: Iterable[Path]) -> list[int]:
    """Documents registered by absolute path inside any of these folders."""
    from backend.models import db, Document

    prefixes = [str(d.resolve()) + "/" for d in dirs]
    if not prefixes:
        return []
    return [
        did for did, path in db.session.query(Document.id, Document.path)
        if path and path.startswith("/") and any(path.startswith(prefix) for prefix in prefixes)
    ]


def _optional_plan(kind: str, roots: list[Path]) -> dict[str, Any]:
    """What deleting one optional kind removes: ORM ``rows``, whole ``dirs``,
    single ``files``, Library ``doc_ids`` and ``folder_ids``, and how many
    projects are ``running`` and therefore left out."""
    from backend.models import db, Document, Folder

    plan: dict[str, Any] = {"rows": [], "dirs": [], "files": [], "doc_ids": [], "folder_ids": [], "running": 0}

    if kind == "productions":
        from backend.models import Production
        from backend.services.production_service import STAGE_TO_AGENT

        rows = Production.query.all()
        busy = {p.id for p in rows if _project_busy(p, STAGE_TO_AGENT)}
        plan["running"] = len(busy)
        plan["rows"] = [p for p in rows if p.id not in busy]
        # A folder left by a production deleted earlier has no row; it goes too.
        for root in roots:
            for sub in PRODUCTION_SUBDIRS:
                if (root / sub).is_dir():
                    plan["dirs"].extend(
                        d for d in sorted((root / sub).iterdir())
                        if d.is_dir() and d.name.isdigit() and int(d.name) not in busy
                    )
        ids = {p.id for p in plan["rows"]} | {int(d.name) for d in plan["dirs"]}
        for fid, path in db.session.query(Folder.id, Folder.path):
            m = PRODUCTION_FOLDER_RE.match(path or "")
            if m and int(m.group(2)) in ids:
                plan["folder_ids"].append(fid)
        doc_ids = set(_docs_inside(plan["dirs"]))
        if plan["folder_ids"]:
            doc_ids.update(
                did for (did,) in db.session.query(Document.id).filter(Document.folder_id.in_(plan["folder_ids"]))
            )
        plan["doc_ids"] = sorted(doc_ids)

    elif kind == "music_videos":
        from backend.models import MusicVideo
        from backend.services.music_video_service import STAGE_TO_AGENT

        rows = MusicVideo.query.all()
        busy = {mv.id for mv in rows if _project_busy(mv, STAGE_TO_AGENT)}
        plan["running"] = len(busy)
        plan["rows"] = [mv for mv in rows if mv.id not in busy]
        for root in roots:
            if (root / "videos").is_dir():
                for d in sorted((root / "videos").iterdir()):
                    m = MUSIC_VIDEO_DIR_RE.match(d.name)
                    if m and d.is_dir() and int(m.group(1)) not in busy:
                        plan["dirs"].append(d)
        doc_ids = set(_docs_inside(plan["dirs"]))
        output_ids = [mv.output_document_id for mv in plan["rows"] if mv.output_document_id]
        if output_ids:
            # The finished video is filed in the Library; the editor wrote the
            # file itself into its renders folder under the same name.
            for did, filename in db.session.query(Document.id, Document.filename).filter(
                Document.id.in_(output_ids)
            ):
                doc_ids.add(did)
                for root in roots:
                    render = root / "videos" / "editor-renders" / (filename or "")
                    if filename and render.is_file():
                        plan["files"].append(render)
        plan["doc_ids"] = sorted(doc_ids)

    elif kind == "cast_samples":
        from backend.models import Subject, SubjectSample

        keep: set[str] = set()
        for (paths,) in db.session.query(Subject.ref_image_paths):
            keep.update(str(Path(x).resolve().with_suffix("")) for x in (paths or []) if isinstance(x, str))
        for sample in SubjectSample.query.all():
            if sample.promoted_to_training or sample.status == "generating":
                plan["running"] += sample.status == "generating"
                if sample.image_path:
                    keep.add(str(Path(sample.image_path).resolve().with_suffix("")))
            else:
                plan["rows"].append(sample)
        # Each sample is a picture and its caption, sharing a name.
        plan["files"] = [
            f for f in _files_under(root / "character_samples" for root in roots)
            if str(f.resolve().with_suffix("")) not in keep
        ]

    else:
        raise ValueError(f"unknown history kind: {kind}")
    return plan


def _plan_size(plan: dict[str, Any]) -> tuple[int, int]:
    files, size = len(plan["files"]), _files_size(plan["files"])
    for d in plan["dirs"]:
        n, b = _tree_stats(d)
        files, size = files + n, size + b
    return files, size


def _count_optional(roots: list[Path]) -> dict[str, Any]:
    counts: dict[str, Any] = {}
    for kind in OPTIONAL_KINDS:
        try:
            plan = _optional_plan(kind, roots)
            files, size = _plan_size(plan)
            counts[kind] = {"items": len(plan["rows"]), "files": files, "bytes": size, "running": plan["running"]}
        except Exception as e:
            logger.warning("generation history count for %s failed: %s", kind, e)
            counts[kind] = {"items": 0, "files": 0, "bytes": 0, "running": 0}
    return counts


def _delete_optional(kind: str, roots: list[Path], errors: list[str]) -> dict[str, int]:
    from backend.models import db

    plan = _optional_plan(kind, roots)
    files, size = _plan_size(plan)
    removed = {"items": len(plan["rows"]), "files": files, "bytes": size, "skipped": plan["running"]}
    for d in plan["dirs"]:
        try:
            shutil.rmtree(d)
        except Exception as e:
            errors.append(f"{kind}/{d.name}: {e}")
    _unlink_all(plan["files"], kind, errors)
    if kind == "cast_samples":
        _prune_empty_dirs(root / "character_samples" for root in roots)
    _delete_db_rows(plan["folder_ids"], plan["doc_ids"], [])
    try:
        for row in plan["rows"]:
            db.session.delete(row)  # through the session, so shots and versions cascade
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return removed


def _job_history_ids(image_ids: Iterable[str], keep: Iterable[str] = ()) -> list[str]:
    """job_history rows for generation work.

    Generation shares the ``unified`` kind with indexing and other processes,
    so rows are picked by ``native_id`` or by the ``process_type`` a generator
    stamps (``HISTORY_PROCESS_TYPES``), never by kind alone.
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
        if native_id in image_set or process_type in HISTORY_PROCESS_TYPES:
            ids.append(jid)
    return ids


def _batch_job_history_ids(batch_ids: Iterable[str]) -> list[str]:
    """job_history rows recorded for these batches, matched on native_id only.

    The bulk path additionally sweeps every ``unified`` row stamped
    image_generation, which is right when all batches are going and wrong for
    one: that would take other batches' history with it.
    """
    from backend.models import db, JobHistory

    ids = [b for b in batch_ids if b]
    if not ids:
        return []
    return [jid for (jid,) in db.session.query(JobHistory.id).filter(JobHistory.native_id.in_(ids))]


def delete_batch_history_rows(
    *,
    image_batch_ids: Iterable[str] = (),
    video_batch_ids: Iterable[str] = (),
    batch_dirs: Iterable[str | Path] = (),
    triggered_by: str = "batch_delete",
) -> dict[str, int]:
    """Remove the database rows mirroring these batch directories.

    The per-batch delete routes own the directory; this owns everything that
    pointed into it — documents (and their vectors), the folder row and
    job_history — so deleting one batch leaves the state Settings →
    Maintenance → Delete History leaves for all of them. Records the same
    retention_audit row that path records.
    """
    image_ids = [b for b in image_batch_ids if b]
    video_ids = [b for b in video_batch_ids if b]
    upload = _upload_dir().resolve()
    moved_paths = []
    for d in batch_dirs:
        try:
            moved_paths.append(Path(d).resolve().relative_to(upload).as_posix())
        except (TypeError, ValueError):
            continue
    folder_ids, doc_ids = _batch_targets(image_ids, video_ids, moved_paths)
    jh_ids = _batch_job_history_ids(image_ids + video_ids)
    deleted = _delete_db_rows(folder_ids, sorted(doc_ids), jh_ids)

    if any(deleted.values()):
        from backend.services.retention_audit_service import record_deletion
        record_deletion(
            actor="user",
            kind="generation_history_batch",
            operation="manual_delete",
            item_count=sum(deleted.values()),
            parameters={"images": image_ids, "videos": video_ids, **deleted},
            triggered_by=triggered_by,
        )
    return deleted


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
    upload_dir: Optional[str | Path] = None,
    comfyui_dir: Optional[str | Path] = None,
    output_dir: Optional[str | Path] = None,
    cache_dir: Optional[str | Path] = None,
) -> dict[str, Any]:
    """What "Delete History" would remove, for the confirmation dialog."""
    upload = _upload_dir(upload_dir)
    batches = _discover_all_batches(upload)
    images = [b for b in batches if b["kind"] == "images"]
    videos = [b for b in batches if b["kind"] == "videos"]
    loose = _loose_generated_files(upload)
    infographics = _infographic_files(upload)
    audio_root = _audio_root(upload)
    audio_files = _audio_generated_files(audio_root)
    audio_jobs = _audio_job_files(audio_root)
    comfy = _comfyui_dir(comfyui_dir)
    scratch = {sub: _comfyui_scratch(comfy, sub) for sub in COMFYUI_SCRATCH_DIRS}
    roots = _output_roots(output_dir)
    cache = _cache_dir(cache_dir)
    outputs = _output_history_files(roots)
    upscales = _upscaling_files(roots)
    staging = _comfyui_staging_files(cache)
    thumbnails = _files_under([cache / THUMBNAILS_SUBDIR])

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
        folder_ids, doc_ids = _db_targets(
            [b["rel"] for b in batches],
            [p.relative_to(upload).as_posix() for p in loose + infographics],
        )
        stale_folders, stale_docs = _stale_targets(upload)
        stale_docs = stale_docs + _stale_render_docs(upload)
        db_counts = {
            "documents": len(set(doc_ids) | set(stale_docs)),
            "folders": len(set(folder_ids) | set(stale_folders)),
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
    comfy_counts["staging"] = {"files": len(staging), "bytes": _files_size(staging)}
    comfy_bytes = sum(c["bytes"] for c in comfy_counts.values())
    loose_bytes = _files_size(loose)
    infographic_bytes = _files_size(infographics)
    extra = {
        **{key: {"files": len(paths), "bytes": _files_size(paths)} for key, paths in outputs.items()},
        "upscales": {"files": len(upscales), "bytes": _files_size(upscales), "running": _upscales_running()},
        "previews": {"files": len(thumbnails), "bytes": _files_size(thumbnails)},
    }
    total_bytes = (
        sum(b["bytes"] for b in batches) + audio_bytes + comfy_bytes + loose_bytes + infographic_bytes
        + sum(c["bytes"] for c in extra.values())
    )
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
        "loose": {"files": len(loose), "bytes": loose_bytes},
        "infographics": {"files": len(infographics), "bytes": infographic_bytes},
        **extra,
        "optional": _count_optional(roots),
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
    output_dir: Optional[str | Path] = None,
    cache_dir: Optional[str | Path] = None,
    include: Iterable[str] = (),
    triggered_by: str = "settings_ui",
) -> dict[str, Any]:
    """Delete all batch-image, batch-video and audio generation history,
    single renders, infographics, tool outputs, narrations, upscales and
    Library previews, plus ComfyUI's output/ and input/ scratch.

    Each stage is isolated: a failure is recorded in ``errors`` and the
    remaining stages still run, so a bad sidecar cannot leave batch
    directories behind and a bad directory cannot leave DB rows behind.
    Batches a generator is still working on are skipped and reported.
    ``include`` names the ``OPTIONAL_KINDS`` to delete as well.
    """
    upload = _upload_dir(upload_dir)
    errors: list[str] = []
    skipped: dict[str, Any] = {"images": [], "videos": [], "audio": [], "comfyui": [], "upscales": 0}
    deleted: dict[str, Any] = {
        "images": {"batches": 0, "files": 0, "bytes": 0},
        "videos": {"batches": 0, "files": 0, "bytes": 0},
        "audio": {"jobs": 0, "files": 0, "bytes": 0},
        "loose": {"files": 0, "bytes": 0},
        "infographics": {"files": 0, "bytes": 0},
        "tool_outputs": {"files": 0, "bytes": 0},
        "narrations": {"files": 0, "bytes": 0},
        "upscales": {"files": 0, "bytes": 0},
        "previews": {"files": 0, "bytes": 0},
        "comfyui": {sub: {"files": 0, "bytes": 0} for sub in (*COMFYUI_SCRATCH_DIRS, "staging")},
        "documents": 0,
        "folders": 0,
        "job_history": 0,
    }
    removed_ids: dict[str, list[str]] = {"images": [], "videos": []}
    removed_batch_paths: list[str] = []
    removed_file_paths: list[str] = []

    # 1. Batch directories. A batch whose metadata still says "running" but
    #    that no live generator owns was interrupted; it is history now.
    generators = {"images": _generator_instance("images"), "videos": _generator_instance("videos")}
    live = {kind: _active_batch_ids(generators[kind]) for kind in ("images", "videos")}
    for batch in _discover_all_batches(upload):
        kind, bid = batch["kind"], batch["batch_id"]
        if bid in live[kind]:
            skipped[kind].append(bid)
            continue
        try:
            shutil.rmtree(batch["dir"])
        except Exception as e:
            errors.append(f"{kind}/{bid}: {e}")
            continue
        removed_ids[kind].append(bid)
        removed_batch_paths.append(batch["rel"])
        deleted[kind]["batches"] += 1
        deleted[kind]["files"] += batch["files"]
        deleted[kind]["bytes"] += batch["bytes"]
    for kind in ("images", "videos"):
        _forget_batches(generators[kind], removed_ids[kind])

    # 1a. Single renders and infographics: files, written whole when a render ends.
    for key, paths, label in (
        ("loose", _loose_generated_files(upload), "render"),
        ("infographics", _infographic_files(upload), "infographic"),
    ):
        removed, freed = _unlink_all(paths, label, errors)
        deleted[key]["files"] = len(removed)
        deleted[key]["bytes"] = freed
        removed_file_paths.extend(p.relative_to(upload).as_posix() for p in removed)

    # 1a'. Tool outputs and narrations under the outputs folder, upscales
    #      unless one is in flight (it reads input/ and writes output/), and
    #      the previews the Library made of all of the above.
    roots = _output_roots(output_dir)
    cache = _cache_dir(cache_dir)
    for key, paths in _output_history_files(roots).items():
        removed, freed = _unlink_all(paths, key, errors)
        deleted[key] = {"files": len(removed), "bytes": freed}
    skipped["upscales"] = _upscales_running()
    if not skipped["upscales"]:
        removed, freed = _unlink_all(_upscaling_files(roots), "upscale", errors)
        deleted["upscales"] = {"files": len(removed), "bytes": freed}
        _prune_empty_dirs(
            root / UPSCALING_SUBDIR / sub for root in roots for sub in UPSCALING_SCRATCH_DIRS
        )
    removed, freed = _unlink_all(_files_under([cache / THUMBNAILS_SUBDIR]), "preview", errors)
    deleted["previews"] = {"files": len(removed), "bytes": freed}
    _prune_empty_dirs([cache / THUMBNAILS_SUBDIR])

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
        removed, freed = _unlink_all(_comfyui_staging_files(cache), "comfyui staging", errors)
        deleted["comfyui"]["staging"] = {"files": len(removed), "bytes": freed}
        try:
            _comfyui_clear_history()
        except Exception as e:
            errors.append(f"comfyui history: {e}")

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

    # 3. Database mirrors of what is now gone from disk, by this run or earlier.
    try:
        folder_ids, doc_ids = _db_targets(removed_batch_paths, removed_file_paths)
        stale_folders, stale_docs = _stale_targets(upload)
        stale_docs = stale_docs + _stale_render_docs(upload)
        jh_ids = _job_history_ids(removed_ids["images"], keep=skipped["images"] + skipped["videos"])
        deleted.update(_delete_db_rows(
            sorted(set(folder_ids) | set(stale_folders)), sorted(set(doc_ids) | set(stale_docs)), jh_ids
        ))
    except Exception as e:
        errors.append(f"database: {e}")

    # 3a. The projects the caller asked for. After the sweep above, which looks
    #     at the disk as it was left by the stages before it.
    deleted["optional"] = {}
    for kind in OPTIONAL_KINDS:
        if kind not in set(include):
            continue
        try:
            deleted["optional"][kind] = _delete_optional(kind, roots, errors)
        except Exception as e:
            errors.append(f"{kind}: {e}")

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
        for key in ("loose", "infographics", "tool_outputs", "narrations", "upscales", "previews"):
            if deleted[key]["files"]:
                record_deletion(
                    actor="user",
                    kind=f"generation_history_{key}",
                    operation="bulk_delete",
                    item_count=deleted[key]["files"],
                    bytes_freed=deleted[key]["bytes"],
                    triggered_by=triggered_by,
                )
        for kind, removed in deleted["optional"].items():
            if removed["items"] or removed["files"]:
                record_deletion(
                    actor="user",
                    kind=f"generation_history_{kind}",
                    operation="bulk_delete",
                    item_count=removed["items"],
                    bytes_freed=removed["bytes"],
                    parameters=removed,
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
        + deleted["loose"]["bytes"] + deleted["infographics"]["bytes"]
        + sum(deleted[key]["bytes"] for key in ("tool_outputs", "narrations", "upscales", "previews"))
        + sum(c["bytes"] for c in deleted["comfyui"].values())
        + sum(r["bytes"] for r in deleted["optional"].values())
    )
    return {
        "success": not errors,
        "deleted": deleted,
        "skipped": skipped,
        "errors": errors,
        "sidecar_available": sidecar_available,
        "bytes_freed": bytes_freed,
    }
