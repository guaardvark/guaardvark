"""
Animation Generator Service
Generates animated GIFs and MP4 videos by producing frame sequences via
txt2img (frame 1) + img2img (frames 2-N), then assembling with imageio.
Optionally uses a vision model to steer frame evolution.
"""

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class AnimationRequest:
    prompt: str
    motion_prompt: str = ""
    num_frames: int = 8
    strength: float = 0.20
    width: int = 512
    height: int = 512
    fps: int = 8
    output_format: str = "both"  # gif, mp4, both
    style: str = "realistic"
    model: str = "sd-1.5"
    use_vision_steering: bool = False
    loop: bool = True  # ping-pong for smooth loop
    seed: int = None
    negative_prompt: str = "blurry, low quality, distorted, deformed"
    num_inference_steps: int = 20
    guidance_scale: float = 7.5


@dataclass
class AnimationResult:
    success: bool = False
    gif_path: Optional[str] = None
    mp4_path: Optional[str] = None
    gif_url: Optional[str] = None
    mp4_url: Optional[str] = None
    frame_count: int = 0
    generation_time: float = 0.0
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class AnimationGenerator:
    """Generates frame-sequence animations using SD txt2img + img2img."""

    def __init__(self):
        from backend.config import OUTPUT_DIR
        self.output_dir = Path(OUTPUT_DIR) / "generated_animations"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, request: AnimationRequest) -> AnimationResult:
        """Main entry point: generate an animation from a request."""
        result = AnimationResult()
        start_time = time.time()

        try:
            from backend.services.offline_image_generator import (
                get_image_generator, ImageGenerationRequest
            )

            generator = get_image_generator()
            if not generator.service_available:
                result.error = "Image generation service not available"
                return result

            # Validate frame count
            request.num_frames = max(2, min(request.num_frames, 24))
            # Ensure dimensions are divisible by 8
            request.width = (request.width // 8) * 8
            request.height = (request.height // 8) * 8

            logger.info(
                f"AnimationGenerator: {request.num_frames} frames @ "
                f"{request.width}x{request.height}, strength={request.strength}, "
                f"vision_steering={request.use_vision_steering}"
            )

            # Step 1: Generate frame 1 via txt2img
            frame1_prompt = request.prompt
            if request.motion_prompt:
                frame1_prompt = f"{request.prompt}, beginning of motion: {request.motion_prompt}"

            frame1_request = ImageGenerationRequest(
                prompt=frame1_prompt,
                negative_prompt=request.negative_prompt,
                width=request.width,
                height=request.height,
                num_inference_steps=request.num_inference_steps,
                guidance_scale=request.guidance_scale,
                style=request.style,
                model=request.model,
                seed=request.seed,
            )

            frame1_result = generator.generate_image(frame1_request)
            if not frame1_result.success or not frame1_result.image_path:
                result.error = f"Frame 1 generation failed: {frame1_result.error}"
                result.generation_time = time.time() - start_time
                return result

            frames: List[Image.Image] = []
            current_frame = Image.open(frame1_result.image_path).convert("RGB")
            frames.append(current_frame.copy())
            base_seed = frame1_result.seed_used or 42

            logger.info(f"Frame 1 generated in {frame1_result.generation_time:.1f}s")

            # Step 2: Generate frames 2-N via img2img
            current_prompt = request.prompt
            for i in range(1, request.num_frames):
                progress = (i + 1) / request.num_frames
                frame_prompt = self._build_frame_prompt(
                    request.prompt, request.motion_prompt, i, request.num_frames
                )

                # Vision steering: every 4 frames, ask the vision model
                if request.use_vision_steering and i % 4 == 0 and i > 0:
                    steered = self._vision_steer(
                        current_frame, request.prompt, request.motion_prompt, i, request.num_frames
                    )
                    if steered:
                        frame_prompt = steered
                        logger.info(f"Vision steering at frame {i+1}: {steered[:80]}...")

                frame_result = generator.generate_image_from_image(
                    prompt=frame_prompt,
                    init_image=current_frame,
                    strength=request.strength,
                    negative_prompt=request.negative_prompt,
                    width=request.width,
                    height=request.height,
                    num_inference_steps=request.num_inference_steps,
                    guidance_scale=request.guidance_scale,
                    seed=base_seed + i,
                    model=request.model,
                )

                if not frame_result.success or not frame_result.image_path:
                    logger.warning(f"Frame {i+1} failed: {frame_result.error}. Using previous frame.")
                    # On failure, duplicate previous frame to keep sequence going
                    frames.append(current_frame.copy())
                    continue

                current_frame = Image.open(frame_result.image_path).convert("RGB")
                frames.append(current_frame.copy())
                logger.info(f"Frame {i+1}/{request.num_frames} generated in {frame_result.generation_time:.1f}s")

            # Step 3: Ping-pong loop (append reversed frames minus endpoints)
            if request.loop and len(frames) > 2:
                frames = frames + frames[-2:0:-1]

            result.frame_count = len(frames)

            # Step 4: Assemble into GIF and/or MP4
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_id = uuid.uuid4().hex[:8]
            base_name = f"anim_{timestamp}_{unique_id}"

            if request.output_format in ("gif", "both"):
                gif_path = self._assemble_gif(frames, base_name, request.fps)
                if gif_path:
                    result.gif_path = str(gif_path)
                    result.gif_url = f"/api/outputs/generated_animations/{gif_path.name}"

            if request.output_format in ("mp4", "both"):
                mp4_path = self._assemble_mp4(frames, base_name, request.fps)
                if mp4_path:
                    result.mp4_path = str(mp4_path)
                    result.mp4_url = f"/api/outputs/generated_animations/{mp4_path.name}"

            result.success = True
            result.generation_time = time.time() - start_time
            result.metadata = {
                "prompt": request.prompt,
                "motion_prompt": request.motion_prompt,
                "num_frames": request.num_frames,
                "total_frames": result.frame_count,
                "strength": request.strength,
                "fps": request.fps,
                "width": request.width,
                "height": request.height,
                "loop": request.loop,
                "vision_steering": request.use_vision_steering,
                "model": request.model,
            }

            logger.info(
                f"Animation generated: {result.frame_count} frames in "
                f"{result.generation_time:.1f}s "
                f"(GIF: {result.gif_url}, MP4: {result.mp4_url})"
            )

        except Exception as e:
            logger.error(f"Animation generation failed: {e}", exc_info=True)
            result.error = str(e)
            result.generation_time = time.time() - start_time

        return result

    def _build_frame_prompt(
        self, base_prompt: str, motion_prompt: str, frame_idx: int, total_frames: int
    ) -> str:
        """Build an evolving prompt for frame N that incorporates motion."""
        if not motion_prompt:
            return base_prompt

        progress = frame_idx / max(total_frames - 1, 1)
        # Describe progression so the model understands temporal position
        if progress < 0.25:
            phase = "beginning"
        elif progress < 0.5:
            phase = "early middle"
        elif progress < 0.75:
            phase = "late middle"
        else:
            phase = "near end"

        return (
            f"{base_prompt}, {motion_prompt}, "
            f"{phase} of the motion sequence, frame {frame_idx + 1} of {total_frames}"
        )

    def _vision_steer(
        self, current_frame: Image.Image, base_prompt: str,
        motion_prompt: str, frame_idx: int, total_frames: int
    ) -> Optional[str]:
        """Feed current frame to the vision model to get a refined prompt."""
        try:
            import ollama
            import base64
            import io

            # Convert PIL image to base64
            buffer = io.BytesIO()
            current_frame.save(buffer, format="PNG")
            img_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

            remaining = total_frames - frame_idx
            steering_prompt = (
                f"You are generating frames for an animation. "
                f"The scene is: {base_prompt}. "
                f"The motion is: {motion_prompt}. "
                f"This is frame {frame_idx + 1} of {total_frames} ({remaining} frames remaining). "
                f"Describe this image in a single detailed sentence suitable as a "
                f"Stable Diffusion prompt, incorporating slight progression of the motion "
                f"for the next frame. Be specific about visual details, pose, and composition. "
                f"Output ONLY the prompt, nothing else."
            )

            # Use a vision model — try qwen3-vl first
            vision_models = ["qwen3-vl:8b", "llava:7b", "moondream:latest"]
            for model in vision_models:
                try:
                    response = ollama.chat(
                        model=model,
                        messages=[{
                            "role": "user",
                            "content": steering_prompt,
                            "images": [img_b64],
                        }],
                        options={"num_predict": 150, "temperature": 0.3},
                    )
                    text = response.get("message", {}).get("content", "").strip()
                    if text and len(text) > 20:
                        # Strip any thinking tags
                        import re
                        text = re.sub(r'<think>[\s\S]*?</think>\s*', '', text).strip()
                        return text
                except Exception:
                    continue

            return None
        except Exception as e:
            logger.warning(f"Vision steering failed: {e}")
            return None

    def _assemble_gif(self, frames: List[Image.Image], base_name: str, fps: int) -> Optional[Path]:
        """Assemble frames into an animated GIF."""
        try:
            output_path = self.output_dir / f"{base_name}.gif"
            duration_ms = int(1000 / fps)

            # Save using PIL for GIF (better quality than imageio for GIF)
            frames[0].save(
                output_path,
                save_all=True,
                append_images=frames[1:],
                duration=duration_ms,
                loop=0,
                optimize=True,
            )

            logger.info(f"GIF assembled: {output_path} ({os.path.getsize(output_path) / 1024:.0f}KB)")
            return output_path
        except Exception as e:
            logger.error(f"GIF assembly failed: {e}", exc_info=True)
            return None

    def _assemble_mp4(self, frames: List[Image.Image], base_name: str, fps: int) -> Optional[Path]:
        """Assemble frames into an MP4 video."""
        try:
            import numpy as np

            output_path = self.output_dir / f"{base_name}.mp4"

            # Convert PIL images to numpy arrays for imageio
            np_frames = [np.array(f) for f in frames]

            try:
                import imageio
                imageio.mimwrite(
                    str(output_path),
                    np_frames,
                    fps=fps,
                    macro_block_size=1,
                )
            except Exception:
                # Fallback: use FFmpeg subprocess
                import subprocess
                import tempfile

                with tempfile.TemporaryDirectory() as tmpdir:
                    for i, frame in enumerate(frames):
                        frame.save(os.path.join(tmpdir, f"frame_{i:04d}.png"))

                    subprocess.run([
                        "ffmpeg", "-y",
                        "-framerate", str(fps),
                        "-i", os.path.join(tmpdir, "frame_%04d.png"),
                        "-c:v", "libx264",
                        "-pix_fmt", "yuv420p",
                        "-preset", "fast",
                        str(output_path),
                    ], capture_output=True, check=True)

            logger.info(f"MP4 assembled: {output_path} ({os.path.getsize(output_path) / 1024:.0f}KB)")
            return output_path
        except Exception as e:
            logger.error(f"MP4 assembly failed: {e}", exc_info=True)
            return None


# Singleton
_animation_generator = None


def get_animation_generator() -> AnimationGenerator:
    global _animation_generator
    if _animation_generator is None:
        _animation_generator = AnimationGenerator()
    return _animation_generator
