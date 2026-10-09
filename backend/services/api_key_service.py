"""This install's API key: where the running key comes from, and creating,
replacing and removing it from Settings → Access.

The key lives in the repo's ``.env`` as ``GUAARDVARK_API_KEY`` (written with the
same writer as the profile and the Ollama policy, so every other line and the
file's mode are kept) and in this process's environment, which is what
``auth_guard`` reads on every request. Changing both means a new key works at
once and survives a restart.

The Settings page manages the key only when ``.env`` is where the running key
came from. A key set another way (exported before ``start.sh``, a service
manager, Docker Compose) would come back at the next start, so the page says
where to change it instead.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend import profiles as P
from backend.utils.auth_guard import API_KEY_ENV, configured_api_key

logger = logging.getLogger(__name__)

# secrets.token_urlsafe(32): 43 characters from [A-Za-z0-9_-], safe unquoted in
# .env and in a shell that sources it.
KEY_BYTES = 32
DOCKER_ENV = "GUAARDVARK_DOCKER"

DOCKER_NOTE = (
    "This install runs in Docker. Its key is GUAARDVARK_API_KEY in the .env "
    "file next to docker-compose.yml; change it there and run ./start-docker.sh "
    "again."
)
ENVIRONMENT_NOTE = (
    "This install's key was set outside Settings (in the environment Guaardvark "
    "was started with, not in its .env file). Change it where it was set, then "
    "restart Guaardvark."
)
RESTART_NOTE = (
    "The .env file names an API key the running Guaardvark has not loaded. "
    "Restart Guaardvark to use it."
)
READ_ONLY_NOTE = "Guaardvark cannot write its .env file, so the key cannot be changed from here."

_lock = threading.Lock()


class KeyChangeRefused(Exception):
    """The key cannot be changed from here; ``code`` says why."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class KeyState:
    # The running process requires a key.
    configured: bool
    # none: no key; env_file: the running key is the one in .env;
    # environment: the running key came from somewhere else.
    source: str
    # .env names a key the running process has not loaded.
    restart_needed: bool
    env_writable: bool
    docker: bool
    # Settings may create, replace and remove the key; manage_note says why not.
    manageable: bool
    manage_note: Optional[str]


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def in_docker() -> bool:
    return (os.environ.get(DOCKER_ENV) or "").strip().lower() in ("1", "true", "yes", "on")


def key_state(root: Optional[Path] = None) -> KeyState:
    running = configured_api_key()
    saved = (P.read_env_value(API_KEY_ENV, root) or "").strip()
    if not running:
        source = "none"
    elif saved and _same(saved, running):
        source = "env_file"
    else:
        source = "environment"
    restart_needed = bool(saved) and not (running and _same(saved, running))
    docker = in_docker()
    writable = P.env_file_writable(root)

    note = None
    if docker:
        note = DOCKER_NOTE
    elif source == "environment" and not saved:
        note = ENVIRONMENT_NOTE
    elif restart_needed:
        note = RESTART_NOTE
    elif not writable:
        note = READ_ONLY_NOTE
    return KeyState(
        configured=bool(running),
        source=source,
        restart_needed=restart_needed,
        env_writable=writable,
        docker=docker,
        manageable=note is None,
        manage_note=note,
    )


def _refuse_unless_manageable(state: KeyState) -> None:
    if not state.manageable:
        raise KeyChangeRefused("key_not_manageable", state.manage_note or READ_ONLY_NOTE)


def _apply(key: Optional[str], root: Optional[Path]) -> None:
    # .env first: if it cannot be written, the running key stays as it was.
    P.set_env_value(API_KEY_ENV, key, root)
    if key is None:
        os.environ.pop(API_KEY_ENV, None)
    else:
        os.environ[API_KEY_ENV] = key


def create_key(root: Optional[Path] = None) -> str:
    """A new key for an install that has none. Returns it; it is not stored
    anywhere this module can show again."""
    with _lock:
        state = key_state(root)
        if state.configured:
            raise KeyChangeRefused(
                "key_exists",
                "This install already has an API key. Use Replace to make a new one.",
            )
        _refuse_unless_manageable(state)
        key = secrets.token_urlsafe(KEY_BYTES)
        _apply(key, root)
        return key


def replace_key(root: Optional[Path] = None) -> str:
    """A new key in place of the current one (or the first one). Every device
    holding the old key loses access until it is given the new one."""
    with _lock:
        _refuse_unless_manageable(key_state(root))
        key = secrets.token_urlsafe(KEY_BYTES)
        _apply(key, root)
        return key


def remove_key(root: Optional[Path] = None) -> bool:
    """Remove the key. Returns False when there was none. Protected actions
    then answer only the Guaardvark machine itself again."""
    with _lock:
        state = key_state(root)
        _refuse_unless_manageable(state)
        if not state.configured:
            return False
        _apply(None, root)
        return True
