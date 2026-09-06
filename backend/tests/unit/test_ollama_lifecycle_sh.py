"""stop.sh's Ollama policy: stop what start.sh started, nothing else, unless asked."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
LIB = REPO / "scripts" / "lib" / "ollama_lifecycle.sh"

# Stub every external command the helper may call; each one appends its argv to a log.
_STUBS = {
    "kill": '#!/bin/sh\necho "kill $*" >> "$STUB_LOG"\n[ "$1" = "-0" ] && exit 1\nexit 0\n',
    "pgrep": '#!/bin/sh\necho "pgrep $*" >> "$STUB_LOG"\necho 4242\n',
    "ps": '#!/bin/sh\necho "ps $*" >> "$STUB_LOG"\necho "$STUB_USER"\n',
    "whoami": '#!/bin/sh\necho "$STUB_USER"\n',
    "systemctl": '#!/bin/sh\necho "systemctl $*" >> "$STUB_LOG"\n',
    "sudo": '#!/bin/sh\necho "sudo $*" >> "$STUB_LOG"\nexit 0\n',
    "lsof": '#!/bin/sh\necho "lsof $*" >> "$STUB_LOG"\n',
    "curl": '#!/bin/sh\nexit 1\n',
    "sleep": '#!/bin/sh\nexit 0\n',
}


@pytest.fixture
def sandbox(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in _STUBS.items():
        f = bindir / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.log"
    log.write_text("")
    env = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "STUB_LOG": str(log),
        "STUB_USER": "tester",
        "HOME": str(tmp_path),
    }
    return tmp_path, log, env


def run(sandbox, mode, pid_file="", extra_env=None, flags=("0", "0")):
    tmp_path, log, env = sandbox
    env = {**env, **(extra_env or {})}
    script = (
        'enable -n kill\n'  # use the PATH stub, not the bash builtin
        f'. "{LIB}"\n'
        f'mode=$(ollama_stop_mode {flags[0]} {flags[1]})\n'
        f'[ -n "{mode}" ] && mode="{mode}"\n'
        f'stop_ollama "$mode" "{pid_file}"\n'
        'echo "MODE=$mode KILLED=$ollama_killed"\n'
    )
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, check=True)
    return proc.stdout, log.read_text()


def test_mode_resolution(sandbox):
    out, _ = run(sandbox, "", flags=("0", "0"))
    assert "MODE=owned" in out
    out, _ = run(sandbox, "", flags=("1", "0"))
    assert "MODE=all" in out
    out, _ = run(sandbox, "", flags=("1", "1"))
    assert "MODE=keep" in out
    out, _ = run(sandbox, "", extra_env={"GUAARDVARK_OLLAMA_KEEP_RUNNING": "1"}, flags=("1", "0"))
    assert "MODE=keep" in out


def test_owned_mode_stops_only_the_pid_file_instance(sandbox):
    tmp_path, _, _ = sandbox
    pid_file = tmp_path / "ollama.pid"
    pid_file.write_text("777\n")
    # A kill stub whose liveness check flips once TERM has been delivered.
    (tmp_path / "bin" / "kill").write_text('#!/bin/sh\necho "kill $*" >> "$STUB_LOG"\nif [ "$1" = "-0" ]; then [ -f "$STUB_LOG.alive" ] && exit 0 || exit 1; fi\n[ "$1" = "-TERM" ] && rm -f "$STUB_LOG.alive"\nexit 0\n')
    (tmp_path / "calls.log.alive").write_text("")
    out, calls = run(sandbox, "owned", str(pid_file))
    assert "KILLED=1" in out
    assert "kill -TERM 777" in calls
    assert "pgrep" not in calls and "systemctl" not in calls and "lsof" not in calls
    assert not pid_file.exists()


def test_owned_mode_without_a_pid_file_touches_nothing(sandbox):
    out, calls = run(sandbox, "owned", "/nonexistent/ollama.pid")
    assert "KILLED=0" in out
    assert calls.strip() == ""
    assert "leaving it as it is" in out


def test_keep_mode_touches_nothing_even_with_a_pid_file(sandbox):
    tmp_path, _, _ = sandbox
    pid_file = tmp_path / "ollama.pid"
    pid_file.write_text("777\n")
    out, calls = run(sandbox, "keep", str(pid_file))
    assert "KILLED=0" in out and calls.strip() == ""
    assert pid_file.exists()


def test_all_mode_reaches_user_processes_and_systemd(sandbox):
    out, calls = run(sandbox, "all", "/nonexistent/ollama.pid")
    assert "pgrep -f (^|/)ollama serve$" in calls
    assert "kill -TERM 4242" in calls
    assert "sudo -n systemctl stop ollama" in calls
    assert "lsof -i TCP:11434" in calls


def test_all_mode_never_kills_another_users_serve(sandbox):
    tmp_path, _, _ = sandbox
    (tmp_path / "bin" / "ps").write_text('#!/bin/sh\necho "ps $*" >> "$STUB_LOG"\necho ollama\n')
    out, calls = run(sandbox, "all", "/nonexistent/ollama.pid")
    assert "kill -TERM 4242" not in calls


def test_dry_run_reports_without_acting(sandbox):
    tmp_path, _, _ = sandbox
    pid_file = tmp_path / "ollama.pid"
    pid_file.write_text("777\n")
    (tmp_path / "bin" / "kill").write_text('#!/bin/sh\necho "kill $*" >> "$STUB_LOG"\nexit 0\n')
    out, calls = run(sandbox, "all", str(pid_file), extra_env={"GUAARDVARK_STOP_DRY_RUN": "1"})
    assert "[dry-run] would stop PID 777" in out
    assert "kill -TERM" not in calls and "sudo" not in calls
    assert pid_file.exists()
