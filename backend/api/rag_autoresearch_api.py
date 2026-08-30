"""RAG Autoresearch API — REST endpoints for dashboard and manual triggers."""
from flask import Blueprint, jsonify, request
from backend.services.rag_autoresearch_service import get_autoresearch_service
from backend.models import ExperimentRun, EvalPair, ResearchConfig, Setting, db

DEFAULT_START_BUDGET_HOURS = 6.0

autoresearch_bp = Blueprint("autoresearch", __name__, url_prefix="/api/autoresearch")


@autoresearch_bp.route("/status", methods=["GET"])
def get_status():
    svc = get_autoresearch_service()
    return jsonify(svc.get_status())


@autoresearch_bp.route("/start", methods=["POST"])
def start_loop():
    """Alias for a bounded ResearchRun. The unbounded in-process loop is
    how the 2026-08 runaway started; Play /start now shares kickoff()
    with Research Tonight.
    """
    from backend.services.research_run_service import get_research_run_service
    body = request.get_json(silent=True) or {}
    hours = body.get("budget_hours")
    if hours is None:
        hours = DEFAULT_START_BUDGET_HOURS
    result = get_research_run_service().kickoff(
        mode=body.get("mode", "rag_tuning"),
        budget_hours=hours,
        trigger="manual",
    )
    status = 409 if "error" in result else 202
    return jsonify(result), status


def _set_kill_flag(value: str) -> None:
    s = Setting.query.filter_by(key="autoresearch_kill").first()
    if s:
        s.value = value
    else:
        db.session.add(Setting(key="autoresearch_kill", value=value))
    db.session.commit()


@autoresearch_bp.route("/stop", methods=["POST"])
def stop_loop():
    svc = get_autoresearch_service()
    svc.pause()
    # pause() only reaches THIS process's singleton; a loop running inside the
    # Celery worker never sees it. The persisted kill flag is checked by every
    # loop/run on each iteration, wherever it lives. /start and run kickoff
    # clear it again.
    _set_kill_flag("true")
    return jsonify({"status": "paused"})


