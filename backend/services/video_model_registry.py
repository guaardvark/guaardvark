"""
Video model registry — SINGLE SOURCE OF TRUTH for video-model file layout.

Issue #36 root cause #2: the model filenames lived in THREE independently
hand-edited maps that had to agree byte-for-byte:
  1. the download destination (`files[].dst`),
  2. the install/"is it ready?" check (`check_files`), and
  3. the ComfyUI generation loader (`WAN22_MODELS` in comfyui_video_generator.py).

When any one drifted (e.g. a HuggingFace repo reshuffle), the download wrote a
file the generator never loaded → a silent blank render or a model that shows
"not installed" forever. This module collapses all three into one map:

  - `VIDEO_MODEL_REGISTRY` is the only place filenames are written.
  - `check_files` for entries that use `files` is DERIVED from `files[].dst`
    (you edit `files`, never check_files) — see `_normalize_registry()`.
  - The ComfyUI loader map is DERIVED via `wan_comfyui_map()` — the generator
    no longer keeps its own copy.

Both the batch-video API (download/install) and comfyui_video_generator
(generation) import from here, so the two can no longer disagree.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def comfyui_models_dir() -> Path:
    """Root of ComfyUI's models/ tree (where downloads land)."""
    try:
        from backend.config import COMFYUI_DIR
    except ImportError:
        COMFYUI_DIR = os.path.join(
            os.environ.get("GUAARDVARK_ROOT", "."), "plugins", "comfyui", "ComfyUI"
        )
    return Path(COMFYUI_DIR) / "models"


def is_model_installed(model_id: str) -> bool:
    """True when every check_file for model_id exists and is non-empty."""
    entry = VIDEO_MODEL_REGISTRY.get(model_id)
    if not entry:
        return False
    base = comfyui_models_dir() / entry.get("local_subdir", "")
    for check_file in entry.get("check_files", []):
        fpath = base / check_file
        if not fpath.exists() or fpath.stat().st_size == 0:
            return False
    return True


# ── MiniMax H3 shared capability data ────────────────────────────────────
# Every H3 generation entry carries the same contract; the variants differ
# only in precision, size and the VRAM tier they are meant for. Declared once
# here and spliced into each entry so a limit is never re-typed per variant.
#
# Frame grid: the model samples 17k+5 frames at 24 fps (124 = ~5 s). The
# trained range is 124-362 frames (~5-15 s); the node accepts less, and the
# shipped "short" preset (73 frames, ~3 s) rendered for an external tester,
# so the floor stays at 3 s until a measured run says otherwise.
H3_FRAME_RULE = "17k+5"
H3_NATIVE_FPS = 24
H3_ASPECT_RATIOS = ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]
H3_LICENSE = {
    "name": "MiniMax H3 Community License",
    "url": "https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE",
    "form_url": "https://platform.minimax.io/h3-license",
    "attribution": "MiniMax H3",
    # Static text only. The product never checks entitlement online; the
    # person installing reads this and decides, as with every gated model.
    "note": (
        "The license names the EU, UK, South Korea and USA as Excluded "
        "Territories; MiniMax offers an application form for those. Commercial "
        "products must display 'MiniMax H3' in their UI; revenue above 20M USD "
        "needs written authorization; outputs may not be used to train other "
        "models."
    ),
}
# Ten style embeddings shipped beside the weights (models/embeddings). A
# preset appends the token to the prompt after enhancement so the enhancer
# cannot rewrite it. Ids are the filename stems minus the "minimaxh3_" prefix.
H3_STYLE_EMBEDDING_IDS = [
    "art_is_explosion", "blooming_flowers", "bullet_time", "dark_magic",
    "fire_breath", "four_seasons", "kiss_camera", "spiral_ascent",
    "storm_magic", "truman_show",
]
H3_STYLE_EMBEDDINGS = [
    {
        "id": sid,
        "label": sid.replace("_", " ").capitalize(),
        "token": f"embedding:minimaxh3_{sid}",
        "file": f"minimaxh3_{sid}.safetensors",
    }
    for sid in H3_STYLE_EMBEDDING_IDS
]
# Speed profiles: the official template samples 20 steps without CFG. The
# turbo LoRAs are Comfy-Org's distilled variants; their step counts come from
# docs.comfy.org ("slightly lower audio and motion quality"), not from a
# measurement here. min_steps is the no-bad-knob floor a preset may not go
# below; an explicit value a person typed still wins and is logged.
# The 4-step fl2v LoRA is tuned for the 768 px canvas, so it is gated to a
# 768 short edge until a run at 480p is compared.
H3_FL2VA_SPEED_PROFILES = {
    "standard": {"label": "Standard (20 steps)", "steps": 20, "min_steps": 20},
    "turbo-8": {
        "label": "Turbo (8 steps)",
        "lora": "minimax-h3-fl2v-turbo-8step",
        "strength": 1.0,
        "steps": 8,
        "min_steps": 8,
    },
    "turbo-4-768p": {
        "label": "Turbo 768p (4 steps)",
        "lora": "minimax-h3-fl2v-turbo-4step-768p",
        "strength": 1.0,
        "steps": 4,
        "min_steps": 4,
        "min_short_edge": 768,
    },
}
H3_REF2VA_SPEED_PROFILES = {
    "standard": {"label": "Standard (20 steps)", "steps": 20, "min_steps": 20},
    "turbo-4": {
        "label": "Turbo (4 steps, experimental)",
        "lora": "minimax-h3-ref2v-turbo-4step",
        "strength": 1.0,
        "steps": 4,
        "min_steps": 4,
        "experimental": True,
    },
}
# Duration tiers: only the 175-frame tier ships until the 10 s / 15 s runs
# record a pixel-area cap for each (Phase 0 of the H3 plan). The UI offers a
# duration only when its tier exists here.
H3_DURATION_TIERS = [{"frames": 175, "seconds": 7.3, "max_pixel_area": 768 * 1344}]
# Keys shared by every H3 generation entry. `tier_defaults` (per VRAM class)
# and `speed_profiles` are per variant.
_H3_COMMON = {
    "type": "minimax",
    "dimension_alignment": 32,
    # Template note: native canvas is a 768px short edge, capped at 768x1344.
    "max_pixel_area": 768 * 1344,
    "aspect_ratios": H3_ASPECT_RATIOS,
    "audio_out": True,
    "audio_in": True,
    "cfg": False,
    "native_fps": H3_NATIVE_FPS,
    "frame_rule": H3_FRAME_RULE,
    "max_frames": 175,
    "min_clip_s": 3.0,
    "max_clip_s": 15.0,
    "duration_tiers": H3_DURATION_TIERS,
    "min_steps": 20,
    "default_steps": 20,
    "style_embeddings": H3_STYLE_EMBEDDINGS,
    "license": H3_LICENSE,
}
_H3_FL2VA_MODES = ["t2v", "i2v", "l2v", "flf2v"]
_H3_REF_LIMITS = {"images": 9, "videos": 3, "audios": 3, "files": 12, "video_seconds": [2, 15]}


