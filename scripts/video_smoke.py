#!/usr/bin/env python3
"""Render one short clip with every installed video model and print a pass/fail
table, so "test the pipeline for every downloaded model" is one command.

For each model /api/batch-video/models lists as ready with a t2v or i2v mode,
and each of those modes, one clip is queued at /api/batch-video/generate/text
(or /generate/image) — the route the Studio uses — one at a time, polled to the
end, downloaded, and checked with the post-render frame checker
(backend/services/video_consistency_metrics.inspect_video_frames).

Every clip is the smallest the model accepts, so a full run stays short:
  length      the model's declared minimum (min_frames, min_clip_s) and at least
              one second at its native frame rate, snapped up onto its frame grid
  size        the smallest canvas its tier_defaults declare, else 16:9 with a
              480 px short side on its alignment grid (the backend clamps further)
  steps       min_steps, else default_steps; no speed profile, no upscale, no
              frame interpolation, prompt enhancement off
The prompt, seed and start frame are fixed so runs compare over time.

Results go to data/outputs/video_smoke/<time>/ (gitignored): results.json and
the clips. Nothing is installed or downloaded: a model that is not ready is
skipped, and the route refuses to fetch one. Ctrl-C cancels the clip in flight
and keeps the results so far.

Examples:
  python scripts/video_smoke.py --dry-run
  python scripts/video_smoke.py --keep-going
  python scripts/video_smoke.py --models wan22-5b,ltx23-distilled-fp8 --mode t2v
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROMPT = ("A red fox trots through fresh snow past pine trees in soft morning light, "
          "the camera tracking alongside at shoulder height.")
SEED = 20260925
MIN_SECONDS = 1.0     # a one-frame clip does not exercise a video model's motion
SHORT_SIDE = 480      # the canvas used when a model declares none
TERMINAL = ("completed", "error", "failed", "cancelled")
_RULE = re.compile(r"^(\d+)[nk]\+(\d+)$")


# ── What to render ───────────────────────────────────────────────────────────

def snap_up(frames: int, rule) -> int:
    """``frames`` moved up onto a frame rule such as 4n+1 or 17k+5."""
    m = _RULE.match(str(rule or "").replace(" ", ""))
    if not m:
        return int(frames)
    step, offset = int(m.group(1)), int(m.group(2))
    k = max(0, math.ceil((int(frames) - offset) / step))
    return offset + k * step


def smallest_clip(caps: dict) -> dict:
    """The shortest, smallest, fewest-step clip a model's capability record allows."""
    fps = int(caps.get("native_fps") or 24)
    declared = max(int(caps.get("min_frames") or 1), math.ceil(float(caps.get("min_clip_s") or 0) * fps))
    frames = snap_up(max(declared, math.ceil(MIN_SECONDS * fps)), caps.get("frame_rule"))
    if caps.get("max_frames"):
        frames = min(frames, int(caps["max_frames"]))

    canvases = [(int(t["width"]), int(t["height"])) for t in (caps.get("tier_defaults") or {}).values()
                if t.get("width") and t.get("height")]
    if canvases:
        width, height = min(canvases, key=lambda wh: wh[0] * wh[1])
    else:
        ratios = caps.get("aspect_ratios") or ["16:9"]
        ratio = "16:9" if "16:9" in ratios else ratios[0]
        rw, rh = (int(x) for x in ratio.split(":"))
        align = int(caps.get("dimension_alignment") or 16) or 16
        if caps.get("output_alignment"):  # a two-stage graph's file lands on a coarser grid
            align = math.lcm(align, int(caps["output_alignment"]))
        short = SHORT_SIDE
        long_side = short * max(rw, rh) / min(rw, rh)
        long_side = max(align, int(long_side // align) * align)
        short = max(align, int(short // align) * align)
        width, height = (long_side, short) if rw >= rh else (short, long_side)
    steps = int(caps.get("min_steps") or caps.get("default_steps") or 20)
    return {"duration_frames": frames, "fps": fps, "width": width, "height": height, "num_inference_steps": steps,
            "frame_rule": caps.get("frame_rule")}


def plan(models: list, *, only=None, mode: str = "both") -> tuple:
    """(runs, skipped): one run per ready model and mode, and why others are left out."""
    runs, skipped = [], []
    wanted_modes = ("t2v", "i2v") if mode == "both" else (mode,)
    for row in models:
        mid = row.get("id")
        caps = row.get("capabilities") or {}
        if only and mid not in only:
            continue
        if not caps:
            continue  # a companion file, not a generation model
        modes = [m for m in wanted_modes if m in (caps.get("modes") or [])]
        if not modes:
            if only:
                skipped.append({"model": mid, "reason": f"no {' or '.join(wanted_modes)} mode"})
            continue
        if not row.get("is_ready"):
            missing = ", ".join(row.get("missing_files") or []) or "not installed"
            skipped.append({"model": mid, "reason": f"not ready ({missing})"})
            continue
        clip = smallest_clip(caps)
        for m in modes:
            runs.append({"model": mid, "mode": m, **clip})
    for mid in sorted(set(only or []) - {r.get("id") for r in models}):
        skipped.append({"model": mid, "reason": "not in /api/batch-video/models"})
    return runs, skipped


def request_body(run: dict, start_frame: str | None) -> tuple:
    """(route, body) for one run."""
    body = {
        "model": run["model"], "seed": SEED,
        "duration_frames": run["duration_frames"], "fps": run["fps"],
        "width": run["width"], "height": run["height"],
        "num_inference_steps": run["num_inference_steps"], "steps_explicit": "true",
        "interpolation_multiplier": 1, "upscale": "false",
        "enhance_prompt": "false", "prompt_style": "none",
        "metadata": {"display_name": f"smoke {run['model']} {run['mode']}", "smoke": True},
    }
    if run["mode"] == "i2v":
        return "/batch-video/generate/image", {**body, "prompt": PROMPT, "image_paths": [start_frame]}
    return "/batch-video/generate/text", {**body, "prompts": [PROMPT]}


def write_start_frame(path: Path, width: int = 832, height: int = 480) -> Path:
    """The fixed first frame for image-to-video runs: sky, snow, a sun and trees."""
    import numpy as np
    from PIL import Image

    y = np.linspace(0, 1, height)[:, None, None]
    frame = np.where(y < 0.6, [70, 120, 200] + y * [60, 60, 30], [235, 238, 245]) * np.ones((height, width, 3))
    yy, xx = np.ogrid[:height, :width]
    frame[(yy - height * 0.2) ** 2 + (xx - width * 0.75) ** 2 < (height * 0.07) ** 2] = [250, 220, 140]
    for i, cx in enumerate(np.linspace(0.08, 0.5, 6)):
        top = int(height * (0.35 + 0.03 * (i % 2)))
        for row in range(top, int(height * 0.62)):
            half = int((row - top) * 0.35)
            c = int(cx * width)
            frame[row, max(0, c - half):c + half + 1] = [30, 80, 45]
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(frame.clip(0, 255).astype("uint8")).save(path)
    return path


# ── Running ──────────────────────────────────────────────────────────────────

class Interrupted(Exception):
    def __init__(self, record):
        super().__init__("interrupted")
        self.record = record


def _error_kind(status: dict, first: dict):
    """The failure kind (job_types.RenderErrorKind) when the backend reports one."""
    return (first.get("error_kind") or (first.get("failure") or {}).get("kind")
            or (status.get("failure") or {}).get("kind"))


def _check(clip: Path, run: dict):
    from backend.services.video_consistency_metrics import frame_grid_step, inspect_video_frames

    r = inspect_video_frames(clip, expected_width=run["width"], expected_height=run["height"],
                             expected_frames=run["duration_frames"],
                             frame_tolerance=frame_grid_step(run.get("frame_rule")))
    return {k: r.get(k) for k in ("readable", "width", "height", "frames", "fps")} | {
        "flags": [f["code"] for f in r.get("flags") or []],
        "observations": [f["code"] for f in r.get("observations") or []],
    }


def run_one(session, base: str, run: dict, start_frame, out_dir: Path, timeout_s: float, poll_s: float = 3.0) -> dict:
    route, body = request_body(run, start_frame)
    record = {**run, "status": None, "seconds": None, "error": None, "error_kind": None}
    t0 = time.time()
    payload = session.post(base + route, json=body, timeout=60).json()
    if not payload.get("success"):
        err = payload.get("error")
        record.update(status="refused", seconds=round(time.time() - t0, 1),
                      error=err.get("message") if isinstance(err, dict) else err,
                      error_kind=((err or {}).get("details") or {}).get("failure", {}).get("kind")
                      if isinstance(err, dict) else None)
        return record
    batch_id = payload["data"]["batch_id"]
    record["batch_id"] = batch_id
    status = {}
    try:
        while time.time() - t0 < timeout_s:
            time.sleep(poll_s)
            try:
                status = session.get(f"{base}/batch-video/status/{batch_id}", timeout=30).json().get("data") or {}
            except Exception:  # noqa: BLE001 — keep polling through a restart blip
                continue
            if status.get("status") in TERMINAL:
                break
        else:
            session.post(f"{base}/batch-video/batch/{batch_id}/cancel", timeout=30)
            record.update(status="timeout", seconds=round(time.time() - t0, 1),
                          error=f"no result after {int(timeout_s)}s; cancelled")
            return record
    except KeyboardInterrupt:
        session.post(f"{base}/batch-video/batch/{batch_id}/cancel", timeout=30)
        record.update(status="cancelled", seconds=round(time.time() - t0, 1))
        raise Interrupted(record)
    first = (status.get("results") or [{}])[0]
    record.update(status=status.get("status"), seconds=round(time.time() - t0, 1),
                  error=first.get("error") or status.get("error"), error_kind=_error_kind(status, first))
    if first.get("success") and first.get("video_path"):
        clip = out_dir / f"{run['model']}_{run['mode']}.mp4"
        resp = session.get(f"{base}/batch-video/video/{batch_id}/{first['video_path']}", timeout=300)
        if resp.ok:
            clip.write_bytes(resp.content)
            record["clip"] = clip.name
            record["check"] = _check(clip, run)
    return record


def passed(record: dict) -> bool:
    check = record.get("check") or {}
    return record.get("status") == "completed" and bool(check.get("readable")) and not check.get("flags")


def table(records: list) -> str:
    head = ("model", "mode", "result", "seconds", "frames", "size", "flags", "error kind")
    rows = [head]
    for r in records:
        check = r.get("check") or {}
        size = f"{check['width']}x{check['height']}" if check.get("width") else "-"
        rows.append((
            r["model"], r["mode"], "PASS" if passed(r) else (r.get("status") or "FAIL").upper(),
            f"{r['seconds']:.0f}" if r.get("seconds") is not None else "-",
            str(check.get("frames") or "-"), size,
            ",".join(check.get("flags") or []) or "-", r.get("error_kind") or ("-" if passed(r) else "?"),
        ))
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(head))]
    lines = ["  ".join(str(c).ljust(w) for c, w in zip(row, widths)) for row in rows]
    return "\n".join([lines[0], "  ".join("-" * w for w in widths), *lines[1:]])


def default_base() -> str:
    api = (os.environ.get("GUAARDVARK_API") or "").strip()
    if api:
        return api.rstrip("/")
    url = (os.environ.get("GUAARDVARK_URL") or "").strip()
    return f"{url.rstrip('/')}/api" if url else "http://127.0.0.1:5000/api"


def _write(out_dir: Path, records: list, skipped: list, started: str) -> Path:
    path = out_dir / "results.json"
    path.write_text(json.dumps({
        "started": started, "prompt": PROMPT, "seed": SEED,
        "passed": sum(passed(r) for r in records), "total": len(records),
        "runs": records, "skipped": skipped,
    }, indent=2))
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=default_base(),
                    help="API root: $GUAARDVARK_API, else $GUAARDVARK_URL/api, else http://127.0.0.1:5000/api")
    ap.add_argument("--models", help="comma-separated model ids (default: every ready model)")
    ap.add_argument("--mode", choices=("t2v", "i2v", "both"), default="both")
    ap.add_argument("--dry-run", action="store_true", help="print the requests; queue nothing")
    ap.add_argument("--keep-going", action="store_true", help="carry on after a clip fails")
    ap.add_argument("--image", help="start frame for i2v, a path on the backend's machine "
                                    "(default: a fixed frame written under the output folder)")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds per clip before it is cancelled")
    ap.add_argument("--out", help="output folder (default data/outputs/video_smoke/<time>)")
    args = ap.parse_args(argv)

    import requests

    session = requests.Session()
    listing = session.get(f"{args.base}/batch-video/models", timeout=60).json()
    models = ((listing.get("data") or {}).get("models")) or []
    only = [m.strip() for m in (args.models or "").split(",") if m.strip()] or None
    runs, skipped = plan(models, only=only, mode=args.mode)
    started = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else ROOT / "data" / "outputs" / "video_smoke" / started

    start_frame = args.image
    if args.dry_run:
        for run in runs:
            route, body = request_body(run, start_frame or str(out_dir / "start_frame.png"))
            print(json.dumps({"route": route, "body": body}))
        for s in skipped:
            print(f"skip {s['model']}: {s['reason']}")
        print(f"{len(runs)} clip(s); nothing was queued.")
        return 0
    if not runs:
        print("No ready video model to render.", *(f"skip {s['model']}: {s['reason']}" for s in skipped), sep="\n")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    if start_frame is None and any(r["mode"] == "i2v" for r in runs):
        start_frame = str(write_start_frame(out_dir / "start_frame.png"))
    records = []
    try:
        for i, run in enumerate(runs, 1):
            print(f"[{i}/{len(runs)}] {run['model']} {run['mode']} "
                  f"{run['width']}x{run['height']} {run['duration_frames']}f {run['num_inference_steps']} steps",
                  flush=True)
            record = run_one(session, args.base, run, start_frame, out_dir, args.timeout)
            records.append(record)
            _write(out_dir, records, skipped, started)
            if not passed(record) and not args.keep_going:
                print("stopping at the first failure (--keep-going renders the rest)")
                break
    except Interrupted as stop:
        records.append(stop.record)
        _write(out_dir, records, skipped, started)
        print(f"\ninterrupted; the clip in flight was cancelled.\n{table(records)}\nresults: {out_dir / 'results.json'}")
        return 130
    print(table(records))
    for s in skipped:
        print(f"skip {s['model']}: {s['reason']}")
    print(f"results: {_write(out_dir, records, skipped, started)}")
    return 0 if records and all(passed(r) for r in records) and len(records) == len(runs) else 1


if __name__ == "__main__":
    sys.exit(main())
