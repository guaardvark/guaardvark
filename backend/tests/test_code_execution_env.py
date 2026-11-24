"""Code run from the editor gets a short allow-list of environment variables
and a fresh temporary working folder. Not a boundary (the code runs as the
same user); it keeps the backend's secrets out of casual output."""

import os
import subprocess
from importlib import util
from pathlib import Path

import pytest


_MODULE_PATH = Path(__file__).resolve().parents[1] / "api" / "code_execution_api.py"


@pytest.fixture
def code_execution_api():
    spec = util.spec_from_file_location("code_execution_api_under_test", _MODULE_PATH)
    module = util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


DROPPED = {
    "GUAARDVARK_API_KEY": "the-key",
    "SECRET_KEY": "flask",
    "DATABASE_URL": "postgresql://user:password@localhost/guaardvark",
    "SQLALCHEMY_DATABASE_URI": "postgresql://user:password@localhost/guaardvark",
    "REDIS_URL": "redis://:password@localhost:6379/0",
    "CELERY_BROKER_URL": "redis://:password@localhost:6379/1",
    "CELERY_RESULT_BACKEND": "redis://:password@localhost:6379/2",
    "HF_TOKEN": "hf_x",
    "DISCORD_BOT_TOKEN": "bot",
    "HTTPS_PROXY": "http://user:password@proxy:3128",
    "PIP_INDEX_URL": "https://user:password@pypi.example/simple",
    "DB_PASS": "pw",
    "GUAARDVARK_NETWORK_ACCESS": "1",
    "HF_HOME": "/srv/hf",
}
KEPT = {
    "PATH": "/usr/bin",
    "HOME": "/home/someone",
    "USER": "someone",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TERM": "xterm",
    "TMPDIR": "/var/tmp",
    "VIRTUAL_ENV": "/srv/app/venv",
    "CUDA_VISIBLE_DEVICES": "0",
    "NVIDIA_VISIBLE_DEVICES": "all",
    "LD_LIBRARY_PATH": "/usr/local/cuda/lib64",
    "NODE_PATH": "/usr/lib/node_modules",
}


def test_execution_env_is_an_allow_list(code_execution_api, monkeypatch):
    for name, value in {**DROPPED, **KEPT}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("PYTHONPATH", raising=False)

    env = code_execution_api.execution_env()

    assert set(env) == set(KEPT)
    for name, value in KEPT.items():
        assert env[name] == value, name


def test_pythonpath_passes_only_when_it_is_this_installs(code_execution_api, monkeypatch):
    install_root = code_execution_api.INSTALL_ROOT
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)

    monkeypatch.setenv("PYTHONPATH", str(install_root))
    assert code_execution_api.execution_env()["PYTHONPATH"] == str(install_root)

    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(install_root), "/srv/elsewhere"]))
    assert "PYTHONPATH" not in code_execution_api.execution_env()

    monkeypatch.setenv("VIRTUAL_ENV", "/srv/elsewhere/venv")
    monkeypatch.setenv("PYTHONPATH", "/srv/elsewhere/venv/lib")
    assert code_execution_api.execution_env()["PYTHONPATH"] == "/srv/elsewhere/venv/lib"


@pytest.mark.parametrize("runner", ["execute_command", "execute_command_with_stdin"])
def test_editor_code_runs_with_the_allow_listed_env_in_a_fresh_folder(code_execution_api, monkeypatch, runner):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "the-key")
    monkeypatch.setenv("HOME", "/home/someone")
    seen = {}

    real_popen = subprocess.Popen

    def watched_popen(command, **kwargs):
        seen["env"] = kwargs.get("env")
        seen["cwd"] = kwargs.get("cwd")
        seen["cwd_existed"] = bool(kwargs.get("cwd")) and Path(kwargs["cwd"]).is_dir()
        return real_popen(command, **kwargs)

    monkeypatch.setattr(code_execution_api.subprocess, "Popen", watched_popen)

    result = getattr(code_execution_api, runner)(["python3", "-c", "pass"])

    assert result["success"] is True
    assert "GUAARDVARK_API_KEY" not in seen["env"]
    assert seen["env"]["HOME"] == "/home/someone"
    # A fresh folder, present for the run and gone afterwards.
    assert seen["cwd_existed"] is True
    assert Path(seen["cwd"]).name.startswith("guaardvark-run-")
    assert not Path(seen["cwd"]).exists()


def test_a_given_working_directory_is_used_as_is(code_execution_api, monkeypatch, tmp_path):
    seen = {}

    real_popen = subprocess.Popen

    def watched_popen(command, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        return real_popen(command, **kwargs)

    monkeypatch.setattr(code_execution_api.subprocess, "Popen", watched_popen)

    code_execution_api.execute_command(["true"], cwd=str(tmp_path))

    assert seen["cwd"] == str(tmp_path)
    assert tmp_path.exists()


def test_a_timeout_stops_child_processes_and_keeps_the_output(code_execution_api, tmp_path):
    marker = tmp_path / "child-alive"
    child = f"import time, pathlib; time.sleep(3); pathlib.Path({str(marker)!r}).write_text('x')"
    code = (f"import subprocess, sys, time; print('started', flush=True); "
            f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)")

    result = code_execution_api.execute_command(["python3", "-c", code], timeout=1)

    assert result["success"] is False and result.get("timedOut") is True
    assert "started" in result["stdout"]
    import time
    time.sleep(3.5)
    assert not marker.exists()  # the child was stopped with its parent


def test_a_background_child_does_not_turn_a_finished_run_into_a_timeout(code_execution_api):
    code = ("import subprocess, sys; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)']); print('done')")

    result = code_execution_api.execute_command(["python3", "-c", code], timeout=10)

    assert result["success"] is True
    assert result["stdout"].strip() == "done"


def test_output_is_cut_at_the_limit(code_execution_api):
    code = "import sys\nfor _ in range(4): sys.stdout.write('x' * 1048576)"

    result = code_execution_api.execute_command(["python3", "-c", code], timeout=20)

    assert result["success"] is True
    assert len(result["stdout"]) < code_execution_api.MAX_OUTPUT_SIZE + 100
    assert "output cut" in result["stdout"]