VIDEO_MODEL_REGISTRY = {
    "cogvideox-5b": {
        "name": "CogVideoX 5B",
        "description": "Text-to-video, 6s clips. Best quality, needs ~16GB VRAM.",
        "hf_repo": "THUDM/CogVideoX-5b",
        "local_subdir": "CogVideo/CogVideoX-5b",
        # Snapshot download → explicit check paths (subpaths inside the snapshot).
        "check_files": ["transformer/diffusion_pytorch_model-00001-of-00002.safetensors", "vae/diffusion_pytorch_model.safetensors"],
        "size_gb": 11.3,
        "vram_mb": 16000,
        "type": "cogvideox",
        "dimension_alignment": 16,
    },
    "cogvideox-5b-i2v": {
        "name": "CogVideoX 1.5 5B I2V (BF16)",
        "description": "Image-to-video, 6s clips. Full precision, best quality. Needs ~16GB VRAM. "
                       "Pulls the CogVideoX VAE + T5 encoder.",
        "hf_repo": "Kijai/CogVideoX-comfy",
        # The wrapper's single-file loader (CogVideoXModelLoader) enumerates
        # models/diffusion_models, but this file has lived in checkpoints/ since
        # the first install, so it stays canonical there and is hard-linked into
        # diffusion_models/ (also_link at download; the generator reconciles
        # existing installs). Before 2026-08-28 the workflow ignored this file
        # entirely and asked DownloadAndLoadCogVideoModel for a hub id, which
        # fetched a second 11GB diffusers snapshot from Hugging Face during
        # generation — a download the person never clicked.
        "local_subdir": "checkpoints",
        "files": [
            {
                "src": "CogVideoX_1_5_5b_I2V_bf16.safetensors",
                "dst": "CogVideoX_1_5_5b_I2V_bf16.safetensors",
                "also_link": "diffusion_models",
            },
        ],
        # ComfyUI's CogVideoX workflow loads the T5 encoder via CLIPLoader; the
        # single-file loader needs the VAE as its own file (the diffusers
        # snapshot used to carry it).
        "requires": ["t5-encoder", "cogvideox-vae"],
        "size_gb": 10.4,
        "vram_mb": 16000,
        "type": "cogvideox",
        "dimension_alignment": 16,
    },
    "cogvideox-vae": {
        "name": "CogVideoX VAE (BF16)",
        "description": "Required by CogVideoX I2V — CogVideoXVAELoader reads it from vae/.",
        "hf_repo": "Kijai/CogVideoX-comfy",
        "local_subdir": "vae",
        "files": [
            {"src": "cogvideox_vae_bf16.safetensors", "dst": "cogvideox_vae_bf16.safetensors"},
        ],
        "size_gb": 0.43,
        "vram_mb": 0,
        "type": "vae",
    },
    # Wan GGUFs live in HighNoise/ and LowNoise/ subfolders in the repo, but
    # ComfyUI's UnetLoaderGGUF loads them flat from models/unet/. The `files`
    # spec below maps each repo path (`src`) to the exact on-disk name ComfyUI
    # expects (`dst`), so we pull ONLY the two Q5_K_M experts — not all 13
    # quants — and they land where both the loader and the install-check look.
    # check_files is DERIVED from files[].dst (do not add it by hand).
    "wan22-14b": {
        "name": "Wan 2.2 14B MoE (GGUF Q5_K)",
        "description": "State-of-the-art video gen. Two-expert MoE architecture, best quality on 16GB GPU. Requires both HighNoise + LowNoise experts.",
        "hf_repo": "QuantStack/Wan2.2-T2V-A14B-GGUF",
        "local_subdir": "unet",
        "files": [
            {"src": "HighNoise/Wan2.2-T2V-A14B-HighNoise-Q5_K_M.gguf", "dst": "Wan2.2-T2V-A14B-HighNoise-Q5_K_M.gguf"},
            {"src": "LowNoise/Wan2.2-T2V-A14B-LowNoise-Q5_K_M.gguf", "dst": "Wan2.2-T2V-A14B-LowNoise-Q5_K_M.gguf"},
        ],
        # A WAN unet is useless without its VAE + text encoder — installing this
        # model pulls them too, so one click yields a render-ready setup.
        "requires": ["wan-vae", "wan-umt5"],
        "size_gb": 21.0,
        "vram_mb": 11000,
        "type": "wan",
        "dimension_alignment": 32,
        "max_pixel_area": 1_000_000,
        # Landscape, its transpose, and square. The earlier claim that off-native
        # frames "come back warped" does not survive the evidence: the output
        # directory holds seven 1:1 Wan I2V renders (512x512 and 736x736,
        # 2026-08-14), one of them the project's own demo clip.
        #
        # What actually warped was the sampler shift. It was scaled by pixel area,
        # so every non-native size sampled at 3.0-4.8 against the 8.0 these models
        # are trained at, and the result was the colour bleed reported as "rainbow
        # morphs". Forbidding the ratio treated the symptom; the shift is fixed at
        # its source instead. The area clamp below still applies.
        "aspect_ratios": ["16:9", "9:16", "1:1"],
    },
    "wan22-14b-i2v": {
        "name": "Wan 2.2 14B I2V MoE (GGUF Q5_K)",
        "description": "Top-tier image-to-video. Same MoE architecture as Wan 2.2 T2V — start frame conditions an 81-frame clip. Beats CogVideoX I2V on motion + cinematic feel.",
        "hf_repo": "QuantStack/Wan2.2-I2V-A14B-GGUF",
        "local_subdir": "unet",
        # I2V experts are loaded from a nested unet/Wan2.2-I2V/<HighNoise|LowNoise>/
        # path, so dst keeps that nesting (the ComfyUI loader map derives from it).
        "files": [
            {"src": "HighNoise/Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf", "dst": "Wan2.2-I2V/HighNoise/Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf"},
            {"src": "LowNoise/Wan2.2-I2V-A14B-LowNoise-Q5_K_M.gguf", "dst": "Wan2.2-I2V/LowNoise/Wan2.2-I2V-A14B-LowNoise-Q5_K_M.gguf"},
        ],
        "requires": ["wan-vae", "wan-umt5"],
        "size_gb": 21.0,
        "vram_mb": 11000,
        "type": "wan",
        "dimension_alignment": 32,
        "max_pixel_area": 1_000_000,
        # Landscape, its transpose, and square. The earlier claim that off-native
        # frames "come back warped" does not survive the evidence: the output
        # directory holds seven 1:1 Wan I2V renders (512x512 and 736x736,
        # 2026-08-14), one of them the project's own demo clip.
        #
        # What actually warped was the sampler shift. It was scaled by pixel area,
        # so every non-native size sampled at 3.0-4.8 against the 8.0 these models
        # are trained at, and the result was the colour bleed reported as "rainbow
        # morphs". Forbidding the ratio treated the symptom; the shift is fixed at
        # its source instead. The area clamp below still applies.
        "aspect_ratios": ["16:9", "9:16", "1:1"],
    },
    "wan22-5b": {
        "name": "Wan 2.2 TI2V-5B (fp16)",
        "description": "Single 5B text+image-to-video model built for 16GB cards — fits VRAM (no CPU offload, no 22GB MoE), fast. Native 1280x704 @ 24fps. The consumer-GPU answer to the A14B.",
        "hf_repo": "Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
        "local_subdir": "diffusion_models",
        "files": [
            {"src": "split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors", "dst": "wan2.2_ti2v_5B_fp16.safetensors"},
        ],
        # TI2V-5B uses the NEW Wan 2.2 VAE (16x16x4), not the 2.1 VAE the A14B uses.
        "requires": ["wan22-vae", "wan-umt5"],
        "size_gb": 9.5,
        "vram_mb": 11000,
        "type": "wan",
        "dimension_alignment": 32,
        "max_pixel_area": 1_000_000,
        # Landscape, its transpose, and square. The earlier claim that off-native
        # frames "come back warped" does not survive the evidence: the output
        # directory holds seven 1:1 Wan I2V renders (512x512 and 736x736,
        # 2026-08-14), one of them the project's own demo clip.
        #
        # What actually warped was the sampler shift. It was scaled by pixel area,
        # so every non-native size sampled at 3.0-4.8 against the 8.0 these models
        # are trained at, and the result was the colour bleed reported as "rainbow
        # morphs". Forbidding the ratio treated the symptom; the shift is fixed at
        # its source instead. The area clamp below still applies.
        "aspect_ratios": ["16:9", "9:16", "1:1"],
    },
    "wan-vae": {
        "name": "Wan 2.1/2.2 VAE",
        "description": "Required by all Wan video models. Shared between versions.",
        "hf_repo": "QuantStack/Wan2.2-T2V-A14B-GGUF",
        "local_subdir": "vae",
        # Repo name is Wan2.1_VAE.safetensors; ComfyUI's VAELoader expects the
        # lowercase wan_2.1_vae.safetensors — download maps one to the other.
        "files": [
            {"src": "VAE/Wan2.1_VAE.safetensors", "dst": "wan_2.1_vae.safetensors"},
        ],
        "size_gb": 0.25,
        "vram_mb": 0,
        "type": "vae",
    },
    "wan22-vae": {
        "name": "Wan 2.2 VAE",
        "description": "Required by Wan 2.2 TI2V-5B — 16x16x4 compression, NOT interchangeable with the 2.1 VAE.",
        "hf_repo": "Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
        "local_subdir": "vae",
        "files": [
            {"src": "split_files/vae/wan2.2_vae.safetensors", "dst": "wan2.2_vae.safetensors"},
        ],
        "size_gb": 1.4,
        "vram_mb": 0,
        "type": "vae",
    },
    "wan-umt5": {
        "name": "UMT5-XXL Text Encoder (FP8)",
        "description": "Required by Wan 2.1/2.2 models for text encoding.",
        "hf_repo": "Osrivers/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "local_subdir": "text_encoders",
        "files": [
            {"src": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "dst": "umt5_xxl_fp8_e4m3fn_scaled.safetensors"},
        ],
        "size_gb": 6.3,
        "vram_mb": 0,
        "type": "encoder",
    },
    "t5-encoder": {
        "name": "T5-XXL Text Encoder (FP8)",
        "description": "Required by CogVideoX models for text encoding.",
        "hf_repo": "comfyanonymous/flux_text_encoders",
        "local_subdir": "clip",
        # CogVideoX workflow's CLIPLoader loads clip/t5/google_t5-v1_1-xxl_
        # encoderonly-fp8_e4m3fn.safetensors — the flux t5xxl fp8 IS that
        # encoder, just under a different name, so we rename on download.
        "files": [
            {"src": "t5xxl_fp8_e4m3fn.safetensors", "dst": "t5/google_t5-v1_1-xxl_encoderonly-fp8_e4m3fn.safetensors"},
        ],
        "size_gb": 4.6,
        "vram_mb": 0,
        "type": "encoder",
    },
    "codeformer": {
        "name": "CodeFormer (Face Restore)",
        "description": "Post-processing weights for Fix Anatomy — restores faces and reduces anatomy "
                       "defects after video generation. Requires the facerestore_cf ComfyUI node "
                       "(installed automatically when ComfyUI starts).",
        "local_subdir": "facerestore_models",
        "direct_urls": [
            {
                "url": "https://github.com/sczhou/CodeFormer/releases/download/v0.1.0/codeformer.pth",
                "dst": "codeformer.pth",
            }
        ],
        "size_gb": 0.35,
        "vram_mb": 0,
        "type": "facerestore",
    },
    "realesrgan-x2": {
        "name": "Real-ESRGAN 2x Upscaler",
        "description": "Upscales video frames 2x. Applied as post-processing after generation.",
        "hf_repo": "ai-forever/Real-ESRGAN",
        "hf_filename": "RealESRGAN_x2.pth",
        "local_subdir": "upscale_models",
        "check_files": ["RealESRGAN_x2.pth"],
        "size_gb": 0.07,
        "vram_mb": 0,
        "type": "upscaler",
    },
    # ── FLUX keyframe / storyboard IMAGE models ─────────────────────────────────
    # These are ComfyUI models (GGUF unet + encoders + VAE) that MUST live in
    # ComfyUI/models/{unet,clip,vae}, so they ride this registry's downloader (same
    # as Wan) rather than the diffusers Image-Models modal (which targets data/models/).
    # The filenames here are the SINGLE SOURCE OF TRUTH and must match the defaults
    # in comfyui_image_generator.py (FLUX_UNET / FLUX_T5 / FLUX_CLIP / FLUX_VAE and
    # the FLUX_DEV_* set) or the keyframe workflow throws "Value not in list".
    "flux-schnell": {
        "name": "FLUX.1-schnell (keyframe / storyboard image model)",
        "description": "Default keyframe + storyboard image model (fast, ~8 steps, Apache-2.0). "
                       "Needed for cinematic keyframes and the video keyframe→I2V path. Pulls its "
                       "CLIP-L + T5 + VAE companions automatically.",
        "hf_repo": "city96/FLUX.1-schnell-gguf",
        "local_subdir": "unet",
        "files": [
            {"src": "flux1-schnell-Q8_0.gguf", "dst": "flux1-schnell-Q8_0.gguf"},
        ],
        "requires": ["flux-clip-l", "flux-t5-fp8", "flux-vae-ae"],
        "size_gb": 12.6,
        "vram_mb": 12000,
        "type": "flux",
    },
    "flux-dev": {
        "name": "FLUX.1-dev (high-fidelity keyframe — GATED)",
        "description": "Higher-fidelity keyframe model for the strongest character identity lock. "
                       "GATED: the install needs a Hugging Face token that has accepted the FLUX.1-dev "
                       "license, or the download 401s. Shares the CLIP-L + VAE companions; adds the FP16 T5.",
        "hf_repo": "black-forest-labs/FLUX.1-dev",
        "local_subdir": "unet",
        "files": [
            {"src": "flux1-dev.safetensors", "dst": "flux1-dev.safetensors"},
        ],
        "requires": ["flux-clip-l", "flux-t5-fp16", "flux-vae-ae"],
        "size_gb": 23.8,
        "vram_mb": 12000,
        "type": "flux",
    },
    "flux-clip-l": {
        "name": "FLUX CLIP-L Text Encoder",
        "description": "Required by every FLUX image model (schnell + dev). Loaded by DualCLIPLoader.",
        "hf_repo": "comfyanonymous/flux_text_encoders",
        "local_subdir": "clip",
        "files": [
            {"src": "clip_l.safetensors", "dst": "clip_l.safetensors"},
        ],
        "size_gb": 0.25,
        "vram_mb": 0,
        "type": "encoder",
    },
    "flux-t5-fp8": {
        "name": "FLUX T5-XXL Text Encoder (FP8)",
        "description": "Required by FLUX.1-schnell. FP8 keeps it light for the 16GB card. "
                       "Installs to clip/t5/ to match the schnell workflow's loader path.",
        "hf_repo": "comfyanonymous/flux_text_encoders",
        "local_subdir": "clip",
        "files": [
            {"src": "t5xxl_fp8_e4m3fn.safetensors", "dst": "t5/t5xxl_fp8_e4m3fn.safetensors"},
        ],
        "size_gb": 4.9,
        "vram_mb": 0,
        "type": "encoder",
    },
    "flux-t5-fp16": {
        "name": "FLUX T5-XXL Text Encoder (FP16)",
        "description": "Required by FLUX.1-dev (the dev branch loads the FP16 T5 directly from clip/).",
        "hf_repo": "comfyanonymous/flux_text_encoders",
        "local_subdir": "clip",
        "files": [
            {"src": "t5xxl_fp16.safetensors", "dst": "t5xxl_fp16.safetensors"},
        ],
        "size_gb": 9.8,
        "vram_mb": 0,
        "type": "encoder",
    },
    "flux-vae-ae": {
        "name": "FLUX VAE (ae)",
        "description": "Shared autoencoder for all FLUX image models (schnell + dev). From the "
                       "ungated FLUX.1-schnell repo.",
        "hf_repo": "black-forest-labs/FLUX.1-schnell",
        "local_subdir": "vae",
        "files": [
            {"src": "ae.safetensors", "dst": "ae.safetensors"},
        ],
        "size_gb": 0.33,
        "vram_mb": 0,
        "type": "vae",
    },
    # ── FLUX.1 Kontext [dev] — instruction IMAGE EDITING (not video) ─────────────
    # Shares the ComfyUI models/ tree, so it rides this SSOT downloader alongside the
    # video entries. Drives "put a cowboy hat on this character"-style edits from chat.
    # NON-COMMERCIAL license. Companions (t5xxl_fp8, clip_l, ae.safetensors) are
    # already on disk from the FLUX stack and reused verbatim — NOT re-downloaded.
    "flux-kontext-dev": {
        "name": "FLUX.1 Kontext [dev] (GGUF Q6_K)",
        "description": "Natural-language image editing — edits an uploaded image from a text "
                       "instruction. Non-commercial license. ~10GB; fits 16GB with the fp8 T5 "
                       "encoder smart-offloaded.",
        "hf_repo": "QuantStack/FLUX.1-Kontext-dev-GGUF",
        "hf_filename": "flux1-kontext-dev-Q6_K.gguf",
        "local_subdir": "unet",
        "check_files": ["flux1-kontext-dev-Q6_K.gguf"],
        "size_gb": 9.85,
        "vram_mb": 14000,
        "type": "flux-edit",
    },
    # ── LTX-2.3 (Lightricks) — 16GB Ada: distilled FP8 + Gemma FP4 ──────────────
    # Requires ComfyUI ≥ 0.16.1 (native LTX-2.3). Transformer-only FP8 lives in
    # diffusion_models/ (Kijai layout). Install click pulls Gemma + text projection
    # + video VAE so one Ready state is actually runnable.
    "ltx23-distilled-fp8": {
        "name": "LTX-2.3 Distilled FP8 (16GB)",
        "description": "Lightricks LTX-2.3 distilled 22B — FP8 for RTX 40xx 16GB. "
                       "8 steps, CFG=1. Up to ~10s (161 frames @ 16fps). T2V + I2V. "
                       "Pulls Gemma FP4 + text projection + video VAE.",
        "hf_repo": "Kijai/LTX2.3_comfy",
        "local_subdir": "diffusion_models",
        "files": [
            {
                "src": "diffusion_models/ltx-2.3-22b-distilled-1.1_transformer_only_fp8_scaled.safetensors",
                "dst": "ltx-2.3-22b-distilled-1.1_transformer_only_fp8_scaled.safetensors",
            },
        ],
        "requires": ["ltx-gemma-fp4", "ltx-text-projection", "ltx-vae", "ltx-audio-vae"],
        "size_gb": 25.2,
        # 14000 (not 16000): distilled FP8 + Gemma-on-CPU runs on 16GB cards; the
        # gpu_session fit check adds +1024MB margin, and 16000+1024 > ~16376 total
        # falsely blocked VideoGen batches that already rendered via the direct path.
        "vram_mb": 14000,
        "type": "ltx",
        "dimension_alignment": 32,
    },
    "ltx-gemma-fp4": {
        "name": "Gemma 3 12B IT (FP4) — LTX text encoder",
        "description": "Required by LTX-2.3 on 16/24GB. FP4 leaves headroom for the "
                       "transformer; BF16 Gemma will OOM beside it.",
        "hf_repo": "Comfy-Org/ltx-2",
        "local_subdir": "text_encoders",
        "files": [
            {
                "src": "split_files/text_encoders/gemma_3_12B_it_fp4_mixed.safetensors",
                "dst": "gemma_3_12B_it_fp4_mixed.safetensors",
            },
        ],
        "size_gb": 9.5,
        "vram_mb": 0,
        "type": "encoder",
    },
    "ltx-text-projection": {
        "name": "LTX-2.3 Text Projection",
        "description": "Maps Gemma embeddings into LTX-2.3 transformer space. Required "
                       "companion for DualCLIPLoader / LTX text encode.",
        "hf_repo": "Kijai/LTX2.3_comfy",
        "local_subdir": "text_encoders",
        "files": [
            {
                "src": "text_encoders/ltx-2.3_text_projection_bf16.safetensors",
                "dst": "ltx-2.3_text_projection_bf16.safetensors",
            },
        ],
        "size_gb": 2.3,
        "vram_mb": 0,
        "type": "encoder",
    },
    "ltx-vae": {
        "name": "LTX-2.3 Video VAE",
        "description": "Video VAE for LTX-2.3 decode. Required for transformer-only "
                       "checkpoints (VAE is not baked into the FP8 file).",
        "hf_repo": "Kijai/LTX2.3_comfy",
        "local_subdir": "vae",
        "files": [
            {
                "src": "vae/LTX23_video_vae_bf16.safetensors",
                "dst": "LTX23_video_vae_bf16.safetensors",
            },
        ],
        "size_gb": 1.45,
        "vram_mb": 0,
        "type": "vae",
    },
    "ltx-audio-vae": {
        "name": "LTX-2.3 Audio VAE",
        "description": "Required companion — LTX-2.3 is an AV model; empty audio latents "
                       "must be concatenated before sampling. also_link places it in "
                       "checkpoints/ where LTXVAudioVAELoader enumerates.",
        "hf_repo": "Kijai/LTX2.3_comfy",
        "local_subdir": "vae",
        "files": [
            {
                "src": "vae/LTX23_audio_vae_bf16.safetensors",
                "dst": "LTX23_audio_vae_bf16.safetensors",
                "also_link": "checkpoints",
            },
        ],
        "size_gb": 0.36,
        "vram_mb": 0,
        "type": "vae",
    },
    "ltx-distilled-lora": {
        "name": "LTX-2.3 Distilled LoRA v1.1",
        "description": "Optional — some native templates pair the LoRA with a dev "
                       "checkpoint. Not required for the distilled FP8 transformer path.",
        "hf_repo": "Kijai/LTX2.3_comfy",
        "local_subdir": "loras",
        "files": [
            {
                "src": "loras/ltx-2.3-22b-distilled-1.1_lora-dynamic_fro09_avg_rank_111_bf16.safetensors",
                "dst": "ltx-2.3-22b-distilled-1.1_lora-dynamic_fro09_avg_rank_111_bf16.safetensors",
            },
        ],
        "size_gb": 2.74,
        "vram_mb": 0,
        "type": "lora",
    },
    # ── LTX-2.5 (Lightricks) — 16GB Ada: distilled Comfy int8 + Gemma 4 int8 ──
    # Official ComfyUI T2V/I2V templates (0.32+). Gated repo: accept the license
    # on Hugging Face with the HF_TOKEN account before Install will succeed.
    # Gemma 4 ships with projections baked in — no DualCLIP / text_projection.
    "ltx25-distilled-int8": {
        "name": "LTX-2.5 Distilled Int8 (16GB)",
        "description": "Lightricks LTX-2.5 distilled 22B — Comfy int8+convrot for "
                       "RTX 40xx 16GB. 8 steps, CFG=1. Up to ~10s. T2V + I2V. "
                       "Gated: accept Lightricks/LTX-2.5 on Hugging Face first. "
                       "Requires ComfyUI ≥ 0.32.0. Pulls Gemma 4 + DiffVAE + "
                       "audio VAE + spatial upscaler.",
        "hf_repo": "Lightricks/LTX-2.5",
        "local_subdir": "diffusion_models",
        "files": [
            {
                "src": "diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
                "dst": "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
            },
        ],
        "requires": [
            "ltx25-gemma4-int8",
            "ltx25-vae",
            "ltx25-audio-vae",
            "ltx25-spatial-upscaler",
        ],
        "size_gb": 20.03,
        "vram_mb": 14000,
        "type": "ltx",
        "dimension_alignment": 32,
    },
    "ltx25-gemma4-int8": {
        "name": "Gemma 4 12B + proj (Int8) — LTX-2.5 text encoder",
        "description": "Required by LTX-2.5. Projections are baked in (CLIPLoader, "
                       "not DualCLIP). Int8 so it can sit on CPU beside the "
                       "transformer on 16GB.",
        "hf_repo": "Lightricks/LTX-2.5",
        "local_subdir": "text_encoders",
        "files": [
            {
                "src": "text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
                "dst": "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
            },
        ],
        "size_gb": 14.32,
        "vram_mb": 0,
        "type": "encoder",
    },
    "ltx25-vae": {
        "name": "LTX-2.5 Video VAE (DiffVAE)",
        "description": "Diffusion video decoder for LTX-2.5. Official ComfyUI "
                       "templates use this, not the conv VAE.",
        "hf_repo": "Lightricks/LTX-2.5",
        "local_subdir": "vae",
        "files": [
            {
                "src": "vae/ltx-2.5-video-vae-bf16.safetensors",
                "dst": "ltx-2.5-video-vae-bf16.safetensors",
            },
        ],
        "size_gb": 1.37,
        "vram_mb": 0,
        "type": "vae",
    },
    "ltx25-audio-vae": {
        "name": "LTX-2.5 Audio VAE",
        "description": "Required companion — LTX-2.5 is an AV model; empty audio "
                       "latents must be concatenated before sampling. also_link "
                       "places it in checkpoints/ where LTXVAudioVAELoader "
                       "enumerates (2026-08-14: fresh installs failed ComfyUI "
                       "validation because only vae/ received the file).",
        "hf_repo": "Lightricks/LTX-2.5",
        "local_subdir": "vae",
        "files": [
            {
                "src": "vae/ltx-2.5-audio-vae-bf16.safetensors",
                "dst": "ltx-2.5-audio-vae-bf16.safetensors",
                "also_link": "checkpoints",
            },
        ],
        "size_gb": 0.34,
        "vram_mb": 0,
        "type": "vae",
    },
    "ltx25-spatial-upscaler": {
        "name": "LTX-2.5 Spatial Upscaler x2",
        "description": "Latent x2 spatial upsampler for the official two-stage "
                       "distilled pipeline. Stage 1 runs at half res so 16GB "
                       "output stays 768×512.",
        "hf_repo": "Lightricks/LTX-2.5",
        "local_subdir": "latent_upscale_models",
        "files": [
            {
                "src": "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
                "dst": "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
            },
        ],
        "size_gb": 0.93,
        "vram_mb": 0,
        "type": "upscaler",
    },
    # ── HunyuanVideo (Tencent) — 13B, GGUF Q5_K_M sized for 16GB cards ──────
    # Base weights ship without a content filter. Native ComfyUI nodes plus the
    # ComfyUI-GGUF loader; the LLaVA-3 8B text encoder sits on CPU on ≤20GB
    # cards (same residency policy as Wan's UMT5).
    "hunyuan-t2v": {
        "name": "HunyuanVideo 13B T2V (GGUF Q5_K_M)",
        "description": "Tencent HunyuanVideo 720p text-to-video. Cinematic motion, "
                       "strong prompt adherence, no content filter. ~9.5GB UNet; "
                       "LLaVA-3 text encoder loads on CPU on 16GB GPUs. 24fps, "
                       "frame counts 4n+1 (73 ≈ 3s).",
        "hf_repo": "city96/HunyuanVideo-gguf",
        "local_subdir": "unet",
        "files": [
            {"src": "hunyuan-video-t2v-720p-Q5_K_M.gguf", "dst": "hunyuan-video-t2v-720p-Q5_K_M.gguf"},
        ],
        "requires": ["hunyuan-llava-te", "hunyuan-clip-l", "hunyuan-vae"],
        "size_gb": 9.45,
        "vram_mb": 11000,
        "type": "hunyuan",
        "dimension_alignment": 16,
        "max_pixel_area": 1_000_000,
    },
    "hunyuan-i2v": {
        "name": "HunyuanVideo 13B I2V (GGUF Q5_K_M)",
        "description": "Tencent HunyuanVideo-I2V (v2 'replace' weights) — image-to-video "
                       "that follows the start frame closely. Same text encoder + VAE "
                       "as the T2V model plus the LLaVA vision tower. 24fps, 4n+1 frames.",
        "hf_repo": "city96/HunyuanVideo-I2V-gguf",
        "local_subdir": "unet",
        "files": [
            {"src": "hunyuan-video-i2v-720p-Q5_K_M.gguf", "dst": "hunyuan-video-i2v-720p-Q5_K_M.gguf"},
        ],
        "requires": ["hunyuan-llava-te", "hunyuan-clip-l", "hunyuan-vae", "hunyuan-clip-vision"],
        "size_gb": 9.45,
        "vram_mb": 11000,
        "type": "hunyuan",
        "dimension_alignment": 16,
        "max_pixel_area": 1_000_000,
    },
    "hunyuan-llava-te": {
        "name": "LLaVA-Llama-3 8B Text Encoder (FP8) — HunyuanVideo",
        "description": "Required by HunyuanVideo T2V/I2V. Loaded through DualCLIPLoader "
                       "together with clip_l; placed on CPU on 16GB cards.",
        "hf_repo": "Comfy-Org/HunyuanVideo_repackaged",
        "local_subdir": "text_encoders",
        "files": [
            {"src": "split_files/text_encoders/llava_llama3_fp8_scaled.safetensors",
             "dst": "llava_llama3_fp8_scaled.safetensors"},
        ],
        "size_gb": 9.09,
        "vram_mb": 0,
        "type": "encoder",
    },
    "hunyuan-clip-l": {
        "name": "CLIP-L Text Encoder — HunyuanVideo",
        "description": "Required by HunyuanVideo (second half of the DualCLIPLoader pair).",
        "hf_repo": "Comfy-Org/HunyuanVideo_repackaged",
        "local_subdir": "text_encoders",
        "files": [
            {"src": "split_files/text_encoders/clip_l.safetensors", "dst": "clip_l.safetensors"},
        ],
        "size_gb": 0.25,
        "vram_mb": 0,
        "type": "encoder",
    },
    "hunyuan-vae": {
        "name": "HunyuanVideo VAE (BF16)",
        "description": "Required by HunyuanVideo T2V/I2V. Decoded tiled (256px / 64 frames).",
        "hf_repo": "Comfy-Org/HunyuanVideo_repackaged",
        "local_subdir": "vae",
        "files": [
            {"src": "split_files/vae/hunyuan_video_vae_bf16.safetensors", "dst": "hunyuan_video_vae_bf16.safetensors"},
        ],
        "size_gb": 0.49,
        "vram_mb": 0,
        "type": "vae",
    },
    "hunyuan-clip-vision": {
        "name": "LLaVA-Llama-3 Vision Tower — HunyuanVideo I2V",
        "description": "Required by HunyuanVideo I2V only: encodes the start frame for "
                       "TextEncodeHunyuanVideo_ImageToVideo.",
        "hf_repo": "Comfy-Org/HunyuanVideo_repackaged",
        "local_subdir": "clip_vision",
        "files": [
            {"src": "split_files/clip_vision/llava_llama3_vision.safetensors", "dst": "llava_llama3_vision.safetensors"},
        ],
        "size_gb": 0.65,
        "vram_mb": 0,
        "type": "clip_vision",
    },
    # ── MiniMax H3 ────────────────────────────────────────────────────────────
    # "Which MiniMax": the local-weights H3 release (Comfy-Org/MiniMax-H3), not
    # the Hailuo cloud API nodes that share the template name. Native ComfyUI
    # support (MiniMaxH3ImageToVideo etc.) landed in v0.30.0 (PR #15224); the
    # bundled ComfyUI is v0.33.0. Two checkpoints: fl2va covers T2V and
    # first/last-frame I2V; ref2va is reference-to-video (up to 9 images,
    # 3 clips, 3 audio files). Each is offered as a precision ladder: pruned
    # int8 for 16 GB cards, unpruned int8 for 24 GB, bf16 for 48 GB+. Only the
    # 16 GB rung has been exercised; the others are declared from the repo's
    # file list and the vendor's size classes, marked unmeasured below.
    "minimax-h3-int8": {
        "name": "MiniMax H3 (Int8, 16GB)",
        "description": "MiniMax H3 omni-modal video — generates picture and native "
                       "stereo audio in one pass. Pruned int8+convrot transformer for "
                       "RTX 40xx 16GB. T2V + first/last-frame I2V, 24fps, ~5-15s. "
                       "Requires ComfyUI ≥ 0.30.0. Pulls Qwen3-VL 32B encoder + "
                       "video VAE + audio VAE + style embeddings (~42GB total).",
        "hf_repo": "Comfy-Org/MiniMax-H3",
        "local_subdir": "diffusion_models",
        "files": [
            {
                "src": "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
                "dst": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
            },
        ],
        "requires": [
            "minimax-h3-qwen3vl-nvfp4",
            "minimax-h3-vae",
            "minimax-h3-audio-vae",
            "minimax-h3-style-embeddings",
        ],
        "size_gb": 20.97,
        # Same class as ltx25-distilled-int8 (20GB int8+convrot transformer, which
        # renders on this 16GB tier at 14000). Not yet measured for H3 itself —
        # replaced by the Phase 0 benchmark peak when it is recorded.
        "vram_mb": 14000,
        "min_vram_gb": 16,
        **_H3_COMMON,
        "modes": _H3_FL2VA_MODES,
        "speed_profiles": H3_FL2VA_SPEED_PROFILES,
        # Per-VRAM-class starting points the Video Generator seeds its controls
        # from. 16 GB starts at the template's 480p canvas and standard steps;
        # whether turbo-8 becomes the 16 GB default is decided by the
        # benchmark's 1-vs-5 comparison, not assumed.
        "tier_defaults": {
            "16": {"width": 864, "height": 480, "speed_profile": "standard", "frames": 124},
            "24": {"width": 1344, "height": 768, "speed_profile": "standard", "frames": 124},
        },
    },
    "minimax-h3-ref2va-int8": {
        "name": "MiniMax H3 Reference (Int8, 16GB)",
        "description": "MiniMax H3 reference-to-video: up to 9 reference images, "
                       "3 reference clips and 3 audio files lock identity, motion, "
                       "camera and voice, or edit and continue a clip. Pruned "
                       "int8+convrot for RTX 40xx 16GB, 24fps, ~5-15s with native "
                       "audio. Shares the Qwen3-VL encoder, VAEs and embeddings "
                       "with MiniMax H3.",
        "hf_repo": "Comfy-Org/MiniMax-H3",
        "local_subdir": "diffusion_models",
        "files": [
            {
                "src": "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
                "dst": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
            },
        ],
        "requires": [
            "minimax-h3-qwen3vl-nvfp4",
            "minimax-h3-vae",
            "minimax-h3-audio-vae",
            "minimax-h3-style-embeddings",
        ],
        "size_gb": 19.53,
        # Same architecture and size class as the fl2va build; unmeasured.
        "vram_mb": 14000,
        "min_vram_gb": 16,
        **_H3_COMMON,
        "modes": ["ref2v"],
        "ref_limits": _H3_REF_LIMITS,
        "speed_profiles": H3_REF2VA_SPEED_PROFILES,
        "tier_defaults": {
            "16": {"width": 864, "height": 480, "speed_profile": "standard", "frames": 124},
            "24": {"width": 1344, "height": 768, "speed_profile": "standard", "frames": 124},
        },
    },
    "minimax-h3-int8-full": {
        "name": "MiniMax H3 (Int8 unpruned, 24GB+)",
        "description": "MiniMax H3 with the full modulation weights (Comfy-Org's "
                       "pruned builds drop ~40% of them). Int8+convrot transformer "
                       "plus the int8 Qwen3-VL encoder; meant for 24GB-class cards. "
                       "Unmeasured: declared from the repo's size classes.",
        "hf_repo": "Comfy-Org/MiniMax-H3",
        "local_subdir": "diffusion_models",
        "files": [
            {
                "src": "diffusion_models/minimax_h3_fl2va_int8_convrot.safetensors",
                "dst": "minimax_h3_fl2va_int8_convrot.safetensors",
            },
        ],
        "requires": [
            "minimax-h3-qwen3vl-int8",
            "minimax-h3-vae",
            "minimax-h3-audio-vae",
            "minimax-h3-style-embeddings",
        ],
        "size_gb": 31.70,
        # Unmeasured. Sized so gpu_session refuses the 16 GB tier outright
        # instead of offload-thrashing; a 24 GB run replaces this number.
        "vram_mb": 22000,
        "min_vram_gb": 24,
        **_H3_COMMON,
        "modes": _H3_FL2VA_MODES,
        "speed_profiles": H3_FL2VA_SPEED_PROFILES,
        "tier_defaults": {
            "24": {"width": 1344, "height": 768, "speed_profile": "standard", "frames": 124},
        },
    },
    "minimax-h3-bf16": {
        "name": "MiniMax H3 (BF16, 48GB+)",
        "description": "MiniMax H3 at full bf16 precision with the bf16 Qwen3-VL "
                       "encoder (~110GB of weights). For workstation cards with "
                       "48GB or more. Unmeasured: declared from the repo's size "
                       "classes.",
        "hf_repo": "Comfy-Org/MiniMax-H3",
        "local_subdir": "diffusion_models",
        "files": [
            {
                "src": "diffusion_models/minimax_h3_fl2va_bf16.safetensors",
                "dst": "minimax_h3_fl2va_bf16.safetensors",
            },
        ],
        "requires": [
            "minimax-h3-qwen3vl-bf16",
            "minimax-h3-vae",
            "minimax-h3-audio-vae",
            "minimax-h3-style-embeddings",
        ],
        "size_gb": 61.74,
        # Unmeasured; the floor keeps it off every consumer tier.
        "vram_mb": 44000,
        "min_vram_gb": 48,
        **_H3_COMMON,
        "modes": _H3_FL2VA_MODES,
        "speed_profiles": H3_FL2VA_SPEED_PROFILES,
        "tier_defaults": {
            "48": {"width": 1344, "height": 768, "speed_profile": "standard", "frames": 124},
        },
    },
    "minimax-h3-qwen3vl-nvfp4": {
        "name": "Qwen3-VL 32B (NVFP4 AWQ) — MiniMax H3 text encoder",
        "description": "Required by MiniMax H3 (CLIPLoader, type 'minimax'). NVFP4 is "
                       "the template default and the smallest cut (15.7GB); on "
                       "pre-Blackwell cards ComfyUI runs it as emulated ops via "
                       "comfy_kitchen's dequantize_nvfp4 rather than native fp4 matmul.",
        "hf_repo": "Comfy-Org/MiniMax-H3",
        "local_subdir": "text_encoders",
        "files": [
            {
                "src": "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
                "dst": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
            },
        ],
        "size_gb": 15.69,
        "vram_mb": 0,
        "type": "encoder",
    },
    "minimax-h3-qwen3vl-int8": {
        "name": "Qwen3-VL 32B (Int8) — MiniMax H3 text encoder",
        "description": "Encoder for the unpruned int8 H3 build on 24GB-class cards; "
                       "no fp4 emulation.",
        "hf_repo": "Comfy-Org/MiniMax-H3",
        "local_subdir": "text_encoders",
        "files": [
            {
                "src": "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
                "dst": "qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
            },
        ],
        "size_gb": 25.28,
        "vram_mb": 0,
        "type": "encoder",
    },
    "minimax-h3-qwen3vl-bf16": {
        "name": "Qwen3-VL 32B (BF16) — MiniMax H3 text encoder",
        "description": "Full-precision encoder for the bf16 H3 build.",
        "hf_repo": "Comfy-Org/MiniMax-H3",
        "local_subdir": "text_encoders",
        "files": [
            {
                "src": "text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors",
                "dst": "qwen3vl_32b_minimax_h3_bf16.safetensors",
            },
        ],
        "size_gb": 47.98,
        "vram_mb": 0,
        "type": "encoder",
    },
    "minimax-h3-vae": {
        "name": "MiniMax H3 Video VAE (FP16)",
        "description": "Required by MiniMax H3. Loaded through the plain VAELoader from vae/.",
        "hf_repo": "Comfy-Org/MiniMax-H3",
        "local_subdir": "vae",
        "files": [
            {
                "src": "vae/minimax_h3_video_vae_fp16.safetensors",
                "dst": "minimax_h3_video_vae_fp16.safetensors",
            },
        ],
        "size_gb": 5.21,
        "vram_mb": 0,
        "type": "vae",
    },
    "minimax-h3-audio-vae": {
        "name": "MiniMax H3 Audio VAE (FP32)",
        "description": "Required by MiniMax H3 — the model samples a joint video+audio "
                       "latent, so the audio decoder is not optional. Plain VAELoader "
                       "from vae/ (unlike LTX, no checkpoints/ link needed).",
        "hf_repo": "Comfy-Org/MiniMax-H3",
        "local_subdir": "vae",
        "files": [
            {
                "src": "vae/minimax_h3_audio_vae_fp32.safetensors",
                "dst": "minimax_h3_audio_vae_fp32.safetensors",
            },
        ],
        "size_gb": 0.61,
        "vram_mb": 0,
        "type": "vae",
    },
    "minimax-h3-style-embeddings": {
        "name": "MiniMax H3 style embeddings",
        "description": "Ten prompt embeddings shipped with H3 (bullet time, dark magic, "
                       "four seasons, ...). Tiny; pulled with every H3 build so the "
                       "style preset can never point at a missing file.",
        "hf_repo": "Comfy-Org/MiniMax-H3",
        "local_subdir": "embeddings",
        "files": [
            {"src": f"embeddings/{e['file']}", "dst": e["file"]} for e in H3_STYLE_EMBEDDINGS
        ],
        "size_gb": 0.011,
        "vram_mb": 0,
        "type": "embedding",
    },
    # Turbo LoRAs are optional (not in `requires`): 2 GB each, chosen through a
    # speed profile. Preflight names the missing file when a profile asks for
    # one that is not installed.
    "minimax-h3-fl2v-turbo-8step": {
        "name": "MiniMax H3 Turbo LoRA (8-step)",
        "description": "Optional distilled LoRA for the fl2va builds: 8 sampling steps "
                       "instead of 20 at slightly lower audio and motion quality "
                       "(docs.comfy.org). Selected via the Turbo speed profile.",
        "hf_repo": "Comfy-Org/MiniMax-H3",
        "local_subdir": "loras",
        "files": [
            {
                "src": "loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
                "dst": "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
            },
        ],
        "size_gb": 1.96,
        "vram_mb": 0,
        "type": "lora",
        "applies_to": ["minimax-h3-int8", "minimax-h3-int8-full", "minimax-h3-bf16"],
    },
    "minimax-h3-fl2v-turbo-4step-768p": {
        "name": "MiniMax H3 Turbo LoRA (4-step, 768p)",
        "description": "Optional distilled LoRA for the fl2va builds tuned at the 768 px "
                       "canvas: 4 sampling steps. Selected via the Turbo 768p speed "
                       "profile, which requires a 768 px short edge.",
        "hf_repo": "Comfy-Org/MiniMax-H3",
        "local_subdir": "loras",
        "files": [
            {
                "src": "loras/minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
                "dst": "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
            },
        ],
        "size_gb": 1.96,
        "vram_mb": 0,
        "type": "lora",
        "applies_to": ["minimax-h3-int8", "minimax-h3-int8-full", "minimax-h3-bf16"],
    },
    "minimax-h3-ref2v-turbo-4step": {
        "name": "MiniMax H3 Reference Turbo LoRA (4-step, v0.1)",
        "description": "Optional distilled LoRA for the ref2va build: 4 sampling steps. "
                       "Vendor-labelled v0.1; the profile is marked experimental.",
        "hf_repo": "Comfy-Org/MiniMax-H3",
        "local_subdir": "loras",
        "files": [
            {
                "src": "loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
                "dst": "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
            },
        ],
        "size_gb": 1.96,
        "vram_mb": 0,
        "type": "lora",
        "applies_to": ["minimax-h3-ref2va-int8"],
    },
}


