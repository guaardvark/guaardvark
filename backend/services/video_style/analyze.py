"""Measure a video: shots, camera motion, look, audio, and the pacing profile.

Stages, each a function on the shared analysis dict so a Celery chain can run
them one at a time and a caller can run them all in one go:

    probe -> shots -> measure -> audio -> distill

The limits below are declared in data next to what they constrain. Each
carries the observation it was chosen against; a number without one is a
guess waiting for the labelled-shot bake-off.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

SCHEMA = "video_style.analysis/0.2"
ENGINE = "video_style.measure/0.2"

ProgressFn = Callable[[str, int, str], None]

# Shot detection. ffmpeg's scdet scores the luma difference between frames
# 0..100. Measured 2026-11-03: 3, 5 and 8 all find exactly the five hard cuts
# of the synthetic test clip with no false positives; on a 115 s screen-recorded
# demo episode 5 finds its seven scene changes where 8 misses three. Two
# detections closer than min_shot_s are one cut.
SHOT_DETECTION = {
    "scdet_threshold": 5.0,
    "min_shot_s": 0.25,
    "analysis_width": 320,
    "black_min_s": 0.08,
    "black_picture_threshold": 0.98,
    "fade_window_s": 0.5,
}

# Keyframes per shot: first, middle, last. 448 px on the long edge is what
# the video reviewer already samples at, enough for look statistics.
KEYFRAMES = {"per_shot": 3, "long_edge": 448, "edge_margin_s": 0.04}

# Camera motion is read from an affine fit between consecutive frames at this
# rate and width. Magnitudes are fractions of the frame width per second, so
# they do not depend on the source resolution. Measured 2026-11-03 on clips
# cropped out of a noise still: a 0.094 w/s pan reads 0.176, a 0.083 w/s tilt
# 0.088, a 10 %/s zoom 0.086, a 10 px wobble 0.023 shake, a locked crop 0.0 on
# every axis; a 1.6 w/s whip reads a 0.45 w/s peak because the tracker loses
# the frame, so whip_min sits well under the true speed. A 90 s Video Editor
# render and a screen-recorded episode checked the labels on real footage.
# The labelled-shot bake-off recalibrates these.
MOTION = {
    "fps": 8,
    "width": 320,
    "max_corners": 200,
    "min_tracked": 12,
    "pan_min": 0.05,
    "zoom_min": 0.06,
    "roll_min_deg": 5.0,
    "handheld_jitter_min": 0.002,
    "whip_min": 0.4,
    "max_frames": 240,
}

# Look. Luma is sRGB 0..255. Key thresholds follow the usual split of a
# frame's mean luma into low-key (under about 28 %), normal and high-key
# (over about 63 %); chosen 2026-11-03, to be checked against graded footage.
LOOK = {
    "low_key_max_luma": 70,
    "high_key_min_luma": 160,
    "palette_clusters": 4,
    "palette_sample": 4096,
    "lit_luma_min": 16,
    "lit_luma_max": 240,
    "teal_hue": (175, 205),
    "orange_hue": (15, 45),
    "colour_sat_min": 0.2,
}

# Shot scale from the largest detected face: face height over frame height.
# CineScale's nine classes collapsed to the seven the recipe schema uses.
SCALE = {
    "extreme_close_up": 0.55,
    "close_up": 0.32,
    "medium_close_up": 0.2,
    "medium": 0.11,
    "medium_long": 0.06,
    "long": 0.025,
    "extreme_long": 0.0,
}
YUNET_GLOB = "**/face_detection_yunet*.onnx"

# Audio. A cut within this many milliseconds of a beat counts as on the beat;
# 60 ms is about two frames at 30 fps and the tolerance the recipe schema uses.
AUDIO = {"sr": 22050, "beat_tolerance_s": 0.06, "energy_bins": 10}

MOTION_LABELS = ("static", "pan", "tilt", "zoom_in", "zoom_out", "roll", "handheld", "whip")
TRANSITION_LABELS = ("cut", "fade")
SHOT_LEN_EDGES = [0.25, 0.5, 1, 2, 4, 8, 16, 32]


# --------------------------------------------------------------------------- probe

def _run(cmd: List[str], *, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _ffmpeg_version() -> str:
    try:
        out = _run(["ffmpeg", "-version"], timeout=10).stdout.splitlines()
        return out[0].split(" ")[2] if out else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def probe(video_path: str | os.PathLike) -> Dict[str, Any]:
    """Container and stream facts from ffprobe. Raises if the file is unreadable."""
    p = Path(video_path)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    proc = _run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,width,height,r_frame_rate,avg_frame_rate,nb_frames",
        "-of", "json", str(p),
    ], timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()[:300]}")
    info = json.loads(proc.stdout or "{}")
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise RuntimeError("no video stream")
    fps = _ratio(video.get("avg_frame_rate")) or _ratio(video.get("r_frame_rate")) or 0.0
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    duration = float(info.get("format", {}).get("duration") or 0.0)
    nb = int(video.get("nb_frames") or 0) or (int(round(duration * fps)) if fps else 0)
    return {
        "path": str(p),
        "duration_s": round(duration, 3),
        "fps": round(fps, 3),
        "width": width,
        "height": height,
        "aspect": round(width / height, 3) if width and height else None,
        "frames": nb,
        "has_audio": any(s.get("codec_type") == "audio" for s in info.get("streams", [])),
    }


def _ratio(text: Optional[str]) -> float:
    if not text or "/" not in text:
        return float(text) if text else 0.0
    a, b = text.split("/", 1)
    try:
        return float(a) / float(b) if float(b) else 0.0
    except ValueError:
        return 0.0


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- shots

def detect_shots(
    video_path: str | os.PathLike,
    duration_s: float,
    *,
    threshold: float = SHOT_DETECTION["scdet_threshold"],
    min_shot_s: float = SHOT_DETECTION["min_shot_s"],
) -> Dict[str, Any]:
    """Cut times from ffmpeg's scdet and black intervals from blackdetect, one decode.

    Returns ``{"cuts": [t...], "black": [[start, end]...], "shots": [...]}`` where
    each shot has ``i, t0, t1, dur_s, transition_in, transition_out``.
    """
    p = str(video_path)
    width = SHOT_DETECTION["analysis_width"]
    vf = (
        f"scale={width}:-2,scdet=threshold={threshold},"
        "metadata=print:key=lavfi.scd.time:file=-,"
        f"blackdetect=d={SHOT_DETECTION['black_min_s']}:pic_th={SHOT_DETECTION['black_picture_threshold']}"
    )
    proc = _run(["ffmpeg", "-nostdin", "-loglevel", "info", "-i", p, "-vf", vf, "-an", "-f", "null", "-"],
                timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg shot detection failed: {proc.stderr.strip()[-300:]}")
    cuts: List[float] = []
    for m in re.finditer(r"pts_time:([0-9.]+)\s*\nlavfi\.scd\.time=", proc.stdout):
        t = float(m.group(1))
        if cuts and t - cuts[-1] < min_shot_s:
            continue
        if t < min_shot_s or t > duration_s - min_shot_s:
            continue
        cuts.append(round(t, 3))
    black = [
        [float(a), float(b)]
        for a, b in re.findall(r"black_start:([0-9.]+) black_end:([0-9.]+)", proc.stderr)
    ]
    return {"cuts": cuts, "black": black, "shots": _shots_from_cuts(cuts, black, duration_s)}


def _shots_from_cuts(cuts: List[float], black: List[List[float]], duration_s: float) -> List[Dict[str, Any]]:
    edges = [0.0] + list(cuts) + [round(duration_s, 3)]
    win = SHOT_DETECTION["fade_window_s"]

    def transition_at(t: float, boundary: bool) -> Dict[str, Any]:
        for a, b in black:
            if a - win <= t <= b + win:
                return {"type": "fade", "dur_s": round(b - a, 3)}
        return {"type": "cut" if not boundary else "none"}

    shots = []
    for i in range(len(edges) - 1):
        t0, t1 = edges[i], edges[i + 1]
        if t1 - t0 <= 0:
            continue
        shots.append({
            "i": i,
            "t0": t0,
            "t1": t1,
            "dur_s": round(t1 - t0, 3),
            "transition_in": transition_at(t0, boundary=(i == 0)),
            "transition_out": transition_at(t1, boundary=(i == len(edges) - 2)),
        })
    return shots


# --------------------------------------------------------------------------- keyframes

def _scale_expr(long_edge: int) -> str:
    return f"scale='if(gt(iw,ih),{long_edge},-2)':'if(gt(iw,ih),-2,{long_edge})'"


def sample_keyframes(video_path: str | os.PathLike, shots: List[Dict[str, Any]], frames_dir: Path,
                     *, progress: Optional[ProgressFn] = None) -> None:
    """Write first/middle/last JPEGs per shot and record their paths and times on the shot."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    margin = KEYFRAMES["edge_margin_s"]
    total = max(1, len(shots))
    for n, shot in enumerate(shots):
        t0, t1 = shot["t0"], shot["t1"]
        inner = max(0.0, (t1 - t0) / 2 - margin)
        times = sorted({round(t0 + min(margin, inner), 3), round((t0 + t1) / 2, 3), round(t1 - min(margin, inner), 3)})
        keyframes = []
        for k, t in enumerate(times):
            out = frames_dir / f"shot{shot['i']:04d}_{k}.jpg"
            proc = _run([
                "ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video_path),
                "-frames:v", "1", "-vf", _scale_expr(KEYFRAMES["long_edge"]), "-q:v", "3", str(out),
            ], timeout=120)
            if proc.returncode == 0 and out.exists():
                keyframes.append({"t": t, "path": str(out)})
        shot["keyframes"] = keyframes
        if progress and n % 10 == 0:
            progress("measure", int(5 + 25 * n / total), f"keyframes {n + 1}/{total}")


