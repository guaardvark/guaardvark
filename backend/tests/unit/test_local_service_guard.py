"""The root conftest's guards: a run does not reach the services of a local
install, the plugin runner runs nothing, and the gated background loops start
empty."""

import ast
import errno
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _root_conftest():
    path = REPO_ROOT / "conftest.py"
    for mod in list(sys.modules.values()):
        file = getattr(mod, "__file__", None)
        if file and Path(file).resolve() == path:
            return mod
    raise AssertionError("the root conftest is not loaded")


@pytest.fixture
def guard():
    return _root_conftest()


@pytest.fixture
def listener():
    """A real listening socket on a free port of this machine."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        yield server.getsockname()[1]
    finally:
        server.close()


@pytest.fixture
def as_service(guard, listener, monkeypatch):
    """The listener's port, declared a local service for this test."""
    monkeypatch.setitem(guard.LOCAL_SERVICE_PORTS, listener, "a stand-in service")
    monkeypatch.setattr(guard._SERVICE_GATE, "allowed", False)
    return listener


def test_the_stock_services_and_every_plugin_port_are_listed(guard):
    ports = guard.LOCAL_SERVICE_PORTS
    for port in (6379, 11434, 8188, 5000, 5173):
        assert port in ports
    for manifest in (REPO_ROOT / "plugins").glob("*/plugin.json"):
        port = json.loads(manifest.read_text(encoding="utf-8")).get("port")
        if isinstance(port, int):
            assert port in ports, f"{manifest.parent.name} listens on {port}"


def test_ports_named_in_the_settings_are_listed(guard, tmp_path):
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "plugin.json").write_text('{"port": 8455}', encoding="utf-8")
    ports = guard._local_service_ports(
        settings={
            "REDIS_URL": "redis://localhost:6390/0",
            "GUAARDVARK_COMFYUI_URL": "http://127.0.0.1:8199",
            "OLLAMA_BASE_URL": "http://ollama.example.test:11500",
            "FLASK_PORT": "5055",
            "VITE_PORT": "not a number",
        },
        plugins_dir=tmp_path,
    )
    assert ports[6390] == "Redis"
    assert ports[8199] == "ComfyUI"
    assert ports[5055] == "the backend"
    assert ports[8455] == "the demo plugin"
    assert 11500 not in ports  # another machine's Ollama is not a local service


def test_a_local_service_port_is_refused(guard, as_service):
    refused_before = len(guard._SERVICE_GATE.refused)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(ConnectionRefusedError) as raised:
            client.connect(("127.0.0.1", as_service))
        assert raised.value.errno == errno.ECONNREFUSED
        assert client.connect_ex(("localhost", as_service)) == errno.ECONNREFUSED
    finally:
        client.close()
    assert guard._SERVICE_GATE.refused[refused_before:] == [("a stand-in service", as_service)] * 2


def test_any_other_local_port_connects(listener):
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect(("127.0.0.1", listener))
    finally:
        client.close()


def test_a_service_port_on_another_machine_is_not_the_guards_business(guard):
    assert guard._refused_service(("192.0.2.10", 8188)) is None
    assert guard._refused_service("/run/some.sock") is None


def test_the_live_ollama_switch_lifts_ollama_alone(guard, monkeypatch):
    monkeypatch.setattr(guard._SERVICE_GATE, "allowed", False)
    monkeypatch.setenv("GUAARDVARK_TEST_LIVE_OLLAMA", "1")
    assert guard._refused_service(("127.0.0.1", 11434)) is None
    assert guard._refused_service(("127.0.0.1", 8188)) == "ComfyUI"


@pytest.mark.integration
def test_an_integration_test_is_let_through(guard, listener, monkeypatch):
    monkeypatch.setitem(guard.LOCAL_SERVICE_PORTS, listener, "a stand-in service")
    assert guard._SERVICE_GATE.allowed is True
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect(("127.0.0.1", listener))
    finally:
        client.close()


def test_the_gate_is_back_on_after_an_integration_test(guard):
    if os.environ.get("GUAARDVARK_TEST_LIVE_SERVICES") == "1":
        pytest.skip("the whole run was let through")
    assert guard._SERVICE_GATE.allowed is False


def _sidecar_command():
    return [sys.executable, str(REPO_ROOT / "backend" / "services" / "plugin_runner.py"), "--sidecar"]


