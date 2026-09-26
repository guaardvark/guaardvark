"""One place that answers what a given model can do.

The problem this exists to end: the same question — "does this model have eyes?"
— was being answered independently in six places that disagreed with each other
and, more importantly, with Ollama. On this box that produced two live faults at
once. A model whose name contains "gemma4" but which has no vision tower was
being handed images, earning an Ollama 400. Two genuinely multimodal models were
being sent through a describe-then-inject detour built for blind models, quietly
downgrading a model that could see for itself.

The fix is not a better pattern list. It is to stop guessing: Ollama's
``/api/show`` reports a ``capabilities`` array, and it is right. Note that
``/api/tags`` is NOT a substitute — measured 2026-09-22, tags omits ``vision``
for every ``gemma4`` tag on this machine while show reports it.

One thing ``/api/show`` cannot tell us is what coordinate convention a vision
model emits when asked to point at something, so that part is declared data with
a measured-or-refuse policy. See ``coords_for``.

Importable from Flask, Celery and the MCP server alike: no app context, no
``backend.app`` import, no Flask.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from backend.services.model_capability_data import (
    COORD_STYLES,
    DEFAULT_STYLE,
    EXTERNAL_MODEL_ROWS,
    EYE_BORROW_EYE_AT_MOST_PX,
    EYE_BORROW_NATIVE_WORSE_THAN_PX,
    EYE_JUDGE_MIN_BOTH_RATE,
    FAMILY_COORD_DEFAULTS,
    SHIPPED_ACCURACY_SCREEN,
    SHIPPED_ACCURACY_SOURCE,
    SHIPPED_EYE_ACCURACY,
    SHIPPED_EYE_JUDGE,
    name_looks_vision,
)

logger = logging.getLogger(__name__)

SURFACES = ("agent_screen", "chat", "mcp", "ambient")

# Probed conventions land here. Machine-local and gitignored, like the servo
# calibration it sits beside — a convention is a measurement about a model on a
# machine, not a fact about the source tree.
PROBE_STORE = Path(__file__).resolve().parents[2] / "data" / "training" / "model_coord_probe.json"

# Below this, a convention is a guess rather than a measurement, and the model
# is reported as unable to drive the screen until someone probes it.
MEASURED_CONFIDENCE = 0.7
# A measured eye worse than this is ranked below a trusted unmeasured one. On a
# 1000px board, 100px is two and a half typical buttons; an eye that coarse is
# a known quantity, but not a good one.
USABLE_ACCURACY_PX = 100.0

# How candidate eyes are ordered. "accuracy" (default): the best MEASURED eye on
# this screen wins, then convention confidence, then size. "confidence": the
# pre-2026-09-22 order, for comparison or rollback. On a box with nothing
# measured the two agree, so a fresh clone behaves as it always did.
EYE_RANKING_ENV = "GUAARDVARK_EYE_RANKING"

_CACHE_TTL = 60.0
_cache: Dict[Tuple[str, str, Optional[Tuple[int, int]]], Tuple[float, "ModelProfile"]] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CoordConvention:
    """How to read the numbers a model gives back when asked to point.

    ``order is None`` means we do not know, and the servo must refuse rather
    than guess. Guessing here does not fail loudly; it puts the click somewhere
    plausible and wrong, which is the worst outcome available.
    """
    order: Optional[str]          # "xy" | "yx" | None
    grid: Optional[int]           # normalisation denominator, None = raw pixels
    normalised: bool
    source: str                   # row | probe | probe_legacy | family | prompt_contract | probe_failed
    confidence: float
    style: str = "google_box2d"   # which request dialect to send (COORD_STYLES key)
    min_num_predict: int = 128
    think_uncontrollable: bool = False
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Eyes:
    """Who looks at the screen for this model."""
    model: Optional[str]          # None => the model sees for itself
    mechanism: str                # native | sibling_vlm | none
    reason: str                   # plain English, for the UI and the logs


@dataclass(frozen=True)
class ModelProfile:
    tag: str
    exists: bool
    sees_natively: bool
    supports_tools: bool
    supports_thinking: bool
    context_window: int
    size_mb: float
    architecture: str
    eyes: Eyes
    coords: CoordConvention
    can_drive_screen: bool
    blockers: Tuple[str, ...]
    evidence: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Vision
# ---------------------------------------------------------------------------

def _info(tag: str) -> Optional[dict]:
    try:
        from backend.utils.ollama_resource_manager import get_model_info
        return get_model_info(tag)
    except Exception as e:  # noqa: BLE001
        logger.debug("model info lookup failed for %r: %s", tag, e)
        return None


def sees_natively(tag: str) -> bool:
    """The single vision truth. Ollama first, declared rows second, name last."""
    return _vision_with_evidence(tag)[0]


def _vision_with_evidence(tag: str) -> Tuple[bool, str]:
    if not tag:
        return False, "no_tag"
    row = EXTERNAL_MODEL_ROWS.get(tag)
    info = _info(tag)
    if info is not None:
        caps = info.get("capabilities") or []
        # Deliberately NOT falling back to counting ".vision." keys in
        # model_info: measured 2026-09-22, several models that genuinely report
        # the vision capability expose zero such keys (gemma4:12b,
        # muse-glimmer:30b, the qwen3.6 MoE coding tags). Absence of tower
        # metadata says nothing, so treating it as a signal invents both false
        # negatives and false confidence.
        return ("vision" in caps), "api_show_capabilities"
    if row is not None:
        return bool(row.get("sees_natively")), "declared_row"
    # Ollama unreachable. Guess, and say that we guessed.
    return name_looks_vision(tag), "name_guess_ollama_unreachable"


# ---------------------------------------------------------------------------
# Coordinates
# ---------------------------------------------------------------------------

def _load_probe_store() -> dict:
    """The pre-store probe file. Kept one release for migration; see coords_for."""
    try:
        return json.loads(PROBE_STORE.read_text())
    except Exception:
        return {}


def _measurements(tag: str, screen: Optional[Tuple[int, int]]) -> Dict[str, Any]:
    """Thin seam over the calibration store so tests can patch one name.

    A model this machine never measured falls back to the accuracy shipped in
    model_capability_data, when the installed build is the one measured.
    """
    try:
        from backend.services.servo_knowledge_store import load_model_measurements
        m = load_model_measurements(tag, *(screen or (None, None)))
    except Exception as e:  # noqa: BLE001
        logger.debug("measurement store unavailable for %r: %s", tag, e)
        m = {"screen": None, "coords": None, "accuracy": None}
    if not m.get("accuracy"):
        shipped = _shipped_accuracy(tag)
        if shipped:
            m = dict(m, accuracy=shipped, screen=SHIPPED_ACCURACY_SCREEN)
    return m


_digest_cache: Dict[str, Any] = {"at": 0.0, "digests": {}}


def _installed_digests() -> Dict[str, str]:
    """tag -> Ollama manifest digest, refreshed at most once a minute."""
    now = time.time()
    with _lock:
        if now - _digest_cache["at"] < _CACHE_TTL:
            return _digest_cache["digests"]
    digests: Dict[str, str] = {}
    try:
        import requests
        from backend.utils.ollama_resource_manager import get_ollama_base_url
        r = requests.get(f"{get_ollama_base_url()}/api/tags", timeout=5)
        if r.ok:
            digests = {m["name"]: m.get("digest", "") for m in r.json().get("models", [])}
    except Exception:
        pass
    with _lock:
        _digest_cache.update(at=now, digests=digests)
    return digests


def _shipped_accuracy(tag: str) -> Optional[Dict[str, Any]]:
    """The shipped accuracy row for this tag, only if the installed build matches."""
    row = SHIPPED_EYE_ACCURACY.get(tag or "")
    if not row:
        return None
    if not str(_installed_digests().get(tag, "")).startswith(row["digest"]):
        return None
    return dict(row, mode="anchor", source=SHIPPED_ACCURACY_SOURCE, shipped=True)


def accuracy_px(tag: str, screen: Optional[Tuple[int, int]] = None) -> Optional[float]:
    """Median pointing error for this model, measured here or shipped; None if unknown."""
    px = (_measurements(tag, screen).get("accuracy") or {}).get("median_px")
    return float(px) if px is not None else None


def judge_rate(tag: str, screen: Optional[Tuple[int, int]] = None) -> Optional[float]:
    """How often this eye judges a marker's offset right on both axes:
    measured here, else shipped for the installed build, else None."""
    local = (_measurements(tag, screen).get("judge") or {}).get("both_rate")
    if local is not None:
        return float(local)
    row = SHIPPED_EYE_JUDGE.get(tag or "")
    if row and str(_installed_digests().get(tag, "")).startswith(row["digest"]):
        return float(row["both_rate"])
    return None


def judges_well(tag: str, screen: Optional[Tuple[int, int]] = None) -> Optional[bool]:
    """True/False when the eye's judging is measured, None when it is not."""
    rate = judge_rate(tag, screen)
    return None if rate is None else rate >= EYE_JUDGE_MIN_BOTH_RATE


