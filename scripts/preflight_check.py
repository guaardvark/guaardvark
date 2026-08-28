#!/usr/bin/env python3
"""
Preflight check script for Guaardvark.
Validates that the application can start cleanly after sync/update.

Usage:
    python3 scripts/preflight_check.py          # Run all checks
    python3 scripts/preflight_check.py --quick   # Import checks only
"""

import sys
import os
import importlib
import shutil
import subprocess

# Resolve project root
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")

# Ensure backend is on path
sys.path.insert(0, PROJECT_ROOT)

PASS = "\033[32m✔\033[0m"
FAIL = "\033[31m✖\033[0m"
WARN = "\033[33m⚠\033[0m"

errors = []
warnings = []


def check(label, condition, error_msg=None):
    if condition:
        print(f"  {PASS} {label}")
        return True
    else:
        msg = error_msg or f"{label} failed"
        print(f"  {FAIL} {label}: {msg}")
        errors.append(msg)
        return False


def warn(label, msg):
    print(f"  {WARN} {label}: {msg}")
    warnings.append(msg)


def check_critical_imports():
    """Verify all critical backend modules can be imported."""
    print("\n\033[1m[1/4] Critical imports\033[0m")

    critical_modules = [
        "backend.config",
        "backend.models",
        "backend.app",
        "backend.celery_app",
    ]

    # Key symbols that must be importable from config
    config_symbols = [
        "GUAARDVARK_ROOT",
        "GUAARDVARK_MODE",
        "DATABASE_URL",
        "STORAGE_DIR",
        "UPLOAD_DIR",
        "OUTPUT_DIR",
    ]

    all_ok = True
    for mod_name in critical_modules:
        try:
            mod = importlib.import_module(mod_name)
            print(f"  {PASS} import {mod_name}")
        except Exception as e:
            print(f"  {FAIL} import {mod_name}: {e}")
            errors.append(f"Cannot import {mod_name}: {e}")
            all_ok = False

    # Verify config exports
    try:
        from backend import config
        for sym in config_symbols:
            if hasattr(config, sym):
                print(f"  {PASS} backend.config.{sym}")
            else:
                print(f"  {FAIL} backend.config.{sym} missing")
                errors.append(f"backend.config.{sym} is not defined")
                all_ok = False
    except ImportError:
        pass  # Already reported above

    return all_ok


def check_api_modules():
    """Verify all API blueprint modules can be imported."""
    print("\n\033[1m[2/4] API module imports\033[0m")

    api_dir = os.path.join(BACKEND_DIR, "api")
    if not os.path.isdir(api_dir):
        warn("API directory", f"{api_dir} not found")
        return True

    all_ok = True
    api_files = sorted(
        f for f in os.listdir(api_dir)
        if f.endswith("_api.py") and not f.startswith("_")
    )

    for filename in api_files:
        mod_name = f"backend.api.{filename[:-3]}"
        try:
            importlib.import_module(mod_name)
            print(f"  {PASS} {mod_name}")
        except Exception as e:
            short_err = str(e).split("\n")[0][:80]
            print(f"  {FAIL} {mod_name}: {short_err}")
            errors.append(f"Cannot import {mod_name}: {short_err}")
            all_ok = False

    return all_ok


def check_service_modules():
    """Verify all service modules can be imported."""
    print("\n\033[1m[3/4] Service module imports\033[0m")

    services_dir = os.path.join(BACKEND_DIR, "services")
    if not os.path.isdir(services_dir):
        warn("Services directory", f"{services_dir} not found")
        return True

    all_ok = True
    service_files = sorted(
        f for f in os.listdir(services_dir)
        if f.endswith(".py") and not f.startswith("_")
    )

    for filename in service_files:
        mod_name = f"backend.services.{filename[:-3]}"
        try:
            importlib.import_module(mod_name)
            print(f"  {PASS} {mod_name}")
        except Exception as e:
            short_err = str(e).split("\n")[0][:80]
            # Some services need GPU/optional deps - downgrade to warning
            if "No module named" in str(e) and any(
                dep in str(e) for dep in ["cv2", "imageio", "torch", "diffusers", "piper"]
            ):
                warn(mod_name, f"Optional dependency missing: {short_err}")
            else:
                print(f"  {FAIL} {mod_name}: {short_err}")
                errors.append(f"Cannot import {mod_name}: {short_err}")
                all_ok = False

    return all_ok


