"""Hardened subprocess runner for the plugin's CLI calls (auto-editor, melt).

Why this exists — do NOT revert to ``subprocess.run(capture_output=True)``:

    auto-editor and melt each spawn ffmpeg as a *grandchild* that inherits the
    stdout/stderr PIPE write-ends. ``subprocess.run(capture_output=True)`` calls
    ``Popen.communicate()``, which drains those pipes with reader threads until
    EOF on *every* write-end. If a grandchild (ffmpeg) is still alive — or even
    just briefly overlaps the parent's exit — the write-end stays open, EOF
    never arrives, ``communicate()`` blocks, and the direct child is left a
    zombie (``Z``) because the call never reaches ``wait()``. Observed
    2026-05-30 as an intermittent hang on ``/auto-editor/trim``: the output file
    was fully written but the HTTP response never returned.

    Redirecting child output to a regular file instead of a pipe removes the
    failure mode entirely: ``Popen.wait()`` waits only for the *immediate* child
    and returns the instant it exits, regardless of any lingering grandchild.

Hardening applied:
  * stdout+stderr -> a temp file (no PIPE, no communicate(), no reader threads)
  * stdin = DEVNULL (a server-spawned child must never block on TTY input)
  * start_new_session=True so a timeout can SIGKILL the whole process group
    (auto-editor + its ffmpeg grandchildren), not just the direct child.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import tempfile
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ProcResult:
    """Outcome of a hardened run. ``output`` is merged stdout+stderr."""

    returncode: int
    output: str


def run_logged(cmd: list[str], *, timeout_s: float = 600.0) -> ProcResult:
    """Run ``cmd`` capturing merged output to a temp file. See module docstring.

    On timeout the entire process group is SIGKILLed (so leaked ffmpeg
    grandchildren are reaped) and ``subprocess.TimeoutExpired`` is re-raised,
    matching the previous ``subprocess.run(timeout=...)`` contract.
    """
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as logf:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait()
            raise
        logf.seek(0)
        return ProcResult(returncode=proc.returncode, output=logf.read())
