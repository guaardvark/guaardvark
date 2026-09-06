# Guaardvark — Modifications for Mac Summary

**Goal:** Make Guaardvark's main features run on an **RTX machine** (GPU-heavy work) on top of a **MacBook Pro M5 48GB** (the always-on host), working around the Mac's **limited unified memory (48GB)**.

**Memory strategy (the core constraint):**
- **LLM → ollama-cloud `deepseek-v4`** — the chat brain runs in the cloud, so the local Mac does **not** hold a large LLM in memory.
- **Image generation → Z-Image Turbo via ComfyUI** — the local memory budget is reserved for image generation (the heaviest local workload).
- **LoRA training → RunPod plugin** — training is offloaded to a cloud GPU (RunPod), not run on the Mac.
- **Video → simple pic-to-video via FFmpeg** — lightweight, no heavy video model in memory.
- **Captions → generated from the customer profile** — text-based, cheap.
- **Audio → whisper STT + TTS** — speech-to-text runs through an external `whisper.cpp` server (offloaded, MPS-friendly) and text-to-speech is handled locally/lightweight, so audio never competes with image generation for the limited memory budget.

Everything below is the set of modifications (across 42 GitButler virtual branches) that implement this.

---

## 1. LLM / Cloud routing (save local memory)

The chat brain is moved to the cloud so the Mac doesn't hold a big local model.

