"""Media model registry — single source of truth for stills + character LoRA bases.

Mirrors the philosophy of the Ollama model selector and video_model_registry:
profiles are declared here; Settings pick defaults; Cast/LoRA train + inference
must agree on `base_model_id` (no more silent "force SDXL when any LoRA").

Roles:
  - stills_t2i: default / chat / batch image generation
  - lora_train: character/environment/prop LoRA training target
  - max_quality: optional higher-ceiling stills (FLUX-dev)

Train backends are pluggable. Only backends with train_ready=True may run.
Z-Image is the product default; FLUX is max-quality; SDXL is legacy only.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Profile IDs (stable API / DB values) ─────────────────────────────────────
ZIMAGE_TURBO = "zimage-turbo"
FLUX_DEV = "flux-dev"
SDXL_LEGACY = "sdxl-legacy"
KREA2_TURBO = "krea2-turbo"
AUTO = "auto"

DEFAULT_STILLS_MODEL = ZIMAGE_TURBO
DEFAULT_CAST_TRAIN_BASE = ZIMAGE_TURBO
DEFAULT_MAX_QUALITY_MODEL = FLUX_DEV

# Setting keys (settings table via get_setting / save_setting)
SETTING_STILLS_MODEL = "media_stills_model"
SETTING_CAST_TRAIN_BASE = "media_cast_train_base"
SETTING_MAX_QUALITY_MODEL = "media_max_quality_model"

ENV_STILLS = "GUAARDVARK_STILLS_MODEL"
ENV_CAST_TRAIN = "GUAARDVARK_CAST_TRAIN_BASE"
ENV_MAX_QUALITY = "GUAARDVARK_MAX_QUALITY_MODEL"


MEDIA_MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    ZIMAGE_TURBO: {
        "id": ZIMAGE_TURBO,
        "name": "Z-Image Turbo",
        "description": (
            "Default stills + character LoRA base. Fast, strong prompt adherence, "
            "best daily-driver quality on 16GB GPUs."
        ),
        "family": "zimage",
        "roles": ["stills_t2i", "lora_train"],
        "recommended": True,
        "tier": "default",
        # Diffusers / offline_image_generator catalog key
        "offline_model_key": "zimage-turbo",
        "hf_id": "Tongyi-MAI/Z-Image-Turbo",
        "lora_format": "zimage",
        "inference_engine": "offline",  # offline_image_generator (+ future Comfy)
        "comfy_model_tag": None,
        "train_backend": "peft_zimage",
        "train_ready": True,
        "train_status_note": (
            "PEFT flow-matching LoRA on Z-Image Turbo (16GB-safe: cache latents, "
            "768 default). Optional Ostris turbo adapter via ZIMAGE_TURBO_TRAIN_ADAPTER."
        ),
        "vram_train_mb": 12000,
        "vram_infer_mb": 11000,
        "order": 0,
    },
    FLUX_DEV: {
        "id": FLUX_DEV,
        "name": "FLUX.1 Dev",
        "description": (
            "Max-quality stills / character path. Heavier, slower, stronger ceiling "
            "and mature LoRA ecosystem."
        ),
        "family": "flux",
        "roles": ["stills_t2i", "lora_train", "max_quality"],
        "recommended": False,
        "tier": "max_quality",
        "offline_model_key": None,  # Comfy path today
        "hf_id": "black-forest-labs/FLUX.1-dev",
        "lora_format": "flux",
        "inference_engine": "comfy",
        "comfy_model_tag": "flux-dev",
        "train_backend": "ai_toolkit_flux",
        "train_ready": False,
        "train_status_note": (
            "FLUX character training will use a dedicated recipe (quantized on 16GB). "
            "Not the product default — use for max-quality identity."
        ),
        "vram_train_mb": 14000,
        "vram_infer_mb": 12000,
        "order": 1,
    },
    KREA2_TURBO: {
        "id": KREA2_TURBO,
        "name": "Krea 2 Turbo",
        "description": "Optional stills model. High quality but much slower than Z-Image on this box.",
        "family": "krea2",
        "roles": ["stills_t2i"],
        "recommended": False,
        "tier": "optional",
        "offline_model_key": "krea2-turbo",
        "hf_id": "krea/Krea-2-Turbo",
        "lora_format": None,  # not a cast train base
        "inference_engine": "offline",
        "comfy_model_tag": None,
        "train_backend": None,
        "train_ready": False,
        "train_status_note": "Not used for character LoRA training.",
        "vram_train_mb": 0,
        "vram_infer_mb": 14000,
        "order": 2,
    },
    SDXL_LEGACY: {
        "id": SDXL_LEGACY,
        "name": "SDXL (Legacy)",
        "description": (
            "Deprecated identity base. Kept only so existing SDXL LoRAs and the "
            "current PEFT trainer still work until Z-Image/FLUX trainers ship."
        ),
        "family": "sdxl",
        "roles": ["stills_t2i", "lora_train"],
        "recommended": False,
        "tier": "legacy",
        "offline_model_key": "sd-xl",
        "hf_id": "stabilityai/stable-diffusion-xl-base-1.0",
        "lora_format": "kohya_sdxl",
        "inference_engine": "comfy",
        "comfy_model_tag": "sdxl",
        "train_backend": "peft_sdxl",
        "train_ready": True,  # only fully wired trainer today
        "train_status_note": "Legacy. Prefer Z-Image once its train backend is ready.",
        "vram_train_mb": 12000,
        "vram_infer_mb": 8000,
        "order": 99,
        "deprecated": True,
    },
}


# ── Render limits (read through backend/services/image_render_limits.py) ──────
#
# One row per family, then per-model rows keyed by the stills catalog id
# (OfflineImageGenerator.available_models) that name their family and override
# what differs. Fields:
#   max_side / max_pixel_area / min_side   canvas ceiling and floor (px)
#   dimension_alignment                   grid each side snaps to (px)
#   width / height                        canvas when a request names none
#   default_steps / min_steps             steps when none are given; the floor a
#                                         default or an agent may not go under
#   cfg_when_unset                        guidance when none is given
#   steps_range / cfg_range               the model's working envelope
#   min_dimensions                        smallest canvas the model is validated at
#   hard_clamp                            False: out-of-range values are kept and
#                                         warned about (quality slider owns them)
#   prompt_style                          "natural" (LLM encoder, prose) or "tags"
#   vram_mb / ram_gb                      admission price at 1 MP; *_slope_* add
#                                         per megapixel above that; flux is priced
#                                         for ComfyUI and has no slope
#   engine                                "offline" (Diffusers) or "comfy"
#   starts_from_family                    the caller-side defaults are the family
#                                         row's unless GUAARDVARK_IMAGE_STRICT_LIMITS
#                                         is on (the model's own values are what the
#                                         validator recommends)
IMAGE_FAMILY_SPECS: dict[str, dict[str, Any]] = {
    # Z-Image: official canvas ~512-2048 per side / ~2048^2 area. The long side
    # may reach 2688 so 16:9 2K packs (2688x1472, ~3.95 MP) fit under the area.
    # 9 steps / guidance 0.0 is the official HF recipe (9 steps -> 8 DiT
    # forwards, CFG distilled out). min_steps 2 measured 2026-09-13 on a 16 GB
    # card at 1024x1024, seed 1984, verbatim prompts, three scenes at 1-9 steps:
    # 1 step is visibly broken (grain, blank faces, smeared text), 2 is clean and
    # only slightly softer than 3-9. prompt_style "natural": Qwen3 encoder,
    # measured 2026-09-07 (tag suffixes read as scene content, 4/4 seeds).
    # vram 11000 with CPU model offload; ram 21.0 measured 2026-08-05 (peak RSS
    # 20.9-21.0 GB flat at 1024/1448/2048 after the unload-leak fixes; 16.3 GB
    # for one 1024 image on a 32 GB box). Slopes calibrated 2026-08-04 on a
    # 16 GB 4070 Ti SUPER, sequential offload with VAE tiling: 1448^2 peak
    # 12467 MB, 2048^2 peak 9534 MB.
    "zimage": {
        "max_side": 2688, "max_pixel_area": 2048 * 2048, "min_side": 256, "dimension_alignment": 16,
        "width": 1024, "height": 1024, "default_steps": 9, "min_steps": 2, "cfg_when_unset": 0.0,
        "prompt_style": "natural", "engine": "offline",
        "vram_mb": 11000, "ram_gb": 21.0, "vram_slope_mb_per_mp": 500, "ram_slope_gb_per_mp": 1.0,
    },
    # Krea 2: native 2K, same canvas as Z-Image. The family row is Turbo
    # (8 steps, CFG-free); Raw overrides below. Model-offload peak ~14 GB on a
    # 16 GB card (2026-07-11); sequential offload on cards up to 18 GB peaks at
    # ~10 GB. Also an LLM encoder (Qwen3-VL) but not A/B'd, so "tags".
    "krea2": {
        "max_side": 2688, "max_pixel_area": 2048 * 2048, "min_side": 256, "dimension_alignment": 16,
        "width": 1024, "height": 1024, "default_steps": 8, "cfg_when_unset": 0.0,
        "prompt_style": "tags", "engine": "offline",
        "vram_mb": 14000, "vram_mb_sequential": 10000, "ram_gb": 24.0,
        "vram_slope_mb_per_mp": 1000, "ram_slope_gb_per_mp": 1.0,
    },
    # FLUX.1-dev: design range ~0.1-2.0 MP (not 2048^2), through ComfyUI.
    # 28 steps / FluxGuidance 3.5 is the verified quality point.
    "flux": {
        "max_side": 1920, "max_pixel_area": 2_100_000, "min_side": 256, "dimension_alignment": 16,
        "width": 1024, "height": 1024, "default_steps": 28, "cfg_when_unset": 3.5,
        "prompt_style": "tags", "engine": "comfy",
        "vram_mb": 12000, "ram_gb": 16.0,
    },
    "sdxl": {
        "max_side": 1536, "max_pixel_area": 1536 * 1536, "min_side": 256, "dimension_alignment": 16,
        "width": 1024, "height": 1024, "default_steps": 25, "cfg_when_unset": 7.0,
        "prompt_style": "tags", "engine": "offline",
        "vram_mb": 8000, "ram_gb": 10.0, "vram_slope_mb_per_mp": 1500, "ram_slope_gb_per_mp": 1.0,
    },
    "sd": {
        "max_side": 768, "max_pixel_area": 768 * 768, "min_side": 256, "dimension_alignment": 16,
        "width": 512, "height": 512, "default_steps": 20, "cfg_when_unset": 7.5,
        "prompt_style": "tags", "engine": "offline",
        "vram_mb": 4000, "ram_gb": 6.0, "vram_slope_mb_per_mp": 800, "ram_slope_gb_per_mp": 0.5,
    },
}

IMAGE_MODEL_LIMITS: dict[str, dict[str, Any]] = {
    # CFG-distilled: the steps range is the recommended envelope, low end the
    # measured floor above; out-of-range values are warned about, not clamped.
    "zimage-turbo": {"family": "zimage", "steps_range": (2, 30), "cfg_range": (0.0, 2.0),
                     "min_dimensions": (512, 512), "hard_clamp": False},
    "krea2-turbo": {"family": "krea2", "steps_range": (4, 20), "cfg_range": (0.0, 1.0),
                    "min_dimensions": (512, 512), "hard_clamp": False},
    # Krea 2 Raw: the base checkpoint, ~52 steps / CFG 3.5.
    "krea2-raw": {"family": "krea2", "default_steps": 52, "cfg_when_unset": 3.5,
                  "steps_range": (20, 80), "cfg_range": (1.0, 7.0),
                  "min_dimensions": (512, 512), "hard_clamp": False},
    # FluxGuidance, not classic CFG. Heavy: batches run one image at a time.
    "flux-dev": {"family": "flux", "steps_range": (8, 50), "cfg_range": (1.0, 6.0),
                 "min_dimensions": (512, 512), "hard_clamp": False, "force_max_workers": 1},
    # SDXL: guidance above 9 renders black images, so its range is enforced.
    "sd-xl": {"family": "sdxl", "steps_range": (20, 40), "cfg_range": (4.0, 9.0),
              "min_dimensions": (768, 768)},
    "sdxl-turbo": {"family": "sdxl", "starts_from_family": True, "default_steps": 4, "cfg_when_unset": 0.0,
                   "steps_range": (1, 4), "cfg_range": (0.0, 1.0), "min_dimensions": (768, 768)},
    "sd-1.5": {"family": "sd", "steps_range": (10, 50), "cfg_range": (1.0, 15.0),
               "min_dimensions": (512, 512)},
    # SD 1.5 fine-tunes, portrait-first canvases.
    "realistic-vision": {"family": "sd", "starts_from_family": True, "default_steps": 30, "cfg_when_unset": 8.0,
                         "steps_range": (25, 40), "cfg_range": (7.0, 10.0),
                         "min_dimensions": (512, 512), "width": 512, "height": 768},
    "epic-realism": {"family": "sd", "starts_from_family": True, "default_steps": 35, "cfg_when_unset": 7.5,
                     "steps_range": (30, 40), "cfg_range": (7.0, 9.0),
                     "min_dimensions": (512, 512), "width": 512, "height": 768},
}

def get_profile(model_id: str | None) -> Optional[dict[str, Any]]:
    if not model_id:
        return None
    mid = str(model_id).strip().lower()
    if mid in ("sd-xl", "sdxl", "sdxl-base", "sdxl-base-1.0"):
        mid = SDXL_LEGACY
    if mid in ("flux", "flux.1-dev", "flux1-dev"):
        mid = FLUX_DEV
    if mid in ("z-image-turbo", "zimage", "tongyi-mai/z-image-turbo"):
        mid = ZIMAGE_TURBO
    return MEDIA_MODEL_REGISTRY.get(mid)


def list_profiles(
    *,
    role: str | None = None,
    include_legacy: bool = True,
) -> list[dict[str, Any]]:
    rows = []
    for p in MEDIA_MODEL_REGISTRY.values():
        if role and role not in (p.get("roles") or []):
            continue
        if not include_legacy and p.get("deprecated"):
            continue
        rows.append(dict(p))
    rows.sort(key=lambda r: (r.get("order", 50), r.get("name") or ""))
    return rows


def resolve_stills_model(requested: str | None = None) -> str:
    """Resolve stills model id for generation (auto → Settings default)."""
    req = (requested or "").strip().lower()
    if req and req != AUTO:
        # Accept offline catalog keys that map into registry
        if req in MEDIA_MODEL_REGISTRY:
            return req
        p = get_profile(req)
        if p:
            return p["id"]
        # Pass through offline keys (realistic-vision, etc.) outside registry
        return req
    return get_stills_model_setting()


def get_stills_model_setting() -> str:
    return _read_setting(SETTING_STILLS_MODEL, ENV_STILLS, DEFAULT_STILLS_MODEL)


def get_cast_train_base_setting() -> str:
    return _read_setting(SETTING_CAST_TRAIN_BASE, ENV_CAST_TRAIN, DEFAULT_CAST_TRAIN_BASE)


def get_max_quality_model_setting() -> str:
    return _read_setting(SETTING_MAX_QUALITY_MODEL, ENV_MAX_QUALITY, DEFAULT_MAX_QUALITY_MODEL)


def set_stills_model_setting(model_id: str) -> str:
    return _write_setting(SETTING_STILLS_MODEL, model_id, allow_auto=True)


def set_cast_train_base_setting(model_id: str) -> str:
    mid = _normalize_train_base(model_id)
    return _write_setting(SETTING_CAST_TRAIN_BASE, mid, allow_auto=False)


def set_max_quality_model_setting(model_id: str) -> str:
    mid = (model_id or DEFAULT_MAX_QUALITY_MODEL).strip().lower()
    if mid not in MEDIA_MODEL_REGISTRY:
        raise ValueError(f"Unknown max-quality model: {model_id}")
    return _write_setting(SETTING_MAX_QUALITY_MODEL, mid, allow_auto=False)


def _normalize_train_base(model_id: str) -> str:
    p = get_profile(model_id)
    if not p:
        raise ValueError(f"Unknown cast train base: {model_id}")
    if "lora_train" not in (p.get("roles") or []):
        raise ValueError(f"{p['id']} is not a character LoRA train base")
    return p["id"]


def _read_setting(key: str, env_name: str, default: str) -> str:
    try:
        from backend.utils.settings_utils import get_setting
        val = get_setting(key, default=None)
        if val:
            return str(val).strip().lower()
    except Exception:
        pass
    env = os.environ.get(env_name, "").strip().lower()
    if env:
        return env
    return default


def _write_setting(key: str, model_id: str, *, allow_auto: bool) -> str:
    mid = (model_id or "").strip().lower() or (AUTO if allow_auto else "")
    if allow_auto and mid == AUTO:
        pass
    elif mid not in MEDIA_MODEL_REGISTRY and not (allow_auto and mid == AUTO):
        # stills may use offline keys not in registry (e.g. realistic-vision)
        if not allow_auto:
            raise ValueError(f"Unknown model id: {model_id}")
    try:
        from backend.utils.settings_utils import save_setting
        save_setting(key, mid)
    except Exception as e:
        logger.warning("media_model_registry: save_setting(%s) failed: %s", key, e)
    return mid


def subject_base_model_id(subject) -> str:
    """Base model this subject's LoRA is trained for (or will be)."""
    raw = getattr(subject, "training_settings_json", None) or {}
    if isinstance(raw, dict):
        bid = raw.get("base_model_id") or raw.get("base_model")
        if bid:
            p = get_profile(str(bid))
            if p:
                return p["id"]
    # Infer from existing LoRA sidecar if present
    lp = getattr(subject, "lora_path", None)
    if lp:
        meta = read_lora_sidecar(lp)
        if meta and meta.get("base_model_id"):
            p = get_profile(str(meta["base_model_id"]))
            if p:
                return p["id"]
        # Historic LoRAs with no sidecar field → SDXL (old trainer)
        if meta is not None or (lp and Path(lp).is_file()):
            return SDXL_LEGACY
    return get_cast_train_base_setting()