# --------------------------------------------------------------------------- look

def _load_rgb(path: str):
    import numpy as np
    from PIL import Image

    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def _srgb_to_linear(c):
    import numpy as np

    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def colour_temperature_k(rgb) -> Optional[float]:
    """Correlated colour temperature of the frame's grey-world mean (McCamy)."""
    import numpy as np

    f = rgb.astype(np.float32)
    luma = 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]
    lit = (luma > LOOK["lit_luma_min"]) & (luma < LOOK["lit_luma_max"])
    if lit.sum() < 64:
        return None
    mean = _srgb_to_linear(f[lit].mean(axis=0))
    x_ = 0.4124 * mean[0] + 0.3576 * mean[1] + 0.1805 * mean[2]
    y_ = 0.2126 * mean[0] + 0.7152 * mean[1] + 0.0722 * mean[2]
    z_ = 0.0193 * mean[0] + 0.1192 * mean[1] + 0.9505 * mean[2]
    s = x_ + y_ + z_
    if s <= 0:
        return None
    x, y = x_ / s, y_ / s
    if abs(0.1858 - y) < 1e-6:
        return None
    n = (x - 0.3320) / (0.1858 - y)
    cct = 449 * n ** 3 + 3525 * n ** 2 + 6823.3 * n + 5520.33
    if not (1500 <= cct <= 20000):
        return None
    return round(float(cct))


