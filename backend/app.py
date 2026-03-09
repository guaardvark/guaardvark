

import importlib
import os
import shutil
import subprocess
import sys
import time
from typing import Optional

# Prevent dual-import when running as `python -m backend.app`.
# Python sets __name__ = '__main__' but doesn't add 'backend.app' to sys.modules,
# so any `from backend.app import X` would re-import the module, creating a second
# Flask app and corrupting shared state (socketio, db._app_engines WeakKeyDict, etc).
if __name__ == "__main__" and "backend.app" not in sys.modules:
    sys.modules["backend.app"] = sys.modules["__main__"]

import redis
from celery import Celery
from flask import Flask, jsonify, request, redirect
from flask_executor import Executor
from flask_socketio import SocketIO

try:
    from flask_cors import CORS
except Exception:

    def CORS(*_args, **_kwargs):
        return None


from flask_sqlalchemy import SQLAlchemy

try:
    from flask_migrate import Migrate
except Exception:

    class Migrate:
        def __init__(self, *a, **k):
            pass


import json
import logging
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

import click
import requests
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from backend.utils.chat_utils import (
    DEFAULT_FALLBACK_SYSTEM_PROMPT,
    GLOBAL_DEFAULT_SYSTEM_PROMPT_RULE_NAME,
)

import backend.api.voice_api

try:
    from dotenv import load_dotenv
except Exception:

    def load_dotenv(*_args, **_kwargs):
        return False


import numpy as _np

import backend.config as config
from backend import rule_utils
from backend.utils.project_config import load_config

from packaging import version
if version.parse(_np.__version__) < version.parse("1.26.0"):
    print(
        f"WARNING: NumPy version {_np.__version__} may be incompatible; recommended 1.26.4+",
        file=sys.stderr,
    )

try:
    import torch
    if torch.cuda.is_available():
        from backend.cuda_config import configure_cuda_optimizations
        cuda_status = configure_cuda_optimizations(verbose=True)
        print(f"CUDA optimizations applied: {', '.join(cuda_status.get('optimizations_applied', []))}")
    else:
        print("CUDA not available - running in CPU mode")
except ImportError as e:
    print(f"PyTorch not available or CUDA config import failed: {e}")
except Exception as e:
    print(f"Warning: CUDA optimization failed (non-critical): {e}")

__version__ = "1.19.5"

backend_dir = os.path.dirname(os.path.abspath(__file__))
project_root = str(config.GUAARDVARK_ROOT)
load_dotenv(os.path.join(project_root, ".env"), override=False)
PROJECT_CONFIG = load_config()

LOG_DIR = str(config.LOG_DIR)
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE_PATH = os.path.join(LOG_DIR, "backend.log")

root_logger = logging.getLogger()

for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)
    handler.close()

file_event_handler = TimedRotatingFileHandler(
    LOG_FILE_PATH,
    when="midnight",
    interval=1,
    backupCount=10,
    encoding="utf-8",
    delay=False,
    utc=False,
)
formatter = logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s [PID:%(process)d TID:%(thread)d] : %(message)s"
)
file_event_handler.setFormatter(formatter)
_adv_debug = os.getenv("ADVANCED_DEBUG", "false").lower() == "true"
debug_env = os.getenv("FLASK_DEBUG", "false").lower() == "true" or _adv_debug
log_level_env = os.getenv("BACKEND_LOG_LEVEL")
base_log_level = (
    getattr(logging, log_level_env.upper(), logging.INFO)
    if log_level_env
    else (logging.DEBUG if debug_env else logging.WARNING)
)
file_event_handler.setLevel(base_log_level)

root_logger.addHandler(file_event_handler)
root_logger.setLevel(base_log_level)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
console_handler.setLevel(base_log_level)
root_logger.addHandler(console_handler)

werkzeug_level = os.getenv("WERKZEUG_LOG_LEVEL")
if werkzeug_level:
    logging.getLogger("werkzeug").setLevel(
        getattr(logging, werkzeug_level.upper(), logging.WARNING)
    )

root_logger.info(f"[STARTUP] Logging initialized. Log file: {LOG_FILE_PATH}")

# LLM Debug logger — separate file, always DEBUG level (gated by setting check in helper)
LLM_DEBUG_LOG_PATH = os.path.join(LOG_DIR, "llm_debug.log")
_llm_debug_handler = TimedRotatingFileHandler(
    LLM_DEBUG_LOG_PATH, when="midnight", interval=1, backupCount=10,
    encoding="utf-8", delay=False, utc=False,
)
_llm_debug_handler.setLevel(logging.DEBUG)
_llm_debug_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s"
))
_llm_debug_logger = logging.getLogger("guaardvark.llm_debug")
_llm_debug_logger.addHandler(_llm_debug_handler)
_llm_debug_logger.setLevel(logging.DEBUG)
_llm_debug_logger.propagate = False
root_logger.info(f"[STARTUP] LLM debug logger initialized. Log file: {LLM_DEBUG_LOG_PATH}")
werkzeug_log_level = os.getenv("WERKZEUG_LOG_LEVEL", "WARNING").upper()
werkzeug_logger = logging.getLogger("werkzeug")
werkzeug_logger.setLevel(getattr(logging, werkzeug_log_level, logging.WARNING))

try:
    from backend.utils.llama_index_local_config import force_local_llama_index_config
    force_local_llama_index_config()
    root_logger.info(" Forced local LlamaIndex configuration before imports")
except ImportError as e:
    root_logger.error(f"Could not import local LlamaIndex config: {e}")
except Exception as e:
    root_logger.error(f"Failed to force local LlamaIndex config: {e}")

try:
    from llama_index.core import (
        PromptTemplate,
        Settings,
        StorageContext,
        VectorStoreIndex,
        load_index_from_storage,
    )
    from llama_index.llms.ollama import Ollama

    llama_index_imported_successfully = True
except ImportError as e:
    root_logger.critical(f"Failed to import LlamaIndex components: {e}")
    sys.exit("CRITICAL: LlamaIndex is not installed or failed to import. The application cannot start.")

root_logger.info("Using LlamaIndex local embeddings only")

logger_module = logging.getLogger(__name__)


