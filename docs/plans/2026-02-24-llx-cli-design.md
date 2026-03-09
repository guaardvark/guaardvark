# LLX CLI Design Document

**Date**: 2026-02-24
**Status**: Approved

## Overview

`llx` is an interactive CLI for the Guaardvark platform. It provides full access to chat, file management, RAG search, content generation, project management, and system administration from the terminal. Designed for both human power users (rich formatted output, streaming, REPL mode) and automation (JSON output, pipe-friendly I/O).

## Technology Stack

- **Python 3.12+**
- **typer** — CLI framework (built on Click, gives automatic --help, subcommands, completions)
- **rich** — Terminal formatting (tables, markdown rendering, syntax highlighting, progress bars, Live display)
- **python-socketio** — Socket.IO client for chat streaming and job progress
- **httpx** — HTTP client (async-capable, timeout handling)

## File Structure

```
cli/
├── setup.py                 # Package setup, "llx" entry point
├── requirements.txt         # typer[all], rich, python-socketio, httpx
├── llx/
│   ├── __init__.py          # Version string
│   ├── main.py              # Typer app, registers command groups, global flags
│   ├── client.py            # LlxClient — single HTTP abstraction for all API calls
│   ├── streaming.py         # Socket.IO client for chat streaming + job progress
│   ├── config.py            # Config loading (~/.llx/config.json), defaults, llx init
│   ├── output.py            # Rich formatters, markdown rendering, pipe detection, JSON mode
│   ├── repl.py              # Interactive REPL mode (llx with no args)
│   └── commands/
│       ├── __init__.py
│       ├── chat.py          # Chat with streaming, sessions, pipe input, export
│       ├── files.py         # File list/upload/download/delete/mkdir
│       ├── search.py        # Semantic search
│       ├── projects.py      # Project CRUD
│       ├── rules.py         # Rules management + import/export
│       ├── agents.py        # Agent listing/info
│       ├── generate.py      # Content generation (CSV, image)
│       ├── jobs.py          # Job management + live watch
│       ├── settings.py      # Settings get/set/list
│       └── system.py        # health, status dashboard, models, init
```

## Command Reference

### Global Flags

All commands support:
- `--json` / `-j` — Output as JSON (for scripting)
- `--server URL` — Override server URL (default from config)
- `--help` — Auto-generated help text

### Commands

| Command | Description |
|---------|-------------|
| `llx` (no args) | Enter interactive REPL |
| `llx init` | First-run setup wizard |
| `llx status` | System dashboard (server, model, GPU, jobs) |
| `llx health` | Quick health check |
| `llx models list` | List available LLM models |
| `llx models active` | Show currently active model |
| `llx chat "prompt"` | Chat with streaming response |
| `llx chat --resume "prompt"` | Continue last conversation |
| `llx chat --session ID "prompt"` | Resume specific session |
| `llx chat --list` | List recent chat sessions |
| `llx chat --export` | Export conversation to markdown |
| `llx chat --no-rag "prompt"` | Chat without RAG context |
| `llx search "query"` | Semantic search over indexed docs |
| `llx search "query" --limit N` | Limit number of results |
| `llx files list` | List files in root |
| `llx files list --path /folder` | List files in specific folder |
| `llx files upload FILE` | Upload a file |
| `llx files download ID` | Download a file by ID |
| `llx files delete ID` | Delete a file |
| `llx files mkdir NAME` | Create a folder |
| `llx projects list` | List all projects |
| `llx projects create NAME` | Create a project |
| `llx projects delete ID` | Delete a project |
| `llx projects info ID` | Show project details |
| `llx rules list` | List all rules/prompts |
| `llx rules create --name N --content C` | Create a rule |
| `llx rules delete ID` | Delete a rule |
| `llx rules export --file rules.json` | Export rules to file |
| `llx rules import --file rules.json` | Import rules from file |
| `llx agents list` | List configured agents |
| `llx agents info ID` | Show agent details |
| `llx generate csv "prompt" --output file.csv` | Generate CSV content |
| `llx generate image "prompt"` | Generate an image |
| `llx jobs list` | List recent jobs |
| `llx jobs status ID` | Check job status |
| `llx jobs watch ID` | Live-watch job progress |
| `llx jobs cancel ID` | Cancel a running job |
| `llx settings list` | Show all settings |
| `llx settings get KEY` | Get a setting value |
| `llx settings set KEY VALUE` | Set a setting |
| `llx --install-completion` | Install shell tab completions |

## Core Component Designs

### client.py — HTTP Client

Single class all commands use. Never import httpx directly in commands.

```python
class LlxClient:
    def __init__(self, server_url: str, api_key: str | None = None):
        self.http = httpx.Client(
            base_url=server_url,
            timeout=30.0,
            headers={"X-API-Key": api_key} if api_key else {},
        )

    def get(self, path: str, **params) -> dict
    def post(self, path: str, **data) -> dict
    def upload(self, path: str, file_path: Path) -> dict
    def download(self, path: str, dest: Path) -> Path
    def delete(self, path: str) -> dict
```

All methods raise `LlxError(message, status_code)` on failure. The error message comes from the server's response, not from httpx internals.

Connection failures raise `LlxConnectionError` with a helpful message: "Cannot connect to Guaardvark at {url}. Is the server running? Try: ./start.sh"

### streaming.py — Socket.IO Client

Handles the async streaming for chat and job progress.

```python
class LlxStreamer:
    def __init__(self, server_url: str):
        self.sio = socketio.Client()

    def stream_chat(
        self,
        session_id: str,
        message: str,
        on_token: Callable[[str], None],
        on_complete: Callable[[str, dict], None],
        on_error: Callable[[str], None],
    ) -> None:
        """Connect, join session, POST chat, stream tokens, disconnect."""

    def watch_job(
        self,
        job_id: str,
        on_progress: Callable[[dict], None],
        on_complete: Callable[[dict], None],
    ) -> None:
        """Subscribe to job progress events."""
```

