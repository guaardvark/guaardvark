# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Guaardvark** (Version 2.4.1) is a full-stack AI application platform that integrates LLM capabilities with content generation, RAG (Retrieval-Augmented Generation), and multi-modal AI processing.

Official site: https://guaardvark.com

### Technology Stack

**Backend:**
- Flask 3.0.0 with Flask-SocketIO (real-time WebSocket communication)
- SQLAlchemy 3.1.1 + Alembic migrations (SQLite)
- Celery 5.4.0 + Redis 5.0.4 (async task processing)
- Ariadne 0.26.2 (GraphQL API)
- LlamaIndex 0.12.43 with Ollama integration (RAG/LLM)
- PyTorch 2.2.2 (ML/AI)
- Diffusers 0.31.0, Transformers 4.48.0 (image/video generation)
- Piper TTS, Whisper.cpp (voice processing)

**Frontend:**
- React 18.2.0 + Vite 4.5.0
- Material-UI v5.15.0 (design system)
- Zustand 4.4.0 (state management)
- Apollo Client 4.0.7 (GraphQL), Axios 1.6.0 (REST), Socket.io-client 4.7.0
- Monaco Editor 0.53.0 (code editing)

**System Requirements:**
- Python 3.12+
- Node.js 20+
- Redis 5.0+
- FFmpeg (for voice processing)
- Ollama (optional, for local LLMs)

## Common Development Commands

### Starting the Application

```bash
# Full startup with health checks and auto-build
./start.sh

# Fast startup (skip dependency checks and builds)
./start.sh --fast

# Startup with comprehensive health diagnostics
./start.sh --test

# Skip database migrations check
./start.sh --skip-migrations

# Disable automatic frontend rebuild
./start.sh --no-auto-build

# Skip voice API health check
./start.sh --no-voice
```

The `start.sh` script (v5.1) handles:
- Killing previous instances from this environment
- Installing Python/Node dependencies on first run
- Ensuring Ollama and Redis services are running
- Running database migration pre-flight checks
- Building Whisper.cpp for voice processing
- Auto-building frontend if source files changed
- Starting Flask backend (port 5000)
- Starting Celery workers
- Starting Vite frontend (port 5173)
- Running health checks

**Access URLs:**
- Frontend: http://localhost:5173
- Backend API: http://localhost:5000
- Health Check: http://localhost:5000/api/health

### Stopping the Application

```bash
./stop.sh
```

### Testing

```bash
# Run all backend tests
python3 run_tests.py

# Run specific test file
python3 -m pytest backend/tests/test_rules.py -vv

# Run tests in test mode (uses in-memory database)
GUAARDVARK_MODE=test python3 -m pytest backend/tests -vv
```

The `run_tests.py` script automatically:
- Installs requirements and Playwright browsers
- Checks and applies database migrations
- Runs pytest with proper environment variables
- Saves test results to `logs/test_results/`

### Database Migrations

```bash
# Check migration status
python3 scripts/check_migrations.py

# Create new migration
cd backend
source venv/bin/activate
flask db migrate -m "description"

# Apply migrations
flask db upgrade

# Check current revision
alembic current
```

**Important:** The startup script runs a pre-flight migration check that will:
- Detect pending migrations and auto-apply them
- Detect multiple heads and abort (requires manual fix)
- Skip checks if `GUAARDVARK_SKIP_MIGRATIONS=1` is set

### Frontend Development

```bash
cd frontend

# Install dependencies
npm install

# Development server (with hot reload)
npm run dev -- --host --port=5173

# Production build
npm run build

# Preview production build
npm run preview

# Lint code
npm run lint
```

### Backend Development

```bash
cd backend

# Activate virtual environment
source venv/bin/activate

# Install/update dependencies
pip install -r requirements.txt

# Run Flask directly (for debugging)
export FLASK_APP=backend.app
export GUAARDVARK_ROOT=/path/to/guaardvark
flask run --debug --host=0.0.0.0 --port=5000

# Start Celery worker manually
celery -A backend.celery_app.celery worker --loglevel=info --concurrency=2
```

### Celery Management

```bash
# Start Celery workers
./start_celery.sh

# View Celery logs
tail -f logs/celery.log

# Monitor Celery tasks (via backend API)
curl http://localhost:5000/api/health/celery
```

## Architecture

### High-Level Structure

