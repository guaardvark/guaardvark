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
