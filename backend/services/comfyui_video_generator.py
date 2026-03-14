
import logging
import json
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
        "cogvideox-5b-i2v": "THUDM/CogVideoX-5b-I2V",
    }

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
        return workflow

    def _create_cogvideox_i2v_workflow(
        self,
        image_filename: str,
        prompt: str,
        negative_prompt: str = "",
        model_name: str = "THUDM/CogVideoX-5b-I2V",
        num_frames: int = 49,
        num_inference_steps: int = 50,
        guidance_scale: float = 6.0,
        width: int = 720,
        height: int = 480,
        seed: Optional[int] = None,
        fps: int = 8,
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

    def generate_video(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        if not self.service_available:
            return VideoGenerationResult(
                success=False,
                error="ComfyUI service not available. Please start ComfyUI at http://127.0.0.1:8188",
                prompt_used=request.prompt,
            )

        batch_dir = request.output_dir or (self.cache_dir / f"batch_{uuid.uuid4().hex}")
        batch_dir = Path(batch_dir)

        item_id = request.metadata.get("item_id") if request.metadata else None
        if not item_id:
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

            # ── Route by model type ──────────────────────────────────
            if model in ("cogvideox-2b", "cogvideox-5b"):
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

            logger.info("Sending workflow to ComfyUI...")
            prompt_id = self._queue_prompt(workflow)

            if not prompt_id:
                result.error = "Failed to queue workflow in ComfyUI"
                return result

            logger.info(f"Waiting for ComfyUI to complete generation (prompt_id: {prompt_id})...")
            outputs = self._wait_for_completion(prompt_id, timeout=600)

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

            logger.info(f"Video generation successful: {result.video_path}")
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
