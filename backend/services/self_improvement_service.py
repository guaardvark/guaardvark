"""
SelfImprovementService — autonomous self-improvement loop.

Three modes:
  1. Scheduled: periodic test suite runs with auto-fix
  2. Reactive: error-triggered self-healing
  3. Directed: user/Claude-submitted improvement tasks

Plus: servo optimization (periodic click calibration analysis)

TO RE-ENABLE SELF-IMPROVEMENT:
  1. POST /api/self-improvement/toggle {"enabled": true}
  2. POST /api/self-improvement/lock-codebase {"locked": false}
  3. Ensure Celery beat is running (start.sh handles this)
  4. Servo optimization runs every 3 hours (or POST /api/self-improvement/servo/optimize)
  5. General self-check runs every N hours (GUAARDVARK_SELF_IMPROVEMENT_INTERVAL, default 6)
"""
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.utils.node_identity import local_node_id

logger = logging.getLogger(__name__)

# Test runs this service starts never reach a database. An address nothing
# listens on, not an unset DATABASE_URL: config replaces a missing one with the
# default local address, which is a stock install's own database.
_UNREACHABLE_DATABASE_URL = "postgresql://self-improvement-tests@127.0.0.1:1/none"


# The tests the self-check and the snapshot read as the system's health. Tests
# that drive a live model are marked integration (conftest_sandbox.requires_llm)
# and left out: they pass or fail by the model's draw, and a "fix" for one would
# change working code.
_HEALTH_RUN_TESTS = (
    "backend/tests/test_self_improvement.py",
    "backend/tests/test_code_tools.py",
    "-m", "not integration",
)

# A run still "running" this long after it started has lost its worker: Celery
# kills every task at 40 minutes (task_time_limit in backend/celery_app.py), and
# a reactive heal in the API process is one agent call. Two hours leaves room.
STALE_RUN_AFTER = timedelta(hours=2)
# Written to error_message so the scan views and the jobs list show why.
STALE_RUN_REASON = "Interrupted: the worker running this scan stopped before it finished"


def _test_env(**extra) -> Dict[str, str]:
    env = dict(os.environ)
    env.update(DATABASE_URL=_UNREACHABLE_DATABASE_URL, GUAARDVARK_MODE="test", **extra)
    # A temp dir inside the checkout is dropped so pytest's tmp_path lands in the
    # system one. start.sh points TMPDIR at data/piptmp while it installs
    # dependencies, and the processes it starts keep it; a test sandbox under
    # git-ignored data is refused by the code tools as local data.
    checkout = Path(os.environ.get("GUAARDVARK_ROOT", ".")).resolve()
    for var in ("TMPDIR", "TEMP", "TMP"):
        value = env.get(var)
        if value and Path(value).resolve().is_relative_to(checkout):
            del env[var]
    return env


def _is_codebase_locked() -> bool:
    """User-facing kill switch — same semantics as before (default UNLOCKED).

    Affects ALL agent code edits, including user-initiated ones via chat. The
    self-improvement-specific apply gate is `_is_self_improvement_apply_enabled()`
    below; this one stays as the global emergency brake the user controls.
    """
    lock_file = os.path.join(os.environ.get("GUAARDVARK_ROOT", "."), "data", ".codebase_lock")
    if os.path.exists(lock_file):
        return True
    try:
        from backend.models import db, SystemSetting
        setting = db.session.query(SystemSetting).filter_by(key="codebase_locked").first()
        return setting and setting.value.lower() == "true"
    except Exception:
        return False


def _is_self_improvement_enabled() -> bool:
    """Analysis-path gate. Defaults to ENABLED when there's no explicit setting.

    "Enabled" means the scan-and-propose loop is allowed to run. The actual
    apply step inside that loop is governed by `_is_self_improvement_apply_enabled()`
    (default False) — so analysis surfaces proposals out of the box, but the
    agent doesn't auto-rewrite files until the user explicitly opts in.
    Existing installs that explicitly disabled self-improvement keep their
    setting.
    """
    try:
        from backend.models import db, SystemSetting
        setting = db.session.query(SystemSetting).filter_by(key="self_improvement_enabled").first()
        if setting is None:
            return True  # no explicit choice → analysis on (apply still gated)
        return setting.value.lower() == "true"
    except Exception:
        return False  # DB unreachable → don't run scheduled work


def _is_self_improvement_apply_enabled() -> bool:
    """Whether the self-improvement loop is allowed to actually write files.

    Defaults to False — analysis runs and proposes, but no edits happen until
    the user sets `self_improvement_apply_enabled=true` in system_setting.
    This is checked inside the code-edit tool when `_self_improvement_context`
    is set on the call, so user-initiated chat edits are unaffected.
    """
    try:
        from backend.models import db, SystemSetting
        setting = db.session.query(SystemSetting).filter_by(
            key="self_improvement_apply_enabled"
        ).first()
        if setting is None:
            return False  # default: analysis-only, no apply
        return setting.value.lower() == "true"
    except Exception:
        return False  # DB unreachable → fail closed, no apply


