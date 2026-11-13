"""A Cline retry starts without the previous attempt's status file."""

import service.agent_backends.cline_backend as cline
from service.models import SwarmTask


class _Popen:
    pid = 4242

    def __init__(self, *args, **kwargs):
        pass


def test_spawn_removes_a_stale_status_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cline.subprocess, "Popen", _Popen)
    backend = cline.ClineBackend.__new__(cline.ClineBackend)
    monkeypatch.setattr(backend, "command_prefix", lambda config: ["cline", "--message"], raising=False)
    monkeypatch.setattr(backend, "_build_prompt", lambda task: "do it", raising=False)

    (tmp_path / cline.DONE_MARKER).write_text("crashed\n")
    task = SwarmTask(id="t1", title="t1", description="")
    backend.spawn(str(tmp_path), task, {})

    assert not (tmp_path / cline.DONE_MARKER).exists()


def test_spawn_without_a_status_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cline.subprocess, "Popen", _Popen)
    backend = cline.ClineBackend.__new__(cline.ClineBackend)
    monkeypatch.setattr(backend, "command_prefix", lambda config: ["cline", "--message"], raising=False)
    monkeypatch.setattr(backend, "_build_prompt", lambda task: "do it", raising=False)

    proc = backend.spawn(str(tmp_path), SwarmTask(id="t1", title="t1", description=""), {})
    assert proc.pid == 4242
