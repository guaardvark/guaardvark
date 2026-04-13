"""Plugin Runner Sidecar.

A small standalone Python process that runs plugin start/stop scripts on
behalf of the main backend.

WHY THIS EXISTS
---------------
PyTorch + CUDA + fork() is undefined behavior. Once the main backend imports
torch and initializes CUDA (which happens during the very first image gen,
or even just at import time on line 82 of backend/app.py), every subsequent
subprocess.run() forks a child that inherits CUDA driver state the parent
thinks it owns exclusively. This corrupts CUDA state in the parent. The
process eventually aborts with no Python traceback — only a "leaked semaphore"
warning from multiprocessing.resource_tracker at interpreter shutdown.

(Observed twice on 2026-04-11: PIDs 3047360 and 3065470 both died this way
after the user toggled GPU plugins on/off a few times.)

The fix: spawn THIS sidecar at backend startup BEFORE torch is imported.
The sidecar's process tree never loads CUDA. When IT forks for subprocess.run,
no CUDA corruption occurs. The main backend never forks again — it just sends
JSON commands here and reads JSON responses back.

PROTOCOL
--------
Line-delimited JSON over stdin/stdout.

Request:
    {"id": int, "cmd": "run", "argv": [str], "cwd": str, "timeout": int}
    {"id": int, "cmd": "ping"}
    {"id": int, "cmd": "shutdown"}

Response:
    {"id": int, "ok": bool, "stdout": str, "stderr": str, "rc": int, "error": str?}

The response "id" always matches the request "id", so the client can verify
correlation. Requests are serialized by a lock on the client side.

USAGE
-----
From backend/app.py, before the first `import torch`:

    from backend.services.plugin_runner import PluginRunnerClient
    PluginRunnerClient.get().start()

From plugin_manager.py (or anywhere else):

    from backend.services.plugin_runner import PluginRunnerClient
    result = PluginRunnerClient.get().run(
        argv=["bash", "/path/to/plugin/start.sh"],
        cwd="/path/to/plugin",
        timeout=60,
    )
    if result["ok"] and result["rc"] == 0:
        ...

DO NOT import anything from backend.* in this module — it must be possible
to run as a standalone script with no torch in sys.modules.
"""

import json
import logging
import os
import subprocess
import sys
import threading
from typing import List, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# SIDECAR (runs when this file is invoked as `python plugin_runner.py --sidecar`)
# ============================================================================

