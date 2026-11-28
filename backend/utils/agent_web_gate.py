"""Web access, enforced on the screen agent's own browser.

"Web access" in Settings (allow_web_search) used to gate only the web tools:
search, fetch, analyze. The screen agent drives a real Firefox, and with web
access off it still opened any site it was asked to (2026-10-02: example.com
loaded with the setting off). This module makes the setting real for that
browser:

* The agent profile's user.js carries a managed block. With web access off it
  points every proxied request at a closed local port (127.0.0.1:9) and turns
  off the paths that would go around a proxy (DNS over HTTPS, WebRTC, direct
  failover, prefetch). localhost and file:// pages are not proxied, so local
  pages (the trainers, the Guaardvark UI) still load. With web access on, the
  block sets a direct connection.
* Firefox reads user.js only at start, so turning web access off also stops
  any running agent task and closes the agent Firefox. Turning it on closes an
  idle agent Firefox so its next start connects directly.
* scripts/agent_firefox_launch.sh and scripts/start_agent_display.sh apply the
  block before every Firefox start (``python -m backend.utils.agent_web_gate
  apply``), asking the backend for the setting and failing closed when it
  cannot be read.
"""

from __future__ import annotations

import logging
import os
import signal
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

GATE_BEGIN = "// >>> guaardvark managed: outside-site access (written by backend/utils/agent_web_gate.py)"
GATE_END = "// <<< guaardvark managed: outside-site access"

# A port nothing listens on: a proxied request is refused, never sent.
_BLACKHOLE = ("127.0.0.1", 9)

_BLOCKED_PREFS = (
    ("network.proxy.type", 1),
    ("network.proxy.http", _BLACKHOLE[0]),
    ("network.proxy.http_port", _BLACKHOLE[1]),
    ("network.proxy.ssl", _BLACKHOLE[0]),
    ("network.proxy.ssl_port", _BLACKHOLE[1]),
    ("network.proxy.socks", _BLACKHOLE[0]),
    ("network.proxy.socks_port", _BLACKHOLE[1]),
    ("network.proxy.socks_remote_dns", True),
    ("network.proxy.share_proxy_settings", False),
    ("network.proxy.no_proxies_on", "localhost, 127.0.0.1, [::1]"),
    ("network.proxy.allow_hijacking_localhost", False),
    ("network.proxy.failover_direct", False),
    ("network.trr.mode", 5),
    ("network.dns.disablePrefetch", True),
    ("network.prefetch-next", False),
    ("media.peerconnection.enabled", False),
)

_OPEN_PREFS = (
    ("network.proxy.type", 0),
    ("media.peerconnection.enabled", True),
)


def default_profile_dir() -> str:
    from backend.utils.agent_display_utils import get_firefox_profile_path
    return get_firefox_profile_path()


def _pref_line(name: str, value) -> str:
    if isinstance(value, bool):
        v = "true" if value else "false"
    elif isinstance(value, int):
        v = str(value)
    else:
        v = '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f'user_pref("{name}", {v});'


def gate_block(allowed: bool) -> str:
    prefs = _OPEN_PREFS if allowed else _BLOCKED_PREFS
    state = "ON: direct connection" if allowed else "OFF: outside sites refused, local pages only"
    lines = [GATE_BEGIN, f"// Web access {state}. Changed from Settings, not here."]
    lines += [_pref_line(n, v) for n, v in prefs]
    lines.append(GATE_END)
    return "\n".join(lines) + "\n"


def write_firefox_gate(allowed: bool, profile_dir: Optional[str] = None) -> str:
    """Put the managed block for ``allowed`` into the profile's user.js,
    replacing any earlier one. Returns the user.js path."""
    profile_dir = profile_dir or default_profile_dir()
    os.makedirs(profile_dir, exist_ok=True)
    path = os.path.join(profile_dir, "user.js")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        text = ""
    start = text.find(GATE_BEGIN)
    if start != -1:
        end = text.find(GATE_END, start)
        end = len(text) if end == -1 else end + len(GATE_END)
        text = text[:start].rstrip("\n") + "\n" + text[end:].lstrip("\n")
    text = text.rstrip("\n") + ("\n\n" if text.strip() else "") + gate_block(allowed)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    return path