### config.py — Configuration

Config file: `~/.llx/config.json`

```json
{
  "server": "http://localhost:5000",
  "api_key": null,
  "default_output": "table",
  "chat_session_history": 50
}
```

Sessions file: `~/.llx/sessions.json` — stores recent session IDs with preview text for `--resume` and `--list`.

`llx init` creates the config interactively:
1. Prompt for server URL (default: http://localhost:5000)
2. Test connection to /api/health
3. Show active model and system info
4. Write config file
5. Offer to install shell completions

### output.py — Output Formatting

```python
def is_pipe() -> bool:
    """Detect if stdout is piped (not a TTY)."""

def print_table(data: list[dict], columns: list[str]) -> None:
    """Rich table for humans, JSON array for pipes/--json."""

def print_panel(title: str, content: str) -> None:
    """Rich panel with border."""

def print_markdown(text: str) -> None:
    """Render markdown with syntax highlighting for code blocks."""

def print_json(data: Any) -> None:
    """Pretty-printed JSON output."""

def print_error(message: str) -> None:
    """Red error message to stderr."""

def print_success(message: str) -> None:
    """Green success message."""
```

When `--json` is set OR stdout is a pipe, all output functions emit JSON instead of Rich formatting.

### repl.py — Interactive REPL

Launched when `llx` is run with no arguments.

- Custom prompt: `llx> `
- Parses input as if it were command-line args (e.g., `chat "hello"` = `llx chat "hello"`)
- Maintains chat session across commands (no need for --resume within REPL)
- `help` shows available commands
- `exit` / `quit` / Ctrl+D exits
- Arrow keys for history (via readline/prompt_toolkit)
- Catches exceptions gracefully — never crashes the REPL

### Chat Command — Streaming Flow

1. Generate or reuse session_id (UUID)
2. If stdin has piped content, read it and prepend to message
3. Connect Socket.IO to server
4. Emit `chat:join` with `{session_id}`
5. POST `/api/enhanced-chat` with `{session_id, message, use_rag}`
6. Listen for events:
   - `chat:token` → append token to Rich Live display
   - `chat:thinking` → show spinner with status
   - `chat:tool_call` → show tool call info
   - `chat:complete` → finalize display, show footer (time, tokens)
   - `chat:error` → print error
7. On Ctrl+C → emit `chat:abort`, disconnect
8. Save session to `~/.llx/sessions.json`

### Status Dashboard

`llx status` shows a Rich panel:

```
╭─ Guaardvark System Status ─────────────────────╮
│ Server:  http://localhost:5000  ✓ Online    │
│ Model:   llama3.2:latest       ✓ Loaded    │
│ Celery:  2 workers             ✓ Active    │
│ GPU:     NVIDIA RTX 4090       78% Memory  │
│ CPU:     12% util              4.2GB RAM   │
│ Jobs:    2 running, 0 queued               │
│ Docs:    847 indexed                       │
╰─────────────────────────────────────────────╯
```

Aggregates data from `/api/health`, `/api/system/metrics`, `/api/model/active`, `/api/health/celery`.

## Backend API Endpoints Used

| CLI Command | HTTP Method | Endpoint |
|------------|-------------|----------|
| chat | POST | `/api/enhanced-chat` |
| chat (streaming) | Socket.IO | `chat:join`, `chat:token`, `chat:complete` |
| search | POST | `/api/search/semantic` |
| files list | GET | `/api/files/browse?path=` |
| files upload | POST | `/api/files/upload` |
| files download | GET | `/api/files/document/:id/download` |
| files delete | DELETE | `/api/files/document/:id` |
| files mkdir | POST | `/api/files/folder` |
| projects list | GET | `/api/projects` |
| projects create | POST | `/api/projects` |
| projects delete | DELETE | `/api/projects/:id` |
| rules list | GET | `/api/rules` |
| rules create | POST | `/api/rules` |
| rules delete | DELETE | `/api/rules/:id` |
| rules export | GET | `/api/meta/export-rules` |
| rules import | POST | `/api/meta/import-rules` |
| agents list | GET | `/api/agents` |
| agents info | GET | `/api/agents/:id` |
| generate csv | POST | `/api/generate/csv` |
| generate image | POST | `/api/batch-image-generation` |
| jobs list | GET | `/api/unified-jobs` |
| jobs status | GET | `/api/generate/status?job_id=` |
| jobs watch | Socket.IO | `subscribe` + `progress` events |
| settings list | GET | `/api/settings` |
| settings get | GET | `/api/settings/:key` |
| settings set | POST | `/api/settings/:key` |
| health | GET | `/api/health` |
| status | GET | `/api/health` + `/api/system/metrics` + `/api/model` + `/api/health/celery` |
| models list | GET | `/api/model/available` |
| models active | GET | `/api/model/active` |

## Error Handling

- **Connection refused**: "Cannot connect to Guaardvark at {url}. Is the server running?"
- **401/403**: "Authentication failed. Check your API key with: llx init"
- **404**: "Resource not found: {details}"
- **500**: "Server error: {server message}"
- **Timeout**: "Request timed out after {n}s. The server may be busy."
- **Socket.IO disconnect**: Reconnect once, then fail with message.

All errors go to stderr. Exit code 1 for errors, 0 for success.

## Installation

```bash
cd cli
pip install -e .

# Or from project root:
pip install -e ./cli

# Verify:
llx --version
llx init
```

## Dependencies

```
typer[all]>=0.9.0
rich>=13.0.0
python-socketio>=5.10.0
httpx>=0.25.0
```
