Here's the improved README content in Markdown format:

# Guaardvark

**Version 2.5.1** · [guaardvark.com](https://guaardvark.com)

## Overview

Guaardvark is a self-hosted AI platform that runs entirely on your hardware. No cloud, no API keys, and your data never leaves your machine. This platform provides a comprehensive set of features, including conversational AI, image and video generation, file management, and more.

## Features

### AI & Chat

* **Conversational AI with RAG**: Chat grounded in your documents via hybrid BM25 + vector retrieval with per-project context isolation
* **Runtime model switching**: Change LLMs and embedding models through the UI without restarting. Old model unloaded before new one loads (no OOM crashes)
* **Streaming responses**: Real-time token streaming via Socket.IO with conversational fast-path (~700ms for simple messages)
* **KV cache optimization**: System prompt locked in Ollama KV cache between turns for faster follow-up responses
* **Voice interface**: Speech-to-text (Whisper.cpp) + text-to-speech (Piper TTS) with real-time streaming
* **Session isolation**: Per-project chat sessions with persistent history

### RAG & Indexing

* **Hybrid retrieval**: BM25 keyword + vector semantic search combined for best-of-both retrieval
* **Multiple embedding models**: Switch between lightweight (300M) and high-quality (4B+) embedding models via UI
* **Smart chunking**: Content-aware strategies: code files get AST-informed chunking, prose gets semantic splitting
* **Entity extraction**: Automatic entity and relationship indexing for structured knowledge
* **RAG Autoresearch**: Autonomous RAG optimization loop: evaluates retrieval quality with LLM-as-judge scoring, runs experiments to improve parameters, keeps improvements and reverts regressions

### Self-Improvement

* **Automated self-check**: Runs test suite, parses failures, dispatches AI agent to read code and fix bugs
* **Three modes**: Scheduled (periodic), Reactive (error-triggered), Directed (user-submitted tasks)
* **Live progress**: Real-time Socket.IO progress events with stage tracking (testing, analyzing, fixing, complete)
* **Codebase protection**: Lock switch prevents self-improvement from modifying code during development
* **Cross-machine learning**: Fixes are broadcast to other Guaardvark instances via the Interconnector

### Video Generation

* **State-of-the-art models**: Wan2.2 14B MoE (two-pass HighNoise/LowNoise), CogVideoX 2B/5B, Stable Video Diffusion — all running locally via ComfyUI
* **Text-to-Video and Image-to-Video**: generate video from text prompts or animate still images with motion direction control
* **Quality tiers**: Draft (raw output), Standard (2x FPS interpolation via RIFE 4.9), Cinema (2x FPS + 2x Real-ESRGAN upscaling)
* **Prompt enhancement**: five styles (Cinematic, Realistic, Artistic, Anime, None) automatically enrich prompts for better output
* **Advanced Editor**: one-click launch to ComfyUI's node editor, themed to match the Guaardvark UI, for full workflow customization
* **Model management**: download Wan2.2 GGUF checkpoints, CogVideoX weights, RIFE, and Real-ESRGAN models from HuggingFace with real-time progress bars

### Image Generation

* **Stable Diffusion** via Diffusers library — runs directly on your GPU with batch queue management
* **Auto-registration**: generated images are automatically added to the Documents/Files system under `/Images/`
* **Image library**: dedicated page with thumbnail grid, lightbox preview, keyboard navigation, batch operations

### Content Pipelines

* **Bulk content pipelines**: CSV/XML generation for content at scale
* **WordPress integration**: Pull content from WordPress sites, generate at scale, sync back

### Agent & Code Tools

* **ReACT agent loop**: Reads, writes, executes, and verifies code autonomously with iterative refinement
* **Tool execution guard**: Circuit breaker (2 failures blocks tool), duplicate call detection, fallback suggestions
* **Code editor**: Monaco Editor integration with syntax highlighting, multi-file editing
* **Browser automation**: Playwright-powered: navigate, click, fill forms, screenshot, extract content
* **Desktop automation**: pyautogui for mouse, keyboard, screen capture (opt-in, disabled by default)
* **MCP integration**: Connect to any MCP-compatible tool server

### File & Document Management

* **Desktop-metaphor UI**: Draggable folder icons, resizable windows, snap-to-grid, right-click context menus
* **Folder properties**: Link folders to clients, projects, and websites with cascading properties to all children
* **Code repository detection**: Mark folders as code repos with automatic language/framework detection
* **Drag-and-drop upload**: Drop files or entire folder trees; nested folder structures are preserved
* **Image library**: Thumbnail grids, lightbox preview with keyboard navigation, batch operations

### Multi-Machine (Interconnector)

* **Family sync**: Connect multiple Guaardvark instances running on different machines
* **Code sync**: Push/pull codebase changes between instances
* **Learning broadcast**: Self-improvement fixes propagate to all family members
* **Node management**: Master/client architecture with approval workflows

### Plugin System & GPU Management

* **Plugin-managed GPU services**: Ollama, ComfyUI, and GPU Embedding run as managed plugins with start/stop controls, health checks, and per-plugin log viewers
* **VRAM budget bar**: live nvidia-smi monitoring shows real-time VRAM usage, GPU utilization %, temperature, and per-plugin estimated allocation
* **GPU conflict detection**: exclusive-access plugins (Ollama vs ComfyUI) are auto-switched to prevent VRAM collisions
* **Model download management**: VideoModelsModal, ImageModelsModal, and VoiceModelsModal let you download models from HuggingFace with real-time progress tracking, accessible from Settings

### System

* **Dashboard**: Live status cards for model health, self-improvement, RAG autoresearch, GPU resources
* **Celery task system**: Background processing for long-running operations with live progress tracking
* **Four built-in themes**: Default, Musk, Hacker, Vader (all dark) with accent-colored UI
* **Profile customization**: Per-instance nickname and avatar image
* **CLI**: Full platform access via terminal with interactive REPL

## Quick Start

```bash
git clone https://github.com/guaardvark/guaardvark.git
cd guaardvark
./start.sh
```

The startup script handles everything on first run: Python venv, Node dependencies, PostgreSQL, Redis, Whisper.cpp, database migrations, frontend build, and all services. First run requires your system password once (to set up PostgreSQL). After that, launches are instant.

| Service | URL |
|---------|-----|
| Web UI | http://localhost:5173 |
| API | http://localhost:5000 |
| Health Check | http://localhost:5000/api/health |

```bash
./start.sh                    # Full startup with health checks
./start.sh --fast             # Skip dependency checks and builds
./start.sh --test             # Comprehensive health diagnostics
./start.sh --skip-migrations  # Skip migration pre-flight
./start.sh --no-voice         # Skip voice API health check
./stop.sh                     # Stop all services
```

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

## Technology Stack

**Backend:** Flask 3.0 · SQLAlchemy + PostgreSQL · Celery + Redis · LlamaIndex + Ollama · PyTorch · Diffusers · ComfyUI (Wan2.2, CogVideoX, RIFE, Real-ESRGAN) · Whisper.cpp · Piper TTS · Ariadne (GraphQL)

**Frontend:** React 18 · Vite · Material-UI v5 · Zustand · Apollo Client · Monaco Editor · Socket.IO

**CLI:** Typer · Rich · httpx · python-socketio

## Architecture

```
Browser UI / guaardvark CLI
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

- **Chat + RAG:** Message -> intent routing -> hybrid retrieval (BM25 + vector)