def better_eye_for(tag: str, screen: Optional[Tuple[int, int]] = None) -> Optional[Dict[str, Any]]:
    """An installed eye to lend a model that can see but cannot point.

    None unless the model's measured error exceeds EYE_BORROW_NATIVE_WORSE_THAN_PX
    and an installed model with a known convention measures at most
    EYE_BORROW_EYE_AT_MOST_PX and at most half the model's own error. An eye
    that will not fit beside the model in VRAM is never lent for this: the
    model already sees, so the fallback is only worse aim, not blindness.
    """
    if os.environ.get("GUAARDVARK_EYE_BORROW", "1").strip() == "0":
        return None
    own = accuracy_px(tag, screen)
    if own is None or own <= EYE_BORROW_NATIVE_WORSE_THAN_PX:
        return None
    candidates = [m for m in _installed()
                  if m != tag and sees_natively(m) and coords_for(m, screen).order]
    ranked = [r for r in rank_eyes(candidates, screen)
              if r["fits"] is not False and r["accuracy_px"] is not None
              and r["accuracy_px"] <= EYE_BORROW_EYE_AT_MOST_PX
              and r["accuracy_px"] <= own / 2
              and coords_for(r["tag"], screen).confidence >= MEASURED_CONFIDENCE]
    if not ranked:
        return None
    # Accuracy in bands of half the bar, then memory, then size. Within a band
    # another pixel buys nothing and a smaller eye buys speed: gemma4:12b
    # answers in about a second, qwen3.6:27b in six, and both hit every dot.
    # Across bands the hits differ (qwen3.5:9b at 14px hit 22 of 30 where
    # gemma4:12b hit 30), so accuracy leads. Sitting beside the model in VRAM
    # saves a model swap per step.
    budget = _vram_budget_mb()
    brain_mb = _size_mb(tag)
    band = EYE_BORROW_EYE_AT_MOST_PX / 2

    def _beside(r):
        return budget is None or (r["size_mb"] + brain_mb) * 1.15 <= budget

    ranked.sort(key=lambda r: (int(r["accuracy_px"] // band), 0 if _beside(r) else 1,
                               r["size_mb"]))
    best = ranked[0]
    return {"tag": best["tag"], "eye_px": best["accuracy_px"], "own_px": own}


def _conv_from_record(rec: Dict[str, Any], source: str) -> CoordConvention:
    if not rec.get("order"):
        return CoordConvention(order=None, grid=None, normalised=False,
                               source="probe_failed", confidence=0.0,
                               style=rec.get("style") or DEFAULT_STYLE,
                               min_num_predict=int(rec.get("min_num_predict", 128) or 128),
                               detail={"tried": rec.get("tried", []), "reason": rec.get("reason", "")})
    grid = rec.get("grid", 1000)
    return CoordConvention(
        order=rec["order"], grid=grid, normalised=bool(grid),
        source=source, confidence=float(rec.get("confidence", 0.9)),
        style=rec.get("style") or DEFAULT_STYLE,
        min_num_predict=int(rec.get("min_num_predict", 128)),
        detail={"tried": rec.get("tried", []), "reason": rec.get("reason", "")},
    )


def coords_for(tag: str, screen: Optional[Tuple[int, int]] = None) -> CoordConvention:
    """What convention this model points in, how to ask, and how sure we are.

    Order of authority:

    1. An explicit ``coord_order`` in MODEL_VISION_CONFIGS. Hand-measured and
       load-bearing; never overridden here. Rows are Gemma-dialect by
       construction.
    2. A measured ``coords`` section in the calibration store, written by the
       coordinate probe after trying every dialect against known targets.
    3. The pre-store probe file, for one release, with a nudge to migrate.
    4. The architecture family default, declared in model_capability_data.
    5. The prompt contract: the Google-style request the servo sends by default
       demands ``[y1,x1,y2,x2]`` normalised to 1000, so a model that answers
       with a box at all is claiming to have followed it. Low confidence on
       purpose. It replaces a silent "xy" default that contradicted the very
       prompt the system had just sent.
    """
    try:
        from backend.services.servo_knowledge_store import get_vision_config
        cfg = get_vision_config(tag) or {}
        if cfg.get("coord_order"):
            return CoordConvention(order=cfg["coord_order"], grid=cfg.get("internal_width", 1000),
                                   normalised=True, source="row", confidence=1.0,
                                   style=cfg.get("coord_style", DEFAULT_STYLE))
    except Exception as e:  # noqa: BLE001
        logger.debug("vision config lookup failed for %r: %s", tag, e)

    rec = _measurements(tag, screen).get("coords")
    if rec:
        return _conv_from_record(rec, "probe")

    legacy = _load_probe_store()
    rec = legacy.get(tag) if tag else None
    if rec:
        logger.info("coords for %s came from the pre-store probe file; run "
                    "`probe_coord_order --migrate-legacy` to move it into the calibration store", tag)
        return _conv_from_record(rec, "probe_legacy")

    info = _info(tag) or {}
    fam = (info.get("architecture") or "").lower()
    fd = FAMILY_COORD_DEFAULTS.get(fam)
    if fd:
        return CoordConvention(order=fd["order"], grid=fd["grid"], normalised=fd["normalised"],
                               source="family", confidence=fd["confidence"],
                               style=fd.get("style", DEFAULT_STYLE),
                               min_num_predict=int(fd.get("min_num_predict", 128)),
                               think_uncontrollable=bool(fd.get("think_uncontrollable", False)))

    return CoordConvention(order="yx", grid=1000, normalised=True,
                           source="prompt_contract", confidence=0.5, style=DEFAULT_STYLE)


# ---------------------------------------------------------------------------
# Eyes
# ---------------------------------------------------------------------------

def _installed() -> list:
    try:
        import requests
        from backend.utils.ollama_resource_manager import get_ollama_base_url
        r = requests.get(f"{get_ollama_base_url()}/api/tags", timeout=5)
        return [m["name"] for m in r.json().get("models", [])] if r.ok else []
    except Exception:
        return []


def _resident() -> list:
    try:
        import requests
        from backend.utils.ollama_resource_manager import get_ollama_base_url
        r = requests.get(f"{get_ollama_base_url()}/api/ps", timeout=5)
        return [m["name"] for m in r.json().get("models", [])] if r.ok else []
    except Exception:
        return []


def _size_mb(tag: str) -> float:
    return float((_info(tag) or {}).get("size_mb") or 1e9)


def eye_ranking_mode() -> str:
    mode = (os.environ.get(EYE_RANKING_ENV) or "accuracy").strip().lower()
    return mode if mode in ("accuracy", "confidence") else "accuracy"


def _vram_budget_mb() -> Optional[float]:
    """The card's total VRAM; None when the coordinator cannot say.

    Total, not free: what is free right now is an accident of whichever model
    happened to load last (a 24B bake-off model left resident by a sweep made
    every other eye "not fit" and won the ranking by default). Ollama swaps
    models in and out on demand, so the question that is stable across
    sessions is whether the eye fits on the card at all. A soft signal, never
    a hard gate.
    """
    try:
        from backend.services.gpu_resource_coordinator import get_available_vram
        v = get_available_vram() or {}
        if v.get("success") is False:
            return None
        mb = v.get("total_mb")
        if not mb:
            mb = v.get("available_mb", v.get("free_mb"))
        return float(mb) if mb else None
    except Exception:
        return None


def rank_eyes(candidates: list, screen: Optional[Tuple[int, int]] = None) -> list:
    """Order candidate eyes. Returns [{tag, accuracy_px, accuracy_screen,
    same_screen, confidence, size_mb, fits}], best first.

    In "accuracy" mode the key is (does_not_fit, screen_mismatch, accuracy_px or
    INF, -confidence, size_mb). A model with no measurement sorts after every
    measured one, which is what makes a fresh clone fall through to the old
    confidence order. `fits` is soft: an eye that will not sit beside the brain
    in VRAM is demoted, never removed, and unknown VRAM counts as fits.

    One INFO line lists every candidate's number, so whichever eye was chosen
    the reason is in the log rather than in someone's head.
    """
    mode = eye_ranking_mode()
    want = f"{screen[0]}x{screen[1]}" if screen else None
    budget = _vram_budget_mb()
    resident = set(_resident())
    rows = []
    for tag in candidates:
        m = _measurements(tag, screen)
        acc = (m.get("accuracy") or {})
        acc_px = acc.get("median_px")
        conv = coords_for(tag, screen)
        size = _size_mb(tag)
        # An eye already resident evidently fits; otherwise ask whether it
        # would, with headroom for its context.
        fits = True if tag in resident else (None if budget is None else (budget >= size * 1.15))
        rows.append({
            "tag": tag,
            "accuracy_px": float(acc_px) if acc_px is not None else None,
            "accuracy_screen": m.get("screen"),
            "same_screen": (want is None) or (m.get("screen") == want),
            "confidence": conv.confidence,
            "size_mb": size,
            "fits": fits,
        })
    if mode == "confidence":
        rows.sort(key=lambda r: (-r["confidence"], r["size_mb"]))
    else:
        # Buckets first, numbers second. "Measured" must not beat "unmeasured"
        # regardless of how bad the measurement is: the first live run ranked a
        # 138px eye above a hand-measured row simply because it was the only
        # one with a number yet. A measured-and-usable eye outranks a trusted
        # unmeasured one, which outranks a measured-but-poor one, which
        # outranks a guess.
        def _bucket(r):
            acc = r["accuracy_px"]
            if acc is not None and acc <= USABLE_ACCURACY_PX:
                return 0
            if acc is None and r["confidence"] >= MEASURED_CONFIDENCE:
                return 1
            if acc is not None:
                return 2
            return 3
        rows.sort(key=lambda r: (
            1 if r["fits"] is False else 0,
            _bucket(r),
            0 if r["same_screen"] else 1,
            r["accuracy_px"] if r["accuracy_px"] is not None else float("inf"),
            -r["confidence"],
            r["size_mb"],
        ))
    if rows:
        logger.info(
            "eye ranking=%s on %s: %s", mode, want or "any screen",
            "; ".join(
                f"{r['tag']} conf={r['confidence']:.2f} "
                f"acc={'%.0fpx' % r['accuracy_px'] if r['accuracy_px'] is not None else 'unmeasured'}"
                f"{'' if r['same_screen'] else '@' + str(r['accuracy_screen'])} "
                f"fits={'?' if r['fits'] is None else ('yes' if r['fits'] else 'no')}"
                for r in rows
            ),
        )
    return rows


def eyes_for(tag: str, surface: str = "agent_screen",
             screen: Optional[Tuple[int, int]] = None) -> Eyes:
    """Who looks at the screen for this model.

    A model that can see does its own looking. Only a blind one borrows eyes,
    ranked by rank_eyes, with a model already resident preferred among the
    top-ranked so the answer costs no model swap.
    """
    if sees_natively(tag):
        return Eyes(model=None, mechanism="native", reason="This model sees the screen itself.")

    installed = _installed()
    if not installed:
        return Eyes(model=None, mechanism="none",
                    reason="Ollama is not reachable, so no eyes can be loaned.")

    candidates = [m for m in installed if m != tag and sees_natively(m)]
    if surface == "agent_screen":
        candidates = [m for m in candidates if coords_for(m, screen).order]
    if not candidates:
        return Eyes(model=None, mechanism="none",
                    reason="No vision-capable model is installed to lend eyes.")

    ranked = [r["tag"] for r in rank_eyes(candidates, screen)] if surface == "agent_screen" \
        else sorted(candidates, key=_size_mb)
    resident = set(_resident())
    # Resident wins only among eyes that are at least as good as the best: a
    # loaded but poor eye should not beat an unloaded excellent one.
    if ranked and ranked[0] not in resident:
        best = ranked[0]
        for m in ranked:
            if m in resident and _measurements(m, screen).get("accuracy") == _measurements(best, screen).get("accuracy"):
                return Eyes(model=m, mechanism="sibling_vlm",
                            reason=f"{m} is already loaded and can see; no model swap needed.")
    pick = ranked[0]
    why = "already loaded" if pick in resident else "will be loaded alongside it"
    return Eyes(model=pick, mechanism="sibling_vlm",
                reason=f"{tag} cannot see, so {pick} ({why}) will look.")


# ---------------------------------------------------------------------------
# The whole answer
# ---------------------------------------------------------------------------

def resolve(tag: str, surface: str = "agent_screen",
            screen: Optional[Tuple[int, int]] = None) -> ModelProfile:
    if surface not in SURFACES:
        raise ValueError(f"unknown surface {surface!r}; expected one of {SURFACES}")
    ck = (tag or "", surface, tuple(screen) if screen else None)
    now = time.time()
    with _lock:
        hit = _cache.get(ck)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]

    info = _info(tag)
    vision, evidence = _vision_with_evidence(tag)
    from backend.services.model_capabilities import record_from_info
    record = record_from_info(tag, info, with_vision=False)
    eyes = eyes_for(tag, surface, screen)
    coords = coords_for(tag, screen)

    blockers = []
    if info is None and tag not in EXTERNAL_MODEL_ROWS:
        blockers.append("Ollama cannot describe this model — capabilities are guessed from its name.")
    if not vision and eyes.model is None:
        blockers.append(eyes.reason)
    if coords.source == "probe_failed":
        tried = ", ".join(sorted({t.get("style", "?") for t in coords.detail.get("tried", [])})) or "the default dialect"
        blockers.append("Probed and could not point: no usable answer to the pointing prompt "
                        f"in any of: {tried}. It can see; it cannot point.")
    elif coords.order is None:
        blockers.append("Coordinate convention unknown — run the coordinate probe before clicking.")
    elif coords.confidence < MEASURED_CONFIDENCE:
        blockers.append(f"Coordinate convention is a guess ({coords.source}) — run the "
                        f"coordinate probe to confirm it before trusting a click.")

    prof = ModelProfile(
        tag=tag,
        exists=info is not None,
        sees_natively=vision,
        supports_tools=record.tools,
        supports_thinking=record.thinking,
        context_window=record.native_context,
        size_mb=record.size_mb,
        architecture=record.architecture,
        eyes=eyes,
        coords=coords,
        # A guessed convention is not a licence to click. Reading a model's
        # output with the wrong axis order does not fail loudly — it clicks a
        # plausible wrong place, mirrored across the diagonal, and reports
        # success. Measured on gemma4:e4b: 326px median error read the wrong
        # way against 63px read the right way.
        can_drive_screen=bool((vision or eyes.model) and coords.order
                              and coords.confidence >= MEASURED_CONFIDENCE),
        blockers=tuple(blockers),
        evidence={"vision": evidence, "coords": coords.source},
    )
    with _lock:
        _cache[ck] = (now, prof)
    return prof


