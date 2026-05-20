"""CLI entrypoint behavior for non-interactive mode."""

import json

from typer.testing import CliRunner

from llx.main import app


runner = CliRunner()


def test_non_interactive_without_command_exits_2():
    result = runner.invoke(app, ["--non-interactive"])
    assert result.exit_code == 2
    assert "No command provided in non-interactive mode" in result.stderr


def test_non_interactive_json_without_command_returns_structured_error():
    result = runner.invoke(app, ["--non-interactive", "--json"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "NO_COMMAND_PROVIDED"
    assert "non-interactive mode" in payload["error"]["message"]
    assert payload["error"]["hint"]


def test_non_interactive_with_command_runs_normally():
    result = runner.invoke(app, ["--non-interactive", "--version"])
    assert result.exit_code == 0
    assert "guaardvark" in result.stdout
