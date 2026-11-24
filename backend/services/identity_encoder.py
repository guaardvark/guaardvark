"""The image encoder behind the Cast identity check: where it lives, Install, Remove.

The "embed" identity method (video_consistency_metrics) compares pictures with a
self-supervised encoder declared in identity_method_data.IMAGE_ENCODERS. Its
weights reach this machine only through ``start_install``, which the Install
button in Settings > Generation > Cast identity check calls. Nothing here runs
at start-up, and the identity check itself only ever reads the folder.

Install fetches the declared files at the pinned commit into
data/models/identity_encoder/<folder>/, checks the weights against the declared
sha256, and only then writes ``install.json``. A folder without that record, or
with a record for another commit, is not installed: a half-finished or tampered
download never reaches a score. No Hugging Face token is sent (token=False); the
repo is public, and a token in .env has no business on this request.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from backend.services.identity_method_data import IDENTITY_METHODS, IMAGE_ENCODERS

logger = logging.getLogger(__name__)

RECORD_NAME = "install.json"

# No byte arriving for this long fails the install with a plain reason, the same
# limit the other model downloads use.
_STALL_SECONDS = 180

_lock = threading.Lock()
_job: Dict[str, Any] = {}


class InstallFailed(RuntimeError):
    """The download arrived but is not the declared encoder; the message says why."""


class Refused(Exception):
    """An Install or Remove that cannot happen now, with the reason a person reads."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _idle_job() -> Dict[str, Any]:
    return {"state": "idle", "progress": 0, "downloaded_mb": 0.0, "error": None,
            "started_at": None, "finished_at": None, "epoch": 0}


_job.update(_idle_job())


def encoder_repo() -> str:
    """The encoder the embed method names (a key of IMAGE_ENCODERS)."""
    return (IDENTITY_METHODS.get("embed") or {}).get("encoder") or ""


def encoder_entry(repo: Optional[str] = None) -> Dict[str, Any]:
    entry = IMAGE_ENCODERS.get(repo or encoder_repo())
    if entry is None:
        raise KeyError(f"no image encoder is declared for {repo or encoder_repo()!r}")
    return entry


def install_root() -> Path:
    """data/models/identity_encoder under GUAARDVARK_ROOT (the checkout by default)."""
    root = os.environ.get("GUAARDVARK_ROOT") or str(Path(__file__).resolve().parents[2])
    return Path(root) / "data" / "models" / "identity_encoder"


def encoder_dir(repo: Optional[str] = None) -> Path:
    return install_root() / encoder_entry(repo)["folder"]


