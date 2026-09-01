#!/usr/bin/env python3
"""Time a video model through the real generation path and record what the
card did while it rendered.

One run = one batch of one clip queued at /api/batch-video/generate/text (or
/generate/image with --image), polled to completion, with nvidia-smi sampled
every half second and the ComfyUI process's peak resident memory (VmHWM) read
from /proc. Each run appends one JSON line to the output file, so a matrix of
runs is a matrix of lines, and the numbers that end up as registry comments
("measured against ...") have a record behind them.

Examples:
  scripts/bench_video_render.py --model minimax-h3-int8 --width 864 --height 480 \
      --frames 124 --steps 20 --label "run1 480p standard pytorch"
  scripts/bench_video_render.py --model minimax-h3-int8 --speed-profile turbo-8 \
      --width 864 --height 480 --frames 124 --label "run5 480p turbo-8 ck"

The backend, and ComfyUI through it, must already be running with the flags
under test (the attention backend is a launch flag of the ComfyUI plugin).
Nothing here installs or downloads anything.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "logs" / "bench_video_render.jsonl"


def _gpu_sample() -> tuple[int, int]:
    """(used_mb, util_pct) from nvidia-smi, or (0, 0) when unavailable."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip().splitlines()[0]
        used, util = (int(x.strip()) for x in out.split(","))
        return used, util
    except Exception:
        return 0, 0


def _comfy_pid() -> int | None:
    pid_file = ROOT / "pids" / "comfyui.pid"
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        return None


