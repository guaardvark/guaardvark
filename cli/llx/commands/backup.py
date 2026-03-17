"""Backup and restore commands."""

import typer
from pathlib import Path

from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_server, get_global_json
from llx.theme import make_console
from llx import output

console = make_console()
backup_app = typer.Typer(help="Backup and restore system data.", no_args_is_help=True)


@backup_app.command("create")
def create_backup(
    backup_type: str = typer.Option(
        "full", "--type", "-t", help="Backup type: full, data, code_release"
    ),
    name: str = typer.Option(None, "--name", "-n", help="Custom backup name"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Create a system backup."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    try:
        client = get_client(server)

        from rich.live import Live
        from rich.spinner import Spinner

        payload = {"type": backup_type}
        if name:
            payload["name"] = name

        if not json_out and not output.is_pipe():
            with Live(
                Spinner("dots", text="[llx.dim]Creating backup...[/llx.dim]"),
                console=console,
                transient=True,
            ):
                data = client.post("/api/backups/create", json=payload)
        else:
            data = client.post("/api/backups/create", json=payload)

        if json_out or output.is_pipe():
            output.print_json(data)
        else:
            filename = data.get("file", "?")
            output.print_success(f"Backup created: {filename}")

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


@backup_app.command("list")
def list_backups(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List available backups on the server."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    try:
        client = get_client(server)
        data = client.get("/api/backups")
        backups = data.get("backups", [])

        if json_out or output.is_pipe():
            output.print_json(backups)
        elif not backups:
            console.print("[llx.dim]No backups found.[/llx.dim]")
        else:
            from llx.theme import make_table

            table = make_table(title="Backups")
            table.add_column("Filename")
            for b in backups:
                table.add_row(b)
            console.print(table)

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


@backup_app.command("download")
def download_backup(
    filename: str = typer.Argument(..., help="Backup filename to download"),
    dest: Path = typer.Option(Path("."), "--dest", "-d", help="Destination directory"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Download a backup file from the server."""
    server = server or get_global_server()

    try:
        client = get_client(server)
        dest_file = dest / filename if dest.is_dir() else dest
        client.download(f"/api/backups/{filename}/download", dest_file)
        output.print_success(f"Downloaded to {dest_file}")

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


@backup_app.command("restore")
def restore_backup(
    file: Path = typer.Argument(..., help="Local backup ZIP file to restore"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Restore system from a backup file. This overwrites existing data."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    if not file.exists():
        output.print_error(f"File not found: {file}")
        raise typer.Exit(1)

    if not str(file).endswith(".zip"):
        output.print_error("Only .zip backup files are supported.")
        raise typer.Exit(1)

    if not force and not json_out:
        confirm = typer.confirm(
            f"Restore from {file.name}? This will overwrite existing data"
        )
        if not confirm:
            console.print("[llx.dim]Cancelled.[/llx.dim]")
            raise typer.Exit(0)

    try:
        client = get_client(server)

        from rich.live import Live
        from rich.spinner import Spinner

        if not json_out and not output.is_pipe():
            with Live(
                Spinner("dots", text="[llx.dim]Restoring backup...[/llx.dim]"),
                console=console,
                transient=True,
            ):
                data = client.upload("/api/backups/restore", file)
        else:
            data = client.upload("/api/backups/restore", file)

        if json_out or output.is_pipe():
            output.print_json(data)
        else:
            output.print_success("Backup restored successfully")
            # Show summary of restored items
            summary = {k: v for k, v in data.items() if isinstance(v, int) and v > 0}
            if summary:
                output.print_kv(summary, title="Restored Items")

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


@backup_app.command("delete")
def delete_backup(
    filename: str = typer.Argument(..., help="Backup filename to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Delete a backup from the server."""
    server = server or get_global_server()

    if not force:
        confirm = typer.confirm(f"Delete backup {filename}?")
        if not confirm:
            console.print("[llx.dim]Cancelled.[/llx.dim]")
            raise typer.Exit(0)

    try:
        client = get_client(server)
        client.delete(f"/api/backups/{filename}")
        output.print_success(f"Deleted {filename}")

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)
