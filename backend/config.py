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

# The active profile fills in env defaults for anything .env left unset. It
# runs here, before the first flag below is read, because several services
# re-parse os.environ themselves at import. workstation sets nothing.
from backend.profiles import active_profile as _active_profile, apply_env as _apply_profile_env
_apply_profile_env(_active_profile())

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
def _comfyui_default_url() -> str:
    """Effective ComfyUI URL from the plugin manifest.

    plugin.local.json (untracked per-install override) beats plugin.json,
    so a custom port — e.g. an external ComfyUI Desktop on 8000 — set once
    survives updates and drives every backend consumer.
    """
    import json as _json
    for name in ("plugin.local.json", "plugin.json"):
        manifest = os.path.join(GUAARDVARK_ROOT, "plugins", "comfyui", name)
        try:
            with open(manifest, "r", encoding="utf-8") as f:
                port = int(_json.load(f).get("port"))
        except (OSError, ValueError, TypeError):
            continue
        return f"http://127.0.0.1:{port}"
    return "http://127.0.0.1:8188"

COMFYUI_URL = os.environ.get("GUAARDVARK_COMFYUI_URL") or _comfyui_default_url()
COMFYUI_DIR = os.environ.get("GUAARDVARK_COMFYUI_DIR", os.path.join(GUAARDVARK_ROOT, "plugins", "comfyui", "ComfyUI"))
COMFYUI_VENV = os.environ.get("GUAARDVARK_COMFYUI_VENV", os.path.join(GUAARDVARK_ROOT, "backend", "venv"))
COMFYUI_OUTPUT_DIR = os.environ.get("COMFYUI_OUTPUT_DIR", os.path.join(OUTPUT_DIR, "video"))
VIDEO_GENERATION_BACKEND = os.environ.get("GUAARDVARK_VIDEO_BACKEND", "auto")  # "comfyui" | "offline" | "auto"
COMFYUI_IDLE_TIMEOUT = int(os.environ.get("GUAARDVARK_COMFYUI_IDLE_TIMEOUT", "1800"))

# Google Indexing API (Search Console URL submission)
# Path to the service-account JSON key. Defaults inside the project; override
# with GOOGLE_INDEXING_KEY_PATH to point at an existing key elsewhere.
GOOGLE_INDEXING_KEY_PATH = _resolve_path(
    "GOOGLE_INDEXING_KEY_PATH", "data/credentials/google-indexing.json"
)
# Default per-site daily submission cap (Indexing API quota is 200/day/project;
# we stay under it). Per-site overrides live in the GoogleIndexingConfig table.
GOOGLE_INDEXING_DAILY_CAP = int(os.environ.get("GOOGLE_INDEXING_DAILY_CAP", "190"))

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

# Film Crew / Production Pipeline improvements
FILM_CREW_LIPSYNC_ENABLED = os.environ.get("GUAARDVARK_FILM_CREW_LIPSYNC", "false").lower() == "true"
FILM_CREW_PARALLEL_RENDER = os.environ.get("GUAARDVARK_FILM_CREW_PARALLEL", "false").lower() == "true"

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

# Per-model dedup cosine thresholds. Cosine-similarity distributions differ by embedding
# model, so a single global threshold mis-dedups (drops everything or nothing) when the
# active model changes. Match on a substring of the active model name; unknown models fall
# back to CHUNK_SIMILARITY_THRESHOLD. Calibrate new entries with the RAG eval harness —
# do NOT guess values. The global env var still overrides everything.
CHUNK_SIMILARITY_THRESHOLDS_BY_MODEL = {
    "nomic-embed-text": 0.85,  # historical default this constant was tuned against
}


def get_dedup_threshold(model_name: str) -> float:
    """Resolve the near-duplicate cosine threshold for the active embedding model.

    Falls back to CHUNK_SIMILARITY_THRESHOLD for any model without a calibrated entry
    (and logs once-uncalibrated at debug), so behavior is never worse than the legacy
    global threshold.
    """
    name = (model_name or "").lower()
    for key, val in CHUNK_SIMILARITY_THRESHOLDS_BY_MODEL.items():
        if key in name:
            return val
    return CHUNK_SIMILARITY_THRESHOLD