def _sidecar_main() -> None:
    """Main loop for the sidecar process. Reads requests on stdin, writes responses on stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except Exception as e:
            sys.stdout.write(json.dumps({"id": -1, "ok": False, "error": f"bad json: {e}"}) + "\n")
            sys.stdout.flush()
            continue

        req_id = req.get("id", -1)
        cmd = req.get("cmd")

        if cmd == "ping":
            resp = {"id": req_id, "ok": True}
        elif cmd == "shutdown":
            sys.stdout.write(json.dumps({"id": req_id, "ok": True}) + "\n")
            sys.stdout.flush()
            return  # Exit cleanly
        elif cmd == "run":
            argv = req.get("argv") or []
            cwd = req.get("cwd")
            timeout = req.get("timeout", 60)
            try:
                result = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=cwd,
                )
                resp = {
                    "id": req_id,
                    "ok": True,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "rc": result.returncode,
                }
            except subprocess.TimeoutExpired as e:
                resp = {
                    "id": req_id,
                    "ok": False,
                    "error": f"timeout after {e.timeout}s",
                    "stdout": (e.stdout or "") if isinstance(e.stdout, str) else "",
                    "stderr": (e.stderr or "") if isinstance(e.stderr, str) else "",
                    "rc": -1,
                }
            except Exception as e:
                resp = {
                    "id": req_id,
                    "ok": False,
                    "error": str(e),
                    "stdout": "",
                    "stderr": "",
                    "rc": -1,
                }
        else:
            resp = {"id": req_id, "ok": False, "error": f"unknown cmd: {cmd}"}

        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


# ============================================================================
# CLIENT (used by the main backend)
# ============================================================================

class PluginRunnerClient:
    """Singleton client that talks to the plugin_runner sidecar.

    Call PluginRunnerClient.get().start() exactly once at backend startup,
    BEFORE torch is imported. After that, run() is thread-safe.
    """

    _instance: Optional["PluginRunnerClient"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._stderr_thread: Optional[threading.Thread] = None

    @classmethod
    def get(cls) -> "PluginRunnerClient":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start(self) -> None:
        """Spawn the sidecar process. Idempotent — does nothing if already running."""
        if self._proc is not None and self._proc.poll() is None:
            return  # Already running

        # Run the sidecar as a script (not a module) to bypass package import
        # machinery and guarantee no transitive backend.* imports.
        sidecar_path = os.path.abspath(__file__)
        self._proc = subprocess.Popen(
            [sys.executable, sidecar_path, "--sidecar"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
        )

        # Drain sidecar stderr in a background thread so the 64KB pipe buffer
        # can't fill up and deadlock subprocess.run inside the sidecar. In
        # normal operation the sidecar writes nothing to stderr, but any
        # unexpected Python warning, traceback, or resource notice would
        # silently wedge the whole plugin subsystem without this drain.
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            daemon=True,
            name="plugin_runner-stderr-drain",
        )
        self._stderr_thread.start()

        # Sanity check with a ping
        result = self._send({"cmd": "ping"})
        if not result.get("ok"):
            raise RuntimeError(f"Plugin runner sidecar failed ping: {result}")

    def _drain_stderr(self) -> None:
        """Continuously read the sidecar's stderr stream so the pipe buffer
        never fills. Anything the sidecar writes to stderr is logged as debug
        (it's noise for normal ops but useful when debugging crashes)."""
        if not self._proc or not self._proc.stderr:
            return
        try:
            for line in self._proc.stderr:
                stripped = line.rstrip()
                if stripped:
                    logger.debug(f"plugin_runner sidecar stderr: {stripped}")
        except Exception as e:
            logger.debug(f"plugin_runner stderr drain exited: {e}")

    def run(self, argv: List[str], cwd: Optional[str] = None, timeout: int = 60) -> dict:
        """Run a command in the sidecar. Returns a dict with keys:
            ok    (bool) — True if the command ran (regardless of exit code)
            rc    (int)  — exit code (0 = success)
            stdout, stderr (str)
            error (str, optional) — set if ok is False
        """
        return self._send({"cmd": "run", "argv": argv, "cwd": cwd, "timeout": timeout})

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def shutdown(self) -> None:
        """Tell the sidecar to exit cleanly. Safe to call multiple times."""
        if self._proc is None or self._proc.poll() is not None:
            return
        try:
            self._send({"cmd": "shutdown"})
        except Exception:
            pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            try:
                self._proc.wait(timeout=2)
            except Exception:
                pass

    def _send(self, req: dict) -> dict:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                raise RuntimeError("Plugin runner sidecar is not running")

            req_id = self._next_id
            self._next_id += 1
            req["id"] = req_id

            try:
                self._proc.stdin.write(json.dumps(req) + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                raise RuntimeError(f"Plugin runner sidecar pipe broken: {e}")

            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError("Plugin runner sidecar closed unexpectedly")

            try:
                resp = json.loads(line)
            except Exception as e:
                raise RuntimeError(f"Plugin runner sidecar returned bad JSON: {line!r} ({e})")

            if resp.get("id") != req_id:
                raise RuntimeError(
                    f"Plugin runner sidecar id mismatch: expected {req_id}, got {resp.get('id')}"
                )

            return resp


# ============================================================================
# Entry point
# ============================================================================

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--sidecar":
        _sidecar_main()
    else:
        print("Usage: python plugin_runner.py --sidecar", file=sys.stderr)
        sys.exit(1)