def _normalize_registry() -> None:
    """Derive `check_files` from `files[].dst` for every entry that uses `files`.

    This is what makes `files` the single source of truth: the install/ready
    check and the ComfyUI loader both read paths that are guaranteed identical to
    the download destination, so they cannot drift (issue #36).
    """
    for mid, entry in VIDEO_MODEL_REGISTRY.items():
        files = entry.get("files")
        if files:
            entry["check_files"] = [f["dst"] for f in files]
        elif entry.get("direct_urls"):
            entry["check_files"] = [f["dst"] for f in entry["direct_urls"]]
        elif "check_files" not in entry and "hf_filename" in entry:
            entry["check_files"] = [entry["hf_filename"]]


def vram_mb_for_model(model_id: str, *, default: int = 11000) -> int:
    """VRAM debit estimate for gpu_session / orchestrator (registry SSOT)."""
    entry = VIDEO_MODEL_REGISTRY.get(model_id or "") or {}
    vram = int(entry.get("vram_mb") or 0)
    return vram if vram > 0 else default


# 16GB-consumer defaults — Wan 2.2 5B TI2V fits without CPU offload.
DEFAULT_T2V_MODEL = "wan22-5b"
DEFAULT_I2V_MODEL = "wan22-5b"


def _comfyui_reachable() -> bool:
    try:
        from backend.services.comfyui_video_generator import get_video_generator
        vg = get_video_generator()
        if getattr(vg, "service_available", False):
            return True
        if hasattr(vg, "_check_comfyui_connection"):
            return bool(vg._check_comfyui_connection())
    except Exception:
        pass
    return False


