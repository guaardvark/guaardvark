# backend/api/infographic_api.py
# REST endpoints for the infographic generator.
#
#   POST /api/infographic/generate
#       body: {title, scene, footer?, hashtags?, callouts?, style?, aspect?,
#              raw_prompt?, seed?, model?}
#       returns: {filename, image_url, prompt, model, model_name, width, height,
#                 seed, duration_s, document_id}
#       Synchronous: waits for the GPU if another render holds it, then blocks
#       until the PNG is saved under outputs/infographics.
#
#   GET  /api/infographic/view?filename=...
#       Serves a saved infographic.
#
#   GET  /api/infographic/status
#       Whether a default model is installed, which one renders, and the
#       offered models with their install state.
#
#   GET  /api/infographic/models
#       The offered models. They install through the Image Models downloader
#       (POST /api/batch-image/models/download {model_path: <id>}).

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request, send_file

logger = logging.getLogger(__name__)

infographic_bp = Blueprint("infographic_api", __name__, url_prefix="/api/infographic")


@infographic_bp.route("/status", methods=["GET"])
def status():
    from backend.services.infographic_generator import get_infographic_generator

    gen = get_infographic_generator()
    models = gen.models()
    default = next((m["id"] for m in models if m["default"] and m["installed"]), None)
    return jsonify({
        "ready": default is not None,
        "default_model": default,
        "models": models,
    })


@infographic_bp.route("/models", methods=["GET"])
def list_models():
    from backend.services.infographic_generator import get_infographic_generator

    return jsonify({"success": True, "models": get_infographic_generator().models()})


@infographic_bp.route("/generate", methods=["POST"])
def generate():
    from backend.services.infographic_generator import (
        ASPECT_DIMS,
        STYLE_PRESETS,
        InfographicModelMissing,
        InfographicSpec,
        get_infographic_generator,
    )
    from backend.services.job_operation_gate import GpuBusyError

    body = request.get_json(silent=True) or {}

    # Hashtags arrive as a list or as a single comma/space-separated string;
    # normalize both shapes so the frontend can be lazy.
    raw_tags = body.get("hashtags") or []
    if isinstance(raw_tags, str):
        raw_tags = [t.strip() for t in raw_tags.replace(",", " ").split() if t.strip()]

    raw_callouts = body.get("callouts") or []
    if isinstance(raw_callouts, str):
        raw_callouts = [c.strip() for c in raw_callouts.splitlines() if c.strip()]

    style = str(body.get("style") or "editorial")
    aspect = str(body.get("aspect") or "16:9")
    if style not in STYLE_PRESETS:
        return jsonify({
            "success": False,
            "error": f"Unknown style '{style}'; use one of: {', '.join(STYLE_PRESETS)}.",
        }), 400
    if aspect not in ASPECT_DIMS:
        return jsonify({
            "success": False,
            "error": f"Unknown aspect '{aspect}'; use one of: {', '.join(ASPECT_DIMS)}.",
        }), 400

    spec = InfographicSpec(
        title=str(body.get("title", "")).strip(),
        scene=str(body.get("scene", "")).strip(),
        footer=str(body.get("footer", "")).strip(),
        hashtags=[str(t).lstrip("#") for t in raw_tags],
        callouts=[str(c) for c in raw_callouts],
        style=style,
        aspect=aspect,
        raw_prompt=str(body.get("raw_prompt", "")),
    )

    if not (spec.scene or spec.raw_prompt):
        return jsonify({
            "success": False,
            "error": "Either 'scene' or 'raw_prompt' is required.",
        }), 400

    seed = body.get("seed")
    if seed is not None:
        try:
            seed = int(seed)
        except (TypeError, ValueError):
            seed = None

    model = str(body.get("model") or "").strip() or None

    gen = get_infographic_generator()
    try:
        result = gen.generate(spec, seed=seed, model=model)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except InfographicModelMissing as e:
        return jsonify({"success": False, "error": str(e)}), 409
    except GpuBusyError as e:  # still busy after the wait, or the render cannot fit this card
        return jsonify({"success": False, "error": str(e)}), 503
    except Exception as e:
        logger.exception("Infographic generation failed")
        return jsonify({"success": False, "error": str(e)}), 500

    return jsonify({"success": True, **result})


@infographic_bp.route("/view", methods=["GET"])
def view():
    from backend.services.infographic_generator import InfographicGenerator

    filename = request.args.get("filename", "").strip()
    if not filename:
        return jsonify({"error": "filename required"}), 400
    path = InfographicGenerator.image_path(filename)
    if path is None:
        return jsonify({"error": "image not available"}), 404
    return send_file(path, mimetype="image/png")