def assert_train_ready(base_model_id: str) -> dict[str, Any]:
    """Return profile or raise ValueError if training cannot run."""
    p = get_profile(base_model_id)
    if not p:
        raise ValueError(f"Unknown train base: {base_model_id}")
    if "lora_train" not in (p.get("roles") or []):
        raise ValueError(f"{p['id']} does not support LoRA training")
    if not p.get("train_ready"):
        note = p.get("train_status_note") or "Trainer not ready for this base."
        raise ValueError(
            f"Character training for '{p['name']}' is not ready yet. {note} "
            f"Use Settings → Media models → Cast train base → '{ZIMAGE_TURBO}' "
            f"(default) or '{SDXL_LEGACY}' (legacy)."
        )
    return p


def lora_compatible_with_inference(base_model_id: str, inference_model: str | None) -> bool:
    """True if a LoRA trained for base_model_id can be applied under inference_model."""
    base = get_profile(base_model_id)
    if not base:
        return False
    inf = (inference_model or "").strip().lower()
    if not inf or inf == AUTO:
        # Default stills path must match train base family for cast
        stills = get_stills_model_setting()
        if stills == AUTO:
            stills = DEFAULT_STILLS_MODEL
        inf_profile = get_profile(stills)
    else:
        inf_profile = get_profile(inf)
        if not inf_profile and inf in ("sd-xl", "sdxl"):
            inf_profile = get_profile(SDXL_LEGACY)
        if not inf_profile and "flux" in inf:
            inf_profile = get_profile(FLUX_DEV)
        if not inf_profile and ("zimage" in inf or "z-image" in inf):
            inf_profile = get_profile(ZIMAGE_TURBO)
    if not inf_profile:
        # Unknown offline key: only OK if same string as offline_model_key
        return base.get("offline_model_key") == inf
    return base.get("family") == inf_profile.get("family")