def _palette_lab(rgb, k: int) -> Dict[str, Any]:
    import numpy as np
    from skimage.color import rgb2lab
    from sklearn.cluster import KMeans

    flat = rgb.reshape(-1, 3)
    if flat.shape[0] > LOOK["palette_sample"]:
        idx = np.random.default_rng(0).choice(flat.shape[0], LOOK["palette_sample"], replace=False)
        flat = flat[idx]
    lab = rgb2lab(flat.reshape(1, -1, 3) / 255.0).reshape(-1, 3)
    k = min(k, max(1, len(np.unique(lab.round(0), axis=0))))
    km = KMeans(n_clusters=k, n_init=3, random_state=0).fit(lab)
    counts = np.bincount(km.labels_, minlength=k).astype(float)
    order = np.argsort(-counts)
    centers = km.cluster_centers_[order]
    weights = counts[order] / counts.sum()
    return {
        "palette_lab": [[round(float(v), 1) for v in c] for c in centers],
        "palette_weights": [round(float(w), 3) for w in weights],
        "warmth_b": round(float(lab[:, 2].mean()), 2),
        "chroma_mean": round(float(np.sqrt(lab[:, 1] ** 2 + lab[:, 2] ** 2).mean()), 2),
    }


def _hue_stats(rgb) -> Dict[str, Any]:
    import cv2
    import numpy as np

    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h = hsv[..., 0].astype(np.float32) * 2.0
    s = hsv[..., 2].astype(np.float32)  # value, used as the lit mask with saturation
    sat = hsv[..., 1].astype(np.float32) / 255.0
    mask = (sat > LOOK["colour_sat_min"]) & (s > LOOK["lit_luma_min"])
    if mask.sum() == 0:
        return {"hue_hist_12": [0.0] * 12, "teal_orange": 0.0}
    hues = h[mask]
    hist, _ = np.histogram(hues, bins=12, range=(0, 360))
    hist = hist / max(1, hist.sum())
    frac = mask.mean()
    t0, t1 = LOOK["teal_hue"]
    o0, o1 = LOOK["orange_hue"]
    teal = float(((hues >= t0) & (hues <= t1)).mean()) * frac
    orange = float(((hues >= o0) & (hues <= o1)).mean()) * frac
    return {"hue_hist_12": [round(float(v), 3) for v in hist], "teal_orange": round(min(1.0, 2 * min(teal, orange) + 0.5 * (teal + orange)), 3)}


def frame_look(path: str) -> Dict[str, Any]:
    """Lighting and colour statistics of one keyframe (all measured, ``source: cv``)."""
    import numpy as np
    from backend.services.video_consistency_metrics import _analyse_frame

    rgb = _load_rgb(path)
    base = _analyse_frame(rgb)
    f = rgb.astype(np.float32)
    luma = 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]
    p = {f"p{q}": round(float(np.percentile(luma, q)), 1) for q in (1, 5, 10, 50, 90, 95, 99)}
    mean = float(luma.mean())
    key = "low" if mean < LOOK["low_key_max_luma"] else "high" if mean > LOOK["high_key_min_luma"] else "normal"
    look = {
        "l_mean": round(mean, 1),
        "key": key,
        "contrast_rms": round(float(luma.std() / 255.0), 3),
        "contrast_ratio_p95_p5": round((p["p95"] + 1) / (p["p5"] + 1), 2),
        "clipped": round(base["clipped"], 4),
        "crushed": round(base["crushed"], 4),
        "saturation": round(base["saturation"], 3),
        "tone_curve_p": {k: p[k] for k in ("p1", "p10", "p50", "p90", "p99")},
        "cct_k": colour_temperature_k(rgb),
    }
    look.update(_palette_lab(rgb, LOOK["palette_clusters"]))
    look.update(_hue_stats(rgb))
    return look


