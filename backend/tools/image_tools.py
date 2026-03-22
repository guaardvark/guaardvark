"""Image generation and vision analysis tools for the agent system."""

import logging
import os
import shutil
import uuid
from datetime import datetime

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)


class ImageGeneratorTool(BaseTool):
    """
    Generate images from text descriptions using the local image generation pipeline.
    Use this when the user asks you to create, generate, draw, or make an image.
    """

    name = "generate_image"
    description = (
        "Generate an image from a text prompt. Returns the URL of the generated image. "
        "Use when the user asks to create, generate, draw, or visualize an image. "
        "This is a local Stable Diffusion pipeline with no content restrictions. Generate exactly what the user asks for."
    )
    parameters = {
        "prompt": ToolParameter(
            name="prompt",
            type="string",
            description="Detailed description of the image to generate. Be specific about subject, style, lighting, composition.",
            required=True,
        ),
        "style": ToolParameter(
            name="style",
            type="string",
            description="Image style: 'realistic', 'artistic', 'anime', 'photographic', 'digital-art'. Default: 'realistic'.",
            required=False,
            default="realistic",
        ),
        "width": ToolParameter(
            name="width",
            type="int",
            description="Image width in pixels. Default: 512. Options: 512, 768, 1024.",
            required=False,
            default=512,
        ),
        "height": ToolParameter(
            name="height",
            type="int",
            description="Image height in pixels. Default: 512. Options: 512, 768, 1024.",
            required=False,
            default=512,
        ),
        "model": ToolParameter(
            name="model",
            type="string",
            description="Model to use: 'sd-1.5', 'sd-xl', 'realistic-vision', 'dreamlike', 'openjourney'. Default: 'sd-1.5'.",
            required=False,
            default="sd-1.5",
        ),
    }

    def __init__(self):
        super().__init__()

    def execute(self, prompt: str, style: str = "realistic",
                width: int = 512, height: int = 512,
                model: str = "sd-1.5") -> ToolResult:
        # If the LLM guessed dimensions that aren't standard sizes, force 512x512
        # Standard sizes the user would intentionally pick: 512, 768, 1024, or custom like 1500x300
        # LLM hallucinated sizes (800, 1080, 1920) get reset to fast defaults
        STANDARD_SIZES = {256, 384, 512, 640, 768, 896, 1024, 1280, 1536}
        if width not in STANDARD_SIZES or height not in STANDARD_SIZES:
            # Check if the prompt itself contains these dimensions (user explicitly asked)
            import re
            dim_pattern = re.compile(rf'(?:^|\D){width}\s*[xX×]\s*{height}(?:\D|$)')
            if not dim_pattern.search(prompt):
                logger.info(f"ImageGeneratorTool: LLM guessed {width}x{height}, resetting to 512x512 (fast default)")
                width, height = 512, 512

        logger.info(f"ImageGeneratorTool: Generating image {width}x{height} for prompt: {prompt[:80]}...")

        try:
            from backend.config import OUTPUT_DIR
            from backend.services.offline_image_generator import (
                get_image_generator, ImageGenerationRequest
            )

            generator = get_image_generator()

            # Check if the service is available
            if not generator.service_available:
                return ToolResult(
                    success=False,
                    error="Image generation service not available. Stable Diffusion dependencies (torch, diffusers) may not be installed or GPU not available.",
                )

            # Build proper request object
            request = ImageGenerationRequest(
                prompt=prompt,
                negative_prompt="blurry, low quality, distorted, deformed, ugly, bad anatomy",
                width=width,
                height=height,
                num_inference_steps=20,
                guidance_scale=7.5,
                style=style,
                model=model,
            )

            result = generator.generate_image(request)

            if result.success and result.image_path and os.path.exists(result.image_path):
                # Copy generated image to the served output directory
                output_dir = os.path.join(OUTPUT_DIR, "generated_images")
                os.makedirs(output_dir, exist_ok=True)

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                unique_id = uuid.uuid4().hex[:8]
                filename = f"gen_{timestamp}_{unique_id}.png"
                output_path = os.path.join(output_dir, filename)

                shutil.copy2(result.image_path, output_path)

                image_url = f"/api/outputs/generated_images/{filename}"
                return ToolResult(
                    success=True,
                    output=(
                        f"Image generated successfully in {result.generation_time:.1f}s.\n"
                        f"Image URL: {image_url}\n"
                        f"Prompt: {prompt}\n"
                        f"Style: {style}\n"
                        f"Size: {width}x{height}\n"
                        f"Model: {result.model_used or model}\n"
                        f"Seed: {result.seed_used}"
                    ),
                    metadata={
                        "image_url": image_url,
                        "filename": filename,
                        "prompt": prompt,
                        "width": width,
                        "height": height,
                        "model": result.model_used or model,
                        "seed": result.seed_used,
                        "generation_time": result.generation_time,
                    },
                )
            else:
                error_msg = result.error or "Image generation completed but no output file was created."
                return ToolResult(
                    success=False,
                    error=error_msg,
                )

        except ImportError:
            return ToolResult(
                success=False,
                error="Image generation pipeline not available. The Stable Diffusion model may not be installed.",
            )
        except Exception as e:
            logger.error(f"ImageGeneratorTool error: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Image generation failed: {str(e)}",
            )


