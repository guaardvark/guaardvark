import logging
import os
import tempfile
from pathlib import Path

try:
    from dotenv import load_dotenv
    _config_root = Path(__file__).resolve().parents[1]
    load_dotenv(_config_root / ".env", override=False)
except ImportError:
    pass

_default_root = Path(__file__).resolve().parents[1]
_env_root = os.environ.get("GUAARDVARK_ROOT")
if _env_root:
    candidate = Path(_env_root)
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        if os.access(str(candidate), os.W_OK):
            GUAARDVARK_ROOT = candidate
        else:
            print(
                f"WARNING: GUAARDVARK_ROOT '{candidate}' is not writable. Using '{_default_root}'."
            )
            GUAARDVARK_ROOT = _default_root
    except Exception:
        print(
            f"WARNING: Failed to use GUAARDVARK_ROOT '{candidate}'. Using '{_default_root}'."
        )
        GUAARDVARK_ROOT = _default_root
else:
    GUAARDVARK_ROOT = _default_root
os.environ["GUAARDVARK_ROOT"] = str(GUAARDVARK_ROOT)
GUAARDVARK_MODE = os.environ.get("GUAARDVARK_MODE", "default")
GUAARDVARK_PROJECT_NAME = os.environ.get("GUAARDVARK_PROJECT_NAME", "Guaardvark")


def _resolve_path(env_var: str, default_relative: str) -> str:
    path = os.environ.get(env_var, default_relative)
    p = Path(path)
    if not p.is_absolute():
        p = GUAARDVARK_ROOT / p
    return str(p)


STORAGE_DIR = _resolve_path("GUAARDVARK_STORAGE_DIR", "data")
UPLOAD_DIR = _resolve_path("GUAARDVARK_UPLOAD_DIR", "data/uploads")
OUTPUT_DIR = _resolve_path("GUAARDVARK_OUTPUT_DIR", "data/outputs")
CACHE_DIR = _resolve_path("GUAARDVARK_CACHE_DIR", "data/cache")
LOG_DIR = _resolve_path("GUAARDVARK_LOG_DIR", "logs")
BACKUP_DIR = _resolve_path("GUAARDVARK_BACKUP_DIR", "backups")

# Video Generation / ComfyUI configuration
COMFYUI_URL = os.environ.get("GUAARDVARK_COMFYUI_URL", "http://127.0.0.1:8188")
COMFYUI_DIR = os.environ.get("GUAARDVARK_COMFYUI_DIR", os.path.join(GUAARDVARK_ROOT, "plugins", "comfyui", "ComfyUI"))
COMFYUI_VENV = os.environ.get("GUAARDVARK_COMFYUI_VENV", os.path.join(GUAARDVARK_ROOT, "backend", "venv"))
COMFYUI_OUTPUT_DIR = os.environ.get("COMFYUI_OUTPUT_DIR", os.path.join(OUTPUT_DIR, "video"))
VIDEO_GENERATION_BACKEND = os.environ.get("GUAARDVARK_VIDEO_BACKEND", "auto")  # "comfyui" | "offline" | "auto"
COMFYUI_IDLE_TIMEOUT = int(os.environ.get("GUAARDVARK_COMFYUI_IDLE_TIMEOUT", "1800"))

_config_logger = logging.getLogger(__name__)
_config_logger.info(f"Config initialized - GUAARDVARK_ROOT: {GUAARDVARK_ROOT}")
_config_logger.info(f"Config initialized - STORAGE_DIR: {STORAGE_DIR}")

ENHANCED_CONTEXT_ENABLED = os.environ.get("GUAARDVARK_ENHANCED_CONTEXT", "true").lower() == "true"
ADVANCED_RAG_ENABLED = os.environ.get("GUAARDVARK_ADVANCED_RAG", "true").lower() == "true"
RAG_DEBUG_ENABLED = os.environ.get("GUAARDVARK_RAG_DEBUG", "true").lower() == "true"
CONTEXT_PERSISTENCE_DIR = _resolve_path("GUAARDVARK_CONTEXT_DIR", "data/context")

