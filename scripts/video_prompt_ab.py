#!/usr/bin/env python3
"""A/B the prompt style, the enhancer, guidance and the negative prompt on one
video model, same prompt and seed, through the real generation route.

Every variant is one batch of one clip queued at /api/batch-video/generate/text
and polled to completion, one at a time. Guidance and the negative prompt are
always sent explicitly, so the backend's GUAARDVARK_VIDEO_REFERENCE_DEFAULTS
setting does not change what a variant renders. The clips are downloaded next
to a results.json and an index.html that plays them side by side, under
data/outputs/video_prompt_ab/<time>/ (gitignored).

Plans:
  case    the 2026-09-24 report (style 3d_animation, enhancer on, cfg 7.5, the
          enhancer's negative with the identity guard) and one variant per factor
          changed from it (on Wan 14B T2V also the fixed shift 8), plus what
          GUAARDVARK_VIDEO_REFERENCE_DEFAULTS=1 sends
  styles  every prompt style at the values that setting sends: the evidence
          for a model's prompt_styles_withheld
  full    every combination of --styles, --enhance, --cfg and --negative

Examples:
  scripts/video_prompt_ab.py --dry-run
  scripts/video_prompt_ab.py --model wan22-14b --plan case
  scripts/video_prompt_ab.py --model wan22-14b --plan styles --seeds 1234,99
  scripts/video_prompt_ab.py --model ltx23-distilled-fp8 --plan case --frames 97

The backend and ComfyUI must be running and the model installed; nothing here
installs or downloads anything. Ctrl-C cancels the clip in flight and keeps the
results so far.
"""
from __future__ import annotations

import argparse
import html
import itertools
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CASE_PROMPT = ("An outdoor concert on a green lawn, a black cartoon aardvark rapper on stage, "
               "hundreds of fans")
TERMINAL = ("completed", "error", "failed", "cancelled")


def negative_text(kind: str, model: str, style: str, enhance: bool = True) -> str:
    """The negative a variant sends.

    legacy     what the enhancer fills in today: the style negative with the
               identity-bleed guard
    style      the style negative without the guard
    template   the negative the model's reference ComfyUI template ships (Wan, LTX)
    setting    what GUAARDVARK_VIDEO_REFERENCE_DEFAULTS=1 sends for a request
               without a cast member: the model's negative_when_unset, else the
               style negative without the guard when the enhancer runs
    none       nothing; the backend fills in its own default
    """
    from backend.services.video_model_registry import (
        LTX_REFERENCE_NEGATIVE, VIDEO_MODEL_REGISTRY, WAN_REFERENCE_NEGATIVE, model_capabilities,
    )
    from backend.utils.prompt_enhancer import get_default_negative_prompt

    if kind == "legacy":
        return get_default_negative_prompt(style)
    if kind == "style":
        return get_default_negative_prompt(style, identity_guard=False)
    if kind == "template":
        family = (VIDEO_MODEL_REGISTRY.get(model) or {}).get("type")
        return {"wan": WAN_REFERENCE_NEGATIVE, "ltx": LTX_REFERENCE_NEGATIVE}.get(family, "")
    if kind == "setting":
        declared = model_capabilities(model).get("negative_when_unset")
        return declared or (get_default_negative_prompt(style, identity_guard=False) if enhance else "")
    if kind == "none":
        return ""
    raise ValueError(f"unknown negative kind {kind!r}")


def cfg_value(kind: str, model: str):
    from backend.services.video_model_registry import model_capabilities

    if kind == "reference":
        return model_capabilities(model).get("cfg_when_unset")
    return float(kind)


def variant(name: str, model: str, *, style: str, enhance: bool, cfg: str, negative: str,
            wan_sampler_profile=None) -> dict:
    return {"name": name, "style": style, "enhance": enhance, "cfg_kind": cfg, "negative_kind": negative,
            "guidance_scale": cfg_value(cfg, model), "negative_prompt": negative_text(negative, model, style, enhance),
            "wan_sampler_profile": wan_sampler_profile}


