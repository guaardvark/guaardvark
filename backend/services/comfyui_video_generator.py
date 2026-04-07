
import logging
import json
import subprocess
import time
import os
import shutil
import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
import uuid

import requests

logger = logging.getLogger(__name__)

try:
    from backend.config import CACHE_DIR, COMFYUI_URL, COMFYUI_OUTPUT_DIR
    config_available = True
except ImportError:
    config_available = False
    CACHE_DIR = "/tmp/guaardvark_cache"


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
    interpolation_multiplier: int = 2  # 1 = no interpolation, 2 = double fps, 4 = quad fps
    prompt_style: str = "cinematic"   # Enhancement style: cinematic, realistic, artistic, anime, none
    enhance_prompt: bool = True       # Whether to run prompt through the enhancer


@dataclass
class VideoGenerationResult:
    success: bool
    prompt_used: str = ""
    video_path: Optional[str] = None
    frame_paths: List[str] = field(default_factory=list)
    thumbnail_path: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)


class ComfyUIVideoGenerator:

    def __init__(self):
        project_root = Path(__file__).parent.parent.parent

        self.comfy_url = COMFYUI_URL if config_available else os.environ.get("GUAARDVARK_COMFYUI_URL", "http://127.0.0.1:8188")

        self.templates_dir = project_root / "data" / "templates"
        self.templates_dir.mkdir(parents=True, exist_ok=True)

        self.cache_dir = Path(CACHE_DIR) / "generated_videos"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Default output dir for standalone (non-batch) video generation
        try:
            from backend.config import UPLOAD_DIR as _upload_dir
            self.default_output_dir = Path(_upload_dir) / "Videos"
        except ImportError:
            self.default_output_dir = self.cache_dir
        self.default_output_dir.mkdir(parents=True, exist_ok=True)

        self.comfy_output_dir = Path(COMFYUI_OUTPUT_DIR if config_available else os.environ.get('COMFYUI_OUTPUT_DIR', os.path.join(os.environ.get('GUAARDVARK_ROOT', '.'), 'data', 'outputs', 'video')))

        self.service_available = self._check_comfyui_connection()

        if self.service_available:
            logger.info(f"ComfyUI video generator connected to {self.comfy_url}")
        else:
            logger.warning(f"ComfyUI not available at {self.comfy_url}. Video generation will fail unless ComfyUI is started.")

    def _check_comfyui_connection(self) -> bool:
        try:
            response = requests.get(self.comfy_url, timeout=2)
            return response.status_code == 200
        except requests.exceptions.RequestException:
            return False

    def _upload_image_to_comfyui(self, image_path: str) -> Optional[str]:
        try:
            with open(image_path, 'rb') as f:
                files = {'image': f}
                data = {'type': 'input', 'overwrite': 'true'}
                response = requests.post(
                    f"{self.comfy_url}/upload/image",
                    files=files,
                    data=data,
                    timeout=30
                )
                response.raise_for_status()

            result = response.json()
            uploaded_name = result.get("name")
            logger.info(f"Uploaded image to ComfyUI as: {uploaded_name}")
            return uploaded_name

        except Exception as e:
            logger.error(f"Failed to upload image to ComfyUI: {e}")
            return None

    def _create_svd_workflow(
        self,
        image_filename: str,
        num_frames: int = 25,
        motion_bucket_id: int = 127,
        fps: int = 7,
        seed: Optional[int] = None,
    ) -> dict:
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)

        workflow = {
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": 20,
                    "cfg": 2.5,
                    "sampler_name": "euler",
                    "scheduler": "karras",
                    "denoise": 1,
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["5", 0]
                }
            },
            "4": {
                "class_type": "ImageOnlyCheckpointLoader",
                "inputs": {
                    "ckpt_name": "svd_xt.safetensors"
                }
            },
            "5": {
                "class_type": "SVD_img2vid_Conditioning",
                "inputs": {
                    "width": 512,
                    "height": 512,
                    "video_frames": num_frames,
                    "motion_bucket_id": motion_bucket_id,
                    "fps": fps,
                    "augmentation_level": 0,
                    "clip_vision": ["4", 1],
                    "init_image": ["8", 0],
                    "vae": ["4", 2]
                }
            },
            "6": {
                "class_type": "EmptyLatentImage",
                "inputs": {
                    "width": 512,
                    "height": 512,
                    "batch_size": 1
                }
            },
            "7": {
                "class_type": "EmptyLatentImage",
                "inputs": {
                    "width": 512,
                    "height": 512,
                    "batch_size": 1
                }
            },
            "8": {
                "class_type": "LoadImage",
                "inputs": {
                    "image": image_filename
                }
            },
            "9": {
                "class_type": "VAEDecode",
                "inputs": {
                    "samples": ["3", 0],
                    "vae": ["4", 2]
                }
            },
            "10": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "frame_rate": fps,
                    "loop_count": 0,
                    "filename_prefix": "svd_video",
                    "format": "video/h264-mp4",
                    "images": ["9", 0]
                }
            }
        }

        return workflow

    # ── CogVideoX model mapping ──────────────────────────────────────────────

    COGVIDEOX_MODELS = {
        "cogvideox-2b": "THUDM/CogVideoX-2b",
        "cogvideox-5b": "THUDM/CogVideoX-5b",
        "cogvideox-5b-i2v": "kijai/CogVideoX-5b-1.5-I2V",
    }

    # ── Wan 2.2 model mapping ────────────────────────────────────────────────

    WAN22_MODELS = {
        "wan22-14b": {
            "unet_high": "Wan2.2-T2V-A14B-HighNoise-Q5_K_M.gguf",
            "unet_low": "Wan2.2-T2V-A14B-LowNoise-Q5_K_M.gguf",
            "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "vae": "wan_2.1_vae.safetensors",
        },
    }

    def _add_cogvideox_optional_nodes(
        self,
        workflow: dict,
        sampler_node_id: str,
        teacache_threshold: Optional[float] = None,
        feta_weight: Optional[float] = None,
    ) -> dict:
        """Add optional TeaCache and/or FETA nodes to a CogVideoX workflow.

        Args:
            workflow: The ComfyUI workflow dict (modified in-place).
            sampler_node_id: Node ID of the CogVideoSampler.
            teacache_threshold: If set, add TeaCache with this rel_l1_thresh (0.1-1.0).
            feta_weight: If set, add Enhance-A-Video with this weight (0.1-3.0).

        Returns:
            The modified workflow dict.
        """
        existing_ids = [int(k) for k in workflow.keys() if k.isdigit()]
        next_id = max(existing_ids) + 1

        if teacache_threshold is not None:
            tea_id = str(next_id)
            next_id += 1
            workflow[tea_id] = {
                "class_type": "CogVideoXTeaCache",
                "inputs": {
                    "rel_l1_thresh": float(teacache_threshold),
                }
            }
            workflow[sampler_node_id]["inputs"]["teacache_args"] = [tea_id, 0]
            logger.info(f"Added TeaCache (threshold={teacache_threshold}) to CogVideoX workflow")

        if feta_weight is not None:
            feta_id = str(next_id)
            next_id += 1
            workflow[feta_id] = {
                "class_type": "CogVideoEnhanceAVideo",
                "inputs": {
                    "weight": float(feta_weight),
                    "start_percent": 0.0,
                    "end_percent": 1.0,
                }
            }
            workflow[sampler_node_id]["inputs"]["feta_args"] = [feta_id, 0]
            logger.info(f"Added Enhance-A-Video (weight={feta_weight}) to CogVideoX workflow")

        return workflow

    def _create_cogvideox_text2video_workflow(
        self,
        prompt: str,
        negative_prompt: str = "",
        model_name: str = "THUDM/CogVideoX-2b",
        num_frames: int = 49,
        num_inference_steps: int = 50,
        guidance_scale: float = 6.0,
        width: int = 720,
        height: int = 480,
        seed: Optional[int] = None,
        fps: int = 8,
        interpolation_multiplier: int = 2,
    ) -> dict:
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)

        workflow = {
            "1": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": "t5/google_t5-v1_1-xxl_encoderonly-fp8_e4m3fn.safetensors",
                    "type": "sd3",
                }
            },
            "2": {
                "class_type": "CogVideoTextEncode",
                "inputs": {
                    "clip": ["1", 0],
                    "prompt": prompt,
                    "strength": 1,
                    "force_offload": False,
                }
            },
            "3": {
                "class_type": "CogVideoTextEncode",
                "inputs": {
                    "clip": ["2", 1],
                    "prompt": negative_prompt,
                    "strength": 1,
                    "force_offload": True,
                }
            },
            "4": {
                "class_type": "DownloadAndLoadCogVideoModel",
                "inputs": {
                    "model": model_name,
                    "precision": "bf16",
                    "fp8_transformer": "disabled",
                    "compile": False,
                    "attention_mode": "sdpa",
                    "device": "main_device",
                }
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {
                    "width": width,
                    "height": height,
                    "batch_size": 1,
                }
            },
            "6": {
                "class_type": "CogVideoSampler",
                "inputs": {
                    "model": ["4", 0],
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "samples": ["5", 0],
                    "num_frames": num_frames,
                    "steps": num_inference_steps,
                    "cfg": guidance_scale,
                    "seed": seed,
                    "control_after_generate": "fixed",
                    "scheduler": "CogVideoXDDIM",
                    "denoise_strength": 1.0,
                }
            },
            "7": {
                "class_type": "CogVideoDecode",
                "inputs": {
                    "vae": ["4", 1],
                    "samples": ["6", 0],
                    "enable_vae_tiling": True,
                    "tile_sample_min_height": 240,
                    "tile_sample_min_width": 360,
                    "tile_overlap_factor_height": 0.2,
                    "tile_overlap_factor_width": 0.2,
                    "auto_tile_size": True,
                }
            },
            "8": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["7", 0],
                    "frame_rate": fps,
                    "loop_count": 0,
                    "filename_prefix": "cogvideo",
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "pingpong": False,
                    "save_output": True,
                    "videopreview": {
                        "hidden": False,
                        "paused": False,
                        "params": {},
                    },
                }
            },
        }

        # Add RIFE frame interpolation if multiplier > 1
        if interpolation_multiplier > 1:
            self._add_rife_interpolation(
                workflow,
                source_node_id="7",        # CogVideoDecode
                video_combine_node_id="8",  # VHS_VideoCombine
                base_fps=fps,
                multiplier=interpolation_multiplier,
            )

        return workflow

    def _create_cogvideox_i2v_workflow(
        self,
        image_filename: str,
        prompt: str,
        negative_prompt: str = "",
        model_name: str = "kijai/CogVideoX-5b-1.5-I2V",
        num_frames: int = 49,
        num_inference_steps: int = 50,
        guidance_scale: float = 6.0,
        width: int = 720,
        height: int = 480,
        seed: Optional[int] = None,
        fps: int = 8,
        interpolation_multiplier: int = 2,
    ) -> dict:
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)

        workflow = {
            "1": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": "t5/google_t5-v1_1-xxl_encoderonly-fp8_e4m3fn.safetensors",
                    "type": "sd3",
                }
            },
            "2": {
                "class_type": "CogVideoTextEncode",
                "inputs": {
                    "clip": ["1", 0],
                    "prompt": prompt,
                    "strength": 1,
                    "force_offload": False,
                }
            },
            "3": {
                "class_type": "CogVideoTextEncode",
                "inputs": {
                    "clip": ["2", 1],
                    "prompt": negative_prompt,
                    "strength": 1,
                    "force_offload": True,
                }
            },
            "4": {
                "class_type": "DownloadAndLoadCogVideoModel",
                "inputs": {
                    "model": model_name,
                    "precision": "bf16",
                    "fp8_transformer": "disabled",
                    "compile": False,
                    "attention_mode": "sdpa",
                    "device": "main_device",
                }
            },
            "5": {
                "class_type": "LoadImage",
                "inputs": {
                    "image": image_filename,
                }
            },
            "10": {
                "class_type": "ImageResizeKJ",
                "inputs": {
                    "image": ["5", 0],
                    "width_input": width,
                    "height_input": height,
                    "interpolation": "lanczos",
                    "keep_proportion": False,
                    "divisible_by": 16,
                }
            },
            "9": {
                "class_type": "CogVideoImageEncode",
                "inputs": {
                    "vae": ["4", 1],
                    "start_image": ["10", 0],
                }
            },
            "6": {
                "class_type": "CogVideoSampler",
                "inputs": {
                    "model": ["4", 0],
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "image_cond_latents": ["9", 0],
                    "num_frames": num_frames,
                    "steps": num_inference_steps,
                    "cfg": guidance_scale,
                    "seed": seed,
                    "control_after_generate": "fixed",
                    "scheduler": "CogVideoXDDIM",
                    "denoise_strength": 1.0,
                }
            },
            "7": {
                "class_type": "CogVideoDecode",
                "inputs": {
                    "vae": ["4", 1],
                    "samples": ["6", 0],
                    "enable_vae_tiling": True,
                    "tile_sample_min_height": 240,
                    "tile_sample_min_width": 360,
                    "tile_overlap_factor_height": 0.2,
                    "tile_overlap_factor_width": 0.2,
                    "auto_tile_size": True,
                }
            },
            "8": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["7", 0],
                    "frame_rate": fps,
                    "loop_count": 0,
                    "filename_prefix": "cogvideo_i2v",
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "pingpong": False,
                    "save_output": True,
                    "videopreview": {
                        "hidden": False,
                        "paused": False,
                        "params": {},
                    },
                }
            },
        }

        # Add RIFE frame interpolation if multiplier > 1
        if interpolation_multiplier > 1:
            self._add_rife_interpolation(
                workflow,
                source_node_id="7",        # CogVideoDecode
                video_combine_node_id="8",  # VHS_VideoCombine
                base_fps=fps,
                multiplier=interpolation_multiplier,
            )

        return workflow

    def _create_wan22_t2v_workflow(
        self,
        prompt: str,
        negative_prompt: str = "",
        model_key: str = "wan22-14b",
        num_frames: int = 81,
        num_inference_steps: int = 20,
        guidance_scale: float = 3.5,
        width: int = 640,
        height: int = 640,
        seed: Optional[int] = None,
        fps: int = 16,
        interpolation_multiplier: int = 2,
    ) -> dict:
        """Build a ComfyUI API-format workflow for Wan 2.2 MoE text-to-video.

        Uses two-pass architecture: HighNoise expert for layout/motion,
        LowNoise expert for detail refinement. GGUF models loaded via
        ComfyUI-GGUF custom node (UnetLoaderGGUF).
        """
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)

        model_files = self.WAN22_MODELS.get(model_key, self.WAN22_MODELS["wan22-14b"])

        # Default negative prompt for anatomy quality
        if not negative_prompt:
            negative_prompt = (
                "blurry, low quality, extra fingers, extra limbs, deformed hands, "
                "deformed face, disfigured, static, overexposed, worst quality, "
                "NSFW, nude"
            )

        midpoint = num_inference_steps // 2

        workflow = {
            # ── Model Loading ──────────────────────────────────────────────
            # Node 1: Load HighNoise GGUF expert
            "1": {
                "class_type": "UnetLoaderGGUF",
                "inputs": {
                    "unet_name": model_files["unet_high"],
                }
            },
            # Node 2: Load LowNoise GGUF expert
            "2": {
                "class_type": "UnetLoaderGGUF",
                "inputs": {
                    "unet_name": model_files["unet_low"],
                }
            },
            # Node 3: Load UMT5 text encoder (Wan clip type)
            "3": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": model_files["clip"],
                    "type": "wan",
                    "device": "default",
                }
            },
            # Node 4: Load Wan VAE
            "4": {
                "class_type": "VAELoader",
                "inputs": {
                    "vae_name": model_files["vae"],
                }
            },

            # ── Text Encoding ──────────────────────────────────────────────
            # Node 5: Positive prompt
            "5": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "clip": ["3", 0],
                    "text": prompt,
                }
            },
            # Node 6: Negative prompt
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "clip": ["3", 0],
                    "text": negative_prompt,
                }
            },

            # ── Latent ─────────────────────────────────────────────────────
            # Node 7: Empty video latent
            "7": {
                "class_type": "EmptyHunyuanLatentVideo",
                "inputs": {
                    "width": width,
                    "height": height,
                    "length": num_frames,
                    "batch_size": 1,
                }
            },

            # ── Noise Scheduling ───────────────────────────────────────────
            # Node 8: ModelSamplingSD3 for HighNoise expert (shift=8.0)
            "8": {
                "class_type": "ModelSamplingSD3",
                "inputs": {
                    "model": ["1", 0],
                    "shift": 8.0,
                }
            },
            # Node 9: ModelSamplingSD3 for LowNoise expert (shift=8.0)
            "9": {
                "class_type": "ModelSamplingSD3",
                "inputs": {
                    "model": ["2", 0],
                    "shift": 8.0,
                }
            },

            # ── Two-Pass Sampling (MoE) ────────────────────────────────────
            # Steps are SPLIT at midpoint: HighNoise does steps 0→mid,
            # LowNoise continues from mid→end. Total steps = num_inference_steps.

            # Node 10: Pass 1 — HighNoise expert (layout + motion, first half)
            "10": {
                "class_type": "KSamplerAdvanced",
                "inputs": {
                    "model": ["8", 0],
                    "positive": ["5", 0],
                    "negative": ["6", 0],
                    "latent_image": ["7", 0],
                    "add_noise": "enable",
                    "noise_seed": seed,
                    "control_after_generate": "randomize",
                    "steps": num_inference_steps,
                    "cfg": guidance_scale,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "start_at_step": 0,
                    "end_at_step": midpoint,
                    "return_with_leftover_noise": "enable",
                }
            },
            # Node 11: Pass 2 — LowNoise expert (detail refinement, second half)
            "11": {
                "class_type": "KSamplerAdvanced",
                "inputs": {
                    "model": ["9", 0],
                    "positive": ["5", 0],
                    "negative": ["6", 0],
                    "latent_image": ["10", 0],
                    "add_noise": "disable",
                    "noise_seed": 0,
                    "control_after_generate": "fixed",
                    "steps": num_inference_steps,
                    "cfg": guidance_scale,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "start_at_step": midpoint,
                    "end_at_step": 10000,
                    "return_with_leftover_noise": "disable",
                }
            },

            # ── Decode + Output ────────────────────────────────────────────
            # Node 12: VAE Decode — tiled for HD+ so your GPU doesn't rage-quit
            "12": self._build_vae_decode_node("11", "4", width, height),
            # Node 13: Create video from frames
            "13": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["12", 0],
                    "frame_rate": fps,
                    "loop_count": 0,
                    "filename_prefix": "wan22_t2v",
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "pingpong": False,
                    "save_output": True,
                    "videopreview": {
                        "hidden": False,
                        "paused": False,
                        "params": {},
                    },
                }
            },
        }

        # Add RIFE frame interpolation if multiplier > 1
        if interpolation_multiplier > 1:
            self._add_rife_interpolation(
                workflow,
                source_node_id="12",       # VAEDecode
                video_combine_node_id="13", # VHS_VideoCombine
                base_fps=fps,
                multiplier=interpolation_multiplier,
            )

        return workflow

    def _build_vae_decode_node(self, samples_node: str, vae_node: str, width: int, height: int) -> dict:
        """Pick the right VAE decode strategy based on resolution.

        Standard VAEDecode works fine up to 720p. Above that, tiled decoding
        saves your VRAM from a very bad day.
        """
        use_tiled = width >= 1280 or height >= 1280
        if use_tiled:
            return {
                "class_type": "VAEDecodeTiled",
                "inputs": {
                    "samples": [samples_node, 0],
                    "vae": [vae_node, 0],
                    "tile_size": 480,
                    "overlap": 64,
                    "temporal_size": 64,
                    "temporal_overlap": 8,
                }
            }
        return {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": [samples_node, 0],
                "vae": [vae_node, 0],
            }
        }

    def _add_rife_interpolation(
        self,
        workflow: dict,
        source_node_id: str,
        video_combine_node_id: str,
        base_fps: int,
        multiplier: int = 2,
    ) -> dict:
        """Insert a RIFE VFI interpolation node between a frame source and VHS_VideoCombine.

        Args:
            workflow: The ComfyUI workflow dict (modified in-place).
            source_node_id: Node ID that outputs IMAGE frames (e.g. VAEDecode).
            video_combine_node_id: Node ID of VHS_VideoCombine to rewire.
            base_fps: The original frame rate before interpolation.
            multiplier: Frame multiplier (2 = double FPS, 4 = quad FPS).

        Returns:
            The modified workflow dict.
        """
        # Pick the next available node ID
        existing_ids = [int(k) for k in workflow.keys() if k.isdigit()]
        rife_node_id = str(max(existing_ids) + 1)

        # Insert RIFE VFI node
        workflow[rife_node_id] = {
            "class_type": "RIFE VFI",
            "inputs": {
                "frames": [source_node_id, 0],
                "ckpt_name": "rife49.pth",
                "clear_cache_after_n_frames": 10,
                "multiplier": multiplier,
                "fast_mode": True,
                "ensemble": True,
                "scale_factor": 1.0,
                "dtype": "float32",
                "torch_compile": False,
                "batch_size": 1,
            }
        }

        # Rewire VHS_VideoCombine to take frames from RIFE instead of source
        workflow[video_combine_node_id]["inputs"]["images"] = [rife_node_id, 0]
        workflow[video_combine_node_id]["inputs"]["frame_rate"] = base_fps * multiplier

        logger.info(
            f"Added RIFE interpolation (x{multiplier}): "
            f"node {source_node_id} -> RIFE({rife_node_id}) -> VHS_VideoCombine({video_combine_node_id}), "
            f"FPS {base_fps} -> {base_fps * multiplier}"
        )

        return workflow

    def _add_upscale_node(
        self,
        workflow: dict,
        source_node_id: str,
        video_combine_node_id: str,
    ) -> dict:
        """Insert Real-ESRGAN 2x upscale between a frame source and VHS_VideoCombine.

        Args:
            workflow: The ComfyUI workflow dict (modified in-place).
            source_node_id: Node ID that outputs IMAGE frames (e.g. RIFE or VAEDecode).
            video_combine_node_id: Node ID of VHS_VideoCombine to rewire.

        Returns:
            The modified workflow dict.
        """
        existing_ids = [int(k) for k in workflow.keys() if k.isdigit()]
        loader_id = str(max(existing_ids) + 1)
        upscale_id = str(max(existing_ids) + 2)

        # Load the upscale model
        workflow[loader_id] = {
            "class_type": "UpscaleModelLoader",
            "inputs": {
                "model_name": "RealESRGAN_x2.pth",
            }
        }

        # Apply upscaling to frames
        workflow[upscale_id] = {
            "class_type": "ImageUpscaleWithModel",
            "inputs": {
                "upscale_model": [loader_id, 0],
                "image": [source_node_id, 0],
            }
        }

        # Rewire VHS_VideoCombine to take frames from upscaler
        workflow[video_combine_node_id]["inputs"]["images"] = [upscale_id, 0]

        logger.info(
            f"Added Real-ESRGAN 2x upscale: "
            f"node {source_node_id} -> Upscale({upscale_id}) -> VHS_VideoCombine({video_combine_node_id})"
        )

        return workflow

    def _queue_prompt(self, workflow: dict) -> Optional[str]:
        try:
            payload = {"prompt": workflow}
            response = requests.post(
                f"{self.comfy_url}/prompt",
                json=payload,
                timeout=10
            )
            response.raise_for_status()

            result = response.json()
            prompt_id = result.get("prompt_id")
            logger.info(f"Queued workflow in ComfyUI: {prompt_id}")
            return prompt_id

        except Exception as e:
            logger.error(f"Failed to queue workflow in ComfyUI: {e}")
            return None

    def _wait_for_completion(self, prompt_id: str, timeout: int = 600) -> Optional[dict]:
        start_time = time.time()
        last_log_time = start_time

        while time.time() - start_time < timeout:
            try:
                response = requests.get(
                    f"{self.comfy_url}/history/{prompt_id}",
                    timeout=5
                )
                response.raise_for_status()
                history = response.json()

                if prompt_id in history:
                    outputs = history[prompt_id].get('outputs', {})
                    logger.info(f"Generation complete: {prompt_id}")
                    return outputs

                current_time = time.time()
                if current_time - last_log_time > 10:
                    elapsed = int(current_time - start_time)
                    logger.info(f"Waiting for generation... ({elapsed}s elapsed)")
                    last_log_time = current_time

            except Exception as e:
                logger.warning(f"Error checking generation status: {e}")

            time.sleep(2)

        logger.error(f"Generation timed out after {timeout}s")
        return None

    def _download_result(self, outputs: dict, destination_dir: Path) -> List[str]:
        downloaded_files = []

        try:
            for node_id, node_output in outputs.items():
                if 'gifs' in node_output:
                    for item in node_output['gifs']:
                        filename = item.get('filename')
                        if filename:
                            downloaded_files.extend(
                                self._download_file(filename, destination_dir, file_type='output', subfolder=item.get('subfolder', ''))
                            )

                if 'images' in node_output:
                    for item in node_output['images']:
                        filename = item.get('filename')
                        if filename:
                            downloaded_files.extend(
                                self._download_file(filename, destination_dir, file_type='output', subfolder=item.get('subfolder', ''))
                            )

            logger.info(f"Downloaded {len(downloaded_files)} files from ComfyUI")
            return downloaded_files

        except Exception as e:
            logger.error(f"Failed to download results from ComfyUI: {e}")
            return []

    def _download_file(self, filename: str, destination_dir: Path, file_type: str = 'output', subfolder: str = '') -> List[str]:
        try:
            params = {"filename": filename, "type": file_type}
            if subfolder:
                params["subfolder"] = subfolder

            query = urllib.parse.urlencode(params)
            url = f"{self.comfy_url}/view?{query}"

            destination_path = destination_dir / filename
            destination_path.parent.mkdir(parents=True, exist_ok=True)

            logger.info(f"Downloading from ComfyUI: {url}")
            urllib.request.urlretrieve(url, destination_path)

            return [str(destination_path)]

        except Exception as e:
            logger.error(f"Failed to download file {filename}: {e}")
            return []

    def _extract_thumbnail(self, video_path: Path, thumbnail_path: Path) -> bool:
        """Extract the first frame from a video as a JPEG thumbnail using ffmpeg."""
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-i", str(video_path),
                    "-vf", "select=eq(n\\,0)",
                    "-frames:v", "1",
                    "-q:v", "2",
                    "-y", str(thumbnail_path),
                ],
                capture_output=True,
                timeout=30,
            )
            if thumbnail_path.exists() and thumbnail_path.stat().st_size > 0:
                logger.info(f"Extracted thumbnail: {thumbnail_path}")
                return True
            logger.warning(f"ffmpeg ran but thumbnail not created (rc={result.returncode})")
            return False
        except FileNotFoundError:
            logger.warning("ffmpeg not found on system, cannot extract thumbnail")
            return False
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg thumbnail extraction timed out")
            return False
        except Exception as e:
            logger.warning(f"Failed to extract thumbnail: {e}")
            return False

    def generate_video(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        # Re-check live connection if the cached flag says unavailable.
        # ComfyUI may have been started on-demand by the router since init.
        if not self.service_available:
            self.service_available = self._check_comfyui_connection()
        if not self.service_available:
            return VideoGenerationResult(
                success=False,
                error="ComfyUI service not available. Please start ComfyUI at http://127.0.0.1:8188",
                prompt_used=request.prompt,
            )

        # ── Prompt enhancement ───────────────────────────────────────
        if request.enhance_prompt and request.prompt:
            try:
                from backend.utils.prompt_enhancer import enhance_video_prompt, get_default_negative_prompt
                request.prompt = enhance_video_prompt(
                    request.prompt,
                    style=request.prompt_style,
                    width=request.width,
                    height=request.height,
                )
                if not request.negative_prompt:
                    request.negative_prompt = get_default_negative_prompt(style=request.prompt_style)
                logger.info(f"Prompt enhanced (style={request.prompt_style}): {request.prompt[:120]}...")
            except Exception as e:
                logger.warning(f"Prompt enhancement failed, using original prompt: {e}")

        if request.output_dir:
            batch_dir = Path(request.output_dir)
        else:
            # Standalone generation — Bates-stamped folder in Videos/
            try:
                from backend.services.output_registration import bates_name
                folder_name = bates_name("video_batch", "", self.default_output_dir)
            except Exception:
                folder_name = f"batch_{uuid.uuid4().hex}"
            batch_dir = self.default_output_dir / folder_name
        batch_dir = Path(batch_dir)

        item_id = request.metadata.get("item_id") if request.metadata else None
        if not item_id:
            # Bates-stamped item ID instead of UUID soup
            try:
                from backend.services.output_registration import bates_name
                item_id = bates_name("video", "", batch_dir)
            except Exception:
                item_id = f"item_{uuid.uuid4().hex}"
            if request.metadata:
                request.metadata["item_id"] = item_id

        item_dir = batch_dir / item_id
        videos_dir = item_dir / "videos"
        frames_dir = item_dir / "frames"
        thumbs_dir = item_dir / "thumbnails"

        videos_dir.mkdir(parents=True, exist_ok=True)
        frames_dir.mkdir(parents=True, exist_ok=True)
        thumbs_dir.mkdir(parents=True, exist_ok=True)

        result = VideoGenerationResult(
            success=False,
            prompt_used=request.prompt,
            metadata=request.metadata or {},
        )

        try:
            image_path = request.metadata.get("image_path") if request.metadata else None
            model = request.model or "svd"
            seed = request.seed if request.seed is not None else int(time.time() * 1000) % (2**31)

            interpolation = request.interpolation_multiplier

            # ── Route by model type ──────────────────────────────────
            if model in self.WAN22_MODELS or model in ("wan22", "wan2.2"):
                if image_path:
                    result.error = "Wan 2.2 is text-to-video only. Use CogVideoX 5B I2V or SVD for image-to-video."
                    return result
                # Text-to-video via Wan 2.2 GGUF
                model_key = model if model in self.WAN22_MODELS else "wan22-14b"
                workflow = self._create_wan22_t2v_workflow(
                    prompt=request.prompt,
                    negative_prompt=request.negative_prompt,
                    model_key=model_key,
                    num_frames=request.duration_frames,
                    num_inference_steps=request.num_inference_steps,
                    guidance_scale=request.guidance_scale,
                    width=request.width,
                    height=request.height,
                    seed=seed,
                    fps=request.fps,
                    interpolation_multiplier=interpolation,
                )
                logger.info(f"Using Wan 2.2 text-to-video ({model_key}) via ComfyUI GGUF")

            elif model in ("cogvideox-2b", "cogvideox-5b"):
                if image_path:
                    result.error = f"{model} is text-to-video only. Use cogvideox-5b-i2v for image-to-video."
                    return result
                # Text-to-video via CogVideoX
                hf_model = self.COGVIDEOX_MODELS.get(model, "THUDM/CogVideoX-2b")
                workflow = self._create_cogvideox_text2video_workflow(
                    prompt=request.prompt,
                    model_name=hf_model,
                    num_frames=request.duration_frames,
                    num_inference_steps=request.num_inference_steps,
                    guidance_scale=request.guidance_scale,
                    width=request.width,
                    height=request.height,
                    seed=seed,
                    fps=request.fps,
                    interpolation_multiplier=interpolation,
                )
                # Add optional TeaCache / FETA nodes for CogVideoX
                meta = request.metadata or {}
                self._add_cogvideox_optional_nodes(
                    workflow, sampler_node_id="6",
                    teacache_threshold=meta.get("teacache_threshold"),
                    feta_weight=meta.get("feta_weight"),
                )
                logger.info(f"Using CogVideoX text-to-video ({model}) via ComfyUI")

            elif model == "cogvideox-5b-i2v":
                # Image-to-video via CogVideoX
                if not image_path or not Path(image_path).exists():
                    result.error = "CogVideoX image-to-video requires an input image."
                    return result
                uploaded_image = self._upload_image_to_comfyui(image_path)
                if not uploaded_image:
                    result.error = "Failed to upload image to ComfyUI"
                    return result
                hf_model = self.COGVIDEOX_MODELS.get(model, "THUDM/CogVideoX-5b-I2V")
                workflow = self._create_cogvideox_i2v_workflow(
                    image_filename=uploaded_image,
                    prompt=request.prompt,
                    model_name=hf_model,
                    num_frames=request.duration_frames,
                    num_inference_steps=request.num_inference_steps,
                    guidance_scale=request.guidance_scale,
                    width=request.width,
                    height=request.height,
                    seed=seed,
                    fps=request.fps,
                    interpolation_multiplier=interpolation,
                )
                # Add optional TeaCache / FETA nodes for CogVideoX I2V
                meta = request.metadata or {}
                self._add_cogvideox_optional_nodes(
                    workflow, sampler_node_id="6",
                    teacache_threshold=meta.get("teacache_threshold"),
                    feta_weight=meta.get("feta_weight"),
                )
                logger.info(f"Using CogVideoX image-to-video via ComfyUI")

            else:
                # SVD image-to-video (legacy)
                if not image_path or not Path(image_path).exists():
                    result.error = "SVD requires an input image."
                    return result
                uploaded_image = self._upload_image_to_comfyui(image_path)
                if not uploaded_image:
                    result.error = "Failed to upload image to ComfyUI"
                    return result
                motion_bucket_id = max(1, min(255, int(request.motion_strength * 127)))
                workflow = self._create_svd_workflow(
                    image_filename=uploaded_image,
                    num_frames=request.duration_frames,
                    motion_bucket_id=motion_bucket_id,
                    fps=request.fps,
                    seed=seed,
                )
                logger.info(f"Using SVD image-to-video via ComfyUI")

            # Apply Real-ESRGAN 2x upscale if requested
            upscale = request.metadata.get("upscale", False) if request.metadata else False
            if upscale:
                # Find VHS_VideoCombine and its current frame source
                vhs_node_id = next(
                    (nid for nid, node in workflow.items() if node.get("class_type") == "VHS_VideoCombine"),
                    None
                )
                if vhs_node_id:
                    # The node currently feeding images to VHS_VideoCombine
                    source_ref = workflow[vhs_node_id]["inputs"].get("images", [None])[0]
                    if source_ref:
                        self._add_upscale_node(workflow, source_ref, vhs_node_id)

            logger.info("Sending workflow to ComfyUI...")
            prompt_id = self._queue_prompt(workflow)

            if not prompt_id:
                result.error = "Failed to queue workflow in ComfyUI"
                return result

            # Timeouts scaled to what the GPU actually needs. If you're rendering
            # 1080p at 50 steps with upscaling, go make a sandwich. Or two.
            is_wan = model in self.WAN22_MODELS or model in ("wan22", "wan2.2")
            steps = request.num_inference_steps or 30
            has_upscale = request.metadata.get("upscale", False) if request.metadata else False
            is_high_res = max(request.width or 0, request.height or 0) >= 1280
            if is_wan and is_high_res:
                gen_timeout = 7200  # 2 hours — HD/1080p with CPU offloading takes a while
            elif is_wan and (steps >= 50 or has_upscale):
                gen_timeout = 2400  # 40 min — max quality + cinema upscale
            elif is_wan:
                gen_timeout = 1200  # 20 min — standard Wan generations
            else:
                gen_timeout = 600   # 10 min — lighter models
            logger.info(f"Waiting for ComfyUI to complete generation (prompt_id: {prompt_id}, timeout: {gen_timeout}s, steps: {steps}, upscale: {has_upscale}, high_res: {is_high_res})...")
            outputs = self._wait_for_completion(prompt_id, timeout=gen_timeout)

            if not outputs:
                result.error = "ComfyUI generation timed out or failed"
                return result

            logger.info("Downloading results from ComfyUI...")
            downloaded_files = self._download_result(outputs, videos_dir)

            if not downloaded_files:
                result.error = "No files were generated by ComfyUI"
                return result

            result.video_path = str(Path(downloaded_files[0]).relative_to(batch_dir))
            result.frame_paths = [str(Path(f).relative_to(batch_dir)) for f in downloaded_files]
            result.success = True

            # Extract thumbnail from the first video file
            video_file = Path(downloaded_files[0])
            if video_file.exists() and video_file.suffix.lower() in (".mp4", ".webm", ".avi", ".mov"):
                thumb_filename = video_file.stem + "_thumb.jpg"
                thumb_path = thumbs_dir / thumb_filename
                if self._extract_thumbnail(video_file, thumb_path):
                    result.thumbnail_path = str(thumb_path.relative_to(batch_dir))

            logger.info(f"Video generation successful: {result.video_path}")

            # Register into Documents/Files system if not batch-controlled
            # (batch-controlled videos get registered by the batch_video_generator)
            is_batch_controlled = (request.metadata or {}).get("batch_controlled", False)
            if not is_batch_controlled and result.success:
                try:
                    from backend.services.output_registration import register_file, ensure_subfolder
                    batch_folder_name = batch_dir.name
                    ensure_subfolder("Videos", batch_folder_name)
                    for vid_file in videos_dir.glob("*.mp4"):
                        register_file(
                            physical_path=str(vid_file),
                            folder_name="Videos",
                            subfolder_name=batch_folder_name,
                            file_metadata={"source": "comfyui", "prompt": request.prompt[:200]},
                        )
                except Exception as reg_err:
                    logger.warning(f"Video registration into Documents failed (non-critical): {reg_err}")

            return result

        except Exception as e:
            logger.error(f"Error during video generation: {e}")
            import traceback
            logger.error(traceback.format_exc())
            result.error = str(e)
            result.success = False
            return result


_video_generator_instance: Optional[ComfyUIVideoGenerator] = None


def get_video_generator() -> ComfyUIVideoGenerator:
    global _video_generator_instance
    if _video_generator_instance is None:
        _video_generator_instance = ComfyUIVideoGenerator()
    return _video_generator_instance
