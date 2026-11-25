"""`guaardvark mcp client`: the external MCP servers the agent uses (connect, list and
call tools, manage configuration). `guaardvark mcp` itself covers Guaardvark as an
MCP server for other agents."""

import json

import typer

from llx import output
from llx.approval import can_prompt, decide, matches_any
from llx.client import LlxConnectionError, LlxError, get_client
from llx.global_opts import get_global_json, get_global_server
from llx.theme import make_console

console = make_console()

mcp_client_app = typer.Typer(
    help="External MCP servers the agent uses: connect, list and call tools, manage configuration",
    no_args_is_help=True,
)

BASE = "/api/automation/mcp"


def _setup(server, json_out):
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    return get_client(server), json_out or output.is_pipe()


def _fail(e):
    if isinstance(e, LlxError):
        raise output.fail(e)
    output.print_error(str(e))
    raise typer.Exit(1)


@mcp_client_app.command("status")
def mcp_status(
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """MCP client status: servers, tools, recent errors."""
    try:
        client, as_json = _setup(server, json_out)
        data = client.get(f"{BASE}/status")
        if as_json:
            output.print_json(data)
            return
        output.print_kv({
            "Enabled": data.get("mcp_enabled"),
            "SDK": data.get("sdk_version") or "not installed",
            "Servers": f"{data.get('servers_connected')}/{data.get('servers_configured')} connected",
            "Tools": data.get("total_tools_available"),
            "Calls": data.get("total_calls"),
        }, title="MCP")
        for err in data.get("config_errors", []):
            output.print_warning(f"config: {err}")
        for err in data.get("errors", [])[-5:]:
            output.print_warning(f"{err.get('server')}: {err.get('error')}")
    except (LlxConnectionError, LlxError) as e:
        _fail(e)


@mcp_client_app.command("servers")
def mcp_servers(
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List configured MCP servers."""
    try:
        client, as_json = _setup(server, json_out)
        data = client.get(f"{BASE}/servers")
        servers = data.get("servers", [])
        if as_json:
            output.print_json(data)
            return
        rows = [{
            "name": s["name"],
            "status": s.get("status"),
            "tools": s.get("tool_count") if s.get("connected") else "-",
            "command": s.get("command") or "",
            "auto": "yes" if s.get("auto_connect") else "",
            "last error": (s.get("last_error") or "")[:60],
        } for s in servers]
        output.print_table(rows, title=f"MCP servers ({len(rows)})")
        for err in data.get("config_errors", []):
            output.print_warning(f"config: {err}")
        if not rows:
            console.print("[llx.dim]Add one with: guaardvark mcp client add NAME --command npx --arg -y --arg <package>[/llx.dim]")
    except (LlxConnectionError, LlxError) as e:
        _fail(e)


@mcp_client_app.command("show")
def mcp_show(
    name: str = typer.Argument(..., help="Server name"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Show a server's definition, capabilities, tools and recent stderr."""
    try:
        client, as_json = _setup(server, json_out)
        detail = client.get(f"{BASE}/servers/{name}").get("server", {})
        if as_json:
            output.print_json(detail)
            return
        output.print_kv({
            "Status": detail.get("status"),
            "Definition": json.dumps(detail.get("definition", {})),
            "Protocol": detail.get("protocol_version") or "-",
            "Server": (detail.get("server_info") or {}).get("name", "-"),
            "Last error": detail.get("last_error") or "-",
        }, title=f"MCP server {name}")
        _print_tools(detail.get("tools", []))
        if detail.get("stderr_tail"):
            console.print("[llx.dim]--- stderr (tail) ---[/llx.dim]")
            console.print(detail["stderr_tail"][-1500:], markup=False, highlight=False)
    except (LlxConnectionError, LlxError) as e:
        _fail(e)


@mcp_client_app.command("connect")
def mcp_connect(
    name: str = typer.Argument(..., help="Server name"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Connect to a configured MCP server."""
    try:
        client, as_json = _setup(server, json_out)
        data = client.post(f"{BASE}/connect", json={"server": name})
        if as_json:
            output.print_json(data)
            return
        output.print_success(f"{data.get('message', 'Connected')}: {name} "
                             f"({data.get('tools', 0)} tools, {data.get('duration_ms', 0)}ms)")
    except (LlxConnectionError, LlxError) as e:
        _fail(e)


@mcp_client_app.command("disconnect")
def mcp_disconnect(
    name: str = typer.Argument(..., help="Server name"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Disconnect from an MCP server."""
    try:
        client, as_json = _setup(server, json_out)
        data = client.post(f"{BASE}/disconnect", json={"server": name})
        if as_json:
            output.print_json(data)
            return
        output.print_success(f"Disconnected from {name}")
    except (LlxConnectionError, LlxError) as e:
        _fail(e)


def _print_tools(tools, server_name=None):
    policy_label = {"allow": "auto", "confirm": "asks first", "deny": "blocked"}
    rows = [{
        **({"server": server_name} if server_name else {}),
        "tool": t.get("name"),
        "policy": policy_label.get(t.get("policy"), t.get("policy") or ""),
        "call in chat as": t.get("proxyName", ""),
        "description": (t.get("description") or "")[:70],
    } for t in tools]
    output.print_table(rows, title=f"Tools ({len(rows)})")


@mcp_client_app.command("tools")
def mcp_tools(
    name: str = typer.Argument(None, help="Only this server"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List tools from connected MCP servers, with their approval policy."""
    try:
        client, as_json = _setup(server, json_out)
        data = client.get(f"{BASE}/tools", **({"server": name} if name else {}))
        if as_json:
            output.print_json(data)
            return
        if name:
            _print_tools(data.get("tools", []))
        else:
            rows = []
            for srv, tools in data.get("tools", {}).items():
                rows.extend({**t, "_server": srv} for t in tools)
            if not rows:
                output.print_warning("No connected servers. Try: guaardvark mcp client connect NAME")
                return
            output.print_table([{
                "server": t["_server"],
                "tool": t.get("name"),
                "policy": {"allow": "auto", "confirm": "asks first", "deny": "blocked"}.get(t.get("policy"), ""),
                "description": (t.get("description") or "")[:60],
            } for t in rows], title=f"MCP tools ({len(rows)})")
    except (LlxConnectionError, LlxError) as e:
        _fail(e)


def _parse_kv_args(pairs: list[str]) -> dict:
    """--arg key=value (value parsed as JSON when possible)."""
    out = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise typer.BadParameter(f"expected key=value, got '{pair}'", param_hint="--arg")
        key, raw = pair.split("=", 1)
        try:
            out[key] = json.loads(raw)
        except json.JSONDecodeError:
            out[key] = raw
    return out


@mcp_client_app.command("call")
def mcp_call(
    name: str = typer.Argument(..., help="Server name"),
    tool: str = typer.Argument(..., help="Tool name on that server"),
    args_json: str = typer.Option(None, "--args", help='Arguments as a JSON object, e.g. \'{"path": "."}\''),
    arg: list[str] = typer.Option(None, "--arg", help="Argument as key=value (repeatable; value may be JSON)"),
    approve: list[str] = typer.Option(None, "--approve", "-a",
                                      help="Skip the prompt for tools matching this glob (repeatable)"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Call a tool on an MCP server directly.

    Tools that change things ask for confirmation first (default: No); without
    a terminal they are refused unless matched by --approve.
    """
    try:
        client, as_json = _setup(server, json_out)
        arguments = {}
        if args_json:
            try:
                arguments = json.loads(args_json)
            except json.JSONDecodeError as e:
                raise typer.BadParameter(f"invalid JSON: {e}", param_hint="--args")
            if not isinstance(arguments, dict):
                raise typer.BadParameter("must be a JSON object", param_hint="--args")
        arguments.update(_parse_kv_args(arg))

        tools = client.get(f"{BASE}/tools", server=name).get("tools", [])
        tool_def = next((t for t in tools if t.get("name") == tool), None)
        if tool_def is None:
            raise LlxError(f"Unknown tool '{tool}' on {name}. Available: "
                           f"{', '.join(t.get('name', '') for t in tools)}")
        if tool_def.get("policy") == "deny":
            raise LlxError(f"'{name}/{tool}' is blocked by the server's policy "
                           f"({tool_def.get('policyReason')})")
        if tool_def.get("policy") == "confirm":
            request = {"tool": tool, "mcp_server": name, "mcp_tool": tool, "params": arguments,
                       "reason": tool_def.get("policyReason")}
            approved, how = decide(request, console, list(approve or []), can_prompt() and not as_json)
            if not approved:
                output.print_error(f"Not run: {how}")
                raise typer.Exit(2)

        data = client.post(f"{BASE}/execute", json={"server": name, "tool": tool,
                                                     "arguments": arguments, "caller": "cli"})
        if as_json:
            output.print_json(data)
            return
        text = data.get("text", "")
        output.print_markdown(text if text else "_(no output)_")
        console.print(f"[llx.dim]{name}/{tool}  |  {data.get('duration_ms', 0)}ms[/llx.dim]")
    except (LlxConnectionError, LlxError) as e:
        _fail(e)


@mcp_client_app.command("resources")
def mcp_resources(
    name: str = typer.Argument(None, help="Only this server"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List resources exposed by connected MCP servers."""
    try:
        client, as_json = _setup(server, json_out)
        data = client.get(f"{BASE}/resources", **({"server": name} if name else {}))
        if as_json:
            output.print_json(data)
            return
        rows = []
        for srv, entry in data.get("resources", {}).items():
            for r in entry.get("resources", []):
                rows.append({"server": srv, "uri": r.get("uri"), "name": r.get("name", ""),
                             "type": r.get("mimeType", "")})
            for t in entry.get("templates", []):
                rows.append({"server": srv, "uri": t.get("uriTemplate"), "name": t.get("name", ""),
                             "type": "template"})
        output.print_table(rows, title=f"MCP resources ({len(rows)})")
    except (LlxConnectionError, LlxError) as e:
        _fail(e)


@mcp_client_app.command("read")
def mcp_read(
    name: str = typer.Argument(..., help="Server name"),
    uri: str = typer.Argument(..., help="Resource URI"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Read a resource from an MCP server."""
    try:
        client, as_json = _setup(server, json_out)
        data = client.post(f"{BASE}/resources/read", json={"server": name, "uri": uri})
        if as_json:
            output.print_json(data)
            return
        print(data.get("text", ""))
    except (LlxConnectionError, LlxError) as e:
        _fail(e)


@mcp_client_app.command("prompts")
def mcp_prompts(
    name: str = typer.Argument(None, help="Only this server"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List prompt templates from connected MCP servers."""
    try:
        client, as_json = _setup(server, json_out)
        data = client.get(f"{BASE}/prompts", **({"server": name} if name else {}))
        if as_json:
            output.print_json(data)
            return
        rows = [{"server": srv, "prompt": p.get("name"),
                 "arguments": ", ".join(a.get("name", "") for a in p.get("arguments") or []),
                 "description": (p.get("description") or "")[:60]}
                for srv, prompts in data.get("prompts", {}).items() for p in prompts]
        output.print_table(rows, title=f"MCP prompts ({len(rows)})")
    except (LlxConnectionError, LlxError) as e:
        _fail(e)


@mcp_client_app.command("add")
def mcp_add(
    name: str = typer.Argument(..., help="Server name (letters, digits, - and _)"),
    command: str = typer.Option(..., "--command", "-c", help="Program on this machine that runs the server, e.g. npx"),
    arg: list[str] = typer.Option(None, "--arg", help="Command argument (repeatable, in order)"),
    env: list[str] = typer.Option(None, "--env", "-e",
                                  help="KEY=value for the server (repeatable). Use KEY=${VAR} to "
                                       "reference a backend environment variable instead of a secret"),
    auto_connect: bool = typer.Option(False, "--auto-connect", help="Connect when the backend starts"),
    description: str = typer.Option(None, "--description", "-d", help="One line on what it is for"),
    keyword: list[str] = typer.Option(None, "--keyword", "-k",
                                      help="Chat keyword that offers this server's tools (repeatable)"),
    allow: list[str] = typer.Option(None, "--allow", help="Only expose tools matching this glob"),
    deny: list[str] = typer.Option(None, "--deny", help="Never expose tools matching this glob"),
    confirm: list[str] = typer.Option(None, "--confirm", help="Always ask before tools matching this glob"),
    auto_approve: list[str] = typer.Option(None, "--auto-approve",
                                           help="Never ask before tools matching this glob"),
    timeout: float = typer.Option(None, "--timeout", help="Per-call timeout in seconds"),
    connect: bool = typer.Option(True, "--connect/--no-connect", help="Connect right after saving"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Add or replace an MCP server in data/config/mcp_servers.json.

    Example: guaardvark mcp client add fs --command npx --arg -y
    --arg @modelcontextprotocol/server-filesystem --arg ~/shared --auto-connect
    """
    try:
        client, as_json = _setup(server, json_out)
        definition = {
            "autoConnect": auto_connect,
            "keywords": list(keyword or []),
            "allowTools": list(allow or []),
            "denyTools": list(deny or []),
            "confirmTools": list(confirm or []),
            "autoApproveTools": list(auto_approve or []),
        }
        if description:
            definition["description"] = description
        if timeout:
            definition["timeout"] = timeout
        definition.update({"command": command, "args": list(arg or []),
                           "env": {k: str(v) for k, v in _split_pairs(env, "--env").items()}})
        data = client.put(f"{BASE}/servers/{name}", json=definition)
        result = {"saved": data}
        if connect:
            try:
                result["connect"] = client.post(f"{BASE}/connect", json={"server": name})
            except LlxError as e:
                result["connect"] = {"success": False, "error": e.message}
        if as_json:
            output.print_json(result)
            return
        output.print_success(f"Saved MCP server '{name}'")
        if connect:
            c = result["connect"]
            if c.get("success"):
                output.print_success(f"Connected ({c.get('tools', 0)} tools)")
            else:
                output.print_warning(f"Saved, but connecting failed: {c.get('error')}")
    except (LlxConnectionError, LlxError) as e:
        _fail(e)


def _split_pairs(pairs, hint):
    out = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise typer.BadParameter(f"expected KEY=value, got '{pair}'", param_hint=hint)
        k, v = pair.split("=", 1)
        out[k] = v
    return out


@mcp_client_app.command("remove")
def mcp_remove(
    name: str = typer.Argument(..., help="Server name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Remove an MCP server from the configuration."""
    try:
        client, as_json = _setup(server, json_out)
        if not yes and can_prompt() and not as_json:
            if not typer.confirm(f"Remove MCP server '{name}'?", default=False):
                raise typer.Exit(1)
        data = client.delete(f"{BASE}/servers/{name}")
        if as_json:
            output.print_json(data)
            return
        output.print_success(f"Removed {name}")
    except (LlxConnectionError, LlxError) as e:
        _fail(e)


@mcp_client_app.command("reload")
def mcp_reload(
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Re-read the MCP configuration (servers whose definition changed are restarted)."""
    try:
        client, as_json = _setup(server, json_out)
        data = client.post(f"{BASE}/reload-config", json={})
        if as_json:
            output.print_json(data)
            return
        output.print_success(f"Reloaded: {', '.join(data.get('servers', [])) or 'no servers'}")
        for err in data.get("errors", []):
            output.print_warning(err)
    except (LlxConnectionError, LlxError) as e:
        _fail(e)


@mcp_client_app.command("audit")
def mcp_audit(
    limit: int = typer.Option(25, "--limit", "-n", help="Number of entries"),
    tool_filter: str = typer.Option(None, "--tool", help="Only entries whose tool matches this glob"),
    server: str = typer.Option(None, "--server", "-s", help="Override server URL"),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """Recent MCP calls (who called what, and whether it ran)."""
    try:
        client, as_json = _setup(server, json_out)
        entries = client.get(f"{BASE}/audit-log", limit=limit).get("entries", [])
        if tool_filter:
            entries = [e for e in entries if matches_any(e.get("tool", ""), [tool_filter])]
        if as_json:
            output.print_json(entries)
            return
        output.print_table([{
            "time": (e.get("ts") or "")[11:19],
            "call": f"{e.get('server')}/{e.get('tool')}",
            "by": e.get("caller", ""),
            "result": "ok" if e.get("success") else (e.get("decision") or "failed"),
            "ms": e.get("duration_ms", ""),
            "error": (e.get("error") or "")[:50],
        } for e in entries], title=f"MCP activity ({len(entries)})")
    except (LlxConnectionError, LlxError) as e:
        _fail(e)
