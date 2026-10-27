"""Run code a model wrote without network access.

execute_python and execute_javascript run what a chat model writes. Unless
the person allows it (Settings, model_code_network; off by default), that code
runs with no network: on Linux inside a bubblewrap network namespace, where
connections fail with "Network is unreachable" and names do not resolve, while
files, devices and the working folder stay as they are. Loopback inside the
namespace is its own, so the code cannot reach local services either. Where no
such sandbox is available (no bwrap, a container without user namespaces,
macOS, Windows), the code is not run and the reason says how to allow it.

A model's reply is text; it reaches the network only when something runs it.
This is the limit on the part of Guaardvark that runs it.
"""
from __future__ import annotations

import functools
import logging
import shutil
import subprocess
import sys
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

SETTING_KEY = "model_code_network"

# Same files and devices as a normal run; only the network is new and empty.
_BWRAP_ARGS = ["--bind", "/", "/", "--dev-bind", "/dev", "/dev", "--proc", "/proc",
               "--unshare-net", "--die-with-parent", "--"]


def network_allowed() -> bool:
    """Whether the person let model-run code use the network (off by default)."""
    try:
        from backend.utils.settings_utils import get_setting
        return str(get_setting(SETTING_KEY, default="false")).strip().lower() in ("true", "1", "yes", "on")
    except Exception:
        return False


@functools.lru_cache(maxsize=1)
def _bwrap() -> Optional[str]:
    """bwrap's path when it can make a network namespace here, else None.

    Checked once by running `true` inside one: Ubuntu's AppArmor rule on
    unprivileged user namespaces stops `unshare` but allows bwrap, and a
    container without user namespaces stops both.
    """
    if not sys.platform.startswith("linux"):
        return None
    path = shutil.which("bwrap")
    if not path:
        return None
    try:
        probe = subprocess.run([path, *_BWRAP_ARGS, "true"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.info("code sandbox: bwrap probe failed: %s", exc)
        return None
    if probe.returncode != 0:
        logger.info("code sandbox: bwrap cannot make a network namespace here: %s",
                    (probe.stderr or b"").decode(errors="replace").strip()[:200])
        return None
    return path


def wrap(command: List[str]) -> Tuple[Optional[List[str]], Optional[str]]:
    """(command to run, None), or (None, why it cannot run).

    With the network allowed the command is unchanged. Otherwise it runs in a
    network namespace, or not at all where none can be made.
    """
    if network_allowed():
        return list(command), None
    bwrap = _bwrap()
    if bwrap:
        return [bwrap, *_BWRAP_ARGS, *command], None
    if sys.platform.startswith("linux"):
        how = ("Installing bubblewrap (the bwrap command) lets it run without the network; "
               "inside a container, user namespaces must be allowed.")
    else:
        how = "This system has no sandbox Guaardvark can use to cut code off from the network."
    return None, ("Code the assistant writes runs without internet access, and this machine cannot "
                  f"enforce that. {how} To run it with network access, turn on \"Let code the "
                  "assistant runs use the internet\" in Settings → Chat.")
