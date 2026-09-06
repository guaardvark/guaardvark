"""
Batch Video Generator Service.

Provides batch orchestration for video generation tasks using the
OfflineVideoGenerator. Supports text-to-video and image-to-video
workflows, with frame-by-frame generation for memory-constrained
environments.
"""

import json
import logging
import os
import queue
import subprocess
import threading
import time
import uuid
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from backend.utils.path_guard import PathEscapesRoot, contained

try:
    from backend.config import UPLOAD_DIR
except ImportError:
    UPLOAD_DIR = "/tmp/guaardvark_uploads"

logger = logging.getLogger(__name__)


_I2V_CAPTION_PROMPT = (
    "Describe this image for a video generator in 1-2 sentences: the subject's "
    "identity or species, costume/clothing with colors and materials, pose, "
    "setting, and lighting. Describe exactly what is shown — do not invent "
    "details that are not visible."
)


def _caption_image_for_i2v(image_path: str) -> str:
    """VLM caption of an I2V source image, or "" when the VLM is unavailable.

    Uses the shared offline VisionAnalyzer (same model as character_captioner /
    film_curator). Never raises — I2V must proceed with a generic prompt rather
    than fail the item over captioning."""
    try:
        from PIL import Image
        from backend.utils.vision_analyzer import VisionAnalyzer
        img = Image.open(str(image_path)).convert("RGB")
        res = VisionAnalyzer().analyze(img, _I2V_CAPTION_PROMPT, think=False)
        if getattr(res, "success", False) and (getattr(res, "description", "") or "").strip():
            return res.description.strip()
        logger.warning("I2V auto-caption: VLM gave no description for %s", image_path)
    except Exception as e:
        logger.warning("I2V auto-caption failed for %s: %s", image_path, e)
    return ""


def _derive_display_name(text: str, max_len: int = 40) -> str:
    """Trim a prompt down to something the Media Library card can show."""
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"

# Dedicated video generation log file
_video_log_handler = None
def _get_video_logger():
    global _video_log_handler
    if _video_log_handler is None:
        try:
            from backend.config import LOG_DIR
            log_path = Path(LOG_DIR) / "video_generation.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            # Avoid duplicate lines when BatchVideoGenerator is constructed more
            # than once (or when both this module and comfy already share a handler).
            target = str(log_path.resolve())

            def _has_video_file_handler(lg: logging.Logger) -> bool:
                for h in lg.handlers:
                    if isinstance(h, logging.FileHandler):
                        try:
                            if str(Path(getattr(h, "baseFilename", "")).resolve()) == target:
                                return True
                        except Exception:
                            continue
                return False

            _video_log_handler = logging.FileHandler(str(log_path))
            _video_log_handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] %(message)s"
            ))
            if not _has_video_file_handler(logger):
                logger.addHandler(_video_log_handler)
            logger.setLevel(logging.INFO)

            try:
                comfy_log = logging.getLogger("backend.services.comfyui_video_generator")
                if not _has_video_file_handler(comfy_log):
                    comfy_log.addHandler(_video_log_handler)
                comfy_log.setLevel(logging.INFO)
            except Exception:
                pass
        except Exception:
            pass
    return logger


@dataclass
class BatchVideoItem:
    id: str
    prompt: Optional[str] = None
    image_path: Optional[str] = None
    # End frame for models that declare l2v / flf2v (MiniMax H3).
    last_frame_path: Optional[str] = None
    # Anchors for models that declare audio_in: [{"kind", "path", "frame_idx", ...}].
    guides: List[Dict] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)


@dataclass
class BatchVideoRequest:
    batch_id: str
    items: List[BatchVideoItem]
    output_dir: str
    model: str = "wan22-5b"  # 16GB-native single model (A14B offloads → ~38 min/clip)
    duration_frames: int = 49  # 2s @ 24fps (Wan 5B is 24fps-native; was 25 @ 7fps = choppy); 49 is also CogVideoX's max
    fps: int = 24
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
    fidelity_mode: bool = False  # "Exact text / preserve fidelity" — light enhancement only
    wan_sampler_profile: Optional[str] = None  # Wan 5B: "adaptive" | "official"
    negative_prompt: str = ""
    freeu: bool = False
    face_restore: bool = False
    lora_name: Optional[str] = None
    lora_strength: float = 1.0
    # Capability-contract knobs (video_model_registry): a declared speed profile
    # (turbo LoRA + step count) and a declared style embedding id.
    speed_profile: Optional[str] = None
    style_embedding: Optional[str] = None
    # Cast members (trained character LoRAs) to lock into each clip. Resolved once
    # per batch via cast_lock.subjects_to_lock; the LoRA is baked into the cinematic
    # keyframe (the video model can't apply it). Selecting cast implies cinematic.
    subject_ids: List[int] = field(default_factory=list)
    # Quality pipeline (v2.6.2 — ported from the music-video generator). All opt-in;
    # defaults preserve the existing fast single-pass text-to-video behavior.
    director_mode: bool = False           # rewrite each prompt via the Video Director (cinematic)
    cinematic_keyframe: bool = False      # FLUX still -> Wan2.2 I2V per clip (forces serial render)
    director_guidance: Optional[str] = None  # optional free-text steer for the director
    storyboard_concept: Optional[str] = None  # expand ONE concept into len(items) connected shots
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
    status: str  # "pending", "queued", "running", "completed", "error", "cancelled"
    total_videos: int
    completed_videos: int = 0
    failed_videos: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    results: List[BatchVideoResult] = field(default_factory=list)
    error: Optional[str] = None
    output_dir: Optional[str] = None
    metadata: Dict = field(default_factory=dict)
    retry_data: Optional[Dict] = None  # persisted original prompts/image_paths + params for one-click retry on failed batches
    # Pipeline progress (Phase 1): stage-level signal for UI when WS is flaky.
    # Stages: queued | gpu_wait | storyboard | director | keyframe | generate | post | register | done
    stage: str = "queued"
    current_item: Optional[str] = None
    progress_pct: Optional[int] = None