def preflight_video_model(model_id: str) -> tuple[bool, str]:
    """Return (ready, error_message). Blocks silent fallback to the wrong backend."""
    entry = VIDEO_MODEL_REGISTRY.get(model_id or "")
    if not entry:
        return False, f"Unknown video model '{model_id}'"

    name = entry.get("name") or model_id
    mtype = entry.get("type")

    if mtype == "wan":
        if not is_model_installed(model_id):
            return False, (
                f"{name} is not installed. Open Manage Video Models to download it "
                f"before queuing a batch."
            )
        if not _comfyui_reachable():
            return False, (
                f"{name} requires ComfyUI. Start the ComfyUI plugin, then retry."
            )
        return True, ""

    if mtype == "cogvideox":
        if model_id == "cogvideox-5b":
            offline_ok = False
            try:
                from backend.services.offline_video_generator import OfflineVideoGenerator
                off = OfflineVideoGenerator()
                # Importable diffusers + a GPU used to count as "ready"; with an
                # empty in-process cache that let a batch start and download 20GB.
                offline_ok = bool(getattr(off, "cogvideox_available", False)) and off.is_model_cached(model_id)
            except Exception:
                offline_ok = False
            if offline_ok or is_model_installed(model_id):
                return True, ""
            return False, (
                "CogVideoX 5B is not ready: install the model via Manage Video Models "
                "or ensure the offline diffusers backend (torch + GPU) is available."
            )

        if not is_model_installed(model_id):
            return False, (
                f"{name} is not installed. Open Manage Video Models to download it."
            )
        for dep in entry.get("requires", []):
            if not is_model_installed(dep):
                dep_name = (VIDEO_MODEL_REGISTRY.get(dep) or {}).get("name") or dep
                return False, (
                    f"{name} is missing companion '{dep_name}'. "
                    f"Open Manage Video Models and Install again (companions auto-pull)."
                )
        if not _comfyui_reachable():
            return False, (
                f"{name} requires ComfyUI for image-to-video. Start ComfyUI, then retry."
            )
        return True, ""

    if mtype == "hunyuan":
        if not is_model_installed(model_id):
            return False, (
                f"{name} is not installed. Open Manage Video Models to download it "
                f"(and its LLaVA / CLIP-L / VAE companions) before queuing a batch."
            )
        for dep in entry.get("requires", []):
            if not is_model_installed(dep):
                dep_name = (VIDEO_MODEL_REGISTRY.get(dep) or {}).get("name") or dep
                return False, (
                    f"{name} is missing companion '{dep_name}'. "
                    f"Open Manage Video Models and Install again (companions auto-pull)."
                )
        if not _comfyui_reachable():
            return False, (
                f"{name} requires ComfyUI with the ComfyUI-GGUF custom node. "
                f"Start the ComfyUI plugin, then retry."
            )
        return True, ""

    if mtype == "ltx":
        if not is_model_installed(model_id):
            return False, (
                f"{name} is not installed. Open Manage Video Models to download it "
                f"(and its Gemma / VAE companions) before queuing a batch."
            )
        for dep in entry.get("requires", []):
            if not is_model_installed(dep):
                dep_name = (VIDEO_MODEL_REGISTRY.get(dep) or {}).get("name") or dep
                return False, (
                    f"{name} is missing companion '{dep_name}'. "
                    f"Open Manage Video Models and Install again (companions auto-pull)."
                )
        if not _comfyui_reachable():
            if str(model_id).startswith("ltx25"):
                return False, (
                    f"{name} requires ComfyUI ≥ 0.32.0 with LTX-2.5 support. "
                    f"Start the ComfyUI plugin, then retry."
                )
            return False, (
                f"{name} requires ComfyUI ≥ 0.16.1 with LTX-2.3 support. "
                f"Start the ComfyUI plugin, then retry."
            )
        return True, ""

    if mtype == "minimax":
        if not is_model_installed(model_id):
            return False, (
                f"{name} is not installed. Open Manage Video Models to download it "
                f"(and its Qwen3-VL / VAE companions) before queuing a batch."
            )
        for dep in entry.get("requires", []):
            if not is_model_installed(dep):
                dep_name = (VIDEO_MODEL_REGISTRY.get(dep) or {}).get("name") or dep
                return False, (
                    f"{name} is missing companion '{dep_name}'. "
                    f"Open Manage Video Models and Install again (companions auto-pull)."
                )
        if not _comfyui_reachable():
            return False, (
                f"{name} requires ComfyUI ≥ 0.30.0 with MiniMax H3 support. "
                f"Start the ComfyUI plugin, then retry."
            )
        return True, ""

    return True, ""


