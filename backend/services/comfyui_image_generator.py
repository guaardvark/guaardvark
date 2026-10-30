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
basename because STORAGE_DIR/training/loras is on ComfyUI's LoRA search path
(plugins/comfyui/guaardvark_model_paths.yaml, passed at launch).
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

import requests

# Failure kinds named where each failure is detected (job_types).
from backend.services.job_types import RenderErrorKind as K, RenderFailure

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
    _KONTEXT = _VMR.get("flux-kontext-dev") or {}
    KONTEXT_UNET = _KONTEXT.get("hf_filename", "flux1-kontext-dev-Q6_K.gguf")
    KONTEXT_DEFAULT_STEPS = int(_KONTEXT.get("default_steps") or 28)
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
    # Qwen-Image 2.1 text-to-image: loader filenames, the official template's
    # sampling defaults and the ComfyUI node classes the graph needs.
    _QWEN21 = _VMR.get("qwen-image-21") or {}
    QWEN21_UNET = ((_QWEN21.get("files") or [{}])[0].get("dst")
                   or "qwen_image_2.1_int8_convrot.safetensors")
    QWEN21_CLIP = (((_VMR.get("qwen-image-21-clip") or {}).get("files") or [{}])[0].get("dst")
                   or "qwen3vl_8b_int8_convrot.safetensors")
    QWEN21_VAE = (((_VMR.get("qwen-image-21-vae") or {}).get("files") or [{}])[0].get("dst")
                  or "qwen_image_2.1_vae_bf16.safetensors")
    QWEN21_DEFAULT_STEPS = int(_QWEN21.get("default_steps") or 25)
    QWEN21_DEFAULT_CFG = float(_QWEN21.get("default_cfg") or 1.0)
    QWEN21_NODES = tuple(_QWEN21.get("comfy_nodes") or ("TextEncodeQwenImage21", "QwenImage21Cache"))
    # MOSS-Image text-to-image: loader filenames, sampling defaults and nodes.
    _MOSS = _VMR.get("moss-image") or {}
    MOSS_UNET = ((_MOSS.get("files") or [{}])[0].get("dst")
                 or "moss_image_sft_int8_convrot.safetensors")
    MOSS_CLIP = (((_VMR.get("moss-image-clip") or {}).get("files") or [{}])[0].get("dst")
                 or "qwen3.5_9b_w4a8_gptq.safetensors")
    MOSS_VAE = (((_VMR.get("moss-image-vae") or {}).get("files") or [{}])[0].get("dst")
                or "flux2-vae_small.safetensors")
    MOSS_DEFAULT_STEPS = int(_MOSS.get("default_steps") or 25)
    MOSS_DEFAULT_CFG = float(_MOSS.get("default_cfg") or 2.5)
    MOSS_NODES = tuple(_MOSS.get("comfy_nodes") or ("MOSSImageLayoutPrompt", "EmptyFlux2LatentImage"))
    # FLUX 3 Image Klein (distilled): loader filenames, sampling defaults, nodes.
    _FLUX3 = _VMR.get("flux3-klein") or {}
    FLUX3_UNET = ((_FLUX3.get("files") or [{}])[0].get("dst") or "flux3-image-klein-int8-convrot.safetensors")
    FLUX3_CLIP = (((_VMR.get("flux3-klein-clip") or {}).get("files") or [{}])[0].get("dst")
                  or "qwen3vl_4b_int8_convrot.safetensors")
    FLUX3_UPSAMPLER = (((_VMR.get("flux3-klein-upsampler") or {}).get("files") or [{}])[0].get("dst")
                       or "qwen3.5_9b_flux3_prompt_upsampler_w4a8.safetensors")
    FLUX3_VAE = (((_VMR.get("flux3-klein-vae") or {}).get("files") or [{}])[0].get("dst")
                 or "flux2-vae.safetensors")
    FLUX3_DEFAULT_STEPS = int(_FLUX3.get("default_steps") or 15)
    FLUX3_NODES = tuple(_FLUX3.get("comfy_nodes") or ("Flux3LayoutPrompt", "TextGenerate", "EmptyFlux2LatentImage"))
    _MOSS_EDIT = _VMR.get("moss-image-edit") or {}
    MOSS_EDIT_LORA = ((_MOSS_EDIT.get("files") or [{}])[0].get("dst")
                      or "moss_image_edit_lora_rank_64_bf16.safetensors")
    MOSS_EDIT_DEFAULT_STEPS = int(_MOSS_EDIT.get("default_steps") or 30)
    MOSS_EDIT_DEFAULT_CFG = float(_MOSS_EDIT.get("default_cfg") or 4.0)
except Exception:  # pragma: no cover - registry import is environment-specific
    KONTEXT_UNET = "flux1-kontext-dev-Q6_K.gguf"
    KONTEXT_DEFAULT_STEPS = 28
    QWEN_EDIT_UNET = "qwen_image_edit_2509_fp8_e4m3fn.safetensors"
    QWEN_EDIT_MIN_STEPS = 20
    QWEN_EDIT_CLIP = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
    QWEN_EDIT_VAE = "qwen_image_vae.safetensors"
    PULID_FLUX_FILE = "pulid_flux_v0.9.1.safetensors"
    PULID_MIN_STEPS = 20
    QWEN21_UNET = "qwen_image_2.1_int8_convrot.safetensors"
    QWEN21_CLIP = "qwen3vl_8b_int8_convrot.safetensors"
    QWEN21_VAE = "qwen_image_2.1_vae_bf16.safetensors"
    QWEN21_DEFAULT_STEPS = 25
    QWEN21_DEFAULT_CFG = 1.0
    QWEN21_NODES = ("TextEncodeQwenImage21", "QwenImage21Cache")
    MOSS_UNET = "moss_image_sft_int8_convrot.safetensors"
    MOSS_CLIP = "qwen3.5_9b_w4a8_gptq.safetensors"
    MOSS_VAE = "flux2-vae_small.safetensors"
    MOSS_DEFAULT_STEPS = 25
    MOSS_DEFAULT_CFG = 2.5
    MOSS_NODES = ("MOSSImageLayoutPrompt", "EmptyFlux2LatentImage")
    FLUX3_UNET = "flux3-image-klein-int8-convrot.safetensors"
    FLUX3_CLIP = "qwen3vl_4b_int8_convrot.safetensors"
    FLUX3_UPSAMPLER = "qwen3.5_9b_flux3_prompt_upsampler_w4a8.safetensors"
    FLUX3_VAE = "flux2-vae.safetensors"
    FLUX3_DEFAULT_STEPS = 15
    FLUX3_NODES = ("Flux3LayoutPrompt", "TextGenerate", "EmptyFlux2LatentImage")
    MOSS_EDIT_LORA = "moss_image_edit_lora_rank_64_bf16.safetensors"
    MOSS_EDIT_DEFAULT_STEPS = 30
    MOSS_EDIT_DEFAULT_CFG = 4.0
    _is_model_installed = None
    _comfy_models_dir = None