def _ensure_redis_running(redis_url: str, fatal: bool = True) -> None:
    if not redis or not redis_url.startswith("redis://"):
        return
    try:
        redis.from_url(redis_url).ping()
        return
    except Exception:
        root_logger.error(
            "Redis is not running. Start with redis-server or update your REDIS_URL."
        )
        started = False
        if shutil.which("redis-server"):
            try:
                subprocess.Popen(
                    ["redis-server", "--daemonize", "yes"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(1)
                redis.from_url(redis_url).ping()
                started = True
                root_logger.info("redis-server started automatically")
            except Exception as err:
                root_logger.error("Failed to start redis-server automatically: %s", err)
        else:
            root_logger.error("redis-server command not found")
        if not started and fatal:
            root_logger.critical("Redis unavailable and could not be started")
            raise SystemExit("Redis server required but not running")


def _ensure_redis_client():
    try:
        redis_url = os.environ.get(
            "CELERY_BROKER_URL",
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        )

        client = redis.from_url(redis_url, socket_connect_timeout=3)
        client.ping()
        return client

    except Exception as e:
        root_logger.error(f"Failed to create Redis client: {e}")
        return None


def make_celery(flask_app: Flask) -> Celery:
    broker_url = os.environ.get(
        "CELERY_BROKER_URL",
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    )
    result_backend = os.environ.get("CELERY_RESULT_BACKEND", broker_url)

    if config.DISABLE_CELERY:
        root_logger.warning("Celery/Redis disabled by environment.")
        celery_app = Celery(
            flask_app.import_name,
            broker="memory://",
            backend="cache+memory://",
        )
        celery_app.conf.task_always_eager = True
    else:
        _ensure_redis_running(broker_url, fatal=True)
        celery_app = Celery(
            flask_app.import_name,
            broker=broker_url,
            backend=result_backend,
        )

    celery_app.conf.update(flask_app.config)
    celery_app.autodiscover_tasks(["backend"], related_name="celery_tasks_isolated")

    class ContextTask(celery_app.Task):
        abstract = True

        def __call__(self, *args, **kwargs):
            with flask_app.app_context():
                return super().__call__(*args, **kwargs)

    celery_app.Task = ContextTask
    return celery_app


try:
    from backend.config import (
        CLIENT_LOGO_FOLDER,
        DATABASE_URL,
        DEFAULT_EMBEDDING_MODEL,
        DEFAULT_LLM,
        LLM_REQUEST_TIMEOUT,
        OLLAMA_BASE_URL,
        OUTPUT_DIR,
        SECRET_KEY,
        STORAGE_DIR,
        UPLOAD_FOLDER,
    )

    logger_module.info(
        f"LLM request timeout configured for {LLM_REQUEST_TIMEOUT} seconds"
    )

    from backend.models import Model, Rule, Setting, db
    from backend.services.indexing_service import get_or_create_index
    from backend.utils import index_manager, llm_service, prompt_utils, progress_manager
except ImportError as e:
    logger_module.critical(
        f"CRITICAL: Failed to load core config or utilities: {e}", exc_info=True
    )
    sys.exit(
        "CRITICAL: App initialization failed due to config/model/util import errors."
    )


def create_app():
    app = Flask(__name__)
    app.url_map.strict_slashes = False

    _initialize_app_components(app)

    return app

def _initialize_app_components(app):
    global start_time, executor, socketio, celery

    start_time = time.time()
    executor = Executor(app)
    app.executor = executor
    from backend.socketio_instance import socketio, FRONTEND_URL
    celery = make_celery(app)
    
    app.logger.setLevel(
        root_logger.level
    )  # Ensure app.logger respects the root logger's level

    flask_env = os.getenv("FLASK_ENV", "development")
    if flask_env == "production":
        app.debug = False
        app.logger.info("Debug mode forcefully disabled in production environment")
    else:
        app.debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
        if app.debug:
            app.logger.warning("Debug mode is enabled - this should only be used in development")
    
    app.logger.info(f"Using database URL: {DATABASE_URL}")
    
    app.config.update(
        {
            "SQLALCHEMY_DATABASE_URI": DATABASE_URL,
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            "SECRET_KEY": os.getenv("SECRET_KEY", "dev-secret-key"),
            "UPLOAD_FOLDER": config.UPLOAD_DIR,
            "CLIENT_LOGO_FOLDER": config.CLIENT_LOGO_FOLDER,
            "STORAGE_DIR": config.STORAGE_DIR,
            "OUTPUT_DIR": config.OUTPUT_DIR,
            "CACHE_DIR": config.CACHE_DIR,
            "LLM_REQUEST_TIMEOUT": LLM_REQUEST_TIMEOUT,
            "MAX_CONTENT_LENGTH": 100 * 1024 * 1024,
        }
    )
    app.logger.info(
        f"Backend application version {__version__} starting..."
    )
    app.logger.info(
        f"CORS policy configured to allow specific origins."
    )

    if flask_env == "production":
        allowed_origins = [FRONTEND_URL]
        supports_credentials = True
        app.logger.info(f"Production CORS: Allowing only {FRONTEND_URL}")
    else:
        allowed_origins = [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://localhost:5175",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:5175",
            FRONTEND_URL,
        ]
        supports_credentials = True
        app.logger.info(f"Development CORS: Allowing {len(allowed_origins)} origins")

    interconnector_master_mode = False
    try:
        from backend.api.interconnector_api import _get_config
        interconnector_config = _get_config()
        if interconnector_config:
            if interconnector_config.get("node_mode") == "master":
                interconnector_master_mode = True
                import re
                lan_patterns = [
                    r"http://192\.168\.\d+\.\d+:\d+",
                    r"http://10\.\d+\.\d+\.\d+:\d+",
                    r"http://172\.(1[6-9]|2\d|3[01])\.\d+\.\d+:\d+",
                    r"https://192\.168\.\d+\.\d+:\d+",
                    r"https://10\.\d+\.\d+\.\d+:\d+",
                    r"https://172\.(1[6-9]|2\d|3[01])\.\d+\.\d+:\d+",
                ]
                allowed_origins = lan_patterns + allowed_origins
                supports_credentials = False
                app.logger.info("CORS: Master mode - allowing LAN origins for interconnector")
            master_url = interconnector_config.get("master_url")
            if master_url:
                from urllib.parse import urlparse
                parsed = urlparse(master_url)
                master_origin = f"{parsed.scheme}://{parsed.netloc}"
                if master_origin not in allowed_origins:
                    allowed_origins.append(master_origin)
                    app.logger.info(f"CORS: Added master origin {master_origin}")
    except Exception as cors_err:
        if "application context" not in str(cors_err).lower():
            app.logger.warning(f"Could not load interconnector config for CORS: {cors_err}")

    CORS(
        app,
        origins=allowed_origins,
        supports_credentials=supports_credentials,
        allow_headers=["Content-Type", "Authorization", "X-API-Key"],
        methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    )

    socketio.init_app(app)
    app.logger.info("CORS and SocketIO configured with secure origins")

    try:
        from backend.utils.unified_progress_system import get_unified_progress
        unified_progress = get_unified_progress()
        unified_progress.initialize(output_dir=config.OUTPUT_DIR, socketio=socketio, flask_app=app)
        app.logger.info("Unified Progress System initialized with SocketIO and Flask app context")
        
        import threading
        import glob
        import json
        from pathlib import Path
        
        def poll_celery_progress():
            last_modified_times = {}
            poll_count = 0
            TERMINAL_STATUSES = {'complete', 'error', 'cancelled', 'end'}
            STALE_THRESHOLD = 2700  # 45 minutes - indexing can take 15-25 min per doc

            while True:
                try:
                    poll_count += 1

                    progress_dir = Path(config.OUTPUT_DIR) / ".progress_jobs"
                    if progress_dir.exists():
                        metadata_files = list(progress_dir.glob("*/metadata.json"))

                        for metadata_file in metadata_files:
                            try:
                                current_mtime = metadata_file.stat().st_mtime
                                file_key = str(metadata_file)

                                # Skip files that haven't changed since last poll
                                if file_key in last_modified_times and last_modified_times[file_key] == current_mtime:
                                    continue

                                # Mark stale non-terminal files as error (zombie job cleanup)
                                if time.time() - current_mtime > STALE_THRESHOLD:
                                    try:
                                        with open(metadata_file, 'r') as f:
                                            stale_meta = json.load(f)
                                        if stale_meta.get('status') not in TERMINAL_STATUSES:
                                            stale_meta['status'] = 'error'
                                            stale_meta['message'] = 'Timed out — worker likely crashed'
                                            stale_meta['is_complete'] = True
                                            stale_meta['completion_time_utc'] = datetime.utcnow().isoformat()
                                            with open(metadata_file, 'w') as f:
                                                json.dump(stale_meta, f, indent=4)
                                            app.logger.info(f"Marked stale job as error: {stale_meta.get('job_id', 'unknown')}")
                                    except Exception:
                                        pass
                                    last_modified_times[file_key] = current_mtime
                                    continue

                                with open(metadata_file, 'r') as f:
                                    metadata = json.load(f)

                                # Skip terminal statuses — no need to re-emit completed/errored jobs
                                job_status = metadata.get('status', 'unknown')
                                if job_status in TERMINAL_STATUSES:
                                    last_modified_times[file_key] = current_mtime
                                    continue

                                event_data = {
                                    'job_id': metadata.get('job_id', 'unknown'),
                                    'progress': metadata.get('progress', 0),
                                    'message': metadata.get('message', ''),
                                    'status': job_status,
                                    'process_type': metadata.get('process_type', 'unknown'),
                                    'timestamp': metadata.get('last_update_utc', metadata.get('timestamp', '')),
                                    **metadata.get('additional_data', {})
                                }

                                socketio.emit("job_progress", event_data, to="global_progress")
                                app.logger.info(f"Polled and emitted progress: {metadata.get('job_id')} at {event_data['progress']}%")

                                last_modified_times[file_key] = current_mtime

                            except (json.JSONDecodeError, KeyError, OSError) as e:
                                continue

                            except Exception as e:
                                app.logger.warning(f"Error processing progress file {metadata_file}: {e}")

                    if poll_count % 30 == 0:
                        app.logger.debug(f"Celery progress polling active - monitoring {len(last_modified_times)} files")

                    time.sleep(1)
                except Exception as e:
                    app.logger.error(f"Error in Celery progress polling: {e}")
                    time.sleep(5)
        
        polling_thread = threading.Thread(target=poll_celery_progress, daemon=True)
        polling_thread.start()
        app.logger.info("Started Celery progress polling thread")

        # Redis pub/sub relay: Celery workers publish progress to Redis,
        # this thread subscribes and re-emits via SocketIO
        def relay_redis_progress():
            try:
                r = redis.Redis(host='localhost', port=6379, db=0)
                pubsub = r.pubsub()
                pubsub.subscribe('guaardvark:progress')
                app.logger.info("Redis progress relay subscribed to guaardvark:progress")
                for msg in pubsub.listen():
                    if msg['type'] == 'message':
                        try:
                            event_data = json.loads(msg['data'])
                            process_id = event_data.get('job_id', '')
                            if process_id:
                                socketio.emit('job_progress', event_data, to=process_id, namespace='/')
                            socketio.emit('job_progress', event_data, to='global_progress', namespace='/')
                            app.logger.info(f"↪ Relayed Redis progress: {process_id} at {event_data.get('progress')}%")
                        except (json.JSONDecodeError, KeyError) as e:
                            app.logger.warning(f"Bad progress message from Redis: {e}")
            except Exception as e:
                app.logger.error(f"Redis progress relay error: {e}")

        relay_thread = threading.Thread(target=relay_redis_progress, daemon=True)
        relay_thread.start()
        app.logger.info("Started Redis progress relay thread")

    except Exception as e:
        app.logger.error(f"Failed to initialize Unified Progress System: {e}")

    def perform_startup_security_checks():
        security_warnings = []
        
        if app.debug:
            security_warnings.append(" Debug mode is enabled - disable in production")
        
        if app.config.get("SECRET_KEY") == "dev-secret-key":
            security_warnings.append(" Using default secret key - change in production")
        
        if flask_env == "production" and len(allowed_origins) > 1:
            security_warnings.append(" Multiple CORS origins in production - restrict to single domain")
        
        max_upload = app.config.get("MAX_CONTENT_LENGTH", 0)
        if max_upload > 500 * 1024 * 1024:
            security_warnings.append(" Large file upload limit - consider reducing for security")
        
        if "sqlite" in app.config.get("SQLALCHEMY_DATABASE_URI", ""):
            security_warnings.append(" Using SQLite in production - consider PostgreSQL for production")
        
        if security_warnings:
            app.logger.warning("SECURITY WARNINGS DETECTED:")
            for warning in security_warnings:
                app.logger.warning(f"   {warning}")
            app.logger.warning("Please address these security issues before deploying to production")
        else:
            app.logger.info("Startup security checks passed")

    perform_startup_security_checks()

    @app.before_request
    def enforce_https():
        if flask_env == "production" and not request.is_secure:
            url = request.url.replace("http://", "https://", 1)
            return redirect(url, code=301)

    @app.before_request
    def log_request_info_flask():
        app.logger.debug(
            f"Incoming {request.method} request to {request.path} from {request.remote_addr}"
        )

    # Protect sensitive endpoints (code execution, backup restore/delete)
    from backend.utils.auth_guard import check_endpoint_auth
    app.before_request(check_endpoint_auth)

    @app.after_request
    def set_security_headers_flask(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        if request.path.startswith('/api/'):
            response.headers['X-CSRF-Token-Required'] = 'true'

        return response

    @app.after_request
    def log_response_info(response):
        app.logger.debug(
            f"Response {response.status_code} for {request.method} {request.path}"
        )
        return response

    @app.after_request
    def cleanup_database_session(response):
        try:
            if request.path.startswith('/api/bulk-csv/') or \
               request.path.startswith('/api/generate/') or \
               request.path.startswith('/api/simple-csv/'):
                app.logger.debug(f"Skipping session cleanup for CSV generation endpoint: {request.path}")
                return response
                
            if hasattr(db, 'session') and db.session:
                try:
                    if hasattr(db.session, 'in_transaction') and db.session.in_transaction():
                        app.logger.debug("Transaction in progress - skipping global session cleanup")
                        return response
                        
                    if hasattr(db.session, 'registry') and db.session.registry:
                        db.session.remove()
                        app.logger.debug("Database session cleaned up after request")
                    else:
                        app.logger.debug("Session registry not available - skipping cleanup")
                        
                except Exception as session_check_error:
                    app.logger.debug(f"Session state check failed, skipping cleanup: {session_check_error}")
                    
        except Exception as e:
            app.logger.debug(f"Failed to cleanup database session: {e}")
        
        return response

    try:
        if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:///"):
            db_path_cfg = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
            if not os.path.isabs(db_path_cfg):
                db_path_cfg = os.path.join(project_root, db_path_cfg)
            app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path_cfg}"
            os.makedirs(os.path.dirname(os.path.abspath(db_path_cfg)), exist_ok=True)
            app.logger.info(f"DB directory ensured: {db_path_cfg}")
            expected_final_path = str(config.GUAARDVARK_ROOT / "data" / "database" / "system_analysis.db")
            if os.path.abspath(db_path_cfg) != expected_final_path:
                app.logger.error(
                    f"CRITICAL: Database path mismatch! Expected: {expected_final_path}, "
                    f"Got: {os.path.abspath(db_path_cfg)}. Database isolation may be broken!"
                )

        for key_dir in ["UPLOAD_FOLDER", "CLIENT_LOGO_FOLDER", "STORAGE_DIR", "OUTPUT_DIR"]:
            path_val = app.config.get(key_dir)
            if not path_val:
                raise KeyError(f"Configuration key '{key_dir}' not found.")
            if not os.path.isabs(path_val):
                full_path_val = os.path.join(project_root, path_val)
            else:
                full_path_val = path_val
            os.makedirs(full_path_val, exist_ok=True)
            app.logger.info(f"Directory ensured: {full_path_val} (from {key_dir})")
            app.config[key_dir] = full_path_val

    except Exception as e:
        app.logger.error(f"Failed to setup directories: {e}")
        raise

    db.init_app(app)

    try:
        from backend.tools import initialize_all_tools, get_registered_tools
        tool_registry = initialize_all_tools()
        app.tool_registry = tool_registry
        app.logger.info(f"Tool Registry initialized with {len(get_registered_tools())} tools: {', '.join(get_registered_tools())}")
    except Exception as e:
        app.logger.warning(f"Tool Registry initialization failed (non-critical): {e}")
        app.tool_registry = None

    try:
        from backend.plugins import get_plugin_manager, get_plugin_registry
        registry = get_plugin_registry()
        app.logger.info(f"Plugin Registry initialized, discovered {len(registry.get_all_plugins())} plugins")
        
        manager = get_plugin_manager()
        app.plugin_manager = manager
        app.logger.info("Plugin Manager initialized successfully")
    except Exception as e:
        app.logger.warning(f"Plugin Manager initialization failed (non-critical): {e}", exc_info=True)
        app.plugin_manager = None

    from backend.utils.blueprint_discovery import auto_register_blueprints
    auto_register_blueprints(app)

    try:
        from backend.services.browser_automation_service import register_browser_shutdown
        register_browser_shutdown(app)
    except Exception as e:
        app.logger.debug(f"Browser shutdown registration skipped: {e}")

    return app

app = create_app()


try:
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:///"):
        db_path_cfg = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
        if not os.path.isabs(db_path_cfg):
            db_path_cfg = os.path.join(project_root, db_path_cfg)
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path_cfg}"
        os.makedirs(os.path.dirname(os.path.abspath(db_path_cfg)), exist_ok=True)
        app.logger.info(f"DB directory ensured: {db_path_cfg}")

    for key_dir in ["UPLOAD_FOLDER", "CLIENT_LOGO_FOLDER", "STORAGE_DIR", "OUTPUT_DIR"]:

        path_val = app.config.get(key_dir)
        if not path_val:
            raise KeyError(f"Configuration key '{key_dir}' not found.")
        full_path_val = (
            os.path.abspath(os.path.join(project_root, path_val))
            if not os.path.isabs(path_val)
            else path_val
        )
        os.makedirs(full_path_val, exist_ok=True)
        app.logger.info(f"Directory ensured: {full_path_val} (from {key_dir})")
        app.config[key_dir] = full_path_val

    migrations_dir = os.path.join(project_root, "backend", "migrations")
    if os.path.isdir(migrations_dir):
        Migrate(app, db, directory=migrations_dir)
    else:
        Migrate(app, db)
    from backend.utils import migration_utils

    # Skip migration checks if start.sh already verified them
    migrations_already_verified = os.environ.get("GUAARDVARK_MIGRATIONS_VERIFIED")

    if not os.environ.get("GUAARDVARK_SKIP_MIGRATIONS") and not migrations_already_verified:
        try:
            migration_utils.ensure_single_head(migrations_dir, auto_merge=True)
        except Exception as mig_err:
            app.logger.critical(f"Migration error: {mig_err}")
            if os.environ.get("PYTEST_SKIP_MIGRATION_CHECK"):
                app.logger.error("Skipping fatal exit due to test environment.")
            else:
                sys.exit("CRITICAL: Multiple Alembic heads detected")

    with app.app_context():
        try:
            if os.path.isdir(migrations_dir) and not os.environ.get(
                "PYTEST_SKIP_MIGRATION_CHECK"
            ) and not os.environ.get("GUAARDVARK_SKIP_MIGRATIONS") and not migrations_already_verified:
                try:
                    app.logger.info(f"Starting database migrations from {migrations_dir}...")
                    from flask_migrate import upgrade as alembic_upgrade

                    alembic_upgrade(directory=migrations_dir)
                    app.logger.info("Database migrations applied successfully.")
                except Exception as mig_err:
                    app.logger.error(
                        f"Failed to apply migrations automatically: {mig_err}",
                        exc_info=True,
                    )

            app.logger.info("Creating/verifying database tables...")
            db.create_all()
            app.logger.info("Database tables created/verified successfully.")


            app.logger.info(
                "Global system prompt rule creation disabled - using hardcoded default"
            )

        except OperationalError as op_err:
            app.logger.error(f"Database operation error during create_all: {op_err}")
            app.logger.error("Ensure migrations are up-to-date (`flask db upgrade`).")
except Exception as e_init:
    app.logger.critical(f"Initialization error (DB/Dirs): {e_init}", exc_info=True)
    sys.exit("CRITICAL: App initialization failed (DB/Dirs).")

def initialize_llm_and_index_async():
    import time
    time.sleep(2)

    try:
        with app.app_context():
            app.logger.warning("[LLM-Init] Setting up LLM and index (background thread)...")

            llm = llm_service.get_llm_for_startup()
            embed_model = llm_service.get_default_embed_model()
            index_manager.configure_global_settings(llm=llm, embed_model=embed_model)

            get_or_create_index()

            from backend.services.indexing_service import index as llama_index_from_service
            from backend.services.indexing_service import (
                storage_context as storage_context_from_service,
            )

            if llama_index_from_service is None:
                app.logger.error("CRITICAL: Index initialization failed - llama_index_from_service is None")
                raise RuntimeError("Failed to initialize LlamaIndex during startup")

            if storage_context_from_service is None:
                app.logger.error("CRITICAL: Storage context initialization failed - storage_context_from_service is None")
                raise RuntimeError("Failed to initialize storage context during startup")

            app.config["LLAMA_INDEX_LLM"] = llm
            app.config["LLAMA_INDEX_EMBED_MODEL"] = embed_model
            app.config["LLAMA_INDEX_INDEX"] = llama_index_from_service
            app.config["LLAMA_INDEX_STORAGE_CONTEXT"] = storage_context_from_service
            app.config["LLAMA_INDEX_QUERY_ENGINE"] = None
            app.config["LLAMA_INDEX_CHAT_ENGINE"] = None

            try:
                llm_service.persist_active_model_name(getattr(llm, "model", DEFAULT_LLM))
            except Exception as e:
                app.logger.warning(f"[LLM-Init] Failed to persist active model on startup: {e}")

            try:
                model_name = getattr(llm, "model", "unknown")
                app.logger.warning(f"[LLM-Init] Warming up model '{model_name}' (loading into GPU)...")
                warmup_start = time.time()
                llm.complete("warmup")
                warmup_duration = time.time() - warmup_start
                app.logger.warning(f"[LLM-Init] Model warmup completed in {warmup_duration:.1f}s — ready for chat")
            except Exception as e:
                app.logger.error(f"[LLM-Init] Model warmup FAILED: {e} — first chat will be slow", exc_info=True)

            app.logger.warning("[LLM-Init] LLM and index initialization completed successfully")
    except Exception as e:
        app.logger.error(f"[LLM-Init] FAILED to initialize LLM and index: {e}", exc_info=True)

import threading
llm_init_thread = threading.Thread(target=initialize_llm_and_index_async, daemon=True, name="LLM-Index-Init")
llm_init_thread.start()
app.logger.info("LLM and index initialization started in background thread - Flask will start immediately")

try:
    from backend.api.cache_api import cache_bp
    from backend.api.generation_api import generation_bp
    from backend.api.entity_indexing_api import entity_indexing_bp
    from backend.api.entity_links_api import entity_links_bp
    from backend.api.rag_debug_api import rag_debug_bp
    from backend.api.automation_api import automation_bp

    early_blueprints_imported = True
except ImportError as e:
    logger_module.critical(
        f"CRITICAL: Failed to import core blueprints (generation_bp, cache_bp, entity_indexing_bp, entity_links_bp): {e}",
        exc_info=True,
    )
    early_blueprints_imported = False
    generation_bp = None
    cache_bp = None
    entity_indexing_bp = None
    entity_links_bp = None
    rag_debug_bp = None

app.logger.info("[ROUTING] Registering blueprints with automated discovery...")


is_celery_worker = os.environ.get('CELERY_WORKER_MODE', 'false').lower() == 'true'

if is_celery_worker:
    app.logger.info("Running in Celery worker mode - skipping blueprint registration")
    registration_summary = {
        'registration': {
            'total_discovered': 0,
            'registered': 0,
            'skipped': 0,
            'errors': 0
        },
        'errors': []
    }
else:
    app.logger.info("Blueprint registration completed during app creation")

if not is_celery_worker:
    critical_blueprints = [
        ("backend.api.enhanced_chat_api.enhanced_chat_bp", "enhanced_chat_bp"),
        ("backend.api.voice_api.voice_bp", "voice_bp"),
        ("backend.api.bulk_generation_api.bulk_gen_bp", "bulk_gen_bp"),
        ("backend.api.batch_image_generation_api.batch_image_bp", "batch_image_bp"),
        ("backend.api.backup_api.backup_bp", "backup_bp"),
    ]
    
    for bp_path_str, bp_expected_name in critical_blueprints:
        try:
            module_name, blueprint_attr_name = bp_path_str.rsplit(".", 1)
            module = __import__(module_name, fromlist=[blueprint_attr_name])
            blueprint_obj = getattr(module, blueprint_attr_name)
            if blueprint_obj.name not in app.blueprints:
                app.register_blueprint(blueprint_obj)
                app.logger.info(f"Fallback registered: {blueprint_attr_name}")
        except Exception as fallback_error:
            app.logger.error(f"Fallback registration failed for {bp_expected_name}: {fallback_error}")


blueprints_to_register_list = []


app.logger.info("Blueprint registration method: AUTOMATED DISCOVERY")
app.logger.info(" Manual maintenance eliminated - blueprints auto-discovered from filesystem")

try:
    if early_blueprints_imported:
        if generation_bp:
            if generation_bp.name not in app.blueprints:
                app.register_blueprint(generation_bp)
                app.logger.debug("Registered early imported blueprint: generation_bp")
        else:
            app.logger.error(
                "generation_bp was not imported successfully, cannot register."
            )
        if cache_bp:
            if cache_bp.name not in app.blueprints:
                app.register_blueprint(cache_bp)
                app.logger.debug("Registered early imported blueprint: cache_bp")
        else:
            app.logger.error("cache_bp was not imported successfully, cannot register.")
        if entity_indexing_bp:
            if entity_indexing_bp.name not in app.blueprints:
                app.register_blueprint(entity_indexing_bp)
                app.logger.debug("Registered early imported blueprint: entity_indexing_bp")
        else:
            app.logger.error("entity_indexing_bp was not imported successfully, cannot register.")
        if entity_links_bp:
            if entity_links_bp.name not in app.blueprints:
                app.register_blueprint(entity_links_bp)
                app.logger.debug("Registered early imported blueprint: entity_links_bp")
        else:
            app.logger.error("entity_links_bp was not imported successfully, cannot register.")
        if rag_debug_bp:
            if rag_debug_bp.name not in app.blueprints:
                app.register_blueprint(rag_debug_bp)
                app.logger.debug("Registered early imported blueprint: rag_debug_bp")
        else:
            app.logger.error("rag_debug_bp was not imported successfully, cannot register.")
        if automation_bp:
            if automation_bp.name not in app.blueprints:
                app.register_blueprint(automation_bp)
                app.logger.debug("Registered early imported blueprint: automation_bp")
        else:
            app.logger.error("automation_bp was not imported successfully, cannot register.")
    else:
        app.logger.warning(
            "Skipping registration of generation_bp and cache_bp due to earlier import failure."
        )
    app.logger.info("Blueprint registration process completed.")
except Exception as e_bp_reg_block:
    app.logger.error(
        f"CRITICAL ERROR during blueprint registration block: {e_bp_reg_block}",
        exc_info=True,
    )

try:
    from backend import socketio_events
except Exception as e_events:
    app.logger.error(f"Failed to import socketio events: {e_events}")

try:
    scheduler_spec = importlib.util.find_spec("backend.services.task_scheduler")
    if scheduler_spec is not None:
        task_sched_mod = importlib.import_module("backend.services.task_scheduler")
        scheduler_instance = task_sched_mod.init_task_scheduler(app)
        try:
            from backend.services.resource_manager import ResourceManager

            resource_manager = ResourceManager(scheduler_instance)
            resource_manager.start()
        except Exception as e_rm:
            app.logger.error(f"Failed to start resource manager: {e_rm}")
    else:
        app.logger.warning("Task scheduler not found; skipping.")
except Exception as e_sched:
    app.logger.error(f"Failed to start task scheduler: {e_sched}")

    try:
        from backend.services.distributed_coordinator import (
            init_distributed_coordinator,
        )
        from backend.services.node_registry import (
            NodeCapability,
            NodeType,
            create_node_info,
            init_node_registry,
        )

        node_type = NodeType.MASTER
        capabilities = [
            NodeCapability.CHAT,
            NodeCapability.INDEXING,
            NodeCapability.GENERATION,
            NodeCapability.SEARCH,
            NodeCapability.COMPUTE,
            NodeCapability.STORAGE,
        ]

        node_type_env = os.getenv("GUAARDVARK_NODE_TYPE", "master").lower()
        if node_type_env in [nt.value for nt in NodeType]:
            node_type = NodeType(node_type_env)

            if node_type == NodeType.RASPBERRY_PI:
                capabilities = [
                    NodeCapability.VOICE,
                    NodeCapability.MONITORING,
                    NodeCapability.CHAT,
                ]
            elif node_type == NodeType.TRADING:
                capabilities = [
                    NodeCapability.TRADING,
                    NodeCapability.MONITORING,
                    NodeCapability.COMPUTE,
                ]
            elif node_type == NodeType.MONITOR:
                capabilities = [NodeCapability.MONITORING, NodeCapability.SCRAPING]
            elif node_type == NodeType.INFERENCE:
                capabilities = [
                    NodeCapability.CHAT,
                    NodeCapability.GENERATION,
                    NodeCapability.COMPUTE,
                ]
            elif node_type == NodeType.STORAGE:
                capabilities = [
                    NodeCapability.STORAGE,
                    NodeCapability.INDEXING,
                    NodeCapability.BACKUP,
                ]

        port = int(os.getenv("PORT", 5000))
        node_info = create_node_info(
            node_type=node_type,
            capabilities=capabilities,
            port=port,
            metadata={
                "version": __version__,
                "startup_time": datetime.now().isoformat(),
            },
        )

        redis_client = _ensure_redis_client()
        if redis_client:
            app.redis = redis_client

            node_registry = init_node_registry(redis_client, node_info)
            distributed_coordinator = init_distributed_coordinator(
                redis_client, node_registry
            )

            node_registry.start()
            distributed_coordinator.start()

            app.logger.info(
                f"Distributed system initialized as {node_type.value} node with capabilities: {[c.value for c in capabilities]}"
            )
        else:
            app.logger.warning("Redis not available, distributed system disabled")

    except Exception as e_distributed:
        app.logger.error(f"Failed to initialize distributed system: {e_distributed}")

    startup_duration = time.time() - start_time
    app.logger.info("Application startup completed in %.2fs", startup_duration)

except Exception as e_bp_reg_block:
    app.logger.error(
        f"CRITICAL ERROR during blueprint registration block: {e_bp_reg_block}",
        exc_info=True,
    )


@app.route("/api/debug/env", methods=["GET"])
def debug_env():
    try:
        import os
        import re as _re
        from backend.config import GUAARDVARK_ROOT, DATABASE_URL, STORAGE_DIR

        # Mask password in database URL for display
        masked_db_url = _re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', DATABASE_URL)
        masked_sqlalchemy_uri = _re.sub(
            r'://([^:]+):([^@]+)@', r'://\1:***@',
            app.config.get("SQLALCHEMY_DATABASE_URI", "NOT SET")
        )

        env_info = {
            "llamax_root": {
                "from_config": str(GUAARDVARK_ROOT),
                "from_env": os.environ.get("GUAARDVARK_ROOT", "NOT SET"),
                "match": str(GUAARDVARK_ROOT) == os.environ.get("GUAARDVARK_ROOT", ""),
            },
            "database": {
                "database_url": masked_db_url,
                "sqlalchemy_uri": masked_sqlalchemy_uri,
            },
            "storage": {
                "storage_dir": STORAGE_DIR,
                "from_config": app.config.get("STORAGE_DIR", "NOT SET"),
            },
            "working_directory": os.getcwd(),
            "process_id": os.getpid(),
        }
        
        return jsonify(env_info), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/health")
def health_check():
    uptime_seconds = time.time() - start_time if "start_time" in globals() else 0
    return (
        jsonify(
            {
                "status": "ok",
                "uptime_seconds": round(uptime_seconds, 2),
                "version": app.config.get("APP_VERSION", "N/A"),
                "index_loaded": bool(app.config.get("LLAMA_INDEX_INDEX")),
            }
        ),
        200,
    )


@app.route("/api/health/db")
def health_db():
    from backend.utils import migration_utils

    status = migration_utils.get_health()
    return jsonify(status), 200


_celery_health_cache = {"data": None, "timestamp": 0}
_HEALTH_CACHE_DURATION = 30

@app.route("/api/health/celery")
def health_celery():
    import time

    current_time = time.time()
    if (_celery_health_cache["data"] is not None and
        current_time - _celery_health_cache["timestamp"] < _HEALTH_CACHE_DURATION):
        return _celery_health_cache["data"]

    previous_status = None
    if _celery_health_cache["data"] is not None:
        try:
            previous_status = _celery_health_cache["data"][0].get_json().get("status")
        except Exception:
            pass

    try:
        from backend.celery_app import celery
        result = celery.send_task('backend.celery_tasks_isolated.ping', queue='health')
        status = result.get(timeout=15)

        inspect = celery.control.inspect()
        active_tasks = inspect.active()
        
        worker_info = {
            "active_tasks": len(active_tasks.get('celery@GUAARDVARK', [])) if active_tasks else 0,
            "result": status
        }
        
        response_data = jsonify({"status": "up", **worker_info}), 200
        
        if previous_status != "up":
            try:
                from backend.socketio_events import emit_health_status_change
                emit_health_status_change("celery", "up", worker_info)
            except Exception as e:
                app.logger.warning(f"Failed to emit health status change: {e}")
        
        _celery_health_cache["data"] = response_data
        _celery_health_cache["timestamp"] = current_time
        
        return response_data
    except Exception as exc:
        error_msg = str(exc)
        
        if "timeout" in error_msg.lower():
            error_msg = f"Worker busy or overloaded: {error_msg}"
        
        error_response = jsonify({
            "status": "down", 
            "error": error_msg,
            "suggestion": "Worker may be processing large tasks. Check /api/celery/tasks for details."
        }), 503
        
        if previous_status != "down":
            try:
                from backend.socketio_events import emit_health_status_change
                emit_health_status_change("celery", "down", {"error": error_msg})
            except Exception as e:
                app.logger.warning(f"Failed to emit health status change: {e}")
        
        _celery_health_cache["data"] = error_response
        _celery_health_cache["timestamp"] = current_time - (_HEALTH_CACHE_DURATION - 5)
        
        return error_response


@app.route("/api/version")
def get_version():
    from flask import jsonify
    return jsonify({
        "version": __version__,
        "name": "guaardvark",
        "description": "LLM-powered development environment",
        "timestamp": "2025-09-27T07:15:00Z"
    }), 200


@app.route("/api/meta/test-llm")
def test_llm():
    try:
        return jsonify({
            "status": "available",
            "message": "LLM service is configured",
            "model": "llama3:latest"
        }), 200
    except Exception as e:
        return jsonify({
            "status": "unavailable",
            "error": str(e)
        }), 503


@app.route("/api/health/redis")
def health_redis():
    try:
        redis_client = _ensure_redis_client()
        redis_client.ping()
        return jsonify({"status": "up"}), 200
    except Exception as exc:
        return jsonify({"status": "down", "error": str(exc)}), 503


@app.route("/api/health/tools")
def health_tools():
    try:
        from backend.tools.tool_registry_init import get_registered_tools, get_tools_by_category

        tools = get_registered_tools()
        categories = get_tools_by_category()

        return jsonify({
            "status": "up" if len(tools) > 0 else "degraded",
            "total_tools": len(tools),
            "tools": tools,
            "categories": {cat: len(names) for cat, names in categories.items()}
        }), 200
    except Exception as exc:
        return jsonify({
            "status": "down",
            "error": str(exc)
        }), 503


@app.route("/api/health/db-connections")
def health_db_connections():
    try:
        from backend.utils.db_utils import get_db_connection_info
        
        connection_info = get_db_connection_info()
        
        if "error" in connection_info:
            return jsonify({"status": "unhealthy", "error": connection_info["error"]}), 503
        
        checked_out = connection_info.get("checked_out", 0)
        pool_size = connection_info.get("pool_size", 0)
        
        status = "healthy"
        if checked_out >= pool_size * 0.8:
            status = "warning"
        
        return jsonify({
            "status": status,
            "message": "Database connection pool information",
            "connections": connection_info
        }), 200
        
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 503


@app.route("/api/db/cleanup-connections", methods=["POST"])
def cleanup_db_connections():
    try:
        from backend.utils.db_utils import cleanup_idle_connections
        
        result = cleanup_idle_connections()
        
        if "error" in result:
            return jsonify({"success": False, "error": result["error"]}), 500
        
        return jsonify({"success": True, "result": result}), 200
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/routes")
def list_routes_api():
    routes = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        methods = ",".join(sorted(rule.methods - {"HEAD", "OPTIONS"}))
        routes.append({"rule": rule.rule, "methods": methods})
    return jsonify({"routes": routes}), 200


@app.route("/api/api-docs")
def api_docs():
    docs = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        view = app.view_functions[rule.endpoint]
        doc = (view.__doc__ or "").strip()
        methods = ",".join(sorted(rule.methods - {"HEAD", "OPTIONS"}))
        docs.append({"rule": rule.rule, "methods": methods, "doc": doc})
    return jsonify({"docs": docs}), 200


@app.errorhandler(400)
def handle_bad_request(e):
    msg = str(e.description) if hasattr(e, "description") else "Invalid request."
    app.logger.warning(
        f"Bad Request (400): {msg} for {request.url}",
        exc_info=e if app.debug else False,
    )
    return jsonify({"error": "Bad Request", "message": msg}), 400


@app.errorhandler(404)
def handle_not_found(e):
    app.logger.warning(
        f"Not Found (404): {request.url}", exc_info=e if app.debug else False
    )
    return jsonify({"error": "Not Found", "message": "Resource not found"}), 404


@app.errorhandler(405)
def handle_method_not_allowed(e):
    app.logger.warning(
        f"Method Not Allowed (405): {request.method} for {request.url}",
        exc_info=e if app.debug else False,
    )
    return (
        jsonify(
            {
                "error": "Method Not Allowed",
                "message": f"{request.method} not allowed for this URL",
            }
        ),
        405,
    )


@app.errorhandler(SQLAlchemyError)
def handle_sqlalchemy_db_error(e):
    app.logger.error(f"Database Error: {e}", exc_info=True)
    try:
        if db and db.session:
            db.session.rollback()
    except Exception as rb_err:
        app.logger.error(f"Rollback failed: {rb_err}")
    msg = (
        "Database integrity error."
        if isinstance(e, IntegrityError)
        else "A database error occurred."
    )
    return jsonify({"error": "Database Error", "message": msg}), 500


@app.errorhandler(500)
def handle_internal_server_error(e):
    app.logger.error(f"Internal Server Error (500): {e}", exc_info=True)
    try:
        if db and db.session:
            db.session.rollback()
    except Exception as rb_err_500:
        app.logger.error(f"Rollback failed in 500 handler: {rb_err_500}")
    return (
        jsonify(
            {
                "error": "Internal Server Error",
                "message": "An unexpected error occurred. Check server logs.",
            }
        ),
        500,
    )


@app.cli.command("seed-db")
@click.option("--force", is_flag=True, help="Force seeding even if models exist.")
def seed_database_cli(force):
    logger_cli = app.logger
    seeds = [
        {"name": "llama3", "version": "latest", "quantized": False},
        {"name": "codellama", "version": "latest", "quantized": False},
    ]
    added, skipped = 0, 0
    try:
        with app.app_context():
            for mdl in seeds:
                exists = db.session.query(Model).filter_by(name=mdl["name"]).first()
                if exists and not force:
                    skipped += 1
                elif exists and force:
                    logger_cli.info(f"Overwriting model: {mdl['name']}")
                    exists.version = mdl.get("version", exists.version)
                    exists.quantized = mdl.get("quantized", exists.quantized)
                    added += 1
                else:
                    logger_cli.info(f"Adding model: {mdl['name']}")
                    db.session.add(Model(**mdl))
                    added += 1
            db.session.commit()
            logger_cli.info(f"Seed done: added/updated={added}, skipped={skipped}")
            print(f"Seeded/Updated {added} models. Skipped {skipped}.")
    except Exception as e_seed_db:
        if db and db.session:
            db.session.rollback()
        logger_cli.error(f"Seeding failed: {e_seed_db}", exc_info=True)
        print(f"Error during seeding: {e_seed_db}")


@app.cli.command("seed-prompts")
@click.option("--force", is_flag=True, help="Force update existing prompts.")
def seed_prompts_cli(force):
    logger_cli = app.logger
    if not prompt_utils:
        print("Error: prompt_utils module could not be imported for seed-prompts.")
        return

    default_qa_text = getattr(
        prompt_utils,
        "FALLBACK_QA_PROMPT_TEXT",
        "Answer the question based on the context provided.",
    )
    default_cg_text = getattr(
        prompt_utils,
        "FALLBACK_CODE_GEN_PROMPT_TEXT",
        "Generate code for the following task: {task_description}. Output filename should be {output_target_filename}",
    )
    default_cg_text = default_cg_text.replace(
        "{output_filename}", "{output_target_filename}"
    )

    prompts_to_seed = [
        {
            "name": "qa_default",
            "level": "SYSTEM",
            "type": "QA_TEMPLATE",
            "rule_text": default_qa_text,
            "target_models_json": json.dumps(["__ALL__"]),
            "is_active": True,
        },
        {
            "name": "code_gen_default",
            "level": "PROMPT",
            "type": "PROMPT_TEMPLATE",
            "rule_text": default_cg_text,
            "target_models_json": json.dumps(["__ALL__"]),
        },
        {
            "name": GLOBAL_DEFAULT_SYSTEM_PROMPT_RULE_NAME,
            "level": "SYSTEM",
            "type": "SYSTEM_PROMPT",
            "rule_text": DEFAULT_FALLBACK_SYSTEM_PROMPT,
            "target_models_json": json.dumps(["__ALL__"]),
        },
    ]
    VALID_RULE_TYPES = [
        "PROMPT_TEMPLATE",
        "QA_TEMPLATE",
        "COMMAND_RULE",
        "FILTER_RULE",
        "FORMATTING_RULE",
        "SYSTEM_PROMPT",
        "OTHER",
    ]
    added, updated, skipped = 0, 0, 0
    if not Rule or not db:
        print("Error: Rule model or DB session not available for seed-prompts.")
        return

    try:
        with app.app_context():
            for p_data in prompts_to_seed:
                name = p_data.get("name")
                db.session.query(Rule).filter(
                    Rule.name == name, Rule.is_active == True
                ).update({"is_active": False})
                valid_types = {
                    "PROMPT_TEMPLATE",
                    "QA_TEMPLATE",
                    "COMMAND_RULE",
                    "FILTER_RULE",
                    "FORMATTING_RULE",
                    "SYSTEM_PROMPT",
                    "OTHER",
                }
                if p_data.get("type") not in valid_types:
                    logger_cli.warning(
                        f"Skipping prompt {p_data.get('name')} with invalid type {p_data.get('type')}"
                    )
                    continue
                if p_data["name"] == "qa_default":
                    duplicates = (
                        db.session.query(Rule)
                        .filter(
                            Rule.name == "qa_default",
                            sa.or_(Rule.level != "SYSTEM", Rule.type != "QA_TEMPLATE"),
                        )
                        .all()
                    )
                    for dup in duplicates:
                        if dup.is_active:
                            logger_cli.info(
                                f"Deactivating legacy qa_default rule ID {dup.id}"
                            )
                            dup.is_active = False
                            updated += 1
                existing_prompt = (
                    db.session.query(Rule)
                    .filter_by(
                        level=p_data["level"], name=p_data["name"], type=p_data["type"]
                    )
                    .first()
                )
                if p_data["name"] == "qa_default" and existing_prompt:
                    duplicates_same = (
                        db.session.query(Rule)
                        .filter(
                            Rule.name == "qa_default",
                            Rule.level == "SYSTEM",
                            Rule.type == "QA_TEMPLATE",
                            Rule.id != existing_prompt.id,
                        )
                        .all()
                    )
                    for dup in duplicates_same:
                        if dup.is_active:
                            logger_cli.info(
                                f"Deactivating duplicate qa_default rule ID {dup.id}"
                            )
                            dup.is_active = False
                            updated += 1
                if existing_prompt and not force:
                    skipped += 1
                    logger_cli.info(f"Skipping existing prompt: {p_data['name']}")
                elif existing_prompt and force:
                    logger_cli.info(f"Updating existing prompt: {p_data['name']}")
                    existing_prompt.rule_text = p_data["rule_text"]
                    existing_prompt.target_models_json = p_data["target_models_json"]
                    existing_prompt.is_active = p_data.get(
                        "is_active", existing_prompt.is_active
                    )
                    updated += 1
                else:
                    if p_data["type"] not in VALID_RULE_TYPES:
                        logger_cli.error(
                            f"Invalid rule type '{p_data['type']}' for prompt {p_data['name']}. Skipping."
                        )
                        skipped += 1
                        continue
                    logger_cli.info(f"Adding new prompt: {p_data['name']}")
                    new_prompt = Rule(
                        name=p_data["name"],
                        level=p_data["level"],
                        type=p_data["type"],
                        rule_text=p_data["rule_text"],
                        target_models_json=p_data["target_models_json"],
                        is_active=p_data.get("is_active", True),
                    )
                    db.session.add(new_prompt)
                    added += 1
            db.session.commit()
            logger_cli.info(
                f"Prompt seeding done: added={added}, updated={updated}, skipped={skipped}"
            )
            print(
                f"Prompt seeding finished. Added: {added}, Updated: {updated}, Skipped: {skipped}."
            )
    except Exception as e_seed_prompts:
        if db and db.session:
            db.session.rollback()
        logger_cli.error(f"Prompt seeding failed: {e_seed_prompts}", exc_info=True)
        print(f"Error during prompt seeding: {e_seed_prompts}")


@app.cli.command("db-health")
def db_health_cli():
    from backend.utils import migration_utils

    status = migration_utils.get_health()
    print(json.dumps(status, indent=2))


@app.cli.command("celery-health")
def celery_health_cli():
    result = celery.send_task('backend.celery_tasks_isolated.ping', queue='health')
    try:
        response = result.get(timeout=5)
    except Exception as exc:
        print(json.dumps({"status": "down", "error": str(exc)}))
        return
    print(json.dumps({"status": "up", "result": response}))


@app.cli.command("list-routes")
def list_routes_cli():
    output = []
    for rule in app.url_map.iter_rules():
        methods = ",".join(sorted(rule.methods - {"HEAD", "OPTIONS"}))
        output.append({"endpoint": rule.rule, "methods": methods})
    print(json.dumps(output, indent=2))


@app.cli.command("scan-sql")
def scan_sql_cli():
    from backend.tools import sql_scan

    sql_scan.main()


@app.cli.command("dead-code-scan")
def dead_code_cli():
    from backend.tools import dead_code_scan

    dead_code_scan.main()


@app.cli.command("index-entities")
@click.option("--force", is_flag=True, help="Force reindexing of all entities.")
@click.option("--type", help="Index only specific entity type (client, project, website, task).")
def index_entities_cli(force, type):
    from backend.services.entity_indexing_service import get_entity_indexing_service
    
    try:
        service = get_entity_indexing_service()
        
        if type:
            if type not in ["client", "project", "website", "task"]:
                print(f"Error: Invalid entity type '{type}'. Must be one of: client, project, website, task")
                return
            
            print(f"Indexing {type} entities...")
            
            if type == "client":
                from backend.models import Client
                entities = db.session.query(Client).all()
                success_count = sum(1 for entity in entities if service.index_client(entity))
            elif type == "project":
                from backend.models import Project
                entities = db.session.query(Project).all()
                success_count = sum(1 for entity in entities if service.index_project(entity))
            elif type == "website":
                from backend.models import Website
                entities = db.session.query(Website).all()
                success_count = sum(1 for entity in entities if service.index_website(entity))
            elif type == "task":
                from backend.models import Task
                entities = db.session.query(Task).all()
                success_count = sum(1 for entity in entities if service.index_task(entity))
            
            error_count = len(entities) - success_count
            print(f"Indexed {success_count} {type} entities, {error_count} errors")
            
        else:
            print("Indexing all entities...")
            results = service.index_all_entities()
            print(f"Entity indexing complete:")
            print(f"  Clients: {results.get('clients', 0)}")
            print(f"  Projects: {results.get('projects', 0)}")
            print(f"  Websites: {results.get('websites', 0)}")
            print(f"  Tasks: {results.get('tasks', 0)}")
            print(f"  Errors: {results.get('errors', 0)}")
        
        if service.storage_context:
            from backend.config import INDEX_ROOT
            persist_dir = getattr(service.storage_context, "persist_dir", INDEX_ROOT)
            if persist_dir and ("/storage" in persist_dir or "\\storage" in persist_dir or persist_dir.endswith("/storage") or persist_dir.endswith("\\storage")):
                persist_dir = INDEX_ROOT
                print(f"Prevented use of legacy storage folder, using {persist_dir} instead")
            service.storage_context.persist(persist_dir=persist_dir)
            print("Index changes persisted successfully")
        
    except Exception as e:
        print(f"Error indexing entities: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_host = os.environ.get("FLASK_RUN_HOST", "0.0.0.0")
    run_port = int(os.environ.get("FLASK_PORT", os.environ.get("FLASK_RUN_PORT", "5000")))
    app.logger.info(
        f"Starting Flask+SocketIO server on {run_host}:{run_port} "
        f"(LLM timeout: {app.config.get('LLM_REQUEST_TIMEOUT', LLM_REQUEST_TIMEOUT)}s)"
    )
    socketio.run(app, host=run_host, port=run_port, debug=app.debug,
                 allow_unsafe_werkzeug=True)
else:
    app.logger.info(
        f"Application instance '{app.name}' version {__version__} created and configured for WSGI server."
    )
