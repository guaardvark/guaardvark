
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from typing import Optional

import requests
from flask import Blueprint, current_app, jsonify, request, send_file
from sqlalchemy import desc

try:
    import psutil
except Exception:
    psutil = None

try:
    from llama_index.core import Settings
    from llama_index.core.storage.docstore import SimpleDocumentStore
    from llama_index.core.storage.index_store import SimpleIndexStore

    llama_index_available = True
except Exception:
    Settings = SimpleDocumentStore = SimpleIndexStore = None
    llama_index_available = False

try:
    import sys as _sys

    from backend.api.model_api import get_available_ollama_models
    from backend.models import Document, Project, Rule, Setting, db
    from backend.utils import prompt_utils

    _sys.modules[__name__ + ".prompt_utils"] = prompt_utils
    from backend import rule_utils
    from backend.utils.chat_utils import GLOBAL_DEFAULT_SYSTEM_PROMPT_RULE_NAME

    local_imports_ok = True
except Exception as e:
    local_imports_ok = False
    db = Document = Setting = Rule = Project = get_available_ollama_models = (
        prompt_utils
    ) = rule_utils = GLOBAL_DEFAULT_SYSTEM_PROMPT_RULE_NAME = None
    logging.getLogger(__name__).critical(
        f"Failed local imports in diagnostics_api: {e}", exc_info=True
    )


diagnostics_bp = Blueprint("diagnostics_api", __name__, url_prefix="/api/meta")

# Allow-list mirroring CATEGORIES in scripts/consolidated_selftest.py. The value
# becomes a subprocess argument, so only a key from here may pass through.
_SELFTEST_CATEGORIES = {
    c: c for c in ("database", "services", "filesystem", "backend", "llm", "imports")
}
logger = logging.getLogger(__name__)
METRICS_LOG_LEVEL = os.getenv("GUAARDVARK_METRICS_LOG_LEVEL", "INFO").upper()
metrics_logger = logging.getLogger("metrics")
metrics_logger.setLevel(getattr(logging, METRICS_LOG_LEVEL, logging.INFO))
metrics_logger.propagate = False
if not metrics_logger.handlers:
    metrics_logger.addHandler(logging.NullHandler())

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")


def _clear_pycache_folders(root_dir: str) -> int:
    folders_deleted = 0
    for root, dirs, files in os.walk(root_dir, topdown=False):
        if os.path.basename(root) == "__pycache__":
            try:
                logger.debug(f"Removing pycache dir: {root}")
                shutil.rmtree(root)
                folders_deleted += 1
            except OSError as e:
                logger.error(f"Error removing directory {root}: {e}")
    return folders_deleted


@diagnostics_bp.route("/clear-pycache-folders", methods=["POST"])
def clear_pycache_folders_endpoint():
    logger.info("API: Received POST /api/meta/clear-pycache-folders request")
    try:
        backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        folders_deleted = _clear_pycache_folders(backend_dir)
        logger.info(f"Cleared {folders_deleted} __pycache__ folders.")
        return (
            jsonify({"message": f"Cleared {folders_deleted} __pycache__ folders."}),
            200,
        )
    except Exception as e:
        logger.error(f"Error during pycache folder clearing: {e}", exc_info=True)
        return jsonify({"error": f"An error occurred: {e}"}), 500


