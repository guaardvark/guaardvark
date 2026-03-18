"""
SelfImprovementService — autonomous self-improvement loop.

Three modes:
  1. Scheduled: periodic test suite runs with auto-fix
  2. Reactive: error-triggered self-healing
  3. Directed: user/Claude-submitted improvement tasks
"""
import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _is_codebase_locked() -> bool:
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
    try:
        from backend.models import db, SystemSetting
        setting = db.session.query(SystemSetting).filter_by(key="self_improvement_enabled").first()
        return setting and setting.value.lower() == "true"
    except Exception:
        return False


class SelfImprovementService:
    """Manages the autonomous self-improvement loop."""

    _instance = None
    _lock = threading.Lock()

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
        logger.info("SelfImprovementService initialized")

    def _emit_progress(self, stage: str, detail: str = "", progress: float = 0.0, **extra):
        """Emit a self-improvement progress event via SocketIO."""
        try:
            from backend.socketio_instance import socketio
            socketio.emit("self_improvement_progress", {
                "stage": stage,
                "detail": detail,
                "progress": progress,
                "running": self._running,
                "timestamp": time.time(),
                **extra,
            })
        except Exception:
            pass  # Socket may not be available in test mode

    def _is_safe_to_run(self) -> bool:
        if _is_codebase_locked():
            logger.warning("Self-improvement blocked: codebase is locked")
            return False
        if not _is_self_improvement_enabled():
            logger.info("Self-improvement is disabled")
            return False
        if self._running:
            logger.warning("Self-improvement already running")
            return False
        return True

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

    def run_self_check(self) -> Dict[str, Any]:
        """Mode 1: Run test suite, identify failures, dispatch agent to fix."""
        if not self._is_safe_to_run():
            return {"success": False, "reason": "Self-improvement cannot run"}

        self._running = True
        start_time = time.time()
        run_record = None
        self._emit_progress("starting", "Initializing self-check", 0.0)

        try:
            from backend.models import db, SelfImprovementRun
            run_record = SelfImprovementRun(
                trigger="scheduled",
                status="running",
                node_id=os.environ.get("GUAARDVARK_NODE_ID", "local"),
            )
            db.session.add(run_record)
            db.session.commit()

            self._emit_progress("testing", "Running test suite", 0.1)
            root = os.environ.get("GUAARDVARK_ROOT", ".")
            result = subprocess.run(
                ["python3", "-m", "pytest", "backend/tests/test_self_improvement.py",
                 "backend/tests/test_code_tools.py", "-v", "--tb=short", "--no-header"],
                capture_output=True, text=True, timeout=300, cwd=root,
                env={**os.environ, "GUAARDVARK_MODE": "test"},
            )

            test_output = result.stdout + result.stderr
            failures = self._parse_test_failures(test_output)

            run_record.test_results_before = json.dumps({
                "total_failures": len(failures),
                "failures": failures,
                "return_code": result.returncode,
            })

            self._emit_progress("analyzed", f"Found {len(failures)} failure(s)", 0.3,
                                failures_found=len(failures), return_code=result.returncode)

            if not failures and result.returncode == 0:
                run_record.status = "success"
                run_record.duration_seconds = time.time() - start_time
                db.session.commit()
                self._emit_progress("complete", "All tests passing", 1.0, status="success")
                return {"success": True, "message": "All tests passing", "failures": 0}

            # Return code nonzero but parser found nothing — record as unparsed failure
            if not failures and result.returncode != 0:
                failures = [{"file": "unknown", "test_name": "unparsed_failure", "error": test_output[-500:]}]

            changes = []
            for i, failure in enumerate(failures):
                if not self._is_safe_to_run():
                    break
                progress = 0.3 + (0.6 * (i / max(len(failures), 1)))
                self._emit_progress("fixing", f"Fixing {failure['test_name']} ({i+1}/{len(failures)})",
                                    progress, current_fix=i+1, total_fixes=len(failures))
                change = self._attempt_fix(failure)
                if change:
                    changes.append(change)

            # Verification: re-run tests to confirm fixes worked
            if changes:
                self._emit_progress("verifying", "Re-running tests to verify fixes", 0.9)
                test_files = ["backend/tests/test_self_improvement.py", "backend/tests/test_code_tools.py"]
                verify_results = self._verify_fix(test_files)
                run_record.test_results_after = json.dumps(verify_results)
                if not verify_results["all_passed"]:
                    logger.warning(f"Verification failed: {verify_results['total_failures']} failures remain")
                    run_record.status = "unverified"
                else:
                    logger.info("Verification passed: all tests passing after fixes")

            run_record.changes_made = json.dumps(changes)
            run_record.status = "success" if changes else "failed"
            run_record.duration_seconds = time.time() - start_time
            db.session.commit()

            if changes:
                self._broadcast_learnings(changes, run_record)

            self._emit_progress("complete", f"{len(changes)} fix(es) applied", 1.0,
                                status=run_record.status, fixes_applied=len(changes),
                                failures_found=len(failures))

            return {
                "success": True,
                "failures_found": len(failures),
                "fixes_applied": len(changes),
                "changes": changes,
            }

        except Exception as e:
            logger.error(f"Self-check failed: {e}", exc_info=True)
            self._emit_progress("error", str(e)[:200], 0.0, status="failed")
            if run_record:
                run_record.status = "failed"
                run_record.error_message = str(e)
                run_record.duration_seconds = time.time() - start_time
                try:
                    from backend.models import db
                    db.session.commit()
                except Exception:
                    pass
            return {"success": False, "reason": str(e)}
        finally:
            self._running = False

    def _attempt_fix(self, failure: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Dispatch code_assistant agent to fix a test failure."""
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
            )

            message = (
                f"Fix this failing test. "
                f"Test file: {failure['file']}, test: {failure['test_name']}. "
                f"Error: {failure['error']}. "
                f"Read the test first to understand what is expected, "
                f"then read the source code, then fix the bug."
            )

            executor.set_tool_context(_self_improvement_context=True, _reasoning=message)

            result = executor.execute(message, session_context=agent_config.system_prompt)

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

    def _verify_fix(self, test_files):
        """Re-run tests after agent fixes to verify they pass."""
        try:
            root = os.environ.get("GUAARDVARK_ROOT", ".")
            result = subprocess.run(
                ["python3", "-m", "pytest"] + test_files + ["-v", "--tb=short", "--no-header"],
                capture_output=True, text=True, timeout=300, cwd=root,
                env={**os.environ, "GUAARDVARK_MODE": "test"},
            )
            failures = self._parse_test_failures(result.stdout + result.stderr)
            return {
                "total_failures": len(failures),
                "failures": failures,
                "return_code": result.returncode,
                "all_passed": result.returncode == 0 and len(failures) == 0,
            }
        except Exception as e:
            logger.error(f"Verification run failed: {e}")
            return {"total_failures": -1, "failures": [], "return_code": -1, "all_passed": False}

    def _broadcast_learnings(self, changes: List[Dict], run_record):
        """Create InterconnectorLearning records and broadcast to family."""
        try:
            from backend.models import db, InterconnectorLearning
            for change in changes:
                learning = InterconnectorLearning(
                    source_node_id=os.environ.get("GUAARDVARK_NODE_ID", "local"),
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
        try:
            from backend.models import db, SelfImprovementRun
            run_record = SelfImprovementRun(
                trigger="reactive",
                status="running",
                node_id=os.environ.get("GUAARDVARK_NODE_ID", "local"),
            )
            db.session.add(run_record)
            db.session.commit()

            failure = {
                "file": file,
                "test_name": f"runtime_error_line_{line}",
                "error": f"{error_type} at {file}:{line}\n{traceback_str[:500]}",
            }
            change = self._attempt_fix(failure)

            run_record.status = "success" if change else "failed"
            run_record.changes_made = json.dumps([change] if change else [])
            db.session.commit()

        except Exception as e:
            logger.error(f"Self-healing failed: {e}", exc_info=True)
        finally:
            self._running = False

    def submit_directed_task(
        self, description: str, target_files: List[str] = None, priority: str = "medium"
    ) -> Dict[str, Any]:
        """Mode 3: User/Claude-submitted improvement task."""
        if not self._is_safe_to_run():
            return {"success": False, "reason": "Self-improvement cannot run"}

        self._running = True
        try:
            from backend.models import db, SelfImprovementRun
            run_record = SelfImprovementRun(
                trigger="directed",
                status="running",
                node_id=os.environ.get("GUAARDVARK_NODE_ID", "local"),
            )
            db.session.add(run_record)
            db.session.commit()

            failure = {
                "file": ", ".join(target_files) if target_files else "unknown",
                "test_name": "directed_improvement",
                "error": description,
            }
            change = self._attempt_fix(failure)

            run_record.status = "success" if change else "failed"
            run_record.changes_made = json.dumps([change] if change else [])
            db.session.commit()

            return {"success": bool(change), "change": change}
        except Exception as e:
            logger.error(f"Directed improvement failed: {e}", exc_info=True)
            return {"success": False, "reason": str(e)}
        finally:
            self._running = False


def get_self_improvement_service() -> SelfImprovementService:
    return SelfImprovementService()
