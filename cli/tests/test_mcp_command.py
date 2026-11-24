"""Test guaardvark mcp commands (serve, etc.)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure worktree's cli/ package takes precedence over any site-packages editable install
CLI_DIR = Path(__file__).resolve().parents[1]
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from typer.testing import CliRunner

from llx.main import app

runner = CliRunner()


def test_serve_stdio_runs_expected_argv(tmp_path, monkeypatch):
    fake_root = tmp_path / "fake_repo"
    fake_root.mkdir()
    (fake_root / "start.sh").touch()
    fake_py = fake_root / "backend" / "venv" / "bin" / "python"
    fake_py.parent.mkdir(parents=True)
    fake_py.touch()

    monkeypatch.setenv("GUAARDVARK_ROOT", str(fake_root))

    executed = {}

    def mock_execv(py, argv):
        executed["py"] = py
        executed["argv"] = argv
        executed["cwd"] = os.getcwd()
        raise SystemExit(0)

    monkeypatch.setattr(os, "execv", mock_execv)

    result = runner.invoke(app, ["mcp", "serve"])

    assert result.exit_code == 0
    assert executed["py"] == str(fake_py)
    assert executed["argv"] == [str(fake_py), "-m", "backend.mcp"]
    assert executed["cwd"] == str(fake_root)


def test_serve_http_runs_expected_argv(tmp_path, monkeypatch):
    fake_root = tmp_path / "fake_repo"
    fake_root.mkdir()
    (fake_root / "start.sh").touch()
    fake_py = fake_root / "backend" / "venv" / "bin" / "python"
    fake_py.parent.mkdir(parents=True)
    fake_py.touch()

    monkeypatch.setenv("GUAARDVARK_ROOT", str(fake_root))

    executed = {}

    def mock_execv(py, argv):
        executed["py"] = py
        executed["argv"] = argv
        executed["cwd"] = os.getcwd()
        raise SystemExit(0)

    monkeypatch.setattr(os, "execv", mock_execv)

    result = runner.invoke(app, ["mcp", "serve", "--http"])

    assert result.exit_code == 0
    assert executed["py"] == str(fake_py)
    assert executed["argv"] == [str(fake_py), "-m", "backend.mcp", "http"]
    assert executed["cwd"] == str(fake_root)


def _no_exec(py, argv):
    # A real exec would replace the test process with the MCP server.
    raise AssertionError(f"mcp serve tried to exec {argv}")


def test_serve_from_any_folder_uses_the_installed_checkout(tmp_path, monkeypatch):
    fake_root = tmp_path / "fake_repo"
    (fake_root / "backend" / "venv" / "bin").mkdir(parents=True)
    (fake_root / "start.sh").touch()
    fake_py = fake_root / "backend" / "venv" / "bin" / "python"
    fake_py.touch()
    elsewhere = tmp_path / "client_folder"
    elsewhere.mkdir()

    monkeypatch.delenv("GUAARDVARK_ROOT", raising=False)
    monkeypatch.chdir(elsewhere)
    from llx.commands import mcp as mcp_cmd
    monkeypatch.setattr(mcp_cmd, "_installed_checkout", lambda: fake_root)

    executed = {}

    def mock_execv(py, argv):
        executed["argv"] = argv
        executed["cwd"] = os.getcwd()
        raise SystemExit(0)

    monkeypatch.setattr(os, "execv", mock_execv)

    result = runner.invoke(app, ["mcp", "serve"])

    assert result.exit_code == 0
    assert executed["argv"] == [str(fake_py), "-m", "backend.mcp"]
    assert executed["cwd"] == str(fake_root)


def test_serve_without_root_exits_nonzero_with_message(tmp_path, monkeypatch):
    # No GUAARDVARK_ROOT, a cwd outside any checkout, and no installed checkout to fall back to
    monkeypatch.delenv("GUAARDVARK_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    from llx.commands import mcp as mcp_cmd
    monkeypatch.setattr(mcp_cmd, "_installed_checkout", lambda: None)
    monkeypatch.setattr(os, "execv", _no_exec)

    result = runner.invoke(app, ["mcp", "serve"])

    assert result.exit_code != 0

    # Ensure ONE clear line was printed to stderr
    err = result.stderr if hasattr(result, "stderr") and result.stderr else result.output
    lines = [line for line in err.splitlines() if line.strip()]
    assert len(lines) == 1, f"Expected 1 line in stderr, got {lines}"

    msg = lines[0]
    assert "start.sh" in msg
    assert "GUAARDVARK_ROOT" in msg or "cwd" in msg
    assert "checkout is required" in msg
    assert "git clone https://github.com/guaardvark/guaardvark" in msg
    assert "./start.sh" in msg


def test_find_checkout_skips_a_foreign_start_sh(tmp_path, monkeypatch):
    from llx.commands import mcp as mcp_cmd

    foreign = tmp_path / "other_project"
    foreign.mkdir()
    (foreign / "start.sh").touch()
    monkeypatch.delenv("GUAARDVARK_ROOT", raising=False)
    monkeypatch.chdir(foreign)
    found = mcp_cmd._find_checkout()
    assert found != foreign.resolve()
    assert found is None or (found / "backend" / "mcp" / "__main__.py").is_file()


def test_installed_checkout_reads_the_saved_root_pointer(tmp_path, monkeypatch):
    from llx.commands import mcp as mcp_cmd

    checkout = tmp_path / "checkout"
    (checkout / "backend" / "mcp").mkdir(parents=True)
    (checkout / "backend" / "mcp" / "__main__.py").touch()
    home = tmp_path / "home"
    (home / ".config" / "guaardvark").mkdir(parents=True)
    (home / ".config" / "guaardvark" / "root").write_text(f"{checkout}\n")
    monkeypatch.setattr(mcp_cmd, "__file__", str(tmp_path / "a" / "b" / "c" / "mcp.py"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert mcp_cmd._installed_checkout() == checkout.resolve()
