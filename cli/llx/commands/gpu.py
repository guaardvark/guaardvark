"""GPU orchestrator — status and lock release."""

import typer

from llx import output
from llx.client import LlxConnectionError, LlxError, get_client
from llx.global_opts import get_global_json, get_global_server

gpu_app = typer.Typer(help="GPU status and owner lock", no_args_is_help=True)


def _client(server):
    return get_client(server or get_global_server())


def _unwrap(data):
    return data.get("data", data) if isinstance(data, dict) else data


@gpu_app.command("status")
def gpu_status(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show GPU availability, VRAM, and current owner lock."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        data = _unwrap(_client(server).get("/api/gpu/status"))
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": data})
            return
        if not isinstance(data, dict):
            output.print_json(data)
            return
        pairs = {
            "Name": data.get("gpu_name") or data.get("name") or "—",
            "Available": data.get("available", "—"),
            "Owner": data.get("owner") or "none",
            "VRAM used": data.get("vram_used") or data.get("memory_used") or "—",
            "VRAM total": data.get("vram_total") or data.get("memory_total") or "—",
            "Util %": data.get("utilization") or data.get("gpu_percent") or "—",
        }
        output.print_kv(pairs, title="GPU")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@gpu_app.command("release")
def gpu_release(
    force: bool = typer.Option(False, "--force", help="Force-release the lock"),
    restart_ollama: bool = typer.Option(False, "--restart-ollama"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Release the GPU owner lock (Ollama / ComfyUI)."""
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    path = "/api/gpu/lock/force-release" if force else "/api/gpu/lock/release"
    try:
        data = _client(server).post(path, json={"restart_ollama": restart_ollama})
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": _unwrap(data)})
            return
        output.print_success(data.get("message", "GPU lock released"))
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)
