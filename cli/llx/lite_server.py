"""Embedded lite Flask server for zero-dependency guaardvark launch.

Provides the minimum API surface for TUI chat, model management,
and health checks without requiring PostgreSQL, Redis, or Celery.
"""
import httpx
from flask import Flask, request, jsonify

from llx import __version__
from llx.launch_config import load_launch_config, save_launch_config, resolve_ollama_url


def create_lite_app(db_path: str | None = None) -> Flask:
    """Create a minimal Flask app for lite mode."""
    app = Flask(__name__)

    @app.route("/api/health")
    def health():
        cfg = load_launch_config()
        return jsonify({
            "status": "ok",
            "mode": "lite",
            "version": __version__,
            "model": cfg.get("model"),
        })

    @app.route("/api/model/list")
    def model_list():
        ollama_url = resolve_ollama_url()
        try:
            resp = httpx.get(f"{ollama_url}/api/tags", timeout=10)
            resp.raise_for_status()
            models = resp.json().get("models", [])
        except Exception:
            models = []

        formatted = []
        for m in models:
            formatted.append({
                "name": m.get("name", ""),
                "size": m.get("size", 0),
                "modified_at": m.get("modified_at", ""),
                "full_name": m.get("name", ""),
            })

        return jsonify({
            "success": True,
            "message": "Models retrieved",
            "data": {"models": formatted},
        })

    @app.route("/api/model/status")
    def model_status():
        cfg = load_launch_config()
        return jsonify({
            "success": True,
            "message": "Model status retrieved",
            "data": {
                "text_model": cfg.get("model", "unknown"),
                "vision_model": None,
                "vision_loaded": False,
                "image_gen_model": None,
                "image_gen_loaded": False,
            },
        })

    @app.route("/api/model/set", methods=["POST"])
    def model_set():
        data = request.get_json()
        model_name = data.get("model")
        if not model_name:
            return jsonify({"success": False, "message": "Model name required"}), 400

        cfg = load_launch_config()
        cfg["model"] = model_name
        save_launch_config(cfg)

        return jsonify({
            "success": True,
            "message": f"Model set to {model_name}",
            "data": {"model": model_name},
        })

    @app.route("/api/chat/unified", methods=["POST"])
    def chat_unified():
        data = request.get_json()
        message = data.get("message", "")
        cfg = load_launch_config()
        model = cfg.get("model", "llama3.3")
        ollama_url = resolve_ollama_url()

        try:
            ollama_resp = httpx.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": message}],
                    "stream": False,
                },
                timeout=180,
            )
            ollama_resp.raise_for_status()
            result = ollama_resp.json()
            content = result.get("message", {}).get("content", "")
        except Exception as e:
            return jsonify({
                "success": False,
                "message": f"Ollama error: {e}",
            }), 502

        return jsonify({
            "success": True,
            "data": {
                "response": content,
                "model": model,
                "session_id": data.get("session_id"),
            },
        })

    return app


def start_lite_server(port: int = 5000) -> None:
    """Start the lite server in the current process."""
    from llx.theme import make_console
    console = make_console()

    app = create_lite_app()

    console.print(f"[llx.success]Lite server starting on port {port}[/llx.success]")
    console.print(f"[llx.dim]Mode: SQLite, no Redis/Celery[/llx.dim]")

    app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)