```
Guaardvark/
├── backend/           # Flask application
│   ├── api/          # 60+ API endpoint modules (REST + GraphQL)
│   ├── services/     # 34+ business logic services
│   ├── tasks/        # Celery background tasks
│   ├── tools/        # Utility tools (voice, video, code)
│   ├── utils/        # Helper modules
│   ├── routes/       # Upload/download routes
│   ├── handlers/     # Database handlers
│   ├── migrations/   # Alembic database migrations
│   └── tests/        # Test suite
├── frontend/         # React/Vite application
│   ├── src/
│   │   ├── pages/    # 31 page components
│   │   ├── components/ # UI components (organized by feature)
│   │   ├── contexts/ # React contexts
│   │   ├── stores/   # Zustand stores
│   │   ├── hooks/    # Custom React hooks
│   │   ├── utils/    # Helper functions
│   │   └── api/      # API client configuration
│   └── dist/         # Production build output
├── data/             # Application data storage
│   ├── database/     # SQLite databases
│   ├── uploads/      # User-uploaded files
│   ├── outputs/      # Generated outputs
│   ├── cache/        # Cached data
│   └── context/      # Context persistence
├── logs/             # Log files
├── scripts/          # Utility scripts
└── docs/             # Documentation
```

### Key Architectural Patterns

1. **Modular API Layer**: 60+ specialized API modules under `backend/api/` handle distinct concerns:
   - Chat APIs: `enhanced_chat_api.py`, `agent_chat_api.py`, `simple_chat_api.py`
   - Generation: `unified_generation_api.py`, `batch_image_generation_api.py`, `batch_video_generation_api.py`
   - RAG/Indexing: `indexing_api.py`, `entity_indexing_api.py`, `metadata_indexing_api.py`, `search_api.py`
   - Content: `files_api.py`, `upload_api.py`, `output_api.py`, `content_management_api.py`
   - System: `agents_api.py`, `tools_api.py`, `projects_api.py`, `rules_api.py`, `jobs_api.py`

2. **Service Layer Pattern**: Business logic lives in `backend/services/`:
   - Agent services: `agent_executor.py`, `agent_router.py`, `llm_service.py`
   - Generation services: `batch_image_generator.py`, `batch_video_generator.py`, `unified_file_generation.py`
   - Indexing services: `indexing_service.py`, `entity_indexing_service.py`, `entity_relationship_indexer.py`
   - Content services: `image_content_service.py`, `wordpress_content_processor.py`

3. **Async Task Processing**: Celery tasks in `backend/tasks/` handle long-running operations:
   - Background jobs run via Redis message broker
   - Task status tracked via `/api/health/celery` endpoint

4. **Real-time Communication**: Flask-SocketIO provides WebSocket communication:
   - Event handlers in `backend/socketio_events.py`
   - Socket instance in `backend/socketio_instance.py`
   - Frontend connects via Socket.io-client

5. **RAG Pipeline**: LlamaIndex-based retrieval system:
   - Vector storage managed by `unified_index_manager.py`
   - Entity extraction and relationship indexing
   - Multiple indexing strategies (simple, entity, metadata)
   - Debug support via `rag_debug_api.py`

6. **Multi-modal AI Processing**:
   - Image generation: Diffusers + Transformers (local or ComfyUI)
   - Video generation: CogVideoX via `comfyui_video_generator.py`
   - Voice: Whisper.cpp (STT) + Piper TTS
   - LLM: Ollama integration via LlamaIndex

### Configuration System

All paths resolve relative to `GUAARDVARK_ROOT` (project root):

```python
# backend/config.py defines:
GUAARDVARK_ROOT          # Project root directory
DATABASE_PATH        # SQLite database: data/database/system_analysis.db
STORAGE_DIR          # data/
UPLOAD_DIR           # data/uploads/
OUTPUT_DIR           # data/outputs/
CACHE_DIR            # data/cache/
LOG_DIR              # logs/
CONTEXT_PERSISTENCE_DIR  # data/context/
```

