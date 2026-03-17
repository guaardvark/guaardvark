import logging
import os
import uuid
import gc
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

if "PYTORCH_CUDA_ALLOC_CONF" not in os.environ:
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = (
        "expandable_segments:False,max_split_size_mb:512"
    )
    logger.info("Set PYTORCH_CUDA_ALLOC_CONF to prevent CUDA memory allocation errors")

try:
    import imageio

    imageio_available = True
except Exception as e:
    logger.warning(f"imageio not available: {e}")
    imageio_available = False

try:
    from PIL import Image, ImageDraw, ImageFont

    pillow_available = True
except Exception as e:
    logger.warning(f"Pillow not available: {e}")
    pillow_available = False

try:
    import torch

    torch_available = True
except Exception as e:
    logger.warning(f"PyTorch not available: {e}")
    torch_available = False

try:
    from diffusers import StableVideoDiffusionPipeline, DiffusionPipeline

    diffusers_available = True
    svd_available = True
except Exception as e:
    logger.warning(f"Diffusers/SVD not available for video generation: {e}")
    DiffusionPipeline = None
    StableVideoDiffusionPipeline = None
    diffusers_available = False
    svd_available = False

try:
    from diffusers import CogVideoXPipeline, CogVideoXImageToVideoPipeline

    cogvideox_available = True
except Exception as e:
    logger.warning(f"CogVideoX not available: {e}")
    CogVideoXPipeline = None
    CogVideoXImageToVideoPipeline = None
    cogvideox_available = False


try:
    from backend.config import CACHE_DIR

    config_available = True
except ImportError:
    config_available = False
    CACHE_DIR = "/tmp/guaardvark_cache"

try:
    from backend.services.gpu_resource_coordinator import get_gpu_coordinator

    gpu_coordinator_available = True
except ImportError:
    gpu_coordinator_available = False
    get_gpu_coordinator = None

try:
    from backend.services.offline_image_generator import (
        get_image_generator,
        ImageGenerationRequest,
    )

    image_generator_available = True
except ImportError as e:
    logger.warning(f"Image generator not available for text-to-video: {e}")
    image_generator_available = False
    get_image_generator = None
    ImageGenerationRequest = None


def force_clear_gpu_memory() -> dict:
    result = {"success": False, "before": {}, "after": {}, "freed_gb": 0}

    if not torch_available or not torch.cuda.is_available():
        result["error"] = "CUDA not available"
        return result

    try:
        before_allocated = torch.cuda.memory_allocated() / (1024**3)
        before_reserved = torch.cuda.memory_reserved() / (1024**3)
        result["before"] = {
            "allocated_gb": before_allocated,
            "reserved_gb": before_reserved,
        }

        logger.info(
            f"GPU memory before cleanup: {before_allocated:.2f} GB allocated, {before_reserved:.2f} GB reserved"
        )

        if image_generator_available and get_image_generator is not None:
            try:
                img_gen = get_image_generator()
                if hasattr(img_gen, "_pipeline") and img_gen._pipeline is not None:
                    logger.info("Force unloading image generator pipeline...")
                    try:
                        if hasattr(img_gen._pipeline, "to"):
                            img_gen._pipeline.to("cpu")
                    except Exception:
                        pass
                    del img_gen._pipeline
                    img_gen._pipeline = None
                    if hasattr(img_gen, "_current_model"):
                        img_gen._current_model = None
            except Exception as e:
                logger.warning(f"Error unloading image generator: {e}")

        for _ in range(5):
            gc.collect()

        torch.cuda.empty_cache()
        torch.cuda.synchronize()

        torch.cuda.reset_peak_memory_stats()
        if hasattr(torch.cuda, "reset_accumulated_memory_stats"):
            torch.cuda.reset_accumulated_memory_stats()

        if hasattr(torch.cuda, "ipc_collect"):
            torch.cuda.ipc_collect()

        torch.cuda.empty_cache()
        torch.cuda.synchronize()

        gc.collect()
        torch.cuda.empty_cache()

        after_allocated = torch.cuda.memory_allocated() / (1024**3)
        after_reserved = torch.cuda.memory_reserved() / (1024**3)
        result["after"] = {
            "allocated_gb": after_allocated,
            "reserved_gb": after_reserved,
        }
        result["freed_gb"] = before_reserved - after_reserved
        result["success"] = True

        logger.info(
            f"GPU memory after cleanup: {after_allocated:.2f} GB allocated, {after_reserved:.2f} GB reserved"
        )
        logger.info(f"Freed approximately {result['freed_gb']:.2f} GB of GPU memory")

        return result

    except Exception as e:
        logger.error(f"Error during GPU memory cleanup: {e}")
        result["error"] = str(e)
        return result


