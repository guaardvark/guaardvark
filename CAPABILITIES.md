# Guaardvark — Full Capabilities List

**Version 2.5.3** · [guaardvark.com](https://guaardvark.com)

This document is a comprehensive reference of everything Guaardvark can do. For a quick overview, see [README.md](README.md).

---

## Table of Contents

- [AI Chat & Conversation](#ai-chat--conversation)
- [AgentBrain — Three-Tier Routing](#agentbrain--three-tier-routing)
- [RAG & Document Intelligence](#rag--document-intelligence)
- [RAG Autoresearch](#rag-autoresearch)
- [Self-Improvement Engine](#self-improvement-engine)
- [Lesson Pearls & Memory](#lesson-pearls--memory)
- [Autonomous Screen Agents](#autonomous-screen-agents)
- [Agent & Code Tools](#agent--code-tools)
- [MCP Integration](#mcp-integration)
- [Image & Video Generation](#image--video-generation)
- [Audio Studio (Audio Foundry)](#audio-studio-audio-foundry)
- [Video Editor — Shotcut-lite](#video-editor--shotcut-lite)
- [Outreach System](#outreach-system)
- [Swarm Orchestrator & Film Crew](#swarm-orchestrator--film-crew)
- [GPU Image & Video Upscaling](#gpu-image--video-upscaling)
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

Guaardvark's chat system is the primary interface for interacting with your AI. Two pipelines handle different use cases.

### Core Chat
- **Streaming responses** via Socket.IO — tokens appear in real-time as the model generates
- **Conversational fast-path** — simple messages (greetings, follow-ups) skip RAG and tool routing entirely, responding in ~700ms instead of 25+ seconds
- **Intent routing** — automatically detects whether a message needs RAG retrieval, tool use, or a direct conversational response
- **Per-project sessions** — chat context is isolated by project; switching projects gives you a clean context with that project's documents
- **Session persistence** — conversation history persists across page reloads and browser sessions; sessions also have a `mode` field stored server-side
- **System prompts (Rules)** — customizable system prompts that shape AI behavior, manageable via the Rules page
- **Multi-model support** — switch between any Ollama model at runtime without restarting

### Agent Mode (`/agent` and `/chat`)
- **Modal session toggle** — type `/agent` to flip the session into screen-control mode (every message becomes a screen-control task); `/chat` (or `/exit`) flips back
- **Sticky** — the mode lives on the session, not the message — survives reloads
- **Visible cue** — agent-mode sessions show an orange chip above the chat input
- **Speak AND act** — agent-mode messages still route through the chat LLM, so the model narrates briefly, calls `agent_task_execute`, and summarizes the result

### Per-Iteration Thinking Display
- **Live reasoning trail** — for screen-control tasks, the agent loop's per-step thinking streams into the chat as it happens (no more 30+ second blackouts followed by a single "completed" line)
- **What you see** — each iteration shows action label + full reasoning ("Step 8 — click: I see the address bar and want to clear it…")
- **Pivots and stuck-loop signals** also stream — when the loop forces a wait after a repeated failure, that decision is visible
- **Persists in history** — the trail stays in the message after streaming completes, so you can scroll back and audit the run

### Model Management
- **Runtime model switching** — change the active LLM through Settings; the old model is unloaded from VRAM before the new one loads (prevents OOM)
- **Embedding model switching** — swap embedding models via dropdown; triggers re-indexing confirmation since vector spaces are incompatible across models
- **Live health detection** — dashboard probes Ollama on every request to show actual model availability (not a stale startup flag)
- **KV cache optimization** — `num_keep: -1` locks the system prompt prefix in Ollama's KV cache, making follow-up turns faster
- **GPU VRAM monitoring** — real-time VRAM usage bar with loaded model indicators in Settings

---

## AgentBrain — Three-Tier Routing

A neural router that decides how much work a message deserves before any tools fire. Saves seconds per turn on simple questions and unlocks deeper deliberation when it's warranted.

### The Three Tiers

| Tier | Name | Latency | LLM Calls | When It Fires |
|------|------|---------|-----------|---------------|
| 1 | **Reflex** | <100ms | 0 | Media commands, greetings, exact-match recipes |
| 2 | **Instinct** | 1–3s | 1 | Most requests — single LLM call with relevant tools in scope |
| 3 | **Deliberation** | 5–30s | 3–10 | Multi-step reasoning (full ReACT loop) |

### Routing Signals
- Pre-computed reflex table for fast literal matches
- Conversational classifier filters out small-talk before tools get loaded
- Semantic tool selection picks the right ≤15 tools for the message
- Screen-active flag gates desktop/agent tools so they only appear when relevant

### Gemma4 Direct Path
- When the active model is Gemma4 (native vision + tool use) AND the agent screen is active, the brain skips ReACT bloat and sends Gemma4 a minimal prompt with a screenshot + the task
- Gemma4 returns JSON action steps directly; the loop executes them
- For chat without screen actions, Gemma4 responds normally

### Configuration
- Toggle via `AGENT_BRAIN_ENABLED` in `backend/config.py`
- Falls back gracefully to the legacy UnifiedChatEngine path if brain state isn't ready

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
- **Multiple model support** — switch between lightweight (embeddinggemma 300M) and high-quality (mxbai-embed-large, bge-m3, snowflake-arctic-embed) models
- **Full-precision option** — BF16 embeddings available for maximum quality
- **Query-time embedding** — every RAG search query is embedded with the same model for consistent vector space matching

### Indexing
- **Automatic on upload** — files are indexed when uploaded through the UI or API
- **Bulk indexing** — "Index All" button processes the entire document library
- **Code-specific indexing** — detects programming languages, extracts imports/classes/functions, chunks by logical boundaries
- **GPU-accelerated indexing** — optional GPU Embedding plugin offloads embedding generation to CUDA with CPU fallback
- **Progress tracking** — real-time progress bar during indexing operations via Socket.IO

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
- **Pending fixes queue** — proposed changes can require user approval before applying

### Live Progress
- **Socket.IO events** at each stage: `starting`, `testing`, `analyzed`, `fixing`, `complete`, `error`
- **Dashboard card** shows real-time progress bar with color-coded stages
- **Run button** disabled while a check is in progress

---

## Lesson Pearls & Memory

A user-curated memory system that captures successful agent runs and makes them available in future sessions.

### Begin / End Lesson
- **Bracket a successful run** — slash commands or buttons mark the start and end of a teachable sequence
- **Distiller** — at End Lesson, an LLM summarizes what happened into a single durable lesson
- **Saved as AgentMemory** — lessons of type `lesson_summary` get loaded into the system prompt next session
- **Editable rows** — fix or remove a misperceived lesson without re-recording

### Vision-Actionable Knowledge (LEARNING_PRINCIPLES.md)
- **Stored knowledge describes WHAT to look for**, not where it sits (no pixel coordinates)
- **Short labels for the servo** (≤4 words), rich context for the brain
- **Recipes, lessons, traces, memories** all bound by the same contract

### Memory Surfaces
- **MEMORY_BLOCK** — recent memories substituted into the system prompt at decision time
- **Memory Management Section** in Settings — browse, edit, delete saved memories
- **Live recall** — when a memory matches the current context, the LLM can quote it directly

---

## Autonomous Screen Agents

Guaardvark drives a real Ubuntu desktop on a virtual display — clicking, typing, scrolling, and reading the screen like a human user. Used for outreach, file management, web research, and anything the model can't accomplish via API alone.

### Virtual Display
- **Xvfb on `:99`** — 1024×1024 headless X server, isolated from the user's real session
- **Full XFCE desktop** — `xfce4-session` running via `dbus-run-session` with a scrubbed environment; standard Applications menu, desktop icons, taskbar, file manager (Thunar). Vision models recognize it instantly because it looks like any other Ubuntu desktop
- **VNC viewer** — x11vnc on port 5999 (password-protected) lets the user watch the agent live, embedded in the frontend as a draggable card
- **Isolated XDG dirs** — agent's `~/.agent_desktop/`, dedicated `XDG_CONFIG_HOME`, dedicated `XDG_RUNTIME_DIR`. The user's real desktop and configs are invisible to the agent

### See-Think-Act-Verify Loop
- **SEE** — screen capture (mss) + optional DOM extraction (Firefox CDP/BiDi)
- **THINK** — Gemma4 (or other unified VLM) decides the next action, returning JSON with `action`, `target_description`, `text/keys`, `reasoning`, and `success_proof`
- **ACT** — execute via the servo (vision-targeted click) or direct (type/hotkey/scroll)
- **VERIFY** — post-action screenshot delta; failed steps flag the LLM that the attempt didn't change the screen
- **Recipes** — known-good action sequences in `data/agent/recipes.json` execute deterministically before the loop is ever invoked, with optional `preconditions` (visibility checks) that skip recipes when their UI isn't on screen
- **Strategy cooldowns** — repeated failures on the same action class force the loop to wait and re-observe before retrying

### Servo Controller
- **Vision-targeted clicking** — the servo asks the vision model "where is X on this screen?" and clicks the returned coordinates
- **Visibility guard** — pre-click "do you actually see this?" check rejects hallucinated targets before the cursor moves
- **Per-model calibration** — `MODEL_VISION_CONFIGS` in `servo_knowledge_store.py` maps each chat model to its preferred eyes (gemma4 native, moondream for text-only) and any scale-factor calibration learned over time
- **Failure capture** — exhausted click attempts save the screenshot + corrections log to `data/training/failures/` for offline review

### Training Data Capture
- **Every click recorded** to `data/training/knowledge/servo_archive.jsonl` — target description, raw coords, scaled coords, actual click position, success/failure, model, attempt #, time taken
- **Self-improvement engine** reads the archive to refine calibration
- **Optional Comments/Vision Trainer pages** — interactive practice modes that keep the servo clicking long after a normal task would have stopped

### Agent Tools
- `agent_task_execute` — full natural-language screen task (drives the full SEE-THINK-ACT loop)
- `agent_screen_capture` — single screenshot of the virtual display
- `agent_mode_start` / `agent_mode_stop` — open/close the session (internal; the LLM should call `agent_task_execute` directly)

---

## Agent & Code Tools

A ReACT-loop agent that can autonomously work with code and the system.

### Agent Capabilities
- **Read files** — examine any file in the project
- **Edit code** — precise text replacement with verification
- **List files** — explore directory structure (configurable depth up to 5 levels)
- **Execute code** — run Python/shell commands and inspect output
- **Web search** — search the internet for information
- **Browser automation** — navigate websites, fill forms, take screenshots (via Playwright, separate from the screen-control agent)

### Safety Features
- **Circuit breaker** — after 2 consecutive failures, a tool is temporarily blocked
- **Duplicate detection** — hash-based detection prevents the agent from making identical tool calls
- **Fallback suggestions** — when a tool fails, the system suggests alternative approaches
- **Iteration limits** — configurable maximum iterations per agent run
- **Tool approval gates** — dangerous tools (file write, shell exec) can require human approval per call

### Code Editor Page
- **Monaco Editor** — VS Code-quality editing in the browser with syntax highlighting for 50+ languages
- **Multi-file tabs** — open and edit multiple files simultaneously
- **File tree** — browse project structure in a sidebar
- **AI assistant pane** — chat with the agent about the open file

### Uncle Claude Escalation
- When the local model is stuck, Guaardvark can escalate to the Anthropic API (Claude) for a second opinion
- Token budget tracked and surfaced in the Dashboard's Family card
- Toggleable per-session; never auto-fires without configuration

---

## MCP Integration

Guaardvark speaks Model Context Protocol — both as a server (exposing its tools to external clients) and as a client (calling tools from external MCP servers).

### MCP Server (Phase 1)
- **Stdio transport** — `backend/mcp/` runs an MCP server that any MCP-compatible client (Claude Desktop, Cursor, etc.) can connect to
- **23 native tools exposed** — covers chat, RAG, file management, image generation, agent control
- **58 output resources** — file contents, generated images, search results, etc., available via MCP's resource protocol
- **Tested against Claude Desktop** — works end-to-end

### MCP Client
- **`mcp_connect` tool** — register external MCP servers at runtime
- **`mcp_execute` tool** — call any tool on a connected server
- **Live inventory** — connected-server tools surface in the chat LLM's tool list so it can pick them by name without going through `mcp_execute`
- **State sync** — `mcp_get_state`, `mcp_disconnect`, etc. for managing connections

---

## Image & Video Generation

### Image Generation
- **Stable Diffusion** via Diffusers library — runs directly on your GPU
- **Batch generation** — queue multiple prompts with different parameters
- **Auto-registration** — generated images are automatically added to the Documents/Files system under `/Images/`
- **Celery background processing** — generation runs as async jobs with progress tracking
- **Image library** — dedicated page with thumbnail grid, lightbox preview, keyboard navigation, batch operations
- **Image model management** — ImageModelsModal for downloading and managing Stable Diffusion checkpoints
- **Inline images in chat** — when the chat generates an image, it appears inline and persists in history with the assistant message

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
- **Batch queue** — queue / cancel / interrupt running jobs

#### Model Management (VideoModelsModal)
- Browse all available video models with installed/available status
- Download models from HuggingFace with real-time progress bars showing speed (MB/s), downloaded/total size
- Models include: Wan2.2 GGUF checkpoints (HighNoise + LowNoise), Wan VAE, CogVideoX weights, RIFE 4.9, Real-ESRGAN 2x
- Accessible from the Video Generator page and Settings page

---

## Audio Studio (Audio Foundry)

Local audio generation for voiceover, music, ambience, and effects. Shipped as the `audio_foundry` plugin.

### Voiceover
- **Chatterbox** — expressive neural TTS with style/emotion control
- **Kokoro-82M** — fast, light, multilingual TTS (English + Spanish voices, more languages on the model side)
- **Piper** — local neural TTS fallback for environments where the heavier engines aren't appropriate
- **Streaming output** — audio chunks stream to the browser as the engine produces them

### Music Generation
- **ACE-Step v1 (3.5B)** — full-song generation with vocals; runs locally on GPU
- **Suno-compatible workflow** — same prompt shape as Suno's hosted service, but local

### Sound Effects / Ambience
- **Stable Audio Open** — generate sound effects and ambience tracks via diffusion
- **Negative prompts** supported for filtering out unwanted sonic textures
- **Guidance scale + steps** configurable per generation

### Dual-Venv Architecture
- **`venv-music/`** — torch-sensitive ML packages live in an isolated env so the main backend isn't dragged through every torch upgrade
- **Daemon mode** — the audio engine runs as a long-lived daemon; the backend talks to it over HTTP/socket so model load happens once
- **OOM-safe** — model unload/swap is explicit, no silent CPU fallback

### Audio Library
- **DocumentsPage audio player** — preview, rename, organize generated audio files alongside everything else
- **Filename uniqueness** — migration 005 ensures generated audio doesn't collide with imports

---

## Video Editor — Shotcut-lite

A non-linear video editor built into Guaardvark for assembling generated clips into finished videos.

### Timeline
- **Multi-track timeline** — video, audio, overlay
- **Drag-and-drop clips** from the Media Library directly onto the timeline
- **Trim, split, ripple-delete** standard timeline operations
- **Keyboard shortcuts** — J/K/L playback, arrow-key nudging, etc.
- **1-step undo** with on-screen indicator

### Audio
- **Audio Foundry track** — generate voiceover or music directly into a timeline track
- **Mix volume per clip / per track**

### Media Library
- **Project-scoped media bin** — clips from prior video generations show up automatically
- **N+1 fix** — bulk-loaded thumbnails (no per-clip request storm)

### Export
- **Celery async render** — long renders run in the background, progress visible in the footer bar
- **UUID-tracked jobs** — each render gets a stable ID for status polling
- **MP4 / WebM** output

### Orchestrator Integration
- The video editor can be driven by the Production Pipeline (Film Crew) — agents drop generated clips into the timeline automatically

---

## Outreach System

Supervised AI for social-media engagement. Three-phase pipeline that drafts comments and posts but gates every public action behind explicit user approval.

### Three Phases
1. **Recon** — search for candidate posts/threads (YouTube, Reddit, Discord) matching configured topic targets. Uses web search + light LLM filtering. Outputs candidates to a queue; **never posts**
2. **Content** — for each candidate, an LLM drafts a comment in the user's voice with persona enforcement. Outputs drafts; **never posts**
3. **Outreach** — when the user reviews and approves a draft, the screen-control agent navigates to the target page and submits the comment

### Safety
- **Kill switch** — single toggle that halts all outreach activity immediately
- **Dual grader** — drafts get scored by two independent LLMs; low-scoring drafts get rejected before they reach the user
- **DOM-GUARD** — the posting agent verifies the target element exists in the DOM before clicking (rejects hallucinated post buttons)
- **Persona enforcement** — central persona.draft_outreach_text ensures drafts sound like the user, not like an AI
- **UTM tagging** — every guaardvark.com link in an outbound post is tagged so attribution survives
- **Randomized jitter** — type and click delays vary to avoid robotic patterns
- **Cadence + dedup** — per-platform cadence limits and content-hash dedup prevent spam

### Surfaces
- **Outreach Review page** at `localhost:5175/outreach` — queued drafts with approve/reject/edit controls
- **Activity feed** — agent-driven outreach work shows up in the unified Jobs/Activity surface
- **Telemetry** — recon/content/outreach metrics streamed to the dashboard

---

## Swarm Orchestrator & Film Crew

Parallel AI agent execution across isolated worktrees. Each agent gets its own git branch and workspace; results merge back cleanly.

### Swarm Orchestrator
- **Isolated worktrees** — each agent works in `.swarm-worktrees/<swarm-id>/<task>/`
- **Parallel task execution** — N agents run simultaneously on independent slices of work
- **Cherry-pick integration** — successful results integrate via git cherry-pick; failed branches leave no trace
- **Deadlock detection** — circular dependencies between agents flagged before they hang the swarm
- **Local backend optional** — can run via Ollama's built-in Claude Code integration (free, offline) or via Anthropic API

### Film Crew (Production Pipeline)
Five-agent swarm for coordinated media generation:
- **Screenwriter** — generates the script + scene breakdown from a logline
- **Casting** — assigns characters to LoRAs (trained via the LoRA Trainer plugin) or stock characters
- **Cinematographer** — produces shot list with camera moves, framing, lens choices
- **Storyboard** — generates keyframe images for each shot via the image generation pipeline
- **Editor** — assembles generated clips into the final video via the Video Editor

### LoRA Trainer Plugin
- **Character / environment / prop LoRAs** trained from reference images
- **CUDA daemon** with bf16 precision (~46 MB per LoRA, down from 93 MB in v1.0)
- **Real-torch isolation** — separate venv prevents torch version conflicts with the main backend

---

## GPU Image & Video Upscaling

Dedicated upscaling plugin for sharpening generated content to 4K/8K.

### Models
- **Real-ESRGAN 2x / 4x** — proven anime/photo upscaler
- **Custom checkpoints** — drop-in via the model browser

### Pipeline
- **`upscaling` plugin** — runs as its own GPU service (port 8202); accepts image or video, returns upscaled output
- **spandrel + torch.compile** — fused inference for speed
- **Integrated with video pipeline** — Cinema-tier output uses the upscaler as a post-processing step
- **Standalone usage** — upscale any image or video from the Documents page

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
- **Wake word listening** — optional, configurable wake phrase

### Text-to-Speech
- **Piper TTS** — local neural text-to-speech with multiple voice models
- **Kokoro / Chatterbox** — heavier engines available via the Audio Foundry plugin
- **Streaming output** — audio generated and streamed as the response is produced
- **Narrate button** — every assistant message gets a one-click TTS playback control

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

### Backup & Restore
- **Granular backup** — Data Backup (uploads/logos/training data), Code Backup, Full Backup
- **Schema-migration-aware** — restores adapt to schema diffs across versions
- **Cross-version compatible** — backups taken on one Guaardvark version restore cleanly to another

---

## Dashboard & Monitoring

The dashboard provides a live overview of system status.

### Status Cards
- **Family & Self-Improvement** — Uncle Claude status, self-improvement toggle, recent run history, token budget, live progress bar during self-checks
- **RAG Autoresearch** — experiment status, history, optimization parameters
- **Semantic Search** — quick search across all indexed documents
- **Drag-and-drop grid** — rearrange the dashboard layout to your taste

### System Health
- **Model status** — active model name and loading state shown in page headers
- **LLM ready indicator** — live Ollama probe (not a stale startup flag)
- **GPU resources** — VRAM usage bar with loaded model chips in Settings
- **Plugins page** — dedicated GPU service management page with VRAM budget bar, per-plugin controls, log viewer, and conflict detection
- **Activity / Jobs feed** — unified view of running and recent background jobs (indexing, generation, outreach, etc.)

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

### Cluster Foundation
- **Socket.IO chat bridge** — cross-node streaming chat (Phase 3 wired; awaits a frontend/middleware enable for full end-to-end)
- **Dependency-graph aware** — the cluster knows which nodes have which models loaded

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
| Browser (headless) | Playwright | Navigate, click, fill forms, screenshot, extract content — for tasks that don't need a visible screen |
| Screen agent | xdotool + mss + Gemma4 | Drives the visible `:99` desktop end-to-end; clicks, types, reads the screen with vision |
| Desktop (host) | pyautogui | Mouse, keyboard, screen capture on the host display (off by default for security) |
| MCP | Protocol | Connect to any MCP-compatible tool server |

```bash
GUAARDVARK_BROWSER_AUTOMATION=true
GUAARDVARK_DESKTOP_AUTOMATION=true   # Off by default (security)
GUAARDVARK_MCP_ENABLED=true
GUAARDVARK_AGENT_DISPLAY=99          # Override virtual display number
GUAARDVARK_AGENT_BROWSER=firefox     # Override agent's browser
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

### Quality Roadmap (v2.5.3)
- **Standardized JSON contracts** for all automation outputs
- **Quality gates** — every release runs the CLI against a fixture suite before publishing
- **Cross-platform PATH handling** — wrapper scripts work on macOS, Linux, WSL

---

## Plugin System

Plugin-based GPU service management with live monitoring and conflict detection.

### Architecture
Each plugin lives in `plugins/<name>/` with a `plugin.json` manifest declaring its service type, port, VRAM estimate, health endpoints, and configuration. Plugins are loaded automatically at startup.

**Manifest vs. runtime state separation:** `plugin.json` is a static manifest — same bytes on every machine. Live runtime state (`enabled`, `auto_start`, per-machine config) lives in `data/plugin_state.json` (gitignored). Toggling a plugin from the `/plugins` UI writes only to the runtime state file; the manifest is never mutated at runtime.

### Available Plugins

| Plugin | Port | Purpose |
|---|---|---|
| **Ollama** | 11434 | Local LLM and embedding inference (chat, RAG, agents) |
| **ComfyUI** | 8188 | Image + video generation (Wan2.2, CogVideoX, SVD, RIFE, Real-ESRGAN) |
| **Audio Foundry** | — | Voiceover (Chatterbox / Kokoro / Piper), music (ACE-Step / Suno), SFX/ambience (Stable Audio Open). Dual-venv with torch isolation |
| **Upscaling** | 8202 | GPU image/video upscaling via spandrel + torch.compile |
| **Vision Pipeline** | 8201 | Real-time scene narration, camera feed, video chat input |
| **Swarm** | 8210 | Parallel agent orchestration in isolated worktrees |
| **LoRA Trainer** | — | Train character/environment/prop LoRAs for the Film Crew (CUDA, bf16) |
| **Discord Bot** | 8200 | Discord bot integration — chat, image generation, search via Guaardvark backend |
| **GPU Embedding** | 5002 | GPU-accelerated text embeddings for faster indexing (CPU fallback) |
| **Training** | — | Vision/servo training data collection and dataset management |

### Plugins Page (GPU Management)
- **Plugin cards** — each plugin shows name, description, version, status (running/stopped/starting/error), and health indicator
- **Start/Stop controls** — toggle individual GPU services on and off
- **Enable/Disable** — persistently enable or disable plugins across restarts (writes to `plugin_state.json`)
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
- **Flask 3.0** — HTTP server with 68+ REST API blueprints (auto-discovered)
- **SQLAlchemy + PostgreSQL** — ORM with 42 models; Alembic migrations + a custom `schema_sync.py` (single master)
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

### System Mapper
- **Constellation view** — d3-force-driven visualization of the codebase (~712 nodes across the current repo)
- **Dependency analysis** — Python import graph + JS module graph + cross-language references
- **Reachability analysis** — flags files that are imported but never executed (stale candidates)
- **Lifecycle tagging** — every file gets `live` / `dormant` / `stale` based on usage patterns
- **Codebase audits** — generates reports that drive cleanup work

### Key Design Patterns
- **Modular API layer** — each feature gets its own Flask blueprint, auto-discovered via `blueprint_discovery.py`
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
10. Auto-starts the agent's virtual display (`:99`) with XFCE if `xfce4` is installed

### Subsequent Runs
```bash
./start.sh          # Detects everything is set up, starts services instantly
./start.sh --fast   # Skip all checks, fastest possible startup
./stop.sh           # Stop all services
```

### Agent Display
```bash
./scripts/start_agent_display.sh start    # Bring up Xvfb + XFCE on :99
./scripts/start_agent_display.sh stop     # Tear it down
./scripts/start_agent_display.sh status   # Health check
```
Requires `sudo apt install xfce4 dbus-x11` on first setup.

### Dependency Reconciler
- **Branch-aware sync** — on `git checkout`, the reconciler inspects venv / requirements.txt / alembic head / package.json and re-syncs only what changed
- **Single-master-migration policy** — `schema_sync.py` is the authoritative migrator; `alembic upgrade head` is deprecated for application use
- **TDD-driven** — 87 tests cover the reconciler's behavior across branch switches, partial states, and rollback scenarios
- Drops the "I just switched branches and now nothing works" failure mode

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
- `xfce_agent.log` — Agent's XFCE session output
- `x11vnc_agent.log` — VNC server for the agent display
- `test_results/` — Test execution output

---

*Built with local-first AI in mind. Your data, your hardware, your rules.*
