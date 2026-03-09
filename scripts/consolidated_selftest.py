#!/usr/bin/env python3
"""
Consolidated self-test script for Guaardvark.
Called by diagnostics_api.py for Quick and Full health checks.

Usage:
    python consolidated_selftest.py --quick --output results.json --quiet
    python consolidated_selftest.py --all --output results.json --quiet
    python consolidated_selftest.py --category database --output results.json --quiet
"""

import argparse
import json
import os
import sys
import time
import subprocess
import socket
import importlib

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("GUAARDVARK_ROOT", PROJECT_ROOT)


def run_test(name, fn):
    """Run a single test function and return result dict."""
    start = time.time()
    try:
        result = fn()
        duration = time.time() - start
        if isinstance(result, dict):
            return {
                "name": name,
                "status": result.get("status", "pass"),
                "duration": duration,
                "details": result.get("details", "OK"),
                "error_message": result.get("error_message"),
            }
        return {"name": name, "status": "pass", "duration": duration, "details": str(result) if result else "OK"}
    except Exception as e:
        return {
            "name": name,
            "status": "fail",
            "duration": time.time() - start,
            "details": "Exception",
            "error_message": str(e),
        }


# ---------------------------------------------------------------------------
# Test categories
# ---------------------------------------------------------------------------

def test_database():
    """Database connectivity and integrity tests."""
    tests = []

    def db_connection():
        from sqlalchemy import create_engine, inspect, text
        from backend.config import DATABASE_URL
        engine = create_engine(DATABASE_URL)
        with engine.connect() as conn:
            inspector = inspect(engine)
            tables = inspector.get_table_names()
            return {"status": "pass", "details": f"{len(tables)} tables found"}

    def db_document_count():
        from sqlalchemy import create_engine, text
        from backend.config import DATABASE_URL
        engine = create_engine(DATABASE_URL)
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM documents")).scalar()
            return {"status": "pass", "details": f"{count} documents"}

    def db_migration_current():
        from sqlalchemy import create_engine, text
        from backend.config import DATABASE_URL
        engine = create_engine(DATABASE_URL)
        try:
            with engine.connect() as conn:
                rev = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
                if rev:
                    return {"status": "pass", "details": f"Revision: {rev[0][:12]}"}
                return {"status": "warning", "details": "No alembic version found"}
        except Exception as e:
            return {"status": "warning", "details": str(e)}

    tests.append(run_test("db_connection", db_connection))
    tests.append(run_test("db_document_count", db_document_count))
    tests.append(run_test("db_migration_current", db_migration_current))
    return tests


def test_services():
    """External service connectivity tests."""
    tests = []

    def redis_connection():
        import redis
        r = redis.Redis(host="localhost", port=6379, db=0, socket_timeout=3)
        r.ping()
        return {"status": "pass", "details": "Redis responding"}

    def ollama_connection():
        import urllib.request
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            model_count = len(data.get("models", []))
            return {"status": "pass", "details": f"{model_count} models available"}

    def celery_broker():
        import redis
        r = redis.Redis(host="localhost", port=6379, db=0, socket_timeout=3)
        queues = r.keys("celery*") + r.keys("_kombu*")
        return {"status": "pass", "details": f"{len(queues)} Celery queue keys"}

    tests.append(run_test("redis_connection", redis_connection))
    tests.append(run_test("ollama_connection", ollama_connection))
    tests.append(run_test("celery_broker", celery_broker))
    return tests


def test_filesystem():
    """File system and storage tests."""
    tests = []

    def storage_dirs():
        from backend.config import STORAGE_DIR, UPLOAD_DIR, OUTPUT_DIR, INDEX_ROOT
        dirs = {
            "STORAGE_DIR": str(STORAGE_DIR),
            "UPLOAD_DIR": str(UPLOAD_DIR),
            "OUTPUT_DIR": str(OUTPUT_DIR),
            "INDEX_ROOT": str(INDEX_ROOT),
        }
        missing = [k for k, v in dirs.items() if not os.path.isdir(v)]
        if missing:
            return {"status": "fail", "details": f"Missing: {', '.join(missing)}", "error_message": str(missing)}
        return {"status": "pass", "details": f"All {len(dirs)} directories accessible"}

    def index_files():
        from backend.config import INDEX_ROOT
        docstore = os.path.join(str(INDEX_ROOT), "docstore.json")
        if not os.path.exists(docstore):
            return {"status": "warning", "details": "No docstore.json — index may not be built yet"}
        size = os.path.getsize(docstore)
        if size == 0:
            return {"status": "warning", "details": "docstore.json is empty"}
        return {"status": "pass", "details": f"docstore.json: {size / 1024 / 1024:.1f} MB"}

    def vector_store():
        from backend.config import INDEX_ROOT
        vs_path = os.path.join(str(INDEX_ROOT), "default__vector_store.json")
        if not os.path.exists(vs_path):
            return {"status": "warning", "details": "No vector store file"}
        size = os.path.getsize(vs_path)
        if size == 0:
            return {"status": "warning", "details": "Vector store file is empty (0 bytes) — needs rebuild"}
        return {"status": "pass", "details": f"vector_store: {size / 1024 / 1024:.1f} MB"}

    def disk_space():
        import shutil
        usage = shutil.disk_usage(PROJECT_ROOT)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < 1:
            return {"status": "fail", "details": f"{free_gb:.1f} GB free", "error_message": "Less than 1 GB free"}
        if free_gb < 5:
            return {"status": "warning", "details": f"{free_gb:.1f} GB free"}
        return {"status": "pass", "details": f"{free_gb:.1f} GB free"}

    tests.append(run_test("storage_directories", storage_dirs))
    tests.append(run_test("index_files", index_files))
    tests.append(run_test("vector_store", vector_store))
    tests.append(run_test("disk_space", disk_space))
    return tests