@autoresearch_bp.route("/history", methods=["GET"])
def get_history():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    runs = (
        ExperimentRun.query
        .order_by(ExperimentRun.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    return jsonify({
        "experiments": [r.to_dict() for r in runs.items],
        "total": runs.total,
        "page": page,
        "pages": runs.pages,
    })


@autoresearch_bp.route("/config", methods=["GET"])
def get_config():
    svc = get_autoresearch_service()
    config = svc._load_config()
    return jsonify(config)


@autoresearch_bp.route("/config/reset", methods=["POST"])
def reset_config():
    from backend.config import AUTORESEARCH_DEFAULT_PARAMS
    svc = get_autoresearch_service()
    config = {
        "version": 1,
        "baseline_score": 0.0,
        "params": dict(AUTORESEARCH_DEFAULT_PARAMS),
        "phase": 1,
        "phase_plateau_count": 0,
    }
    svc._save_config(config)
    return jsonify({"status": "reset", "config": config})


@autoresearch_bp.route("/eval-pairs", methods=["GET"])
def get_eval_pairs():
    pairs = EvalPair.query.order_by(EvalPair.created_at.desc()).limit(200).all()
    return jsonify({"pairs": [p.to_dict() for p in pairs], "count": len(pairs)})


@autoresearch_bp.route("/eval-pairs/regenerate", methods=["POST"])
def regenerate_eval_pairs():
    svc = get_autoresearch_service()
    from backend.services.rag_eval_harness import LLMUnavailableError
    try:
        body = request.get_json(silent=True) or {}
        from backend.config import (
            AUTORESEARCH_EVAL_PAIR_TARGET,
            AUTORESEARCH_EVAL_PAIR_REGENERATE_MAX,
        )
        count = body.get("count") if isinstance(body, dict) else None
        if count is not None:
            try:
                count = int(count)
            except (TypeError, ValueError):
                count = AUTORESEARCH_EVAL_PAIR_TARGET
            count = max(1, min(count, AUTORESEARCH_EVAL_PAIR_REGENERATE_MAX))
        pairs = svc.eval_harness.generate_eval_set(target_count=count)
    except LLMUnavailableError as e:
        return jsonify({"error": str(e)}), 503
    if not pairs:
        from backend.config import AUTORESEARCH_MIN_CORPUS_SIZE
        n_text = svc.eval_harness.text_document_count()
        if n_text < AUTORESEARCH_MIN_CORPUS_SIZE:
            return jsonify({"error": (
                f"Only {n_text} indexed documents carry text; autoresearch needs "
                f"{AUTORESEARCH_MIN_CORPUS_SIZE}. Images, audio and unextracted "
                f"files do not count — index some documents first.")}), 400
        return jsonify({"error": "No pairs generated — the LLM returned nothing usable for any document"}), 400
    # Regeneration REPLACES the active set: deactivate the old generation so
    # eval cost doesn't compound with every regenerate.
    EvalPair.query.filter(EvalPair.is_active.isnot(False)).update(
        {"is_active": False, "stale_reason": "superseded_by_regeneration"},
        synchronize_session=False,
    )
    for pair_data in pairs:
        pair = EvalPair(**{k: v for k, v in pair_data.items() if k in EvalPair.__table__.columns.keys()})
        pair.is_active = True
        db.session.add(pair)
    db.session.commit()
    return jsonify({"status": "regenerated", "count": len(pairs)})


@autoresearch_bp.route("/promotions", methods=["GET"])
def get_promotions():
    """Promotion history — every config autoresearch ever promoted or received."""
    rows = ResearchConfig.query.order_by(ResearchConfig.created_at.desc()).limit(100).all()
    return jsonify({"promotions": [r.to_dict() for r in rows]})


def _activate_config(row) -> None:
    ResearchConfig.query.filter_by(is_active=True).update({"is_active": False})
    row.is_active = True
    row.status = "promoted"
    db.session.commit()
    from backend.utils.experiment_context import invalidate_active_params_cache
    invalidate_active_params_cache()


@autoresearch_bp.route("/promotions/<config_id>/activate", methods=["POST"])
def activate_promotion(config_id):
    """Manually activate a config (including family-broadcast candidates)."""
    row = db.session.get(ResearchConfig, config_id)
    if row is None:
        return jsonify({"error": "Unknown config id"}), 404
    _activate_config(row)
    return jsonify({"status": "activated", "config": row.to_dict()})


@autoresearch_bp.route("/promotions/revert", methods=["POST"])
def revert_promotion():
    """Deactivate the current config and activate the previous promoted one.

    With no predecessor, retrieval falls back to legacy defaults — the
    pre-autoresearch behavior, which is always a safe place to land.
    """
    current = ResearchConfig.query.filter_by(is_active=True).first()
    if current is None:
        return jsonify({"status": "nothing_active"})
    current.is_active = False
    current.status = "superseded"
    previous = (
        ResearchConfig.query.filter(
            ResearchConfig.id != current.id,
            ResearchConfig.status == "promoted",
        )
        .order_by(ResearchConfig.promoted_at.desc().nullslast())
        .first()
    )
    if previous is not None:
        previous.is_active = True
    db.session.commit()
    from backend.utils.experiment_context import invalidate_active_params_cache
    invalidate_active_params_cache()
    return jsonify({
        "status": "reverted",
        "now_active": previous.to_dict() if previous else None,
    })


@autoresearch_bp.route("/experiments", methods=["POST"])
def log_experiment():
    """Ledger write for swarm code-tuning arms (source=code_arm).

    Arms run as coding agents in worktrees; this is how their results land in
    the same ExperimentRun ledger the RAG-tuning loop uses, so one morning
    report covers both engines.
    """
    body = request.get_json(silent=True) or {}
    if not body.get("parameter") or body.get("status") not in ("keep", "discard", "crash"):
        return jsonify({"error": "parameter and status(keep|discard|crash) required"}), 400
    import uuid as _uuid
    row = ExperimentRun(
        id=str(_uuid.uuid4()),
        run_tag=body.get("run_tag"),
        phase=0,  # code arms are not phase-parameter experiments
        parameter_changed=str(body["parameter"])[:200],
        old_value=None,
        new_value=str(body.get("new_value", ""))[:500],
        hypothesis=body.get("hypothesis"),
        composite_score=float(body.get("composite_score", 0.0) or 0.0),
        baseline_score=float(body.get("baseline_score", 0.0) or 0.0),
        delta=body.get("delta"),
        status=body["status"],
        proposal_source=body.get("source", "code_arm"),
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"status": "logged", "id": row.id}), 201


@autoresearch_bp.route("/runs", methods=["POST"])
def create_run():
    """Kick off a research run NOW ('research tonight' button)."""
    from backend.services.research_run_service import get_research_run_service
    body = request.get_json(silent=True) or {}
    result = get_research_run_service().kickoff(
        mode=body.get("mode", "rag_tuning"),
        budget_hours=body.get("budget_hours"),
        trigger="manual",
    )
    status = 409 if "error" in result else 202
    return jsonify(result), status


@autoresearch_bp.route("/runs", methods=["GET"])
def list_runs():
    from backend.models import ResearchRun
    runs = ResearchRun.query.order_by(ResearchRun.created_at.desc()).limit(30).all()
    return jsonify({"runs": [r.to_dict() for r in runs]})


@autoresearch_bp.route("/runs/<run_id>", methods=["GET"])
def get_run(run_id):
    from backend.models import ResearchRun
    run = db.session.get(ResearchRun, run_id)
    if run is None:
        return jsonify({"error": "Unknown run"}), 404
    return jsonify(run.to_dict(include_report=True))


@autoresearch_bp.route("/runs/<run_id>/ledger", methods=["GET"])
def get_run_ledger(run_id):
    """The run's experiment ledger as TSV (karpathy results.tsv analog)."""
    from backend.models import ResearchRun
    run = db.session.get(ResearchRun, run_id)
    if run is None:
        return jsonify({"error": "Unknown run"}), 404
    rows = (
        ExperimentRun.query.filter_by(run_tag=run.run_tag)
        .order_by(ExperimentRun.created_at.asc())
        .all()
    )
    lines = ["experiment_id\tparameter\tchange\tscore\tdelta\tstatus\tsource"]
    for r in rows:
        lines.append(
            f"{r.id[:8]}\t{r.parameter_changed}\t{r.old_value}->{r.new_value}\t"
            f"{r.composite_score:.4f}\t{r.delta if r.delta is not None else ''}\t"
            f"{r.status}\t{r.proposal_source or ''}"
        )
    return "\n".join(lines), 200, {"Content-Type": "text/tab-separated-values"}


@autoresearch_bp.route("/metrics", methods=["GET"])
def get_metrics():
    """Recent per-experiment retrieval metrics + provenance."""
    limit = request.args.get("limit", 50, type=int)
    runs = (
        ExperimentRun.query.order_by(ExperimentRun.created_at.desc())
        .limit(min(limit, 200))
        .all()
    )
    return jsonify({
        "experiments": [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "parameter": r.parameter_changed,
                "new_value": r.new_value,
                "hypothesis": r.hypothesis,
                "status": r.status,
                "composite_score": r.composite_score,
                "delta": r.delta,
                "proposal_source": r.proposal_source,
                "proposer_model": r.proposer_model,
                "judge_model": r.judge_model,
                "retrieval_metrics": r.retrieval_metrics,
                "run_tag": r.run_tag,
            }
            for r in runs
        ]
    })


