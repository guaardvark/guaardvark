"""ComfyUI video workflow builders (mixin).

Extracted from comfyui_video_generator.py so generate orchestration stays thin.
Methods are unchanged — ComfyUIVideoGenerator inherits ComfyUIVideoWorkflowMixin.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class ComfyUIVideoWorkflowMixin:
    """Wan / CogVideoX / LTX workflow graphs + post nodes (RIFE, upscale, FreeU, …)."""

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
                    # Offload the T5 encoder off-GPU after the positive encode too
                    # (was False) so T5 isn't co-resident with the transformer+VAE
                    # — cuts the CogVideoX OOM hotspot on a 16GB card.
                    "force_offload": True,
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
                    # Offload the T5 encoder off-GPU after the positive encode too
                    # (was False) so T5 isn't co-resident with the transformer+VAE
                    # — cuts the CogVideoX OOM hotspot on a 16GB card.
                    "force_offload": True,
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
                    # KJNodes ImageResizeKJ schema drifted: fields used to be
                    # width_input/height_input/interpolation; now they're
                    # width/height/upscale_method (with upscale_method as a
                    # required enum). divisible_by stays required as well.
                    "image": ["5", 0],
                    "width": width,
                    "height": height,
                    "upscale_method": "lanczos",
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
        clip_device = self._wan_clip_device()

        # Default negative prompt for anatomy quality
        if not negative_prompt:
            negative_prompt = (
                "blurry, low quality, worst quality, deformed, disfigured, poor anatomy, "
                "bad proportions, extra limbs, missing limbs, mutated hands, fused fingers, "
                "extra fingers, deformed face, asymmetrical eyes, weird body, static, "
                "overexposed, "
            )

        midpoint = num_inference_steps // 2
        logger.info(
            "Wan T2V MoE workflow clip_device=%s (TE off-GPU frees UNet residency on 16GB)",
            clip_device,
        )

        shift = self._wan_dynamic_shift(width, height)

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
            # Node 3: Load UMT5 text encoder (Wan clip type).
            # device=cpu on ≤20GB cards: same weights, TE never occupies GPU VRAM
            # so the ~10GB UNet is not forced into CPU offload thrash.
            "3": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": model_files["clip"],
                    "type": "wan",
                    "device": clip_device,
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
            # Node 8: ModelSamplingSD3 for HighNoise expert
            "8": {
                "class_type": "ModelSamplingSD3",
                "inputs": {
                    "model": ["1", 0],
                    "shift": shift,
                }
            },
            # Node 9: ModelSamplingSD3 for LowNoise expert
            "9": {
                "class_type": "ModelSamplingSD3",
                "inputs": {
                    "model": ["2", 0],
                    "shift": shift,
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


    def _create_wan22_i2v_workflow(
        self,
        image_filename: str,
        prompt: str,
        negative_prompt: str = "",
        model_key: str = "wan22-14b-i2v",
        num_frames: int = 81,
        num_inference_steps: int = 20,
        guidance_scale: float = 3.5,
        width: int = 832,
        height: int = 480,
        seed: Optional[int] = None,
        fps: int = 16,
        interpolation_multiplier: int = 2,
    ) -> dict:
        # Same MoE two-pass dance as Wan T2V, but the empty latent gets swapped
        # for WanImageToVideo — that node bakes the start frame into the
        # conditioning and hands back a properly-shaped image-conditioned latent.
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)

        model_files = self.WAN22_MODELS.get(model_key, self.WAN22_MODELS["wan22-14b-i2v"])
        clip_device = self._wan_clip_device()

        if not negative_prompt:
            negative_prompt = (
                "blurry, low quality, worst quality, deformed, disfigured, poor anatomy, "
                "bad proportions, extra limbs, missing limbs, mutated hands, fused fingers, "
                "extra fingers, deformed face, asymmetrical eyes, weird body, static, "
                "overexposed"
            )

        midpoint = num_inference_steps // 2
        shift = self._wan_dynamic_shift(width, height)
        logger.info(
            "Wan I2V MoE workflow clip_device=%s, dynamic_shift=%.1f (TE off-GPU frees UNet residency on 16GB)",
            clip_device,
            shift,
        )

        workflow = {
            "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": model_files["unet_high"]}},
            "2": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": model_files["unet_low"]}},
            "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": model_files["clip"], "type": "wan", "device": clip_device}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": model_files["vae"]}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": prompt}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": negative_prompt}},
            "14": {"class_type": "LoadImage", "inputs": {"image": image_filename}},
            # WanImageToVideo: takes pos/neg cond + start image + vae →
            # returns image-conditioned pos/neg + a length-N latent.
            "7": {
                "class_type": "WanImageToVideo",
                "inputs": {
                    "positive": ["5", 0],
                    "negative": ["6", 0],
                    "vae": ["4", 0],
                    "width": width,
                    "height": height,
                    "length": num_frames,
                    "batch_size": 1,
                    "start_image": ["14", 0],
                },
            },
            "8": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": shift}},
            "9": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": shift}},
            "10": {
                "class_type": "KSamplerAdvanced",
                "inputs": {
                    "model": ["8", 0],
                    "positive": ["7", 0],
                    "negative": ["7", 1],
                    "latent_image": ["7", 2],
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
                },
            },
            "11": {
                "class_type": "KSamplerAdvanced",
                "inputs": {
                    "model": ["9", 0],
                    "positive": ["7", 0],
                    "negative": ["7", 1],
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
                },
            },
            "12": self._build_vae_decode_node("11", "4", width, height),
            "13": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["12", 0],
                    "frame_rate": fps,
                    "loop_count": 0,
                    "filename_prefix": "wan22_i2v",
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "pingpong": False,
                    "save_output": True,
                    "videopreview": {"hidden": False, "paused": False, "params": {}},
                },
            },
        }

        if interpolation_multiplier > 1:
            self._add_rife_interpolation(
                workflow,
                source_node_id="12",
                video_combine_node_id="13",
                base_fps=fps,
                multiplier=interpolation_multiplier,
            )

        return workflow


    def _create_wan22_5b_workflow(
        self,
        prompt: str,
        negative_prompt: str = "",
        model_key: str = "wan22-5b",
        image_filename: Optional[str] = None,
        num_frames: int = 121,
        num_inference_steps: int = 20,
        guidance_scale: float = 5.0,
        width: int = 1280,
        height: int = 704,
        seed: Optional[int] = None,
        fps: int = 24,
        interpolation_multiplier: int = 1,
        sampler_profile: Optional[str] = None,
    ) -> dict:
        """Wan 2.2 TI2V-5B — single-model text+image-to-video that FITS 16GB (no MoE
        two-pass, no CPU offload → none of the 38-min-per-clip A14B pain). Graph mirrors
        ComfyUI's bundled `video_wan2_2_5B_ti2v` template: UNETLoader + CLIPLoader(wan) +
        VAELoader(wan2.2 VAE) → CLIPTextEncode ×2 → Wan22ImageToVideoLatent (start_image
        optional) → ModelSamplingSD3(dynamic shift) → single KSampler (euler/simple, denoise 1)
        → decode. image_filename set → image-to-video; else text-to-video.
        """
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)

        cfg = self.WAN22_MODELS.get(model_key, {})
        unet = cfg.get("unet") or "wan2.2_ti2v_5B_fp16.safetensors"
        clip = cfg.get("clip") or "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
        vae = cfg.get("vae") or "wan2.2_vae.safetensors"
        clip_device = self._wan_clip_device()

        if not negative_prompt:
            negative_prompt = (
                "blurry, low quality, worst quality, deformed, disfigured, poor anatomy, "
                "bad proportions, extra limbs, missing limbs, mutated hands, fused fingers, "
                "extra fingers, deformed face, asymmetrical eyes, weird body, static, "
                "overexposed"
            )

        # Wan22ImageToVideoLatent: vae + dims (+ optional start_image) → conditioned LATENT.
        latent_inputs = {
            "vae": ["4", 0],
            "width": width,
            "height": height,
            "length": num_frames,
            "batch_size": 1,
        }
        
        profile_key = self._wan5b_sampler_profile(sampler_profile)
        profile = self.WAN5B_SAMPLER_PROFILES[profile_key]
        shift = profile["shift"] if profile["shift"] is not None else self._wan_dynamic_shift(width, height)

        logger.info(
            "Wan TI2V-5B workflow clip_device=%s profile=%s sampler=%s shift=%.1f",
            clip_device, profile_key, profile["sampler"], shift,
        )

        workflow = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
            "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip, "type": "wan", "device": clip_device}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": prompt}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": negative_prompt}},
            "7": {"class_type": "Wan22ImageToVideoLatent", "inputs": latent_inputs},
            "8": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": shift}},
            # Single sampling pass — the whole point (no high/low expert swap).
            "10": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["8", 0],
                    "positive": ["5", 0],
                    "negative": ["6", 0],
                    "latent_image": ["7", 0],
                    "seed": seed,
                    "steps": num_inference_steps,
                    "cfg": guidance_scale,
                    "sampler_name": profile["sampler"],
                    "scheduler": "simple",
                    "denoise": 1.0,
                },
            },
            "12": self._build_vae_decode_node("10", "4", width, height),
            "13": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["12", 0],
                    "frame_rate": fps,
                    "loop_count": 0,
                    "filename_prefix": "wan22_5b",
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "pingpong": False,
                    "save_output": True,
                    "videopreview": {"hidden": False, "paused": False, "params": {}},
                },
            },
        }

        # Image-to-video: bake the start frame into the latent node.
        if image_filename:
            workflow["14"] = {"class_type": "LoadImage", "inputs": {"image": image_filename}}
            workflow["7"]["inputs"]["start_image"] = ["14", 0]

        if interpolation_multiplier > 1:
            self._add_rife_interpolation(
                workflow,
                source_node_id="12",
                video_combine_node_id="13",
                base_fps=fps,
                multiplier=interpolation_multiplier,
            )

        return workflow


    def _create_ltx23_t2v_workflow(
        self,
        prompt: str,
        negative_prompt: str = "",
        model_key: str = "ltx23-distilled-fp8",
        num_frames: int = 65,
        num_inference_steps: int = 8,
        guidance_scale: float = 1.0,
        width: int = 768,
        height: int = 512,
        seed: Optional[int] = None,
        fps: float = 16.0,
        interpolation_multiplier: int = 1,
    ) -> dict:
        """LTX-2.3 distilled T2V — AV-aware core ComfyUI graph.

        LTX-2.3 is an audio-video model: sampling on a video-only latent crashes
        in rotary PE with T=0 audio (`reshape [2, 0, 32, -1]`). We always concat
        an empty audio latent, sample the AV pair, then separate and decode video.

        Graph: UNETLoader + DualCLIPLoader(ltxv) + video VAE + audio VAE →
        CLIPTextEncode ×2 → EmptyLTXVLatentVideo + LTXVEmptyLatentAudio →
        LTXVConcatAVLatent → LTXVConditioning → ModelSamplingLTXV → KSampler →
        LTXVSeparateAVLatent → VAEDecode → VHS_VideoCombine.
        """
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)

        self._ensure_ltx_models()
        cfg = self.LTX_MODELS.get(model_key, {})
        unet = cfg.get("unet") or "ltx-2.3-22b-distilled-1.1_transformer_only_fp8_scaled.safetensors"
        clip = cfg.get("clip") or "gemma_3_12B_it_fp4_mixed.safetensors"
        text_proj = cfg.get("text_projection") or "ltx-2.3_text_projection_bf16.safetensors"
        vae = cfg.get("vae") or "LTX23_video_vae_bf16.safetensors"
        audio_vae = cfg.get("audio_vae") or "LTX23_audio_vae_bf16.safetensors"
        length = self._ltx_frame_count(num_frames)

        if not negative_prompt:
            negative_prompt = (
                "blurry, low quality, worst quality, deformed, disfigured, poor anatomy, "
                "bad proportions, extra limbs, static, jitter, morphing, watermark"
            )

        clip_device = self._wan_clip_device()

        workflow = {
            "1": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": unet, "weight_dtype": "default"},
            },
            "2": {
                "class_type": "DualCLIPLoader",
                "inputs": {
                    "clip_name1": clip,
                    "clip_name2": text_proj,
                    "type": "ltxv",
                    "device": clip_device,
                },
            },
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
            "4": {"class_type": "LTXVAudioVAELoader", "inputs": {"ckpt_name": audio_vae}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative_prompt}},
            "7": {
                "class_type": "EmptyLTXVLatentVideo",
                "inputs": {
                    "width": width,
                    "height": height,
                    "length": length,
                    "batch_size": 1,
                },
            },
            "8": {
                "class_type": "LTXVEmptyLatentAudio",
                "inputs": {
                    "frames_number": length,
                    "frame_rate": float(fps),
                    "batch_size": 1,
                    "audio_vae": ["4", 0],
                },
            },
            "9": {
                "class_type": "LTXVConcatAVLatent",
                "inputs": {"video_latent": ["7", 0], "audio_latent": ["8", 0]},
            },
            "10": {
                "class_type": "LTXVConditioning",
                "inputs": {
                    "positive": ["5", 0],
                    "negative": ["6", 0],
                    "frame_rate": float(fps),
                },
            },
            "11": {
                "class_type": "ModelSamplingLTXV",
                "inputs": {
                    "model": ["1", 0],
                    "max_shift": 2.05,
                    "base_shift": 0.95,
                    "latent": ["9", 0],
                },
            },
            "12": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["11", 0],
                    "positive": ["10", 0],
                    "negative": ["10", 1],
                    "latent_image": ["9", 0],
                    "seed": seed,
                    "steps": max(1, int(num_inference_steps or 8)),
                    "cfg": float(guidance_scale if guidance_scale is not None else 1.0),
                    "sampler_name": "euler_ancestral_cfg_pp",
                    "scheduler": "simple",
                    "denoise": 1.0,
                },
            },
            "13": {
                "class_type": "LTXVSeparateAVLatent",
                "inputs": {"av_latent": ["12", 0]},
            },
            "14": self._build_vae_decode_node("13", "3", width, height),
            "15": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["14", 0],
                    "frame_rate": float(fps),
                    "loop_count": 0,
                    "filename_prefix": "ltx23_t2v",
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "pingpong": False,
                    "save_output": True,
                    "videopreview": {"hidden": False, "paused": False, "params": {}},
                },
            },
        }

        if interpolation_multiplier > 1:
            self._add_rife_interpolation(
                workflow,
                source_node_id="14",
                video_combine_node_id="15",
                base_fps=fps,
                multiplier=interpolation_multiplier,
            )
        return workflow


    def _create_ltx23_i2v_workflow(
        self,
        image_filename: str,
        prompt: str,
        negative_prompt: str = "",
        model_key: str = "ltx23-distilled-fp8",
        num_frames: int = 65,
        num_inference_steps: int = 8,
        guidance_scale: float = 1.0,
        width: int = 768,
        height: int = 512,
        seed: Optional[int] = None,
        fps: float = 16.0,
        interpolation_multiplier: int = 1,
        strength: float = 1.0,
    ) -> dict:
        """LTX-2.3 distilled I2V — AV concat path with LTXVImgToVideo start frame."""
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)

        self._ensure_ltx_models()
        cfg = self.LTX_MODELS.get(model_key, {})
        unet = cfg.get("unet") or "ltx-2.3-22b-distilled-1.1_transformer_only_fp8_scaled.safetensors"
        clip = cfg.get("clip") or "gemma_3_12B_it_fp4_mixed.safetensors"
        text_proj = cfg.get("text_projection") or "ltx-2.3_text_projection_bf16.safetensors"
        vae = cfg.get("vae") or "LTX23_video_vae_bf16.safetensors"
        audio_vae = cfg.get("audio_vae") or "LTX23_audio_vae_bf16.safetensors"
        length = self._ltx_frame_count(num_frames)
        clip_device = self._wan_clip_device()

        if not negative_prompt:
            negative_prompt = (
                "blurry, low quality, worst quality, deformed, disfigured, poor anatomy, "
                "bad proportions, extra limbs, static, jitter, morphing, watermark"
            )

        workflow = {
            "1": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": unet, "weight_dtype": "default"},
            },
            "2": {
                "class_type": "DualCLIPLoader",
                "inputs": {
                    "clip_name1": clip,
                    "clip_name2": text_proj,
                    "type": "ltxv",
                    "device": clip_device,
                },
            },
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
            "4": {"class_type": "LTXVAudioVAELoader", "inputs": {"ckpt_name": audio_vae}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative_prompt}},
            "7": {"class_type": "LoadImage", "inputs": {"image": image_filename}},
            "8": {
                "class_type": "LTXVPreprocess",
                "inputs": {"image": ["7", 0], "img_compression": 18},
            },
            "9": {
                "class_type": "LTXVImgToVideo",
                "inputs": {
                    "positive": ["5", 0],
                    "negative": ["6", 0],
                    "vae": ["3", 0],
                    "image": ["8", 0],
                    "width": width,
                    "height": height,
                    "length": length,
                    "batch_size": 1,
                    "strength": float(strength),
                },
            },
            "10": {
                "class_type": "LTXVEmptyLatentAudio",
                "inputs": {
                    "frames_number": length,
                    "frame_rate": float(fps),
                    "batch_size": 1,
                    "audio_vae": ["4", 0],
                },
            },
            "11": {
                "class_type": "LTXVConcatAVLatent",
                "inputs": {"video_latent": ["9", 2], "audio_latent": ["10", 0]},
            },
            "12": {
                "class_type": "LTXVConditioning",
                "inputs": {
                    "positive": ["9", 0],
                    "negative": ["9", 1],
                    "frame_rate": float(fps),
                },
            },
            "13": {
                "class_type": "ModelSamplingLTXV",
                "inputs": {
                    "model": ["1", 0],
                    "max_shift": 2.05,
                    "base_shift": 0.95,
                    "latent": ["11", 0],
                },
            },
            "14": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["13", 0],
                    "positive": ["12", 0],
                    "negative": ["12", 1],
                    "latent_image": ["11", 0],
                    "seed": seed,
                    "steps": max(1, int(num_inference_steps or 8)),
                    "cfg": float(guidance_scale if guidance_scale is not None else 1.0),
                    "sampler_name": "euler_ancestral_cfg_pp",
                    "scheduler": "simple",
                    "denoise": 1.0,
                },
            },
            "15": {
                "class_type": "LTXVSeparateAVLatent",
                "inputs": {"av_latent": ["14", 0]},
            },
            "16": self._build_vae_decode_node("15", "3", width, height),
            "17": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["16", 0],
                    "frame_rate": float(fps),
                    "loop_count": 0,
                    "filename_prefix": "ltx23_i2v",
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "pingpong": False,
                    "save_output": True,
                    "videopreview": {"hidden": False, "paused": False, "params": {}},
                },
            },
        }

        if interpolation_multiplier > 1:
            self._add_rife_interpolation(
                workflow,
                source_node_id="16",
                video_combine_node_id="17",
                base_fps=fps,
                multiplier=interpolation_multiplier,
            )
        return workflow


    # Official LTX-2.5 distilled sigma schedules (ComfyUI 0.32 T2V/I2V templates).
    _LTX25_STAGE1_SIGMAS = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
    _LTX25_STAGE2_SIGMAS = "0.909375, 0.725, 0.421875, 0.0"

    @staticmethod
    def _ltx25_stage1_size(width: int, height: int, align: int = 32) -> tuple[int, int]:
        """Half-res stage-1 so the x2 latent upsampler lands on the requested size."""
        w = max(align, (int(width) // 2) // align * align)
        h = max(align, (int(height) // 2) // align * align)
        return w, h

    def _ltx25_loader_cfg(self, model_key: str) -> dict:
        self._ensure_ltx_models()
        cfg = self.LTX_MODELS.get(model_key, {})
        return {
            "unet": cfg.get("unet") or "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
            "clip": cfg.get("clip") or "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
            "vae": cfg.get("vae") or "ltx-2.5-video-vae-bf16.safetensors",
            "audio_vae": cfg.get("audio_vae") or "ltx-2.5-audio-vae-bf16.safetensors",
            "upscale_model": cfg.get("upscale_model") or "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
        }

    @staticmethod
    def _ltx25_default_negative(negative_prompt: str) -> str:
        if negative_prompt:
            return negative_prompt
        return (
            "blurry, low quality, worst quality, deformed, disfigured, poor anatomy, "
            "bad proportions, extra limbs, static, jitter, morphing, watermark"
        )

    def _create_ltx25_t2v_workflow(
        self,
        prompt: str,
        negative_prompt: str = "",
        model_key: str = "ltx25-distilled-int8",
        num_frames: int = 65,
        num_inference_steps: int = 8,
        guidance_scale: float = 1.0,
        width: int = 768,
        height: int = 512,
        seed: Optional[int] = None,
        fps: float = 16.0,
        interpolation_multiplier: int = 1,
    ) -> dict:
        """LTX-2.5 distilled T2V — official two-stage ComfyUI graph, video-only decode.

        Stage 1 runs at half the requested size; LTXVLatentUpsampler ×2 restores
        the user-facing resolution so 16GB cards stay at 768×512 out, not 1536×1024.

        Graph: UNETLoader + CLIPLoader(ltxv) + DiffVAE + audio VAE →
        CLIPTextEncode ×2 → EmptyLTXVLatentVideo (half) + LTXVEmptyLatentAudio →
        concat → DualCFG + ManualSigmas stage-1 → upsample → DualCFG stage-2 →
        separate → VAEDecode → VHS_VideoCombine.

        Audio is sampled (required by rotary PE) then discarded — same contract as 2.3.
        """
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)

        files = self._ltx25_loader_cfg(model_key)
        length = self._ltx_frame_count(num_frames)
        stage_w, stage_h = self._ltx25_stage1_size(width, height)
        clip_device = self._wan_clip_device()
        negative_prompt = self._ltx25_default_negative(negative_prompt)
        cfg = float(guidance_scale if guidance_scale is not None else 1.0)
        # Distilled schedule is fixed; keep the arg for API symmetry / logging.
        _ = num_inference_steps

        workflow = {
            "1": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": files["unet"], "weight_dtype": "default"},
            },
            "2": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": files["clip"],
                    "type": "ltxv",
                    "device": clip_device,
                },
            },
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": files["vae"]}},
            "4": {"class_type": "LTXVAudioVAELoader", "inputs": {"ckpt_name": files["audio_vae"]}},
            "5": {
                "class_type": "LatentUpscaleModelLoader",
                "inputs": {"model_name": files["upscale_model"]},
            },
            "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative_prompt}},
            "8": {
                "class_type": "EmptyLTXVLatentVideo",
                "inputs": {
                    "width": stage_w,
                    "height": stage_h,
                    "length": length,
                    "batch_size": 1,
                },
            },
            "9": {
                "class_type": "LTXVEmptyLatentAudio",
                "inputs": {
                    "frames_number": length,
                    "frame_rate": float(fps),
                    "batch_size": 1,
                    "audio_vae": ["4", 0],
                },
            },
            "10": {
                "class_type": "LTXVConcatAVLatent",
                "inputs": {"video_latent": ["8", 0], "audio_latent": ["9", 0]},
            },
            "11": {
                "class_type": "LTXVConditioning",
                "inputs": {
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "frame_rate": float(fps),
                },
            },
            "12": {
                "class_type": "CFGGuider",
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["11", 0],
                    "negative": ["11", 1],
                    "cfg": cfg,
                },
            },
            "13": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "13b": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "gradient_estimation"}},
            "14": {
                "class_type": "ManualSigmas",
                "inputs": {"sigmas": self._LTX25_STAGE1_SIGMAS},
            },
            "15": {
                "class_type": "RandomNoise",
                "inputs": {"noise_seed": seed},
            },
            "16": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {
                    "noise": ["15", 0],
                    "guider": ["12", 0],
                    "sampler": ["13", 0],
                    "sigmas": ["14", 0],
                    "latent_image": ["10", 0],
                },
            },
            "17": {
                "class_type": "LTXVSeparateAVLatent",
                "inputs": {"av_latent": ["16", 0]},
            },
            "18": {
                "class_type": "LTXVLatentUpsampler",
                "inputs": {
                    "samples": ["17", 0],
                    "upscale_model": ["5", 0],
                    "vae": ["3", 0],
                },
            },
            "19": {
                "class_type": "LTXVConcatAVLatent",
                "inputs": {"video_latent": ["18", 0], "audio_latent": ["17", 1]},
            },
            "20": {
                "class_type": "ManualSigmas",
                "inputs": {"sigmas": self._LTX25_STAGE2_SIGMAS},
            },
            "20b": {
                "class_type": "RandomNoise",
                "inputs": {"noise_seed": (seed + 1) % (2**31)},
            },
            "21": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {
                    "noise": ["20b", 0],
                    "guider": ["12", 0],
                    "sampler": ["13b", 0],
                    "sigmas": ["20", 0],
                    "latent_image": ["19", 0],
                },
            },
            "22": {
                "class_type": "LTXVSeparateAVLatent",
                "inputs": {"av_latent": ["21", 0]},
            },
            "23": self._build_vae_decode_node("22", "3", width, height),
            "24": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["23", 0],
                    "frame_rate": float(fps),
                    "loop_count": 0,
                    "filename_prefix": "ltx25_t2v",
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "pingpong": False,
                    "save_output": True,
                    "videopreview": {"hidden": False, "paused": False, "params": {}},
                },
            },
        }

        if interpolation_multiplier > 1:
            self._add_rife_interpolation(
                workflow,
                source_node_id="23",
                video_combine_node_id="24",
                base_fps=fps,
                multiplier=interpolation_multiplier,
            )
        return workflow

    def _create_ltx25_i2v_workflow(
        self,
        image_filename: str,
        prompt: str,
        negative_prompt: str = "",
        model_key: str = "ltx25-distilled-int8",
        num_frames: int = 65,
        num_inference_steps: int = 8,
        guidance_scale: float = 1.0,
        width: int = 768,
        height: int = 512,
        seed: Optional[int] = None,
        fps: float = 16.0,
        interpolation_multiplier: int = 1,
        strength: float = 1.0,
    ) -> dict:
        """LTX-2.5 distilled I2V — two-stage stack with LTXVImgToVideo start frame."""
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)

        files = self._ltx25_loader_cfg(model_key)
        length = self._ltx_frame_count(num_frames)
        stage_w, stage_h = self._ltx25_stage1_size(width, height)
        clip_device = self._wan_clip_device()
        negative_prompt = self._ltx25_default_negative(negative_prompt)
        cfg = float(guidance_scale if guidance_scale is not None else 1.0)
        _ = num_inference_steps

        workflow = {
            "1": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": files["unet"], "weight_dtype": "default"},
            },
            "2": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": files["clip"],
                    "type": "ltxv",
                    "device": clip_device,
                },
            },
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": files["vae"]}},
            "4": {"class_type": "LTXVAudioVAELoader", "inputs": {"ckpt_name": files["audio_vae"]}},
            "5": {
                "class_type": "LatentUpscaleModelLoader",
                "inputs": {"model_name": files["upscale_model"]},
            },
            "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative_prompt}},
            "8": {"class_type": "LoadImage", "inputs": {"image": image_filename}},
            "9": {
                "class_type": "LTXVPreprocess",
                "inputs": {"image": ["8", 0], "img_compression": 18},
            },
            "10": {
                "class_type": "LTXVImgToVideo",
                "inputs": {
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "vae": ["3", 0],
                    "image": ["9", 0],
                    "width": stage_w,
                    "height": stage_h,
                    "length": length,
                    "batch_size": 1,
                    "strength": float(strength),
                },
            },
            "11": {
                "class_type": "LTXVEmptyLatentAudio",
                "inputs": {
                    "frames_number": length,
                    "frame_rate": float(fps),
                    "batch_size": 1,
                    "audio_vae": ["4", 0],
                },
            },
            "12": {
                "class_type": "LTXVConcatAVLatent",
                "inputs": {"video_latent": ["10", 2], "audio_latent": ["11", 0]},
            },
            "13": {
                "class_type": "LTXVConditioning",
                "inputs": {
                    "positive": ["10", 0],
                    "negative": ["10", 1],
                    "frame_rate": float(fps),
                },
            },
            "14": {
                "class_type": "CFGGuider",
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["13", 0],
                    "negative": ["13", 1],
                    "cfg": cfg,
                },
            },
            "15": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "15b": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "gradient_estimation"}},
            "16": {
                "class_type": "ManualSigmas",
                "inputs": {"sigmas": self._LTX25_STAGE1_SIGMAS},
            },
            "17": {
                "class_type": "RandomNoise",
                "inputs": {"noise_seed": seed},
            },
            "18": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {
                    "noise": ["17", 0],
                    "guider": ["14", 0],
                    "sampler": ["15", 0],
                    "sigmas": ["16", 0],
                    "latent_image": ["12", 0],
                },
            },
            "19": {
                "class_type": "LTXVSeparateAVLatent",
                "inputs": {"av_latent": ["18", 0]},
            },
            "20": {
                "class_type": "LTXVLatentUpsampler",
                "inputs": {
                    "samples": ["19", 0],
                    "upscale_model": ["5", 0],
                    "vae": ["3", 0],
                },
            },
            # Re-anchor the source image on the upscaled latent before stage-2
            # sampling (official distilled-I2V structure) — without this, stage 2
            # re-paints full-res detail guided by text alone and identity drifts.
            "20b": {
                "class_type": "LTXVImgToVideoInplace",
                "inputs": {
                    "vae": ["3", 0],
                    "image": ["9", 0],
                    "latent": ["20", 0],
                    "strength": float(strength),
                    "bypass": False,
                },
            },
            "21": {
                "class_type": "LTXVConcatAVLatent",
                "inputs": {"video_latent": ["20b", 0], "audio_latent": ["19", 1]},
            },
            "22": {
                "class_type": "ManualSigmas",
                "inputs": {"sigmas": self._LTX25_STAGE2_SIGMAS},
            },
            "22b": {
                "class_type": "RandomNoise",
                "inputs": {"noise_seed": (seed + 1) % (2**31)},
            },
            "23": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {
                    "noise": ["22b", 0],
                    "guider": ["14", 0],
                    "sampler": ["15b", 0],
                    "sigmas": ["22", 0],
                    "latent_image": ["21", 0],
                },
            },
            "24": {
                "class_type": "LTXVSeparateAVLatent",
                "inputs": {"av_latent": ["23", 0]},
            },
            "25": self._build_vae_decode_node("24", "3", width, height),
            "26": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["25", 0],
                    "frame_rate": float(fps),
                    "loop_count": 0,
                    "filename_prefix": "ltx25_i2v",
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "pingpong": False,
                    "save_output": True,
                    "videopreview": {"hidden": False, "paused": False, "params": {}},
                },
            },
        }

        if interpolation_multiplier > 1:
            self._add_rife_interpolation(
                workflow,
                source_node_id="25",
                video_combine_node_id="26",
                base_fps=fps,
                multiplier=interpolation_multiplier,
            )
        return workflow


    # ── HunyuanVideo (Tencent, 13B) ────────────────────────────────────────
    # Guidance-distilled flow model: no negative prompt; the guidance scale rides
    # in FluxGuidance. Values mirror ComfyUI's bundled hunyuan_video template.
    HUNYUAN_SHIFT = 7.0
    HUNYUAN_VAE_TILE = {"tile_size": 256, "overlap": 64, "temporal_size": 64, "temporal_overlap": 8}
    # I2V v2 ("replace") weights. Higher = more weight on the text prompt vs the image.
    HUNYUAN_I2V_IMAGE_INTERLEAVE = 4

    def _hunyuan_cfg(self, model_key: str, fallback_key: str) -> dict:
        cfg = self.HUNYUAN_MODELS.get(model_key) or self.HUNYUAN_MODELS.get(fallback_key) or {}
        return {
            "unet": cfg.get("unet") or f"hunyuan-video-{'i2v' if 'i2v' in fallback_key else 't2v'}-720p-Q5_K_M.gguf",
            "clip_l": cfg.get("clip_l") or "clip_l.safetensors",
            "clip_llava": cfg.get("clip_llava") or "llava_llama3_fp8_scaled.safetensors",
            "vae": cfg.get("vae") or "hunyuan_video_vae_bf16.safetensors",
            "clip_vision": cfg.get("clip_vision") or "llava_llama3_vision.safetensors",
        }

    def _hunyuan_loader_nodes(self, cfg: dict) -> dict:
        """Nodes 1-3: GGUF UNet, DualCLIPLoader (clip_l + LLaVA-3), VAE."""
        clip_device = self._wan_clip_device()
        logger.info("HunyuanVideo workflow unet=%s clip_device=%s", cfg["unet"], clip_device)
        return {
            "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": cfg["unet"]}},
            "2": {
                "class_type": "DualCLIPLoader",
                "inputs": {
                    "clip_name1": cfg["clip_l"],
                    "clip_name2": cfg["clip_llava"],
                    "type": "hunyuan_video",
                    "device": clip_device,
                },
            },
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": cfg["vae"]}},
        }

    def _hunyuan_tail_nodes(
        self,
        *,
        guidance_node: str,
        latent: list,
        num_inference_steps: int,
        seed: int,
        fps: int,
        filename_prefix: str,
    ) -> dict:
        """Nodes 20-27: shift → guider/scheduler/sampler → tiled decode → mp4 mux."""
        return {
            "20": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": self.HUNYUAN_SHIFT}},
            "21": {"class_type": "BasicGuider", "inputs": {"model": ["20", 0], "conditioning": [guidance_node, 0]}},
            "22": {
                "class_type": "BasicScheduler",
                "inputs": {"model": ["20", 0], "scheduler": "simple", "steps": num_inference_steps, "denoise": 1.0},
            },
            "23": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "24": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
            "25": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {
                    "noise": ["24", 0],
                    "guider": ["21", 0],
                    "sampler": ["23", 0],
                    "sigmas": ["22", 0],
                    "latent_image": latent,
                },
            },
            "26": {
                "class_type": "VAEDecodeTiled",
                "inputs": {"samples": ["25", 0], "vae": ["3", 0], **self.HUNYUAN_VAE_TILE},
            },
            "27": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["26", 0],
                    "frame_rate": fps,
                    "loop_count": 0,
                    "filename_prefix": filename_prefix,
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "pingpong": False,
                    "save_output": True,
                    "videopreview": {"hidden": False, "paused": False, "params": {}},
                },
            },
        }

    def _create_hunyuan_t2v_workflow(
        self,
        prompt: str,
        model_key: str = "hunyuan-t2v",
        num_frames: int = 73,
        num_inference_steps: int = 20,
        guidance_scale: float = 6.0,
        width: int = 848,
        height: int = 480,
        seed: Optional[int] = None,
        fps: int = 24,
        interpolation_multiplier: int = 1,
    ) -> dict:
        """HunyuanVideo text-to-video on ComfyUI's native nodes with a GGUF UNet.

        Graph: DualCLIPLoader(clip_l + LLaVA-3, hunyuan_video) → CLIPTextEncode →
        FluxGuidance → BasicGuider → SamplerCustomAdvanced (euler/simple, shift 7)
        → VAEDecodeTiled → VHS mp4. ``num_frames`` must already be 4n+1.
        """
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)
        cfg = self._hunyuan_cfg(model_key, "hunyuan-t2v")
        workflow = self._hunyuan_loader_nodes(cfg)
        workflow.update({
            "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
            "5": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["4", 0], "guidance": guidance_scale}},
            "6": {
                "class_type": "EmptyHunyuanLatentVideo",
                "inputs": {"width": width, "height": height, "length": num_frames, "batch_size": 1},
            },
        })
        workflow.update(self._hunyuan_tail_nodes(
            guidance_node="5",
            latent=["6", 0],
            num_inference_steps=num_inference_steps,
            seed=seed,
            fps=fps,
            filename_prefix="hunyuan_t2v",
        ))
        if interpolation_multiplier > 1:
            self._add_rife_interpolation(
                workflow,
                source_node_id="26",
                video_combine_node_id="27",
                base_fps=fps,
                multiplier=interpolation_multiplier,
            )
        return workflow

    def _create_hunyuan_i2v_workflow(
        self,
        image_filename: str,
        prompt: str,
        model_key: str = "hunyuan-i2v",
        num_frames: int = 73,
        num_inference_steps: int = 20,
        guidance_scale: float = 6.0,
        width: int = 848,
        height: int = 480,
        seed: Optional[int] = None,
        fps: int = 24,
        interpolation_multiplier: int = 1,
    ) -> dict:
        """HunyuanVideo image-to-video (v2 "replace" conditioning).

        The start frame goes through the LLaVA vision tower into
        TextEncodeHunyuanVideo_ImageToVideo and is also written into the first
        latent frame by HunyuanImageToVideo; sampling/decoding matches the T2V graph.
        """
        if seed is None:
            seed = int(time.time() * 1000) % (2**31)
        cfg = self._hunyuan_cfg(model_key, "hunyuan-i2v")
        workflow = self._hunyuan_loader_nodes(cfg)
        workflow.update({
            "4": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": cfg["clip_vision"]}},
            "5": {"class_type": "LoadImage", "inputs": {"image": image_filename}},
            "6": {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["4", 0], "image": ["5", 0], "crop": "center"}},
            "7": {
                "class_type": "TextEncodeHunyuanVideo_ImageToVideo",
                "inputs": {
                    "clip": ["2", 0],
                    "clip_vision_output": ["6", 0],
                    "prompt": prompt,
                    "image_interleave": self.HUNYUAN_I2V_IMAGE_INTERLEAVE,
                },
            },
            "8": {
                "class_type": "HunyuanImageToVideo",
                "inputs": {
                    "positive": ["7", 0],
                    "vae": ["3", 0],
                    "width": width,
                    "height": height,
                    "length": num_frames,
                    "batch_size": 1,
                    "guidance_type": "v2 (replace)",
                    "start_image": ["5", 0],
                },
            },
            "9": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["8", 0], "guidance": guidance_scale}},
        })
        workflow.update(self._hunyuan_tail_nodes(
            guidance_node="9",
            latent=["8", 1],
            num_inference_steps=num_inference_steps,
            seed=seed,
            fps=fps,
            filename_prefix="hunyuan_i2v",
        ))
        if interpolation_multiplier > 1:
            self._add_rife_interpolation(
                workflow,
                source_node_id="26",
                video_combine_node_id="27",
                base_fps=fps,
                multiplier=interpolation_multiplier,
            )
        return workflow

    def _build_vae_decode_node(self, samples_node: str, vae_node: str, width: int, height: int) -> dict:
        """Pick the right VAE decode strategy based on resolution.

        Standard VAEDecode works fine for tiny tests. Above that, tiled decoding
        saves your VRAM from a very bad day. Lowered threshold to 720 for video.
        """
        use_tiled = width >= 720 or height >= 720
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


    def _add_freeu_node(self, workflow: dict, model_node_id: str, is_cogvideo: bool = False) -> str:
        """Insert FreeU_V2 node to improve generation quality.
        Returns the ID of the new FreeU node.
        """
        existing_ids = [int(k) for k in workflow.keys() if k.isdigit()]
        freeu_id = str(max(existing_ids) + 1)
        
        # FreeU V2 defaults (tuned for video models generally)
        b1, b2, s1, s2 = 1.01, 1.02, 0.99, 0.95
        if is_cogvideo:
            b1, b2, s1, s2 = 1.1, 1.2, 0.9, 0.2

        workflow[freeu_id] = {
            "class_type": "FreeU_V2",
            "inputs": {
                "model": [model_node_id, 0],
                "b1": b1,
                "b2": b2,
                "s1": s1,
                "s2": s2,
            }
        }
        logger.info(f"Added FreeU_V2 node ({freeu_id}) after model node {model_node_id}")
        return freeu_id


    def _add_lora_loader(self, workflow: dict, model_node_id: str, clip_node_id: str, lora_name: str, strength: float = 1.0) -> tuple[str, str]:
        """Insert a LoraLoader node.
        Returns the new (model_node_id, clip_node_id) to use in downstream nodes.
        """
        if not lora_name:
            return model_node_id, clip_node_id
            
        existing_ids = [int(k) for k in workflow.keys() if k.isdigit()]
        lora_id = str(max(existing_ids) + 1)
        
        workflow[lora_id] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": [model_node_id, 0],
                "clip": [clip_node_id, 0],
                "lora_name": lora_name,
                "strength_model": strength,
                "strength_clip": strength,
            }
        }
        logger.info(f"Added LoraLoader node ({lora_id}) for {lora_name} (strength: {strength})")
        return lora_id, lora_id


    def _add_face_detailer_node(self, workflow: dict, source_node_id: str, video_combine_node_id: str) -> dict:
        """Insert FaceRestoreWithModel node for human realism before VHS_VideoCombine.
        """
        existing_ids = [int(k) for k in workflow.keys() if k.isdigit()]
        restore_loader_id = str(max(existing_ids) + 1)
        restore_node_id = str(max(existing_ids) + 2)
        
        workflow[restore_loader_id] = {
            "class_type": "FaceRestoreModelLoader",
            "inputs": {
                "model_name": "codeformer.pth"
            }
        }
        
        workflow[restore_node_id] = {
            "class_type": "FaceRestoreCFWithModel",
            "inputs": {
                "facerestore_model": [restore_loader_id, 0],
                "image": [source_node_id, 0],
                "facedetection": "retinaface_resnet50",
                "codeformer_fidelity": 0.5,
            },
        }

        # Rewire VHS_VideoCombine to take frames from FaceRestore
        workflow[video_combine_node_id]["inputs"]["images"] = [restore_node_id, 0]

        logger.info(f"Added FaceRestoreCFWithModel ({restore_node_id}) after node {source_node_id}")
        return workflow


    def _add_frame_export(self, workflow: dict, item_id) -> bool:
        """Q3: tee the FINAL frames (the exact images feeding VHS_VideoCombine — after
        any upscale / face-restore / RIFE interpolation) into a lossless PNG sequence
        ALONGSIDE the MP4. Additive: never disturbs the video path; if the frame source
        can't be found we just skip. The PNGs let the user stitch in their own editor
        without the h264/yuv420p loss the MP4 carries."""
        try:
            vhs_node_id = next(
                (nid for nid, node in workflow.items() if node.get("class_type") == "VHS_VideoCombine"),
                None,
            )
            if not vhs_node_id:
                return False
            source_ref = workflow[vhs_node_id]["inputs"].get("images", [None])[0]
            if not source_ref:
                return False
            import re as _re
            slug = _re.sub(r"[^A-Za-z0-9_-]", "_", str(item_id or "clip"))
            existing_ids = [int(k) for k in workflow.keys() if str(k).isdigit()]
            nid = (max(existing_ids) + 1) if existing_ids else 9000
            while str(nid) in workflow:
                nid += 1
            workflow[str(nid)] = {
                "class_type": "SaveImage",
                "inputs": {
                    "filename_prefix": f"frames/{slug}_frame",
                    "images": [source_ref, 0],
                },
            }
            logger.info("Frame export enabled: SaveImage(%s) tapped from frame source %s", nid, source_ref)
            return True
        except Exception as e:
            logger.warning("Frame export node not added (non-fatal): %s", e)
            return False

