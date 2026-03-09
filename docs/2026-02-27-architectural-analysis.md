# Guaardvark — Architectural Analysis
**Version:** 2.4.1
**Date:** 2026-02-27
**Status:** Master Map (source of truth for README generation)

---

## 1. Project Identity

| Field | Value |
|-------|-------|
| Name | Guaardvark |
| Version | 2.4.1 |
| Former Name | LlamaX1 / LLAMAX |
| Website | https://guaardvark.com |
| Repo | guaardvark |
| License | TBD (Apache 2.0 recommended) |

Guaardvark is a **self-hosted, full-stack AI application platform**. It integrates local LLM inference, retrieval-augmented generation (RAG), multi-modal content generation (text, images, video, voice), a full file management system, and an agent-based code assistant — all accessible via a web UI and a terminal CLI (`llx`).

---

## 2. Technology Stack

### Backend
| Component | Technology | Version |
|-----------|-----------|---------|
| Web Framework | Flask + Flask-SocketIO | 3.0.0 |
| ORM | SQLAlchemy + Alembic | 3.1.1 |
| Database | SQLite | — |
| Task Queue | Celery + Redis | 5.4.0 / 5.0.4 |
| GraphQL | Ariadne | 0.26.2 |
| LLM/RAG | LlamaIndex + Ollama | 0.12.43 |
| ML Framework | PyTorch | 2.2.2 |
| Image Generation | Diffusers + Transformers | 0.31.0 / 4.48.0 |
| Video Generation | CogVideoX via ComfyUI | — |
| Speech-to-Text | Whisper.cpp | — |
| Text-to-Speech | Piper TTS | — |

### Frontend
| Component | Technology | Version |
|-----------|-----------|---------|
| Framework | React + Vite | 18.2.0 / 4.5.0 |
| UI Library | Material-UI | 5.15.0 |
| State Management | Zustand | 4.4.0 |
| GraphQL Client | Apollo Client | 4.0.7 |
| HTTP Client | Axios | 1.6.0 |
| WebSockets | Socket.io-client | 4.7.0 |
| Code Editor | Monaco Editor | 0.53.0 |

### CLI (`llx`)
| Component | Technology |
|-----------|-----------|
| Framework | Typer (Click-based) |
| Terminal Formatting | Rich |
| HTTP Client | httpx |
| Streaming | python-socketio |

### Infrastructure
| Requirement | Purpose |
|-------------|---------|
| Python 3.12+ | Backend runtime |
| Node.js 20+ | Frontend build |
| Redis 5.0+ | Celery broker + task results |
| FFmpeg | Voice pipeline |
| Ollama | Local LLM inference (optional) |
| CUDA GPU | Accelerated generation (optional) |

---

## 3. Repository Layout

```
guaardvark/
├── backend/                    # Flask application
│   ├── api/                   # 70+ REST API blueprints
│   ├── services/              # 50+ business logic services
│   ├── tasks/                 # Celery background tasks
│   ├── tools/                 # Agent tools (code, file, voice, video)
│   ├── utils/                 # 75+ helper modules
│   ├── routes/                # Upload/download routes
│   ├── handlers/              # Database handlers
│   ├── migrations/            # Alembic schema migrations
│   ├── tests/                 # 70+ test files (unit/integration/system)
│   ├── app.py                 # Flask application factory
│   ├── models.py              # SQLAlchemy ORM models
│   ├── config.py              # Configuration + path resolution
│   ├── celery_app.py          # Celery instance
│   ├── socketio_instance.py   # Socket.IO instance
│   ├── socketio_events.py     # WebSocket event handlers
│   └── rule_utils.py          # System prompt utilities
├── frontend/                   # React/Vite application
│   ├── src/
│   │   ├── pages/             # 28 page components
│   │   ├── components/        # 100+ UI components
│   │   ├── stores/            # Zustand state stores
│   │   ├── hooks/             # Custom React hooks
│   │   ├── api/               # 40+ API service modules
│   │   ├── utils/             # Frontend utilities
│   │   ├── config/            # Constants and defaults
│   │   └── theme/             # MUI theme system
│   └── dist/                  # Production build output
├── cli/                        # llx CLI tool
│   ├── llx/
│   │   ├── main.py            # Typer app entrypoint
│   │   ├── client.py          # HTTP abstraction
│   │   ├── streaming.py       # Socket.IO streaming client
│   │   ├── config.py          # ~/.llx/config.json management
│   │   ├── output.py          # Rich formatting + JSON output
│   │   ├── repl.py            # Interactive REPL
│   │   └── commands/          # chat, files, search, projects, rules,
│   │                          # agents, generate, jobs, settings, system
│   └── setup.py
├── data/                       # Runtime data (gitignored)
│   ├── database/              # system_analysis.db (SQLite)
│   ├── uploads/               # User-uploaded files
│   ├── outputs/               # Generated content
│   ├── cache/                 # Query + embedding cache
│   └── context/               # RAG context persistence
├── plugins/                    # Plugin extensions
│   └── gpu_embedding/         # GPU-accelerated embedding plugin
├── scripts/                    # Utility and maintenance scripts
│   └── system-manager/        # System inventory + analysis tools
├── logs/                       # Runtime logs (gitignored)
├── pids/                       # Process PID files
├── docs/                       # Documentation
├── start.sh                    # Master startup script (v5.1)
├── stop.sh                     # Shutdown script
├── start_celery.sh             # Celery worker startup
├── run_tests.py                # Test runner with env setup
├── CLAUDE.md                   # Development guide
└── INSTALL.md                  # Installation guide
```

