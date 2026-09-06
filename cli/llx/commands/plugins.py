"""Plugin management — list, start, stop, enable, disable, logs."""

import typer

from llx import output
from llx.client import LlxConnectionError, LlxError, get_client
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console

console = make_console()
plugins_app = typer.Typer(help="GPU / service plugins", no_args_is_help=True)


def _client(server):
    return get_client(server or get_global_server())


def _unwrap(data):
    return data.get("data", data) if isinstance(data, dict) else data


@plugins_app.command("list")
def plugins_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List registered plugins and their status."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _unwrap(_client(server).get("/api/plugins"))
        plugins = data.get("plugins", []) if isinstance(data, dict) else data
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": {"plugins": plugins}})
            return
        rows = []
        for p in plugins or []:
            rows.append({
                "id": p.get("id", p.get("name", "")),
                "status": p.get("status", p.get("state", "")),
                "enabled": p.get("enabled", ""),
                "port": p.get("port", ""),
            })
        output.print_table(rows, columns=["id", "status", "enabled", "port"], title="Plugins")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@plugins_app.command("start")
def plugins_start(
    plugin_id: str = typer.Argument(..., help="Plugin id (comfyui, ollama, ...)"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Start a plugin."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _client(server).post(f"/api/plugins/{plugin_id}/start")
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": _unwrap(data)})
            return
        output.print_success(data.get("message", f"Started {plugin_id}"))
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@plugins_app.command("stop")
def plugins_stop(
    plugin_id: str = typer.Argument(..., help="Plugin id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Stop a plugin."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _client(server).post(f"/api/plugins/{plugin_id}/stop")
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": _unwrap(data)})
            return
        output.print_success(data.get("message", f"Stopped {plugin_id}"))
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@plugins_app.command("enable")
def plugins_enable(
    plugin_id: str = typer.Argument(..., help="Plugin id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Persistently enable a plugin."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _client(server).post(f"/api/plugins/{plugin_id}/enable")
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": _unwrap(data)})
            return
        output.print_success(data.get("message", f"Enabled {plugin_id}"))
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@plugins_app.command("disable")
def plugins_disable(
    plugin_id: str = typer.Argument(..., help="Plugin id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Persistently disable a plugin."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _client(server).post(f"/api/plugins/{plugin_id}/disable")
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": _unwrap(data)})
            return
        output.print_success(data.get("message", f"Disabled {plugin_id}"))
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@plugins_app.command("status")
def plugins_status(
    plugin_id: str = typer.Argument(None, help="Optional plugin id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show one plugin, or the orchestrator state."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = _client(server)
        if plugin_id:
            data = _unwrap(client.get(f"/api/plugins/{plugin_id}"))
        else:
            data = _unwrap(client.get("/api/plugins/orchestrator/state"))
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": data})
            return
        if isinstance(data, dict):
            output.print_kv({k: v for k, v in data.items() if not isinstance(v, (dict, list))}, title=plugin_id or "Orchestrator")
        else:
            console.print(data)
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@plugins_app.command("logs")
def plugins_logs(
    plugin_id: str = typer.Argument(..., help="Plugin id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show plugin info (includes recent log fields when the API provides them)."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _unwrap(_client(server).get(f"/api/plugins/{plugin_id}"))
        logs = ""
        if isinstance(data, dict):
            logs = data.get("logs") or data.get("log") or data.get("recent_logs") or ""
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": data})
            return
        if logs:
            console.print(logs)
        else:
            output.print_kv(data if isinstance(data, dict) else {"info": data}, title=plugin_id)
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)