def gate_state(profile_dir: Optional[str] = None) -> Optional[bool]:
    """What the profile's user.js says: True open, False blocked, None no block."""
    path = os.path.join(profile_dir or default_profile_dir(), "user.js")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return None
    start = text.find(GATE_BEGIN)
    if start == -1:
        return None
    end = text.find(GATE_END, start)
    block = text[start:] if end == -1 else text[start:end]
    return 'user_pref("network.proxy.type", 0);' in block


def agent_firefox_pids(profile_dir: Optional[str] = None) -> List[int]:
    """Main processes of a Firefox started on this install's agent profile.

    Matched on the exact profile path in the command line, so a second
    install's agent browser and the person's own Firefox are never touched.
    """
    profile_dir = os.path.realpath(profile_dir or default_profile_dir())
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                args = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
        except OSError:
            continue
        if not args or "firefox" not in os.path.basename(args[0]):
            continue
        if any(a.startswith("-contentproc") for a in args):
            continue
        if "--profile" in args:
            i = args.index("--profile")
            if i + 1 < len(args) and os.path.realpath(args[i + 1]) == profile_dir:
                pids.append(int(entry))
    return pids


def close_agent_firefox(profile_dir: Optional[str] = None, wait_s: float = 6.0) -> int:
    """Ask the agent Firefox to quit (SIGTERM), then force it. Returns how many."""
    pids = agent_firefox_pids(profile_dir)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + wait_s
    while time.time() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.2)
    for pid in pids:
        if _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    return len(pids)


def _alive(pid: int) -> bool:
    from backend.utils.proc_alive import pid_alive

    return pid_alive(pid)


def enforce(allowed: bool, reason: str = "") -> Dict[str, object]:
    """Make the agent browser match web access now.

    Off: the block goes into user.js, a running agent task is stopped and the
    agent Firefox is closed, so nothing it does after this can reach outside.
    On: the open block goes in, and an idle agent Firefox is closed so its
    next start connects directly; a running task is left alone.
    """
    out: Dict[str, object] = {"allowed": bool(allowed)}
    try:
        out["user_js"] = write_firefox_gate(allowed)
    except Exception as e:
        logger.error(f"[WEB-GATE] could not write the agent browser's prefs: {e}")
        out["error"] = f"could not write the agent browser's prefs: {e}"
    task_stopped = False
    try:
        from backend.services.agent_control_service import get_agent_control_service
        acs = get_agent_control_service()
        active = bool(getattr(acs, "_active", False))
        if not allowed and active:
            acs.kill()
            task_stopped = True
    except Exception as e:
        logger.warning(f"[WEB-GATE] agent task check failed: {e}")
        active = False
    out["task_stopped"] = task_stopped
    closed = 0
    if not allowed or not active:
        try:
            closed = close_agent_firefox()
        except Exception as e:
            logger.warning(f"[WEB-GATE] closing the agent browser failed: {e}")
    out["browser_closed"] = closed
    logger.warning(
        f"[WEB-GATE] web access {'on' if allowed else 'OFF'} ({reason or 'setting changed'}): "
        f"task stopped={task_stopped}, agent browser closed={closed}"
    )
    return out


def _setting_from_backend() -> Optional[bool]:
    """The setting as the running backend reports it, or None."""
    import json
    import urllib.request
    port = os.environ.get("FLASK_PORT", "5000")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/settings/web_access", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        value = (data.get("data") or data).get("allow_web_search")
        return None if value is None else bool(value)
    except Exception:
        return None


def main(argv: Optional[List[str]] = None) -> int:
    """``apply`` writes the block for the current setting; with the backend
    unreachable it writes the blocked one (fail closed)."""
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "apply":
        print("usage: python -m backend.utils.agent_web_gate apply [--profile DIR]")
        return 2
    profile = None
    if "--profile" in args:
        profile = args[args.index("--profile") + 1]
    allowed = _setting_from_backend()
    note = ""
    if allowed is None:
        allowed, note = False, " (backend not reachable: blocked until it is)"
    path = write_firefox_gate(bool(allowed), profile)
    print(f"agent browser web access: {'on' if allowed else 'OFF'}{note} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
