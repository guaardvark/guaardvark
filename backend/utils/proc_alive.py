"""Whether a process ID still names a running process.

``os.kill(pid, 0)`` also succeeds for a zombie: a process that has exited but
whose parent has not collected its exit status yet. A child started with
``subprocess.Popen`` and never waited on stays a zombie for as long as its
parent runs, so a signal-0 check reports a finished job as running forever.

Standard library only, so scripts and other processes can load this file
without importing the rest of the backend. When you hold the ``Popen`` object,
``Popen.poll()`` is the better check: it reaps the child and keeps its exit
code. This helper reaps a zombie child too, after which ``Popen.poll()`` on it
reports a return code of 0.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Optional


def _windows_pid_running(pid: int) -> bool:
    """Windows has no signal 0: os.kill(pid, 0) sends CTRL_C_EVENT to a process
    group. Open the process and ask whether it has exited instead."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        synchronize, query_limited = 0x00100000, 0x1000
        handle = kernel32.OpenProcess(synchronize | query_limited, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: it exists
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT: still running
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - cannot tell: never signal, report not running
        return False


def _proc_state(pid: int) -> Optional[str]:
    """The process state letter (R, S, Z, ...), or None when it cannot be read.

    Linux: field 3 of /proc/<pid>/stat, counted after the last ')' because the
    command name in field 2 may contain spaces and parentheses. Elsewhere
    (macOS): ``ps -o stat=``.
    """
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            stat = f.read().decode("ascii", "replace")
        fields = stat[stat.rfind(")") + 1:].split()
        return fields[0] if fields else None
    except FileNotFoundError:
        if os.path.exists("/proc/self/stat"):
            return "X"  # /proc works and the entry is gone: the process just ended
    except OSError:
        return None
    try:
        out = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out[:1] or None


def _reap_if_child(pid: int) -> None:
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass  # not our child: its own parent collects it


def pid_alive(pid: Any) -> bool:
    """True while ``pid`` names a running process; a zombie counts as gone.

    A process owned by another user counts as running. A zombie that is this
    process's own child is reaped here so it does not linger.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        return _windows_pid_running(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    except OSError:
        return False
    if _proc_state(pid) not in ("Z", "X"):
        return True
    _reap_if_child(pid)
    return False