- **`feat/llm-providers`** — OpenAI-compatible chat provider + smart multi-provider escalation; force UTF-8 decoding in cloud LLM streaming (fixes mojibake); model-management UI + cloud model in status bar; surface `.env` cloud models in the Music Video Director dropdown.
- **`voice-openai-routing`** — guard: don't load a local Ollama model when an OpenAI-compatible endpoint is active; Discord voice via pi-omni router + OpenAI-compatible main chat.
- **`feat/filmcrew-openai-compatible`** — route Film Crew agents and the director_service LLM calls through the OpenAI-compatible provider (so film crew doesn't need a local LLM).
- **`fix/vision-sync`** — never treat text-only `gemma4:26b-mlx` as vision-capable; prefer `qwen3.8`; route cast identity sync through `qwen3.8` vision + cloud consensus model.

> **Local image analysis model:** Guaardvark's local vision analysis (`VisionAnalyzer`) runs through Ollama. It **prefers a Qwen model** (e.g. `qwen3.8`) for image analysis, because `gemma4:26b-mlx` is text-only despite the name (it returns `400 "does not support image input"`). Gemma4 remains only as a **fallback** (priority: Qwen → any vision model already in VRAM → configured gemma4 → `gemma4:e4b`/`moondream`/`llava`). Gemma4:12b is still used as the default **text** LLM for planning (e.g. character-sheet generation), not for image analysis.
- **`chore/llm-providers-followup`** — ModelManagementSection collapsible alert + ISSUES note.

## 2. Image generation — Z-Image Turbo via ComfyUI (the local memory budget)

The Mac's memory is reserved for image generation, routed through ComfyUI (works on Apple Silicon MPS and on the RTX box).

> **Model storage:** By default Guaardvark stores its models in its own location (e.g. `data/models/stable_diffusion`). With these changes it can now also **use models that live in ComfyUI's shared model home** (`~/ComfyUI-Shared` via `GUAARDVARK_COMFYUI_DIR` + the `extra_model_paths` bridge), so a model downloaded once for ComfyUI is reused instead of being duplicated into Guaardvark's own directory — saving disk and avoiding re-downloads.

- **`feat/zimage-comfyui`** — route Z-Image image generation through ComfyUI; don't free ComfyUI resident models when rendering through ComfyUI.
- **`feat/zimage-comfyui-mps`** — enable Z-Image generation on Apple Silicon via ComfyUI; apply Z-Image character LoRAs model-only in the ComfyUI Z-Image graph; auto-link trained LoRAs into ComfyUI when Z-Image is routed through ComfyUI.
- **`feat/imagemodel-comfyui`** — add `/imagemodel comfyui` chat image backend; accept it regardless of download status; detect ComfyUI engines from live `/object_info` instead of a bundled dir.
- **`fix/chat-cast-lora-resolution`** — resolve a cast LoRA from the user message in `generate_image` (so `[starship_captain]` / "Starship Captain" loads the trained LoRA even when the LLM strips the trigger).

## 3. LoRA training — RunPod plugin (offload to cloud GPU)

Training is offloaded to RunPod so the Mac's memory/GPU isn't consumed by training.

- **`feat/runpod-lora-trainer`** — add RunPod remote LoRA trainer as an alternative plugin; pod Dockerfile on a modern CUDA 12 base + bundled trainer scripts; S3/R2 input staging + output bucket/prefix + in-service smoke mode; pass `job_id` through the remote trainer + progress ETA.
- **`feat/runpod-lora-trainer-setup`** — RunPod remote LoRA trainer setup (SDK deps, pod runner, issue doc); map the internal zimage model id to the HF repo in the pod runner.
- **`feat/lora-training-fixes`** — keep facial-hair terms in the cast description, `job_id` passthrough, pod path guard.
- **`feat/lora-trainer-mps`** — Apple Silicon (MPS) support for the local LoRA trainer (fallback path).
- **`fix/lora-trainer-timeout`** — raise daemon/task timeouts to fit slow MPS training; fail loudly + reconcile timeouts so training can't loop silently.

## 4. Video — simple pic-to-video via FFmpeg (lightweight)

Video generation is kept lightweight with FFmpeg (no heavy video model in memory).

- **`feat/ffmpeg-still-video`** — FFmpeg still-to-video generation with camera-only motion patterns; configurable focus point for Ken Burns zoom/pan; pan directions incl. random; hide AI model config in FFmpeg mode; bin drag-to-reorder + up/down arrow reorder; FFmpeg batches visible in the video library with correct counts.
- **`feat/video-captions-ffmpeg`** — caption export/import + code-editor editing; FFmpeg fit-framing & transparency; FFmpeg framing modes (letterbox / zoom-to-fill / match-image) with min+max size.
- **`fix/video-editor-registration`** — point video-editor registration `backend_url` at the running backend (5055).
- **`fix/mps-video-unload`** — resolve `_mps_available` NameError (offline video import) + Files repo-root browse crash.
- **`feat/music-video-i2v-model`** — I2V model dropdown in the music video approval panel.
- **`feat/audio-music-polish-progress`** — cloud music-prompt rewriter + music generation progress.

## 5. Captions from customer profile

- **`feat/video-captions-ffmpeg`** — caption generation/editing tied to the customer profile (caption export/import, code-editor editing, FFmpeg fit-framing & transparency).
- **`chore/misc-docs`** — video caption + FFmpeg guides, caption SRT sample, message.md.

## 6. Film Crew + Audio pipeline (MPS-friendly, memory-aware)

- **`feat/audio-mps-whisper-filmcrew`** — MPS support + remote-capable AudioFoundry; route STT through an external whisper.cpp server; ComfyUI MPS GPU support + auto-start + status check; Film Crew resumable rendering + per-shot clip persistence; show RenderProgress during rendering. (Whisper STT is offloaded to an external server and TTS is handled locally/lightweight, so audio stays out of the image-generation memory budget.)
- **`feat/filmcrew-i2v-speed-config`** — configurable I2V speed/quality env vars (fast default); sustained ComfyUI down-detection so a busy ComfyUI doesn't orphan renders.
- **`feat/cast-shot-count`** — per-run shot count (16/32) for character generation.
- **`fix/chat-cast-image-routing`** — text-analysis routing + cast generation celery queue.

## 7. UI / polish

- **`feat/collapsible-alerts`** + **`fix/collapsible-alert-ref`** — collapsible alerts; MUI Collapse/Grow ref handling in CollapsibleAlert and PluginsPage.
- **`chore/production-detail-collapsible`** / **`chore/filmcrew-collapsible`** — collapsible alert in ProductionDetail / CreateProductionDialog.
- **`fix/infographic-import`** — InfographicGenerator stray import statement.
- **`feat/video-audio-systemmap-ui`** — System Map / Audio Studio contrast in light mode; play `final.mp4` in browser on the Production page.
- **`feat/mcp-start`** — optional MCP server startup and cleanup in start/stop scripts.

## 8. Config / docs / housekeeping

- **`chore/agent-config-and-docs`** — agent project-type configs, AGENT/KNOWLEDGE docs, RunPod issues.
- **`chore/gitignore-ignore-client-media`** — ignore `data/clients`, JP guide screenshots, `*.mp4`.
- **Docs branches** (not merged into `dev`; cleaned up separately): `docs/video-audio-systemmap-guides`, `docs/generation-diagram`, `docs/filmcrew-fountain-skill`, `docs/guaardvark-cli-skill`, `docs/guides`, `docs/issues-tracker`, `docs/issues-feature-requests`, `docs/git-workflow-actual-plan`.

---

## How the memory strategy maps to the code

| Workload | Where it runs | Memory impact on the Mac |
|----------|---------------|--------------------------|
| Chat / LLM | ollama-cloud `deepseek-v4` | ~none (cloud) |
| Image gen | Z-Image Turbo via ComfyUI | the main local memory budget |
| LoRA training | RunPod (cloud GPU) | ~none (offloaded) |
| Video (pic→video) | FFmpeg | low |
| Captions | from customer profile (text) | ~none |

## Branch management

The 42 applied branches are organized into two tracks — a **macOS track** (Apple Silicon support) and a **general track** (platform-agnostic improvements) — plus a docs-only group, documented in `docs/git-workflow-actual_plan.md` (merge order, conflict hotspots, and per-PR smoke tests are documented there). `main` is synced to `upstream/main` (`e4331689`); the `dev` integration branch is created from it and receives the track PRs.

---

## `.env` variables

Guaardvark reads its configuration from the repo-root `.env` file. The variables below are the ones that matter for this RTX-on-Mac setup (grouped by concern). Paths resolve through `backend/config.py`; secrets and `DATABASE_URL` come from `.env`.

### Core / runtime
| Variable | Purpose |
|----------|---------|
| `GUAARDVARK_ROOT` | Repo root anchor; all storage/log/backup paths derive from it. |
| `GUAARDVARK_MODE` | Runtime mode: `default` or `test`. |
| `DATABASE_URL` | Postgres connection string (default `postgresql://guaardvark:guaardvark@localhost:5432/guaardvark`). |
| `REDIS_URL` | Redis connection for Celery / sockets. |
| `SECRET_KEY` | Flask session/secret key. |
| `FLASK_PORT` / `VITE_PORT` | Backend (default 5055) and frontend (default 5173) ports. |

### LLM / cloud routing (the memory strategy)
| Variable | Purpose |
|----------|---------|
| `GUAARDVARK_DEFAULT_LLM` | Default chat model. |
| `GUAARDVARK_OPENAI_API_KEY` / `GUAARDVARK_OPENAI_BASE_URL` / `GUAARDVARK_OPENAI_MODEL` | OpenAI-compatible provider — used to point the chat brain at **ollama-cloud `deepseek-v4`** so no large LLM is held in local memory. |
| `GUAARDVARK_MISTRAL_API_KEY` / `GUAARDVARK_MISTRAL_MODEL` / `GUAARDVARK_MISTRAL_BASE_URL` | Optional Mistral provider (multi-provider escalation). |
| `OLLAMA_BASE_URL` | Local Ollama endpoint (used when not routing to the cloud). |
| `GUAARDVARK_EMBEDDING_MODEL` | Embedding model for RAG. |
| `GUAARDVARK_CLAUDE_API_ENABLED` / `GUAARDVARK_CLAUDE_MODEL` / `GUAARDVARK_CLAUDE_MAX_TOKENS` / `GUAARDVARK_CLAUDE_TOKEN_BUDGET` / `GUAARDVARK_CLAUDE_ESCALATION_MODE` | Optional "Uncle Claude" guardian / escalation. |

### ComfyUI / image generation
| Variable | Purpose |
|----------|---------|
| `GUAARDVARK_COMFYUI_DIR` | ComfyUI **shared model home** (e.g. `~/ComfyUI-Shared`). Lets Guaardvark reuse models already in ComfyUI instead of its own `data/models/stable_diffusion`. |
| `GUAARDVARK_COMFYUI_URL` | ComfyUI endpoint (default `http://127.0.0.1:8188`). |
| `GUAARDVARK_COMFYUI_VENV` | ComfyUI virtualenv path. |
| `GUAARDVARK_COMFYUI_IDLE_TIMEOUT` | Idle timeout before ComfyUI is freed. |
| `COMFYUI_OUTPUT_DIR` | ComfyUI output directory. |

### RunPod / LoRA training (offloaded to cloud GPU)
| Variable | Purpose |
|----------|---------|
| `GUAARDVARK_RUNPOD_API_KEY` | RunPod API key for the remote LoRA trainer. |
| `GUAARDVARK_RUNPOD_ENDPOINT_ID` | RunPod serverless endpoint id. |
| `GUAARDVARK_RUNPOD_MAX_JOB_SECONDS` | Hard ceiling for a training job. |
| `GUAARDVARK_RUNPOD_POLL_INTERVAL` | Poll interval while waiting on a job. |
| `GUAARDVARK_RUNPOD_OUTPUT_BUCKET` | S3/R2 bucket for training artifacts. |

### Whisper / audio (STT + TTS)
| Variable | Purpose |
|----------|---------|
| `WHISPER_SERVER` / `WHISPER_SERVER_BIN` | External `whisper.cpp` server binary (STT is offloaded here). |
| `WHISPER_SERVER_MODEL` / `WHISPER_SERVER_PORT` | Whisper model and server port. |
| `WHISPER_DIR` / `WHISPER_BUILD_DIR` / `WHISPER_CLI` | Whisper install/build paths and CLI. |

### Video
| Variable | Purpose |
|----------|---------|
| `GUAARDVARK_VIDEO_BACKEND` | Video backend — `ffmpeg` for the lightweight pic-to-video path (vs an AI model). |

### GPU / memory management
| Variable | Purpose |
|----------|---------|
| `GUAARDVARK_GPU_IDLE_TIMEOUT` | Idle timeout before a GPU model is evicted. |
| `GUAARDVARK_GPU_EVICTION_GRACE` | Grace period before eviction. |
| `GUAARDVARK_GPU_QUALITY_TIER` | Quality tier (e.g. `balanced`). |
| `GUAARDVARK_CHAT_KEEP_ALIVE_CPU` / `GUAARDVARK_CHAT_KEEP_ALIVE_GPU` | Keep-alive for the chat model on CPU vs GPU. |
| `GUAARDVARK_EMBED_KEEP_ALIVE_CPU` / `GUAARDVARK_EMBED_KEEP_ALIVE_GPU` | Keep-alive for the embedding model. |

### MCP
| Variable | Purpose |
|----------|---------|
| `GUAARDVARK_MCP_ENABLED` | Enable the MCP server. |
| `GUAARDVARK_MCP_SERVERS` | MCP server config. |
| `GUAARDVARK_MCP_TIMEOUT` | MCP timeout. |

### Ollama tuning (when a local model is used)
| Variable | Purpose |
|----------|---------|
| `OLLAMA_KEEP_ALIVE` | How long a model stays loaded. |
| `OLLAMA_MAX_LOADED_MODELS` | Max models resident at once (memory control). |
| `OLLAMA_NUM_CTX` | Context window. |
| `OLLAMA_NUM_PARALLEL` | Parallel requests. |
| `OLLAMA_KV_CACHE_TYPE` | KV cache quantization (memory control). |
| `OLLAMA_FLASH_ATTENTION` | Flash attention on/off. |

### Storage directories (all derived from `GUAARDVARK_ROOT`)
`GUAARDVARK_STORAGE_DIR`, `GUAARDVARK_OUTPUT_DIR`, `GUAARDVARK_UPLOAD_DIR`, `GUAARDVARK_CACHE_DIR`, `GUAARDVARK_LOG_DIR`, `GUAARDVARK_BACKUP_DIR`, `GUAARDVARK_CONTEXT_DIR` — override where data, outputs, uploads, cache, logs, backups, and context live.