def read_lora_sidecar(lora_path: str | None) -> Optional[dict[str, Any]]:
    if not lora_path:
        return None
    p = Path(lora_path)
    side = p.with_suffix(".json")
    if not side.is_file():
        return None
    try:
        data = json.loads(side.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def write_lora_sidecar(
    lora_path: str | Path,
    *,
    subject_id: int,
    subject_name: str,
    trigger_word: str,
    base_model_id: str,
    ref_count: int,
    steps: int | None = None,
    mock: bool = False,
    extra: dict | None = None,
) -> Path:
    """Write the mandatory LoRA artifact sidecar (base_model_id is required)."""
    profile = get_profile(base_model_id) or {}
    out = Path(lora_path).with_suffix(".json")
    payload = {
        "subject_id": subject_id,
        "subject_name": subject_name,
        "trigger_word": trigger_word,
        "base_model_id": profile.get("id") or base_model_id,
        "lora_format": profile.get("lora_format"),
        "family": profile.get("family"),
        "train_backend": profile.get("train_backend"),
        "instance_prompt": f"a photo of {trigger_word}",
        "ref_count": ref_count,
        "steps": steps,
        "mock": bool(mock),
        "schema_version": 2,
    }
    if extra:
        payload.update(extra)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def resolve_inference_for_loras(lora_paths: list[str]) -> dict[str, Any]:
    """Given LoRA file paths, decide inference engine/model or raise on conflict.

    Returns dict: base_model_id, family, inference_engine, comfy_model_tag,
    offline_model_key, lora_format.
    """
    if not lora_paths:
        raise ValueError("no loras")
    bases: list[str] = []
    for lp in lora_paths:
        meta = read_lora_sidecar(lp)
        if meta and meta.get("base_model_id"):
            bases.append(str(meta["base_model_id"]))
        else:
            # Pre-registry LoRAs from the SDXL PEFT trainer
            bases.append(SDXL_LEGACY)
    uniq = []
    for b in bases:
        p = get_profile(b)
        bid = p["id"] if p else b
        if bid not in uniq:
            uniq.append(bid)
    if len(uniq) > 1:
        raise ValueError(
            f"Cannot mix LoRAs trained for different bases in one generate: {uniq}. "
            "Generate one character base at a time."
        )
    bid = uniq[0]
    p = get_profile(bid)
    if not p:
        raise ValueError(f"Unknown LoRA base_model_id: {bid}")
    return {
        "base_model_id": p["id"],
        "family": p["family"],
        "inference_engine": p["inference_engine"],
        "comfy_model_tag": p.get("comfy_model_tag"),
        "offline_model_key": p.get("offline_model_key"),
        "lora_format": p.get("lora_format"),
        "profile": p,
    }


def _lora_strength_settings() -> dict[str, float]:
    from backend.services.cast_lock import (
        DEFAULT_FLUX_DEV_STRENGTH,
        DEFAULT_SDXL_STRENGTH,
        DEFAULT_ZIMAGE_STRENGTH,
        SETTING_STRENGTH_FLUX,
        SETTING_STRENGTH_SDXL,
        SETTING_STRENGTH_ZIMAGE,
        resolve_lora_strength,
    )
    return {
        "zimage": resolve_lora_strength("zimage-turbo"),
        "sdxl": resolve_lora_strength("sdxl"),
        "flux": resolve_lora_strength("flux-dev"),
        "defaults": {
            "zimage": DEFAULT_ZIMAGE_STRENGTH,
            "sdxl": DEFAULT_SDXL_STRENGTH,
            "flux": DEFAULT_FLUX_DEV_STRENGTH,
        },
        "keys": {
            "zimage": SETTING_STRENGTH_ZIMAGE,
            "sdxl": SETTING_STRENGTH_SDXL,
            "flux": SETTING_STRENGTH_FLUX,
        },
    }


def set_character_lora_strength(family: str, value: float) -> float:
    """Persist operator override for character LoRA strength for a family."""
    from backend.services.cast_lock import (
        SETTING_STRENGTH_FLUX,
        SETTING_STRENGTH_SDXL,
        SETTING_STRENGTH_ZIMAGE,
        resolve_lora_strength,
    )
    from backend.models import SystemSetting, db

    fam = (family or "").lower().strip()
    key = {
        "zimage": SETTING_STRENGTH_ZIMAGE,
        "zimage-turbo": SETTING_STRENGTH_ZIMAGE,
        "sdxl": SETTING_STRENGTH_SDXL,
        "flux": SETTING_STRENGTH_FLUX,
        "flux-dev": SETTING_STRENGTH_FLUX,
    }.get(fam)
    if not key:
        raise ValueError(f"Unknown LoRA strength family: {family}")
    clamped = resolve_lora_strength(
        "zimage-turbo" if "zimage" in fam else ("flux-dev" if "flux" in fam else "sdxl"),
        float(value),
    )
    row = SystemSetting.query.filter_by(key=key).first()
    if row is None:
        row = SystemSetting(key=key, value=str(clamped))
        db.session.add(row)
    else:
        row.value = str(clamped)
    db.session.commit()
    return clamped


def public_settings_payload() -> dict[str, Any]:
    """JSON for Settings / Studio headers."""
    stills = get_stills_model_setting()
    train = get_cast_train_base_setting()
    maxq = get_max_quality_model_setting()
    return {
        "stills_model": stills,
        "cast_train_base": train,
        "max_quality_model": maxq,
        "character_lora_strength": _lora_strength_settings(),
        "defaults": {
            "stills_model": DEFAULT_STILLS_MODEL,
            "cast_train_base": DEFAULT_CAST_TRAIN_BASE,
            "max_quality_model": DEFAULT_MAX_QUALITY_MODEL,
        },
        "profiles": list_profiles(include_legacy=True),
        "train_profiles": list_profiles(role="lora_train", include_legacy=True),
        "stills_profiles": list_profiles(role="stills_t2i", include_legacy=True),
    }
