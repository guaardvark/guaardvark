# Guaardvark

**Version 2.5.1** · [guaardvark.com](https://guaardvark.com)

A self-hosted AI platform that runs entirely on your hardware. Chat with your documents using RAG, generate images and video, manage files with a desktop-style UI, run autonomous code agents, self-improve with automated testing, sync across machines, and talk to your AI with voice — all through one unified interface backed by local LLMs via Ollama.

> **No cloud dependencies. No API keys required. Your data stays on your machine.**

<p align="center">
  <video src="https://github.com/guaardvark/guaardvark/raw/master/docs/screenshots/guaardvark-demo.mp4" autoplay loop muted playsinline width="100%"></video>
</p>

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/guaardvark/guaardvark/actions/workflows/ci.yml/badge.svg)](https://github.com/guaardvark/guaardvark/actions/workflows/ci.yml)
[![GitHub stars](https://img.shields.io/github/stars/guaardvark/guaardvark?style=social)](https://github.com/guaardvark/guaardvark/stargazers)
[![GitHub issues](https://img.shields.io/github/issues/guaardvark/guaardvark)](https://github.com/guaardvark/guaardvark/issues)
[![Sponsor](https://img.shields.io/badge/Sponsor-Guaardvark-ff69b4?logo=github-sponsors)](https://github.com/sponsors/guaardvark)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-yellow?logo=buy-me-a-coffee)](https://buymeacoffee.com/guaardvark)

---

## Screenshots

| Dashboard | Chat with RAG |
|:-:|:-:|
| ![Dashboard](docs/screenshots/dashboard-layout-a.png) | ![Chat](docs/screenshots/dashboard-cards-chat.png) |

| Video Generation | Plugin System |
|:-:|:-:|
| ![Video Gen](docs/screenshots/video-gen-page.png) | ![Plugins](docs/screenshots/plugins-page.png) |

| Document Manager | Image Editor |
|:-:|:-:|
| ![Documents](docs/screenshots/documents-page-001.png) | ![Image Editor](docs/screenshots/image-editor.png) |

| Image Generation | Media Gallery |
|:-:|:-:|
| ![Image Gen](docs/screenshots/image-gen-page.png) | ![Gallery](docs/screenshots/image-gallery-page.png) |

| Agents & Chat | Agent Tools |
|:-:|:-:|
| ![Agents](docs/screenshots/agents-page-duck-chat.png) | ![Tools](docs/screenshots/agent-tools-page.png) |

| Rules & Prompts | Settings & Models |
|:-:|:-:|
| ![Rules](docs/screenshots/rules-page.png) | ![Settings](docs/screenshots/settings-page-models.png) |

| Websites | Voice Settings |
|:-:|:-:|
| ![Websites](docs/screenshots/websites-page.png) | ![Voice](docs/screenshots/voice-settings.png) |

| Job Scheduler | System Dashboard |
|:-:|:-:|
| ![Jobs](docs/screenshots/job-scheduler-page.png) | ![System](docs/screenshots/system-dashboard.png) |

| Interconnector | Video Settings |
|:-:|:-:|
| ![Interconnector](docs/screenshots/interconnector-001.png) | ![Video Settings](docs/screenshots/video-settings.png) |

| Hacker Theme | Vader Theme |
|:-:|:-:|
| ![Hacker](docs/screenshots/hacker-theme.png) | ![Vader](docs/screenshots/vader-theme.png) |

---

## Highlights

- **Fully local** — All AI processing runs on your hardware via Ollama. No cloud, no API keys, no data leaving your machine.
- **One-command launch** — `./start.sh` handles everything: database, dependencies, builds, services. First run takes a few minutes; after that, it's instant.
- **Self-improving** — Guaardvark runs its own test suite, identifies failures, and dispatches an AI agent to fix them autonomously.
- **Multi-machine sync** — The Interconnector links multiple Guaardvark instances into a family that shares learnings, syncs code, and coordinates models.
- **Desktop-style file manager** — Drag-and-drop files, folder windows, right-click menus, properties panels — feels like a native OS, runs in the browser.
- **Swap models at runtime** — Switch LLMs and embedding models on the fly through the UI. GPU memory is managed automatically.

---

## Features

### AI & Chat
- **Conversational AI with RAG** — Chat grounded in your documents via hybrid BM25 + vector retrieval with per-project context isolation
- **Runtime model switching** — Change LLMs and embedding models through the UI without restarting. Old model unloaded before new one loads (no OOM crashes)
- **Streaming responses** — Real-time token streaming via Socket.IO with conversational fast-path (~700ms for simple messages)
- **KV cache optimization** — System prompt locked in Ollama KV cache between turns for faster follow-up responses
- **Voice interface** — Speech-to-text (Whisper.cpp) + text-to-speech (Piper TTS) with real-time streaming
- **Session isolation** — Per-project chat sessions with persistent history

### RAG & Indexing
- **Hybrid retrieval** — BM25 keyword + vector semantic search combined for best-of-both retrieval
- **Multiple embedding models** — Switch between lightweight (300M) and high-quality (4B+) embedding models via UI
- **Smart chunking** — Content-aware strategies: code files get AST-informed chunking, prose gets semantic splitting
- **Entity extraction** — Automatic entity and relationship indexing for structured knowledge
- **RAG Autoresearch** — Autonomous RAG optimization loop: evaluates retrieval quality with LLM-as-judge scoring, runs experiments to improve parameters, keeps improvements and reverts regressions

### Self-Improvement
- **Automated self-check** — Runs test suite, parses failures, dispatches AI agent to read code and fix bugs
- **Three modes** — Scheduled (periodic), Reactive (error-triggered), Directed (user-submitted tasks)
- **Live progress** — Real-time Socket.IO progress events with stage tracking (testing, analyzing, fixing, complete)
- **Codebase protection** — Lock switch prevents self-improvement from modifying code during development
- **Cross-machine learning** — Fixes are broadcast to other Guaardvark instances via the Interconnector

### Video Generation
- **State-of-the-art models** — Wan2.2 14B MoE (two-pass HighNoise/LowNoise), CogVideoX 2B/5B, Stable Video Diffusion — all running locally via ComfyUI
- **Text-to-Video and Image-to-Video** — generate video from text prompts or animate still images with motion direction control
- **Quality tiers** — Draft (raw output), Standard (2x FPS interpolation via RIFE 4.9), Cinema (2x FPS + 2x Real-ESRGAN upscaling)
- **Prompt enhancement** — five styles (Cinematic, Realistic, Artistic, Anime, None) automatically enrich prompts for better output
- **Advanced Editor** — one-click launch to ComfyUI's node editor, themed to match the Guaardvark UI, for full workflow customization
- **Model management** — download Wan2.2 GGUF checkpoints, CogVideoX weights, RIFE, and Real-ESRGAN models from HuggingFace with real-time progress bars

### Image Generation
- **Stable Diffusion** via Diffusers library — runs directly on your GPU with batch queue management
- **Auto-registration** — generated images are automatically added to the Documents/Files system under `/Images/`
- **Image library** — dedicated page with thumbnail grid, lightbox preview, keyboard navigation, batch operations

### Content Pipelines
- **Bulk content pipelines** — CSV/XML generation for content at scale
- **WordPress integration** — Pull content from WordPress sites, generate at scale, sync back

### Agent & Code Tools
- **ReACT agent loop** — Reads, writes, executes, and verifies code autonomously with iterative refinement
- **Tool execution guard** — Circuit breaker (2 failures blocks tool), duplicate call detection, fallback suggestions
- **Code editor** — Monaco Editor integration with syntax highlighting, multi-file editing
- **Browser automation** — Playwright-powered: navigate, click, fill forms, screenshot, extract content
- **Desktop automation** — pyautogui for mouse, keyboard, screen capture (opt-in, disabled by default)
- **MCP integration** — Connect to any MCP-compatible tool server

### File & Document Management
- **Desktop-metaphor UI** — Draggable folder icons, resizable windows, snap-to-grid, right-click context menus
- **Folder properties** — Link folders to clients, projects, and websites with cascading properties to all children
- **Code repository detection** — Mark folders as code repos with automatic language/framework detection
- **Drag-and-drop upload** — Drop files or entire folder trees; nested folder structures are preserved
- **Image library** — Thumbnail grids, lightbox preview with keyboard navigation, batch operations

### Multi-Machine (Interconnector)
- **Family sync** — Connect multiple Guaardvark instances running on different machines
- **Code sync** — Push/pull codebase changes between instances
- **Learning broadcast** — Self-improvement fixes propagate to all family members
- **Node management** — Master/client architecture with approval workflows

### Plugin System & GPU Management
- **Plugin-managed GPU services** — Ollama, ComfyUI, and GPU Embedding run as managed plugins with start/stop controls, health checks, and per-plugin log viewers
- **VRAM budget bar** — live nvidia-smi monitoring shows real-time VRAM usage, GPU utilization %, temperature, and per-plugin estimated allocation
- **GPU conflict detection** — exclusive-access plugins (Ollama vs ComfyUI) are auto-switched to prevent VRAM collisions
- **Model download management** — VideoModelsModal, ImageModelsModal, and VoiceModelsModal let you download models from HuggingFace with real-time progress tracking, accessible from Settings

### System
- **Dashboard** — Live status cards for model health, self-improvement, RAG autoresearch, GPU resources
- **Celery task system** — Background processing for long-running operations with live progress tracking
- **Four built-in themes** — Default, Musk, Hacker, Vader (all dark) with accent-colored UI
- **Profile customization** — Per-instance nickname and avatar image
- **CLI (`llx`)** — Full platform access via terminal with interactive REPL

---

## Quick Start

```bash
git clone https://github.com/guaardvark/guaardvark.git
cd guaardvark
./start.sh
```

That's it. The startup script handles everything on first run:
- Installs Python venv and Node dependencies
- Provisions PostgreSQL (creates database, user, credentials)
- Installs and starts Redis
- Builds Whisper.cpp for voice processing
- Runs database migrations
- Builds the frontend
- Starts all services

First run requires your system password once (to set up PostgreSQL). After that, launches are instant with no password needed.

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
| PostgreSQL | 14+ | Auto-installed by `start.sh` |
| Redis | 5.0+ | Auto-installed by `start.sh` |
| FFmpeg | any | Auto-installed for voice processing |
| Ollama | latest | Local LLM inference |
| CUDA GPU | — | Optional; accelerates generation and embeddings |
| ComfyUI | latest | Auto-managed plugin for video/image generation |

**Recommended hardware:**
- 16GB+ RAM
- NVIDIA GPU with 16GB VRAM (recommended for Wan2.2 video generation; 8GB minimum for image generation and smaller models)
- SSD for vector store performance

---

## Technology Stack

**Backend:** Flask 3.0 · SQLAlchemy + PostgreSQL · Celery + Redis · LlamaIndex + Ollama · PyTorch · Diffusers · ComfyUI (Wan2.2, CogVideoX, RIFE, Real-ESRGAN) · Whisper.cpp · Piper TTS · Ariadne (GraphQL)

**Frontend:** React 18 · Vite · Material-UI v5 · Zustand · Apollo Client · Monaco Editor · Socket.IO

**CLI:** Typer · Rich · httpx · python-socketio

---

## Architecture

```
Browser UI / llx CLI
        | HTTP + WebSocket
        v
Flask Application (port 5000)
  |-- 68 REST API blueprints
  |-- GraphQL (Ariadne)
  \-- Socket.IO (real-time streaming)
        |
  Service Layer (48 modules)
  |-- Agent Executor (ReACT loop)
  |-- RAG Pipeline (LlamaIndex)
  |-- Self-Improvement Engine
  |-- Generation Services
  \-- Interconnector Sync
        |
  +-----+----------------+
  v     v                v
PostgreSQL  Celery+Redis  Ollama/GPU
```

**Key flows:**

- **Chat + RAG:** Message -> intent routing -> hybrid retrieval (BM25 + vector) -> Ollama completion -> Socket.IO stream
- **Agent task:** Message -> ReACT loop -> tool calls (read/edit/execute code) -> iterative refinement -> verification
- **Self-check:** Trigger -> pytest run -> parse failures -> agent fix attempts -> broadcast learnings
- **Image generation:** Prompt -> Celery job -> Diffusers GPU pipeline -> auto-register to file system
- **Video generation:** Prompt -> enhance (style suffix) -> ComfyUI workflow (Wan2.2/CogVideoX) -> RIFE interpolation -> Real-ESRGAN upscale -> output
- **File indexing:** Upload -> parse -> chunk (content-aware) -> embed -> LlamaIndex vector store

---

## Project Structure

```
guaardvark/
|-- backend/
|   |-- api/            # 68 Flask blueprint modules
|   |-- services/       # 48 business logic modules
|   |-- tools/          # Agent-callable tools (code, browser, voice, web)
|   |-- utils/          # 76 helpers (RAG, context, progress, CSV)
|   |-- tasks/          # Celery background tasks
|   |-- migrations/     # Alembic database migrations
|   |-- tests/          # 60 tests (unit / integration / system)
|   |-- app.py          # Flask application factory
|   |-- models.py       # SQLAlchemy ORM models
|   \-- config.py       # Configuration + path resolution
|-- frontend/
|   |-- src/
|   |   |-- pages/      # 31 page components
|   |   |-- components/ # 129 UI components
|   |   |-- stores/     # Zustand state management
|   |   |-- hooks/      # Custom React hooks
|   |   \-- api/        # 39 API service modules
|   \-- dist/           # Production build
|-- cli/                # llx CLI tool
|-- plugins/            # GPU service plugins (ollama, comfyui, gpu_embedding)
|-- scripts/            # Utilities and system manager
|-- data/               # Runtime data (gitignored)
\-- start.sh / stop.sh  # Service management
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
DATABASE_URL=postgresql://...          # Auto-generated by start.sh
REDIS_URL=redis://localhost:6379/0
GUAARDVARK_ENHANCED_MODE=true          # Enhanced context features
GUAARDVARK_RAG_DEBUG=false             # RAG debug endpoints
GUAARDVARK_SKIP_MIGRATIONS=0
GUAARDVARK_BROWSER_AUTOMATION=true
GUAARDVARK_DESKTOP_AUTOMATION=false    # Disabled by default (security)
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

## Logs

| File | Contents |
|------|---------|
| `logs/backend.log` | Flask application |
| `logs/celery_main.log` | Main Celery worker |
| `logs/celery_training.log` | Training/GPU worker |
| `logs/frontend.log` | Vite dev server |
| `logs/setup.log` | Dependency installation |
| `logs/test_results/` | Test execution output |

---

## Support the Project

Guaardvark is built with love by a solo developer. If it's useful to you, consider supporting continued development:

- [Ko-fi](https://ko-fi.com/albenze) (zero fees!)
- [GitHub Sponsors](https://github.com/sponsors/guaardvark)
- [PayPal](https://paypal.me/albenze)
- [Venmo](https://venmo.com/albenze)
- [Cash App](https://cash.app/$DeanAlbenze)

Star the repo if you find it interesting — it helps with visibility!

---

## Contributing

We welcome contributions! See the [Contributing Guide](CONTRIBUTING.md) to get started.

Looking for something to work on? Check out issues labeled [`good first issue`](https://github.com/guaardvark/guaardvark/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22).

---

## License

[MIT License](LICENSE) — Copyright (c) 2025-2026 Albenze, Inc.
