# Guaardvark

**Version 2.4.1** · [guaardvark.com](https://guaardvark.com)

A self-hosted AI platform that runs entirely on your hardware. Chat with your documents using RAG, generate images and video, manage files with a desktop-style UI, automate code with an agent assistant, and talk to your AI with voice — all through one unified interface backed by local LLMs via Ollama.

> **No cloud dependencies. No API keys required. Your data stays on your machine.**

---

## Screenshots

| Dashboard | Chat with RAG |
|:-:|:-:|
| ![Dashboard](docs/screenshots/dashboard.png) | ![Chat](docs/screenshots/chat.png) |

| Document Manager | Image Library |
|:-:|:-:|
| ![Documents](docs/screenshots/documents.png) | ![Images](docs/screenshots/images.png) |

---

## Features

- **Chat with RAG** — Conversational AI grounded in your documents via hybrid BM25 + vector retrieval, with per-project context isolation
- **Multi-modal generation** — Batch image generation (Stable Diffusion via Diffusers), video generation (CogVideoX), bulk CSV/XML content pipelines
- **Agent code assistant** — ReACT-loop agent that reads, writes, executes, and verifies code autonomously with tool use
- **Voice interface** — Speech-to-text (Whisper.cpp) + text-to-speech (Piper TTS) with real-time streaming
- **File & document management** — Desktop-metaphor UI with draggable icons, folder windows, right-click context menus, thumbnail grids, and lightbox preview
- **Image library** — Organize AI-generated images into folders, browse with thumbnails, full-size lightbox with keyboard navigation
- **WordPress integration** — Pull content from WordPress sites, generate at scale, sync back
- **Automation tools** — Browser automation (Playwright), desktop automation (pyautogui), MCP server integration
- **CLI** — Full platform access via the `llx` terminal tool with interactive REPL
- **Plugin system** — Drop-in extensions without modifying core code
- **Themes** — Four built-in dark themes (Default, Musk, Hacker, Vader) with accent-colored UI
- **Offline-capable** — All AI processing runs locally via Ollama; no cloud services required

---

## Quick Start

```bash
git clone https://github.com/guaardvark/guaardvark.git
cd guaardvark
cp .env.example .env       # Edit with your settings
./start.sh
```

The startup script handles everything on first run: Python venv, Node dependencies, Whisper.cpp build, database migrations, frontend build, and service startup.

**Access:**
| Service | URL |
|---------|-----|
| Web UI | http://localhost:5173 |
| API | http://localhost:5000 |
| Health Check | http://localhost:5000/api/health |

### Startup Options

```bash
./start.sh                    # Full startup with health checks
./start.sh --fast             # Skip dependency checks and builds
./start.sh --test             # Comprehensive health diagnostics
./start.sh --skip-migrations  # Skip migration pre-flight
./start.sh --no-voice         # Skip voice API health check
./stop.sh                     # Stop all services
```

---

## Requirements

| Dependency | Version | Notes |
|-----------|---------|-------|
| Python | 3.12+ | Backend runtime |
| Node.js | 20+ | Frontend build |
| Redis | 5.0+ | Task queue broker |
| FFmpeg | any | Voice processing |
| Ollama | latest | Local LLM inference (optional) |
| CUDA GPU | — | Accelerates image/video generation (optional) |

---

## Technology Stack

**Backend:** Flask 3.0 · SQLAlchemy + SQLite · Celery + Redis · LlamaIndex + Ollama · PyTorch · Diffusers · Whisper.cpp · Piper TTS · Ariadne (GraphQL)

**Frontend:** React 18 · Vite · Material-UI v5 · Zustand · Apollo Client · Monaco Editor · Socket.io

**CLI:** Typer · Rich · httpx · python-socketio

---

## Architecture

```
Browser UI / llx CLI
        │ HTTP + WebSocket
        ▼
Flask Application (port 5000)
  ├── 68 REST API blueprints
  ├── GraphQL (Ariadne)
  └── Socket.IO (real-time streaming)
        │
  Service Layer (48 modules)
  ├── Agent Executor (ReACT loop)
  ├── RAG Pipeline (LlamaIndex)
  ├── Generation Services
  └── System Services
        │
  ┌─────┼────────────────┐
  ▼     ▼                ▼
SQLite  Celery + Redis   Ollama / GPU
```

**Key flows:**

- **Chat + RAG:** Message → intent routing → hybrid retrieval (BM25 + vector) → Ollama completion → Socket.IO stream
- **Agent task:** Message → ReACT loop → tool calls (read/edit/execute code) → iterative refinement → verification
- **Image generation:** Prompt → Celery job → Diffusers GPU pipeline → auto-register to file system → `data/outputs/`
- **File indexing:** Upload → parse → chunk → embed → LlamaIndex vector store (per-project isolation)

---

## Project Structure

```
guaardvark/
├── backend/
│   ├── api/            # 68 Flask blueprint modules
│   ├── services/       # 48 business logic modules
│   ├── tools/          # Agent-callable tools (code, browser, voice, web)
│   ├── utils/          # 76 helpers (RAG, context, progress, CSV)
│   ├── tasks/          # Celery background tasks
│   ├── migrations/     # Alembic database migrations
│   ├── tests/          # 60 tests (unit / integration / system)
│   ├── app.py          # Flask application factory
│   ├── models.py       # SQLAlchemy ORM models
│   └── config.py       # Configuration + path resolution
├── frontend/
│   ├── src/
│   │   ├── pages/      # 28 page components
│   │   ├── components/ # 129 UI components
│   │   ├── stores/     # Zustand state management
│   │   ├── hooks/      # Custom React hooks
│   │   └── api/        # 39 API service modules
│   └── dist/           # Production build
├── cli/                # llx CLI tool
├── plugins/            # Plugin extensions
├── scripts/            # Utilities and system manager
├── data/               # Runtime data (gitignored)
└── start.sh / stop.sh  # Service management
```

---

## CLI — `llx`

Install and use the terminal client:

```bash
cd cli && pip install -e .
llx init
```

```bash
llx status                      # System dashboard
llx chat "explain this codebase" # Chat with RAG streaming
llx chat --no-rag "hello"       # Direct LLM, no document context
llx search "query"              # Semantic search across documents
llx files list                  # Browse files
llx files upload report.pdf     # Upload and index a file
llx generate csv "50 blog post ideas about AI" --output ideas.csv
llx jobs watch JOB_ID           # Live job progress
llx rules list                  # List system prompts
llx                             # Interactive REPL
```

---

## Configuration

All paths resolve relative to `GUAARDVARK_ROOT`. Key environment variables (`.env`):

```bash
GUAARDVARK_ROOT=/path/to/guaardvark   # Project root (auto-detected)
FLASK_PORT=5000
VITE_PORT=5173
REDIS_URL=redis://localhost:6379/0
GUAARDVARK_ENHANCED_MODE=true          # Enhanced context features
GUAARDVARK_RAG_DEBUG=false             # RAG debug endpoints
GUAARDVARK_SKIP_MIGRATIONS=0
GUAARDVARK_BROWSER_AUTOMATION=true
GUAARDVARK_DESKTOP_AUTOMATION=false    # Disabled by default
GUAARDVARK_MCP_ENABLED=true            # MCP tool server integration
```

---

## Database Migrations

```bash
python3 scripts/check_migrations.py          # Check status

cd backend && source venv/bin/activate
flask db migrate -m "description"            # Create migration
flask db upgrade                             # Apply migration

flask db merge heads -m "merge heads"        # Fix multiple heads
```

The startup script automatically applies pending migrations. Set `GUAARDVARK_SKIP_MIGRATIONS=1` to bypass.

---

## Testing

```bash
python3 run_tests.py                            # All tests
python3 -m pytest backend/tests/unit -vv       # Unit only
python3 -m pytest backend/tests/integration -vv # Integration only
GUAARDVARK_MODE=test python3 -m pytest backend/tests -vv
```

Results are saved to `logs/test_results/`.

Test layers:
- **Unit** — Isolated, no external dependencies
- **Integration** — Flask test client with real database
- **System** — Full server + Playwright end-to-end
- **Agent** — ReACT loop and code tool validation

---

## Automation Tools

| Tool | Backend | Description |
|------|---------|-------------|
| Browser | Playwright | Navigate, click, fill forms, screenshot, extract content |
| Desktop | pyautogui | Mouse, keyboard, screen capture, window management |
| MCP | Protocol | Connect to any MCP-compatible tool server |

Enable via environment variables:
```bash
GUAARDVARK_BROWSER_AUTOMATION=true
GUAARDVARK_DESKTOP_AUTOMATION=true   # Off by default (security)
GUAARDVARK_MCP_ENABLED=true
```

---

## Plugins

Place plugins in `plugins/<name>/` with a `plugin.json` manifest. Loaded automatically at startup.

Current plugins:
- **gpu_embedding** — GPU-accelerated text embeddings for faster indexing

---

## Logs

| File | Contents |
|------|---------|
| `logs/backend.log` | Flask application |
| `logs/celery.log` | Celery task workers |
| `logs/frontend.log` | Vite dev server |
| `logs/setup.log` | Dependency installation |
| `logs/test_results/` | Test execution output |

---

## Customization

### Profile & Nickname

Guaardvark can be personalized per installation via **Settings > Profile**:

- **Profile Image**: A 300x300 square image used in the sidebar and as the AI avatar. Click the image to change it. Default: `data/uploads/system/profile-default.png`
- **Nickname**: Displayed in the sidebar and browser tab. The brand name is always "Guaardvark" — the nickname is how users customize their instance.

The default profile image (`profile-default.png`) is included in backups and new installations automatically.

---

## License

License TBD — open-source release planned.