def shift_is_scaled(model: str) -> bool:
    """Wan 14B T2V: the one Wan graph still on the resolution-scaled shift unless
    a sampler profile is named (ComfyUIVideoGenerator._wan14b_t2v_shift)."""
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY, model_capabilities

    caps = model_capabilities(model)
    return (VIDEO_MODEL_REGISTRY.get(model) or {}).get("type") == "wan" and not caps.get("supports_i2v")


def plan_variants(plan: str, model: str, *, styles=None, enhance=None, cfgs=None, negatives=None) -> list:
    """The variants a plan renders, in order."""
    from backend.services.video_model_registry import model_capabilities

    caps = model_capabilities(model)
    if not caps:
        raise SystemExit(f"{model} is not a video model in the registry")
    has_cfg = bool(caps.get("cfg"))
    has_negative = bool(caps.get("negative_prompt"))
    if plan == "case":
        base = dict(style="3d_animation", enhance=True, cfg="7.5", negative="legacy")
        out = [variant("reported", model, **base)]
        if has_cfg:
            out.append(variant("cfg-reference", model, **{**base, "cfg": "reference"}))
        if has_negative:
            out.append(variant("negative-no-guard", model, **{**base, "negative": "style"}))
            if negative_text("template", model, "none"):
                out.append(variant("negative-template", model, **{**base, "negative": "template"}))
        scaled = shift_is_scaled(model)
        if scaled:
            out.append(variant("shift-8", model, **base, wan_sampler_profile="official"))
        out.append(variant("enhancer-off", model, **{**base, "enhance": False}))
        out.append(variant("reference-setting", model, **{**base, "cfg": "reference" if has_cfg else "7.5",
                                                          "negative": "setting" if has_negative else "none"},
                           wan_sampler_profile="official" if scaled else None))
        return out
    if plan == "styles":
        chosen = styles or list(caps.get("prompt_styles") or [])
        profile = "official" if shift_is_scaled(model) else None
        return [variant(f"style-{s}", model, style=s, enhance=s != "none", cfg="reference" if has_cfg else "7.5",
                        negative="setting" if has_negative else "none", wan_sampler_profile=profile) for s in chosen]
    if plan == "full":
        out = []
        for s, e, c, n in itertools.product(styles or ["3d_animation"], enhance or [True, False],
                                            cfgs or ["7.5", "reference"], negatives or ["legacy", "setting"]):
            out.append(variant(f"{s}-{'enh' if e else 'raw'}-cfg{c}-neg{n}", model, style=s, enhance=e, cfg=c, negative=n))
        return out
    raise SystemExit(f"unknown plan {plan!r}")


def request_body(v: dict, args, seed: int) -> dict:
    body = {
        "prompts": [args.prompt],
        "model": args.model,
        "width": args.width, "height": args.height,
        "duration_frames": args.frames,
        "num_inference_steps": args.steps,
        "steps_explicit": "true",
        "seed": seed,
        "interpolation_multiplier": args.interpolation,
        "upscale": "false",
        "prompt_style": v["style"],
        "enhance_prompt": "true" if v["enhance"] else "false",
        "negative_prompt": v["negative_prompt"],
        "metadata": {"display_name": f"A/B {args.model} {v['name']} s{seed}", "ab": True},
    }
    if v["guidance_scale"] is not None:
        body["guidance_scale"] = v["guidance_scale"]
    if v.get("wan_sampler_profile"):
        body["wan_sampler_profile"] = v["wan_sampler_profile"]
    if args.fps:
        body["fps"] = args.fps
    return body


def _frame_check(path: Path, width: int, height: int):
    """The post-render checker (backend/services/video_consistency_metrics.py,
    PR #233) when this checkout has it."""
    try:
        from backend.services.video_consistency_metrics import inspect_video_frames
    except ImportError:
        return None
    r = inspect_video_frames(path, expected_width=width, expected_height=height)
    return {"metrics": r.get("metrics"), "flags": [f["code"] for f in r.get("flags") or []]}