AGENT_BRAIN_ENABLED = os.environ.get("GUAARDVARK_AGENT_BRAIN", "true").lower() == "true"

BROWSER_AUTOMATION_ENABLED = os.environ.get("GUAARDVARK_BROWSER_AUTOMATION", "true").lower() == "true"
BROWSER_HEADLESS = os.environ.get("GUAARDVARK_BROWSER_HEADLESS", "true").lower() == "true"
BROWSER_MAX_PAGES = int(os.environ.get("GUAARDVARK_BROWSER_MAX_PAGES", "5"))
BROWSER_TIMEOUT = int(os.environ.get("GUAARDVARK_BROWSER_TIMEOUT", "30000"))

DESKTOP_AUTOMATION_ENABLED = os.environ.get("GUAARDVARK_DESKTOP_AUTOMATION", "false").lower() == "true"
GUI_AUTOMATION_ENABLED = os.environ.get("GUAARDVARK_GUI_AUTOMATION", "false").lower() == "true"
AGENT_BROWSER = os.environ.get("GUAARDVARK_AGENT_BROWSER", "")  # firefox|chromium|chrome (auto-detected if empty)

ALLOWED_AUTOMATION_PATHS = [
    str(GUAARDVARK_ROOT / "data"),
    os.path.expanduser("~/Documents"),
    os.path.expanduser("~/Downloads"),
    "/tmp",
]
if os.environ.get("GUAARDVARK_ALLOWED_PATHS"):
    ALLOWED_AUTOMATION_PATHS.extend(os.environ.get("GUAARDVARK_ALLOWED_PATHS").split(":"))

ALLOWED_APPS = [
    "code", "code-insiders",
    "firefox", "firefox-esr", "chrome", "chromium", "chromium-browser",
    "gnome-terminal", "konsole", "xterm", "alacritty", "kitty",
    "nautilus", "dolphin", "thunar", "nemo",
    "gedit", "kate", "nano", "vim",
    "libreoffice", "gimp", "inkscape",
]
if os.environ.get("GUAARDVARK_ALLOWED_APPS"):
    ALLOWED_APPS.extend(os.environ.get("GUAARDVARK_ALLOWED_APPS").split(":"))

MCP_ENABLED = os.environ.get("GUAARDVARK_MCP_ENABLED", "true").lower() == "true"
MCP_TIMEOUT = int(os.environ.get("GUAARDVARK_MCP_TIMEOUT", "30"))
MCP_SERVERS_CONFIG = os.environ.get("GUAARDVARK_MCP_SERVERS", "{}")

# Uncle Claude configuration
CLAUDE_API_ENABLED = os.environ.get("GUAARDVARK_CLAUDE_API_ENABLED", "true").lower() == "true"
CLAUDE_DEFAULT_MODEL = os.environ.get("GUAARDVARK_CLAUDE_MODEL", "claude-sonnet-4-20250514")
CLAUDE_MAX_OUTPUT_TOKENS = int(os.environ.get("GUAARDVARK_CLAUDE_MAX_TOKENS", "4096"))
CLAUDE_MONTHLY_TOKEN_BUDGET = int(os.environ.get("GUAARDVARK_CLAUDE_TOKEN_BUDGET", "1000000"))
CLAUDE_ESCALATION_MODE = os.environ.get("GUAARDVARK_CLAUDE_ESCALATION_MODE", "manual")  # manual, smart, always

# Self-improvement configuration
SELF_IMPROVEMENT_ENABLED = os.environ.get("GUAARDVARK_SELF_IMPROVEMENT", "false").lower() == "true"
SELF_IMPROVEMENT_INTERVAL_HOURS = int(os.environ.get("GUAARDVARK_SELF_IMPROVEMENT_INTERVAL", "6"))
SELF_HEALING_ERROR_THRESHOLD = int(os.environ.get("GUAARDVARK_SELF_HEALING_THRESHOLD", "3"))
SELF_HEALING_WINDOW_MINUTES = int(os.environ.get("GUAARDVARK_SELF_HEALING_WINDOW", "60"))