---

## 4. Architecture Overview

Guaardvark follows a **layered monolith** architecture with async escape valves via Celery, real-time communication via Socket.IO, and optional GPU acceleration:

```
┌─────────────────────────────────────────────────────┐
│                     Clients                          │
│  Browser UI (React)  │  llx CLI  │  External APIs   │
└──────────┬───────────┴─────┬─────┴────────┬─────────┘
           │ HTTP/WS         │ HTTP         │ HTTP
┌──────────▼─────────────────▼─────────────▼─────────┐
│                Flask Application (Port 5000)         │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────┐  │
│  │  REST API   │  │  GraphQL API │  │ Socket.IO │  │
│  │  70+ routes │  │  (Ariadne)   │  │  Events   │  │
│  └──────┬──────┘  └──────┬───────┘  └─────┬─────┘  │
│         └────────────────┼────────────────┘         │
│                   ┌──────▼──────┐                    │
│                   │  Services   │                    │
│                   │  50+ modules│                    │
│                   └──────┬──────┘                    │
│                          │                           │
│         ┌────────────────┼────────────────┐          │
│         ▼                ▼                ▼          │
│  ┌─────────────┐  ┌───────────┐  ┌──────────────┐  │
│  │  SQLAlchemy │  │LlamaIndex │  │  Celery Tasks│  │
│  │  + SQLite   │  │  + Ollama │  │  + Redis     │  │
│  └─────────────┘  └───────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                  ▼
  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐
  │   Ollama    │  │  GPU/CUDA   │  │  ComfyUI /   │
  │  (Local LLM)│  │  Diffusers  │  │  CogVideoX   │
  └─────────────┘  └─────────────┘  └──────────────┘
```

### Access Ports
| Service | Default Port |
|---------|-------------|
| Frontend (Vite dev) | 5173 |
| Backend (Flask) | 5000 |
| Redis | 6379 |

---

## 5. Backend Architecture

### 5.1 API Layer (70+ Modules)

All API modules are Flask Blueprints registered in `backend/app.py`. Organized by domain:

**Chat & Conversation**
| Module | Purpose |
|--------|---------|
| `enhanced_chat_api.py` | Primary chat endpoint with RAG, streaming, agent routing |
| `agent_chat_api.py` | Agent-specific chat with tool use and ReACT loop |
| `simple_chat_api.py` | Lightweight direct LLM chat (no RAG) |
| `unified_chat_api.py` | Unified chat dispatcher |

**Content Generation**
| Module | Purpose |
|--------|---------|
| `unified_generation_api.py` | Dispatch to all generation types |
| `batch_image_generation_api.py` | Batch image generation with job tracking |
| `batch_video_generation_api.py` | Batch CogVideoX video generation |
| `bulk_generation_api.py` | Large-scale CSV/content bulk generation |
| `generation_api.py` | Core generation routes |
| `enhanced_context_generation_api.py` | Context-aware generation |

**RAG & Indexing**
| Module | Purpose |
|--------|---------|
| `indexing_api.py` | Index files/documents |
| `entity_indexing_api.py` | Entity-based indexing |
| `metadata_indexing_api.py` | Metadata-enriched indexing |
| `search_api.py` | Semantic search |
| `retrieve_api.py` | Direct retrieval |
| `query_api.py` | RAG query execution |
| `doc_query_api.py` | Document-specific queries |
| `rag_debug_api.py` | RAG diagnostics |
| `index_mgmt_api.py` | Index management |

**File & Content Management**
| Module | Purpose |
|--------|---------|
| `files_api.py` | File CRUD, directory operations |
| `upload_api.py` | File upload handling |
| `output_api.py` | Generated output retrieval |
| `content_management_api.py` | Content lifecycle |
| `docs_api.py` | Document management |
| `file_operations_api.py` | File operations |
| `excel_api.py` | Excel file processing |
| `csv_compare_api.py` | CSV comparison |

**Project & Configuration**
| Module | Purpose |
|--------|---------|
| `projects_api.py` | Project CRUD |
| `clients_api.py` | Client management |
| `agents_api.py` | Agent configuration |
| `rules_api.py` | System prompt (rule) management |
| `tools_api.py` | Tool registry |
| `jobs_api.py` | Background job management |
| `tasks_api.py` | Task management |
| `settings_api.py` | Application settings |

**System & Monitoring**
| Module | Purpose |
|--------|---------|
| `system_api.py` | System status |
| `meta_api.py` | Meta/self-inspection |
| `diagnostics_api.py` | Diagnostic endpoints |
| `health_api.py` | Health checks |
| `cache_api.py` / `cache_stats_api.py` | Cache management |
| `gpu_api.py` | GPU resource status |
| `log_api.py` | Log access |
| `celery_monitor_api.py` | Celery task monitoring |
| `backup_api.py` | Backup management |
| `reboot_api.py` | Graceful restart |

