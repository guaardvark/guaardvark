import os

from flask import Blueprint, Response, abort, current_app, request

log_bp = Blueprint("log_api", __name__, url_prefix="/api/logs")


@log_bp.route("/tail", methods=["GET"])
def tail_logs():

    try:
        num = int(request.args.get("lines", 50))
    except ValueError:
        num = 50
    log_path = os.path.join(current_app.config.get("LOG_DIR", "logs"), "backend.log")
    if not os.path.exists(log_path):
        return Response("log file not found", status=404)
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()[-num:]
    return Response("".join(lines), mimetype="text/plain")
