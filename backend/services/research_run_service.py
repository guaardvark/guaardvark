"""Research Run engine — bounded overnight autoresearch with a morning report.

Karpathy-autoresearch DNA: a run has a tag, a fixed wall-clock budget, a
ledger (ExperimentRun rows stamped with the run_tag), keep/discard per
experiment, a frozen research-program snapshot, and a report you read in the
morning. Guardrails (all inherited from the 2026-08 runaway postmortem):

- Preconditions fail LOUDLY (`failed_precondition` + report) — the run never
  degrades into random-proposal noise because Ollama was off or the eval set
  was empty.
- Wall-clock hard cap + iteration hard cap + pacing floor.
- Cross-process kill flag (`autoresearch_kill` Setting) checked every cycle
  and before every model call.
- Runs on its own worker (backend/services/research_worker.py), never the main
  one. GPU politeness: every model call first waits while a render or training
  job holds the GPU (counted against the run budget, not the experiment's), and
  experiments also yield while the GPU is busy.
- Nightly winners are stored as CANDIDATE configs; only the run-end
  confirmation eval (candidate vs currently-active, same eval set) activates
  the best one. No single-lucky-eval promotions.
"""
import functools
import logging
import os
import time
import uuid
from datetime import timedelta

from backend.config import (
    AUTORESEARCH_KEEP_MIN_DELTA,
    AUTORESEARCH_MIN_EXPERIMENT_INTERVAL,
    AUTORESEARCH_PHASE_PLATEAU_THRESHOLD,
)
from backend.services.rag_eval_harness import same_model
from backend.utils.clock import utcnow

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
HALT_REASON_MAX = 200  # ResearchRun.halt_reason column width
# A pending run whose task no worker reports yet may be waiting for the research
# worker to boot, or behind it in the queue; it is reaped only once this old.
PENDING_REAP_AFTER = timedelta(minutes=2)


class ResearchRunStopped(BaseException):
    """The run was stopped; raised in place of its next model call.

    A BaseException so the eval and experiment code, which records any
    Exception as a crashed experiment, lets it through to the run loop; it is
    caught there and inside execute_run, never by Celery.
    """


