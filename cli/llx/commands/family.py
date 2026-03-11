"""Interconnector family network management — list nodes, sync, health checks."""

import time
import typer
from rich.live import Live
from rich.table import Table

from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console, make_panel, ICON_ONLINE, ICON_OFFLINE, ICON_SUCCESS, ICON_WARNING
from llx import output

console = make_console()
family_app = typer.Typer(help="Interconnector family network management")


@family_app.command("list")
def family_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List all family nodes and their sync status."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/interconnector/nodes")
        nodes = data.get("nodes", data.get("data", []))
        if isinstance(nodes, dict):
            nodes = nodes.get("nodes", [])

        if json_out or output.is_pipe():
            output.print_json(nodes)
            return

        if not nodes:
            console.print("[llx.dim]No family members registered.[/llx.dim]")
            console.print("[llx.dim]Use the Settings UI or API to register nodes.[/llx.dim]")
            return

        table = Table(title="Family Network", border_style="llx.panel.border", show_lines=True)
        table.add_column("Name", style="llx.accent")
        table.add_column("Host")
        table.add_column("Port", justify="right")
        table.add_column("Status")
        table.add_column("Last Sync")
        table.add_column("Role")

        for node in nodes:
            name = node.get("name") or node.get("node_id", "?")[:12]
            host = node.get("host", "?")
            port = str(node.get("port", "?"))
            is_online = node.get("is_online", node.get("status") == "online")
            icon = ICON_ONLINE if is_online else ICON_OFFLINE
            status_style = "llx.status.online" if is_online else "llx.status.offline"
            status_text = f"[{status_style}]{icon} {'Online' if is_online else 'Offline'}[/{status_style}]"

            last_sync = node.get("last_sync_at") or node.get("last_seen_at", "Never")
            if isinstance(last_sync, str) and "T" in last_sync:
                last_sync = last_sync.split("T")[0] + " " + last_sync.split("T")[1][:5]

            role = node.get("role", "member").capitalize()
            table.add_row(name, host, port, status_text, str(last_sync), role)

        console.print(table)
        console.print(f"\n[llx.dim]{len(nodes)} node(s) in family[/llx.dim]")

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


@family_app.command("status")
def family_status(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show interconnector status and network health."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/interconnector/status")
        status_data = data.get("data", data)

        if json_out or output.is_pipe():
            output.print_json(status_data)
            return

        enabled = status_data.get("enabled", False)
        role = status_data.get("role", "unknown")
        node_count = status_data.get("connected_nodes", status_data.get("node_count", 0))
        pending = status_data.get("pending_updates", 0)

        lines = []
        e_icon = ICON_ONLINE if enabled else ICON_OFFLINE
        e_style = "llx.status.online" if enabled else "llx.status.offline"
        lines.append(f"[{e_style}]{e_icon} Interconnector {'Enabled' if enabled else 'Disabled'}[/{e_style}]")
        lines.append(f"[llx.kv.key]Role:[/llx.kv.key]             {role}")
        lines.append(f"[llx.kv.key]Connected Nodes:[/llx.kv.key]  {node_count}")
        if pending:
            lines.append(f"[llx.warning]{ICON_WARNING} Pending Updates: {pending}[/llx.warning]")
        else:
            lines.append(f"[llx.kv.key]Pending Updates:[/llx.kv.key]  0")

        node_id = status_data.get("node_id")
        if node_id:
            lines.append(f"[llx.kv.key]Node ID:[/llx.kv.key]          {node_id[:16]}...")

        console.print(make_panel("\n".join(lines), title="Interconnector"))

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


@family_app.command("sync")
def family_sync(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Trigger a manual sync with all connected family nodes."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        console.print("[llx.accent]Triggering sync...[/llx.accent]")
        data = client.post("/api/interconnector/sync/push")

        if json_out or output.is_pipe():
            output.print_json(data)
            return

        synced = data.get("synced_nodes", data.get("synced", 0))
        errors = data.get("errors", [])
        output.print_success(f"Sync complete. {synced} node(s) synced.")
        if errors:
            for err in errors:
                output.print_warning(f"  {err}")

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


@family_app.command("health")
def family_health(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Check connectivity to all family nodes."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/interconnector/nodes")
        nodes = data.get("nodes", data.get("data", []))
        if isinstance(nodes, dict):
            nodes = nodes.get("nodes", [])

        if json_out or output.is_pipe():
            output.print_json(nodes)
            return

        if not nodes:
            console.print("[llx.dim]No family members to check.[/llx.dim]")
            return

        console.print("[llx.accent]Checking family node health...[/llx.accent]\n")
        online = 0
        for node in nodes:
            name = node.get("name") or node.get("node_id", "?")[:12]
            host = node.get("host", "?")
            port = node.get("port", "?")
            is_online = node.get("is_online", node.get("status") == "online")
            if is_online:
                online += 1
                console.print(f"  [llx.status.online]{ICON_ONLINE}[/llx.status.online] {name:<20} {host}:{port}")
            else:
                console.print(f"  [llx.status.offline]{ICON_OFFLINE}[/llx.status.offline] {name:<20} {host}:{port}  [llx.dim]unreachable[/llx.dim]")

        total = len(nodes)
        color = "llx.success" if online == total else ("llx.warning" if online > 0 else "llx.error")
        console.print(f"\n[{color}]{online}/{total} nodes online[/{color}]")

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)
