import os
import sys
import subprocess
import json


def check_venv(path):
    """Checks if the venv is healthy and matches requirements.txt."""
    backend_dir = os.path.join(path, "backend")
    venv_dir = os.path.join(backend_dir, "venv")
    # Use requirements-base.txt (minimal deps) if available, since PyTorch
    # and optional packages are handled separately by the smart installer
    req_base = os.path.join(backend_dir, "requirements-base.txt")
    req_file = (
        req_base
        if os.path.exists(req_base)
        else os.path.join(backend_dir, "requirements.txt")
    )

    if not os.path.exists(venv_dir):
        return False, "Virtual environment missing."

    if not os.path.exists(req_file):
        return True, "No requirements.txt found (skipping dependency check)."

    # Use the venv's python to get installed packages
    python_exe = os.path.join(venv_dir, "bin", "python")
    if not os.path.exists(python_exe):
        return False, "Venv python executable missing."

    try:
        # Get installed packages using the venv's python
        installed_raw = subprocess.check_output(
            [python_exe, "-m", "pip", "freeze"], stderr=subprocess.STDOUT
        ).decode()

        # Robustly parse installed packages using regex
        import re

        installed = set()
        for line in installed_raw.splitlines():
            match = re.match(r"^([a-zA-Z0-9_\-\.]+)", line.strip())
            if match:
                # Normalize underscores to hyphens for robust comparison
                installed.add(match.group(1).replace("_", "-").lower())

        missing = []
        with open(req_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Robustly extract the package name from requirements.txt
                match = re.match(r"^([a-zA-Z0-9_\-\.]+)", line)
                if match:
                    # Normalize underscores to hyphens
                    pkg = match.group(1).replace("_", "-").lower()
                    if pkg and pkg not in installed:
                        missing.append(pkg)

        if missing:
            return (
                False,
                f"Missing dependencies: {', '.join(missing[:5])}{'...' if len(missing) > 5 else ''}",
            )

    except Exception as e:
        return False, f"Error checking dependencies: {str(e)}"

    return True, "Dependencies OK."


def check_core_files(path):
    """Checks for existence of core files."""
    required = ["start.sh", "backend/app.py", "frontend/package.json"]
    missing = [f for f in required if not os.path.exists(os.path.join(path, f))]
    if missing:
        return False, f"Missing core files: {', '.join(missing)}"
    return True, "Core files present."


def check_redis():
    """Checks if local redis is reachable."""
    try:
        import redis

        r = redis.Redis(host="localhost", port=6379, db=0, socket_timeout=1)
        r.ping()
        return True, "Redis OK."
    except Exception:
        return False, "Redis unreachable."


def run_all_checks(path):
    results = {}

    path = os.path.abspath(path)
    if not os.path.exists(path):
        return {"error": "Path does not exist"}

    results["core_files"] = check_core_files(path)
    results["venv"] = check_venv(path)
    # results["redis"] = check_redis() # Optional, might not be needed for every check

    overall_healthy = all(r[0] for r in results.values())

    return {"healthy": overall_healthy, "details": results, "path": path}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No path provided"}))
        sys.exit(1)

    target_path = sys.argv[1]
    print(json.dumps(run_all_checks(target_path)))
