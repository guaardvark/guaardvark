"""Lightweight consistency / quality metrics for generated video + post-LoRA smoke.

This is a *starter* module per PIPELINES_IMPROVEMENTS.md (item #5).
Goal: give signal on identity preservation (for cast subjects), motion, and artifact rates
without heavy new dependencies. All functions are best-effort and fail open.

Intended call sites:
- lora_posttrain_smoke after the SDXL smoke still
- batch_video_generator on completion of cinematic items
- music_video / film crew final steps

Store results in job metadata or sidecar JSON next to the asset.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def compute_basic_video_stats(video_path: str | Path) -> Dict[str, Any]:
    """Return cheap stats: duration (if ffprobe), size, rough black % via re-use of existing logic.
    Never raises; returns partial dict on any failure.
    """
    p = Path(video_path)
    stats: Dict[str, Any] = {"path": str(p), "exists": p.exists()}
    if not p.exists():
        return stats
    try:
        stats["size_bytes"] = p.stat().st_size
    except Exception:
        pass

    # Reuse the spirit of the blank-video checker without duplicating ffmpeg blackdetect.
    # For now just note existence + size. Full black % can be added by caller if needed.
    try:
        import subprocess
        import re
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", str(p)],
            capture_output=True, text=True, timeout=15
        )
        if proc.returncode == 0:
            dur = float(proc.stdout.strip() or 0)
            stats["duration_s"] = round(dur, 2)
    except Exception:
        pass
    return stats


def _histogram_vec(path: str, bins: int = 16):
    from PIL import Image
    import numpy as np
    img = Image.open(path).convert("RGB").resize((64, 64))
    arr = np.asarray(img, dtype=np.float32)
    hist = []
    for c in range(3):
        h, _ = np.histogram(arr[:, :, c], bins=bins, range=(0, 256), density=True)
        hist.append(h)
    v = np.concatenate(hist)
    n = float(np.linalg.norm(v)) or 1.0
    return v / n


def score_identity_preservation(
    ref_paths: list[str],
    candidate_path: str,
    *,
    method: str = "hist",  # "hist" | "size" | "clip" | "face"
) -> Dict[str, Any]:
    """Best-effort identity score between training refs and a generated candidate.

    Default ``hist``: mean cosine similarity of RGB histograms (cheap, no extra deps
    beyond PIL/numpy already used elsewhere). Higher is better in [0,1].
    """
    result = {"method": method, "score": 0.5, "details": {}}
    try:
        cand = Path(candidate_path)
        if not cand.exists():
            result["details"]["error"] = "candidate missing"
            return result

        if method in ("hist", "auto"):
            import numpy as np
            try:
                cand_v = _histogram_vec(str(cand))
                sims = []
                for rp in ref_paths:
                    try:
                        if Path(rp).is_file():
                            sims.append(float(np.dot(cand_v, _histogram_vec(rp))))
                    except Exception:
                        pass
                if sims:
                    score = float(sum(sims) / len(sims))
                    # Cosine of normalized hist is typically ~0.7–0.99 for same subject.
                    result["score"] = max(0.0, min(1.0, score))
                    result["method"] = "hist"
                    result["details"] = {
                        "n_refs": len(sims),
                        "mean_cosine": round(score, 4),
                        "min_cosine": round(min(sims), 4),
                        "max_cosine": round(max(sims), 4),
                    }
                    return result
            except Exception as e:
                logger.debug("hist identity score failed, falling back to size: %s", e)
                method = "size"

        if method == "size":
            sizes = []
            for rp in ref_paths:
                try:
                    sizes.append(Path(rp).stat().st_size)
                except Exception:
                    pass
            if sizes:
                avg_ref = sum(sizes) / len(sizes)
                cand_size = cand.stat().st_size
                ratio = min(cand_size, avg_ref) / max(cand_size, avg_ref) if max(cand_size, avg_ref) else 0
                result["score"] = max(0.3, min(0.95, ratio))
                result["method"] = "size"
                result["details"] = {
                    "avg_ref_size": int(avg_ref),
                    "cand_size": cand_size,
                    "ratio": round(ratio, 3),
                }
    except Exception as e:
        logger.debug("identity score computation skipped: %s", e)
        result["details"]["error"] = str(e)[:200]
    return result


def compute_frame_consistency(video_path: str | Path, sample_frames: int = 5) -> Dict[str, Any]:
    """Cheap temporal consistency proxy: sample a few frames and measure avg pixel diff.
    Higher variance can indicate jitter or good motion — use together with other signals.
    Requires imageio or PIL + opencv optional.
    """
    out: Dict[str, Any] = {"samples": 0, "mean_abs_diff": None}
    try:
        import numpy as np
        from PIL import Image
        p = Path(video_path)
        if not p.exists():
            return out
        # Very rough: use ffmpeg to extract a few frames to /tmp, diff them.
        # To stay light we just report that we could open the container.
        out["samples"] = sample_frames
        out["mean_abs_diff"] = "not_implemented"  # placeholder for real diff pipeline
    except Exception as e:
        logger.debug("frame consistency skipped: %s", e)
    return out


# ── Frame-level quality gate ─────────────────────────────────────────────────
# Every threshold the frame checker uses, with why it sits where it does.
# Luma is Rec.709 on 0-255 RGB; saturation is (max-min)/max per pixel. The
# values follow from what each defect looks like in an 8-bit yuv420p MP4 (the
# format VHS_VideoCombine writes); they have not yet been calibrated against a
# corpus of real renders. scripts/video_quality_scan.py prints the metrics for
# a folder of clips so they can be.
QUALITY_THRESHOLDS: Dict[str, Dict[str, Any]] = {
    "dark_luma": {"value": 6, "why": (
        "A NaN latent decodes to RGB 0; yuv420p limited range brings it back as 0-3 and "
        "compression ringing lifts a few edge pixels, so the 99th percentile of a truly "
        "black region stays under 6 while the darkest real shadow detail sits above it.")},
    "black_tile_scene_luma": {"value": 24, "why": (
        "A tile only counts as a black tile when the frame around it is not itself dark; "
        "below a mean luma of 24 the whole shot is night or a fade and a black tile is scenery.")},
    "tile_grid": {"value": 8, "why": (
        "8x8 tiles on the frame: each tile is ~100-170 px on the canvases these models "
        "render, the scale of the black rectangles NaN latents leave, while a tile is small "
        "enough that one damaged patch fills it.")},
    "clipped_luma": {"value": 250, "why": "Luma at or above 250 of 255 has lost its highlight detail."},
    "clipped_share": {"value": 0.20, "why": (
        "A bright sky or a lamp can clip a few percent of a frame; a fifth of the frame "
        "clipped on average across the samples is a blown-out render.")},
    "crushed_luma": {"value": 5, "why": "Luma at or below 5 of 255 has lost its shadow detail."},
    "crushed_share": {"value": 0.35, "why": (
        "Night scenes legitimately hold a lot of near-black; over a third of every sampled "
        "frame crushed is beyond a dark grade.")},
    "washed_out_spread": {"value": 40, "why": (
        "A washed-out render squeezes its luma into a narrow band (lifted blacks, dimmed "
        "whites): under 40 of 255 between the 5th and 95th percentile. The clean test clip "
        "spans 64 and the washed-out one 13; real renders are to be measured.")},
    "washed_out_mean": {"value": 90, "why": (
        "Low spread in a dark frame is a moody grade, not a wash-out; only a frame whose mean "
        "luma is above 90 counts.")},
    "desaturated": {"value": 0.06, "why": (
        "A mean saturation under 0.06 reads as greyscale; generated colour footage sits well "
        "above 0.15. Pixels darker than 20 are left out, their saturation is noise.")},
    "oversaturated_share": {"value": 0.50, "why": (
        "More than half the (non-dark) pixels at saturation above 0.9 is posterised colour, "
        "not a vivid scene.")},
    "frozen_diff": {"value": 0.5, "why": (
        "Mean absolute luma difference between consecutive frames (0-255, frames scaled to "
        "160 px wide). Identical frames re-encoded by libx264 differ by up to 0.35 while the "
        "encoder settles and ~0.01 after; a 1 px pan every other frame measures ~1.7 on the "
        "frames that move.")},
    "frozen_share": {"value": 0.5, "why": (
        "A clip is frozen when one unbroken run of still frames covers half of it; a held "
        "beat of a second or two is a shot, not a failure.")},
    "frame_count_grid": {"value": None, "why": (
        "Models move a length onto their frame grid (4n+1, 8n+1, 17k+5), up or down, so an "
        "expected count is allowed one grid step either way, multiplied by the RIFE factor.")},
    "samples": {"value": 8, "why": "Every Nth frame such that about 8 are checked, plus the first and last."},
}

_FLAG_TEXT = {
    "unreadable": "the file could not be decoded",
    "black_frames": "black frames",
    "black_tiles": "black tiles (NaN latents decode as black rectangles)",
    "clipped_highlights": "blown-out highlights",
    "crushed_shadows": "crushed shadows",
    "washed_out": "washed out (low contrast, lifted blacks)",
    "desaturated": "almost no colour",
    "oversaturated": "posterised, oversaturated colour",
    "frozen": "frozen video (frames do not change)",
    "wrong_size": "wrong frame size",
    "wrong_frame_count": "wrong number of frames",
}


# Checks that are recorded as observations, not flags: over the 171 clips in a
# local render folder (Wan 2.2 5B/14B T2V and I2V, LTX 2.3/2.5, Hunyuan I2V,
# MiniMax H3, CogVideoX) each of these fired on renders a person judged fine,
# and none on a render known to be damaged. A check moves back to a flag when
# its threshold stops marking good clips; scripts/video_quality_scan.py shows
# the metrics to calibrate against.
OBSERVED_ONLY: Dict[str, str] = {
    "black_tiles": (
        "60 of 171 clips, all of the ones inspected dark scenery (night sky, black studio "
        "backdrops): scenery black decodes to the same 0-6 luma as a NaN rectangle, and "
        "requiring the tile to be lit elsewhere in the clip still left 20 good clips"),
    "crushed_shadows": "12 of 171, deliberate dark neon grades",
    "desaturated": "5 of 171, colourless subjects (white and grey objects)",
    "oversaturated": "6 of 171, neon-lit scenes",
    "frozen": "21 of 171, including slow push-ins whose frames do change",
}


def _t(key: str):
    return QUALITY_THRESHOLDS[key]["value"]


def _flag(code: str, detail: str) -> Dict[str, str]:
    return {"code": code, "message": f"{_FLAG_TEXT[code]}: {detail}"}


def expected_output_frames(requested: int, interpolation: int = 1) -> int:
    """Frames the MP4 holds for a requested length: RIFE's multiplier m turns n
    frames into (n - 1) * m + 1."""
    n = max(1, int(requested or 0))
    m = max(1, int(interpolation or 1))
    return (n - 1) * m + 1


def frame_grid_step(frame_rule: Optional[str]) -> int:
    """The step of a "4n+1"-style rule, or 1 when there is none."""
    import re

    m = re.match(r"^(\d+)[a-z]\+\d+$", str(frame_rule or "").replace(" ", ""))
    return int(m.group(1)) if m else 1


def _analyse_frame(rgb) -> Dict[str, float]:
    import numpy as np

    f = rgb.astype(np.float32)
    luma = 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]
    mx = f.max(axis=2)
    sat = np.where(mx > 0, (mx - f.min(axis=2)) / np.maximum(mx, 1e-6), 0.0)
    lit = mx > 20
    n = _t("tile_grid")
    h, w = luma.shape
    black_tiles = 0
    if float(luma.mean()) >= _t("black_tile_scene_luma"):
        for ty in range(n):
            for tx in range(n):
                tile = luma[ty * h // n:(ty + 1) * h // n, tx * w // n:(tx + 1) * w // n]
                if tile.size and float(np.percentile(tile, 99)) <= _t("dark_luma"):
                    black_tiles += 1
    return {
        "mean_luma": float(luma.mean()),
        "p99_luma": float(np.percentile(luma, 99)),
        "spread": float(np.percentile(luma, 95) - np.percentile(luma, 5)),
        "clipped": float((luma >= _t("clipped_luma")).mean()),
        "crushed": float((luma <= _t("crushed_luma")).mean()),
        "saturation": float(sat[lit].mean()) if lit.any() else 0.0,
        "oversaturated": float((sat[lit] > 0.9).mean()) if lit.any() else 0.0,
        "black_tiles": black_tiles,
    }


def inspect_video_frames(
    video_path: str | Path,
    *,
    expected_width: Optional[int] = None,
    expected_height: Optional[int] = None,
    expected_frames: Optional[int] = None,
    frame_tolerance: int = 0,
) -> Dict[str, Any]:
    """Decode a clip and flag what makes it unusable: black frames or tiles,
    clipped highlights, crushed shadows, a washed-out or colourless image,
    posterised colour, frozen motion, and a size or length other than the
    request's. Returns {"readable", "width", "height", "frames", "fps",
    "duration_s", "sampled", "metrics", "flags": [{"code", "message"}],
    "observations": [...]}; a check in OBSERVED_ONLY reports under
    observations, not flags. Thresholds: QUALITY_THRESHOLDS. Never raises."""
    import numpy as np

    out: Dict[str, Any] = {"readable": False, "flags": [], "metrics": {}}
    p = Path(video_path)
    try:
        import av
    except ImportError:
        out["error"] = "PyAV is not installed"
        return out
    if not p.is_file():
        out["flags"].append(_flag("unreadable", f"no file at {p}"))
        return out

    diffs: list = []
    samples: Dict[int, Any] = {}
    try:
        with av.open(str(p)) as container:
            stream = container.streams.video[0]
            out["width"], out["height"] = int(stream.codec_context.width), int(stream.codec_context.height)
            rate = stream.average_rate or stream.guessed_rate
            out["fps"] = round(float(rate), 3) if rate else None
            declared = int(stream.frames or 0)
            step = max(1, (declared or 64) // _t("samples"))
            aspect = out["height"] / max(1, out["width"])
            small_w = 160
            small_h = max(2, int(round(small_w * aspect / 2)) * 2)
            prev_luma = last = None
            count = 0
            for frame in container.decode(stream):
                rgb = frame.to_ndarray(width=small_w, height=small_h, format="rgb24")
                luma = rgb.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
                if prev_luma is not None:
                    diffs.append(float(np.abs(luma - prev_luma).mean()))
                prev_luma = luma
                if count % step == 0:
                    samples[count] = rgb
                last = (count, rgb)
                count += 1
            if last is not None:
                samples[last[0]] = last[1]
            out["frames"] = count
            out["duration_s"] = round(count / float(rate), 3) if rate and count else None
    except Exception as e:  # noqa: BLE001 — a clip that will not decode is a flag, not a crash
        out["flags"].append(_flag("unreadable", str(e)[:200]))
        return out

    if not samples:
        out["flags"].append(_flag("unreadable", "no video frames"))
        return out
    out["readable"] = True
    out["sampled"] = sorted(samples)
    per = {i: _analyse_frame(samples[i]) for i in out["sampled"]}
    metric = lambda k: float(np.mean([m[k] for m in per.values()]))  # noqa: E731
    out["metrics"] = {k: round(metric(k), 4) for k in (
        "mean_luma", "spread", "clipped", "crushed", "saturation", "oversaturated")}
    flags = out["flags"]
    n = len(per)

    black = [i for i, m in per.items() if m["p99_luma"] <= _t("dark_luma")]
    out["black_sampled"] = len(black)
    if black:
        flags.append(_flag("black_frames", f"{len(black)} of {n} sampled frames (frames {black[:6]})"))
    tiled = {i: m["black_tiles"] for i, m in per.items() if m["black_tiles"]}
    if tiled:
        worst = max(tiled.values())
        flags.append(_flag("black_tiles", f"in {len(tiled)} of {n} sampled frames, up to {worst} of "
                                          f"{_t('tile_grid') ** 2} tiles (frames {sorted(tiled)[:6]})"))
    if out["metrics"]["clipped"] > _t("clipped_share"):
        flags.append(_flag("clipped_highlights", f"{out['metrics']['clipped']:.0%} of pixels at full white"))
    if out["metrics"]["crushed"] > _t("crushed_share") and not black:
        flags.append(_flag("crushed_shadows", f"{out['metrics']['crushed']:.0%} of pixels at full black"))
    if out["metrics"]["spread"] < _t("washed_out_spread") and out["metrics"]["mean_luma"] > _t("washed_out_mean"):
        flags.append(_flag("washed_out", f"luma spread {out['metrics']['spread']:.0f} of 255 "
                                         f"around a mean of {out['metrics']['mean_luma']:.0f}"))
    if out["metrics"]["saturation"] < _t("desaturated") and not black:
        flags.append(_flag("desaturated", f"mean saturation {out['metrics']['saturation']:.2f}"))
    if out["metrics"]["oversaturated"] > _t("oversaturated_share"):
        flags.append(_flag("oversaturated", f"{out['metrics']['oversaturated']:.0%} of pixels fully saturated"))

    if len(diffs) >= 4:
        run = best = 0
        for d in diffs:
            run = run + 1 if d < _t("frozen_diff") else 0
            best = max(best, run)
        out["metrics"]["longest_still_run"] = best + 1 if best else 0
        if best + 1 >= _t("frozen_share") * out["frames"] and best:
            flags.append(_flag("frozen", f"{best + 1} of {out['frames']} frames unchanged in one run"))

    if expected_width and expected_height and (out["width"], out["height"]) != (int(expected_width), int(expected_height)):
        flags.append(_flag("wrong_size", f"{out['width']}x{out['height']}, asked for "
                                         f"{int(expected_width)}x{int(expected_height)}"))
    if expected_frames and abs(out["frames"] - int(expected_frames)) > int(frame_tolerance):
        flags.append(_flag("wrong_frame_count", f"{out['frames']} frames, expected {int(expected_frames)}"
                                                f" (±{int(frame_tolerance)})"))
    out["observations"] = [f for f in flags if f["code"] in OBSERVED_ONLY]
    out["flags"] = [f for f in flags if f["code"] not in OBSERVED_ONLY]
    return out


DEFAULT_VIDEO_REVIEW_MODEL = "minicpm-v4.5:latest"

_REVIEW_PROMPT = (
    "These {n} frames are sampled in order from a {dur}s AI-generated video clip. "
    "Review it as a strict QA reviewer of generated video. Reply with ONLY a JSON "
    "object with these keys: "
    '"summary" (one sentence: what happens across the clip), '
    '"temporal_coherence" (does the subject/motion stay consistent frame-to-frame, '
    "or morph/flicker/teleport — be specific), "
    '"artifacts" (array of concrete visual defects: warping, extra limbs, texture '
    "crawl, blur, duplicated objects; empty array if none), "
    '"quality_score" (integer 0-10, 10 = flawless), '
    '"justification" (one line for the score).'
)


def _extract_frames_b64(video_path: str | Path, n: int = 6, width: int = 448) -> list[str]:
    """Sample n evenly-spaced frames as base64 JPEGs via ffmpeg. [] on any failure."""
    import base64
    import subprocess
    import tempfile

    p = Path(video_path)
    if not p.exists():
        return []
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=nb_frames", "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
            capture_output=True, text=True, timeout=15,
        )
        nb = int((probe.stdout or "0").strip() or 0)
    except Exception:
        nb = 0
    step = max(1, nb // n) if nb else 1

    with tempfile.TemporaryDirectory() as td:
        pattern = f"select='not(mod(n\\,{step}))'" if nb else "select='eq(pict_type\\,I)'"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(p),
                 "-vf", f"{pattern},scale={width}:-1", "-frames:v", str(n),
                 "-vsync", "vfr", f"{td}/f%02d.jpg"],
                capture_output=True, timeout=60,
            )
        except Exception as e:
            logger.warning("video review: ffmpeg frame extract failed: %s", e)
            return []
        frames = sorted(Path(td).glob("f*.jpg"))
        out = []
        for fp in frames[:n]:
            try:
                out.append(base64.b64encode(fp.read_bytes()).decode())
            except Exception:
                pass
        return out


def review_video_quality(
    video_path: str | Path,
    *,
    sample_frames: int = 6,
    model: Optional[str] = None,
    annotate: bool = False,
) -> Dict[str, Any]:
    """VLM temporal QA of a generated clip — the real signal compute_frame_consistency
    only gestured at. Samples frames and asks a video-capable local VLM (default
    MiniCPM-V 4.5) to judge scene, temporal coherence, artifacts, and a 0-10 score.

    Catches the failure single-frame metrics can't: subject morphing / flicker /
    teleporting across frames. Best-effort and fails OPEN — returns
    {"available": False, "reason": ...} when ffmpeg / ollama / the model is missing,
    so callers can treat it as an optional signal.

    annotate=True writes the result into the asset's .metrics.json sidecar.
    """
    import json as _json
    import re as _re

    model = model or __import__("os").environ.get(
        "GUAARDVARK_VIDEO_REVIEW_MODEL", DEFAULT_VIDEO_REVIEW_MODEL
    )
    result: Dict[str, Any] = {"available": False, "model": model}

    frames = _extract_frames_b64(video_path, n=sample_frames)
    if not frames:
        result["reason"] = "no_frames"
        return result

    dur = compute_basic_video_stats(video_path).get("duration_s", "?")
    prompt = _REVIEW_PROMPT.format(n=len(frames), dur=dur)

    try:
        import ollama
        from backend.utils.ollama_resource_manager import think_payload
        resp = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt, "images": frames}],
            format="json",
            options={"temperature": 0.2, "num_predict": 500},
            **think_payload(model),
        )
        raw = (resp["message"]["content"] or "").strip()
    except Exception as e:  # noqa: BLE001 — optional signal, never crash the caller
        logger.warning("video review: VLM call failed: %s", e)
        result["reason"] = f"vlm_unavailable: {e}"
        return result

    review: Dict[str, Any] = {}
    try:
        review = _json.loads(raw)
    except _json.JSONDecodeError:
        m = _re.search(r"\{.*\}", raw, _re.DOTALL)
        if m:
            try:
                review = _json.loads(m.group(0))
            except _json.JSONDecodeError:
                pass
    if not review:
        result["reason"] = "unparseable_review"
        result["raw"] = raw[:300]
        return result

    try:
        score = int(round(float(review.get("quality_score"))))
        review["quality_score"] = max(0, min(10, score))
    except (TypeError, ValueError):
        review["quality_score"] = None

    result.update({"available": True, "frames_reviewed": len(frames), "review": review})
    if annotate:
        annotate_asset(video_path, {"vlm_review": result})
    return result


def annotate_asset(asset_path: str | Path, metrics: Dict[str, Any]) -> None:
    """Write/append a .metrics.json sidecar next to the asset for later inspection."""
    try:
        p = Path(asset_path)
        side = p.with_suffix(p.suffix + ".metrics.json")
        import json
        existing = {}
        if side.exists():
            try:
                existing = json.loads(side.read_text())
            except Exception:
                pass
        existing.update(metrics)
        side.write_text(json.dumps(existing, indent=2))
    except Exception as e:
        logger.debug("could not write metrics sidecar for %s: %s", asset_path, e)


# Convenience for smoke test callers
def score_smoke_vs_refs(ref_image_paths: list[str], smoke_path: str) -> Dict[str, Any]:
    stats = compute_basic_video_stats(smoke_path)  # works for png too (size only)
    ident = score_identity_preservation(ref_image_paths, smoke_path, method="size")
    return {"stats": stats, "identity": ident}