**Environment Variables** (`.env`):
- `GUAARDVARK_ROOT` - Project root (enforced by start.sh)
- `FLASK_PORT` - Backend port (default: 5000)
- `VITE_PORT` - Frontend port (default: 5173)
- `GUAARDVARK_ENHANCED_MODE` - Enable enhanced features
- `GUAARDVARK_CONTEXT_PERSISTENCE` - Enable context persistence
- `GUAARDVARK_RAG_DEBUG` - Enable RAG debugging
- `GUAARDVARK_SKIP_MIGRATIONS` - Skip migration checks
- `REDIS_URL` - Redis connection (default: redis://localhost:6379/0)
- `CELERY_BROKER_URL` - Celery broker URL
- `CELERY_RESULT_BACKEND` - Celery results backend

**Feature Flags** (backend/config.py):
- `ENHANCED_CONTEXT_ENABLED` - Enhanced context features
- `ADVANCED_RAG_ENABLED` - Advanced RAG features
- `RAG_DEBUG_ENABLED` - RAG debugging endpoints

### Database Schema

SQLAlchemy models defined in `backend/models.py` (v1.10):
- Projects, Clients, Files, Documents
- Conversations, Messages
- Rules (system prompts), Agents, Tools
- Jobs, Tasks
- Training datasets
- WordPress sites and pages
- Image/video generation records

**Migration management:**
- Alembic migrations in `backend/migrations/`
- Pre-flight checks via `scripts/check_migrations.py`
- Auto-upgrade during startup (unless `--skip-migrations`)

### Frontend Architecture

**State Management:**
- Zustand stores in `src/stores/` for global state
- React Context in `src/contexts/` (StatusContext, LayoutContext)
- Apollo Client for GraphQL state

**Routing:**
- React Router v6 with nested routes
- 31 page components in `src/pages/`
- Lazy loading for performance

**UI Components:**
- Material-UI components with custom theming
- Dark/light mode support via `theme.js`
- Component organization by feature area
- Monaco Editor integration for code editing

**API Communication:**
- Apollo Client for GraphQL queries/mutations
- Axios for REST endpoints
- Socket.io-client for real-time updates
- API configuration in `src/api/` and `src/apollo/`

## Important Notes for Development

### Environment Isolation

The `start.sh` script is **environment-aware** - it only kills processes from the current installation:
- Checks process working directory against `GUAARDVARK_ROOT`
- Uses PID files in `pids/` directory
- Won't interfere with other Guaardvark instances on the same machine

### Path Resolution

**Always use GUAARDVARK_ROOT** for path resolution:
- Backend: Import paths from `backend.config`
- Never hardcode absolute paths
- All data directories are relative to GUAARDVARK_ROOT

### Database Migrations

**Before modifying models:**
1. Read `backend/models.py` to understand current schema
2. Make model changes
3. Generate migration: `flask db migrate -m "description"`
4. Review generated migration in `backend/migrations/versions/`
5. Test migration: `flask db upgrade`
6. Run pre-flight check: `python3 scripts/check_migrations.py`

**If multiple heads detected:**
```bash
cd backend
source venv/bin/activate
flask db merge heads -m "merge heads"
```

### GPU Memory Management

The startup script sets `PYTORCH_CUDA_ALLOC_CONF="expandable_segments:False,max_split_size_mb:512"` to prevent CUDA memory allocation errors during heavy GPU operations (CogVideoX, etc.).

### Redis Database Isolation

Guaardvark uses Redis database 0. If running multiple instances, configure different Redis databases via `REDIS_URL`.

### Logging

All logs go to `logs/` directory:
- `backend.log` - Backend application logs
- `backend_startup.log` - Flask startup logs
- `celery.log` - Celery worker logs
- `frontend.log` - Vite dev server logs
- `setup.log` - Dependency installation logs
- `test_results/` - Test execution results

### Voice Processing

Whisper.cpp is built from source on first startup:
- Source: `backend/tools/voice/whisper.cpp/`
- Binary: `backend/tools/voice/whisper.cpp/build/bin/whisper-cli`
- Library: `backend/tools/voice/whisper.cpp/build/src/libwhisper.so.1`
- Models: Downloaded to `backend/tools/voice/whisper.cpp/models/`

### Frontend Build System

Vite automatically rebuilds when source files change:
- Development: `npm run dev` with hot module replacement
- Production: `npm run build` creates optimized bundle in `dist/`
- The startup script auto-builds if `dist/` is missing or stale

### Adding New API Endpoints

1. Create module in `backend/api/` (e.g., `my_feature_api.py`)
2. Define Blueprint and routes
3. Register Blueprint in `backend/app.py` (in `create_app()`)
4. Add service logic to `backend/services/` if needed
5. Add tests to `backend/tests/`

### Adding New Frontend Pages

1. Create page component in `frontend/src/pages/`
2. Add route in `frontend/src/App.jsx`
3. Create associated components in `frontend/src/components/`
4. Add API calls in `frontend/src/api/` or use Apollo for GraphQL
5. Update navigation in sidebar component if needed

### Working with Rules/Prompts

Rules are system prompts managed via `backend/rule_utils.py`:
- Default fallback prompt defined in `backend/utils/chat_utils.py`
- Global default rule: `GLOBAL_DEFAULT_SYSTEM_PROMPT_RULE_NAME`
- CRUD operations via `rules_api.py`
- Frontend UI: `RulesPage.jsx`

### Celery Task Development

1. Define task in `backend/tasks/` or inline with `@celery.task`
2. Import Celery instance from `backend.celery_app`
3. Use `task.delay()` or `task.apply_async()` to queue
4. Monitor via `/api/health/celery` endpoint
5. View logs in `logs/celery.log`
