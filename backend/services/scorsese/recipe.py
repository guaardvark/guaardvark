"""Style recipes from measured profiles, and the distance between two profiles.

A Scorsese recipe is a superset of the Video Editor's StyleRecipe
(data/agent/style_recipes/README.md): the base keys bias the Art Director
and the arranger exactly as before, and the ``scorsese`` block carries what
was measured, for the Music Video cut planner, the Director's style clause
and the Film Crew. Files sit beside the hand-written recipes; a file without a
``scorsese`` block is never overwritten unless asked.
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

RECIPE_SCHEMA = "scorsese.style/0.1"

# Slugs the Video Editor's filter and transition catalogs know
# (plugins/video_editor/mlt/filters.py, transitions.py).
FILTER_SLUGS = {"warm-tint", "cool-tint", "high-contrast-bw", "desaturate", "vignette", "slow-zoom-in", "oldfilm"}
TRANSITION_FOR = {"cut": "hard-cut", "dissolve": "cross-dissolve", "fade": "dip-to-black"}

# Art Director enums (plugins/video_editor/service/art_director.py) that the base
# recipe keys bias. Energy from average shot length: a trailer cut under 1.5 s
# reads as frenetic, a music video around 2-3 s as high, dialogue scenes at
# 3-6 s as medium, anything slower as calm.
ENERGY_BY_ASL = ((1.5, ["frenetic", "high"]), (3.0, ["high", "medium"]), (6.0, ["medium"]), (math.inf, ["calm", "medium"]))
# Look thresholds mirror analyze.LOOK; warm below 4500 K, cool above 7000 K.
WARM_CCT_MAX = 4500
COOL_CCT_MIN = 7000
DESATURATED_MAX = 0.12
HIGH_CONTRAST_MIN = 12.0

# Distance weights: every term is in [0, 1]; "all" is the default preset.
DISTANCE_PRESETS = {
    "all": {"shot_length": 1.0, "alpha": 0.5, "scale_mix": 1.0, "motion_mix": 1.0, "transition_mix": 0.5,
            "key_mix": 0.5, "scale_matrix": 0.5, "palette": 1.0, "tone_curve": 0.5, "energy_curve": 0.5,
            "cut_rate_curve": 0.5, "beat_sync": 0.5},
    "pacing-only": {"shot_length": 1.0, "alpha": 0.5, "transition_mix": 0.5, "energy_curve": 0.5,
                    "cut_rate_curve": 1.0, "beat_sync": 1.0},
    "look-only": {"key_mix": 1.0, "palette": 1.0, "tone_curve": 1.0},
}


def recipes_dir(override: Optional[str | Path] = None) -> Path:
    if override is not None:
        return Path(override)
    from backend.config import STORAGE_DIR

    return Path(STORAGE_DIR) / "agent" / "style_recipes"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug or "recipe"


# --------------------------------------------------------------------------- build

def build_recipe(analysis: Dict[str, Any], name: str, *, description: Optional[str] = None) -> Dict[str, Any]:
    """A style recipe from a finished analysis. Base keys for the Video Editor,
    the ``scorsese`` block for everything that can read numbers."""
    profile = analysis.get("profile") or {}
    cutting = profile.get("cutting") or {}
    look = profile.get("look") or {}
    audio = profile.get("audio") or {}
    beat = profile.get("beat_sync") or {}
    asl = cutting.get("asl_s")
    motion_mix = profile.get("motion_mix") or {}
    transition_mix = cutting.get("transition_mix") or {}

    energy = next(vals for ceiling, vals in ENERGY_BY_ASL if (asl or math.inf) < ceiling) if asl else []
    moving = sum(v for k, v in motion_mix.items() if k != "static")
    if moving < 0.25:
        prefer_motion = ["static", "slow"]
    elif motion_mix.get("handheld", 0) + motion_mix.get("whip", 0) > 0.3:
        prefer_motion = ["fast", "medium"]
    else:
        prefer_motion = ["medium", "slow"]

    filters: List[str] = []
    cct = look.get("cct_k_median")
    if cct and cct < WARM_CCT_MAX:
        filters.append("warm-tint")
    elif cct and cct > COOL_CCT_MIN:
        filters.append("cool-tint")
    sat = look.get("saturation_mean")
    contrast = look.get("contrast_ratio_median")
    if sat is not None and sat < DESATURATED_MAX:
        filters.append("high-contrast-bw" if (contrast or 0) >= HIGH_CONTRAST_MIN else "desaturate")
    if (look.get("key_mix") or {}).get("low", 0) >= 0.5:
        filters.append("vignette")
    if motion_mix.get("zoom_in", 0) >= 0.2:
        filters.append("slow-zoom-in")
    transitions = [TRANSITION_FOR[k] for k in transition_mix if k in TRANSITION_FOR]
    if not transitions:
        transitions = ["hard-cut"]

    shots = analysis.get("shots") or []
    template = _shot_list_template(shots, beat.get("bpm"))
    recipe = {
        "name": name,
        "description": description or _describe(profile),
        "art_director_bias": {"prefer_subjects": [], "prefer_energy": energy, "prefer_motion": prefer_motion},
        "filter_palette": [f for f in filters if f in FILTER_SLUGS],
        "transition_palette": transitions,
        "global_filter": None,
        "audio_treatment": "as-is",
        "scorsese": {
            "schema": RECIPE_SCHEMA,
            "pacing": {
                "target_asl_s": asl,
                "msl_s": cutting.get("msl_s"),
                "shot_len_hist_log2": cutting.get("shot_len_hist_log2"),
                "shot_len_hist_edges_s": cutting.get("shot_len_hist_edges_s"),
                "shot_len_1f_alpha": cutting.get("shot_len_1f_alpha"),
                "adjacent_len_corr": cutting.get("adjacent_len_corr"),
                "cut_rate_curve": cutting.get("cut_rate_curve"),
                "energy_curve": profile.get("energy_curve"),
                "cut_on_beat": {
                    "prob": beat.get("frac_cuts_on_beat"),
                    "chance_level": beat.get("frac_cuts_on_beat_chance"),
                    "grid": "beat",
                    "tolerance_ms": 60,
                    "bpm": beat.get("bpm"),
                } if beat else None,
            },
            "shot_list_template": template,
            "mixes": {
                "scale_mix": profile.get("scale_mix") or {},
                "scale_transition_matrix": profile.get("scale_transition_matrix"),
                "scale_labels": profile.get("scale_labels"),
                "motion_mix": motion_mix,
                "transition_mix": transition_mix,
                "key_mix": look.get("key_mix") or {},
            },
            "framing_rules": {
                "center_framed_prob": (profile.get("composition") or {}).get("center_framed_frac"),
                "symmetry_mean": (profile.get("composition") or {}).get("symmetry_mean"),
            },
            "look_targets": {
                "key": max((look.get("key_mix") or {"normal": 1}).items(), key=lambda kv: kv[1])[0],
                "cct_k": cct,
                "saturation": sat,
                "contrast_ratio": contrast,
                "teal_orange": look.get("teal_orange_mean"),
                "warmth_b": look.get("warmth_b_median"),
                "palette_lab": look.get("global_palette_lab"),
                "tone_curve_p": look.get("tone_curve_p"),
            },
            "transition_rules": {
                "default": max(transition_mix.items(), key=lambda kv: kv[1])[0] if transition_mix else "cut",
                "mix": transition_mix,
            },
            "audio_rules": {
                "loudness_target_lufs": audio.get("lufs_i"),
                "loudness_range_lu": audio.get("lra"),
                "onset_density": audio.get("onset_density"),
                "bpm": beat.get("bpm"),
            },
            "provenance": {
                "derived_from": (analysis.get("source") or {}).get("sha256"),
                "source_name": analysis.get("name"),
                "duration_s": (analysis.get("source") or {}).get("duration_s"),
                "shot_count": cutting.get("shot_count"),
                "engine": (analysis.get("provenance") or {}).get("engine"),
                "tools": (analysis.get("provenance") or {}).get("tools"),
                "measured_at": (analysis.get("provenance") or {}).get("measured_at"),
            },
        },
    }
    return recipe


def _shot_list_template(shots: List[Dict[str, Any]], bpm: Optional[float]) -> List[Dict[str, Any]]:
    """Open, body, climax, end slots read off the measured shots."""
    if not shots:
        return []

    def slot(name: str, shot: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
        d = {
            "slot": name,
            "scale": (shot.get("scale") or {}).get("label"),
            "motion": (shot.get("motion") or {}).get("label"),
            "dur_s": shot["dur_s"],
            "key": (shot.get("look") or {}).get("key"),
        }
        if bpm:
            d["dur_beats"] = round(shot["dur_s"] * bpm / 60, 1)
        d.update(extra)
        return d

    first, last = shots[0], shots[-1]
    climax = max(shots, key=lambda s: ((s.get("motion") or {}).get("magnitude_mean_w_s") or 0.0) / max(s["dur_s"], 0.1))
    return [
        slot("open", first, transition_in=first["transition_in"]["type"]),
        {"slot": "body", "repeat": "until 85% of runtime", "scale_sampler": "mixes.scale_mix",
         "scale_transitions": "mixes.scale_transition_matrix", "motion_sampler": "mixes.motion_mix",
         "shot_len_sampler": "pacing.shot_len_hist_log2"},
        slot("climax", climax, at_fraction=round(climax["t0"] / max(last["t1"], 1e-6), 2)),
        slot("end", last, transition_out=last["transition_out"]["type"]),
    ]


def _describe(profile: Dict[str, Any]) -> str:
    cutting = profile.get("cutting") or {}
    look = profile.get("look") or {}
    asl = cutting.get("asl_s")
    parts = []
    if asl:
        parts.append(f"average shot {asl:.1f} s over {cutting.get('shot_count')} shots")
    key = max((look.get("key_mix") or {}).items(), key=lambda kv: kv[1], default=(None, 0))[0]
    if key:
        parts.append(f"{key}-key")
    cct = look.get("cct_k_median")
    if cct:
        parts.append(f"{int(cct)} K")
    return "Measured style: " + ", ".join(parts) + "." if parts else "Measured style."


# --------------------------------------------------------------------------- clause

def style_clause(recipe: Dict[str, Any]) -> str:
    """One or two sentences of camera language for DirectorBrief.style, built only
    from measured values present in the recipe."""
    s = recipe.get("scorsese") or {}
    pacing = s.get("pacing") or {}
    mixes = s.get("mixes") or {}
    look = s.get("look_targets") or {}
    bits: List[str] = []
    asl = pacing.get("target_asl_s")
    if asl:
        pace = "cut fast" if asl < 2 else "cut briskly" if asl < 4 else "hold shots" if asl < 8 else "hold long takes"
        bits.append(f"{pace}, about {asl:.1f} s a shot")
    scale_mix = mixes.get("scale_mix") or {}
    if scale_mix:
        top = sorted(scale_mix.items(), key=lambda kv: -kv[1])[:2]
        bits.append("mostly " + " and ".join(k.replace("_", " ") + "s" for k, _ in top))
    motion_mix = mixes.get("motion_mix") or {}
    if motion_mix:
        top = sorted(motion_mix.items(), key=lambda kv: -kv[1])
        lead = top[0][0]
        extra = [k for k, v in top[1:] if v >= 0.15]
        cam = {"static": "a locked camera", "pan": "pans", "tilt": "tilts", "zoom_in": "push-ins",
               "zoom_out": "pull-outs", "roll": "rolls", "handheld": "handheld movement", "whip": "whip pans"}
        phrase = cam.get(lead, lead)
        if extra:
            phrase += " with some " + ", ".join(cam.get(k, k) for k in extra)
        bits.append(phrase)
    look_bits = []
    if look.get("key"):
        look_bits.append(f"{look['key']}-key light")
    cct = look.get("cct_k")
    if cct:
        look_bits.append(f"{'warm' if cct < WARM_CCT_MAX else 'cool' if cct > COOL_CCT_MIN else 'neutral'} ({int(cct)} K)")
    sat = look.get("saturation")
    if sat is not None:
        look_bits.append("desaturated" if sat < DESATURATED_MAX else "saturated" if sat > 0.45 else "natural colour")
    if (look.get("teal_orange") or 0) >= 0.3:
        look_bits.append("teal-and-orange grade")
    if look_bits:
        bits.append(", ".join(look_bits))
    cut = s.get("cut_on_beat") or pacing.get("cut_on_beat") or {}
    if cut and cut.get("prob") is not None and cut.get("chance_level") is not None and cut["prob"] > cut["chance_level"] + 0.15:
        bits.append(f"cuts land on the beat ({int(cut['prob'] * 100)}%)")
    trans = (s.get("transition_rules") or {}).get("default")
    if trans == "fade":
        bits.append("fades between shots")
    elif trans == "cut":
        bits.append("hard cuts")
    if not bits:
        return ""
    text = "; ".join(bits)
    return text[0].upper() + text[1:] + "."


# --------------------------------------------------------------------------- files

def save_recipe(recipe: Dict[str, Any], *, recipes_dir_override: Optional[str | Path] = None,
                overwrite: bool = False) -> Path:
    """Write the recipe as <slug>.json. A hand-written recipe (no ``scorsese``
    block) at that slug is left alone unless ``overwrite`` is set."""
    d = recipes_dir(recipes_dir_override)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slugify(recipe['name'])}.json"
    if path.exists() and not overwrite:
        try:
            existing = json.loads(path.read_text())
        except (OSError, ValueError):
            existing = {}
        if "scorsese" not in existing:
            raise FileExistsError(f"{path.name} is a hand-written recipe; pass overwrite to replace it")
    path.write_text(json.dumps(recipe, indent=2) + "\n")
    return path


def list_recipes(*, recipes_dir_override: Optional[str | Path] = None) -> List[Dict[str, Any]]:
    d = recipes_dir(recipes_dir_override)
    out = []
    if not d.is_dir():
        return out
    for path in sorted(d.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            logger.warning("skipping malformed recipe %s: %s", path.name, e)
            continue
        s = data.get("scorsese") or {}
        out.append({
            "name": data.get("name") or path.stem,
            "slug": path.stem,
            "description": data.get("description", ""),
            "measured": bool(s),
            "target_asl_s": (s.get("pacing") or {}).get("target_asl_s"),
            "derived_from": (s.get("provenance") or {}).get("source_name"),
            "path": str(path),
        })
    return out


def load_recipe(name: str, *, recipes_dir_override: Optional[str | Path] = None) -> Optional[Dict[str, Any]]:
    d = recipes_dir(recipes_dir_override)
    target = slugify(name)
    for path in d.glob("*.json") if d.is_dir() else []:
        if path.stem == target or slugify(path.stem) == target:
            try:
                return json.loads(path.read_text())
            except (OSError, ValueError):
                return None
    for row in list_recipes(recipes_dir_override=recipes_dir_override):
        if slugify(row["name"]) == target:
            return json.loads(Path(row["path"]).read_text())
    return None


# --------------------------------------------------------------------------- distance

def _wasserstein_hist(a: Optional[List[float]], b: Optional[List[float]]) -> Optional[float]:
    if not a or not b or len(a) != len(b):
        return None
    ca = cb = 0.0
    total = 0.0
    for x, y in zip(a, b):
        ca += x
        cb += y
        total += abs(ca - cb)
    return min(1.0, total / max(1, len(a) - 1))


def _js(a: Optional[Dict[str, float]], b: Optional[Dict[str, float]]) -> Optional[float]:
    if not a and not b:
        return None
    a, b = a or {}, b or {}
    keys = set(a) | set(b)
    pa = {k: a.get(k, 0.0) for k in keys}
    pb = {k: b.get(k, 0.0) for k in keys}
    za, zb = sum(pa.values()) or 1.0, sum(pb.values()) or 1.0
    m = {k: (pa[k] / za + pb[k] / zb) / 2 for k in keys}

    def kl(p, z):
        return sum((p[k] / z) * math.log2((p[k] / z) / m[k]) for k in keys if p[k] > 0 and m[k] > 0)

    return round(math.sqrt(max(0.0, (kl(pa, za) + kl(pb, zb)) / 2)), 4)


def _curve_l2(a: Optional[List[float]], b: Optional[List[float]], scale: float = 1.0) -> Optional[float]:
    if not a or not b:
        return None
    n = min(len(a), len(b))

    def resample(v):
        return [v[int(i * len(v) / n)] for i in range(n)]

    ra, rb = resample(a), resample(b)
    return min(1.0, math.sqrt(sum(((x - y) / scale) ** 2 for x, y in zip(ra, rb)) / n))


def _palette_emd(a: Optional[List[List[float]]], b: Optional[List[List[float]]]) -> Optional[float]:
    """Mean nearest-colour distance between two Lab palettes, both ways, over 100."""
    if not a or not b:
        return None

    def nearest(p, q):
        return sum(min(math.dist(x, y) for y in q) for x in p) / len(p)

    return min(1.0, (nearest(a, b) + nearest(b, a)) / 2 / 100.0)


def _matrix_frob(a, b) -> Optional[float]:
    if not a or not b or len(a) != len(b):
        return None
    total = sum((x - y) ** 2 for ra, rb in zip(a, b) for x, y in zip(ra, rb))
    return min(1.0, math.sqrt(total) / math.sqrt(len(a)))


def distance(profile_a: Dict[str, Any], profile_b: Dict[str, Any], *, preset: str = "all",
             weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Weighted distance in [0, 1] between two profiles, plus every term.
    Terms a profile lacks are left out and the weights renormalised."""
    w = dict(DISTANCE_PRESETS.get(preset, DISTANCE_PRESETS["all"]))
    if weights:
        w.update(weights)
    ca, cb = profile_a.get("cutting") or {}, profile_b.get("cutting") or {}
    la, lb = profile_a.get("look") or {}, profile_b.get("look") or {}
    ba, bb = profile_a.get("beat_sync") or {}, profile_b.get("beat_sync") or {}
    tone_a = la.get("tone_curve_p") or {}
    tone_b = lb.get("tone_curve_p") or {}
    alpha_a, alpha_b = ca.get("shot_len_1f_alpha"), cb.get("shot_len_1f_alpha")
    terms = {
        "shot_length": _wasserstein_hist(ca.get("shot_len_hist_log2"), cb.get("shot_len_hist_log2")),
        "alpha": min(1.0, abs(alpha_a - alpha_b) / 2) if alpha_a is not None and alpha_b is not None else None,
        "scale_mix": _js(profile_a.get("scale_mix"), profile_b.get("scale_mix")),
        "motion_mix": _js(profile_a.get("motion_mix"), profile_b.get("motion_mix")),
        "transition_mix": _js(ca.get("transition_mix"), cb.get("transition_mix")),
        "key_mix": _js(la.get("key_mix"), lb.get("key_mix")),
        "scale_matrix": _matrix_frob(profile_a.get("scale_transition_matrix"), profile_b.get("scale_transition_matrix")),
        "palette": _palette_emd(la.get("global_palette_lab"), lb.get("global_palette_lab")),
        "tone_curve": _curve_l2([tone_a.get(k, 0) for k in ("p1", "p10", "p50", "p90", "p99")],
                                [tone_b.get(k, 0) for k in ("p1", "p10", "p50", "p90", "p99")], scale=255.0)
        if tone_a and tone_b else None,
        "energy_curve": _curve_l2(profile_a.get("energy_curve"), profile_b.get("energy_curve")),
        "cut_rate_curve": _curve_l2(ca.get("cut_rate_curve"), cb.get("cut_rate_curve"), scale=2.0),
        "beat_sync": abs(ba["frac_cuts_on_beat"] - bb["frac_cuts_on_beat"])
        if ba.get("frac_cuts_on_beat") is not None and bb.get("frac_cuts_on_beat") is not None else None,
    }
    used = {k: v for k, v in terms.items() if v is not None and w.get(k)}
    total_w = sum(w[k] for k in used) or 1.0
    total = sum(w[k] * v for k, v in used.items()) / total_w
    return {"total": round(total, 4), "preset": preset, "terms": {k: (round(v, 4) if v is not None else None) for k, v in terms.items()},
            "weights": {k: w[k] for k in used}}
