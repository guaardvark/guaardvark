#!/usr/bin/env python3
"""Servo aim calibration — fit the eye's raw anchor bias against DOM truth.

Runs the REAL servo estimate path (calibration disabled, so we measure the raw
eye) against the vision trainer page, whose DOM knows every target's exact
screen position. Fits a per-axis linear map raw ≈ a + b·truth with outlier
trimming, sanity-checks the slopes, and saves it to the Tier-1.5 runtime store
(data/training/servo_calibration.json). The ServoController inverts the map on
every anchor from then on.

Prereq: agent display :99 up, BiDi Firefox on :9222 showing vision_trainer.html
(the runner navigates there itself), Ollama serving the eye model.

Usage:
  PYTHONPATH=. GUAARDVARK_MODE=test backend/venv/bin/python -m backend.tools.servo_calibrate \
      [--samples 12] [--model gemma4:e4b] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import time
from typing import List, Optional, Tuple

# Derived from the repo root — a hardcoded absolute path here breaks every
# install but the original dev box (2026-08-05 pre-push hygiene sweep).
from pathlib import Path as _Path
TRAINER_URL = (_Path(__file__).resolve().parents[2] / "data" / "agent" / "files" / "vision_trainer.html").as_uri()

TRUTH_JS = """(() => {
  const t = document.querySelector('.target');
  if (!t) return "none";
  const r = t.getBoundingClientRect();
  return JSON.stringify({cx: r.left + r.width/2 + window.mozInnerScreenX,
                         cy: r.top + r.height/2 + window.mozInnerScreenY,
                         label: t.textContent.trim()});
})()"""

RESPAWN_JS = "document.querySelector('.target') && document.querySelector('.target').click()"


def _bidi(expr: str):
    import websocket as _ws
    ws = _ws.create_connection("ws://localhost:9222/session", timeout=10, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "session.new", "params": {"capabilities": {}}}))
        json.loads(ws.recv())
        ws.send(json.dumps({"id": 2, "method": "browsingContext.getTree", "params": {}}))
        ctx = json.loads(ws.recv())["result"]["contexts"][0]["context"]
        ws.send(json.dumps({"id": 3, "method": "script.evaluate", "params": {
            "expression": expr, "target": {"context": ctx}, "awaitPromise": False}}))
        r = json.loads(ws.recv())
        ws.send(json.dumps({"id": 4, "method": "session.end", "params": {}}))
        return r["result"]["result"].get("value")
    finally:
        ws.close()


def fit_axis(truths: List[float], raws: List[float], mad_k: float = 3.5) -> Tuple[float, float, int]:
    """Least-squares raw = a + b·truth with a robust (MAD) outlier-trim pass.

    MAD, not σ: a single wild outlier inflates σ enough to hide itself, while
    the median absolute deviation stays anchored to the inliers.
    Returns (a, b, n_used). Pure function — unit-testable without a display.
    """
    def _lsq(ts, rs):
        n = len(ts)
        tm, rm = sum(ts) / n, sum(rs) / n
        denom = sum((t - tm) ** 2 for t in ts) or 1e-9
        b = sum((t - tm) * (r - rm) for t, r in zip(ts, rs)) / denom
        return rm - b * tm, b

    import statistics
    # Initial fit via Theil–Sen (median of pairwise slopes): a single wild
    # sample has LEVERAGE under least-squares — it tilts the line toward
    # itself so residual-trimming can't see it. The pairwise-slope median
    # shrugs it off entirely.
    n = len(truths)
    slopes = [
        (raws[j] - raws[i]) / (truths[j] - truths[i])
        for i in range(n) for j in range(i + 1, n)
        if abs(truths[j] - truths[i]) > 1e-9
    ]
    b = statistics.median(slopes)
    a = statistics.median(r - b * t for t, r in zip(truths, raws))

    residuals = [r - (a + b * t) for t, r in zip(truths, raws)]
    med = statistics.median(residuals)
    mad = statistics.median(abs(e - med) for e in residuals) or 1e-9
    cutoff = mad_k * 1.4826 * mad
    kept = [(t, r) for t, r, e in zip(truths, raws, residuals) if abs(e - med) <= cutoff]
    if len(kept) >= 3:
        a, b = _lsq([k[0] for k in kept], [k[1] for k in kept])
    return a, b, len(kept)


def collect_samples(model: str, n: int, settle_s: float = 0.8):
    """Drive the raw (uncalibrated) servo path; pair anchors with DOM truth."""
    import re
    from backend.services.local_screen_backend import LocalScreenBackend
    from backend.utils.vision_analyzer import VisionAnalyzer
    from backend.services.servo_controller import ServoController
    from backend.services.servo_knowledge_store import get_vision_config

    screen = LocalScreenBackend()
    analyzer = VisionAnalyzer(default_model=model)
    cfg = dict(get_vision_config(model))
    cfg["disable_calibration"] = True  # measure the RAW eye
    servo = ServoController(screen, analyzer, vision_config=cfg)

    samples = []
    for i in range(n):
        raw_truth = _bidi(TRUTH_JS)
        if raw_truth == "none":
            break
        truth = json.loads(raw_truth)
        img, _ = screen.capture()
        servo._estimate_coordinates(img, f"colored circle with number {truth['label']}")
        # The correction targets the ANCHOR (it places the refine crop) — parse
        # the raw anchor box from the verbatim telemetry, not the refined final.
        # re.S: the eye answers in a fenced, multi-line JSON block.
        m = re.search(r'anchor:.*?"box_2d":\s*\[([\d\s,.-]+)\]', servo._last_raw_response or "", re.S)
        if m:
            y1, x1, y2, x2 = [float(v) for v in m.group(1).split(",")[:4]]
            ax = (x1 + x2) / 2 / 1000 * servo.screen_w
            ay = (y1 + y2) / 2 / 1000 * servo.screen_h
            samples.append((truth["cx"], truth["cy"], ax, ay))
            print(f"  #{len(samples):2d} truth=({truth['cx']:4.0f},{truth['cy']:4.0f}) "
                  f"anchor=({ax:4.0f},{ay:4.0f}) err=({ax-truth['cx']:+4.0f},{ay-truth['cy']:+4.0f})")
        else:
            print(f"  sample {i+1}: no parseable anchor — skipped")
        _bidi(RESPAWN_JS)  # move the dot for the next sample
        time.sleep(settle_s)
    return samples, servo.screen_w, servo.screen_h


# ── Wave 2: fit from TRUTH-LABELED archive rows, validate, gated save ────────

ARCHIVE = None  # resolved lazily so tests can patch


def load_labeled_pairs_from_archive(archive_path=None):
    """(truth_xy, raw_anchor_xy, screen_wh) triples from truth-labeled rows.

    The correction targets the ANCHOR (it places the refine crop), so the raw
    anchor box is re-parsed from the verbatim raw_response telemetry.
    """
    import re
    from pathlib import Path as _P
    path = _P(archive_path) if archive_path else \
        _P(__file__).resolve().parents[2] / "data" / "training" / "knowledge" / "servo_archive.jsonl"
    pairs = []
    try:
        lines = open(path).read().splitlines()
    except FileNotFoundError:
        return pairs
    for line in lines:
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = d.get("truth") or {}
        if t.get("target_cx") is None or t.get("target_cy") is None:
            continue
        m = re.search(r'anchor:.*?"box_2d":\s*\[([\d\s,.-]+)\]', d.get("raw_response") or "")
        if not m:
            continue
        try:
            y1, x1, y2, x2 = [float(v) for v in m.group(1).split(",")[:4]]
        except ValueError:
            continue
        w, h = (d.get("screen_size") or [1000, 1000])[:2]
        ax, ay = (x1 + x2) / 2 / 1000 * w, (y1 + y2) / 2 / 1000 * h
        pairs.append(((float(t["target_cx"]), float(t["target_cy"])), (ax, ay), (w, h)))
    return pairs


def load_labeled_pairs_from_bench(bench_path):
    """(truth_xy, raw_anchor_xy, screen_wh) triples from an eye_bakeoff run.

    Same shape as load_labeled_pairs_from_archive, different source. The bench
    rows come from frames captured with no clicks and truth read out of the
    page DOM, so they carry none of the marker contamination a live click
    session leaves behind — and unlike a live session this can be re-fitted
    offline as many times as you like without a display, a browser or Ollama.

    Only rows from a run with the refinement OFF are usable: the correction
    targets the ANCHOR, because the anchor is what places the refine crop.
    """
    import json as _j
    from pathlib import Path as _P
    d = _j.loads(_P(bench_path).read_text())
    disp = d.get("display") or [1000, 1000]
    pairs = []
    for res in d.get("results", []):
        mode = res.get("mode", "")
        if mode not in ("anchor", "calibrated"):
            print(f"  skipping mode={mode!r} — needs the anchor, not a refined point")
            continue
        if mode == "calibrated":
            print("  WARNING: mode=calibrated rows already have a fit applied; "
                  "fitting on them stacks a correction on a correction")
        for row in res.get("targets", []):
            if row.get("err_x") is None or not row.get("pred"):
                continue
            gx, gy = row["gt"]
            px, py = row["pred"]
            pairs.append(((float(gx), float(gy)), (float(px), float(py)),
                          (int(disp[0]), int(disp[1]))))
    return pairs


def fit_radial(pairs):
    """The X-leg model: raw ≈ C + k·(truth − C). Fit k robustly (median of
    per-sample radius ratios about screen center)."""
    import statistics
    if not pairs:
        return None
    w, h = pairs[0][2]
    cx, cy = w / 2.0, h / 2.0
    ratios = []
    for (tx, ty), (rx, ry), _ in pairs:
        tr = ((tx - cx) ** 2 + (ty - cy) ** 2) ** 0.5
        rr = ((rx - cx) ** 2 + (ry - cy) ** 2) ** 0.5
        if tr > 40:  # near-center samples carry no radial signal
            ratios.append(rr / tr)
    if len(ratios) < 4:
        return None
    return {"model": "radial", "k": round(statistics.median(ratios), 4),
            "cx": cx, "cy": cy}


def fit_piecewise_y(pairs, elbows=(350, 400, 450)):
    """Identity at/below elbow (raw-y), linear correction above the collapse
    zone. Returns the candidate list (one per elbow) — validation picks."""
    out = []
    for elbow in elbows:
        seg = [(t[1], r[1]) for t, r, _ in pairs if r[1] < elbow]
        if len(seg) < 6:
            continue
        a, b, _ = fit_axis([s[0] for s in seg], [s[1] for s in seg])
        if 0.3 <= abs(b) <= 1.7:
            out.append({"model": "piecewise_y", "elbow": float(elbow),
                        "a_y": round(a, 2), "b_y": round(b, 4)})
    return out


def _apply_candidate(cand, rx, ry, w, h):
    if cand is None:  # identity
        return rx, ry
    fam = cand["model"]
    if fam == "radial":
        return (cand["cx"] + (rx - cand["cx"]) / cand["k"],
                cand["cy"] + (ry - cand["cy"]) / cand["k"])
    if fam == "piecewise_y":
        return rx, (ry if ry >= cand["elbow"] else (ry - cand["a_y"]) / cand["b_y"])
    return ((rx - cand["a_x"]) / cand["b_x"], (ry - cand["a_y"]) / cand["b_y"])


def evaluate_candidate(cand, pairs, catch_px=80.0):
    """(mean_error, catch_rate) of corrected anchors vs truth."""
    if not pairs:
        return float("inf"), 0.0
    m = evaluate_candidate_axes(cand, pairs, catch_px)
    return m["mean"], m["catch"]


def evaluate_candidate_axes(cand, pairs, catch_px=80.0):
    """Same evaluation, but keeping the axes apart.

    A fit that halves Y error while doubling X error is a clear win on mean
    Euclidean distance and a clear loss in practice. The aggregate cannot show
    that; these numbers can.
    """
    import statistics as _st
    if not pairs:
        return {"mean": float("inf"), "catch": 0.0, "med_x": None, "med_y": None, "n": 0}
    errs, ex, ey = [], [], []
    for (tx, ty), (rx, ry), (w, h) in pairs:
        px, py = _apply_candidate(cand, rx, ry, w, h)
        ex.append(abs(px - tx))
        ey.append(abs(py - ty))
        errs.append(((px - tx) ** 2 + (py - ty) ** 2) ** 0.5)
    return {
        "mean": sum(errs) / len(errs),
        "catch": sum(1 for e in errs if e <= catch_px) / len(errs),
        "med_x": _st.median(ex),
        "med_y": _st.median(ey),
        "n": len(errs),
    }


def split_by_position(pairs, eval_frac=0.25, seed=11):
    """Hold out whole TARGET POSITIONS so the gate tests generalization, not
    memorized dots."""
    import random as _r
    by_pos = {}
    for p in pairs:
        by_pos.setdefault((round(p[0][0]), round(p[0][1])), []).append(p)
    keys = sorted(by_pos)
    _r.Random(seed).shuffle(keys)
    n_eval = max(1, int(len(keys) * eval_frac)) if len(keys) > 1 else 0
    eval_keys = set(keys[:n_eval])
    train = [p for k in keys[n_eval:] for p in by_pos[k]]
    heldout = [p for k in keys[:n_eval] for p in by_pos[k]] if n_eval else []
    return train, heldout


def fit_from_archive(model_name: str, dry_run: bool, archive_path=None) -> int:
    """The Wave-2 gated fit: candidates vs identity on held-out positions;
    auto-apply ONLY on improvement (operator rule); prior entry kept for rollback."""
    pairs = load_labeled_pairs_from_archive(archive_path)
    print(f"labeled anchor pairs: {len(pairs)}")
    return fit_and_gate(pairs, model_name, dry_run)


def live_fit(model_name: str, n_samples: int, dry_run: bool) -> int:
    """Collect (truth, raw-anchor) pairs live — fresh RANDOM position per
    sample, so the collapse zone is always represented — then fit + gate.
    Teach-archive fitting starves on position spread (the dot only moves on
    hit/reload; a whole session can yield 3-4 positions); the live prober gets
    a new position every ~6s."""
    from backend.services.social_outreach.reddit_outreach import _bidi_navigate
    print(f"navigating agent Firefox to trainer: {_bidi_navigate(TRAINER_URL)}")
    time.sleep(1.5)
    print(f"collecting {n_samples} spread samples with {model_name} (raw eye)…")
    samples, w, h = collect_samples(model_name, n_samples)
    pairs = [((tx, ty), (ax, ay), (w, h)) for tx, ty, ax, ay in samples]
    print(f"labeled anchor pairs: {len(pairs)}")
    return fit_and_gate(pairs, model_name, dry_run)


def dedupe_by_position(pairs):
    """Average pairs per target position so a 15-click stuck streak counts as
    ONE sample, not fifteen. Repeat-heavy archives otherwise dominate the fit
    and the held-out draw (observed 2026-08-01: a refit on repeat-weighted
    archive data replaced a better fit with a worse one)."""
    by_pos = {}
    for (t, r, wh) in pairs:
        by_pos.setdefault((round(t[0]), round(t[1])), []).append((t, r, wh))
    out = []
    for k, group in by_pos.items():
        n = len(group)
        t = (sum(g[0][0] for g in group) / n, sum(g[0][1] for g in group) / n)
        r = (sum(g[1][0] for g in group) / n, sum(g[1][1] for g in group) / n)
        out.append((t, r, group[0][2]))
    return out


def fit_and_gate(pairs, model_name: str, dry_run: bool) -> int:
    pairs = dedupe_by_position(pairs)
    print(f"deduped to {len(pairs)} distinct-position samples")
    if len(pairs) < 12:
        print("ABORT: <12 distinct positions — collect more spread (--live-fit)")
        return 1
    train, heldout = split_by_position(pairs)
    if not heldout:
        print("ABORT: only one target position — need spread (teach with reload-respawn)")
        return 1
    print(f"train={len(train)} pairs, held-out={len(heldout)} pairs "
          f"({len(set((round(p[0][0]), round(p[0][1])) for p in heldout))} unseen positions)")

    tx = [p[0][0] for p in train]; ty = [p[0][1] for p in train]
    rx = [p[1][0] for p in train]; ry = [p[1][1] for p in train]
    a_x, b_x, _ = fit_axis(tx, rx)
    a_y, b_y, _ = fit_axis(ty, ry)
    # A Y-only candidate alongside the joint fit. Measured three times now, on
    # three different boards, the eye's X is unbiased (slope 1.006, intercept
    # 7px on 2026-09-22) while its Y is a stable compression toward screen
    # centre (slope 0.65-0.69 every time). Fitting X on a couple of dozen noisy
    # samples can only import that noise as a correction. Stored as family
    # "linear" with b_x exactly 1.0, which _apply_calibration computes as
    # (x - 0)/1 — an exact identity on X, not an approximate one. The held-out
    # gate below picks between this and the joint fit on the evidence.
    candidates = [None,
                  {"model": "linear", "a_x": round(a_x, 2), "b_x": round(b_x, 4),
                   "a_y": round(a_y, 2), "b_y": round(b_y, 4)},
                  {"model": "linear", "a_x": 0.0, "b_x": 1.0,
                   "a_y": round(a_y, 2), "b_y": round(b_y, 4)},
                  fit_radial(train)]
    candidates += fit_piecewise_y(train)
    candidates = [c for c in candidates if c is not None or c is None]  # keep identity marker

    print("\ncandidate                                    held-out mean   catch(≤80px)   med|X|   med|Y|")
    scored = []
    for cand in candidates:
        if cand is not None and cand["model"] == "linear" and not (0.3 <= abs(b_x) <= 1.7 and 0.3 <= abs(b_y) <= 1.7):
            continue
        m = evaluate_candidate_axes(cand, heldout)
        mean_e, catch = m["mean"], m["catch"]
        name = "identity" if cand is None else json.dumps(cand)[:44]
        print(f"  {name:44s} {mean_e:7.1f}px        {catch*100:5.1f}%   "
              f"{m['med_x']:6.1f}   {m['med_y']:6.1f}")
        scored.append((mean_e, -catch, cand))
    # Incumbent challenge: the currently-stored fit must be beaten, not just
    # identity — otherwise a lucky/unlucky held-out draw can replace a better
    # fit with a worse one (observed live 2026-08-01; rolled back).
    from backend.services.servo_knowledge_store import load_servo_calibration
    if pairs:
        _w, _h = pairs[0][2]
    incumbent = load_servo_calibration(model_name, int(_w), int(_h))
    inc_err, inc_catch = (evaluate_candidate(incumbent, heldout)
                          if incumbent else (float("inf"), -1.0))
    if incumbent:
        _im = evaluate_candidate_axes(incumbent, heldout)
        print(f"  {'INCUMBENT ' + incumbent.get('model', '?'):44s} {inc_err:7.1f}px        "
              f"{inc_catch*100:5.1f}%   {_im['med_x']:6.1f}   {_im['med_y']:6.1f}")

    scored.sort(key=lambda s: (s[0], s[1]))
    best_err, neg_catch, best = scored[0]
    id_err, id_catch = evaluate_candidate(None, heldout)

    beats_identity = best is not None and best_err < id_err and -neg_catch >= id_catch
    beats_incumbent = best_err < inc_err and -neg_catch >= inc_catch
    if not (beats_identity and beats_incumbent):
        keeper = "incumbent" if incumbent and inc_err <= id_err else "identity"
        print(f"\nVERDICT: {keeper} stands (challenger must beat BOTH identity and "
              f"the incumbent on mean err AND catch rate). Nothing saved.")
        return 0

    print(f"\nVERDICT: {best['model']} wins — {id_err:.1f}px → {best_err:.1f}px, "
          f"catch {id_catch*100:.1f}% → {-neg_catch*100:.1f}%")
    if dry_run:
        print("dry-run: not saved")
        return 0

    from backend.services.servo_knowledge_store import (
        save_servo_calibration, _load_calibration_file, _calibration_key,
    )
    if pairs:
        w, h = pairs[0][2]
    key = _calibration_key(model_name, int(w), int(h))
    prior = _load_calibration_file().get(key)
    entry = dict(best)
    entry.update({
        "samples": len(pairs), "heldout_mean_err_px": round(best_err, 1),
        "identity_mean_err_px": round(id_err, 1),
        "fitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "teach_archive_truth",
    })
    if prior:
        entry["_rollback"] = prior
    save_servo_calibration(model_name, int(w), int(h), entry)
    print(f"SAVED → {key} (prior kept under _rollback)")
    return 0


def main(argv: Optional[list] = None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=12)
    ap.add_argument("--model", default="gemma4:e4b")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-archive", action="store_true",
                    help="Wave 2: fit+validate from truth-labeled archive rows (no live probing)")
    ap.add_argument("--live-fit", action="store_true",
                    help="Wave 2: collect spread samples live (fresh position each), then fit+gate")
    ap.add_argument("--from-bench", default="",
                    help="fit+validate from one or more eye_bakeoff runs, comma-separated "
                         "(offline: no display, no browser, no Ollama). Point it at "
                         "bakeoff_anchor.json.")
    args = ap.parse_args(argv)

    if args.from_bench:
        pairs = []
        for _b in [b.strip() for b in args.from_bench.split(",") if b.strip()]:
            got = load_labeled_pairs_from_bench(_b)
            print(f"  {len(got):4d} pairs from {_b}")
            pairs += got
        print(f"loaded {len(pairs)} truth-labeled pairs")
        if not pairs:
            print("ABORT: no usable rows — was the run made with --mode anchor?")
            return 1
        return fit_and_gate(pairs, args.model, args.dry_run)
    if args.from_archive:
        return fit_from_archive(args.model, args.dry_run)
    if args.live_fit:
        return live_fit(args.model, args.samples, args.dry_run)

    from backend.services.servo_knowledge_store import (
        CALIBRATION_SLOPE_MIN, CALIBRATION_SLOPE_MAX, save_servo_calibration,
    )
    from backend.services.social_outreach.reddit_outreach import _bidi_navigate

    print(f"navigating agent Firefox to trainer: {_bidi_navigate(TRAINER_URL)}")
    time.sleep(1.5)

    print(f"collecting {args.samples} samples with model {args.model} (raw eye)…")
    samples, w, h = collect_samples(args.model, args.samples)
    if len(samples) < 8:
        print(f"ABORT: only {len(samples)} usable samples (<8) — not enough for a trustworthy fit")
        return 1

    tx = [s[0] for s in samples]; ty = [s[1] for s in samples]
    rx = [s[2] for s in samples]; ry = [s[3] for s in samples]
    a_x, b_x, n_x = fit_axis(tx, rx)
    a_y, b_y, n_y = fit_axis(ty, ry)

    before = [((r - t) ** 2 + (r2 - t2) ** 2) ** 0.5 for t, t2, r, r2 in samples]
    after = [(((( (r - a_x) / b_x) - t) ** 2) + ((((r2 - a_y) / b_y) - t2) ** 2)) ** 0.5
             for t, t2, r, r2 in samples]
    print(f"\nX: raw = {a_x:+.1f} + {b_x:.4f}·truth  (n={n_x})")
    print(f"Y: raw = {a_y:+.1f} + {b_y:.4f}·truth  (n={n_y})")
    print(f"anchor error  before: {sum(before)/len(before):.1f}px   after-correction: {sum(after)/len(after):.1f}px")

    for b in (b_x, b_y):
        if not (CALIBRATION_SLOPE_MIN <= abs(b) <= CALIBRATION_SLOPE_MAX):
            print(f"ABORT: slope {b:.3f} outside sanity bounds [{CALIBRATION_SLOPE_MIN}, {CALIBRATION_SLOPE_MAX}] — not saving")
            return 1

    if args.dry_run:
        print("dry-run: not saved")
        return 0

    key = save_servo_calibration(args.model, w, h, {
        "a_x": round(a_x, 2), "b_x": round(b_x, 4),
        "a_y": round(a_y, 2), "b_y": round(b_y, 4),
        "samples": len(samples),
        "mean_err_before_px": round(sum(before) / len(before), 1),
        "mean_err_after_px": round(sum(after) / len(after), 1),
        "fitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "trainer_dom_truth",
    })
    print(f"SAVED calibration → {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