def check_reconciler_sentinel():
    """Fail RED if the dependency reconciler failed and was never repaired.

    scripts/dep_reconciler.py writes logs/.dep_reconcile_failed on failure and
    removes it on the next successful backend_venv run. While it exists, the
    venv is known-incomplete — a green "All checks passed" here would be a lie
    (this exact lie shipped on the 24.04 client box install, 2026-08).
    """
    print("\n\033[1m[R] Dependency reconciler state\033[0m")
    sentinel = os.path.join(PROJECT_ROOT, "logs", ".dep_reconcile_failed")
    if not os.path.isfile(sentinel):
        print(f"  {PASS} No unresolved reconciler failure")
        return True
    try:
        with open(sentinel) as f:
            detail = f.read().strip()
    except OSError:
        detail = "(sentinel unreadable)"
    print(f"  {FAIL} Last dependency reconcile FAILED and has not succeeded since:")
    for line in detail.splitlines():
        print(f"      {line}")
    # Name the reconciler(s) that failed and where their log is; "run the
    # repair script" alone loops when the failing step is one the repair
    # does not cover (seen on macOS, #41).
    failed = [ln.strip() for ln in detail.splitlines() if ln.strip().startswith("- ")]
    log_line = next((ln.strip() for ln in detail.splitlines() if ln.strip().startswith("Full log:")), "")
    errors.append(
        "Dependency reconciler failed: "
        + ("; ".join(failed) if failed else "(see logs/.dep_reconcile_failed)")
        + (f". {log_line}" if log_line else "")
        + ". Repair: ./scripts/dep_reconciler.py --force  (or ./scripts/heal_backend_venv.sh); "
        "if the same step fails again, paste that log in an issue"
    )
    return False


