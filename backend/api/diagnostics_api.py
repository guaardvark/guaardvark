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
logger = logging.getLogger(__name__)
METRICS_LOG_LEVEL = os.getenv("GUAARDVARK_METRICS_LOG_LEVEL", "INFO").upper()
metrics_logger = logging.getLogger("metrics")
metrics_logger.setLevel(getattr(logging, METRICS_LOG_LEVEL, logging.INFO))
metrics_logger.propagate = False
if not metrics_logger.handlers:
    metrics_logger.addHandler(logging.NullHandler())

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")


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
                status["db_path"] = _re.sub(
                    r"://([^:]+):([^@]+)@", r"://\1:***@", db_uri
                )
                try:
                    from sqlalchemy import text

                    with db.engine.connect() as conn:
                        result = conn.execute(
                            text("SELECT pg_database_size(current_database())")
                        )
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


@diagnostics_bp.route("/test-llm", methods=["GET"])
def test_llm_endpoint():
    logger.info("API: Received GET /api/meta/test-llm request")

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
    start = time.monotonic()
    from backend.utils.llm_service import run_llm_chat_prompt

    try:
        resp = run_llm_chat_prompt("ping", llm_instance=llm)
        duration = time.monotonic() - start
        model_name = getattr(llm, "model", "unknown")
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
        "gpu_percent": None,
        "gpu_temp": None,
        "gpu_mem": None,
        "gpu_tools_available": None,
        "gpu_check_error": None,
    }
    try:
        if psutil:
            metrics["cpu_percent"] = psutil.cpu_percent(interval=None)
            metrics["cpu_mem"] = psutil.virtual_memory().percent
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
    metrics["gpu_tools_available"] = bool(nvidia_cmd or nvitop_cmd)
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
                    used = float(parts[2])
                    total = float(parts[3])
                    if total > 0:
                        metrics["gpu_mem"] = round((used / total) * 100, 2)
        except Exception as e_gpu:
            gpu_error = f"nvidia-smi error: {e_gpu}"
            logger.debug(f"GPU metrics unavailable via nvidia-smi: {e_gpu}")
    elif nvitop_cmd:
        try:
            subprocess.check_output([nvitop_cmd, "--version"], text=True)
        except Exception as e_gpu:
            gpu_error = f"nvitop error: {e_gpu}"
            logger.debug(f"GPU check via nvitop failed: {e_gpu}")
    else:
        gpu_error = "nvidia-smi and nvitop not found"

    if gpu_error:
        metrics["gpu_check_error"] = gpu_error

    return jsonify(metrics), 200


def _gather_diagnostics() -> dict:
    global rule_utils, db, Document, Setting, Rule, Project

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
        "version": current_app.config.get("APP_VERSION", "N/A"),
    }

    results["rule_details"] = []
    results["rule_warnings"] = []
    results["qa_hello_response"] = None

    try:

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
                results["active_model_health"] = (
                    f"Import Error: requests module not available - {e}"
                )
            except Exception as e:
                results["active_model_health"] = f"Error: {e}"
        else:
            results["active_model_status"] = "Not configured"

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
                        logger.debug(
                            f"SelfTest: QA prompt loaded from database rule ID {rule_id}"
                        )
                    else:
                        logger.debug(
                            "SelfTest: QA prompt loaded using fallback template"
                        )
                else:
                    logger.warning("SelfTest: QA prompt missing required placeholders")
            except Exception as e:
                logger.error("SelfTest: QA prompt load error: %s", e, exc_info=True)

        try:
            from backend.utils.llm_service import run_llm_chat_prompt

            results["qa_hello_response"] = run_llm_chat_prompt("hello")
        except Exception as e:
            results["qa_hello_response"] = f"[Error running LLM: {e}]"

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


