#!/usr/bin/env python3
"""Measure how well the chat answers questions about Guaardvark's own code.

Each question in backend/tests/fixtures/self_analysis/questions.json goes
through the real chat path (POST /api/chat/unified, plan mode off) in a fresh
session. The reply is scored by code:

  found      it names a file where the answer lives (any gold file)
  grounded   every file path it names exists in the checkout
  citations  every path:line it gives is inside that file

A question passes when all three hold. Approval cards are declined. Every
session is deleted. A JSON log goes to logs/self_analysis_evals/.

    backend/venv/bin/python scripts/eval_self_analysis.py --repeat 1
    backend/venv/bin/python scripts/eval_self_analysis.py --model gemma4:e2b --repeat 3

--model switches the chat model for the run and back, as eval_plan_mode.py does.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "backend" / "tests" / "fixtures" / "self_analysis" / "questions.json"
OUT_DIR = ROOT / "logs" / "self_analysis_evals"
API = "http://127.0.0.1:5000"
TURN_TIMEOUT_S = 420
PATH_RE = re.compile(r"(?<![\w/.-])((?:backend|frontend|cli|scripts|plugins)/[\w./-]+\.(?:py|jsx?|tsx?|json|sh|md))(?::(\d+))?")


def score(reply: str, gold: list[str]) -> dict:
    refs = PATH_RE.findall(reply or "")
    paths = {p for p, _ in refs}
    found = any(g in paths or g in (reply or "") for g in gold)
    missing = sorted(p for p in paths if not (ROOT / p).is_file())
    bad_lines = []
    for p, line in refs:
        if line and (ROOT / p).is_file():
            n = sum(1 for _ in (ROOT / p).open(encoding="utf-8", errors="replace"))
            if not 1 <= int(line) <= n:
                bad_lines.append(f"{p}:{line}")
    checks = {"found": found, "grounded": not missing, "citations": not bad_lines}
    return {"checks": checks, "pass": all(checks.values()), "missing": missing, "bad_lines": bad_lines}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--tag", default="")
    args = parser.parse_args(argv)

    import requests
    import socketio
    from scripts.eval_plan_mode import active_model, gpu_is_busy, switch_model

    busy = gpu_is_busy()
    if busy:
        print(f"The GPU is in use by {busy}; not measuring.", file=sys.stderr)
        return 2
    items = [i for i in json.loads(FIXTURE.read_text())["items"] if not args.only or i["id"] in args.only]
    original = active_model()
    model = args.model or original
    if not switch_model(model):
        print(f"Could not switch the chat model to {model}.", file=sys.stderr)
        return 2

    events: dict = {}
    sio = socketio.Client(reconnection=True)

    @sio.on("*")
    def _any(event, data):
        if isinstance(data, dict) and data.get("session_id"):
            events.setdefault(data["session_id"], []).append((event, data))

    @sio.on("chat:tool_approval_request")
    def _decline(data):
        sio.emit("chat:tool_approval_response", {"session_id": data.get("session_id"), "approved": False,
                                                  "approval_id": data.get("approval_id"),
                                                  "request_id": data.get("request_id")})

    sio.connect(API, headers={"Cookie": "self_analysis_eval=1"}, transports=["websocket"], wait_timeout=10)
    results = []
    try:
        for rep in range(1, args.repeat + 1):
            for item in items:
                sid = f"sa-eval-{uuid.uuid4().hex[:10]}"
                sio.emit("chat:join", {"session_id": sid})
                time.sleep(0.3)
                t0 = time.time()
                requests.post(f"{API}/api/chat/unified", timeout=60,
                              json={"session_id": sid, "message": item["question"], "options": {}})
                data, end = None, time.time() + TURN_TIMEOUT_S
                while time.time() < end and data is None:
                    for ev, d in events.get(sid, []):
                        if ev in ("chat:complete", "chat:error"):
                            data = d
                            break
                    time.sleep(0.5)
                reply = (data or {}).get("response") or (data or {}).get("error") or ""
                tools = [d.get("tool") for ev, d in events.get(sid, []) if ev == "chat:tool_result"]
                row = {"id": item["id"], "rep": rep, "secs": round(time.time() - t0, 1), **score(reply, item["gold"]),
                       "tools": tools, "reply": reply[:1500]}
                requests.delete(f"{API}/api/enhanced-chat/sessions/{sid}", timeout=30)
                results.append(row)
                flag = "PASS" if row["pass"] else "FAIL " + ",".join(k for k, v in row["checks"].items() if not v)
                print(f"{item['id']:<20} r{rep} {row['secs']:>6.1f}s {flag}  tools={tools}", flush=True)
    finally:
        sio.disconnect()
        if original and original != model:
            switch_model(original)

    n = len(results)
    summary = {"model": model, "n": n, "passed": sum(r["pass"] for r in results),
               "found": sum(r["checks"]["found"] for r in results),
               "invented_paths": sum(len(r["missing"]) for r in results),
               "bad_citations": sum(len(r["bad_lines"]) for r in results),
               "median_secs": sorted(r["secs"] for r in results)[n // 2] if n else None,
               "measured": dt.date.today().isoformat(), "tag": args.tag}
    print(json.dumps(summary, indent=1))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{model.replace(':', '_')}{'-' + args.tag if args.tag else ''}.json"
    out.write_text(json.dumps({"summary": summary, "results": results}, indent=1))
    print(f"log: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