# The model tag the Qwen-Image 2.1 graph dispatches on (shipped id and the
# prefix of every user id of the family).
QWEN21_TAG = "qwen-image-21"
# TextEncodeQwenImage21's resolution input, as the official template sets it.
QWEN21_ENCODER_RESOLUTION = 1024
# The sides the model samples on; the template's usage note asks for multiples of 32.
QWEN21_SIDE_ALIGNMENT = 32

# The model tag the MOSS-Image graph dispatches on (shipped id and the prefix of
# every user id of the family).
MOSS_TAG = "moss-image"
# MOSS-Image samples a FLUX.2 latent; its loader asks for sides in multiples of 16.
MOSS_SIDE_ALIGNMENT = 16
# The official templates' negative (image_moss_image_t2i). A caller's own
# negative replaces it.
MOSS_NEGATIVE = "nsfw, worst quality, bad anatomy, watermark, jpeg artifact"
# The model tag the FLUX 3 Image Klein graph dispatches on. Checked before every
# FLUX.1 branch: "flux" is in the tag.
FLUX3_TAG = "flux3"
# The official distilled template's TextGenerate settings for the prompt
# upsampler (image_flux3_klein_distilled_t2i).
FLUX3_UPSAMPLE_MAX_TOKENS = 3584
# The ComfyUI release that first carries each family's graph, for the message a
# render on an older server gets.
_GRAPH_MIN_COMFY = {QWEN21_TAG: ("Qwen-Image 2.1", "0.37"), MOSS_TAG: ("MOSS-Image", "0.42"),
                    FLUX3_TAG: ("FLUX 3 Image Klein", "0.41")}


def _flux3_upsampler_instructions() -> str | None:
    """BFL's system prompt for the FLUX 3 prompt upsampler, read from the
    official ComfyUI template the comfyui-workflow-templates package installs
    beside ComfyUI. None when the package or the node is not there."""
    try:
        from importlib import resources
        tpl = resources.files("comfyui_workflow_templates_json") / "templates" / "image_flux3_klein_distilled_t2i.json"
        data = json.loads(tpl.read_text(encoding="utf-8"))
        for sg in (data.get("definitions") or {}).get("subgraphs") or []:
            for node in sg.get("nodes") or []:
                text = ((node.get("widgets_values") or [""])[0]) if node.get("type") == "PrimitiveStringMultiline" else ""
                if isinstance(text, str) and "prompt upsampler" in text:
                    return text
    except Exception as e:  # noqa: BLE001 — absent package or a changed template
        logger.warning("FLUX 3 upsampler instructions unavailable: %s", e)
    return None

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


# /object_info is a large payload that answers both "which engines are installed"
# and "which node classes exist". Cache it briefly so a per-model listing does
# not fan out one probe per row, and so a down ComfyUI does not cost a timeout
# per row. Short enough that starting/stopping the plugin is picked up within a
# few seconds. Set the TTL to 0 to disable caching.
_ENGINE_CACHE_TTL_SECONDS = float(
    os.environ.get("GUAARDVARK_COMFYUI_ENGINE_CACHE_TTL", "5")
)
_ENGINE_CACHE: dict[str, tuple[float, dict | None]] = {}
_CACHE_MISS = object()


def _engine_cache_get(comfy_url: str):
    """The cached object_info (a dict, or None for an unreachable server), or
    ``_CACHE_MISS`` when nothing fresh is cached."""
    if _ENGINE_CACHE_TTL_SECONDS <= 0:
        return _CACHE_MISS
    cached = _ENGINE_CACHE.get(comfy_url)
    if cached is not None and (time.monotonic() - cached[0]) < _ENGINE_CACHE_TTL_SECONDS:
        return cached[1]
    return _CACHE_MISS


def _engine_cache_put(comfy_url: str, info: dict | None) -> None:
    if _ENGINE_CACHE_TTL_SECONDS > 0:
        _ENGINE_CACHE[comfy_url] = (time.monotonic(), info)



def edit_steps(steps, *, default: int, floor: int = 0, label: str = "") -> tuple:
    """Sampling steps for an edit graph: ``(steps, notice)``.

    No count given renders the model's ``default``; a count that is given is
    used as given. Where the model's registry entry declares a ``floor``, a
    count below it is raised to the floor and ``notice`` says so.
    """
    try:
        asked = int(steps or 0)
    except (TypeError, ValueError):
        asked = 0
    if asked <= 0:
        return max(int(default), int(floor)), None
    if asked >= floor:
        return asked, None
    return int(floor), f"{label} needs at least {floor} steps; raised {asked} to {floor}."


def _input_choices(info: dict, node: str, key: str) -> list[str]:
    """The values ``/object_info`` lists for one loader input, e.g. the files
    DualCLIPLoader's clip_name1 can name."""
    try:
        lst = info.get(node, {}).get("input", {}).get("required", {}).get(key, [])
        if isinstance(lst, list) and lst and isinstance(lst[0], list):
            return [str(x) for x in lst[0]]
        if isinstance(lst, list) and lst and isinstance(lst[0], str):
            return [str(x) for x in lst]
    except Exception:
        pass
    return []


