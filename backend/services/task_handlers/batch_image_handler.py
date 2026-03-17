# backend/services/task_handlers/batch_image_handler.py
# Handler for batch image generation tasks
# Version 2.0 - Full implementation wrapping batch_image_generator

import logging
from datetime import datetime
from typing import Dict, Any, Optional, Callable, List

from .base_handler import BaseTaskHandler, TaskResult, TaskResultStatus

logger = logging.getLogger(__name__)


class BatchImageHandler(BaseTaskHandler):
    """
    Handler for batch image generation.
    Wraps: batch_image_generation_api.py, batch_image_generator.py
    """

    @property
    def handler_name(self) -> str:
        return "batch_image"

    @property
    def display_name(self) -> str:
        return "Batch Image Generation"

    @property
    def process_type(self) -> str:
        return "image_generation"

    @property
    def celery_queue(self) -> str:
        return "generation"

    @property
    def default_priority(self) -> int:
        return 3  # Higher priority for image tasks (GPU-bound)

    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "required": ["prompts"],
            "properties": {
                "prompts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string"},
                            "negative_prompt": {"type": "string", "default": ""},
                            "style": {"type": "string", "default": "realistic"},
                            "width": {"type": "integer", "default": 512},
                            "height": {"type": "integer", "default": 512},
                            "steps": {"type": "integer", "default": 20},
                            "guidance": {"type": "number", "default": 7.5},
                            "seed": {"type": "integer"},
                            "model": {"type": "string", "default": "sd-1.5"},
                        },
                        "required": ["prompt"],
                    },
                    "description": "List of image generation prompts",
                },
                "batch_name": {"type": "string", "description": "Name for the batch"},
                "model": {
                    "type": "string",
                    "default": "sd-1.5",
                    "enum": [
                        "sd-1.5",
                        "sd-xl",
                        "dreamlike",
                        "deliberate",
                        "realistic-vision",
                    ],
                    "description": "Default image generation model",
                },
                "max_workers": {
                    "type": "integer",
                    "default": 2,
                    "description": "Number of concurrent workers (use 1 for GPU)",
                },
                "generate_thumbnails": {
                    "type": "boolean",
                    "default": True,
                    "description": "Generate thumbnail images",
                },
                "content_preset": {
                    "type": "string",
                    "enum": [
                        "auto",
                        "person_portrait",
                        "person_full_body",
                        "product_photo",
                        "landscape",
                        "abstract",
                    ],
                    "description": "Content preset for quality enhancement",
                },
                "auto_enhance": {
                    "type": "boolean",
                    "default": True,
                    "description": "Enable automatic quality enhancement",
                },
                "enhance_anatomy": {
                    "type": "boolean",
                    "default": True,
                    "description": "Enhance human anatomy in images",
                },
                "enhance_faces": {
                    "type": "boolean",
                    "default": True,
                    "description": "Enhance facial features",
                },
                "enhance_hands": {
                    "type": "boolean",
                    "default": True,
                    "description": "Enhance hand rendering",
                },
            },
        }

    def execute(
        self,
        task: Any,
        config: Dict[str, Any],
        progress_callback: Callable[[int, str, Optional[Dict[str, Any]]], None],
    ) -> TaskResult:
        """
        Execute batch image generation.
        Uses the BatchImageGenerator service for actual generation.
        """
        started_at = datetime.now()

        try:
            # Import the batch image generator
            from backend.services.batch_image_generator import (
                get_batch_image_generator,
                BatchPrompt,
                BatchImageRequest,
            )

            generator = get_batch_image_generator()
            if not generator:
                return TaskResult(
                    status=TaskResultStatus.FAILED,
                    message="Batch image generator service not available",
                    error_message="Could not initialize BatchImageGenerator",
                    started_at=started_at,
                    completed_at=datetime.now(),
                )

            # Parse prompts from config
            raw_prompts = config.get("prompts", [])
            if not raw_prompts:
                return TaskResult(
                    status=TaskResultStatus.FAILED,
                    message="No prompts provided",
                    error_message="prompts list is empty",
                    started_at=started_at,
                    completed_at=datetime.now(),
                )

            progress_callback(
                0,
                f"Preparing batch of {len(raw_prompts)} images",
                {"total_images": len(raw_prompts)},
            )

            # Convert to BatchPrompt objects
            batch_prompts = []
            default_model = config.get("model", "sd-1.5")
            content_preset = config.get("content_preset")
            auto_enhance = config.get("auto_enhance", True)
            enhance_anatomy = config.get("enhance_anatomy", True)
            enhance_faces = config.get("enhance_faces", True)
            enhance_hands = config.get("enhance_hands", True)

            for i, p in enumerate(raw_prompts):
                if isinstance(p, str):
                    # Simple string prompt
                    batch_prompts.append(
                        BatchPrompt(
                            id=f"prompt_{i+1}",
                            prompt=p,
                            model=default_model,
                            content_preset=content_preset,
                            auto_enhance=auto_enhance,
                            enhance_anatomy=enhance_anatomy,
                            enhance_faces=enhance_faces,
                            enhance_hands=enhance_hands,
                        )
                    )
                elif isinstance(p, dict):
                    # Full prompt config
                    batch_prompts.append(
                        BatchPrompt(
                            id=p.get("id", f"prompt_{i+1}"),
                            prompt=p.get("prompt", ""),
                            negative_prompt=p.get("negative_prompt", ""),
                            style=p.get("style", "realistic"),
                            width=p.get("width", 512),
                            height=p.get("height", 512),
                            steps=p.get("steps", 20),
                            guidance=p.get("guidance", 7.5),
                            seed=p.get("seed"),
                            model=p.get("model", default_model),
                            content_preset=p.get("content_preset", content_preset),
                            auto_enhance=p.get("auto_enhance", auto_enhance),
                            enhance_anatomy=p.get("enhance_anatomy", enhance_anatomy),
                            enhance_faces=p.get("enhance_faces", enhance_faces),
                            enhance_hands=p.get("enhance_hands", enhance_hands),
                        )
                    )

            if not batch_prompts:
                return TaskResult(
                    status=TaskResultStatus.FAILED,
                    message="No valid prompts after parsing",
                    error_message="Could not parse any valid prompts from config",
                    started_at=started_at,
                    completed_at=datetime.now(),
                )

            progress_callback(
                5, f"Starting generation of {len(batch_prompts)} images", None
            )

            # Create batch request
            batch_name = config.get("batch_name", f"task_{task.id}")
            max_workers = config.get("max_workers", 2)
            generate_thumbnails = config.get("generate_thumbnails", True)

            # Create output directory
            output_dir = generator._create_output_directory(
                generator._generate_batch_id()
                if not hasattr(task, "job_id")
                else f"batch_{task.job_id}"
            )

            request = BatchImageRequest(
                batch_id=f"task_{task.id}_{task.job_id or 'sync'}",
                prompts=batch_prompts,
                output_dir=str(output_dir),
                max_workers=max_workers,
                generate_thumbnails=generate_thumbnails,
                content_preset=content_preset,
                auto_enhance=auto_enhance,
                enhance_anatomy=enhance_anatomy,
                enhance_faces=enhance_faces,
                enhance_hands=enhance_hands,
            )

            # Start batch generation (this runs in a background thread)
            batch_id = generator.start_batch_generation(request)

            progress_callback(
                10,
                f"Batch {batch_id} started",
                {"batch_id": batch_id, "output_dir": str(output_dir)},
            )

            # Poll for completion with progress updates
            completed_images = 0
            failed_images = 0
            total_images = len(batch_prompts)

            import time

            max_wait_seconds = 3600  # 1 hour max
            poll_interval = 2  # Check every 2 seconds
            waited = 0

            while waited < max_wait_seconds:
                status = generator.get_batch_status(batch_id)

                if status is None:
                    return TaskResult(
                        status=TaskResultStatus.FAILED,
                        message=f"Lost track of batch {batch_id}",
                        error_message="Batch status not found",
                        started_at=started_at,
                        completed_at=datetime.now(),
                    )

                completed_images = status.completed_images
                failed_images = status.failed_images

                # Calculate progress (10-95% range for generation)
                if total_images > 0:
                    progress = int(
                        10 + (completed_images + failed_images) / total_images * 85
                    )
                else:
                    progress = 10

                progress_callback(
                    progress,
                    f"Generated {completed_images}/{total_images} images",
                    {
                        "completed_images": completed_images,
                        "failed_images": failed_images,
                        "total_images": total_images,
                        "batch_id": batch_id,
                    },
                )

                if status.status in ("completed", "error", "cancelled"):
                    break

                time.sleep(poll_interval)
                waited += poll_interval

            # Get final status
            final_status = generator.get_batch_status(batch_id)
            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()

            if final_status is None:
                return TaskResult(
                    status=TaskResultStatus.FAILED,
                    message="Batch generation lost",
                    error_message="Could not retrieve final batch status",
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration,
                )

            progress_callback(
                100,
                "Batch generation complete",
                {
                    "completed_images": final_status.completed_images,
                    "failed_images": final_status.failed_images,
                },
            )

            # Collect output files
            output_files = []
            if final_status.results:
                for result in final_status.results:
                    if result.success and result.image_path:
                        output_files.append(result.image_path)

            # Determine final status
            if final_status.status == "error" or final_status.completed_images == 0:
                return TaskResult(
                    status=TaskResultStatus.FAILED,
                    message=f"Batch generation failed: {final_status.error or 'Unknown error'}",
                    error_message=final_status.error,
                    items_processed=final_status.completed_images,
                    items_total=total_images,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration,
                )

            if final_status.failed_images > 0:
                return TaskResult(
                    status=TaskResultStatus.PARTIAL,
                    message=f"Generated {final_status.completed_images}/{total_images} images ({final_status.failed_images} failed)",
                    output_files=output_files,
                    output_data={
                        "batch_id": batch_id,
                        "output_dir": str(output_dir),
                        "completed_images": final_status.completed_images,
                        "failed_images": final_status.failed_images,
                    },
                    items_processed=final_status.completed_images,
                    items_total=total_images,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration,
                )

            return TaskResult(
                status=TaskResultStatus.SUCCESS,
                message=f"Successfully generated {final_status.completed_images} images",
                output_files=output_files,
                output_data={
                    "batch_id": batch_id,
                    "output_dir": str(output_dir),
                    "completed_images": final_status.completed_images,
                },
                items_processed=final_status.completed_images,
                items_total=total_images,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
            )

        except ImportError as e:
            logger.error(f"Batch image generator import failed: {e}")
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message="Batch image generator not available",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
            )
        except Exception as e:
            logger.error(f"Batch image handler error: {e}", exc_info=True)
            return TaskResult(
                status=TaskResultStatus.FAILED,
                message=f"Batch image generation failed: {str(e)}",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
            )

    def get_estimated_duration(self, config: Dict[str, Any]) -> Optional[int]:
        """Estimate based on prompt count and image size"""
        prompts = config.get("prompts", [])
        if not prompts:
            return None

        # Base time per image (seconds)
        base_time = 30

        # Adjust for image size if specified
        for p in prompts:
            if isinstance(p, dict):
                width = p.get("width", 512)
                height = p.get("height", 512)
                if width > 512 or height > 512:
                    base_time = 60  # Larger images take longer
                    break

        return len(prompts) * base_time

    def can_retry(self, task: Any, error: Exception) -> bool:
        """Retry on transient GPU errors"""
        error_msg = str(error).lower()
        # Don't retry on CUDA out of memory - need manual intervention
        if "cuda" in error_msg and "memory" in error_msg:
            return False
        return super().can_retry(task, error)
