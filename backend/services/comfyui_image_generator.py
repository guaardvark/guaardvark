"""ComfyUI SDXL image generator — the LoRA-aware ImageGenerator the Storyboard
Artist needs but never had.

This is the missing bridge: the LoRA trainer produces SDXL character LoRAs, but
nothing applied them at generation time (storyboard gen ignored `loras`
entirely). This class builds an SDXL txt2img workflow with a LoraLoader chain so
the trained character actually shows up in the frame — and that consistent frame
is what the SVD I2V step animates, carrying identity into video.

Model loading uses DiffusersLoader against ComfyUI/models/diffusers/sdxl-base-1.0
(a symlink to the diffusers-format SDXL we already have on disk), so no
single-file checkpoint conversion is needed. Trained LoRAs are referenced by
basename because data/training/loras is registered as a ComfyUI loras search
path via extra_model_paths.yaml.
"""
from __future__ import annotations

import logging
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

try:
    from backend.config import COMFYUI_URL
    _COMFY_URL = COMFYUI_URL
except Exception:  # pragma: no cover - config import is environment-specific
    _COMFY_URL = os.environ.get("GUAARDVARK_COMFYUI_URL", "http://127.0.0.1:8188")

# DiffusersLoader reads from ComfyUI/models/diffusers/<this>. Set up as a symlink
# to the diffusers-format SDXL base by the LoRA-consistency wiring.
SDXL_DIFFUSERS_MODEL = os.environ.get("GUAARDVARK_SDXL_DIFFUSERS", "sdxl-base-1.0")

# Flux asset names for the keyframe/storyboard flux branch (align with infographic
# documented downloads for out-of-box success). Users can override via env if they
# use different quants / filenames in their ComfyUI/models/unet/ and clip/ dirs.
# Common issue: exact filename must appear in ComfyUI's object_info list for the
# loader node, or you get "Value not in list" validation errors.
FLUX_UNET = os.environ.get("GUAARDVARK_FLUX_UNET", "flux1-schnell-Q8_0.gguf")
FLUX_T5 = os.environ.get("GUAARDVARK_FLUX_T5", "t5/t5xxl_fp8_e4m3fn.safetensors")
FLUX_CLIP = os.environ.get("GUAARDVARK_FLUX_CLIP", "clip_l.safetensors")
FLUX_VAE = os.environ.get("GUAARDVARK_FLUX_VAE", "ae.safetensors")

# ── Z-Image Turbo via ComfyUI ────────────────────────────────────────────────
# Gated by GUAARDVARK_ZIMAGE_USE_COMFYUI=1 in batch_image_generator. These names
# must exist inside the reachable ComfyUI (they are checked by the running server's
# object_info). Overridable so a different Z-Image build / Qwen CLIP / VAE works.
ZIMAGE_UNET = os.environ.get("GUAARDVARK_ZIMAGE_UNET", "z_image_turbo_bf16.safetensors")
ZIMAGE_CLIP = os.environ.get("GUAARDVARK_ZIMAGE_CLIP", "qwen_3_4b.safetensors")
ZIMAGE_CLIP_TYPE = os.environ.get("GUAARDVARK_ZIMAGE_CLIP_TYPE", "lumina2")
ZIMAGE_VAE = os.environ.get("GUAARDVARK_ZIMAGE_VAE", "ae.safetensors")
ZIMAGE_SAMPLER = os.environ.get("GUAARDVARK_ZIMAGE_SAMPLER", "res_multistep")
ZIMAGE_SCHEDULER = os.environ.get("GUAARDVARK_ZIMAGE_SCHEDULER", "simple")
# Flow-matching shift for Z-Image Turbo (ModelSamplingAuraFlow). 3 matches the
# working Z-Image Turbo workflow on the reference machine (ComfyUI's
# image_z_image_turbo-api_guaardvark.json sets ModelSamplingAuraFlow shift=3);
# it is not a guess. The distilled model is CFG-free: positive conditioning
# drives the sampler, the negative is zeroed via ConditioningZeroOut, and cfg
# stays 1.0. Env-overridable for other Z-Image builds.
ZIMAGE_SHIFT = float(os.environ.get("GUAARDVARK_ZIMAGE_SHIFT", "3"))

# FLUX-dev (full transformer) keyframe path — the identity-lock route for trained
# character LoRAs. Unlike the schnell GGUF branch, this one ACTUALLY chains LoRAs
# (LoraLoaderModelOnly), uses FluxGuidance, and renders at dev step counts. An fp8
# unet keeps the 12B transformer inside a 16 GB card (ComfyUI smart-offloads T5/clip).
# Verified end-to-end on sage_harlow 2026-06-22 (strength 0.9, 28 steps, guidance 3.5).
FLUX_DEV_UNET = os.environ.get("GUAARDVARK_FLUX_DEV_UNET", "flux1-dev.safetensors")
FLUX_DEV_T5 = os.environ.get("GUAARDVARK_FLUX_DEV_T5", "t5xxl_fp16.safetensors")
FLUX_DEV_WEIGHT_DTYPE = os.environ.get("GUAARDVARK_FLUX_DEV_DTYPE", "fp8_e4m3fn")
FLUX_DEV_GUIDANCE = float(os.environ.get("GUAARDVARK_FLUX_DEV_GUIDANCE", "3.5"))

# PuLID identity experiment switches (the likeness loss at weight 1.0 and 1.5
# on fp8 was the 2026-09-15 suspicion; the real cause was the node freeing its data).
# UNETLoader's weight_dtype choices are default | fp8_e4m3fn | fp8_e4m3fn_fast
# | fp8_e5m2 (ComfyUI nodes.py, UNETLoader.INPUT_TYPES); there is no bf16
# choice. "default" loads the tensors as stored, and flux1-dev.safetensors is
# stored as BF16 (780 tensors in its header, read 2026-09-19), so a bf16
# request maps to "default".
PULID_UNET_DTYPES = {
    "fp8_e4m3fn": "fp8_e4m3fn",
    "fp8_e4m3fn_fast": "fp8_e4m3fn_fast",
    "fp8_e5m2": "fp8_e5m2",
    "bf16": "default",
    "default": "default",
}
# Which apply node builds the graph. PuLID_ComfyUI (the SD/SDXL node) cannot
# drive FLUX: its loader splits the checkpoint into image_proj.* and
# ip_adapter.* keys and ApplyPulid patches the UNet's attn2 cross-attention
# (custom_nodes/PuLID_ComfyUI/pulid.py), while pulid_flux_v0.9.1.safetensors
# carries only pulid_ca.* and pulid_encoder.* keys and FLUX's double/single
# blocks have no attn2 (custom_nodes/ComfyUI-PuLID-Flux/pulidflux.py). Both
# read 2026-09-19.
# The identity weight/window defaults live on the pulid-flux registry entry
# (with the measurement behind them); None in a signature means "use those".
PULID_IDENTITY_DEFAULTS = {"weight": 1.0, "start_at": 0.2, "end_at": 1.0}


