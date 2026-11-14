"""Telling the server from a flask command that runs once and exits."""
import pytest

from backend.utils.process_role import flask_cli_command, one_shot_command

FLASK = "/usr/local/bin/flask"
FLASK_MODULE = "/usr/local/lib/python3.12/site-packages/flask/__main__.py"


@pytest.fixture
def from_cli(monkeypatch):
    monkeypatch.setenv("FLASK_RUN_FROM_CLI", "true")


@pytest.mark.parametrize("argv, command", [
    ([FLASK, "db", "upgrade"], "db"),
    ([FLASK, "load-rules", "bundle.json"], "load-rules"),
    ([FLASK_MODULE, "run", "--host", "0.0.0.0", "--port", "5000"], "run"),
    ([FLASK, "--app", "backend.app", "--debug", "run"], "run"),
    ([FLASK, "-e", ".env", "shell"], "shell"),
    ([FLASK], None),
])
def test_the_command_of_a_flask_invocation(from_cli, argv, command):
    assert flask_cli_command(argv) == command


@pytest.mark.parametrize("argv", [
    [FLASK, "db", "upgrade"],
    [FLASK_MODULE, "db", "upgrade"],
    [FLASK, "shell"],
])
def test_a_command_that_exits_is_one_shot(from_cli, argv):
    assert one_shot_command(argv) is True


@pytest.mark.parametrize("argv", [
    [FLASK_MODULE, "run", "--host", "0.0.0.0", "--port", "5000"],
    [FLASK, "run"],
    ["/usr/local/bin/celery", "-A", "backend.celery_app.celery", "worker"],
    ["scripts/schema_sync.py"],
    ["backend/app.py"],
    [],
])
def test_the_server_and_other_programs_are_not(from_cli, argv):
    assert one_shot_command(argv) is False


def test_without_the_flask_cli_flag_nothing_is_one_shot(monkeypatch):
    monkeypatch.delenv("FLASK_RUN_FROM_CLI", raising=False)
    assert flask_cli_command([FLASK, "db", "upgrade"]) is None
    assert one_shot_command([FLASK, "db", "upgrade"]) is False


def test_a_child_of_the_server_is_not_a_flask_command(from_cli):
    # Children inherit FLASK_RUN_FROM_CLI from `flask run`.
    assert one_shot_command(["/app/scripts/some_job.py", "db", "upgrade"]) is False
