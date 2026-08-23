"""Tests for backend bootstrap and port discovery."""
import json
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

        with patch("llx.backend_bootstrap.is_backend_healthy", return_value=True):
            from llx.backend_bootstrap import ensure_backend_running

            url, started = ensure_backend_running(quiet=True)
            assert started is False
            assert url == "http://127.0.0.1:5000"

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
