
import contextlib
import logging
import os
import re
import uuid
import time
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
import tempfile
import threading

# Prevent PyTorch VRAM heap fragmentation
if "PYTORCH_CUDA_ALLOC_CONF" not in os.environ:
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

logger = logging.getLogger(__name__)


def _detach_exception(exc: BaseException) -> None:
    """Drop an exception's traceback/context so it stops pinning dead frames.

    2026-08-04 client box 2048² incident: `except ... as e` keeps e.__traceback__,
    whose frames hold the failed diffusers forward's locals — the pipeline,
    latents, and decode activations. With that alive, _unload_pipeline() frees
    ~nothing, and the OOM ladder stacked 2-3 host-resident pipelines (50-100GB)
    until the desktop died. Call this BEFORE any unload/reload in an OOM handler.
    On CPython, sys.exc_info() derives the traceback from the value, so nulling
    it inside the live except block genuinely releases the frames.
    """
    try:
        exc.__traceback__ = None
        exc.__context__ = None
        exc.__cause__ = None
    except Exception:
        pass


class RamWatchdogAbort(RuntimeError):
    """Raised mid-denoise when system RAM falls below the survival floor.

    Deliberately handled BEFORE the CUDA-OOM ladder: reloading pipelines while
    the box is out of RAM deepens the exact hole we're escaping (2026-08-04).
    """


try:
    import torch
    from diffusers import (
        StableDiffusionPipeline,
        StableDiffusionXLPipeline,
        StableDiffusionImg2ImgPipeline,
        StableDiffusionXLImg2ImgPipeline,
        DPMSolverMultistepScheduler
    )
    from PIL import Image
    import safetensors
    diffusion_available = True
    logger.info("Diffusion dependencies loaded successfully")
except ImportError as e:
    diffusion_available = False
    logger.warning(f"Diffusion dependencies not available: {e}")


# CUDA errors after which the driver has torn the context down. Every later CUDA
# call in this process fails with the same text; torch cannot rebuild the context,
# only a process restart can. Out-of-memory is deliberately absent: it is
# recoverable and belongs to the OOM ladder in generate_image().
# Observed 2026-09-03 on a 16 GB Blackwell box: one Xid 8 (channel watchdog,
# "the launch timed out and was terminated") and the backend then failed every
# image request for nine hours, each reported as a model download problem.
_FATAL_CUDA_MARKERS = (
    "launch timed out",            # cudaErrorLaunchTimeout — watchdog / Xid 8
    "unspecified launch failure",  # cudaErrorLaunchFailure
    "illegal memory access",       # cudaErrorIllegalAddress
    "illegal instruction",         # cudaErrorIllegalInstruction
    "misaligned address",          # cudaErrorMisalignedAddress
    "invalid program counter",     # cudaErrorInvalidPc
    "hardware stack error",        # cudaErrorHardwareStackError
    "device-side assert",          # cudaErrorAssert
    "uncorrectable ecc error",     # cudaErrorECCUncorrectable
)


def is_fatal_cuda_error(exc: BaseException) -> bool:
    """True when ``exc`` is a CUDA error that leaves the process's context dead."""
    msg = (str(exc) or "").lower()
    if "out of memory" in msg:
        return False
    return any(marker in msg for marker in _FATAL_CUDA_MARKERS)

try:
    from diffusers import FlowMatchEulerDiscreteScheduler
except Exception:
    FlowMatchEulerDiscreteScheduler = None

# Z-Image (Tongyi-MAI) ships in diffusers >= 0.38. Import separately so an older
# diffusers that lacks ZImagePipeline doesn't disable the whole diffusion stack.
try:
    from diffusers import ZImagePipeline, ZImageImg2ImgPipeline
    zimage_available = True
except Exception:  # ImportError on older diffusers
    ZImagePipeline = None
    ZImageImg2ImgPipeline = None
    zimage_available = False

# Krea 2 Turbo (krea.ai): 12B DiT, CFG-distilled 8-step inference. Ships in
# diffusers >= 0.39. Import separately so missing support doesn't break SD/SDXL.
try:
    from diffusers import Krea2Pipeline
    krea2_available = True
except Exception:
    Krea2Pipeline = None
    krea2_available = False

try:
    from backend.config import CACHE_DIR
    config_available = True
except ImportError:
    config_available = False
    CACHE_DIR = "/tmp/guaardvark_cache"

try:
    from backend.services.face_restoration_service import get_face_restoration_service
    face_restoration_available = True
except ImportError as e:
    face_restoration_available = False
    logger.warning(f"Face restoration service not available: {e}")

@dataclass
class ImageGenerationRequest:
    prompt: str
    negative_prompt: str = ""
    # Canvas / sampling: prefer resolve_stills_defaults() at call sites. Defaults
    # here are modern (1024 + Z-Image HF recipe 9/0) so chat/agent paths that
    # omit knobs do not inherit SD-era 512/20/7.5.
    width: int = 1024
    height: int = 1024
    num_inference_steps: int = 9
    guidance_scale: float = 0.0
    style: str = "realistic"
    seed: Optional[int] = None
    model: str = "auto"
    content_preset: Optional[str] = None
    auto_enhance: bool = True
    enhance_anatomy: bool = True
    enhance_faces: bool = True
    enhance_hands: bool = True
    # Opt-in only — face restore is slow and was defaulting True for chat.
    restore_faces: bool = False
    face_restoration_weight: float = 0.5
    remove_background: bool = False  # post-process with rembg -> transparent RGBA PNG
    # Batch runs set this so we don't reload/unload a 6B pipeline every image —
    # that peak (especially Z-Image CPU offload) was OOM-killing the Flask process.
    keep_pipeline_loaded: bool = False
    # Character LoRAs (Z-Image / future). Paths to .safetensors + optional strength.
    loras: Optional[List[str]] = None
    lora_scale: float = 1.0

@dataclass
class ImageGenerationResult:
    success: bool
    image_path: Optional[str] = None
    image_data: Optional[bytes] = None
    prompt_used: str = ""
    negative_prompt_used: str = ""
    model_used: str = ""
    generation_time: float = 0.0
    image_size: Tuple[int, int] = (512, 512)
    seed_used: Optional[int] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = None