@dataclass
class VideoGenerationRequest:
    prompt: str = ""
    negative_prompt: str = ""
    model: str = "svd"
    duration_frames: int = 25
    fps: int = 7
    width: int = 512
    height: int = 512
    motion_strength: float = 1.0
    num_inference_steps: int = 25
    guidance_scale: float = 7.5
    seed: Optional[int] = None
    generate_frames_only: bool = False
    frames_per_batch: int = 1
    combine_frames: bool = False
    output_dir: Optional[Path] = None
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class VideoGenerationResult:
    success: bool
    prompt_used: str = ""
    video_path: Optional[str] = None
    frame_paths: List[str] = field(default_factory=list)
    thumbnail_path: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)


class OfflineVideoGenerator:

    SVD_MODELS = {
        "svd": "stabilityai/stable-video-diffusion-img2vid",
        "svd-xt": "stabilityai/stable-video-diffusion-img2vid-xt",
    }

    COGVIDEOX_MODELS = {
        "cogvideox-2b": {
            "repo": "THUDM/CogVideoX-2b",
            "type": "text2video",
            "max_frames": 49,
            "fps": 8,
            "resolution": (720, 480),
            "vram_required": 12,
        },
        "cogvideox-5b": {
            "repo": "THUDM/CogVideoX-5b",
            "type": "text2video",
            "max_frames": 49,
            "fps": 8,
            "resolution": (720, 480),
            "vram_required": 16,
        },
        "cogvideox-5b-i2v": {
            "repo": "THUDM/CogVideoX-5b-I2V",
            "type": "image2video",
            "max_frames": 49,
            "fps": 8,
            "resolution": (720, 480),
            "vram_required": 16,
        },
    }

    def __init__(self):
        project_root = Path(__file__).parent.parent.parent
        self.models_dir = project_root / "data" / "models" / "video_diffusion"
        self.cache_dir = Path(CACHE_DIR) / "generated_videos"
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._svd_pipeline = None
        self._cogvideox_pipeline = None
        self._current_model = None
        self._current_model_type = None  # "svd" or "cogvideox"

        self.device = "cpu"
        self.dtype = None
        self.gpu_vram_gb = 0
        if torch_available:
            if torch.cuda.is_available():
                self.device = "cuda"
                self.dtype = torch.float16
                try:
                    self.gpu_vram_gb = torch.cuda.get_device_properties(
                        0
                    ).total_memory / (1024**3)
                except Exception:
                    self.gpu_vram_gb = 8
                logger.info(
                    f"Video generator using CUDA with float16 (VRAM: {self.gpu_vram_gb:.1f}GB)"
                )
            else:
                self.device = "cpu"
                self.dtype = torch.float32
                logger.info("Video generator using CPU with float32")

        self.service_available = (
            diffusers_available or pillow_available or imageio_available
        )
        self.svd_available = (
            svd_available and torch_available and image_generator_available
        )
        self.cogvideox_available = cogvideox_available and torch_available
        self.ai_available = self.svd_available or self.cogvideox_available

        if not self.service_available:
            logger.error(
                "Video generation service unavailable - missing required dependencies"
            )
        else:
            models_str = []
            if self.svd_available:
                models_str.append("SVD")
            if self.cogvideox_available:
                models_str.append("CogVideoX")
            if models_str:
                logger.info(
                    f"Video generation service available with AI support: {', '.join(models_str)}"
                )
            else:
                logger.info(
                    "Video generation service available (placeholder mode only)"
                )

    def _make_output_dirs(
        self, batch_dir: Path, item_id: str
    ) -> Tuple[Path, Path, Path]:
        item_dir = batch_dir / item_id
        videos_dir = item_dir / "videos"
        frames_dir = item_dir / "frames"
        thumbs_dir = item_dir / "thumbnails"
        videos_dir.mkdir(parents=True, exist_ok=True)
        frames_dir.mkdir(parents=True, exist_ok=True)
        thumbs_dir.mkdir(parents=True, exist_ok=True)
        return videos_dir, frames_dir, thumbs_dir

    def _load_svd_pipeline(self, model_key: str = "svd"):
        if not svd_available:
            raise RuntimeError("SVD not available - diffusers not installed properly")

        model_id = self.SVD_MODELS.get(model_key, self.SVD_MODELS["svd"])

        if self._svd_pipeline is not None and self._current_model == model_id:
            return self._svd_pipeline

        if self._svd_pipeline is not None:
            del self._svd_pipeline
            self._svd_pipeline = None
            gc.collect()
            if torch_available and torch.cuda.is_available():
                torch.cuda.empty_cache()

        gc.collect()
        if torch_available and torch.cuda.is_available():
            torch.cuda.empty_cache()
            logger.info(
                f"GPU memory before loading SVD: {torch.cuda.memory_allocated() / 1024**3:.2f} GB"
            )

        logger.info(f"Loading SVD model: {model_id}")

        try:
            self._svd_pipeline = StableVideoDiffusionPipeline.from_pretrained(
                model_id,
                torch_dtype=self.dtype,
                variant="fp16" if self.dtype == torch.float16 else None,
                cache_dir=str(self.models_dir),
            )

            if self.device == "cuda":
                try:
                    self._svd_pipeline.enable_sequential_cpu_offload()
                    logger.info("Enabled sequential CPU offload for memory efficiency")
                except Exception as e:
                    logger.warning(
                        f"Sequential CPU offload failed, trying model offload: {e}"
                    )
                    try:
                        self._svd_pipeline.enable_model_cpu_offload()
                        logger.info("Enabled model CPU offload")
                    except Exception as e2:
                        logger.warning(
                            f"Model CPU offload also failed, using direct GPU: {e2}"
                        )
                        self._svd_pipeline.to(self.device)

                if hasattr(self._svd_pipeline, "enable_vae_slicing"):
                    self._svd_pipeline.enable_vae_slicing()
                    logger.info("Enabled VAE slicing")
            else:
                self._svd_pipeline.to(self.device)

            self._current_model = model_id
            logger.info(f"SVD model loaded successfully")
            return self._svd_pipeline

        except Exception as e:
            logger.error(f"Failed to load SVD model: {e}")
            raise RuntimeError(f"Failed to load SVD model: {e}")

    def _unload_image_generator(self):
        if image_generator_available:
            try:
                img_gen = get_image_generator()
                if hasattr(img_gen, "_pipeline") and img_gen._pipeline is not None:
                    logger.info("Unloading image generator pipeline to free GPU memory")
                    try:
                        if hasattr(img_gen._pipeline, "to"):
                            img_gen._pipeline.to("cpu")
                    except Exception:
                        pass
                    del img_gen._pipeline
                    img_gen._pipeline = None
                    if hasattr(img_gen, "_current_model"):
                        img_gen._current_model = None
                    if hasattr(img_gen, "_loaded_model"):
                        img_gen._loaded_model = None
                    gc.collect()
                    if torch_available and torch.cuda.is_available():
                        torch.cuda.empty_cache()
                        torch.cuda.synchronize()
                    logger.info("Image generator unloaded successfully")
            except Exception as e:
                logger.warning(f"Failed to unload image generator: {e}")

    def _generate_initial_image(
        self,
        prompt: str,
        width: int,
        height: int,
        seed: Optional[int] = None,
        num_inference_steps: int = 25,
        guidance_scale: float = 7.5,
    ) -> Optional[Image.Image]:
        if not image_generator_available:
            logger.error("Image generator not available for text-to-video")
            return None

        try:
            img_generator = get_image_generator()
            img_request = ImageGenerationRequest(
                prompt=prompt,
                width=width,
                height=height,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale,
                seed=seed,
                style="realistic",
            )
            result = img_generator.generate_image(img_request)

            if result.success and result.image_path:
                image = Image.open(result.image_path).convert("RGB")
                self._unload_image_generator()
                return image
            elif result.error:
                logger.error(f"Failed to generate initial image: {result.error}")
            return None

        except Exception as e:
            logger.error(f"Error generating initial image: {e}")
            return None

    def _unload_svd_pipeline(self):
        if self._svd_pipeline is not None:
            try:
                del self._svd_pipeline
                self._svd_pipeline = None
                if self._current_model_type == "svd":
                    self._current_model = None
                    self._current_model_type = None
                gc.collect()
                if torch_available and torch.cuda.is_available():
                    torch.cuda.empty_cache()
                logger.info("SVD pipeline unloaded to free GPU memory")
            except Exception as e:
                logger.warning(f"Failed to unload SVD pipeline: {e}")

    def _load_cogvideox_pipeline(self, model_key: str = "cogvideox-2b"):
        if not cogvideox_available:
            raise RuntimeError(
                "CogVideoX not available - diffusers version may be too old"
            )

        model_config = self.COGVIDEOX_MODELS.get(model_key)
        if not model_config:
            raise ValueError(f"Unknown CogVideoX model: {model_key}")

        model_id = model_config["repo"]
        model_type = model_config["type"]

        if self._cogvideox_pipeline is not None and self._current_model == model_id:
            return self._cogvideox_pipeline

        self._unload_all_pipelines()

        gc.collect()
        if torch_available and torch.cuda.is_available():
            torch.cuda.empty_cache()
            logger.info(
                f"GPU memory before loading CogVideoX: {torch.cuda.memory_allocated() / 1024**3:.2f} GB"
            )

        logger.info(f"Loading CogVideoX model: {model_id} (type: {model_type})")

        try:
            if model_type == "image2video":
                PipelineClass = CogVideoXImageToVideoPipeline
            else:
                PipelineClass = CogVideoXPipeline

            use_dtype = self.dtype
            if torch_available and torch.cuda.is_available():
                if torch.cuda.is_bf16_supported():
                    use_dtype = torch.bfloat16
                    logger.info("Using bfloat16 for better memory efficiency")

            self._cogvideox_pipeline = PipelineClass.from_pretrained(
                model_id,
                torch_dtype=use_dtype,
                cache_dir=str(self.models_dir),
            )

            if self.device == "cuda":
                try:
                    self._cogvideox_pipeline.enable_model_cpu_offload()
                    logger.info("Enabled model CPU offload for CogVideoX")
                except Exception as e:
                    logger.warning(f"Model CPU offload failed: {e}")
                    try:
                        self._cogvideox_pipeline.enable_sequential_cpu_offload()
                        logger.info("Enabled sequential CPU offload for CogVideoX")
                    except Exception as e2:
                        logger.warning(
                            f"Sequential CPU offload also failed, using direct GPU: {e2}"
                        )
                        self._cogvideox_pipeline.to(self.device)

                if hasattr(self._cogvideox_pipeline, "vae"):
                    if hasattr(self._cogvideox_pipeline.vae, "enable_slicing"):
                        self._cogvideox_pipeline.vae.enable_slicing()
                    if hasattr(self._cogvideox_pipeline.vae, "enable_tiling"):
                        self._cogvideox_pipeline.vae.enable_tiling()
                    logger.info("Enabled VAE slicing and tiling for CogVideoX")
                if hasattr(self._cogvideox_pipeline, "enable_vae_slicing"):
                    self._cogvideox_pipeline.enable_vae_slicing()
                if hasattr(self._cogvideox_pipeline, "enable_vae_tiling"):
                    self._cogvideox_pipeline.enable_vae_tiling()

                if hasattr(self._cogvideox_pipeline, "enable_attention_slicing"):
                    try:
                        self._cogvideox_pipeline.enable_attention_slicing(
                            slice_size="max"
                        )
                        logger.info("Enabled attention slicing for CogVideoX")
                    except Exception as e:
                        logger.warning(f"Attention slicing not available: {e}")

                try:
                    if hasattr(
                        self._cogvideox_pipeline,
                        "enable_xformers_memory_efficient_attention",
                    ):
                        self._cogvideox_pipeline.enable_xformers_memory_efficient_attention()
                        logger.info(
                            "Enabled xformers memory efficient attention for CogVideoX"
                        )
                except Exception as e:
                    logger.debug(f"xformers not available: {e}")
            else:
                self._cogvideox_pipeline.to(self.device)

            self._current_model = model_id
            self._current_model_type = "cogvideox"
            logger.info(f"CogVideoX model loaded successfully")
            return self._cogvideox_pipeline

        except Exception as e:
            logger.error(f"Failed to load CogVideoX model: {e}")
            raise RuntimeError(f"Failed to load CogVideoX model: {e}")

    def _unload_cogvideox_pipeline(self):
        if self._cogvideox_pipeline is not None:
            try:
                del self._cogvideox_pipeline
                self._cogvideox_pipeline = None
                if self._current_model_type == "cogvideox":
                    self._current_model = None
                    self._current_model_type = None
                gc.collect()
                if torch_available and torch.cuda.is_available():
                    torch.cuda.empty_cache()
                logger.info("CogVideoX pipeline unloaded to free GPU memory")
            except Exception as e:
                logger.warning(f"Failed to unload CogVideoX pipeline: {e}")

    def _unload_all_pipelines(self):
        self._unload_svd_pipeline()
        self._unload_cogvideox_pipeline()
        self._unload_image_generator()

    def _generate_cogvideox_frames(
        self,
        prompt: str,
        frames_dir: Path,
        num_frames: int = 49,
        num_inference_steps: int = 50,
        guidance_scale: float = 6.0,
        seed: Optional[int] = None,
        model_key: str = "cogvideox-2b",
        image: Optional[Image.Image] = None,
    ) -> List[str]:
        if not cogvideox_available:
            raise RuntimeError("CogVideoX not available")

        model_config = self.COGVIDEOX_MODELS.get(model_key)
        if not model_config:
            raise ValueError(f"Unknown CogVideoX model: {model_key}")

        frame_paths: List[str] = []

        try:
            logger.info(
                "Aggressively freeing GPU memory before CogVideoX generation..."
            )
            self._unload_all_pipelines()

            cleanup_result = force_clear_gpu_memory()
            if cleanup_result.get("success"):
                logger.info(
                    f"GPU cleanup freed {cleanup_result.get('freed_gb', 0):.2f} GB"
                )
            else:
                logger.warning(
                    f"GPU cleanup may have failed: {cleanup_result.get('error', 'unknown')}"
                )

            max_frames = model_config["max_frames"]
            actual_num_frames = min(num_frames, max_frames)
            vram_required = model_config.get("vram_required", 16)

            if torch_available and torch.cuda.is_available():
                total_mem = torch.cuda.get_device_properties(0).total_memory
                allocated = torch.cuda.memory_allocated()
                try:
                    reserved = torch.cuda.memory_reserved(0)
                except (AttributeError, TypeError):
                    reserved = allocated
                free_mem = total_mem - reserved

                logger.info(
                    f"GPU memory after aggressive cleanup: {allocated / 1024**3:.2f} GB allocated, {reserved / 1024**3:.2f} GB reserved, {free_mem / 1024**3:.2f} GB free"
                )

                if free_mem / 1024**3 < vram_required * 0.8:
                    logger.warning(
                        f"Low GPU memory after cleanup: {free_mem / 1024**3:.2f} GB free, {vram_required} GB required"
                    )
                    logger.warning(
                        "Something may be holding GPU memory. Consider restarting the backend."
                    )
                    if actual_num_frames > 24:
                        logger.info(
                            f"Reducing frames from {actual_num_frames} to 24 due to low memory"
                        )
                        actual_num_frames = 24

            pipeline = self._load_cogvideox_pipeline(model_key)

            if torch_available and torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()

            generator = None
            if seed is not None and torch_available:
                generator = torch.Generator(device="cpu").manual_seed(seed)

            if not torch_available or not torch.cuda.is_available():
                max_frames = model_config["max_frames"]
                actual_num_frames = min(num_frames, max_frames)

            target_width, target_height = model_config["resolution"]

            logger.info(
                f"Generating {actual_num_frames} frames with CogVideoX at {target_width}x{target_height}..."
            )

            if torch_available and torch.cuda.is_available():
                allocated = torch.cuda.memory_allocated()
                try:
                    reserved = torch.cuda.memory_reserved(0)
                except (AttributeError, TypeError):
                    reserved = allocated
                logger.info(
                    f"GPU memory before inference: {allocated / 1024**3:.2f} GB allocated, {reserved / 1024**3:.2f} GB reserved"
                )

            inference_mode = torch.inference_mode if torch_available else nullcontext
            with inference_mode() if torch_available else nullcontext():
                with torch.no_grad() if torch_available else nullcontext():
                    if model_config["type"] == "image2video" and image is not None:
                        image_resized = image.resize(
                            (target_width, target_height), Image.Resampling.LANCZOS
                        )
                        video_frames = pipeline(
                            prompt=prompt,
                            image=image_resized,
                            num_frames=actual_num_frames,
                            num_inference_steps=num_inference_steps,
                            guidance_scale=guidance_scale,
                            generator=generator,
                        ).frames[0]
                    else:
                        video_frames = pipeline(
                            prompt=prompt,
                            num_frames=actual_num_frames,
                            num_inference_steps=num_inference_steps,
                            guidance_scale=guidance_scale,
                            generator=generator,
                        ).frames[0]

                    if torch_available and torch.cuda.is_available():
                        torch.cuda.empty_cache()
                        torch.cuda.synchronize()

            for idx, frame in enumerate(video_frames):
                frame_name = f"frame_{idx + 1:04d}.png"
                frame_path = frames_dir / frame_name
                if not isinstance(frame, Image.Image):
                    frame = Image.fromarray(frame)
                frame.save(frame_path)
                frame_paths.append(str(frame_path))

                if (
                    torch_available
                    and torch.cuda.is_available()
                    and (idx + 1) % 10 == 0
                ):
                    torch.cuda.empty_cache()

            logger.info(
                f"Generated {len(frame_paths)} frames with CogVideoX successfully"
            )

            del video_frames
            gc.collect()

            if torch_available and torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
                gc.collect()
                torch.cuda.empty_cache()

            self._unload_cogvideox_pipeline()

            return frame_paths

        except torch.cuda.OutOfMemoryError as e:
            logger.error(f"GPU out of memory during CogVideoX generation: {e}")
            self._unload_cogvideox_pipeline()
            if torch_available and torch.cuda.is_available():
                torch.cuda.empty_cache()
            raise RuntimeError(
                f"GPU out of memory - try cogvideox-2b model or reduce frames: {e}"
            )
        except RuntimeError as e:
            error_str = str(e)
            if (
                "expandable_segment" in error_str
                or "INTERNAL ASSERT FAILED" in error_str
            ):
                logger.error(
                    f"CogVideoX CUDA allocator error (expandable_segment): {e}"
                )
                logger.info("This error is often caused by memory fragmentation. Try:")
                logger.info("1. Restarting the application to clear GPU memory")
                logger.info("2. Using cogvideox-2b instead of cogvideox-5b")
                logger.info("3. Reducing the number of frames")
                self._unload_cogvideox_pipeline()
                if torch_available and torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    torch.cuda.synchronize()
                raise RuntimeError(
                    "CUDA memory allocation error. This is often due to memory fragmentation. "
                    "Try restarting the application or using a smaller model/fewer frames."
                ) from e
            else:
                logger.error(f"CogVideoX frame generation failed: {e}")
                self._unload_cogvideox_pipeline()
                raise
        except Exception as e:
            logger.error(f"CogVideoX frame generation failed: {e}")
            self._unload_cogvideox_pipeline()
            if torch_available and torch.cuda.is_available():
                torch.cuda.empty_cache()
            raise

    def _is_cogvideox_model(self, model_key: str) -> bool:
        return model_key in self.COGVIDEOX_MODELS

    def _is_svd_model(self, model_key: str) -> bool:
        return model_key in self.SVD_MODELS

    def _clamp_resolution(self, width: int, height: int) -> Tuple[int, int]:
        try:
            width = int(width)
            height = int(height)
        except Exception:
            return 512, 512

        max_side = 768
        min_side = 128
        width = max(min_side, min(width, max_side))
        height = max(min_side, min(height, max_side))

        scale = min(max_side / width, max_side / height, 1.0)
        width = int(round(width * scale))
        height = int(round(height * scale))

        width = max(min_side, (width // 8) * 8)
        height = max(min_side, (height // 8) * 8)

        return width, height

    def _generate_svd_frames(
        self,
        image: Image.Image,
        frames_dir: Path,
        num_frames: int = 14,
        fps: int = 7,
        target_size: Tuple[int, int] = (512, 512),
        motion_bucket_id: int = 127,
        noise_aug_strength: float = 0.02,
        num_inference_steps: int = 25,
        guidance_scale: float = 7.5,
        seed: Optional[int] = None,
        model_key: str = "svd",
    ) -> List[str]:
        if not svd_available:
            raise RuntimeError("SVD not available")

        frame_paths: List[str] = []

        try:
            pipeline = self._load_svd_pipeline(model_key)

            target_width, target_height = target_size
            image_resized = image.resize(
                (target_width, target_height), Image.Resampling.LANCZOS
            )

            generator = None
            if seed is not None and torch_available:
                generator = torch.Generator(device="cpu").manual_seed(seed)

            svd_max_frames = 25 if model_key == "svd-xt" else 14
            actual_num_frames = max(1, min(num_frames, svd_max_frames))

            logger.info(
                f"Generating {actual_num_frames} frames with SVD at {target_width}x{target_height}..."
            )

            if torch_available and torch.cuda.is_available():
                logger.info(
                    f"GPU memory before inference: {torch.cuda.memory_allocated() / 1024**3:.2f} GB"
                )

            frames = pipeline(
                image_resized,
                num_frames=actual_num_frames,
                num_inference_steps=num_inference_steps,
                motion_bucket_id=motion_bucket_id,
                noise_aug_strength=noise_aug_strength,
                generator=generator,
                decode_chunk_size=2,
            ).frames[0]

            for idx, frame in enumerate(frames):
                frame_name = f"frame_{idx + 1:04d}.png"
                frame_path = frames_dir / frame_name
                if not isinstance(frame, Image.Image):
                    frame = Image.fromarray(frame)
                frame.save(frame_path)
                frame_paths.append(str(frame_path))

            logger.info(f"Generated {len(frame_paths)} frames successfully")

            self._unload_svd_pipeline()

            return frame_paths

        except torch.cuda.OutOfMemoryError as e:
            logger.error(f"GPU out of memory during SVD generation: {e}")
            self._unload_svd_pipeline()
            raise RuntimeError(
                f"GPU out of memory - try reducing resolution or frames: {e}"
            )
        except Exception as e:
            logger.error(f"SVD frame generation failed: {e}")
            self._unload_svd_pipeline()
            raise

    def _generate_placeholder_frames(
        self,
        frames_dir: Path,
        num_frames: int,
        size: Tuple[int, int],
        prompt: str,
    ) -> List[str]:
        frame_paths: List[str] = []
        if not pillow_available:
            raise RuntimeError("Pillow is required for placeholder frame generation")

        width, height = size
        font = None
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

        for idx in range(num_frames):
            img = Image.new("RGB", (width, height), color=(20 + idx * 3 % 200, 40, 80))
            draw = ImageDraw.Draw(img)
            text = f"Frame {idx + 1}/{num_frames}\nPrompt: {prompt[:60]}"
            if font:
                draw.text((10, 10), text, fill=(255, 255, 255), font=font)
            else:
                draw.text((10, 10), text, fill=(255, 255, 255))
            frame_name = f"frame_{idx + 1:04d}.png"
            frame_path = frames_dir / frame_name
            img.save(frame_path)
            frame_paths.append(str(frame_path))
        return frame_paths

    def _combine_frames_to_video(
        self,
        frames_dir: Path,
        output_video_path: Path,
        fps: int,
    ) -> Optional[str]:
        if not imageio_available:
            logger.error("imageio is required to combine frames into video")
            return None

        frame_files = sorted(frames_dir.glob("frame_*.png"))
        if not frame_files:
            logger.error("No frames found to combine")
            return None

        try:
            frames = [imageio.imread(frame) for frame in frame_files]
            imageio.mimwrite(
                str(output_video_path), frames, fps=fps, macro_block_size=1
            )
            return str(output_video_path)
        except Exception as e:
            logger.error(f"Failed to combine frames into video: {e}")
            return None

    def generate_video(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        if not self.service_available:
            return VideoGenerationResult(
                success=False,
                error="Video generation service not available - missing dependencies",
                prompt_used=request.prompt,
            )

        is_batch_controlled = (
            request.metadata.get("batch_controlled", False)
            if request.metadata
            else False
        )
        gpu_lock_acquired = False

        if (
            not is_batch_controlled
            and gpu_coordinator_available
            and get_gpu_coordinator
        ):
            coordinator = get_gpu_coordinator()
            item_id_for_lock = (
                request.metadata.get("item_id", "single_video")
                if request.metadata
                else "single_video"
            )
            lock_result = coordinator.acquire_for_video_generation(
                batch_id=f"single_{item_id_for_lock}", lease_seconds=1800
            )
            if not lock_result.get("success"):
                return VideoGenerationResult(
                    success=False,
                    error=f"Could not acquire GPU: {lock_result.get('error')}",
                    prompt_used=request.prompt,
                )
            gpu_lock_acquired = True
            logger.info(f"GPU lock acquired for single video generation")

        try:
            return self._generate_video_impl(request)
        finally:
            if gpu_lock_acquired and gpu_coordinator_available and get_gpu_coordinator:
                coordinator = get_gpu_coordinator()
                coordinator.release_video_generation_lock(restart_ollama=True)
                logger.info("GPU lock released after single video generation")

    def _generate_video_impl(
        self, request: VideoGenerationRequest
    ) -> VideoGenerationResult:
        batch_dir = request.output_dir or (self.cache_dir / f"batch_{uuid.uuid4().hex}")
        batch_dir = Path(batch_dir)
        item_id = request.metadata.get("item_id") if request.metadata else None
        if not item_id:
            item_id = f"item_{uuid.uuid4().hex}"
            request.metadata["item_id"] = item_id
        videos_dir, frames_dir, thumbs_dir = self._make_output_dirs(batch_dir, item_id)

        result = VideoGenerationResult(
            success=False,
            prompt_used=request.prompt,
            metadata=request.metadata,
        )

        seed_value = request.seed
        if seed_value not in (None, ""):
            try:
                seed_value = int(seed_value)
            except Exception:
                seed_value = None

        def _rel(path: Path) -> str:
            try:
                return str(path.relative_to(batch_dir))
            except Exception:
                return str(path)

        try:
            frame_paths = []

            is_cogvideox = self._is_cogvideox_model(request.model)
            is_svd = self._is_svd_model(request.model)

            logger.info(
                f"Video generation request - model: {request.model}, is_cogvideox: {is_cogvideox}, is_svd: {is_svd}"
            )
            logger.info(
                f"AI availability - ai_available: {self.ai_available}, cogvideox_available: {self.cogvideox_available}, svd_available: {self.svd_available}"
            )

            image_path = (
                request.metadata.get("image_path") if request.metadata else None
            )
            has_input_image = image_path and Path(image_path).exists()

            if self.ai_available:
                logger.info(
                    f"Generating video with {'CogVideoX' if is_cogvideox else 'SVD'} for prompt: {request.prompt[:100]}..."
                )

                if is_cogvideox and self.cogvideox_available:
                    try:
                        initial_image = None
                        model_config = self.COGVIDEOX_MODELS.get(request.model, {})

                        if model_config.get("type") == "image2video":
                            if has_input_image:
                                logger.info(f"Using provided image: {image_path}")
                                initial_image = Image.open(image_path).convert("RGB")
                            else:
                                logger.warning(
                                    "CogVideoX I2V model requires an input image"
                                )
                                request.model = "cogvideox-2b"
                                model_config = self.COGVIDEOX_MODELS.get(
                                    request.model, {}
                                )

                        frame_paths = self._generate_cogvideox_frames(
                            prompt=request.prompt,
                            frames_dir=frames_dir,
                            num_frames=request.duration_frames,
                            num_inference_steps=request.num_inference_steps,
                            guidance_scale=request.guidance_scale,
                            seed=seed_value,
                            model_key=request.model,
                            image=initial_image,
                        )
                        logger.info(
                            f"CogVideoX generation successful: {len(frame_paths)} frames"
                        )
                    except Exception as e:
                        import traceback

                        logger.error(f"CogVideoX generation failed: {e}")
                        logger.error(f"Full traceback: {traceback.format_exc()}")
                        result.error = f"CogVideoX generation failed: {e}"
                        frame_paths = []

                elif is_svd and self.svd_available:
                    initial_image = None

                    if has_input_image:
                        logger.info(f"Using provided image: {image_path}")
                        initial_image = Image.open(image_path).convert("RGB")
                    else:
                        logger.info("Generating initial image from prompt...")
                        initial_image = self._generate_initial_image(
                            prompt=request.prompt,
                            width=request.width,
                            height=request.height,
                            seed=seed_value,
                            num_inference_steps=request.num_inference_steps,
                            guidance_scale=request.guidance_scale,
                        )

                    if initial_image is not None:
                        try:
                            motion_bucket_id = int(request.motion_strength * 127)
                            motion_bucket_id = max(1, min(255, motion_bucket_id))

                            target_width, target_height = self._clamp_resolution(
                                request.width, request.height
                            )

                            frame_paths = self._generate_svd_frames(
                                image=initial_image,
                                frames_dir=frames_dir,
                                num_frames=request.duration_frames,
                                fps=request.fps,
                                target_size=(target_width, target_height),
                                motion_bucket_id=motion_bucket_id,
                                num_inference_steps=request.num_inference_steps,
                                guidance_scale=request.guidance_scale,
                                seed=seed_value,
                                model_key=request.model,
                            )
                            logger.info(
                                f"SVD generation successful: {len(frame_paths)} frames"
                            )
                        except Exception as e:
                            logger.warning(f"SVD generation failed: {e}")
                            frame_paths = []
                    else:
                        logger.warning("Failed to get initial image for SVD")
                else:
                    logger.warning(f"Model {request.model} not available")

            if not frame_paths:
                logger.info("Using placeholder frame generation")
                frame_paths = self._generate_placeholder_frames(
                    frames_dir=frames_dir,
                    num_frames=max(1, request.duration_frames),
                    size=(request.width, request.height),
                    prompt=request.prompt,
                )

            result.frame_paths = [_rel(Path(p)) for p in frame_paths]

            if frame_paths and pillow_available:
                try:
                    thumb_img = Image.open(frame_paths[0])
                    thumb_path = thumbs_dir / "thumb.jpg"
                    thumb_img.save(thumb_path, format="JPEG")
                    result.thumbnail_path = _rel(thumb_path)
                except Exception as e:
                    logger.warning(f"Failed to create thumbnail: {e}")

            if request.generate_frames_only and not request.combine_frames:
                result.success = True
                return result

            video_name = f"video_{uuid.uuid4().hex}.mp4"
            video_path = videos_dir / video_name
            combined = self._combine_frames_to_video(
                frames_dir=frames_dir,
                output_video_path=video_path,
                fps=max(1, request.fps),
            )
            if combined:
                result.video_path = _rel(Path(combined))
                result.success = True
            else:
                result.success = bool(frame_paths)
                if not result.success:
                    result.error = "Failed to combine frames into video"
                else:
                    result.error = "Frames generated but video muxing is unavailable"
        except Exception as e:
            logger.error(f"Error during video generation: {e}")
            result.error = str(e)
            result.success = False

        return result


_video_generator_instance: Optional[OfflineVideoGenerator] = None


def get_video_generator() -> OfflineVideoGenerator:
    global _video_generator_instance
    if _video_generator_instance is None:
        _video_generator_instance = OfflineVideoGenerator()
    return _video_generator_instance
