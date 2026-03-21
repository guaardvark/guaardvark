# Guaardvark — Full Capabilities List

**Version 2.5.1** · [guaardvark.com](https://guaardvark.com)

This document is a comprehensive reference of everything Guaardvark can do. For a quick overview, see [README.md](README.md).

---

## Table of Contents

- [AI Chat & Conversation](#ai-chat--conversation)
- [RAG & Document Intelligence](#rag--document-intelligence)
- [Self-Improvement Engine](#self-improvement-engine)
- [RAG Autoresearch](#rag-autoresearch)
- [Agent & Code Tools](#agent--code-tools)
- [Image & Video Generation](#image--video-generation)
  - [Video Generation](#video-generation)
  - [Image Generation](#image-generation)
- [Content Generation Pipelines](#content-generation-pipelines)
- [Voice Interface](#voice-interface)
- [File & Document Management](#file--document-management)
- [Dashboard & Monitoring](#dashboard--monitoring)
- [Settings & Configuration](#settings--configuration)
- [Multi-Machine Interconnector](#multi-machine-interconnector)
- [WordPress Integration](#wordpress-integration)
- [Automation Tools](#automation-tools)
- [CLI (llx)](#cli-llx)
- [Plugin System](#plugin-system)
- [System Architecture](#system-architecture)
- [Startup & Operations](#startup--operations)

---

## AI Chat & Conversation

Guaardvark's chat system is the primary interface for interacting with your AI. Two pipelines handle different use cases:

### Core Chat
- **Streaming responses** via Socket.IO — tokens appear in real-time as the model generates
- **Conversational fast-path** — simple messages (greetings, follow-ups) skip RAG and tool routing entirely, responding in ~700ms instead of 25+ seconds
- **Intent routing** — automatically detects whether a message needs RAG retrieval, tool use, or a direct conversational response
- **Per-project sessions** — chat context is isolated by project; switching projects gives you a clean context with that project's documents
- **Session persistence** — conversation history persists across page reloads and browser sessions
- **System prompts (Rules)** — customizable system prompts that shape AI behavior, manageable via the Rules page
- **Multi-model support** — switch between any Ollama model at runtime without restarting

### Model Management
- **Runtime model switching** — change the active LLM through Settings; the old model is unloaded from VRAM before the new one loads (prevents OOM)
- **Embedding model switching** — swap embedding models via dropdown; triggers re-indexing confirmation since vector spaces are incompatible across models
- **Live health detection** — dashboard probes Ollama on every request to show actual model availability (not a stale startup flag)
- **KV cache optimization** — `num_keep: -1` locks the system prompt prefix in Ollama's KV cache, making follow-up turns faster
- **GPU VRAM monitoring** — real-time VRAM usage bar with loaded model indicators in Settings

---

## RAG & Document Intelligence

Retrieval-Augmented Generation grounds chat responses in your actual documents.

### Retrieval Pipeline
- **Hybrid search** — BM25 keyword matching + vector semantic search, combined for best results
- **Per-project indexes** — each project maintains its own vector store; global index for unassigned documents
- **Content-aware chunking** — code files use AST-informed strategies; prose uses semantic splitting
- **Entity extraction** — automatic identification of entities (people, orgs, concepts) and their relationships
- **Metadata indexing** — file metadata (type, size, language, framework) stored alongside content for filtered retrieval

### Embedding Models
- **Multiple model support** — switch between lightweight (embeddinggemma 300M) and high-quality (qwen3-embedding 4B/8B) models
- **Full-precision option** — BF16 embeddings available for maximum quality
- **Query-time embedding** — every RAG search query is embedded with the same model for consistent vector space matching

### Indexing
- **Automatic on upload** — files are indexed when uploaded through the UI or API
- **Bulk indexing** — "Index All" button processes the entire document library
- **Code-specific indexing** — detects programming languages, extracts imports/classes/functions, chunks by logical boundaries
- **Progress tracking** — real-time progress bar during indexing operations via Socket.IO

---

## Self-Improvement Engine

Guaardvark can autonomously test itself, find bugs, and fix them.

### Three Modes
1. **Scheduled** — periodic test suite runs (configurable interval) with automatic fix attempts
2. **Reactive** — error tracking with threshold-based self-healing (N errors in M minutes triggers a fix)
3. **Directed** — user-submitted improvement tasks dispatched to the code agent

### How It Works
1. Runs `pytest` on configured test files
2. Parses `FAILED` lines from output (with fallback regex for edge cases)
3. Dispatches the `code_assistant` agent to read tests, understand expectations, read source, and fix bugs
4. Records all changes and broadcasts learnings to other machines via Interconnector

### Safety
- **Codebase lock** — toggle in Settings prevents self-improvement from modifying any files
- **Return code verification** — checks pytest exit code, not just parsed failures
- **Run history** — all runs recorded in database with status, duration, changes made, and test results

### Live Progress
- **Socket.IO events** at each stage: `starting`, `testing`, `analyzed`, `fixing`, `complete`, `error`
- **Dashboard card** shows real-time progress bar with color-coded stages
- **Run button** disabled while a check is in progress

---

## RAG Autoresearch

An autonomous optimization loop that continuously improves RAG retrieval quality.

### How It Works
1. **Eval harness** — generates evaluation pairs (query + expected answer) and scores retrieval with LLM-as-judge (relevance, grounding, completeness)
2. **Experiment agent** — proposes parameter changes (chunk size, overlap, top-k, similarity threshold)
3. **Orchestrator** — runs experiments, compares scores, keeps improvements, reverts regressions
4. **Phase system** — Phase 1 (query-time params), Phase 2 (index-time params), Phase 3 (model-level)

### Features
- **Celery Beat scheduling** — idle detection triggers experiments when system isn't busy
- **Crash protection** — 3 consecutive failures automatically stops the loop
- **Dashboard card** — shows experiment status, history, and current optimization parameters
- **Settings integration** — configure experiment limits, scoring thresholds, and scheduling

---

## Agent & Code Tools

A ReACT-loop agent that can autonomously work with code and the system.

### Agent Capabilities
- **Read files** — examine any file in the project
- **Edit code** — precise text replacement with verification
- **List files** — explore directory structure (configurable depth up to 5 levels)
- **Execute code** — run Python/shell commands and inspect output
- **Web search** — search the internet for information
- **Browser automation** — navigate websites, fill forms, take screenshots

### Safety Features
- **Circuit breaker** — after 2 consecutive failures, a tool is temporarily blocked
- **Duplicate detection** — hash-based detection prevents the agent from making identical tool calls
- **Fallback suggestions** — when a tool fails, the system suggests alternative approaches
- **Iteration limits** — configurable maximum iterations per agent run

### Code Editor Page
- **Monaco Editor** — VS Code-quality editing in the browser with syntax highlighting for 50+ languages
- **Multi-file tabs** — open and edit multiple files simultaneously
- **File tree** — browse project structure in a sidebar

---

## Image & Video Generation

### Image Generation
- **Stable Diffusion** via Diffusers library — runs directly on your GPU
- **Batch generation** — queue multiple prompts with different parameters
- **Auto-registration** — generated images are automatically added to the Documents/Files system under `/Images/`
- **Celery background processing** — generation runs as async jobs with progress tracking
- **Image library** — dedicated page with thumbnail grid, lightbox preview, keyboard navigation, batch operations
- **Image model management** — ImageModelsModal for downloading and managing Stable Diffusion checkpoints

### Video Generation

Full video generation pipeline running locally via ComfyUI with multiple model backends.

#### Supported Models
- **Wan2.2 14B MoE** — state-of-the-art text-to-video model using GGUF-quantized weights. Two-pass generation: HighNoise pass for the first half of steps, LowNoise pass for the second half. Produces high-quality 720p video at 16 FPS
- **CogVideoX 2B / 5B** — THUDM's text-to-video diffusion models. Lighter weight alternative to Wan2.2, good for faster iteration
- **CogVideoX 5B I2V** — image-to-video variant that animates a still image with text-guided motion
- **Stable Video Diffusion (SVD)** — image-to-video generation for short clips from reference images

#### Generation Modes
- **Text-to-Video** — describe a scene in natural language and generate video from scratch
- **Image-to-Video** — upload a reference image and animate it with motion direction prompts
- **Batch generation** — queue multiple prompts with different parameters for unattended rendering

#### Quality Tiers (Post-Processing)
- **Draft** — raw model output, fastest turnaround
- **Standard** — 2x FPS frame interpolation via RIFE 4.9 (e.g., 16 FPS to 32 FPS) for smoother motion
- **Cinema** — 2x FPS interpolation + 2x spatial upscaling via Real-ESRGAN for maximum quality output

#### Frame Interpolation (RIFE 4.9)
- Doubles or quadruples the frame rate of generated video using optical flow
- Integrated directly into the ComfyUI workflow as a post-processing node
- Configurable multiplier: 2x (double FPS) or 4x (quadruple FPS)

#### Prompt Enhancement
- Automatically enriches user prompts with quality and style descriptors before generation
- Five styles available: **Cinematic** (film grain, shallow DOF, color grading), **Realistic** (photorealistic, 8K detail), **Artistic** (painterly, vivid colors), **Anime** (cel shaded, dynamic poses), **None** (raw prompt)
- Style-specific negative prompts target technical defects without content restrictions
- No LLM calls required — pure string concatenation for instant enhancement

#### Video UI
- **Preset-driven interface** — quality presets (Fast 10-step / Standard 30-step / High 40-step / Maximum 50-step), duration presets, motion presets, and aspect ratio presets
- **Real-time progress** — live progress bar with percentage and step count during generation
- **Video gallery** — browse, preview, rename, download, and delete generated videos
- **Advanced Editor** — one-click launch to ComfyUI's full node-based workflow editor, themed with the Guaardvark color scheme

#### Model Management (VideoModelsModal)
- Browse all available video models with installed/available status
- Download models from HuggingFace with real-time progress bars showing speed (MB/s), downloaded/total size
- Models include: Wan2.2 GGUF checkpoints (HighNoise + LowNoise), Wan VAE, CogVideoX weights, RIFE 4.9, Real-ESRGAN 2x
- Accessible from the Video Generator page and Settings page

---

## Content Generation Pipelines

### Bulk Generation
- **CSV generation** — generate structured data (blog ideas, product descriptions, etc.) as downloadable CSV
- **XML generation** — structured XML output for content management systems
- **Template-based** — customizable generation templates

### File Generation
- **Multi-format** — generate documents in various formats based on prompts
- **Project-scoped** — generated content can be assigned to projects and clients

---

## Voice Interface

### Speech-to-Text
- **Whisper.cpp** — compiled from source on first startup for optimal performance
- **Real-time transcription** — stream audio from microphone, get text in real-time
- **Auto-install** — `cmake` and build tools are automatically installed if missing

### Text-to-Speech
- **Piper TTS** — local neural text-to-speech with multiple voice models
- **Streaming output** — audio generated and streamed as the response is produced

---

## File & Document Management

The Documents page provides a desktop-style file management experience.

### Desktop Metaphor
- **Folder icons** — folders appear as draggable icons on a desktop surface
- **Folder windows** — double-click to open a folder as a resizable, draggable window
- **Window states** — folded (icon), minimized (title bar), maximized (full window)
- **Snap-to-grid** — icons align to a grid when dragged
- **Z-index management** — click a window to bring it to front
- **Window arrangement** — auto-arrange icons and windows with toolbar buttons

### File Operations
- **Drag-and-drop upload** — drop files or entire folder trees; nested structures preserved
- **Upload button** — quick upload from the toolbar
- **Right-click context menu** — rename, delete, move, properties, index
- **Folder creation** — create new folders from context menu or toolbar
- **File thumbnails** — image files show thumbnail previews

### Folder Properties
- **Entity links** — assign folders to clients, projects, and websites
- **Cascading properties** — folder properties automatically apply to all contained files and subfolders
- **Tags and notes** — add metadata to folders for organization
- **Code repository toggle** — mark folders as code repos with auto-detected languages and frameworks
- **Persistent storage** — folder properties saved to database and pre-populated when reopened

### Breadcrumb Navigation
- **Path breadcrumbs** — click any segment to navigate up the folder tree
- **Root navigation** — Home button returns to desktop view

---

## Dashboard & Monitoring

The dashboard provides a live overview of system status.

### Status Cards
- **Family & Self-Improvement** — Uncle Claude status, self-improvement toggle, recent run history, token budget, live progress bar during self-checks
- **RAG Autoresearch** — experiment status, history, optimization parameters
- **Semantic Search** — quick search across all indexed documents

### System Health
- **Model status** — active model name and loading state shown in page headers
- **LLM ready indicator** — live Ollama probe (not a stale startup flag)
- **GPU resources** — VRAM usage bar with loaded model chips in Settings
- **Plugins page** — dedicated GPU service management page with VRAM budget bar, per-plugin controls, log viewer, and conflict detection

---

## Settings & Configuration

Centralized configuration across six sections.

### System
- **Profile** — custom name and avatar image for your instance
- **Chat model** — select active LLM from installed Ollama models
- **Embedding model** — select embedding model with size indicators
- **GPU resource bar** — live VRAM monitoring
- **Model management** — VideoModelsModal, ImageModelsModal, and VoiceModelsModal for downloading models from HuggingFace with real-time progress

### A.I.
- **Enhanced Context** — toggle enhanced context features
- **Advanced RAG** — toggle advanced retrieval features
- **RAG Debug** — enable debug endpoints for retrieval inspection
- **RAG Autoresearch** — configure experiment parameters and scheduling
- **Self-Improvement** — enable/disable, run manual checks, view history
- **Codebase Protection** — lock/unlock code modification by AI

### Voice
- **Voice chat toggle** — enable/disable voice interface
- **Whisper installation** — one-click install/reinstall of Whisper.cpp
- **Voice model selection** — choose TTS voice model

### Integrations
- **Web search** — enable/disable web search tool
- **Interconnector** — toggle and configure multi-machine sync
- **Pending updates banner** — shows when Interconnector has available updates

### Appearance
- **Theme selection** — four dark themes with accent colors
- **View modes** — customize default layouts

### Maintenance
- **Cache clearing** — purge Python cache folders
- **System diagnostics** — Basic, Quick, and Full diagnostic modes
- **Test suite** — run backend tests from the UI
- **Backup/restore** — system configuration backup

---

## Multi-Machine Interconnector

Connect multiple Guaardvark instances into a coordinated family.

### Architecture
- **Master/Client model** — one master node, multiple client nodes
- **API key authentication** — secure communication between nodes
- **Approval workflows** — master can approve/deny sync requests

### Sync Capabilities
- **Code sync** — push/pull codebase changes between instances
- **Data sync** — synchronize entities (documents, projects, clients) across machines
- **Learning broadcast** — self-improvement fixes automatically shared with family members
- **Node registration** — clients register with master, reporting capabilities and status

### Management
- **Toggle from Settings** — enable/disable without opening configuration modal
- **Node status dashboard** — see all connected nodes, their status, and capabilities
- **Sync history** — track what was synced, when, and between which nodes

---

## WordPress Integration

### Content Management
- **Site management** — add and manage multiple WordPress sites
- **Content pulling** — import pages and posts from WordPress
- **Bulk generation** — generate content at scale for WordPress sites
- **Content sync** — push generated content back to WordPress

### Pages
- **WordPress Pages page** — dedicated interface for managing WordPress page content
- **WordPress Sites page** — manage site connections and credentials

---

## Automation Tools

| Tool | Backend | Description |
|------|---------|-------------|
| Browser | Playwright | Navigate, click, fill forms, screenshot, extract content |
| Desktop | pyautogui | Mouse, keyboard, screen capture, window management |
| MCP | Protocol | Connect to any MCP-compatible tool server |

```bash
GUAARDVARK_BROWSER_AUTOMATION=true
GUAARDVARK_DESKTOP_AUTOMATION=true   # Off by default (security)
GUAARDVARK_MCP_ENABLED=true
```

---

## CLI (llx)

Full platform access from the terminal.

### Installation
```bash
cd cli && pip install -e .
llx init
```

### Commands
```bash
llx status                      # System dashboard
llx chat "explain this codebase" # Chat with RAG streaming
llx chat --no-rag "hello"       # Direct LLM, no document context
llx search "query"              # Semantic search across documents
llx files list                  # Browse files
llx files upload report.pdf     # Upload and index a file
llx generate csv "50 ideas"     # Bulk content generation
llx jobs watch JOB_ID           # Live job progress
llx rules list                  # List system prompts
llx                             # Interactive REPL
```

---

## Plugin System

Plugin-based GPU service management with live monitoring and conflict detection.

### Architecture
Each plugin lives in `plugins/<name>/` with a `plugin.json` manifest declaring its service type, port, VRAM estimate, health endpoints, and configuration. Plugins are loaded automatically at startup.

### Available Plugins
- **Ollama** — local LLM inference server. Powers chat, RAG, agents, and text generation. VRAM estimate: ~8 GB
- **ComfyUI** — GPU-accelerated image and video generation server. Supports Wan2.2, CogVideoX, SVD, RIFE interpolation, and Real-ESRGAN upscaling workflows. VRAM estimate: ~6 GB
- **GPU Embedding** — GPU-accelerated text embeddings for faster document indexing. Uses the system's Ollama embedding model with CPU fallback

### Plugins Page (GPU Management)
- **Plugin cards** — each plugin shows name, description, version, status (running/stopped/starting/error), and health indicator
- **Start/Stop controls** — toggle individual GPU services on and off
- **Enable/Disable** — persistently enable or disable plugins across restarts
- **Per-plugin log viewer** — expandable log panel shows recent output from each service
- **Plugin configuration** — edit plugin settings (URL, timeout, model, batch size) through inline config panels

### VRAM Budget Bar
- **Live nvidia-smi monitoring** — polls GPU stats every 5 seconds via nvidia-smi subprocess
- **Visual VRAM bar** — shows used/total VRAM with color-coded thresholds (green/yellow/red)
- **GPU details** — displays GPU name, utilization %, temperature, and per-plugin estimated VRAM segments
- **Per-plugin overlay** — stacked segments show how much VRAM each running plugin is estimated to consume

### GPU Conflict Detection
- **Exclusive access enforcement** — Ollama and ComfyUI require exclusive GPU access; starting one automatically offers to stop the other
- **Pre-flight GPU checks** — video and image generation APIs verify GPU availability before queuing jobs, returning 409 Conflict if the GPU is in use by another service
- **Auto-switching** — the Video Generator page can automatically stop Ollama and start ComfyUI when needed

### Model Download Management
- **VideoModelsModal** — download Wan2.2 GGUF checkpoints, CogVideoX weights, RIFE 4.9, Real-ESRGAN, and Wan VAE from HuggingFace
- **ImageModelsModal** — download and manage Stable Diffusion model checkpoints
- **VoiceModelsModal** — download and manage Piper TTS voice models
- All modals show real-time download progress with speed (MB/s), downloaded/total size, and percentage
- Accessible from Settings page and relevant generation pages

### Plugin API
Plugins can register:
- New API endpoints
- Background tasks
- Tool extensions
- Service hooks

---

## System Architecture

### Backend Stack
- **Flask 3.0** — HTTP server with 68 REST API blueprints
- **SQLAlchemy + PostgreSQL** — ORM with Alembic migrations (auto-provisioned on first run)
- **Celery + Redis** — async task processing with two worker pools (main + training/GPU)
- **LlamaIndex** — RAG pipeline with vector storage, entity extraction, hybrid retrieval
- **Ollama** — local LLM and embedding model inference (managed plugin)
- **ComfyUI** — video/image generation server supporting Wan2.2, CogVideoX, SVD, RIFE, Real-ESRGAN (managed plugin)
- **Socket.IO** — real-time bidirectional communication for streaming and progress
- **Ariadne** — GraphQL API layer

### Frontend Stack
- **React 18** with Vite build system
- **Material-UI v5** — component library with custom dark themes
- **Zustand** — lightweight state management
- **Apollo Client** — GraphQL state management
- **Monaco Editor** — code editing
- **Socket.IO client** — real-time updates

### Key Design Patterns
- **Modular API layer** — each feature gets its own Flask blueprint
- **Service layer** — business logic separated from HTTP handlers
- **Unified progress system** — all background operations report progress through a single Socket.IO channel
- **Environment isolation** — multiple instances can run on the same machine without interference
- **Graceful startup** — `start.sh` detects what needs setup and only does what's necessary

---

## Startup & Operations

### First Run
```bash
git clone https://github.com/guaardvark/guaardvark.git
cd guaardvark
./start.sh
```

First run:
1. Creates Python virtual environment and installs dependencies
2. Installs Node.js dependencies
3. Provisions PostgreSQL (requires system password once, then never again)
4. Starts Redis
5. Builds Whisper.cpp from source
6. Runs database migrations
7. Builds frontend
8. Starts Flask, Celery workers, and Vite dev server
9. Runs health checks

### Subsequent Runs
```bash
./start.sh          # Detects everything is set up, starts services instantly
./start.sh --fast   # Skip all checks, fastest possible startup
./stop.sh           # Stop all services
```

### Environment Isolation
- Process tracking via PID files — only kills processes from this installation
- `GUAARDVARK_ROOT` anchors all path resolution
- Multiple instances can coexist on the same machine with different ports

### Logging
All logs in `logs/`:
- `backend.log` — Flask application
- `celery_main.log` — Main Celery worker (indexing, generation, health)
- `celery_training.log` — Training/GPU worker
- `frontend.log` — Vite dev server
- `setup.log` — Dependency installation
- `test_results/` — Test execution output

---

*Built with local-first AI in mind. Your data, your hardware, your rules.*