def _read_record(folder: Path) -> Dict[str, Any]:
    try:
        return json.loads((folder / RECORD_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def installed_path(repo: Optional[str] = None) -> Optional[Path]:
    """The encoder's folder when a verified install of the pinned commit is there.

    Cheap enough to call before every check: a record read and a few stats,
    no hashing (the hash was checked once, before the record was written).
    """
    repo = repo or encoder_repo()
    entry = IMAGE_ENCODERS.get(repo)
    if entry is None:
        return None
    folder = install_root() / entry["folder"]
    record = _read_record(folder)
    if record.get("revision") != entry["revision"]:
        return None
    if record.get("weights_sha256") != entry["weights_sha256"]:
        return None
    from backend.utils.model_file_integrity import file_complete
    for name in entry["files"]:
        if not file_complete(folder / name):
            return None
    return folder


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _dir_bytes(folder: Path) -> int:
    """Bytes under ``folder``, including the hub client's .incomplete staging."""
    total = 0
    if not folder.exists():
        return 0
    for path in folder.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            pass
    return total


def _job_snapshot() -> Dict[str, Any]:
    with _lock:
        return {k: v for k, v in _job.items() if k != "epoch"}


def status() -> Dict[str, Any]:
    """What the Settings control shows: the entry, whether it is here, any install."""
    from backend.services.identity_method_data import proven_for_model
    from backend.services.video_consistency_metrics import QUALITY_THRESHOLDS

    repo = encoder_repo()
    entry = encoder_entry(repo)
    installed = installed_path(repo) is not None
    proven = proven_for_model("embed", repo, digest=entry["weights_sha256"])
    # The method the post-train smoke check reaches first (score_smoke_identity
    # order): the encoder when it is here and proven, else the vision model when
    # it has any measured row (whether that row matches the installed vision
    # model is only known when it runs), else none.
    if installed and proven:
        check = "embed"
    elif (IDENTITY_METHODS.get("vlm") or {}).get("measured"):
        check = "vlm"
    else:
        check = None
    return {
        "repo": repo,
        "name": entry["name"],
        "license": entry["license"],
        "revision": entry["revision"],
        "size_bytes": entry["size_bytes"],
        "size_mb": round(entry["size_bytes"] / 1_000_000),
        "source": f"https://huggingface.co/{repo}",
        "location": f"data/models/identity_encoder/{entry['folder']}",
        "installed": installed,
        # Whether a measured row lets this encoder's score be shown at all;
        # installed but unproven still runs and reports "not measured".
        "proven": proven,
        "smoke_check": check,
        "smoke_threshold": (IDENTITY_METHODS.get("embed") or {}).get("threshold"),
        # Clips compare the keyframe with the middle frame by the encoder once it
        # is installed, by colour until then.
        "clip_check": "embed" if installed else "colour",
        "clip_floor": QUALITY_THRESHOLDS["keyframe_match_floor"]["value"],
        "install": _job_snapshot(),
    }


def _download_error(exc: BaseException, repo: str) -> str:
    text = str(exc).lower()
    if "offline" in text:
        return ("Hugging Face downloads are switched off in this backend process "
                "(HF_HUB_OFFLINE). Unset it and restart Guaardvark to install.")
    from backend.services.video_model_registry import classify_hf_download_error
    return classify_hf_download_error(exc, repo_id=repo)


def start_install() -> Dict[str, Any]:
    """Fetch the encoder in a background thread. The only path that downloads it."""
    repo = encoder_repo()
    entry = encoder_entry(repo)
    if installed_path(repo) is not None:
        raise Refused(f"{entry['name']} is already installed.")
    with _lock:
        if _job["state"] == "running":
            raise Refused(f"{entry['name']} is already installing.")
        epoch = _job["epoch"] + 1
        _job.update(_idle_job())
        _job.update({"state": "running", "started_at": time.time(), "epoch": epoch})
    threading.Thread(target=_run_install, args=(repo, epoch), daemon=True,
                     name="identity-encoder-install").start()
    return status()


def _run_install(repo: str, epoch: int) -> None:
    entry = encoder_entry(repo)
    folder = install_root() / entry["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    baseline = _dir_bytes(folder)
    total = max(1, int(entry["size_bytes"]))
    stop = threading.Event()

    def update(**fields) -> None:
        with _lock:
            if _job["epoch"] == epoch and _job["state"] == "running":
                _job.update(fields)

    def monitor() -> None:
        last, changed = -1, time.monotonic()
        while not stop.wait(1.0):
            done = max(0, _dir_bytes(folder) - baseline)
            if done != last:
                last, changed = done, time.monotonic()
            elif time.monotonic() - changed > _STALL_SECONDS:
                update(state="failed", finished_at=time.time(),
                       error=(f"The download stalled: nothing arrived for {_STALL_SECONDS} s. "
                              "Check the network and click Install again."))
                return
            update(progress=min(99, int(100 * done / total)),
                   downloaded_mb=round(done / 1_000_000, 1))

    watcher = threading.Thread(target=monitor, daemon=True, name="identity-encoder-progress")
    watcher.start()
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id=repo,
            revision=entry["revision"],
            allow_patterns=list(entry["files"]),
            local_dir=str(folder),
            token=False,
        )
        missing = [n for n in entry["files"] if not (folder / n).is_file()]
        if missing:
            raise InstallFailed(f"The download finished without {', '.join(missing)}.")
        got = _sha256(folder / entry["weights_file"])
        if got != entry["weights_sha256"]:
            (folder / entry["weights_file"]).unlink(missing_ok=True)
            raise InstallFailed(
                f"{entry['weights_file']} does not match the pinned sha256 "
                f"(got {got[:12]}, expected {entry['weights_sha256'][:12]}); it was deleted."
            )
        (folder / RECORD_NAME).write_text(json.dumps({
            "repo": repo,
            "revision": entry["revision"],
            "weights_sha256": got,
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }, indent=2), encoding="utf-8")
        stop.set()
        update(state="done", progress=100, finished_at=time.time(),
               downloaded_mb=round(entry["size_bytes"] / 1_000_000, 1))
        logger.info("Identity encoder installed: %s at %s", repo, entry["revision"][:12])
    except Exception as e:  # noqa: BLE001 - every failure is a reason on the button
        logger.error("Identity encoder install failed: %s", e)
        reason = str(e) if isinstance(e, InstallFailed) else _download_error(e, repo)
        update(state="failed", error=reason, finished_at=time.time())
    finally:
        stop.set()


def remove() -> Dict[str, Any]:
    """Delete the encoder's folder and drop any copy this process has loaded."""
    repo = encoder_repo()
    entry = encoder_entry(repo)
    with _lock:
        if _job["state"] == "running":
            raise Refused(f"{entry['name']} is installing; wait for it to finish.")
    folder = install_root() / entry["folder"]
    if not folder.exists():
        raise Refused(f"{entry['name']} is not installed; there is nothing to remove.")
    shutil.rmtree(folder)
    with _lock:
        _job.update(_idle_job() | {"epoch": _job["epoch"]})
    try:
        from backend.services import video_consistency_metrics as vcm
        vcm.forget_image_encoder(repo)
    except Exception as e:  # noqa: BLE001 - the files are gone either way
        logger.debug("could not drop the loaded encoder: %s", e)
    logger.info("Identity encoder removed: %s", repo)
    return status()
