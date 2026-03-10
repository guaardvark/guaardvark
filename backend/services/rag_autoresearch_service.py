"""RAG Autoresearch Orchestrator — the experiment loop.

Coordinates the eval harness, experiment agent, and config management.
Runs experiments when the system is idle, pauses on user activity.
"""
import json
import os
import time
import logging
import uuid
from datetime import datetime
from threading import Lock

from backend.config import (
    AUTORESEARCH_DEFAULT_PARAMS,
    AUTORESEARCH_MAX_EXPERIMENT_DURATION,
)
from backend.services.rag_eval_harness import RAGEvalHarness
from backend.services.rag_experiment_agent import RAGExperimentAgent

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "rag_experiment_config.json"


class RAGAutoresearchService:
    """Core experiment loop orchestrator."""

    def __init__(self):
        self.eval_harness = RAGEvalHarness()
        self.agent = RAGExperimentAgent()
        self._paused = False
        self._running = False
        self._last_activity = time.time()
        self._lock = Lock()
        self._current_experiment_id = None

    # --- Activity tracking ---

    def record_activity(self):
        """Called by activity tracker middleware on user requests."""
        self._last_activity = time.time()
        if self._running:
            self._paused = True

    def is_idle(self, idle_minutes: int = 10) -> bool:
        """Check if system has been idle for the threshold duration."""
        elapsed = time.time() - self._last_activity
        return elapsed > (idle_minutes * 60)

    def is_running(self) -> bool:
        return self._running

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    # --- Config management ---

    def _config_path(self) -> str:
        root = os.environ.get("GUAARDVARK_ROOT", "")
        return os.path.join(root, "data", CONFIG_FILENAME)

    def _load_config(self) -> dict:
        """Load current experiment config from disk."""
        path = self._config_path()
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            config = {
                "version": 1,
                "baseline_score": 0.0,
                "params": dict(AUTORESEARCH_DEFAULT_PARAMS),
                "phase": 1,
                "phase_plateau_count": 0,
            }
            self._save_config(config)
            return config

    def _save_config(self, config: dict):
        """Atomically save config to disk."""
        path = self._config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp_path, path)

    # --- Experiment execution ---

    def run_single_experiment(self) -> dict:
        """Execute one experiment cycle. Returns result dict."""
        config = self._load_config()
        phase = config.get("phase", 1)
        baseline = config.get("baseline_score", 0.0)
        params = config.get("params", dict(AUTORESEARCH_DEFAULT_PARAMS))

        # 1. Get experiment history
        history = self._get_recent_history(limit=20)

        # 2. Check phase transition
        if self.agent.should_advance_phase(history):
            new_phase = min(phase + 1, 3)
            if new_phase != phase:
                logger.info(f"Advancing from Phase {phase} to Phase {new_phase}")
                config["phase"] = new_phase
                config["phase_plateau_count"] = 0
                self._save_config(config)
                phase = new_phase

        # 3. Agent proposes experiment
        proposal = self.agent.propose_experiment(history, params, phase)
        experiment_id = str(uuid.uuid4())
        self._current_experiment_id = experiment_id

        param_name = proposal["parameter"]
        old_value = params.get(param_name)
        new_value = proposal["new_value"]
        hypothesis = proposal.get("hypothesis", "")

        logger.info(
            f"Experiment {experiment_id[:8]}: {param_name} {old_value} -> {new_value} | {hypothesis}"
        )

        # 4. Apply temporary config
        test_params = dict(params)
        test_params[param_name] = new_value

        # 5. Run eval
        t0 = time.time()
        try:
            eval_result = self.eval_harness.run_full_eval(test_params)
            new_score = eval_result["composite_score"]
            duration = time.time() - t0
        except Exception as e:
            logger.error(f"Experiment crashed: {e}")
            result = {
                "experiment_id": experiment_id,
                "parameter": param_name,
                "old_value": str(old_value),
                "new_value": str(new_value),
                "hypothesis": hypothesis,
                "status": "crash",
                "composite_score": 0.0,
                "baseline_score": baseline,
                "delta": 0.0,
                "duration": time.time() - t0,
                "phase": phase,
            }
            self._log_experiment(result)
            return result

        # 6. Compare to baseline
        delta = round(new_score - baseline, 4)
        status = "keep" if new_score > baseline else "discard"

        # 7. Keep or revert
        if status == "keep":
            config["params"][param_name] = new_value
            config["baseline_score"] = new_score
            config["phase_plateau_count"] = 0
            self._save_config(config)
            self._promote_config(config, new_score, "local")
            logger.info(f"KEEP: {param_name}={new_value} score={new_score:.4f} (delta=+{delta:.4f})")
        else:
            config["phase_plateau_count"] = config.get("phase_plateau_count", 0) + 1
            self._save_config(config)
            logger.info(f"DISCARD: {param_name}={new_value} score={new_score:.4f} (delta={delta:.4f})")

        result = {
            "experiment_id": experiment_id,
            "parameter": param_name,
            "old_value": str(old_value),
            "new_value": str(new_value),
            "hypothesis": hypothesis,
            "status": status,
            "composite_score": new_score,
            "baseline_score": baseline,
            "delta": delta,
            "duration": duration,
            "phase": phase,
            "eval_details": eval_result.get("details", []),
        }

        # 8. Log and broadcast
        self._log_experiment(result)
        if status == "keep":
            self._broadcast_to_family(result)
        self._emit_socket_event(result)

        self._current_experiment_id = None
        return result

    def run_loop(self, max_experiments: int = 0):
        """Run experiment loop until paused or max reached."""
        if self._running:
            logger.warning("Autoresearch loop already running")
            return

        self._running = True
        self._paused = False
        count = 0

        try:
            if not self._check_prerequisites():
                return

            while not self._paused:
                if max_experiments > 0 and count >= max_experiments:
                    logger.info(f"Reached max experiments ({max_experiments})")
                    break

                result = self.run_single_experiment()
                count += 1

                recent = self._get_recent_history(limit=3)
                if len(recent) >= 3 and all(r.get("status") == "crash" for r in recent):
                    logger.error("3 consecutive crashes — pausing autoresearch")
                    break

        finally:
            self._running = False
            self._current_experiment_id = None
            logger.info(f"Autoresearch loop ended after {count} experiments")

    def _check_prerequisites(self) -> bool:
        """Verify system is ready for autoresearch."""
        try:
            from flask import current_app
            if not self.eval_harness.has_sufficient_corpus():
                logger.warning("Insufficient corpus for autoresearch")
                return False
        except RuntimeError:
            pass
        return True

    # --- DB operations ---

    def _log_experiment(self, result: dict):
        """Save experiment result to ExperimentRun table."""
        try:
            from backend.models import ExperimentRun, db
            run = ExperimentRun(
                id=result["experiment_id"],
                phase=result.get("phase", 1),
                parameter_changed=result["parameter"],
                old_value=result.get("old_value"),
                new_value=result.get("new_value"),
                hypothesis=result.get("hypothesis"),
                composite_score=result.get("composite_score", 0.0),
                baseline_score=result.get("baseline_score", 0.0),
                delta=result.get("delta", 0.0),
                status=result["status"],
                eval_details=result.get("eval_details"),
                duration_seconds=result.get("duration"),
            )
            db.session.add(run)
            db.session.commit()
        except Exception as e:
            logger.error(f"Failed to log experiment: {e}")

    def _promote_config(self, config: dict, score: float, source: str):
        """Save winning config to ResearchConfig table."""
        try:
            from backend.models import ResearchConfig, db
            ResearchConfig.query.filter_by(is_active=True).update({"is_active": False})
            new_config = ResearchConfig(
                id=str(uuid.uuid4()),
                params=config["params"],
                composite_score=score,
                is_active=True,
                promoted_at=datetime.utcnow(),
                source=source,
            )
            db.session.add(new_config)
            db.session.commit()
        except Exception as e:
            logger.error(f"Failed to promote config: {e}")

    def _get_recent_history(self, limit: int = 20) -> list:
        """Get recent experiment results from DB."""
        try:
            from backend.models import ExperimentRun
            runs = (
                ExperimentRun.query
                .order_by(ExperimentRun.created_at.desc())
                .limit(limit)
                .all()
            )
            return [r.to_dict() for r in reversed(runs)]
        except Exception:
            return []

    def _broadcast_to_family(self, result: dict):
        """Broadcast winning config via interconnector."""
        try:
            from backend.services.interconnector_sync_service import broadcast_learning
            broadcast_learning(
                learning_type="rag_optimization",
                description=(
                    f"[AUTORESEARCH] {result['parameter']}={result['new_value']}, "
                    f"score={result['composite_score']:.4f}, delta=+{result['delta']:.4f}"
                ),
            )
        except Exception as e:
            logger.debug(f"Family broadcast skipped: {e}")

    def _emit_socket_event(self, result: dict):
        """Emit real-time update via Socket.IO."""
        try:
            from backend.socketio_instance import socketio
            socketio.emit("autoresearch:experiment_complete", {
                "experiment_id": result["experiment_id"],
                "parameter": result["parameter"],
                "status": result["status"],
                "score": result.get("composite_score"),
                "delta": result.get("delta"),
            })
        except Exception:
            pass

    def get_status(self) -> dict:
        """Current status for dashboard."""
        config = self._load_config()
        return {
            "running": self._running,
            "paused": self._paused,
            "current_experiment_id": self._current_experiment_id,
            "phase": config.get("phase", 1),
            "baseline_score": config.get("baseline_score", 0.0),
            "params": config.get("params", {}),
            "total_experiments": self._count_experiments(),
            "total_improvements": self._count_improvements(),
        }

    def _count_experiments(self) -> int:
        try:
            from backend.models import ExperimentRun
            return ExperimentRun.query.count()
        except Exception:
            return 0

    def _count_improvements(self) -> int:
        try:
            from backend.models import ExperimentRun
            return ExperimentRun.query.filter_by(status="keep").count()
        except Exception:
            return 0


# Singleton instance
_autoresearch_service = None


def get_autoresearch_service() -> RAGAutoresearchService:
    global _autoresearch_service
    if _autoresearch_service is None:
        _autoresearch_service = RAGAutoresearchService()
    return _autoresearch_service
