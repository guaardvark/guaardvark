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

# Default root = the running Guaardvark repo. GUAARDVARK_ROOT is set in config.
def _default_root() -> Path:
    return Path(os.environ.get("GUAARDVARK_ROOT", "/home/llamax1/LLAMAX8")).resolve()


def _cache_path_for(root: Path) -> Path:
    """Per-root cache file. Uses the path's last 3 segments as the key so multiple
    code folders (DocumentsPage future use case) each get their own snapshot."""
    storage = Path(os.environ.get("GUAARDVARK_STORAGE_DIR", "/home/llamax1/LLAMAX8/data"))
    cache_dir = storage / "cache" / "system_map"
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


@system_map_bp.route("/snapshot", methods=["GET"])
def snapshot():
    """Return the SystemMap JSON for the requested root (default: Guaardvark itself)."""
    root_arg = request.args.get("root")
    refresh = request.args.get("refresh") == "1"

    try:
        root = Path(root_arg).resolve() if root_arg else _default_root()
    except Exception as exc:
        return jsonify({"success": False, "error": f"Invalid root: {exc}"}), 400

    if not root.is_dir():
        return jsonify({"success": False, "error": f"Not a directory: {root}"}), 400

    cache_file = _cache_path_for(root)

    if not refresh and _is_fresh(cache_file):
        try:
            data = json.loads(cache_file.read_text())
            data["_cache"] = {
                "hit": True,
                "age_seconds": int(time.time() - cache_file.stat().st_mtime),
                "ttl_seconds": CACHE_TTL_SECONDS,
            }
            return jsonify(data)
        except Exception as exc:
            logger.warning(f"Cache read failed for {cache_file}: {exc}; re-computing")

    # Cold cache or refresh — run the analyzer.
    try:
        from backend.services.system_mapper import codebase_map
        t0 = time.time()
        smap = codebase_map(root)
        elapsed = time.time() - t0
        logger.info(f"Generated system map for {root} in {elapsed:.2f}s "
                    f"({smap.file_count} files, {len(smap.findings)} findings)")
    except Exception as exc:
        logger.exception("system map computation failed")
        return jsonify({"success": False, "error": f"map failed: {exc}"}), 500

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
    return jsonify(payload)


@system_map_bp.route("/health", methods=["GET"])
def health():
    """Smoke endpoint — confirms the analyzer module imports and registers cleanly."""
    try:
        from backend.services.system_mapper import codebase_map  # noqa: F401
        return jsonify({"status": "ok", "default_root": str(_default_root())})
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500
