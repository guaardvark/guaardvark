# MCP servers the assistant uses

Guaardvark is an MCP **client** as well as an MCP server. This page is about
the client side: it starts MCP servers on this machine (stdio) and gives their
tools to the assistant in chat, the agents, the web UI and the `llx` CLI.
Remote (HTTP) MCP servers are not supported; a config entry with a `url` is
rejected.

For Guaardvark as an MCP server for other agents, see `llx mcp --help`
(`serve`, `config`, `install`, `doctor`).

- Client: `backend/services/mcp_client_service.py`, built on the official
  [`mcp` Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- Config parsing and child environment: `backend/services/mcp_config.py`
- Tool policy and output hygiene: `backend/services/mcp_policy.py`
- Agent tools: `backend/tools/mcp_tools.py`

## Configuring servers

Servers live in `data/config/mcp_servers.json`, in the same `mcpServers`
format as Claude Desktop; see `mcp_servers.json.example` beside it. You can
also add servers on the **MCP Servers** page (Settings → Agents → Manage MCP
servers), with `llx mcp client add`, or through the REST API. Each of these
writes the file atomically with mode 0600.

```json
{
  "mcpServers": {
    "fs": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/shared"],
      "autoConnect": true,
      "keywords": ["shared folder"],
      "denyTools": ["move_file"]
    }
  }
}
```

| Field | Meaning |
|---|---|
| `command`, `args`, `cwd` | The server program. A list `command` (legacy format) is also accepted. |
| `env` | Extra environment for the process. `${VAR}` copies a variable from the backend's environment. |
| `autoConnect` | Connect when the backend starts (`GUAARDVARK_MCP_AUTOCONNECT=false` turns this off globally). |
| `disabled` | Keep the entry but never connect. |
| `timeout` | Per-request timeout in seconds (default `GUAARDVARK_MCP_TIMEOUT`, 30). |
| `description`, `keywords` | Shown to the model. Messages that contain a keyword, the server name, or the word "mcp" offer this server's tools in chat. |
| `denyTools`, `allowTools`, `confirmTools`, `autoApproveTools` | Tool policy globs (see below). |

`GUAARDVARK_MCP_SERVERS` can hold the same JSON; the file wins when both
define a server. Other settings (`backend/config.py`): `GUAARDVARK_MCP_ENABLED`,
`GUAARDVARK_MCP_TIMEOUT`, `GUAARDVARK_MCP_CONNECT_TIMEOUT`,
`GUAARDVARK_MCP_MAX_OUTPUT_CHARS` (how much tool output reaches the model,
default 16000), `GUAARDVARK_MCP_AUTOCONNECT`, `GUAARDVARK_MCP_CONFIG_FILE`.

These are the client's settings. Guaardvark's own MCP server
(`python -m backend.mcp`, the one other agents connect to) loads the same
`.env` and has its own names: `GUAARDVARK_MCP_SERVER_ENABLED` switches it off,
and `GUAARDVARK_MCP_SERVER_TIMEOUT` is its per-call ceiling (default 120 s).
`GUAARDVARK_MCP_ENABLED=false` does not stop the server. The server does read
`GUAARDVARK_MCP_TIMEOUT` when `GUAARDVARK_MCP_SERVER_TIMEOUT` is unset, so set
both when the two should differ.

**Secrets.** Put secrets in `env`, preferably as `${VAR}` references, never in
`args`: the MCP Servers page and `GET /api/automation/mcp/servers/<name>` show
`args` and never show `env` values.

## How the assistant uses MCP tools

When a server connects, each of its tools is registered as a tool named
`mcp__<server>__<tool>`, with the parameters from the server's input schema.
The registration follows the server: it is removed when the server
disconnects or crashes (detected by health pings) and refreshed when the
server sends `tools/list_changed`.

Meta-tools: `mcp_list_servers`, `mcp_connect`, `mcp_disconnect`,
`mcp_list_tools`, `mcp_execute`, `mcp_list_resources`, `mcp_read_resource`,
`mcp_list_prompts`, `mcp_get_prompt`, `mcp_get_state`.

Server output is capped and labelled as untrusted data before it reaches the
model, and server-provided descriptions are sanitised.

## Tool policy and approval

| Decision | When | Effect |
|---|---|---|
| **deny** | matches `denyTools`, or `allowTools` is set and the tool does not match it | Never offered to the model; direct calls are refused |
| **confirm** | matches `confirmTools`; or the server marks it `destructiveHint`; or it has no `readOnlyHint` and its name looks mutating (write, delete, run, create, send, ...) | The person approves each call |
| **allow** | matches `autoApproveTools`, marked `readOnlyHint`, or none of the above | Runs without asking |

A **confirm** tool raises the chat's approval card (web and `llx chat`), the
same card other approval-gated tools use, with its once / session / task
scopes. It runs only after a yes. On any path with no one to ask (the sync
chat endpoint, `/api/agent/chat`, Celery, the screen loop) it refuses with an
explanatory error. `mcp_execute` never approves a gated tool; it names the
`mcp__` tool to call instead.

## CLI

```bash
llx mcp client status            # enabled, SDK version, servers, recent errors
llx mcp client servers           # configured servers and their status
llx mcp client add fs --command npx --arg -y --arg @modelcontextprotocol/server-filesystem \
    --arg /path/to/shared --auto-connect --deny move_file
llx mcp client tools fs          # tools with their policy
llx mcp client call fs read_text_file --arg path=/path/to/shared/notes.md
llx mcp client call fs write_file --args '{"path": "x", "content": "y"}'   # asks first
llx mcp client resources | read | prompts | show NAME | reload | remove NAME | audit
```

`llx mcp client call` asks `Approve? [y/N]` before a confirm tool. Without a
terminal (`--json`, pipes) it refuses unless the tool matches
`--approve <glob>`.

## REST API (`/api/automation/mcp`)

| Method & path | Purpose |
|---|---|
| `GET /status` | Service state |
| `GET /servers` | Server list |
| `GET /servers/<name>` | Server detail, tools with policy, stderr tail |
| `PUT/DELETE /servers/<name>` | Edit the config (localhost or API key only) |
| `POST /reload-config` | Re-read the config file (localhost or API key only) |
| `POST /connect`, `POST /disconnect` | `{server}` |
| `GET /tools?server=` | Tools with their policy |
| `POST /execute` | `{server, tool, arguments}`; a caller on this machine or with the API key approves confirm tools. Where the opt-out below lets other callers in, they can run only allow tools |
| `GET /resources`, `POST /resources/read` | Resources |
| `GET /prompts`, `POST /prompts/get` | Prompts |
| `GET /audit-log` | Recent calls |

## Security model

- Config writes start programs, so `PUT/DELETE /servers/<name>` and
  `/reload-config` always need a local caller or the `X-API-Key` header.
- All of `/api/automation/`, `/api/tools/execute` and `/api/tools/jobs/` are
  behind the same rule. A browser on another device uses them once it is
  signed in with this install's API key in Settings → Access; until then the MCP
  Servers page and the Tools page say so and link there. A browser on the
  Guaardvark machine itself is a local caller. Once `GUAARDVARK_API_KEY` is
  set, every caller needs the key, that browser included.
- `GUAARDVARK_PROTECT_TOOL_ENDPOINTS=false` in `.env` (then restart the
  backend) opens those routes to every host that can reach the backend. Config
  writes stay closed.
- Server processes get a minimal environment: `PATH`, `HOME`, locale,
  proxy/CA settings, Node/Python basics, plus their own `env`. `DATABASE_URL`,
  API keys, tokens and Redis/Celery URLs are withheld.
- Every call is audit-logged (`logs/mcp_audit.log`, `GET /audit-log`), and
  server stderr goes to `logs/mcp/<server>.log`.
- Server processes are stopped when the backend exits.
