"""
Batch Video Generator Service.

Provides batch orchestration for video generation tasks using the
OfflineVideoGenerator. Supports text-to-video and image-to-video
workflows, with frame-by-frame generation for memory-constrained
environments.
"""

import json
import logging
import subprocess
import threading
import uuid
import shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from backend.services.video_generation_router import (
    VideoGenerationRequest,
    VideoGenerationResult,
    get_video_generator,
)
from backend.services.gpu_resource_coordinator import get_gpu_coordinator

logger = logging.getLogger(__name__)

# Dedicated video generation log file
_video_log_handler = None


def _get_video_logger():
    global _video_log_handler
    if _video_log_handler is None:
        try:
            from backend.config import LOG_DIR

            log_path = Path(LOG_DIR) / "video_generation.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            _video_log_handler = logging.FileHandler(str(log_path))
            _video_log_handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            )
            logger.addHandler(_video_log_handler)
        except Exception:
            pass
    return logger


@dataclass
class BatchVideoItem:
    id: str
    prompt: Optional[str] = None
    image_path: Optional[str] = None
    metadata: Dict = field(default_factory=dict)


@dataclass
class BatchVideoRequest:
    batch_id: str
    items: List[BatchVideoItem]
    output_dir: str
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
    interpolation_multiplier: int = 2
    prompt_style: str = "cinematic"
    enhance_prompt: bool = True
    metadata: Dict = field(default_factory=dict)


@dataclass
class BatchVideoResult:
    item_id: str
    success: bool
    video_path: Optional[str] = None
    frame_paths: List[str] = field(default_factory=list)
    thumbnail_path: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict = field(default_factory=dict)


@dataclass
class BatchVideoStatus:
    batch_id: str
    status: str  # "pending", "running", "completed", "error", "cancelled"
    total_videos: int
    completed_videos: int = 0
    failed_videos: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    results: List[BatchVideoResult] = field(default_factory=list)
    error: Optional[str] = None
    output_dir: Optional[str] = None
    metadata: Dict = field(default_factory=dict)


