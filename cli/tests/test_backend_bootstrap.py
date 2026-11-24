"""Tests for backend bootstrap and port discovery."""
import json
import shutil
import socket
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest


class TestFlaskPortResolution:
    def test_resolve_flask_port_from_env_var(self, monkeypatch):
        from llx.backend_bootstrap import resolve_flask_port

        monkeypatch.setenv("FLASK_PORT", "5055")
        assert resolve_flask_port() == 5055

    def test_resolve_flask_port_from_repo_env(self, monkeypatch, tmp_path):
        from llx.backend_bootstrap import resolve_flask_port

        monkeypatch.delenv("FLASK_PORT", raising=False)
        monkeypatch.delenv("FLASK_RUN_PORT", raising=False)
        (tmp_path / ".env").write_text("FLASK_PORT=5001\n")
        assert resolve_flask_port(tmp_path) == 5001

    def test_resolve_flask_port_default(self, monkeypatch, tmp_path):
        from llx.backend_bootstrap import resolve_flask_port

        monkeypatch.delenv("FLASK_PORT", raising=False)
        monkeypatch.delenv("FLASK_RUN_PORT", raising=False)
        # runtime.json is a real per-user file and resolution reads it before
        # falling back, so without this the default is only reached on a machine
        # with no instance running.
        monkeypatch.setattr(
            "llx.backend_bootstrap.RUNTIME_FILE", tmp_path / "absent-runtime.json"
        )
        assert resolve_flask_port(tmp_path) == 5000


    def test_runtime_file_of_another_install_is_skipped(self, monkeypatch, tmp_path):
        from llx.backend_bootstrap import resolve_flask_port

        monkeypatch.delenv("FLASK_PORT", raising=False)
        monkeypatch.delenv("FLASK_RUN_PORT", raising=False)
        runtime = tmp_path / "runtime.json"
        monkeypatch.setattr("llx.backend_bootstrap.RUNTIME_FILE", runtime)
        repo = tmp_path / "repo"
        repo.mkdir()
        runtime.write_text(json.dumps({"root": str(tmp_path / "other"), "backend_port": 5100}))
        assert resolve_flask_port(repo) == 5000
        runtime.write_text(json.dumps({"root": str(repo), "backend_port": 5100}))
        assert resolve_flask_port(repo) == 5100
        runtime.write_text(json.dumps({"backend_port": 5100}))
        assert resolve_flask_port(repo) == 5100


@pytest.mark.real_port_holders
class TestOtherInstallOnPort:
    """Real listeners on a free loopback port, run from scratch install folders.
    Only lookups run; nothing is signalled."""

    @staticmethod
    def _install(path):
        (path / "backend").mkdir(parents=True)
        (path / "start.sh").write_text("")
        (path / "backend" / "app.py").write_text("")
        return path

    @staticmethod
    def _listener(cwd):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        code = ("import socket,time;s=socket.socket();"
                "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
                f"s.bind(('127.0.0.1',{port}));s.listen();print('up',flush=True);time.sleep(60)")
        proc = subprocess.Popen([sys.executable, "-c", code], cwd=str(cwd), stdout=subprocess.PIPE, text=True)
        assert proc.stdout.readline().strip() == "up"
        return proc, port

    @pytest.fixture
    def roots(self, tmp_path):
        if not (shutil.which("lsof") or shutil.which("ss")):
            pytest.skip("neither lsof nor ss to find a port's listener")
        mine = self._install(tmp_path / "mine")
        nested = self._install(mine / "worktrees" / "wt")
        other = self._install(tmp_path / "other")
        return mine, nested, other

    @pytest.mark.parametrize("where,expect", [("mine", None), ("other", "other"), ("nested", "nested")])
    def test_listener_owner(self, roots, where, expect):
        from llx.backend_bootstrap import other_install_on_port

        mine, nested, other = roots
        cwd = {"mine": mine / "backend", "other": other / "backend", "nested": nested}[where]
        proc, port = self._listener(cwd)
        try:
            found = other_install_on_port(mine, port)
        finally:
            proc.kill()
            proc.wait()
        if expect is None:
            assert found is None
        else:
            assert found == (proc.pid, {"other": other, "nested": nested}[expect].resolve())

    def test_no_listener_means_no_verdict(self, roots):
        from llx.backend_bootstrap import other_install_on_port

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        assert other_install_on_port(roots[0], port) is None


class TestDiscoverRuntimeServer:
    def test_stale_pid_but_healthy_port(self, monkeypatch, tmp_path):
        runtime_dir = tmp_path / ".guaardvark"
        runtime_dir.mkdir()
        (runtime_dir / "runtime.json").write_text(
            json.dumps({"backend_port": 5000, "backend_pid": 999999999})
        )
        monkeypatch.setattr("llx.config.RUNTIME_FILE", runtime_dir / "runtime.json")

        with patch("llx.config._health_check_port", return_value=True):
            from llx.config import _discover_runtime_server

            assert _discover_runtime_server() == "http://localhost:5000"

    def test_stale_pid_and_unhealthy_port_returns_none(self, monkeypatch, tmp_path):
        runtime_dir = tmp_path / ".guaardvark"
        runtime_dir.mkdir()
        (runtime_dir / "runtime.json").write_text(
            json.dumps({"backend_port": 5000, "backend_pid": 999999999})
        )
        monkeypatch.setattr("llx.config.RUNTIME_FILE", runtime_dir / "runtime.json")

        with patch("llx.config._health_check_port", return_value=False):
            from llx.config import _discover_runtime_server

            assert _discover_runtime_server() is None


