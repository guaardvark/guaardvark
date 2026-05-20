#!/usr/bin/env python3
"""Quality gate for CI and release pipelines.

  python scripts/quality_gate.py --mode static   # no server required
  python scripts/quality_gate.py --mode full     # needs running backend (optional)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _static_gate() -> int:
    baseline_path = REPO / "data" / "quality" / "baseline.json"
    if not baseline_path.exists():
        print("FAIL: missing data/quality/baseline.json", file=sys.stderr)
        return 1
    try:
        doc = json.loads(baseline_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"FAIL: invalid baseline JSON: {e}", file=sys.stderr)
        return 1
    for key in ("schema_version", "thresholds", "baselines"):
        if key not in doc:
            print(f"FAIL: baseline missing key {key!r}", file=sys.stderr)
            return 1
    # Syntax-check scorecard module without importing backend package graph
    import py_compile

    sc_path = REPO / "backend" / "services" / "quality_scorecard.py"
    try:
        py_compile.compile(str(sc_path), doraise=True)
    except py_compile.PyCompileError as e:
        print(f"FAIL: quality_scorecard.py compile: {e}", file=sys.stderr)
        return 1
    print("OK: static quality gate passed")
    return 0


def _full_gate(base_url: str) -> int:
    import requests

    try:
        r = requests.get(f"{base_url.rstrip('/')}/api/meta/quality-scorecard", timeout=30)
        payload = r.json()
    except Exception as e:
        print(f"FAIL: could not fetch scorecard: {e}", file=sys.stderr)
        return 1
    if r.status_code != 200:
        print(f"FAIL: HTTP {r.status_code}: {payload}", file=sys.stderr)
        return 1
    data = payload.get("data", payload)
    summary = data.get("summary") or {}
    if not summary.get("overall_pass"):
        print(f"FAIL: scorecard gates: {json.dumps(summary, indent=2)}", file=sys.stderr)
        return 1
    print("OK: full quality gate passed")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=("static", "full"), default="static")
    p.add_argument("--base-url", default="http://127.0.0.1:5002")
    args = p.parse_args()
    if args.mode == "static":
        return _static_gate()
    return _full_gate(args.base_url)


if __name__ == "__main__":
    raise SystemExit(main())
