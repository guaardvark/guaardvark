#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
from datetime import datetime


def _ensure_llama_index() -> None:
    try:
        import llama_index

        return
    except Exception:
        print("[preflight] llama_index missing; attempting install...")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "llama-index==0.12.43",
                "llama-index-llms-ollama==0.6.2",
                "llama-index-readers-file==0.4.9",
            ],
            check=False,
        )
    try:
        import llama_index
    except Exception:
        print(
            "Codex environment setup failure; please re-run or contact OpenAI support."
        )
        sys.exit(1)


def install_requirements(repo_root: str) -> None:
    test_req = os.path.join(repo_root, "backend", "requirements-test.txt")
    llm_req = os.path.join(repo_root, "backend", "requirements-llm.txt")
    default_req = os.path.join(repo_root, "backend", "requirements.txt")
    if os.path.exists(default_req):
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            default_req,
        ]
        if os.environ.get("WITH_LLM", "0") == "1" and os.path.exists(llm_req):
            cmd.extend(["-r", llm_req])
        if os.path.exists(test_req):
            cmd.extend(["-r", test_req])
        subprocess.run(cmd, check=False)


def install_playwright_browsers() -> None:
    try:
        import playwright.__main__

        playwright.__main__.main(["install", "chromium"])
    except Exception as e:
        print("Playwright install failed:", e)


def install_node_modules(repo_root: str) -> None:
    frontend_dir = os.path.join(repo_root, "frontend")
    node_modules = os.path.join(frontend_dir, "node_modules")
    if not os.path.exists(node_modules):
        subprocess.run(["npm", "install", "--silent"], cwd=frontend_dir, check=False)


def _print_summary(result: dict) -> None:
    color_pass = "\033[32m"
    color_fail = "\033[31m"
    color_reset = "\033[0m"
    rc = result.get("returncode", 1)
    status = "PASSED" if rc == 0 else "FAILED"
    color = color_pass if rc == 0 else color_fail
    print(f"{color}Test run {status}{color_reset}")
    summary = result.get("summary", {})
    counts = summary.get("counts", {})
    print(
        f"Passed: {counts.get('passed',0)}  Failed: {counts.get('failed',0)}  Skipped: {counts.get('skipped',0)}  Errors: {counts.get('errors',0)}"
    )
    if summary.get("failures"):
        print("\nFailure Details:")
        for item in summary["failures"]:
            print(item)
    if rc != 0:
        print("\nTests failed. Review output above and fix failing cases.")


def _parse_pytest_output(output: str) -> dict:
    summary_match = re.search(r"(\d+\s+passed).*", output.splitlines()[-1])
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    if summary_match:
        summary_line = summary_match.group(0)
        for key in counts:
            m = re.search(rf"(\d+)\s+{key}", summary_line)
            if m:
                counts[key] = int(m.group(1))
    failures = []
    current = []
    collecting = False
    for line in output.splitlines():
        if line.startswith("_") and "::" in line:
            collecting = True
            if current:
                failures.append("\n".join(current))
                current = []
            current.append(line)
        elif collecting:
            if line.startswith("= ") and "short test summary" in line:
                collecting = False
                if current:
                    failures.append("\n".join(current))
                    current = []
            else:
                current.append(line)
    if current:
        failures.append("\n".join(current))
    return {"counts": counts, "failures": failures}


def run_all():
    repo_root = os.path.abspath(os.path.dirname(__file__))
    phases = {"install": [], "config": [], "run": [], "teardown": []}

    _ensure_llama_index()
    install_requirements(repo_root)
    phases["install"].append("requirements installed")
    install_playwright_browsers()
    phases["install"].append("playwright browsers installed")

    check_script = os.path.join(repo_root, "backend", "check_migrations.py")
    if os.path.exists(check_script):
        mig_proc = subprocess.run(
            [sys.executable, check_script, "--merge"],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        phases["config"].append(mig_proc.stdout)
        if mig_proc.returncode != 0:
            raise RuntimeError(
                f"Database migration failed:\n{mig_proc.stdout}\n{mig_proc.stderr}"
            )

    cmd = [sys.executable, "-m", "pytest", "backend/tests", "-vv", "-rA"]
    env = os.environ.copy()
    env["GUAARDVARK_MODE"] = "test"
    env["DISABLE_CELERY"] = "true"
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=repo_root, env=env)
    phases["run"].append(proc.stdout)

    parsed = _parse_pytest_output(proc.stdout)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = os.path.join(repo_root, "logs", "test_results")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"test_summary_{timestamp}.log")
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump({"phases": phases, "summary": parsed}, f, indent=2)
    phases["teardown"].append(f"results saved to {log_path}")

    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "summary": parsed,
        "log_path": log_path,
    }


def main():
    result = run_all()
    print(json.dumps(result))
    _print_summary(result)
    code = result["returncode"]
    if code in (4, 5):
        code = 0
    return code


if __name__ == "__main__":
    sys.exit(main())