def supports_first_frame_i2v(model_id: str) -> bool:
    """True when the model animates a supplied first frame itself.

    Derived from what each family already declares — the Wan/Hunyuan loader
    maps carry a t2v/i2v/ti2v type, LTX and MiniMax H3 take a first frame in
    their own graphs, CogVideoX names its I2V build — so the cinematic
    keyframe path never swaps a model for a different family behind the
    person's back. Before 2026-08-29 every non-Cog model without "i2v" in its
    id was animated by Wan 2.2 14B I2V: LTX, Wan 5B and MiniMax renders came
    back as Wan renders."""
    entry = VIDEO_MODEL_REGISTRY.get(model_id or "")
    if not entry:
        return False
    modes = entry.get("modes")
    if modes:
        # Declared capability wins: the H3 reference build has no first-frame
        # input, so it must never be picked to animate a keyframe.
        return "i2v" in modes or "flf2v" in modes
    mtype = entry.get("type")
    if mtype in ("ltx", "minimax"):
        return True
    if mtype == "wan":
        return (wan_comfyui_map().get(model_id) or {}).get("type") in ("i2v", "ti2v")
    if mtype == "hunyuan":
        return (hunyuan_comfyui_map().get(model_id) or {}).get("type") == "i2v"
    if mtype == "cogvideox":
        return "i2v" in model_id
    return False