def _identity_default(name: str, value):
    return PULID_IDENTITY_DEFAULTS[name] if value is None else value


PULID_NODE_VARIANTS = {
    "pulid_flux": {
        "supported": True,
        "package": "ComfyUI-PuLID-Flux",
        "apply_class": "ApplyPulidFlux",
        "reason": "",
    },
    "pulid_classic": {
        "supported": False,
        "package": "PuLID_ComfyUI",
        "apply_class": "ApplyPulid",
        "reason": (
            "PuLID_ComfyUI targets SD/SDXL: its loader expects image_proj.* and "
            "ip_adapter.* keys and ApplyPulid patches attn2 cross-attention; the "
            "FLUX checkpoint has pulid_ca.*/pulid_encoder.* keys and FLUX blocks "
            "have no attn2, so this node cannot drive FLUX.1-dev."
        ),
    },
}


def resolve_pulid_unet_dtype(unet_dtype: str | None) -> str:
    """The UNETLoader weight_dtype for a requested dtype; None keeps the env default."""
    if unet_dtype is None or unet_dtype == "":
        return FLUX_DEV_WEIGHT_DTYPE
    try:
        return PULID_UNET_DTYPES[str(unet_dtype)]
    except KeyError:
        raise ValueError(
            f"unet_dtype {unet_dtype!r} is not one of {sorted(PULID_UNET_DTYPES)}"
        ) from None


def resolve_pulid_node_variant(node_variant: str | None) -> dict:
    """The variant entry, or ValueError naming why it cannot be used."""
    key = node_variant or "pulid_flux"
    entry = PULID_NODE_VARIANTS.get(str(key))
    if entry is None:
        raise ValueError(f"node_variant {key!r} is not one of {sorted(PULID_NODE_VARIANTS)}")
    if not entry["supported"]:
        raise ValueError(f"node_variant {key!r} unsupported: {entry['reason']}")
    return entry

# ── FLUX.1 Kontext [dev] — instruction image editing ───────────────────────────
# The loader filename is single-sourced from the ComfyUI-models registry (SSOT) so
# the download destination and the loader node can never drift (issue #36 class of
# bug). Companions (t5xxl_fp8, clip_l, ae) are the SAME files the FLUX branches
# already use above — no new asset names introduced.
try:
    from backend.services.video_model_registry import (
        VIDEO_MODEL_REGISTRY as _VMR,
        is_model_installed as _is_model_installed,
        comfyui_models_dir as _comfy_models_dir,
    )
    KONTEXT_UNET = _VMR.get("flux-kontext-dev", {}).get("hf_filename", "flux1-kontext-dev-Q6_K.gguf")
    _QWEN_EDIT = _VMR.get("qwen-image-edit") or {}
    QWEN_EDIT_UNET = ((_QWEN_EDIT.get("files") or [{}])[0].get("dst")
                      or "qwen_image_edit_2509_fp8_e4m3fn.safetensors")
    QWEN_EDIT_MIN_STEPS = int(_QWEN_EDIT.get("min_steps") or 20)
    _QWEN_CLIP = _VMR.get("qwen-image-clip") or {}
    QWEN_EDIT_CLIP = ((_QWEN_CLIP.get("files") or [{}])[0].get("dst")
                      or "qwen_2.5_vl_7b_fp8_scaled.safetensors")
    _QWEN_VAE = _VMR.get("qwen-image-vae") or {}
    QWEN_EDIT_VAE = ((_QWEN_VAE.get("files") or [{}])[0].get("dst")
                     or "qwen_image_vae.safetensors")
    _PULID = _VMR.get("pulid-flux") or {}
    PULID_FLUX_FILE = ((_PULID.get("files") or [{}])[0].get("dst")
                       or "pulid_flux_v0.9.1.safetensors")
    PULID_MIN_STEPS = int(_PULID.get("min_steps") or 20)
    PULID_IDENTITY_DEFAULTS.update(_PULID.get("identity_defaults") or {})
except Exception:  # pragma: no cover - registry import is environment-specific
    KONTEXT_UNET = "flux1-kontext-dev-Q6_K.gguf"
    QWEN_EDIT_UNET = "qwen_image_edit_2509_fp8_e4m3fn.safetensors"
    QWEN_EDIT_MIN_STEPS = 20
    QWEN_EDIT_CLIP = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
    QWEN_EDIT_VAE = "qwen_image_vae.safetensors"
    PULID_FLUX_FILE = "pulid_flux_v0.9.1.safetensors"
    PULID_MIN_STEPS = 20
    _is_model_installed = None
    _comfy_models_dir = None

# A neutral SDXL negative — keeps anatomy/quality sane without fighting the LoRA.
DEFAULT_NEGATIVE = (
    "lowres, bad anatomy, bad hands, cropped, worst quality, low quality, "
    "jpeg artifacts, watermark, signature, deformed, extra limbs, blurry, "
    # Identity/anatomy-bleed guard (character-LoRA "horse-head" failure mode). Scoped to
    # human-animal HYBRID artifacts so a legitimately-present animal still renders. Kept in
    # sync with backend/utils/prompt_enhancer.IDENTITY_BLEED_NEGATIVE.
    "animal head, horse head, animal ears, animal face, fur on face, snout, muzzle, "
    "human-animal hybrid, anthropomorphic, extra head, two heads, mutated anatomy"
)



def _registry_vram(model_id: str, default: int = 12000) -> int:
    """VRAM debit for a chat edit graph: the registry entry's measured value."""
    try:
        from backend.services.video_model_registry import vram_mb_for_model
        return int(vram_mb_for_model(model_id, default=default))
    except Exception:  # noqa: BLE001 — registry import is environment-specific
        return default
