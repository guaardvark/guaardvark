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
    AUTORESEARCH_KEEP_MIN_DELTA,
    AUTORESEARCH_MAX_EXPERIMENT_DURATION,
    AUTORESEARCH_MAX_EXPERIMENTS_PER_RUN,
    AUTORESEARCH_MIN_EXPERIMENT_INTERVAL,
    AUTORESEARCH_PHASE_PLATEAU_THRESHOLD,
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
        self._current_parameter = None
        # Seconds between experiments. An attribute (not the constant inline) so
        # tests can zero it; production leaves it at the config floor.
        self.experiment_interval = AUTORESEARCH_MIN_EXPERIMENT_INTERVAL

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

    def run_single_experiment(self, run_tag: str = None,
                              promote_mode: str = "active") -> dict:
        """Execute one experiment cycle. Returns result dict.

        run_tag stamps the ledger row with the owning ResearchRun (nightly
        runs). promote_mode: "active" promotes winners live immediately
        (legacy /start behavior); "candidate" stores winners inactive for the
        run-end A/B confirmation to activate.
        """
        config = self._load_config()
        phase = config.get("phase", 1)
        baseline = config.get("baseline_score", 0.0)
        params = config.get("params", dict(AUTORESEARCH_DEFAULT_PARAMS))

        # 1. Get experiment history
        history = self._get_recent_history(limit=20)

        # 2. Check phase transition
        if self.agent.should_advance_phase(history):
            from backend.services.rag_experiment_agent import MAX_PHASE
            new_phase = min(phase + 1, MAX_PHASE)
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
        self._current_parameter = proposal.get("parameter")

        param_name = proposal["parameter"]
        old_value = params.get(param_name)
        new_value = proposal["new_value"]
        hypothesis = proposal.get("hypothesis", "")
        # Provenance for the ledger: was this a real LLM proposal or the random
        # fallback, and which models proposed/judged.
        provenance = {
            "proposal_source": proposal.get("source", "llm"),
            "proposer_model": getattr(self.agent, "proposer_model_name", None),
            "judge_model": getattr(self.eval_harness, "judge_model_name", None),
            "run_tag": run_tag,
        }

        logger.info(
            f"Experiment {experiment_id[:8]}: {param_name} {old_value} -> {new_value} | {hypothesis}"
        )

        # 4. Apply temporary config
        test_params = dict(params)
        test_params[param_name] = new_value

        # 5. Two-fidelity eval: F0 retrieval screen, F1 judge subset, F2 full.
        t0 = time.time()
        fidelity = 1
        f0_lose = False
        base_retr = {}
        test_retr = {}
        try:
            self.eval_harness.begin_experiment_budget(
                duration_s=AUTORESEARCH_MAX_EXPERIMENT_DURATION,
            )
            try:
                base_retr = self.eval_harness.run_retrieval_eval(dict(params)) or {}
                test_retr = self.eval_harness.run_retrieval_eval(test_params) or {}
            except Exception as e:
                logger.debug(f"F0 retrieval screen skipped: {e}")
                base_retr, test_retr = {}, {}

            f0_lose = (
                (base_retr.get("num_scored") or 0) > 0
                and (test_retr.get("num_scored") or 0) > 0
                and (test_retr.get("mrr") or 0) < (base_retr.get("mrr") or 0)
                and (test_retr.get("hit_rate_at_k") or 0)
                    < (base_retr.get("hit_rate_at_k") or 0)
            )
            if f0_lose:
                fidelity = 0
                eval_result = {
                    "composite_score": baseline,
                    "num_pairs": test_retr.get("num_pairs") or 1,
                    "details": [],
                    "retrieval": test_retr,
                    "parse_fail_ratio": 0.0,
                    "parse_fail_crash": False,
                }
            else:
                subset = self.eval_harness._select_judge_subset()
                eval_result = self.eval_harness.run_full_eval(
                    test_params, pairs=subset,
                )
                fidelity = 1
                if eval_result.get("parse_fail_crash"):
                    raise RuntimeError(
                        "judge_parse_fail_ratio="
                        f"{eval_result.get('parse_fail_ratio')}"
                    )
                f1_delta = (eval_result.get("composite_score") or 0.0) - baseline
                if f1_delta >= AUTORESEARCH_KEEP_MIN_DELTA:
                    try:
                        n_active = len(self.eval_harness._get_active_eval_pairs())
                    except Exception:
                        n_active = len(subset or [])
                    if n_active > len(subset or []):
                        eval_result = self.eval_harness.run_full_eval(test_params)
                        fidelity = 2
                        if eval_result.get("parse_fail_crash"):
                            raise RuntimeError(
                                "judge_parse_fail_ratio="
                                f"{eval_result.get('parse_fail_ratio')}"
                            )
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
                "fidelity": fidelity,
                **provenance,
            }
            self._log_experiment(result)
            self._current_experiment_id = None
            self._current_parameter = None
            return result

        # 5b. A 0-pair eval "succeeds" with score 0.0 in about a millisecond and
        # would be logged as an ordinary discard — which is exactly how the
        # 2026-08 runaway kept spinning for 3.4 days. No eval pairs means no
        # experiment actually happened; report it as a crash so run_loop's
        # consecutive-crash guard halts the loop instead of iterating forever.
        if eval_result.get("num_pairs", 0) == 0:
            logger.error(
                "Eval set is empty — nothing was measured. Generate eval pairs "
                "(POST /api/autoresearch/eval-pairs/regenerate) before running experiments."
            )
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
                "duration": duration,
                "phase": phase,
                "fidelity": fidelity,
                **provenance,
            }
            self._log_experiment(result)
            self._current_experiment_id = None
            self._current_parameter = None
            return result

        # 6. Compare to baseline — min-delta matches run-end confirmation.
        # Retrieval improvement can keep when composite does not drop.
        retr = eval_result.get("retrieval") or test_retr or {}
        delta = round(new_score - baseline, 4)
        status = self._decide_keep(
            new_score, baseline, retr, base_retr, f0_lose=f0_lose,
        )

        # 7. Keep or revert
        promoted_id = None
        if status == "keep":
            config["params"][param_name] = new_value
            config["baseline_score"] = new_score
            config["phase_plateau_count"] = 0
            self._save_config(config)
            promoted_id = self._promote_config(
                config, new_score, "local",
                activate=(promote_mode == "active"),
            )
            logger.info(
                f"KEEP: {param_name}={new_value} score={new_score:.4f} "
                f"(delta=+{delta:.4f} fidelity={fidelity})"
            )
        else:
            config["phase_plateau_count"] = config.get("phase_plateau_count", 0) + 1
            self._save_config(config)
            logger.info(
                f"DISCARD: {param_name}={new_value} score={new_score:.4f} "
                f"(delta={delta:.4f} fidelity={fidelity})"
            )

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
            "retrieval_metrics": retr if retr else eval_result.get("retrieval"),
            "fidelity": fidelity,
            "config_id": promoted_id,
            **provenance,
        }

        # 8. Log and broadcast
        self._log_experiment(result)
        if status == "keep":
            self._broadcast_to_family(result)
        self._emit_socket_event(result)

        self._current_experiment_id = None
        self._current_parameter = None
        return result

    def _decide_keep(
        self, new_score, baseline, new_retr, base_retr, f0_lose=False,
    ) -> str:
        """Keep only a real improvement: min-delta on composite, or retrieval
        up with composite not dropping. F0 losers never keep."""
        if f0_lose:
            return "discard"
        delta = (new_score or 0.0) - (baseline or 0.0)
        retr_improved = False
        if new_retr and base_retr:
            retr_improved = (
                (new_retr.get("mrr") or 0) > (base_retr.get("mrr") or 0)
                or (new_retr.get("hit_rate_at_k") or 0)
                    > (base_retr.get("hit_rate_at_k") or 0)
            )
        if delta >= AUTORESEARCH_KEEP_MIN_DELTA:
            return "keep"
        if retr_improved and delta >= 0:
            return "keep"
        return "discard"

    def run_loop(self, max_experiments: int = 0):
        """Run experiment loop until paused, disabled, capped, or plateaued.

        A single invocation is always FINITE: callers asking for "unbounded"
        (max_experiments=0) get AUTORESEARCH_MAX_EXPERIMENTS_PER_RUN. The
        2026-08-07..10 runaway ran max_experiments=0 inside a Celery beat task
        with an empty eval set — ~3,500 no-op discards/second for 3.4 days
        (134M ExperimentRun rows) with no way to stop it short of killing the
        worker: self._paused lives per-process, and neither the /stop endpoint
        nor activity tracking ever reach the Celery process.
        """
        if self._running:
            logger.warning("Autoresearch loop already running")
            return

        if max_experiments <= 0:
            max_experiments = AUTORESEARCH_MAX_EXPERIMENTS_PER_RUN

        self._running = True
        self._paused = False
        count = 0

        try:
            if not self._check_prerequisites():
                return

            while not self._paused:
                if count >= max_experiments:
                    logger.info(f"Reached max experiments ({max_experiments})")
                    break

                if self._stop_requested():
                    logger.info("Autoresearch stop requested via settings — stopping loop")
                    break

                result = self.run_single_experiment()
                count += 1

                recent = self._get_recent_history(limit=3)
                if len(recent) >= 3 and all(r.get("status") == "crash" for r in recent):
                    logger.error("3 consecutive crashes — pausing autoresearch")
                    break

                # Terminal condition: another discard while the LAST phase is
                # already plateaued. There is no phase 4 to advance to, so more
                # iterations can only rediscover the plateau — stop instead of
                # spinning against it. A "keep" resets the plateau and the loop
                # continues, so recovery stays possible.
                from backend.services.rag_experiment_agent import MAX_PHASE
                cfg = self._load_config()
                if (
                    result.get("status") == "discard"
                    and cfg.get("phase", 1) >= MAX_PHASE
                    and cfg.get("phase_plateau_count", 0) >= AUTORESEARCH_PHASE_PLATEAU_THRESHOLD
                ):
                    logger.info(
                        "Phase 3 plateaued after %d consecutive discards — research "
                        "complete for this corpus; stopping loop",
                        cfg.get("phase_plateau_count", 0),
                    )
                    break

                # Pacing floor. Real evals take minutes, so this costs nothing —
                # but a degenerate fast-failing experiment can no longer spin.
                time.sleep(self.experiment_interval)

        finally:
            self._running = False
            self._current_experiment_id = None
            self._current_parameter = None
            # The whole loop runs inside an app context pushed by the caller; tidy
            # the scoped session so a long-lived daemon doesn't leak connections.
            try:
                from backend.models import db
                db.session.remove()
            except Exception:
                pass
            logger.info(f"Autoresearch loop ended after {count} experiments")

    def _stop_requested(self) -> bool:
        """Cross-process kill switch, checked every loop iteration.

        self._paused only reaches whichever process handled the /stop request or
        the user's HTTP activity — a loop running inside a Celery worker never
        hears either. The dedicated `autoresearch_kill` Settings row is shared
        state every process can see: POST /stop sets it, /start and run kickoff
        clear it. (Deliberately NOT the auto_enabled toggle — that only gates
        AUTO-start; flipping it off must not kill a manual run mid-flight.)
        """
        try:
            from backend.models import Setting
            s = Setting.query.filter_by(key="autoresearch_kill").first()
            return s is not None and str(s.value).strip().lower() == "true"
        except Exception:
            return False

    # Backwards-compat alias (older tests/callers)
    _disabled_in_settings = _stop_requested

    def _check_prerequisites(self) -> bool:
        """Verify system is ready for autoresearch.

        Fail CLOSED: the corpus check hits the DB and needs a Flask app context.
        The caller (run_loop's thread target) is responsible for pushing one. If it
        didn't — or the check otherwise errors — we must NOT proceed without the
        corpus gate, so we return False instead of silently barrelling ahead.
        """
        try:
            if not self.eval_harness.has_sufficient_corpus():
                logger.warning("Insufficient corpus for autoresearch")
                return False
            # An empty eval set makes every experiment a ~1ms score-0.0 no-op —
            # there is nothing to research. Refuse to start rather than spin.
            if not self.eval_harness._get_active_eval_pairs():
                logger.warning(
                    "No eval pairs — autoresearch has nothing to measure. Generate an "
                    "eval set first (POST /api/autoresearch/eval-pairs/regenerate)."
                )
                return False
        except RuntimeError as e:
            # Almost always "Working outside of application context" — a real
            # prerequisite-verification failure, not a reason to run anyway.
            logger.error(f"Prerequisite check could not run (no app context?): {e}")
            return False
        except Exception as e:
            logger.error(f"Prerequisite check failed: {e}")
            return False
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
                run_tag=result.get("run_tag"),
                proposal_source=result.get("proposal_source"),
                proposer_model=result.get("proposer_model"),
                judge_model=result.get("judge_model"),
                retrieval_metrics=result.get("retrieval_metrics"),
            )
            db.session.add(run)
            db.session.commit()
        except Exception as e:
            logger.error(f"Failed to log experiment: {e}")

    def _promote_config(self, config: dict, score: float, source: str,
                        activate: bool = True):
        """Save a winning config to the ResearchConfig table.

        activate=True: goes live immediately (deactivates predecessors).
        activate=False: stored as a CANDIDATE — nightly-run winners stay
        inactive until the run-end A/B confirmation activates the best one.
        Returns the new row id (or None on failure).
        """
        try:
            from backend.models import ResearchConfig, db
            if activate:
                ResearchConfig.query.filter_by(is_active=True).update({"is_active": False})
            new_config = ResearchConfig(
                id=str(uuid.uuid4()),
                params=config["params"],
                composite_score=score,
                is_active=activate,
                promoted_at=datetime.utcnow() if activate else None,
                source=source,
                status="promoted" if activate else "candidate",
            )
            db.session.add(new_config)
            db.session.commit()
            if activate:
                # Promoted params now feed live retrieval (get_active_rag_params);
                # drop the cache so the change applies immediately, not after TTL.
                from backend.utils.experiment_context import invalidate_active_params_cache
                invalidate_active_params_cache()
            return new_config.id
        except Exception as e:
            logger.error(f"Failed to promote config: {e}")
            return None

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
        """Share a winning config with the interconnector family.

        Two real channels (the old `broadcast_learning` import never existed —
        this method was a permanent silent ImportError): an
        InterconnectorLearning row, which the DB entity sync carries to peers,
        and a broadcast_directive so active peers hear about it immediately.
        Receivers store foreign configs as CANDIDATES only (is_active=False) —
        a peer's winner is never auto-activated here.
        """
        description = (
            f"[AUTORESEARCH] {result['parameter']}={result['new_value']}, "
            f"score={result['composite_score']:.4f}, delta=+{result['delta']:.4f}"
        )
        try:
            from backend.models import InterconnectorLearning, db
            learning = InterconnectorLearning(
                source_node_id=os.environ.get("GUAARDVARK_NODE_ID", "local"),
                learning_type="rag_optimization",
                description=description,
                confidence=min(1.0, max(0.0, result.get("delta", 0.0))),
                model_used=result.get("judge_model") or "local",
            )
            db.session.add(learning)
            db.session.commit()
        except Exception as e:
            logger.debug(f"Family learning row skipped: {e}")
            try:
                from backend.models import db
                db.session.rollback()
            except Exception:
                pass
        try:
            from backend.services.interconnector_sync_service import get_sync_service
            get_sync_service().broadcast_directive(
                directive=f"rag_config_promoted: {description}",
                reason="autoresearch promotion",
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
        """Current status for dashboard.

        `running` is true if THIS process's loop is active OR a ResearchRun
        row is pending/running — the overnight engine no longer sets
        `_running` on this singleton.
        """
        config = self._load_config()
        active_run = None
        eval_pair_count = 0
        try:
            from backend.models import ResearchRun, EvalPair
            from datetime import datetime as _dt
            run = (
                ResearchRun.query
                .filter(ResearchRun.status.in_(("running", "pending")))
                .order_by(ResearchRun.created_at.desc())
                .first()
            )
            if run is not None:
                active_run = run.to_dict()
                remaining = None
                if run.started_at and run.wall_clock_budget_s is not None:
                    elapsed = (_dt.utcnow() - run.started_at).total_seconds()
                    remaining = max(0, int(run.wall_clock_budget_s - elapsed))
                active_run["budget_remaining_s"] = remaining
            eval_pair_count = (
                EvalPair.query.filter(EvalPair.is_active.isnot(False)).count()
            )
        except Exception:
            pass
        running = bool(self._running or active_run)
        return {
            "running": running,
            "paused": self._paused,
            "current_experiment_id": self._current_experiment_id,
            "current_parameter": self._current_parameter,
            "phase": config.get("phase", 1),
            "baseline_score": config.get("baseline_score", 0.0),
            "params": config.get("params", {}),
            "total_experiments": self._count_experiments(),
            "total_improvements": self._count_improvements(),
            "active_run": active_run,
            "eval_pair_count": eval_pair_count,
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
