"""Network access, for Settings → Access: whether every device on this
machine's local network may run protected actions without the API key.

The switch is ``GUAARDVARK_NETWORK_ACCESS`` in the repo's ``.env`` (written with
the same writer as the API key and the profile) and in this process's
environment, which ``auth_guard`` reads on every request, so a change applies at
once and survives a restart. A network admin can set it in ``.env`` before the
first start. Off or unset is the stock behaviour.

Settings changes it only when ``.env`` is where the running value comes from.
In Docker every request reaches the backend from Docker's own network, so the
local-network test cannot tell devices apart and the switch stays off there.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend import profiles as P
from backend.services.api_key_service import in_docker
from backend.utils.auth_guard import NETWORK_ACCESS_ENV, network_access_open

DOCKER_NOTE = (
    "This install runs in Docker, where every device reaches Guaardvark through "
    "Docker's network, so network access cannot tell local devices from others. "
    "Use the API key instead."
)
ENVIRONMENT_NOTE = (
    "Network access was set outside Settings (in the environment Guaardvark was "
    "started with, not in its .env file). Change it where it was set, then "
    "restart Guaardvark."
)
READ_ONLY_NOTE = "Guaardvark cannot write its .env file, so network access cannot be changed from here."

_lock = threading.Lock()


class NetworkAccessRefused(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class NetworkAccessState:
    enabled: bool
    manageable: bool
    manage_note: Optional[str]


def _flag(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def network_access_state(root: Optional[Path] = None) -> NetworkAccessState:
    running = network_access_open()
    saved = P.read_env_value(NETWORK_ACCESS_ENV, root)
    note = None
    if in_docker():
        note = DOCKER_NOTE
    elif os.environ.get(NETWORK_ACCESS_ENV) is not None and (saved is None or _flag(saved) != running):
        note = ENVIRONMENT_NOTE
    elif not P.env_file_writable(root):
        note = READ_ONLY_NOTE
    return NetworkAccessState(enabled=running, manageable=note is None, manage_note=note)


def set_network_access(enabled: bool, root: Optional[Path] = None) -> bool:
    """Turn network access on or off in .env and in this process. Returns the
    new state."""
    with _lock:
        state = network_access_state(root)
        if not state.manageable:
            raise NetworkAccessRefused(state.manage_note or READ_ONLY_NOTE)
        # .env first: if it cannot be written, the running value stays as it was.
        P.set_env_value(NETWORK_ACCESS_ENV, "1" if enabled else None, root)
        if enabled:
            os.environ[NETWORK_ACCESS_ENV] = "1"
        else:
            os.environ.pop(NETWORK_ACCESS_ENV, None)
        return enabled