def _ask(proc, request):
    proc.stdin.write(json.dumps(request) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


def test_the_plugin_runner_is_a_stand_in_that_runs_nothing(guard, tmp_path):
    marker = tmp_path / "ran"
    held_before = len(guard._LOOP_GATE.held)
    proc = subprocess.Popen(_sidecar_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1)
    try:
        assert _ask(proc, {"id": 1, "cmd": "ping"}) == {"id": 1, "ok": True}
        answer = _ask(proc, {"id": 2, "cmd": "run", "argv": ["touch", str(marker)], "timeout": 5})
        assert answer["ok"] is False and answer["rc"] == -1
        assert "under pytest" in answer["error"]
        assert _ask(proc, {"id": 3, "cmd": "shutdown"}) == {"id": 3, "ok": True}
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            pipe.close()
    assert not marker.exists()
    assert guard._LOOP_GATE.held[held_before:] == ["plugin runner"]


def _display_command(action):
    return ["bash", str(REPO_ROOT / "scripts" / "start_agent_display.sh"), action]


@pytest.mark.parametrize("action", ["start", "stop", "restart"])
def test_the_agent_display_is_never_started_or_stopped(guard, action):
    assert guard._is_display_command(_display_command(action))

    done = subprocess.run(_display_command(action), capture_output=True, text=True, timeout=30)

    assert done.returncode == 1
    assert "not started or stopped under pytest" in done.stderr


def test_the_display_script_is_where_the_guard_looks(guard):
    assert (REPO_ROOT / "scripts" / "start_agent_display.sh").is_file()
    assert not guard._is_display_command(_display_command("status"))
    assert not guard._is_display_command(["bash", "-c", "echo start_agent_display.sh start"])
    assert not guard._is_display_command("bash scripts/start_agent_display.sh start")


def test_code_that_asks_for_the_display_gets_a_refusal(guard):
    from backend.utils import agent_display_utils

    result = agent_display_utils.run_display_script("start")

    assert result["success"] is False and result["returncode"] == 1


def test_other_commands_start_as_given(guard):
    assert not guard._is_sidecar_command([sys.executable, "-c", "pass"])
    assert not guard._is_sidecar_command("plugin_runner.py --sidecar")
    done = subprocess.run([sys.executable, "-c", "print('ran')"], capture_output=True, text=True, timeout=30)
    assert done.stdout.strip() == "ran"


def _qualnames(source: Path):
    names = set()

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(prefix + child.name)
                walk(child, prefix + child.name + ".<locals>.")
            elif isinstance(child, ast.ClassDef):
                names.add(prefix + child.name)
                walk(child, prefix + child.name + ".")
            else:
                walk(child, prefix)

    walk(ast.parse(source.read_text(encoding="utf-8")), "")
    return names


def test_every_gated_loop_names_a_function_in_the_backend(guard):
    for module, qualname in guard.GATED_LOOPS:
        source = REPO_ROOT / "backend" / (module.replace(".", "/") + ".py")
        assert source.is_file(), source
        assert qualname in _qualnames(source), f"{qualname} is not defined in {source}"


def test_the_sidecar_script_is_where_the_guard_looks(guard):
    assert (REPO_ROOT.joinpath(*guard._SIDECAR_SCRIPT)).is_file()
    assert guard._is_sidecar_command(_sidecar_command())


def _thread_running_as(module, qualname, ran):
    def body():
        ran.append(True)

    body.__module__, body.__qualname__ = module, qualname
    return threading.Thread(target=body, daemon=True)


def test_a_gated_loop_starts_with_an_empty_body(guard):
    ran = []
    thread = _thread_running_as("backend.services.inbound_guard_watch", "start_background.<locals>.loop", ran)
    held_before = len(guard._LOOP_GATE.held)
    thread.start()
    thread.join(timeout=5)
    assert ran == []
    assert guard._LOOP_GATE.held[held_before:] == ["inbound guard watch"]


def test_other_threads_run_as_usual():
    ran = []
    thread = _thread_running_as(__name__, "worker", ran)
    thread.start()
    thread.join(timeout=5)
    assert ran == [True]


def test_the_fixture_lets_a_gated_loop_run(background_loops):
    ran = []
    thread = _thread_running_as("backend.services.inbound_guard_watch", "start_background.<locals>.loop", ran)
    thread.start()
    thread.join(timeout=5)
    assert ran == [True]