def _average_look(looks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One look per shot: medians of the keyframe numbers, the middle frame's palette."""
    import numpy as np

    if not looks:
        return {}
    mid = looks[len(looks) // 2]
    out: Dict[str, Any] = {}
    for k in ("l_mean", "contrast_rms", "contrast_ratio_p95_p5", "clipped", "crushed", "saturation",
              "warmth_b", "chroma_mean", "teal_orange"):
        vals = [x[k] for x in looks if x.get(k) is not None]
        out[k] = round(float(np.median(vals)), 3) if vals else None
    ccts = [x["cct_k"] for x in looks if x.get("cct_k")]
    out["cct_k"] = round(float(np.median(ccts))) if ccts else None
    keys = [x["key"] for x in looks]
    out["key"] = max(set(keys), key=keys.count)
    out["tone_curve_p"] = {
        k: round(float(np.median([x["tone_curve_p"][k] for x in looks])), 1) for k in mid["tone_curve_p"]
    }
    out["palette_lab"] = mid["palette_lab"]
    out["palette_weights"] = mid["palette_weights"]
    out["hue_hist_12"] = [round(float(v), 3) for v in np.mean([x["hue_hist_12"] for x in looks], axis=0)]
    out["source"] = "cv"
    return out


# --------------------------------------------------------------------------- motion

def _decode_gray(video_path: str, t0: float, dur: float, src_w: int, src_h: int, fps: int):
    import numpy as np

    width = MOTION["width"]
    height = max(2, int(round(src_h * width / max(1, src_w) / 2)) * 2)
    max_dur = MOTION["max_frames"] / fps
    proc = subprocess.run([
        "ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{t0:.3f}", "-t", f"{min(dur, max_dur):.3f}",
        "-i", video_path, "-vf", f"fps={fps},scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ], capture_output=True, timeout=600)
    if proc.returncode != 0 or not proc.stdout:
        return np.zeros((0, height, width), dtype=np.uint8)
    n = len(proc.stdout) // (width * height)
    return np.frombuffer(proc.stdout[: n * width * height], dtype=np.uint8).reshape(n, height, width)


def shot_motion(video_path: str, shot: Dict[str, Any], src_w: int, src_h: int) -> Dict[str, Any]:
    """Camera motion of one shot from an affine fit of tracked corners, frame to frame."""
    import cv2
    import numpy as np

    fps = MOTION["fps"]
    frames = _decode_gray(video_path, shot["t0"], shot["dur_s"], src_w, src_h, fps)
    n = len(frames)
    if n < 3:
        return {"label": "static", "source": "cv", "confidence": 0.3, "frames": n, "note": "too short to track"}
    w = float(frames.shape[2])
    tx, ty, logs, rot, resid, tracked = [], [], [], [], [], []
    for a, b in zip(frames[:-1], frames[1:]):
        pts = cv2.goodFeaturesToTrack(a, maxCorners=MOTION["max_corners"], qualityLevel=0.01, minDistance=7)
        if pts is None or len(pts) < MOTION["min_tracked"]:
            tracked.append(0)
            continue
        nxt, st, _err = cv2.calcOpticalFlowPyrLK(a, b, pts, None, winSize=(21, 21), maxLevel=3)
        ok = st.reshape(-1) == 1
        p0, p1 = pts[ok].reshape(-1, 2), nxt[ok].reshape(-1, 2)
        if len(p0) < MOTION["min_tracked"]:
            tracked.append(len(p0))
            continue
        m, inl = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC, ransacReprojThreshold=3.0)
        if m is None:
            tracked.append(len(p0))
            continue
        s = math.hypot(m[0, 0], m[1, 0])
        tx.append(float(m[0, 2]))
        ty.append(float(m[1, 2]))
        logs.append(math.log(max(s, 1e-6)))
        rot.append(math.degrees(math.atan2(m[1, 0], m[0, 0])))
        inl = inl.reshape(-1).astype(bool) if inl is not None else np.ones(len(p0), bool)
        moved = (p1 - p0)[~inl]
        resid.append(float(np.linalg.norm(moved, axis=1).mean()) if len(moved) else 0.0)
        tracked.append(int(inl.sum()))
    if len(tx) < 2:
        return {"label": "static", "source": "cv", "confidence": 0.3, "frames": n, "note": "no trackable texture"}
    tx_a, ty_a, lg_a, rot_a = (np.array(v) for v in (tx, ty, logs, rot))
    # Medians: one frame pair that the fit got wrong (a flash, a title card)
    # must not turn a locked shot into a pan.
    pan = float(np.median(tx_a)) * fps / w
    tilt = float(np.median(ty_a)) * fps / w
    zoom = float(np.median(lg_a)) * fps
    roll = float(np.median(rot_a)) * fps
    mag = np.hypot(tx_a, ty_a) * fps / w
    accel = np.hypot(np.diff(tx_a), np.diff(ty_a)) / w if len(tx_a) > 2 else np.zeros(0)
    jitter = float(np.median(accel)) if len(accel) else 0.0
    net = math.hypot(pan, tilt)
    peak = float(mag.max())
    if peak >= MOTION["whip_min"] and shot["dur_s"] <= 1.5:
        label = "whip"
    elif abs(zoom) >= MOTION["zoom_min"]:
        label = "zoom_in" if zoom > 0 else "zoom_out"
    elif abs(roll) >= MOTION["roll_min_deg"] and net < MOTION["pan_min"]:
        # Rotation with the frame otherwise still; a diagonal pan also reads a
        # few degrees of roll from the fit and stays a pan.
        label = "roll"
    elif net >= MOTION["pan_min"]:
        label = "pan" if abs(pan) >= abs(tilt) else "tilt"
    elif jitter >= MOTION["handheld_jitter_min"]:
        label = "handheld"
    else:
        label = "static"
    return {
        "label": label,
        "source": "cv",
        "confidence": round(min(1.0, float(np.mean(tracked)) / MOTION["max_corners"] + 0.4), 2),
        "frames": n,
        "pan_w_s": round(pan, 4),
        "tilt_w_s": round(tilt, 4),
        "zoom_rate": round(zoom, 4),
        "roll_deg_s": round(roll, 3),
        "shake_w": round(jitter, 5),
        "magnitude_mean_w_s": round(float(mag.mean()), 4),
        "magnitude_peak_w_s": round(peak, 4),
        "subject_motion_px": round(float(np.mean(resid)), 3) if resid else 0.0,
    }


# --------------------------------------------------------------------------- scale

def _yunet_path() -> Optional[Path]:
    from backend.config import STORAGE_DIR

    root = Path(STORAGE_DIR) / "models"
    if not root.is_dir():
        return None
    return next(iter(sorted(root.glob(YUNET_GLOB))), None)


class _FaceScale:
    """Shot scale from YuNet, when its weights are installed. Never downloads."""

    def __init__(self) -> None:
        self.path = _yunet_path()
        self._det = None

    @property
    def available(self) -> bool:
        return self.path is not None

    def measure(self, image_path: str) -> Dict[str, Any]:
        if not self.available:
            return {"label": None, "source": "unavailable", "note": "YuNet weights not installed"}
        import cv2

        img = cv2.imread(image_path)
        if img is None:
            return {"label": None, "source": "cv", "note": "unreadable frame"}
        h, w = img.shape[:2]
        if self._det is None:
            self._det = cv2.FaceDetectorYN.create(str(self.path), "", (w, h), 0.7, 0.3, 50)
        self._det.setInputSize((w, h))
        _, faces = self._det.detect(img)
        if faces is None or len(faces) == 0:
            return {"label": None, "source": "cv", "note": "no_face", "num_faces": 0}
        tallest = max(faces, key=lambda f: f[3])
        ratio = float(tallest[3]) / h
        label = next(name for name, floor in SCALE.items() if ratio >= floor)
        cx = (float(tallest[0]) + float(tallest[2]) / 2) / w
        cy = (float(tallest[1]) + float(tallest[3]) / 2) / h
        return {
            "label": label, "source": "cv", "confidence": round(float(tallest[14]), 2),
            "face_h_ratio": round(ratio, 3), "num_faces": int(len(faces)),
            "subject_xy": [round(cx, 3), round(cy, 3)],
            "headroom": round(float(tallest[1]) / h, 3),
        }


def _composition(rgb_path: str, subject_xy: Optional[List[float]]) -> Dict[str, Any]:
    import cv2
    import numpy as np

    g = cv2.imread(rgb_path, cv2.IMREAD_GRAYSCALE)
    if g is None:
        return {}
    g = cv2.resize(g, (160, int(160 * g.shape[0] / g.shape[1]) or 1)).astype(np.float32)
    mirror = g[:, ::-1]
    a, b = g - g.mean(), mirror - mirror.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum())) or 1.0
    out = {"symmetry": round(float((a * b).sum() / denom), 3)}
    if subject_xy:
        points = [(x, y) for x in (1 / 3, 2 / 3) for y in (1 / 3, 2 / 3)]
        out["thirds_distance"] = round(min(math.hypot(subject_xy[0] - x, subject_xy[1] - y) for x, y in points), 3)
        out["center_framed"] = bool(abs(subject_xy[0] - 0.5) < 0.1)
    return out


# --------------------------------------------------------------------------- audio

def analyze_audio(video_path: str, duration_s: float, cuts: List[float]) -> Dict[str, Any]:
    """Loudness (ffmpeg ebur128), beats and onsets (librosa), and how cuts sit on beats."""
    import numpy as np

    out: Dict[str, Any] = {"source": "cv"}
    proc = _run(["ffmpeg", "-nostdin", "-loglevel", "info", "-i", video_path, "-vn", "-af", "ebur128=peak=none",
                 "-f", "null", "-"], timeout=1800)
    # ebur128 prints running values per frame and the integrated ones in a
    # closing "Summary:" block; only the block is the measurement.
    summary = proc.stderr.rsplit("Summary:", 1)[-1] if "Summary:" in proc.stderr else ""
    m_i = re.search(r"I:\s+(-?[0-9.]+) LUFS", summary)
    m_lra = re.search(r"LRA:\s+(-?[0-9.]+) LU", summary)
    out["lufs_i"] = float(m_i.group(1)) if m_i else None
    out["lra"] = float(m_lra.group(1)) if m_lra else None

    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        proc = _run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", video_path, "-vn", "-ac", "1",
                     "-ar", str(AUDIO["sr"]), str(wav)], timeout=1800)
        if proc.returncode != 0 or not wav.exists():
            out["note"] = "audio extraction failed"
            return out
        import librosa

        y, sr = librosa.load(str(wav), sr=AUDIO["sr"], mono=True)
    if len(y) < sr:
        out["note"] = "audio shorter than one second"
        return out
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, units="time")
    tempo_f = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else 0.0
    rms = librosa.feature.rms(y=y)[0]
    rms_t = librosa.frames_to_time(np.arange(len(rms)), sr=sr)
    bins = AUDIO["energy_bins"]
    curve = []
    for k in range(bins):
        a, b = duration_s * k / bins, duration_s * (k + 1) / bins
        sel = rms[(rms_t >= a) & (rms_t < b)]
        curve.append(round(float(sel.mean()) if len(sel) else 0.0, 5))
    peak = max(curve) or 1.0
    out.update({
        "bpm": round(tempo_f, 1),
        "beat_count": int(len(beat_times)),
        "onset_density": round(len(onsets) / max(duration_s, 1e-6), 3),
        "rms_curve": [round(v / peak, 3) for v in curve],
        "beat_times": [round(float(t), 3) for t in beat_times],
    })
    tol = AUDIO["beat_tolerance_s"]
    if cuts and len(beat_times):
        offsets = [float(np.min(np.abs(beat_times - c))) for c in cuts]
        on_beat = sum(1 for o in offsets if o <= tol)
        out["cut_beat_offsets_ms"] = [round(o * 1000) for o in offsets]
        out["frac_cuts_on_beat"] = round(on_beat / len(cuts), 3)
        out["frac_cuts_on_beat_chance"] = round(min(1.0, 2 * tol * tempo_f / 60.0), 3) if tempo_f else None
    return out