def invalidate(tag: Optional[str] = None) -> None:
    """Drop cached answers. Call on model switch and after a probe writes."""
    with _lock:
        if tag is None:
            _cache.clear()
        else:
            for k in [k for k in _cache if k[0] == tag]:
                _cache.pop(k, None)


def describe_for_ui(tag: str, surface: str = "agent_screen",
                    screen: Optional[Tuple[int, int]] = None) -> dict:
    """The shape a model picker needs: can it drive the screen, and what else loads."""
    p = resolve(tag, surface, screen)
    alongside = []
    if p.eyes.model:
        alongside.append({"kind": "model", "tag": p.eyes.model,
                          "vram_mb": round(_size_mb(p.eyes.model)), "why": "eyes"})
    acc = (_measurements(tag, screen).get("accuracy") or {})
    return {
        "tag": p.tag, "exists": p.exists, "sees_natively": p.sees_natively,
        "can_drive_screen": p.can_drive_screen, "blockers": list(p.blockers),
        "supports_tools": p.supports_tools, "supports_thinking": p.supports_thinking,
        "context_window": p.context_window, "size_mb": round(p.size_mb),
        "architecture": p.architecture,
        "coords": {"order": p.coords.order, "grid": p.coords.grid, "style": p.coords.style,
                   "source": p.coords.source, "confidence": p.coords.confidence},
        "accuracy": {"median_px": acc.get("median_px"), "n": acc.get("n"),
                     "screen": _measurements(tag, screen).get("screen")},
        "eyes": {"model": p.eyes.model, "mechanism": p.eyes.mechanism, "reason": p.eyes.reason},
        "will_load_alongside": alongside,
        "evidence": p.evidence,
    }