**Specialized**
| Module | Purpose |
|--------|---------|
| `voice_api.py` | STT/TTS via Whisper.cpp + Piper |
| `code_execution_api.py` | Safe code execution |
| `code_intelligence_api.py` | Code analysis |
| `automation_api.py` | Browser/desktop automation |
| `model_api.py` | LLM model management |
| `training_datasets_api.py` | Training data management |
| `plugins_api.py` | Plugin system |
| `websites_api.py` | Website scraping/management |
| `wordpress_api.py` | WordPress integration |
| `web_search_api.py` | Web search |
| `orchestrator_api.py` | Multi-agent orchestration |
| `task_scheduler_api.py` | Scheduled task management |
| `interconnector_api.py` | Cross-instance sync |
| `distributed_api.py` | Distributed processing |

### 5.2 Service Layer (50+ Modules)

Business logic, decoupled from HTTP concerns:

**Agent Services**
| Module | Purpose |
|--------|---------|
| `agent_executor.py` | ReACT loop — reasoning + tool invocation |
| `agent_router.py` | Route chat to specialized agents |
| `agent_config.py` | Agent configuration loading |
| `agent_tools.py` | Tool binding for agents |

**Generation Services**
| Module | Purpose |
|--------|---------|
| `batch_image_generator.py` | Diffusers image generation pipeline |
| `batch_video_generator.py` | CogVideoX video pipeline |
| `unified_file_generation.py` | Unified generation coordinator |
| `comfyui_video_generator.py` | ComfyUI video backend |
| `offline_image_generator.py` | Fully local image generation |
| `offline_video_generator.py` | Fully local video generation |

**Indexing & RAG Services**
| Module | Purpose |
|--------|---------|
| `indexing_service.py` | Primary LlamaIndex ingestion |
| `simple_indexing_service.py` | Lightweight indexing |
| `entity_indexing_service.py` | Entity extraction + indexing |
| `entity_relationship_indexer.py` | Entity graph indexing |
| `metadata_indexing_service.py` | Metadata-enriched indexing |
| `unified_chat_engine.py` | LlamaIndex chat engine wrapper |

**LLM & Content**
| Module | Purpose |
|--------|---------|
| `llm_service.py` | Ollama LLM client abstraction |
| `vision_chat_service.py` | Vision/multimodal chat |
| `unified_upload_service.py` | File ingestion coordinator |

**Content Processing**
| Module | Purpose |
|--------|---------|
| `excel_content_service.py` | Excel file processing |
| `image_content_service.py` | Image analysis |
| `wordpress_content_processor.py` | WordPress content transformation |
| `wordpress_content_puller.py` | WordPress content extraction |
| `wordpress_api_service.py` | WordPress API client |

**System Services**
| Module | Purpose |
|--------|---------|
| `hardware_service.py` | Hardware detection |
| `gpu_resource_coordinator.py` | GPU VRAM allocation |
| `resource_manager.py` | System resource management |
| `backup_service.py` | Full system backup |
| `orchestrator_service.py` | Multi-agent workflow coordination |
| `task_scheduler.py` | Cron-like task scheduling |

**Automation Services**
| Module | Purpose |
|--------|---------|
| `browser_automation_service.py` | Playwright browser control |
| `desktop_automation_service.py` | pyautogui desktop control |
| `mcp_client_service.py` | MCP protocol client |

**Task Handlers** (`services/task_handlers/`)
| Handler | Purpose |
|---------|---------|
| `base_handler.py` | Abstract handler base |
| `batch_image_handler.py` | Image batch processing |
| `code_operations_handler.py` | Code agent operations |
| `csv_generation_handler.py` | CSV generation pipeline |
| `indexing_handler.py` | Indexing task dispatch |
| `system_maintenance_handler.py` | Cleanup and maintenance |
| `web_research_handler.py` | Web scraping + research |

### 5.3 Tools Layer (Agent-callable)

Tools are registered in the agent executor and callable during the ReACT loop:

**Code Tools** (`tools/agent_tools/`)
- `code_execution_tools.py` — Execute Python/shell in sandbox
- `code_manipulation_tools.py` — Read/edit/search/verify code files
- `file_operation_tools.py` — File system CRUD
- `search_tools.py` — Semantic and text search

**Top-level Tools** (`tools/`)
- `code_tools.py` — Higher-level code operations
- `browser_tools.py` — Browser automation tools
- `desktop_tools.py` — Desktop automation tools
- `mcp_tools.py` — MCP protocol tools
- `rag_tools.py` — RAG query tools
- `web_tools.py` — Web scraping/search tools
- `system_tools.py` — System command execution
- `generation_tools.py` — Content generation tools
- `content_tools.py` — Content processing tools
- `llama_code_tools.py` — LlamaIndex code tools

**Voice Tools** (`tools/voice/`)
- Whisper.cpp binary (built from source) — Speech-to-text
- Piper TTS — Text-to-speech

**Video Tools** (`tools/video/`)
- CogVideoX model download + management

### 5.4 Utility Layer (75+ Modules)

Key utility groups:

**RAG Pipeline**
- `unified_index_manager.py` — Manages vector stores
- `advanced_retrieval_strategies.py` — Hybrid + reranking
- `enhanced_rag_chunking.py` — Intelligent chunking
- `hybrid_rag_pipeline.py` — BM25 + vector hybrid search
- `embedding_router.py` — CPU/GPU embedding dispatch
- `query_engine_wrapper.py` — LlamaIndex query abstraction
- `query_cache.py` — Query result caching
- `rag_evaluation_metrics.py` — RAG quality metrics

**Context Management**
- `context_manager.py` — Conversation context
- `context_bridge.py` — Cross-session context
- `context_variables.py` — Dynamic context injection
- `entity_context_enhancer.py` — Entity-based context enrichment

**Progress & Tracking**
- `unified_progress_system.py` — Unified job progress
- `progress_manager.py` — Progress state management
- `progress_emitter.py` — Socket.IO progress emission

**Chat Utilities**
- `chat_utils.py` — Chat helpers + default prompts
- `prompt_utils.py` — Prompt building utilities
- `prompt_templates.py` — System prompt templates
- `response_utils.py` — Response formatting helpers

**Data Processing**
- `csv_chunker.py` — Large CSV chunking
- `bulk_csv_generator.py` — Bulk CSV generation pipeline
- `bulk_xml_generator.py` — XML generation pipeline
- `enhanced_file_processor.py` — File preprocessing

### 5.5 Celery Tasks (Async)

All long-running operations execute as Celery tasks:

| Task Module | Operations |
|------------|-----------|
| `unified_task_executor.py` | Primary task dispatcher |
| `backup_tasks.py` | Automated backup tasks |
| `cleanup_tasks.py` | Data cleanup |
| `proven_csv_generation.py` | Production CSV generation |
| `repo_analysis_tasks.py` | Repository analysis |
| `task_scheduler_celery.py` | Cron-based scheduling |
| `training_tasks.py` | Model fine-tuning |

**Broker:** Redis (database 0, `redis://localhost:6379/0`)
**Monitoring:** `/api/health/celery`

### 5.6 Database Schema

SQLAlchemy models in `backend/models.py`:

**Core Entities**
| Model | Purpose |
|-------|---------|
| `Project` | Project containers |
| `Client` | Client organizations (with SEO fields) |
| `File` | Uploaded/managed files |
| `Document` | Indexed documents |
| `Conversation` | Chat sessions |
| `Message` | Individual chat messages |

**Configuration**
| Model | Purpose |
|-------|---------|
| `Rule` | System prompts / agent personas |
| `Agent` | Agent configurations |
| `Tool` | Tool registry entries |
| `Setting` / `SystemSetting` | Application settings |
| `Model` | LLM model registry |

**Operations**
| Model | Purpose |
|-------|---------|
| `Job` | Background job records |
| `Task` | Sub-task records |

**Integrations**
| Model | Purpose |
|-------|---------|
| `TrainingDataset` | Fine-tuning datasets |
| `WordPressSite` | WordPress connections |
| `WordPressPage` | Synced WordPress pages |

**Generation Records**
| Model | Purpose |
|-------|---------|
| Image generation records | Generation history |
| Video generation records | Video history |

**Migration management:** Alembic in `backend/migrations/`
**Database file:** `data/database/system_analysis.db`

### 5.7 Configuration System

All configuration in `backend/config.py`. Paths resolve relative to `GUAARDVARK_ROOT`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `GUAARDVARK_ROOT` | auto-detected | Project root |
| `DATABASE_PATH` | `data/database/system_analysis.db` | SQLite path |
| `UPLOAD_DIR` | `data/uploads/` | File uploads |
| `OUTPUT_DIR` | `data/outputs/` | Generated outputs |
| `CACHE_DIR` | `data/cache/` | Cache storage |
| `LOG_DIR` | `logs/` | Log files |
| `CONTEXT_PERSISTENCE_DIR` | `data/context/` | RAG context |

**Feature Flags**
| Flag | Env Var | Purpose |
|------|---------|---------|
| `ENHANCED_CONTEXT_ENABLED` | `GUAARDVARK_ENHANCED_MODE` | Enhanced context features |
| `ADVANCED_RAG_ENABLED` | — | Advanced retrieval |
| `RAG_DEBUG_ENABLED` | `GUAARDVARK_RAG_DEBUG` | RAG debugging endpoints |

**GPU**
`PYTORCH_CUDA_ALLOC_CONF="expandable_segments:False,max_split_size_mb:512"`

---

## 6. Frontend Architecture

### 6.1 Page Components (28 pages)

