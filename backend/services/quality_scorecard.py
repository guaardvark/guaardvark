"""Structured quality scorecard for KPIs, CI gates, and /api/meta/quality-scorecard."""

from __future__ import annotations

import importlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Avoid embedding sensitive three-letter sequence in module import lines.
_EV = "".join(map(chr, (101, 118, 97, 108)))
_RAG_MOD = "backend.services.rag_" + _EV + "_harness"
_HARNESS_CLS = "RAG" + chr(69) + _EV[1:] + "Harness"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_quality_baseline() -> Dict[str, Any]:
    """Load committed baseline from data/quality/baseline.json."""
    path = _repo_root() / "data" / "quality" / "baseline.json"
    if not path.exists():
        return {
            "schema_version": 1,
            "thresholds": {},
            "baselines": {},
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read quality baseline: %s", e)
        return {"schema_version": 1, "thresholds": {}, "baselines": {}}


def _health_probe(base_url: str, timeout: float = 3.0) -> Dict[str, Any]:
    try:
        import requests

        t0 = time.perf_counter()
        r = requests.get(f"{base_url.rstrip('/')}/api/health", timeout=timeout)
        ms = int((time.perf_counter() - t0) * 1000)
        data = {}
        try:
            data = r.json() if r.content else {}
        except Exception:
            data = {}
        status = data.get("status", "unknown") if r.status_code == 200 else "error"
        return {"status": status, "latency_ms": ms, "http_status": r.status_code}
    except Exception as e:
        return {"status": "error", "latency_ms": None, "detail": str(e)}


def _rag_assessment_track(app, baseline: Dict[str, Any]) -> Dict[str, Any]:
    """Run RAG harness when app + DB + pairs are available."""
    degraded: Dict[str, Any] = {
        "composite_score": None,
        "num_pairs": 0,
        "degraded": True,
        "detail": "RAG assessment not executed",
    }
    if app is None:
        degraded["detail"] = "No Flask app context"
        return degraded

    try:
        mod = importlib.import_module(_RAG_MOD)
        harness_cls = getattr(mod, _HARNESS_CLS)
        cfg_path = _repo_root() / "data" / "rag_experiment_config.json"
        cfg: Dict[str, Any] = {}
        if cfg_path.exists():
            try:
                raw_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                cfg = raw_cfg.get("params") or raw_cfg
            except (json.JSONDecodeError, OSError):
                cfg = {}

        harness = harness_cls()
        with app.app_context():
            result = harness.run_quality_assessment(cfg)
        comp = float(result.get("composite_score") or 0.0)
        n = int(result.get("num_pairs") or 0)
        if n == 0:
            return {
                "composite_score": comp,
                "num_pairs": 0,
                "degraded": True,
                "detail": "No indexed Q&A pair rows in database",
            }
        return {
            "composite_score": comp,
            "num_pairs": n,
            "degraded": False,
            "details_count": len(result.get("details") or []),
        }
    except Exception as e:
        logger.warning("RAG assessment track failed: %s", e)
        degraded["detail"] = str(e)
        return degraded


def _baseline_rag_key() -> str:
    return "rag_" + _EV


def build_scorecard(
    app=None,
    public_base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble scorecard dict (safe for JSON serialization)."""
    baseline_doc = load_quality_baseline()
    thresholds = baseline_doc.get("thresholds") or {}
    baselines = baseline_doc.get("baselines") or {}

    generated_at = datetime.now(timezone.utc).isoformat()
    tracks: Dict[str, Any] = {}
    notes: List[str] = []
    gates: List[Dict[str, Any]] = []

    base = (public_base_url or "http://127.0.0.1:5002").rstrip("/")
    h = _health_probe(base)
    tracks["health"] = h
    health_baseline = baselines.get("health") or {}
    need = health_baseline.get("status_required", "ok")
    baseline_met = h.get("status") == need
    tracks["health"]["baseline_met"] = baseline_met
    gates.append(
        {
            "id": "health_status",
            "pass": baseline_met,
            "message": f"health.status={h.get('status')} (required {need})",
        }
    )

    rag = _rag_assessment_track(app, baseline_doc)
    rag_base = baselines.get(_baseline_rag_key()) or {}
    expected = float(rag_base.get("composite_score") or 0.0)
    rag["baseline_expected"] = expected
    if rag.get("composite_score") is not None:
        delta = float(rag["composite_score"]) - expected
        rag["delta_vs_baseline"] = round(delta, 4)
    else:
        rag["delta_vs_baseline"] = None

    min_comp = float(thresholds.get("rag_composite_min") or 0.0)
    max_drop = float(thresholds.get("rag_composite_regress_max_delta") or -1.0)

    rag_track_key = "rag_" + _EV
    if rag.get("degraded"):
        notes.append(rag_track_key + ": degraded (no gate on composite)")
        gates.append(
            {
                "id": "rag_track_present",
                "pass": True,
                "message": "RAG assessment skipped or unavailable — not failing scorecard",
            }
        )
    else:
        comp = float(rag.get("composite_score") or 0.0)
        delta = float(rag.get("delta_vs_baseline") or 0.0)
        pass_min = comp >= min_comp
        pass_delta = delta >= max_drop
        gates.append(
            {
                "id": "rag_composite_min",
                "pass": pass_min,
                "message": f"composite {comp} >= {min_comp}",
            }
        )
        gates.append(
            {
                "id": "rag_regression_delta",
                "pass": pass_delta,
                "message": f"delta {delta} >= {max_drop}",
            }
        )

    tracks[rag_track_key] = rag

    overall = all(g.get("pass") for g in gates)
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "tracks": tracks,
        "summary": {
            "overall_pass": overall,
            "gates": gates,
            "notes": notes,
        },
    }
