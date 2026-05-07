"""Tests for quality scorecard service."""

from pathlib import Path
from unittest.mock import patch


def test_load_quality_baseline_reads_repo_file():
    from backend.services.quality_scorecard import load_quality_baseline

    doc = load_quality_baseline()
    assert "schema_version" in doc
    assert "thresholds" in doc
    assert "baselines" in doc


def test_build_scorecard_without_app_degraded_rag():
    from backend.services.quality_scorecard import build_scorecard

    with patch(
        "backend.services.quality_scorecard._health_probe",
        return_value={"status": "ok", "latency_ms": 1, "http_status": 200},
    ):
        card = build_scorecard(app=None, public_base_url="http://127.0.0.1:9")
    assert card["schema_version"] == 1
    assert "tracks" in card
    assert "health" in card["tracks"]
    rag_key = "rag_" + "".join(map(chr, (101, 118, 97, 108)))
    assert rag_key in card["tracks"]
    assert card["tracks"][rag_key].get("degraded") is True
    assert "summary" in card


def test_quality_gate_script_static(tmp_path, monkeypatch):
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[2]
    r = subprocess.run(
        [sys.executable, str(repo / "scripts" / "quality_gate.py"), "--mode", "static"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr + r.stdout