# RAG Autoresearch configuration
AUTORESEARCH_ENABLED = os.environ.get("GUAARDVARK_AUTORESEARCH_ENABLED", "true").lower() == "true"
AUTORESEARCH_IDLE_MINUTES = int(os.environ.get("GUAARDVARK_AUTORESEARCH_IDLE_MINUTES", "10"))
AUTORESEARCH_MAX_EXPERIMENT_DURATION = 300  # floor for the per-experiment deadline (seconds)
# The real deadline is max(floor, HEADROOM x pairs x measured seconds per pair).
# Measured 2026-08-30 on gemma4 12B via Ollama: the 18-pair baseline took ~600s,
# so a fixed 300s crashed every experiment at exactly 300.7s. F1 judges a subset
# and F2 may re-run the full set, hence 2x the full-set estimate.
AUTORESEARCH_EXPERIMENT_DEADLINE_HEADROOM = 2.0
# Before any pair cost has been measured (fresh box, or a baseline cached by an
# older build) the floor is a trap: on 2026-08-30 the F0 retrieval screen alone
# took ~400s because Ollama swapped the 12B answer model and the 4B embedding
# model on every pair, and three experiments in a row died at "calls=0". The
# first experiment gets this instead; every later one uses measurements.
AUTORESEARCH_EXPERIMENT_DEADLINE_UNMEASURED = 1800
AUTORESEARCH_MAX_LLM_CALLS_PER_EXPERIMENT = 200
AUTORESEARCH_PHASE_PLATEAU_THRESHOLD = 10  # consecutive discards before phase advance
# Keep bar matches run-end confirmation (research_run_service). Any smaller
# positive delta is LLM-judge jitter, not a real improvement — measured
# against the 2026-08 overnight ledgers where 0.001 keeps promoted noise.
AUTORESEARCH_KEEP_MIN_DELTA = 0.05
# TPE-lite needs a handful of scored trials before it beats LLM/random.
AUTORESEARCH_TPE_MIN_HISTORY = 8
# Drop parse-failed judge pairs from the mean; crash the experiment if more
# than this fraction of the judged set failed to parse.
AUTORESEARCH_PARSE_FAIL_CRASH_RATIO = 0.5
# Bounds that keep the loop finite and paced. A single loop invocation may never
# run unbounded (the 2026-08-07..10 runaway spun ~3,500 no-op experiments/second
# for 3.4 days = 134M rows, because 0 eval pairs made each "experiment" a 1ms
# discard and max_experiments=0 meant forever). Real evals take minutes, so the
# floor costs nothing; a degenerate fast-fail can no longer spin.
AUTORESEARCH_MAX_EXPERIMENTS_PER_RUN = 25   # cap when caller passes 0/unbounded
AUTORESEARCH_MIN_EXPERIMENT_INTERVAL = 5.0  # seconds between experiments, minimum
AUTORESEARCH_MIN_CORPUS_SIZE = 10  # minimum indexed documents to enable
AUTORESEARCH_SHADOW_CORPUS_SIZE = 100  # documents in shadow eval corpus
# Overnight default. 100 pairs × (answer + judge) on local Ollama ate a 6h
# window for ~10 noisy trials (2026-08). 30 pairs + a 20-pair F1 judge
# subset leaves room for >20 trials in 6h. Explicit regenerate may still
# request up to AUTORESEARCH_EVAL_PAIR_REGENERATE_MAX.
AUTORESEARCH_EVAL_PAIR_TARGET = 30
AUTORESEARCH_EVAL_PAIR_REGENERATE_MAX = 100
AUTORESEARCH_JUDGE_SUBSET = 20  # F1 LLM-judge sample; F2 is the full active set
AUTORESEARCH_STALENESS_SAMPLE_RATE = 0.1  # fraction of pairs to spot-check
AUTORESEARCH_STALENESS_THRESHOLD = 0.2  # fraction of stale pairs triggering regen