# KV Cache optimization
COMPACTION_THRESHOLD = float(os.environ.get("GUAARDVARK_COMPACTION_THRESHOLD", "0.7"))
CHUNK_SIMILARITY_THRESHOLD = float(os.environ.get("GUAARDVARK_CHUNK_SIMILARITY_THRESHOLD", "0.85"))

# RAG Autoresearch configuration
AUTORESEARCH_ENABLED = os.environ.get("GUAARDVARK_AUTORESEARCH_ENABLED", "true").lower() == "true"
AUTORESEARCH_IDLE_MINUTES = int(os.environ.get("GUAARDVARK_AUTORESEARCH_IDLE_MINUTES", "10"))
AUTORESEARCH_MAX_EXPERIMENT_DURATION = 300  # 5 minutes, matching Karpathy's time budget
AUTORESEARCH_MAX_LLM_CALLS_PER_EXPERIMENT = 200
AUTORESEARCH_PHASE_PLATEAU_THRESHOLD = 10  # consecutive discards before phase advance
AUTORESEARCH_MIN_CORPUS_SIZE = 10  # minimum indexed documents to enable
AUTORESEARCH_SHADOW_CORPUS_SIZE = 100  # documents in shadow eval corpus
AUTORESEARCH_EVAL_PAIR_TARGET = 100  # target eval pairs per generation
AUTORESEARCH_STALENESS_SAMPLE_RATE = 0.1  # fraction of pairs to spot-check
AUTORESEARCH_STALENESS_THRESHOLD = 0.2  # fraction of stale pairs triggering regen

# Default RAG experiment parameters
AUTORESEARCH_DEFAULT_PARAMS = {
    # Phase 1 — query-time
    "top_k": 5,
    "dedup_threshold": 0.85,
    "context_window_chunks": 3,
    "reranking_enabled": False,
    "query_expansion": False,
    "hybrid_search_alpha": 0.0,
    # Phase 2 — index-time
    "chunk_size": 1000,
    "chunk_overlap": 200,
    "use_semantic_splitting": False,
    "use_hierarchical_splitting": False,
    "extract_entities": False,
    "preserve_structure": False,
}

PROTECTED_RAG_PARAMS = []  # params autoresearch cannot touch (user-configurable)

# Protected files (cannot be modified by self-improvement)
PROTECTED_FILES = [
    "backend/services/claude_advisor_service.py",
    "backend/services/self_improvement_service.py",
    "backend/services/tool_execution_guard.py",
    "backend/tools/agent_tools/code_manipulation_tools.py",
    "backend/app.py",
    "backend/config.py",
    "backend/models.py",
    "killswitch.sh",
    "stop.sh",
    "start.sh",
]

UPLOAD_FOLDER = UPLOAD_DIR
CLIENT_LOGO_FOLDER = str(Path(UPLOAD_DIR) / "logos")
SYSTEM_DIR = str(Path(STORAGE_DIR) / "system")

if GUAARDVARK_MODE == "test":
    tmp_root = Path(tempfile.gettempdir()) / "guaardvark_test"
    UPLOAD_DIR = str(tmp_root / "uploads")
    UPLOAD_FOLDER = UPLOAD_DIR
    CLIENT_LOGO_FOLDER = str(Path(UPLOAD_DIR) / "logos")
    SYSTEM_DIR = str(Path(STORAGE_DIR) / "system")
    STORAGE_DIR = str(tmp_root / "storage")
    OUTPUT_DIR = str(tmp_root / "outputs")
    CACHE_DIR = str(tmp_root / "cache")
    LOG_DIR = str(tmp_root / "logs")
    BACKUP_DIR = str(tmp_root / "backups")

INDEX_ROOT = STORAGE_DIR

DEFAULT_INDEX_PROJECT_ID = None

