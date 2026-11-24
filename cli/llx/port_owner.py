"""Which Guaardvark install holds a TCP port.

The Python side of ``scripts/lib/proc_owner.sh``. ``guaardvark launch --full`` runs
start.sh in the background with its output discarded, so it checks the ports first
and refuses to start one install over another's running services.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


def install_root_of(path: str | os.PathLike) -> Path | None:
    """Innermost directory holding ``path`` that is a Guaardvark install
    (it has start.sh and backend/app.py), resolved, or None."""
    p = Path(path)
    if not p.is_absolute():
        return None
    for d in (p, *p.parents):
        if (d / "start.sh").is_file() and (d / "backend" / "app.py").is_file():
            return d.resolve()
    return None


def _proc_cwd(pid: int) -> str:
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        cwd = ""
        if not os.path.exists("/proc/self") and shutil.which("lsof"):
            out = subprocess.run(["lsof", "-a", "-d", "cwd", "-p", str(pid), "-Fn"],
                                 capture_output=True, text=True).stdout
            cwd = next((line[1:] for line in out.splitlines() if line.startswith("n")), "")
    return "" if cwd.endswith(" (deleted)") else cwd


def _proc_args(pid: int) -> list[str]:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0")
    except OSError:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "args="],
                             capture_output=True, text=True).stdout
        return out.split()


def _proc_env_root(pid: int) -> str:
    try:
        env = Path(f"/proc/{pid}/environ").read_bytes().decode(errors="replace")
    except OSError:
        return ""
    for item in env.split("\0"):
        if item.startswith("GUAARDVARK_ROOT="):
            return item.split("=", 1)[1]
    return ""


def process_install_root(pid: int) -> Path | None:
    """The install ``pid`` runs from: its working directory, else an absolute path on
    its command line, else GUAARDVARK_ROOT in its environment. None when no install."""
    cwd = _proc_cwd(pid)
    root = install_root_of(cwd) if cwd else None
    if root is None:
        for arg in _proc_args(pid):
            if arg.startswith("/"):
                root = install_root_of(arg)
                if root is not None:
                    break
    if root is None:
        env_root = _proc_env_root(pid)
        if env_root:
            root = install_root_of(env_root)
    return root


def port_listener_pids(port: int) -> list[int]:
    """PIDs listening on TCP ``port`` (only processes this user can see)."""
    if shutil.which("lsof"):
        out = subprocess.run(["lsof", "-i", f"TCP:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True).stdout
        pids = out.split()
    elif shutil.which("ss"):
        out = subprocess.run(["ss", "-tlpn"], capture_output=True, text=True).stdout
        pids = []
        for line in out.splitlines():
            fields = line.split()
            if len(fields) >= 4 and fields[3].rsplit(":", 1)[-1] == str(port):
                pids += re.findall(r"pid=(\d+)", line)
    else:
        return []
    return sorted({int(p) for p in pids if p.isdigit()})


def _dotenv_value(root: Path, key: str) -> str | None:
    try:
        lines = (root / ".env").read_text().splitlines()
    except OSError:
        return None
    value = None
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        if line.startswith(f"{key}="):
            value = line.split("=", 1)[1].strip().strip("'\"")
    return value


def start_ports(root: str | os.PathLike) -> list[int]:
    """The backend and web UI ports start.sh at ``root`` will use: its .env (which
    start.sh sources over the environment), then the environment, then 5000 and 5173."""
    root = Path(root)
    ports = []
    for key, default in (("FLASK_PORT", 5000), ("VITE_PORT", 5173)):
        raw = _dotenv_value(root, key) or os.environ.get(key) or str(default)
        try:
            ports.append(int(raw))
        except ValueError:
            ports.append(default)
    return ports


def other_install_holders(root: str | os.PathLike, ports) -> list[dict]:
    """Listeners on ``ports`` that belong to a Guaardvark install other than ``root``:
    ``[{"port", "pid", "install"}]``. Programs that are no install are not listed;
    start.sh decides about those as before."""
    own = Path(root).resolve()
    found = []
    for port in dict.fromkeys(int(p) for p in ports):
        for pid in port_listener_pids(port):
            holder = process_install_root(pid)
            if holder is not None and holder != own:
                found.append({"port": port, "pid": pid, "install": str(holder)})
    return found