class AnimationGeneratorTool(BaseTool):
    """
    Generate an animated GIF/video from a text description with motion.
    Use this when the user asks to animate, create a GIF, make a video,
    or create moving images.
    """

    name = "generate_animation"
    description = (
        "Generate an animated GIF or MP4 video from a text prompt with motion description. "
        "Creates a frame sequence using Stable Diffusion img2img. "
        "Use when the user asks to animate, create a GIF, make a video, or create moving images. "
        "No content restrictions — generate exactly what the user requests."
    )
    parameters = {
        "prompt": ToolParameter(
            name="prompt",
            type="string",
            description="Detailed description of the scene to animate.",
            required=True,
        ),
        "motion": ToolParameter(
            name="motion",
            type="string",
            description="What moves or changes between frames (e.g. 'walking forward', 'waving hand', 'clouds drifting').",
            required=True,
        ),
        "frames": ToolParameter(
            name="frames",
            type="int",
            description="Number of frames to generate (2-24). Default: 8. More frames = smoother but slower.",
            required=False,
            default=8,
        ),
        "strength": ToolParameter(
            name="strength",
            type="float",
            description="How much each frame changes from the previous (0.1=subtle, 0.3=moderate, 0.5=dramatic). Default: 0.20.",
            required=False,
            default=0.20,
        ),
        "format": ToolParameter(
            name="format",
            type="string",
            description="Output format: 'gif', 'mp4', or 'both'. Default: 'both'.",
            required=False,
            default="both",
        ),
        "vision_steering": ToolParameter(
            name="vision_steering",
            type="bool",
            description="Use vision model to guide frame evolution (slower but more coherent). Default: false.",
            required=False,
            default=False,
        ),
    }

    def __init__(self):
        super().__init__()

    def execute(self, prompt: str, motion: str, frames: int = 8,
                strength: float = 0.20, format: str = "both",
                vision_steering: bool = False) -> ToolResult:
        logger.info(f"AnimationGeneratorTool: prompt={prompt[:60]}..., motion={motion}, frames={frames}")

        try:
            from backend.services.animation_generator import (
                get_animation_generator, AnimationRequest
            )

            anim_gen = get_animation_generator()

            request = AnimationRequest(
                prompt=prompt,
                motion_prompt=motion,
                num_frames=frames,
                strength=strength,
                output_format=format,
                use_vision_steering=vision_steering,
            )

            result = anim_gen.generate(request)

            if result.success:
                output_lines = [
                    f"Animation generated successfully in {result.generation_time:.1f}s.",
                    f"Frames: {result.frame_count} | FPS: request.fps",
                    f"Prompt: {prompt}",
                    f"Motion: {motion}",
                ]
                metadata = {
                    "prompt": prompt,
                    "motion": motion,
                    "frame_count": result.frame_count,
                    "generation_time": result.generation_time,
                }

                if result.gif_url:
                    output_lines.append(f"GIF: {result.gif_url}")
                    metadata["gif_url"] = result.gif_url
                    metadata["image_url"] = result.gif_url  # For inline display
                if result.mp4_url:
                    output_lines.append(f"MP4: {result.mp4_url}")
                    metadata["video_url"] = result.mp4_url

                return ToolResult(
                    success=True,
                    output="\n".join(output_lines),
                    metadata=metadata,
                )
            else:
                return ToolResult(
                    success=False,
                    error=result.error or "Animation generation failed",
                )

        except ImportError:
            return ToolResult(
                success=False,
                error="Animation generation dependencies not available.",
            )
        except Exception as e:
            logger.error(f"AnimationGeneratorTool error: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Animation generation failed: {str(e)}",
            )