class BatchVideoGenerator:
    """Service for generating multiple videos in batch with basic progress tracking."""

    def __init__(self):
        project_root = Path(__file__).parent.parent.parent
        self.base_output_dir = project_root / "data" / "outputs" / "batch_videos"
        self.base_output_dir.mkdir(parents=True, exist_ok=True)

        self.active_batches: Dict[str, BatchVideoStatus] = {}
        self.batch_lock = threading.Lock()

        self.video_generator = get_video_generator()
        self.service_available = self.video_generator.service_available
        _get_video_logger()  # Initialize dedicated log file
        logger.info(
            f"BatchVideoGenerator initialized - Service available: {self.service_available}"
        )

    @staticmethod
    def _extract_thumbnail(video_path: Path, thumbnail_path: Path) -> bool:
        """Extract the first frame from a video as a JPEG thumbnail using ffmpeg."""
        try:
            thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "ffmpeg",
                    "-i",
                    str(video_path),
                    "-vf",
                    "select=eq(n\\,0)",
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    "-y",
                    str(thumbnail_path),
                ],
                capture_output=True,
                timeout=30,
            )
            if thumbnail_path.exists() and thumbnail_path.stat().st_size > 0:
                logger.info(f"Extracted thumbnail: {thumbnail_path}")
                return True
            return False
        except Exception as e:
            logger.warning(f"Failed to extract thumbnail: {e}")
            return False

    def _get_batch_dir(self, batch_id: str) -> Path:
        return self.base_output_dir / batch_id

    def _save_metadata(self, batch_status: BatchVideoStatus) -> None:
        try:
            batch_dir = Path(
                batch_status.output_dir or self._get_batch_dir(batch_status.batch_id)
            )
            batch_dir.mkdir(parents=True, exist_ok=True)
            metadata_file = batch_dir / "batch_metadata.json"
            serializable = asdict(batch_status)
            # Convert datetime to isoformat
            if batch_status.start_time:
                serializable["start_time"] = batch_status.start_time.isoformat()
            if batch_status.end_time:
                serializable["end_time"] = batch_status.end_time.isoformat()
            with open(metadata_file, "w") as f:
                json.dump(serializable, f, indent=2)
        except Exception as e:  # pragma: no cover - best effort
            logger.warning(f"Failed to save batch metadata: {e}")

    def _run_batch(
        self, batch_request: BatchVideoRequest, status: BatchVideoStatus
    ) -> None:
        # Acquire GPU lock before starting video generation
        gpu_coordinator = get_gpu_coordinator()
        lock_result = gpu_coordinator.acquire_for_video_generation(
            batch_id=batch_request.batch_id, lease_seconds=3600  # 1 hour max
        )

        if not lock_result.get("success"):
            status.status = "error"
            status.error = f"Could not acquire GPU: {lock_result.get('error')}"
            status.end_time = datetime.now()
            self._save_metadata(status)
            logger.error(
                f"Batch {batch_request.batch_id} failed to acquire GPU lock: {lock_result.get('error')}"
            )
            return

        try:
            status.start_time = datetime.now()
            status.status = "running"
            self._save_metadata(status)

            batch_dir = Path(batch_request.output_dir)
            batch_dir.mkdir(parents=True, exist_ok=True)

            for item in batch_request.items:
                if status.status == "cancelled":
                    break

                try:
                    meta = dict(item.metadata or {})
                    meta.setdefault("item_id", item.id)
                    # Mark as batch-controlled so individual generate_video doesn't acquire its own lock
                    meta["batch_controlled"] = True
                    if item.image_path:
                        meta.setdefault("image_path", item.image_path)

                    gen_request = VideoGenerationRequest(
                        prompt=item.prompt or "",
                        model=batch_request.model,
                        duration_frames=batch_request.duration_frames,
                        fps=batch_request.fps,
                        width=batch_request.width,
                        height=batch_request.height,
                        motion_strength=batch_request.motion_strength,
                        num_inference_steps=batch_request.num_inference_steps,
                        guidance_scale=batch_request.guidance_scale,
                        seed=batch_request.seed,
                        generate_frames_only=batch_request.generate_frames_only,
                        frames_per_batch=batch_request.frames_per_batch,
                        combine_frames=batch_request.combine_frames,
                        output_dir=batch_dir,
                        metadata=meta,
                        interpolation_multiplier=batch_request.interpolation_multiplier,
                        prompt_style=batch_request.prompt_style,
                        enhance_prompt=batch_request.enhance_prompt,
                    )

                    result: VideoGenerationResult = self.video_generator.generate_video(
                        gen_request
                    )
                    batch_result = BatchVideoResult(
                        item_id=item.id,
                        success=result.success,
                        video_path=result.video_path,
                        frame_paths=result.frame_paths,
                        thumbnail_path=result.thumbnail_path,
                        error=result.error,
                        metadata=result.metadata,
                    )
                    status.results.append(batch_result)
                    if result.success:
                        status.completed_videos += 1
                    else:
                        status.failed_videos += 1
                except Exception as e:  # pragma: no cover - runtime safety
                    logger.error(f"Error generating video for item {item.id}: {e}")
                    status.failed_videos += 1
                    status.results.append(
                        BatchVideoResult(
                            item_id=item.id,
                            success=False,
                            error=str(e),
                        )
                    )
                finally:
                    self._save_metadata(status)

            status.status = "completed" if status.failed_videos == 0 else "error"
            status.end_time = datetime.now()
            self._save_metadata(status)

        finally:
            # Always release GPU lock when batch completes (success or failure)
            gpu_coordinator.release_video_generation_lock(restart_ollama=True)
            logger.info(f"Batch {batch_request.batch_id} released GPU lock")

    def start_batch_from_prompts(
        self,
        prompts: List[str],
        **params,
    ) -> BatchVideoStatus:
        batch_id = (
            params.get("batch_id")
            or f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        )
        items = [
            BatchVideoItem(
                id=str(uuid.uuid4()), prompt=p, metadata={"source": "prompt"}
            )
            for p in prompts
        ]
        return self._start_batch(batch_id=batch_id, items=items, **params)

    def start_batch_from_images(
        self,
        image_paths: List[str],
        **params,
    ) -> BatchVideoStatus:
        batch_id = (
            params.get("batch_id")
            or f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        )
        user_prompt = params.pop("prompt", "")
        items = [
            BatchVideoItem(
                id=str(uuid.uuid4()),
                prompt=user_prompt or f"Image-to-video: {Path(path).name}",
                image_path=path,
                metadata={"source": "image", "image_path": path},
            )
            for path in image_paths
        ]
        return self._start_batch(batch_id=batch_id, items=items, **params)

    def _start_batch(
        self, batch_id: str, items: List[BatchVideoItem], **params
    ) -> BatchVideoStatus:
        batch_dir = self._get_batch_dir(batch_id)
        batch_dir.mkdir(parents=True, exist_ok=True)

        seed_param = params.get("seed")
        seed_value = None
        if seed_param not in (None, ""):
            try:
                seed_value = int(seed_param)
            except Exception:
                seed_value = None

        batch_request = BatchVideoRequest(
            batch_id=batch_id,
            items=items,
            output_dir=str(batch_dir),
            model=params.get("model", "svd"),
            duration_frames=int(params.get("duration_frames", 25)),
            fps=int(params.get("fps", 7)),
            width=int(params.get("width", 512)),
            height=int(params.get("height", 512)),
            motion_strength=float(params.get("motion_strength", 1.0)),
            num_inference_steps=int(params.get("num_inference_steps", 25)),
            guidance_scale=float(params.get("guidance_scale", 7.5)),
            seed=seed_value,
            generate_frames_only=bool(params.get("generate_frames_only", False)),
            frames_per_batch=int(params.get("frames_per_batch", 1)),
            combine_frames=bool(params.get("combine_frames", False)),
            interpolation_multiplier=int(params.get("interpolation_multiplier", 2)),
            prompt_style=params.get("prompt_style", "cinematic"),
            enhance_prompt=bool(params.get("enhance_prompt", True)),
            metadata=params.get("metadata", {}),
        )

        status = BatchVideoStatus(
            batch_id=batch_id,
            status="pending",
            total_videos=len(items),
            output_dir=str(batch_dir),
            metadata=params.get("metadata", {}),
        )

        with self.batch_lock:
            self.active_batches[batch_id] = status

        # Launch background thread
        t = threading.Thread(
            target=self._run_batch, args=(batch_request, status), daemon=True
        )
        t.start()

        return status

    def get_batch_status(self, batch_id: str) -> Optional[BatchVideoStatus]:
        with self.batch_lock:
            status = self.active_batches.get(batch_id)
        if status:
            return status

        # Try to load from disk
        batch_dir = self._get_batch_dir(batch_id)
        metadata_file = batch_dir / "batch_metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, "r") as f:
                    data = json.load(f)
                results = [BatchVideoResult(**res) for res in data.get("results", [])]
                # Retroactively extract thumbnails for results that have videos but no thumbnail
                metadata_changed = False
                for res in results:
                    if res.video_path and not res.thumbnail_path:
                        video_file = batch_dir / res.video_path
                        if video_file.exists() and video_file.suffix.lower() in (
                            ".mp4",
                            ".webm",
                            ".avi",
                            ".mov",
                        ):
                            thumb_filename = video_file.stem + "_thumb.jpg"
                            # Place thumbnail in a thumbnails subdir next to the video
                            thumbs_dir = video_file.parent.parent / "thumbnails"
                            thumb_path = thumbs_dir / thumb_filename
                            if self._extract_thumbnail(video_file, thumb_path):
                                res.thumbnail_path = str(
                                    thumb_path.relative_to(batch_dir)
                                )
                                metadata_changed = True
                if metadata_changed:
                    # Persist the updated thumbnail paths back to metadata
                    try:
                        for i, res in enumerate(results):
                            if res.thumbnail_path and i < len(data.get("results", [])):
                                data["results"][i][
                                    "thumbnail_path"
                                ] = res.thumbnail_path
                        with open(metadata_file, "w") as f:
                            json.dump(data, f, indent=2)
                    except Exception:
                        pass  # Best effort

                start_time = (
                    datetime.fromisoformat(data["start_time"])
                    if data.get("start_time")
                    else None
                )
                end_time = (
                    datetime.fromisoformat(data["end_time"])
                    if data.get("end_time")
                    else None
                )
                return BatchVideoStatus(
                    batch_id=data["batch_id"],
                    status=data.get("status", "completed"),
                    total_videos=data.get("total_videos", len(results)),
                    completed_videos=data.get("completed_videos", 0),
                    failed_videos=data.get("failed_videos", 0),
                    start_time=start_time,
                    end_time=end_time,
                    results=results,
                    error=data.get("error"),
                    output_dir=data.get("output_dir"),
                    metadata=data.get("metadata", {}),
                )
            except Exception as e:  # pragma: no cover
                logger.error(f"Failed to load batch status for {batch_id}: {e}")
                return None
        return None

    def list_batches(self) -> List[Dict]:
        batches = []
        try:
            for batch_dir in self.base_output_dir.iterdir():
                if not batch_dir.is_dir():
                    continue
                metadata_file = batch_dir / "batch_metadata.json"
                batch_id = batch_dir.name
                entry = {"batch_id": batch_id, "status": "unknown"}
                if metadata_file.exists():
                    try:
                        with open(metadata_file, "r") as f:
                            data = json.load(f)
                        entry.update(
                            {
                                "status": data.get("status", "unknown"),
                                "total_videos": data.get("total_videos", 0),
                                "completed_videos": data.get("completed_videos", 0),
                                "failed_videos": data.get("failed_videos", 0),
                                "start_time": data.get("start_time"),
                                "end_time": data.get("end_time"),
                                "display_name": data.get("metadata", {}).get(
                                    "display_name"
                                ),
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Failed to read metadata for {batch_id}: {e}")
                batches.append(entry)
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to list video batches: {e}")
        return batches

    def delete_batch(self, batch_id: str) -> bool:
        batch_dir = self._get_batch_dir(batch_id)
        if not batch_dir.exists():
            return False
        try:
            shutil.rmtree(batch_dir)
            with self.batch_lock:
                self.active_batches.pop(batch_id, None)
            return True
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to delete batch {batch_id}: {e}")
            return False

    def rename_batch(self, batch_id: str, new_name: str) -> bool:
        batch_dir = self._get_batch_dir(batch_id)
        if not batch_dir.exists():
            return False
        metadata_file = batch_dir / "batch_metadata.json"
        try:
            if metadata_file.exists():
                with open(metadata_file, "r") as f:
                    data = json.load(f)
                data.setdefault("metadata", {})["display_name"] = new_name
                with open(metadata_file, "w") as f:
                    json.dump(data, f, indent=2)
            return True
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to rename batch {batch_id}: {e}")
            return False

    def get_preview_thumbnail(self, batch_id: str) -> Optional[Path]:
        batch_dir = self._get_batch_dir(batch_id)
        if batch_dir.exists():
            thumbs = sorted(batch_dir.glob("**/thumbnails/*.jpg"))
            if thumbs:
                return thumbs[0]
        return None

    def combine_frames(
        self, batch_id: str, item_id: Optional[str] = None, fps: int = 7
    ) -> Optional[str]:
        batch_dir = self._get_batch_dir(batch_id)
        if not batch_dir.exists():
            return None

        # Determine target item directory
        item_dir: Optional[Path] = None
        if item_id:
            candidate = batch_dir / item_id
            if candidate.exists():
                item_dir = candidate
        else:
            # Best-effort fallback: use the first item frames directory
            candidates = sorted(batch_dir.glob("*/frames"))
            if candidates:
                item_dir = candidates[0].parent

        if not item_dir:
            return None

        frames_dir = item_dir / "frames"
        videos_dir = item_dir / "videos"
        if not frames_dir.exists():
            return None
        videos_dir.mkdir(parents=True, exist_ok=True)

        video_path = videos_dir / f"video_{uuid.uuid4().hex}.mp4"
        combined = self.video_generator._combine_frames_to_video(
            frames_dir, video_path, fps
        )
        if not combined:
            return None

        rel_path = str(Path(combined).relative_to(batch_dir))

        # Update in-memory status if present
        with self.batch_lock:
            status = self.active_batches.get(batch_id)
            if status:
                for res in status.results:
                    if res.item_id == item_dir.name:
                        res.video_path = rel_path
                        res.success = res.success or bool(res.frame_paths)
                self._save_metadata(status)
                return rel_path

        # Update persisted metadata if batch not active
        metadata_file = batch_dir / "batch_metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, "r") as f:
                    data = json.load(f)
                for res in data.get("results", []):
                    if res.get("item_id") == item_dir.name:
                        res["video_path"] = rel_path
                with open(metadata_file, "w") as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to update metadata after combining frames: {e}")

        return rel_path


_batch_video_generator_instance: Optional[BatchVideoGenerator] = None


def get_batch_video_generator() -> BatchVideoGenerator:
    global _batch_video_generator_instance
    if _batch_video_generator_instance is None:
        _batch_video_generator_instance = BatchVideoGenerator()
    return _batch_video_generator_instance
