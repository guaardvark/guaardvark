"""Agent management commands."""

import typer
from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_json, get_global_server
from llx import output
from pathlib import Path

agents_app = typer.Typer(help="Agent configuration and info", no_args_is_help=True)


@agents_app.command("list")
def agents_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List all configured agents."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/agents")
        agents = data.get("agents", [])

        if json_out or output.is_pipe():
            output.print_json(agents)
            return

        rows = [
            {
                "id": a.get("id", ""),
                "name": a.get("name", ""),
                "enabled": "Yes" if a.get("enabled") else "No",
                "tools": str(len(a.get("tools", []))),
            }
            for a in agents
        ]
        output.print_table(
            rows,
            columns=["id", "name", "enabled", "tools"],
            title=f"Agents ({len(rows)})",
        )

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@agents_app.command("info")
def agents_info(
    agent_id: str = typer.Argument(..., help="Agent ID"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show agent details and available tools."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get(f"/api/agents/{agent_id}")
        agent = data.get("agent", data)
        tools = data.get("tools_detail", [])

        if json_out or output.is_pipe():
            output.print_json(data)
            return

        output.print_kv(
            {
                "ID": agent.get("id", ""),
                "Name": agent.get("name", ""),
                "Enabled": str(agent.get("enabled", False)),
                "Description": agent.get("description", "—"),
                "Max iterations": str(agent.get("max_iterations", "—")),
            },
            title="Agent",
        )

        if tools:
            rows = [
                {"name": t.get("name", ""), "description": t.get("description", "")}
                for t in tools
            ]
            output.print_table(rows, columns=["name", "description"], title="Tools")

    except (LlxConnectionError, LlxError) as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@agents_app.command("run")
def run_agent(
    prompt: str = typer.Argument(..., help="Message/prompt to send to the agent"),
    agent_id: str = typer.Option(
        None, "--agent", "-a", help="Agent ID (auto-matched if omitted)"
    ),
    max_iterations: int = typer.Option(
        10, "--max-iter", "-n", help="Max agent iterations"
    ),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Execute an agent with a prompt. Auto-selects best agent if --agent is omitted."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    try:
        client = get_client(server)
        console = output._out

        if not json_out and not output.is_pipe():
            from rich.live import Live
            from rich.spinner import Spinner

            with Live(
                Spinner("dots", text="[llx.dim]Agent working...[/llx.dim]"),
                console=console,
                transient=True,
            ):
                data = client.post(
                    "/api/agents/execute",
                    json={
                        "agent_id": agent_id,
                        "message": prompt,
                        "context": {"max_iterations": max_iterations},
                    },
                )
        else:
            data = client.post(
                "/api/agents/execute",
                json={
                    "agent_id": agent_id,
                    "message": prompt,
                    "context": {"max_iterations": max_iterations},
                },
            )

        if json_out or output.is_pipe():
            output.print_json(data)
            return

        result = data.get("result", data)
        agent_used = data.get("agent_used", agent_id or "auto")
        answer = (
            result.get("final_answer", "") if isinstance(result, dict) else str(result)
        )
        iterations = result.get("iterations", "?") if isinstance(result, dict) else "?"
        steps = result.get("steps", []) if isinstance(result, dict) else []

        from rich.markdown import Markdown

        console.print(
            f"\n[llx.accent]Agent:[/llx.accent] {agent_used}  [llx.dim]|[/llx.dim]  [llx.accent]Iterations:[/llx.accent] {iterations}"
        )
        if steps:
            tool_calls = sum(len(s.get("tool_calls", [])) for s in steps)
            console.print(f"[llx.accent]Tool calls:[/llx.accent] {tool_calls}")
        console.print()
        console.print(Markdown(answer))
        console.print()

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)


@agents_app.command("update")
def update_agent(
    agent_id: str = typer.Argument(..., help="Agent ID to update"),
    enabled: bool = typer.Option(
        None, "--enabled/--disabled", help="Enable or disable agent"
    ),
    max_iterations: int = typer.Option(None, "--max-iter", "-n", help="Max iterations"),
    prompt_file: Path = typer.Option(
        None, "--prompt-file", "-f", help="System prompt from file"
    ),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Update an agent's configuration."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)

    payload = {}
    if enabled is not None:
        payload["enabled"] = enabled
    if max_iterations is not None:
        payload["max_iterations"] = max_iterations
    if prompt_file:
        payload["system_prompt"] = prompt_file.read_text(encoding="utf-8")

    if not payload:
        output.print_error(
            "No updates specified. Use --enabled, --max-iter, or --prompt-file."
        )
        raise typer.Exit(1)

    try:
        client = get_client(server)
        data = client._request("PATCH", f"/api/agents/{agent_id}", json=payload)

        if json_out or output.is_pipe():
            output.print_json(data)
        else:
            output.print_success(f"Agent '{agent_id}' updated")

    except LlxConnectionError as e:
        output.print_error(str(e))
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message)
        raise typer.Exit(1)
