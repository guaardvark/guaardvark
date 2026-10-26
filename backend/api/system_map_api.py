"""HTTP surface for the system_mapper SystemMap.

The System Map page (`/system-map`), the map tools and MCP clients read the
same stored map (backend/services/system_mapper/store.py). A read never
builds: it returns the last map with a ``_cache`` note saying whether the map
still describes the files on disk (``stale``) and whether a rebuild is
running (``refreshing``). A stale or missing map starts one background
rebuild per root; POST /refresh starts one on demand.

Endpoints
---------
GET  /api/system-map/snapshot            stored map (?root=, ?refresh=1 starts a rebuild)
GET  /api/system-map/findings            ranked findings only (?severity=, ?kind=, ?include_dismissed=1)
GET  /api/system-map/query               ?mode=where|module|symbol|route|tool|subsystem|impact|findings&q=
POST /api/system-map/refresh             start a rebuild now
POST /api/system-map/findings/<id>/dispatch
POST /api/system-map/findings/<id>/dismiss
GET  /api/system-map/code-index          is there a code search index for the checkout?
POST /api/system-map/code-index          build or refresh it with the zvec_grep plugin, in the background
GET  /api/system-map/health
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from flask import Blueprint, jsonify, request
from backend.utils.path_guard import PathEscapesRoot, contained

logger = logging.getLogger(__name__)

system_map_bp = Blueprint("system_map", __name__, url_prefix="/api/system-map")


# Default root = the running Guaardvark repo. config.py always sets GUAARDVARK_ROOT
# (resolving it to a real, writable path), so import from there rather than baking
# in a machine-specific literal.
def _default_root() -> Path:
    from backend.config import GUAARDVARK_ROOT
    return Path(GUAARDVARK_ROOT).resolve()


def _cache_path_for(root: Path) -> Path:
    """The stored snapshot for root (store.snapshot_path)."""
    from backend.services.system_mapper import store
    return store.snapshot_path(root)


def _allowed_root(root_arg: str) -> Path | None:
    """A caller-supplied root, accepted only inside the running codebase or the
    uploads directory (where Code Repository folders live)."""
    from backend.config import GUAARDVARK_ROOT, UPLOAD_DIR
    for base in (GUAARDVARK_ROOT, UPLOAD_DIR):
        try:
            return contained(base, root_arg)
        except PathEscapesRoot:
            continue
    return None


def _resolve_root(root_arg: str | None):
    """(root_path, None) on success, or (None, (json_error, status))."""
    if not root_arg:
        root = _default_root()
    else:
        root = _allowed_root(root_arg)
        if root is None:
            return None, (jsonify({
                "success": False,
                "error": "root must be inside the running codebase or the uploads directory",
            }), 400)
    if not root.is_dir():
        return None, (jsonify({"success": False, "error": f"Not a directory: {root}"}), 400)
    return root, None


def read_cached(root: Path):
    """(payload, age_seconds) for the last map stored for root, whatever its
    age, or (None, None) when there is none or it cannot be read."""
    from backend.services.system_mapper import store
    return store.read_snapshot(root)


def compute_and_cache(root: Path) -> dict:
    """Map root now in this thread, store it, and return the payload with its
    ``_cache`` note. Raises when the map cannot be computed."""
    from backend.services.system_mapper import store
    return store.build(root)


def _stored(root: Path, refresh: bool = False):
    """(payload, None) from the store, or (None, (json, status)) while the
    first map of root is still being built. Never builds in this thread."""
    from backend.services.system_mapper import store
    payload, note = store.state(root)
    if refresh and not note.get("refreshing"):
        store.start(root)
        note["refreshing"] = True
    if payload is None:
        return None, (jsonify({
            "success": False, "status": "computing", "_cache": note,
            "reason": "No map of this folder exists yet; it is being built. Ask again in a few seconds.",
        }), 202)
    payload["_cache"] = note
    return payload, None


@system_map_bp.route("/snapshot", methods=["GET"])
def snapshot():
    """The stored SystemMap for the requested root (default: Guaardvark itself)."""
    root, err = _resolve_root(request.args.get("root"))
    if err:
        return err
    payload, err = _stored(root, refresh=request.args.get("refresh") == "1")
    if err:
        return err
    return jsonify(payload)


@system_map_bp.route("/refresh", methods=["POST"])
def refresh():
    """Start rebuilding the map of root in the background (one build per root)."""
    from backend.services.system_mapper import store
    body = request.get_json(silent=True) or {}
    root, err = _resolve_root(body.get("root"))
    if err:
        return err
    job = store.start(root)
    return jsonify({"success": True, "status": "computing",
                    "running_seconds": int(time.time() - job.started)}), 202


@system_map_bp.route("/findings", methods=["GET"])
def findings():
    """Ranked, lightweight findings list for the findings panel.

    Query: ?root=&severity=high,medium&kind=ghost-endpoint&include_dismissed=1
    Returns findings only (not the full graph) so the panel can poll cheaply.
    """
    from backend.services.system_mapper import actions
    from backend.services.system_mapper import core

    root, err = _resolve_root(request.args.get("root"))
    if err:
        return err
    payload, err = _stored(root)
    if err:
        return err

    include_dismissed = request.args.get("include_dismissed") == "1"
    items = actions.ranked_findings(payload, root, include_dismissed=include_dismissed)

    # Default to actionable severities (high+medium) when the caller doesn't
    # specify — the low/info tier is noise for the default panel view.
    raw_sev = request.args.get("severity")
    if raw_sev is None:
        sev_filter = {"high", "medium"}
    else:
        sev_filter = {s for s in raw_sev.split(",") if s}
    kind_filter = {k for k in (request.args.get("kind") or "").split(",") if k}

    try:
        limit = int(request.args.get("limit", 100))
    except (TypeError, ValueError):
        limit = 100

    items = core.filter_findings(
        items,
        severities=sev_filter or None,
        kinds=kind_filter or None,
        limit=limit,
    )

    counts: dict[str, int] = {}
    for f in items:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1

    return jsonify({
        "success": True,
        "root": str(root),
        "findings": items,
        "counts": counts,
        "total": len(items),
        "stats": payload.get("stats", {}),
        "_cache": payload.get("_cache"),
    })


@system_map_bp.route("/query", methods=["GET"])
def query():
    """A focused question about the map, answered as text: ?mode=&q= (or
    ?q= alone, e.g. "what calls run_chat_turn")."""
    from backend.services.system_mapper import query as query_mod
    from backend.services.system_mapper import store

    root, err = _resolve_root(request.args.get("root"))
    if err:
        return err
    mode = (request.args.get("mode") or "").strip().lower()
    q = (request.args.get("q") or "").strip()
    if not mode:
        mode, q = query_mod.parse(q)
    payload, err = _stored(root)
    if err:
        return err
    index = store.load_index(root, payload)
    if index is None:
        store.start(root)
        return jsonify({"success": False, "status": "computing",
                        "reason": "The map's query index is being built. Ask again in a few seconds."}), 202
    text = query_mod.answer(root, payload, index, mode, q)
    return jsonify({"success": True, "mode": mode, "q": q, "answer": text, "_cache": payload.get("_cache")})


@system_map_bp.route("/findings/<finding_id>/dispatch", methods=["POST"])
def dispatch(finding_id):
    """Send a finding to the self-improvement agent as a directed task."""
    from backend.services.system_mapper import actions, store

    body = request.get_json(silent=True) or {}
    root, err = _resolve_root(body.get("root"))
    if err:
        return err
    payload, _age = store.read_snapshot(root)
    if payload is None:
        return jsonify({"success": False, "reason": "No map yet; open the System Map page first.",
                        "finding_id": finding_id}), 404

    finding = actions.find_finding(payload, finding_id)
    if finding is None:
        return jsonify({"success": False, "reason": "finding not found",
                        "finding_id": finding_id}), 404

    # (b) Authoritative dispatchability gate. The UI already hides the button for
    # advisory kinds, but the endpoint must not run the fix engine for a
    # liveness/dead-symbol/architectural finding even if called directly.
    kind = finding.get("kind")
    if kind not in actions.DISPATCHABLE_KINDS:
        return jsonify({
            "success": False, "finding_id": finding_id,
            "reason": (f"Finding kind '{kind}' is advisory-only and is not "
                       f"auto-dispatchable — review it manually."),
        }), 200

    # One open fix per finding: a second dispatch while the first one's
    # PendingFix waits for review would stage the same change again.
    held = actions.open_dispatch(root, finding)
    if held:
        return jsonify({"success": False, "finding_id": finding_id, "skipped": True, **held,
                        "reason": actions._held_reason(finding, held)}), 200

    # Synchronous precheck so the user gets the REAL reason immediately when the
    # self-improvement agent can't run (locked / disabled / already running),
    # instead of a silent no-op.
    try:
        from backend.services.self_improvement_service import get_self_improvement_service
        pre = get_self_improvement_service().dispatch_precheck()
    except Exception as exc:  # service import/init failure → report, don't 500
        logger.warning("dispatch precheck failed: %s", exc)
        pre = {"ok": False, "reason": f"self-improvement unavailable: {exc}"}
    if not pre.get("ok"):
        return jsonify({"success": False, "finding_id": finding_id,
                        "reason": pre.get("reason", "self-improvement cannot run")}), 200

    # (b) Run the actual fix attempt ASYNCHRONOUSLY on Celery — submit_directed_task
    # calls the LLM (and possibly the GPU); doing it inline would block/freeze the
    # request thread.
    description = actions.describe(finding)
    priority = body.get("priority", "medium")
    target_files = list(finding.get("paths") or [])
    # A finding whose remedy is fully determined by its evidence is staged as
    # an exact proposal; the agent is only asked when judgment is needed.
    proposal = actions.mechanical_proposal(finding, root)
    try:
        from backend.celery_app import celery as celery_app
        async_res = celery_app.send_task(
            "self_improvement.run_directed_async",
            kwargs={"task_description": description,
                    "target_files": target_files, "priority": priority,
                    "proposal": proposal},
        )
        actions.record_dispatch(root, finding, {"task_id": getattr(async_res, "id", None)})
        reason = (
            "Dispatched — the fix is mechanical, so the exact change is being "
            "staged as a PendingFix for your review in Settings."
            if proposal else
            "Dispatched to the self-improvement agent — running in the "
            "background. Review the proposed fix (a PendingFix) in Settings."
        )
        return jsonify({
            "success": True, "queued": True,
            "task_id": getattr(async_res, "id", None),
            "finding_id": finding_id,
            "mechanical": bool(proposal),
            "reason": reason,
        }), 202
    except Exception as exc:
        logger.exception("dispatch enqueue failed")
        return jsonify({
            "success": False, "finding_id": finding_id,
            "reason": (f"Could not queue the task — is the Celery worker running? "
                       f"({exc})"),
        }), 200


@system_map_bp.route("/findings/<finding_id>/dismiss", methods=["POST"])
def dismiss(finding_id):
    """Acknowledge a finding so it stops showing (persists across re-runs)."""
    from backend.services.system_mapper import actions
    body = request.get_json(silent=True) or {}
    root, err = _resolve_root(body.get("root"))
    if err:
        return err
    undo = body.get("undo") is True
    ids = (actions.undismiss if undo else actions.dismiss)(root, finding_id)
    return jsonify({"success": True, "dismissed": sorted(ids), "undo": undo})


# ── Code search index (zvec_grep plugin) ────────────────────────────────────
#
# The index lives in <checkout>/.zvec-grep and is built by the plugin's own
# scripts/index.sh with the embedding model its Install step downloaded into
# plugins/zvec_grep/cache/models. Nothing here downloads: without the
# installed plugin and its model the request is refused.

_index_job: dict = {}
_index_lock = threading.Lock()


def _zvec_paths(root: Path) -> dict:
    plugin = root / "plugins" / "zvec_grep"
    return {
        "plugin": plugin,
        "script": plugin / "scripts" / "index.sh",
        "zg": plugin / "node_modules" / ".bin" / "zg",
        "models": plugin / "cache" / "models",
        "index": root / ".zvec-grep",
    }


def _code_index_status(root: Path) -> dict:
    p = _zvec_paths(root)
    models_ready = p["models"].is_dir() and any(p["models"].iterdir())
    with _index_lock:
        job = dict(_index_job.get(str(root)) or {})
    proc = job.pop("proc", None)
    running = proc is not None and proc.poll() is None
    if proc is not None and not running and "exit_code" not in job:
        job["exit_code"] = proc.returncode
    return {
        "indexed": p["index"].is_dir(),
        "installed": p["zg"].exists() and models_ready,
        "running": running,
        "started_at": job.get("started_at"),
        "exit_code": job.get("exit_code"),
        "log_tail": _tail(job.get("log")),
    }


def _tail(path, n: int = 15) -> str:
    if not path:
        return ""
    try:
        return "\n".join(Path(path).read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return ""


@system_map_bp.route("/code-index", methods=["GET"])
def code_index_status():
    root = _default_root()
    return jsonify({"success": True, **_code_index_status(root)})


@system_map_bp.route("/code-index", methods=["POST"])
def code_index_build():
    """Build or refresh the code search index of the checkout in the background."""
    root = _default_root()
    p = _zvec_paths(root)
    if not p["script"].is_file():
        return jsonify({"success": False, "reason": "The zvec_grep plugin is not part of this install."}), 400
    if not p["zg"].exists():
        return jsonify({"success": False, "reason": (
            "The zvec_grep plugin is not installed. Install it from the Plugins page first; "
            "building the index downloads nothing.")}), 400
    if not (p["models"].is_dir() and any(p["models"].iterdir())):
        return jsonify({"success": False, "reason": (
            "The zvec_grep plugin's embedding model is missing from plugins/zvec_grep/cache/models. "
            "Run the plugin's Install from the Plugins page; this button does not download it.")}), 400
    with _index_lock:
        job = _index_job.get(str(root))
        if job and job.get("proc") is not None and job["proc"].poll() is None:
            return jsonify({"success": True, "status": "running",
                            "started_at": job.get("started_at")}), 202
        from backend.config import LOG_DIR
        log_path = Path(LOG_DIR) / "zvec_grep_index.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "ab")
        env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
        proc = subprocess.Popen(["bash", str(p["script"]), str(root)], cwd=str(root),
                                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=env, start_new_session=True)
        log.close()
        _index_job[str(root)] = {"proc": proc, "started_at": time.time(), "log": str(log_path)}
    return jsonify({"success": True, "status": "started", "started_at": time.time()}), 202


@system_map_bp.route("/health", methods=["GET"])
def health():
    """Smoke endpoint — confirms the analyzer module imports and registers cleanly."""
    try:
        from backend.services.system_mapper import codebase_map  # noqa: F401
        return jsonify({"status": "ok", "default_root": str(_default_root())})
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500
