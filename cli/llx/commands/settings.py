"""Settings commands — get, set, list."""

import typer
from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_json, get_global_server
from llx import output

settings_app = typer.Typer(help="Application settings")


@settings_app.command("list")
def settings_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show all settings."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        settings = {}
        for key in ["web_access", "advanced_debug"]:
            try:
                data = client.get(f"/api/settings/{key}")
                settings[key] = data.get("data", data)
            except LlxError:
                settings[key] = "unavailable"

        if json_out or output.is_pipe():
            output.print_json(settings)
        else:
            output.print_kv(
                {k: str(v) for k, v in settings.items()},
                title="Settings",
            )
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@settings_app.command("get")
def settings_get(
    key: str = typer.Argument(..., help="Setting key"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Get a setting value."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get(f"/api/settings/{key}")
        result = data.get("data", data)
        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            output.print_kv({key: str(result)})
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@settings_app.command("set")
def settings_set(
    key: str = typer.Argument(..., help="Setting key"),
    value: str = typer.Argument(..., help="Setting value"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Set a setting value."""
    server = server or get_global_server()
    try:
        parsed: str | bool | int = value
        if value.lower() in ("true", "false"):
            parsed = value.lower() == "true"
        elif value.isdigit():
            parsed = int(value)

        client = get_client(server)
        client.post(f"/api/settings/{key}", json={key: parsed})
        output.print_success(f"Set {key} = {parsed}")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