def i2v_model_for(model_id: str, default: str = "wan22-14b-i2v") -> str:
    """The model that animates a keyframe for `model_id`: the model itself
    when it takes a first frame, else its same-family I2V sibling
    (wan22-14b → wan22-14b-i2v, hunyuan-t2v → hunyuan-i2v, cogvideox-5b →
    cogvideox-5b-i2v), else `default`."""
    mid = model_id or ""
    if supports_first_frame_i2v(mid):
        return mid
    entry = VIDEO_MODEL_REGISTRY.get(mid) or {}
    for candidate in (f"{mid}-i2v", mid.replace("-t2v", "-i2v")):
        if candidate != mid and candidate in VIDEO_MODEL_REGISTRY \
                and VIDEO_MODEL_REGISTRY[candidate].get("type") == entry.get("type") \
                and supports_first_frame_i2v(candidate):
            return candidate
    # No name-pattern sibling (the H3 reference build): the same-family
    # first-frame model that shares the most companions, so the keyframe is
    # still animated by the family the person picked.
    if entry:
        shared = set(entry.get("requires", []))
        siblings = [
            (len(shared & set(e.get("requires", []))), cid)
            for cid, e in VIDEO_MODEL_REGISTRY.items()
            if cid != mid and e.get("type") == entry.get("type") and supports_first_frame_i2v(cid)
        ]
        if siblings:
            return max(siblings)[1]
    return default