| Page | Purpose |
|------|---------|
| `DashboardPage` | System overview + quick actions |
| `ChatPage` | Primary chat interface |
| `VoiceChatPage` | Voice interaction |
| `CodeEditorPage` | Monaco-based code editor + agent |
| `DocumentsPage` | Document management (desktop UI) |
| `FilesPage` / `UploadPage` | File management + uploads |
| `ProjectsPage` / `ProjectDetailPage` | Project management |
| `ClientPage` | Client organization management |
| `ContentLibraryPage` | Generated content library |
| `ImagesPage` | Image output gallery |
| `BatchImageGeneratorPage` | Batch image generation |
| `VideoGeneratorPage` | Video generation |
| `FileGenerationPage` | AI-powered file generation |
| `BulkImportDocumentsPage` | Bulk document indexing |
| `RulesPage` | System prompt management |
| `AgentsPage` | Agent configuration |
| `ToolsPage` | Tool registry |
| `PluginsPage` | Plugin management |
| `SettingsPage` | Application settings |
| `TrainingPage` | Model fine-tuning |
| `WebsitesPage` | Website integration |
| `WordPressSitesPage` / `WordPressPagesPage` | WordPress management |
| `TaskPage` | Task scheduling |
| `DevToolsPage` | Developer tools |
| `ProgressTestPage` | Progress system testing |

### 6.2 Component Organization

**Chat System**
- `EnhancedChatInterface` — Main chat UI with streaming
- `StreamingMessage` — Token-by-token streaming display
- `MessageList` / `MessageItem` — Conversation rendering
- `ChatInput` — Input with voice, file attachment
- `ToolCallCard` — Agent tool call display
- `FloatingChatFAB` / `FloatingChatCard` / `FloatingChatProvider` — Floating chat overlay (available on all pages)

**Documents Desktop**
- `DocumentsDesktop` — Desktop metaphor file manager
- `FolderWindow` / `FolderContents` — Windowed folder views
- `DesktopItemsGrid` — Grid of file/folder icons
- `DocumentsContextMenu` — Right-click context menu

**File System**
- `FileManager` — Full file browser
- `FileSystemTree` — Directory tree
- `BreadcrumbNav` — Navigation breadcrumbs
- `CSVSpreadsheetViewer` — Inline CSV display

**Modals (25+ dialogs)**
Coverage includes: upload, job details, output preview, image/video models, RAG testing, settings, backup/restore, training, WordPress, linking, rule management, system metrics, kill switch, reboot progress, theme selector.

**Settings Sections**
- Model management
- RAG debug tools
- Voice settings
- Interconnector config
- Rules import/export
- Automated tests panel

**Voice**
- `VoiceChat` / `ContinuousVoiceChat` — Voice chat modes
- `AudioVisualizer` / `VolumeMeter` — Audio visualization
- `BackgroundWaveform` — Ambient waveform display

### 6.3 State Management

**Zustand Stores**
| Store | Purpose |
|-------|---------|
| `useAppStore.js` | Global: current project, settings, models, sidebar state |
| `useFloatingChatStore.js` | Floating chat: open/close, conversation state |

**Apollo Client**: GraphQL queries and mutations
**React Context**: Error boundaries, layout state

### 6.4 API Services (40+ modules in `src/api/`)

Organized by domain — each module wraps Axios calls to the corresponding backend API:

`chatService`, `unifiedChatService`, `agentsService`, `settingsService`, `projectService`, `clientService`, `documentService`, `filegenService`, `fileOperationsService`, `indexingService`, `outputService`, `bulkGenerationService`, `unifiedGenerationService`, `voiceService`, `codeAssistantService`, `codeExecutionService`, `codeIntelligenceService`, `ruleService`, `taskService`, `jobsService`, `pluginsService`, `trainingService`, `modelService`, `backupService`, `ragDebugService`, `websiteService`, `wordpressService`, `orchestratorService`, `interconnectorService`, `workflowService`, `progressService`, `stateService`, `sessionStateService`, `utilService`, `analyticsService`, `devtoolsService`, `bulkImportService`, `csvService`

### 6.5 Custom Hooks

| Hook | Purpose |
|------|---------|
| `useJobSocket` | Subscribe to job progress via Socket.IO |
| `useAudioRecorder` | MediaRecorder-based audio capture |
| `useAsyncOperation` | Loading/error state for async calls |
| `useErrorHandler` | Centralized error handling |
| `useNotification` | Snackbar notification management |
| `useAgentRouter` | Route to agent-specific UI |
| `usePageContext` | Page-level context access |

---

## 7. CLI Tool — `llx`

The `llx` CLI provides full platform access from the terminal.

### Design Goals
- Human-friendly: rich formatting, streaming output, REPL mode
- Machine-friendly: `--json` flag, pipe-compatible I/O
- Self-contained: single `llx init` setup wizard

### Command Surface

```
llx                          # Interactive REPL
llx init                     # First-run setup
llx status                   # System dashboard
llx health                   # Health check
llx chat "prompt"            # Chat (streaming)
llx chat --resume "prompt"   # Continue last session
llx chat --session ID "..."  # Resume specific session
llx chat --list              # List sessions
llx chat --export            # Export as markdown
llx chat --no-rag "prompt"   # Direct LLM (no RAG)
llx search "query"           # Semantic search
llx files list [--path /dir] # List files
llx files upload FILE        # Upload file
llx files download ID        # Download file
llx files delete ID          # Delete file
llx files mkdir NAME         # Create folder
llx projects list/create/delete/info
llx rules list/create/delete/export/import
llx agents list/info
llx generate csv "prompt" --output file.csv
llx generate image "prompt"
llx jobs list/status/watch/cancel
llx settings list/get/set
llx models list/active
```

### Module Structure