@autoresearch_bp.route("/settings", methods=["GET"])
def get_settings():
    keys = [
        "rag_autoresearch_idle_minutes",
        "rag_autoresearch_auto_enabled",
        "rag_autoresearch_max_experiments",
        "rag_autoresearch_phase_limit",
        "rag_autoresearch_judge_model",
        "autoresearch_proposer_model",
        "autoresearch_judge_model",
        "autoresearch_nightly_window",
    ]
    settings = {}
    for key in keys:
        s = Setting.query.filter_by(key=key).first()
        settings[key] = s.value if s else None
    defaults = {
        "rag_autoresearch_idle_minutes": "10",
        # Off by default — auto-start is an explicit opt-in (see check_idle task).
        "rag_autoresearch_auto_enabled": "false",
        "rag_autoresearch_max_experiments": "0",
        "rag_autoresearch_phase_limit": "1",
        "rag_autoresearch_judge_model": "",
        "autoresearch_proposer_model": "",
        "autoresearch_judge_model": "",
        "autoresearch_nightly_window": "01:00-06:00",
    }
    for k, v in defaults.items():
        if settings[k] is None:
            settings[k] = v
    return jsonify(settings)


@autoresearch_bp.route("/settings", methods=["PUT"])
def update_settings():
    data = request.get_json()
    for key, value in data.items():
        if key.startswith(("rag_autoresearch_", "autoresearch_")):
            s = Setting.query.filter_by(key=key).first()
            if s:
                s.value = str(value)
            else:
                s = Setting(key=key, value=str(value))
                db.session.add(s)
    db.session.commit()
    return jsonify({"status": "updated"})