# ── Capability contract ──────────────────────────────────────────────────────
# Flat keys on a generation entry describe what the model can do, so the
# Video Generator, MCP tools and batch generators read data instead of
# testing `type == "minimax"`. Entries that predate the contract get family
# defaults from model_capabilities(); nothing here is a second registry.
CAPABILITY_MODES = ("t2v", "i2v", "l2v", "flf2v", "ref2v")
GENERATION_TYPES = ("wan", "cogvideox", "ltx", "hunyuan", "minimax")


def _derived_modes(model_id: str, entry: dict) -> list:
    mtype = entry.get("type")
    if mtype == "wan":
        kind = (wan_comfyui_map().get(model_id) or {}).get("type")
        return {"t2v": ["t2v"], "i2v": ["i2v"], "ti2v": ["t2v", "i2v"]}.get(kind, ["t2v"])
    if mtype == "hunyuan":
        return ["i2v"] if (hunyuan_comfyui_map().get(model_id) or {}).get("type") == "i2v" else ["t2v"]
    if mtype == "cogvideox":
        return ["i2v"] if "i2v" in model_id else ["t2v"]
    if mtype in ("ltx", "minimax"):
        return ["t2v", "i2v"]
    return []


def model_capabilities(model_id: str) -> dict:
    """The capability record for a generation entry (empty dict for companions
    and unknown ids). Declared keys win; the rest are family defaults."""
    entry = VIDEO_MODEL_REGISTRY.get(model_id or "") or {}
    if entry.get("type") not in GENERATION_TYPES:
        return {}
    caps = {
        "modes": entry.get("modes") or _derived_modes(model_id, entry),
        "audio_out": bool(entry.get("audio_out", False)),
        "audio_in": bool(entry.get("audio_in", False)),
        "cfg": bool(entry.get("cfg", True)),
        "aspect_ratios": list(entry.get("aspect_ratios") or []),
        "dimension_alignment": entry.get("dimension_alignment"),
        "max_pixel_area": entry.get("max_pixel_area"),
        "native_fps": entry.get("native_fps"),
        "frame_rule": entry.get("frame_rule"),
        "max_frames": entry.get("max_frames"),
        "min_clip_s": entry.get("min_clip_s"),
        "max_clip_s": entry.get("max_clip_s"),
        "duration_tiers": list(entry.get("duration_tiers") or []),
        "min_steps": entry.get("min_steps"),
        "default_steps": entry.get("default_steps"),
        "speed_profiles": dict(entry.get("speed_profiles") or {}),
        "style_embeddings": list(entry.get("style_embeddings") or []),
        "ref_limits": entry.get("ref_limits"),
        "tier_defaults": dict(entry.get("tier_defaults") or {}),
        "min_vram_gb": entry.get("min_vram_gb"),
        "license": entry.get("license"),
    }
    caps["supports_t2v"] = "t2v" in caps["modes"]
    caps["supports_i2v"] = "i2v" in caps["modes"] or "flf2v" in caps["modes"]
    return caps


def speed_profile_for(model_id: str, profile: str | None) -> dict | None:
    """Resolve a declared speed profile to its settings plus the LoRA filename
    the builder loads (``lora_file``), or None when the model does not declare
    that profile. A profile without a LoRA resolves with ``lora_file`` None."""
    if not profile:
        return None
    entry = VIDEO_MODEL_REGISTRY.get(model_id or "") or {}
    spec = (entry.get("speed_profiles") or {}).get(profile)
    if not spec:
        return None
    resolved = dict(spec)
    resolved["id"] = profile
    resolved["lora_file"] = None
    lora_id = spec.get("lora")
    if lora_id:
        lora_entry = VIDEO_MODEL_REGISTRY.get(lora_id) or {}
        files = lora_entry.get("files") or []
        resolved["lora_file"] = files[0]["dst"] if files else None
        resolved["lora_installed"] = is_model_installed(lora_id)
    return resolved


def style_embedding_token(model_id: str, style_id: str | None) -> str | None:
    """The prompt token for a declared style embedding id, or None."""
    if not style_id:
        return None
    for emb in (VIDEO_MODEL_REGISTRY.get(model_id or "") or {}).get("style_embeddings") or []:
        if emb.get("id") == style_id:
            return emb.get("token")
    return None


def vram_tier_for(total_vram_mb: int | float | None, tiers) -> str | None:
    """The largest declared tier key (a VRAM class in GB, as a string) that the
    card meets. Cards that report a few hundred MB under a round number still
    count for it, matching hardware_policy's grace for 15.9 GB "16 GB" cards."""
    if not total_vram_mb or not tiers:
        return None
    total_gb = float(total_vram_mb) / 1024.0 + 0.5
    best = None
    for key in tiers:
        try:
            need = float(key)
        except (TypeError, ValueError):
            continue
        if need <= total_gb and (best is None or need > float(best)):
            best = key
    return best


def tier_defaults_for(model_id: str, total_vram_mb: int | float | None = None) -> dict:
    """The starting settings for this card, chosen from the entry's
    ``tier_defaults``. Probes VRAM when not supplied; returns {} when the
    entry declares no tiers or the card is below every declared class."""
    tiers = (VIDEO_MODEL_REGISTRY.get(model_id or "") or {}).get("tier_defaults") or {}
    if not tiers:
        return {}
    if total_vram_mb is None:
        try:
            from backend.services.gpu_resource_coordinator import get_available_vram
            total_vram_mb = (get_available_vram() or {}).get("total_mb") or 0
        except Exception:
            total_vram_mb = 0
    key = vram_tier_for(total_vram_mb, tiers)
    if key is None:
        return {}
    return {"tier": key, **tiers[key]}


def wan_comfyui_map() -> dict:
    """Build the ComfyUI Wan loader map from the registry (never raises).

    Returns {model_id: {type, unet_high, unet_low, clip, vae}} derived from the
    same `files[].dst` the downloader writes — so the loader always points at the
    bytes that were actually fetched. Replaces the hand-maintained WAN22_MODELS
    copy in comfyui_video_generator.py.
    """
    out = {}
    try:
        for mid, entry in VIDEO_MODEL_REGISTRY.items():
            if entry.get("type") != "wan":
                continue
            dsts = [f["dst"] for f in entry.get("files", [])]
            high = next((d for d in dsts if "HighNoise" in d), None)
            low = next((d for d in dsts if "LowNoise" in d), None)
            vae = clip = None
            for dep in entry.get("requires", []):
                dep_entry = VIDEO_MODEL_REGISTRY.get(dep, {})
                dep_files = dep_entry.get("files", [])
                dep_dst = dep_files[0]["dst"] if dep_files else (dep_entry.get("check_files") or [None])[0]
                if dep_entry.get("type") == "vae":
                    vae = dep_dst
                elif dep_entry.get("type") == "encoder":
                    clip = dep_dst
            single = high is None and low is None  # single-model TI2V (Wan 2.2 5B)
            out[mid] = {
                "type": "ti2v" if single else ("i2v" if "i2v" in mid else "t2v"),
                "single": single,
                "unet": dsts[0] if (single and dsts) else None,
                "unet_high": high,
                "unet_low": low,
                "clip": clip,
                "vae": vae,
            }
    except Exception as e:  # never break generation import over a registry quirk
        logger.error("wan_comfyui_map() build failed: %s", e, exc_info=True)
    return out


def is_ltx25_model(model_id: str) -> bool:
    """True for LTX-2.5 generation ids (Gemma 4 + two-stage, no text_projection)."""
    mid = str(model_id or "")
    if mid.startswith("ltx25"):
        return True
    entry = VIDEO_MODEL_REGISTRY.get(mid) or {}
    return any(str(d).startswith("ltx25") for d in entry.get("requires", []))


def classify_hf_download_error(exc: BaseException, *, repo_id: str | None = None) -> str:
    """Turn a Hugging Face 401/403 into the same copy the image catalog uses.

    Non-gated failures are returned as ``str(exc)`` unchanged.
    """
    msg = str(exc).lower()
    gated = any(
        token in msg
        for token in (
            "401",
            "403",
            "gated",
            "restricted",
            "cannot access",
            "access to model",
            "401 client error",
            "403 client error",
        )
    )
    if not gated:
        return str(exc)
    has_token = bool(
        os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    )
    if not has_token:
        return (
            "Gated on Hugging Face — set HF_TOKEN in .env and restart the backend."
        )
    url = f"https://huggingface.co/{repo_id}" if repo_id else "the model page"
    return (
        f"Gated on Hugging Face — open {url} and click "
        "'Agree and access repository' with the account your HF_TOKEN belongs to."
    )


def ltx_comfyui_map() -> dict:
    """Build the ComfyUI LTX loader map from the registry (never raises).

    2.3 entries: {unet, clip, text_projection, vae, audio_vae}
    2.5 entries: {unet, clip, vae, audio_vae, upscale_model} — projections
    are baked into the Gemma 4 file, so there is no DualCLIP companion.
    Paths come from the same ``files[].dst`` the downloader writes.
    """
    out = {}
    try:
        for mid, entry in VIDEO_MODEL_REGISTRY.items():
            if entry.get("type") != "ltx":
                continue
            dsts = [f["dst"] for f in entry.get("files", [])]
            unet = dsts[0] if dsts else None
            vae = audio_vae = clip = text_projection = upscale_model = None
            for dep in entry.get("requires", []):
                dep_entry = VIDEO_MODEL_REGISTRY.get(dep, {})
                dep_files = dep_entry.get("files", [])
                dep_dst = dep_files[0]["dst"] if dep_files else (dep_entry.get("check_files") or [None])[0]
                if not dep_dst:
                    continue
                if dep in ("ltx-audio-vae", "ltx25-audio-vae"):
                    audio_vae = dep_dst
                elif dep in ("ltx-text-projection",):
                    text_projection = dep_dst
                elif dep_entry.get("type") == "vae":
                    vae = dep_dst
                elif dep_entry.get("type") == "encoder":
                    clip = dep_dst
                elif dep_entry.get("type") == "upscaler" or dep.endswith("spatial-upscaler"):
                    upscale_model = dep_dst
            mapped = {
                "type": "ti2v",
                "unet": unet,
                "clip": clip,
                "vae": vae,
                "audio_vae": audio_vae,
            }
            if is_ltx25_model(mid):
                mapped["upscale_model"] = upscale_model
            else:
                mapped["text_projection"] = text_projection
            out[mid] = mapped
    except Exception as e:
        logger.error("ltx_comfyui_map() build failed: %s", e, exc_info=True)
    return out


