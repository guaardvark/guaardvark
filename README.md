# Guaardvark

**Version 2.5.2** · [guaardvark.com](https://guaardvark.com)

The self-hosted AI workstation. Agents that see your screen and use your apps. RAG that reads your documents. Image and video generation on your GPU. Voice interface. Self-improving code. All running locally — your data never leaves your machine.

<p align="center">
  <img src="docs/screenshots/guaardvark-demo.gif" alt="Guaardvark Demo" width="100%">
</p>

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/guaardvark/guaardvark/actions/workflows/ci.yml/badge.svg)](https://github.com/guaardvark/guaardvark/actions/workflows/ci.yml)
[![GitHub stars](https://img.shields.io/github/stars/guaardvark/guaardvark?style=social)](https://github.com/guaardvark/guaardvark/stargazers)
[![GitHub issues](https://img.shields.io/github/issues/guaardvark/guaardvark)](https://github.com/guaardvark/guaardvark/issues)
[![Sponsor](https://img.shields.io/badge/Sponsor-Guaardvark-ff69b4?logo=github-sponsors)](https://github.com/sponsors/guaardvark)

```bash
git clone https://github.com/guaardvark/guaardvark.git && cd guaardvark && ./start.sh
```

One command. Installs everything. Starts all services. Done.

### AI-Generated Film — Made Entirely with Guaardvark

Every frame generated on a single desktop GPU. No cloud. No stock footage. No API keys.

[![Gotham Rising — AI-Generated Short Film](https://img.youtube.com/vi/8MdtM3HurJo/maxresdefault.jpg)](https://www.youtube.com/watch?v=8MdtM3HurJo)

---

## What Makes This Different

### Agents That Can See and Act

Guaardvark agents don't just generate text — they control a real virtual desktop. They see the screen through vision models, move the mouse, click buttons, type text, navigate browsers, and report what they find. 25+ deterministic action recipes for instant browser control, with a unified vision brain that sees and decides in a single inference call.

| Agent Control | Agent Tools |
|:-:|:-:|
| ![Agents](docs/screenshots/agents-v2.jpg) | ![Tools](docs/screenshots/tools-v2.jpg) |

- **Unified vision brain** — qwen3-vl sees the screen and decides the next action in one call (0.5s per step)
- **Recipe engine** — 25 deterministic browser actions (navigate, tabs, scroll, search, find) execute instantly from a JSON library
- **Obstacle detection** — automatically handles popups, permission dialogs, and notification bars with thinking model escalation
- **Self-QA sweep** — agent navigates every page of its own UI and reports what's working and what's broken
- **Live agent monitor** — real-time SEE/THINK/ACT transcript of every decision the agent makes
- **Integrated screen viewer** — watch the agent's virtual display live from any page, with popup window mode

### Self-Improving AI

The system runs its own test suite, identifies failures, dispatches an AI agent to read the code and fix the bugs, verifies the fix, and broadcasts the learning to other instances. No human in the loop.

- **Three modes** — Scheduled (every 6 hours), Reactive (triggered by repeated 500 errors), Directed (manual tasks)
- **Guardian review** — code changes are reviewed before applying
- **Verification loop** — re-runs tests after every fix to confirm it worked
- **Pending fixes queue** — stage, review, approve, or reject proposed changes
- **Cross-machine learning** — fixes propagate to all connected instances via the Interconnector

### Video Generation Pipeline

State-of-the-art video generation running entirely on your GPU. No cloud APIs, no per-minute billing, no content restrictions.

| Video Generation | Plugin System |
|:-:|:-:|
| ![Video Gen](docs/screenshots/video-gen-v2.jpg) | ![Plugins](docs/screenshots/plugins-v2.jpg) |

- **Wan2.2 14B MoE** — two-pass HighNoise/LowNoise pipeline for cinematic quality
- **CogVideoX** — 2B and 5B parameter models for fast generation
- **Quality tiers** — Draft (raw), Standard (2x FPS via RIFE 4.9), Cinema (2x FPS + 2x Real-ESRGAN upscale)
- **Text-to-Video and Image-to-Video** — generate from prompts or animate still images
- **Prompt enhancement** — five styles (Cinematic, Realistic, Artistic, Anime, None)
- **Batch processing** — queue multiple videos with background Celery workers
- **ComfyUI integration** — one-click launch to the node editor for custom workflows

### RAG That Actually Works

Chat grounded in your documents. Upload files, build a knowledge base, and ask questions. The AI reads and understands your content — not just keyword matching.

| Chat with RAG | Document Manager |
|:-:|:-:|
| ![Chat](docs/screenshots/chat-v2.jpg) | ![Documents](docs/screenshots/documents-v2.jpg) |

- **Hybrid retrieval** — BM25 keyword + vector semantic search combined
- **Smart chunking** — code files get AST-informed chunking, prose gets semantic splitting
- **Multiple embedding models** — switch between lightweight (300M) and high-quality (4B+) via UI
- **RAG Autoresearch** — autonomous optimization loop that experiments with parameters, keeps improvements, reverts regressions
- **Entity extraction** — automatic entity and relationship indexing
- **Per-project isolation** — each project has its own knowledge base and chat context

---

## Full Feature Set

### AI & Chat
- Runtime model switching — swap LLMs through the UI, GPU memory managed automatically
- Streaming responses via Socket.IO with conversational fast-path (~700ms)
- Voice interface — Whisper.cpp STT + Piper TTS with narration and voiceover
- Tool call transparency — see every tool call, parameters, results, and timing inline in chat
- Session history with search, grouping, previews, and persistent tool call data

### Image Generation
- Stable Diffusion via Diffusers library — batch queue, auto-registration to file system
- Image library with thumbnail grid, lightbox preview, keyboard navigation, batch operations

### Agent & Code Tools
- **50 registered tools** across 10 categories — web search, browser automation, code execution, file management, media control, MCP integration
- **9 specialized agents** — code assistant, content creator, research agent, browser automation, vision control, and more
- **ReACT agent loop** — iterative reasoning, action, observation with tool execution guard and circuit breaker
- **Monaco code editor** — built-in IDE with AI-powered explain, fix, and generate via right-click context menu
- **Self-demo system** — automated feature tour with screen recording and TTS narration

### File & Document Management
- Desktop-style UI — draggable folder icons, resizable windows, right-click context menus
- Drag-and-drop upload preserving folder structures
- Folder properties linked to clients, projects, and websites

### Multi-Machine Sync (Interconnector)
- Connect multiple instances into a family that shares code, learnings, and model configs
- Master/client architecture with approval workflows and pre-sync backups

### Plugin System & GPU Management
- Ollama, ComfyUI, GPU Embedding run as managed plugins with health checks and log viewers
- Live VRAM monitoring with GPU conflict detection
- Model download management from HuggingFace with progress tracking

### System
- Dashboard with live status cards for model health, GPU, self-improvement, RAG
- Celery background task system with live progress
- Six built-in themes
- CLI with interactive REPL — `guaardvark status`, `guaardvark chat`, `guaardvark search`

---

## Screenshots

| Dashboard | Image Generation |
|:-:|:-:|
| ![Dashboard](docs/screenshots/dashboard-v2.jpg) | ![Images](docs/screenshots/images-v2.jpg) |

| Code Editor | Projects |
|:-:|:-:|
| ![Code Editor](docs/screenshots/code-editor-v2.jpg) | ![Projects](docs/screenshots/projects-v2.jpg) |

| Rules & Prompts | Settings |
|:-:|:-:|
| ![Rules](docs/screenshots/rules-v2.jpg) | ![Settings](docs/screenshots/settings-v2.jpg) |

| Clients | Notes |
|:-:|:-:|
| ![Clients](docs/screenshots/clients-v2.jpg) | ![Notes](docs/screenshots/notes-v2.jpg) |

---

## Quick Start

```bash
git clone https://github.com/guaardvark/guaardvark.git
cd guaardvark
./start.sh
```

First run handles everything: Python venv, Node dependencies, PostgreSQL, Redis, Whisper.cpp, database migrations, frontend build, and all services. Requires your system password once for PostgreSQL setup.

| Service | URL |
|---------|-----|
| Web UI | http://localhost:5173 |
| API | http://localhost:5000 |
| Health Check | http://localhost:5000/api/health |

```bash
./start.sh                    # Full startup with health checks
./start.sh --fast             # Skip dependency checks
./start.sh --test             # Health diagnostics
./stop.sh                     # Stop all services
```

---

## Requirements

| Dependency | Version | Notes |
|-----------|---------|-------|
| Python | 3.12+ | Backend |
| Node.js | 20+ | Frontend build |
| PostgreSQL | 14+ | Auto-installed |
| Redis | 5.0+ | Auto-installed |
| Ollama | latest | Local LLM inference |
| CUDA GPU | 8GB+ VRAM | 16GB recommended for video generation |

---

## Architecture

```
Browser / CLI
    | HTTP + WebSocket
    v
Flask (68 REST blueprints + GraphQL + Socket.IO)
    |
Service Layer (48 modules)
|-- Agent Executor (ReACT loop + 50 tools + vision brain)
|-- RAG Pipeline (LlamaIndex + hybrid retrieval)
|-- Self-Improvement Engine (detect → fix → verify → broadcast)
|-- Generation Services (image, video, voice, content)
|-- Interconnector (multi-machine sync)
\-- Agent Control (virtual screen + servo controller + recipes)
    |
+---+---+---+
v   v   v   v
PostgreSQL  Redis/Celery  Ollama  Virtual Display
```

**Frontend:** React 18 · Vite · Material-UI v5 · Zustand · Apollo Client · Monaco Editor · Socket.IO

---

## CLI

```bash
guaardvark status                       # System dashboard
guaardvark chat "explain this codebase" # Chat with RAG
guaardvark search "query"              # Semantic search
guaardvark files upload report.pdf     # Upload and index
guaardvark generate csv "50 blog ideas" --output ideas.csv
guaardvark                             # Interactive REPL
```

---

## Support the Project

Guaardvark is built with love by a solo developer. If it's useful to you:

- [Ko-fi](https://ko-fi.com/albenze) (zero fees!)
- [GitHub Sponsors](https://github.com/sponsors/guaardvark)
- [PayPal](https://paypal.me/albenze)

Star the repo if you find it interesting — it helps with visibility.

---

## Contributing

We welcome contributions! See the [Contributing Guide](CONTRIBUTING.md) to get started.

Looking for something to work on? Check out issues labeled [`good first issue`](https://github.com/guaardvark/guaardvark/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22).

---

## License

[MIT License](LICENSE) — Copyright (c) 2025-2026 Albenze, Inc.