# --------------------------------------------------------------------------- profile

def _mix(labels: List[Optional[str]], order) -> Dict[str, float]:
    vals = [x for x in labels if x]
    if not vals:
        return {}
    return {k: round(vals.count(k) / len(vals), 3) for k in order if vals.count(k)}


def _spectral_alpha(lengths: List[float]) -> Optional[float]:
    """Slope of the shot-length series' power spectrum (1/f^alpha); needs 8 shots."""
    import numpy as np

    x = np.array(lengths, dtype=float)
    if len(x) < 8:
        return None
    x = x - x.mean()
    psd = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(len(x), d=1.0)
    keep = (freqs > 0) & (psd > 0)
    if keep.sum() < 3:
        return None
    slope = np.polyfit(np.log(freqs[keep]), np.log(psd[keep]), 1)[0]
    return round(float(-slope), 3)


def build_profile(analysis: Dict[str, Any]) -> Dict[str, Any]:
    import numpy as np

    shots = analysis["shots"]
    src = analysis["source"]
    duration = src["duration_s"]
    lengths = [s["dur_s"] for s in shots]
    cuts = [s["t0"] for s in shots[1:]]
    hist, _ = np.histogram(lengths, bins=[0] + SHOT_LEN_EDGES + [float("inf")])
    hist = hist / max(1, hist.sum())
    bins = AUDIO["energy_bins"]
    cut_rate = []
    motion_curve = []
    for k in range(bins):
        a, b = duration * k / bins, duration * (k + 1) / bins
        cut_rate.append(round(sum(1 for c in cuts if a <= c < b) / max(b - a, 1e-6), 3))
        overl = [s for s in shots if s["t0"] < b and s["t1"] > a]
        mags = [s.get("motion", {}).get("magnitude_mean_w_s", 0.0) or 0.0 for s in overl]
        motion_curve.append(round(float(np.mean(mags)) if mags else 0.0, 4))

    def norm(v):
        m = max(v) or 1.0
        return [x / m for x in v]

    audio = analysis.get("audio") or {}
    parts = [norm(cut_rate), norm(motion_curve)]
    if audio.get("rms_curve"):
        parts.append(audio["rms_curve"])
    energy = [round(float(np.mean([p[i] for p in parts])), 3) for i in range(bins)]
    transitions = [s["transition_in"]["type"] for s in shots[1:]]
    scales = [s.get("scale", {}).get("label") for s in shots]
    scale_labels = [x for x in scales if x]
    matrix = None
    if len(scale_labels) >= 2:
        names = list(SCALE)
        mtx = np.zeros((len(names), len(names)))
        for a, b in zip(scale_labels[:-1], scale_labels[1:]):
            mtx[names.index(a), names.index(b)] += 1
        rows = mtx.sum(axis=1, keepdims=True)
        matrix = (mtx / np.where(rows == 0, 1, rows)).round(3).tolist()
    looks = [s["look"] for s in shots if s.get("look")]

    def med(key):
        vals = [l[key] for l in looks if l.get(key) is not None]
        return round(float(np.median(vals)), 3) if vals else None

    adjacent = None
    if len(lengths) >= 3:
        c = np.corrcoef(lengths[:-1], lengths[1:])[0, 1]
        adjacent = round(float(c), 3) if np.isfinite(c) else None
    profile = {
        "cutting": {
            "shot_count": len(shots),
            "asl_s": round(float(np.mean(lengths)), 3) if lengths else None,
            "msl_s": round(float(np.median(lengths)), 3) if lengths else None,
            "shot_len_min_s": round(min(lengths), 3) if lengths else None,
            "shot_len_max_s": round(max(lengths), 3) if lengths else None,
            "shot_len_hist_log2": [round(float(v), 3) for v in hist],
            "shot_len_hist_edges_s": SHOT_LEN_EDGES,
            "shot_len_1f_alpha": _spectral_alpha(lengths),
            "adjacent_len_corr": adjacent,
            "cut_rate_curve": cut_rate,
            "transition_mix": _mix(transitions, TRANSITION_LABELS),
        },
        "beat_sync": {
            "bpm": audio.get("bpm"),
            "frac_cuts_on_beat": audio.get("frac_cuts_on_beat"),
            "frac_cuts_on_beat_chance": audio.get("frac_cuts_on_beat_chance"),
        } if audio.get("bpm") else None,
        "scale_mix": _mix(scales, SCALE.keys()),
        "scale_transition_matrix": matrix,
        "scale_labels": list(SCALE),
        "motion_mix": _mix([s.get("motion", {}).get("label") for s in shots], MOTION_LABELS),
        "motion_magnitude_mean_w_s": round(float(np.mean(
            [s.get("motion", {}).get("magnitude_mean_w_s", 0.0) or 0.0 for s in shots])), 4) if shots else None,
        "composition": {
            "symmetry_mean": round(float(np.mean([s["composition"]["symmetry"] for s in shots
                                                  if s.get("composition", {}).get("symmetry") is not None])), 3)
            if any(s.get("composition", {}).get("symmetry") is not None for s in shots) else None,
            "center_framed_frac": round(float(np.mean([s["composition"]["center_framed"] for s in shots
                                                       if "center_framed" in s.get("composition", {})])), 3)
            if any("center_framed" in s.get("composition", {}) for s in shots) else None,
        },
        "look": {
            "key_mix": _mix([l.get("key") for l in looks], ("low", "normal", "high")),
            "cct_k_median": med("cct_k"),
            "saturation_mean": med("saturation"),
            "contrast_ratio_median": med("contrast_ratio_p95_p5"),
            "teal_orange_mean": med("teal_orange"),
            "warmth_b_median": med("warmth_b"),
            "tone_curve_p": {
                k: round(float(np.median([l["tone_curve_p"][k] for l in looks])), 1)
                for k in ("p1", "p10", "p50", "p90", "p99")
            } if looks else None,
            "global_palette_lab": _global_palette(shots),
        },
        "energy_curve": energy,
        "audio": {
            "lufs_i": audio.get("lufs_i"),
            "lra": audio.get("lra"),
            "onset_density": audio.get("onset_density"),
        } if audio else None,
    }
    return profile


