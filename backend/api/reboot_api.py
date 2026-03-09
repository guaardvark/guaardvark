# backend/api/reboot_api.py
# Version: v7 - Standalone log server survives restart for live terminal output

import json
import logging
import os
import re
import subprocess
import sys
import time

from flask import Blueprint, current_app, jsonify, request, Response, stream_with_context

reboot_bp = Blueprint("reboot", __name__, url_prefix="/api")
logger = logging.getLogger(__name__)

ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07')

# ---------------------------------------------------------------------------
# GET /api/reboot/log  — fallback log reader (when log server isn't available)
# ---------------------------------------------------------------------------

@reboot_bp.route("/reboot/log", methods=["GET"])
def get_reboot_log():
    """Returns reboot log file contents with offset-based incremental reading."""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    log_file_path = os.path.join(project_root, "logs", "reboot.log")

    try:
        if not os.path.isfile(log_file_path):
            return jsonify({"success": False, "content_lines": [], "offset": 0, "size": 0})

        file_size = os.path.getsize(log_file_path)
        offset = request.args.get("offset", 0, type=int)
        if offset < 0:
            offset = 0
        if offset > file_size:
            offset = 0  # file was rewritten

        with open(log_file_path, "r", encoding="utf-8", errors="replace") as f:
            if offset > 0:
                f.seek(offset)
            content = f.read()

        content = ANSI_RE.sub("", content)
        content_lines = [ln for ln in content.split("\n") if ln.strip()]

        return jsonify({
            "success": True,
            "content_lines": content_lines,
            "offset": file_size,
            "size": file_size,
        })
    except Exception as e:
        logger.error(f"Error reading reboot log: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e), "offset": 0, "size": 0}), 500


# ---------------------------------------------------------------------------
# POST /api/reboot/stream  — main reboot handler
# ---------------------------------------------------------------------------

@reboot_bp.route("/reboot/stream", methods=["POST"])
def stream_reboot():
    """
    1. Spawns a standalone log server on (FLASK_PORT + 1000) that survives
       stop.sh (runs with cwd=/tmp, different process pattern).
    2. Launches start.sh detached via nohup, output appended to reboot.log.
    3. Streams initial output via SSE, then tells frontend to poll the log server.
    """
    logger.warning("Reboot requested — starting stream")

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    start_script_path = os.path.join(project_root, "start.sh")
    log_file_path = os.path.join(project_root, "logs", "reboot.log")
    log_server_script = os.path.join(project_root, "backend", "utils", "reboot_log_server.py")

    os.makedirs(os.path.dirname(log_file_path), exist_ok=True)

    flask_port = int(os.environ.get("FLASK_PORT", 5000))
    log_server_port = flask_port + 1000

    # --- Validate start.sh ---
    if not os.path.isfile(start_script_path):
        def err():
            yield f"data: {json.dumps({'type': 'error', 'message': f'start.sh not found at {start_script_path}'})}\n\n"
        return Response(stream_with_context(err()), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache"})

    if not os.access(start_script_path, os.X_OK):
        try:
            os.chmod(start_script_path, 0o755)
        except Exception:
            pass

    def generate_stream():
        log_server_url = None

        try:
            # -- Clear log file --
            with open(log_file_path, "w", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] Reboot initiated\n")
                f.flush()

            yield _sse("status", message="Starting reboot process...")

            # -- Spawn standalone log server --
            log_server_url = f"http://localhost:{log_server_port}"
            try:
                # Kill any leftover log server from a previous reboot
                import urllib.request
                try:
                    urllib.request.urlopen(f"{log_server_url}/shutdown", timeout=2)
                    time.sleep(0.3)
                except Exception:
                    pass

                subprocess.Popen(
                    [
                        sys.executable,
                        log_server_script,
                        "--port", str(log_server_port),
                        "--log-file", log_file_path,
                        "--timeout", "300",
                    ],
                    cwd="/tmp",  # stop.sh checks CWD — /tmp won't match project root
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=os.setsid if hasattr(os, "setsid") else None,
                )
                time.sleep(0.5)
                yield _sse("log_server", url=log_server_url)
                yield _sse("status", message="Log server ready — live output will continue during restart")
            except Exception as exc:
                logger.error(f"Failed to start log server: {exc}")
                yield _sse("warning", message=f"Log server unavailable: {exc}")
                log_server_url = None

            # -- Launch start.sh (appends to reboot.log) --
            nohup_cmd = f'nohup stdbuf -oL -eL bash "{start_script_path}" >> "{log_file_path}" 2>&1 &'
            subprocess.Popen(
                ["bash", "-c", nohup_cmd],
                cwd=project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid if hasattr(os, "setsid") else None,
            )
            time.sleep(0.5)
            yield _sse("status", message="Reboot process launched")

            # -- Stream initial output for ~5 seconds before Flask dies --
            start_time = time.time()
            last_size = 0
            sent_lines = set()

            while time.time() - start_time < 5:
                try:
                    if os.path.exists(log_file_path):
                        cur_size = os.path.getsize(log_file_path)
                        if cur_size > last_size:
                            with open(log_file_path, "r", encoding="utf-8", errors="replace") as f:
                                f.seek(last_size)
                                new = f.read()
                            for raw_line in new.split("\n"):
                                clean = ANSI_RE.sub("", raw_line).strip()
                                if clean and clean not in sent_lines:
                                    sent_lines.add(clean)
                                    yield _sse("output", line=clean)
                            last_size = cur_size
                except Exception:
                    pass
                time.sleep(0.3)

            # -- Tell frontend to switch to log-server polling --
            yield _sse("complete", returnCode=None, polling=True, logServerUrl=log_server_url)

        except Exception as exc:
            logger.error(f"Reboot stream error: {exc}", exc_info=True)
            yield _sse("error", message=str(exc))
        finally:
            yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# POST /api/reboot  — legacy redirect
# ---------------------------------------------------------------------------

@reboot_bp.route("/reboot", methods=["POST"])
def trigger_service_restart():
    return stream_reboot()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _sse(event_type, **kwargs):
    """Format a Server-Sent Event data line."""
    payload = {"type": event_type, **kwargs}
    return f"data: {json.dumps(payload)}\n\n"
