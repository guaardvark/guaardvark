"""Root conftest: the bootstrap extension tests need, and the guards every
test run gets.

backend/tests/conftest.py inserts the repo root on sys.path and sets the
skip flags for its own tree; tests under extensions/<id>/tests/ are outside
that tree and get the same here.

A run does not reach the services of a local install (see
LOCAL_SERVICE_PORTS), the backend's plugin runner and the background loops
named in GATED_LOOPS do not run under pytest (see _LOOP_GATE), and the agent
display is never started or stopped (see _is_display_command).
"""
import errno
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

_REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_ROOT))
# The CLI package under test is this checkout's, not whichever copy is
# installed in the environment (a second checkout or a worktree shares one).
sys.path.insert(0, str(_REPO_ROOT / "cli"))
os.environ.setdefault("PYTEST_SKIP_MIGRATION_CHECK", "1")
os.environ.setdefault("PYTEST_SKIP_LLAMA_CHECK", "1")
os.environ.setdefault("GUAARDVARK_MODE", "test")

import socket

import pytest

# ---- Background loops ----
#
# Importing backend.app can start these threads. Under pytest they start with
# an empty body; a test that needs one asks for the `background_loops` fixture.
# backend/tests/unit/test_local_service_guard.py keeps the names in step.

GATED_LOOPS = {
    ("services.inbound_guard_watch", "start_background.<locals>.loop"): "inbound guard watch",
}


class _LoopGate:
    def __init__(self):
        self.allowed = False
        self.held = []


_LOOP_GATE = _LoopGate()
_real_thread_start = threading.Thread.start


def _loop_name(target):
    func = getattr(target, "__func__", target)
    module = getattr(func, "__module__", None) or ""
    if module.startswith("backend."):
        module = module[len("backend."):]
    return GATED_LOOPS.get((module, getattr(func, "__qualname__", None)))


def _gated_thread_start(self):
    name = _loop_name(getattr(self, "_target", None))
    if name and not _LOOP_GATE.allowed:
        _LOOP_GATE.held.append(name)
        self._target, self._args, self._kwargs = None, (), {}
    return _real_thread_start(self)


threading.Thread.start = _gated_thread_start


@pytest.fixture
def background_loops():
    """Let the gated background loops and the plugin runner start during this test.

    The test supplies the app they run against (an in-memory database, stubbed
    services); the gate is back on when the test ends.
    """
    _LOOP_GATE.allowed = True
    try:
        yield _LOOP_GATE
    finally:
        _LOOP_GATE.allowed = False


# ---- The plugin runner ----
#
# Importing backend.app starts a sidecar process that runs plugin start and
# stop scripts on request. Under pytest it is replaced by a stand-in that
# answers its ping and refuses to run anything, so no test can start or stop a
# real service through it. The `background_loops` fixture lifts this as well.

_SIDECAR_SCRIPT = ("backend", "services", "plugin_runner.py")
_SIDECAR_STAND_IN = (
    "import json, sys\n"
    "for line in sys.stdin:\n"
    "    try:\n"
    "        req = json.loads(line)\n"
    "    except ValueError:\n"
    "        continue\n"
    "    cmd = req.get('cmd')\n"
    "    resp = {'id': req.get('id', -1), 'ok': cmd in ('ping', 'shutdown')}\n"
    "    if not resp['ok']:\n"
    "        resp.update(error='the plugin runner does not run commands under pytest',\n"
    "                    stdout='', stderr='', rc=-1)\n"
    "    sys.stdout.write(json.dumps(resp) + '\\n')\n"
    "    sys.stdout.flush()\n"
    "    if cmd == 'shutdown':\n"
    "        break\n"
)


def _is_sidecar_command(args) -> bool:
    return (isinstance(args, (list, tuple)) and len(args) == 3 and args[2] == "--sidecar"
            and Path(str(args[1])).parts[-3:] == _SIDECAR_SCRIPT)


_real_popen_init = subprocess.Popen.__init__


# ---- The agent display ----
#
# The agent's virtual desktop (Xvfb plus a desktop session) is started on
# demand by scripts/start_agent_display.sh, and code under test reaches that
# through several routes. A test run must not start a desktop on the machine,
# or stop the one a person's agent is using, so under pytest the script is
# replaced by a stand-in that fails and says why. `status` only reads and runs
# as given.

_DISPLAY_SCRIPT = ("scripts", "start_agent_display.sh")
_DISPLAY_STAND_IN = (
    "import sys\n"
    "sys.stderr.write('the agent display is not started or stopped under pytest\\n')\n"
    "sys.exit(1)\n"
)


def _is_display_command(args) -> bool:
    if not isinstance(args, (list, tuple)):
        return False
    words = [str(a) for a in args]
    for index, word in enumerate(words):
        if Path(word).parts[-2:] == _DISPLAY_SCRIPT:
            return words[index + 1:index + 2] != ["status"]
    return False


def _gated_popen_init(self, args, *rest, **kwargs):
    if _is_sidecar_command(args) and not _LOOP_GATE.allowed:
        _LOOP_GATE.held.append("plugin runner")
        args = [args[0], "-c", _SIDECAR_STAND_IN]
    elif _is_display_command(args):
        _LOOP_GATE.held.append("agent display")
        args = [sys.executable, "-c", _DISPLAY_STAND_IN]
    return _real_popen_init(self, args, *rest, **kwargs)


subprocess.Popen.__init__ = _gated_popen_init


