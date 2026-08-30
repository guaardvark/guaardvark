"""Research Run engine — bounded overnight autoresearch with a morning report.

Karpathy-autoresearch DNA: a run has a tag, a fixed wall-clock budget, a
ledger (ExperimentRun rows stamped with the run_tag), keep/discard per
experiment, a frozen research-program snapshot, and a report you read in the
morning. Guardrails (all inherited from the 2026-08 runaway postmortem):

- Preconditions fail LOUDLY (`failed_precondition` + report) — the run never
  degrades into random-proposal noise because Ollama was off or the eval set
  was empty.
- Wall-clock hard cap + iteration hard cap + pacing floor.
- Cross-process kill flag (`autoresearch_kill` Setting) checked every cycle.
- GPU politeness: yields (with backoff, counted against the budget) while the
  user's image/video generation is active.
- Nightly winners are stored as CANDIDATE configs; only the run-end
  confirmation eval (candidate vs currently-active, same eval set) activates
  the best one. No single-lucky-eval promotions.
"""
import logging
import os
import time
import uuid
from datetime import datetime, timedelta

from backend.config import (
    AUTORESEARCH_KEEP_MIN_DELTA,
    AUTORESEARCH_MIN_EXPERIMENT_INTERVAL,
    AUTORESEARCH_PHASE_PLATEAU_THRESHOLD,
)

logger = logging.getLogger(__name__)

DEFAULT_BUDGET_HOURS = 6.0
MAX_BUDGET_HOURS = 12.0
HARD_ITERATION_CAP = 500          # belt-and-braces above the wall clock
GPU_YIELD_SLEEP_S = 60            # sleep while the GPU is busy
CONSECUTIVE_CRASH_HALT = 3
CONFIRMATION_MIN_DELTA = AUTORESEARCH_KEEP_MIN_DELTA
# Celery time_limit sits above MAX_BUDGET_HOURS so the wall-clock cap in
# execute_run fires first; the task limit is the last-resort kill.
EXECUTE_RUN_TIME_LIMIT_S = int(MAX_BUDGET_HOURS * 3600) + 3600
EXECUTE_RUN_SOFT_LIMIT_S = int(MAX_BUDGET_HOURS * 3600) + 1800

PROGRAM_PATH = os.path.join("data", "rag_research_program.md")

DEFAULT_PROGRAM = """# Research Program

This file is YOURS to edit (the human's). It is snapshotted into every
research run and shown to the proposer LLM and to code-tuning arms. Use it
to direct the search: which parameters matter, what you've observed, what
to avoid, whether tonight should prefer retrieval knobs or code.

## Directives
- Prioritize retrieval quality (hit rate / MRR) over answer style.
- Prefer simple changes; a small gain that complicates the config is not
  worth it (simplicity criterion).
- If an experiment class keeps losing, say so here and steer elsewhere.
- Code arms: one change, measure with the RAG eval harness, never self-score.

## Notes
(none yet)
"""

VALID_MODES = ("unified", "rag_tuning", "code_tuning")
SWARM_POLL_S = 15
SI_PREFLIGHT_CAP_S = 600  # 10 min — snapshot_pytest is usually far cheaper