# Default RAG experiment parameters. These MUST mirror actual production
# behavior (the values retrieval uses when nothing is promoted), so that an
# experiment's baseline measures the system users actually experience:
# rerank defaults ON (GUAARDVARK_RERANK_ENABLED default), alpha matches the
# GUAARDVARK_HYBRID_SEARCH_ALPHA default, chat returns 3 chunks.
AUTORESEARCH_DEFAULT_PARAMS = {
    # Phase 1 — query-time
    "top_k": 5,
    "dedup_threshold": 0.85,
    "context_window_chunks": 3,
    "reranking_enabled": True,
    "query_expansion": False,
    "hybrid_search_alpha": 0.3,
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
    "backend/services/guarded_code_service.py",
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
# Use 127.0.0.1, not "localhost": on macOS "localhost" resolves to both ::1 and
# 127.0.0.1, and if Ollama is bound IPv4-only (the default for Ollama.app and
# `brew services`), a connect to the ::1 address can fail — so the backend "can't see"
# an Ollama that other apps reach fine. 127.0.0.1 is unambiguous. Override with the
# OLLAMA_BASE_URL env var (full URL) to point Guaardvark at an Ollama elsewhere.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
ACTIVE_MODEL_FILE = os.path.join(STORAGE_DIR, "active_model.json")

# --- Cloud LLM providers (optional, opt-in; OFF by default) -------------------
# Local Ollama is always the default. Cloud providers are gated behind a master
# "cloud_models_enabled" DB setting (see services/llm_provider.py) AND the
# provider's API key being present below. Keys live in .env (never the DB).
# Embeddings/RAG always stay on local Ollama regardless of the chat provider.
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "").strip()
MISTRAL_BASE_URL = os.environ.get("GUAARDVARK_MISTRAL_BASE_URL", "https://api.mistral.ai/v1").rstrip("/")
MISTRAL_DEFAULT_MODEL = os.environ.get("GUAARDVARK_MISTRAL_MODEL", "mistral-large-latest").strip()
MISTRAL_REQUEST_TIMEOUT = int(os.environ.get("GUAARDVARK_MISTRAL_TIMEOUT", "120"))

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


def _hardware_default_llm() -> str:
    """Hardware-aware hard fallback for the default chat model.

    Mirrors the get_chat_keep_alive / default_advanced_rag hardware-detection pattern
    in this file: on a small box (≤8GB RAM) or ARM (aarch64/arm64), a fresh install
    should default to a 1-3B tag so first-run chat actually loads; otherwise the
    standard vision-capable Gemma4 default (gemma4:e2b — the model the agentic
    system is validated against). GUAARDVARK_DEFAULT_LLM always overrides.
    Detection failure stays defensive and never crashes.
    """
    env = os.environ.get("GUAARDVARK_DEFAULT_LLM")
    if env:
        return env
    try:
        import platform
        arch = platform.machine().lower()
        ram_gb = 0
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        ram_gb = int(line.split()[1]) / 1024 / 1024
                        break
        except OSError:
            ram_gb = 0
        if arch in ("aarch64", "arm64") or (0 < ram_gb <= 8):
            return "llama3.2:1b"
    except Exception:
        pass
    return "gemma4:e2b"


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
            return saved or _hardware_default_llm()

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
    return saved or _hardware_default_llm()


def get_embedding_vram_estimates() -> dict:
    return {
        "qwen3-embedding:4b-q4_K_M": {"vram_mb": 2400, "dimensions": 2560},
        "qwen3-embedding": {"vram_mb": 2400, "dimensions": 2560},
        "embeddinggemma:latest": {"vram_mb": 800, "dimensions": 768},
        "embeddinggemma": {"vram_mb": 800, "dimensions": 768},
        "nomic-embed-text": {"vram_mb": 400, "dimensions": 768},
        "all-minilm": {"vram_mb": 150, "dimensions": 384},
    }


