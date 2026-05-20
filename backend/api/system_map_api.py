"""HTTP surface for the system_mapper SystemMap.

Frontend (`/system-map` route) hits this endpoint to fetch the JSON the
constellation canvas renders. The map computation costs ~3 seconds on a
cold cache; we serve a disk-cached snapshot and only re-run when the cache
ages out (or the caller passes ?refresh=1).

Endpoint
--------
GET  /api/system-map/snapshot           — cached snapshot (re-computed if stale)
GET  /api/system-map/snapshot?refresh=1 — force re-compute
GET  /api/system-map/snapshot?root=<path> — map an arbitrary codebase
                                            (DocumentsPage "Analyze codebase"
                                            wires to this)
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

system_map_bp = Blueprint("system_map", __name__, url_prefix="/api/system-map")

# Cache freshness: re-compute if the snapshot on disk is older than this.
# 5 minutes is the sweet spot for an interactive map — users hit refresh once
# while exploring, and the compute cost is masked by the cache for follow-ups.
CACHE_TTL_SECONDS = 300

# Default root = the running Guaardvark repo. config.py always sets GUAARDVARK_ROOT
# (resolving it to a real, writable path), so import from there rather than baking
# in a machine-specific literal.
def _default_root() -> Path:
    from backend.config import GUAARDVARK_ROOT
    return Path(GUAARDVARK_ROOT).resolve()


def _storage_dir() -> Path:
    from backend.config import GUAARDVARK_ROOT
    return Path(os.environ.get("GUAARDVARK_STORAGE_DIR")
                or os.path.join(str(GUAARDVARK_ROOT), "data"))


def _cache_path_for(root: Path) -> Path:
    """Per-root cache file. Uses the path's last 3 segments as the key so multiple
    code folders (DocumentsPage future use case) each get their own snapshot."""
    cache_dir = _storage_dir() / "cache" / "system_map"
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Slug = last few path segments, sanitized
    parts = [p for p in root.parts if p not in ("", "/", "\\")]
    slug = "_".join(parts[-3:]).replace("/", "_") or "default"
    return cache_dir / f"{slug}.json"


def _is_fresh(cache_file: Path) -> bool:
    if not cache_file.is_file():
        return False
    age = time.time() - cache_file.stat().st_mtime
    return age < CACHE_TTL_SECONDS


def _resolve_root(root_arg: str | None):
    """(root_path, None) on success, or (None, (json_error, status))."""
    try:
        root = Path(root_arg).resolve() if root_arg else _default_root()
    except Exception as exc:
        return None, (jsonify({"success": False, "error": f"Invalid root: {exc}"}), 400)
    if not root.is_dir():
        return None, (jsonify({"success": False, "error": f"Not a directory: {root}"}), 400)
    return root, None


def _load_or_compute(root: Path, refresh: bool):
    """Return (payload_dict, None) or (None, (json_error, status)). Serves the
    disk-cached snapshot unless stale or refresh=1."""
    cache_file = _cache_path_for(root)
    if not refresh and _is_fresh(cache_file):
        try:
            data = json.loads(cache_file.read_text())
            data["_cache"] = {
                "hit": True,
                "age_seconds": int(time.time() - cache_file.stat().st_mtime),
                "ttl_seconds": CACHE_TTL_SECONDS,
            }
            return data, None
        except Exception as exc:
            logger.warning(f"Cache read failed for {cache_file}: {exc}; re-computing")

    try:
        from backend.services.system_mapper import codebase_map
        t0 = time.time()
        smap = codebase_map(root)
        elapsed = time.time() - t0
        logger.info(f"Generated system map for {root} in {elapsed:.2f}s "
                    f"({smap.file_count} files, {len(smap.findings)} findings)")
    except Exception as exc:
        logger.exception("system map computation failed")
        return None, (jsonify({"success": False, "error": f"map failed: {exc}"}), 500)

    payload = smap.to_dict()
    try:
        cache_file.write_text(json.dumps(payload))
    except Exception as exc:
        logger.warning(f"Failed to write cache {cache_file}: {exc}")
    payload["_cache"] = {
        "hit": False,
        "computed_in_seconds": round(elapsed, 2),
        "ttl_seconds": CACHE_TTL_SECONDS,
    }
    return payload, None


@system_map_bp.route("/snapshot", methods=["GET"])
def snapshot():
    """Return the SystemMap JSON for the requested root (default: Guaardvark itself)."""
    root, err = _resolve_root(request.args.get("root"))
    if err:
        return err
    payload, err = _load_or_compute(root, request.args.get("refresh") == "1")
    if err:
        return err
    return jsonify(payload)


@system_map_bp.route("/findings", methods=["GET"])
def findings():
    """Ranked, lightweight findings list for the findings panel.

    Query: ?root=&severity=high,medium&kind=ghost-endpoint&include_dismissed=1
    Returns findings only (not the full graph) so the panel can poll cheaply.
    """
    from backend.services.system_mapper import actions

    root, err = _resolve_root(request.args.get("root"))
    if err:
        return err
    payload, err = _load_or_compute(root, request.args.get("refresh") == "1")
    if err:
        return err

    include_dismissed = request.args.get("include_dismissed") == "1"
    items = actions.ranked_findings(payload, root, include_dismissed=include_dismissed)

    sev_filter = {s for s in (request.args.get("severity") or "").split(",") if s}
    kind_filter = {k for k in (request.args.get("kind") or "").split(",") if k}
    if sev_filter:
        items = [f for f in items if f.get("severity") in sev_filter]
    if kind_filter:
        items = [f for f in items if f.get("kind") in kind_filter]

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


@system_map_bp.route("/findings/<finding_id>/dispatch", methods=["POST"])
def dispatch(finding_id):
    """Send a finding to the self-improvement agent as a directed task."""
    from backend.services.system_mapper import actions

    body = request.get_json(silent=True) or {}
    root, err = _resolve_root(body.get("root"))
    if err:
        return err
    payload, err = _load_or_compute(root, refresh=False)
    if err:
        return err

    finding = actions.find_finding(payload, finding_id)
    if finding is None:
        return jsonify({"success": False, "error": "finding not found"}), 404

    try:
        result = actions.dispatch_finding(finding, priority=body.get("priority", "medium"))
    except Exception as exc:
        logger.exception("dispatch failed")
        return jsonify({"success": False, "error": str(exc)}), 500
    return jsonify({"success": result.get("success", False), "result": result,
                    "finding_id": finding_id})


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


@system_map_bp.route("/health", methods=["GET"])
def health():
    """Smoke endpoint — confirms the analyzer module imports and registers cleanly."""
    try:
        from backend.services.system_mapper import codebase_map  # noqa: F401
        return jsonify({"status": "ok", "default_root": str(_default_root())})
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500