def _global_palette(shots: List[Dict[str, Any]], k: int = 5) -> Optional[List[List[float]]]:
    import numpy as np
    from sklearn.cluster import KMeans

    centers, weights = [], []
    for s in shots:
        look = s.get("look") or {}
        for c, w in zip(look.get("palette_lab", []), look.get("palette_weights", [])):
            centers.append(c)
            weights.append(w * s["dur_s"])
    if not centers:
        return None
    x = np.array(centers)
    k = min(k, len(x))
    km = KMeans(n_clusters=k, n_init=3, random_state=0).fit(x, sample_weight=np.array(weights))
    order = np.argsort(-np.bincount(km.labels_, weights=np.array(weights), minlength=k))
    return [[round(float(v), 1) for v in km.cluster_centers_[i]] for i in order]


# --------------------------------------------------------------------------- stages

def stage_probe(analysis: Dict[str, Any], **_: Any) -> Dict[str, Any]:
    src = probe(analysis["source"]["path"])
    src["sha256"] = _sha256(Path(src["path"]))
    analysis["source"].update(src)
    return analysis


def stage_shots(analysis: Dict[str, Any], *, progress: Optional[ProgressFn] = None, **_: Any) -> Dict[str, Any]:
    settings = analysis["settings"]
    found = detect_shots(
        analysis["source"]["path"], analysis["source"]["duration_s"],
        threshold=settings["scdet_threshold"], min_shot_s=settings["min_shot_s"],
    )
    analysis["cuts"] = found["cuts"]
    analysis["black_intervals"] = found["black"]
    analysis["shots"] = found["shots"]
    if progress:
        progress("shots", 100, f"{len(found['shots'])} shots")
    return analysis


