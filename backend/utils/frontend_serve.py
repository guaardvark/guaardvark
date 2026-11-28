"""How this install's interface is served, and getting a change into it.

start.sh writes ``.start_cache/frontend_serve`` when it launches the frontend
server: ``build`` (``vite preview`` over frontend/dist, the default) or ``dev``
(the Vite dev server over frontend/src). The dev server picks an edited file
up by itself. A served build does not: frontend/dist has to be rebuilt, which
:func:`request_rebuild` starts in a detached process
(scripts/frontend_rebuild.py), and the page reloaded once that has finished.

No record means this backend was not started by start.sh (the Docker images
serve a dist baked into the image); nothing is rebuilt there.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SERVE_RECORD = Path(".start_cache") / "frontend_serve"
BUILD_RECORD = Path(".start_cache") / "frontend_build.json"
REBUILD_SCRIPT = Path("scripts") / "frontend_rebuild.py"


def serve_mode(root) -> Optional[str]:
    """"build", "dev", or None when start.sh left no record."""
    try:
        mode = (Path(root) / SERVE_RECORD).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return mode if mode in ("build", "dev") else None


def serves_build(root) -> bool:
    return serve_mode(root) == "build"


def request_rebuild(root) -> bool:
    """Start a rebuild of frontend/dist if this install serves it.

    Returns True when a rebuild process was started. It outlives the caller,
    so it may be asked for from a request or a worker task alike; concurrent
    requests queue behind one lock inside the script.
    """
    root = Path(root)
    if not serves_build(root):
        return False
    script = root / REBUILD_SCRIPT
    if not script.is_file():
        return False
    try:
        subprocess.Popen(  # noqa: S603 - fixed argv, this install's own script
            [sys.executable, str(script), str(root)],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        logger.warning("Could not start the interface rebuild", exc_info=True)
        return False
    return True


def _read_build_record(root) -> Dict[str, Any]:
    try:
        data = json.loads((Path(root) / BUILD_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def interface_state(root, changed_at: float) -> Dict[str, Any]:
    """Whether a frontend change made at ``changed_at`` is in what is served.

    ``ready_ts`` is the moment a page reload started showing it: the change
    itself under the dev server, the end of the rebuild under a served build,
    None while that rebuild has not finished. ``failed`` carries the reason a
    rebuild could not be made, after which only a restart applies the change.
    """
    if not serves_build(root):
        return {"ready_ts": changed_at, "failed": None}
    record = _read_build_record(root)
    try:
        ended = float(record.get("ts") or 0)
    except (TypeError, ValueError):
        ended = 0.0
    if ended < changed_at:
        return {"ready_ts": None, "failed": None}
    state = record.get("state")
    if state == "ok":
        return {"ready_ts": ended, "failed": None}
    if state == "failed":
        reason = record.get("reason")
        if not reason:
            code = record.get("exit_code")
            reason = "the interface build ran out of memory" if code == 137 else f"the interface build failed (exit code {code})"
        return {"ready_ts": None, "failed": reason}
    return {"ready_ts": None, "failed": None}