class ResearchRunService:
    """Creates and executes bounded research runs."""

    def __init__(self):
        self._running_run_id = None

    # ---- kickoff -------------------------------------------------------

    def kickoff(self, mode: str = "unified", budget_hours: float = None,
                trigger: str = "manual") -> dict:
        """Create a ResearchRun row and enqueue the Celery owner task.

        mode: unified (default) | rag_tuning | code_tuning
        """
        from backend.models import ResearchRun, db

        budget_hours = min(float(budget_hours or DEFAULT_BUDGET_HOURS), MAX_BUDGET_HOURS)
        if mode not in VALID_MODES:
            mode = "unified"

        self._recover_stale_runs()

        active = ResearchRun.query.filter(
            ResearchRun.status.in_(("running", "pending"))
        ).first()
        if active is not None:
            return {"error": "A research run is already in progress", "run": active.to_dict()}

        if mode in ("code_tuning", "unified"):
            gate = self._code_gate_error()
            # unified degrades (code half skipped later); code-only refuses.
            if gate and mode == "code_tuning":
                return {"error": gate}

        run_tag = f"{mode}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
        run = ResearchRun(
            id=str(uuid.uuid4()),
            run_tag=run_tag,
            mode=mode,
            status="pending",
            wall_clock_budget_s=int(budget_hours * 3600),
            program_snapshot=self._load_program(),
        )
        db.session.add(run)
        db.session.commit()

        # Clear any stale kill flag from a previous stop.
        self._set_kill(False)

        try:
            self._enqueue_execute_run(run.id)
        except Exception as e:
            run.status = "failed_precondition"
            run.halt_reason = f"celery_unreachable ({e.__class__.__name__})"
            run.ended_at = datetime.utcnow()
            run.report_md = self._write_report(
                run, [], precondition_failure=run.halt_reason,
            )
            db.session.commit()
            logger.error(f"Research run {run_tag}: {run.halt_reason}")
            return {"error": run.halt_reason, "run": run.to_dict()}

        logger.info(f"Research run {run_tag} kicked off ({trigger}, "
                    f"mode {mode}, budget {budget_hours:.1f}h) via celery")
        return {"status": "started", "run": run.to_dict()}

    # ---- director / code-tuning -----------------------------------------

    def _code_gate_error(self) -> str:
        """Why the code half cannot run, or empty string if allowed."""
        try:
            from backend.services.self_improvement_service import (
                _is_codebase_locked, _is_self_improvement_enabled,
            )
            if _is_codebase_locked():
                return "codebase_locked — code-tuning runs are forbidden"
            if not _is_self_improvement_enabled():
                return "self_improvement_disabled — enable it to allow code-tuning runs"
        except ImportError:
            return "safety gates unavailable — refusing code-tuning run"
        return ""

    def _swarm_reachable(self) -> bool:
        try:
            from backend.api.swarm_api import _proxy_get
            _data, status = _proxy_get("/swarm/status")
            return status < 500
        except Exception:
            return False

    def _diagnose(self, svc) -> dict:
        """Fail-soft probes. Never raises; never applies code."""
        d = {
            "rag_plateaued": False,
            "phase": 1,
            "plateau_count": 0,
            "baseline": 0.0,
            "code_allowed": True,
            "code_skip_reason": None,
            "swarm_up": False,
            "tests_red": False,
            "pending_fixes": 0,
        }
        try:
            cfg = svc._load_config()
            d["phase"] = cfg.get("phase", 1)
            d["plateau_count"] = int(cfg.get("phase_plateau_count") or 0)
            d["baseline"] = float(cfg.get("baseline_score") or 0.0)
            d["rag_plateaued"] = (
                d["plateau_count"] >= AUTORESEARCH_PHASE_PLATEAU_THRESHOLD
            )
        except Exception:
            pass
        gate = self._code_gate_error()
        d["swarm_up"] = self._swarm_reachable()
        if gate:
            d["code_allowed"] = False
            d["code_skip_reason"] = gate.split(" — ")[0] if " — " in gate else gate
        elif not d["swarm_up"]:
            d["code_allowed"] = False
            d["code_skip_reason"] = "swarm_unreachable"
        try:
            from backend.services.self_improvement_service import (
                get_self_improvement_service,
            )
            snap = get_self_improvement_service().snapshot_pytest()
            d["pytest"] = {
                k: snap.get(k) for k in
                ("ok", "skipped", "reason", "red", "failures", "return_code")
            }
            d["tests_red"] = bool(snap.get("red"))
            d["si_skipped"] = snap.get("skipped")
        except Exception as e:
            d["pytest"] = {"ok": False, "reason": e.__class__.__name__}
        try:
            from backend.models import PendingFix
            d["pending_fixes"] = PendingFix.query.filter_by(status="proposed").count()
        except Exception:
            pass
        return d

    def _allocate(self, diagnose: dict, budget_s: int) -> dict:
        """Split wall-clock. 70/30 unless RAG is plateaued (then 30/70).

        Measured against AUTORESEARCH_PHASE_PLATEAU_THRESHOLD consecutive
        discards (the 2026-08 overnight plateau) and swarm's RAM freeze-guard:
        if the code half cannot run, 100% of remaining budget stays on RAG.
        """
        si_preflight_s = 0
        remaining = max(0, int(budget_s))
        if not diagnose.get("code_allowed"):
            return {
                "rag_s": remaining, "code_s": 0,
                "si_preflight_s": si_preflight_s,
                "code_skip": diagnose.get("code_skip_reason") or "code_not_allowed",
            }
        if diagnose.get("rag_plateaued"):
            rag_frac, code_frac = 0.30, 0.70
        else:
            rag_frac, code_frac = 0.70, 0.30
        rag_s = int(remaining * rag_frac)
        code_s = max(0, remaining - rag_s)
        return {
            "rag_s": rag_s, "code_s": code_s,
            "si_preflight_s": si_preflight_s,
            "code_skip": None,
        }

    def _meta(self, run) -> dict:
        p = run.promotions
        return dict(p) if isinstance(p, dict) else {"candidate_ids": list(p or [])}

    def _save_meta(self, run, meta: dict) -> None:
        from backend.models import db
        run.promotions = meta
        db.session.commit()

    def _log_heal_row(self, run, diagnose: dict) -> None:
        try:
            from backend.models import ExperimentRun, db
            pytest_info = diagnose.get("pytest") or {}
            row = ExperimentRun(
                id=str(uuid.uuid4()),
                run_tag=run.run_tag,
                phase=0,
                parameter_changed="pytest_snapshot",
                old_value=None,
                new_value=f"failures={pytest_info.get('failures')}",
                hypothesis="SI analysis-only preflight (apply not invoked)",
                composite_score=0.0,
                baseline_score=run.baseline_score or 0.0,
                delta=0.0,
                status="discard" if diagnose.get("tests_red") else "keep",
                proposal_source="heal",
                retrieval_metrics={"layer": "heal", "pytest": pytest_info},
            )
            db.session.add(row)
            db.session.commit()
        except Exception:
            logger.debug("heal ledger row skipped", exc_info=True)

    def _launch_code_swarm(self, run_tag: str, diagnosis_text: str = "") -> dict:
        root = os.environ.get("GUAARDVARK_ROOT", "")
        template_path = os.path.join(
            root, "plugins", "swarm", "templates", "autoresearch-code-tuning.md"
        )
        try:
            with open(template_path, "r") as f:
                plan = f.read()
        except FileNotFoundError:
            return {"error": f"plan template missing: {template_path}"}
        plan = (plan
                .replace("{RUN_TAG}", run_tag)
                .replace("{DIAGNOSIS}", diagnosis_text or "(no diagnosis)"))
        plan_dir = os.path.join(root, "data", "autoresearch", "plans")
        os.makedirs(plan_dir, exist_ok=True)
        plan_path = os.path.join(plan_dir, f"{run_tag}.md")
        with open(plan_path, "w") as f:
            f.write(plan)
        try:
            from backend.api.swarm_api import _proxy_post
            data, status = _proxy_post("/swarm/launch", json_data={
                "plan_path": plan_path,
                "self_code": True,
                "auto_merge": False,
                "acknowledge_dirty_tree": False,
            })
            if status >= 400 or not isinstance(data, dict):
                return {"error": f"swarm launch failed (HTTP {status}): {data}"}
        except Exception as e:
            return {"error": f"swarm sidecar unreachable: {e}"}
        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        swarm_id = inner.get("swarm_id") or inner.get("id") or data.get("swarm_id")
        return {"swarm_id": swarm_id, "plan": plan}

    def _wait_swarm(self, swarm_id: str, deadline: float) -> str:
        if not swarm_id:
            return "no_swarm_id"
        try:
            from backend.api.swarm_api import _proxy_get
        except Exception:
            return "swarm_unreachable"
        while time.time() < deadline:
            if self._kill_requested():
                return "killed"
            try:
                data, status = _proxy_get(f"/swarm/status/{swarm_id}")
            except Exception:
                time.sleep(SWARM_POLL_S)
                continue
            payload = data.get("data") if isinstance(data, dict) else None
            if not isinstance(payload, dict):
                payload = data if isinstance(data, dict) else {}
            st = (payload.get("status") or "").lower()
            if st in ("completed", "done", "failed", "stopped", "error"):
                return st or "completed"
            if status == 404:
                return "completed"
            time.sleep(SWARM_POLL_S)
        return "budget_exhausted"

    def _stage_code_keeps(self, run) -> list:
        """Copy keep code-arms into PendingFix — human apply, never auto-merge."""
        from backend.models import ExperimentRun, PendingFix, db
        keeps = (
            ExperimentRun.query
            .filter_by(run_tag=run.run_tag, status="keep", proposal_source="code_arm")
            .all()
        )
        ids = []
        for k in keeps:
            diff = (
                f"autoresearch code keep ({run.run_tag})\n"
                f"parameter: {k.parameter_changed}\n"
                f"change: {k.new_value}\n"
                f"score: {k.composite_score} (baseline {k.baseline_score}, "
                f"delta {k.delta})\n"
                f"hypothesis: {k.hypothesis}\n"
                f"Review the run branch; do not apply until the diff is real.\n"
            )
            pf = PendingFix(
                file_path="(code-tuning arm — see swarm worktree / run branch)",
                proposed_diff=diff,
                original_content="",
                proposed_new_content="",
                fix_description=f"[autoresearch {run.run_tag}] {k.parameter_changed}",
                severity="medium",
                status="proposed",
            )
            db.session.add(pf)
            db.session.flush()
            ids.append(pf.id)
        if ids:
            db.session.commit()
        return ids

    def _run_code_slice(self, run, budget_s: int, diagnose: dict) -> str:
        if budget_s <= 0:
            return "code slice skipped (zero budget)"
        diagnosis_text = (
            f"RAG plateaued={diagnose.get('rag_plateaued')} "
            f"phase={diagnose.get('phase')} "
            f"plateau_count={diagnose.get('plateau_count')} "
            f"baseline={diagnose.get('baseline')} "
            f"tests_red={diagnose.get('tests_red')} "
            f"pending_fixes={diagnose.get('pending_fixes')}"
        )
        launched = self._launch_code_swarm(run.run_tag, diagnosis_text)
        if launched.get("error"):
            return f"code slice skipped: {launched['error']}"
        swarm_id = launched.get("swarm_id")
        meta = self._meta(run)
        meta["swarm_id"] = swarm_id
        self._save_meta(run, meta)
        halt = self._wait_swarm(swarm_id, time.time() + budget_s)
        staged = self._stage_code_keeps(run)
        meta = self._meta(run)
        meta["pending_fix_ids"] = staged
        self._save_meta(run, meta)
        return f"swarm {swarm_id} halt={halt}; staged {len(staged)} PendingFix(es)"

    # ---- execution -----------------------------------------------------

    def execute_run(self, run_id: str) -> None:
        from backend.models import ResearchRun, db
        from backend.services.rag_autoresearch_service import get_autoresearch_service

        run = db.session.get(ResearchRun, run_id)
        if run is None:
            logger.error(f"Research run {run_id} vanished before start")
            return
        svc = get_autoresearch_service()
        mode = run.mode or "rag_tuning"

        ok, reason = self._check_preconditions(svc)
        if not ok:
            run.status = "failed_precondition"
            run.halt_reason = reason
            run.ended_at = datetime.utcnow()
            run.report_md = self._write_report(run, [], precondition_failure=reason)
            db.session.commit()
            self._emit_run_complete(run)
            logger.error(f"Research run {run.run_tag}: {reason}")
            return

        run.status = "running"
        run.started_at = datetime.utcnow()
        db.session.commit()
        self._running_run_id = run_id

        t0 = time.time()
        budget = run.wall_clock_budget_s if run.wall_clock_budget_s is not None \
            else int(DEFAULT_BUDGET_HOURS * 3600)

        diagnose = {}
        split = {"rag_s": budget, "code_s": 0, "code_skip": None}
        if mode in ("unified", "code_tuning"):
            diagnose = self._diagnose(svc)
            if mode == "code_tuning":
                split = {
                    "rag_s": 0,
                    "code_s": budget,
                    "code_skip": None if diagnose.get("code_allowed")
                    else diagnose.get("code_skip_reason"),
                }
            else:
                split = self._allocate(diagnose, budget)
            meta = self._meta(run)
            meta["diagnose"] = diagnose
            meta["split"] = split
            self._save_meta(run, meta)
            if diagnose.get("tests_red") or diagnose.get("pytest"):
                self._log_heal_row(run, diagnose)

        ledger = []
        candidate_ids = []
        halt_reason = "budget_exhausted"
        status_at_end = "completed"
        code_note = None
        promotion_note = None

        if split.get("rag_s", 0) > 0 and mode != "code_tuning":
            ledger, candidate_ids, halt_reason, status_at_end = self._run_rag_slice(
                run, svc, t0, split["rag_s"],
            )
            if status_at_end == "failed_precondition":
                self._running_run_id = None
                return

        remaining = budget - (time.time() - t0)
        if (mode in ("unified", "code_tuning")
                and not split.get("code_skip")
                and remaining > 0
                and diagnose.get("code_allowed")):
            code_budget = min(int(split.get("code_s") or 0), max(0, int(remaining)))
            try:
                code_note = self._run_code_slice(run, code_budget, diagnose)
            except Exception as e:
                code_note = f"code slice failed: {e}"
                logger.warning(f"Run {run.run_tag} code slice failed: {e}")
        elif split.get("code_skip"):
            code_note = f"code half skipped: {split['code_skip']}"

        if status_at_end == "completed" or halt_reason in (
            "budget_exhausted", "plateaued",
        ):
            try:
                promotion_note = self._confirm_and_activate(
                    svc, run, candidate_ids=candidate_ids,
                )
            except Exception as e:
                promotion_note = f"confirmation failed: {e}"
                logger.warning(f"Run {run.run_tag} confirmation failed: {e}")

        notes = [n for n in (promotion_note, code_note) if n]
        run.status = status_at_end
        run.halt_reason = halt_reason
        run.ended_at = datetime.utcnow()
        run.report_md = self._write_report(
            run, ledger, promotion_note="; ".join(notes) if notes else None,
        )
        db.session.commit()
        self._running_run_id = None
        self._emit_run_complete(run)
        logger.info(f"Research run {run.run_tag} finished: {halt_reason}, "
                    f"{len(ledger)} experiments")

    def _run_rag_slice(self, run, svc, t0, budget_s):
        from backend.models import db

        ledger = []
        candidate_ids = []
        crash_streak = 0
        halt_reason = "budget_exhausted"
        status_at_end = "completed"

        cfg = svc._load_config()
        baseline = cfg.get("baseline_score") or 0.0
        if cfg.get("avg_pair_seconds") and not getattr(svc.eval_harness, "avg_pair_seconds", None):
            svc.eval_harness.avg_pair_seconds = float(cfg["avg_pair_seconds"])
        if not baseline:
            try:
                base_eval = svc.eval_harness.run_full_eval(dict(cfg.get("params", {})))
                baseline = base_eval.get("composite_score", 0.0)
                cfg["baseline_score"] = baseline
                if getattr(svc.eval_harness, "avg_pair_seconds", None):
                    cfg["avg_pair_seconds"] = round(svc.eval_harness.avg_pair_seconds, 2)
                svc._save_config(cfg)
            except Exception as e:
                run.status = "failed_precondition"
                run.halt_reason = f"baseline_eval_failed: {e}"
                run.ended_at = datetime.utcnow()
                run.report_md = self._write_report(run, [], precondition_failure=run.halt_reason)
                db.session.commit()
                self._emit_run_complete(run)
                return [], [], "baseline_eval_failed", "failed_precondition"
        run.baseline_score = baseline
        db.session.commit()

        while True:
            elapsed = time.time() - t0
            if elapsed >= budget_s:
                halt_reason = "budget_exhausted"
                break
            if len(ledger) >= HARD_ITERATION_CAP:
                halt_reason = "iteration_cap"
                break
            if self._kill_requested():
                halt_reason = "killed"
                status_at_end = "killed"
                break

            try:
                from backend.utils.gpu_check import gpu_busy
                if gpu_busy():
                    logger.info(f"Research run {run.run_tag}: GPU busy — yielding {GPU_YIELD_SLEEP_S}s")
                    time.sleep(GPU_YIELD_SLEEP_S)
                    continue
            except Exception:
                pass

            result = svc.run_single_experiment(run_tag=run.run_tag,
                                               promote_mode="candidate")
            if isinstance(result.get("retrieval_metrics"), dict):
                result["retrieval_metrics"].setdefault("layer", "params")
            elif result.get("retrieval_metrics") is None:
                result["retrieval_metrics"] = {"layer": "params"}
            ledger.append(result)
            if result.get("config_id"):
                candidate_ids.append(result["config_id"])
            run.experiments_completed = len(ledger)
            meta = self._meta(run)
            meta["candidate_ids"] = list(candidate_ids)
            run.promotions = meta
            best = max((float(r.get("composite_score") or 0.0) for r in ledger), default=0.0)
            run.best_score = max(best, baseline)
            db.session.commit()

            if result.get("status") == "crash":
                crash_streak += 1
                if crash_streak >= CONSECUTIVE_CRASH_HALT:
                    halt_reason = "consecutive_crashes"
                    status_at_end = "halted"
                    break
            else:
                crash_streak = 0

            cfg = svc._load_config()
            from backend.services.rag_experiment_agent import MAX_PHASE
            if (result.get("status") == "discard"
                    and cfg.get("phase", 1) >= MAX_PHASE
                    and cfg.get("phase_plateau_count", 0) >= AUTORESEARCH_PHASE_PLATEAU_THRESHOLD):
                halt_reason = "plateaued"
                break

            time.sleep(AUTORESEARCH_MIN_EXPERIMENT_INTERVAL)

        return ledger, candidate_ids, halt_reason, status_at_end

    # ---- confirmation (B5) --------------------------------------------

    def _confirm_and_activate(self, svc, run, candidate_ids=None) -> str:
        """A/B-confirm the best candidate from this run against the currently
        active config on a fresh eval; activate only a clear winner.

        Scoped to this run: candidate_ids collected from keep rows, else
        local candidates created after run.started_at. Family-broadcast
        leftovers are not considered.
        """
        from backend.models import ResearchConfig, db

        ids = list(candidate_ids or [])
        if not ids and isinstance(run.promotions, list):
            ids = [x for x in run.promotions if isinstance(x, str)]
        if not ids and isinstance(run.promotions, dict):
            ids = [x for x in (run.promotions.get("candidate_ids") or [])
                   if isinstance(x, str)]
        q = ResearchConfig.query.filter_by(status="candidate")
        if ids:
            q = q.filter(ResearchConfig.id.in_(ids))
        else:
            from sqlalchemy import or_
            q = q.filter(or_(
                ResearchConfig.source == "local",
                ResearchConfig.source.is_(None),
            ))
            if run.started_at is not None:
                q = q.filter(ResearchConfig.created_at >= run.started_at)
        candidates = q.order_by(ResearchConfig.composite_score.desc()).all()
        if not candidates:
            return "no candidate configs produced"
        best = candidates[0]

        active = ResearchConfig.query.filter_by(is_active=True).first()
        active_params = dict(active.params) if active else {}

        cand_eval = svc.eval_harness.run_full_eval(dict(best.params))
        base_eval = svc.eval_harness.run_full_eval(active_params)
        cand_score = cand_eval.get("composite_score", 0.0)
        base_score = base_eval.get("composite_score", 0.0)

        # Archive the comparison through the (previously dormant) A/B framework.
        try:
            from backend.utils.rag_evaluation_metrics import ABTestingFramework
            ab = ABTestingFramework()
            test_id = f"confirm-{run.run_tag}"
            ab.create_test(test_id, "active", "candidate",
                           description=f"Run-end confirmation for {run.run_tag}")
            ab.active_tests[test_id]["results_a"] = [{"metrics": {"composite": base_score}}]
            ab.active_tests[test_id]["results_b"] = [{"metrics": {"composite": cand_score}}]
            ab.complete_test(test_id)
        except Exception as e:
            logger.debug(f"A/B archive skipped: {e}")

        delta = round(cand_score - base_score, 4)
        losers = candidates[1:]
        for row in losers:
            row.status = "rejected"

        if delta >= CONFIRMATION_MIN_DELTA:
            if active is not None:
                active.is_active = False
                active.status = "superseded"
            best.is_active = True
            best.status = "promoted"
            best.promoted_at = datetime.utcnow()
            db.session.commit()
            from backend.utils.experiment_context import invalidate_active_params_cache
            invalidate_active_params_cache()
            run.promotions = [best.id]
            return (f"candidate CONFIRMED and activated: {cand_score:.3f} vs "
                    f"{base_score:.3f} (delta +{delta:.3f})")
        best.status = "rejected"
        db.session.commit()
        return (f"candidate NOT confirmed ({cand_score:.3f} vs {base_score:.3f}, "
                f"delta {delta:+.3f} < {CONFIRMATION_MIN_DELTA}) — nothing activated")

    # ---- preconditions / plumbing -------------------------------------

    def _check_preconditions(self, svc) -> tuple:
        """Fail loudly, never degrade. Returns (ok, reason)."""
        # 1. Ollama reachable? (The old loop degraded to random.choice noise.)
        try:
            import requests
            from backend.config import OLLAMA_BASE_URL
            resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
            if resp.status_code != 200:
                return False, f"ollama_unreachable (HTTP {resp.status_code})"
        except Exception as e:
            return False, f"ollama_unreachable ({e.__class__.__name__}) — start Ollama and re-kick"
        # 2. Corpus + eval pairs (same gates as the loop, surfaced with names).
        try:
            if not svc.eval_harness.has_sufficient_corpus():
                return False, "insufficient_corpus"
            if not svc.eval_harness._get_active_eval_pairs():
                return False, "no_eval_pairs — POST /api/autoresearch/eval-pairs/regenerate first"
        except Exception as e:
            return False, f"prerequisite_check_failed ({e})"
        return True, ""

    def _enqueue_execute_run(self, run_id: str) -> None:
        from backend.celery_app import celery
        celery.send_task(
            "autoresearch.execute_run",
            args=[run_id],
            time_limit=EXECUTE_RUN_TIME_LIMIT_S,
            soft_time_limit=EXECUTE_RUN_SOFT_LIMIT_S,
        )

    def _celery_has_live_execute_run(self):
        """True if a worker reports autoresearch.execute_run active.

        False if inspect succeeded and none are active. None if inspect failed
        (do not clobber a maybe-live 6h run).
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
                    if (t.get("name") or "") == "autoresearch.execute_run":
                        return True
            return False
        except Exception:
            return None

    def _recover_stale_runs(self) -> int:
        """Halt ResearchRun rows left `running`/`pending` after a dead worker.

        A live execute_run task (celery inspect) is left alone. If inspect is
        unavailable, only pending rows that never started and are older than
        2 minutes are reaped — a running 6h eval is not assumed dead.
        """
        from backend.models import ResearchRun, db
        live = self._celery_has_live_execute_run()
        q = ResearchRun.query.filter(ResearchRun.status.in_(("running", "pending")))
        if live is True:
            return 0
        if live is None:
            cutoff = datetime.utcnow() - timedelta(minutes=2)
            rows = q.filter(
                ResearchRun.started_at.is_(None),
                ResearchRun.created_at < cutoff,
            ).all()
        else:
            rows = q.all()
        n = 0
        for row in rows:
            row.status = "halted"
            row.halt_reason = "worker_crashed"
            row.ended_at = datetime.utcnow()
            n += 1
        if n:
            db.session.commit()
            logger.warning("Recovered %d stale research run(s)", n)
        return n

    def _kill_requested(self) -> bool:
        try:
            from backend.models import Setting
            s = Setting.query.filter_by(key="autoresearch_kill").first()
            return s is not None and str(s.value).strip().lower() == "true"
        except Exception:
            return False

    def _set_kill(self, value: bool) -> None:
        try:
            from backend.models import Setting, db
            s = Setting.query.filter_by(key="autoresearch_kill").first()
            if s:
                s.value = "true" if value else "false"
            else:
                db.session.add(Setting(key="autoresearch_kill",
                                       value="true" if value else "false"))
            db.session.commit()
        except Exception:
            pass

    def _load_program(self) -> str:
        root = os.environ.get("GUAARDVARK_ROOT", "")
        path = os.path.join(root, PROGRAM_PATH)
        try:
            with open(path, "r") as f:
                return f.read()
        except FileNotFoundError:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write(DEFAULT_PROGRAM)
            except Exception:
                pass
            return DEFAULT_PROGRAM

    def _finalize_crashed(self, run_id: str) -> None:
        try:
            from backend.models import ResearchRun, db
            run = db.session.get(ResearchRun, run_id)
            if run and run.status in ("pending", "running"):
                run.status = "halted"
                run.halt_reason = "worker_crashed"
                run.ended_at = datetime.utcnow()
                db.session.commit()
                self._emit_run_complete(run)
        except Exception:
            logger.exception("Failed to finalize crashed run")

    # ---- report --------------------------------------------------------

    def _write_report(self, run, ledger: list, promotion_note: str = None,
                      precondition_failure: str = None) -> str:
        """The morning report: what happened tonight, in plain markdown."""
        lines = [f"# Research Run — {run.run_tag}", ""]

        if precondition_failure:
            lines += [
                f"**DID NOT RUN**: `{precondition_failure}`",
                "",
                "Fix the precondition and kick off again — the run refused to "
                "start rather than produce noise (no random-fallback mode).",
            ]
            return "\n".join(lines)

        try:
            from backend.models import ExperimentRun
            extra = ExperimentRun.query.filter_by(run_tag=run.run_tag).all()
            seen = {r.get("experiment_id") or r.get("id") for r in ledger}
            merged = list(ledger)
            for row in extra:
                if row.id not in seen:
                    d = row.to_dict()
                    d["parameter"] = d.get("parameter_changed")
                    merged.append(d)
            ledger = merged
        except Exception:
            pass

        keeps = [r for r in ledger if r.get("status") == "keep"]
        discards = [r for r in ledger if r.get("status") == "discard"]
        crashes = [r for r in ledger if r.get("status") == "crash"]
        llm_props = [r for r in ledger if r.get("proposal_source") == "llm"]
        tpe_props = [r for r in ledger if r.get("proposal_source") == "tpe"]
        code_props = [r for r in ledger if r.get("proposal_source") == "code_arm"]
        heal_props = [r for r in ledger if r.get("proposal_source") == "heal"]
        f0 = [r for r in ledger if r.get("fidelity") == 0]

        best = run.best_score or 0.0
        base = run.baseline_score or 0.0
        lines += [
            f"**Headline**: baseline {base:.3f} → best {best:.3f} "
            f"({'+' if best >= base else ''}{best - base:.3f}) over "
            f"{len(ledger)} experiments ({len(keeps)} keep / {len(discards)} discard / "
            f"{len(crashes)} crash; {len(code_props)} code-arm / {len(heal_props)} heal). "
            f"Halt: `{run.halt_reason}`.",
            "",
            f"**Promotion**: {promotion_note or 'n/a'}",
            "",
        ]

        meta = self._meta(run) if run is not None else {}
        diagnose = meta.get("diagnose") or {}
        split = meta.get("split") or {}
        if diagnose or split:
            skip = split.get("code_skip")
            lines += [
                "**Director**:",
                f"- mode `{run.mode}`",
                f"- RAG plateaued: {diagnose.get('rag_plateaued')} "
                f"(phase {diagnose.get('phase')}, "
                f"discards {diagnose.get('plateau_count')})",
                f"- budget split: RAG {split.get('rag_s', '—')}s / "
                f"code {split.get('code_s', '—')}s"
                + (f" — code skipped `{skip}`" if skip else ""),
                f"- pytest red: {diagnose.get('tests_red')} "
                f"(apply not invoked)",
                f"- swarm: {meta.get('swarm_id') or 'n/a'}; "
                f"PendingFixes staged: {len(meta.get('pending_fix_ids') or [])}",
                "",
            ]

        if ledger:
            n = len(ledger)
            tpe_pct = len(tpe_props) / n * 100.0
            llm_pct = len(llm_props) / n * 100.0
            rand_pct = 100.0 - tpe_pct - llm_pct
            lines.append(
                f"**Proposal quality**: {tpe_pct:.0f}% TPE, {llm_pct:.0f}% LLM, "
                f"{rand_pct:.0f}% random fallback."
            )
            if f0:
                lines.append(
                    f"**Fidelity**: {len(f0)}/{n} discarded at F0 (retrieval screen, "
                    "no LLM judge)."
                )
            judge_models = {r.get("judge_model") for r in ledger if r.get("judge_model")}
            prop_models = {r.get("proposer_model") for r in ledger if r.get("proposer_model")}
            if judge_models and judge_models == prop_models:
                lines.append("**⚠ single-model judging**: proposer and judge ran on the "
                             "same model — scores carry self-confirmation bias. "
                             "Configure `autoresearch_judge_model` in Settings.")
            lines.append("")
            lines.append("| # | parameter | change | delta | status | source |")
            lines.append("|---|-----------|--------|-------|--------|--------|")
            for i, r in enumerate(ledger, 1):
                lines.append(
                    f"| {i} | {r.get('parameter')} | {r.get('old_value')} → "
                    f"{r.get('new_value')} | {r.get('delta', 0):+.3f} | "
                    f"{r.get('status')} | {r.get('proposal_source', '?')} |"
                )
            lines.append("")

            retr = [r.get("retrieval_metrics") for r in ledger if r.get("retrieval_metrics")]
            if retr:
                first_hit = retr[0].get("hit_rate_at_k", 0)
                last_hit = retr[-1].get("hit_rate_at_k", 0)
                lines.append(f"**Retrieval**: hit-rate {first_hit:.2f} → {last_hit:.2f} "
                             f"across the run (per-experiment values in the ledger).")
                lines.append("")

        if crashes:
            lines.append("**Crash log**:")
            for r in crashes:
                lines.append(f"- {r.get('parameter')}={r.get('new_value')}")
            lines.append("")

        return "\n".join(lines)

    def _emit_run_complete(self, run) -> None:
        try:
            from backend.socketio_instance import socketio
            socketio.emit("autoresearch:run_complete", run.to_dict())
        except Exception:
            pass


_research_run_service = None


def get_research_run_service() -> ResearchRunService:
    global _research_run_service
    if _research_run_service is None:
        _research_run_service = ResearchRunService()
    return _research_run_service