def _clip(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---- ledger reading ------------------------------------------------------
# Rows in one ExperimentRun ledger are of three kinds: parameter experiments
# (the RAG loop, measured by the eval harness), health checks (the pytest
# snapshot, proposal_source "heal") and self-reported rows (code-tuning arms
# and anything else posted to /experiments, which the harness did not score).

def is_health_check(row: dict) -> bool:
    return row.get("proposal_source") == "heal"


def is_self_reported(row: dict) -> bool:
    metrics = row.get("retrieval_metrics") or {}
    return bool(metrics.get("self_reported")) or row.get("proposal_source") == "code_arm"


def is_parameter_experiment(row: dict) -> bool:
    return not is_health_check(row) and not is_self_reported(row)


def measured_score(row: dict):
    """The judged composite of a parameter experiment, or None when the row
    measured nothing: a crash, a health check, a self-reported row, or an F0
    screen (which records the baseline as its score without judging)."""
    if not is_parameter_experiment(row) or row.get("status") == "crash":
        return None
    metrics = row.get("retrieval_metrics") or {}
    fidelity = row.get("fidelity", metrics.get("fidelity"))
    judged = metrics.get("judged_pairs")
    if fidelity is None and judged is None:
        # Rows from before fidelity was recorded: an F0 screen kept no details.
        if not row.get("eval_details"):
            return None
    elif (fidelity is not None and int(fidelity) < 1) or judged == 0:
        return None
    score = row.get("composite_score")
    return float(score) if score is not None else None


def summarize_ledger(rows: list) -> dict:
    """Counts and measured scores for one run's ledger rows, oldest first.

    latest_score is the newest measured experiment and may be below the
    baseline; best_tried_score is the best measured one. Both are None when
    nothing was measured.
    """
    scores = [s for s in (measured_score(r) for r in rows) if s is not None]
    return {
        "experiments": sum(1 for r in rows if is_parameter_experiment(r)),
        "measured_experiments": len(scores),
        "latest_score": scores[-1] if scores else None,
        "best_tried_score": max(scores) if scores else None,
        "health_checks": sum(1 for r in rows if is_health_check(r)),
        "self_reported": sum(1 for r in rows if is_self_reported(r)),
    }


class ResearchRunService:
    """Creates and executes bounded research runs."""

    def __init__(self):
        self._running_run_id = None

    # ---- kickoff -------------------------------------------------------

    def kickoff(self, mode: str = "unified", budget_hours: float = None,
                trigger: str = "manual") -> dict:
        """Create a ResearchRun row and enqueue the Celery owner task.

        mode: unified (default) | rag_tuning | code_tuning
        trigger: manual | nightly. Stored on the row; nightly runs also
        require an independent judge model.

        Preconditions are checked here, before anything is queued. A refused
        run is recorded as `failed_precondition` with every reason, and the
        result carries `not_run: True` (HTTP 422 at the API).
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
                return {"error": gate, "not_run": True, "reasons": [gate]}

        run_tag = f"{mode}-{utcnow().strftime('%Y%m%d-%H%M%S')}"
        if ResearchRun.query.filter_by(run_tag=run_tag).first() is not None:
            # A refused run keeps its row, so a retry in the same second needs its own tag.
            run_tag = f"{run_tag}-{uuid.uuid4().hex[:4]}"
        run = ResearchRun(
            id=str(uuid.uuid4()),
            run_tag=run_tag,
            mode=mode,
            status="pending",
            wall_clock_budget_s=int(budget_hours * 3600),
            program_snapshot=self._load_program(),
            promotions={"trigger": trigger},
        )
        db.session.add(run)
        db.session.commit()

        from backend.services.rag_autoresearch_service import get_autoresearch_service
        reasons, warnings = self._precondition_failures(
            get_autoresearch_service(), trigger=trigger,
        )
        if reasons:
            self._refuse(run, reasons, warnings)
            logger.warning(f"Research run {run_tag} not run ({trigger}): "
                           f"{'; '.join(reasons)}")
            return {
                "error": "; ".join(reasons),
                "not_run": True,
                "reasons": reasons,
                "warnings": warnings,
                "run": run.to_dict(),
            }
        if warnings:
            meta = self._meta(run)
            meta["warnings"] = warnings
            self._save_meta(run, meta)

        # Clear any stale kill flag from a previous stop.
        self._set_kill(False)

        from backend.services.research_worker import WorkerStartError
        try:
            self._enqueue_execute_run(run.id)
        except WorkerStartError as e:
            reason = f"research_worker_unavailable — {e}"
            self._refuse(run, [reason], warnings)
            logger.error(f"Research run {run_tag}: {reason}")
            return {"error": reason, "not_run": True, "reasons": [reason],
                    "run": run.to_dict()}
        except Exception as e:
            reason = f"celery_unreachable ({e.__class__.__name__})"
            self._refuse(run, [reason], warnings)
            logger.error(f"Research run {run_tag}: {reason}")
            return {"error": reason, "not_run": True, "reasons": [reason],
                    "run": run.to_dict()}

        logger.info(f"Research run {run_tag} kicked off ({trigger}, "
                    f"mode {mode}, budget {budget_hours:.1f}h) via celery")
        return {"status": "started", "run": run.to_dict(), "warnings": warnings}

    def _refuse(self, run, reasons: list, warnings: list = None) -> None:
        """Close a run that did not start: every reason in the report, the
        first ones in halt_reason (the column holds 200 characters)."""
        from backend.models import db
        meta = self._meta(run)
        meta["precondition_reasons"] = list(reasons)
        if warnings:
            meta["warnings"] = list(warnings)
        meta.pop("current", None)
        run.promotions = meta
        run.status = "failed_precondition"
        run.halt_reason = _clip("; ".join(reasons), HALT_REASON_MAX)
        run.ended_at = utcnow()
        run.report_md = self._write_report(
            run, [], precondition_failure=reasons, warnings=warnings,
        )
        db.session.commit()
        self._emit_run_complete(run)

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
        """Record the pytest snapshot as a health check. Its status is
        pass/fail/skipped, never keep/discard: it is not an experiment."""
        try:
            from backend.models import ExperimentRun, db
            pytest_info = diagnose.get("pytest") or {}
            if diagnose.get("tests_red"):
                status = "fail"
            elif pytest_info.get("skipped"):
                status = "skipped"
            else:
                status = "pass" if pytest_info.get("ok") else "fail"
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
                status=status,
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
            if st in ("completed", "done", "failed", "stopped", "error", "cancelled"):
                return st or "completed"
            if status == 404:
                # The swarm service no longer knows this swarm (it restarted):
                # the outcome is unknown, which is not "completed".
                return "error"
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
        # The swarm's arms run the eval harness and models of their own; they
        # start only when no render or training holds the GPU, and never once
        # the run has been stopped.
        self._gpu_turn(run)
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
        if run.status != "pending":
            # A redelivered task (the broker re-sends an unacked task after its
            # visibility timeout) must not run a finished run a second time.
            logger.warning(f"Research run {run.run_tag} is {run.status}, not pending; "
                           "not executing it again")
            return
        svc = get_autoresearch_service()
        mode = run.mode or "rag_tuning"
        trigger = self._meta(run).get("trigger") or "manual"

        ok, reason = self._check_preconditions(svc, trigger=trigger)
        if not ok:
            self._refuse(run, reason.split("; "))
            logger.error(f"Research run {run.run_tag}: {reason}")
            return

        # Claim the row: of two deliveries racing here, only one moves it on.
        claimed = (
            ResearchRun.query
            .filter_by(id=run_id, status="pending")
            .update({"status": "running", "started_at": utcnow()},
                    synchronize_session=False)
        )
        db.session.commit()
        if not claimed:
            logger.warning(f"Research run {run.run_tag} was claimed by another worker")
            return
        db.session.refresh(run)
        self._running_run_id = run_id
        turn = functools.partial(self._gpu_turn, run)
        svc.eval_harness.gpu_turn = turn
        svc.agent.gpu_turn = turn
        try:
            self._execute_claimed(run, svc, mode)
        except ResearchRunStopped:
            # The run's steps handle a stop where it can happen; one raised
            # anywhere else still closes the run as killed.
            run.status = "killed"
            run.halt_reason = "killed"
            run.ended_at = utcnow()
            run.report_md = self._write_report(run, [], promotion_note="the run was stopped")
            db.session.commit()
            self._emit_run_complete(run)
        finally:
            svc.eval_harness.gpu_turn = None
            svc.agent.gpu_turn = None
            self._running_run_id = None

    def _gpu_turn(self, run) -> float:
        """Every model call of the run waits here while a render or training
        job holds the GPU. Raises ResearchRunStopped once the run is stopped."""
        from backend.utils.gpu_check import wait_for_gpu_turn

        def on_wait(reason):
            logger.info(f"Research run {run.run_tag}: waiting for the GPU ({reason})")

        waited = wait_for_gpu_turn(should_stop=self._kill_requested, on_wait=on_wait)
        if waited is None:
            raise ResearchRunStopped(run.run_tag)
        if waited >= 1:
            logger.info(f"Research run {run.run_tag}: GPU free after {waited:.0f}s")
            meta = self._meta(run)
            meta["gpu_wait_s"] = round(float(meta.get("gpu_wait_s") or 0) + waited)
            self._save_meta(run, meta)
        return waited

    def _execute_claimed(self, run, svc, mode: str) -> None:
        from backend.models import db

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
                return

        remaining = budget - (time.time() - t0)
        if (mode in ("unified", "code_tuning")
                and not split.get("code_skip")
                and remaining > 0
                and diagnose.get("code_allowed")):
            code_budget = min(int(split.get("code_s") or 0), max(0, int(remaining)))
            try:
                code_note = self._run_code_slice(run, code_budget, diagnose)
            except ResearchRunStopped:
                code_note = "code half skipped: the run was stopped"
                halt_reason, status_at_end = "killed", "killed"
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
            except ResearchRunStopped:
                promotion_note = "confirmation not run: the run was stopped"
                halt_reason, status_at_end = "killed", "killed"
            except Exception as e:
                promotion_note = f"confirmation failed: {e}"
                logger.warning(f"Run {run.run_tag} confirmation failed: {e}")

        notes = [n for n in (promotion_note, code_note) if n]
        run.status = status_at_end
        run.halt_reason = halt_reason
        run.ended_at = utcnow()
        run.report_md = self._write_report(
            run, ledger, promotion_note="; ".join(notes) if notes else None,
        )
        db.session.commit()
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

        if self._kill_requested():
            return [], [], "killed", "killed"

        # Resolve judge and answer models from tonight's settings, not from
        # whatever this worker cached on an earlier run.
        svc.eval_harness.reset_models()
        cfg = svc._load_config()
        if cfg.get("avg_pair_seconds") and not getattr(svc.eval_harness, "avg_pair_seconds", None):
            svc.eval_harness.avg_pair_seconds = float(cfg["avg_pair_seconds"])
        try:
            measured = self._measure_baseline(svc, cfg)
        except ResearchRunStopped:
            return [], [], "killed", "killed"
        except Exception as e:
            self._refuse(run, [_clip(f"baseline_eval_failed: {e}", HALT_REASON_MAX)])
            return [], [], "baseline_eval_failed", "failed_precondition"
        baseline = measured["score"]
        run.baseline_score = baseline
        meta = self._meta(run)
        meta["baseline"] = measured
        try:
            judge = svc.eval_harness.judge_status()
            if isinstance(judge, dict):
                meta["judge"] = judge
        except Exception:
            pass
        self._save_meta(run, meta)

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

            # Job order on: hold between experiments while a chat or voice turn
            # runs. Like the GPU wait below, the held time is the run's budget.
            from backend.services import job_arbiter
            job_arbiter.yield_point("autoresearch", run.run_tag, label=f"Autoresearch run {run.run_tag}",
                                    should_stop=self._kill_requested)
            if self._kill_requested():
                continue

            try:
                from backend.utils.gpu_check import gpu_busy
                if gpu_busy():
                    logger.info(f"Research run {run.run_tag}: GPU busy — yielding {GPU_YIELD_SLEEP_S}s")
                    time.sleep(GPU_YIELD_SLEEP_S)
                    continue
            except Exception:
                pass

            try:
                result = svc.run_single_experiment(
                    run_tag=run.run_tag, promote_mode="candidate",
                    on_proposal=lambda proposal: self._note_current(run, proposal),
                )
            except ResearchRunStopped:
                # Stopped mid-experiment: nothing was decided, so nothing is logged.
                svc._current_experiment_id = None
                svc._current_parameter = None
                halt_reason = "killed"
                status_at_end = "killed"
                break
            if isinstance(result.get("retrieval_metrics"), dict):
                result["retrieval_metrics"].setdefault("layer", "params")
            elif result.get("retrieval_metrics") is None:
                result["retrieval_metrics"] = {"layer": "params"}
            ledger.append(result)
            if result.get("config_id"):
                candidate_ids.append(result["config_id"])
            run.experiments_completed = len(ledger)
            summary = summarize_ledger(ledger)
            meta = self._meta(run)
            meta["candidate_ids"] = list(candidate_ids)
            meta["latest_score"] = summary["latest_score"]
            meta["best_tried_score"] = summary["best_tried_score"]
            meta["measured_experiments"] = summary["measured_experiments"]
            run.promotions = meta
            # Best measured experiment, or NULL; never the baseline standing in.
            run.best_score = summary["best_tried_score"]
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

        meta = self._meta(run)
        if meta.pop("current", None) is not None:
            self._save_meta(run, meta)
        return ledger, candidate_ids, halt_reason, status_at_end

    def _note_current(self, run, proposal: dict) -> None:
        """Record the change under test in the run metadata, where /status in
        the web process can read it while the worker measures it."""
        meta = self._meta(run)
        meta["current"] = {
            "parameter": proposal.get("parameter"),
            "new_value": str(proposal.get("new_value")),
            "started_at": utcnow().isoformat(),
        }
        self._save_meta(run, meta)

    def _measure_baseline(self, svc, cfg: dict) -> dict:
        """Score the current params on the full active eval set, now.

        Every run measures its own baseline: a stored score may come from
        another judge, another eval set or another corpus. Saves the score,
        when it was measured and on which eval generation into the config,
        and resets the plateau count, since earlier discards were judged
        against a different number. Raises when nothing was measured.
        """
        harness = svc.eval_harness
        harness.begin_experiment_budget(duration_s=svc._experiment_deadline_seconds(cfg))
        result = harness.run_full_eval(dict(cfg.get("params") or {}))
        pairs = int(result.get("num_pairs") or 0)
        if pairs == 0:
            raise RuntimeError("no eval pairs were measured")
        if result.get("parse_fail_crash"):
            raise RuntimeError(f"judge_parse_fail_ratio={result.get('parse_fail_ratio')}")
        try:
            generations = sorted({
                p.get("eval_generation_id") for p in harness._get_active_eval_pairs()
                if p.get("eval_generation_id")
            })
        except Exception:
            generations = []
        measured = {
            "score": float(result.get("composite_score") or 0.0),
            "measured_at": utcnow().isoformat(),
            "eval_generation": ",".join(generations) or None,
            "pairs": pairs,
            "judged_pairs": result.get("judged_pairs"),
        }
        cfg["baseline_score"] = measured["score"]
        cfg["baseline_measured_at"] = measured["measured_at"]
        cfg["baseline_eval_generation"] = measured["eval_generation"]
        cfg["baseline_pairs"] = pairs
        cfg["phase_plateau_count"] = 0
        if getattr(harness, "avg_pair_seconds", None):
            cfg["avg_pair_seconds"] = round(harness.avg_pair_seconds, 2)
        svc._save_config(cfg)
        return measured

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

        # Rows hold only the tuned params. Spelled out in full, each eval
        # measures what that row would serve live, not the row on top of the
        # active one.
        # Each eval gets a fresh budget; the last experiment's may be spent.
        deadline = svc._experiment_deadline_seconds({})
        svc.eval_harness.begin_experiment_budget(duration_s=deadline)
        cand_eval = svc.eval_harness.run_full_eval(svc._full_params(dict(best.params)))
        svc.eval_harness.begin_experiment_budget(duration_s=deadline)
        base_eval = svc.eval_harness.run_full_eval(svc._full_params(active_params))
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
            best.promoted_at = utcnow()
            db.session.commit()
            from backend.utils.experiment_context import invalidate_active_params_cache
            invalidate_active_params_cache()
            meta = self._meta(run)
            meta["promoted_ids"] = [best.id]
            run.promotions = meta
            return (f"candidate CONFIRMED and activated: {cand_score:.3f} vs "
                    f"{base_score:.3f} (delta +{delta:.3f})")
        best.status = "rejected"
        db.session.commit()
        return (f"candidate NOT confirmed ({cand_score:.3f} vs {base_score:.3f}, "
                f"delta {delta:+.3f} < {CONFIRMATION_MIN_DELTA}) — nothing activated")

    # ---- preconditions / plumbing -------------------------------------

    def _precondition_failures(self, svc, trigger: str = "manual") -> tuple:
        """Every reason a run cannot start, and warnings that do not stop it.

        Returns (reasons, warnings), each a list of "code — explanation"
        strings. Fail loudly, never degrade: a run on these conditions only
        produces noise. The judge check refuses nightly runs and warns on
        manual ones.
        """
        reasons, warnings = [], []
        # 1. Ollama reachable? (The old loop degraded to random.choice noise.)
        try:
            import requests
            from backend.config import OLLAMA_BASE_URL
            resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
            if resp.status_code != 200:
                reasons.append(f"ollama_unreachable (HTTP {resp.status_code})")
        except Exception as e:
            reasons.append(f"ollama_unreachable ({e.__class__.__name__}) — start Ollama and re-kick")
        # 2. Corpus and eval pairs, each surfaced with its own name.
        harness = svc.eval_harness
        try:
            if not harness.has_sufficient_corpus():
                from backend.config import AUTORESEARCH_MIN_CORPUS_SIZE
                reasons.append(
                    f"insufficient_corpus — {harness.text_document_count()} indexed text "
                    f"documents, {AUTORESEARCH_MIN_CORPUS_SIZE} needed")
        except Exception as e:
            reasons.append(f"prerequisite_check_failed ({e})")
        try:
            sources = harness.eval_source_status()
            active = int(sources.get("active") or 0)
            not_indexed = int(sources.get("not_indexed") or 0)
            if active == 0:
                reasons.append("no_eval_pairs — regenerate eval pairs first")
            elif not_indexed:
                by_status = ", ".join(
                    f"{status}: {n}" for status, n in sorted(sources["by_status"].items())
                    if status != "INDEXED")
                reasons.append(
                    f"eval_sources_not_indexed — {not_indexed} of {active} active eval "
                    f"pairs come from documents that are not indexed ({by_status}) — "
                    "index them or regenerate")
        except Exception as e:
            reasons.append(f"prerequisite_check_failed ({e})")
        # 3. A model grading its own answers.
        try:
            judge = harness.judge_status()
        except Exception:
            judge = {}
        problem = judge.get("problem")
        if problem:
            answer = judge.get("answer_model") or "the active model"
            text = (
                f"judge_unset — no autoresearch judge model is set, so {answer} grades its own answers"
                if problem == "judge_unset" else
                f"judge_same_as_answer_model — the judge model is {answer}, which also writes the answers"
            )
            (reasons if trigger == "nightly" else warnings).append(text)
        return reasons, warnings

    def _check_preconditions(self, svc, trigger: str = "manual") -> tuple:
        """Returns (ok, reason) with every failing reason joined by '; '."""
        reasons, _warnings = self._precondition_failures(svc, trigger=trigger)
        return (not reasons), "; ".join(reasons)

    def _enqueue_execute_run(self, run_id: str) -> None:
        """Queue the run for the research worker, starting that worker if needed."""
        from backend.celery_app import celery
        from backend.services import research_worker

        research_worker.dispatch(lambda: celery.send_task(
            "autoresearch.execute_run",
            args=[run_id],
            queue=research_worker.QUEUE,
            time_limit=EXECUTE_RUN_TIME_LIMIT_S,
            soft_time_limit=EXECUTE_RUN_SOFT_LIMIT_S,
        ))

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

        A live execute_run task (celery inspect) is left alone. A pending row
        that never started is reaped only once older than PENDING_REAP_AFTER:
        until then its task may be waiting for the research worker to boot. If
        inspect is unavailable, only those old pending rows are reaped; a
        running 6h eval is not assumed dead.
        """
        from backend.models import ResearchRun, db
        from sqlalchemy import or_
        live = self._celery_has_live_execute_run()
        q = ResearchRun.query.filter(ResearchRun.status.in_(("running", "pending")))
        if live is True:
            return 0
        old_pending = (
            ResearchRun.started_at.is_(None)
            & (ResearchRun.created_at < utcnow() - PENDING_REAP_AFTER)
        )
        if live is None:
            rows = q.filter(old_pending).all()
        else:
            rows = q.filter(or_(ResearchRun.status == "running", old_pending)).all()
        n = 0
        for row in rows:
            row.status = "halted"
            row.halt_reason = "worker_crashed"
            row.ended_at = utcnow()
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
                run.ended_at = utcnow()
                db.session.commit()
                self._emit_run_complete(run)
        except Exception:
            logger.exception("Failed to finalize crashed run")

    # ---- report --------------------------------------------------------

    def _write_report(self, run, ledger: list, promotion_note: str = None,
                      precondition_failure=None, warnings: list = None) -> str:
        """The morning report: what happened tonight, in plain markdown.

        precondition_failure: a reason string or a list of them; the report
        then says the run did not run and lists every reason.
        """
        lines = [f"# Research Run — {run.run_tag}", ""]

        if precondition_failure:
            reasons = (precondition_failure if isinstance(precondition_failure, (list, tuple))
                       else [precondition_failure])
            lines += ["**DID NOT RUN**:", ""]
            lines += [f"- `{r}`" for r in reasons]
            lines += [
                "",
                "Fix these and start again. The run refused to start rather "
                "than produce scores that measure nothing.",
            ]
            if warnings:
                lines += ["", "**Warnings**:", ""] + [f"- {w}" for w in warnings]
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

        param_rows = [r for r in ledger if is_parameter_experiment(r)]
        code_rows = [r for r in ledger if is_self_reported(r)]
        heal_rows = [r for r in ledger if is_health_check(r)]
        keeps = [r for r in param_rows if r.get("status") == "keep"]
        discards = [r for r in param_rows if r.get("status") == "discard"]
        crashes = [r for r in param_rows if r.get("status") == "crash"]
        summary = summarize_ledger(param_rows)
        meta = self._meta(run)
        baseline_info = meta.get("baseline") if isinstance(meta.get("baseline"), dict) else {}

        base = run.baseline_score
        latest, best = summary["latest_score"], summary["best_tried_score"]

        def score(x):
            return "—" if x is None else f"{x:.3f}"

        def change(x):
            return "" if x is None or base is None else f" ({x - base:+.3f})"

        measured_at = baseline_info.get("measured_at")
        head = f"**Headline**: baseline {score(base)}" + (
            f" (measured {measured_at})" if measured_at else " (not measured by this run)")
        if summary["measured_experiments"]:
            head += (f" → latest {score(latest)}{change(latest)}; best tried "
                     f"{score(best)}{change(best)} over {summary['measured_experiments']} "
                     "measured experiment(s)")
        else:
            head += "; no experiment was measured"
        head += (f" ({len(keeps)} keep / {len(discards)} discard / {len(crashes)} crash "
                 f"of {len(param_rows)} tried)")
        if heal_rows:
            head += f"; {len(heal_rows)} health check(s) not counted"
        head += f". Halt: `{run.halt_reason}`."
        lines += [head, "", f"**Promotion**: {promotion_note or 'n/a'}", ""]

        if meta.get("warnings"):
            lines += ["**Warnings**:", ""] + [f"- {w}" for w in meta["warnings"]] + [""]

        if meta.get("gpu_wait_s"):
            waited = int(meta["gpu_wait_s"])
            span = f"{waited} s" if waited < 120 else f"{waited // 60} min"
            lines += [f"**GPU**: waited {span} for renders or training to finish; "
                      "that time counts against the run's budget.", ""]

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

        judge = meta.get("judge") if isinstance(meta.get("judge"), dict) else {}
        single = judge.get("independent") is False
        for r in param_rows:
            answer = (r.get("retrieval_metrics") or {}).get("answer_model")
            if same_model(r.get("judge_model"), answer):
                single = True
        if single:
            judged_by = judge.get("configured") or next(
                (r.get("judge_model") for r in param_rows if r.get("judge_model")), None)
            lines += [
                "**⚠ single-model judging**: the model that answered the eval "
                f"questions also graded them (judge: {judged_by or 'not set'}) — "
                "scores carry self-confirmation bias. Set `autoresearch_judge_model` "
                "to a different model; nightly runs refuse without one.",
                "",
            ]

        if param_rows:
            n = len(param_rows)
            tpe_pct = sum(1 for r in param_rows if r.get("proposal_source") == "tpe") / n * 100.0
            llm_pct = sum(1 for r in param_rows if r.get("proposal_source") == "llm") / n * 100.0
            rand_pct = 100.0 - tpe_pct - llm_pct
            lines.append(
                f"**Proposal quality** (parameter experiments): {tpe_pct:.0f}% TPE, "
                f"{llm_pct:.0f}% LLM, {rand_pct:.0f}% random fallback."
            )
            f0 = [r for r in param_rows
                  if r.get("fidelity", (r.get("retrieval_metrics") or {}).get("fidelity")) == 0]
            if f0:
                lines.append(
                    f"**Fidelity**: {len(f0)}/{n} discarded at F0 (retrieval screen, "
                    "no LLM judge; not counted as measured)."
                )
            lines.append("")
            lines.append("| # | parameter | change | score | delta | status | source |")
            lines.append("|---|-----------|--------|-------|-------|--------|--------|")
            for i, r in enumerate(param_rows, 1):
                lines.append(
                    f"| {i} | {r.get('parameter')} | {r.get('old_value')} → "
                    f"{r.get('new_value')} | {score(measured_score(r))} | "
                    f"{(r.get('delta') or 0):+.3f} | {r.get('status')} | "
                    f"{r.get('proposal_source', '?')} |"
                )
            lines.append("")

            retr = [m for m in ((r.get("retrieval_metrics") or {}) for r in param_rows)
                    if m.get("hit_rate_at_k") is not None]
            if retr:
                lines.append(
                    f"**Retrieval**: hit-rate {retr[0]['hit_rate_at_k']:.2f} → "
                    f"{retr[-1]['hit_rate_at_k']:.2f} across {len(retr)} experiment(s) that "
                    "scored retrieval. A pair counts as a hit when any returned chunk is "
                    "its source chunk, so hit-rate rises with top_k on its own.")
                lines.append("")

        if code_rows:
            lines += [
                "**Self-reported** (code arms; scores were posted by the arm, "
                "not measured by the eval harness):",
                "",
            ]
            for r in code_rows:
                lines.append(
                    f"- {r.get('parameter') or r.get('parameter_changed')}: "
                    f"{r.get('new_value')} — {r.get('status')}, reported "
                    f"{score(r.get('composite_score'))} ({(r.get('delta') or 0):+.3f})")
            lines.append("")

        if heal_rows:
            lines.append("**Health checks** (pytest snapshot, not experiments): "
                         + ", ".join(f"{r.get('status')} ({r.get('new_value')})"
                                     for r in heal_rows) + ".")
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