def _comfyui_loras_dir() -> Optional[Path]:
    """Locate the running ComfyUI's ``models/loras`` directory (best-effort).

    ``GUAARDVARK_COMFYUI_LORAS_DIR`` wins when set (the operator knows where the
    running ComfyUI keeps its loras). Otherwise the configured ``COMFYUI_DIR`` and
    the bundled plugin copy are probed, and the first that exists wins.
    """
    candidates: list[Path] = []
    env_dir = os.environ.get("GUAARDVARK_COMFYUI_LORAS_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
    try:
        from backend.config import COMFYUI_DIR
        candidates.append(Path(COMFYUI_DIR) / "models" / "loras")
    except Exception:
        pass
    candidates.append(
        Path(__file__).resolve().parents[3] / "plugins" / "comfyui" / "ComfyUI" / "models" / "loras"
    )
    for c in candidates:
        if c.is_dir():
            return c
    return None


def ensure_lora_in_comfyui(lora_path: str) -> bool:
    """Symlink a trained LoRA into ComfyUI's ``models/loras`` so a
    ``LoraLoaderModelOnly`` node can resolve it by basename.

    Only acts when Z-Image is routed through ComfyUI
    (``GUAARDVARK_ZIMAGE_USE_COMFYUI=1``). Returns True if the LoRA is present in
    ComfyUI's loras dir (linked now, or already there). Best-effort: never raises.
    """
    # Parse the flag in exactly one place (stills_pipeline owns the env var), so
    # renaming it cannot leave this gate reading a dead name.
    from backend.services.stills_pipeline import zimage_via_comfyui_enabled
    if not zimage_via_comfyui_enabled():
        return False
    p = Path(lora_path)
    if not p.exists():
        return False
    loras_dir = _comfyui_loras_dir()
    if loras_dir is None:
        logger.warning("ensure_lora_in_comfyui: could not locate ComfyUI loras dir for %s", p.name)
        return False
    target = loras_dir / p.name
    if target.is_symlink() and not target.exists():
        # Dangling symlink (target moved or removed): a broken link reads as
        # absent to ``exists()``, so heal it instead of failing to link below.
        try:
            target.unlink()
        except OSError as e:
            logger.warning("Could not remove dangling LoRA link %s: %s", target, e)
            return False
    if target.exists():
        return True
    try:
        target.symlink_to(p.resolve())
        logger.info("Linked LoRA %s into ComfyUI loras dir %s", p.name, loras_dir)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not link LoRA %s into ComfyUI: %s", p.name, e)
        return False


# Engine list is a live /object_info query (a large payload). Cache it briefly so
# a per-model listing does not fan out one probe per row, and so a down ComfyUI
# does not cost a timeout per row. Short enough that starting/stopping the plugin
# is picked up within a few seconds. Set the TTL to 0 to disable caching.
_ENGINE_CACHE_TTL_SECONDS = float(
    os.environ.get("GUAARDVARK_COMFYUI_ENGINE_CACHE_TTL", "5")
)
_ENGINE_CACHE: dict[str, tuple[float, list[str]]] = {}


def _engine_cache_get(comfy_url: str) -> list[str] | None:
    if _ENGINE_CACHE_TTL_SECONDS <= 0:
        return None
    cached = _ENGINE_CACHE.get(comfy_url)
    if cached is not None and (time.monotonic() - cached[0]) < _ENGINE_CACHE_TTL_SECONDS:
        return list(cached[1])
    return None


def _engine_cache_put(comfy_url: str, engines: list[str]) -> None:
    if _ENGINE_CACHE_TTL_SECONDS > 0:
        _ENGINE_CACHE[comfy_url] = (time.monotonic(), list(engines))


class ComfyUIImageGenerator:
    """Implements the storyboard ImageGenerator protocol with real LoRA support.

    generate_image(prompt, loras, output_path, width, height) -> output_path
    """

    # 0.25 is the sweet spot for these rank-16 SDXL character LoRAs — verified on
    # sage_harlow at a fixed seed: 0.25 is sharp + on-model, 0.4 starts to look
    # over-processed, and 0.6 "fries" the image into a blurry mush.
    def __init__(self, comfy_url: str | None = None, lora_strength: float = 0.25, model: str | None = None,
                 flux_unet: str | None = None, flux_t5: str | None = None,
                 flux_clip: str | None = None, flux_vae: str | None = None,
                 flux_dev_unet: str | None = None):
        self.comfy_url = (comfy_url or _COMFY_URL).rstrip("/")
        self.lora_strength = lora_strength
        self.model = model or "sdxl"  # "flux-schnell", "sdxl", "sdxl-lora" etc. (from MV keyframe_model)
        # Allow per-instance override (e.g. from MV settings for different quants)
        self.flux_unet = flux_unet or FLUX_UNET
        self.flux_t5 = flux_t5 or FLUX_T5
        self.flux_clip = flux_clip or FLUX_CLIP
        self.flux_vae = flux_vae or FLUX_VAE
        self.flux_dev_unet = flux_dev_unet or FLUX_DEV_UNET

    # ── connectivity ──────────────────────────────────────────────────
    def _available(self) -> bool:
        try:
            return requests.get(self.comfy_url, timeout=3).status_code == 200
        except requests.exceptions.RequestException:
            return False

    def comfyui_installed_engines(self) -> list[str]:
        """Which image engines can the reachable ComfyUI actually run?

        Queries the running server's ``/object_info`` — the authoritative source
        for where the live models are (an external Comfy Desktop install, not the
        bundled plugin dir). Returns engine tags like ``['zimage', 'flux-dev']``.

        The result is cached for a few seconds (see ``_ENGINE_CACHE_TTL_SECONDS``)
        so callers that must not block — and per-model listings — do not issue a
        probe each time. An unreachable server caches an empty list for the same
        TTL, so a down ComfyUI does not cost a timeout per model.
        """
        cached = _engine_cache_get(self.comfy_url)
        if cached is not None:
            return cached
        try:
            resp = requests.get(f"{self.comfy_url}/object_info", timeout=5)
            resp.raise_for_status()
            info = resp.json()
        except Exception as e:
            logger.warning("ComfyUI object_info probe failed: %s", e)
            _engine_cache_put(self.comfy_url, [])
            return []

        def _choices(node: str, key: str) -> list[str]:
            try:
                lst = info.get(node, {}).get("input", {}).get("required", {}).get(key, [])
                if isinstance(lst, list) and lst and isinstance(lst[0], list):
                    return [str(x) for x in lst[0]]
                if isinstance(lst, list) and lst and isinstance(lst[0], str):
                    return [str(x) for x in lst]
            except Exception:
                pass
            return []

        unet = set(_choices("UNETLoader", "unet_name"))
        unet_gguf = set(_choices("UnetLoaderGGUF", "unet_name"))
        all_unet = unet | unet_gguf
        clip = set(_choices("CLIPLoader", "clip_name"))
        dual = set(_choices("DualCLIPLoader", "clip_name1"))
        vae = set(_choices("VAELoader", "vae_name"))

        engines: list[str] = []
        if ZIMAGE_UNET in all_unet and ZIMAGE_CLIP in clip and ZIMAGE_VAE in vae:
            engines.append("zimage")
        if (FLUX_DEV_UNET in all_unet and FLUX_DEV_T5 in dual
                and FLUX_CLIP in dual and FLUX_VAE in vae):
            engines.append("flux-dev")
        if (FLUX_UNET in all_unet and FLUX_T5 in dual
                and FLUX_CLIP in dual and FLUX_VAE in vae):
            engines.append("flux-schnell")
        _engine_cache_put(self.comfy_url, engines)
        return engines

    # ── workflow ──────────────────────────────────────────────────────
    def _build_workflow(
        self, *, prompt: str, negative: str, lora_names: list[str],
        width: int, height: int, seed: int, steps: int, cfg: float,
        model: str | None = None,
        steps_explicit: bool = False,
    ) -> dict:
        effective_model = model or self.model
        ml = (effective_model or "").lower()

        # ── Capability guard (subject-16 + media model registry) ──────────────
        # Character LoRAs are tied to a base_model_id (sidecar schema v2). Never
        # force every LoRA onto SDXL — only SDXL-format LoRAs use the SDXL chain.
        # FLUX LoRAs use flux-dev; mismatched model tags are corrected to the
        # LoRA's family so identity is applied, not silently dropped.
        if lora_names:
            info = None
            try:
                from backend.services.media_model_registry import resolve_inference_for_loras
                # Caller may pass basenames only; resolve_inference needs paths when possible.
                # Prefer full paths from kwargs stored on self if present.
                lora_paths = getattr(self, "_last_lora_paths", None) or list(lora_names)
                info = resolve_inference_for_loras(
                    [p if ("/" in p or p.endswith(".safetensors")) else p for p in lora_paths]
                )
            except Exception as e:
                # Pre-registry LoRAs / basename-only: fall back to historic SDXL force
                # when flux-schnell would drop LoRAs entirely.
                logger.debug("LoRA base resolve failed (%s); using legacy flux→sdxl guard", e)
                if "flux" in ml and "dev" not in ml:
                    logger.warning(
                        "Keyframe model=%r WITH %d LoRA(s); assuming SDXL legacy LoRAs — "
                        "overriding to sdxl.",
                        effective_model, len(lora_names),
                    )
                    effective_model = "sdxl"
                    ml = "sdxl"
            # Family correction runs OUTSIDE the resolve try/except so the deliberate
            # Z-Image refusal below propagates instead of being swallowed by the
            # legacy-SDXL fallback above.
            if info is not None:
                tag = info.get("comfy_model_tag") or "sdxl"
                if info.get("family") == "sdxl" and ("flux" in ml or "zimage" in ml or "z-image" in ml):
                    logger.warning(
                        "LoRAs are SDXL (base=%s) but model=%r — overriding to sdxl so identity applies.",
                        info.get("base_model_id"), effective_model,
                    )
                    effective_model = "sdxl"
                    ml = "sdxl"
                elif info.get("family") == "flux" and "flux" not in ml:
                    logger.warning(
                        "LoRAs are FLUX (base=%s) but model=%r — overriding to %s.",
                        info.get("base_model_id"), effective_model, tag,
                    )
                    effective_model = tag
                    ml = tag
                elif info.get("family") == "zimage" and "zimage" not in ml and "z-image" not in ml:
                    from backend.services.stills_pipeline import zimage_via_comfyui_enabled
                    if not zimage_via_comfyui_enabled():
                        # Explicit refusal rather than a silent reroute: with the flag
                        # off the LoRA is never linked into ComfyUI
                        # (ensure_lora_in_comfyui returns early), so correcting to
                        # zimage could only fail later inside ComfyUI with a less
                        # clear error.
                        raise RuntimeError(
                            "Z-Image LoRA (base_model_id=%s) paired with model=%r needs "
                            "GUAARDVARK_ZIMAGE_USE_COMFYUI=1 to render on the ComfyUI "
                            "Z-Image graph; set the flag or use the offline Z-Image engine."
                            % (info.get("base_model_id"), effective_model)
                        )
                    # Deliberate: with the opt-in on, a Z-Image LoRA paired with
                    # another engine is corrected to the Z-Image graph instead of
                    # being refused, so a trained identity is applied rather than
                    # silently dropped.
                    logger.warning(
                        "LoRAs are Z-Image (base=%s) but model=%r — overriding to zimage so identity applies.",
                        info.get("base_model_id"), effective_model,
                    )
                    effective_model = "zimage"
                    ml = "zimage"

        if "flux" in ml and "dev" in ml:
            # FLUX-dev branch. As of the subject-16 fix this only fires for an
            # explicit flux-dev model with NO LoRAs (plain flux-dev stills) — the
            # capability guard above re-routes every LoRA request to the SDXL
            # branch because this app's character LoRAs are SDXL. The LoraLoaderModelOnly
            # chain below is retained for a FUTURE flux trainer; a flux-format LoRA
            # would need to bypass the guard (e.g. a model tag like "flux-dev-loras")
            # to reach it. Model-only chain: FLUX character LoRAs don't train the
            # text encoder (SimpleTuner "text encoder was not trained"), so clip is
            # left untouched and the trigger word in the prompt does the identity work.
            # Dev UNET/T5 come from the FLUX_DEV_* module constants (override via
            # GUAARDVARK_FLUX_DEV_UNET / _T5) — NOT the per-instance flux_unet/flux_t5,
            # which default to the schnell GGUF and would break this graph.
            model_src = ["unet", 0]
            wf: dict = {
                "unet": {
                    "class_type": "UNETLoader",
                    "inputs": {"unet_name": self.flux_dev_unet, "weight_dtype": FLUX_DEV_WEIGHT_DTYPE},
                },
                "clip": {
                    "class_type": "DualCLIPLoader",
                    "inputs": {"clip_name1": FLUX_DEV_T5, "clip_name2": self.flux_clip, "type": "flux"},
                },
                "vae_loader": {
                    "class_type": "VAELoader",
                    "inputs": {"vae_name": self.flux_vae},
                },
            }
            for i, name in enumerate(lora_names):
                nid = f"lora_{i}"
                wf[nid] = {
                    "class_type": "LoraLoaderModelOnly",
                    "inputs": {"model": model_src, "lora_name": name, "strength_model": self.lora_strength},
                }
                model_src = [nid, 0]
            # FluxGuidance owns "guidance" (user slider). KSampler cfg stays 1.0
            # (distilled). Soft floor on steps avoids accidental 1-step garbage;
            # upper bound is operator-owned (quality slider / batch params).
            flux_guidance = float(cfg) if cfg is not None and float(cfg) > 0 else FLUX_DEV_GUIDANCE
            flux_guidance = max(1.0, min(flux_guidance, 10.0))
            flux_steps = int(steps) if steps_explicit else max(4, min(int(steps) if steps else 28, 100))
            wf["pos"] = {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["clip", 0]}}
            wf["guid"] = {"class_type": "FluxGuidance", "inputs": {"conditioning": ["pos", 0], "guidance": flux_guidance}}
            # FLUX-dev is CFG-distilled (cfg=1.0) so the negative is inert; an empty
            # encode keeps the KSampler contract valid without fighting the LoRA.
            wf["neg"] = {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["clip", 0]}}
            wf["latent"] = {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}}
            wf["sampler"] = {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": flux_steps,
                    "cfg": 1.0,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "denoise": 1.0,
                    "model": model_src,
                    "positive": ["guid", 0],
                    "negative": ["neg", 0],
                    "latent_image": ["latent", 0],
                },
            }
            wf["vae"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["vae_loader", 0]}}
            wf["save"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": "storyboard-flux-dev", "images": ["vae", 0]}}
            return wf

        if "zimage" in ml or "z-image" in ml:
            # Z-Image Turbo via ComfyUI — UNETLoader + Lumina2 CLIP + ae VAE.
            # Flow-matching: ModelSamplingAuraFlow (shift) wraps the UNet, the
            # distilled model is CFG-free (cfg=1.0) with the negative zeroed via
            # ConditioningZeroOut, and latents use EmptySD3LatentImage.
            # Z-Image character LoRAs train only the transformer (not the text
            # encoder), so they are applied model-only via LoraLoaderModelOnly —
            # the same node the FLUX-dev branch uses. The chain feeds the UNet
            # into the AuraFlow sampler; the CLIP is untouched and the trigger
            # word in the prompt does the identity work.
            wf = {
                "unet": {
                    "class_type": "UNETLoader",
                    "inputs": {"unet_name": ZIMAGE_UNET, "weight_dtype": "default"},
                },
                "clip": {
                    "class_type": "CLIPLoader",
                    "inputs": {"clip_name": ZIMAGE_CLIP, "type": ZIMAGE_CLIP_TYPE},
                },
                "vae_loader": {
                    "class_type": "VAELoader",
                    "inputs": {"vae_name": ZIMAGE_VAE},
                },
                "pos": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": prompt, "clip": ["clip", 0]},
                },
                "neg": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": negative, "clip": ["clip", 0]},
                },
                "neg_zero": {
                    "class_type": "ConditioningZeroOut",
                    "inputs": {"conditioning": ["neg", 0]},
                },
                "latent": {
                    "class_type": "EmptySD3LatentImage",
                    "inputs": {"width": width, "height": height, "batch_size": 1},
                },
            }
            # Chain LoraLoaderModelOnly nodes (model-only): each wraps the previous
            # node's MODEL so multiple LoRAs stack; the CLIP is not touched.
            model_src = ["unet", 0]
            for i, name in enumerate(lora_names):
                nid = f"lora_{i}"
                wf[nid] = {
                    "class_type": "LoraLoaderModelOnly",
                    "inputs": {"model": model_src, "lora_name": name, "strength_model": self.lora_strength},
                }
                model_src = [nid, 0]
            wf["sampling"] = {
                "class_type": "ModelSamplingAuraFlow",
                "inputs": {"shift": ZIMAGE_SHIFT, "model": model_src},
            }
            wf["sampler"] = {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["sampling", 0],
                    "seed": seed,
                    "steps": steps,
                    "cfg": 1.0,
                    "sampler_name": ZIMAGE_SAMPLER,
                    "scheduler": ZIMAGE_SCHEDULER,
                    "positive": ["pos", 0],
                    "negative": ["neg_zero", 0],
                    "latent_image": ["latent", 0],
                    "denoise": 1.0,
                },
            }
            wf["vae"] = {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["sampler", 0], "vae": ["vae_loader", 0]},
            }
            wf["save"] = {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "guaardvark-zimage", "images": ["vae", 0]},
            }
            return wf

        if effective_model and "flux" in effective_model.lower():
            # Basic flux branch for storyboard keyframes (P1 wiring per approved plan).
            # Reuses patterns from the working infographic flux workflow.
            # Uses separate VAELoader (ae.safetensors) + correct clip_l (not _sdxl).
            # Hardcoded names must match files present in the running ComfyUI
            # (see GUAARDVARK_FLUX_* envs above). Mismatch => "Value not in list"
            # validation errors from UnetLoaderGGUF / DualCLIPLoader.
            # VAEDecode must not index non-existent output (was ["clip", 2] causing
            # "tuple index out of range").
            wf: dict = {
                "unet": {
                    "class_type": "UnetLoaderGGUF",
                    "inputs": {"unet_name": self.flux_unet},
                },
                "clip": {
                    "class_type": "DualCLIPLoader",
                    "inputs": {
                        "clip_name1": self.flux_t5,
                        "clip_name2": self.flux_clip,
                        "type": "flux",
                    },
                },
                "vae_loader": {
                    "class_type": "VAELoader",
                    "inputs": {"vae_name": self.flux_vae},
                },
                "pos": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": prompt, "clip": ["clip", 0]},
                },
                "neg": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": negative, "clip": ["clip", 0]},
                },
                "latent": {
                    "class_type": "EmptyLatentImage",
                    "inputs": {"width": width, "height": height, "batch_size": 1},
                },
                "sampler": {
                    "class_type": "KSampler",
                    "inputs": {
                        "seed": seed,
                        "steps": steps if steps_explicit else min(steps, 8),
                        "cfg": 1.0,
                        "sampler_name": "euler",
                        "scheduler": "simple",
                        "denoise": 1.0,
                        "model": ["unet", 0],
                        "positive": ["pos", 0],
                        "negative": ["neg", 0],
                        "latent_image": ["latent", 0],
                    },
                },
                "vae": {
                    "class_type": "VAEDecode",
                    "inputs": {"samples": ["sampler", 0], "vae": ["vae_loader", 0]},
                },
                "save": {
                    "class_type": "SaveImage",
                    "inputs": {"filename_prefix": "storyboard-flux", "images": ["vae", 0]},
                },
            }
            return wf

        # Default SDXL path (unchanged for compat; supports LoRA chaining).
        wf: dict = {
            "loader": {
                "class_type": "DiffusersLoader",
                "inputs": {"model_path": SDXL_DIFFUSERS_MODEL},
            },
        }

        # Chain LoraLoaders: each consumes the previous node's MODEL+CLIP.
        model_src = ["loader", 0]
        clip_src = ["loader", 1]
        for i, name in enumerate(lora_names):
            node_id = f"lora_{i}"
            wf[node_id] = {
                "class_type": "LoraLoader",
                "inputs": {
                    "lora_name": name,
                    "strength_model": self.lora_strength,
                    "strength_clip": self.lora_strength,
                    "model": model_src,
                    "clip": clip_src,
                },
            }
            model_src = [node_id, 0]
            clip_src = [node_id, 1]

        wf["pos"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": clip_src},
        }
        wf["neg"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": clip_src},
        }
        wf["latent"] = {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        }
        wf["ksampler"] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed, "steps": steps, "cfg": cfg,
                "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1.0,
                "model": model_src, "positive": ["pos", 0],
                "negative": ["neg", 0], "latent_image": ["latent", 0],
            },
        }
        wf["vae"] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["ksampler", 0], "vae": ["loader", 2]},
        }
        wf["save"] = {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "storyboard", "images": ["vae", 0]},
        }
        return wf

    # ── submission ────────────────────────────────────────────────────
    def _queue(self, workflow: dict) -> Optional[str]:
        resp = requests.post(f"{self.comfy_url}/prompt", json={"prompt": workflow}, timeout=15)
        resp.raise_for_status()
        return resp.json().get("prompt_id")

    def _wait(self, prompt_id: str, timeout: int = 300) -> Optional[dict]:
        start = time.time()
        while time.time() - start < timeout:
            try:
                resp = requests.get(f"{self.comfy_url}/history/{prompt_id}", timeout=5)
                resp.raise_for_status()
                hist = resp.json()
                if prompt_id in hist:
                    return hist[prompt_id].get("outputs", {})
            except requests.exceptions.RequestException as e:
                logger.warning("ComfyUI history poll error: %s", e)
            time.sleep(2)
        return None

    def _fetch_first_image(self, outputs: dict, output_path: str) -> Optional[str]:
        for node_output in outputs.values():
            for item in node_output.get("images", []):
                filename = item.get("filename")
                if not filename:
                    continue
                params = {"filename": filename, "type": item.get("type", "output")}
                if item.get("subfolder"):
                    params["subfolder"] = item["subfolder"]
                url = f"{self.comfy_url}/view?{urllib.parse.urlencode(params)}"
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                urllib.request.urlretrieve(url, output_path)
                return output_path
        return None

    # ── instruction editing (FLUX.1 Kontext) ──────────────────────────
    def _upload_image_to_comfyui(self, image_path: str) -> Optional[str]:
        """POST a local image to ComfyUI's /upload/image and return its server-side
        name (replicates comfyui_video_generator._upload_image_to_comfyui — that file
        is owned by the video path, so the small uploader is duplicated here)."""
        try:
            with open(image_path, "rb") as fh:
                files = {"image": (os.path.basename(image_path), fh, "image/png")}
                resp = requests.post(f"{self.comfy_url}/upload/image", files=files, timeout=30)
            resp.raise_for_status()
            return resp.json().get("name")
        except Exception as e:
            logger.error("Kontext: failed to upload edit image to ComfyUI: %s", e)
            return None

    def _kontext_installed(self) -> bool:
        """Honest install gate — True only when the Kontext GGUF is actually on disk."""
        if _is_model_installed:
            try:
                return _is_model_installed("flux-kontext-dev")
            except Exception:
                pass
        try:
            base = _comfy_models_dir() if _comfy_models_dir else (
                Path(__file__).resolve().parents[3] / "plugins" / "comfyui" / "ComfyUI" / "models"
            )
            f = base / "unet" / KONTEXT_UNET
            return f.exists() and f.stat().st_size > 0
        except Exception:
            return False

    def _registry_installed(self, model_id: str) -> bool:
        if _is_model_installed:
            try:
                return bool(_is_model_installed(model_id))
            except Exception:
                return False
        return False

    def qwen_edit_installed(self) -> bool:
        return self._registry_installed("qwen-image-edit")

    def pulid_installed(self) -> bool:
        return self._registry_installed("pulid-flux") and self._registry_installed("flux-dev")

    def _build_qwen_edit_workflow(
        self, *, src_names: list[str], instruction: str, steps: int,
        cfg: float, seed: int, pad: dict | None = None,
    ) -> dict:
        """Official Comfy Qwen-Image-Edit 2509 graph (FP8, AuraFlow shift 3, CFGNorm).

        Sampler floor is the Comfy Original table: 20 steps / CFG 2.5. The 4-step
        Lightning LoRA is not in this graph — it is a different quality contract.
        """
        n = max(int(steps), QWEN_EDIT_MIN_STEPS)
        cfg = float(cfg) if cfg else 2.5
        names = [s for s in (src_names or []) if s][:3]
        if not names:
            raise RuntimeError("Qwen-Image-Edit needs at least one source image")
        wf: dict = {
            "unet": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": QWEN_EDIT_UNET, "weight_dtype": "default"},
            },
            "clip": {
                "class_type": "CLIPLoader",
                "inputs": {"clip_name": QWEN_EDIT_CLIP, "type": "qwen_image"},
            },
            "vae_loader": {"class_type": "VAELoader", "inputs": {"vae_name": QWEN_EDIT_VAE}},
            "cfg_norm": {
                "class_type": "CFGNorm",
                "inputs": {"model": ["unet", 0], "strength": 1.0},
            },
            "shift": {
                "class_type": "ModelSamplingAuraFlow",
                "inputs": {"model": ["cfg_norm", 0], "shift": 3.0},
            },
        }
        image_src = None
        for i, name in enumerate(names):
            nid = f"load{i+1}"
            wf[nid] = {"class_type": "LoadImage", "inputs": {"image": name}}
            img_ref = [nid, 0]
            if i == 0:
                if pad and any(int(pad.get(k) or 0) for k in ("left", "top", "right", "bottom")):
                    wf["pad"] = {
                        "class_type": "ImagePadForOutpaint",
                        "inputs": {
                            "image": img_ref,
                            "left": int(pad.get("left") or 0),
                            "top": int(pad.get("top") or 0),
                            "right": int(pad.get("right") or 0),
                            "bottom": int(pad.get("bottom") or 0),
                            "feathering": int(pad.get("feathering") or 40),
                        },
                    }
                    img_ref = ["pad", 0]
                wf["scale"] = {"class_type": "FluxKontextImageScale", "inputs": {"image": img_ref}}
                image_src = ["scale", 0]
                wf["encode"] = {
                    "class_type": "VAEEncode",
                    "inputs": {"pixels": ["scale", 0], "vae": ["vae_loader", 0]},
                }
            # Plus node: image1 is the scaled/padded primary; 2 and 3 are extra refs.
            if i == 0:
                wf["_img1"] = image_src
            elif i == 1:
                wf["_img2"] = img_ref
            else:
                wf["_img3"] = img_ref
        pos_inputs = {
            "clip": ["clip", 0],
            "prompt": instruction,
            "vae": ["vae_loader", 0],
            "image1": wf.pop("_img1"),
        }
        img2 = wf.pop("_img2", None)
        img3 = wf.pop("_img3", None)
        if img2:
            pos_inputs["image2"] = img2
        if img3:
            pos_inputs["image3"] = img3
        wf["pos"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": pos_inputs}
        neg_inputs = dict(pos_inputs)
        neg_inputs["prompt"] = ""
        wf["neg"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": neg_inputs}
        wf["sampler"] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed, "steps": n, "cfg": cfg,
                "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                "model": ["shift", 0], "positive": ["pos", 0],
                "negative": ["neg", 0], "latent_image": ["encode", 0],
            },
        }
        wf["vae"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["vae_loader", 0]}}
        wf["save"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": "edit-qwen", "images": ["vae", 0]}}
        return wf

    def _build_pulid_workflow(
        self, *, src_image_name: str, prompt: str, width: int, height: int,
        steps: int, seed: int, weight: float | None = None, start_at: float | None = None,
        end_at: float | None = None, unet_dtype: str | None = None,
        node_variant: str | None = "pulid_flux",
    ) -> dict:
        """The PuLID-FLUX graph. Defaults are the product's; the keyword
        overrides are the likeness experiment's switches (see
        scripts/experiments/pulid_matrix.py)."""
        weight = _identity_default('weight', weight)
        start_at = _identity_default('start_at', start_at)
        end_at = _identity_default('end_at', end_at)
        n = max(int(steps), PULID_MIN_STEPS)
        variant = resolve_pulid_node_variant(node_variant)
        weight_dtype = resolve_pulid_unet_dtype(unet_dtype)
        start_at = float(start_at)
        end_at = float(end_at)
        if not (0.0 <= start_at <= 1.0 and 0.0 <= end_at <= 1.0 and start_at <= end_at):
            raise ValueError(f"start_at/end_at must satisfy 0 <= start_at <= end_at <= 1, got {start_at}/{end_at}")
        return {
            "unet": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": self.flux_dev_unet, "weight_dtype": weight_dtype},
            },
            "clip": {
                "class_type": "DualCLIPLoader",
                "inputs": {"clip_name1": FLUX_DEV_T5, "clip_name2": self.flux_clip, "type": "flux"},
            },
            "vae_loader": {"class_type": "VAELoader", "inputs": {"vae_name": self.flux_vae}},
            "pulid": {"class_type": "PulidFluxModelLoader", "inputs": {"pulid_file": PULID_FLUX_FILE}},
            "insight": {"class_type": "PulidFluxInsightFaceLoader", "inputs": {"provider": "CPU"}},
            "eva": {"class_type": "PulidFluxEvaClipLoader", "inputs": {}},
            "load": {"class_type": "LoadImage", "inputs": {"image": src_image_name}},
            "apply": {
                "class_type": variant["apply_class"],
                "inputs": {
                    "model": ["unet", 0],
                    "pulid_flux": ["pulid", 0],
                    "eva_clip": ["eva", 0],
                    "face_analysis": ["insight", 0],
                    "image": ["load", 0],
                    "weight": float(weight),
                    "start_at": start_at,
                    "end_at": end_at,
                    "unique_id": "pulid_apply",
                },
            },
            "pos": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["clip", 0]}},
            "guid": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["pos", 0], "guidance": FLUX_DEV_GUIDANCE}},
            "neg": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["clip", 0]}},
            "latent": {
                "class_type": "EmptySD3LatentImage",
                "inputs": {"width": int(width), "height": int(height), "batch_size": 1},
            },
            "sampler": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed, "steps": n, "cfg": 1.0,
                    "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                    "model": ["apply", 0], "positive": ["guid", 0],
                    "negative": ["neg", 0], "latent_image": ["latent", 0],
                },
            },
            "vae": {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["vae_loader", 0]}},
            "save": {"class_type": "SaveImage", "inputs": {"filename_prefix": "identity-pulid", "images": ["vae", 0]}},
        }

    def _run_edit_graph(self, workflow: dict, output_path: str, *, job: str, vram_mb: int, timeout: int) -> str:
        from backend.services.gpu_resource_policy import gpu_session
        from backend.services.job_types import JobKind
        import uuid as _uuid
        with gpu_session(
            JobKind.VIDEO_RENDER, f"{job}_{_uuid.uuid4().hex[:8]}",
            on_busy="raise", evict_ollama=True, free_comfyui=True,
            vram_estimate_mb=vram_mb, require_fit=True, cross_process=True,
        ):
            prompt_id = self._queue(workflow)
            if not prompt_id:
                raise RuntimeError("ComfyUI did not accept the image workflow")
            outputs = self._wait(prompt_id, timeout=timeout)
            if outputs is None:
                raise RuntimeError(f"ComfyUI image job timed out (prompt {prompt_id})")
            result = self._fetch_first_image(outputs, output_path)
            if result is None:
                raise RuntimeError(f"ComfyUI produced no image for prompt {prompt_id}")
        return result

    def edit_image_qwen(
        self, *, image_paths: list[str], instruction: str, output_path: str,
        steps: int = 20, cfg: float = 2.5, seed: int = 42, pad: dict | None = None,
    ) -> str:
        if not self._available():
            raise RuntimeError(f"ComfyUI not reachable at {self.comfy_url} — cannot edit image")
        if not self.qwen_edit_installed():
            from backend.services.image_editing_packs import missing_message
            raise RuntimeError(missing_message("edit_image"))
        names = []
        for p in image_paths:
            if not p or not os.path.exists(p):
                raise RuntimeError(f"Source image not found: {p}")
            name = self._upload_image_to_comfyui(p)
            if not name:
                raise RuntimeError("Failed to upload the source image to ComfyUI")
            names.append(name)
        workflow = self._build_qwen_edit_workflow(
            src_names=names, instruction=instruction,
            steps=steps, cfg=cfg, seed=seed, pad=pad,
        )
        result = self._run_edit_graph(
            workflow, output_path, job="chat_qwen_edit",
            vram_mb=_registry_vram("qwen-image-edit"), timeout=600,
        )
        logger.info("Qwen-Image-Edit complete: %s", result)
        return result

    def generate_with_identity(
        self, *, image_path: str, prompt: str, output_path: str,
        width: int = 768, height: int = 1024, steps: int = 20, seed: int = 42,
        weight: float | None = None, start_at: float | None = None, end_at: float | None = None,
        unet_dtype: str | None = None, node_variant: str | None = "pulid_flux",
    ) -> str:
        weight = _identity_default('weight', weight)
        start_at = _identity_default('start_at', start_at)
        end_at = _identity_default('end_at', end_at)
        if not self._available():
            raise RuntimeError(f"ComfyUI not reachable at {self.comfy_url}")
        if not os.path.exists(image_path):
            raise RuntimeError(f"Source image not found: {image_path}")
        if not self.pulid_installed():
            from backend.services.image_editing_packs import missing_message
            raise RuntimeError(missing_message("generate_identity"))
        src_name = self._upload_image_to_comfyui(image_path)
        if not src_name:
            raise RuntimeError("Failed to upload the face reference to ComfyUI")
        workflow = self._build_pulid_workflow(
            src_image_name=src_name, prompt=prompt,
            width=width, height=height, steps=steps, seed=seed, weight=weight,
            start_at=start_at, end_at=end_at, unet_dtype=unet_dtype,
            node_variant=node_variant,
        )
        result = self._run_edit_graph(
            workflow, output_path, job="chat_pulid",
            vram_mb=_registry_vram("pulid-flux"), timeout=600,
        )
        logger.info("PuLID-FLUX identity generate complete: %s", result)
        return result

    def _build_kontext_workflow(self, *, src_image_name: str, instruction: str,
                                steps: int, guidance: float, seed: int) -> dict:
        # Native ComfyUI Kontext graph (nodes verified present in this fork:
        # comfy_extras/nodes_flux.py + nodes_edit_model.py). We use CLIPTextEncode +
        # FluxGuidance + ReferenceLatent — NOT CLIPTextEncodeFlux, which bakes its own
        # guidance and would double-apply it alongside FluxGuidance.
        return {
            "unet": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": KONTEXT_UNET}},
            "clip": {"class_type": "DualCLIPLoader",
                     "inputs": {"clip_name1": self.flux_t5, "clip_name2": self.flux_clip, "type": "flux"}},
            "vae_loader": {"class_type": "VAELoader", "inputs": {"vae_name": self.flux_vae}},
            "load": {"class_type": "LoadImage", "inputs": {"image": src_image_name}},
            "scale": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["load", 0]}},
            "encode": {"class_type": "VAEEncode", "inputs": {"pixels": ["scale", 0], "vae": ["vae_loader", 0]}},
            "pos": {"class_type": "CLIPTextEncode", "inputs": {"text": instruction, "clip": ["clip", 0]}},
            "ref": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["pos", 0], "latent": ["encode", 0]}},
            "guid": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["ref", 0], "guidance": guidance}},
            "neg": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["pos", 0]}},
            "sampler": {"class_type": "KSampler",
                        "inputs": {"seed": seed, "steps": steps, "cfg": 1.0,
                                   "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                                   "model": ["unet", 0], "positive": ["guid", 0],
                                   "negative": ["neg", 0], "latent_image": ["encode", 0]}},
            "vae": {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["vae_loader", 0]}},
            "save": {"class_type": "SaveImage", "inputs": {"filename_prefix": "edit-kontext", "images": ["vae", 0]}},
        }

    def edit_image(self, *, image_path: str, instruction: str, output_path: str,
                   steps: int = 28, guidance: float = 2.5, seed: int = 42) -> str:
        """Instruction-guided edit of an existing image via FLUX.1 Kontext [dev].
        Honest failure if ComfyUI is down or the Kontext model isn't installed —
        never returns a fake/unedited image. Default 28 steps (Kontext is under-rendered
        at 20); guidance ~2.5 (do not exceed ~3.5 — over-bakes/identity-drift; cfg stays 1.0).

        Holds the GPU for the whole edit (exclusivity + evict Ollama + free ComfyUI UNDER
        the held lease) so the ~11GB Kontext load can't OOM against a resident chat model
        or a concurrent render — enforced HERE so no caller can bypass it."""
        if not self._available():
            raise RuntimeError(f"ComfyUI not reachable at {self.comfy_url} — cannot edit image")
        if not os.path.exists(image_path):
            raise RuntimeError(f"Source image not found: {image_path}")
        if not self._kontext_installed():
            raise RuntimeError(
                f"FLUX.1 Kontext [dev] model not installed (expected "
                f"ComfyUI/models/unet/{KONTEXT_UNET}). Image editing is unavailable "
                f"until that model finishes downloading."
            )
        from backend.services.gpu_resource_policy import gpu_session
        from backend.services.job_types import JobKind
        import uuid as _uuid
        with gpu_session(JobKind.VIDEO_RENDER, f"chat_edit_{_uuid.uuid4().hex[:8]}",
                         on_busy="raise", evict_ollama=True, free_comfyui=True,
                         vram_estimate_mb=11000, require_fit=True, cross_process=True):
            src_name = self._upload_image_to_comfyui(image_path)
            if not src_name:
                raise RuntimeError("Failed to upload the source image to ComfyUI")
            workflow = self._build_kontext_workflow(
                src_image_name=src_name, instruction=instruction,
                steps=max(int(steps), 1), guidance=guidance, seed=seed,
            )
            prompt_id = self._queue(workflow)
            if not prompt_id:
                raise RuntimeError("ComfyUI did not accept the Kontext edit workflow")
            outputs = self._wait(prompt_id)
            if outputs is None:
                raise RuntimeError(f"ComfyUI image edit timed out (prompt {prompt_id})")
            result = self._fetch_first_image(outputs, output_path)
            if result is None:
                raise RuntimeError(f"ComfyUI produced no edited image for prompt {prompt_id}")
        logger.info("Kontext edit complete: %s", result)
        return result

    # ── public API (ImageGenerator protocol) ──────────────────────────
    def _preflight_loras(self, lora_paths: list[str]) -> None:
        """Best-effort preflight for LoRA paths (media team audit P1-5 / P3-12).
        Checks common locations (data/training/loras + Comfy loras search).
        Logs warning + skips missing ones instead of hard-failing the batch
        (one bad cast LoRA shouldn't nuke an entire storyboard pass).
        Also clamps strength at call sites.
        """
        if not lora_paths:
            return
        search_dirs = []
        try:
            # data/training/loras is the canonical storage for user-trained ones.
            from backend.config import STORAGE_DIR
            search_dirs.append(Path(STORAGE_DIR) / "training" / "loras")
        except Exception:
            pass
        try:
            # Comfy registers extra_model_paths; probe a likely loras/ subdir next to ComfyUI.
            # This is read-only best-effort; the actual LoraLoader inside Comfy will
            # resolve by basename anyway.
            search_dirs.append(Path(__file__).resolve().parents[3] / "plugins" / "comfyui" / "ComfyUI" / "models" / "loras")
        except Exception:
            pass

        for p in lora_paths:
            if not p:
                continue
            # When Z-Image is routed through ComfyUI, make sure the trained LoRA is
            # linked into ComfyUI's models/loras so LoraLoaderModelOnly can find it.
            try:
                ensure_lora_in_comfyui(p)
            except Exception:
                pass
            pth = Path(p)
            found = pth.exists()
            if not found:
                for d in search_dirs:
                    if (d / pth.name).exists():
                        found = True
                        break
            if not found:
                logger.warning("LoRA preflight: %s not found in training/loras or Comfy loras search; proceeding without it (cast identity may be lost)", p)

    def generate_image(
        self, *, prompt: str, loras: list[str] | None = None,
        output_path: str, width: int = 1024, height: int = 1024,
        negative_prompt: str | None = None, seed: int = 42,
        steps: int = 30, cfg: float = 7.0,
        steps_explicit: bool = False,
        model: str | None = None,  # e.g. keyframe_model from MV settings ("flux-schnell", "sdxl"...)
    ) -> str:
        if not self._available():
            raise RuntimeError(
                f"ComfyUI not reachable at {self.comfy_url} — cannot generate storyboard image"
            )

        effective_model = model or self.model
        # ComfyUI resolves LoRAs by basename within its loras search paths;
        # data/training/loras is registered via extra_model_paths.yaml.
        lora_paths = [p for p in (loras or []) if p]
        lora_names = [os.path.basename(p) for p in lora_paths]
        # Full paths for media_model_registry sidecar lookup in _build_workflow.
        self._last_lora_paths = lora_paths

        # Run preflight (logs warnings for missing; does not raise).
        self._preflight_loras(lora_paths)

        # Align model tag with LoRA base when possible (SDXL vs FLUX).
        if lora_paths:
            try:
                from backend.services.media_model_registry import resolve_inference_for_loras
                info = resolve_inference_for_loras(lora_paths)
                if info.get("comfy_model_tag"):
                    effective_model = info["comfy_model_tag"]
                # Z-Image family is applied via the model-only LoRA chain in the
                # Z-Image workflow branch; no refusal needed.
            except RuntimeError:
                raise
            except Exception:
                pass

        workflow = self._build_workflow(
            prompt=prompt,
            negative=negative_prompt or DEFAULT_NEGATIVE,
            lora_names=lora_names,
            width=width, height=height, seed=seed, steps=steps, cfg=cfg,
            model=effective_model,
            steps_explicit=steps_explicit,
        )

        prompt_id = self._queue(workflow)
        if not prompt_id:
            raise RuntimeError("ComfyUI did not accept the image workflow")

        outputs = self._wait(prompt_id)
        if outputs is None:
            raise RuntimeError(f"ComfyUI image generation timed out (prompt {prompt_id})")

        result = self._fetch_first_image(outputs, output_path)
        if result is None:
            raise RuntimeError(f"ComfyUI produced no image for prompt {prompt_id}")

        self.last_steps = next(
            node["inputs"]["steps"] for node in workflow.values()
            if node.get("class_type") == "KSampler"
        )
        logger.info("Storyboard image generated (%d LoRAs): %s", len(lora_names), result)
        return result
