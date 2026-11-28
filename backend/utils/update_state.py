"""Whether the running backend still matches the code on disk.

An Interconnector apply records what it changed in
``data/interconnector/pending_restart.json`` (:func:`record_update_applied`).
``/api/health`` and the apply response report :func:`restart_state`, which
compares that record and the VERSION file with this process, so the UI can say
when a page reload or a full restart is needed to finish an update.

Import this module at process start: ``BOOT_ID``, ``BOOT_TIME`` and
``STARTUP_VERSION`` are fixed at first import.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from backend.utils import frontend_serve

_VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"

PENDING_RESTART_RELPATH = Path("data") / "interconnector" / "pending_restart.json"

# pip and npm inputs: changing one means start.sh has to install before the new
# code can run.
_DEPENDENCY_FILE = re.compile(r"^(requirements[\w.-]*\.txt|constraints\.txt|package(-lock)?\.json)$")


def read_disk_version() -> Optional[str]:
    """VERSION as it is on disk now, or None when it cannot be read."""
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


BOOT_ID = uuid.uuid4().hex
BOOT_TIME = time.time()
STARTUP_VERSION = read_disk_version()


def classify_changed_paths(paths: Iterable[str]) -> Dict[str, bool]:
    """Which parts of the install a set of repo-relative paths touches.

    Backend tests are left out of ``backend_changed``: the running server never
    imports them.
    """
    backend = frontend = deps = False
    for raw in paths:
        path = str(raw).replace("\\", "/").lstrip("/")
        if _DEPENDENCY_FILE.match(path.rsplit("/", 1)[-1]):
            deps = True
        if path.startswith("backend/") and not path.startswith("backend/tests/"):
            backend = True
        elif path.startswith("frontend/"):
            frontend = True
    return {"backend_changed": backend, "frontend_changed": frontend, "deps_changed": deps}


def _pending_path(root) -> Path:
    return Path(root) / PENDING_RESTART_RELPATH


def _read_pending(root) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(_pending_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _pending_since_boot(root, boot_time: float) -> Optional[Dict[str, Any]]:
    """The record, if it was written after this process started."""
    pending = _read_pending(root)
    if not pending:
        return None
    try:
        ts = float(pending.get("ts") or 0)
    except (TypeError, ValueError):
        return None
    return pending if ts > boot_time else None


def record_update_applied(
    root,
    changed_paths: Iterable[str],
    *,
    now: Optional[float] = None,
    boot_time: float = BOOT_TIME,
) -> Dict[str, Any]:
    """Add one apply's written paths to the pending-restart record and return it.

    Applies since this process started accumulate; a record from before the
    last restart is replaced, since that restart already picked it up.
    """
    now = time.time() if now is None else now
    paths = {str(p).replace("\\", "/") for p in changed_paths if p}
    frontend_touched = classify_changed_paths(paths)["frontend_changed"]
    previous = _pending_since_boot(root, boot_time)
    if previous:
        paths.update(previous.get("paths") or [])
    ordered = sorted(paths)
    record: Dict[str, Any] = {
        "ts": now,
        "files": len(ordered),
        **classify_changed_paths(ordered),
        "paths": ordered,
    }

    target = _pending_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    if frontend_touched:
        # A served build shows the change only once dist is rebuilt.
        frontend_serve.request_rebuild(root)
    return record


def restart_state(
    root,
    startup_version: Optional[str] = STARTUP_VERSION,
    *,
    boot_time: float = BOOT_TIME,
) -> Dict[str, Any]:
    """What the UI needs to decide between nothing, a reload and a restart.

    ``restart_required`` covers backend code, dependency files and a VERSION
    that differs from the one this process started with. A frontend-only update
    sets ``update`` without it: a page reload is enough, from the moment in
    ``update["ui_ts"]``. That is the update itself under the dev server and the
    end of the rebuild when a build is served (None until then); a rebuild that
    failed leaves the change to a restart.
    """
    disk_version = read_disk_version()
    pending = _pending_since_boot(root, boot_time)

    reasons = []
    if pending and pending.get("backend_changed"):
        reasons.append("backend code changed")
    if pending and pending.get("deps_changed"):
        reasons.append("dependencies changed")
    if disk_version and startup_version and disk_version != startup_version:
        reasons.append(f"version {disk_version} is on disk, {startup_version} is running")

    update = None
    if pending:
        update = {
            key: pending.get(key)
            for key in ("ts", "files", "backend_changed", "frontend_changed", "deps_changed")
        }
        update["ui_ts"] = None
        if pending.get("frontend_changed"):
            interface = frontend_serve.interface_state(root, float(pending.get("ts") or 0))
            update["ui_ts"] = interface["ready_ts"]
            if interface["failed"]:
                reasons.append(interface["failed"])
    return {
        "boot_id": BOOT_ID,
        "disk_version": disk_version,
        "restart_required": bool(reasons),
        "restart_reason": "; ".join(reasons) or None,
        "update": update,
    }