@diagnostics_bp.route("/selftest", methods=["POST"])
def run_selftest():
    logger.info("API: Received POST /api/meta/selftest request")
    if not local_imports_ok or not llama_index_available:
        return jsonify({"error": "Core components unavailable for self-test."}), 503

    data = request.get_json() or {}
    test_mode = data.get("mode", "basic")
    test_category = data.get("category", None)
    include_legacy = data.get("include_legacy", True)

    results = {}

    if include_legacy:
        legacy_results = _gather_diagnostics()

        try:
            pip_check = subprocess.run(
                [sys.executable, "-m", "pip", "check"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            legacy_results["pip_check"] = (
                pip_check.stdout.strip()
                if pip_check.returncode == 0
                else f"Error (code {pip_check.returncode}): {pip_check.stderr.strip()}"
            )
        except subprocess.TimeoutExpired:
            legacy_results["pip_check"] = "Timeout after 30 seconds"
        except Exception as e:
            legacy_results["pip_check"] = f"Error running pip check: {e}"

        try:
            outdated = subprocess.run(
                [sys.executable, "-m", "pip", "list", "--outdated"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            legacy_results["pip_outdated"] = (
                outdated.stdout.strip()
                if outdated.returncode == 0
                else f"Error (code {outdated.returncode})"
            )
        except subprocess.TimeoutExpired:
            legacy_results["pip_outdated"] = "Timeout after 30 seconds"
        except Exception as e:
            legacy_results["pip_outdated"] = f"Error listing outdated packages: {e}"

        results["legacy_diagnostics"] = legacy_results

    if test_mode in ["comprehensive", "quick"] or test_category:
        comprehensive_results = _run_comprehensive_selftest(test_mode, test_category)
        results.update(comprehensive_results)

    advanced_debug_enabled = False
    try:
        if db and Setting:
            setting = db.session.get(Setting, "advanced_debug")
            advanced_debug_enabled = setting.value == "true" if setting else False
    except Exception as e:
        logger.error(f"Failed to read advanced debug setting: {e}")
        advanced_debug_enabled = os.getenv("ADVANCED_DEBUG", "false").lower() == "true"

    if advanced_debug_enabled and include_legacy:
        logger.debug("pip check output:\n%s", pip_check.stdout.strip())
        logger.debug("pip list outdated:\n%s", outdated.stdout.strip())

    overall_status = _determine_overall_status(results)
    results["overall_status"] = overall_status
    results["test_mode"] = test_mode
    results["timestamp"] = datetime.now().isoformat()

    logger.info(
        "Self-test results summary: %s",
        {
            "mode": test_mode,
            "status": overall_status,
            "categories": (
                list(results.get("categories", {}).keys())
                if "categories" in results
                else []
            ),
        },
    )

    return jsonify({"message": "Self-test complete.", "results": results}), 200


def _run_comprehensive_selftest(
    test_mode: str, test_category: Optional[str] = None
) -> dict:
    try:
        script_path = os.path.abspath(
            os.path.join(
                current_app.root_path, "..", "scripts", "consolidated_selftest.py"
            )
        )
        output_path = os.path.abspath(
            os.path.join(
                current_app.root_path, "..", "logs", "api_selftest_results.json"
            )
        )

        if not os.path.exists(script_path):
            logger.error(f"Consolidated selftest script not found at {script_path}")
            return {
                "comprehensive_test_available": False,
                "error": f"Test script not found: {script_path}",
                "execution_time": 0.0,
            }

        cmd = [sys.executable, script_path]
        if test_category:
            cmd.extend(["--category", test_category])
        elif test_mode == "quick":
            cmd.append("--quick")
        else:
            cmd.append("--all")

        cmd.extend(["--output", output_path, "--quiet"])

        start_time = time.time()
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=os.path.join(current_app.root_path, ".."),
        )
        duration = time.time() - start_time

        results = {
            "comprehensive_test_available": True,
            "execution_time": duration,
            "exit_code": proc.returncode,
        }

        try:
            if not os.path.exists(output_path):
                logger.warning(f"Test results file not created: {output_path}")
                results["categories"] = {}
                results["error"] = f"Test results file not created at {output_path}"
            else:
                with open(output_path, "r") as f:
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

        if proc.stdout:
            results["stdout_summary"] = proc.stdout[-500:]
        if proc.stderr:
            results["stderr"] = proc.stderr[-500:]

        return results

    except subprocess.TimeoutExpired:
        logger.error("Comprehensive selftest timed out after 5 minutes")
        return {
            "comprehensive_test_available": False,
            "error": "Test execution timeout (5 minutes)",
            "execution_time": 300.0,
        }
    except Exception as e:
        logger.error(f"Error running comprehensive selftest: {e}", exc_info=True)
        return {
            "comprehensive_test_available": False,
            "error": f"Test execution error: {str(e)}",
            "execution_time": 0.0,
        }


def _determine_overall_status(results: dict) -> str:
    legacy = results.get("legacy_diagnostics", {})
    if legacy.get("llm_basic_response") is False:
        return "CRITICAL"
    if legacy.get("database_health") != "Connected":
        return "WARNING"

    categories = results.get("categories", {})
    if categories:
        category_statuses = [cat.get("status", "ERROR") for cat in categories.values()]

        if any(status == "FAIL" for status in category_statuses):
            return "FAIL"
        elif any(status == "ERROR" for status in category_statuses):
            return "ERROR"
        elif any(status == "PARTIAL" for status in category_statuses):
            return "WARNING"
        elif all(status == "PASS" for status in category_statuses):
            return "PASS"

    if legacy.get("active_model_status") == "Responsive":
        return "PASS"
    else:
        return "WARNING"


@diagnostics_bp.route("/diagnostics/export", methods=["GET"])
def export_diagnostics():
    logger.info("API: Received GET /api/meta/diagnostics/export request")
    if not local_imports_ok or not llama_index_available:
        return jsonify({"error": "Core components unavailable for export."}), 503

    include_system = request.args.get("include_system") in ("1", "true", "True")

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
                    masked_url = _re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", db_url)
                    dest_dir = os.path.join(tmpdir, "db")
                    os.makedirs(dest_dir, exist_ok=True)
                    info_path = os.path.join(dest_dir, "database_info.json")
                    db_info = {
                        "database_url": masked_url,
                        "engine_driver": db.engine.driver,
                    }
                    try:
                        from sqlalchemy import text

                        with db.engine.connect() as conn:
                            size_result = conn.execute(
                                text("SELECT pg_database_size(current_database())")
                            )
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
    logger.info("API: Received POST /api/meta/run-tests request")
    try:
        script_path = os.path.abspath(
            os.path.join(current_app.root_path, "..", "run_tests.py")
        )

        if not os.path.exists(script_path):
            logger.error(f"Test script not found: {script_path}")
            return jsonify({"error": f"Test script not found at {script_path}"}), 404

        proc = subprocess.run(
            [sys.executable, script_path], capture_output=True, text=True, timeout=300
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        try:
            parsed = json.loads(stdout) if stdout else {}
        except json.JSONDecodeError:
            parsed = {"stdout": stdout}
        parsed.setdefault("stderr", stderr)
        parsed.setdefault("returncode", proc.returncode)
        return jsonify({"results": parsed}), 200
    except subprocess.TimeoutExpired:
        logger.error("Test suite timed out after 5 minutes")
        return jsonify({"error": "Test execution timeout (5 minutes)"}), 504
    except Exception as e:
        logger.error(f"Error running tests: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@diagnostics_bp.route("/diagnostics/restore", methods=["POST"])
def restore_diagnostics():
    logger.info("API: Received POST /api/meta/diagnostics/restore request")
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    uploaded = request.files["file"]

    if not uploaded.filename or not uploaded.filename.endswith(".zip"):
        return jsonify({"error": "Only ZIP files are allowed"}), 400

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "upload.zip")
        uploaded.save(zip_path)

        try:
            with zipfile.ZipFile(zip_path, "r") as zipf:
                for info in zipf.infolist():
                    if (
                        ".." in info.filename
                        or info.filename.startswith("/")
                        or "\\" in info.filename
                    ):
                        logger.warning(f"Dangerous path in zip file: {info.filename}")
                        return (
                            jsonify({"error": "ZIP file contains invalid paths"}),
                            400,
                        )

                    if info.file_size > 100 * 1024 * 1024:
                        return (
                            jsonify(
                                {"error": "ZIP file contains files that are too large"}
                            ),
                            400,
                        )

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
                if ".." in rel or rel.startswith("/") or "\\" in rel:
                    logger.warning(f"Dangerous database file path: {rel}")
                    continue
                src = os.path.join(tmpdir, rel)
                if os.path.exists(src) and src.endswith(".json"):
                    logger.info(
                        f"Database info file found in export: {rel} (restore not supported for PostgreSQL)"
                    )

            for rel in meta.get("embedding_dirs", []):
                if ".." in rel or rel.startswith("/") or "\\" in rel:
                    logger.warning(f"Dangerous embedding directory path: {rel}")
                    continue

                src = os.path.join(tmpdir, rel)
                dest_root = current_app.config.get("STORAGE_DIR")
                if dest_root and os.path.isdir(dest_root) and os.path.exists(src):
                    safe_dirname = os.path.basename(rel)
                    if safe_dirname and not safe_dirname.startswith("."):
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
                                logger.error(
                                    f"Failed to restore embedding directory: {e}"
                                )

    return jsonify({"message": "Restore completed."}), 200


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
            if event.process_type.value in [
                "llm_processing",
                "file_generation",
                "csv_processing",
            ]:
                gpu_bound_processes.append(
                    {
                        "process_id": process_id,
                        "type": event.process_type.value,
                        "progress": event.progress,
                        "message": event.message,
                        "status": event.status.value,
                        "gpu_metrics": event.additional_data.get("gpu_metrics"),
                        "gpu_processing_active": event.additional_data.get(
                            "gpu_processing_active"
                        ),
                    }
                )

        return (
            jsonify(
                {
                    "gpu_metrics": gpu_metrics,
                    "gpu_processing_active": gpu_processing_active,
                    "gpu_memory_usage": gpu_memory_usage,
                    "gpu_bound_processes": gpu_bound_processes,
                    "active_process_count": len(active_processes),
                    "gpu_bound_process_count": len(gpu_bound_processes),
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"GPU progress metrics failed: {e}")
        return jsonify({"error": str(e)}), 500
