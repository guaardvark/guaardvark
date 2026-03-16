"""Rules/prompts management commands."""

import json
from pathlib import Path
import typer
from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_json, get_global_server
from llx import output

rules_app = typer.Typer(help="Rules and system prompts management", no_args_is_help=True)


@rules_app.command("list")
def rules_list(
    project_id: int = typer.Option(None, "--project", "-p", help="Filter by project ID"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List all rules."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        params = {}
        if project_id:
            params["project_id"] = project_id
        data = client.get("/api/rules", **params)
        rules = data if isinstance(data, list) else data.get("data", [])

        if json_out or output.is_pipe():
            output.print_json(rules)
            return

        rows = [{
            "id": r.get("id", ""),
            "name": r.get("name", ""),
            "level": r.get("level", ""),
            "type": r.get("type", ""),
            "active": "Yes" if r.get("is_active") else "No",
        } for r in rules]
        output.print_table(rows, columns=["id", "name", "level", "type", "active"], title=f"Rules ({len(rows)})")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@rules_app.command("create")
def rules_create(
    name: str = typer.Argument(..., help="Rule name"),
    content: str = typer.Option(None, "--content", "-c", help="Rule text"),
    file: Path = typer.Option(None, "--file", "-f", help="Read rule text from file", exists=True),
    level: str = typer.Option("USER_GLOBAL", "--level", "-l", help="Rule level"),
    rule_type: str = typer.Option("SYSTEM", "--type", "-t", help="Rule type"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Create a new rule/prompt."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    if file:
        rule_text = file.read_text()
    elif content:
        rule_text = content
    else:
        output.print_error("Provide --content or --file")
        raise typer.Exit(1)

    try:
        client = get_client(server)
        data = client.post("/api/rules", json={
            "name": name,
            "rule_text": rule_text,
            "level": level,
            "type": rule_type,
        })
        result = data.get("data", data)
        if json_out or output.is_pipe():
            output.print_json(result)
        else:
            output.print_success(f"Created rule: {name} (id: {result.get('id', '?')})")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@rules_app.command("delete")
def rules_delete(
    rule_id: int = typer.Argument(..., help="Rule ID"),
    force: bool = typer.Option(False, "--force", "-f"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Delete a rule."""
    server = server or get_global_server()
    if not force:
        typer.confirm(f"Delete rule {rule_id}?", abort=True)
    try:
        client = get_client(server)
        client.delete(f"/api/rules/{rule_id}")
        output.print_success(f"Deleted rule {rule_id}")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@rules_app.command("export")
def rules_export(
    file: Path = typer.Option("rules.json", "--file", "-f", help="Output file"),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Export all rules to a JSON file."""
    server = server or get_global_server()
    try:
        client = get_client(server)
        data = client.get("/api/meta/rules/export")
        rules = data.get("rules", data)
        file.write_text(json.dumps(rules, indent=2))
        output.print_success(f"Exported {len(rules)} rules to {file}")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@rules_app.command("import")
def rules_import(
    file: Path = typer.Argument(..., help="JSON file to import", exists=True),
    server: str = typer.Option(None, "--server", "-s"),
):
    """Import rules from a JSON file."""
    server = server or get_global_server()
    try:
        rules = json.loads(file.read_text())
        client = get_client(server)
        data = client.post("/api/meta/rules/import", json={"rules": rules})
        created = data.get("created", 0)
        updated = data.get("updated", 0)
        skipped = data.get("skipped", 0)
        output.print_success(f"Import complete: {created} created, {updated} updated, {skipped} skipped")
    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)