def test_backend():
    """Backend API health tests."""
    tests = []

    def api_health():
        import urllib.request
        port = os.environ.get("FLASK_PORT", "5002")
        req = urllib.request.Request(f"http://localhost:{port}/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return {"status": "pass", "details": f"Health: {data.get('status', 'unknown')}"}

    def api_celery_health():
        import urllib.request
        import urllib.error
        port = os.environ.get("FLASK_PORT", "5002")
        req = urllib.request.Request(f"http://localhost:{port}/api/health/celery", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                status = data.get("status", "unknown")
                return {"status": "pass" if status in ("ok", "up") else "warning", "details": f"Celery: {status}"}
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read())
                status = body.get("status", "unknown")
                error = body.get("error", "")
                suggestion = body.get("suggestion", "")
                detail = f"Celery: {status}"
                if error:
                    detail += f" - {error}"
                return {"status": "warning", "details": detail, "error_message": suggestion or error}
            except Exception:
                return {"status": "fail", "details": f"HTTP {e.code}", "error_message": str(e)}

    def backend_process():
        port = int(os.environ.get("FLASK_PORT", "5002"))
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        result = s.connect_ex(("localhost", port))
        s.close()
        if result == 0:
            return {"status": "pass", "details": f"Port {port} open"}
        return {"status": "fail", "details": f"Port {port} not responding", "error_message": f"connect_ex returned {result}"}

    tests.append(run_test("backend_process", backend_process))
    tests.append(run_test("api_health", api_health))
    tests.append(run_test("api_celery_health", api_celery_health))
    return tests


def test_llm():
    """LLM and embedding model tests."""
    tests = []

    def ollama_models():
        import urllib.request
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            return {"status": "pass", "details": f"{len(models)} models: {', '.join(models[:5])}"}

    def embedding_model():
        from backend.config import get_active_embedding_model
        model = get_active_embedding_model()
        # Test if model responds
        import urllib.request
        payload = json.dumps({"model": model, "input": "test"}).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            embeddings = data.get("embeddings", [])
            if embeddings:
                dim = len(embeddings[0])
                return {"status": "pass", "details": f"{model} — {dim}D embeddings"}
            return {"status": "warning", "details": f"{model} returned no embeddings"}

    def llm_basic_response():
        import urllib.request
        payload = json.dumps({
            "model": "llama3:latest",
            "prompt": "Say OK",
            "stream": False,
            "options": {"num_predict": 10},
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            response_text = data.get("response", "")[:50]
            return {"status": "pass", "details": f"Response: {response_text}"}

    tests.append(run_test("ollama_models", ollama_models))
    tests.append(run_test("embedding_model", embedding_model))
    tests.append(run_test("llm_basic_response", llm_basic_response))
    return tests


def test_imports():
    """Python dependency import tests."""
    tests = []

    critical_modules = [
        "flask", "flask_socketio", "sqlalchemy", "celery", "redis",
        "llama_index.core", "torch", "transformers",
    ]

    for mod in critical_modules:
        def check_import(m=mod):
            importlib.import_module(m)
            return {"status": "pass", "details": "Importable"}
        tests.append(run_test(f"import_{mod.replace('.', '_')}", check_import))

    return tests


# ---------------------------------------------------------------------------
# Category registry
# ---------------------------------------------------------------------------

CATEGORIES = {
    "database": ("Database", test_database),
    "services": ("External Services", test_services),
    "filesystem": ("File System & Storage", test_filesystem),
    "backend": ("Backend API", test_backend),
    "llm": ("LLM & Embeddings", test_llm),
    "imports": ("Python Dependencies", test_imports),
}

QUICK_CATEGORIES = ["database", "services", "backend"]


def run_categories(category_keys):
    """Run specified categories and return results dict."""
    results = {"categories": {}, "overall_status": "pass"}

    for key in category_keys:
        if key not in CATEGORIES:
            continue
        name, fn = CATEGORIES[key]
        cat_start = time.time()
        tests = fn()
        cat_duration = time.time() - cat_start

        statuses = [t["status"] for t in tests]
        if "fail" in statuses:
            cat_status = "fail"
        elif "warning" in statuses:
            cat_status = "warning"
        else:
            cat_status = "pass"

        passed = sum(1 for s in statuses if s == "pass")
        results["categories"][key] = {
            "name": name,
            "status": cat_status,
            "duration": cat_duration,
            "summary": f"{passed}/{len(tests)} tests passed",
            "tests": tests,
        }

        if cat_status == "fail":
            results["overall_status"] = "fail"
        elif cat_status == "warning" and results["overall_status"] == "pass":
            results["overall_status"] = "warning"

    return results


def main():
    parser = argparse.ArgumentParser(description="Guaardvark consolidated self-test")
    parser.add_argument("--quick", action="store_true", help="Run quick subset of tests")
    parser.add_argument("--all", action="store_true", help="Run all test categories")
    parser.add_argument("--category", type=str, help="Run a specific category")
    parser.add_argument("--output", type=str, help="Output JSON file path")
    parser.add_argument("--quiet", action="store_true", help="Suppress stdout output")
    args = parser.parse_args()

    if args.category:
        keys = [args.category]
    elif args.quick:
        keys = QUICK_CATEGORIES
    else:
        keys = list(CATEGORIES.keys())

    results = run_categories(keys)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)

    if not args.quiet:
        print(json.dumps(results, indent=2))

    sys.exit(0 if results["overall_status"] != "fail" else 1)


if __name__ == "__main__":
    main()