def run_variant(session, args, v: dict, seed: int, out_dir: Path) -> dict:
    body = request_body(v, args, seed)
    record = {**{k: v[k] for k in ("name", "style", "enhance", "cfg_kind", "negative_kind",
                                   "guidance_scale", "negative_prompt", "wan_sampler_profile")}, "seed": seed}
    try:
        preview = session.post(f"{args.base}/batch-video/enhance-preview",
                               json={"prompt": args.prompt, "model": args.model, "prompt_style": v["style"],
                                     "width": args.width, "height": args.height}, timeout=30).json()
        record["positive_prompt"] = (preview.get("data") or {}).get("enhanced_prompt") if v["enhance"] else args.prompt
    except Exception as e:  # noqa: BLE001 — the preview is for the report only
        record["positive_prompt"] = None
        record["preview_error"] = str(e)
    t0 = time.time()
    payload = session.post(f"{args.base}/batch-video/generate/text", json=body, timeout=60).json()
    if not payload.get("success"):
        record.update(status="refused", error=payload.get("error"))
        return record
    batch_id = payload["data"]["batch_id"]
    record["batch_id"] = batch_id
    print(f"  queued {batch_id}", flush=True)
    status = {}
    try:
        while time.time() - t0 < args.timeout:
            time.sleep(3)
            try:
                status = session.get(f"{args.base}/batch-video/status/{batch_id}", timeout=30).json().get("data") or {}
            except Exception:  # noqa: BLE001 — keep polling through a restart blip
                continue
            if status.get("status") in TERMINAL:
                break
        else:
            record["timed_out"] = True
    except KeyboardInterrupt:
        session.post(f"{args.base}/batch-video/batch/{batch_id}/cancel", timeout=30)
        record.update(status="cancelled", wall_s=round(time.time() - t0, 1))
        raise _Interrupted(record)
    first = (status.get("results") or [{}])[0]
    record.update(status=status.get("status"), error=first.get("error") or status.get("error"),
                  wall_s=round(time.time() - t0, 1))
    if first.get("success") and first.get("video_path"):
        clip = out_dir / f"{v['name']}_s{seed}.mp4"
        resp = session.get(f"{args.base}/batch-video/video/{batch_id}/{first['video_path']}", timeout=300)
        if resp.ok:
            clip.write_bytes(resp.content)
            record["clip"] = clip.name
            record["frames"] = _frame_check(clip, args.width, args.height)
    return record


class _Interrupted(Exception):
    def __init__(self, record):
        super().__init__("interrupted")
        self.record = record


def write_report(out_dir: Path, args, records: list) -> None:
    (out_dir / "results.json").write_text(json.dumps(
        {"model": args.model, "prompt": args.prompt, "plan": args.plan, "width": args.width,
         "height": args.height, "frames": args.frames, "steps": args.steps,
         "interpolation": args.interpolation, "variants": records}, indent=2, ensure_ascii=False))
    cells = []
    for r in records:
        video = (f'<video src="{html.escape(r["clip"])}" controls loop muted playsinline></video>'
                 if r.get("clip") else f'<p class="err">{html.escape(str(r.get("error") or r.get("status")))}</p>')
        flags = ", ".join((r.get("frames") or {}).get("flags") or []) or "-"
        cells.append(
            f'<figure>{video}<figcaption><b>{html.escape(r["name"])}</b> seed {r["seed"]}<br>'
            f'style {html.escape(r["style"])}, enhancer {"on" if r["enhance"] else "off"}, '
            f'cfg {r["guidance_scale"]}, negative {html.escape(r["negative_kind"])}'
            f'{", sampler profile " + html.escape(r["wan_sampler_profile"]) if r.get("wan_sampler_profile") else ""}'
            f'<br>flags: {html.escape(flags)}'
            f'<details><summary>text sent</summary><p><i>positive</i> {html.escape(str(r.get("positive_prompt")))}</p>'
            f'<p><i>negative</i> {html.escape(r["negative_prompt"] or "(none)")}</p></details></figcaption></figure>')
    (out_dir / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Video prompt A/B</title><style>"
        "body{font:14px system-ui;margin:16px}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:16px}"
        "video{width:100%}figure{margin:0}.err{color:#b00}</style>"
        f"<h1>{html.escape(args.model)}: {html.escape(args.prompt)}</h1><main>{''.join(cells)}</main>")


