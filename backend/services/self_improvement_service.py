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
        self._current_run_id = None  # tagged onto every progress event so the UI can pin
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
            self._current_run_id = run_record.id

            # Re-emit now that we have a run_id so late subscribers still catch it
            self._emit_progress("starting", "Initializing self-check", 0.05,
                                trigger=run_record.trigger)
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
            # Only set success if verification passed (or no changes to verify)
            if run_record.status != "unverified":
                run_record.status = "success" if changes else "failed"
            run_record.duration_seconds = time.time() - start_time
            db.session.commit()

            if changes and run_record.status != "unverified":
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
            self._current_run_id = None

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

            # Submit for Uncle Claude review
            advisor = get_claude_advisor()
            review = advisor.review_change(
                file_path=file_path,
                current_content=current_content[:2000],
                proposed_diff=proposed_diff,
                reasoning=reasoning,
            )

            logger.info(f"Scale factor review: approved={review.get('approved')} "
                        f"directive={review.get('directive')}")

            # Stage as pending fix regardless of review outcome —
            # human can always approve/reject from the Settings UI
            try:
                from backend.models import db, PendingFix
                fix = PendingFix(
                    file_path=file_path,
                    proposed_diff=proposed_diff,
                    severity="low",
                    status="proposed",
                    reviewed_by="uncle_claude" if advisor.is_available() else "pending",
                )
                db.session.add(fix)
                db.session.commit()
                logger.info(f"Scale factor update staged as pending fix #{fix.id}")
            except Exception as e:
                logger.warning(f"Could not stage pending fix: {e}")

        except Exception as e:
            logger.error(f"Failed to propose scale factor update: {e}", exc_info=True)


    # ── Distillation: extract learned strategies from successful tasks ──

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
            from backend.utils.llm_service import run_llm_chat_prompt
            prompt = self._DISTILL_PROMPT.format(task=task, steps=formatted_steps)
            insight = run_llm_chat_prompt(prompt)
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
        tmp_path = sk_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            os.replace(tmp_path, sk_path)
            logger.info(f"Distilled learning appended to self_knowledge.md: {insight[:80]}...")
        except Exception as e:
            logger.error(f"Failed to write self_knowledge.md: {e}")
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def get_self_improvement_service() -> SelfImprovementService:
    return SelfImprovementService()