def _failure(kind, message: str):
    """A failure message that carries its RenderErrorKind (job_types)."""
    return RenderFailure(kind, message)


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
                 flux_dev_unet: str | None = None, qwen21_unet: str | None = None,
                 flux_dev_t5: str | None = None, text_encoders: dict | None = None,
                 moss_unet: str | None = None, flux3_unet: str | None = None):
        self.comfy_url = (comfy_url or _COMFY_URL).rstrip("/")
        self.lora_strength = lora_strength
        self.model = model or "sdxl"  # "flux-schnell", "sdxl", "sdxl-lora" etc. (from MV keyframe_model)
        # Allow per-instance override (e.g. from MV settings for different quants)
        self.flux_unet = flux_unet or FLUX_UNET
        self.flux_t5 = flux_t5 or FLUX_T5
        self.flux_clip = flux_clip or FLUX_CLIP
        self.flux_vae = flux_vae or FLUX_VAE
        self.flux_dev_unet = flux_dev_unet or FLUX_DEV_UNET
        self.flux_dev_t5 = flux_dev_t5 or FLUX_DEV_T5
        # A user-added Qwen-Image 2.1 UNET; the shipped registry file otherwise.
        self.qwen21_unet = qwen21_unet or QWEN21_UNET
        # A user-added MOSS-Image UNET; the shipped registry file otherwise.
        self.moss_unet = moss_unet or MOSS_UNET
        self.flux3_unet = flux3_unet or FLUX3_UNET
        # Text encoders picked for this render, {slot: loader filename}
        # (user_image_models.resolve_user_encoders; slots from the FLUX family
        # row). A T5-XXL fills the T5 slot of whichever FLUX graph renders, a
        # CLIP-L the CLIP-L slot of both.
        self.user_text_encoders = {k: v for k, v in (text_encoders or {}).items() if v}
        if self.user_text_encoders.get("t5"):
            self.flux_t5 = self.flux_dev_t5 = self.user_text_encoders["t5"]
        if self.user_text_encoders.get("clip_l"):
            self.flux_clip = self.user_text_encoders["clip_l"]

    # ── connectivity ──────────────────────────────────────────────────
    def _available(self) -> bool:
        try:
            return requests.get(self.comfy_url, timeout=3).status_code == 200
        except requests.exceptions.RequestException:
            return False

    def _object_info(self, *, fresh: bool = False) -> dict | None:
        """The running server's ``/object_info``: the authoritative source for the
        node classes it has and where its models are (an external Comfy Desktop
        install, not the bundled plugin dir). None when the server is unreachable.

        Cached for a few seconds (``_ENGINE_CACHE_TTL_SECONDS``) so callers that
        must not block, and per-model listings, do not issue a probe each time;
        an unreachable server is cached too, so a down ComfyUI does not cost a
        timeout per model. ``fresh`` skips the cache for one probe.
        """
        cached = _CACHE_MISS if fresh else _engine_cache_get(self.comfy_url)
        if cached is not _CACHE_MISS:
            return cached
        try:
            resp = requests.get(f"{self.comfy_url}/object_info", timeout=5)
            resp.raise_for_status()
            info = resp.json()
            if not isinstance(info, dict):
                info = {}
        except Exception as e:
            logger.warning("ComfyUI object_info probe failed: %s", e)
            info = None
        _engine_cache_put(self.comfy_url, info)
        return info

    def missing_node_classes(self, class_names) -> list[str]:
        """The node classes in ``class_names`` the running ComfyUI does not have.

        Empty when nothing is asked for (no probe is made), when every one is
        there, and when the server could not be asked even by a fresh probe (its
        own validation then reports what is missing). A cached empty answer, from
        a server that was still starting, is not trusted for this: one uncached
        probe is made first.
        """
        names = list(class_names or ())
        if not names:
            return []
        info = self._object_info()
        if not info:
            info = self._object_info(fresh=True)
        if not info:
            return []
        return [name for name in names if name not in info]

    def comfyui_installed_engines(self) -> list[str]:
        """Which image engines can the reachable ComfyUI actually run?

        Reads ``_object_info`` and returns engine tags like ``['zimage', 'flux-dev']``;
        an unreachable server reads as no engines.
        """
        info = self._object_info()
        if not info:
            return []

        def _choices(node: str, key: str) -> list[str]:
            return _input_choices(info, node, key)

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
        return engines

    def _require_up(self, down_message: str) -> None:
        """Raise ``down_message`` unless ComfyUI answers, followed by the
        sentence every tool gives for a stopped service
        (plugin_bridge.service_down_message), as a COMFYUI_DOWN failure.

        With GUAARDVARK_JOB_SERVICE_START on, ComfyUI is started first through
        the image stage; the reason a start did not help is added to the error.
        """
        if self._available():
            return
        from backend.services.plugin_bridge import (
            job_service_start_enabled, service_down_message, start_for_job,
        )
        why = ""
        if job_service_start_enabled():
            ok, why = start_for_job("image", "generating", is_up=self._available)
            if ok:
                return
        raise RuntimeError(_failure(
            K.COMFYUI_DOWN,
            f"{down_message}. {service_down_message('comfyui', context='image', why=why)}",
        ))

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

        if MOSS_TAG in ml:
            return self._build_moss_workflow(
                prompt=prompt, negative=negative, width=width, height=height,
                seed=seed, steps=steps, cfg=cfg, lora_names=lora_names,
            )

        if FLUX3_TAG in ml:
            if lora_names:
                raise RuntimeError(
                    "FLUX 3 Image Klein renders do not take LoRAs yet. Remove the LoRA "
                    "or pick a FLUX.1, SDXL, Z-Image or MOSS-Image model for it."
                )
            return self._build_flux3_workflow(
                prompt=prompt, width=width, height=height, seed=seed, steps=steps,
            )

        if QWEN21_TAG in ml:
            if lora_names:
                # The graph has no LoRA input; dropping them would silently lose
                # the identity or style the caller asked for.
                raise RuntimeError(
                    "Qwen-Image 2.1 renders do not take LoRAs yet. Remove the LoRA "
                    "or pick a FLUX, SDXL or Z-Image model for it."
                )
            return self._build_qwen21_workflow(
                prompt=prompt, negative=negative, width=width, height=height,
                seed=seed, steps=steps, cfg=cfg,
            )

        if "flux" in ml and "dev" in ml:
            # FLUX-dev branch: plain flux-dev stills, and LoRAs whose sidecar names
            # a FLUX base (the capability guard above retags a non-FLUX model
            # request to flux-dev for them; SDXL LoRAs go to the SDXL branch).
            # Model-only chain: FLUX character LoRAs don't train the
            # text encoder (SimpleTuner "text encoder was not trained"), so clip is
            # left untouched and the trigger word in the prompt does the identity work.
            # Dev UNET/T5 default to the FLUX_DEV_* module constants (override via
            # GUAARDVARK_FLUX_DEV_UNET / _T5), held per instance as flux_dev_unet /
            # flux_dev_t5 — NOT flux_unet/flux_t5, which default to the schnell
            # GGUF and its fp8 T5 and would break this graph.
            model_src = ["unet", 0]
            wf: dict = {
                "unet": {
                    "class_type": "UNETLoader",
                    "inputs": {"unet_name": self.flux_dev_unet, "weight_dtype": FLUX_DEV_WEIGHT_DTYPE},
                },
                "clip": {
                    "class_type": "DualCLIPLoader",
                    "inputs": {"clip_name1": self.flux_dev_t5, "clip_name2": self.flux_clip, "type": "flux"},
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

    def _wait(self, prompt_id: str, timeout: int = 300, should_stop=None) -> Optional[dict]:
        """Poll for the prompt's outputs; None on timeout. When ``should_stop``
        answers True the prompt is cancelled and RenderStopped is raised."""
        start = time.time()
        while time.time() - start < timeout:
            if should_stop is not None and should_stop():
                self._cancel(prompt_id)
                from backend.services.gpu_resource_policy import RenderStopped
                raise RenderStopped(f"while ComfyUI rendered prompt {prompt_id}")
            try:
                resp = requests.get(f"{self.comfy_url}/history/{prompt_id}", timeout=5)
                resp.raise_for_status()
                hist = resp.json()
                if prompt_id in hist:
                    # A prompt that ended in an error has empty outputs; say what
                    # failed (out of memory, a node, an interrupt) with its kind
                    # instead of a later "produced no image".
                    from backend.services.comfyui_video_generator import ComfyUIVideoGenerator
                    failed = ComfyUIVideoGenerator._history_failure(hist[prompt_id])
                    if failed is not None and not hist[prompt_id].get("outputs"):
                        raise RuntimeError(failed)
                    return hist[prompt_id].get("outputs", {})
            except requests.exceptions.RequestException as e:
                logger.warning("ComfyUI history poll error: %s", e)
            time.sleep(2)
        return None

    def _cancel(self, prompt_id: str) -> None:
        """Drop one prompt from ComfyUI, pending or running, and nothing else.

        ComfyUI is shared with the Studio and batch runs, so neither the bare
        ``/interrupt`` nor ``/queue {"clear": true}`` is used. The queue delete
        goes first: ``/interrupt`` with a prompt_id acts only on a running
        prompt, so in the other order a prompt that started between the two
        calls would render to the end.
        """
        for route, body in (("queue", {"delete": [prompt_id]}), ("interrupt", {"prompt_id": prompt_id})):
            try:
                requests.post(f"{self.comfy_url}/{route}", json=body, timeout=5)
            except requests.exceptions.RequestException as e:
                logger.warning("ComfyUI /%s for stopped prompt %s failed: %s", route, prompt_id, e)

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

    def moss_edit_installed(self) -> bool:
        return self._registry_installed("moss-image-edit")

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
        latent = ["encode", 0]
        if "pad" in wf:
            # Outpaint. The edit model copies what its reference image shows, so a
            # padded reference came back with its grey bars unfilled (and a full
            # regenerate could erase the subject). The reference is the original
            # picture; the padded canvas is sampled only where the pad mask is set.
            wf["scale_ref"] = {"class_type": "FluxKontextImageScale", "inputs": {"image": ["load1", 0]}}
            wf["pos"]["inputs"]["image1"] = ["scale_ref", 0]
            wf["neg"]["inputs"]["image1"] = ["scale_ref", 0]
            wf["outpaint_mask"] = {"class_type": "SetLatentNoiseMask",
                                   "inputs": {"samples": ["encode", 0], "mask": ["pad", 1]}}
            latent = ["outpaint_mask", 0]
        wf["sampler"] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed, "steps": n, "cfg": cfg,
                "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                "model": ["shift", 0], "positive": ["pos", 0],
                "negative": ["neg", 0], "latent_image": latent,
            },
        }
        wf["vae"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["vae_loader", 0]}}
        wf["save"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": "edit-qwen", "images": ["vae", 0]}}
        return wf

    def _build_qwen21_workflow(
        self, *, prompt: str, negative: str, width: int, height: int,
        seed: int, steps: int, cfg: float,
    ) -> dict:
        """The official Comfy-Org Qwen-Image 2.1 text-to-image graph
        (image_qwen_image_2_1_t2i): UNETLoader + CLIPLoader(qwen_image) +
        VAELoader, QwenImage21Cache on the model, TextEncodeQwenImage21 for both
        conditionings, EmptyLatentImage, KSampler euler/simple.

        Steps and cfg default to the template's (25, 1.0) when the caller gives
        none; a value that is given is used as given. No floor is declared: nothing
        has been measured on a card yet. Sides snap down to multiples of 32.
        """
        n = int(steps) if steps and int(steps) > 0 else QWEN21_DEFAULT_STEPS
        guidance = float(cfg) if cfg is not None and float(cfg) > 0 else QWEN21_DEFAULT_CFG
        grid = QWEN21_SIDE_ALIGNMENT
        w = max(grid, (int(width) // grid) * grid)
        h = max(grid, (int(height) // grid) * grid)
        # The generic SDXL negative is tag stuffing a prose encoder reads as
        # content; the template leaves the negative empty unless one is asked for.
        neg = "" if negative in (None, "", DEFAULT_NEGATIVE) else str(negative)
        return {
            "unet": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": self.qwen21_unet, "weight_dtype": "default"},
            },
            "clip": {
                "class_type": "CLIPLoader",
                "inputs": {"clip_name": QWEN21_CLIP, "type": "qwen_image"},
            },
            "vae_loader": {"class_type": "VAELoader", "inputs": {"vae_name": QWEN21_VAE}},
            "cache": {
                "class_type": "QwenImage21Cache",
                "inputs": {"model": ["unet", 0], "device": "auto", "dtype": "default"},
            },
            "encode": {
                "class_type": "TextEncodeQwenImage21",
                "inputs": {
                    "clip": ["clip", 0],
                    "prompt": prompt,
                    "negative_prompt": neg,
                    "resolution": QWEN21_ENCODER_RESOLUTION,
                },
            },
            "latent": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": w, "height": h, "batch_size": 1},
            },
            "sampler": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed, "steps": n, "cfg": guidance,
                    "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                    "model": ["cache", 0], "positive": ["encode", 0],
                    "negative": ["encode", 1], "latent_image": ["latent", 0],
                },
            },
            "vae": {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["vae_loader", 0]}},
            "save": {"class_type": "SaveImage", "inputs": {"filename_prefix": "guaardvark-qwen-image-21", "images": ["vae", 0]}},
        }

    def _build_moss_workflow(
        self, *, prompt: str, negative: str, width: int, height: int,
        seed: int, steps: int, cfg: float, lora_names: list[str] | None = None,
    ) -> dict:
        """The official Comfy-Org MOSS-Image SFT text-to-image graph
        (image_moss_image_t2i) with its prompt enhancer off: UNETLoader +
        CLIPLoader(moss_image) + VAELoader, CLIPTextEncode for both
        conditionings, EmptyFlux2LatentImage, KSampler er_sde/simple. The
        model's sampling shift (5.0) is ComfyUI's default for the architecture.

        Steps and cfg default to the template's (25, 2.5) when the caller gives
        none; a value that is given is used as given. Sides snap down to
        multiples of 16. LoRAs chain model-only (LoraLoaderModelOnly) between
        the loader and the sampler; MOSS-Image LoRAs train the transformer, not
        the Qwen3.5 encoder.
        """
        n = int(steps) if steps and int(steps) > 0 else MOSS_DEFAULT_STEPS
        guidance = float(cfg) if cfg is not None and float(cfg) > 0 else MOSS_DEFAULT_CFG
        grid = MOSS_SIDE_ALIGNMENT
        w = max(grid, (int(width) // grid) * grid)
        h = max(grid, (int(height) // grid) * grid)
        # The generic SDXL negative is tag stuffing written for another model;
        # the template's own negative stands in for it.
        neg = MOSS_NEGATIVE if negative in (None, "", DEFAULT_NEGATIVE) else str(negative)
        model_src = ["unet", 0]
        loras = {}
        for i, name in enumerate(lora_names or []):
            nid = f"lora_{i}"
            loras[nid] = {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {"model": model_src, "lora_name": name, "strength_model": self.lora_strength},
            }
            model_src = [nid, 0]
        return {
            "unet": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": self.moss_unet, "weight_dtype": "default"},
            },
            **loras,
            "clip": {
                "class_type": "CLIPLoader",
                "inputs": {"clip_name": MOSS_CLIP, "type": "moss_image"},
            },
            "vae_loader": {"class_type": "VAELoader", "inputs": {"vae_name": MOSS_VAE}},
            "positive": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": prompt}},
            "negative": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": neg}},
            "latent": {
                "class_type": "EmptyFlux2LatentImage",
                "inputs": {"width": w, "height": h, "batch_size": 1},
            },
            "sampler": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed, "steps": n, "cfg": guidance,
                    "sampler_name": "er_sde", "scheduler": "simple", "denoise": 1.0,
                    "model": model_src, "positive": ["positive", 0],
                    "negative": ["negative", 0], "latent_image": ["latent", 0],
                },
            },
            "vae": {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["vae_loader", 0]}},
            "save": {"class_type": "SaveImage", "inputs": {"filename_prefix": "guaardvark-moss-image", "images": ["vae", 0]}},
        }

    def _build_flux3_workflow(
        self, *, prompt: str, width: int, height: int, seed: int, steps: int,
    ) -> dict:
        """The official Comfy-Org FLUX 3 Image Klein distilled text-to-image graph
        (image_flux3_klein_distilled_t2i) with its prompt upsampler on, as the
        model card asks: BFL's Qwen3.5 upsampler expands the prompt through
        TextGenerate, the Qwen3-VL encoder (CLIPLoader type flux3) reads the
        result, EmptyFlux2LatentImage, KSampler er_sde/simple at cfg 1 (the
        checkpoint is distilled; a typed guidance has no use here). Steps default
        to the template's 15; sides snap to multiples of 16."""
        instructions = _flux3_upsampler_instructions()
        if not instructions:
            raise RuntimeError(
                "FLUX 3 Image Klein needs its prompt upsampler instructions, which come with "
                "ComfyUI's workflow templates. Update the ComfyUI plugin and try again."
            )
        n = int(steps) if steps and int(steps) > 0 else FLUX3_DEFAULT_STEPS
        w = max(16, (int(width) // 16) * 16)
        h = max(16, (int(height) // 16) * 16)
        return {
            "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": self.flux3_unet, "weight_dtype": "default"}},
            "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": FLUX3_CLIP, "type": "flux3"}},
            "upsampler_clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": FLUX3_UPSAMPLER, "type": "flux3"}},
            "vae_loader": {"class_type": "VAELoader", "inputs": {"vae_name": FLUX3_VAE}},
            "upsample": {"class_type": "TextGenerate", "inputs": {
                "clip": ["upsampler_clip", 0], "prompt": prompt, "system_prompt": instructions,
                "max_length": FLUX3_UPSAMPLE_MAX_TOKENS, "sampling_mode": "on",
                "sampling_mode.temperature": 1.0, "sampling_mode.top_k": 1, "sampling_mode.top_p": 0.95,
                "sampling_mode.min_p": 0.0, "sampling_mode.repetition_penalty": 1.05,
                "sampling_mode.seed": int(seed) % (2 ** 63), "sampling_mode.presence_penalty": 1.5,
                "thinking": False, "use_default_template": True, "mtp": "off"}},
            "positive": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": ["upsample", 0]}},
            "negative": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": ""}},
            "latent": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
            "sampler": {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": n, "cfg": 1.0, "sampler_name": "er_sde", "scheduler": "simple",
                "denoise": 1.0, "model": ["unet", 0], "positive": ["positive", 0],
                "negative": ["negative", 0], "latent_image": ["latent", 0]}},
            "vae": {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["vae_loader", 0]}},
            "save": {"class_type": "SaveImage", "inputs": {"filename_prefix": "guaardvark-flux3-klein", "images": ["vae", 0]}},
        }

    def _build_moss_edit_workflow(
        self, *, src_name: str, instruction: str, steps: int, cfg: float, seed: int,
    ) -> dict:
        """The official Comfy-Org MOSS-Image edit graph (image_moss_image_edit):
        the photo scaled to ~1 MP on a 16 px grid, MOSS-Image SFT with the edit
        LoRA at 1.0 and skip-layer guidance on block 22, TextEncodeMOSSImageEdit
        for both conditionings (the photo and the VAE as reference), a FLUX.2
        latent the photo's size, KSampler euler/normal."""
        edit_inputs = {"clip": ["clip", 0], "edit_format": "Moss-Image-Edit", "image_strength": 1.0,
                       "images.image_0": ["scaled", 0], "vae": ["vae_loader", 0]}
        return {
            "src": {"class_type": "LoadImage", "inputs": {"image": src_name}},
            "scaled": {"class_type": "ImageScaleToTotalPixels", "inputs": {
                "image": ["src", 0], "upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 16}},
            "size": {"class_type": "GetImageSize", "inputs": {"image": ["scaled", 0]}},
            "latent": {"class_type": "EmptyFlux2LatentImage", "inputs": {
                "width": ["size", 0], "height": ["size", 1], "batch_size": 1}},
            "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": self.moss_unet, "weight_dtype": "default"}},
            "edit_lora": {"class_type": "LoraLoaderModelOnly", "inputs": {
                "model": ["unet", 0], "lora_name": MOSS_EDIT_LORA, "strength_model": 1.0}},
            "slg": {"class_type": "SkipLayerGuidanceDiTSimple", "inputs": {
                "model": ["edit_lora", 0], "double_layers": "", "single_layers": "22",
                "start_percent": 0.0, "end_percent": 1.0}},
            "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": MOSS_CLIP, "type": "moss_image"}},
            "vae_loader": {"class_type": "VAELoader", "inputs": {"vae_name": MOSS_VAE}},
            "positive": {"class_type": "TextEncodeMOSSImageEdit", "inputs": {**edit_inputs, "prompt": instruction}},
            "negative": {"class_type": "TextEncodeMOSSImageEdit", "inputs": {**edit_inputs, "prompt": MOSS_NEGATIVE}},
            "sampler": {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": int(steps), "cfg": float(cfg), "sampler_name": "euler",
                "scheduler": "normal", "denoise": 1.0, "model": ["slg", 0],
                "positive": ["positive", 0], "negative": ["negative", 0], "latent_image": ["latent", 0]}},
            "vae": {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["vae_loader", 0]}},
            "save": {"class_type": "SaveImage", "inputs": {"filename_prefix": "guaardvark-moss-edit", "images": ["vae", 0]}},
        }

    def check_text_encoder_files(self, filenames) -> None:
        """Raise before queueing when the running ComfyUI does not list a text
        encoder picked for this render, instead of ComfyUI's "Value not in list"
        naming every file it has. Not probed when nothing was picked or the
        server cannot be asked; its own validation then speaks."""
        wanted = [f for f in (filenames or ()) if f]
        if not wanted:
            return
        info = self._object_info(fresh=True)
        if not info:
            return
        listed = set(_input_choices(info, "DualCLIPLoader", "clip_name1"))
        missing = [f for f in wanted if f not in listed]
        if missing:
            raise RuntimeError(_failure(
                K.COMPANION_MISSING,
                f"ComfyUI does not list the text encoder {', '.join(missing)} in models/clip or "
                "models/text_encoders. Install it in Manage Image Models, or restart ComfyUI "
                "if it was copied there by hand.",
            ))

    def required_node_classes(self, model: str | None) -> tuple:
        """Node classes a model's graph needs beyond ComfyUI's long-standing core."""
        ml = (model or "").lower()
        if MOSS_TAG in ml:
            return tuple(MOSS_NODES)
        if FLUX3_TAG in ml:
            return tuple(FLUX3_NODES)
        if QWEN21_TAG in ml:
            return tuple(QWEN21_NODES)
        return ()

    def check_graph_nodes(self, model: str | None) -> None:
        """Raise before queueing when the running ComfyUI lacks a node the
        model's graph uses; the message names the ComfyUI version that has it.
        A graph that declares no nodes is not probed for."""
        required = self.required_node_classes(model)
        if not required:
            return
        missing = self.missing_node_classes(required)
        if missing:
            ml = (model or "").lower()
            tag = MOSS_TAG if MOSS_TAG in ml else FLUX3_TAG if FLUX3_TAG in ml else QWEN21_TAG
            label, version = _GRAPH_MIN_COMFY[tag]
            raise RuntimeError(
                f"{label} needs ComfyUI {version} or newer: the running ComfyUI has no "
                f"{', '.join(missing)} node. Update the ComfyUI plugin and try again."
            )

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

    @staticmethod
    def _edit_gpu_session(op_id: str, gpu_wait: dict | None, **session_kwargs):
        """The GPU claim for an edit: refuse when busy, or wait when the caller asks.

        ``gpu_wait`` is ``{"wait_s", "on_wait", "should_stop"}``; chat passes it so
        a second edit queues behind the first instead of failing.
        """
        from backend.services.gpu_resource_policy import gpu_session, gpu_session_when_free
        from backend.services.job_types import JobKind
        if gpu_wait and gpu_wait.get("wait_s"):
            return gpu_session_when_free(
                JobKind.VIDEO_RENDER, op_id,
                wait_s=gpu_wait["wait_s"],
                on_wait=gpu_wait.get("on_wait"),
                should_stop=gpu_wait.get("should_stop"),
                **session_kwargs,
            )
        return gpu_session(JobKind.VIDEO_RENDER, op_id, on_busy="raise", **session_kwargs)

    def _run_edit_graph(self, workflow: dict, output_path: str, *, job: str, vram_mb: int, timeout: int,
                        gpu_wait: dict | None = None, should_stop=None) -> str:
        import uuid as _uuid
        from backend.services.gpu_resource_policy import raise_if_stopped
        with self._edit_gpu_session(
            f"{job}_{_uuid.uuid4().hex[:8]}", gpu_wait,
            evict_ollama=True, free_comfyui=True,
            vram_estimate_mb=vram_mb, require_fit=True, cross_process=True,
        ):
            raise_if_stopped(should_stop, "before ComfyUI was given the graph")
            prompt_id = self._queue(workflow)
            if not prompt_id:
                raise RuntimeError(_failure(K.NODE_ERROR, "ComfyUI did not accept the image workflow"))
            outputs = self._wait(prompt_id, timeout=timeout, should_stop=should_stop)
            if outputs is None:
                raise RuntimeError(_failure(K.RENDER_TIMEOUT, f"ComfyUI image job timed out (prompt {prompt_id})"))
            result = self._fetch_first_image(outputs, output_path)
            if result is None:
                raise RuntimeError(_failure(K.OUTPUT_MISSING, f"ComfyUI produced no image for prompt {prompt_id}"))
        return result

    def edit_image_qwen(
        self, *, image_paths: list[str], instruction: str, output_path: str,
        steps: int | None = None, cfg: float = 2.5, seed: int = 42, pad: dict | None = None,
        gpu_wait: dict | None = None,
    ) -> str:
        """Instruction edit on Qwen-Image-Edit. ``steps`` below the registry floor is
        raised to it; ``last_steps`` and ``last_steps_notice`` say what was rendered."""
        if not self.qwen_edit_installed():
            from backend.services.image_editing_packs import missing_message
            raise RuntimeError(_failure(K.MODEL_NOT_INSTALLED, missing_message("edit_image")))
        self._require_up(f"ComfyUI not reachable at {self.comfy_url} — cannot edit image")
        names = []
        for p in image_paths:
            if not p or not os.path.exists(p):
                raise RuntimeError(f"Source image not found: {p}")
            name = self._upload_image_to_comfyui(p)
            if not name:
                raise RuntimeError("Failed to upload the source image to ComfyUI")
            names.append(name)
        self.last_steps, self.last_steps_notice = edit_steps(
            steps, floor=QWEN_EDIT_MIN_STEPS, default=QWEN_EDIT_MIN_STEPS, label="Qwen-Image-Edit")
        workflow = self._build_qwen_edit_workflow(
            src_names=names, instruction=instruction,
            steps=self.last_steps, cfg=cfg, seed=seed, pad=pad,
        )
        result = self._run_edit_graph(
            workflow, output_path, job="chat_qwen_edit",
            vram_mb=_registry_vram("qwen-image-edit"), timeout=600, gpu_wait=gpu_wait,
        )
        logger.info("Qwen-Image-Edit complete: %s", result)
        return result

    def edit_image_moss(
        self, *, image_path: str, instruction: str, output_path: str,
        steps: int | None = None, cfg: float | None = None, seed: int = 42,
        gpu_wait: dict | None = None,
    ) -> str:
        """Instruction edit of one photo on MOSS-Image with its edit LoRA. No step
        count renders the template's 30; a given one is used as given."""
        if not self.moss_edit_installed():
            from backend.services.image_editing_packs import missing_message
            raise RuntimeError(_failure(K.MODEL_NOT_INSTALLED, missing_message("edit_image")))
        self._require_up(f"ComfyUI not reachable at {self.comfy_url} — cannot edit image")
        if not image_path or not os.path.exists(image_path):
            raise RuntimeError(f"Source image not found: {image_path}")
        missing = self.missing_node_classes(("TextEncodeMOSSImageEdit", "SkipLayerGuidanceDiTSimple"))
        if missing:
            raise RuntimeError(
                "MOSS-Image Edit needs ComfyUI 0.43 or newer: the running ComfyUI has no "
                f"{', '.join(missing)} node. Update the ComfyUI plugin and try again."
            )
        name = self._upload_image_to_comfyui(image_path)
        if not name:
            raise RuntimeError("Failed to upload the source image to ComfyUI")
        self.last_steps, self.last_steps_notice = edit_steps(
            steps, floor=0, default=MOSS_EDIT_DEFAULT_STEPS, label="MOSS-Image Edit")
        workflow = self._build_moss_edit_workflow(
            src_name=name, instruction=instruction, steps=self.last_steps,
            cfg=float(cfg) if cfg else MOSS_EDIT_DEFAULT_CFG, seed=seed,
        )
        result = self._run_edit_graph(
            workflow, output_path, job="chat_moss_edit",
            vram_mb=_registry_vram("moss-image-edit"), timeout=600, gpu_wait=gpu_wait,
        )
        logger.info("MOSS-Image Edit complete: %s", result)
        return result

    def generate_with_identity(
        self, *, image_path: str, prompt: str, output_path: str,
        width: int = 768, height: int = 1024, steps: int = 20, seed: int = 42,
        weight: float | None = None, start_at: float | None = None, end_at: float | None = None,
        unet_dtype: str | None = None, node_variant: str | None = "pulid_flux",
        gpu_wait: dict | None = None,
    ) -> str:
        weight = _identity_default('weight', weight)
        start_at = _identity_default('start_at', start_at)
        end_at = _identity_default('end_at', end_at)
        if not os.path.exists(image_path):
            raise RuntimeError(f"Source image not found: {image_path}")
        if not self.pulid_installed():
            from backend.services.image_editing_packs import missing_message
            raise RuntimeError(_failure(K.MODEL_NOT_INSTALLED, missing_message("generate_identity")))
        self._require_up(f"ComfyUI not reachable at {self.comfy_url}")
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
            vram_mb=_registry_vram("pulid-flux"), timeout=600, gpu_wait=gpu_wait,
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
                   steps: int | None = None, guidance: float = 2.5, seed: int = 42,
                   gpu_wait: dict | None = None) -> str:
        """Instruction-guided edit of an existing image via FLUX.1 Kontext [dev].
        Honest failure if ComfyUI is down or the Kontext model isn't installed —
        never returns a fake/unedited image. No step count renders the registry entry's
        default_steps (Kontext is under-rendered at 20); a count that is given is used as
        given. ``last_steps`` says what was rendered.
        Guidance ~2.5 (do not exceed ~3.5 — over-bakes/identity-drift; cfg stays 1.0).

        Holds the GPU for the whole edit (exclusivity + evict Ollama + free ComfyUI UNDER
        the held lease) so the ~11GB Kontext load can't OOM against a resident chat model
        or a concurrent render — enforced HERE so no caller can bypass it."""
        if not os.path.exists(image_path):
            raise RuntimeError(f"Source image not found: {image_path}")
        if not self._kontext_installed():
            raise RuntimeError(
                f"FLUX.1 Kontext [dev] model not installed (expected "
                f"ComfyUI/models/unet/{KONTEXT_UNET}). Image editing is unavailable "
                f"until that model finishes downloading."
            )
        self._require_up(f"ComfyUI not reachable at {self.comfy_url} — cannot edit image")
        self.last_steps, self.last_steps_notice = edit_steps(steps, default=KONTEXT_DEFAULT_STEPS)
        import uuid as _uuid
        with self._edit_gpu_session(f"chat_edit_{_uuid.uuid4().hex[:8]}", gpu_wait,
                                    evict_ollama=True, free_comfyui=True,
                                    vram_estimate_mb=11000, require_fit=True, cross_process=True):
            src_name = self._upload_image_to_comfyui(image_path)
            if not src_name:
                raise RuntimeError("Failed to upload the source image to ComfyUI")
            workflow = self._build_kontext_workflow(
                src_image_name=src_name, instruction=instruction,
                steps=self.last_steps, guidance=guidance, seed=seed,
            )
            prompt_id = self._queue(workflow)
            if not prompt_id:
                raise RuntimeError(_failure(K.NODE_ERROR, "ComfyUI did not accept the Kontext edit workflow"))
            outputs = self._wait(prompt_id)
            if outputs is None:
                raise RuntimeError(_failure(K.RENDER_TIMEOUT, f"ComfyUI image edit timed out (prompt {prompt_id})"))
            result = self._fetch_first_image(outputs, output_path)
            if result is None:
                raise RuntimeError(_failure(K.OUTPUT_MISSING, f"ComfyUI produced no edited image for prompt {prompt_id}"))
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
            # ComfyUI's own loras/ folder, the other place a LoRA name can resolve.
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
        admit_vram_mb: int | None = None,
        should_stop=None,
    ) -> str:
        """Render one still on the ComfyUI graph ``model`` names.

        ``admit_vram_mb`` claims the GPU for the render the way a chat edit does
        (refuse when busy, require the estimate to fit); callers that already
        hold a session (the batch runner) leave it None.

        ``should_stop`` is polled while the prompt waits and renders; True
        cancels this prompt only and raises RenderStopped.
        """
        self._require_up(
            f"ComfyUI not reachable at {self.comfy_url} — cannot generate the image"
        )

        effective_model = model or self.model
        # ComfyUI resolves LoRAs by basename within its loras search paths;
        # STORAGE_DIR/training/loras is added by plugins/comfyui/guaardvark_model_paths.yaml.
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
        self.check_graph_nodes(effective_model)
        self.check_text_encoder_files(self.user_text_encoders.values())

        if admit_vram_mb:
            result = self._run_edit_graph(
                workflow, output_path, job="stills_comfy", vram_mb=int(admit_vram_mb), timeout=300,
                should_stop=should_stop,
            )
        else:
            from backend.services.gpu_resource_policy import raise_if_stopped
            raise_if_stopped(should_stop, "before ComfyUI was given the graph")
            prompt_id = self._queue(workflow)
            if not prompt_id:
                raise RuntimeError(_failure(K.NODE_ERROR, "ComfyUI did not accept the image workflow"))

            outputs = self._wait(prompt_id, should_stop=should_stop)
            if outputs is None:
                raise RuntimeError(_failure(K.RENDER_TIMEOUT, f"ComfyUI image generation timed out (prompt {prompt_id})"))

            result = self._fetch_first_image(outputs, output_path)
            if result is None:
                raise RuntimeError(_failure(K.OUTPUT_MISSING, f"ComfyUI produced no image for prompt {prompt_id}"))

        self.last_steps = next(
            node["inputs"]["steps"] for node in workflow.values()
            if node.get("class_type") == "KSampler"
        )
        logger.info("Storyboard image generated (%d LoRAs): %s", len(lora_names), result)
        return result