def default_base() -> str:
    api = (os.environ.get("GUAARDVARK_API") or "").strip()
    if api:
        return api.rstrip("/")
    url = (os.environ.get("GUAARDVARK_URL") or "").strip()
    return f"{url.rstrip('/')}/api" if url else "http://127.0.0.1:5000/api"


def _csv(value, cast=str):
    return [cast(x.strip()) for x in value.split(",") if x.strip()] if value else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=default_base(),
                    help="API root: $GUAARDVARK_API, else $GUAARDVARK_URL/api, else http://127.0.0.1:5000/api")
    ap.add_argument("--model", default="wan22-14b")
    ap.add_argument("--prompt", default=CASE_PROMPT)
    ap.add_argument("--plan", choices=("case", "styles", "full"), default="case")
    ap.add_argument("--seeds", default="1234", help="comma-separated; every variant renders once per seed")
    ap.add_argument("--width", type=int, default=864)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--frames", type=int, default=81, help="the report asked for 80; Wan's grid is 4n+1")
    ap.add_argument("--fps", type=int, default=0, help="0 = the model's native rate")
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--interpolation", type=int, default=1, help="RIFE multiplier; the report used 2")
    ap.add_argument("--styles", help="styles for --plan styles/full (default: every offered style / 3d_animation)")
    ap.add_argument("--enhance", help="for --plan full: on,off")
    ap.add_argument("--cfg", help="for --plan full: numbers and/or 'reference'")
    ap.add_argument("--negative", help="for --plan full: legacy,style,template,setting,none")
    ap.add_argument("--timeout", type=int, default=7200, help="seconds per clip")
    ap.add_argument("--dry-run", action="store_true", help="print the request bodies, contact nothing")
    ap.add_argument("--out", help="output folder (default data/outputs/video_prompt_ab/<time>)")
    args = ap.parse_args(argv)

    enhance = [x == "on" for x in _csv(args.enhance)] if args.enhance else None
    variants = plan_variants(args.plan, args.model, styles=_csv(args.styles), enhance=enhance,
                             cfgs=_csv(args.cfg), negatives=_csv(args.negative))
    seeds = _csv(args.seeds, int) or [1234]
    if args.dry_run:
        for v, seed in itertools.product(variants, seeds):
            print(json.dumps({"variant": v["name"], "body": request_body(v, args, seed)}, ensure_ascii=False))
        print(f"{len(variants) * len(seeds)} clip(s) of {args.model}; nothing was queued.")
        return 0

    import requests

    out_dir = Path(args.out) if args.out else ROOT / "data" / "outputs" / "video_prompt_ab" / time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    records = []
    try:
        for i, (v, seed) in enumerate(itertools.product(variants, seeds), 1):
            print(f"[{i}/{len(variants) * len(seeds)}] {v['name']} seed {seed}", flush=True)
            records.append(run_variant(session, args, v, seed, out_dir))
            write_report(out_dir, args, records)
    except _Interrupted as stop:
        records.append(stop.record)
        write_report(out_dir, args, records)
        print(f"\ninterrupted; the clip in flight was cancelled. Partial results: {out_dir}")
        return 130
    print(f"results: {out_dir / 'results.json'}\nside by side: {out_dir / 'index.html'}")
    return 0 if all(r.get("status") == "completed" for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())
