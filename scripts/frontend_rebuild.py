#!/usr/bin/env python3
"""Rebuild frontend/dist while Guaardvark runs, then swap the new build in.

    frontend_rebuild.py <checkout>

start.sh builds the interface before it serves it. A change to frontend files
after that (an Interconnector update, Guaardvark editing its own interface)
reaches the browser only through a new build, which the backend starts here as
a detached process (backend/utils/frontend_serve.py).

The build goes to frontend/dist.next and replaces frontend/dist only when it
has finished, so the running interface is never served from a half-written
folder. Each attempt is recorded in .start_cache/frontend_build.json, the file
start.sh writes and /api/health/frontend reads; ``ts`` there is when the
attempt ended, which the backend compares with the time of the change.

Standard library only: it runs outside the backend, with whatever Python
started it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # not POSIX: builds are then not serialised across processes
    fcntl = None

# Free memory a build needs before it starts. The build of this bundle peaked
# at 3.05 GB resident (Vite 8.3.6 with Rolldown and Oxc, 16 cores, 2026-11);
# 4 GiB leaves the rest of the machine a margin while it runs.
MIN_AVAILABLE_BYTES = 4 * 1024**3
MEMORY_WAIT_S = 600
MEMORY_POLL_S = 15
# A change that lands during a build starts another; a tree that never settles
# is left to the next start.
MAX_ROUNDS = 4


def _available_bytes():
    """MemAvailable on Linux; None where it cannot be read (no gate there)."""
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def _wait_for_memory(needed=MIN_AVAILABLE_BYTES, wait_s=MEMORY_WAIT_S, poll_s=MEMORY_POLL_S,
                     available=_available_bytes, sleep=time.sleep) -> bool:
    deadline = time.monotonic() + wait_s
    while True:
        free = available()
        if free is None or free >= needed:
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(poll_s)


def _fingerprint(root: Path) -> str:
    try:
        out = subprocess.run(
            ["bash", str(root / "scripts" / "lib" / "frontend_build_fp.sh"), str(root)],
            capture_output=True, text=True, timeout=120, check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return out or "unfingerprinted"


def _record(root: Path, state: str, fingerprint: str, exit_code=None, reason=None) -> None:
    out = root / ".start_cache" / "frontend_build.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "state": state,
        "exit_code": exit_code,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ts": time.time(),
        "fingerprint": fingerprint,
        "skipped": False,
        "source": "running",
    }
    if reason:
        data["reason"] = reason
    tmp = out.with_name(out.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data) + "\n", encoding="utf-8")
    os.replace(tmp, out)


def _read_stamp(dist: Path) -> str:
    try:
        return (dist / ".build_fp").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _swap(dist: Path, fresh: Path) -> None:
    """Put ``fresh`` where ``dist`` is. The preview server reads the folder
    from disk on every request, so the next request gets the new build."""
    old = dist.with_name(dist.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if dist.exists():
        os.rename(dist, old)
    os.rename(fresh, dist)
    shutil.rmtree(old, ignore_errors=True)


def _build(frontend: Path, fresh: Path, log_path: Path) -> int:
    npm = shutil.which("npm")
    if not npm:
        return 127
    shutil.rmtree(fresh, ignore_errors=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log:
        log.write(b"\n[frontend_rebuild] building after a change to the interface's files\n")
        log.flush()
        return subprocess.run(
            [npm, "run", "build", "--", "--outDir", fresh.name, "--emptyOutDir"],
            cwd=frontend, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, check=False,
        ).returncode


def rebuild(root: Path, build=_build, wait_for_memory=_wait_for_memory, fingerprint=_fingerprint) -> int:
    frontend = root / "frontend"
    dist = frontend / "dist"
    fresh = frontend / "dist.next"
    log_path = root / "logs" / "frontend.log"

    for _ in range(MAX_ROUNDS):
        fp = fingerprint(root)
        if fp != "unfingerprinted" and _read_stamp(dist) == fp and (dist / "index.html").is_file():
            # Nothing the bundle is built from differs from what is served.
            _record(root, "ok", fp, exit_code=0)
            return 0
        if not wait_for_memory():
            _record(root, "failed", fp, reason="not enough free memory to rebuild the interface while Guaardvark runs")
            return 1
        _record(root, "running", fp)
        rc = build(frontend, fresh, log_path)
        if rc != 0 or not (fresh / "index.html").is_file():
            shutil.rmtree(fresh, ignore_errors=True)
            _record(root, "failed", fp, exit_code=rc or 1)
            return rc or 1
        (fresh / ".build_fp").write_text(fp + "\n", encoding="utf-8")
        _swap(dist, fresh)
        if fingerprint(root) == fp:
            _record(root, "ok", fp, exit_code=0)
            return 0
    # Still changing after the last round: what is served is a finished build
    # of an earlier moment, and the next change or start builds again.
    _record(root, "ok", fp, exit_code=0)
    return 0


def main(argv) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    root = Path(argv[1]).resolve()
    lock_path = root / ".start_cache" / "frontend_rebuild.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as lock:
        if fcntl:
            # One build at a time; a second request waits, then finds the
            # stamp current or builds what changed since.
            fcntl.flock(lock, fcntl.LOCK_EX)
        return rebuild(root)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
