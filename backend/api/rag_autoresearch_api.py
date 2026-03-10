"""RAG Autoresearch API — REST endpoints for dashboard and manual triggers."""
from flask import Blueprint, jsonify, request
from backend.services.rag_autoresearch_service import get_autoresearch_service
from backend.models import ExperimentRun, EvalPair, ResearchConfig, Setting, db

autoresearch_bp = Blueprint("autoresearch", __name__, url_prefix="/api/autoresearch")


@autoresearch_bp.route("/status", methods=["GET"])
def get_status():
    svc = get_autoresearch_service()
    return jsonify(svc.get_status())


@autoresearch_bp.route("/start", methods=["POST"])
def start_loop():
    svc = get_autoresearch_service()
    if svc.is_running():
        return jsonify({"error": "Already running"}), 409
    max_exp = request.json.get("max_experiments", 0) if request.is_json else 0
    import threading
    t = threading.Thread(target=svc.run_loop, kwargs={"max_experiments": max_exp}, daemon=True)
    t.start()
    return jsonify({"status": "started"})


@autoresearch_bp.route("/stop", methods=["POST"])
def stop_loop():
    svc = get_autoresearch_service()
    svc.pause()
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
    pairs = svc.eval_harness.generate_eval_set()
    for pair_data in pairs:
        pair = EvalPair(**{k: v for k, v in pair_data.items() if k in EvalPair.__table__.columns.keys()})
        db.session.add(pair)
    db.session.commit()
    return jsonify({"status": "regenerated", "count": len(pairs)})


@autoresearch_bp.route("/settings", methods=["GET"])
def get_settings():
    keys = [
        "rag_autoresearch_idle_minutes",
        "rag_autoresearch_auto_enabled",
        "rag_autoresearch_max_experiments",
        "rag_autoresearch_phase_limit",
        "rag_autoresearch_judge_model",
    ]
    settings = {}
    for key in keys:
        s = Setting.query.filter_by(key=key).first()
        settings[key] = s.value if s else None
    defaults = {
        "rag_autoresearch_idle_minutes": "10",
        "rag_autoresearch_auto_enabled": "true",
        "rag_autoresearch_max_experiments": "0",
        "rag_autoresearch_phase_limit": "2",
        "rag_autoresearch_judge_model": "",
    }
    for k, v in defaults.items():
        if settings[k] is None:
            settings[k] = v
    return jsonify(settings)


@autoresearch_bp.route("/settings", methods=["PUT"])
def update_settings():
    data = request.get_json()
    for key, value in data.items():
        if key.startswith("rag_autoresearch_"):
            s = Setting.query.filter_by(key=key).first()
            if s:
                s.value = str(value)
            else:
                s = Setting(key=key, value=str(value))
                db.session.add(s)
    db.session.commit()
    return jsonify({"status": "updated"})