def get_embedding_keep_alive():
    """Hardware-aware keep_alive for embedding-model Ollama clients.

    - GPU present: a short TTL (env GUAARDVARK_EMBED_KEEP_ALIVE_GPU, default "5m") so the
      model frees VRAM after idle but isn't reloaded on every query within a session.
    - No GPU: keep resident (env GUAARDVARK_EMBED_KEEP_ALIVE_CPU, default "-1") so a
      CPU-resident model isn't reloaded from disk every idle cycle. RAM-pressure eviction is
      handled separately by the orchestrator — so "resident" does NOT mean "pinned forever".
    Returns an int when the value is numeric (Ollama: seconds, -1 = forever), else the string.
    """
    try:
        from backend.services.gpu_resource_coordinator import has_gpu
        gpu = has_gpu()
    except Exception:
        gpu = False
    val = (os.environ.get("GUAARDVARK_EMBED_KEEP_ALIVE_GPU", "5m") if gpu
           else os.environ.get("GUAARDVARK_EMBED_KEEP_ALIVE_CPU", "-1"))
    try:
        return int(val)
    except (TypeError, ValueError):
        return val


def get_chat_keep_alive():
    """Hardware-aware keep_alive for the chat LLM Ollama clients.

    The chat model (~10-12GB) used to be pinned "24h" at every instantiation, which made
    it squat the GPU on this shared 16GB box — the documented root cause of image/video
    CUDA OOM (decision 2026-06-01: move to a short TTL). This mirrors get_embedding_keep_alive:
    - GPU present: a short TTL (env GUAARDVARK_CHAT_KEEP_ALIVE_GPU, default "15m") so the
      model frees VRAM after idle and the GPU orchestrator's eviction actually sticks instead
      of being re-pinned on the next chat call. A ~1-2s reload after >15m idle is acceptable UX.
    - No GPU: keep resident (env GUAARDVARK_CHAT_KEEP_ALIVE_CPU, default "-1") so a CPU box
      isn't reloading the model from disk every idle cycle. RAM-pressure eviction is handled
      separately by the orchestrator — "resident" does NOT mean "pinned forever".
    Returns an int when numeric (Ollama: seconds, -1 = forever), else the string (e.g. "15m").
    """
    try:
        from backend.services.gpu_resource_coordinator import has_gpu
        gpu = has_gpu()
    except Exception:
        gpu = False
    val = (os.environ.get("GUAARDVARK_CHAT_KEEP_ALIVE_GPU", "15m") if gpu
           else os.environ.get("GUAARDVARK_CHAT_KEEP_ALIVE_CPU", "-1"))
    try:
        return int(val)
    except (TypeError, ValueError):
        return val


def default_advanced_rag() -> bool:
    """Hardware-aware default for advanced_rag when neither DB setting nor env var is set.
    ON (vector RAG) with a GPU; OFF on CPU-only so a fresh Pi/laptop install isn't running
    heavy vector retrieval out of the box. Env GUAARDVARK_ADVANCED_RAG always overrides."""
    env = os.environ.get("GUAARDVARK_ADVANCED_RAG")
    if env is not None:
        return env.lower() == "true"
    try:
        from backend.services.gpu_resource_coordinator import has_gpu
        return has_gpu()
    except Exception:
        return True


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
        "qwen3-embedding:4b-q4_K_M",
        "snowflake-arctic-embed:l",
        "bge-m3",
        "snowflake-arctic-embed2",
        "embeddinggemma:latest",
        "embeddinggemma",
        "nomic-embed-text",
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
            base_url=OLLAMA_BASE_URL,
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

# ---- Cluster Foundation (spec §6.2) ------------------------------------
CLUSTER_ENABLED = os.getenv("CLUSTER_ENABLED", "false").lower() in ("1", "true", "yes")
CLUSTER_ROLE = os.getenv("CLUSTER_ROLE", "solo")  # solo | master | worker
CLUSTER_MASTER_URL = os.getenv("CLUSTER_MASTER_URL", "")
CLUSTER_NODE_ID = os.getenv("CLUSTER_NODE_ID", "")  # set by start.sh from hardware.json
CLUSTER_MASTER_NODE_ID = os.getenv("CLUSTER_MASTER_NODE_ID", "")
CLUSTER_SWEEP_INTERVAL_S = int(os.getenv("CLUSTER_SWEEP_INTERVAL_S", "5"))
CLUSTER_HEARTBEAT_TIMEOUT_S = int(os.getenv("CLUSTER_HEARTBEAT_TIMEOUT_S", "15"))

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