class SelfImprovementService:
    """Manages the autonomous self-improvement loop."""

    _instance = None
    _lock = threading.Lock()

    # Terminal status a cancelled run ends in; reported by every status route.
    CANCELLED = "cancelled"
    # Runs cancelled from inside this process. Class-level so a checkpoint can
    # read it whatever way the singleton was built; the run row is the flag
    # that crosses processes (the API usually cancels a Celery worker's run).
    _cancel_requested_ids: set = set()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._error_tracker = defaultdict(list)  # fingerprint -> [timestamps]
        self._running = False
        self._current_run = None
        self._current_run_id = None  # tagged onto every progress event so the UI can pin
        self._current_trigger = None  # handed to edit_code with the run id
        logger.info("SelfImprovementService initialized")

    def _emit_progress(self, stage: str, detail: str = "", progress: float = 0.0, **extra):
        """Emit a self-improvement progress event via SocketIO.

        Auto-tags the current run_id so frontends can filter events to a
        specific scan — otherwise parallel scans (or stale listeners) would
        see a smear of unrelated progress lines.
        """
        try:
            from backend.socketio_instance import socketio
            payload = {
                "stage": stage,
                "detail": detail,
                "progress": progress,
                "running": self._running,
                "timestamp": time.time(),
                "run_id": self._current_run_id,
                **extra,
            }
            socketio.emit("self_improvement_progress", payload)
        except Exception:
            pass  # Socket may not be available in test mode

    def _gates_open(self) -> bool:
        """The codebase is unlocked and self-improvement is switched on.

        A running scan re-reads these between fixes, so locking the codebase
        or switching self-improvement off stops it at the next one.
        """
        if _is_codebase_locked():
            logger.warning("Self-improvement blocked: codebase is locked")
            return False
        if not _is_self_improvement_enabled():
            logger.info("Self-improvement is disabled")
            return False
        return True

    def _is_safe_to_run(self) -> bool:
        if not self._gates_open():
            return False
        if self._running:
            logger.warning("Self-improvement already running")
            return False
        return True

    def request_cancel(self, run_id: int) -> bool:
        """Ask a running scan to stop at its next checkpoint.

        The run row is the flag: its status flips to ``cancelled`` now, so the
        status routes report it immediately, and the runner re-reads the row
        between steps and finishes without overwriting it. The step in flight
        (the pytest subprocess can take minutes) is not interrupted. Returns
        False for an unknown run or one that has already finished.
        """
        from backend.models import db, SelfImprovementRun
        run = db.session.get(SelfImprovementRun, run_id)
        if run is None or run.status != "running":
            return False
        run.status = self.CANCELLED
        run.error_message = "Cancelled by the user"
        db.session.commit()
        self._cancel_requested_ids.add(run_id)
        # run_id is passed explicitly: the API process has no _current_run_id.
        self._emit_progress("cancelled", "Stopping at the next step", 0.0,
                            status=self.CANCELLED, run_id=run_id)
        return True

    def _cancel_requested(self, run_id: Optional[int]) -> bool:
        """Checkpoint between steps: was this run cancelled, here or elsewhere?

        Reads the status column directly rather than the loaded row, so a
        cancel committed by another process is seen and the row's pending
        changes are untouched.
        """
        if run_id is None:
            return False
        if run_id in self._cancel_requested_ids:
            return True
        try:
            from backend.models import db, SelfImprovementRun
            status = db.session.query(SelfImprovementRun.status).filter_by(id=run_id).scalar()
        except Exception as e:
            logger.debug(f"Cancel checkpoint could not read run {run_id}: {e}")
            return False
        if status == self.CANCELLED:
            self._cancel_requested_ids.add(run_id)
            return True
        return False

    def _finish_cancelled(self, run_record, start_time: float, changes: List[Dict]) -> Dict[str, Any]:
        """Close out a run that hit a cancel checkpoint, keeping the fixes it staged."""
        from backend.models import db
        run_record.status = self.CANCELLED
        run_record.changes_made = json.dumps(changes)
        run_record.duration_seconds = time.time() - start_time
        db.session.commit()
        self._emit_progress("cancelled", f"Stopped after {len(changes)} fix(es) staged", 1.0,
                            status=self.CANCELLED, fixes_staged=len(changes))
        return {"success": False, "cancelled": True, "run_id": run_record.id,
                "fixes_staged": len(changes), "changes": changes}

    @staticmethod
    def _staged_fixes(run_id: Optional[int]) -> list:
        """PendingFix rows this run staged, oldest first."""
        if run_id is None:
            return []
        from backend.models import PendingFix
        return PendingFix.query.filter_by(run_id=run_id).order_by(PendingFix.id).all()

    @staticmethod
    def _staged_changes(fixes: list, failure: Dict[str, str],
                        answer: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """One change record per staged fix for ``failure``.

        The agent's closing answer describes the fix when it gave one; the
        staged row's own description is the fallback.
        """
        answer_text = (answer or {}).get("fix_description") or ""
        return [
            {"file": f.file_path, "test": failure["test_name"], "pending_fix_id": f.id,
             "fix_description": (answer_text or f.fix_description or "")[:500]}
            for f in fixes
        ]

    def dispatch_precheck(self) -> Dict[str, Any]:
        """Public, side-effect-free check of whether a directed dispatch can run.

        Returns {"ok": bool, "reason": str}. Lets callers (e.g. the System-Map
        dispatch endpoint) show the user the REAL reason immediately instead of a
        silent no-op, before queuing async work."""
        if _is_codebase_locked():
            return {"ok": False, "reason": (
                "Codebase is locked (data/.codebase_lock or the codebase_locked "
                "SystemSetting). Unlock via POST /api/self-improvement/lock-codebase "
                "{\"locked\": false} to allow edits.")}
        if not _is_self_improvement_enabled():
            return {"ok": False, "reason": (
                "Self-improvement is disabled — enable via POST "
                "/api/self-improvement/toggle {\"enabled\": true}.")}
        if self._running:
            return {"ok": False, "reason": "A self-improvement run is already in progress."}
        return {"ok": True, "reason": "ready"}

    def _error_fingerprint(self, file: str, line: int, error_type: str) -> str:
        raw = f"{file}:{line}:{error_type}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _parse_test_failures(self, pytest_output: str) -> List[Dict[str, str]]:
        failures = []
        # Pattern with error message: FAILED path::test - error
        pattern = r"FAILED\s+(\S+?)::(\S+)\s*-\s*(.*)"
        for match in re.finditer(pattern, pytest_output):
            file_path, test_name, error = match.groups()
            failures.append({
                "file": file_path.strip(),
                "test_name": test_name.strip(),
                "error": error.strip(),
            })
        # Fallback: FAILED path::test (no dash-separated error)
        if not failures:
            pattern2 = r"FAILED\s+(\S+?)::(\S+)"
            for match in re.finditer(pattern2, pytest_output):
                file_path, test_name = match.groups()
                failures.append({
                    "file": file_path.strip(),
                    "test_name": test_name.strip(),
                    "error": "Test failed (see output for details)",
                })
        return failures

    def _confirm_failures(self, failures: List[Dict[str, str]], root: str):
        """(failures that fail again, failures that passed when re-run).

        Re-runs only the failing tests. If the re-run cannot be read (it
        errors, or fails without naming a test), the first run's failures
        stand."""
        if not failures:
            return failures, []
        node_ids = [f"{f['file']}::{f['test_name']}" for f in failures]
        try:
            rerun = subprocess.run(
                ["python3", "-m", "pytest", *node_ids, "-v", "--tb=short", "--no-header"],
                capture_output=True, text=True, timeout=300, cwd=root, env=_test_env(),
            )
        except Exception as e:
            logger.warning(f"Re-running the failing tests failed ({e}); acting on the first run")
            return failures, []
        again = {(f["file"], f["test_name"]) for f in self._parse_test_failures(rerun.stdout + rerun.stderr)}
        if rerun.returncode != 0 and not again:
            return failures, []
        confirmed = [f for f in failures if (f["file"], f["test_name"]) in again]
        flaky = [f for f in failures if (f["file"], f["test_name"]) not in again]
        if flaky:
            logger.info(f"Self-check: {len(flaky)} failure(s) passed on a re-run and are left alone as flaky")
        return confirmed, flaky

    def snapshot_pytest(self, timeout: int = 120) -> Dict[str, Any]:
        """Analysis-only pytest snapshot. Never dispatches fixes or writes files.

        Used by the overnight research director as a preflight signal. Tests
        red is information, not a license to apply. If a self-check is already
        running, we skip rather than race it.
        """
        if _is_codebase_locked():
            return {"ok": False, "skipped": True, "reason": "codebase_locked", "red": False}
        if not _is_self_improvement_enabled():
            return {"ok": False, "skipped": True, "reason": "self_improvement_disabled", "red": False}
        if self._running:
            return {"ok": False, "skipped": True, "reason": "si_already_running", "red": False}
        root = os.environ.get("GUAARDVARK_ROOT", ".")
        try:
            result = subprocess.run(
                ["python3", "-m", "pytest", *_HEALTH_RUN_TESTS,
                 "-q", "--tb=no", "--no-header"],
                capture_output=True, text=True, timeout=timeout, cwd=root,
                env=_test_env(),
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "skipped": False, "reason": "pytest_timeout",
                    "red": True, "failures": -1, "return_code": None}
        except Exception as e:
            return {"ok": False, "skipped": False, "reason": f"pytest_error:{e.__class__.__name__}",
                    "red": False, "failures": 0, "return_code": None}
        failures = self._parse_test_failures(result.stdout + result.stderr)
        red = bool(failures) or result.returncode != 0
        return {
            "ok": True,
            "skipped": False,
            "red": red,
            "failures": len(failures),
            "return_code": result.returncode,
        }

    def run_self_check(self) -> Dict[str, Any]:
        """Mode 1: Run test suite, identify failures, dispatch agent to fix.

        Each fix the agent stages is tested against its failing test file in a
        scratch copy of the checkout (_verify_fix), and the result is written
        on the pending fix for the person who reviews it.
        """
        if not self._is_safe_to_run():
            return {"success": False, "reason": "Self-improvement cannot run"}

        self._running = True
        self._current_trigger = "scheduled"
        start_time = time.time()
        run_record = None
        run_id = None
        error = None
        self._emit_progress("starting", "Initializing self-check", 0.0)

        try:
            from backend.models import db, SelfImprovementRun
            run_record = SelfImprovementRun(
                trigger="scheduled",
                status="running",
                node_id=local_node_id(),
            )
            db.session.add(run_record)
            db.session.commit()
            run_id = run_record.id
            self._current_run_id = run_id

            # Re-emit now that we have a run_id so late subscribers still catch it
            self._emit_progress("starting", "Initializing self-check", 0.05,
                                trigger=run_record.trigger)
            self._emit_progress("testing", "Running test suite", 0.1)
            root = os.environ.get("GUAARDVARK_ROOT", ".")
            result = subprocess.run(
                ["python3", "-m", "pytest", *_HEALTH_RUN_TESTS, "-v", "--tb=short", "--no-header"],
                capture_output=True, text=True, timeout=300, cwd=root,
                env=_test_env(),
            )

            test_output = result.stdout + result.stderr
            failures = self._parse_test_failures(test_output)
            # Some of these tests drive the model and fail now and then. A test
            # that passes when run again is flaky, not broken, and a "fix" for it
            # would change working code: only failures that repeat are acted on.
            failures, flaky = self._confirm_failures(failures, root)

            run_record.test_results_before = json.dumps({
                "total_failures": len(failures),
                "failures": failures,
                "flaky": flaky,
                "return_code": result.returncode,
            })

            if self._cancel_requested(run_id):
                return self._finish_cancelled(run_record, start_time, [])

            self._emit_progress("analyzed", f"Found {len(failures)} failure(s)", 0.3,
                                failures_found=len(failures), return_code=result.returncode)

            if not failures and result.returncode == 0:
                run_record.status = "success"
                run_record.duration_seconds = time.time() - start_time
                db.session.commit()
                self._emit_progress("complete", "All tests passing", 1.0, status="success")
                return {"success": True, "message": "All tests passing", "failures": 0}

            if not failures and flaky:
                run_record.status = "success"
                run_record.duration_seconds = time.time() - start_time
                db.session.commit()
                message = f"{len(flaky)} failure(s) passed on a re-run; left alone as flaky"
                self._emit_progress("complete", message, 1.0, status="success")
                return {"success": True, "message": message, "failures": 0, "flaky": len(flaky)}

            # Return code nonzero but parser found nothing — record as unparsed failure
            if not failures and result.returncode != 0:
                failures = [{"file": "unknown", "test_name": "unparsed_failure", "error": test_output[-500:]}]

            # A fix counts only when the agent staged a PendingFix for this run;
            # its closing answer alone ("I could not find the problem") is not one.
            changes = []
            verifications = []
            from backend.services import job_arbiter
            for i, failure in enumerate(failures):
                # Job order on: each fix is a step; hold before it while a chat
                # or voice turn runs.
                job_arbiter.yield_point("self_improvement", run_id, label="Self-improvement",
                                        should_stop=lambda: self._cancel_requested(run_id))
                if self._cancel_requested(run_id):
                    return self._finish_cancelled(run_record, start_time, changes)
                # Not _is_safe_to_run: this run is the one running, so it would always stop here.
                if not self._gates_open():
                    break
                progress = 0.3 + (0.6 * (i / max(len(failures), 1)))
                self._emit_progress("fixing", f"Fixing {failure['test_name']} ({i+1}/{len(failures)})",
                                    progress, current_fix=i+1, total_fixes=len(failures))
                before = {f.id for f in self._staged_fixes(run_id)}
                answer = self._attempt_fix(failure)
                new = [f for f in self._staged_fixes(run_id) if f.id not in before]
                staged = self._staged_changes(new, failure, answer)
                if new and not self._cancel_requested(run_id):
                    self._emit_progress("verifying", f"Testing the fix for {failure['test_name']} in a scratch copy",
                                        progress, current_fix=i+1, total_fixes=len(failures))
                    verification = self._verify_fix([failure["file"]], new)
                    self._attach_verification(new, verification)
                    summary = {k: verification.get(k) for k in
                               ("tests", "all_passed", "total_failures", "return_code", "error")}
                    for change in staged:
                        change["verification"] = summary
                    verifications.append({"test": failure["test_name"],
                                          "pending_fix_ids": [f.id for f in new], **verification})
                changes.extend(staged)

            if self._cancel_requested(run_id):
                return self._finish_cancelled(run_record, start_time, changes)

            run_record.changes_made = json.dumps(changes)
            if verifications:
                run_record.test_results_after = json.dumps(verifications)
            fixes_verified = sum(1 for c in changes if (c.get("verification") or {}).get("all_passed"))
            if changes:
                run_record.status = "success"
                message = f"{len(changes)} fix(es) staged for review"
            else:
                run_record.status = "failed"
                message = f"No fix staged for {len(failures)} failure(s)"
                run_record.error_message = message
            run_record.duration_seconds = time.time() - start_time
            db.session.commit()

            if changes:
                self._broadcast_learnings(changes, run_record)

            self._emit_progress("complete", message, 1.0,
                                status=run_record.status, fixes_staged=len(changes),
                                fixes_verified=fixes_verified, failures_found=len(failures))

            return {
                "success": bool(changes),
                "run_id": run_id,
                "message": message,
                "failures_found": len(failures),
                "fixes_staged": len(changes),
                "fixes_verified": fixes_verified,
                "changes": changes,
            }

        except Exception as e:
            error = e
            logger.error(f"Self-check failed: {e}", exc_info=True)
            self._emit_progress("error", str(e)[:200], 0.0, status="failed")
            return {"success": False, "reason": str(e)}
        finally:
            if run_id is not None:
                self._fail_if_still_running(run_id, error, time.time() - start_time)
            self._running = False
            self._current_run_id = None
            self._current_trigger = None

    def _attempt_fix(self, failure: Dict[str, str], message: str = None) -> Optional[Dict[str, Any]]:
        """Dispatch code_assistant agent to fix a test failure (or run `message`)."""
        try:
            from backend.services.agent_executor import AgentExecutor
            from backend.services.agent_config import AgentConfigManager
            from backend.services.agent_tools import get_tool_registry

            config_manager = AgentConfigManager()
            agent_config = config_manager.get_agent("code_assistant")
            if not agent_config:
                logger.error("code_assistant agent not found")
                return None

            registry = get_tool_registry()
            executor = AgentExecutor(
                tool_registry=registry,
                llm=None,  # uses default from Settings
                max_iterations=agent_config.max_iterations,
                agent=agent_config,
            )

            message = message or (
                f"Fix this failing test. "
                f"Test file: {failure['file']}, test: {failure['test_name']}. "
                f"Error: {failure['error']}. "
                f"Read the test first to understand what is expected, "
                f"then read the source code, then fix the bug."
            )

            executor.set_tool_context(
                _self_improvement_context=True,
                _reasoning=message,
                _run_id=self._current_run_id,
                _trigger=getattr(self, "_current_trigger", None),
            )

            result = executor.execute(message)

            if result and result.final_answer:
                return {
                    "file": failure["file"],
                    "test": failure["test_name"],
                    "fix_description": result.final_answer[:500],
                    "iterations": result.iterations,
                }
            return None

        except Exception as e:
            logger.error(f"Agent fix attempt failed for {failure['test_name']}: {e}", exc_info=True)
            return None

    # The same limit as the self-check's own test run.
    VERIFY_TIMEOUT_SECONDS = 300

    def _verify_fix(self, test_files: List[str], fixes: list) -> Dict[str, Any]:
        """Run the failing test file(s) with staged fixes applied, in a scratch copy.

        The copy is a temporary git worktree, in the system temp dir, of the
        checkout as it is now (uncommitted changes to tracked files included);
        the fixes' exact replacements are made there and nowhere else, and the
        copy is removed afterwards. Only the named test files run, with
        GUAARDVARK_MODE=test, GUAARDVARK_ROOT pointing at the copy and
        DATABASE_URL pointed at an address nothing listens on, so the tests cannot reach a database.

        Returns all_passed, total_failures, failures and return_code from that
        run, the test files run, and ``error`` saying why nothing ran (no test
        file, no git checkout, a fix that no longer matches, a timeout).
        """
        result: Dict[str, Any] = {
            "tests": self._runnable_test_files(test_files), "all_passed": False,
            "total_failures": 0, "failures": [], "return_code": None, "error": None,
        }
        if not result["tests"]:
            result["error"] = "no failing test file to run"
            return result

        root = Path(os.environ.get("GUAARDVARK_ROOT", ".")).resolve()
        scratch = Path(tempfile.mkdtemp(prefix="guaardvark-verify-"))
        tree = scratch / "checkout"
        made = False
        try:
            # HEAD when the working tree has no changes or git cannot record them.
            snapshot = self._git(root, "stash", "create")
            base = snapshot.stdout.strip() if snapshot.returncode == 0 else ""
            added = self._git(root, "worktree", "add", "--detach", str(tree), base or "HEAD")
            if added.returncode != 0:
                detail = (added.stderr or added.stdout or "").strip()[-200:]
                result["error"] = f"could not make a scratch copy: {detail}"
                return result
            made = True

            for fix in fixes:
                problem = self._apply_in_copy(fix, root, tree)
                if problem:
                    result["error"] = problem
                    return result

            env = _test_env(GUAARDVARK_ROOT=str(tree))
            run = subprocess.run(
                ["python3", "-m", "pytest", *result["tests"], "-v", "--tb=short", "--no-header"],
                capture_output=True, text=True, timeout=self.VERIFY_TIMEOUT_SECONDS,
                cwd=str(tree), env=env,
            )
            failures = self._parse_test_failures(run.stdout + run.stderr)
            result.update(failures=failures, total_failures=len(failures),
                          return_code=run.returncode,
                          all_passed=run.returncode == 0 and not failures)
        except subprocess.TimeoutExpired as e:
            result["error"] = f"timed out after {e.timeout:g} s"
        except Exception as e:
            logger.error(f"Verification run failed: {e}")
            result["error"] = f"{type(e).__name__}: {e}"
        finally:
            self._remove_scratch_copy(root, tree, scratch, made)
        return result

    @staticmethod
    def _runnable_test_files(test_files: List[str]) -> List[str]:
        """Relative .py paths that stay inside the checkout, each once."""
        runnable: List[str] = []
        for name in test_files or []:
            name = (name or "").strip()
            path = Path(name)
            if (not name.endswith(".py") or path.is_absolute() or ".." in path.parts
                    or name in runnable):
                continue
            runnable.append(name)
        return runnable

    @staticmethod
    def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True, timeout=120)

    @staticmethod
    def _apply_in_copy(fix, root: Path, tree: Path) -> Optional[str]:
        """Make one staged replacement in the scratch copy; the reason when it cannot be made."""
        old, new = fix.original_content, fix.proposed_new_content
        if old is None or new is None:
            return f"pending fix #{fix.id} has no exact replacement to apply"
        path = Path(fix.file_path)
        if not path.is_absolute():
            path = root / path
        try:
            relative = path.resolve().relative_to(root)
        except ValueError:
            return f"pending fix #{fix.id} is outside the checkout"
        target = tree / relative
        if (target.is_symlink() or not target.is_file()
                or not target.resolve().is_relative_to(tree.resolve())):
            return f"{relative.as_posix()} is not a tracked file in the scratch copy"
        text = target.read_text(encoding="utf-8")
        if text.count(old) != 1:
            return f"pending fix #{fix.id} no longer matches {relative.as_posix()}"
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        return None

    def _remove_scratch_copy(self, root: Path, tree: Path, scratch: Path, made: bool) -> None:
        """Remove the scratch worktree and its temp dir; never raises."""
        try:
            if made and self._git(root, "worktree", "remove", "--force", str(tree)).returncode != 0:
                shutil.rmtree(tree, ignore_errors=True)
                self._git(root, "worktree", "prune")
        except Exception as e:
            logger.warning(f"Could not remove the scratch copy {tree}: {e}")
        shutil.rmtree(scratch, ignore_errors=True)

    @staticmethod
    def _verification_note(verification: Dict[str, Any]) -> str:
        """One line on the pending fix saying how its failing test did."""
        from backend.utils.display_paths import display_text
        tests = ", ".join(verification.get("tests") or []) or "the failing test"
        if verification.get("error"):
            return f"Scratch-copy test run: not run ({display_text(verification['error'])})."
        if verification.get("all_passed"):
            return f"Scratch-copy test run: {tests} passed with this fix applied."
        failures = verification.get("failures") or []
        if failures:
            names = ", ".join(f["test_name"] for f in failures[:5])
            if len(failures) > 5:
                names += f" and {len(failures) - 5} more"
            return f"Scratch-copy test run: {tests} fails with this fix applied ({names})."
        return (f"Scratch-copy test run: {tests} did not pass with this fix applied "
                f"(pytest exit code {verification.get('return_code')}).")

    def _attach_verification(self, fixes: list, verification: Dict[str, Any]) -> None:
        """Add the verification line to each fix's description.

        The description is what the Fixes dialog shows every reviewer, and
        approving a fix replaces its review notes.
        """
        from backend.models import db
        note = self._verification_note(verification)
        for fix in fixes:
            fix.fix_description = f"{fix.fix_description}\n\n{note}" if fix.fix_description else note
        db.session.commit()

    def _broadcast_learnings(self, changes: List[Dict], run_record):
        """Create InterconnectorLearning records and broadcast to family.

        Only staged fixes (records carrying a pending_fix_id) are learnings; an
        agent's answer without one is not sent to other machines.
        """
        staged = [c for c in changes if c.get("pending_fix_id")]
        if not staged:
            return
        try:
            from backend.models import db, InterconnectorLearning
            for change in staged:
                learning = InterconnectorLearning(
                    source_node_id=local_node_id(),
                    learning_type="bug_fix",
                    description=change.get("fix_description", ""),
                    code_diff=json.dumps(change),
                    confidence=0.7,
                    model_used=os.environ.get("GUAARDVARK_ACTIVE_MODEL", "unknown"),
                    uncle_reviewed=run_record.uncle_reviewed,
                )
                db.session.add(learning)
            db.session.commit()
        except Exception as e:
            logger.error(f"Failed to broadcast learnings: {e}", exc_info=True)

    def track_error(self, file: str, line: int, error_type: str, traceback_str: str):
        """Mode 2: Track errors for reactive self-healing."""
        from backend.config import SELF_HEALING_ERROR_THRESHOLD, SELF_HEALING_WINDOW_MINUTES

        fp = self._error_fingerprint(file, line, error_type)
        now = datetime.now()
        cutoff = now - timedelta(minutes=SELF_HEALING_WINDOW_MINUTES)

        self._error_tracker[fp] = [t for t in self._error_tracker[fp] if t > cutoff]
        self._error_tracker[fp].append(now)

        if len(self._error_tracker[fp]) >= SELF_HEALING_ERROR_THRESHOLD:
            logger.info(f"Error threshold reached for {file}:{line} ({error_type}), triggering self-healing")
            self._error_tracker[fp] = []
            threading.Thread(
                target=self.heal,
                args=(file, line, error_type, traceback_str),
                daemon=True,
            ).start()

    def heal(self, file: str, line: int, error_type: str, traceback_str: str):
        """Reactive fix for repeated errors."""
        if not self._is_safe_to_run():
            return

        self._running = True
        run_id = None
        error = None
        try:
            from backend.models import db, SelfImprovementRun
            run_record = SelfImprovementRun(
                trigger="reactive",
                status="running",
                node_id=local_node_id(),
            )
            db.session.add(run_record)
            db.session.commit()
            run_id = run_record.id
            # _attempt_fix hands these to edit_code, which stages PendingFix rows under
            # the run; "reactive" marks it unattended, like a scheduled run.
            self._current_run_id = run_id
            self._current_trigger = "reactive"

            failure = {
                "file": file,
                "test_name": f"runtime_error_line_{line}",
                "error": f"{error_type} at {file}:{line}\n{traceback_str[:500]}",
            }
            answer = self._attempt_fix(failure)

            # Same rule as a directed run: success means a fix was staged.
            changes = self._staged_changes(self._staged_fixes(run_record.id), failure, answer)
            if self._cancel_requested(run_record.id):
                run_record.status = self.CANCELLED
            elif changes:
                run_record.status = "success"
            else:
                run_record.status = "failed"
                run_record.error_message = (
                    (answer or {}).get("fix_description") or "agent produced no answer")
            run_record.changes_made = json.dumps(changes)
            db.session.commit()

        except Exception as e:
            error = e
            logger.error(f"Self-healing failed: {e}", exc_info=True)
        finally:
            if run_id is not None:
                self._fail_if_still_running(run_id, error)
            self._running = False
            self._current_run_id = None
            self._current_trigger = None

    def _fail_if_still_running(self, run_id: int, error: Optional[BaseException],
                               duration_seconds: Optional[float] = None) -> None:
        """Close a run row its runner left 'running' as failed, with the error.

        Otherwise /scans, /runs and the scan progress view report the run as
        live forever. A row already closed (success, failed, cancelled) is
        left as it is.
        """
        from backend.models import db, SelfImprovementRun
        try:
            # The error may have left the session mid-transaction.
            db.session.rollback()
            run = db.session.get(SelfImprovementRun, run_id)
            if run is None or run.status != "running":
                return
            run.status = "failed"
            run.error_message = (
                f"{type(error).__name__}: {error}" if error is not None
                else "Stopped before it finished"
            )
            if duration_seconds is not None:
                run.duration_seconds = duration_seconds
            db.session.commit()
        except Exception as e:
            logger.error(f"Could not close self-improvement run {run_id}: {e}")
            try:
                db.session.rollback()
            except Exception:
                pass

    def recover_stale_runs(self, probe_workers: bool = True) -> int:
        """Close runs left ``running`` by a worker that died or was restarted.

        _fail_if_still_running only sees a runner that returns; a killed
        process leaves its row ``running`` for good, and /status, /runs and
        the jobs list report a live scan forever. Same shape as
        ResearchRunService._recover_stale_runs: a run this process owns is
        left alone; when celery inspect reports no self-improvement task
        anywhere, every other running row is dead; when a task is live (it
        cannot say which row it owns) or inspect is unavailable, only rows
        older than STALE_RUN_AFTER are closed. ``probe_workers=False`` skips
        inspect, for the polled status routes. Returns the rows closed.
        """
        from backend.models import db, SelfImprovementRun
        try:
            q = db.session.query(SelfImprovementRun).filter(SelfImprovementRun.status == "running")
            if self._current_run_id is not None:
                q = q.filter(SelfImprovementRun.id != self._current_run_id)
            rows = q.all()
            if not rows:
                return 0
            live = self._celery_has_live_run() if probe_workers else None
            # Rows carry the model's naive local timestamp, so the cutoff does too.
            cutoff = datetime.now() - STALE_RUN_AFTER
            n = 0
            for run in rows:
                if live is not False and not (run.timestamp and run.timestamp < cutoff):
                    continue
                run.status = "failed"
                run.error_message = STALE_RUN_REASON
                n += 1
            if n:
                db.session.commit()
                logger.warning("Closed %d stale self-improvement run(s) as failed", n)
            return n
        except Exception as e:
            logger.error(f"Could not recover stale self-improvement runs: {e}")
            try:
                db.session.rollback()
            except Exception:
                pass
            return 0

    @staticmethod
    def _celery_has_live_run():
        """True if a worker reports a self_improvement task active.

        False if inspect succeeded and none are active. None if inspect
        failed (do not clobber a maybe-live run).
        """
        try:
            from backend.celery_app import celery
            insp = celery.control.inspect(timeout=1.0)
            if insp is None:
                return None
            active = insp.active()
            if active is None:
                return None
            for _worker, tasks in (active or {}).items():
                for t in tasks or []:
                    if (t.get("name") or "").startswith("self_improvement."):
                        return True
            return False
        except Exception:
            return None

    def submit_directed_task(
        self, description: str, target_files: List[str] = None, priority: str = "medium",
        proposal: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Mode 3: User/Claude-submitted improvement task.

        ``proposal`` is an exact old/new text change already derived from the
        task's evidence (see system_mapper.actions.mechanical_proposal). It is
        staged as-is, behind the same human gate, and the agent is not asked to
        rediscover it: on 2026-09-05 two directed runs on a one-line wiring fix
        ended with the local model calling a tool that does not exist."""
        if not self._is_safe_to_run():
            return {"success": False, "reason": "Self-improvement cannot run"}

        self._running = True
        run_id = None
        error = None
        try:
            from backend.models import db, SelfImprovementRun
            run_record = SelfImprovementRun(
                trigger="directed",
                status="running",
                node_id=local_node_id(),
            )
            db.session.add(run_record)
            db.session.commit()
            run_id = run_record.id

            self._current_run_id = run_id
            change = None
            if proposal:
                change = self._stage_proposal(proposal, run_record.id)
            else:
                failure = {
                    "file": ", ".join(target_files) if target_files else "unknown",
                    "test_name": "directed_improvement",
                    "error": description,
                }
                change = self._attempt_fix(failure, message=self._directed_message(
                    description, target_files))

            # A directed run succeeded only if something was actually staged.
            # The agent's closing prose is not a change: on 2026-08-29 two runs
            # burned 15 iterations on execute_python, answered with a directory
            # listing, and were recorded as "success" with nothing in the queue.
            from backend.models import PendingFix
            staged = PendingFix.query.filter_by(run_id=run_record.id).all()
            if self._cancel_requested(run_record.id):
                run_record.status = self.CANCELLED
                run_record.changes_made = "[]"
                db.session.commit()
                return {"success": False, "cancelled": True, "run_id": run_record.id,
                        "change": change, "pending_fix_ids": [f.id for f in staged]}
            if staged:
                run_record.status = "success"
                run_record.changes_made = json.dumps([
                    {"file": f.file_path, "pending_fix_id": f.id,
                     "fix_description": (f.fix_description or "")[:500]}
                    for f in staged
                ])
            else:
                run_record.status = "no_change"
                run_record.changes_made = "[]"
                run_record.error_message = (
                    (change or {}).get("fix_description") or "agent produced no answer")
            db.session.commit()

            return {"success": bool(staged), "change": change,
                    "pending_fix_ids": [f.id for f in staged]}
        except Exception as e:
            error = e
            logger.error(f"Directed improvement failed: {e}", exc_info=True)
            return {"success": False, "reason": str(e)}
        finally:
            if run_id is not None:
                self._fail_if_still_running(run_id, error)
            self._running = False
            self._current_run_id = None

    def _stage_proposal(self, proposal: Dict[str, Any], run_id: int) -> Optional[Dict[str, Any]]:
        """Stage a mechanical proposal through the guarded path (path, lock,
        protected-file and exact-match checks apply). Returns the change record,
        or a description of why staging was refused."""
        from backend.services.guarded_code_service import GuardedCodeError, stage_pending_fix
        try:
            fix_id = stage_pending_fix(
                proposal["path"], proposal["old_text"], proposal["new_text"],
                proposal.get("description") or "mechanical proposal",
                severity=proposal.get("severity") or "medium", run_id=run_id,
            )
        except GuardedCodeError as exc:
            logger.warning("mechanical proposal refused: %s", exc)
            return {"file": proposal.get("path"), "fix_description": f"not staged: {exc}"}
        return {"file": proposal.get("path"), "pending_fix_id": fix_id,
                "fix_description": (proposal.get("description") or "")[:500]}

    @staticmethod
    def _directed_message(description: str, target_files: List[str] = None) -> str:
        """Prompt for a dispatched finding — a change proposal, not a test repair."""
        files = ", ".join(target_files) if target_files else "(find them with search_code)"
        return (
            f"Directed improvement task. This is not a failing test.\n\n{description}\n\n"
            f"Target file(s): {files}.\n"
            "Steps: read the target file with read_code; decide the smallest correct "
            "change; propose it with edit_code (in this context edit_code stages a "
            "proposal for human review — it does not write to disk). "
            "Do not use execute_python. If no change is warranted, say why and stop."
        )


    def optimize_servo(self) -> Dict[str, Any]:
        """Mode 2: Analyze servo knowledge archive and propose reflex updates.

        Reads Tier 2 (archives), discovers patterns, proposes changes
        to Tier 1 (reflexes) in servo_knowledge_store.py.

        The loop:
          1. Read archive stats (success rate, avg error, per-model data)
          2. Check if current reflexes match observed behavior
          3. If a better scale factor is found, propose a code change
          4. Uncle Claude reviews the change
          5. If approved, stage as pending fix
        """
        if not self._is_safe_to_run():
            return {"success": False, "reason": "Self-improvement cannot run"}

        self._running = True
        try:
            from backend.services.servo_knowledge_store import get_servo_archive, get_vision_config

            archive = get_servo_archive()
            stats = archive.get_stats()

            result = {
                "archive_stats": stats,
                "recommendations": [],
                "changes_proposed": 0,
            }

            if stats["total"] < 10:
                result["reason"] = f"Not enough data yet ({stats['total']} interactions, need 10+)"
                return result

            # Check each model's calibration
            for model_name, model_stats in stats.get("by_model", {}).items():
                if model_stats["total"] < 5:
                    continue

                suggested = archive.suggest_scale_factor(model_name)
                if not suggested:
                    continue

                # Get current config for this model — the actual source of truth
                model_config = get_vision_config(model_name)
                current_sx = model_config.get("scale_x", 1.0)
                current_sy = model_config.get("scale_y", 1.0)
                new_sx = suggested["scale_x"]
                new_sy = suggested["scale_y"]

                # Only propose change if difference is significant (>2%)
                drift_x = abs(new_sx - current_sx) / current_sx
                drift_y = abs(new_sy - current_sy) / current_sy

                if drift_x > 0.02 or drift_y > 0.02:
                    recommendation = {
                        "type": "scale_factor_update",
                        "model": model_name,
                        "current": {"x": current_sx, "y": current_sy},
                        "suggested": {"x": new_sx, "y": new_sy},
                        "drift_percent": {"x": round(drift_x * 100, 1), "y": round(drift_y * 100, 1)},
                        "sample_count": suggested["sample_count"],
                        "model_success_rate": model_stats["success_rate"],
                        "model_avg_error": model_stats["avg_error_px"],
                    }
                    result["recommendations"].append(recommendation)

                    # Propose the code change
                    self._propose_reflex_update(recommendation)
                    result["changes_proposed"] += 1
                else:
                    result["recommendations"].append({
                        "type": "calibration_ok",
                        "model": model_name,
                        "message": f"Scale factors within 2% of optimal (drift: x={drift_x*100:.1f}%, y={drift_y*100:.1f}%)",
                        "success_rate": model_stats["success_rate"],
                    })

            # Analyze correction directions for systematic bias
            # If the model consistently needs corrections in the same direction,
            # that reveals a spatial estimation bias we can compensate for
            bias = self._analyze_correction_bias(archive)
            if bias:
                result["correction_bias"] = bias

            logger.info(f"Servo optimization: {stats['total']} interactions, "
                        f"{stats['success_rate']}% success, "
                        f"{result['changes_proposed']} changes proposed")

            return {"success": True, **result}

        except Exception as e:
            logger.error(f"Servo optimization failed: {e}", exc_info=True)
            return {"success": False, "reason": str(e)}
        finally:
            self._running = False

    @staticmethod
    def _analyze_correction_bias(archive) -> Optional[Dict[str, Any]]:
        """Mine correction logs for systematic directional bias.

        If the model consistently needs nudging in the same direction,
        that's a learnable offset the self-improvement engine can fix.
        No ground truth needed — just "which way did we have to correct?"
        """
        if not archive._archive_path.exists():
            return None

        direction_counts = {}
        total_corrections = 0

        with open(archive._archive_path) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    for corr in entry.get("correction_log", []):
                        d = corr.get("direction", "")
                        if d:
                            direction_counts[d] = direction_counts.get(d, 0) + 1
                            total_corrections += 1
                except (json.JSONDecodeError, KeyError):
                    continue

        if total_corrections < 10:
            return None

        # Find dominant correction direction
        sorted_dirs = sorted(direction_counts.items(), key=lambda x: -x[1])
        top_dir, top_count = sorted_dirs[0]
        top_pct = round(top_count / total_corrections * 100, 1)

        return {
            "total_corrections": total_corrections,
            "dominant_direction": top_dir,
            "dominant_pct": top_pct,
            "all_directions": {k: round(v / total_corrections * 100, 1) for k, v in sorted_dirs},
            "interpretation": (
                f"Model needs '{top_dir}' correction {top_pct}% of the time — "
                f"consider adding a {top_dir} offset to raw coordinates"
                if top_pct > 40 else
                "No strong directional bias detected"
            ),
        }

    def _propose_reflex_update(self, recommendation: Dict[str, Any]):
        """Propose a scale factor update for Uncle Claude review.

        Targets the MODEL_VISION_CONFIGS dict in servo_knowledge_store.py —
        that's where per-model scale factors actually live. Previous version
        targeted non-existent REFLEXES keys (coordinate_scale_x/y) which was
        like mailing a letter to a house that doesn't exist.
        """
        try:
            from backend.services.claude_advisor_service import get_claude_advisor

            file_path = "backend/services/servo_knowledge_store.py"
            root = os.environ.get("GUAARDVARK_ROOT", ".")
            full_path = os.path.join(root, file_path)

            with open(full_path) as f:
                current_content = f.read()

            sx = recommendation["suggested"]["x"]
            sy = recommendation["suggested"]["y"]
            model = recommendation["model"]
            samples = recommendation["sample_count"]
            old_sx = recommendation["current"]["x"]
            old_sy = recommendation["current"]["y"]

            reasoning = (
                f"Servo archive analysis ({samples} interactions with {model}) "
                f"suggests scale factors should be x={sx}, y={sy} "
                f"(currently x={old_sx}, y={old_sy}). "
                f"Success rate: {recommendation['model_success_rate']}%. "
                f"Avg error: {recommendation['model_avg_error']}px."
            )

            # Build a proposed diff targeting the model's entry in MODEL_VISION_CONFIGS
            # We search for the model key and its scale_x/scale_y lines
            proposed_diff = (
                f'--- a/{file_path}\n+++ b/{file_path}\n'
                f'@@ MODEL_VISION_CONFIGS["{model}"] @@\n'
                f'-        "scale_x": {old_sx},\n'
                f'+        "scale_x": {sx},\n'
                f'-        "scale_y": {old_sy},\n'
                f'+        "scale_y": {sy},\n'
            )

            # Uncle Claude reviews it only when scheduled sends are on: this runs
            # from beat, and the review uploads source to Anthropic.
            from backend.services.claude_advisor_service import scheduled_sends_allowed
            advisor = get_claude_advisor()
            reviewed = scheduled_sends_allowed()
            if reviewed:
                review = advisor.review_change(
                    file_path=file_path,
                    current_content=current_content[:2000],
                    proposed_diff=proposed_diff,
                    reasoning=reasoning,
                )
            else:
                review = {"approved": None, "directive": None,
                          "reason": "not sent: scheduled sends to Uncle Claude are off"}

            logger.info(f"Scale factor review: approved={review.get('approved')} "
                        f"directive={review.get('directive')}")

            # Only a review that returned approved True or False happened;
            # anything else is recorded as not reviewed, never as approved.
            approved = review.get("approved")
            reason = review.get("reason") or ""
            if approved is True:
                reviewed_by, review_notes = "uncle_claude", reason or None
            elif approved is False:
                reviewed_by, review_notes = "uncle_claude", f"rejected: {reason or 'no reason given'}"
            else:
                reviewed_by, review_notes = None, f"not reviewed: {reason or 'no verdict returned'}"

            # Stage as pending fix regardless of review outcome —
            # human can always approve/reject from the Settings UI
            try:
                from backend.models import db, PendingFix
                if not getattr(self, '_current_run_id', None):
                    logger.info("PendingFix created without run_id (ad-hoc from _attempt_fix; per team audit)")

                fix = PendingFix(
                    file_path=file_path,
                    proposed_diff=proposed_diff,
                    severity="low",
                    status="proposed",
                    reviewed_by=reviewed_by,
                    review_notes=review_notes,
                )
                db.session.add(fix)
                db.session.commit()
                logger.info(f"Scale factor update staged as pending fix #{fix.id}")
            except Exception as e:
                logger.warning(f"Could not stage pending fix: {e}")

        except Exception as e:
            logger.error(f"Failed to propose scale factor update: {e}", exc_info=True)


    # ── Distillation: extract learned strategies from successful tasks ──
    # This + lesson_reconciler is the foundation for SkillOpt-style automatic skill (recipe/knowledge) optimization
    # without touching model weights. Trajectories from ACS/screen runs or general tool calls feed propose-edit-validate
    # on external artifacts (recipes.json as structured skills, self_knowledge as heuristics/tool policies).
    # Future: add mini-batch optimizer pass on recent Facts/memory trajectories, propose specific recipe edits
    # (e.g. "add wait_until_visible after hotkey", "new recipe for pattern", "shorten target to <=6 words"),
    # validate with success_proof/verification scorer + edit budget, use negative memory for rejections, epoch meta-reflection.
    # See VentureBeat SkillOpt article for the deep-learning-style controls on text skills. Recipes have proven
    # high-leverage for cross-model reliability (Gemma4+ and others).

    _DISTILL_PROMPT = (
        "You are analyzing an agent's task execution that succeeded after initial failures.\n"
        "Extract ONLY the key insight or strategy that made it work.\n"
        "Write a single concise bullet point — focus on the STRATEGY, not specific coordinates or UI state.\n"
        "Keep it under 2 sentences. Do not include markdown formatting, just plain text.\n\n"
        "Task: {task}\n"
        "Steps taken:\n{steps}\n"
    )

    _DISTILL_MARKERS = ("<!-- AUTO-DISTILLED START -->", "<!-- AUTO-DISTILLED END -->")
    _DISTILL_MAX_ENTRIES = 20

    def distill_task_learning(self, task: str, steps: list, model_name: str = ""):
        """
        Extract the winning strategy from a multi-step task and append to
        self_knowledge.md under the auto-managed section.

        Called async via Celery after tasks that succeeded with retries.
        """
        from backend.config import GUAARDVARK_ROOT

        sk_path = os.path.join(GUAARDVARK_ROOT, "data", "agent", "self_knowledge.md")
        if not os.path.isfile(sk_path):
            logger.warning("self_knowledge.md not found, skipping distillation")
            return

        # Format steps into a readable trace
        step_lines = []
        for i, s in enumerate(steps, 1):
            status = "FAILED" if s.get("failed") else "OK"
            action = s.get("action_type", "?")
            target = s.get("target", "")
            text = s.get("text", "")
            keys = s.get("keys", "")
            detail = target or text or (str(keys) if keys else "")
            step_lines.append(f"  {i}. {action}: {detail} [{status}]")
        formatted_steps = "\n".join(step_lines)

        # Count failures — only distill if there were actual failures to learn from
        failure_count = sum(1 for s in steps if s.get("failed"))
        if failure_count == 0:
            logger.debug("No failures in trace, skipping distillation (nothing to learn)")
            return

        # Call LLM to extract the insight
        try:
            from backend.utils.llm_service import is_llm_failure_reply, run_llm_chat_prompt
            prompt = self._DISTILL_PROMPT.format(task=task, steps=formatted_steps)
            insight = run_llm_chat_prompt(prompt)
            if is_llm_failure_reply(insight):
                logger.warning(f"Distillation skipped: the model call failed ({insight.strip()})")
                return
            if not insight or len(insight.strip()) < 10:
                logger.warning("Distillation returned empty/short result, skipping")
                return
            insight = insight.strip()
            # Clean up any markdown the model might add
            insight = re.sub(r'^[-*•]\s*', '', insight)
            insight = re.sub(r'^\*\*.*?\*\*\s*', '', insight)
        except Exception as e:
            logger.error(f"Distillation LLM call failed: {e}")
            return

        # Read existing file and find markers
        try:
            with open(sk_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.error(f"Failed to read self_knowledge.md: {e}")
            return

        start_marker, end_marker = self._DISTILL_MARKERS
        start_idx = content.find(start_marker)
        end_idx = content.find(end_marker)

        if start_idx == -1 or end_idx == -1:
            logger.warning("Auto-distilled markers not found in self_knowledge.md, skipping")
            return

        # Parse existing entries
        section_start = start_idx + len(start_marker)
        existing_section = content[section_start:end_idx]
        existing_entries = [
            line.strip() for line in existing_section.strip().split("\n")
            if line.strip().startswith("- ")
        ]

        # Deduplicate: skip if >60% word overlap with any existing entry
        insight_words = set(insight.lower().split())
        for entry in existing_entries:
            entry_text = re.sub(r'^\- \*\*\[.*?\]\*\*\s*', '', entry)
            entry_words = set(entry_text.lower().split())
            if insight_words and entry_words:
                overlap = len(insight_words & entry_words) / max(len(insight_words), 1)
                if overlap > 0.6:
                    logger.info(f"Distillation skipped — similar entry exists: {entry[:60]}...")
                    return

        # Build new entry with metadata
        today = datetime.now().strftime("%Y-%m-%d")
        model_tag = model_name or "unknown"
        new_entry = f"- **[{today}, {model_tag}]** {insight}"

        # Prune if over max entries (remove oldest, which are at the bottom)
        if len(existing_entries) >= self._DISTILL_MAX_ENTRIES:
            existing_entries = existing_entries[:self._DISTILL_MAX_ENTRIES - 1]

        # Rebuild the section: heading + new entry at top + existing entries
        heading = "### Learned Strategies (auto-distilled from successful sessions)\n"
        all_entries = [new_entry] + existing_entries
        new_section = f"\n{heading}\n" + "\n".join(all_entries) + "\n\n"

        # Write atomically
        new_content = content[:start_idx + len(start_marker)] + new_section + content[end_idx:]

        # This file is part of every agent prompt, so the inbound guard reads the
        # lesson first. While enforcing, a held lesson waits in the review list.
        from backend.services import inbound_guard_service as guard
        verdict = None
        if guard.is_on():
            try:
                verdict = guard.check_and_gate(
                    [guard.change_for_file(sk_path, content, new_content)],
                    source="self_knowledge",
                    subject="distilled lesson for self_knowledge.md",
                    payload={"kind": "write_file", "path": guard.relative(sk_path), "content": new_content},
                )
            except guard.InboundRefused as refused:
                logger.warning(f"Distilled learning not written: {refused}")
                return

        tmp_path = sk_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            os.replace(tmp_path, sk_path)
            logger.info(f"Distilled learning appended to self_knowledge.md: {insight[:80]}...")
            if verdict is not None:
                guard.landed([sk_path], source="self_knowledge", subject="distilled lesson", verdict=verdict)
        except Exception as e:
            logger.error(f"Failed to write self_knowledge.md: {e}")
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def get_self_improvement_service() -> SelfImprovementService:
    return SelfImprovementService()