# ---- Local services ----
#
# A developer's machine usually has the real stack running: Redis, Ollama,
# ComfyUI, the backend, the web UI and the plugin services. A unit test that
# reaches one of them queues behind the person's own work (a chat model load,
# a render), reads or changes live state (a cancel test can interrupt the
# render in progress), and passes or fails on what happens to be running; CI
# has none of them. So a connection to one of their ports on this machine is
# refused, exactly as it is in CI, from the moment this file is imported (the
# app probes several of them while it is being imported).
# Tests marked `integration` are let through. GUAARDVARK_TEST_LIVE_SERVICES=1
# lifts it for a whole run, GUAARDVARK_TEST_LIVE_OLLAMA=1 for Ollama alone.
#
# This covers Python sockets in the test process. A child process, or a client
# written in C, is not seen here.

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0", "::", "::ffff:127.0.0.1"}
_SERVICE_URL_SETTINGS = {
    "REDIS_URL": "Redis", "CELERY_BROKER_URL": "Redis", "CELERY_RESULT_BACKEND": "Redis",
    "OLLAMA_BASE_URL": "Ollama", "OLLAMA_URL": "Ollama", "GUAARDVARK_COMFYUI_URL": "ComfyUI",
}
_SERVICE_PORT_SETTINGS = {"FLASK_PORT": "the backend", "VITE_PORT": "the web UI",
                          "GUAARDVARK_TEST_OLLAMA_PORT": "Ollama"}


def _env_files():
    """The .env files the backend reads: this checkout's, and GUAARDVARK_ROOT's."""
    files = [_REPO_ROOT / ".env"]
    root = os.environ.get("GUAARDVARK_ROOT")
    if root and Path(root).resolve() != _REPO_ROOT:
        files.append(Path(root) / ".env")
    return files


def _dotenv_settings():
    values = {}
    for env_file in _env_files():
        if not env_file.is_file():
            continue
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            key, sep, value = line.partition("=")
            if sep and key.strip() not in values:
                values[key.strip()] = value.strip().strip("'\"")
    return values


def _local_service_ports(settings=None, plugins_dir=None):
    """Port -> service name for every service of a local install: the stock
    ports, the ones this checkout's environment and .env name, and each
    plugin's declared port."""
    ports = {6379: "Redis", 11434: "Ollama", 8188: "ComfyUI", 5000: "the backend", 5173: "the web UI"}
    if settings is None:
        settings = {**_dotenv_settings(), **os.environ}
    for key, name in _SERVICE_URL_SETTINGS.items():
        try:
            parts = urlsplit(settings.get(key) or "")
            if parts.port and (parts.hostname or "").lower() in _LOCAL_HOSTS:
                ports[parts.port] = name
        except ValueError:
            continue
    for key, name in _SERVICE_PORT_SETTINGS.items():
        if str(settings.get(key) or "").isdigit():
            ports[int(settings[key])] = name
    for manifest in sorted(Path(plugins_dir or _REPO_ROOT / "plugins").glob("*/plugin.json")):
        try:
            port = json.loads(manifest.read_text(encoding="utf-8")).get("port")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(port, int) and not isinstance(port, bool):
            ports.setdefault(port, f"the {manifest.parent.name} plugin")
    return ports


LOCAL_SERVICE_PORTS = _local_service_ports()

# backend.config resolves ComfyUI from this variable before the plugin
# manifest, so code under test, and any child process it starts, gets a port
# nothing listens on, even in a shell that exported the live install's .env.
# Set after the ports above are read, so a live URL's port stays refused.
DEAD_COMFYUI_URL = "http://127.0.0.1:9"
if os.environ.get("GUAARDVARK_TEST_LIVE_SERVICES") != "1":
    os.environ["GUAARDVARK_COMFYUI_URL"] = DEAD_COMFYUI_URL
    LOCAL_SERVICE_PORTS.setdefault(9, "ComfyUI")


class _ServiceGate:
    def __init__(self):
        self.allowed = os.environ.get("GUAARDVARK_TEST_LIVE_SERVICES") == "1"
        self.refused = []


_SERVICE_GATE = _ServiceGate()


def _refused_service(address):
    """The name of the local service ``address`` belongs to when the run may
    not reach it, else None."""
    if _SERVICE_GATE.allowed or not isinstance(address, tuple) or len(address) < 2:
        return None
    name = LOCAL_SERVICE_PORTS.get(address[1])
    if name is None or str(address[0]).lower() not in _LOCAL_HOSTS:
        return None
    if name == "Ollama" and os.environ.get("GUAARDVARK_TEST_LIVE_OLLAMA") == "1":
        return None
    _SERVICE_GATE.refused.append((name, address[1]))
    return name


_real_connect, _real_connect_ex = socket.socket.connect, socket.socket.connect_ex


def _guarded_connect(self, address):
    name = _refused_service(address)
    if name:
        raise ConnectionRefusedError(
            errno.ECONNREFUSED,
            f"unit tests do not reach {name} on :{address[1]} of this machine; "
            "mark the test `integration` or mock the call")
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    if _refused_service(address):
        return errno.ECONNREFUSED
    return _real_connect_ex(self, address)


socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex


@pytest.fixture(autouse=True)
def _local_services_for_integration_tests(request):
    """Tests marked `integration` reach the local services; no other test does."""
    if not request.node.get_closest_marker("integration") or _SERVICE_GATE.allowed:
        yield
        return
    _SERVICE_GATE.allowed = True
    try:
        yield
    finally:
        _SERVICE_GATE.allowed = False