def check_gpu_stack():
    """On a working-GPU box, torch MUST import — never downgrade it to a warning.

    check_service_modules() treats a missing torch as an optional dep (right for
    CPU/ARM boxes, wrong on a GPU box: image/video generation silently dies).
    Gate on nvidia-smi actually running, not merely existing, so boxes with a
    stale driver package don't get blocked.
    """
    print("\n\033[1m[G] GPU stack\033[0m")
    smi = shutil.which("nvidia-smi")
    if smi is None:
        print(f"  {PASS} No nvidia-smi — CPU/ARM box, torch not required")
        return True
    try:
        result = subprocess.run([smi], capture_output=True, timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        result = None
    if result is None or result.returncode != 0:
        warn("nvidia-smi", "present but not functional (no driver loaded?) — skipping torch requirement")
        return True
    try:
        torch = importlib.import_module("torch")
        print(f"  {PASS} import torch ({getattr(torch, '__version__', '?')})")
        if not torch.cuda.is_available():
            warn("torch.cuda", "torch imports but CUDA is unavailable (CPU wheel installed? driver mismatch?)")
    except Exception as e:
        short_err = str(e).split("\n")[0][:120]
        print(f"  {FAIL} import torch: {short_err}")
        errors.append(
            f"NVIDIA GPU detected but torch cannot import ({short_err}). "
            "Image/video generation will be dead. Fix: bash scripts/install_pytorch.sh "
            "(or ./scripts/heal_backend_venv.sh)"
        )
        return False
    return True


def _load_dotenv_redis_url():
    """Read REDIS_URL / CELERY_BROKER_URL from project .env (no third-party dotenv)."""
    env_path = os.path.join(PROJECT_ROOT, ".env")
    values = {}
    if not os.path.isfile(env_path):
        return values
    try:
        with open(env_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def check_redis_broker():
    """Fail RED when .env points at a broker that does not PING.

    Historical failure mode: start_redis.sh left REDIS_URL=redis://localhost:6380/0
    after an alt-port sidecar died, while system Redis on :6379 was fine. Preflight
    used to pass and Celery spammed Connection refused forever.
    """
    print("\n\033[1m[B] Redis broker\033[0m")
    env = _load_dotenv_redis_url()
    url = env.get("CELERY_BROKER_URL") or env.get("REDIS_URL") or os.environ.get(
        "CELERY_BROKER_URL"
    ) or os.environ.get("REDIS_URL") or "redis://localhost:6379/0"

    # Parse redis://[:password@]host:port/db
    port = "6379"
    password = None
    try:
        rest = url.split("://", 1)[-1]
        if "@" in rest:
            creds, hostpart = rest.rsplit("@", 1)
            if creds.startswith(":"):
                password = creds[1:]
            elif ":" in creds:
                password = creds.split(":", 1)[1]
            else:
                password = creds
        else:
            hostpart = rest
        hostport = hostpart.split("/")[0]
        if ":" in hostport:
            port = hostport.rsplit(":", 1)[-1]
    except Exception:
        pass

    redis_cli = shutil.which("redis-cli")
    if redis_cli is None:
        warn("redis-cli", "not installed — cannot verify broker (install redis-tools)")
        return True

    cmd = [redis_cli, "-p", str(port), "ping"]
    if password:
        cmd = [redis_cli, "-p", str(port), "-a", password, "--no-auth-warning", "ping"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"  {FAIL} Redis broker PING failed ({url}): {e}")
        errors.append(
            f"Redis broker not reachable at {url}. "
            "Fix: ./start_redis.sh  (rewrites REDIS_URL to a live port) then re-run ./start.sh"
        )
        return False

    out = (result.stdout or "").strip()
    if result.returncode == 0 and out.upper() == "PONG":
        print(f"  {PASS} Redis broker PING ok ({url.split('@')[-1] if '@' in url else url})")
        return True

    err = (result.stderr or result.stdout or "connection failed").strip()[:120]
    print(f"  {FAIL} Redis broker PING failed ({url}): {err}")
    errors.append(
        f"Redis broker not reachable at {url} ({err}). "
        "Fix: ./start_redis.sh then ensure REDIS_URL/CELERY_BROKER_URL in .env match a live port"
    )
    return False


def check_frontend():
    """Check frontend build state."""
    print("\n\033[1m[4/4] Frontend\033[0m")

    frontend_dir = os.path.join(PROJECT_ROOT, "frontend")
    node_modules = os.path.join(frontend_dir, "node_modules")
    dist_dir = os.path.join(frontend_dir, "dist")
    package_json = os.path.join(frontend_dir, "package.json")

    check("package.json exists", os.path.isfile(package_json))

    if os.path.isdir(node_modules):
        print(f"  {PASS} node_modules installed")
    else:
        warn("node_modules", "Missing - run 'npm install' in frontend/")

    if os.path.isdir(dist_dir):
        print(f"  {PASS} dist/ build exists")
    else:
        warn("dist/", "Missing - will be built on startup")

    return True


def main():
    quick = "--quick" in sys.argv

    print("\033[1m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m")
    print("\033[1m  Guaardvark Preflight Check\033[0m")
    print("\033[1m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m")

    # Always clear pycache first
    print("\n\033[1m[0/4] Clearing __pycache__\033[0m")
    result = subprocess.run(
        ["find", BACKEND_DIR, "-path", "*/venv", "-prune", "-o",
         "-type", "d", "-name", "__pycache__", "-exec", "rm", "-rf", "{}", "+"],
        capture_output=True, text=True
    )
    print(f"  {PASS} Cleared stale bytecode cache")

    check_critical_imports()
    # Run in --quick too: start.sh's boot preflight is --quick, and these two
    # are exactly the checks that catch a half-installed venv (24.04 lesson).
    check_reconciler_sentinel()
    check_gpu_stack()
    # Broker must answer PING before Celery; stale REDIS_URL=:6380 was a silent outage.
    check_redis_broker()

    if not quick:
        check_api_modules()
        check_service_modules()
        check_frontend()

    # Summary
    print("\n\033[1m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m")
    if errors:
        print(f"\033[31m  {len(errors)} error(s) found:\033[0m")
        for e in errors:
            print(f"    - {e}")
        print("\033[1m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m")
        return 1
    elif warnings:
        print(f"\033[33m  {len(warnings)} warning(s), 0 errors — OK to start\033[0m")
        print("\033[1m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m")
        return 0
    else:
        print(f"\033[32m  All checks passed\033[0m")
        print("\033[1m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m")
        return 0


if __name__ == "__main__":
    sys.exit(main())