@diagnostics_bp.route("/status", methods=["GET"])
def get_system_status():
    logger.info("API: Received GET /api/meta/status request")
    if not local_imports_ok or not llama_index_available:
        return jsonify({"error": "Core components unavailable for status check."}), 503

    python_version_str = (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    platform_str = sys.platform

    status = {
        "active_model": "N/A",
        "document_count": 0,
        "index_status": "N/A",
        "model_count": 0,
        "ollama_reachable": False,
        "timestamp": datetime.now().timestamp(),
        "version": current_app.config.get("APP_VERSION", "N/A"),
        "python_version": python_version_str,
        "platform": platform_str,
        "db_path": "N/A",
        "db_size_kb": "N/A",
    }
    try:
        llm_from_config = current_app.config.get("LLAMA_INDEX_LLM")
        if llm_from_config and hasattr(llm_from_config, "model"):
            status["active_model"] = getattr(llm_from_config, "model")
        elif Settings and Settings.llm and hasattr(Settings.llm, "model"):
            status["active_model"] = getattr(Settings.llm, "model")

        if db and Document:
            try:
                import re as _re
                status["document_count"] = db.session.query(Document.id).count()
                db_uri = str(db.engine.url)
                # Mask password in database URL for display
                status["db_path"] = _re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', db_uri)
                try:
                    from sqlalchemy import text
                    with db.engine.connect() as conn:
                        result = conn.execute(text("SELECT pg_database_size(current_database())"))
                        size_bytes = result.scalar()
                        status["db_size_kb"] = round(size_bytes / 1024, 2)
                except Exception:
                    status["db_size_kb"] = "N/A"
            except Exception as db_err:
                logger.error(f"Status: Error getting DB info: {db_err}")
                status["document_count"] = "Error"
                status["db_path"] = "Error"
                status["db_size_kb"] = "Error"

        storage_dir = current_app.config.get("STORAGE_DIR")
        if storage_dir and os.path.exists(os.path.join(storage_dir, "docstore.json")):
            status["index_status"] = "Exists"
        else:
            status["index_status"] = "Not Found/Empty"
            if storage_dir:
                status[
                    "index_status"
                ] += f" (Checked: {os.path.join(storage_dir, 'docstore.json')})"
            else:
                status["index_status"] += " (Storage dir not configured)"

        ollama_models_result = (
            get_available_ollama_models()
            if callable(get_available_ollama_models)
            else []
        )
        if isinstance(ollama_models_result, list):
            status["ollama_reachable"] = True
            status["model_count"] = len(ollama_models_result)
        else:
            status["ollama_reachable"] = False
            status["model_count"] = 0
            if (
                isinstance(ollama_models_result, dict)
                and "error" in ollama_models_result
            ):
                logger.warning(
                    f"Ollama check for status failed: {ollama_models_result['error']}"
                )
    except Exception as e:
        logger.error(f"Error gathering system status: {e}", exc_info=True)
        status["error"] = f"Failed to gather full status: {e}"
    return jsonify(status), 200


@diagnostics_bp.route("/llm-ready", methods=["GET"])
def llm_ready_endpoint():
    """Check if Ollama is reachable and has a model available."""
    model = None
    ready = False
    try:
        # First check if the app's LLM object is configured
        llm = current_app.config.get("LLAMA_INDEX_LLM")
        if llm:
            model = getattr(llm, "model", None)

        # Always verify Ollama is actually reachable
        resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
        if resp.ok:
            models = resp.json().get("models", [])
            if models:
                ready = True
                # If app LLM model not set, report first available
                if not model:
                    model = models[0].get("name")
            # Update the app flag so other code stays consistent
            current_app.config["LLM_READY"] = ready
    except Exception:
        ready = False
    return jsonify({"ready": ready, "model": model}), 200


# POST only: it loads the model and runs it, and a GET can be sent by any
# page (an <img>) without asking first.
@diagnostics_bp.route("/test-llm", methods=["POST"])
def test_llm_endpoint():
    logger.info("API: Received POST /api/meta/test-llm request")

    llm = None
    try:
        if llama_index_available:
            from llama_index.core import Settings
            llm = Settings.llm

        if not llm:
            llm = current_app.config.get("LLAMA_INDEX_LLM")

    except Exception as e:
        logger.warning(f"Error accessing LLM configuration: {e}")

    if not llm:
        return jsonify({"error": "LLM not configured"}), 503
    model_name = getattr(llm, "model", None) or "unknown"
    # Fast connectivity test: cap output and disable chain-of-thought so thinking
    # models (gemma4:12b, qwen3, ...) don't spend the whole test reasoning before
    # answering (an uncapped "ping" was ~6.5s on gemma4:12b; this is ~0.3s).
    from backend.utils.ollama_resource_manager import think_payload
    start = time.monotonic()
    try:
        import ollama as _ollama

        _kwargs = dict(
            model=model_name,
            messages=[{"role": "user", "content": "Reply with the single word: pong"}],
            stream=False,
            options={"num_predict": 32},
        )
        _kwargs.update(think_payload(model_name))  # only thinking-capable models accept `think`
        r = _ollama.chat(**_kwargs)
        msg = r.get("message", {}) if isinstance(r, dict) else getattr(r, "message", {})
        resp = (msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")) or ""
        duration = time.monotonic() - start
        return (
            jsonify(
                {
                    "model": model_name,
                    "duration_sec": round(duration, 3),
                    "response": resp,
                }
            ),
            200,
        )
    except Exception as e:
        logger.error("test-llm failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@diagnostics_bp.route("/metrics", methods=["GET"])
def get_system_metrics():
    metrics_logger.log(
        getattr(logging, METRICS_LOG_LEVEL, logging.INFO),
        "API: Received GET /api/meta/metrics request",
    )
    metrics = {
        "cpu_percent": None,
        "cpu_temp": None,
        "cpu_mem": None,
        "cpu_mem_used_gb": None,
        "cpu_mem_total_gb": None,
        "gpu_percent": None,
        "gpu_temp": None,
        "gpu_mem": None,
        "gpu_mem_used_gb": None,
        "gpu_mem_total_gb": None,
        "gpu_tools_available": None,
        "gpu_check_error": None,
    }
    try:
        if psutil:
            metrics["cpu_percent"] = psutil.cpu_percent(interval=None)
            vmem = psutil.virtual_memory()
            metrics["cpu_mem"] = vmem.percent
            metrics["cpu_mem_used_gb"] = round(vmem.used / (1024 ** 3), 2)
            metrics["cpu_mem_total_gb"] = round(vmem.total / (1024 ** 3), 2)
            try:
                temps = psutil.sensors_temperatures()
                if temps:
                    first_key = next(iter(temps))
                    entry = temps[first_key][0]
                    metrics["cpu_temp"] = getattr(entry, "current", None)
            except Exception as e_t:
                logger.debug(f"psutil temp read failed: {e_t}")
        else:
            if hasattr(os, "getloadavg"):
                load1, _, _ = os.getloadavg()
                metrics["cpu_percent"] = round(
                    min(100.0, (load1 / os.cpu_count()) * 100), 2
                )
    except Exception as e_cpu:
        logger.warning(f"CPU metrics unavailable: {e_cpu}")

    nvidia_cmd = shutil.which("nvidia-smi")
    nvitop_cmd = shutil.which("nvitop")
    rocm_cmd = shutil.which("rocm-smi")
    metrics["gpu_tools_available"] = bool(nvidia_cmd or nvitop_cmd or rocm_cmd)
    gpu_error = None
    if nvidia_cmd:
        try:
            output = subprocess.check_output(
                [
                    nvidia_cmd,
                    "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
            )
            if output:
                parts = [p.strip() for p in output.strip().split(",")]
                if len(parts) >= 4:
                    metrics["gpu_temp"] = float(parts[0])
                    metrics["gpu_percent"] = float(parts[1])
                    used_mb = float(parts[2])
                    total_mb = float(parts[3])
                    metrics["gpu_mem_used_gb"] = round(used_mb / 1024.0, 2)
                    metrics["gpu_mem_total_gb"] = round(total_mb / 1024.0, 2)
                    if total_mb > 0:
                        metrics["gpu_mem"] = round((used_mb / total_mb) * 100, 2)
        except Exception as e_gpu:
            gpu_error = f"nvidia-smi error: {e_gpu}"
            logger.debug(f"GPU metrics unavailable via nvidia-smi: {e_gpu}")
    elif nvitop_cmd:
        try:
            subprocess.check_output([nvitop_cmd, "--version"], text=True)
        except Exception as e_gpu:
            gpu_error = f"nvitop error: {e_gpu}"
            logger.debug(f"GPU check via nvitop failed: {e_gpu}")
    elif rocm_cmd:
        from backend.utils.gpu_metrics import rocm_smi_metrics
        rocm = rocm_smi_metrics(rocm_cmd)
        if rocm.get("available"):
            metrics["gpu_temp"] = rocm.get("temperature")
            metrics["gpu_percent"] = rocm.get("utilization")
            used_mb = rocm.get("memory_used_mb")
            total_mb = rocm.get("memory_total_mb")
            if used_mb is not None and total_mb:
                metrics["gpu_mem_used_gb"] = round(used_mb / 1024.0, 2)
                metrics["gpu_mem_total_gb"] = round(total_mb / 1024.0, 2)
                metrics["gpu_mem"] = rocm.get("memory_percent")
        else:
            gpu_error = rocm.get("error")
    else:
        gpu_error = "nvidia-smi, nvitop and rocm-smi not found"

    if gpu_error:
        metrics["gpu_check_error"] = gpu_error

    return jsonify(metrics), 200


# The stages of _gather_diagnostics, in the order they run.
LEGACY_STEPS = (
    ("ollama", "Ollama service"),
    ("model", "Chat model reply"),
    ("database", "Database, indexing queue and rules"),
    ("folders", "Storage, upload and output folders"),
    ("prompt", "Question-answering prompt"),
    ("chat", "Chat round trip"),
    ("log", "Backend log"),
)


def _app_version() -> str:
    try:
        from backend.utils.update_state import STARTUP_VERSION
        return STARTUP_VERSION or "N/A"
    except Exception:
        return "N/A"


def _legacy_step_outcome(key: str, r: dict) -> tuple:
    """(status, detail) for one finished stage of _gather_diagnostics."""
    if key == "ollama":
        if r.get("ollama_reachable"):
            return "passed", f"{r.get('model_count', 0)} models"
        return "failed", "Not reachable"
    if key == "model":
        name = r.get("active_model_name") or "No chat model"
        status = r.get("active_model_status", "Unknown")
        return ("passed" if r.get("llm_basic_response") else "failed"), f"{name}: {status}"
    if key == "database":
        if not r.get("db_connection"):
            return "failed", "Not connected"
        return "passed", f"{r.get('document_count_db', 0)} documents; {r.get('indexing_queue_status')}"
    if key == "folders":
        closed = [n for n in ("storage", "upload", "output") if not r.get(f"{n}_dir_accessible")]
        return ("failed", "Not writable: " + ", ".join(closed)) if closed else ("passed", "All writable")
    if key == "prompt":
        return ("passed", "Loadable") if r.get("qa_prompt_loadable") else ("warning", "Not found")
    if key == "chat":
        reply = str(r.get("qa_hello_response") or "")
        if not reply or reply.startswith("[Error"):
            return "failed", reply[:120] or "No reply"
        return "passed", reply[:80]
    if key == "log":
        text = str(r.get("backend_log_errors") or "")
        return ("passed" if text.startswith("No ") else "warning"), text
    return "passed", ""


def _gather_diagnostics(on_step=None) -> dict:
    """Run the basic checks. ``on_step(key, results)`` is called as each stage
    of LEGACY_STEPS begins, with the results gathered so far."""
    global rule_utils, db, Document, Setting, Rule, Project

    def _stage(key):
        if on_step is not None:
            on_step(key, results)

    results = {
        "ollama_reachable": False,
        "active_model_name": None,
        "active_model_status": "Unknown",
        "active_model_health": "Unknown",
        "model_count": 0,
        "db_connection": False,
        "index_storage_exists": False,
        "document_count_db": 0,
        "storage_dir_accessible": False,
        "storage_dir_path": None,
        "upload_dir_accessible": False,
        "upload_dir_path": None,
        "output_dir_accessible": False,
        "output_dir_path": None,
        "llm_basic_response": False,
        "qa_prompt_loadable": False,
        "indexing_queue_status": "N/A",
        "recent_indexing_errors": "N/A",
        "backend_log_errors": "N/A",
        "qa_default_rule_id": None,
        "system_prompt_source": None,
        "timestamp": datetime.now().timestamp(),
        "version": current_app.config.get("APP_VERSION") or _app_version(),
    }

    results["rule_details"] = []
    results["rule_warnings"] = []
    results["qa_hello_response"] = None

    try:
        _stage("ollama")
        ollama_models_res = get_available_ollama_models()
        if isinstance(ollama_models_res, list):
            results["ollama_reachable"] = True
            results["model_count"] = len(ollama_models_res)
        else:
            logger.warning(
                "SelfTest: Ollama check failed: %s",
                (
                    ollama_models_res.get("error")
                    if isinstance(ollama_models_res, dict)
                    else "Unknown error"
                ),
            )

        llm_conf = current_app.config.get("LLAMA_INDEX_LLM")
        if llm_conf and hasattr(llm_conf, "model"):
            results["active_model_name"] = getattr(llm_conf, "model")
        elif Settings and Settings.llm and hasattr(Settings.llm, "model"):
            results["active_model_name"] = getattr(Settings.llm, "model")

        try:
            if rule_utils and db:
                qa_text, qa_id = rule_utils.get_active_qa_default_template(
                    db.session, model_name=results["active_model_name"]
                )
                if qa_text:
                    results["qa_default_rule_id"] = qa_id
                    results["system_prompt_source"] = f"qa_default:{qa_id}"
                else:
                    sys_text, sys_id = rule_utils.get_active_system_prompt(
                        GLOBAL_DEFAULT_SYSTEM_PROMPT_RULE_NAME,
                        db.session,
                        model_name=results["active_model_name"],
                    )
                    if sys_text:
                        results["system_prompt_source"] = (
                            f"{GLOBAL_DEFAULT_SYSTEM_PROMPT_RULE_NAME}:{sys_id}"
                        )
                    else:
                        results["system_prompt_source"] = "HARD_CODED_DEFAULT_PROMPT"
            else:
                results["system_prompt_source"] = "MODULES_NOT_AVAILABLE"
        except Exception as e:
            logger.error(
                "SelfTest: Error fetching system prompt info: %s", e, exc_info=True
            )

        _stage("model")
        if results["active_model_name"]:
            results["active_model_status"] = "Configured"
            try:
                llm_to_test = current_app.config.get("LLAMA_INDEX_LLM") or Settings.llm
                if llm_to_test:
                    response = llm_to_test.complete("Test prompt for selftest.")
                    if response and getattr(response, "text", "").strip():
                        results["llm_basic_response"] = True
                        results["active_model_status"] = "Responsive"
                    else:
                        logger.warning(
                            "SelfTest: LLM basic response short/empty. Resp: %s",
                            response.text if response else "None",
                        )
                        results["active_model_status"] = (
                            "Configured but not responsive/empty response"
                        )
                else:
                    results["active_model_status"] = "Not loaded in LlamaIndex Settings"
            except Exception as e:
                logger.error("SelfTest: LLM basic response error: %s", e, exc_info=True)
                results["active_model_status"] = f"Error: {str(e)[:50]}"

            try:
                import requests
                resp = requests.post(
                    f"{OLLAMA_BASE_URL}/api/show",
                    json={"name": results["active_model_name"]},
                    timeout=10,
                )
                results["active_model_health"] = (
                    "Loaded" if resp.ok else f"Not Found ({resp.status_code})"
                )
            except ImportError as e:
                results["active_model_health"] = f"Import Error: requests module not available - {e}"
            except Exception as e:
                results["active_model_health"] = f"Error: {e}"
        else:
            results["active_model_status"] = "Not configured"

        _stage("database")
        if db and Setting and Document and Rule:
            try:
                db.session.query(Setting.key).first()
                results["db_connection"] = True
                results["document_count_db"] = db.session.query(Document.id).count()
                pending_count = (
                    db.session.query(Document)
                    .filter(Document.index_status.in_(["PENDING", "INDEXING"]))
                    .count()
                )
                results["indexing_queue_status"] = (
                    f"{pending_count} items pending/indexing"
                    if pending_count > 0
                    else "Idle / Queue empty"
                )
                error_docs = (
                    db.session.query(Document.filename, Document.error_message)
                    .filter(Document.index_status == "ERROR")
                    .order_by(desc(Document.updated_at))
                    .limit(3)
                    .all()
                )
                if error_docs:
                    error_summary = [
                        f"'{doc.filename}': {(doc.error_message or 'No error message')[:70]}..."
                        for doc in error_docs
                    ]
                    results["recent_indexing_errors"] = (
                        f"{len(error_docs)} recent error(s). Examples: {'; '.join(error_summary)}"
                    )
                    if db.session.query(Document).filter(
                        Document.index_status == "ERROR"
                    ).count() > len(error_docs):
                        results["recent_indexing_errors"] += " (More errors exist)"
                else:
                    results["recent_indexing_errors"] = (
                        "No recent indexing errors found in DB."
                    )

                from hashlib import sha256

                all_rules = Rule.query.order_by(Rule.id).all()
                active_map = {}
                for r in all_rules:
                    sig = sha256(
                        str((r.name, r.level, r.type, r.command_label)).encode("utf-8")
                    ).hexdigest()
                    rule_dict = r.to_dict()
                    rule_dict["sig"] = sig
                    rule_dict["target_models_json"] = r.target_models_json
                    rule_dict.pop("project", None)
                    rule_dict.pop("target_models", None)
                    rule_dict["is_active"] = bool(r.is_active)
                    if rule_dict["is_active"]:
                        existing = active_map.get(sig)
                        rule_dict["active_dup"] = bool(existing)
                        active_map[sig] = rule_dict
                    results["rule_details"].append(rule_dict)
                active_dupes = [
                    rd for rd in results["rule_details"] if rd.get("active_dup")
                ]
                if active_dupes:
                    results["rule_warnings"].append(
                        f"{len(active_dupes)} duplicate active rules detected"
                    )
            except Exception as e:
                logger.error(
                    "SelfTest: DB and Rule checks failed: %s", e, exc_info=True
                )

        _stage("folders")
        storage_dir_path_val = current_app.config.get("STORAGE_DIR")
        results["storage_dir_path"] = storage_dir_path_val
        if storage_dir_path_val and os.path.exists(
            os.path.join(storage_dir_path_val, "docstore.json")
        ):
            results["index_storage_exists"] = True

        dirs_to_check = {
            "STORAGE_DIR": ("storage_dir_path", "storage_dir_accessible"),
            "UPLOAD_FOLDER": ("upload_dir_path", "upload_dir_accessible"),
            "OUTPUT_DIR": ("output_dir_path", "output_dir_accessible"),
        }

        for config_key, (path_key, access_key) in dirs_to_check.items():
            dir_path = current_app.config.get(config_key)
            results[path_key] = dir_path
            if dir_path and os.path.isdir(dir_path):
                test_file = os.path.join(
                    dir_path, f".selftest_{datetime.now().timestamp()}"
                )
                try:
                    with open(test_file, "w") as f:
                        f.write("test")
                    os.remove(test_file)
                    results[access_key] = True
                except Exception as e:
                    logger.warning(
                        "SelfTest: Write access check failed for %s: %s", dir_path, e
                    )
            else:
                logger.warning(
                    "SelfTest: Dir %s for %s not found or not a directory.",
                    dir_path,
                    config_key,
                )

        _stage("prompt")
        if prompt_utils and results["active_model_name"]:
            try:
                from backend import rule_utils
                from backend.models import db
                
                template, rule_id = rule_utils.get_active_qa_default_template(
                    db.session, model_name=results["active_model_name"]
                )
                
                if (
                    template
                    and "{query_str}" in template
                    and "{context_str}" in template
                ):
                    results["qa_prompt_loadable"] = True
                    if rule_id:
                        logger.debug(f"SelfTest: QA prompt loaded from database rule ID {rule_id}")
                    else:
                        logger.debug("SelfTest: QA prompt loaded using fallback template")
                else:
                    logger.warning("SelfTest: QA prompt missing required placeholders")
            except Exception as e:
                logger.error("SelfTest: QA prompt load error: %s", e, exc_info=True)

        _stage("chat")
        try:
            from backend.utils.llm_service import run_llm_chat_prompt

            results["qa_hello_response"] = run_llm_chat_prompt("hello")
        except Exception as e:
            results["qa_hello_response"] = f"[Error running LLM: {e}]"

        _stage("log")
        log_file_path = current_app.config.get(
            "LOG_FILE_PATH",
            os.path.join(current_app.root_path, "..", "logs", "backend.log"),
        )
        if os.path.exists(log_file_path):
            try:
                with open(log_file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                recent_lines = lines[-200:]
                error_count = sum("ERROR" in line for line in recent_lines)
                critical_count = sum("CRITICAL" in line for line in recent_lines)
                results["backend_log_errors"] = (
                    f"{error_count} ERRORs, {critical_count} CRITICALs in last ~200 lines."
                    if error_count or critical_count
                    else "No ERROR/CRITICAL messages in last ~200 lines."
                )
            except Exception as e:
                results["backend_log_errors"] = f"Error reading log file: {e}"
        else:
            results["backend_log_errors"] = f"Log file not found at {log_file_path}."
    except Exception as e:
        logger.error("Error during diagnostics gathering: %s", e, exc_info=True)

    return results


def _pip_check(legacy_results: dict) -> None:
    try:
        pip_check = subprocess.run(
            [sys.executable, "-m", "pip", "check"],
            capture_output=True, text=True, timeout=30
        )
        if pip_check.returncode == 0:
            legacy_results["pip_check"] = pip_check.stdout.strip()
        else:
            # pip lists the conflicts on stdout.
            conflicts = (pip_check.stdout or pip_check.stderr or "").strip()
            legacy_results["pip_check"] = (
                f"Error (code {pip_check.returncode}): {conflicts or 'no detail given'}")
    except subprocess.TimeoutExpired:
        legacy_results["pip_check"] = "Timeout after 30 seconds"
    except Exception as e:
        legacy_results["pip_check"] = f"Error running pip check: {e}"


def _pip_outdated(legacy_results: dict) -> None:
    # Listing outdated packages asks PyPI about every installed package.
    from backend.utils.settings_utils import web_access_block_reason
    if web_access_block_reason("check PyPI for newer packages"):
        legacy_results["pip_outdated"] = "Skipped: it asks PyPI, and web access is off"
        return
    try:
        outdated = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--outdated"],
            capture_output=True, text=True, timeout=30
        )
        legacy_results["pip_outdated"] = outdated.stdout.strip() if outdated.returncode == 0 else f"Error (code {outdated.returncode})"
    except subprocess.TimeoutExpired:
        legacy_results["pip_outdated"] = "Timeout after 30 seconds"
    except Exception as e:
        legacy_results["pip_outdated"] = f"Error listing outdated packages: {e}"


def _pip_step_outcome(text: str) -> tuple:
    text = str(text or "")
    if text.startswith("Skipped"):
        return "skipped", text
    if text.startswith(("Error", "Timeout")):
        return "warning", text.splitlines()[0][:160]
    lines = text.splitlines()
    return "passed", (lines[0][:160] if len(lines) == 1 else f"{max(0, len(lines) - 2)} newer versions available")


def _selftest_results(test_mode: str, test_category: Optional[str], include_legacy: bool,
                      run=None) -> dict:
    """Run a self-test and return its results. ``run`` (a diagnostic_runs.Run)
    is told each step as it starts and ends."""
    results = {}

    if include_legacy:
        if run is None:
            legacy_results = _gather_diagnostics()
        else:
            state = {"key": None}

            def on_step(key, partial):
                if state["key"]:
                    run.finish_step(state["key"], *_legacy_step_outcome(state["key"], partial))
                state["key"] = key
                run.start_step(key)

            legacy_results = _gather_diagnostics(on_step)
            if state["key"]:
                run.finish_step(state["key"], *_legacy_step_outcome(state["key"], legacy_results))

        for key, check in (("pip_check", _pip_check), ("pip_outdated", _pip_outdated)):
            if run is not None:
                run.start_step(key)
            check(legacy_results)
            if run is not None:
                run.finish_step(key, *_pip_step_outcome(legacy_results.get(key)))

        results["legacy_diagnostics"] = legacy_results

    if test_mode in ["comprehensive", "quick"] or test_category:
        comprehensive_results = _run_comprehensive_selftest(
            test_mode, test_category, on_event=_selftest_event_sink(run))
        results.update(comprehensive_results)

    advanced_debug_enabled = False
    try:
        if db and Setting:
            setting = db.session.get(Setting, "advanced_debug")
            advanced_debug_enabled = (
                setting.value == "true" if setting else False)
    except Exception as e:
        logger.error(f"Failed to read advanced debug setting: {e}")
        advanced_debug_enabled = (
            os.getenv("ADVANCED_DEBUG", "false").lower() == "true")

    if advanced_debug_enabled and include_legacy:
        # The recorded results, not the subprocess objects: neither exists
        # when its check was skipped (web access off) or timed out.
        logger.debug("pip check output:\n%s", legacy_results.get("pip_check"))
        logger.debug("pip list outdated:\n%s", legacy_results.get("pip_outdated"))

    overall_status = _determine_overall_status(results)
    results["overall_status"] = overall_status
    results["test_mode"] = test_mode
    results["timestamp"] = datetime.now().isoformat()

    logger.info("Self-test results summary: %s", {
        "mode": test_mode,
        "status": overall_status,
        "categories": list(results.get("categories", {}).keys())
        if "categories" in results else []
    })
    return results


def _selftest_request():
    """(mode, category, include_legacy) from the request body, or an error response."""
    data = request.get_json(silent=True) or {}
    test_mode = data.get("mode", "basic")
    if test_mode not in ("basic", "quick", "comprehensive"):
        return None, (jsonify({"error": "Unknown self-test mode"}), 400)
    category_arg = data.get("category", None)
    # Only an allow-listed key reaches the subprocess argument list.
    test_category = _SELFTEST_CATEGORIES.get(str(category_arg)) if category_arg is not None else None
    if category_arg is not None and test_category is None:
        return None, (jsonify({"error": "Unknown self-test category"}), 400)
    return (test_mode, test_category, data.get("include_legacy", True)), None


@diagnostics_bp.route("/selftest", methods=["POST"])
def run_selftest():
    logger.info("API: Received POST /api/meta/selftest request")
    if not local_imports_ok or not llama_index_available:
        return jsonify({"error": "Core components unavailable for self-test."}), 503

    parsed, error = _selftest_request()
    if error:
        return error
    results = _selftest_results(*parsed)
    return jsonify({"message": "Self-test complete.", "results": results}), 200


# Step labels for the categories of scripts/consolidated_selftest.py.
_SELFTEST_QUICK = ("database", "services", "backend")
_SELFTEST_LABELS = {
    "database": "Database", "services": "External services", "filesystem": "File system and storage",
    "backend": "Backend API", "llm": "LLM and embeddings", "imports": "Python dependencies",
}
_RUN_STATUS = {"pass": "passed", "fail": "failed", "warning": "warning"}


def _selftest_event_sink(run):
    """Turn the self-test script's progress lines into steps on ``run``."""
    if run is None:
        return None
    state = {"category": None, "done": 0}

    def on_event(event):
        kind = event.get("event")
        if kind == "category_start":
            state["category"], state["done"] = event.get("key"), 0
            run.start_step(f"cat_{state['category']}")
        elif kind == "test_start" and state["category"]:
            run.step_detail(f"cat_{state['category']}", str(event.get("name", "")))
        elif kind == "category":
            run.finish_step(f"cat_{event.get('key')}", _RUN_STATUS.get(event.get("status"), "failed"),
                            str(event.get("summary", "")), event.get("duration"))
    return on_event


def _diagnostics_worker(app, parsed):
    test_mode, test_category, include_legacy = parsed

    def work(run):
        steps = []
        if include_legacy:
            steps += [(key, label, "System") for key, label in LEGACY_STEPS]
            steps += [("pip_check", "Package conflicts (pip check)", "System"),
                      ("pip_outdated", "Newer package versions", "System")]
        if test_category:
            categories = (test_category,)
        elif test_mode == "quick":
            categories = _SELFTEST_QUICK
        elif test_mode == "comprehensive":
            categories = tuple(_SELFTEST_LABELS)
        else:
            categories = ()
        steps += [(f"cat_{c}", _SELFTEST_LABELS[c], "Self-test") for c in categories]
        run.plan(steps)
        with app.app_context():
            try:
                results = _selftest_results(test_mode, test_category, include_legacy, run=run)
            finally:
                if db is not None:
                    db.session.remove()  # this thread's session, not a request's
        overall = results.get("overall_status")
        with run.lock:
            run.result = results
            run.current = None
            run.status = {"PASS": "passed", "WARNING": "warning"}.get(overall, "failed")
            if run.status == "failed":
                run.error = f"Diagnostics finished: {overall}"
    return work


@diagnostics_bp.route("/selftest/start", methods=["POST"])
def start_selftest():
    """Start a self-test in the background; poll /api/meta/runs/<id> for it."""
    if not local_imports_ok or not llama_index_available:
        return jsonify({"error": "Core components unavailable for self-test."}), 503
    parsed, error = _selftest_request()
    if error:
        return error
    from backend.services import diagnostic_runs
    try:
        run = diagnostic_runs.start(
            "diagnostics", parsed[0],
            _diagnostics_worker(current_app._get_current_object(), parsed),
            title="Diagnostics")
    except diagnostic_runs.AlreadyRunning as busy:
        return jsonify({"run": busy.run.snapshot(), "already_running": True}), 200
    return jsonify({"run": run.snapshot()}), 202


def _run_comprehensive_selftest(
        test_mode: str, test_category: Optional[str] = None, on_event=None) -> dict:
    try:
        script_path = os.path.abspath(os.path.join(
            current_app.root_path, "..", "scripts", "consolidated_selftest.py"))
        output_path = os.path.abspath(os.path.join(
            current_app.root_path, "..", "logs", "api_selftest_results.json"))

        if not os.path.exists(script_path):
            logger.error(f"Consolidated selftest script not found at {script_path}")
            return {
                "comprehensive_test_available": False,
                "error": f"Test script not found: {script_path}",
                "execution_time": 0.0
            }

        cmd = [sys.executable, script_path]
        if test_category:
            cmd.extend(["--category", test_category])
        elif test_mode == "quick":
            cmd.append("--quick")
        else:
            cmd.append("--all")

        cmd.extend(["--output", output_path, "--quiet"])
        if on_event is not None:
            cmd.append("--progress")

        start_time = time.time()
        proc = _run_reporting(cmd, os.path.join(current_app.root_path, ".."), 300, on_event)
        duration = time.time() - start_time

        results = {
            "comprehensive_test_available": True,
            "execution_time": duration,
            "exit_code": proc.returncode
        }

        try:
            if not os.path.exists(output_path):
                logger.warning(f"Test results file not created: {output_path}")
                results["categories"] = {}
                results["error"] = f"Test results file not created at {output_path}"
            else:
                with open(output_path, 'r') as f:
                    test_data = json.load(f)
                results.update(test_data)
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in test results: {e}")
            results["categories"] = {}
            results["error"] = f"Invalid JSON in test results: {str(e)}"
        except IOError as e:
            logger.warning(f"Could not read test results file: {e}")
            results["categories"] = {}
            results["error"] = f"Could not read test results: {str(e)}"

        if proc.stdout and on_event is None:
            results["stdout_summary"] = proc.stdout[-500:]
        if proc.stderr:
            results["stderr"] = proc.stderr[-500:]

        return results

    except subprocess.TimeoutExpired:
        logger.error("Comprehensive selftest timed out after 5 minutes")
        return {
            "comprehensive_test_available": False,
            "error": "Test execution timeout (5 minutes)",
            "execution_time": 300.0
        }
    except Exception as e:
        logger.error(f"Error running comprehensive selftest: {e}",
                     exc_info=True)
        return {
            "comprehensive_test_available": False,
            "error": f"Test execution error: {str(e)}",
            "execution_time": 0.0
        }


def _run_reporting(cmd, cwd, timeout, on_event):
    """subprocess.run with captured text output; with ``on_event`` each JSON
    line the child prints is passed to it as it arrives."""
    if on_event is None:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    import threading
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd)
    timed_out = threading.Event()

    def _expire():
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout, _expire)
    timer.start()
    stderr_chunks = []
    drain = threading.Thread(target=lambda: stderr_chunks.append(proc.stderr.read()), daemon=True)
    drain.start()
    stdout_lines = []
    try:
        for line in proc.stdout:
            stdout_lines.append(line)
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                on_event(event)
        proc.wait()
        drain.join(timeout=5)
    finally:
        timer.cancel()
    if timed_out.is_set():
        raise subprocess.TimeoutExpired(cmd, timeout)
    return subprocess.CompletedProcess(cmd, proc.returncode, "".join(stdout_lines), "".join(stderr_chunks))


def _determine_overall_status(results: dict) -> str:
    legacy = results.get("legacy_diagnostics")
    if legacy:
        if legacy.get("llm_basic_response") is False:
            return "CRITICAL"
        if legacy.get("db_connection") is False:
            return "FAIL"
    legacy = legacy or {}

    categories = results.get("categories", {})
    if categories:
        # The self-test script writes its statuses in lower case.
        category_statuses = [
            str(cat.get("status", "ERROR")).upper() for cat in categories.values()]

        if any(status == "FAIL" for status in category_statuses):
            return "FAIL"
        elif any(status == "ERROR" for status in category_statuses):
            return "ERROR"
        elif any(status in ("PARTIAL", "WARNING") for status in category_statuses):
            return "WARNING"
        elif all(status == "PASS" for status in category_statuses):
            return "PASS"

    if legacy.get("active_model_status") == "Responsive":
        return "PASS"
    else:
        return "WARNING"


# POST only: gathering diagnostics runs the model twice and writes probe files
# (and, with include_system, a copy of the data), which a GET from any page
# could otherwise start.
@diagnostics_bp.route("/diagnostics/export", methods=["POST"])
def export_diagnostics():
    logger.info("API: Received POST /api/meta/diagnostics/export request")
    if not local_imports_ok or not llama_index_available:
        return jsonify({"error": "Core components unavailable for export."}), 503

    body = request.get_json(silent=True) or {}
    include_system = str(body.get("include_system", request.args.get("include_system"))) in ("1", "true", "True")

    results = _gather_diagnostics()
    if include_system:
        results["included_system_data"] = True
        results["db_files"] = []
        results["embedding_dirs"] = []
    try:
        active_rules = [
            r for r in results.get("rule_details", []) if r.get("is_active")
        ]
        log_file_path = current_app.config.get(
            "LOG_FILE_PATH",
            os.path.join(current_app.root_path, "..", "logs", "backend.log"),
        )
        log_lines: list[str] = []
        if os.path.exists(log_file_path):
            with open(log_file_path, "r", encoding="utf-8") as f:
                log_lines = f.readlines()[-500:]

        with tempfile.TemporaryDirectory() as tmpdir:
            diag_path = os.path.join(tmpdir, "diagnostics.json")
            rules_path = os.path.join(tmpdir, "active_rules.json")
            log_path = os.path.join(tmpdir, "backend_log_tail.txt")
            with open(diag_path, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2)
            with open(rules_path, "w", encoding="utf-8") as f:
                json.dump(active_rules, f, indent=2)
            with open(log_path, "w", encoding="utf-8") as f:
                f.writelines(log_lines)

            extra_items: list[tuple[str, str]] = []
            if include_system:
                # For PostgreSQL, export database info instead of copying a file
                if db and db.engine:
                    import re as _re
                    db_url = str(db.engine.url)
                    masked_url = _re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', db_url)
                    dest_dir = os.path.join(tmpdir, "db")
                    os.makedirs(dest_dir, exist_ok=True)
                    info_path = os.path.join(dest_dir, "database_info.json")
                    db_info = {"database_url": masked_url, "engine_driver": db.engine.driver}
                    try:
                        from sqlalchemy import text
                        with db.engine.connect() as conn:
                            size_result = conn.execute(text("SELECT pg_database_size(current_database())"))
                            db_info["database_size_bytes"] = size_result.scalar()
                    except Exception as e_size:
                        db_info["database_size_error"] = str(e_size)
                    with open(info_path, "w", encoding="utf-8") as f_info:
                        json.dump(db_info, f_info, indent=2)
                    rel = os.path.relpath(info_path, tmpdir)
                    results["db_files"].append(rel)
                    extra_items.append((info_path, rel))

                embed_dirs = []
                storage_dir = current_app.config.get("STORAGE_DIR")
                if storage_dir and os.path.isdir(storage_dir):
                    embed_dirs.append(storage_dir)
                for key in ("EMBEDDINGS_DIR", "VECTORSTORE_DIR"):
                    val = current_app.config.get(key)
                    if val and os.path.isdir(val):
                        embed_dirs.append(val)
                for ed in embed_dirs:
                    dest = os.path.join(tmpdir, "embeddings", os.path.basename(ed))
                    shutil.copytree(ed, dest)
                    rel = os.path.relpath(dest, tmpdir)
                    results["embedding_dirs"].append(rel)
                    extra_items.append((dest, rel))

            zip_path = os.path.join(tmpdir, "diagnostics_export.zip")
            with zipfile.ZipFile(zip_path, "w") as zipf:
                zipf.write(diag_path, "diagnostics.json")
                zipf.write(rules_path, "active_rules.json")
                zipf.write(log_path, "backend_log_tail.txt")
                for src, rel in extra_items:
                    if os.path.isdir(src):
                        for root, dirs, files in os.walk(src):
                            for fn in files:
                                full = os.path.join(root, fn)
                                arc = os.path.join(rel, os.path.relpath(full, src))
                                zipf.write(full, arc)
                    else:
                        zipf.write(src, rel)

            return send_file(
                zip_path, as_attachment=True, download_name="diagnostics_export.zip"
            )
    except Exception as e:
        logger.error("Error creating diagnostics export: %s", e, exc_info=True)
        return jsonify({"error": f"Failed to create export: {e}"}), 500


@diagnostics_bp.route("/run-tests", methods=["POST"])
def run_tests_endpoint():
    """Start the test suite in the background; poll /api/meta/runs/<id> for it.

    ``scope`` is "quick" (run_tests.py --quick) or "full". The full suite runs
    for about nine minutes on a fast workstation, past any request timeout.
    """
    logger.info("API: Received POST /api/meta/run-tests request")
    from backend.services import diagnostic_runs
    scope = (request.get_json(silent=True) or {}).get("scope", "quick")
    if scope not in ("quick", "full"):
        return jsonify({"error": "Unknown test scope"}), 400
    repo_root = os.path.abspath(os.path.join(current_app.root_path, ".."))
    if not os.path.exists(os.path.join(repo_root, "run_tests.py")):
        return jsonify({"error": f"Test script not found at {repo_root}/run_tests.py"}), 404
    try:
        run = diagnostic_runs.start(
            "tests", scope,
            lambda run: diagnostic_runs.run_test_suite(run, repo_root),
            title="Test suite")
    except diagnostic_runs.AlreadyRunning as busy:
        return jsonify({"run": busy.run.snapshot(), "already_running": True}), 200
    return jsonify({"run": run.snapshot()}), 202


@diagnostics_bp.route("/runs/latest", methods=["GET"])
def latest_run():
    """The newest run of ``kind`` (diagnostics or tests), so a reloaded page
    can pick a running one back up. ``run`` is null when there has been none."""
    from backend.services import diagnostic_runs
    run = diagnostic_runs.latest(request.args.get("kind", "tests"))
    return jsonify({"run": run.snapshot() if run else None}), 200


@diagnostics_bp.route("/runs/<run_id>", methods=["GET"])
def get_run(run_id):
    from backend.services import diagnostic_runs
    run = diagnostic_runs.get(run_id)
    if run is None:
        return jsonify({"error": "No such run. Runs are forgotten when the backend restarts."}), 404
    return jsonify({"run": run.snapshot()}), 200


@diagnostics_bp.route("/runs/<run_id>/cancel", methods=["POST"])
def cancel_run(run_id):
    from backend.services import diagnostic_runs
    run = diagnostic_runs.get(run_id)
    if run is None:
        return jsonify({"error": "No such run."}), 404
    diagnostic_runs.cancel(run_id)
    return jsonify({"run": run.snapshot()}), 200


@diagnostics_bp.route("/diagnostics/restore", methods=["POST"])
def restore_diagnostics():
    logger.info("API: Received POST /api/meta/diagnostics/restore request")
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    uploaded = request.files["file"]
    
    if not uploaded.filename or not uploaded.filename.endswith('.zip'):
        return jsonify({"error": "Only ZIP files are allowed"}), 400
    
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "upload.zip")
        uploaded.save(zip_path)
        
        try:
            with zipfile.ZipFile(zip_path, "r") as zipf:
                for info in zipf.infolist():
                    if '..' in info.filename or info.filename.startswith('/') or '\\' in info.filename:
                        logger.warning(f"Dangerous path in zip file: {info.filename}")
                        return jsonify({"error": "ZIP file contains invalid paths"}), 400
                    
                    if info.file_size > 100 * 1024 * 1024:
                        return jsonify({"error": "ZIP file contains files that are too large"}), 400
                
                zipf.extractall(tmpdir)
        except zipfile.BadZipFile:
            return jsonify({"error": "Invalid or corrupted ZIP file"}), 400
        except Exception as e:
            logger.error(f"Error extracting ZIP file: {e}")
            return jsonify({"error": "Failed to extract ZIP file"}), 400

        meta_path = os.path.join(tmpdir, "diagnostics.json")
        meta = {}
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                try:
                    meta = json.load(f)
                except Exception:
                    meta = {}

        if meta.get("included_system_data"):
            # Database restore is not supported for PostgreSQL databases.
            # The db_files entry now contains metadata only (database_info.json).
            for rel in meta.get("db_files", []):
                if '..' in rel or rel.startswith('/') or '\\' in rel:
                    logger.warning(f"Dangerous database file path: {rel}")
                    continue
                src = os.path.join(tmpdir, rel)
                if os.path.exists(src) and src.endswith('.json'):
                    logger.info(f"Database info file found in export: {rel} (restore not supported for PostgreSQL)")

            for rel in meta.get("embedding_dirs", []):
                if '..' in rel or rel.startswith('/') or '\\' in rel:
                    logger.warning(f"Dangerous embedding directory path: {rel}")
                    continue
                    
                src = os.path.join(tmpdir, rel)
                dest_root = current_app.config.get("STORAGE_DIR")
                if dest_root and os.path.isdir(dest_root) and os.path.exists(src):
                    safe_dirname = os.path.basename(rel)
                    if safe_dirname and not safe_dirname.startswith('.'):
                        dest = os.path.join(dest_root, safe_dirname)
                        
                        dest_real = os.path.realpath(dest)
                        storage_real = os.path.realpath(dest_root)
                        
                        if dest_real.startswith(storage_real + os.sep):
                            try:
                                if os.path.exists(dest):
                                    shutil.rmtree(dest)
                                shutil.copytree(src, dest)
                                logger.info(f"Restored embedding directory to: {dest}")
                            except Exception as e:
                                logger.error(f"Failed to restore embedding directory: {e}")

    return jsonify({"message": "Restore completed."}), 200


@diagnostics_bp.route("/quality-scorecard", methods=["POST"])
def get_quality_scorecard():
    """Structured quality scorecard for KPIs, CI, and automation (see docs/quality/).

    POST only: it runs the RAG assessment (retrieval, answers and judging on
    the model) and probes ``base_url``, which a GET from any page could
    otherwise start. ``base_url`` comes from the JSON body or the query string.
    """
    try:
        from flask import current_app

        from backend.services.quality_scorecard import build_scorecard

        body = request.get_json(silent=True) or {}
        base_url = body.get("base_url") or request.args.get("base_url")
        payload = build_scorecard(
            app=current_app._get_current_object(),
            public_base_url=base_url,
        )
        return jsonify({"success": True, "data": payload}), 200
    except Exception as e:
        logger.error("quality-scorecard failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@diagnostics_bp.route("/gpu-progress", methods=["GET"])
def get_gpu_progress_metrics():
    try:
        from backend.utils.unified_progress_system import get_unified_progress_system
        
        progress_system = get_unified_progress_system()
        gpu_metrics = progress_system.get_gpu_metrics()
        gpu_processing_active = progress_system.is_gpu_processing_active()
        gpu_memory_usage = progress_system.get_gpu_memory_usage()
        
        active_processes = progress_system.get_active_processes()
        gpu_bound_processes = []
        
        for process_id, event in active_processes.items():
            if event.process_type.value in ['llm_processing', 'file_generation', 'csv_processing']:
                gpu_bound_processes.append({
                    "process_id": process_id,
                    "type": event.process_type.value,
                    "progress": event.progress,
                    "message": event.message,
                    "status": event.status.value,
                    "gpu_metrics": event.additional_data.get("gpu_metrics"),
                    "gpu_processing_active": event.additional_data.get("gpu_processing_active")
                })
        
        return jsonify({
            "gpu_metrics": gpu_metrics,
            "gpu_processing_active": gpu_processing_active,
            "gpu_memory_usage": gpu_memory_usage,
            "gpu_bound_processes": gpu_bound_processes,
            "active_process_count": len(active_processes),
            "gpu_bound_process_count": len(gpu_bound_processes)
        }), 200
        
    except Exception as e:
        logger.error(f"GPU progress metrics failed: {e}")
        return jsonify({"error": str(e)}), 500