def normalize_zimage_lora_state_dict(state_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Rewrite PEFT-wrapped Z-Image LoRA keys to Diffusers ``load_lora_weights`` form.

    Our early peft_zimage trainer saved keys like
    ``transformer.base_model.model.layers.*.attention.to_q.lora_A.weight``.
    ZImagePipeline expects ``transformer.layers.*.attention.to_q.lora_A.weight``
    (no PEFT ``base_model.model`` wrapper). Without this remap, PEFT raises
    "Target modules {...} not found in the base model".
    """
    out: Dict[str, Any] = {}
    for key, value in state_dict.items():
        nk = key
        if nk.startswith("transformer.base_model.model."):
            nk = "transformer." + nk[len("transformer.base_model.model.") :]
        elif nk.startswith("base_model.model."):
            nk = "transformer." + nk[len("base_model.model.") :]
        elif ".base_model.model." in nk:
            nk = nk.replace(".base_model.model.", ".", 1)
        out[nk] = value
    return out


class OfflineImageGenerator:

    def __init__(self):
        project_root = Path(__file__).parent.parent.parent
        self.models_dir = project_root / "data" / "models" / "stable_diffusion"
        self.cache_dir = Path(CACHE_DIR) / "generated_images"

        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # No default/fallback model, by design (2026-08-07). SD 1.5 used to sit here
        # as a hidden fallback: when a requested model failed to load — gated repo,
        # missing download, OOM — generation silently continued on SD 1.5 and returned
        # an image. Because the canvas was never re-clamped, a 2021-era 512px model was
        # asked for 1024²/2048² and produced garbage that looked like the requested
        # model misbehaving (observed with Krea 2). A model that cannot be loaded is now
        # an error, and unusable models are filtered out of the menu before the user can
        # pick one — see get_available_models().
        # Curated quality lineup (2026-05-29 cull). Outdated SD1.5/2.1-era models
        # (sd-2.1, sd-turbo, dreamlike, deliberate, openjourney, analog) were removed.
        self.available_models = {
            # Z-Image-Turbo (Tongyi-MAI): 6B, Apache-2.0, ungated. Best prompt
            # adherence per VRAM on a 16 GB card — the preferred all-rounder.
            "zimage-turbo": "Tongyi-MAI/Z-Image-Turbo",
            # FLUX.1-dev — max-quality stills via ComfyUI (not offline Diffusers).
            # Value is a sentinel; batch routes to Comfy when model=flux-dev.
            "flux-dev": "comfy:flux-dev",
            "krea2-turbo": "krea/Krea-2-Turbo",
            "krea2-raw": "krea/Krea-2-Raw",
            "sd-xl": "stabilityai/stable-diffusion-xl-base-1.0",
            "sdxl-turbo": "stabilityai/sdxl-turbo",
            "realistic-vision": "SG161222/Realistic_Vision_V5.1_noVAE",
            "epic-realism": "emilianJR/epiCRealism",
        }
        # Models resolvable internally but NOT shown in menus / list_available_models.
        # Empty since the sd-1.5 hidden fallback was removed; the mechanism stays because
        # get_available_models() and model_recommender still honour it.
        self.hidden_models: set[str] = set()
        # Offline Diffusers cannot load these — batch/API must route to ComfyUI.
        self.comfy_only_models = {"flux-dev"}
        # UI metadata for the visible models (label/description/recommended/order).
        # Drives the centralized dropdown via get_available_models().
        self.model_meta = {
            "zimage-turbo": {"label": "Z-Image Turbo (Best daily)", "description": "Strong prompt adherence + text, fast (~9 steps / CFG 0). Default daily driver.", "recommended": True, "order": 0},
            "flux-dev": {"label": "FLUX.1 Dev (Max quality)", "description": "Highest ceiling stills via ComfyUI (~28 steps, FluxGuidance). Slower; needs Comfy + flux1-dev weights.", "recommended": False, "order": 1, "engine": "comfy"},
            "krea2-turbo": {"label": "Krea 2 Turbo", "description": "12B aesthetic-first model, native 2K, 8 steps CFG-free. Fast inference.", "recommended": False, "order": 2},
            "krea2-raw": {"label": "Krea 2 Raw", "description": "Base 12B checkpoint — less post-trained than Turbo (~52 steps, CFG 3.5). Use for mature/creative prompts or LoRA base.", "recommended": False, "order": 3},
            "sd-xl": {"label": "SDXL Base", "description": "High-res 1024, reliable, huge LoRA ecosystem.", "recommended": False, "order": 4},
            "sdxl-turbo": {"label": "SDXL Turbo (Fast)", "description": "Fast 1024 previews, few steps.", "recommended": False, "order": 5},
            "realistic-vision": {"label": "Realistic Vision", "description": "Top photoreal faces & portraits.", "recommended": False, "order": 6},
            "epic-realism": {"label": "Epic Realism", "description": "Cinematic photorealism.", "recommended": False, "order": 7},
        }

        self.anatomy_negative = "deformed body, distorted anatomy, extra limbs, missing limbs, extra arms, missing arms, extra legs, missing legs, fused limbs, disconnected limbs, floating limbs, asymmetrical body, disproportionate limbs, twisted torso, broken spine, impossible pose, malformed body, mutated anatomy, gross proportions, extra heads, conjoined, siamese, bad anatomy, cropped body, out of frame body, duplicate person, clone"

        self.face_negative = "asymmetrical face, lopsided face, distorted facial features, bad teeth, cross-eyed, lazy eye, eyes looking different directions, uneven eyes, floating eyes, deformed face, malformed face, poorly drawn eyes, poorly drawn nose, poorly drawn mouth, missing eyes, extra eyes, blurry face, low quality face, ugly face"

        self.hands_negative = "bad hands, deformed hands, malformed hands, extra fingers, missing fingers, fused fingers, webbed fingers, too many fingers, wrong number of fingers, six fingers, four fingers, three fingers, mutant hands, claw hands, backwards hands, wrong hand orientation, floating hands, disconnected hands, hands with no wrist, poorly drawn hands"

        self.body_negative = "wrong proportions, head too big, head too small, torso too long, arms too long, arms too short, legs too long, legs too short, unnatural stance, impossible posture, broken joints, dislocated joints, reverse joints"

        self.logic_negative = "floating objects, disconnected elements, impossible physics, wrong perspective, incorrect scale, illogical scene, inconsistent lighting, impossible poses, wrong object placement"

        self.base_negative = "low quality, blurry, distorted, watermark, signature, text, low resolution, pixelated, artifacts, noise, oversaturated, jpeg artifacts"

        self.style_configs = {
            "realistic": {
                "positive_suffix": "photorealistic, high quality, detailed, sharp focus, professional photography, natural lighting, realistic textures, correct proportions",
                "negative_prompt": f"cartoon, anime, illustration, painting, drawing, art, sketch, 3d render, cgi, {self.anatomy_negative}, {self.base_negative}"
            },
            "artistic": {
                "positive_suffix": "artistic, beautiful, creative, masterpiece, fine art, professional artwork, balanced composition, artistic lighting",
                "negative_prompt": f"amateur, {self.anatomy_negative}, {self.base_negative}"
            },
            "cartoon": {
                "positive_suffix": "cartoon style, animated, colorful, clean lines, cel shading, vector illustration, flat design, geometric forms",
                "negative_prompt": f"realistic, photographic, {self.base_negative}"
            },
            "sketch": {
                "positive_suffix": "pencil sketch, hand-drawn, artistic lines, monochrome, detailed linework, professional illustration",
                "negative_prompt": f"colored, photographic, {self.base_negative}"
            },
            "infographic": {
                "positive_suffix": "flat vector illustration, infographic style, clean geometric forms, minimal shadows, professional design, clear composition, no people",
                "negative_prompt": f"photorealism, realistic faces, realistic people, {self.base_negative}"
            },
            "technical": {
                "positive_suffix": "technical illustration, clean lines, precise details, professional diagram, clear composition, minimal style",
                "negative_prompt": f"artistic, {self.base_negative}"
            }
        }

        # Prompt shaping only — deliberately NO steps/guidance/dimensions (2026-08-07).
        # These presets used to carry recommended_steps / recommended_guidance /
        # recommended_dimensions tuned for SD 1.5 (35 steps, CFG 8.0, 512x768). They are
        # keyed on *content type*, so they knew nothing about the selected model: typing
        # a full-body prompt pushed the UI to 35 steps even for Krea 2 Turbo, an 8-step
        # CFG-free model. The backend clamped it back at render time
        # (_soft_clamp_family_sampling), so the numbers on screen were never what ran —
        # a placebo slider. Sampling belongs to the model, and lives in
        # settings_validator's per-model config.
        self.content_presets = {
            "person_portrait": {
                "positive_suffix": "professional portrait photography, natural skin texture, realistic lighting, sharp focus on face, proper facial proportions, symmetrical features",
                "negative_prompt": f"{self.anatomy_negative}, {self.face_negative}, {self.base_negative}"
            },
            "person_full_body": {
                "positive_suffix": "full body shot, proper human proportions, natural pose, correct anatomy, realistic stance, balanced composition, anatomically correct",
                "negative_prompt": f"{self.anatomy_negative}, {self.hands_negative}, {self.body_negative}, {self.logic_negative}, floating limbs, disconnected body parts, {self.base_negative}"
            },
            "person_athletic": {
                "positive_suffix": "athletic activity, natural movement, dynamic pose, proper body mechanics, focused action, correct body proportions",
                "negative_prompt": f"{self.anatomy_negative}, {self.hands_negative}, {self.body_negative}, {self.logic_negative}, stiff pose, unnatural stance, {self.base_negative}"
            },
            "person_working": {
                "positive_suffix": "realistic work scene, natural work pose, logical workspace, proper body posture",
                "negative_prompt": f"{self.anatomy_negative}, {self.hands_negative}, {self.body_negative}, {self.logic_negative}, floating tools, disconnected actions, impossible poses, {self.base_negative}"
            },
            "product_photo": {
                "positive_suffix": "product photography, clean background, studio lighting, commercial quality, sharp focus, professional presentation",
                "negative_prompt": f"blurry, distorted, {self.base_negative}"
            },
            "landscape": {
                "positive_suffix": "landscape photography, scenic, natural lighting, high dynamic range, beautiful composition, vivid colors",
                "negative_prompt": f"blurry, oversaturated, artificial, {self.base_negative}"
            },
            "infographic_preset": {
                "positive_suffix": "flat vector design, clean geometric shapes, minimal design, professional infographic, clear icons, simple composition",
                "negative_prompt": f"photorealistic, 3d, shadows, gradients, complex textures, realistic people, {self.base_negative}"
            },
            "general": {
                "positive_suffix": "high quality, detailed, professional, sharp focus",
                "negative_prompt": f"{self.base_negative}"
            }
        }

        self._pipeline = None
        self._img2img_pipeline = None
        self._img2img_family = None
        self._current_model = None
        # Set by _mark_gpu_fault() after a context-killing CUDA error; read by
        # every entry point so the process fails fast and says why.
        self._gpu_fault: Optional[Dict[str, Any]] = None
        # Offload mode of the resident pipeline: None | "sequential" | "model" | "full"
        self._pipeline_offload_mode = None
        # One-shot force sequential reload after a mid-inference OOM.
        self._force_sequential_offload = False
        # Active character LoRA adapter names loaded on the current pipeline.
        self._loaded_lora_adapters: List[str] = []

        self._device = "cpu"
        if torch.cuda.is_available():
            try:
                dummy = torch.zeros(1, device='cuda')
                _ = dummy + dummy
                torch.cuda.synchronize()
                self._device = "cuda"
            except Exception as e:
                logger.warning(f"CUDA is available but not usable (e.g., PyTorch compatibility issue), falling back to CPU: {e}")
        
        self._generation_lock = threading.RLock()
        # One-shot / once-per-process: avoid WARNING spam when xformers is absent.
        self._xformers_warned = False

        self._compile_failed = False
        self._compile_unet_orig = None
        self._compile_vae_orig = None

        self.service_available = diffusion_available

        logger.info(f"OfflineImageGenerator initialized - Device: {self._device}, Models dir: {self.models_dir}")

    def _get_model_path(self, model_id: str) -> Path:
        model_name = model_id.replace("/", "--")
        return self.models_dir / model_name

    def _is_model_downloaded(self, model_id: str) -> bool:
        # Comfy-only models (FLUX.1-dev): check ComfyUI unet asset, not HF snapshot.
        mid = (model_id or "").lower()
        if mid.startswith("comfy:") or mid == "flux-dev" or "flux1-dev" in mid or mid.endswith("flux-dev"):
            return self._flux_dev_assets_present()
        model_path = self._get_model_path(model_id)
        # A non-empty directory is NOT enough. An aborted gated download leaves a
        # README and an empty images/ folder behind — observed with Krea 2 (1 MB of
        # ~28 GB). That counted as "downloaded", so the menu advertised the model and
        # every run then died inside the loader. Diffusers needs model_index.json.
        if not (model_path / "model_index.json").is_file():
            return False
        # model_index.json is small and lands early, so its presence alone still reads
        # as "ready" while multi-GB shards are still in flight. huggingface_hub tracks
        # those with .incomplete markers under .cache/huggingface/download.
        download_cache = model_path / ".cache" / "huggingface" / "download"
        if download_cache.is_dir():
            if next(download_cache.rglob("*.incomplete"), None) is not None:
                return False
        # ...but a *failed* download cleans those markers up on its way out, taking the
        # only in-flight signal with it. Observed 2026-08-07: Krea 2 Turbo aborted at
        # 14 GB and left a tidy-looking tree with model_index.json, configs, tokenizer
        # and VAE — and zero transformer shards. So confirm the declared weights exist.
        return self._snapshot_weights_present(model_path)

    # Components in a diffusers pipeline that legitimately carry no weight files.
    _WEIGHTLESS_COMPONENTS = frozenset({
        "scheduler", "tokenizer", "tokenizer_2", "tokenizer_3",
        "feature_extractor", "image_processor", "processor",
    })

    def _snapshot_weights_present(self, model_path: Path) -> bool:
        """True when every weight-bearing component named in model_index.json is there.

        Shards are the common casualty of an interrupted download — they are the big
        files, so they finish last. A sharded component also ships an index naming
        every piece, which makes "is this complete?" exactly checkable.
        """
        try:
            import json
            with open(model_path / "model_index.json") as f:
                index = json.load(f)
        except Exception as e:
            logger.debug(f"Could not read model_index.json under {model_path}: {e}")
            return False

        for component, spec in index.items():
            if component.startswith("_") or component in self._WEIGHTLESS_COMPONENTS:
                continue
            # Component entries look like ["diffusers", "AutoencoderKL"]; anything
            # else (nulls for optional pieces, scalars) carries no weights to check.
            if not isinstance(spec, (list, tuple)) or len(spec) != 2:
                continue
            comp_dir = model_path / component
            if not comp_dir.is_dir():
                logger.debug(f"{model_path.name}: component '{component}' missing")
                return False

            shard_index = next(comp_dir.glob("*.index.json"), None)
            if shard_index is not None:
                try:
                    with open(shard_index) as f:
                        weight_map = json.load(f).get("weight_map", {})
                except Exception:
                    return False
                for shard in set(weight_map.values()):
                    if not (comp_dir / shard).is_file():
                        logger.debug(
                            f"{model_path.name}: '{component}' missing shard {shard}"
                        )
                        return False
                continue

            if not any(comp_dir.glob("*.safetensors")) and not any(comp_dir.glob("*.bin")):
                logger.debug(f"{model_path.name}: '{component}' has no weight file")
                return False

        return True

    @staticmethod
    def _flux_dev_assets_present() -> bool:
        """True when Comfy can run the FLUX-dev stills graph (unet + clip + vae)."""
        try:
            from backend.config import COMFYUI_DIR
            root = Path(COMFYUI_DIR) / "models"
        except Exception:
            root = Path("plugins/comfyui/ComfyUI/models")
        unet = root / "unet" / os.environ.get("GUAARDVARK_FLUX_DEV_UNET", "flux1-dev.safetensors")
        vae = root / "vae" / os.environ.get("GUAARDVARK_FLUX_VAE", "ae.safetensors")
        clip = root / "clip" / os.environ.get("GUAARDVARK_FLUX_CLIP", "clip_l.safetensors")
        # T5 may live as clip/t5xxl_*.safetensors or clip/t5/...
        t5_name = os.environ.get("GUAARDVARK_FLUX_DEV_T5", "t5xxl_fp16.safetensors")
        t5_candidates = [
            root / "clip" / t5_name,
            root / "clip" / "t5" / t5_name,
            root / "text_encoders" / t5_name,
        ]
        t5_ok = any(p.is_file() and p.stat().st_size > 0 for p in t5_candidates)
        return all(p.is_file() and p.stat().st_size > 0 for p in (unet, vae, clip)) and t5_ok

    def is_comfy_only_model(self, model_key: str) -> bool:
        key = (model_key or "").strip().lower()
        return key in getattr(self, "comfy_only_models", set()) or key.startswith("flux")

    # How long a repo-access verdict stays good. The menu asks per model, so without
    # a cache every dropdown open would fan out HTTP requests.
    _REPO_ACCESS_TTL_SECONDS = 300

    @staticmethod
    def _hf_token() -> Optional[str]:
        return (
            os.environ.get("HF_TOKEN")
            or os.environ.get("HUGGING_FACE_HUB_TOKEN")
            or None
        )

    def _probe_repo_access(self, repo_id: str) -> str:
        """Can we actually fetch weights for this HF repo?

        Returns one of: ``ok`` | ``needs_token`` | ``needs_licence`` | ``unreachable``.

        Metadata access is not a sufficient test — a gated repo answers 200 on
        /api/models but 403 on the files themselves, which is exactly how Krea 2
        looked "fine" right up until the download failed. So probe a real file.
        """
        cache = getattr(self, "_repo_access_cache", None)
        if cache is None:
            cache = self._repo_access_cache = {}
        now = time.monotonic()
        hit = cache.get(repo_id)
        if hit and now - hit[1] < self._REPO_ACCESS_TTL_SECONDS:
            return hit[0]

        verdict = "unreachable"
        try:
            import requests
            token = self._hf_token()
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            resp = requests.head(
                f"https://huggingface.co/{repo_id}/resolve/main/model_index.json",
                headers=headers, timeout=6, allow_redirects=True,
            )
            if resp.status_code == 200:
                verdict = "ok"
            elif resp.status_code in (401, 403):
                # 401 without a token is "log in"; 403 with one means the account
                # has not accepted this repo's terms.
                verdict = "needs_licence" if token else "needs_token"
            elif resp.status_code == 404:
                verdict = "unreachable"
        except Exception as e:
            logger.debug(f"Repo access probe failed for {repo_id}: {e}")

        cache[repo_id] = (verdict, now)
        return verdict

    def _mark_gpu_fault(self, exc: BaseException, where: str) -> str:
        """Record a context-killing CUDA error; return the user-facing message.

        Only the first fault is recorded — everything after it is the same dead
        context reporting itself. No CUDA calls are made here: freeing tensors on
        a dead context raises again, and the process is being restarted anyway.
        """
        if self._gpu_fault is None:
            first_line = (str(exc) or type(exc).__name__).strip().splitlines()[0]
            self._gpu_fault = {
                "error": first_line,
                "where": where,
                "at": datetime.now().isoformat(timespec="seconds"),
            }
            logger.critical(
                "GPU FAULT during %s: %s — the CUDA context in this process is dead. "
                "Every GPU job will fail until the backend is restarted. Driver report: "
                "journalctl -k -b | grep -i xid",
                where, first_line,
            )
        return self.gpu_fault_message()

    def gpu_fault_message(self) -> Optional[str]:
        """The message every GPU entry point returns once a fault is recorded."""
        fault = self._gpu_fault
        if not fault:
            return None
        return (
            f"GPU fault at {fault['at']} during {fault['where']}: {fault['error']}. "
            "The GPU driver reset this backend's CUDA context and it cannot be "
            "recovered in-process. Restart the backend, then retry — the model and "
            "its download are fine. Driver details: journalctl -k -b | grep -i xid"
        )

    def _load_failure_reason(self, model_key: str, model_id: str) -> str:
        """Explain a load failure in terms the user can act on."""
        fault = self.gpu_fault_message()
        if fault:
            return fault
        if self.is_comfy_only_model(model_key or ""):
            return (
                f"'{model_key}' runs through ComfyUI and its weights are not installed. "
                "Install the FLUX dev unet/clip/vae assets, then retry."
            )
        if not self._is_model_downloaded(model_id):
            access = self._probe_repo_access(model_id)
            if access == "needs_licence":
                return (
                    f"'{model_key}' is not downloaded: {model_id} is a gated repository "
                    f"and your Hugging Face account has not accepted its terms. Visit "
                    f"https://huggingface.co/{model_id}, click 'Agree and access "
                    "repository', then retry."
                )
            if access == "needs_token":
                return (
                    f"'{model_key}' is not downloaded: {model_id} requires authentication. "
                    "Set HF_TOKEN in .env and restart the backend."
                )
            if access == "unreachable":
                return (
                    f"'{model_key}' is not downloaded and {model_id} could not be reached. "
                    "Check network access to huggingface.co."
                )
            return f"'{model_key}' is not downloaded yet ({model_id})."
        return (
            f"'{model_key}' is downloaded but its pipeline failed to load. See the backend "
            "log for the loader error — usually VRAM pressure or an incomplete download."
        )

    def _resolve_model_ref(self, model_ref: str) -> str:
        """Catalog key (e.g. krea2-turbo) → HF repo id; pass through HF ids and auto."""
        if not model_ref or model_ref in ("auto", ""):
            return model_ref
        return self.available_models.get(model_ref, model_ref)

    def _krea2_variant(self, model_ref: str) -> str:
        """turbo = CFG-distilled 8-step; raw = base checkpoint ~52 steps / CFG 3.5."""
        key = (model_ref or "").lower()
        mid = self._resolve_model_ref(model_ref).lower()
        if "raw" in key or "krea-2-raw" in mid.replace("_", "-"):
            return "raw"
        return "turbo"

    def _skip_negative_prompt(self, family: str, model_ref: str, guidance_scale: float) -> bool:
        """Turbo/Z-Image-low-CFG skip negatives; Krea Raw uses full CFG negatives."""
        if family == "krea2":
            return self._krea2_variant(model_ref) == "turbo"
        if family == "zimage":
            return guidance_scale <= 1.0
        return False

    def _model_family(self, model_id: str) -> str:
        """Map a catalog key or HF model id to a pipeline family.

        Drives pipeline class, scheduler, VRAM strategy, and generation params.
        """
        key = (model_id or "").lower()
        mid = self._resolve_model_ref(model_id).lower()
        if key.startswith("krea2") or (
            "krea" in mid
            and (
                "krea-2" in mid.replace("_", "-")
                or "krea2" in mid.replace("-", "").replace("/", "")
            )
        ):
            return "krea2"
        if "z-image" in mid or "zimage" in mid or key.startswith("zimage"):
            return "zimage"
        if "flux" in mid or key.startswith("flux"):
            return "flux"
        if "xl" in mid or "sdxl" in mid:
            return "sdxl"
        return "sd"

    def _build_img2img_pipeline(self, family: str):
        """Share weights from the loaded txt2img pipeline for img2img edits."""
        if family == 'krea2':
            raise RuntimeError(
                "Krea 2 does not support img2img editing yet — use kontext or sd-xl"
            )
        if family == 'zimage':
            if ZImageImg2ImgPipeline is None:
                raise RuntimeError(
                    "Z-Image img2img is unavailable (upgrade diffusers >= 0.38)"
                )
            return ZImageImg2ImgPipeline(
                transformer=self._pipeline.transformer,
                vae=self._pipeline.vae,
                text_encoder=self._pipeline.text_encoder,
                tokenizer=self._pipeline.tokenizer,
                scheduler=self._pipeline.scheduler,
            )
        if family == 'sdxl':
            return StableDiffusionXLImg2ImgPipeline(
                vae=self._pipeline.vae,
                text_encoder=self._pipeline.text_encoder,
                text_encoder_2=self._pipeline.text_encoder_2,
                tokenizer=self._pipeline.tokenizer,
                tokenizer_2=self._pipeline.tokenizer_2,
                unet=self._pipeline.unet,
                scheduler=self._pipeline.scheduler,
            )
        if family == 'sd':
            return StableDiffusionImg2ImgPipeline(
                vae=self._pipeline.vae,
                text_encoder=self._pipeline.text_encoder,
                tokenizer=self._pipeline.tokenizer,
                unet=self._pipeline.unet,
                scheduler=self._pipeline.scheduler,
                safety_checker=None,
                feature_extractor=None,
                requires_safety_checker=False,
            )
        raise RuntimeError(f"Model family '{family}' does not support img2img editing")

    # Measured pipeline footprints per family (bf16 weights + denoise activations),
    # not aspirations. The 2026-06-10 OOM postmortem: a flat 3500MB estimate let the
    # admission check pass while Z-Image actually allocated 9.9GB — straight into a
    # wall of resident Ollama models (gemma 4.95GB + qwen3-embedding 4.32GB).
    # zimage: WITH enable_model_cpu_offload. krea2 model-offload peak ~14GB on 16GB
    # (2026-07-11); sequential offload is used on consumer cards and peaks lower.
    _FAMILY_VRAM_MB = {"krea2": 14000, "zimage": 11000, "sdxl": 8000, "sd": 4000}
    _KREA2_SEQUENTIAL_VRAM_MB = 10000  # layer-by-layer offload on ≤18GB cards
    # CPU-RAM footprint with enable_model_cpu_offload (weights + PyTorch arena).
    # Observed: ~47 GB RSS on 60 GB box during Z-Image batch; gate before load.
    _FAMILY_RAM_GB = {"krea2": 24.0, "zimage": 21.0, "sdxl": 10.0, "sd": 6.0}
    # zimage 24.0 -> 21.0 (2026-08-05): 24.0 predated the ladder/unload leak fixes
    # (the "~47 GB RSS" note above is from that era). The CALIBRATED comment below
    # measured peak RSS flat at 20.9-21.0 GB across 1024/1448/2048 AFTER those fixes.
    # Measured again on a 32GB/RTX5080 box 2026-08-05: 16.3 GB peak for a single
    # 1024 image, 19.9 GB across a 15-image batch - so 21.0 remains conservative.
    # auto-router worst-case: zimage leads on consumer cards; krea2 when absent
    _OFFLOAD_TURBO_VRAM_MB = 11000
    # Prefer sequential offload for krea2 on consumer cards (module offload OOMs).
    _SEQUENTIAL_OFFLOAD_VRAM_GB = 18.0
    # Resolution scaling (2026-08-04 client box 2048² incident): the flat constants
    # above were measured at ~1024², but admission approved 2048² (4× the pixels)
    # under the same numbers. Slope = additional cost per megapixel ABOVE the 1MP
    # calibration point, so 1024² requests still produce exactly the measured
    # constants. CALIBRATED 2026-08-04 on a 16GB 4070 Ti SUPER, zimage sequential
    # offload WITH the vae-level tiling fix active (bounded-scope 3-point run):
    #   1024²=28s, 1448² peak 12467MB, 2048² peak 9534MB CUDA alloc;
    #   peak RSS flat 20.9-21.0GB at ALL THREE sizes (weights dominate — the old
    #   50-100GB RAM blowups were the ladder/unload leaks, not the canvas).
    # Tiled peaks are noisy but bounded WELL under 16GB, so slopes are modest:
    # they price bigger canvases without refusing tiled 2K on 16GB cards.
    # Override via GUAARDVARK_VRAM_SLOPE_MB_PER_MP / GUAARDVARK_RAM_SLOPE_GB_PER_MP.
    _FAMILY_VRAM_SLOPE_MB_PER_MP = {"krea2": 1000, "zimage": 500, "sdxl": 1500, "sd": 800}
    _FAMILY_RAM_SLOPE_GB_PER_MP = {"krea2": 1.0, "zimage": 1.0, "sdxl": 1.0, "sd": 0.5}

    @staticmethod
    def _extra_megapixels(width: Optional[int], height: Optional[int]) -> float:
        """Megapixels beyond the 1MP calibration point; 0 when dims unknown."""
        if not width or not height:
            return 0.0
        return max(0.0, (int(width) * int(height)) / 1_048_576.0 - 1.0)

    def _uses_grouped_query_attention(self) -> bool:
        """True when the loaded transformer has fewer KV heads than query heads.

        That mismatch is what disqualifies the fused SDPA kernels: flash and the
        mem-efficient kernel both refuse dense GQA, so a masked call falls through
        to math. Krea 2 is 48/12; Z-Image is 30/30 and unaffected. Read from the
        config rather than keyed on a model name so a future GQA model is covered
        without anyone remembering to add it.
        """
        cfg = getattr(getattr(self._pipeline, "transformer", None), "config", None)
        if cfg is None:
            return False
        q = getattr(cfg, "num_attention_heads", None) or getattr(cfg, "n_heads", None)
        kv = getattr(cfg, "num_key_value_heads", None) or getattr(cfg, "n_kv_heads", None)
        try:
            return bool(q and kv and int(kv) < int(q))
        except (TypeError, ValueError):
            return False

    def _will_use_sequential_for_krea2(self) -> bool:
        """True when krea2 loads with sequential CPU offload (≤18GB CUDA cards)."""
        if self._force_sequential_offload:
            return True
        total = self._cuda_total_vram_gb()
        return total > 0 and total <= self._SEQUENTIAL_OFFLOAD_VRAM_GB

    def _vram_estimate_mb(
        self, model_id: str, width: Optional[int] = None, height: Optional[int] = None
    ) -> int:
        # No dims ⇒ assume the 1MP calibration point ⇒ exactly the flat constants
        # (backward compatible with every estimate-only caller).
        if model_id in (None, "", "auto"):
            # Auto leads with zimage on consumer GPUs; worst-case is still ~11GB.
            # On roomy GPUs auto may pick krea2 — use the sequential/model peak.
            if self._prefer_krea2_for_auto():
                family, base = "krea2", self._FAMILY_VRAM_MB["krea2"]
            else:
                family, base = "zimage", self._OFFLOAD_TURBO_VRAM_MB
        else:
            family = self._model_family(model_id)
            if family == "flux":
                base = 12000
            elif family == "krea2" and self._will_use_sequential_for_krea2():
                base = self._KREA2_SEQUENTIAL_VRAM_MB
            else:
                base = self._FAMILY_VRAM_MB.get(family, 4000)
        extra_mp = self._extra_megapixels(width, height)
        if extra_mp > 0:
            try:
                slope = int(os.environ["GUAARDVARK_VRAM_SLOPE_MB_PER_MP"])
            except (KeyError, ValueError):
                slope = self._FAMILY_VRAM_SLOPE_MB_PER_MP.get(family, 1500)
            base += round(extra_mp * slope)
        return base

    def _ram_estimate_gb(
        self, model_id: str, width: Optional[int] = None, height: Optional[int] = None
    ) -> float:
        if model_id in (None, "", "auto"):
            family, base = "zimage", self._FAMILY_RAM_GB["zimage"]
        else:
            family = self._model_family(model_id)
            if family == "flux":
                base = 16.0
            else:
                base = self._FAMILY_RAM_GB.get(family, 6.0)
        extra_mp = self._extra_megapixels(width, height)
        if extra_mp > 0:
            try:
                slope = float(os.environ["GUAARDVARK_RAM_SLOPE_GB_PER_MP"])
            except (KeyError, ValueError):
                slope = self._FAMILY_RAM_SLOPE_GB_PER_MP.get(family, 1.0)
            base += extra_mp * slope
        return base

    def _ensure_vram_for_pipeline(
        self, model_id: str, width: Optional[int] = None, height: Optional[int] = None
    ) -> None:
        """Make room on the card BEFORE the pipeline load.

        Evicts Ollama, books ``sd:pipeline`` with hard_fit (refuse if still short).
        Raises RuntimeError when the orchestrator refuses admit — callers should
        surface that as a busy/retry, not CUDA OOM thrash. Pass the request dims
        so the booking prices >1MP canvases (2026-08-04).
        """
        if self._pipeline is not None and self._current_model == model_id:
            return  # already resident — its VRAM is already spent
        fault = self.gpu_fault_message()
        if fault:
            raise RuntimeError(fault)
        estimate_mb = self._vram_estimate_mb(model_id, width, height)
        # Probe/evict is best-effort: a failing CUDA query must not kill the
        # request (2026-08-04: pinned by test_admission_failure_never_raises —
        # only the orchestrator's hard_fit refusal below may raise). The one
        # exception is a context-killing CUDA error: the request is doomed and
        # so is every request after it, so say that instead of admitting.
        try:
            if self._device == "cuda" and torch.cuda.is_available():
                if self._pipeline is None:
                    import gc
                    gc.collect()
                    torch.cuda.empty_cache()
                free_b, total_b = torch.cuda.mem_get_info()
                free_mb, total_mb = free_b // (1024 * 1024), total_b // (1024 * 1024)
                margin_mb = max(1024, int(total_mb * 0.10))
                if free_mb - margin_mb < estimate_mb:
                    from backend.services.gpu_resource_policy import evict_ollama_models
                    logger.info(
                        f"VRAM admission: {free_mb}MB free won't fit {estimate_mb}MB "
                        f"(+{margin_mb}MB margin) for {model_id} — evicting Ollama models"
                    )
                    evict_ollama_models()
        except Exception as probe_err:
            if is_fatal_cuda_error(probe_err):
                raise RuntimeError(self._mark_gpu_fault(probe_err, "VRAM probe")) from probe_err
            logger.warning(
                f"VRAM probe/evict failed (continuing to orchestrator admit): {probe_err}"
            )
        from backend.services.gpu_memory_orchestrator import get_orchestrator
        # hard_fit=True: no "admit anyway" when free stays ~hundreds of MB
        get_orchestrator().request_model(
            "sd:pipeline",
            vram_estimate_mb=estimate_mb,
            priority=85,
            hard_fit=True,
        )

    def _ram_watchdog_callback(self):
        """Per-step denoise callback: abort before RAM exhaustion kills the box.

        2026-08-04: admission is a point-in-time check and its reservation
        expires while a job runs for minutes — nothing guarded the denoise
        itself. On breach we RAISE (never pipeline._interrupt: interrupt skips
        remaining steps but still runs the VAE decode — potentially the very
        allocation being fled). Floor: GUAARDVARK_MIN_FREE_RAM_GB, default
        max(8GB, 6% of total). Returns None when psutil is unavailable.
        """
        try:
            import psutil
        except Exception:
            return None
        try:
            floor_gb = float(os.environ["GUAARDVARK_MIN_FREE_RAM_GB"])
        except (KeyError, ValueError):
            floor_gb = max(8.0, 0.06 * psutil.virtual_memory().total / (1024 ** 3))

        def _cb(pipe, step, timestep, callback_kwargs):
            avail_gb = psutil.virtual_memory().available / (1024 ** 3)
            if avail_gb < floor_gb:
                logger.error(
                    f"RAM watchdog: {avail_gb:.1f}GB available < {floor_gb:.1f}GB "
                    f"floor at denoise step {step} — aborting this item before "
                    "the OOM killer / swap thrash takes the desktop"
                )
                raise RamWatchdogAbort(
                    f"System RAM fell to {avail_gb:.1f}GB free (floor "
                    f"{floor_gb:.1f}GB) during generation at step {step}. Aborted "
                    "to protect the machine — close other applications or reduce "
                    "resolution."
                )
            return callback_kwargs

        return _cb

    def _has_text_intent(self, prompt: str) -> bool:
        """True if the prompt asks for on-image text — bypass enhancement to keep
        spelling intact (HULK -> HUK otherwise). Shared detector lives in
        prompt_enhancer.has_text_intent so image + video stay in sync.
        """
        from backend.utils.prompt_enhancer import has_text_intent
        return has_text_intent(prompt)

    def _cuda_total_vram_gb(self) -> float:
        """Total device VRAM in GB, or 0 if CUDA is unavailable."""
        try:
            if self._device == "cuda" and torch.cuda.is_available():
                return torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
        except Exception:
            pass
        return 0.0

    def _seed_generator(self, seed: int) -> "torch.Generator":
        """Seeded RNG on the pipeline device, falling back to CPU when CUDA cannot
        be initialised (no device, driver mismatch). A CPU generator is accepted by
        every diffusers pipeline, so such a process still reaches admission and
        OOM handling instead of failing at RNG construction. On a working CUDA box
        the generator stays on the GPU, so seeds reproduce as before.
        """
        device = self._device
        if device == "cuda" and not torch.cuda.is_available():
            device = "cpu"
        try:
            return torch.Generator(device=device).manual_seed(seed)
        except RuntimeError:
            return torch.Generator().manual_seed(seed)

    @staticmethod
    def _is_cuda_oom(exc: BaseException) -> bool:
        if isinstance(exc, torch.cuda.OutOfMemoryError):
            return True
        msg = (str(exc) or "").lower()
        return "out of memory" in msg and ("cuda" in msg or "cublas" in msg or "cudnn" in msg)

    def _prefer_krea2_for_auto(self) -> bool:
        """Krea2 is aesthetic-first but ~14GB peak; only auto-lead on roomy GPUs."""
        return self._cuda_total_vram_gb() >= 20.0

    def _auto_select_model(self, prompt: str, style: str = "realistic") -> str:
        """Pick the best DOWNLOADED model for this prompt (chat auto-router).

        Intent-ordered preferences, best first; always falls through to a model
        that actually exists on disk. Z-Image-Turbo leads on ≤16–18GB cards —
        strongest prompt adherence per VRAM. Krea2 leads only on ≥20GB GPUs.
        """
        detection = self.detect_content_type(prompt)
        p = prompt.lower()
        lead = "krea2-turbo" if self._prefer_krea2_for_auto() else "zimage-turbo"
        second = "zimage-turbo" if lead == "krea2-turbo" else "krea2-turbo"

        if detection.get("has_face") and detection.get("has_person"):
            prefs = [lead, second, "realistic-vision", "epic-realism", "sd-xl"]
        elif detection.get("has_person"):
            prefs = [lead, second, "sd-xl", "realistic-vision", "epic-realism"]
        elif any(w in p for w in ("anime", "manga", "cartoon", "illustration", "comic")):
            prefs = [lead, second, "sd-xl"]
        elif detection.get("recommended_preset") in ("landscape", "product_photo"):
            prefs = [lead, second, "sd-xl", "epic-realism"]
        else:  # general / complex
            prefs = [lead, second, "sd-xl", "realistic-vision"]

        for key in prefs:
            model_id = self.available_models.get(key)
            if model_id and self._is_model_downloaded(model_id):
                logger.info(f"Auto-router selected '{key}' for prompt: {prompt[:60]}...")
                return key

        # Nothing preferred is downloaded — fall back to any downloaded model.
        for key, model_id in self.available_models.items():
            if key in self.comfy_only_models:
                continue  # sentinel id, nothing on disk to load
            if self._is_model_downloaded(model_id):
                return key
        # Genuinely nothing on disk. Returning a model that isn't there would only
        # push the failure downstream, so let the caller report it honestly.
        return None

    def _oom_fallback_catalog_key(self, failed_key: str) -> Optional[str]:
        """Next model to try after a CUDA OOM on failed_key (catalog key or HF id)."""
        failed_family = self._model_family(failed_key)
        # Prefer lighter DiT, then SDXL, then classic SD — only if present on disk.
        candidates = []
        if failed_family == "krea2":
            candidates = ["krea2-turbo", "zimage-turbo", "sd-xl", "realistic-vision"]
        elif failed_family == "zimage":
            candidates = ["sd-xl", "realistic-vision"]
        else:
            candidates = ["sd-xl", "realistic-vision"]
        failed_resolved = self._resolve_model_ref(failed_key)
        for key in candidates:
            mid = self.available_models.get(key)
            if not mid or mid == failed_resolved or key == failed_key:
                continue
            if self._is_model_downloaded(mid):
                return key
        return None

    def _soft_clamp_family_sampling(self, request: ImageGenerationRequest, family: str) -> None:
        """Clamp steps/guidance into a safe family envelope without clobbering user presets.

        Used on the primary generate path so Batch UI High / slider values actually run.
        Hard defaults live in ``_apply_family_sampling`` (fallback / family switch only).
        """
        if family == "zimage":
            # Official HF: 9 steps / guidance 0. Soft envelope matches settings_validator.
            steps = int(request.num_inference_steps or 0)
            if steps < 4 or steps > 30:
                request.num_inference_steps = 9
            else:
                request.num_inference_steps = steps
            try:
                g = float(request.guidance_scale)
            except (TypeError, ValueError):
                g = -1.0
            if g < 0.0 or g > 2.0:
                request.guidance_scale = 0.0
            else:
                request.guidance_scale = g
        elif family == "krea2":
            if self._krea2_variant(request.model or "") == "raw":
                steps = int(request.num_inference_steps or 0)
                if steps < 20 or steps > 80:
                    request.num_inference_steps = 52
                else:
                    request.num_inference_steps = steps
                try:
                    g = float(request.guidance_scale)
                except (TypeError, ValueError):
                    g = -1.0
                if g < 1.0 or g > 7.0:
                    request.guidance_scale = 3.5
                else:
                    request.guidance_scale = g
            else:
                steps = int(request.num_inference_steps or 0)
                if steps < 4 or steps > 20:
                    request.num_inference_steps = 8
                else:
                    request.num_inference_steps = steps
                try:
                    g = float(request.guidance_scale)
                except (TypeError, ValueError):
                    g = -1.0
                if g < 0.0 or g > 1.0:
                    request.guidance_scale = 0.0
                else:
                    request.guidance_scale = g

    def _apply_family_sampling(self, request: ImageGenerationRequest, family: str) -> None:
        """Force family-appropriate steps/guidance after model switch or fallback."""
        if family == "krea2":
            if self._krea2_variant(request.model or "") == "raw":
                request.num_inference_steps = 52
                request.guidance_scale = 3.5
            else:
                request.num_inference_steps = 8
                request.guidance_scale = 0.0
        elif family == "zimage":
            # Official HF recipe (9 steps → 8 DiT forwards, CFG distilled → 0.0)
            request.num_inference_steps = 9
            request.guidance_scale = 0.0
        elif family == "sdxl":
            if request.guidance_scale > 9.0:
                request.guidance_scale = 7.5
            elif request.guidance_scale < 4.0:
                request.guidance_scale = 6.0
            if request.num_inference_steps < 20:
                request.num_inference_steps = 25
        else:
            if request.guidance_scale < 4.0:
                request.guidance_scale = 7.5
            if request.num_inference_steps < 15:
                request.num_inference_steps = 20

    # Model repos ship a sample-image gallery, a licence PDF and a README alongside
    # the weights — 39 of Krea 2 Turbo's 55 files. Diffusers never reads any of it, and
    # on a flaky link those JPEGs were what actually broke the download (2026-08-07).
    _SNAPSHOT_IGNORE_PATTERNS = [
        "images/*", "*.pdf", "*.md",
        "*.jpg", "*.jpeg", "*.png", "*.gif", "*.webp",
    ]

    def _redundant_root_checkpoints(self, model_id: str) -> List[str]:
        """Root-level single-file checkpoints that duplicate the sharded layout.

        Some repos ship both: the diffusers component folders AND one consolidated
        checkpoint for single-file loaders. Krea 2 Turbo carries `turbo.safetensors`
        at 26.28 GB — on top of the 26 GB of `transformer/` shards it duplicates.
        `from_pretrained(folder)` reads model_index.json and the component
        subdirectories and never touches the root file, so downloading it doubles the
        transfer for nothing. It is also why the Settings progress bar sat at
        "37.9 / 28.0 GB — 99%" (observed 2026-08-07): the denominator counted only the
        weights the pipeline needs, while the transfer kept going.

        Returns [] for single-file repos, where that checkpoint IS the model.
        """
        try:
            from huggingface_hub import HfApi
            files = HfApi().list_repo_files(model_id, token=self._hf_token())
        except Exception as e:
            logger.debug(f"Could not list files for {model_id}: {e}")
            return []
        if "model_index.json" not in files:
            return []
        return [
            f for f in files
            if "/" not in f and f.endswith((".safetensors", ".ckpt", ".pt", ".bin"))
        ]

    def _snapshot_with_retry(self, model_id: str, model_path: Path) -> tuple[bool, str | None]:
        """snapshot_download with weight-only filtering and resume-on-failure.

        Hugging Face drops connections mid-transfer often enough that a multi-GB
        pull rarely completes first try ("peer closed connection", "Server
        disconnected"). huggingface_hub retries individual files, but when it gives
        up the whole call raises and the model is left half-fetched. Already-complete
        files are skipped on re-entry, so simply going round again is cheap and
        usually finishes the job.
        """
        from huggingface_hub import snapshot_download

        attempts = int(os.environ.get("GUAARDVARK_HF_DOWNLOAD_ATTEMPTS", "4"))
        # Fewer parallel connections are steadier on a saturated or throttled link.
        workers = int(os.environ.get("GUAARDVARK_HF_DOWNLOAD_WORKERS", "4"))

        ignore = list(self._SNAPSHOT_IGNORE_PATTERNS)
        redundant = self._redundant_root_checkpoints(model_id)
        if redundant:
            logger.info(
                f"Skipping redundant single-file checkpoint(s) for {model_id}: "
                f"{', '.join(redundant)} — the sharded component folders are what "
                "diffusers loads"
            )
            ignore.extend(redundant)

        last_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                snapshot_download(
                    repo_id=model_id,
                    local_dir=str(model_path),
                    ignore_patterns=ignore,
                    max_workers=workers,
                )
                if attempt > 1:
                    logger.info(f"{model_id} download completed on attempt {attempt}")
                return True, None
            except Exception as e:
                last_error = e
                if attempt >= attempts:
                    break
                backoff = min(30, 2 ** attempt)
                logger.warning(
                    f"{model_id} download attempt {attempt}/{attempts} failed ({e}); "
                    f"resuming in {backoff}s — completed files are kept"
                )
                time.sleep(backoff)

        msg = (
            f"Download of {model_id} failed after {attempts} attempts: {last_error}. "
            "Partial files were kept, so retrying will resume where it stopped."
        )
        logger.error(msg)
        return False, msg

    def _download_model(self, model_id: str) -> tuple[bool, str | None]:
        if not self.service_available:
            msg = "Diffusion service not available for model download"
            logger.error(msg)
            return False, msg

        try:
            model_path = self._get_model_path(model_id)

            # LOUD first-run banner: this download is multi-GB and used to be
            # invisible outside a raw HF progress bar in backend_startup.log —
            # on the 24.04 client box install it crawled at ~33s/file (unauthenticated
            # + saturated link) and looked exactly like a frozen boot.
            hf_token_set = bool(
                os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
            )
            logger.warning(
                "FIRST-RUN MODEL DOWNLOAD: %s -> %s. This is a one-time multi-GB "
                "download; the backend is NOT frozen — progress is logged here and "
                "image generation stays unavailable until it finishes.%s",
                model_id,
                model_path,
                (
                    ""
                    if hf_token_set
                    else " Downloads are UNAUTHENTICATED and Hugging Face may "
                    "rate-limit them — set HF_TOKEN in .env for faster downloads."
                ),
            )

            family = self._model_family(model_id)

            # Large DiT pipelines (Krea2, Z-Image): snapshot only — do not instantiate
            # during download. from_pretrained() loads weights into RAM and fails on
            # Krea2 with transformers<5.2 (tokenizer vocab + Qwen3-VL rope_parameters).
            if family in ('krea2', 'zimage'):
                if family == 'krea2' and Krea2Pipeline is None:
                    msg = "Krea 2 requested but Krea2Pipeline unavailable (upgrade diffusers >= 0.39)"
                    logger.error(msg)
                    return False, msg
                if family == 'zimage' and ZImagePipeline is None:
                    msg = "Z-Image requested but ZImagePipeline unavailable (upgrade diffusers >= 0.38)"
                    logger.error(msg)
                    return False, msg
                from huggingface_hub import snapshot_download
                logger.info(f"Snapshot-downloading {model_id} (family={family})")
                # local_dir_use_symlinks was removed: deprecated and ignored by
                # current huggingface_hub (it warned on every download).
                ok, msg = self._snapshot_with_retry(model_id, model_path)
                if not ok:
                    return False, msg
                if not self._is_model_downloaded(model_id):
                    msg = f"Snapshot download finished but {model_path} has no model_index.json"
                    logger.error(msg)
                    return False, msg
                logger.info(f"Model {model_id} downloaded successfully (snapshot)")
                return True, None

            elif family == 'sdxl':
                pipeline_class = StableDiffusionXLPipeline
            else:
                pipeline_class = StableDiffusionPipeline

            # Use bf16 on Ada Lovelace+, fp16 otherwise
            if self._device == "cuda":
                gpu_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            else:
                gpu_dtype = torch.float32

            load_kwargs = {
                "torch_dtype": gpu_dtype,
            }

            # safety_checker kwargs only exist on the classic SD pipeline.
            if family == 'sd':
                load_kwargs["safety_checker"] = None
                load_kwargs["requires_safety_checker"] = False

            logger.info(f"Downloading with {pipeline_class.__name__} (family: {family})")

            pipeline = pipeline_class.from_pretrained(
                model_id,
                **load_kwargs
            )

            pipeline.save_pretrained(model_path)

            del pipeline
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            logger.info(f"Model {model_id} downloaded successfully")
            return True, None

        except Exception as e:
            logger.exception(f"Failed to download model {model_id}: {e}")
            return False, str(e)

    # 2026-08-04 client box 2048² incident: ZImagePipeline/Krea2Pipeline don't inherit
    # StableDiffusionMixin, so the old pipeline-level hasattr checks for
    # enable_vae_slicing/enable_vae_tiling silently no-opped for EXACTLY the two
    # families allowed to reach 2K — the 2048² latent decoded whole-tensor, OOM'd
    # the card, and the recovery ladder exhausted system RAM until the desktop
    # died. The capability lives one level down on the autoencoder itself
    # (AutoencoderMixin); offline_video_generator.py has always used the
    # vae-level fallback. These helpers try pipeline level first, then vae level.

    def _enable_vae_slicing_any(self) -> None:
        """Enable VAE slicing at whichever level this pipeline exposes it."""
        pipeline = self._pipeline
        self._vae_slicing_enabled = False
        try:
            if hasattr(pipeline, "enable_vae_slicing"):
                pipeline.enable_vae_slicing()
                self._vae_slicing_enabled = True
                logger.info("Enabled VAE slicing (pipeline level)")
            elif hasattr(getattr(pipeline, "vae", None), "enable_slicing"):
                pipeline.vae.enable_slicing()
                self._vae_slicing_enabled = True
                logger.info("Enabled VAE slicing (vae level)")
            else:
                logger.warning("VAE slicing unavailable at either level for this pipeline")
        except Exception as e:
            logger.error(f"Failed to enable VAE slicing: {e}")

    def _set_vae_tiling(self, enabled: bool) -> bool:
        """Enable/disable VAE tiling at whichever level exists. True if applied."""
        pipeline = self._pipeline
        vae = getattr(pipeline, "vae", None)
        try:
            if hasattr(pipeline, "enable_vae_tiling"):
                if enabled:
                    pipeline.enable_vae_tiling()
                elif hasattr(pipeline, "disable_vae_tiling"):
                    pipeline.disable_vae_tiling()
                return True
            if hasattr(vae, "enable_tiling"):
                if enabled:
                    vae.enable_tiling()
                elif hasattr(vae, "disable_tiling"):
                    vae.disable_tiling()
                return True
        except Exception as e:
            logger.error(f"Failed to {'enable' if enabled else 'disable'} VAE tiling: {e}")
            return False
        return False

    def _load_pipeline(self, model_id: str, *, force_sequential: bool = False) -> bool:
        if not self.service_available:
            return False
        if self._gpu_fault is not None:
            logger.error(
                "Refusing to load %s: %s", model_id, self.gpu_fault_message()
            )
            return False

        try:
            want_sequential = bool(force_sequential or self._force_sequential_offload)
            if (
                self._pipeline
                and self._current_model == model_id
                and (not want_sequential or self._pipeline_offload_mode == "sequential")
            ):
                return True

            if self._pipeline:
                self._unload_pipeline()

            if not self._is_model_downloaded(model_id):
                logger.info(f"Model {model_id} not found locally, downloading...")
                ok, dl_err = self._download_model(model_id)
                if not ok:
                    if dl_err:
                        logger.error(f"Download failed for {model_id}: {dl_err}")
                    return False

            model_path = self._get_model_path(model_id)

            family = self._model_family(model_id)
            if family == 'krea2':
                if Krea2Pipeline is None:
                    logger.error("Krea 2 requested but Krea2Pipeline unavailable (upgrade diffusers >= 0.39)")
                    return False
                pipeline_class = Krea2Pipeline
            elif family == 'zimage':
                pipeline_class = ZImagePipeline
            elif family == 'sdxl':
                pipeline_class = StableDiffusionXLPipeline
            else:
                pipeline_class = StableDiffusionPipeline
            logger.info(f"Loading model with {pipeline_class.__name__} (family: {family})")

            # Use bf16 on Ada Lovelace+ (SM 8.x), fall back to fp16, then fp32
            if self._device == "cuda":
                if torch.cuda.is_bf16_supported():
                    gpu_dtype = torch.bfloat16
                    logger.info("Using bfloat16 (native Ada Lovelace support)")
                else:
                    gpu_dtype = torch.float16
                    logger.info("Using float16")
            else:
                gpu_dtype = torch.float32

            load_kwargs = {
                "torch_dtype": gpu_dtype,
            }

            if family == 'sd':
                load_kwargs["safety_checker"] = None
                load_kwargs["requires_safety_checker"] = False

            self._pipeline = pipeline_class.from_pretrained(
                model_path,
                **load_kwargs
            )

            # Flow-matching DiTs ship their own scheduler — don't force DPM (SD/SDXL only).
            if family not in ('zimage', 'krea2'):
                self._pipeline.scheduler = DPMSolverMultistepScheduler.from_config(
                    self._pipeline.scheduler.config
                )

            # Z-Image / Krea 2 are flow-matching DiTs too large to sit fully resident
            # on a 16 GB card. Both use sequential (layer-by-layer) offload by default
            # on consumer VRAM (≤18GB) so first-pass inference does not OOM and thrash
            # into a doomed sd-xl fallback. Force sequential still wins when set.
            offload_mode = "full"
            if family in ('zimage', 'krea2') and self._device == "cuda":
                total_gb = self._cuda_total_vram_gb()
                use_sequential = (
                    want_sequential
                    or (total_gb > 0 and total_gb <= self._SEQUENTIAL_OFFLOAD_VRAM_GB)
                )
                if use_sequential:
                    try:
                        self._pipeline.enable_sequential_cpu_offload()
                        offload_mode = "sequential"
                        logger.info(
                            f"{family}: enabled sequential CPU offload "
                            f"(VRAM={total_gb:.1f}GB, 16GB-safe path)"
                        )
                    except Exception as e:
                        logger.warning(
                            f"{family} sequential CPU offload unavailable ({e}); "
                            f"trying model offload"
                        )
                        use_sequential = False
                if offload_mode != "sequential":
                    try:
                        self._pipeline.enable_model_cpu_offload()
                        offload_mode = "model"
                        logger.info(f"{family}: enabled model CPU offload (16 GB VRAM safety)")
                    except Exception as e:
                        logger.warning(f"{family} CPU offload unavailable ({e}); loading fully on GPU")
                        self._pipeline = self._pipeline.to(self._device)
                        offload_mode = "full"
            else:
                self._pipeline = self._pipeline.to(self._device)
                offload_mode = "full"

            self._pipeline_offload_mode = offload_mode
            # Clear one-shot force after a successful sequential (or attempted) load.
            self._force_sequential_offload = False

            # channels_last speeds full-GPU UNet paths, but calling .to(channels_last)
            # on accelerate-offloaded DiT transformers can materialize weights on GPU
            # and defeat offload — skip for sequential/model offload modes.
            if self._device == "cuda" and offload_mode == "full":
                if hasattr(self._pipeline, 'unet'):
                    self._pipeline.unet = self._pipeline.unet.to(memory_format=torch.channels_last)
                    logger.info("Enabled channels_last (NHWC) memory format for UNet")
                elif hasattr(self._pipeline, 'transformer'):
                    self._pipeline.transformer = self._pipeline.transformer.to(
                        memory_format=torch.channels_last
                    )
                    logger.info("Enabled channels_last (NHWC) memory format for transformer")

            # Attention backend for grouped-query DiTs (2026-08-24 incident).
            #
            # Krea 2 is 48 query heads / 12 KV heads, and its processor forwards the
            # pipeline's text padding mask into SDPA. On torch 2.5.1 flash refuses a
            # mask and the mem-efficient kernel refuses the GQA head mismatch, so the
            # dispatch silently lands on MATH — which materializes the whole
            # [1, heads, S, S] score matrix. At 1024² (S=4609) that is ~3.8GB and
            # survives; at 2048² (S=16897) it is **51.05GB** and the batch dies on
            # denoise step 0, on a card with 10.8GB free. Measured on this box, same
            # shapes: math 51.05GB (OOM) vs cuDNN 794MB vs no-mask flash 499MB.
            #
            # cuDNN attention takes both the mask and the GQA shape, so name it
            # explicitly rather than leaving the choice to a fallback chain that
            # picks the one kernel that cannot do this. Z-Image is 30/30 heads (no
            # GQA) and never hit this; setting the backend is harmless there.
            self._attention_backend_active = None
            _transformer = getattr(self._pipeline, "transformer", None)
            if _transformer is not None and hasattr(_transformer, "set_attention_backend"):
                for _backend in ("_native_cudnn", "_native_efficient", "_native_flash"):
                    try:
                        _transformer.set_attention_backend(_backend)
                        self._attention_backend_active = _backend
                        logger.info("Attention backend set to %s for %s", _backend, family)
                        break
                    except Exception as e:  # noqa: BLE001
                        logger.debug("Attention backend %s unavailable: %s", _backend, e)
                if self._attention_backend_active is None:
                    logger.warning(
                        "No explicit attention backend could be set for %s; large "
                        "canvases may fall back to the quadratic math kernel", family,
                    )

            if hasattr(self._pipeline, "enable_attention_slicing"):
                # No-op for DiT transformers (it drives UNet set_attention_slice), but
                # still correct for the SD/SDXL pipelines that share this path.
                self._pipeline.enable_attention_slicing()

            if hasattr(self._pipeline, "enable_xformers_memory_efficient_attention"):
                try:
                    self._pipeline.enable_xformers_memory_efficient_attention()
                    logger.info("Enabled xformers memory efficient attention")
                except Exception as e:
                    if not self._xformers_warned:
                        self._xformers_warned = True
                        logger.info(
                            "xformers memory-efficient attention unavailable "
                            "(using default attention): %s",
                            e,
                        )
                    else:
                        logger.debug("xformers still unavailable: %s", e)

            self._enable_vae_slicing_any()

            # VAE tiling only at high resolutions (>1024px) — avoids quality loss at
            # normal sizes. Availability computed across BOTH levels (see the
            # 2026-08-04 incident note at _enable_vae_slicing_any).
            _vae = getattr(self._pipeline, "vae", None)
            self._vae_tiling_via = (
                "pipeline" if hasattr(self._pipeline, "enable_vae_tiling")
                else "vae" if hasattr(_vae, "enable_tiling")
                else None
            )
            self._vae_tiling_available = self._vae_tiling_via is not None
            if self._vae_tiling_available:
                logger.info(
                    f"VAE tiling available via {self._vae_tiling_via} level "
                    "(will activate for resolutions > 1024px)"
                )
            else:
                logger.error(
                    "VAE tiling NOT available at either level for this pipeline — "
                    ">1MP DiT requests will be refused instead of risking an "
                    "untiled decode"
                )

            # torch.compile(mode='reduce-overhead') uses CUDA graphs which allocate
            # persistent IPC semaphores. When the pipeline is later moved to CPU and
            # torch.cuda.empty_cache() is called, those semaphores leak — leaving the
            # process in a state where Python's interpreter shutdown fires
            # `resource_tracker: leaked semaphore` warnings and the process eventually
            # aborts. This is a known PyTorch issue. Observed killing the backend on
            # 2026-04-11 (PIDs 3047360, 3065470, 3074584).
            #
            # DISABLED BY DEFAULT. Set GUAARDVARK_ENABLE_TORCH_COMPILE=1 to re-enable
            # if/when PyTorch fixes the underlying CUDA graph cleanup bug.
            if (
                os.environ.get("GUAARDVARK_ENABLE_TORCH_COMPILE") == "1"
                and hasattr(torch, 'compile')
                and self._device == "cuda"
                and not self._compile_failed
                and offload_mode == "full"  # compile + offload hooks do not mix well
            ):
                try:
                    if hasattr(self._pipeline, 'unet'):
                        self._compile_unet_orig = self._pipeline.unet
                        self._pipeline.unet = torch.compile(self._pipeline.unet, mode="reduce-overhead")
                        logger.info("Enabled torch.compile(mode='reduce-overhead') for UNet")

                    if hasattr(self._pipeline, 'vae'):
                        self._compile_vae_orig = self._pipeline.vae
                        self._pipeline.vae = torch.compile(self._pipeline.vae, mode="reduce-overhead")
                        logger.info("Enabled torch.compile(mode='reduce-overhead') for VAE")
                except Exception as e:
                    logger.warning(f"Failed to enable torch.compile: {e}")
                    self._compile_unet_orig = None
                    self._compile_vae_orig = None

            self._current_model = model_id
            logger.info(
                f"Pipeline loaded successfully with model {model_id} "
                f"(offload={offload_mode})"
            )
            try:
                from backend.services.gpu_memory_orchestrator import get_orchestrator
                get_orchestrator().mark_model_loaded("sd:pipeline")
            except Exception:
                pass
            return True

        except Exception as e:
            logger.error(f"Failed to load pipeline with model {model_id}: {e}")
            if is_fatal_cuda_error(e):
                self._mark_gpu_fault(e, "pipeline load")
            self._pipeline = None
            self._current_model = None
            self._pipeline_offload_mode = None
            return False

    def _detect_subject_count(self, prompt: str) -> Dict[str, Any]:
        prompt_lower = prompt.lower()

        # Word-boundary matching throughout. The old substring test matched the
        # plural "men" inside "element"/"embellishment", flagging single-person
        # costume prompts as multi-person scenes — which then steered enhancement
        # toward group phrasing and produced multi-character images.
        def _has_word(terms: List[str]) -> bool:
            return any(
                re.search(r"\b" + re.escape(t) + r"\b", prompt_lower) for t in terms
            )

        single_indicators = ['a', 'an', 'one', 'single', 'solo', 'alone', 'lone']
        multiple_indicators = ['two', 'three', 'four', 'multiple', 'several', 'many',
                               'group of', 'couple', 'pair of', 'crowd', 'trio', 'duo']

        has_single = _has_word(single_indicators)
        has_multiple = _has_word(multiple_indicators)

        person_plurals = ['men', 'women', 'people', 'workers', 'builders', 'chefs', 'doctors',
                         'teachers', 'children', 'boys', 'girls', 'employees', 'professionals']
        has_plural_subject = _has_word(person_plurals)

        person_singulars = ['man', 'woman', 'person', 'child', 'boy', 'girl']
        has_and_conjunction = False
        if ' and ' in prompt_lower:
            distinct_singulars = [s for s in person_singulars if _has_word([s])]
            if len(distinct_singulars) > 1:
                has_and_conjunction = True

        if has_multiple or has_plural_subject or has_and_conjunction:
            subject_count = "multiple"
        elif has_single:
            subject_count = "single"
        else:
            subject_count = "single"

        return {
            "subject_count": subject_count,
            "is_single_subject": subject_count == "single",
            "is_multiple_subjects": subject_count == "multiple"
        }

    def detect_content_type(self, prompt: str) -> Dict[str, Any]:
        prompt_lower = prompt.lower()

        detection = {
            "has_person": False,
            "has_face": False,
            "has_hands": False,
            "has_action": False,
            "has_interaction": False,
            "has_spatial": False,
            "detected_actions": [],
            "recommended_preset": "general",
            "warnings": [],
            "subject_count_info": {}
        }

        detection["subject_count_info"] = self._detect_subject_count(prompt)

        person_words = ['man', 'woman', 'person', 'people', 'worker', 'builder', 'chef', 'doctor',
                       'teacher', 'child', 'boy', 'girl', 'human', 'employee', 'staff', 'professional',
                       'craftsman', 'mechanic', 'plumber', 'electrician', 'carpenter', 'painter']
        if any(word in prompt_lower for word in person_words):
            detection["has_person"] = True

        face_words = ['portrait', 'face', 'headshot', 'selfie', 'close-up', 'closeup', 'head shot']
        if any(word in prompt_lower for word in face_words):
            detection["has_face"] = True

        hand_words = ['hand', 'holding', 'grabbing', 'gripping', 'carrying', 'lifting', 'pointing',
                     'touching', 'typing', 'writing', 'drawing', 'using']
        if any(word in prompt_lower for word in hand_words):
            detection["has_hands"] = True

        action_map = {
            'building': ['building', 'constructing', 'assembling', 'installing', 'fixing', 'repairing'],
            'working': ['working', 'operating', 'using', 'handling'],
            'cooking': ['cooking', 'baking', 'preparing food', 'chef', 'kitchen'],
            'driving': ['driving', 'steering', 'riding', 'in car', 'behind wheel'],
            'typing': ['typing', 'at computer', 'at keyboard', 'coding', 'programming'],
            'reading': ['reading', 'studying', 'with book', 'looking at'],
            'sports': ['playing', 'running', 'jumping', 'swimming', 'exercising', 'training', 'jogging', 'treadmill', 'workout'],
            'gardening': ['gardening', 'planting', 'watering', 'pruning', 'mowing']
        }

        for action_type, keywords in action_map.items():
            if any(keyword in prompt_lower for keyword in keywords):
                detection["has_action"] = True
                detection["detected_actions"].append(action_type)

        interaction_words = ['with', 'using', 'holding', 'beside', 'operating', 'gripping', 'manipulating']
        if detection["has_person"] and any(word in prompt_lower for word in interaction_words):
            detection["has_interaction"] = True

        spatial_words = ['next to', 'behind', 'in front of', 'beside', 'between', 'under', 'over',
                        'sitting on', 'standing by', 'leaning against', 'near']
        if any(word in prompt_lower for word in spatial_words):
            detection["has_spatial"] = True

        if detection["has_face"] and detection["has_person"]:
            detection["recommended_preset"] = "person_portrait"
        elif detection["has_person"] and 'sports' in detection["detected_actions"]:
            detection["recommended_preset"] = "person_athletic"
        elif detection["has_person"] and detection["has_action"]:
            detection["recommended_preset"] = "person_working"
        elif detection["has_person"]:
            detection["recommended_preset"] = "person_full_body"
        elif any(word in prompt_lower for word in ['landscape', 'scenery', 'nature', 'mountain', 'beach', 'forest', 'sunset', 'sunrise']):
            detection["recommended_preset"] = "landscape"
        elif any(word in prompt_lower for word in ['product', 'item', 'object', 'merchandise', 'bottle', 'package']):
            detection["recommended_preset"] = "product_photo"
        elif any(word in prompt_lower for word in ['infographic', 'diagram', 'chart', 'icon', 'vector', 'flat']):
            detection["recommended_preset"] = "infographic_preset"

        if detection["has_person"] and detection["has_hands"] and detection["has_action"]:
            detection["warnings"].append("Complex scene with person + hands + action may require multiple attempts")
        if len(detection["detected_actions"]) > 1:
            detection["warnings"].append("Multiple actions detected - simpler prompts often yield better results")

        return detection

    def enhance_prompt_for_quality(self, prompt: str, style: str = "realistic",
                                   content_preset: Optional[str] = None,
                                   auto_enhance: bool = True,
                                   enhance_anatomy: bool = True,
                                   enhance_faces: bool = True,
                                   enhance_hands: bool = True,
                                   family: str = "") -> Tuple[str, str, Dict[str, Any]]:
        logger.debug(
            "Image prompt enhancement started "
            f"(prompt_len={len(prompt)}, auto_enhance={auto_enhance})"
        )
        detection = self.detect_content_type(prompt)

        preset_name = content_preset or detection["recommended_preset"]
        preset = self.content_presets.get(preset_name, self.content_presets["general"])
        style_config = self.style_configs.get(style, self.style_configs["realistic"])

        # (priority, text) — priority decides what survives when a long-context
        # encoder budget forces a re-fit: 0 = subject/coherence guards,
        # 1 = anatomy/scene logic, 2 = preset, 3 = style boilerplate.
        enhancements = []
        negative_parts = []

        enhancements.append((3, style_config.get("positive_suffix", "")))
        enhancements.append((2, preset.get("positive_suffix", "")))

        negative_parts.append(self.base_negative)
        negative_parts.append(style_config.get("negative_prompt", ""))
        negative_parts.append(preset.get("negative_prompt", ""))

        if auto_enhance:
            is_single_subject = detection.get("subject_count_info", {}).get("is_single_subject", True)

            if detection["has_person"] and is_single_subject:
                # Explicit count anchor: long descriptive prompts without one read
                # as lookbook/catalog copy and render several figures (Krea Raw).
                enhancements.append((0, "solo, only one person"))

            if detection["has_person"] and enhance_anatomy:
                enhancements.append((1, "correct human proportions, realistic anatomy, proper body structure"))
                negative_parts.append(self.anatomy_negative)

            if detection["has_face"] and enhance_faces:
                enhancements.append((1, "detailed facial features, symmetrical face, natural expression"))
                negative_parts.append(self.face_negative)

            if detection["has_hands"] and enhance_hands:
                enhancements.append((1, "correctly drawn hands, proper finger count, natural hand position"))
                negative_parts.append(self.hands_negative)

            action_enhancements = {
                'building': ['construction scene', 'realistic work pose', 'focused activity'],
                'working': ['realistic work environment', 'logical positioning', 'professional setting'],
                'cooking': ['kitchen scene', 'realistic cooking pose', 'culinary activity'],
                'driving': ['hands on steering wheel', 'seated in vehicle', 'vehicle interior'],
                'typing': ['fingers on keyboard', 'seated at desk', 'office setting'],
                'reading': ['natural reading pose', 'focused attention'],
                'sports': ['athletic pose', 'dynamic movement', 'active motion'],
                'gardening': ['outdoor setting', 'natural environment', 'gardening activity']
            }

            for action in detection["detected_actions"]:
                if action in action_enhancements:
                    enhancements.extend((1, e) for e in action_enhancements[action])
                    negative_parts.append(f"floating objects, illogical {action}")

            if detection["has_spatial"]:
                enhancements.append((1, "correct spatial relationships, logical positioning, proper depth, consistent perspective"))
                negative_parts.append("wrong perspective, floating objects, incorrect scale, impossible physics")

            if detection["has_interaction"] and not is_single_subject:
                enhancements.append((1, "realistic interaction, natural positioning"))
                negative_parts.append("awkward poses, impossible poses")

            enhancements.append((0, "coherent scene, logical composition, consistent lighting, unified style"))
            negative_parts.append("inconsistent elements, mixed styles, impossible scene, conflicting perspectives")

        unique_enhancements = []
        seen = set()
        for _prio, e in enhancements:
            e_clean = e.strip()
            if e_clean and e_clean.lower() not in seen:
                seen.add(e_clean.lower())
                unique_enhancements.append(e_clean)

        enhanced_prompt = f"{prompt}, {', '.join(unique_enhancements)}"

        # Long-context encoders (Krea2/Z-Image) hard-cap at 512 tokens and the
        # tokenizer truncates SILENTLY — on long user prompts the tail that fell
        # off was exactly the coherence/subject guards appended above. Re-fit so
        # the user's prompt always survives intact and suffixes are kept in
        # priority order only while they still fit.
        if (family or "").lower() in self._LONG_CONTEXT_FAMILIES:
            enhanced_prompt = self._fit_enhancements_to_token_budget(
                prompt, enhancements, enhanced_prompt
            )
        logger.debug(
            f"Image prompt enhancement complete (enhanced_prompt_len={len(enhanced_prompt)})"
        )

        unique_negatives = []
        seen_neg = set()
        for n in negative_parts:
            for part in n.split(', '):
                part_clean = part.strip()
                if part_clean and part_clean.lower() not in seen_neg:
                    seen_neg.add(part_clean.lower())
                    unique_negatives.append(part_clean)

        negative_prompt = ", ".join(unique_negatives)

        detection["preset_used"] = preset_name
        detection["style_used"] = style
        detection["enhancements_applied"] = unique_enhancements

        return enhanced_prompt, negative_prompt, detection

    def _enhance_prompt(self, prompt: str, style: str) -> Tuple[str, str]:
        """Light style packaging only. Prefer generate_image's auto_enhance path for
        full quality stuffing; this helper must NOT re-run auto_enhance=True when the
        caller already chose auto_enhance=False (that was a silent re-enhance bug)."""
        style_config = self.style_configs.get(style, self.style_configs.get("realistic", {}))
        return prompt, style_config.get("negative_prompt", "") or ""

    # Krea2Pipeline / Z-Image default max_sequence_length. Their tokenizers
    # truncate anything past this without a warning.
    _LONG_CONTEXT_FAMILIES = ("krea2", "zimage")
    _LONG_CONTEXT_TOKEN_LIMIT = 512

    def _count_prompt_tokens(self, text: str) -> int:
        """Token count via the loaded pipeline's tokenizer when available;
        conservative prose estimate otherwise (~1.5 tokens/word — measured 1.34
        on real long prompts, so the estimate errs toward trimming earlier)."""
        tokenizer = getattr(getattr(self, "_pipeline", None), "tokenizer", None)
        if tokenizer is not None:
            try:
                return len(tokenizer(text).input_ids)
            except Exception:
                pass
        return int(len(text.split()) * 1.5) + 1

    def _fit_enhancements_to_token_budget(
        self,
        prompt: str,
        prioritized_enhancements: List[Tuple[int, str]],
        fused_prompt: str,
    ) -> str:
        """Keep prompt + suffixes within the long-context encoder budget.

        The user's prompt is never trimmed. Suffix phrases are re-added
        greedily in priority order (subject/coherence guards first, style
        boilerplate last) while the total still fits.
        """
        budget = self._LONG_CONTEXT_TOKEN_LIMIT
        if self._count_prompt_tokens(fused_prompt) <= budget:
            return fused_prompt

        if self._count_prompt_tokens(prompt) >= budget:
            logger.warning(
                "User prompt alone is at/over the %d-token encoder limit "
                "(%d words) — enhancement suffixes skipped and the prompt tail "
                "will be truncated by the tokenizer. Shorten the prompt to "
                "keep its ending.",
                budget, len(prompt.split()),
            )
            return prompt

        kept: List[str] = []
        seen = set()
        for _prio, entry in sorted(prioritized_enhancements, key=lambda t: t[0]):
            for phrase in entry.split(', '):
                p_clean = phrase.strip()
                if not p_clean or p_clean.lower() in seen:
                    continue
                candidate = f"{prompt}, {', '.join(kept + [p_clean])}"
                if self._count_prompt_tokens(candidate) > budget:
                    continue
                seen.add(p_clean.lower())
                kept.append(p_clean)
        logger.info(
            "Enhanced prompt exceeded the %d-token encoder budget — kept %d "
            "suffix phrases (priority order), user prompt intact.",
            budget, len(kept),
        )
        return f"{prompt}, {', '.join(kept)}" if kept else prompt

    def _augment_krea2_raw_negatives(
        self, combined_negative: str, detection: Dict[str, Any]
    ) -> str:
        """Krea 2 Raw (base checkpoint, full CFG so negatives DO apply) reads
        catalog-style prompts as lookbook spreads: split panels, caption text,
        or several figures wearing outfit variants. Anti-collage negatives steer
        it back to one photograph; people-count negatives only when the prompt
        is a single-person scene so group prompts stay groups."""
        anti_collage = (
            "collage, split screen, diptych, triptych, grid layout, "
            "multiple panels, magazine layout, caption text"
        )
        combined_negative = (
            f"{combined_negative}, {anti_collage}" if combined_negative else anti_collage
        )
        subject_info = (detection or {}).get("subject_count_info", {})
        if (detection or {}).get("has_person") and subject_info.get("is_single_subject"):
            combined_negative = (
                f"{combined_negative}, multiple people, clones, "
                "duplicate character, twins"
            )
        return combined_negative

    def _optimize_prompt_for_tokens(
        self, prompt: str, max_tokens: int = 75, family: str = ""
    ) -> str:
        """Soft word-budget trim for short CLIP-era encoders only.

        Classic SD 1.x CLIP is ~77 tokens. Word≈token is a rough proxy used as a
        last-resort soft limit — NOT a hard model contract. Z-Image / Krea2 use
        long T5/LLM text encoders (hundreds of tokens); clipping them to 75 words
        silently deleted the tail of detailed user prompts while Verbatim was ON
        (and even when OFF, after style stuffing pushed useful content past 75).

        Never invents content; only shortens when a family still benefits from it.
        """
        if not prompt:
            return prompt
        fam = (family or "").lower()
        # Long-context text encoders: pass through; the tokenizer handles real limits.
        if fam in ("zimage", "krea2"):
            return prompt
        # SDXL dual-CLIP still ~77 tokens/encoder, but detailed prompts routinely
        # exceed 75 *words*. Soft-cap higher so tails survive; encoder truncates
        # the true hard limit.
        if fam == "sdxl":
            max_tokens = max(max_tokens, 150)

        words = prompt.split()
        if len(words) <= max_tokens:
            return prompt

        if any(keyword in prompt.lower() for keyword in ['elements:', 'style keywords:', 'negative prompt:']):
            main_desc = prompt.split('\n')[0].strip()
            return main_desc

        # Prefer keeping the user's words; do NOT append quality boilerplate that
        # would displace even more of their content after the cut.
        logger.info(
            "Soft-trimming prompt from %d words to %d (family=%s) — tail may be dropped",
            len(words), max_tokens, fam or "classic",
        )
        return " ".join(words[:max_tokens])

    def get_prompt_templates(self) -> Dict[str, Dict[str, Any]]:
        return {
            "infographic": {
                "template": """{subject}, {style}, {color_palette}, {background}, {elements}, {mood}

Elements: {element_list}

Style Keywords: {style_keywords}

Negative Prompt: {negative_prompt}""",
                "example": {
                    "subject": "flat vector illustration, infographic style",
                    "style": "clean geometric forms, minimal shadows",
                    "color_palette": "muted palette of blues and grays with accent red",
                    "background": "legal courtroom background with courthouse columns",
                    "elements": "scales of justice, legal documents, gavel, judge's bench silhouette, professional briefcase",
                    "mood": "serious tone",
                    "element_list": "gavel, legal documents with seal, scale of justice, professional desk, law books",
                    "style_keywords": "legal services, professional, corporate law, business consultation, justice, legal practice",
                    "negative_prompt": "no photorealism, no people faces, no over-saturation, no glitter or cartoon color, no watermarks"
                }
            },
            "realistic": {
                "template": "{subject}, {quality}, {lighting}, {composition}, {mood}",
                "example": {
                    "subject": "A majestic mountain landscape at sunset",
                    "quality": "photorealistic, high quality, detailed, sharp focus",
                    "lighting": "golden hour lighting, dramatic clouds",
                    "composition": "balanced composition, professional photography",
                    "mood": "peaceful mood, serene atmosphere"
                }
            },
            "technical": {
                "template": "{subject}, {style}, {details}, {composition}",
                "example": {
                    "subject": "technical diagram of a system",
                    "style": "clean lines, precise details, professional diagram",
                    "details": "clear labels, minimal style, technical illustration",
                    "composition": "clear composition, balanced layout"
                }
            }
        }

    def get_quality_presets(self) -> Dict[str, Dict[str, Any]]:
        return {
            "fast": {
                "num_inference_steps": 15,
                "guidance_scale": 7.0,
                "description": "Quick generation, good for testing"
            },
            "standard": {
                "num_inference_steps": 20,
                "guidance_scale": 7.5,
                "description": "Balanced quality and speed"
            },
            "high": {
                "num_inference_steps": 30,
                "guidance_scale": 8.0,
                "description": "High quality, slower generation"
            },
            "professional": {
                "num_inference_steps": 25,
                "guidance_scale": 7.5,
                "description": "Professional quality for final output"
            }
        }

    def _notify_vision_pipeline(self, action: str):
        """Best-effort notification to vision pipeline. Fire and forget."""
        try:
            import requests as req
            req.post("http://localhost:8201/gpu/contention",
                     json={"source": "image_gen", "action": action}, timeout=1)
        except Exception:
            pass

    def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        start_time = time.time()

        result = ImageGenerationResult(
            success=False,
            prompt_used=request.prompt,
            negative_prompt_used=request.negative_prompt,
            image_size=(request.width, request.height)
        )

        if not self.service_available:
            result.error = "Image generation service not available - missing dependencies"
            return result
        fault = self.gpu_fault_message()
        if fault:
            result.error = fault
            return result

        with self._generation_lock:
            self._notify_vision_pipeline("start")
            # Central gpu_session for *all* direct calls (chat tool, batch, API, edits via img2img).
            # Provides GPU lease + evict + GlobalLoadGate RAM/swap admission using our real estimates.
            # Reentrant-safe; chat tool may outer-wrap.
            from backend.services.gpu_resource_policy import gpu_session
            from backend.services.job_operation_gate import GpuBusyError
            from backend.services.job_types import JobKind
            import uuid as _uuid_local
            # Resolve routing before admission so VRAM/RAM estimates match the loaded model.
            if request.model in (None, "", "auto"):
                request.model = self._auto_select_model(request.prompt, request.style)
                if not request.model:
                    result.error = (
                        "No image model is downloaded yet. Open Settings → Image Models "
                        "and download one (Z-Image Turbo is the recommended default)."
                    )
                    return result
            if (
                self._has_text_intent(request.prompt)
                and "sd-xl" in self.available_models
                and request.model in (None, "", "auto", "sdxl-turbo")
            ):
                if request.model != "sd-xl":
                    logger.info(f"Text intent: routing {request.model} -> sd-xl for crisper type")
                request.model = "sd-xl"

            # FLUX.1-dev is Comfy-only — fail loud so batch routes correctly instead
            # of attempting an HF download of a sentinel "comfy:flux-dev" id.
            if self.is_comfy_only_model(request.model or ""):
                result.error = (
                    f"Model '{request.model}' runs via ComfyUI, not the offline Diffusers "
                    "pipeline. Use the batch FLUX path (or ComfyUIImageGenerator)."
                )
                return result

            model_id = self.available_models.get(request.model)
            if not model_id:
                result.error = (
                    f"Unknown image model '{request.model}'. Available: "
                    + ", ".join(sorted(self.available_models))
                )
                return result
            logger.info(f"Using model: {request.model} -> {model_id}")

            # Family-aware max side + area clamp BEFORE the estimates (2026-08-04):
            # admission must see the final W×H now that estimates scale with
            # resolution. (Z-Image/Krea → 2K; Flux not offline.)
            from backend.services.image_resolution_limits import clamp_image_dimensions
            _clamp_family = self._model_family(model_id)
            ow, oh = request.width, request.height
            request.width, request.height, dim_warns = clamp_image_dimensions(
                request.width, request.height, _clamp_family
            )
            for msg in dim_warns:
                logger.warning(msg)
            if (request.width, request.height) != (ow, oh):
                logger.info(
                    "Resolution clamped %sx%s → %sx%s (family=%s)",
                    ow, oh, request.width, request.height, _clamp_family,
                )
            result.image_size = (request.width, request.height)

            ram_est = self._ram_estimate_gb(request.model, request.width, request.height)
            vram_est = self._vram_estimate_mb(request.model, request.width, request.height)

            _gpu_stack = None
            try:
                _sd_pinned = False
                # 2026-08-04: this session used to be `with ...: pass` — the lease,
                # VRAM fit-check and RAM reservation were acquired and RELEASED
                # before the pipeline load and denoise, so the entire 2048² forward
                # ran with zero coordination held. Hold it for the real work; closed
                # as the LAST step of the finally below. Reentrancy-safe: batch and
                # stills outer sessions make this a pass-through (TLS flag in
                # gpu_resource_policy), so nested paths keep exactly one session.
                from backend.services.gpu_resource_policy import compositor_vram_reserve_mb
                _gpu_stack = contextlib.ExitStack()
                _gpu_stack.enter_context(gpu_session(
                    JobKind.VIDEO_RENDER, f"gen_{_uuid_local.uuid4().hex[:8]}",
                    on_busy="raise", evict_ollama=True, free_comfyui=True,
                    vram_estimate_mb=vram_est, ram_estimate_gb=ram_est,
                    require_fit=True, cross_process=True,
                    vram_reserve_mb=compositor_vram_reserve_mb(),
                ))

                family = self._model_family(model_id)

                # Field calibration: reset the CUDA peak counter so the post-run
                # estimate-vs-measured log reflects THIS generation (2026-08-04).
                try:
                    if torch.cuda.is_available():
                        torch.cuda.reset_peak_memory_stats()
                except Exception:
                    pass

                # 2026-08-04: refuse to run multi-billion-param DiT families on CPU.
                # __init__ silently falls back to CPU when the CUDA kernel probe fails
                # (e.g. a torch wheel without this GPU's sm arch — Blackwell sm_120 on
                # an older build). fp32 CPU inference of a 6B DiT consumes tens of GB
                # of RAM and locks the desktop — identical symptoms to the GPU crash,
                # with only one WARNING line as evidence. Fail loud instead.
                if family in ('zimage', 'krea2') and self._device != "cuda":
                    result.error = (
                        f"CUDA is unavailable/unusable on this box (device="
                        f"{self._device}) — refusing to run {family} on CPU (fp32 CPU "
                        "inference = tens of GB of RAM + desktop lockup). Check that "
                        "torch.cuda.get_arch_list() includes this GPU's architecture "
                        "(e.g. sm_120 for RTX 5060 Ti) and install a matching torch."
                    )
                    result.generation_time = time.time() - start_time
                    return result

                # Family-appropriate sampling. Batch UI often validates against the
                # *requested* model (e.g. zimage-turbo → steps=9, guidance=0.0). If
                # we later land on SDXL (auto-router, load fallback, text reroute),
                # those turbo params produce soft/painterly "artwork" instead of photos.
                # Turbo/DiT: soft-clamp only so quality presets/sliders are not placebo.
                # SDXL: hard correct (black-image / turbo-leftover hazards).
                if family in ('krea2', 'zimage'):
                    self._soft_clamp_family_sampling(request, family)
                elif family == 'sdxl':
                    if request.guidance_scale > 9.0:
                        logger.warning(
                            f"Guidance scale {request.guidance_scale} is too high for SDXL "
                            f"(causes black images). Auto-correcting to 7.5"
                        )
                    elif request.guidance_scale < 4.0:
                        logger.warning(
                            f"Guidance scale {request.guidance_scale} is too low for SDXL. "
                            f"Auto-correcting to 6.0"
                        )
                    elif request.num_inference_steps < 20:
                        logger.warning(
                            f"Steps {request.num_inference_steps} too low for SDXL "
                            f"(turbo leftovers). Auto-correcting to 25"
                        )
                    self._apply_family_sampling(request, family)
                elif request.guidance_scale > 20.0:
                    logger.warning(f"Guidance scale {request.guidance_scale} is extremely high. Capping at 15.0")
                    request.guidance_scale = 15.0

                # (Resolution clamp moved ABOVE the estimates — 2026-08-04.)

                # Make room BEFORE loading — family-aware estimate + Ollama eviction
                # when the card is too full. Runs after ALL model rerouting so the
                # estimate matches the model we actually load.
                self._ensure_vram_for_pipeline(model_id, request.width, request.height)

                if not self._load_pipeline(model_id):
                    # No substitution. This used to silently swap in SD 1.5 and carry
                    # on, which returned a plausible-looking image from a completely
                    # different (and much older) model — the user then blamed the model
                    # they picked. Report why it failed instead.
                    result.error = self._load_failure_reason(request.model, model_id)
                    logger.error(
                        f"Model {request.model} ({model_id}) failed to load: {result.error}"
                    )
                    return result

                # Pin sd:pipeline for the duration of this forward so idle eviction
                # cannot null scheduler mid-denoise (300s default timeout).
                try:
                    from backend.services.gpu_memory_orchestrator import get_orchestrator
                    get_orchestrator().begin_use("sd:pipeline")
                    _sd_pinned = True
                except Exception:
                    _sd_pinned = False

                # Best-effort VRAM hygiene after successful load for this job.
                # Helps the chat LLM reload faster on the next turn.
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.synchronize()
                        torch.cuda.empty_cache()
                except Exception:
                    pass

                # Verbatim Prompts (Settings → Generation): user's EXACT text to the
                # model. Previously this only gated media_director.enhance_prompts —
                # offline_image_generator still stuffed style/anatomy suffixes AND
                # hard-clipped to 75 words, so the toggle was effectively placebo.
                try:
                    from backend.services.media_director import verbatim_prompts_enabled
                    verbatim = bool(verbatim_prompts_enabled())
                except Exception:
                    verbatim = False

                text_mode = self._has_text_intent(request.prompt)
                if verbatim:
                    enhanced_prompt = request.prompt
                    style_negative = ""
                    detection = {}
                    logger.info(
                        "verbatim prompts ON — using user prompt as-is "
                        "(no style stuffing, no word-budget clip; len=%d chars)",
                        len(enhanced_prompt or ""),
                    )
                elif text_mode:
                    # Crisp text/logos need a larger canvas — at 512 the type renders
                    # as mush. Bump capable models to 1024 when the request is below it
                    # (within the per-model max already clamped above: 1536 for these).
                    if family in ("sdxl", "zimage", "krea2") and request.width < 1024 and request.height < 1024:
                        logger.info(
                            f"Text intent: enlarging canvas {request.width}x{request.height} -> 1024x1024 for legible type"
                        )
                        request.width = 1024
                        request.height = 1024
                        result.image_size = (request.width, request.height)
                    style_config = self.style_configs.get(
                        request.style, self.style_configs.get("realistic", {})
                    )
                    style_negative = style_config.get("negative_prompt", "") or ""
                    enhanced_prompt = request.prompt
                    if request.style == "realistic":
                        light_real = "photorealistic, professional photography, natural lighting, sharp focus"
                        if light_real.lower() not in enhanced_prompt.lower():
                            enhanced_prompt = f"{enhanced_prompt}, {light_real}"
                    detection = self.detect_content_type(request.prompt)
                    logger.info(
                        "Text-rendering intent detected — preserving spelling; "
                        f"still applying style={request.style!r} negatives"
                    )
                elif request.auto_enhance:
                    enhanced_prompt, style_negative, detection = self.enhance_prompt_for_quality(
                        prompt=request.prompt,
                        style=request.style,
                        content_preset=request.content_preset,
                        auto_enhance=True,
                        enhance_anatomy=request.enhance_anatomy,
                        enhance_faces=request.enhance_faces,
                        enhance_hands=request.enhance_hands,
                        family=family
                    )
                    logger.info(f"Content detection: {detection.get('recommended_preset')}, enhancements: {len(detection.get('enhancements_applied', []))}")
                else:
                    # auto_enhance=False: keep the user's positive prompt intact.
                    # Only attach style negatives (no positive suffix stuffing).
                    enhanced_prompt, style_negative = self._enhance_prompt(
                        request.prompt, request.style
                    )
                    detection = {}

                # Don't token-trim in text mode or verbatim — user tails must survive.
                # Family-aware: zimage/krea2 never word-clip; sdxl soft 150; classic 75.
                if not text_mode and not verbatim:
                    enhanced_prompt = self._optimize_prompt_for_tokens(
                        enhanced_prompt, family=family
                    )

                combined_negative = request.negative_prompt
                if style_negative:
                    combined_negative = f"{combined_negative}, {style_negative}" if combined_negative else style_negative

                # Verbatim mode is exempt: negatives stay exactly as the user set them.
                if (
                    not verbatim
                    and family == "krea2"
                    and self._krea2_variant(request.model or "") == "raw"
                ):
                    combined_negative = self._augment_krea2_raw_negatives(
                        combined_negative, detection
                    )

                generator = None
                if request.seed is not None:
                    generator = self._seed_generator(request.seed)
                    result.seed_used = request.seed
                else:
                    seed = torch.randint(0, 2**32, (1,)).item()
                    generator = self._seed_generator(seed)
                    result.seed_used = seed

                logger.debug(
                    f"Final image prompt lengths: positive={len(enhanced_prompt)}, "
                    f"negative={len(combined_negative)}"
                )
                logger.info(f"Generating image: {enhanced_prompt[:100]}...")

                # Character LoRAs (Z-Image): load before forward, unload after so the
                # next request cannot leak identity adapters across subjects.
                lora_paths = list(getattr(request, "loras", None) or [])
                lora_scale = float(getattr(request, "lora_scale", 1.0) or 1.0)
                if lora_paths:
                    self._apply_loras(family, lora_paths, lora_scale)

                # Dynamic VAE tiling: only at high res to preserve quality at normal
                # sizes. _set_vae_tiling handles pipeline- and vae-level APIs (the old
                # pipeline-only call silently no-opped for ZImage/Krea2 — 2026-08-04
                # client box 2048² desktop crash).
                _wants_tiling = request.width > 1024 or request.height > 1024
                _tiling_applied = self._set_vae_tiling(_wants_tiling)
                if _wants_tiling and _tiling_applied:
                    logger.info(
                        f"VAE tiling enabled ({request.width}x{request.height} > 1024px) "
                        f"via {getattr(self, '_vae_tiling_via', 'unknown')} level"
                    )
                # Hard gate: succeed tiled or fail cleanly. A >1MP DiT decode without
                # tiling is exactly the allocation that killed the client box's desktop; if a
                # future diffusers bump changes the API again, refuse rather than risk it.
                if (
                    not _tiling_applied
                    and family in ('zimage', 'krea2')
                    and request.width * request.height > 1024 * 1024
                ):
                    result.error = (
                        f"{request.width}x{request.height} with {family} requires VAE "
                        "tiling, which is unavailable on this pipeline build. Refusing "
                        "the untiled decode (it exhausts GPU+system memory). Retry at "
                        "≤1024×1024 or update diffusers."
                    )
                    result.generation_time = time.time() - start_time
                    return result

                # Same shape of gate for the DENOISE side. The tiling gate above only
                # covers the VAE decode; the 2026-08-24 OOM was on denoise step 0.
                # A grouped-query DiT whose attention lands on the math kernel
                # materializes [1, heads, S, S]: at 2048² that is 51GB, which no
                # amount of VRAM reclaim can serve. Refuse honestly instead of
                # burning a full model load per prompt to reach the same OOM.
                if (
                    request.width * request.height > 1024 * 1024
                    and self._uses_grouped_query_attention()
                    and not getattr(self, "_attention_backend_active", None)
                ):
                    result.error = (
                        f"{request.width}x{request.height} with {family} needs a "
                        "mask-capable memory-efficient attention backend "
                        "(cuDNN/efficient/flash); none could be set on this build, so "
                        "attention would fall back to the quadratic math kernel "
                        "(~51GB at 2048²). Refusing rather than OOMing. Retry at "
                        "≤1024×1024 or update torch/diffusers."
                    )
                    result.generation_time = time.time() - start_time
                    return result

                # Mid-denoise RAM floor (2026-08-04): admission can't guard a
                # minutes-long forward; the watchdog aborts per-step on breach.
                _watchdog = self._ram_watchdog_callback()
                _watchdog_kwargs = (
                    {"callback_on_step_end": _watchdog} if _watchdog else {}
                )

                def _call_pipeline(pos_prompt: str, neg_prompt: Optional[str]):
                    """Single forward; raises on OOM / compile failure for recovery."""
                    if family in ('zimage', 'krea2'):
                        self._ensure_flow_scheduler(family)
                        return self._pipeline(
                            prompt=pos_prompt,
                            negative_prompt=neg_prompt,
                            width=request.width,
                            height=request.height,
                            num_inference_steps=request.num_inference_steps,
                            guidance_scale=request.guidance_scale,
                            generator=generator,
                            **_watchdog_kwargs,
                        )
                    if self._device == "cuda":
                        # Match autocast dtype to the LOADED model dtype. The model is
                        # loaded in bf16 on Ada+ (see _load_pipeline), but autocast's
                        # CUDA default is fp16 — which overflows SDXL's VAE to NaN and
                        # yields a pure-black image. bf16 has fp32-range exponents, so
                        # this keeps SDXL/SD output correct.
                        _ac_dtype = (
                            torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
                        )
                        with torch.autocast("cuda", dtype=_ac_dtype):
                            return self._pipeline(
                                prompt=pos_prompt,
                                negative_prompt=neg_prompt,
                                width=request.width,
                                height=request.height,
                                num_inference_steps=request.num_inference_steps,
                                guidance_scale=request.guidance_scale,
                                generator=generator,
                                **_watchdog_kwargs,
                            )
                    return self._pipeline(
                        prompt=pos_prompt,
                        negative_prompt=neg_prompt,
                        width=request.width,
                        height=request.height,
                        num_inference_steps=request.num_inference_steps,
                        guidance_scale=request.guidance_scale,
                        generator=generator,
                        **_watchdog_kwargs,
                    )

                if family in ('zimage', 'krea2'):
                    neg = (
                        None
                        if self._skip_negative_prompt(
                            family, request.model or "", request.guidance_scale
                        )
                        else combined_negative
                    )
                else:
                    neg = combined_negative

                try:
                    output = _call_pipeline(enhanced_prompt, neg)
                except RamWatchdogAbort as ram_err:
                    # System-RAM floor breached mid-denoise: fail THIS item
                    # cleanly and never enter the OOM ladder — reloading
                    # pipelines while the box is out of RAM deepens the exact
                    # hole we're escaping (2026-08-04).
                    _detach_exception(ram_err)
                    import gc as _gc_ram
                    _gc_ram.collect()
                    self._unload_pipeline()
                    result.error = str(ram_err)
                    result.generation_time = time.time() - start_time
                    return result
                except (AssertionError, RuntimeError, torch.cuda.OutOfMemoryError) as infer_err:
                    if is_fatal_cuda_error(infer_err):
                        # Dead context: no retry, no offload ladder, no unload —
                        # every one of those is another CUDA call that fails.
                        result.error = self._mark_gpu_fault(infer_err, "inference")
                        result.generation_time = time.time() - start_time
                        return result
                    # torch.compile recovery (SD/SDXL full-GPU path)
                    is_compile_failure = (
                        (isinstance(infer_err, AssertionError) and not str(infer_err))
                        or any(kw in str(infer_err).lower() for kw in
                               ('triton', 'dynamo', 'inductor', 'cuda graph', 'torch.compile'))
                    )
                    has_compiled_modules = (
                        self._compile_unet_orig is not None or self._compile_vae_orig is not None
                    )
                    if (
                        is_compile_failure
                        and has_compiled_modules
                        and not self._compile_failed
                        and not self._is_cuda_oom(infer_err)
                    ):
                        logger.warning(
                            f"torch.compile first-pass failure "
                            f"({type(infer_err).__name__}: {infer_err or 'no message'}) "
                            f"— stripping compiled wrappers and retrying in eager mode"
                        )
                        if self._compile_unet_orig is not None:
                            self._pipeline.unet = self._compile_unet_orig
                        if self._compile_vae_orig is not None:
                            self._pipeline.vae = self._compile_vae_orig
                        self._compile_failed = True
                        output = _call_pipeline(enhanced_prompt, neg)
                    elif self._is_cuda_oom(infer_err):
                        # 2026-07-11: krea2 model-offload still peaked ~14.3GB on 16GB.
                        # Recover in-process: sequential offload same model, then
                        # lighter catalog fallback. Avoid recursive generate_image
                        # (generation lock is non-reentrant).
                        failed_label = request.model
                        prior_offload = self._pipeline_offload_mode
                        logger.error(
                            f"CUDA OOM during inference with {failed_label} "
                            f"(offload={prior_offload}): {infer_err}"
                        )
                        # Detach the traceback BEFORE unloading — otherwise the
                        # failed forward's frames pin the pipeline and the unload
                        # below frees ~nothing (see _detach_exception docstring).
                        oom_summary = f"{type(infer_err).__name__}: {infer_err}"
                        _detach_exception(infer_err)
                        import gc as _gc
                        _gc.collect()
                        try:
                            if torch.cuda.is_available():
                                torch.cuda.empty_cache()
                        except Exception:
                            pass
                        self._unload_pipeline()
                        try:
                            if torch.cuda.is_available():
                                free_b, _ = torch.cuda.mem_get_info()
                                free_mb = free_b // (1024 * 1024)
                                if free_mb < 4000:
                                    logger.error(
                                        "After OOM unload only %sMB free — refusing "
                                        "sequential/fallback thrash (wait for GPU or free other jobs)",
                                        free_mb,
                                    )
                                    raise RuntimeError(
                                        f"GPU still only {free_mb}MB free after OOM unload. "
                                        f"Wait for other jobs or free VRAM, then retry."
                                    ) from infer_err
                        except RuntimeError:
                            raise
                        except Exception:
                            pass

                        # Large-canvas OOM: reload ladder DISABLED by design
                        # (2026-08-04). With tiling active, a >1MP OOM means genuinely
                        # insufficient memory — a sequential reload retries the same
                        # doomed forward with a second ~24GB host-resident pipeline,
                        # and the old ladder stacked three of them until the desktop
                        # died. Fail the item loudly instead.
                        if request.width * request.height > 1024 * 1024:
                            result.error = (
                                f"CUDA out of memory at {request.width}x{request.height} "
                                f"with {failed_label} ({oom_summary}). Large-canvas OOM "
                                "retry is disabled by design — retry at ≤1024×1024 or "
                                "pick a lighter model."
                            )
                            result.generation_time = time.time() - start_time
                            return result

                        output = None
                        # Attempt 1: same model with sequential offload if not already.
                        if family in ('krea2', 'zimage') and prior_offload != "sequential":
                            logger.warning(
                                f"{family} OOM → retrying with sequential CPU offload"
                            )
                            self._force_sequential_offload = True
                            try:
                                self._ensure_vram_for_pipeline(model_id, request.width, request.height)
                            except RuntimeError as admit_err:
                                raise RuntimeError(
                                    f"GPU busy after OOM — cannot reload: {admit_err}"
                                ) from infer_err
                            if self._load_pipeline(model_id, force_sequential=True):
                                try:
                                    # Rebuild generator after OOM (device state may be dirty)
                                    if request.seed is not None:
                                        generator = self._seed_generator(
                                            request.seed
                                        )
                                    else:
                                        seed = result.seed_used or torch.randint(0, 2**32, (1,)).item()
                                        generator = self._seed_generator(seed)
                                        result.seed_used = seed
                                    output = _call_pipeline(enhanced_prompt, neg)
                                    logger.info(
                                        f"{family} sequential offload retry succeeded after OOM"
                                    )
                                except Exception as seq_err:
                                    if self._is_cuda_oom(seq_err):
                                        logger.warning(
                                            f"Sequential offload still OOM for {failed_label}: {seq_err}"
                                        )
                                        # Same traceback-pinning hazard as the outer
                                        # handler — detach before unloading.
                                        _detach_exception(seq_err)
                                        _gc.collect()
                                        self._unload_pipeline()
                                        try:
                                            if torch.cuda.is_available():
                                                torch.cuda.empty_cache()
                                        except Exception:
                                            pass
                                    else:
                                        raise

                        # Attempt 2: lighter model fallback
                        if output is None:
                            fb_key = self._oom_fallback_catalog_key(failed_label)
                            if not fb_key:
                                raise infer_err
                            logger.warning(
                                f"{failed_label} OOM → falling back to '{fb_key}'"
                            )
                            _gc.collect()
                            request.model = fb_key
                            model_id = self.available_models.get(fb_key)
                            if not model_id:
                                raise infer_err
                            family = self._model_family(model_id)
                            self._apply_family_sampling(request, family)
                            if family in ('zimage', 'krea2'):
                                neg = (
                                    None
                                    if self._skip_negative_prompt(
                                        family, request.model or "", request.guidance_scale
                                    )
                                    else combined_negative
                                )
                            else:
                                neg = combined_negative
                            from backend.services.image_resolution_limits import clamp_image_dimensions
                            request.width, request.height, _ = clamp_image_dimensions(
                                request.width, request.height, family
                            )
                            result.image_size = (request.width, request.height)
                            try:
                                self._ensure_vram_for_pipeline(model_id, request.width, request.height)
                            except RuntimeError as admit_err:
                                raise RuntimeError(
                                    f"GPU busy — cannot load OOM fallback '{fb_key}': {admit_err}"
                                ) from infer_err
                            if not self._load_pipeline(model_id):
                                raise RuntimeError(
                                    f"OOM fallback model '{fb_key}' failed to load"
                                ) from infer_err
                            if request.seed is not None:
                                generator = self._seed_generator(
                                    request.seed
                                )
                            else:
                                seed = result.seed_used or torch.randint(0, 2**32, (1,)).item()
                                generator = self._seed_generator(seed)
                                result.seed_used = seed
                            output = _call_pipeline(enhanced_prompt, neg)
                            logger.info(f"OOM fallback to '{fb_key}' succeeded")
                    else:
                        raise


                image = output.images[0]
                if image is None:
                    result.error = "Pipeline returned no image"
                    result.generation_time = time.time() - start_time
                    return result

                image_id = str(uuid.uuid4())
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"generated_{timestamp}_{image_id}.png"
                image_path = self.cache_dir / filename

                image.save(image_path, "PNG")

                face_restoration_metadata = None
                if request.restore_faces:
                    try:
                        face_service = get_face_restoration_service()
                        service_available = face_service.service_available
                    except Exception as e:
                        logger.warning(f"Could not check face restoration availability: {e}")
                        service_available = False

                    if service_available:
                        should_restore = detection.get("has_person") or detection.get("has_face") if detection else False

                        if should_restore:
                            logger.info("Applying GFPGAN face restoration...")
                            try:
                                success, restored_pil, restore_meta = face_service.restore_face_from_pil(
                                    image=image,
                                    weight=request.face_restoration_weight
                                )

                                if success and restored_pil:
                                    image = restored_pil
                                    image.save(image_path, "PNG")
                                    face_restoration_metadata = restore_meta
                                    logger.info(f"Face restoration applied: {restore_meta.get('faces_detected', 0)} faces enhanced")
                                else:
                                    logger.warning(f"Face restoration failed: {restore_meta.get('error', 'Unknown error') if restore_meta else 'No metadata'}")
                            except Exception as e:
                                logger.error(f"Face restoration error: {e}")
                        else:
                            logger.debug("Skipping face restoration - no faces detected in prompt")
                    else:
                        logger.debug("Face restoration requested but service not available")

                # Optional: knock out the background → transparent RGBA PNG (icons,
                # clip-art, logos). Post-process pass; diffusion itself outputs opaque RGB.
                if getattr(request, "remove_background", False):
                    try:
                        from rembg import remove as _rembg_remove
                        image = _rembg_remove(image)  # returns an RGBA PIL image
                        image.save(image_path, "PNG")  # PNG preserves the alpha channel
                        logger.info("Transparent background applied (rembg)")
                    except Exception as e:
                        logger.error(f"Background removal failed (rembg): {e}")

                result.success = True
                result.image_path = str(image_path)
                result.prompt_used = enhanced_prompt

                # Extra hygiene: release what we can so the chat LLM can reload promptly.
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                        torch.cuda.synchronize()
                except Exception:
                    pass
                result.negative_prompt_used = combined_negative
                result.model_used = self._current_model
                result.generation_time = time.time() - start_time
                result.metadata = {
                    "steps": request.num_inference_steps,
                    "guidance_scale": request.guidance_scale,
                    "style": request.style,
                    "device": self._device,
                    "auto_enhance": request.auto_enhance,
                    "content_preset": detection.get("preset_used") if detection else None,
                    "content_detection": {
                        "has_person": detection.get("has_person"),
                        "has_face": detection.get("has_face"),
                        "has_hands": detection.get("has_hands"),
                        "has_action": detection.get("has_action"),
                        "detected_actions": detection.get("detected_actions", [])
                    } if detection else None,
                    "face_restoration": face_restoration_metadata
                }

                logger.info(f"Image generated successfully in {result.generation_time:.2f}s: {image_path}")
                # Estimate-vs-measured (2026-08-04): accumulates field data for
                # slope calibration across the install base.
                try:
                    if torch.cuda.is_available():
                        _peak_mb = torch.cuda.max_memory_allocated() // (1024 * 1024)
                        logger.info(
                            f"VRAM estimate vs measured: est {vram_est}MB, peak "
                            f"{_peak_mb}MB ({request.width}x{request.height}, "
                            f"offload={self._pipeline_offload_mode})"
                        )
                except Exception:
                    pass

            except Exception as e:
                logger.error(f"Image generation failed: {type(e).__name__}: {e}", exc_info=True)
                if is_fatal_cuda_error(e):
                    result.error = self._mark_gpu_fault(e, "generation")
                else:
                    error_msg = str(e) or f"{type(e).__name__} (no message)"
                    result.error = f"Generation failed: {error_msg}"
                result.generation_time = time.time() - start_time
            finally:
                # Always drop character LoRAs so keep_pipeline_loaded cannot leak identity.
                try:
                    self._unload_loras()
                except Exception:
                    pass
                if _sd_pinned:
                    try:
                        from backend.services.gpu_memory_orchestrator import get_orchestrator
                        get_orchestrator().end_use("sd:pipeline")
                    except Exception:
                        pass
                self._notify_vision_pipeline("stop")
                if not getattr(request, "keep_pipeline_loaded", False):
                    # Immediately free VRAM — don't wait for the 300s idle timer.
                    # The LLM needs the GPU back for the next chat turn.
                    self._unload_pipeline()
                    try:
                        from backend.services.gpu_memory_orchestrator import get_orchestrator
                        get_orchestrator().release_model("sd:pipeline")
                    except Exception:
                        pass
                # Release the gpu_session LAST — after LoRA/pipeline teardown — so
                # the lease and gate cover the whole unit of work (2026-08-04).
                # Guarded own try/except: a close failure must not mask the result.
                if _gpu_stack is not None:
                    try:
                        _gpu_stack.close()
                    except Exception as close_err:
                        logger.warning(f"gpu_session close failed (non-fatal): {close_err}")

        return result

    def _apply_loras(self, family: str, lora_paths: List[str], scale: float = 1.0) -> None:
        """Load character LoRA weights onto the resident pipeline (Z-Image first).

        Anticipated fails:
          - SDXL LoRA on Z-Image pipeline → raise (caller must route by sidecar)
          - missing file → skip with warning
          - pipeline without load_lora_weights → raise
        """
        self._unload_loras()
        if not lora_paths or self._pipeline is None:
            return
        if family not in ("zimage",):
            # SDXL/Flux character LoRAs stay on the Comfy path today.
            raise RuntimeError(
                f"Offline LoRA apply is only implemented for Z-Image (got family={family}). "
                "SDXL/FLUX cast LoRAs must use ComfyUI."
            )
        if not hasattr(self._pipeline, "load_lora_weights"):
            raise RuntimeError("Loaded pipeline cannot load_lora_weights (upgrade diffusers)")

        adapters = []
        weights = []
        for i, path in enumerate(lora_paths):
            p = Path(path)
            if not p.is_file():
                logger.warning("LoRA path missing, skipping: %s", path)
                continue
            # Reject obvious SDXL kohya keys if sidecar says so
            try:
                from backend.services.media_model_registry import read_lora_sidecar
                meta = read_lora_sidecar(str(p))
                if meta and meta.get("family") and meta.get("family") != "zimage":
                    raise RuntimeError(
                        f"LoRA {p.name} is family={meta.get('family')} but pipeline is Z-Image"
                    )
            except RuntimeError:
                raise
            except Exception:
                pass
            name = f"cast_{i}"
            try:
                # Prefer an in-memory remapped dict so PEFT-prefixed saves
                # (transformer.base_model.model.*) from early peft_zimage trains
                # still load. Diffusers accepts a state-dict dict here.
                from safetensors.torch import load_file as _load_st

                raw = _load_st(str(p), device="cpu")
                remapped = normalize_zimage_lora_state_dict(raw)
                n_rewritten = sum(
                    1 for old_k, new_k in zip(raw.keys(), remapped.keys()) if old_k != new_k
                )
                if n_rewritten:
                    logger.info(
                        "Z-Image LoRA %s: stripped PEFT base_model.model prefix from "
                        "%d/%d keys for Diffusers",
                        p.name,
                        n_rewritten,
                        len(remapped),
                    )
                self._pipeline.load_lora_weights(remapped, adapter_name=name)
            except Exception as e:
                raise RuntimeError(f"Failed to load Z-Image LoRA {p}: {e}") from e
            adapters.append(name)
            weights.append(float(scale))

        if not adapters:
            raise RuntimeError("No valid LoRA files to load")
        try:
            if hasattr(self._pipeline, "set_adapters"):
                self._pipeline.set_adapters(adapters, adapter_weights=weights)
        except Exception as e:
            logger.warning("set_adapters failed (%s); adapters loaded with default scale", e)
        self._loaded_lora_adapters = adapters
        logger.info("Applied %d Z-Image LoRA(s) scale=%.2f", len(adapters), scale)

    def _unload_loras(self) -> None:
        """Remove character LoRA adapters from the resident pipeline."""
        if self._pipeline is None:
            self._loaded_lora_adapters = []
            return
        try:
            if self._loaded_lora_adapters and hasattr(self._pipeline, "delete_adapters"):
                try:
                    self._pipeline.delete_adapters(self._loaded_lora_adapters)
                except Exception:
                    for n in self._loaded_lora_adapters:
                        try:
                            self._pipeline.delete_adapters([n])
                        except Exception:
                            pass
            elif hasattr(self._pipeline, "unload_lora_weights"):
                self._pipeline.unload_lora_weights()
        except Exception as e:
            logger.debug("unload_loras best-effort: %s", e)
        self._loaded_lora_adapters = []

    def _ensure_flow_scheduler(self, family: str) -> None:
        """Reattach FlowMatch scheduler if idle eviction nulled it mid-batch.

        Z-Image / Krea2 require FlowMatchEulerDiscreteScheduler. Idle unload used
        to set pipeline.scheduler = None while __call__ was still running.
        """
        if family not in ("zimage", "krea2") or self._pipeline is None:
            return
        if getattr(self._pipeline, "scheduler", None) is not None:
            return
        if FlowMatchEulerDiscreteScheduler is None:
            raise RuntimeError(
                f"{family} pipeline scheduler is None and FlowMatchEulerDiscreteScheduler "
                "is unavailable — reload the model"
            )
        if not self._current_model:
            raise RuntimeError(
                f"{family} pipeline scheduler is None and no model is loaded — "
                "reload the model"
            )
        model_path = self._get_model_path(self._current_model)
        try:
            sched = FlowMatchEulerDiscreteScheduler.from_pretrained(
                str(model_path), subfolder="scheduler"
            )
        except Exception as e:
            logger.warning(
                "Could not reload scheduler from %s/scheduler (%s); using defaults",
                model_path,
                e,
            )
            sched = FlowMatchEulerDiscreteScheduler()
        self._pipeline.scheduler = sched
        logger.error(
            "%s pipeline.scheduler was None — reattached %s (likely idle-eviction race)",
            family,
            type(sched).__name__,
        )

    def _unload_pipeline(self, wait: bool = True) -> bool:
        """Fully unload the pipeline and return RAM/VRAM to the pool.

        Aggressive host RAM release for heavy offloaded models (Z-Image etc).
        Called after every chat gen (keep=False) and at batch end.

        Args:
            wait: If True, block on ``_generation_lock`` until free (internal
                callers already holding the RLock re-enter safely). If False
                (orchestrator idle eviction), refuse immediately when a
                generation is in progress so we never null ``scheduler`` under
                a live ``__call__``.

        Returns:
            True if unloaded (or already empty), False if refused because busy.
        """
        acquired = self._generation_lock.acquire(blocking=wait)
        if not acquired:
            logger.warning(
                "Refusing SD pipeline unload: generation lock held "
                "(would null scheduler mid-denoise)"
            )
            return False
        try:
            return self._unload_pipeline_unlocked()
        finally:
            self._generation_lock.release()

    def _unload_pipeline_unlocked(self) -> bool:
        """Teardown body; caller must hold ``_generation_lock``."""
        if self._pipeline is None:
            return True

        try:
            self._unload_loras()
        except Exception:
            pass

        try:
            import psutil
            proc = psutil.Process()
            rss_before = proc.memory_info().rss / (1024**3)
        except Exception:
            rss_before = 0.0

        pipeline = self._pipeline
        self._pipeline = None
        self._img2img_pipeline = None
        self._img2img_family = None
        self._current_model = None
        self._pipeline_offload_mode = None
        self._compile_unet_orig = None
        self._compile_vae_orig = None
        self._loaded_lora_adapters = []

        # Accelerate CPU-offload hooks retain large CPU weight copies (the hook's
        # weights_map holds the ENTIRE offloaded state dict) until removed.
        # ORDER MATTERS: remove_all_hooks() iterates pipeline.components and only
        # acts on live nn.Modules — running it AFTER the nulling loop below made it
        # a guaranteed no-op, so multi-GB weight maps survived every "unload"
        # (2026-08-04 client box 2048² incident; same defect class as the ComfyUI
        # unpatch_model leak). Hooks must be detached while components are alive.
        # maybe_free_model_hooks deliberately NOT called: after remove_all_hooks it
        # is a no-op, and on any path where hooks remain it RE-APPLIES
        # enable_model_cpu_offload (diffusers pipeline_utils maybe_free_model_hooks).
        try:
            if hasattr(pipeline, "remove_all_hooks"):
                pipeline.remove_all_hooks()
        except Exception as e:
            logger.warning(f"Pipeline hook teardown failed (continuing unload): {e}")

        # Explicitly break references to submodules (weights stay in CPU tensors until all refs gone)
        for attr in ("unet", "vae", "text_encoder", "text_encoder_2", "tokenizer", "tokenizer_2",
                     "scheduler", "transformer", "safety_checker", "feature_extractor"):
            try:
                if hasattr(pipeline, attr):
                    obj = getattr(pipeline, attr)
                    setattr(pipeline, attr, None)
                    del obj
            except Exception:
                pass

        try:
            pipeline.to("cpu")
        except Exception as e:
            logger.debug(f"pipeline.to(cpu) skipped during unload: {e}")

        try:
            del pipeline
        except Exception:
            pass

        import gc
        gc.collect()
        gc.collect()  # second pass often helps release more
        if torch.cuda.is_available():
            try:
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
                if hasattr(torch.cuda, "ipc_collect"):
                    torch.cuda.ipc_collect()
            except Exception:
                pass

        # More aggressive: return memory to OS (glibc)
        try:
            import ctypes
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:
            pass

        try:
            import psutil
            proc = psutil.Process()
            rss_after = proc.memory_info().rss / (1024**3)
            if rss_before > 0:
                logger.info(f"SD pipeline unloaded; RSS {rss_before:.1f}GB -> {rss_after:.1f}GB (delta {rss_before-rss_after:+.1f}GB); hooks/gc/CUDA cleared + malloc_trim")
            else:
                logger.info("SD pipeline unloaded; hooks cleared, gc run, CUDA cache cleared")
        except Exception:
            logger.info("SD pipeline unloaded; hooks cleared, gc run, CUDA cache cleared")
        return True

    def generate_image_from_image(
        self, prompt: str, init_image, strength: float = 0.20,
        negative_prompt: str = "", width: int = 512, height: int = 512,
        num_inference_steps: int = 20, guidance_scale: float = 7.5,
        seed: int = None, model: str = "auto",
        keep_pipeline_loaded: bool = False
    ) -> ImageGenerationResult:
        """Generate an image using img2img — takes an existing PIL Image and
        produces a variation guided by the prompt and strength parameter.

        Args:
            prompt: Text prompt for the output image.
            init_image: PIL.Image input frame.
            strength: How much to change (0.0=identical, 1.0=ignore input).
            Other args mirror generate_image().

        Returns:
            ImageGenerationResult with the new image path.
        """
        result = ImageGenerationResult(success=False)
        start_time = time.time()

        if not self.service_available:
            result.error = "Image generation service not available"
            return result
        fault = self.gpu_fault_message()
        if fault:
            result.error = fault
            return result

        with self._generation_lock:
            self._notify_vision_pipeline("start")
            pipeline_pinned = False
            try:
                if model in (None, "", "auto"):
                    model = self._auto_select_model(prompt, "realistic")

                if model not in self.available_models:
                    # Same catalog rule as txt2img: never resolve an arbitrary
                    # string into a Hugging Face repo download.
                    result.error = f"Unknown image model: {model}"
                    return result
                model_id = self.available_models[model]
                family = self._model_family(model_id)

                if family == 'krea2':
                    result.error = (
                        f"Model {model} does not support img2img editing yet — "
                        "use kontext or sd-xl for edits"
                    )
                    return result

                if family == 'zimage':
                    num_inference_steps = 8
                    guidance_scale = 1.0
                elif family == 'sdxl':
                    if guidance_scale > 9.0:
                        guidance_scale = 7.5
                    elif guidance_scale < 4.0:
                        guidance_scale = 6.0

                from backend.services.image_resolution_limits import clamp_image_dimensions
                width, height, _ = clamp_image_dimensions(int(width), int(height), family)

                # Priced admission, as for txt2img: book sd:pipeline against the
                # real card before loading, so a too-large edit is refused as
                # busy instead of thrashing CUDA.
                try:
                    self._ensure_vram_for_pipeline(model_id, width, height)
                except RuntimeError as admit_err:
                    result.error = f"GPU busy: {admit_err}"
                    return result

                # Ensure the base txt2img pipeline is loaded (downloads model if needed)
                if not self._load_pipeline(model_id):
                    result.error = self._load_failure_reason(model, model_id)
                    return result
                try:
                    from backend.services.gpu_memory_orchestrator import get_orchestrator
                    get_orchestrator().begin_use("sd:pipeline")
                    pipeline_pinned = True
                except Exception:
                    pipeline_pinned = False

                if (
                    self._img2img_pipeline is None
                    or self._current_model != model_id
                    or self._img2img_family != family
                ):
                    logger.info(
                        "Building img2img pipeline for %s (family=%s)",
                        model_id, family,
                    )
                    self._img2img_pipeline = self._build_img2img_pipeline(family)
                    self._img2img_family = family
                    logger.info("img2img pipeline ready (%s)", family)

                # Resize init_image to target dimensions
                if init_image.size != (width, height):
                    init_image = init_image.resize((width, height), Image.LANCZOS)

                # Convert to RGB if needed
                if init_image.mode != "RGB":
                    init_image = init_image.convert("RGB")

                generator = None
                if seed is not None:
                    generator = self._seed_generator(seed)
                    result.seed_used = seed
                else:
                    seed = torch.randint(0, 2**32, (1,)).item()
                    generator = self._seed_generator(seed)
                    result.seed_used = seed

                combined_negative = negative_prompt or "blurry, low quality, distorted"

                logger.info(
                    "img2img (%s): strength=%s, steps=%s, prompt=%r",
                    family, strength, num_inference_steps, prompt[:80],
                )

                call_kwargs = dict(
                    prompt=prompt,
                    image=init_image,
                    strength=strength,
                    width=width,
                    height=height,
                    num_inference_steps=num_inference_steps,
                    guidance_scale=guidance_scale,
                    generator=generator,
                )
                _watchdog = self._ram_watchdog_callback()
                if _watchdog:
                    call_kwargs["callback_on_step_end"] = _watchdog

                if family == 'zimage':
                    # Z-Image is bf16 flow-matching — no autocast; CFG distilled out.
                    call_kwargs["negative_prompt"] = None
                    output = self._img2img_pipeline(**call_kwargs)
                elif self._device == "cuda":
                    _ac_dtype = (
                        torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
                    )
                    with torch.autocast("cuda", dtype=_ac_dtype):
                        output = self._img2img_pipeline(
                            **call_kwargs,
                            negative_prompt=combined_negative,
                        )
                else:
                    output = self._img2img_pipeline(
                        **call_kwargs,
                        negative_prompt=combined_negative,
                    )

                image = output.images[0]
                if image is None:
                    result.error = "img2img pipeline returned no image"
                    result.generation_time = time.time() - start_time
                    return result

                image_id = str(uuid.uuid4())
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"img2img_{timestamp}_{image_id}.png"
                image_path = self.cache_dir / filename
                image.save(image_path, "PNG")

                result.success = True
                result.image_path = str(image_path)
                result.prompt_used = prompt
                result.negative_prompt_used = combined_negative
                result.model_used = self._current_model
                result.generation_time = time.time() - start_time

                logger.info(f"img2img generated in {result.generation_time:.2f}s: {image_path}")

            except Exception as e:
                logger.error(f"img2img failed: {type(e).__name__}: {e}", exc_info=True)
                error_msg = str(e) or f"{type(e).__name__} (no message)"
                result.error = f"img2img failed: {error_msg}"
                result.generation_time = time.time() - start_time
            finally:
                self._notify_vision_pipeline("stop")
                if pipeline_pinned:
                    try:
                        from backend.services.gpu_memory_orchestrator import get_orchestrator
                        get_orchestrator().end_use("sd:pipeline")
                    except Exception:
                        pass
                if not keep_pipeline_loaded:
                    self._unload_pipeline()

        return result

    def get_available_models(self, *, probe_remote: bool = True) -> Dict[str, Any]:
        """Visible image models for menus/API, with a usability verdict per model.

        Carries UI metadata (label/description/recommended/order) so the frontend
        dropdowns are driven entirely from this single source, plus ``availability``:

          ``ready``          weights on disk, selectable now
          ``downloadable``   not on disk but fetchable — selecting it starts a download
          ``needs_licence``  gated repo, account has not accepted the terms
          ``needs_token``    gated repo and no HF_TOKEN configured
          ``unreachable``    not on disk and the repo could not be reached

        Anything other than ready/downloadable is unusable, and the caller is expected
        to keep it out of the picker rather than let a run fail later — a silent
        substitution is what made Krea 2 look like it was producing garbage.

        ``probe_remote=False`` skips the network probe (disk truth only) for callers
        that must not block.
        """
        models = {}

        # Network probes only matter for models that are not already on disk. Run
        # them together so a cold menu costs one round trip, not one per model.
        to_probe = [
            mid for key, mid in self.available_models.items()
            if key not in self.hidden_models
            and not self.is_comfy_only_model(key)
            and not self._is_model_downloaded(mid)
        ]
        if probe_remote and to_probe:
            try:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=min(8, len(to_probe))) as pool:
                    list(pool.map(self._probe_repo_access, to_probe))
            except Exception as e:
                logger.debug(f"Model availability probe failed: {e}")

        for model_key, model_id in self.available_models.items():
            if model_key in self.hidden_models:
                continue
            meta = self.model_meta.get(model_key, {})
            downloaded = self._is_model_downloaded(model_id)
            if downloaded:
                availability = "ready"
            elif self.is_comfy_only_model(model_key):
                # Sentinel id — nothing to fetch from HF; assets are installed for Comfy.
                availability = "unreachable"
            elif not probe_remote:
                availability = "downloadable"
            else:
                access = self._probe_repo_access(model_id)
                availability = "downloadable" if access == "ok" else access
            models[model_key] = {
                "id": model_id,
                "name": model_key,
                "label": meta.get("label", model_key),
                "description": meta.get("description", ""),
                "recommended": meta.get("recommended", False),
                "order": meta.get("order", 99),
                "downloaded": downloaded,
                "availability": availability,
                "selectable": availability in ("ready", "downloadable"),
                "current": model_id == self._current_model,
                "size_estimate": (
                    "28-36GB" if "krea" in model_id.lower()
                    else "23GB+Comfy" if "flux" in model_key or "flux" in model_id.lower()
                    else "16GB" if "z-image" in model_id.lower() or "zimage" in model_key
                    else "12-15GB" if "xl" in model_id.lower()
                    else "4-7GB"
                ),
                "engine": meta.get("engine") or (
                    "comfy" if model_key in getattr(self, "comfy_only_models", set()) else "offline"
                ),
            }

        return models

    def get_service_status(self) -> Dict[str, Any]:
        optimizations = {}
        
        if self._pipeline:
            optimizations = {
                "attention_slicing": hasattr(self._pipeline, "enable_attention_slicing"),
                "xformers_available": hasattr(self._pipeline, "enable_xformers_memory_efficient_attention"),
                # Computed truth, not pipeline-level hasattr — that lie hid the
                # ZImage/Krea2 tiling no-op behind the 2026-08-04 2048² crash.
                "vae_slicing": bool(getattr(self, "_vae_slicing_enabled", False)),
                "vae_tiling": bool(getattr(self, "_vae_tiling_available", False)),
                "vae_tiling_via": getattr(self, "_vae_tiling_via", None),
                "torch_compile_available": hasattr(torch, 'compile'),
                "cpu_offloading_disabled": True
            }
        
        return {
            "service_available": self.service_available,
            "device": self._device,
            "cuda_available": torch.cuda.is_available() if diffusion_available else False,
            "current_model": self._current_model,
            "gpu_fault": self._gpu_fault,
            "models_dir": str(self.models_dir),
            "cache_dir": str(self.cache_dir),
            "available_models": self.get_available_models(),
            "available_styles": list(self.style_configs.keys()),
            "optimizations": optimizations,
            "pytorch_version": torch.__version__ if diffusion_available else "N/A",
            "prompt_templates": self.get_prompt_templates(),
            "quality_presets": self.get_quality_presets()
        }

    def clear_cache(self) -> Dict[str, Any]:
        try:
            import shutil
            if self.cache_dir.exists():
                shutil.rmtree(self.cache_dir)
                self.cache_dir.mkdir(parents=True, exist_ok=True)

            return {"success": True, "message": "Cache cleared successfully"}

        except Exception as e:
            return {"success": False, "error": str(e)}


_generator_instance = None

def get_image_generator() -> OfflineImageGenerator:
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = OfflineImageGenerator()
    return _generator_instance


def generate_image(prompt: str, style: str = "realistic", width: int = 512, height: int = 512,
                  steps: int = 20, guidance: float = 7.5, seed: Optional[int] = None) -> ImageGenerationResult:
    request = ImageGenerationRequest(
        prompt=prompt,
        width=width,
        height=height,
        num_inference_steps=steps,
        guidance_scale=guidance,
        style=style,
        seed=seed
    )

    generator = get_image_generator()
    return generator.generate_image(request)


def get_generator_status() -> Dict[str, Any]:
    generator = get_image_generator()
    return generator.get_service_status()