class TestEnsureBackendRunning:
    def test_skips_when_already_online(self, monkeypatch, tmp_path):
        root = tmp_path / "repo"
        root.mkdir()
        (root / "start.sh").write_text("#!/bin/bash\nexit 0\n")
        monkeypatch.setattr("llx.backend_bootstrap.resolve_guaardvark_root", lambda: root)
        monkeypatch.setattr(
            "llx.backend_bootstrap.RUNTIME_FILE", tmp_path / "absent-runtime.json"
        )

        with patch("llx.backend_bootstrap.is_backend_healthy", return_value=True), \
                patch("llx.backend_bootstrap.other_install_on_port", return_value=None):
            from llx.backend_bootstrap import ensure_backend_running

            url, started = ensure_backend_running(quiet=True)
            assert started is False
            assert url == "http://127.0.0.1:5000"

    def test_another_installs_backend_is_not_taken_for_this_ones(self, monkeypatch, tmp_path):
        root = tmp_path / "repo"
        root.mkdir()
        (root / "start.sh").write_text("#!/bin/bash\nexit 0\n")
        other = tmp_path / "other"
        monkeypatch.setattr("llx.backend_bootstrap.resolve_guaardvark_root", lambda: root)
        monkeypatch.setattr("llx.backend_bootstrap.RUNTIME_FILE", tmp_path / "absent-runtime.json")
        monkeypatch.delenv("FLASK_PORT", raising=False)
        monkeypatch.delenv("FLASK_RUN_PORT", raising=False)
        run = MagicMock()
        monkeypatch.setattr("llx.backend_bootstrap.subprocess.run", run)

        with patch("llx.backend_bootstrap.is_backend_healthy", return_value=True), \
                patch("llx.backend_bootstrap.other_install_on_port", return_value=(4242, other)):
            from llx.backend_bootstrap import ensure_backend_running

            with pytest.raises(RuntimeError) as exc:
                ensure_backend_running(quiet=True)
        message = str(exc.value)
        assert str(other) in message and "4242" in message and str(root) in message
        run.assert_not_called()

    def test_runs_start_script_when_offline(self, monkeypatch, tmp_path):
        root = tmp_path / "repo"
        root.mkdir()
        (root / "start.sh").write_text("#!/bin/bash\nexit 0\n")
        (root / "pids").mkdir()
        (root / "pids" / "backend.pid").write_text("12345")
        monkeypatch.setattr("llx.backend_bootstrap.resolve_guaardvark_root", lambda: root)
        monkeypatch.setattr("llx.backend_bootstrap.RUNTIME_FILE", tmp_path / "runtime.json")

        mock_run = MagicMock(return_value=MagicMock(returncode=0))
        with patch("llx.backend_bootstrap.is_backend_healthy", side_effect=[False, True]):
            with patch("llx.backend_bootstrap.subprocess.run", mock_run):
                from llx.backend_bootstrap import ensure_backend_running

                url, started = ensure_backend_running(quiet=True)
                assert started is True
                assert url == "http://127.0.0.1:5000"
                mock_run.assert_called_once()
                args = mock_run.call_args[0][0]
                assert "--backend-only" in args
                assert "--fast" in args


class TestEnsureBackendRunningInDocker:
    def test_backend_container_points_at_compose_not_start_sh(self, monkeypatch, tmp_path):
        # /app in the backend image has no start.sh; the API is the container's own process.
        root = tmp_path / "app"
        root.mkdir()
        monkeypatch.setattr("llx.backend_bootstrap.resolve_guaardvark_root", lambda: root)
        monkeypatch.setattr("llx.backend_bootstrap.RUNTIME_FILE", tmp_path / "absent-runtime.json")
        monkeypatch.setenv("GUAARDVARK_DOCKER", "1")

        with patch("llx.backend_bootstrap.is_backend_healthy", return_value=False):
            from llx.backend_bootstrap import ensure_backend_running

            with pytest.raises(RuntimeError) as exc:
                ensure_backend_running(quiet=True)
        assert "docker compose logs backend" in str(exc.value)
        assert "start.sh not found" not in str(exc.value)

    def test_docker_checkout_on_the_host_is_not_started_natively(self, monkeypatch, tmp_path):
        # A checkout start-docker.sh set up: its .env has the database password,
        # and start.sh never built backend/venv there.
        root = tmp_path / "guaardvark"
        root.mkdir()
        (root / "start.sh").write_text("#!/bin/bash\nexit 0\n")
        (root / ".env").write_text("GUAARDVARK_API_KEY=k\nGUAARDVARK_POSTGRES_PASSWORD=p\n")
        monkeypatch.setattr("llx.backend_bootstrap.resolve_guaardvark_root", lambda: root)
        monkeypatch.setattr("llx.backend_bootstrap.RUNTIME_FILE", tmp_path / "absent-runtime.json")
        monkeypatch.delenv("GUAARDVARK_DOCKER", raising=False)
        run = MagicMock()
        monkeypatch.setattr("llx.backend_bootstrap.subprocess.run", run)

        with patch("llx.backend_bootstrap.is_backend_healthy", return_value=False):
            from llx.backend_bootstrap import ensure_backend_running

            with pytest.raises(RuntimeError) as exc:
                ensure_backend_running(quiet=True)
        assert "./start-docker.sh" in str(exc.value)
        run.assert_not_called()

        # The same checkout once start.sh has built its venv is a native install.
        (root / "backend" / "venv").mkdir(parents=True)
        from llx.backend_bootstrap import is_docker_checkout

        assert is_docker_checkout(root) is False