# PostgreSQL is the default database.
# DATABASE_URL is set automatically by start_postgres.sh in .env,
# or can be overridden manually for advanced setups.
_DEFAULT_DATABASE_URL = "postgresql://guaardvark:guaardvark@localhost:5432/guaardvark"

_env_db_url = os.environ.get("DATABASE_URL")
if _env_db_url:
    allowed_schemes = ["postgresql", "postgres"]
    if any(_env_db_url.startswith(f"{scheme}://") for scheme in allowed_schemes):
        DATABASE_URL = _env_db_url
        _config_logger.info(f"Using DATABASE_URL from environment: {_env_db_url[:50]}...")
    else:
        _config_logger.warning(
            f"DATABASE_URL has unsupported scheme: {_env_db_url[:20]}... "
            f"Falling back to default PostgreSQL."
        )
        DATABASE_URL = _DEFAULT_DATABASE_URL
else:
    DATABASE_URL = _DEFAULT_DATABASE_URL
    _config_logger.info(f"Using default DATABASE_URL: {DATABASE_URL[:50]}...")

DEFAULT_LLM = None
DEFAULT_EMBEDDING_MODEL = None
OLLAMA_BASE_URL = "http://localhost:11434"
ACTIVE_MODEL_FILE = os.path.join(STORAGE_DIR, "active_model.json")

# GPU Memory Orchestrator settings
GPU_QUALITY_TIER = os.environ.get("GUAARDVARK_GPU_QUALITY_TIER", "balanced")
GPU_EVICTION_GRACE_SECONDS = int(os.environ.get("GUAARDVARK_GPU_EVICTION_GRACE", "30"))
GPU_IDLE_TIMEOUT_SECONDS = int(os.environ.get("GUAARDVARK_GPU_IDLE_TIMEOUT", "300"))