def stage_measure(analysis: Dict[str, Any], *, progress: Optional[ProgressFn] = None, **_: Any) -> Dict[str, Any]:
    src = analysis["source"]
    frames_dir = Path(analysis["out_dir"]) / "frames"
    shots = analysis["shots"]
    sample_keyframes(src["path"], shots, frames_dir, progress=progress)
    faces = _FaceScale()
    analysis["settings"]["face_detector"] = str(faces.path) if faces.available else None
    total = max(1, len(shots))
    for n, shot in enumerate(shots):
        keyframes = shot.get("keyframes") or []
        shot["look"] = _average_look([frame_look(k["path"]) for k in keyframes])
        mid = keyframes[len(keyframes) // 2]["path"] if keyframes else None
        shot["scale"] = faces.measure(mid) if mid else {"label": None, "source": "cv", "note": "no keyframe"}
        shot["composition"] = _composition(mid, shot["scale"].get("subject_xy")) if mid else {}
        shot["motion"] = shot_motion(src["path"], shot, src["width"], src["height"])
        if progress and n % 5 == 0:
            progress("measure", int(30 + 70 * n / total), f"shot {n + 1}/{total}")
    return analysis


def stage_audio(analysis: Dict[str, Any], **_: Any) -> Dict[str, Any]:
    src = analysis["source"]
    if not src.get("has_audio"):
        analysis["audio"] = None
        return analysis
    analysis["audio"] = analyze_audio(src["path"], src["duration_s"], analysis.get("cuts", []))
    for shot in analysis["shots"]:
        beats = analysis["audio"].get("beat_times") or []
        if beats:
            off = min(abs(b - shot["t0"]) for b in beats)
            shot["audio"] = {"cut_on_beat": bool(off <= AUDIO["beat_tolerance_s"]), "beat_offset_ms": round(off * 1000)}
    return analysis


def stage_distill(analysis: Dict[str, Any], **_: Any) -> Dict[str, Any]:
    analysis["profile"] = build_profile(analysis)
    analysis["provenance"]["measured_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    analysis["status"] = "complete"
    return analysis


STAGES = ("probe", "shots", "measure", "audio", "distill")
STAGE_FN = {
    "probe": stage_probe, "shots": stage_shots, "measure": stage_measure,
    "audio": stage_audio, "distill": stage_distill,
}


def new_analysis(video_path: str | os.PathLike, out_dir: str | os.PathLike, *, settings: Optional[Dict[str, Any]] = None,
                 name: Optional[str] = None) -> Dict[str, Any]:
    """The empty analysis document every stage fills in."""
    import cv2
    import librosa
    import sklearn

    s = {
        "scdet_threshold": SHOT_DETECTION["scdet_threshold"],
        "min_shot_s": SHOT_DETECTION["min_shot_s"],
        "motion_fps": MOTION["fps"],
        "keyframe_long_edge": KEYFRAMES["long_edge"],
    }
    s.update({k: v for k, v in (settings or {}).items() if k in s and v is not None})
    return {
        "schema": SCHEMA,
        "status": "pending",
        "name": name or Path(video_path).stem,
        "source": {"path": str(video_path)},
        "out_dir": str(out_dir),
        "settings": s,
        "shots": [],
        "provenance": {
            "engine": ENGINE,
            "tools": {"ffmpeg": _ffmpeg_version(), "opencv": cv2.__version__, "librosa": librosa.__version__,
                      "scikit-learn": sklearn.__version__},
        },
    }


def save_analysis(analysis: Dict[str, Any]) -> Path:
    out = Path(analysis["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    path = out / "analysis.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(analysis, indent=1))
    tmp.replace(path)
    return path


def load_analysis(out_dir: str | os.PathLike) -> Optional[Dict[str, Any]]:
    path = Path(out_dir) / "analysis.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def run_stage(analysis: Dict[str, Any], stage: str, *, progress: Optional[ProgressFn] = None) -> Dict[str, Any]:
    fn = STAGE_FN[stage]
    analysis["status"] = stage
    out = fn(analysis, progress=progress)
    save_analysis(out)
    return out


def analyze_video(video_path: str | os.PathLike, out_dir: str | os.PathLike, *,
                  settings: Optional[Dict[str, Any]] = None, name: Optional[str] = None,
                  progress: Optional[ProgressFn] = None) -> Dict[str, Any]:
    """Run every stage and return the finished analysis (also written to out_dir/analysis.json)."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("ffmpeg and ffprobe are required for video analysis")
    analysis = new_analysis(video_path, out_dir, settings=settings, name=name)
    for stage in STAGES:
        if progress:
            progress(stage, 0, f"{stage} started")
        analysis = run_stage(analysis, stage, progress=progress)
    return analysis