def _vm_hwm_mb(pid: int | None) -> int:
    if not pid:
        return 0
    try:
        for line in (Path("/proc") / str(pid) / "status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


class _Sampler(threading.Thread):
    def __init__(self, interval: float = 0.5):
        super().__init__(daemon=True)
        self.interval = interval
        self.peak_used_mb = 0
        self.util_samples: list[int] = []
        self.busy_started_at: float | None = None
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
            used, util = _gpu_sample()
            self.peak_used_mb = max(self.peak_used_mb, used)
            self.util_samples.append(util)
            if util >= 50 and self.busy_started_at is None:
                self.busy_started_at = time.time()
            self._halt.wait(self.interval)

    def stop(self):
        self._halt.set()


def _steps_per_second_from_log(log_path: Path, since_offset: int) -> tuple[float | None, int]:
    """Parse ComfyUI's tqdm sampler lines written after `since_offset`.
    Returns (seconds per step from the last complete bar, sampler line count)."""
    try:
        with log_path.open("rb") as fh:
            fh.seek(since_offset)
            text = fh.read().decode(errors="replace")
    except Exception:
        return None, 0
    # tqdm writes "100%|██████| 20/20 [01:40<00:00,  5.02s/it]" (or "it/s").
    import re
    best = None
    count = 0
    for chunk in text.replace("\r", "\n").splitlines():
        m = re.search(r"([0-9.]+)\s*(s/it|it/s)\]", chunk)
        if not m:
            continue
        count += 1
        try:
            val = float(m.group(1))
        except ValueError:
            continue
        best = val if m.group(2) == "s/it" else (1.0 / val if val else None)
    return best, count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=os.environ.get("GUAARDVARK_API", "http://127.0.0.1:5000/api"))
    ap.add_argument("--model", required=True)
    ap.add_argument("--prompt", default=(
        "A golden retriever runs along a sunlit beach at low tide, water splashing "
        "around its paws, camera tracking alongside at knee height; waves and "
        "seagulls are heard, no music."
    ))
    ap.add_argument("--image", help="first frame for the image route")
    ap.add_argument("--guide-audio", help="audio file anchored at --guide-frame (models with audio_in)")
    ap.add_argument("--guide-frame", type=int, default=0)
    ap.add_argument("--guide-seconds", type=float, default=0.0, help="cut the guide to this many seconds (0 = clip length)")
    ap.add_argument("--width", type=int, default=864)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--frames", type=int, default=124)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--steps", type=int, default=0, help="0 = model default / profile")
    ap.add_argument("--speed-profile", default=None)
    ap.add_argument("--style-embedding", default=None)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--label", default="")
    ap.add_argument("--style", default="none",
                    help="prompt style; anything but 'none' runs the family's prompt enhancer/compiler")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--timeout", type=int, default=7200)
    args = ap.parse_args()

    comfy_log = ROOT / "logs" / "comfyui.log"
    log_offset = comfy_log.stat().st_size if comfy_log.exists() else 0
    comfy_pid = _comfy_pid()
    hwm_before = _vm_hwm_mb(comfy_pid)

    body = {
        "model": args.model,
        "width": args.width, "height": args.height,
        "duration_frames": args.frames, "fps": args.fps,
        "num_inference_steps": args.steps or 0,
        "seed": args.seed,
        "interpolation_multiplier": 1,
        "enhance_prompt": args.style != "none",
        "prompt_style": args.style,
        "metadata": {"display_name": f"bench {args.label or args.model}", "bench": True},
    }
    if args.speed_profile:
        body["speed_profile"] = args.speed_profile
    if args.style_embedding:
        body["style_embedding"] = args.style_embedding
    guides = []
    if args.guide_audio:
        guides = [[{"kind": "audio", "path": os.path.abspath(args.guide_audio), "frame_idx": args.guide_frame,
                    "duration_s": args.guide_seconds}]]
    if args.image:
        body["image_paths"] = [args.image]
        body["prompt"] = args.prompt
        if guides:
            body["guides"] = guides
        route = "/batch-video/generate/image"
    else:
        body["prompts"] = [args.prompt]
        if guides:
            body["guides"] = guides
        route = "/batch-video/generate/text"

    sampler = _Sampler()
    sampler.start()
    t0 = time.time()
    resp = requests.post(args.base + route, json=body, timeout=60)
    payload = resp.json()
    if not payload.get("success"):
        sampler.stop()
        print("queue refused:", payload.get("error") or payload, file=sys.stderr)
        return 2
    batch_id = payload["data"]["batch_id"]
    print(f"queued {batch_id} ({args.label or args.model})")

    status = {}
    while time.time() - t0 < args.timeout:
        time.sleep(3)
        try:
            status = requests.get(f"{args.base}/batch-video/status/{batch_id}", timeout=30).json().get("data") or {}
        except Exception:
            continue
        if status.get("status") in ("completed", "error", "cancelled"):
            break
    t1 = time.time()
    sampler.stop()
    sampler.join(timeout=2)

    s_per_step, bars = _steps_per_second_from_log(comfy_log, log_offset)
    results = status.get("results") or []
    first = results[0] if results else {}
    record = {
        "label": args.label,
        "model": args.model,
        "guide_audio": bool(args.guide_audio),
        "speed_profile": args.speed_profile,
        "style_embedding": args.style_embedding,
        "canvas": f"{args.width}x{args.height}",
        "frames": args.frames,
        "steps_requested": args.steps,
        "seed": args.seed,
        "image": bool(args.image),
        "status": status.get("status"),
        "error": first.get("error") or status.get("error"),
        "video_path": first.get("video_path"),
        "wall_s": round(t1 - t0, 1),
        "gpu_busy_after_s": round(sampler.busy_started_at - t0, 1) if sampler.busy_started_at else None,
        "s_per_step": s_per_step,
        "sampler_bars_seen": bars,
        "vram_peak_mb": sampler.peak_used_mb,
        "ram_hwm_before_mb": hwm_before,
        "ram_hwm_after_mb": _vm_hwm_mb(comfy_pid),
        "util_mean_pct": round(sum(sampler.util_samples) / len(sampler.util_samples), 1) if sampler.util_samples else None,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    print(json.dumps(record, indent=2))
    return 0 if record["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