def _read_saved_model_name():
    """Read the user's saved model choice from active_model.json (no DB, no Flask context needed)."""
    import json
    try:
        if os.path.isfile(ACTIVE_MODEL_FILE):
            with open(ACTIVE_MODEL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                name = data.get("active_model", "").strip() if isinstance(data, dict) else ""
                if name:
                    return name
    except Exception:
        pass
    return None


def get_default_llm():
    """Return the active LLM model name.

    Priority: saved user choice (if model still available) > preference list > first text model.
    """
    try:
        import requests
        from backend.utils.ollama_resource_manager import is_text_chat_model

        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        if response.status_code != 200:
            saved = _read_saved_model_name()
            return saved or "llama3.1:latest"

        models = response.json().get('models', [])
        available_names = {m.get('name', '') for m in models}

        # 1. Prefer the user's saved model if it's still available in Ollama
        saved = _read_saved_model_name()
        if saved and saved in available_names:
            return saved

        # 2. Fall back to preference list
        preferred_patterns = [
            'llama3.1',
            'llama3',
            'gemma',
            'phi',
            'mistral',
            'qwen',
        ]

        for pattern in preferred_patterns:
            for model in models:
                name = model.get('name', '').lower()
                if not is_text_chat_model(name):
                    continue
                if pattern in name and ':' in name:
                    return model.get('name')

        # 3. Any text chat model
        for model in models:
            name = model.get('name', '').lower()
            if is_text_chat_model(name):
                return model.get('name')

    except Exception:
        pass

    saved = _read_saved_model_name()
    return saved or "llama3.1:latest"


def get_embedding_vram_estimates() -> dict:
    return {
        "qwen3-embedding:8b": {"vram_mb": 6000, "dimensions": 4096},
        "qwen3-embedding-8b": {"vram_mb": 6000, "dimensions": 4096},
        "qwen3-embedding:4b": {"vram_mb": 3000, "dimensions": 2560},
        "qwen3-embedding-4b": {"vram_mb": 3000, "dimensions": 2560},
        "embeddinggemma:latest": {"vram_mb": 800, "dimensions": 768},
        "embeddinggemma": {"vram_mb": 800, "dimensions": 768},
        "qwen3-embedding:0.6b": {"vram_mb": 500, "dimensions": 1024},
        "qwen3-embedding-0.6b": {"vram_mb": 500, "dimensions": 1024},
        "nomic-embed-text": {"vram_mb": 400, "dimensions": 768},
        "all-minilm": {"vram_mb": 150, "dimensions": 384},
    }


def _get_gpu_vram_info() -> dict:
    import subprocess
    result = {"total_vram_mb": 0, "used_by_models_mb": 0, "budget_mb": 0}

    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if proc.returncode == 0 and proc.stdout.strip():
            result["total_vram_mb"] = int(proc.stdout.strip().split("\n")[0])
    except Exception:
        pass

    try:
        import requests
        ps_response = requests.get(f"{OLLAMA_BASE_URL}/api/ps", timeout=5)
        if ps_response.status_code == 200:
            for model in ps_response.json().get("models", []):
                name = model.get("name", "").lower()
                if "embed" not in name and "minilm" not in name:
                    vram_bytes = model.get("size_vram", 0)
                    result["used_by_models_mb"] += vram_bytes // (1024 * 1024)
    except Exception:
        pass

    safety_margin_mb = 500
    result["budget_mb"] = max(0, result["total_vram_mb"] - result["used_by_models_mb"] - safety_margin_mb)
    return result


def get_active_embedding_model() -> str:
    # Check if user has explicitly set an embedding model (via Settings UI)
    # Try DB first, then fall back to env var, then auto-selection
    try:
        from backend.models import Setting, db
        if db and Setting:
            setting = db.session.get(Setting, "active_embedding_model")
            if setting and setting.value:
                _config_logger.info(f"Using user-selected embedding model: {setting.value}")
                return setting.value
    except Exception as e:
        _config_logger.debug(f"DB not available for embedding model lookup: {e}")

    # Env var override (useful when DB is not ready at startup)
    env_model = os.environ.get("GUAARDVARK_EMBEDDING_MODEL")
    if env_model:
        _config_logger.info(f"Using embedding model from env var: {env_model}")
        return env_model

    try:
        import requests
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        available_models = [m["name"] for m in response.json().get("models", [])]
    except Exception:
        return "nomic-embed-text"

    vram_estimates = get_embedding_vram_estimates()

    try:
        vram_info = _get_gpu_vram_info()
        total_vram = vram_info["total_vram_mb"]
        budget = vram_info["budget_mb"]
        has_gpu = total_vram > 0
    except Exception:
        total_vram = 0
        budget = 0
        has_gpu = False

    # Candidate models ordered by quality. Includes 1024-dim group
    # (mxbai, snowflake, bge) which are interchangeable without reindexing.
    candidate_models = [
        "mxbai-embed-large",
        "snowflake-arctic-embed:l",
        "bge-m3",
        "snowflake-arctic-embed2",
        "qwen3-embedding:4b",
        "qwen3-embedding-4b",
        "qwen3-embedding:8b",
        "qwen3-embedding-8b",
        "embeddinggemma:latest",
        "embeddinggemma",
        "nomic-embed-text",
        "qwen3-embedding:0.6b",
        "qwen3-embedding-0.6b",
        "all-minilm",
    ]

    def _model_matches(candidate, available):
        c = candidate.lower()
        a = available.lower()
        a_base = a.split(":")[0]
        return c == a or c == a_base or c.split(":")[0] == a_base

    if has_gpu:
        _config_logger.info(
            f"VRAM-aware embedding selection: total={total_vram}MB, "
            f"used_by_chat={vram_info['used_by_models_mb']}MB, budget={budget}MB"
        )
        for candidate in candidate_models:
            est = vram_estimates.get(candidate, {}).get("vram_mb", 99999)
            if est > budget:
                continue
            for available in available_models:
                if _model_matches(candidate, available):
                    _config_logger.info(
                        f"Selected embedding model: {available} "
                        f"(est. {est}MB, budget {budget}MB)"
                    )
                    return available
    else:
        _config_logger.info("No GPU detected — selecting CPU-friendly embedding model")
        for candidate in reversed(candidate_models):
            for available in available_models:
                if _model_matches(candidate, available):
                    _config_logger.info(f"Selected CPU embedding model: {available}")
                    return available

    if available_models:
        return available_models[0]
    return "nomic-embed-text"


def get_default_embedding_model():
    try:
        from llama_index.embeddings.ollama import OllamaEmbedding

        model_name = get_active_embedding_model()
        logging.getLogger(__name__).info(f"Using Ollama embedding model: {model_name}")

        return OllamaEmbedding(
            model_name=model_name,
            base_url="http://localhost:11434",
        )
    except Exception as e:
        raise RuntimeError(
            f"Failed to initialize embedding model: {e}. "
            f"Please ensure Ollama is running with an embedding model available."
        ) from e


if DEFAULT_LLM is None:
    try:
        DEFAULT_LLM = get_default_llm()
    except Exception as _llm_err:
        logging.warning("Could not initialize default LLM: %s", _llm_err)
        DEFAULT_LLM = None
if DEFAULT_EMBEDDING_MODEL is None:
    try:
        DEFAULT_EMBEDDING_MODEL = get_default_embedding_model()
    except Exception as _embed_err:
        logging.warning("Could not initialize default embedding model: %s", _embed_err)
        DEFAULT_EMBEDDING_MODEL = None

_llm_timeout_env = os.getenv("GUAARDVARK_LLM_REQUEST_TIMEOUT")
LLM_REQUEST_TIMEOUT = float(_llm_timeout_env) if _llm_timeout_env else 7200.0
if _llm_timeout_env:
    logging.info(
        "GUAARDVARK_LLM_REQUEST_TIMEOUT overridden via environment: %s", LLM_REQUEST_TIMEOUT
    )

INDEXING_USE_CUDA = os.getenv("GUAARDVARK_INDEXING_USE_CUDA", "auto").lower()

if INDEXING_USE_CUDA not in ["auto", "force_cpu", "force_cuda"]:
    logging.warning(f"Invalid INDEXING_USE_CUDA value: {INDEXING_USE_CUDA}. Using 'auto'.")
    INDEXING_USE_CUDA = "auto"

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    if GUAARDVARK_MODE == "production":
        raise ValueError("SECRET_KEY environment variable must be set in production mode")
    else:
        SECRET_KEY = "dev-secret-key-change-in-production"
        logging.warning("Using default SECRET_KEY for development. Set SECRET_KEY environment variable for production.")

METRICS_LOG_LEVEL = os.environ.get("GUAARDVARK_METRICS_LOG_LEVEL", "WARNING").upper()

AGENTIC_MAX_TOKENS_FINAL = int(os.environ.get("GUAARDVARK_AGENTIC_MAX_TOKENS", "4096"))
AGENTIC_HISTORY_LIMIT = int(os.environ.get("GUAARDVARK_AGENTIC_HISTORY_LIMIT", "30"))

CHAT_HISTORY_LIMIT_FOR_ENGINE = (
    40
)
CHAT_HISTORY_MAX_TOKENS_FOR_ENGINE = (
    3072
)
CHAT_MEMORY_TOKEN_LIMIT = (
    4096
)

PROJECT_INDEX_MODE = os.environ.get("GUAARDVARK_PROJECT_INDEX_MODE", "global").lower()

DISABLE_CELERY = os.environ.get("DISABLE_CELERY", "false").lower() == "true"

# GPU embedding plugin was removed — the EmbeddingRouter now handles
# GPU/CPU routing natively via Ollama num_gpu=0 for the CPU path.
# Legacy stubs kept for backward compatibility with any remaining imports.
def is_gpu_embedding_plugin_enabled() -> bool:
    return False

def is_gpu_embedding_service_available() -> bool:
    return False

GPU_EMBEDDING_SERVICE_URL = ""
GPU_EMBEDDING_TIMEOUT = 30
GPU_EMBEDDING_FALLBACK_ENABLED = True