class BatchVideoGenerator:
    """Service for generating multiple videos in batch with basic progress tracking."""

    def __init__(self):
        # Videos land directly in data/uploads/Videos/ so DocumentsPage sees them
        self.base_output_dir = Path(UPLOAD_DIR) / "Videos"
        self.base_output_dir.mkdir(parents=True, exist_ok=True)

        self.active_batches: Dict[str, BatchVideoStatus] = {}
        self.batch_lock = threading.Lock()

        # Queue plumbing — one batch runs at a time, the rest stack up.
        self.batch_queue: "queue.Queue[tuple]" = queue.Queue()
        self.cancel_events: Dict[str, threading.Event] = {}
        self.queue_order: List[str] = []  # batch_ids in submission order, oldest first
        self._running_batch_id: Optional[str] = None

        self.video_generator = get_video_generator()
        self.service_available = getattr(self.video_generator, 'service_available', True)
        # Edge graceful: on no-GPU, batch video (which uses offline or Comfy) will inherit unavailable with reason from underlying generator.
        _get_video_logger()  # Initialize dedicated log file

        # Single daemon worker drains the queue. GPU coordinator stays as
        # defense-in-depth inside _run_batch.
        self._worker_thread = threading.Thread(
            target=self._queue_worker, daemon=True, name="batch-video-worker"
        )
        self._worker_thread.start()

        # Only the API process resumes on-disk batches. Celery, the MCP stdio
        # server and ad-hoc scripts construct this class too; if they restored,
        # every restart would render the same batch from two PIDs into one dir.
        if self._restore_allowed():
            self._restore_pending_batches()
        else:
            logger.info("BatchVideoGenerator: restore-on-start skipped in this process")

        logger.info(f"BatchVideoGenerator initialized - Service available: {self.service_available}")

    @staticmethod
    def _restore_allowed() -> bool:
        if os.environ.get("GUAARDVARK_VIDEO_RESTORE_ON_START", "1") != "1":
            return False
        if os.environ.get("CELERY_WORKER_MODE", "").lower() == "true":
            return False
        if os.environ.get("GUAARDVARK_MCP_PROCESS") == "1":
            return False
        return True

    def _restore_pending_batches(self) -> None:
        """Re-enqueue batches left queued/running on disk after a process restart."""
        restored = 0
        try:
            for batch_dir in self.base_output_dir.iterdir():
                if not batch_dir.is_dir():
                    continue
                metadata_file = batch_dir / "batch_metadata.json"
                if not metadata_file.exists():
                    continue
                try:
                    with open(metadata_file, "r") as f:
                        data = json.load(f)
                except Exception:
                    continue

                status = data.get("status")
                if status not in self._ACTIVE_STATUSES:
                    continue

                batch_id = data.get("batch_id") or batch_dir.name
                with self.batch_lock:
                    if batch_id in self.active_batches:
                        continue

                rd = data.get("retry_data")
                if not rd:
                    logger.warning("Cannot restore batch %s: no retry_data", batch_id)
                    if status in ("running", "processing"):
                        data["status"] = "error"
                        data["error"] = "Interrupted by server restart (no retry_data to resume)"
                        data["end_time"] = datetime.now().isoformat()
                        try:
                            with open(metadata_file, "w") as f:
                                json.dump(data, f, indent=2)
                        except Exception:
                            pass
                    continue

                params = dict(rd.get("params") or {})
                params["batch_id"] = batch_id
                mode = rd.get("mode")
                try:
                    if mode == "text":
                        prompts = rd.get("prompts") or []
                        if not prompts:
                            continue
                        self.start_batch_from_prompts(prompts=prompts, **params)
                    elif mode == "image":
                        image_paths = rd.get("image_paths") or []
                        if not image_paths:
                            continue
                        prompt = rd.get("prompt", "") or ""
                        if prompt:
                            params["prompt"] = prompt
                        self.start_batch_from_images(image_paths=image_paths, **params)
                    else:
                        logger.warning("Cannot restore batch %s: unknown mode %s", batch_id, mode)
                        continue
                    restored += 1
                except Exception as e:
                    logger.warning("Failed to restore batch %s: %s", batch_id, e)
        except Exception as e:
            logger.warning("Failed to scan for pending batches: %s", e)

        if restored:
            logger.info("Restored %d pending video batch(es) from disk", restored)

    def _queue_worker(self) -> None:
        """Pulls one batch off the queue at a time. Bouncer at the GPU door."""
        while True:
            try:
                batch_request, status = self.batch_queue.get()
            except Exception as e:
                logger.error(f"Queue worker get() failed: {e}")
                continue

            try:
                cancel_event = self.cancel_events.get(batch_request.batch_id)
                if cancel_event and cancel_event.is_set():
                    status.status = "cancelled"
                    status.end_time = datetime.now()
                    if not status.error:
                        status.error = "Cancelled before start"
                    self._save_metadata(status)
                    logger.info(f"Skipped cancelled batch {batch_request.batch_id}")
                    continue

                self._running_batch_id = batch_request.batch_id
                self._run_batch(batch_request, status)
            except Exception as e:
                logger.error(f"Queue worker crashed on batch {batch_request.batch_id}: {e}")
                status.status = "error"
                status.error = str(e)
                status.end_time = datetime.now()
                self._save_metadata(status)
            finally:
                self._running_batch_id = None
                with self.batch_lock:
                    if batch_request.batch_id in self.queue_order:
                        # Keep completed batches in queue_order for /queue snapshot
                        # so the UI can show recent history. _cleanup_stale_batches
                        # already trims old data.
                        pass

    @staticmethod
    def _extract_thumbnail(video_path: Path, thumbnail_path: Path) -> bool:
        """Extract the first frame from a video as a JPEG thumbnail using ffmpeg."""
        try:
            thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
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
            return False
        except Exception as e:
            logger.warning(f"Failed to extract thumbnail: {e}")
            return False

    def _get_batch_dir(self, batch_id: str) -> Path:
        return contained(self.base_output_dir, batch_id)

    def _attach_quality_metrics(
        self,
        batch_result: BatchVideoResult,
        *,
        video_path: str,
        keyframe_path: Optional[str] = None,
        cinematic: bool = False,
        high_consistency: bool = False,
    ) -> None:
        """Best-effort quality sidecar for completed clips (never raises / never fails the item)."""
        try:
            from backend.services.video_consistency_metrics import (
                compute_basic_video_stats,
                score_identity_preservation,
                review_video_quality,
                annotate_asset,
            )
        except Exception as e:
            logger.debug("quality metrics import failed: %s", e)
            return

        quality: Dict = {"flagged": False, "flag_reasons": []}
        try:
            quality["stats"] = compute_basic_video_stats(video_path)
        except Exception as e:
            logger.debug("basic video stats failed: %s", e)

        if cinematic and keyframe_path and Path(keyframe_path).exists():
            try:
                # Sample a mid-frame via identity score against the keyframe still.
                # score_identity_preservation expects image refs; extract one frame.
                import subprocess
                import tempfile
                with tempfile.TemporaryDirectory() as td:
                    frame_path = str(Path(td) / "mid.jpg")
                    subprocess.run(
                        [
                            "ffmpeg", "-y", "-loglevel", "error",
                            "-ss", "0.5", "-i", str(video_path),
                            "-frames:v", "1", frame_path,
                        ],
                        capture_output=True,
                        timeout=30,
                    )
                    if Path(frame_path).exists():
                        identity = score_identity_preservation(
                            [keyframe_path], frame_path, method="hist"
                        )
                        quality["identity"] = identity
                        score = float(identity.get("score") or 0)
                        if score < 0.5:
                            quality["flagged"] = True
                            quality["flag_reasons"].append(
                                f"low_identity_score:{score:.2f}"
                            )
            except Exception as e:
                logger.debug("identity scoring skipped: %s", e)

        # Optional VLM review for high-consistency / cinematic runs (fail-open).
        if high_consistency or cinematic:
            try:
                review = review_video_quality(video_path, annotate=False)
                quality["vlm_review"] = review
                if review.get("available"):
                    qscore = (review.get("review") or {}).get("quality_score")
                    if isinstance(qscore, (int, float)) and qscore < 5:
                        quality["flagged"] = True
                        quality["flag_reasons"].append(f"low_vlm_score:{qscore}")
            except Exception as e:
                logger.debug("VLM video review skipped: %s", e)

        batch_result.metadata = dict(batch_result.metadata or {})
        batch_result.metadata["quality"] = quality
        try:
            annotate_asset(video_path, {"quality": quality})
        except Exception:
            pass

    @staticmethod
    def _compute_progress_pct(batch_status: BatchVideoStatus) -> int:
        total = max(1, int(batch_status.total_videos or 1))
        done = int(batch_status.completed_videos or 0) + int(batch_status.failed_videos or 0)
        pct = int(round(100.0 * done / total))
        # Stage hints while an item is in flight (before completed_videos bumps).
        stage = (batch_status.stage or "").lower()
        if stage == "gpu_wait":
            return min(pct, 2)
        if stage in ("storyboard", "director"):
            return max(pct, 5)
        if stage == "keyframe":
            return max(pct, min(95, pct + 5))
        if stage in ("generate", "post"):
            return max(pct, min(99, pct + 1))
        if stage == "register":
            return max(pct, 98)
        if stage == "done" or batch_status.status in ("completed", "error", "cancelled"):
            return 100 if batch_status.status == "completed" else min(100, max(pct, 1))
        return min(100, max(0, pct))

    def _set_stage(
        self,
        batch_status: BatchVideoStatus,
        stage: str,
        *,
        current_item: Optional[str] = None,
        save: bool = True,
    ) -> None:
        batch_status.stage = stage
        if current_item is not None:
            batch_status.current_item = current_item
        batch_status.progress_pct = self._compute_progress_pct(batch_status)
        if save:
            self._save_metadata(batch_status)

    def _save_metadata(self, batch_status: BatchVideoStatus) -> None:
        try:
            batch_dir = Path(batch_status.output_dir or self._get_batch_dir(batch_status.batch_id))
            batch_dir.mkdir(parents=True, exist_ok=True)
            metadata_file = batch_dir / "batch_metadata.json"
            batch_status.progress_pct = self._compute_progress_pct(batch_status)
            serializable = asdict(batch_status)
            # Convert datetime to isoformat
            if batch_status.start_time:
                serializable["start_time"] = batch_status.start_time.isoformat()
            if batch_status.end_time:
                serializable["end_time"] = batch_status.end_time.isoformat()
            with open(metadata_file, "w") as f:
                json.dump(serializable, f, indent=2)

            try:
                from backend.socketio_instance import socketio
                # The event name matches frontend expectations and uses the batch_id as the room
                socketio.emit("video_batch:update", serializable, room=batch_status.batch_id)
            except Exception as e:
                logger.debug(f"Failed to emit WebSocket update for batch {batch_status.batch_id}: {e}")

            self._emit_canonical_job_event(batch_status)

        except Exception as e:  # pragma: no cover - best effort
            logger.warning(f"Failed to save batch metadata: {e}")

    def _apply_director(self, batch_request: BatchVideoRequest) -> None:
        """Rewrite each text item's prompt via the Video Director (cinematic enrichment).

        Mutates ``batch_request.items[*].prompt`` in place and disables the lighter
        downstream enhancer (the director already produced a full shot prompt, so the
        generic boilerplate would just dilute it). Never raises — on any failure the
        original prompts stand and generation proceeds unchanged."""
        try:
            from backend.services.director_service import plan, DirectorBrief, DirectorMode
            text_items = [it for it in batch_request.items if (it.prompt or "").strip()]
            if not text_items:
                return
            style = (batch_request.metadata or {}).get("look_and_feel") or batch_request.prompt_style
            result = plan(DirectorBrief(
                mode=DirectorMode.PROMPT_LIST,
                prompts=[it.prompt for it in text_items],
                style=style,
                extra_guidance=getattr(batch_request, "director_guidance", None),
            ))
            # Map shots back to the per-item string list (count-preserving on success; empty
            # on a hard director failure → originals stand via the zip below).
            directed = [s.prompt for s in result.shots]
            changed = 0
            for it, new_prompt in zip(text_items, directed):
                if new_prompt and new_prompt.strip() and new_prompt.strip() != (it.prompt or "").strip():
                    it.prompt = new_prompt.strip()
                    changed += 1
            # Director output is already a complete cinematic prompt; don't double-enhance.
            # Only when it actually produced one: media_director hands the ORIGINALS back on
            # failure (and plan() hands back nothing), and switching the light enhancer off
            # on top of that shipped raw prompts with no enhancement at all.
            if changed:
                batch_request.enhance_prompt = False
                logger.info(
                    f"Video Director enhanced {changed}/{len(text_items)} prompt(s) for batch "
                    f"{batch_request.batch_id}"
                )
            else:
                logger.warning(
                    f"Director returned no rewrites for batch {batch_request.batch_id} "
                    f"({(result.diagnostics or {}).get('reason', 'originals returned')}); "
                    f"prompts and the light enhancer stand as they were."
                )
        except Exception as e:  # noqa: BLE001 — director must never fail a render
            logger.warning(f"Director pass skipped for batch {batch_request.batch_id} (non-fatal): {e}")

    def _apply_storyboard(self, batch_request: BatchVideoRequest) -> None:
        """Expand a single concept into one connected shot per item.

        Mutates each item's prompt in place with the Storyboard agent's output and turns
        off the lighter downstream enhancer (the shots are already full cinematic prompts).
        Never raises — on failure the placeholder prompts (the raw concept) stand."""
        try:
            from backend.services.director_service import plan, DirectorBrief, DirectorMode
            concept = (batch_request.storyboard_concept or "").strip()
            n = len(batch_request.items)
            if not concept or n == 0:
                return
            style = (batch_request.metadata or {}).get("look_and_feel") or batch_request.prompt_style
            result = plan(DirectorBrief(
                mode=DirectorMode.CONCEPT_EXPANSION,
                concept=concept, num_shots=n, style=style,
                extra_guidance=getattr(batch_request, "director_guidance", None),
            ))
            shots = [s.prompt for s in result.shots]
            changed = 0
            for it, shot in zip(batch_request.items, shots):
                if shot and shot.strip():
                    if shot.strip() != (it.prompt or "").strip():
                        changed += 1
                    it.prompt = shot.strip()
            # Same rule as the director pass: the fallback is "the concept, n times",
            # which is not a storyboard and must not switch the enhancer off.
            if changed:
                batch_request.enhance_prompt = False
                logger.info(
                    f"Storyboard expanded one concept into {len(shots)} shot(s) for batch "
                    f"{batch_request.batch_id}"
                )
            else:
                logger.warning(
                    f"Storyboard returned no shots beyond the concept for batch "
                    f"{batch_request.batch_id}; placeholder prompts and the light enhancer stand."
                )
        except Exception as e:  # noqa: BLE001 — storyboard must never fail a render
            logger.warning(f"Storyboard expansion skipped for batch {batch_request.batch_id} (non-fatal): {e}")

    @staticmethod
    def _to_i2v_model(model: Optional[str]) -> str:
        """The model that animates the cinematic keyframe.

        A model that takes a first frame itself (LTX, MiniMax H3, Wan 5B TI2V,
        any *-i2v) keeps the job; a pure T2V model hands it to its same-family
        I2V sibling. Only an unknown model falls back to Wan 2.2 14B I2V. The
        old rule swapped anything without "i2v" in its id for Wan 14B, so a
        person who picked LTX or MiniMax got a Wan render."""
        from backend.services.video_model_registry import i2v_model_for
        return i2v_model_for(model or "")

    def _generate_keyframe_still(self, *, prompt: str, width: int, height: int,
                                 out_path: str, seed: int,
                                 keyframe_model: Optional[str] = None,
                                 loras: Optional[list] = None,
                                 subject_ids: Optional[list] = None,
                                 lora_strength: float = 0.25,
                                 keep_warm: bool = False,
                                 training_resolution: Optional[int] = None,
                                 require_cast: bool = False) -> Optional[str]:
        """Cinematic mode: render a keyframe still, then free VRAM for I2V.

        Character LoRAs bake identity into THIS still (Wan/Cog cannot apply them).
        Prefer subject_ids so identity core + train base match Cast generate.
        """
        try:
            from backend.services.gpu_resource_policy import free_comfyui_vram
            import os
            _long = int(os.environ.get("GUAARDVARK_KEYFRAME_LONG", "1024"))
            if training_resolution:
                try:
                    _min_from_train = max(512, int(int(training_resolution) * 4 / 3))
                    _long = max(_long, _min_from_train)
                except Exception:
                    pass
            _long = max(512, (_long // 16) * 16)
            _ar = (float(width) / float(height)) if height else 1.0
            if width >= height:
                w, h = _long, int(round(_long / _ar))
            else:
                w, h = int(round(_long * _ar)), _long
            w = max(256, (w // 16) * 16)
            h = max(256, (h // 16) * 16)
            sids = [int(x) for x in (subject_ids or []) if str(x).strip()]
            lora_list = list(loras or [])
            has_cast = bool(sids or lora_list)
            if has_cast:
                from backend.services.character_still_pipeline import render_character_still
                # Raw scene prompt — pipeline applies identity core when subjects resolve.
                still_res = render_character_still(
                    prompt,
                    subject_ids=sids or None,
                    lora_paths=lora_list or None,
                    include_bible=True,  # vision bible pins costume for keyframes
                    source="video",
                    width=w,
                    height=h,
                    seed=int(seed),
                    output_path=out_path,
                    lora_strength=lora_strength,
                    keep_pipeline=keep_warm,
                )
                if not still_res.success:
                    logger.error(
                        "Cast keyframe still failed: %s (lock=%r family=%s)",
                        still_res.error,
                        (still_res.metadata or {}).get("lock_prefix"),
                        (still_res.metadata or {}).get("family"),
                    )
                    if require_cast:
                        raise RuntimeError(
                            still_res.error or "Cast keyframe still failed — refusing T2V fallback"
                        )
                    still = None
                else:
                    still = still_res.image_path
                    logger.info(
                        "Cast keyframe still ok: family=%s strength=%s lock=%r",
                        (still_res.metadata or {}).get("family"),
                        (still_res.metadata or {}).get("lora_strength"),
                        ((still_res.metadata or {}).get("lock_prefix") or "")[:100],
                    )
            else:
                from backend.services.comfyui_image_generator import ComfyUIImageGenerator
                model = keyframe_model or "flux-schnell"
                if str(model).lower() in ("from-lora", "from_lora", "auto"):
                    model = "flux-schnell"
                steps = 8 if "flux" in model.lower() else 30
                still = ComfyUIImageGenerator(lora_strength=lora_strength).generate_image(
                    prompt=prompt,
                    loras=None,
                    output_path=out_path,
                    width=w,
                    height=h,
                    seed=int(seed),
                    steps=steps,
                    model=model,
                )
            if not keep_warm:
                try:
                    free_comfyui_vram()
                except Exception:
                    pass
            return still if still and Path(still).exists() else None
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            if require_cast:
                raise
            logger.warning(
                f"Cinematic keyframe generation failed ({e}); falling back to text-to-video"
            )
            return None

    def _run_batch(self, batch_request: BatchVideoRequest, status: BatchVideoStatus) -> None:
        # Serial background queue: retry a busy/resident refusal with backoff
        # until the deadline; a capacity refusal is terminal. Eviction belongs to
        # gpu_session once it has won the slot — this loop never reclaims.
        from backend.services.gpu_resource_policy import gpu_session, vram_probe_snapshot
        from backend.services.job_operation_gate import GpuBusyError, GpuCapacityError
        from backend.services.job_types import JobKind
        from backend.services.video_model_registry import vram_mb_for_model

        slot_id = f"video_render:batch_{batch_request.batch_id}"
        vram_mb = vram_mb_for_model(batch_request.model)
        parallel_comfyui = os.environ.get(
            "GUAARDVARK_BATCH_COMFYUI_PARALLEL", ""
        ).lower() in ("1", "true", "yes")
        cancel_event = self.cancel_events.get(batch_request.batch_id)

        try:
            admit_deadline_s = float(
                os.environ.get("GUAARDVARK_VIDEO_VRAM_WAIT_S", "5400")  # 90 min
            )
        except ValueError:
            admit_deadline_s = 5400.0
        admit_deadline_s = max(60.0, admit_deadline_s)

        backoff_s = 2.0
        deadline = time.time() + admit_deadline_s
        need_mb = int(vram_mb) + 1024

        while True:
            if cancel_event and cancel_event.is_set():
                status.status = "cancelled"
                status.error = "Cancelled while waiting for GPU/VRAM"
                status.end_time = datetime.now()
                self._set_stage(status, "done", save=False)
                self._save_metadata(status)
                return

            snap = vram_probe_snapshot()
            wait_msg = (
                f"Waiting for VRAM — "
                f"{(snap.get('free_mb') or 0) / 1024:.1f}GB free, "
                f"need ~{need_mb / 1024:.1f}GB"
            )
            status.status = status.status if status.status in ("running", "queued", "pending") else "queued"
            if status.status == "pending":
                status.status = "queued"
            status.metadata = dict(status.metadata or {})
            status.metadata["gpu_wait_reason"] = wait_msg
            status.metadata["vram_free_mb"] = snap.get("free_mb")
            status.metadata["vram_need_mb"] = need_mb
            self._set_stage(status, "gpu_wait", current_item=None)

            try:
                with gpu_session(
                    JobKind.VIDEO_RENDER,
                    batch_request.batch_id,
                    on_busy="wait",
                    wait_timeout=120.0,
                    evict_ollama=True,
                    free_comfyui=True,
                    cross_process=True,
                    vram_estimate_mb=vram_mb,
                    require_fit=True,
                    slot_id=slot_id,
                    lease_seconds=3600,
                ):
                    # Clear wait metadata once admitted.
                    status.metadata.pop("gpu_wait_reason", None)
                    self._run_batch_inner(
                        batch_request,
                        status,
                        parallel_comfyui=parallel_comfyui,
                    )
                return
            except GpuCapacityError as e:
                status.status = "error"
                status.error = f"Could not acquire GPU: {e}"
                status.end_time = datetime.now()
                self._set_stage(status, "done", save=False)
                self._save_metadata(status)
                logger.error(
                    "Batch %s capacity refuse (no retry): %s",
                    batch_request.batch_id,
                    e,
                )
                return
            except GpuBusyError as e:
                remaining = deadline - time.time()
                if remaining <= 0:
                    status.status = "error"
                    status.error = (
                        f"Could not acquire enough free VRAM after waiting "
                        f"{int(admit_deadline_s)}s: {e}"
                    )
                    status.end_time = datetime.now()
                    self._set_stage(status, "done", save=False)
                    self._save_metadata(status)
                    logger.error(
                        "Batch %s VRAM wait deadline exceeded: %s",
                        batch_request.batch_id,
                        e,
                    )
                    return

                logger.warning(
                    "Batch %s VRAM resident/busy (%s) — retrying "
                    "in %.0fs (%.0fs left)",
                    batch_request.batch_id,
                    e,
                    min(backoff_s, remaining),
                    remaining,
                )

                sleep_for = min(backoff_s, max(0.5, deadline - time.time()))
                # Wake early on cancel.
                end_sleep = time.time() + sleep_for
                while time.time() < end_sleep:
                    if cancel_event and cancel_event.is_set():
                        break
                    time.sleep(min(1.0, end_sleep - time.time()))
                backoff_s = min(15.0, backoff_s * 1.5)


    def _run_batch_inner(
        self,
        batch_request: BatchVideoRequest,
        status: BatchVideoStatus,
        *,
        parallel_comfyui: bool = False,
    ) -> None:
        cancel_event = self.cancel_events.get(batch_request.batch_id)

        try:
            status.start_time = datetime.now()
            status.status = "running"
            self._set_stage(status, "generate", current_item=None)

            batch_dir = Path(batch_request.output_dir)
            batch_dir.mkdir(parents=True, exist_ok=True)

            # Resolve cast members (trained character LoRAs) ONCE for the batch.
            # The video model can't apply a face LoRA, so identity is baked into the
            # cinematic keyframe (family-aware via character_still_pipeline: Z-Image
            # offline / SDXL / FLUX Comfy). Selecting cast implies cinematic-keyframe.
            # Reuses cast_lock (the single source of truth, same as music-video).
            cast_lora_paths: list[str] = []
            cast_lock_prefix = ""
            cast_lora_strength = batch_request.lora_strength
            cast_training_res: int = 768  # representative from subjects; used for keyframe sizing
            # Q1: an explicitly-picked approved reference still (metadata.keyframe_sample_id)
            # is animated directly as the I2V start frame — that exact 1024-class character
            # image is what makes the clip match the high-quality "Generate Character" stills.
            # Resolved once here (needs app context), consumed per-item below.
            cast_keyframe_image: Optional[str] = None
            if getattr(batch_request, "subject_ids", None):
                try:
                    from flask import current_app
                    try:
                        _app = current_app._get_current_object()
                    except RuntimeError:
                        from backend.app import get_or_create_app
                        _app = get_or_create_app()
                    with _app.app_context():
                        from backend.models import db as _db, Subject
                        from backend.services.cast_lock import subjects_to_lock, resolve_lora_strength
                        subs = [_db.session.get(Subject, int(sid)) for sid in batch_request.subject_ids]
                        cast_lora_paths, cast_lock_prefix = subjects_to_lock(
                            [s for s in subs if s], include_bible=True)
                        # Compute representative training res from cast subjects so
                        # _generate_keyframe_still can produce appropriately scaled seeds.
                        from backend.services.lora_training_settings import settings_for_subject
                        cast_training_res = max(
                            (settings_for_subject(s)["resolution"] for s in subs if s),
                            default=768
                        )
                        # Family-aware strength from first LoRA sidecar (Z-Image ~0.9, SDXL ~0.25).
                        _override = None if batch_request.lora_strength == 1.0 else batch_request.lora_strength
                        _strength_model = "zimage-turbo"
                        if cast_lora_paths:
                            try:
                                from backend.services.media_model_registry import resolve_inference_for_loras
                                _route = resolve_inference_for_loras(cast_lora_paths)
                                _strength_model = (
                                    _route.get("offline_model_key")
                                    or _route.get("comfy_model_tag")
                                    or _route.get("family")
                                    or "zimage-turbo"
                                )
                            except Exception:
                                pass
                        cast_lora_strength = resolve_lora_strength(_strength_model, _override)
                        # Q1: resolve an explicitly-chosen APPROVED still → I2V start frame.
                        # Defends that the sample is approved, on disk, and belongs to one of
                        # the selected subjects before trusting it.
                        kf_sid = (batch_request.metadata or {}).get("keyframe_sample_id")
                        if kf_sid:
                            from backend.models import SubjectSample as _SubjectSample
                            _samp = _db.session.get(_SubjectSample, int(kf_sid))
                            _sel_ids = [int(x) for x in batch_request.subject_ids]
                            if (_samp and _samp.approved and _samp.image_path
                                    and _samp.subject_id in _sel_ids
                                    and Path(_samp.image_path).exists()):
                                cast_keyframe_image = _samp.image_path
                                logger.info(
                                    "Batch %s: animating approved sample %s as the I2V start "
                                    "frame (%s)", batch_request.batch_id, kf_sid, _samp.image_path)
                        _db.session.remove()
                    if cast_lora_paths:
                        logger.info(
                            "Batch %s: locking %d cast LoRA(s) into cinematic keyframes "
                            "(prefix=%r, strength=%.2f)",
                            batch_request.batch_id, len(cast_lora_paths),
                            cast_lock_prefix, cast_lora_strength)
                except Exception as e:
                    logger.warning(
                        "Batch %s: cast resolution failed (%s); proceeding without "
                        "character LoRAs", batch_request.batch_id, e)

            # Storyboard / Director pass: rewrite prompts into cinematic shot prompts before
            # generation. Runs here (background worker, not the HTTP handler) and never
            # raises. Storyboard expands ONE concept into N connected shots; it already
            # produces directed prompts, so it's mutually exclusive with the per-prompt
            # director (running both would just re-direct already-directed shots).
            # Verbatim Prompts: keep the operator's exact text (no director, no style enhance).
            try:
                from backend.services.media_director import verbatim_prompts_enabled
                if verbatim_prompts_enabled():
                    batch_request.director_mode = False
                    batch_request.enhance_prompt = False
                    logger.info(
                        "Batch %s: verbatim prompts ON — video director/enhance off",
                        batch_request.batch_id,
                    )
            except Exception:
                pass
            if getattr(batch_request, "storyboard_concept", None):
                # Storyboard is intentional expansion of a concept into N shots — still
                # allowed under verbatim for the concept→shots step; per-shot enhance is off.
                self._set_stage(status, "storyboard")
                self._apply_storyboard(batch_request)
            elif getattr(batch_request, "director_mode", False):
                self._set_stage(status, "director")
                self._apply_director(batch_request)

            # Parallel processing of items within the batch (major P0 perf win).
            # The batch-level GPU locks are still held for the whole batch (safety),
            # but we overlap python work, status updates, and allow the Comfy queue
            # to see multiple jobs. Per-item cancel is checked.
            items = list(batch_request.items)
            # Warm-model reuse (cinematic): keyframe stills rendered in the pre-pass below
            # are cached here so the per-item animate phase reuses them — turning
            # load-FLUX→evict→load-Wan PER ITEM (2N model loads) into one FLUX load + one Wan
            # load for the whole batch. Empty in non-cinematic batches.
            precomputed_keyframes: dict = {}
            if items:
                def _process_item(item):
                    """Inner worker: returns (batch_result, completed_delta, failed_delta, oom_flag)"""
                    if cancel_event and cancel_event.is_set():
                        return (BatchVideoResult(item_id=item.id, success=False, error="cancelled before start"), 0, 0, False)

                    try:
                        meta = dict(item.metadata or {})
                        meta.setdefault("item_id", item.id)
                        meta["batch_controlled"] = True
                        if item.image_path:
                            meta.setdefault("image_path", item.image_path)

                        # Cinematic mode: for a TEXT item, synthesize a keyframe still and
                        # animate it with Wan 2.2 I2V (the music-video quality path). An item
                        # that already brought its own image just uses that image as-is.
                        # Selecting cast (cast_lora_paths) implies cinematic — it's the only
                        # way to lock a character LoRA into a video (baked into the keyframe).
                        item_model = batch_request.model
                        want_cinematic = (getattr(batch_request, "cinematic_keyframe", False)
                                          or bool(cast_lora_paths)
                                          or bool(cast_keyframe_image))
                        if (want_cinematic and not item.image_path and (item.prompt or "").strip()):
                            self._set_stage(status, "keyframe", current_item=item.id)
                            if cast_keyframe_image:
                                # Q1: the user picked an APPROVED reference still — animate
                                # THAT exact high-quality image (no re-render, no model/res
                                # mismatch). Identity + fidelity carry into the clip directly.
                                meta["image_path"] = cast_keyframe_image
                                meta["cinematic_keyframe"] = True
                                item_model = self._to_i2v_model(batch_request.model)
                            elif item.id in precomputed_keyframes:
                                # Warm-model reuse: this keyframe was rendered in the pre-pass
                                # (the still model stayed warm across the whole batch); just
                                # animate it — no per-item FLUX load/evict here.
                                meta["image_path"] = precomputed_keyframes[item.id]
                                meta["cinematic_keyframe"] = True
                                item_model = self._to_i2v_model(batch_request.model)
                            else:
                                # Raw scene prompt + subject_ids — pipeline owns identity lock.
                                still_path = str(Path(batch_dir) / f"keyframe_{item.id}.png")
                                _cast_ids = list(getattr(batch_request, "subject_ids", None) or [])
                                try:
                                    kf = self._generate_keyframe_still(
                                        prompt=item.prompt,
                                        width=batch_request.width,
                                        height=batch_request.height,
                                        out_path=still_path,
                                        seed=(batch_request.seed if batch_request.seed is not None else 1000),
                                        keyframe_model=(batch_request.metadata or {}).get("keyframe_model"),
                                        loras=(cast_lora_paths or None),
                                        subject_ids=_cast_ids or None,
                                        lora_strength=cast_lora_strength,
                                        training_resolution=cast_training_res,
                                        require_cast=bool(_cast_ids or cast_lora_paths),
                                    )
                                except RuntimeError as e:
                                    br = BatchVideoResult(
                                        item_id=item.id,
                                        success=False,
                                        error=str(e),
                                    )
                                    return (br, 0, 1, False)
                                if kf:
                                    meta["image_path"] = kf
                                    meta["cinematic_keyframe"] = True
                                    item_model = self._to_i2v_model(batch_request.model)
                                elif _cast_ids or cast_lora_paths:
                                    br = BatchVideoResult(
                                        item_id=item.id,
                                        success=False,
                                        error="Cast keyframe still missing — refusing plain T2V",
                                    )
                                    return (br, 0, 1, False)
                                # else: non-cast keyframe failed -> fall through to T2V

                        # I2V with no user prompt: caption the source image so the
                        # text conditioning describes what's actually in the frame.
                        # A contentless prompt lets the model reinvent the subject
                        # during the full-res stage of two-stage pipelines.
                        if item.image_path and not (item.prompt or "").strip():
                            self._set_stage(status, "caption", current_item=item.id)
                            caption = _caption_image_for_i2v(item.image_path)
                            item.prompt = (
                                f"{caption} Subtle natural motion; keep the subject, "
                                f"outfit, and scene exactly as shown."
                                if caption else
                                "Animate this image with subtle natural motion. Keep "
                                "the subject, outfit, and scene exactly as shown."
                            )
                            logger.info(
                                "I2V auto-caption for %s: %s", item.id, item.prompt[:120]
                            )

                        self._set_stage(status, "generate", current_item=item.id)
                        gen_request = VideoGenerationRequest(
                            prompt=item.prompt or "",
                            negative_prompt=batch_request.negative_prompt,
                            model=item_model,
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
                            fidelity_mode=batch_request.fidelity_mode,
                            wan_sampler_profile=batch_request.wan_sampler_profile,
                            freeu=batch_request.freeu,
                            face_restore=batch_request.face_restore,
                            lora_name=batch_request.lora_name,
                            lora_strength=batch_request.lora_strength,
                            speed_profile=batch_request.speed_profile,
                            style_embedding=batch_request.style_embedding,
                            last_frame_path=item.last_frame_path,
                            guides=list(item.guides or []),
                            ref_images=list((item.metadata or {}).get("ref_images") or meta.get("ref_images") or []),
                            ref_videos=list((item.metadata or {}).get("ref_videos") or meta.get("ref_videos") or []),
                            ref_audios=list((item.metadata or {}).get("ref_audios") or meta.get("ref_audios") or []),
                        )

                        result: VideoGenerationResult = self.video_generator.generate_video(gen_request)
                        br = BatchVideoResult(
                            item_id=item.id,
                            success=result.success,
                            video_path=result.video_path,
                            frame_paths=result.frame_paths,
                            thumbnail_path=result.thumbnail_path,
                            error=result.error,
                            metadata=dict(result.metadata or {}),
                        )
                        if result.success and result.video_path:
                            self._set_stage(status, "post", current_item=item.id, save=False)
                            self._attach_quality_metrics(
                                br,
                                video_path=result.video_path,
                                keyframe_path=meta.get("image_path"),
                                cinematic=bool(meta.get("cinematic_keyframe")),
                                high_consistency=bool(
                                    (batch_request.metadata or {}).get("high_consistency")
                                    or getattr(batch_request, "director_mode", False)
                                ),
                            )
                        return (br, 1 if result.success else 0, 0 if result.success else 1, False)
                    except Exception as e:  # pragma: no cover
                        err_str = str(e)
                        logger.error(f"Error generating video for item {item.id}: {e}")
                        is_oom = (
                            isinstance(e, RuntimeError) and ("out of memory" in err_str.lower() or "cuda" in err_str.lower() and "memory" in err_str.lower())
                            or "torch.cuda.OutOfMemoryError" in str(type(e)) or "OutOfMemory" in err_str
                            or "CUDA out of memory" in err_str
                        )
                        oom_note = " (OOM - VRAM exhausted; try smaller res/fewer steps or evict other models)" if is_oom else ""
                        if is_oom and hasattr(self, "video_generator") and hasattr(self.video_generator, "service_available"):
                            try:
                                self.video_generator.service_available = False
                            except Exception:
                                pass
                        br = BatchVideoResult(
                            item_id=item.id,
                            success=False,
                            error=err_str + oom_note,
                        )
                        return (br, 0, 1, is_oom)

                # ── Warm-model reuse pre-pass (cinematic only) ────────────────────
                # Render ALL keyframe stills first with the still model held RESIDENT
                # (keep_warm), evict it ONCE, then let the per-item animate phase below
                # reuse them with the Wan animator staying warm — turning 2N model loads
                # (FLUX→evict→Wan, per item) into 2 (one FLUX, one Wan) for an N-clip batch.
                # Any per-keyframe failure simply falls back to the lazy path in
                # _process_item, so this is a pure optimization.
                _warm_cinematic = bool(getattr(batch_request, "cinematic_keyframe", False) or cast_lora_paths)
                if _warm_cinematic and not cast_keyframe_image:
                    self._set_stage(status, "keyframe")
                    from backend.services.gpu_resource_policy import free_comfyui_vram as _free_comfy
                    for _it in items:
                        if cancel_event and cancel_event.is_set():
                            break
                        if getattr(_it, "image_path", None) or not (_it.prompt or "").strip():
                            continue  # brought its own image / no prompt → no keyframe needed
                        _cast_ids = list(getattr(batch_request, "subject_ids", None) or [])
                        try:
                            _kf = self._generate_keyframe_still(
                                prompt=_it.prompt,
                                width=batch_request.width, height=batch_request.height,
                                out_path=str(Path(batch_dir) / f"keyframe_{_it.id}.png"),
                                seed=(batch_request.seed if batch_request.seed is not None else 1000),
                                keyframe_model=(batch_request.metadata or {}).get("keyframe_model"),
                                loras=(cast_lora_paths or None),
                                subject_ids=_cast_ids or None,
                                lora_strength=cast_lora_strength,
                                keep_warm=True,  # hold the still model across ALL keyframes
                                training_resolution=cast_training_res,
                                require_cast=bool(_cast_ids or cast_lora_paths),
                            )
                        except RuntimeError as e:
                            logger.error("Warm keyframe failed for %s: %s", _it.id, e)
                            _kf = None
                        if _kf:
                            precomputed_keyframes[_it.id] = _kf
                    if precomputed_keyframes:
                        logger.info(
                            "Warm-reuse: pre-rendered %d keyframe(s) with the still model held; "
                            "evicting once before the I2V animator loads",
                            len(precomputed_keyframes))
                        try:
                            _free_comfy()
                        except Exception:
                            pass

                # ComfyUI serializes GPU work — parallel POST /prompt only queues blocking
                # polls and amplifies VRAM pressure. Serial by default; opt-in parallel via
                # GUAARDVARK_BATCH_COMFYUI_PARALLEL=1 (non-cinematic only).
                if (
                    getattr(batch_request, "cinematic_keyframe", False)
                    or not parallel_comfyui
                ):
                    max_workers = 1
                else:
                    max_workers = max(1, min(4, len(items)))
                with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="video-item") as ex:
                    future_map = {ex.submit(_process_item, it): it for it in items}
                    for fut in as_completed(future_map):
                        if cancel_event and cancel_event.is_set():
                            status.status = "cancelled"
                            break
                        try:
                            br, dc, df, oom = fut.result()
                            status.results.append(br)
                            status.completed_videos += dc
                            status.failed_videos += df
                            if oom:
                                status.error = (status.error or "") + "OOM in batch item; "
                        except Exception as e:
                            it = future_map[fut]
                            logger.error(f"Item worker {it.id} failed: {e}")
                            status.failed_videos += 1
                            status.results.append(BatchVideoResult(item_id=it.id, success=False, error=str(e)))
                        finally:
                            self._save_metadata(status)

            if status.status != "cancelled":
                status.status = "completed" if status.failed_videos == 0 else "error"
            status.end_time = datetime.now()
            self._set_stage(status, "register" if status.completed_videos > 0 else "done")

            # Register videos into Documents/Files system
            if status.completed_videos > 0:
                try:
                    from flask import current_app
                    from backend.services.output_registration import ensure_subfolder, register_file
                    try:
                        app = current_app._get_current_object()
                    except RuntimeError:
                        # Worker thread has no request context — grab the singleton
                        # instead of rebuilding the entire Flask app from scratch.
                        from backend.app import get_or_create_app
                        app = get_or_create_app()
                    with app.app_context():
                        try:
                            batch_id = batch_request.batch_id
                            ensure_subfolder("Videos", batch_id)
                            batch_dir = Path(batch_request.output_dir)
                            # What the clip is and who made it travels with the
                            # Document: the editor keeps a soundtrack it knows is
                            # there, and publishing can carry the attribution the
                            # model's license asks for.
                            file_meta = {"source": "batch_generation", "batch_id": batch_id,
                                         "model": batch_request.model}
                            try:
                                from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY
                                lic = (VIDEO_MODEL_REGISTRY.get(batch_request.model) or {}).get("license") or {}
                                if lic.get("attribution"):
                                    file_meta["attribution"] = lic["attribution"]
                            except Exception:
                                pass
                            audio_clips = {
                                Path(r.video_path).name for r in status.results
                                if r.success and r.video_path and (r.metadata or {}).get("has_audio") == "1"
                            }
                            # Register all video files found in the batch directory
                            for vid_file in sorted(batch_dir.rglob("*.mp4")):
                                register_file(
                                    physical_path=str(vid_file),
                                    folder_name="Videos",
                                    subfolder_name=batch_id,
                                    file_metadata={**file_meta, "has_audio": vid_file.name in audio_clips},
                                )
                            logger.info(f"Registered batch {batch_id} videos into Documents system")
                        finally:
                            from backend.models import db as _db
                            _db.session.remove()
                except Exception as reg_err:
                    logger.error(f"Failed to register batch videos: {reg_err}")

            self._set_stage(status, "done")

        finally:
            logger.info(f"Batch {batch_request.batch_id} finished render phase")

    def start_batch_from_prompts(
        self,
        prompts: List[str],
        **params,
    ) -> BatchVideoStatus:
        from backend.services.output_registration import bates_name
        batch_id = params.get("batch_id") or bates_name("video_batch", "", self.base_output_dir)
        guides_per_item = list(params.pop("guides", None) or [])
        items = [
            BatchVideoItem(
                id=str(uuid.uuid4()), prompt=p, metadata={"source": "prompt"},
                guides=list(guides_per_item[i] or []) if i < len(guides_per_item) else [],
            )
            for i, p in enumerate(prompts)
        ]
        metadata = dict(params.get("metadata") or {})
        if not metadata.get("display_name") and prompts:
            metadata["display_name"] = _derive_display_name(prompts[0])
        params["metadata"] = metadata
        return self._start_batch(batch_id=batch_id, items=items, **params)

    def start_batch_from_images(
        self,
        image_paths: List[str],
        **params,
    ) -> BatchVideoStatus:
        from backend.services.output_registration import bates_name
        batch_id = params.get("batch_id") or bates_name("video_batch", "", self.base_output_dir)
        user_prompt = params.pop("prompt", "")
        # Index-paired with image_paths: an end frame per item (flf2v) and the
        # guides per item. Shorter lists leave the remaining items without.
        last_frame_paths = list(params.pop("last_frame_paths", None) or [])
        guides_per_item = list(params.pop("guides", None) or [])
        items = [
            BatchVideoItem(
                id=str(uuid.uuid4()),
                # Left empty on purpose: the render worker auto-captions the
                # image so the prompt describes the actual content (a filename
                # placeholder here used to drive identity drift in LTX I2V).
                prompt=user_prompt or "",
                image_path=path,
                last_frame_path=(last_frame_paths[i] if i < len(last_frame_paths) else None) or None,
                guides=list(guides_per_item[i] or []) if i < len(guides_per_item) else [],
                metadata={"source": "image", "image_path": path},
            )
            for i, path in enumerate(image_paths)
        ]
        metadata = dict(params.get("metadata") or {})
        if not metadata.get("display_name"):
            seed_text = user_prompt or (Path(image_paths[0]).name if image_paths else "")
            if seed_text:
                metadata["display_name"] = _derive_display_name(seed_text)
        params["metadata"] = metadata
        return self._start_batch(batch_id=batch_id, items=items, **params)

    def _start_batch(self, batch_id: str, items: List[BatchVideoItem], **params) -> BatchVideoStatus:
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
            model=params.get("model", "wan22-5b"),
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
            fidelity_mode=bool(params.get("fidelity_mode", False)),
            wan_sampler_profile=params.get("wan_sampler_profile") or None,
            negative_prompt=params.get("negative_prompt", "") or "",
            freeu=bool(params.get("freeu", False)),
            face_restore=bool(params.get("face_restore", False)),
            lora_name=params.get("lora_name"),
            lora_strength=float(params.get("lora_strength", 1.0)),
            speed_profile=params.get("speed_profile") or None,
            style_embedding=params.get("style_embedding") or None,
            subject_ids=[int(s) for s in (params.get("subject_ids") or []) if str(s).strip()],
            director_mode=bool(params.get("director_mode", False)),
            cinematic_keyframe=bool(params.get("cinematic_keyframe", False)),
            director_guidance=params.get("director_guidance") or None,
            storyboard_concept=params.get("storyboard_concept") or None,
            metadata=params.get("metadata", {}),
        )

        status = BatchVideoStatus(
            batch_id=batch_id,
            status="queued",
            total_videos=len(items),
            output_dir=str(batch_dir),
            metadata=params.get("metadata", {}),
            stage="queued",
            progress_pct=0,
        )

        # Persist enough info to allow one-click retry for failed batches without
        # user re-entering all prompts, images, model, steps, fidelity, lora, freeu, tiers etc.
        try:
            is_image_mode = any(getattr(i, "image_path", None) for i in items)
            prompts_list = [i.prompt for i in items]
            image_paths_list = [i.image_path for i in items if getattr(i, "image_path", None)]
            retry_params = {
                "model": batch_request.model,
                "duration_frames": batch_request.duration_frames,
                "fps": batch_request.fps,
                "width": batch_request.width,
                "height": batch_request.height,
                "motion_strength": batch_request.motion_strength,
                "num_inference_steps": batch_request.num_inference_steps,
                "guidance_scale": batch_request.guidance_scale,
                "seed": batch_request.seed,
                "generate_frames_only": batch_request.generate_frames_only,
                "frames_per_batch": batch_request.frames_per_batch,
                "combine_frames": batch_request.combine_frames,
                "interpolation_multiplier": batch_request.interpolation_multiplier,
                "prompt_style": batch_request.prompt_style,
                "enhance_prompt": batch_request.enhance_prompt,
                "fidelity_mode": batch_request.fidelity_mode,
                "wan_sampler_profile": batch_request.wan_sampler_profile,
                "negative_prompt": batch_request.negative_prompt,
                "freeu": batch_request.freeu,
                "face_restore": batch_request.face_restore,
                "lora_name": batch_request.lora_name,
                "lora_strength": batch_request.lora_strength,
                "subject_ids": list(batch_request.subject_ids or []),
                "cinematic_keyframe": bool(batch_request.cinematic_keyframe),
                "director_mode": bool(batch_request.director_mode),
                "metadata": dict(batch_request.metadata or {}),
                # Exact control-panel snapshot for "Adjust & Retry" (restore the UI verbatim).
                "ui_config": params.get("ui_config"),
            }
            if is_image_mode:
                status.retry_data = {
                    "mode": "image",
                    "image_paths": image_paths_list,
                    "prompt": prompts_list[0] if prompts_list else "",
                    "params": retry_params,
                }
            else:
                status.retry_data = {
                    "mode": "text",
                    "prompts": prompts_list,
                    "params": retry_params,
                }
        except Exception:
            # best effort; old batches without it are still loadable
            pass

        with self.batch_lock:
            self.active_batches[batch_id] = status
            self.cancel_events[batch_id] = threading.Event()
            self.queue_order.append(batch_id)

        # Persist queued state immediately so a restart leaves a discoverable trail
        # (Phase 2 will use this for opt-in resume).
        self._save_metadata(status)

        # Stack it on the queue. The single worker thread drains one batch at a time.
        self.batch_queue.put((batch_request, status))
        logger.info(
            f"Enqueued batch {batch_id} ({len(items)} items) — "
            f"queue depth ~{self.batch_queue.qsize()}"
        )

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
                results = [
                    BatchVideoResult(**res)
                    for res in data.get("results", [])
                ]
                # Retroactively extract thumbnails for results that have videos but no thumbnail
                metadata_changed = False
                for res in results:
                    if res.video_path and not res.thumbnail_path:
                        video_file = batch_dir / res.video_path
                        if video_file.exists() and video_file.suffix.lower() in (".mp4", ".webm", ".avi", ".mov"):
                            thumb_filename = video_file.stem + "_thumb.jpg"
                            # Place thumbnail in a thumbnails subdir next to the video
                            thumbs_dir = video_file.parent.parent / "thumbnails"
                            thumb_path = thumbs_dir / thumb_filename
                            if self._extract_thumbnail(video_file, thumb_path):
                                res.thumbnail_path = str(thumb_path.relative_to(batch_dir))
                                metadata_changed = True
                if metadata_changed:
                    # Persist the updated thumbnail paths back to metadata
                    try:
                        for i, res in enumerate(results):
                            if res.thumbnail_path and i < len(data.get("results", [])):
                                data["results"][i]["thumbnail_path"] = res.thumbnail_path
                        with open(metadata_file, "w") as f:
                            json.dump(data, f, indent=2)
                    except Exception:
                        pass  # Best effort

                start_time = datetime.fromisoformat(data["start_time"]) if data.get("start_time") else None
                end_time = datetime.fromisoformat(data["end_time"]) if data.get("end_time") else None
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
                    retry_data=data.get("retry_data"),
                    stage=data.get("stage") or "done",
                    current_item=data.get("current_item"),
                    progress_pct=data.get("progress_pct"),
                )
            except Exception as e:  # pragma: no cover
                logger.error(f"Failed to load batch status for {batch_id}: {e}")
                return None
        return None

    def cancel_batch(self, batch_id: str) -> bool:
        """Cancel a queued or running batch.

        Two-layer interrupt: flip the cancel event (so the worker bails out
        between items) and, if the batch is mid-render, yell at ComfyUI's
        /interrupt endpoint so the current sampler aborts immediately.
        """
        cancellable = ("queued", "running", "pending", "processing")

        # In-memory path — covers anything queued or running
        with self.batch_lock:
            status = self.active_batches.get(batch_id)
            event = self.cancel_events.get(batch_id)

        if status and status.status in cancellable:
            if event:
                event.set()

            was_running = (status.status == "running") or (self._running_batch_id == batch_id)
            status.status = "cancelled"
            status.end_time = datetime.now()
            if not status.error:
                status.error = "Cancelled by user"
            self._save_metadata(status)

            if was_running:
                # Force ComfyUI to abort the current sampler. Without this,
                # cancel only fires between items — useless for a 20-min Wan run.
                try:
                    interrupted = self.video_generator.interrupt()
                    logger.info(
                        f"Cancel batch {batch_id}: running, interrupt sent "
                        f"(ack={interrupted})"
                    )
                except Exception as e:
                    logger.warning(f"Cancel batch {batch_id}: interrupt call failed: {e}")
            else:
                logger.info(f"Cancel batch {batch_id}: queued, will skip when worker reaches it")
            return True

        # Fall back to on-disk metadata for batches no longer tracked in memory
        batch_dir = self._get_batch_dir(batch_id)
        metadata_file = batch_dir / "batch_metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, "r") as f:
                    data = json.load(f)
                if data.get("status") in cancellable:
                    data["status"] = "cancelled"
                    data["end_time"] = datetime.now().isoformat()
                    if not data.get("error"):
                        data["error"] = "Cancelled by user"
                    with open(metadata_file, "w") as f:
                        json.dump(data, f, indent=2)
                    logger.info(f"Cancelled on-disk batch {batch_id}")
                    return True
            except Exception as e:
                logger.error(f"Failed to cancel batch {batch_id}: {e}")
                return False
        return False

    @staticmethod
    def _emit_canonical_job_event(batch_status: BatchVideoStatus) -> None:
        """Push video_gen updates to the unified jobs:* socket channel."""
        try:
            from backend.services.job_registry import adapt_video_gen
            from backend.socketio_instance import socketio
            job_dict = adapt_video_gen(batch_status).to_dict()
            socketio.emit("job:event", job_dict, to="jobs:all", namespace="/")
            socketio.emit("job:event", job_dict, to="jobs:video_gen", namespace="/")
        except Exception as e:
            logger.debug(f"Failed to emit canonical job event for {batch_status.batch_id}: {e}")

    _ACTIVE_STATUSES = frozenset({"queued", "pending", "running", "processing"})

    def list_active_batches(self) -> List[BatchVideoStatus]:
        """In-memory and on-disk batches that are still queued or running."""
        seen: set[str] = set()
        active: List[BatchVideoStatus] = []

        with self.batch_lock:
            running_id = self._running_batch_id
            for batch_id, status in self.active_batches.items():
                if status.status in self._ACTIVE_STATUSES:
                    active.append(status)
                    seen.add(batch_id)

        try:
            for batch_dir in self.base_output_dir.iterdir():
                if not batch_dir.is_dir():
                    continue
                batch_id = batch_dir.name
                if batch_id in seen:
                    continue
                metadata_file = batch_dir / "batch_metadata.json"
                if not metadata_file.exists():
                    continue
                try:
                    with open(metadata_file, "r") as f:
                        data = json.load(f)
                except Exception:
                    continue
                if data.get("status") not in self._ACTIVE_STATUSES:
                    continue
                loaded = self.get_batch_status(batch_id)
                if loaded:
                    active.append(loaded)
                    seen.add(batch_id)
        except Exception as e:
            logger.warning(f"Failed to scan for active batches: {e}")

        return active

    def list_batches_for_jobs(self, *, limit: int = 100) -> List[Dict]:
        """Snapshot for /api/jobs — active batches first, then recent history."""
        snapshot: List[Dict] = []
        seen: set[str] = set()

        with self.batch_lock:
            running_id = self._running_batch_id
            order = list(self.queue_order)

        position = 0
        for batch_id in order:
            with self.batch_lock:
                status = self.active_batches.get(batch_id)
            if not status:
                continue
            position += 1
            entry = self._batch_status_to_job_row(status, position=position, is_running=batch_id == running_id)
            snapshot.append(entry)
            seen.add(batch_id)

        for status in self.list_active_batches():
            if status.batch_id in seen:
                continue
            entry = self._batch_status_to_job_row(
                status,
                position=None,
                is_running=(status.batch_id == self._running_batch_id),
            )
            snapshot.append(entry)
            seen.add(status.batch_id)

        for row in self.list_batches():
            batch_id = row.get("batch_id")
            if not batch_id or batch_id in seen:
                continue
            row = dict(row)
            row.setdefault("metadata", {})
            if row.get("display_name"):
                row["metadata"]["display_name"] = row["display_name"]
            snapshot.append(row)
            seen.add(batch_id)
            if len(snapshot) >= limit:
                break

        return snapshot[:limit]

    @staticmethod
    def _batch_status_to_job_row(
        status: BatchVideoStatus,
        *,
        position: int | None,
        is_running: bool,
    ) -> Dict:
        metadata = dict(status.metadata or {})
        if position is not None:
            metadata["queue_position"] = position
        metadata["is_running"] = is_running
        return {
            "batch_id": status.batch_id,
            "status": status.status,
            "total_videos": status.total_videos,
            "completed_videos": status.completed_videos,
            "failed_videos": status.failed_videos,
            "start_time": status.start_time.isoformat() if status.start_time else None,
            "end_time": status.end_time.isoformat() if status.end_time else None,
            "error": status.error,
            "metadata": metadata,
            "display_name": metadata.get("display_name"),
            "is_running": is_running,
        }

    def cancel_all_active(self, reason: str = "Cancelled by system shutdown") -> List[str]:
        """Cancel every queued/running batch and release GPU resources."""
        cancelled: List[str] = []
        for status in self.list_active_batches():
            batch_id = status.batch_id
            if self.cancel_batch(batch_id):
                cancelled.append(batch_id)
                if not status.error:
                    status.error = reason
                    self._save_metadata(status)

        if cancelled:
            try:
                self.video_generator.interrupt()
            except Exception as e:
                logger.warning(f"cancel_all_active: ComfyUI interrupt failed: {e}")

        try:
            gpu_coordinator = get_gpu_coordinator()
            for batch_id in cancelled:
                gpu_coordinator.release_generic(f"video_render:batch_{batch_id}")
            gpu_coordinator.release_video_generation_lock(restart_ollama=False)
        except Exception as e:
            logger.warning(f"cancel_all_active: GPU lock release failed: {e}")

        try:
            from backend.services.job_operation_gate import get_gate
            from backend.services.job_types import JobKind
            gate = get_gate()
            snap = gate.snapshot()
            holder = snap.get("gpu_holder") or {}
            if holder.get("kind") == JobKind.VIDEO_RENDER.value:
                gate.release_gpu_exclusive(JobKind.VIDEO_RENDER, str(holder.get("native_id", "")))
        except Exception as e:
            logger.warning(f"cancel_all_active: gate release failed: {e}")

        logger.info(f"cancel_all_active: cancelled {len(cancelled)} batch(es): {cancelled}")
        return cancelled

    def list_queue(self) -> List[Dict]:
        """Snapshot of the current queue for the UI panel.

        Returns batches in submission order with a position number.
        Includes queued, running, and recently completed/cancelled batches
        from the in-memory active set.
        """
        snapshot = []
        with self.batch_lock:
            order = list(self.queue_order)
            running_id = self._running_batch_id

        position = 0
        for batch_id in order:
            with self.batch_lock:
                status = self.active_batches.get(batch_id)
            if not status:
                continue
            position += 1
            snapshot.append({
                "position": position,
                "batch_id": batch_id,
                "status": status.status,
                "stage": getattr(status, "stage", None),
                "current_item": getattr(status, "current_item", None),
                "progress_pct": getattr(status, "progress_pct", None),
                "total_videos": status.total_videos,
                "completed_videos": status.completed_videos,
                "failed_videos": status.failed_videos,
                "is_running": batch_id == running_id,
                "start_time": status.start_time.isoformat() if status.start_time else None,
                "end_time": status.end_time.isoformat() if status.end_time else None,
                "display_name": (status.metadata or {}).get("display_name"),
                "error": status.error,
            })
        return snapshot

    def _cleanup_stale_batches(self) -> None:
        """Mark batches stuck in running/pending/processing as cancelled.

        Called during list_batches to auto-recover from crashes/restarts.
        Only affects batches that are NOT actively tracked in memory.
        """
        try:
            for batch_dir in self.base_output_dir.iterdir():
                if not batch_dir.is_dir():
                    continue
                metadata_file = batch_dir / "batch_metadata.json"
                if not metadata_file.exists():
                    continue
                batch_id = batch_dir.name

                # Skip batches that are actively tracked in memory
                with self.batch_lock:
                    if batch_id in self.active_batches:
                        continue

                try:
                    with open(metadata_file, "r") as f:
                        data = json.load(f)
                    if data.get("status") in ("running", "pending", "processing"):
                        data["status"] = "cancelled"
                        data["end_time"] = datetime.now().isoformat()
                        data["error"] = "Interrupted by system restart"
                        with open(metadata_file, "w") as f:
                            json.dump(data, f, indent=2)
                        logger.info(f"Auto-cancelled stale batch {batch_id}")
                except Exception as e:
                    logger.warning(f"Failed to cleanup stale batch {batch_id}: {e}")
        except Exception as e:
            logger.warning(f"Failed to scan for stale batches: {e}")

    def list_batches(self) -> List[Dict]:
        # Auto-cleanup stale batches from previous runs
        self._cleanup_stale_batches()

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
                                "display_name": data.get("metadata", {}).get("display_name"),
                                "can_retry": bool(data.get("retry_data")),
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Failed to read metadata for {batch_id}: {e}")
                batches.append(entry)
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to list video batches: {e}")
        # Newest first so the Video Library shows recent work at the top (and any
        # caller capping the list keeps the most recent batches). start_time is an
        # ISO-8601 string when present; missing/unknown sort to the bottom.
        batches.sort(key=lambda b: b.get("start_time") or b.get("end_time") or "", reverse=True)
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

    def combine_frames(self, batch_id: str, item_id: Optional[str] = None, fps: int = 7) -> Optional[str]:
        batch_dir = self._get_batch_dir(batch_id)
        if not batch_dir.exists():
            return None

        # Determine target item directory
        item_dir: Optional[Path] = None
        if item_id:
            try:
                candidate = contained(batch_dir, item_id)
            except PathEscapesRoot:
                return None
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

        # Each item_dir holds a single rendered video — give it a clean,
        # timestamped name to ensure unique filenames across runs.
        from datetime import datetime
        video_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        video_path = videos_dir / f"video_{video_timestamp}.mp4"
        if video_path.exists():
            # Same item_dir got re-rendered (rare). Add a sequential suffix
            # using the same Files-app convention the resolver uses.
            from backend.utils.filename_resolver import _split_existing_suffix
            stem, n = _split_existing_suffix(f"video_{video_timestamp}")
            while video_path.exists():
                n += 1
                video_path = videos_dir / f"{stem} ({n}).mp4"
        combined = self.video_generator._combine_frames_to_video(frames_dir, video_path, fps)
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