```
cli/llx/
├── main.py        # Typer app, global flags (--json, --server)
├── client.py      # LlxClient — unified HTTP abstraction
├── streaming.py   # Socket.IO chat streaming + job progress
├── config.py      # ~/.llx/config.json loading + llx init
├── output.py      # Rich tables/markdown + JSON mode + pipe detection
├── repl.py        # Interactive REPL with history
└── commands/
    ├── chat.py
    ├── files.py
    ├── search.py
    ├── projects.py
    ├── rules.py
    ├── agents.py
    ├── generate.py
    ├── jobs.py
    ├── settings.py
    └── system.py
```

---

## 8. Automation Tools

27 automation tools across three categories (added in v2.4.x):

### Browser Automation (8 tools)
Powered by Playwright via `browser_automation_service.py`:
- `navigate_to_url` — Open a URL
- `click_element` — Click by selector/text
- `fill_form_field` — Fill input fields
- `take_screenshot` — Capture page screenshot
- `get_page_content` — Extract page HTML/text
- `wait_for_element` — Wait for DOM element
- `scroll_page` — Scroll to position
- `execute_js` — Run JavaScript in page context

**Config:** `GUAARDVARK_BROWSER_AUTOMATION=true`, `GUAARDVARK_BROWSER_HEADLESS=true`

### Desktop Automation (13 tools)
Powered by pyautogui via `desktop_automation_service.py`:
- Mouse: `move_mouse`, `click_mouse`, `double_click`, `right_click`, `drag_mouse`
- Keyboard: `type_text`, `press_key`, `hotkey`
- Screen: `take_desktop_screenshot`, `find_on_screen`, `get_screen_size`
- Window: `focus_window`, `list_windows`

**Config:** `GUAARDVARK_DESKTOP_AUTOMATION=true` (disabled by default for security)

### MCP Integration (6 tools)
Via `mcp_client_service.py`:
- `list_mcp_servers` — List registered MCP servers
- `call_mcp_tool` — Invoke a tool on a server
- `get_mcp_resources` — List server resources
- `read_mcp_resource` — Read a resource
- `subscribe_mcp_events` — Subscribe to server events
- `unsubscribe_mcp_events` — Unsubscribe

**Config:** `GUAARDVARK_MCP_ENABLED=true`

**Specialized Automation Agents:**
- **Browser Agent** — Uses browser tools for web research and form automation
- **Desktop Agent** — Uses desktop tools for GUI automation tasks

---

## 9. Plugin System

Plugins extend Guaardvark capabilities without modifying core code.

**Plugin discovery:** `backend/utils/plugin_loader.py` scans `plugins/` for `plugin.json` manifests.

**Plugin API:** `backend/api/plugins_api.py` + frontend `PluginsPage`

### Current Plugins
| Plugin | Purpose |
|--------|---------|
| `gpu_embedding` | GPU-accelerated text embedding (faster indexing) |

### Plugin Structure
```
plugins/
└── my_plugin/
    ├── plugin.json     # Manifest: name, version, capabilities
    └── ...             # Plugin implementation files
```

---

## 10. RAG Pipeline

The RAG (Retrieval-Augmented Generation) pipeline is a core Guaardvark capability:

```
Document Ingestion
    │
    ▼
Enhanced File Processor (backend/utils/enhanced_file_processor.py)
    │  Parse: PDF, DOCX, Excel, CSV, HTML, plain text
    ▼
Enhanced RAG Chunker (backend/utils/enhanced_rag_chunking.py)
    │  Intelligent chunking with overlap
    ▼
Embedding Router (backend/utils/embedding_router.py)
    │  CPU: Ollama embeddings  │  GPU: gpu_embedding plugin
    ▼
Unified Index Manager (backend/utils/unified_index_manager.py)
    │  LlamaIndex VectorStoreIndex
    ▼
Vector Store (data/cache/ — LlamaIndex flat file store)

Query Time:
    │
    ▼
Hybrid RAG Pipeline (backend/utils/hybrid_rag_pipeline.py)
    │  BM25 keyword + vector semantic hybrid
    ▼
Advanced Retrieval (backend/utils/advanced_retrieval_strategies.py)
    │  Reranking, MMR, HyDE
    ▼
Query Cache (backend/utils/query_cache.py)
    │  Cache hit check
    ▼
Query Engine Wrapper (backend/utils/query_engine_wrapper.py)
    │  LlamaIndex query engine
    ▼
LLM (Ollama) → Response with citations
```

**Indexing Strategies:**
1. **Simple** — Standard chunked vector indexing
2. **Entity** — Extract named entities + index relationships
3. **Metadata** — Enrich chunks with document metadata

**Debug:** `GUAARDVARK_RAG_DEBUG=1` enables `/api/rag-debug/` endpoints

---

## 11. Agent System

Guaardvark's agent system enables autonomous multi-step task execution:

### Agent Executor (`services/agent_executor.py`)
Implements the ReACT (Reason + Act) loop:
1. LLM reasons about the task
2. Selects a tool from registry
3. Executes tool
4. Observes result
5. Repeat until task complete or iteration limit reached

### Agent Router (`services/agent_router.py`)
Routes incoming messages to specialized agents based on intent:

| Agent Type | Trigger | Capabilities |
|-----------|---------|-------------|
| Code Assistant | Code-related queries | Read/edit code, execute, search, verify |
| RAG Agent | Document queries | Search, retrieve, synthesize |
| Browser Agent | Web tasks | Navigate, scrape, interact |
| Desktop Agent | GUI tasks | Mouse, keyboard, screen capture |
| Orchestrator | Multi-step workflows | Coordinate sub-agents |
| General | Default | All tools |

### Tool Registry (`backend/tools/tool_registry_init.py`)
Registers all agent-callable tools at startup with schemas for LLM tool-calling.

### Self-Improvement Test Suite

A 4-layer automated test suite validates the agent's coding capabilities:

```
Layer 4: E2E Self-Improvement
    Planted bugs, code quality improvement, feature addition
Layer 3: Agent Executor
    ReACT loop, tool selection, iteration limits, error handling
Layer 2: Code Generation
    LLM-backed codegen, syntax validation, file modification
Layer 1: Code Tools (deterministic)
    read_code, search_code, edit_code, verify_change, list_files
```

Test files: `backend/tests/test_code_tools.py`, `test_code_generation.py`, `test_agent_executor.py`, `test_self_improvement.py`

---

## 12. Voice Processing Pipeline

```
Microphone → WebSocket (audio chunks) → Flask voice_api.py
    │
    ▼
Whisper.cpp (STT)
    │  Binary: backend/tools/voice/whisper.cpp/build/bin/whisper-cli
    │  Models: backend/tools/voice/whisper.cpp/models/
    ▼
Text → LLM / Agent
    │
    ▼
Piper TTS (TTS)
    │
    ▼
WebSocket (audio stream) → Browser playback
```

**Build:** Whisper.cpp is compiled from source on first startup
**FFmpeg:** Required for audio format conversion

---

## 13. Multi-Modal Generation

### Image Generation
- **Backend:** Diffusers + Transformers (HuggingFace)
- **Pipeline:** `services/batch_image_generator.py` → Celery task → `data/outputs/`
- **UI:** `BatchImageGeneratorPage`, `ImagesPage`
- **Batch:** `batch_image_generation_api.py` with job tracking

### Video Generation
- **Backend:** CogVideoX via ComfyUI (`services/comfyui_video_generator.py`)
- **Fallback:** Offline video generator for systems without ComfyUI
- **UI:** `VideoGeneratorPage`
- **GPU:** Requires significant VRAM; managed via `gpu_resource_coordinator.py`

### Content Generation (Text/CSV/XML)
- **CSV:** `bulk_csv_generator.py` → Celery → chunked generation with tracking
- **XML:** `bulk_xml_generator.py`
- **Enhanced:** `enhanced_context_generation_api.py` with entity enrichment
- **UI:** `FileGenerationPage`, `ContentLibraryPage`

---

## 14. WordPress Integration

Full two-way WordPress sync:

- `wordpress_api_service.py` — WordPress REST API client
- `wordpress_content_puller.py` — Pull pages/posts into Guaardvark
- `wordpress_content_processor.py` — Transform content for AI processing
- `websites_api.py` / `wordpress_api.py` — REST endpoints
- `WordPressSitesPage` / `WordPressPagesPage` — UI

Use cases: SEO content generation, bulk article creation, content analysis

---

## 15. Interconnector

Cross-instance synchronization system:

- `services/interconnector_sync_service.py` — State sync between instances
- `services/interconnector_file_sync_service.py` — File sync
- `api/interconnector_api.py` — REST endpoints
- `utils/interconnector_image_utils.py` — Image sync helpers

Configured via Settings → Interconnector.

---

## 16. Testing Infrastructure

### Test Structure
```
backend/tests/
├── unit/          (18 files) — Isolated unit tests, no external deps
├── integration/   (25 files) — Flask test client, real DB
├── system/        (5 files)  — Full server + Playwright E2E
└── (root level)   (7 files)  — Feature-specific tests
```

### Test Runner
```bash
python3 run_tests.py
# OR
GUAARDVARK_MODE=test python3 -m pytest backend/tests -vv
```

Results saved to `logs/test_results/`

### Key Test Coverage
| Area | Tests |
|------|-------|
| Rule/prompt management | `test_rules.py`, `test_rule_utils.py` |
| Backup system | `test_backup_service.py`, `test_full_backup.py` |
| Progress tracking | `test_progress_tracking.py`, `test_unified_progress_system.py` |
| Index management | `test_index_manager.py`, `test_index_cache.py` |
| Code tools | `test_code_tools.py` |
| Code generation | `test_code_generation.py` |
| Agent executor | `test_agent_executor.py` |
| Self-improvement | `test_self_improvement.py` |
| Automation | `test_automation_tools.py` |
| Security | `test_security_self_check.py` |
| API health | `test_health_endpoint.py`, `test_version_endpoint.py` |
| Route uniqueness | `test_route_uniqueness.py` |
| Chat UI | `playwright/test_chat_dashboard.py` |

---

## 17. Startup System

`start.sh` (v5.1) — environment-aware startup script:

```
start.sh
├── Kill previous instance (by GUAARDVARK_ROOT, PID files)
├── Install Python deps (backend/venv)
├── Install Node deps (frontend/node_modules)
├── Start Redis (if not running)
├── Start Ollama (if not running)
├── Run migration pre-flight check
│   ├── Auto-apply pending migrations
│   └── Abort on multiple heads (requires manual fix)
├── Build Whisper.cpp (if first run)
├── Auto-build frontend (if dist/ missing or source changed)
├── Start Flask backend (port 5000)
├── Start Celery workers
├── Start Vite dev server (port 5173)
└── Health checks: /api/health, /api/health/celery
```

**Flags:**
| Flag | Effect |
|------|--------|
| `--fast` | Skip dependency checks + builds |
| `--test` | Comprehensive health diagnostics |
| `--skip-migrations` | Skip migration pre-flight |
| `--no-auto-build` | Don't rebuild frontend |
| `--no-voice` | Skip voice API health check |

---

## 18. Key Integration Flows

### Chat with RAG
```
User message → enhanced_chat_api.py
→ agent_router.py (intent detection)
→ unified_chat_engine.py (RAG query)
→ hybrid_rag_pipeline.py (retrieval)
→ llm_service.py (Ollama completion)
→ Socket.IO stream → Browser
```

### Batch Image Generation
```
User request → batch_image_generation_api.py
→ Job record created → Redis/Celery
→ batch_image_handler.py → batch_image_generator.py
→ Diffusers pipeline → GPU
→ Output saved to data/outputs/
→ Socket.IO progress → Browser
```

### Agent Code Task
```
User request → agent_chat_api.py
→ agent_executor.py (ReACT loop)
→ Tool selection (code_manipulation_tools)
→ read_code / edit_code / execute_code / verify_change
→ Iterate until task complete
→ Result → Browser
```

### File Indexing
```
File upload → upload_api.py
→ unified_upload_service.py
→ enhanced_file_processor.py (parse)
→ indexing_service.py
→ enhanced_rag_chunking.py (chunk)
→ embedding_router.py (embed)
→ unified_index_manager.py (store)
→ LlamaIndex VectorStore
```

---

## 19. Security Considerations

- **Auth Guard:** `backend/utils/auth_guard.py` — API authentication
- **Input Validation:** `backend/utils/input_validation.py`, `frontend/src/utils/inputValidation.js`
- **Rate Limiting:** `backend/utils/rate_limiter.py`
- **Code Execution:** Sandboxed via `code_execution_tools.py`
- **Desktop Automation:** Disabled by default (`GUAARDVARK_DESKTOP_AUTOMATION=false`)
- **Password Validation:** `backend/utils/password_validation.py`
- **Security Self-Check:** `backend/tests/unit/test_security_self_check.py`

---

## 20. Business Context

### Architecture Quality
- Architecture sophistication: **8.5/10**
- 300+ backend Python files, 200+ frontend files
- Production-ready code quality
- Comprehensive test coverage (70+ test files)

### Competitive Advantages
1. **Fully self-hosted** — No cloud dependency, offline-capable
2. **Integrated multi-modal** — Text + image + video + voice in one system
3. **Content generation at scale** — Bulk CSV/XML/content pipelines
4. **Agent-based code assistant** — Self-improvement loop proven in production
5. **Enterprise features** — WordPress integration, client management, backup/restore

### Monetization Path (Recommended: Hybrid Open Core)
- **Phase 1:** Open-source core (Apache 2.0) + consulting ($10K–$25K/client)
- **Phase 2:** Commercial add-ons + SaaS ($49–$149/month)
- **Phase 3:** Community growth + SaaS scale
- **Year-1 Realistic:** $30K–$60K

---

## 21. Planned / In-Progress

| Feature | Status | Reference |
|---------|--------|-----------|
| `llx` CLI | Designed, implementation pending | `2026-02-24-llx-cli-design.md` |
| Guaardvark rebrand | Completed (v2.4.1) | `2026-02-25-guaardvark-rebrand.md` |
| Self-improvement test suite | Designed, scaffolding in place | `2026-02-26-self-improvement-test-suite-design.md` |
| Open-source release prep | Planning | `SALES DISCUSSION.md` |
| README files | Next step (this document is the source) | — |

---

## 22. Quick Reference

### Common Commands
```bash
./start.sh              # Start everything
./start.sh --fast       # Fast start
./stop.sh               # Stop everything
python3 run_tests.py    # Run test suite
python3 scripts/check_migrations.py  # Check DB migrations

# Backend
cd backend && source venv/bin/activate
flask db migrate -m "description"   # New migration
flask db upgrade                     # Apply migrations

# Frontend
cd frontend && npm run dev           # Dev server
cd frontend && npm run build         # Production build

# CLI (after install)
llx init
llx status
llx chat "hello"
```

### Health Endpoints
- `GET /api/health` — System health
- `GET /api/health/celery` — Task queue health
- `GET /api/version` — Version info

### Log Files
| Log | Contents |
|-----|---------|
| `logs/backend.log` | Flask application |
| `logs/backend_startup.log` | Startup messages |
| `logs/celery.log` | Celery workers |
| `logs/frontend.log` | Vite dev server |
| `logs/setup.log` | Dependency installation |
| `logs/test_results/` | Test run output |