def minimax_comfyui_map() -> dict:
    """Build the ComfyUI MiniMax H3 loader map from the registry (never raises).

    Returns {model_id: {unet, clip, vae, audio_vae}} derived from `files[].dst`,
    so the (future) workflow builder loads exactly the bytes the downloader
    wrote. The two VAEs are told apart by filename because both are type "vae".
    """
    out = {}
    try:
        for mid, entry in VIDEO_MODEL_REGISTRY.items():
            if entry.get("type") != "minimax":
                continue
            dsts = [f["dst"] for f in entry.get("files", [])]
            mapped = {"unet": dsts[0] if dsts else None, "clip": None, "vae": None, "audio_vae": None}
            for dep in entry.get("requires", []):
                dep_entry = VIDEO_MODEL_REGISTRY.get(dep, {})
                dep_files = dep_entry.get("files", [])
                dep_dst = dep_files[0]["dst"] if dep_files else (dep_entry.get("check_files") or [None])[0]
                dep_type = dep_entry.get("type")
                if dep_type == "encoder":
                    mapped["clip"] = dep_dst
                elif dep_type == "vae":
                    mapped["audio_vae" if "audio" in (dep_dst or "") else "vae"] = dep_dst
            out[mid] = mapped
    except Exception as e:
        logger.error("minimax_comfyui_map() build failed: %s", e, exc_info=True)
    return out


def cogvideox_comfyui_map() -> dict:
    """Build the ComfyUI CogVideoX single-file loader map from the registry
    (never raises). Returns {model_id: {unet, vae}} for the wrapper's
    CogVideoXModelLoader (models/diffusion_models) + CogVideoXVAELoader
    (models/vae). Only entries that use `files` are mapped — cogvideox-5b is a
    diffusers snapshot loaded by directory and is not part of this map."""
    out = {}
    try:
        for mid, entry in VIDEO_MODEL_REGISTRY.items():
            if entry.get("type") != "cogvideox" or not entry.get("files"):
                continue
            unet = entry["files"][0]["dst"]
            vae = None
            for dep in entry.get("requires", []):
                dep_entry = VIDEO_MODEL_REGISTRY.get(dep, {})
                if dep_entry.get("type") == "vae" and dep_entry.get("files"):
                    vae = dep_entry["files"][0]["dst"]
            out[mid] = {"unet": unet, "vae": vae}
    except Exception as e:
        logger.error("cogvideox_comfyui_map() build failed: %s", e, exc_info=True)
    return out


def hunyuan_comfyui_map() -> dict:
    """Build the ComfyUI HunyuanVideo loader map from the registry (never raises).

    Returns {model_id: {type, unet, clip_l, clip_llava, vae, clip_vision}} with
    every filename taken from the same ``files[].dst`` the downloader writes.
    ``clip_vision`` is only populated for image-to-video entries.
    """
    out = {}
    try:
        for mid, entry in VIDEO_MODEL_REGISTRY.items():
            if entry.get("type") != "hunyuan":
                continue
            dsts = [f["dst"] for f in entry.get("files", [])]
            mapped = {
                "type": "i2v" if "i2v" in mid else "t2v",
                "unet": dsts[0] if dsts else None,
                "clip_l": None,
                "clip_llava": None,
                "vae": None,
                "clip_vision": None,
            }
            for dep in entry.get("requires", []):
                dep_entry = VIDEO_MODEL_REGISTRY.get(dep, {})
                dep_files = dep_entry.get("files", [])
                dep_dst = dep_files[0]["dst"] if dep_files else (dep_entry.get("check_files") or [None])[0]
                if not dep_dst:
                    continue
                dep_type = dep_entry.get("type")
                if dep_type == "vae":
                    mapped["vae"] = dep_dst
                elif dep_type == "clip_vision":
                    mapped["clip_vision"] = dep_dst
                elif dep_type == "encoder":
                    if "clip_l" in dep_dst:
                        mapped["clip_l"] = dep_dst
                    else:
                        mapped["clip_llava"] = dep_dst
            out[mid] = mapped
    except Exception as e:
        logger.error("hunyuan_comfyui_map() build failed: %s", e, exc_info=True)
    return out


def _verify_capabilities(mid: str, entry: dict) -> list:
    """Contract checks for an entry that declares capabilities: every mode is
    in the vocabulary, every speed profile's LoRA exists and is a LoRA whose
    floor does not exceed its step count, every style token has a file in an
    embedding companion, and a license names its attribution."""
    problems = []
    for mode in entry.get("modes") or []:
        if mode not in CAPABILITY_MODES:
            problems.append(f"{mid}: unknown mode '{mode}'")
    for pid, spec in (entry.get("speed_profiles") or {}).items():
        steps, floor = spec.get("steps"), spec.get("min_steps")
        if not steps or not floor or floor > steps:
            problems.append(f"{mid}: speed profile '{pid}' needs min_steps <= steps")
        lora = spec.get("lora")
        if lora:
            lora_entry = VIDEO_MODEL_REGISTRY.get(lora)
            if not lora_entry or lora_entry.get("type") != "lora" or not lora_entry.get("files"):
                problems.append(f"{mid}: speed profile '{pid}' names unknown LoRA '{lora}'")
            elif mid not in (lora_entry.get("applies_to") or []):
                problems.append(f"{mid}: LoRA '{lora}' does not list it in applies_to")
    embedding_files = set()
    for dep in entry.get("requires", []):
        dep_entry = VIDEO_MODEL_REGISTRY.get(dep) or {}
        if dep_entry.get("type") == "embedding":
            embedding_files.update(f["dst"] for f in dep_entry.get("files", []))
    for emb in entry.get("style_embeddings") or []:
        if emb.get("file") not in embedding_files:
            problems.append(f"{mid}: style embedding '{emb.get('id')}' has no file in an embedding companion")
    if entry.get("min_steps") and entry.get("default_steps") \
            and entry["default_steps"] < entry["min_steps"]:
        problems.append(f"{mid}: default_steps below min_steps")
    lic = entry.get("license")
    if lic is not None and not (lic.get("name") and lic.get("attribution")):
        problems.append(f"{mid}: license must carry name and attribution")
    for tier, defaults in (entry.get("tier_defaults") or {}).items():
        prof = defaults.get("speed_profile")
        if prof and prof not in (entry.get("speed_profiles") or {}):
            problems.append(f"{mid}: tier '{tier}' default names unknown speed profile '{prof}'")
    return problems


def verify_registry() -> list:
    """Sanity-check the registry is internally complete. Returns a list of
    human-readable problems (empty = healthy). Never raises."""
    problems = []
    try:
        for mid, entry in VIDEO_MODEL_REGISTRY.items():
            if not entry.get("check_files"):
                problems.append(f"{mid}: no check_files (and no files/hf_filename to derive from)")
            for dep in entry.get("requires", []):
                if dep not in VIDEO_MODEL_REGISTRY:
                    problems.append(f"{mid}: requires unknown model '{dep}'")
            if entry.get("type") == "wan":
                m = wan_comfyui_map().get(mid, {})
                # Single-model TI2V (5B) has one `unet`; MoE (A14B) has high/low experts.
                required = ("unet", "clip", "vae") if m.get("single") else ("unet_high", "unet_low", "clip", "vae")
                for k in required:
                    if not m.get(k):
                        problems.append(f"{mid}: ComfyUI map missing '{k}' (companion/file not resolvable)")
            if entry.get("type") == "ltx":
                m = ltx_comfyui_map().get(mid, {})
                required = (
                    ("unet", "clip", "vae", "audio_vae", "upscale_model")
                    if is_ltx25_model(mid)
                    else ("unet", "clip", "text_projection", "vae", "audio_vae")
                )
                for k in required:
                    if not m.get(k):
                        problems.append(f"{mid}: LTX ComfyUI map missing '{k}'")
            if entry.get("type") == "minimax":
                m = minimax_comfyui_map().get(mid, {})
                for k in ("unet", "clip", "vae", "audio_vae"):
                    if not m.get(k):
                        problems.append(f"{mid}: MiniMax ComfyUI map missing '{k}'")
                problems.extend(_verify_capabilities(mid, entry))
            if entry.get("type") == "cogvideox" and entry.get("files"):
                m = cogvideox_comfyui_map().get(mid, {})
                for k in ("unet", "vae"):
                    if not m.get(k):
                        problems.append(f"{mid}: CogVideoX ComfyUI map missing '{k}'")
            if entry.get("type") == "hunyuan":
                m = hunyuan_comfyui_map().get(mid, {})
                required = ("unet", "clip_l", "clip_llava", "vae")
                if m.get("type") == "i2v":
                    required += ("clip_vision",)
                for k in required:
                    if not m.get(k):
                        problems.append(f"{mid}: Hunyuan ComfyUI map missing '{k}'")
    except Exception as e:
        problems.append(f"verify_registry crashed: {e}")
    return problems


_normalize_registry()

# Loud-but-non-fatal startup check: drift/typos surface in logs instead of as a
# mysterious blank render later.
_problems = verify_registry()
if _problems:
    logger.error("Video model registry has %d consistency problem(s): %s",
                 len(_problems), "; ".join(_problems))
