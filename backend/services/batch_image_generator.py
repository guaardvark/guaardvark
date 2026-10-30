
import csv
import hashlib
import io
import json
import logging
import os
import queue
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Union

logger = logging.getLogger(__name__)

BLUEPRINT_MAX_ROWS = 50000

try:
    from backend.services.offline_image_generator import get_image_generator, ImageGenerationRequest, ImageGenerationResult
    from backend.utils.unified_progress_system import UnifiedProgressSystem, ProgressEvent, ProcessStatus, ProcessType
    from backend.utils.system_coordinator import SystemCoordinator, ProcessType as CoordProcessType, ResourceType
    offline_gen_available = True
except ImportError as e:
    logger.error(f"Failed to import required dependencies: {e}")
    offline_gen_available = False

try:
    from backend.services.media_director import expand_image_plan
    MEDIA_DIRECTOR_AVAILABLE = True
except Exception as e:  # noqa: BLE001
    logger.warning(f"media_director not available for batch image (will skip): {e}")
    MEDIA_DIRECTOR_AVAILABLE = False

try:
    from backend.config import CACHE_DIR, UPLOAD_DIR
    config_available = True
except ImportError:
    config_available = False
    CACHE_DIR = "/tmp/guaardvark_cache"
    UPLOAD_DIR = "/tmp/guaardvark_uploads"

def _kind_of(error) -> Optional[str]:
    """The RenderErrorKind value an error carries (a RenderFailure, or an
    exception classified by job_operation_gate), else None."""
    if error is None:
        return None
    if isinstance(error, BaseException):
        from backend.services.job_operation_gate import classify_render_exception
        kind = classify_render_exception(error)
    else:
        from backend.services.job_types import failure_kind
        kind = failure_kind(error)
    value = getattr(kind, "value", None)
    return value if value and value != "unknown" else None


def _error_with_kind(exc: BaseException):
    """``str(exc)``, keeping the failure kind with it (job_types.RenderFailure)."""
    kind = _kind_of(exc)
    if kind is None:
        return str(exc)
    from backend.services.job_types import RenderFailure
    return RenderFailure(kind, str(exc))


@dataclass
class BatchPrompt:
    id: str
    prompt: str
    negative_prompt: str = ""
    style: str = "realistic"
    # Defaults align with stills_defaults (modern / auto family). Callers should
    # still run resolve_stills_defaults when model is known.
    width: int = 1024
    height: int = 1024
    steps: int = 9
    guidance: float = 0.0
    seed: Optional[int] = None
    model: str = "auto"
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Character casting: subject_ids preferred (identity core + family routing via
    # character_still_pipeline). loras kept for path-only / diagnostics.
    loras: List[str] = field(default_factory=list)
    subject_ids: List[int] = field(default_factory=list)
    trigger_word: str = ""
    # User-catalog LoRA files (Manage Image Models) stacked on the plain stills
    # path; resolved to paths and one strength at API time.
    adapter_loras: List[str] = field(default_factory=list)
    adapter_scale: float = 0.8
    # User text encoders (Manage Image Models) for a ComfyUI stills render,
    # {slot: loader filename}; resolved at API time like the LoRAs.
    text_encoders: Dict[str, str] = field(default_factory=dict)
    content_preset: Optional[str] = None
    auto_enhance: bool = True
    enhance_anatomy: bool = True
    enhance_faces: bool = True
    enhance_hands: bool = True

@dataclass
class BatchImageRequest:
    batch_id: str
    prompts: List[BatchPrompt]
    output_dir: str
    max_workers: int = 2
    preserve_order: bool = True
    generate_thumbnails: bool = True
    save_metadata: bool = True
    user_id: Optional[str] = None
    project_id: Optional[str] = None
    content_preset: Optional[str] = None
    auto_enhance: bool = True
    enhance_anatomy: bool = True
    enhance_faces: bool = True
    enhance_hands: bool = True
    restore_faces: bool = False
    face_restoration_weight: float = 0.5
    remove_background: bool = False
    # Director / storyboard intelligence (opt-in, defaults preserve old behavior)
    director_mode: bool = False
    director_guidance: Optional[str] = None
    storyboard_concept: Optional[str] = None
    planning_mode: str = "narrative"
    director_model: Optional[str] = None
    user_treatment: Optional[str] = None
    ui_config: Optional[Dict[str, Any]] = None
    retry_data: Optional[Dict[str, Any]] = None
    # Lets the run download a model that is not installed. No page sets it:
    # models install from Install in Manage models, never from Generate.
    allow_model_download: bool = False
    # "image" or "infographic". Infographic prompts render through
    # InfographicGenerator.render_still (see _generate_single_image).
    kind: str = "image"
    # Queue and history label; the first prompt when unset.
    display_name: Optional[str] = None


# Batch-level kwargs the create_batch_from_* factories forward into
# BatchImageRequest. Derived from the dataclass so a new field is carried
# automatically; the API's per-prompt keys (model, width, ...) are excluded
# because they are applied to each BatchPrompt instead.
_BATCH_REQUEST_PARAMS = frozenset(
    BatchImageRequest.__dataclass_fields__.keys()
) - {"batch_id", "prompts", "output_dir"}

@dataclass
class BatchImageResult:
    prompt_id: str
    success: bool
    image_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    generation_time: float = 0.0
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    # RenderErrorKind value (job_types) of a failed image, named where it failed.
    error_kind: Optional[str] = None

@dataclass
class BatchGenerationStatus:
    batch_id: str
    status: str  # "queued", "pending", "running", "completed", "error", "cancelled"
    total_images: int
    completed_images: int
    failed_images: int
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    results: List[BatchImageResult] = field(default_factory=list)
    error: Optional[str] = None
    output_dir: Optional[str] = None
    estimated_time_remaining: Optional[float] = None
    restore_faces: bool = False
    face_restoration_weight: float = 0.5
    generate_thumbnails: bool = True
    remove_background: bool = False
    # Optional UI label (first prompt snippet) for the queue panel
    display_name: Optional[str] = None
    retry_data: Optional[Dict[str, Any]] = None
    # Set while the batch is admitted-but-waiting on VRAM; cleared on admit.
    gpu_wait_reason: Optional[str] = None
    # RenderErrorKind value (job_types) of the batch's own error, when it has one.
    error_kind: Optional[str] = None
    kind: str = "image"


def batch_kind_of(batch_id: str, recorded: Optional[str] = None) -> str:
    """A batch's kind: the one its metadata records, else read from its id."""
    if recorded:
        return recorded
    return "infographic" if (batch_id or "").startswith("InfographicBatch") else "image"

# Seed space shared with the offline path (offline_image_generator uses
# torch.randint(0, 2**32)), so seeds stay comparable across generators.
SEED_SPACE = 2 ** 32


def resolve_image_seed(prompt: "BatchPrompt", batch_id: str = "") -> int:
    """Effective seed for one batch image.

    An explicit per-prompt seed is returned verbatim — deterministic rerun is a
    supported workflow and must never be overridden. Otherwise the seed is
    derived from (batch_id, prompt.id), which gives three properties the old
    hardcoded ``42`` did not:

      * every slot in a batch gets a DIFFERENT seed, so N prompts no longer
        collapse to N identical renders on a deterministic model like FLUX;
      * the same slot in the same batch is stable, so a batch is reproducible
        from its id alone;
      * a fresh batch_id yields fresh images, so re-running is a real re-roll.

    Falls back to a random basis when there is no batch id to key on, so an
    ad-hoc single call still varies rather than pinning to a constant.
    """
    if prompt.seed is not None:
        return int(prompt.seed) % SEED_SPACE
    key = batch_id or f"adhoc-{random.randrange(SEED_SPACE)}"
    basis = f"{key}:{prompt.id or ''}".encode("utf-8", "replace")
    return int.from_bytes(hashlib.blake2b(basis, digest_size=8).digest(), "big") % SEED_SPACE


class BatchImageGenerator:

    _ACTIVE_STATUSES = frozenset({"queued", "pending", "running", "processing"})

    def __init__(self):
        # Images land directly in data/uploads/Images/ so DocumentsPage sees them
        self.base_output_dir = Path(UPLOAD_DIR) / "Images"
        self.cache_dir = Path(CACHE_DIR) / "batch_generation"

        self.base_output_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.active_batches: Dict[str, BatchGenerationStatus] = {}
        self.batch_lock = threading.Lock()
        self.executors: Dict[str, ThreadPoolExecutor] = {}

        # Queue plumbing — one batch runs at a time; the rest stack (mirrors video gen).
        self.batch_queue: "queue.Queue[tuple]" = queue.Queue()
        self.cancel_events: Dict[str, threading.Event] = {}
        self.queue_order: List[str] = []
        self._running_batch_id: Optional[str] = None

        self.progress_system = UnifiedProgressSystem() if offline_gen_available else None
        self.system_coordinator = SystemCoordinator() if offline_gen_available else None
        
        self.image_generator = None
        if offline_gen_available:
            try:
                self.image_generator = get_image_generator()
                logger.info("Image generator initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize image generator: {e}")
                self.image_generator = None

        self.service_available = offline_gen_available and config_available

        self._worker_thread = threading.Thread(
            target=self._queue_worker, daemon=True, name="batch-image-worker"
        )
        self._worker_thread.start()
        logger.info("BatchImageGenerator queue worker started")

        logger.info(f"BatchImageGenerator initialized - Service available: {self.service_available}")

    def _generate_batch_id(self, kind: str = "image") -> str:
        from backend.services.output_registration import bates_name
        # Bates-stamped batch folder: ImageBatch_04-02-2026_001, or
        # InfographicBatch_... for an infographic batch.
        content_type = "infographic_batch" if kind == "infographic" else "image_batch"
        return bates_name(content_type, "", self.base_output_dir)

    def _create_output_directory(self, batch_id: str) -> Path:
        output_dir = self.base_output_dir / batch_id
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "images").mkdir(exist_ok=True)
        (output_dir / "thumbnails").mkdir(exist_ok=True)
        return output_dir

    def _parse_csv_prompts(
        self,
        csv_content: str,
        *,
        form_model: str | None = None,
        form_width: int | None = None,
        form_height: int | None = None,
        form_steps: int | None = None,
        form_steps_explicit: bool = False,
        form_steps_metadata: Dict[str, Any] | None = None,
        form_guidance: float | None = None,
        form_style: str | None = None,
    ) -> List[BatchPrompt]:
        """Parse CSV rows into BatchPrompts.

        Empty CSV cells inherit form-level model/size/steps/guidance (the old
        path ignored the form and hard-coded SD-era 512/20/7.5/sd-1.5).
        """
        from backend.services.stills_defaults import resolve_stills_defaults

        prompts = []
        default_model = (form_model or "auto").strip() or "auto"

        try:
            if hasattr(csv_content, 'read'):
                csv_content = csv_content.read()
            
            if not isinstance(csv_content, str):
                csv_content = str(csv_content)

            csv_file = io.StringIO(csv_content)
            csv_reader = csv.DictReader(csv_file)

            for i, row in enumerate(csv_reader):
                if 'prompt' not in row:
                    logger.warning(f"Row {i+1} missing 'prompt' field, skipping")
                    continue
                
                from backend.services.image_prompt_sanitize import sanitize_image_prompt
                prompt_text = sanitize_image_prompt(row['prompt'] if row.get('prompt') else '')
                if not prompt_text:
                    logger.warning(f"Row {i+1} has empty prompt, skipping")
                    continue

                prompt_id = row.get('id', '').strip() or f"prompt_{i+1}"
                row_model = (row.get('model') or '').strip() or default_model

                def _cell_int(key: str, form_val: int | None):
                    raw = (row.get(key) or '').strip()
                    if raw:
                        return int(raw)
                    return form_val

                def _cell_float(key: str, form_val: float | None):
                    raw = (row.get(key) or '').strip()
                    if raw:
                        return float(raw)
                    return form_val

                try:
                    w_in = _cell_int('width', form_width)
                    h_in = _cell_int('height', form_height)
                    s_in = _cell_int('steps', form_steps)
                    steps_explicit = str(row.get('steps_explicit', form_steps_explicit)).strip().lower() in ('true', '1', 'yes')
                    g_in = _cell_float('guidance', form_guidance)
                    seed = int(row['seed']) if row.get('seed') and str(row.get('seed')).strip() else None

                    resolved = resolve_stills_defaults(
                        row_model,
                        width=w_in,
                        height=h_in,
                        steps=s_in,
                        steps_explicit=steps_explicit,
                        guidance=g_in,
                        replace_legacy_sd_markers=True,
                    )
                    width = (int(resolved["width"]) // 8) * 8
                    height = (int(resolved["height"]) // 8) * 8
                    steps = int(resolved["steps"])
                    guidance = float(resolved["guidance"])
                    
                    if width < 64:
                        width = 64
                    if height < 64:
                        height = 64
                        
                except (ValueError, TypeError) as e:
                    logger.warning(f"Row {i+1} has invalid numeric values, using family defaults: {e}")
                    steps_explicit = False
                    resolved = resolve_stills_defaults(row_model)
                    width = int(resolved["width"])
                    height = int(resolved["height"])
                    steps = int(resolved["steps"])
                    guidance = float(resolved["guidance"])
                    seed = None

                style = (row.get('style') or '').strip() or (form_style or 'realistic')
                prompt = BatchPrompt(
                    id=prompt_id,
                    prompt=prompt_text,
                    negative_prompt=row.get('negative_prompt', '').strip() if row.get('negative_prompt') else '',
                    style=style,
                    width=width,
                    height=height,
                    steps=steps,
                    guidance=guidance,
                    seed=seed,
                    model=row_model,
                    metadata={
                        'steps_explicit': steps_explicit,
                        'steps_requested': (form_steps_metadata or {}).get('steps_requested', resolved['steps_requested'])
                        if not (row.get('steps') or '').strip() else resolved['steps_requested'],
                        'steps_notice': resolved['steps_notice'] or (
                            (form_steps_metadata or {}).get('steps_notice')
                            if not (row.get('steps') or '').strip() else None
                        ),
                        'row_number': i + 1,
                        'original_row': dict(row)
                    }
                )
                prompts.append(prompt)

            if not prompts:
                raise ValueError("No valid prompts found in CSV. Ensure CSV has a 'prompt' column with non-empty values.")

        except csv.Error as e:
            logger.error(f"CSV parsing error: {e}")
            raise ValueError(f"Invalid CSV format: {str(e)}")
        except Exception as e:
            logger.error(f"Failed to parse CSV prompts: {e}", exc_info=True)
            raise ValueError(f"Invalid CSV format: {str(e)}")

        return prompts

    @staticmethod
    def _file_dimensions(image_path) -> Optional[str]:
        """``WxH`` of an image file from its header, or None when unreadable."""
        try:
            from PIL import Image
            with Image.open(image_path) as img:
                width, height = img.size
            return f"{width}x{height}"
        except Exception:  # noqa: BLE001 — a bad header is not a failed render
            return None

    def _create_thumbnail(self, image_path: str, thumbnail_dir: Path) -> Optional[str]:
        try:
            from PIL import Image

            with Image.open(image_path) as image:
                thumb_size = (256, 256)
                image.thumbnail(thumb_size, Image.Resampling.LANCZOS)

                # Ensure RGB for JPEG compatibility (handles RGBA/P modes from generators)
                if image.mode in ('RGBA', 'P'):
                    image = image.convert('RGB')

                thumb_filename = Path(image_path).stem + ".jpg"
                thumb_path = thumbnail_dir / thumb_filename
                image.save(thumb_path, "JPEG", quality=85)

            return str(thumb_path)

        except Exception as e:
            logger.warning(f"Failed to create thumbnail for {image_path}: {e}")
            # As last resort, do not block the result; serving has fallbacks
            return None

    def _cleanup_gpu_memory(self):
        try:
            import psutil
            proc = psutil.Process()
            rss_before = proc.memory_info().rss / (1024**3)
        except Exception:
            rss_before = 0.0

        try:
            if self.image_generator and hasattr(self.image_generator, "_unload_pipeline"):
                self.image_generator._unload_pipeline(forget_slot=True)
            import gc
            import torch
            gc.collect()
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                try:
                    torch.cuda.synchronize()
                except Exception:
                    pass
        except Exception:
            pass

        try:
            import psutil
            proc = psutil.Process()
            rss_after = proc.memory_info().rss / (1024**3)
            if rss_before > 0:
                logger.info(f"Batch cleanup: RSS {rss_before:.1f}GB -> {rss_after:.1f}GB")
        except Exception:
            pass

    def _keep_or_cleanup_gpu_memory(self, batch_status: BatchGenerationStatus) -> None:
        """End of a batch: keep the model loaded for the next one when Settings →
        Generation asks for it and the batch did not fail; otherwise unload."""
        minutes = 0
        if batch_status.status in ("completed", "cancelled"):
            try:
                from backend.utils.settings_utils import get_image_keep_loaded_minutes
                minutes = get_image_keep_loaded_minutes()
            except Exception:  # noqa: BLE001
                minutes = 0
        gen = self.image_generator
        if minutes > 0 and gen is not None and hasattr(gen, "keep_pipeline_for"):
            if gen.keep_pipeline_for(minutes * 60):
                return
        self._cleanup_gpu_memory()

    def _batch_running(self, batch_id: Optional[str] = None) -> bool:
        with self.batch_lock:
            if batch_id is not None:
                status = self.active_batches.get(batch_id)
                return bool(status is not None and status.status == "running")
            return any(s.status == "running" for s in self.active_batches.values())

    def release_batch_vram(self, batch_id: str) -> bool:
        """Orchestrator callback for the ``image_batch:<batch_id>`` booking.

        True means the booking may be dropped. A batch that is still running
        keeps it. For a finished batch the resident pipeline is unloaded too,
        unless another batch is mid-render on it.
        """
        if self._batch_running(batch_id):
            return False
        if not self._batch_running():
            self._cleanup_gpu_memory()
        return True

    def _release_batch_booking(self, batch_id: str) -> None:
        """A finished batch drops its own orchestrator booking; the enclosing
        gpu_session drops it again on exit, which is a no-op by then."""
        try:
            from backend.services.gpu_memory_orchestrator import get_orchestrator_if_created
            orchestrator = get_orchestrator_if_created()
            if orchestrator is not None:
                orchestrator.drop_booking(f"image_batch:{batch_id}")
        except Exception as e:  # noqa: BLE001
            logger.debug("drop_booking(image_batch:%s) failed: %s", batch_id, e)

    def _apply_stills_default_to_auto(self, request: BatchImageRequest) -> Optional[str]:
        """Give every Auto prompt in a batch the Stills model chosen in Settings >
        Generation, when it can run here; returns that key, or None when nothing
        changed (no choice made, or the choice is not installed, logged by
        configured_default_for_auto).

        Resolved once at intake so routing (a FLUX default goes to ComfyUI like
        an explicit flux-dev pick), the resource estimate, and the job record's
        model_used all name the same model. Cast prompts keep their own routing
        by LoRA family.
        """
        gen = self.image_generator
        resolver = getattr(gen, "configured_default_for_auto", None)
        if not callable(resolver):
            return None
        auto_prompts = [
            p for p in (request.prompts or [])
            if (p.model or "").strip().lower() in ("", "auto")
            and not getattr(p, "subject_ids", None)
            and not getattr(p, "loras", None)
        ]
        if not auto_prompts:
            return None
        try:
            preferred = resolver()
        except Exception as e:  # noqa: BLE001
            logger.debug("Stills default not consulted for batch Auto: %s", e)
            return None
        if not preferred:
            return None
        for p in auto_prompts:
            p.model = preferred
            note = self._resample_for_stills_default(p, preferred)
            if note:
                logger.info("Batch %s prompt %s: %s", request.batch_id, p.id, note)
        # The API serialises FLUX (force_max_workers) only for an explicit model
        # name; a substituted one needs the same.
        try:
            from backend.services.image_render_limits import limits_for
            force_w = limits_for(preferred).get("force_max_workers")
        except Exception:  # noqa: BLE001
            force_w = None
        if force_w and int(getattr(request, "max_workers", 0) or 0) > int(force_w):
            request.max_workers = int(force_w)
        logger.info(
            "Batch %s: %d Auto prompt(s) render with the Stills default %r from Settings",
            request.batch_id, len(auto_prompts), preferred,
        )
        return preferred

    @staticmethod
    def _resample_for_stills_default(p: BatchPrompt, key: str) -> Optional[str]:
        """Sampling sent for an Auto row is the product default family's recipe
        (the page mirrors Z-Image's presets for Auto: 9 steps, guidance 0). Under
        a chosen model of another family that recipe is a bad knob (FLUX.1 Dev at
        9 steps with guidance 0 badly under-resolves), so an untyped step count
        takes the chosen family's default and a guidance outside the model's
        range takes its default. A typed step count and an in-range guidance
        stand. Returns the note recorded on the prompt, or None when nothing
        changed (same family, or values the model can use).
        """
        from backend.services.image_render_limits import envelope_cfg
        from backend.services.media_model_registry import DEFAULT_STILLS_MODEL, get_profile
        from backend.services.stills_defaults import model_family, resolve_stills_defaults

        if model_family(key) == model_family(DEFAULT_STILLS_MODEL):
            return None
        own = resolve_stills_defaults(key)
        changes: List[str] = []
        if not p.metadata.get("steps_explicit") and int(p.steps or 0) != int(own["steps"]):
            changes.append(f"steps {p.steps} → {int(own['steps'])}")
            p.steps = int(own["steps"])
        guidance = envelope_cfg(key, p.guidance)
        if p.guidance is None or abs(float(p.guidance) - guidance) > 1e-6:
            changes.append(f"guidance {p.guidance} → {guidance:g}")
            p.guidance = guidance
        if not changes:
            return None
        label = (get_profile(key) or {}).get("name") or key
        note = f"Auto renders with {label} (Settings > Stills): " + ", ".join(changes) + "."
        prior = p.metadata.get("steps_notice")
        p.metadata["steps_notice"] = f"{prior} {note}" if prior else note
        return note

    def _resolve_batch_model_key(self, model_key: str, prompt_text: str = "") -> str:
        """Map a batch prompt model key to a catalog key for resource estimates."""
        if not model_key or model_key in ("auto", ""):
            gen = self.image_generator
            # Price the model the offline auto-router will render with.
            picked = gen._auto_select_model(prompt_text or "") if gen else None
            return picked or "zimage-turbo"
        return model_key

    @staticmethod
    def _character_model_key(prompt: BatchPrompt) -> str:
        """Catalog key a cast/LoRA prompt renders with, by the family of its first LoRA."""
        model_key = "zimage-turbo"
        try:
            from backend.services.media_model_registry import resolve_inference_for_loras
            paths = list(prompt.loras or [])
            if not paths and getattr(prompt, "subject_ids", None):
                from backend.models import Subject, db
                for sid in prompt.subject_ids:
                    s = db.session.get(Subject, int(sid))
                    if s and s.lora_path:
                        paths.append(s.lora_path)
            if paths:
                route = resolve_inference_for_loras(paths)
                if route.get("family") == "zimage":
                    model_key = "zimage-turbo"
                elif route.get("family") == "flux":
                    model_key = "flux-dev"
                else:
                    model_key = "sd-xl"
        except Exception:
            pass
        return model_key

    def _prompt_offline_model(self, prompt: BatchPrompt) -> Optional[str]:
        """Catalog id of the offline pipeline this prompt renders on; None for ComfyUI."""
        gen = self.image_generator
        if gen is None:
            return None
        if prompt.loras or getattr(prompt, "subject_ids", None):
            model_key = self._character_model_key(prompt)
            # Only Z-Image cast renders use the offline pipeline (character_still_pipeline).
            if model_key != "zimage-turbo" or self._zimage_via_comfyui_enabled():
                return None
        elif self._should_use_comfy_stills(prompt):
            return None
        elif self._zimage_via_comfyui_enabled() and self._is_zimage_model(prompt.model):
            return None
        else:
            model_key = self._resolve_batch_model_key(prompt.model, prompt.prompt)
        if self._comfy_t2i_family(model_key):
            return None
        return gen.available_models.get(model_key, model_key)

    def _kept_model_for_batch(self, request: BatchImageRequest) -> Optional[str]:
        """The model kept loaded after the last batch, when this batch renders only with it."""
        gen = self.image_generator
        kept = gen.kept_model() if gen is not None and hasattr(gen, "kept_model") else None
        if not kept or not request.prompts:
            return None
        if all(self._prompt_offline_model(p) == kept for p in request.prompts):
            return kept
        return None

    def _batch_resource_estimates(
        self, request: BatchImageRequest, reuse_model: Optional[str] = None
    ) -> Tuple[int, float]:
        """Worst-case (vram_mb, ram_gb) across offline-generator prompts in this batch.

        ``reuse_model`` is the kept model this batch will render with
        (_kept_model_for_batch); a kept model it will not use is unloaded before
        admission, so it earns no discount.
        """
        if not self.image_generator:
            return 4000, 6.0
        gen = self.image_generator
        vram_mb = 4000
        ram_gb = 6.0
        kept = gen.kept_model() if hasattr(gen, "kept_model") else None
        for prompt in request.prompts:
            if prompt.loras or getattr(prompt, "subject_ids", None):
                model_key = self._character_model_key(prompt)
            else:
                model_key = self._resolve_batch_model_key(prompt.model, prompt.prompt)
            comfy_family = self._comfy_t2i_family(model_key)
            if comfy_family:
                # ComfyUI stills are priced flat by their family row (FLUX-dev fp8
                # ~12GB with T5); the registry prices Qwen-Image 2.1. Workers are
                # serialized elsewhere.
                fam_vram, fam_ram = self._comfy_t2i_price(comfy_family)
                vram_mb = max(vram_mb, fam_vram)
                ram_gb = max(ram_gb, fam_ram)
                continue
            catalog_id = gen.available_models.get(model_key, model_key)

            # Resolution-aware estimates (2026-08-04): clamp the prompt's dims the
            # same way generation will, then let the estimators price the extra
            # megapixels. A 2048² prompt must raise the batch booking above the
            # flat 1024²-calibrated constants.
            pw = getattr(prompt, "width", None)
            ph = getattr(prompt, "height", None)
            try:
                if pw and ph:
                    from backend.services.image_resolution_limits import clamp_image_dimensions
                    pw, ph, _ = clamp_image_dimensions(pw, ph, gen._model_family(catalog_id))
            except Exception:
                pw = ph = None

            # If the model is already loaded (resident) and stays for this batch, its
            # weights are already counted in the system's used RAM, so only the
            # surcharge above 1MP is new. VRAM is discounted only for a pipeline that
            # sits wholly on the card: an offloaded one (Z-Image and Krea 2 on 16GB
            # cards) keeps its weights in RAM and needs its full working peak again.
            resident = (
                getattr(gen, "_pipeline", None) is not None
                and getattr(gen, "_current_model", None) == catalog_id
                and (kept is None or kept == reuse_model)
            )
            if resident:
                if getattr(gen, "_pipeline_offload_mode", None) == "full":
                    model_vram = 1024 + max(0, gen._vram_estimate_mb(catalog_id, pw, ph) - gen._vram_estimate_mb(catalog_id))
                else:
                    model_vram = gen._vram_estimate_mb(catalog_id, pw, ph)
                model_ram = 2.0 + max(0.0, gen._ram_estimate_gb(catalog_id, pw, ph) - gen._ram_estimate_gb(catalog_id))
            else:
                model_vram = gen._vram_estimate_mb(catalog_id, pw, ph)
                model_ram = gen._ram_estimate_gb(catalog_id, pw, ph)

            vram_mb = max(vram_mb, model_vram)
            ram_gb = max(ram_gb, model_ram)
        return vram_mb, ram_gb

    def _gpu_fault_message(self) -> Optional[str]:
        """The offline generator's recorded GPU fault, or None."""
        getter = getattr(self.image_generator, "gpu_fault_message", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            return None

    def _batch_uses_cuda_offline_gen(self) -> bool:
        return bool(
            self.image_generator
            and hasattr(self.image_generator, "_device")
            and self.image_generator._device == "cuda"
        )

    def _fail_batch_gpu_busy(
        self,
        batch_id: str,
        batch_status: BatchGenerationStatus,
        output_dir: Path,
        request: BatchImageRequest,
        message: str,
    ) -> None:
        logger.error(f"Batch {batch_id} blocked: {message}")
        batch_status.status = "error"
        batch_status.error = message
        batch_status.end_time = datetime.now()
        if request.save_metadata:
            try:
                self._save_batch_metadata(batch_status, output_dir)
            except Exception:
                pass
        if self.progress_system:
            try:
                self.progress_system.error_process(
                    process_id=batch_id,
                    message=message,
                    additional_data={"batch_id": batch_id},
                )
            except Exception:
                pass

    def _report_gpu_wait(
        self,
        batch_id: str,
        batch_status: BatchGenerationStatus,
        refusal: BaseException,
        need_mb: int,
        reserve_mb: int = 0,
    ) -> str:
        """Mark the batch as waiting for the GPU and tell the progress feed why.

        The text names the job holding the card when the refusal says who it is,
        so the footer and queue panel read "Queued behind Video Gen" rather than
        "Starting". Returns the message.
        """
        from backend.services.gpu_resource_policy import vram_probe_snapshot
        from backend.services.job_operation_gate import gpu_wait_message

        try:
            snap = vram_probe_snapshot(reserve_mb=reserve_mb)
        except Exception:  # noqa: BLE001 — the wait text is still useful without a probe
            snap = {}
        wait_msg = gpu_wait_message(refusal, snap.get("free_mb"), need_mb)
        batch_status.gpu_wait_reason = wait_msg
        if batch_status.status not in ("running", "queued", "pending"):
            batch_status.status = "queued"
        if self.progress_system:
            try:
                self.progress_system.update_process(
                    process_id=batch_id,
                    progress=0,
                    message=wait_msg,
                    additional_data={
                        "batch_id": batch_id,
                        "gpu_wait_reason": wait_msg,
                        "vram_free_mb": snap.get("free_mb"),
                        "vram_need_mb": need_mb,
                    },
                )
            except Exception:
                pass
        return wait_msg

    def _finish_cancelled_before_start(
        self,
        batch_id: str,
        batch_status: BatchGenerationStatus,
        output_dir: Path,
        request: BatchImageRequest,
        reason: str,
    ) -> None:
        """Terminal bookkeeping for a batch cancelled before any image ran.

        The progress process is created at the top of run_batch(), before the
        VRAM wait, so a cancel there has to close it too or the socket side
        never learns the batch ended.
        """
        batch_status.status = "cancelled"
        if not batch_status.error:
            batch_status.error = reason
        batch_status.end_time = datetime.now()
        batch_status.gpu_wait_reason = None
        if request.save_metadata:
            try:
                self._save_batch_metadata(batch_status, output_dir)
            except Exception:
                pass
        if self.progress_system:
            try:
                self.progress_system.cancel_process(
                    process_id=batch_id,
                    message=f"Batch generation cancelled: {reason}",
                    additional_data={
                        "batch_id": batch_id,
                        "completed": batch_status.completed_images,
                        "failed": batch_status.failed_images,
                        "total": batch_status.total_images,
                    },
                )
            except Exception:
                pass

    def _finish_refused_before_start(
        self,
        batch_id: str,
        batch_status: BatchGenerationStatus,
        output_dir: Path,
        request: BatchImageRequest,
        reason: str,
    ) -> None:
        """Terminal bookkeeping for a batch none of whose images can run, decided
        before the GPU session so nothing was evicted for it."""
        batch_status.status = "error"
        batch_status.error = reason
        batch_status.error_kind = "model_not_installed"
        batch_status.failed_images = batch_status.total_images
        batch_status.end_time = datetime.now()
        batch_status.gpu_wait_reason = None
        if request.save_metadata:
            try:
                self._save_batch_metadata(batch_status, output_dir)
            except Exception:
                pass
        if self.progress_system:
            try:
                self.progress_system.error_process(
                    process_id=batch_id,
                    message=f"Batch generation error: {reason}",
                    additional_data={"batch_id": batch_id, "error": reason},
                )
            except Exception:
                pass

    def _missing_offline_model(self, prompt: BatchPrompt) -> Optional[str]:
        """The install hint when this prompt would render offline with a model that
        is not installed, else None. Cast characters and ComfyUI routes have their
        own weights and are never refused here."""
        if getattr(prompt, "subject_ids", None) or getattr(prompt, "loras", None):
            return None
        if self._should_use_comfy_stills(prompt):
            return None
        if self._zimage_via_comfyui_enabled() and self._is_zimage_model(prompt.model):
            return None
        gen = self.image_generator
        if gen is None or not hasattr(gen, "missing_model_message"):
            return None
        return gen.missing_model_message(prompt.model or "auto")

    @staticmethod
    def _comfy_t2i_family(model_key: str | None) -> str | None:
        """The ComfyUI text-to-image family a model key renders on ("flux",
        "qwen-image-21", "moss-image"), or None for offline models and Comfy families without
        a stills graph (user_model_families.comfy_t2i_family)."""
        from backend.services.user_model_families import comfy_t2i_family
        return comfy_t2i_family(model_key)

    @staticmethod
    def _comfy_t2i_price(family: str) -> Tuple[int, float]:
        """(vram_mb, ram_gb) a ComfyUI stills family is admitted at: its
        IMAGE_FAMILY_SPECS row, which for Qwen-Image 2.1 reads the registry."""
        from backend.services.image_render_limits import limits_for
        lim = limits_for(family)
        return int(lim.get("vram_mb") or 12000), float(lim.get("ram_gb") or 16.0)

    def _should_use_comfy_stills(self, prompt: BatchPrompt) -> bool:
        if self._comfy_t2i_family(prompt.model):
            return True
        adapters = getattr(prompt, "adapter_loras", None) or []
        if not adapters:
            return False
        try:
            from backend.services.stills_defaults import model_family
            return model_family(prompt.model) in ("sdxl", "flux")
        except Exception:
            return False

    def _comfy_t2i_target(self, model_key: str | None) -> Optional[dict]:
        """{family, tag, unet} for a ComfyUI stills model: a user entry's own
        UNET and the graph it renders on (user_image_models.comfy_t2i_target)."""
        family = self._comfy_t2i_family(model_key)
        if not family:
            return None
        shipped = {"family": family, "tag": (model_key or "").strip(), "unet": None}
        if not (model_key or "").strip().startswith("user-"):
            return shipped
        try:
            from backend.services.user_image_models import comfy_t2i_target
            gen = getattr(self, "image_generator", None)
            if gen is None:
                from backend.services.offline_image_generator import get_image_generator
                gen = get_image_generator()
            return comfy_t2i_target(gen, model_key) or shipped
        except Exception:
            return shipped

    def _generate_with_comfy_flux(self, prompt: BatchPrompt, batch_id: str = "") -> Optional[ImageGenerationResult]:
        """ComfyUI stills (FLUX, Qwen-Image 2.1) or SDXL+LoRA via ComfyUI, user-added weights included."""
        try:
            from backend.services.comfyui_image_generator import ComfyUIImageGenerator
        except Exception as e:
            logger.warning("ComfyUI stills path unavailable: %s", e)
            return ImageGenerationResult(
                success=False,
                error=f"ComfyUI stills unavailable: {e}",
                prompt_used=prompt.prompt,
            )

        target = self._comfy_t2i_target(prompt.model)
        family = (target or {}).get("family")
        width = prompt.width if prompt.width and prompt.width >= 512 else 1024
        height = prompt.height if prompt.height and prompt.height >= 512 else 1024
        if family:
            if prompt.width and prompt.width >= 768:
                width = prompt.width
            if prompt.height and prompt.height >= 768:
                height = prompt.height
            try:
                from backend.services.image_resolution_limits import clamp_image_dimensions
                ow, oh = width, height
                width, height, warns = clamp_image_dimensions(width, height, family)
                for msg in warns:
                    logger.warning("%s batch: %s", family, msg)
                if (width, height) != (ow, oh):
                    logger.info("%s batch resolution %sx%s → %sx%s", family, ow, oh, width, height)
            except Exception as e:
                logger.debug("%s dim clamp skipped: %s", family, e)
        # Defaults when the prompt carries none are the family row's (FLUX 28 /
        # FluxGuidance 3.5, Qwen-Image 2.1 25 / cfg 1.0 from the official template).
        from backend.services.image_render_limits import defaults_for
        own = defaults_for(family or "flux-dev")
        steps = int(prompt.steps if prompt.metadata.get("steps_explicit", False) else (prompt.steps or own["steps"]))
        # FLUX: the FluxGuidance value (user "guidance" slider), KSampler cfg stays 1.0
        # inside Comfy. Qwen-Image 2.1: the KSampler cfg itself.
        guidance = float(prompt.guidance) if prompt.guidance is not None else float(own["guidance"])

        import tempfile, os as _os, time as _time
        out_path = _os.path.join(
            tempfile.gettempdir(), f"{(family or 'comfy').replace('-', '_')}_{prompt.id}_{int(_time.time() * 1000)}.png"
        )
        # FLUX is deterministic: identical prompt + identical seed = identical
        # pixels. This branch used to pin the seed to a literal 42 whenever the
        # caller supplied none, so a batch of N prompts burned full GPU time
        # rendering N copies of the same image. Derive per-slot instead.
        seed = resolve_image_seed(prompt, batch_id)
        loras = list(getattr(prompt, "adapter_loras", None) or [])
        unet = (target or {}).get("unet")
        if not family:
            model_tag = "sdxl"
        elif family == "flux":
            # Shipped FLUX ids render on the dev graph; a user .gguf on schnell.
            model_tag = target["tag"] if unet else "flux-dev"
        else:
            model_tag = target["tag"]
        picked_encoders = dict(getattr(prompt, "text_encoders", None) or {})
        try:
            gen = ComfyUIImageGenerator(
                model=model_tag,
                lora_strength=float(getattr(prompt, "adapter_scale", 0.8) or 0.8),
                flux_unet=unet if model_tag == "flux-schnell" else None,
                flux_dev_unet=unet if model_tag == "flux-dev" else None,
                qwen21_unet=unet if family == "qwen-image-21" else None,
                moss_unet=unet if family == "moss-image" else None,
                text_encoders=picked_encoders,
            )
            path = gen.generate_image(
                prompt=prompt.prompt,
                loras=loras,
                output_path=out_path,
                width=width,
                height=height,
                negative_prompt=prompt.negative_prompt or None,
                seed=seed,
                steps=steps,
                steps_explicit=prompt.metadata.get("steps_explicit", False),
                cfg=guidance,
                model=model_tag,
            )
            return ImageGenerationResult(
                success=True,
                image_path=path,
                prompt_used=prompt.prompt,
                model_used=prompt.model or model_tag,
                image_size=(width, height),
                # The seed actually rendered, not the (often None) requested one —
                # otherwise the batch cannot report or reproduce its own output.
                seed_used=seed,
                metadata={
                    "steps": getattr(gen, "last_steps", steps),
                    **({"text_encoders": picked_encoders} if picked_encoders else {}),
                },
            )
        except Exception as e:
            logger.error("ComfyUI %s batch generation failed: %s", model_tag, e)
            return ImageGenerationResult(
                success=False, error=_error_with_kind(e), prompt_used=prompt.prompt
            )

    # ── Z-Image via ComfyUI (optional; GUAARDVARK_ZIMAGE_USE_COMFYUI=1) ────────
    # When enabled, plain Z-Image generation routes to the reachable ComfyUI
    # (reusing its installed z_image_turbo_bf16.safetensors) instead of Guaardvark's
    # offline diffusers pipeline, so no separate model download is needed.
    @staticmethod
    def _zimage_via_comfyui_enabled() -> bool:
        from backend.services.stills_pipeline import zimage_via_comfyui_enabled

        return zimage_via_comfyui_enabled()

    @staticmethod
    def _is_zimage_model(model_key: str | None) -> bool:
        _k = (model_key or "").strip().lower()
        return _k in ("zimage", "zimage-turbo", "z-image", "z_image_turbo") \
            or "zimage" in _k or "z-image" in _k

    def _generate_with_comfyui_zimage(self, prompt: BatchPrompt) -> Optional[ImageGenerationResult]:
        """Generate a plain Z-Image via the reachable ComfyUI instead of the offline
        diffusers pipeline. Reuses the Z-Image model already installed in ComfyUI."""
        try:
            from backend.services.comfyui_image_generator import ComfyUIImageGenerator
        except Exception as e:
            logger.warning("ComfyUI Z-Image path unavailable: %s", e)
            return ImageGenerationResult(
                success=False,
                error=f"ComfyUI Z-Image unavailable: {e}",
                prompt_used=prompt.prompt,
            )

        width = max(int(prompt.width or 1024), 512)
        height = max(int(prompt.height or 1024), 512)
        steps = int(prompt.steps or 9)
        # ComfyUI KSampler needs cfg >= 1.0. Guaardvark's OFFLINE Z-Image path uses
        # guidance 0 (diffusers reads that as "no CFG" for a distilled model), but
        # passing cfg=0 into ComfyUI's KSampler yields ZERO conditioning -> output
        # that ignores the prompt. So map 0/None up to a real cfg (default 1.0, or
        # GUAARDVARK_ZIMAGE_CFG). Explicit guidance >= 1.0 is honored as-is.
        _cfg_default = float(os.environ.get("GUAARDVARK_ZIMAGE_CFG", "1.0"))
        if prompt.guidance is not None and float(prompt.guidance) >= 1.0:
            guidance = float(prompt.guidance)
        else:
            guidance = _cfg_default
        # A seed per image, as on the FLUX path; a fixed fallback rendered every
        # unseeded prompt in a batch with the same noise.
        seed = resolve_image_seed(prompt)

        import tempfile
        out_path = os.path.join(
            tempfile.gettempdir(), f"zimage_{prompt.id}_{int(time.time() * 1000)}.png"
        )
        try:
            gen = ComfyUIImageGenerator(model="zimage")
            path = gen.generate_image(
                prompt=prompt.prompt,
                loras=[],
                output_path=out_path,
                width=width,
                height=height,
                negative_prompt=prompt.negative_prompt or "",
                seed=seed,
                steps=steps,
                cfg=guidance,
                model="zimage",
            )
            return ImageGenerationResult(
                success=True,
                image_path=path,
                prompt_used=prompt.prompt,
                model_used="zimage",
                image_size=(width, height),
                seed_used=seed,
            )
        except Exception as e:
            logger.error("ComfyUI Z-Image batch generation failed: %s", e)
            return ImageGenerationResult(
                success=False, error=_error_with_kind(e), prompt_used=prompt.prompt
            )

    def _generate_with_character_lora(self, prompt: BatchPrompt) -> Optional[ImageGenerationResult]:
        """Generate one image with cast character LoRA(s) via character_still_pipeline.

        Prefer subject_ids so the pipeline applies identity core (trigger + class +
        short marks) and routes by train base — same contract as Cast generate.
        """
        sid_list = [int(x) for x in (prompt.subject_ids or []) if str(x).strip()]
        lora_list = list(prompt.loras or [])
        if not sid_list and not lora_list:
            return None

        # Scene prompt only — do not bare-prepend trigger; pipeline owns lock.
        final_prompt = (prompt.prompt or "").strip()

        width = prompt.width if prompt.width and prompt.width >= 768 else 1024
        height = prompt.height if prompt.height and prompt.height >= 768 else 1024

        import tempfile, os as _os, time as _time
        out_path = _os.path.join(
            tempfile.gettempdir(), f"char_{prompt.id}_{int(_time.time() * 1000)}.png"
        )

        try:
            from backend.services.character_still_pipeline import render_character_still
            # Always pass both: subject_ids for identity core; lora_paths as
            # fallback when worker DB resolve fails (paths were resolved at API time).
            # Steps and guidance are locked to the character's base model defaults
            # (image_render_limits), whatever the page sent; the page's values apply
            # to renders without a character.
            still = render_character_still(
                final_prompt,
                subject_ids=sid_list or None,
                lora_paths=lora_list or None,
                include_bible=True,  # vision bible pins costume (armor, emblem, …)
                source="batch",
                width=width,
                height=height,
                steps=None,
                steps_explicit=False,
                guidance=None,
                seed=prompt.seed,
                negative_prompt=prompt.negative_prompt or "",
                output_path=out_path,
                style=prompt.style or "realistic",
                keep_pipeline=True,
                image_model=prompt.model,
            )
            meta = still.metadata or {}
            logger.info(
                "Batch character still: success=%s family=%s strength=%s lock=%r model=%s",
                still.success,
                meta.get("family"),
                meta.get("lora_strength"),
                (meta.get("lock_prefix") or "")[:120],
                still.model_used,
            )
            if not still.success:
                return ImageGenerationResult(
                    success=False, error=still.error or "character still failed",
                    prompt_used=still.prompt_used or final_prompt,
                )
            return ImageGenerationResult(
                success=True,
                image_path=still.image_path,
                prompt_used=still.prompt_used or final_prompt,
                model_used=still.model_used or "character+lora",
                image_size=(still.width or width, still.height or height),
                seed_used=still.seed_used if still.seed_used is not None else prompt.seed,
                metadata={**meta, "steps": still.steps},
            )
        except Exception as e:
            logger.error(f"Character LoRA generation failed: {e}")
            return ImageGenerationResult(success=False, error=_error_with_kind(e), prompt_used=final_prompt)

    def _generate_infographic(self, prompt: BatchPrompt, batch_id: str) -> ImageGenerationResult:
        """One infographic, rendered as the Infographic page's single render
        is (InfographicGenerator.render_still). The batch's GPU session is
        already held, as on the plain stills path."""
        from backend.services.infographic_generator import InfographicGenerator

        seed = resolve_image_seed(prompt, batch_id)
        try:
            still = InfographicGenerator.render_still(
                prompt.prompt, model_id=prompt.model, width=prompt.width, height=prompt.height,
                seed=seed, hold_gpu=False, keep_pipeline=True,
            )
        except Exception as e:  # noqa: BLE001 — the image fails; the batch goes on
            logger.error(f"Infographic render failed for prompt {prompt.id}: {e}")
            return ImageGenerationResult(success=False, error=_error_with_kind(e), prompt_used=prompt.prompt)
        return ImageGenerationResult(
            success=True,
            image_path=still.image_path,
            prompt_used=still.prompt_used or prompt.prompt,
            model_used=still.model_used or prompt.model,
            generation_time=still.generation_time,
            image_size=(still.width, still.height),
            seed_used=still.seed_used if still.seed_used is not None else seed,
            metadata={**(still.metadata or {}), "steps": still.steps, "guidance": still.guidance},
        )

    def _generate_single_image(self, batch_id: str, prompt: BatchPrompt, output_dir: Path,
                             batch_status: BatchGenerationStatus) -> BatchImageResult:
        start_time = time.time()

        try:
            restore_faces = getattr(batch_status, 'restore_faces', False)
            face_restoration_weight = getattr(batch_status, 'face_restoration_weight', 0.5)
            remove_background = getattr(batch_status, 'remove_background', False)

            # Infographics first: their own measured render, whatever engine
            # the model would otherwise route to.
            if (prompt.metadata or {}).get("render") == "infographic":
                result = self._generate_infographic(prompt, batch_id)
            # Character casting: subject_ids / LoRAs → character_still_pipeline
            # (Z-Image offline or Comfy SDXL/FLUX by train base).
            elif getattr(prompt, "subject_ids", None) or getattr(prompt, "loras", None):
                result = self._generate_with_character_lora(prompt)
            elif self._should_use_comfy_stills(prompt):
                result = self._generate_with_comfy_flux(prompt, batch_id)
            elif self._zimage_via_comfyui_enabled() and self._is_zimage_model(prompt.model):
                result = self._generate_with_comfyui_zimage(prompt)
            else:
                result = None

            if result is None:
                # Shared stills façade (same sanitize/defaults/enhance policy as chat).
                # hold_gpu=False: batch-level gpu_session + adopt_gpu_session already held.
                from backend.services.stills_pipeline import run_stills_pipeline
                enhance = "offline" if prompt.auto_enhance else "none"
                stills = run_stills_pipeline(
                    [prompt.prompt],
                    model=prompt.model or "auto",
                    width=prompt.width,
                    height=prompt.height,
                    steps=prompt.steps,
                    steps_explicit=prompt.metadata.get("steps_explicit", False),
                    guidance=prompt.guidance,
                    style=prompt.style or "realistic",
                    negative_prompt=prompt.negative_prompt or "",
                    seed=prompt.seed,
                    source="batch",
                    enhance=enhance,
                    director=False,  # director already applied at batch level
                    auto_enhance=prompt.auto_enhance,
                    keep_pipeline=True,
                    output="path",
                    allow_model_download=getattr(batch_status, "allow_model_download", False),
                    content_preset=prompt.content_preset,
                    enhance_anatomy=prompt.enhance_anatomy,
                    enhance_faces=prompt.enhance_faces,
                    enhance_hands=prompt.enhance_hands,
                    restore_faces=restore_faces,
                    face_restoration_weight=face_restoration_weight,
                    remove_background=remove_background,
                    hold_gpu=False,
                    replace_legacy_sd_markers=True,
                    loras=list(getattr(prompt, "adapter_loras", None) or []) or None,
                    lora_scale=float(getattr(prompt, "adapter_scale", 0.8) or 0.8),
                )
                still = stills[0] if stills else None
                if still and still.success:
                    result = ImageGenerationResult(
                        success=True,
                        image_path=still.image_path,
                        prompt_used=still.prompt_used,
                        negative_prompt_used=still.negative_used,
                        model_used=still.model_used,
                        generation_time=still.generation_time,
                        image_size=(still.width, still.height),
                        seed_used=still.seed_used,
                        metadata={**still.metadata, "steps": still.steps},
                    )
                else:
                    result = ImageGenerationResult(
                        success=False,
                        error=(still.error if still else "stills pipeline failed"),
                        prompt_used=prompt.prompt,
                    )

            # Shared failure/success handling for BOTH paths. (The old code treated
            # every non-None character-LoRA result as a failure — even successful
            # ones — and dereferenced result.image_path without checking success.)
            if not result.success or not result.image_path:
                error_msg = result.error or "Unknown generation error"
                logger.error(f"Image generation failed for prompt {prompt.id}: {error_msg}")

                return BatchImageResult(
                    prompt_id=prompt.id,
                    success=False,
                    generation_time=time.time() - start_time,
                    error=error_msg,
                    error_kind=_kind_of(result.error),
                )

            image_ext = Path(result.image_path).suffix or ".png"
            # Bates-stamped filename: ImageGen_04-02-2026_001.png
            from backend.services.output_registration import bates_name
            image_filename = bates_name("image", image_ext, output_dir / "images")
            target_path = output_dir / "images" / image_filename

            import shutil
            shutil.move(result.image_path, target_path)

            # The metadata names the size the FILE has. A generator can render
            # another canvas than it was asked for (text-mode enlargement did so
            # on 2026-09-12: 960x544 requested, 1024x1024 written, metadata
            # said 960x544), and the record is what people trust.
            requested = f"{prompt.width}x{prompt.height}"
            actual = self._file_dimensions(target_path) or requested
            if actual != requested:
                logger.warning(
                    "Batch %s prompt %s: asked for %s, the file is %s",
                    batch_id, prompt.id, requested, actual,
                )

            thumbnail_path = None
            if batch_status and hasattr(batch_status, 'generate_thumbnails') and batch_status.generate_thumbnails:
                thumbnail_path = self._create_thumbnail(str(target_path), output_dir / "thumbnails")

            prompt_record = self._prompt_record(prompt)
            typed_prompt = prompt_record["original_prompt"]

            if self.progress_system:
                completed = batch_status.completed_images + 1
                progress = int((completed / batch_status.total_images) * 100)

                self.progress_system.update_process(
                    process_id=batch_id,
                    progress=progress,
                    message=f"Generated image {completed}/{batch_status.total_images}: {typed_prompt[:50]}...",
                    additional_data={
                        "batch_id": batch_id,
                        "generated_count": completed,
                        "target_count": batch_status.total_images,
                        "completed": completed,
                        "total": batch_status.total_images,
                        "current_prompt": typed_prompt[:100]
                    }
                )

            return BatchImageResult(
                prompt_id=prompt.id,
                success=True,
                image_path=str(target_path),
                thumbnail_path=thumbnail_path,
                generation_time=time.time() - start_time,
                metadata={
                    **prompt_record,
                    "style": prompt.style,
                    "dimensions": actual,
                    **({"dimensions_requested": requested} if actual != requested else {}),
                    "steps": (result.metadata or {}).get("steps", prompt.steps),
                    "steps_requested": prompt.metadata.get(
                        "steps_requested", (result.metadata or {}).get("steps_requested", prompt.steps)),
                    "steps_notice": (result.metadata or {}).get("steps_notice") or prompt.metadata.get("steps_notice"),
                    # A prompt left to the model's recipe names what it ran with.
                    "guidance": prompt.guidance if prompt.guidance is not None
                    else (result.metadata or {}).get("guidance"),
                    "seed_used": result.seed_used,
                    "model_used": result.model_used,
                    # The loader files of any text encoders picked in place of
                    # the shipped ones, {slot: file}.
                    **({"text_encoders": result.metadata["text_encoders"]}
                       if (result.metadata or {}).get("text_encoders") else {}),
                    **({"infographic": prompt.metadata["infographic"]}
                       if (prompt.metadata or {}).get("infographic") else {}),
                }
            )

        except Exception as e:
            logger.error(f"Exception during image generation for prompt {prompt.id}: {e}")
            return BatchImageResult(
                prompt_id=prompt.id,
                success=False,
                generation_time=time.time() - start_time,
                error=str(e),
                error_kind=_kind_of(e),
            )
        finally:
            # An exception raised here would replace the function's return value
            # with a traceback, so the CUDA cache flush is best-effort.
            import gc
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
        # Pipeline unload is deferred to batch completion (_cleanup_gpu_memory at
        # run_batch end). Per-image cleanup defeated keep_pipeline_loaded and caused
        # full Z-Image reload + RAM spike → OOM on multi-image batches.

    def _save_batch_metadata(self, batch_status: BatchGenerationStatus, output_dir: Path):
        try:
            metadata = {
                "batch_id": batch_status.batch_id,
                "kind": batch_status.kind,
                "display_name": getattr(batch_status, "display_name", None),
                "status": batch_status.status,
                "error": str(batch_status.error) if batch_status.error else None,
                "error_kind": batch_status.error_kind,
                "total_images": batch_status.total_images,
                "completed_images": batch_status.completed_images,
                "failed_images": batch_status.failed_images,
                "start_time": batch_status.start_time.isoformat() if batch_status.start_time else None,
                "end_time": batch_status.end_time.isoformat() if batch_status.end_time else None,
                "retry_data": getattr(batch_status, "retry_data", None),
                "generation_summary": {
                    "total_generation_time": sum(r.generation_time for r in batch_status.results),
                    "successful_images": [r for r in batch_status.results if r.success],
                    "failed_images": [r for r in batch_status.results if not r.success]
                },
                "results": [
                    {
                        "prompt_id": r.prompt_id,
                        "success": r.success,
                        "status": "completed" if r.success else "failed",
                        "image_path": r.image_path,
                        "thumbnail_path": r.thumbnail_path,
                        "generation_time": r.generation_time,
                        "error": r.error,
                        "error_kind": r.error_kind,
                        "metadata": r.metadata
                    }
                    for r in batch_status.results
                ]
            }

            metadata_file = output_dir / "batch_metadata.json"
            # Atomic write to prevent partial reads during incremental saves
            tmp_file = metadata_file.with_suffix('.json.tmp')
            with open(tmp_file, 'w') as f:
                json.dump(metadata, f, indent=2, default=str)
            tmp_file.replace(metadata_file)

            logger.info(f"Batch metadata saved to {metadata_file}")

        except Exception as e:
            logger.error(f"Failed to save batch metadata: {e}")

    @staticmethod
    def _remember_typed_prompt(prompt: BatchPrompt) -> None:
        """Keep what the person typed before a rewrite replaces ``prompt.prompt``.

        The first rewrite wins: a storyboard pass followed by a director pass
        still names the image by the typed text."""
        if prompt.metadata is None:
            prompt.metadata = {}
        prompt.metadata.setdefault("typed_prompt", (prompt.prompt or "").strip())

    @staticmethod
    def _prompt_record(prompt: BatchPrompt) -> Dict[str, Any]:
        """The prompt keys of an image's metadata.

        ``original_prompt`` is what the person typed, which is what captions,
        the lightbox title and the registered document show. When a chat-model
        rewrite replaced it, ``rewritten_prompt`` carries the text that went on
        to the generator, for the details view."""
        typed = ((prompt.metadata or {}).get("typed_prompt") or prompt.prompt or "").strip()
        sent = (prompt.prompt or "").strip()
        record: Dict[str, Any] = {"original_prompt": typed or sent}
        if typed and sent and sent != typed:
            record["rewritten_prompt"] = sent
        return record

    def _apply_director(self, request: BatchImageRequest) -> None:
        """If director_mode or storyboard_concept, rewrite prompts via Media Director.

        Mutates in place and disables per-prompt offline auto_enhance on the prompts
        the director rewrote (those are already full visual prompts). Uses
        stills_policy for the enhance ladder so chat/batch share the same director
        behavior. Never raises.
        """
        if not MEDIA_DIRECTOR_AVAILABLE:
            return
        try:
            from backend.services.stills_policy import (
                apply_enhance_to_prompts,
                prompt_style_for_model,
                resolve_enhance_mode,
            )

            # BatchImageRequest has no style field of its own; the page applies
            # one style to every row, so the first prompt's style stands for
            # the batch. (Reading request.style here always yielded "".)
            batch_style = next(
                (bp.style for bp in request.prompts if (getattr(bp, "style", "") or "").strip()),
                "",
            )

            if getattr(request, "storyboard_concept", None):
                concept = (request.storyboard_concept or "").strip()
                n = len(request.prompts)
                if concept and n > 0:
                    from backend.services.media_director import storyboard_from_concept
                    res = storyboard_from_concept(
                        concept, n,
                        style=batch_style,
                        extra_guidance=getattr(request, "director_guidance", None),
                    )
                    shots = res.get("prompts") or []
                    # The storyboard's fallback is "the concept, n times": a row
                    # that gets only the concept, or its own text back, keeps the
                    # per-prompt enhancer (the batch-video rule).
                    changed = 0
                    for i, p in enumerate(request.prompts):
                        shot = (shots[i] or "").strip() if i < len(shots) else ""
                        if not shot:
                            continue
                        if shot != concept and shot != (p.prompt or "").strip():
                            p.auto_enhance = False
                            changed += 1
                        self._remember_typed_prompt(p)
                        p.prompt = shot
                    if changed:
                        request.auto_enhance = False
                        logger.info(
                            "Media Director storyboard expanded %s/%s prompts for batch %s",
                            changed, n, request.batch_id,
                        )
                    else:
                        logger.warning(
                            "Media Director storyboard returned only the concept for batch %s; "
                            "the per-prompt enhancer stays as it was",
                            request.batch_id,
                        )
                    return

            # A LoRA's trigger word and wording are written for the LoRA; the
            # rewrite drops the trigger and restyles the scene (a gouache LoRA
            # came back as "a realistic photograph"), so LoRA prompts go as typed.
            if any(getattr(bp, "adapter_loras", None) for bp in request.prompts):
                logger.info("Batch %s carries LoRAs; prompts go to the model as typed", request.batch_id)
                return

            # Model is chosen per prompt; the first explicit one stands for the
            # batch (the page sets one model for all rows).
            batch_model = next(
                (bp.model for bp in request.prompts if (getattr(bp, "model", "") or "").strip()),
                "auto",
            )
            mode = resolve_enhance_mode(
                director=bool(getattr(request, "director_mode", False)),
                auto_enhance=getattr(request, "auto_enhance", True),
                model=batch_model,
            )
            if mode != "director":
                return

            raw = [bp.prompt for bp in request.prompts if (bp.prompt or "").strip()]
            if not raw:
                return
            # One director attempt. enhance_prompts already walks its model ladder
            # and hands the originals back when none answers, so a second call
            # would only repeat the same wait.
            directed = apply_enhance_to_prompts(
                raw, enhance_mode="director", style=batch_style,
                extra_guidance=getattr(request, "director_guidance", None),
                model=batch_model,
            )
            # Only a prompt the director actually rewrote turns the per-prompt
            # enhancer off; an unchanged one keeps it, so a batch whose director
            # fell back still gets the family's normal enhancement.
            idx = 0
            changed = 0
            for bp in request.prompts:
                if (bp.prompt or "").strip() and idx < len(directed):
                    newp = (directed[idx] or "").strip()
                    if newp and newp != (bp.prompt or "").strip():
                        self._remember_typed_prompt(bp)
                        bp.prompt = newp
                        bp.auto_enhance = False
                        changed += 1
                    idx += 1
            if changed:
                request.auto_enhance = False
                logger.info(
                    "Media Director enhanced %s/%s prompts for batch %s (director_mode=%s, family=%s)",
                    changed, len(raw), request.batch_id,
                    getattr(request, "director_mode", False), prompt_style_for_model(batch_model),
                )
            else:
                logger.warning(
                    "Media Director returned no rewrites for batch %s; prompts and the "
                    "per-prompt enhancer stand as they were",
                    request.batch_id,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Director pass skipped for batch image %s (non-fatal): %s",
                getattr(request, "batch_id", "?"), e,
            )

    def _queue_worker(self) -> None:
        """Drain one image batch at a time (GPU bouncer — mirrors batch_video)."""
        while True:
            try:
                request, batch_status, output_dir = self.batch_queue.get()
            except Exception as e:
                logger.error(f"Image queue worker get() failed: {e}")
                continue

            batch_id = request.batch_id
            try:
                cancel_event = self.cancel_events.get(batch_id)
                if cancel_event and cancel_event.is_set():
                    batch_status.status = "cancelled"
                    batch_status.end_time = datetime.now()
                    if not batch_status.error:
                        batch_status.error = "Cancelled before start"
                    try:
                        self._save_batch_metadata(batch_status, Path(output_dir))
                    except Exception:
                        pass
                    logger.info(f"Skipped cancelled image batch {batch_id}")
                    continue

                self._running_batch_id = batch_id
                self._run_batch_job(request, batch_status, Path(output_dir))
            except Exception as e:
                logger.error(f"Image queue worker crashed on batch {batch_id}: {e}")
                batch_status.status = "error"
                batch_status.error = str(e)
                batch_status.end_time = datetime.now()
                try:
                    self._save_batch_metadata(batch_status, Path(output_dir))
                except Exception:
                    pass
            finally:
                self._running_batch_id = None

    def start_batch_generation(self, request: BatchImageRequest) -> str:
        """Enqueue a batch. Returns immediately with status='queued'.

        A single daemon worker drains the queue one batch at a time so concurrent
        submissions stack rather than contending for the GPU (same model as video gen).
        """
        if not self.service_available:
            raise RuntimeError("Batch image generation service not available")

        batch_id = request.batch_id or self._generate_batch_id(request.kind)
        request.batch_id = batch_id
        output_dir = self._create_output_directory(batch_id)
        request.output_dir = str(output_dir)

        first_prompt = ""
        for p in request.prompts or []:
            if (p.prompt or "").strip():
                first_prompt = p.prompt.strip()[:80]
                break

        batch_status = BatchGenerationStatus(
            batch_id=batch_id,
            status="queued",
            total_images=len(request.prompts),
            completed_images=0,
            failed_images=0,
            output_dir=str(output_dir),
            display_name=request.display_name or first_prompt or batch_id,
            kind=request.kind,
        )
        
        batch_status.restore_faces = request.restore_faces
        batch_status.face_restoration_weight = request.face_restoration_weight
        batch_status.generate_thumbnails = request.generate_thumbnails
        batch_status.remove_background = request.remove_background
        batch_status.allow_model_download = request.allow_model_download

        first_p = request.prompts[0] if request.prompts else None
        prompts_list = [p.prompt for p in (request.prompts or [])]
        batch_status.retry_data = request.retry_data or {
            "mode": "text",
            "prompts": prompts_list,
            "params": {
                "model": getattr(first_p, "model", "auto") if first_p else "auto",
                "style": getattr(first_p, "style", "realistic") if first_p else "realistic",
                "width": getattr(first_p, "width", 1024) if first_p else 1024,
                "height": getattr(first_p, "height", 1024) if first_p else 1024,
                "steps": getattr(first_p, "steps", 9) if first_p else 9,
                "guidance": getattr(first_p, "guidance", 0.0) if first_p else 0.0,
                "content_preset": getattr(request, "content_preset", None),
                "auto_enhance": getattr(request, "auto_enhance", True),
                "restore_faces": getattr(request, "restore_faces", False),
                "remove_background": getattr(request, "remove_background", False),
                "director_mode": getattr(request, "director_mode", False),
                "ui_config": getattr(request, "ui_config", None),
            }
        }

        # After retry_data, so a retry still says "auto" and follows the Settings
        # choice current at that time.
        self._apply_stills_default_to_auto(request)

        with self.batch_lock:
            self.active_batches[batch_id] = batch_status
            self.cancel_events[batch_id] = threading.Event()
            self.queue_order.append(batch_id)

        try:
            self._save_batch_metadata(batch_status, output_dir)
        except Exception:
            pass

        self.batch_queue.put((request, batch_status, str(output_dir)))
        logger.info(
            f"Enqueued image batch {batch_id} ({len(request.prompts)} prompts) — "
            f"queue depth ~{self.batch_queue.qsize()}"
        )
        return batch_id

    def _run_batch_job(
        self,
        request: BatchImageRequest,
        batch_status: BatchGenerationStatus,
        output_dir: Path,
    ) -> None:
        """Execute one enqueued batch (called only from the queue worker)."""
        batch_id = request.batch_id

        def run_batch():
            batch_status.status = "running"
            batch_status.start_time = datetime.now()

            if self.progress_system:
                self._progress_process_id = self.progress_system.create_process(
                    process_type=ProcessType.IMAGE_GENERATION,
                    description=f"Batch generation of {len(request.prompts)} images",
                    process_id=batch_id,
                    additional_data={
                        "batch_id": batch_id,
                        "total_images": len(request.prompts)
                    }
                )

            def _run_batch_body(session_held: bool = False):
                # When session_held, run_batch's thread owns the batch-level gpu_session.
                # The gpu_session reentrancy flag is THREAD-LOCAL, so executor worker
                # threads must adopt it — otherwise the per-image / in-generator
                # gpu_session tries to claim the gate the batch already holds and every
                # image fails instantly with GpuBusyError ("GPU is busy...").
                def _gen_one(prompt):
                    if session_held:
                        from backend.services.gpu_resource_policy import adopt_gpu_session
                        with adopt_gpu_session():
                            return self._generate_single_image(batch_id, prompt, output_dir, batch_status)
                    return self._generate_single_image(batch_id, prompt, output_dir, batch_status)

                try:
                    # Verbatim Prompts: force exact user text through (no director rewrite,
                    # no offline style stuffing / word clip). Must run before director.
                    try:
                        from backend.services.media_director import verbatim_prompts_enabled
                        if verbatim_prompts_enabled():
                            request.auto_enhance = False
                            request.director_mode = False
                            for bp in request.prompts or []:
                                bp.auto_enhance = False
                            logger.info(
                                "Batch %s: verbatim prompts ON — director off, auto_enhance off",
                                batch_id,
                            )
                    except Exception:
                        pass

                    # Director / storyboard pass (if requested). Does NOT raise. Disables auto_enhance on success.
                    try:
                        self._apply_director(request)
                    except Exception:
                        pass

                    # The rewrite runs the chat model, which this batch's GPU session
                    # had just cleared off the card. When the batch no longer fits
                    # beside it, clear it again before the first image renders: left
                    # resident it took 8 GB and MOSS-Image rendered from system RAM.
                    if session_held:
                        try:
                            from backend.services.gpu_resource_policy import evict_ollama_models, fit_verdict
                            if not fit_verdict(vram_mb, reserve_mb=reserve_mb).headroom:
                                evict_ollama_models()
                                deadline = time.time() + 15
                                while time.time() < deadline and not fit_verdict(vram_mb, reserve_mb=reserve_mb).ok:
                                    time.sleep(0.5)
                        except Exception as e:  # noqa: BLE001 — the render reports its own memory failure
                            logger.warning("Batch %s: chat model eviction after the rewrite failed: %s", batch_id, e)

                    # One image at a time on a CUDA card: one pipeline per card, and a
                    # second worker only waits on its generation lock. Measured 2026-10-24
                    # on a 16 GB card (Z-Image Turbo, 1344x768, 9 steps, 6 images): one
                    # worker 160 s, two workers 171 s, each image 23 s against 46 s.
                    max_workers = 1 if self._batch_uses_cuda_offline_gen() else request.max_workers

                    results = []
                    # preserve_order: results are listed in prompt order rather than
                    # completion order (they differ whenever max_workers > 1).
                    prompt_order = {p.id: i for i, p in enumerate(request.prompts)}

                    def _ordered_results() -> List[BatchImageResult]:
                        # A new list, not an in-place sort: /status may be iterating
                        # batch_status.results from another thread.
                        if not request.preserve_order:
                            return list(results)
                        return sorted(results, key=lambda r: prompt_order.get(r.prompt_id, len(prompt_order)))

                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        self.executors[batch_id] = executor

                        pending_futures = []
                        prompt_index = 0

                        while prompt_index < len(request.prompts) or pending_futures:
                            if batch_status.status == "cancelled":
                                logger.info(f"Batch {batch_id} cancelled, stopping new submissions")
                                for future in pending_futures:
                                    future.cancel()
                                break

                            while prompt_index < len(request.prompts) and len(pending_futures) < max_workers:
                                if batch_status.status == "cancelled":
                                    break

                                prompt = request.prompts[prompt_index]
                                future = executor.submit(_gen_one, prompt)
                                pending_futures.append(future)
                                prompt_index += 1

                            if pending_futures:
                                done_futures = []
                                for future in pending_futures[:]:
                                    if future.done():
                                        done_futures.append(future)
                                        pending_futures.remove(future)

                                if not done_futures and pending_futures:
                                    try:
                                        import concurrent.futures
                                        done, _ = concurrent.futures.wait(
                                            pending_futures,
                                            timeout=0.5,
                                            return_when=concurrent.futures.FIRST_COMPLETED
                                        )
                                        for future in done:
                                            done_futures.append(future)
                                            pending_futures.remove(future)
                                    except Exception:
                                        pass

                                for future in done_futures:
                                    try:
                                        result = future.result()
                                        results.append(result)

                                        with self.batch_lock:
                                            if result.success:
                                                batch_status.completed_images += 1
                                            else:
                                                batch_status.failed_images += 1
                                                if not batch_status.error:
                                                    batch_status.error = result.error
                                                    batch_status.error_kind = result.error_kind

                                            batch_status.results = _ordered_results()

                                        if request.save_metadata:
                                            try:
                                                self._save_batch_metadata(batch_status, output_dir)
                                            except Exception:
                                                pass

                                    except Exception as e:
                                        logger.error(f"Task failed: {e}")
                                        with self.batch_lock:
                                            batch_status.failed_images += 1

                            # A context-killing CUDA error fails every later prompt
                            # the same way; trying them only repeats the error once
                            # per prompt. Fail the rest now and say why once.
                            fault = self._gpu_fault_message()
                            if fault and prompt_index < len(request.prompts):
                                skipped = len(request.prompts) - prompt_index
                                logger.error(
                                    "Batch %s: GPU fault — failing the remaining %d "
                                    "prompt(s) without trying them: %s",
                                    batch_id, skipped, fault,
                                )
                                with self.batch_lock:
                                    batch_status.failed_images += skipped
                                    batch_status.error = fault
                                prompt_index = len(request.prompts)

                    batch_status.end_time = datetime.now()
                    if batch_status.status == "cancelled":
                        pass
                    elif batch_status.completed_images == 0 and batch_status.failed_images > 0:
                        batch_status.status = "error"
                        if not batch_status.error:
                            batch_status.error = "All images failed to generate."
                    else:
                        batch_status.status = "completed"

                    if request.save_metadata:
                        self._save_batch_metadata(batch_status, output_dir)

                    if batch_status.status == "completed" and batch_status.completed_images > 0:
                        try:
                            from flask import current_app
                            from backend.services.output_registration import ensure_folder_for_dir, register_file
                            try:
                                app = current_app._get_current_object()
                            except RuntimeError:
                                from backend.app import get_or_create_app
                                app = get_or_create_app()
                            with app.app_context():
                                try:
                                    ensure_folder_for_dir(output_dir)
                                    images_dir = output_dir / "images"
                                    for img_file in sorted(images_dir.glob("*")):
                                        if img_file.is_file():
                                            img_meta = {}
                                            for r in batch_status.results:
                                                if r.success and r.image_path and Path(r.image_path).name == img_file.name:
                                                    img_meta = r.metadata or {}
                                                    break
                                            register_file(
                                                physical_path=str(img_file),
                                                folder_name="Images",
                                                subfolder_name=batch_id,
                                                file_metadata={
                                                    "source": "infographic" if batch_status.kind == "infographic" else "batch_generation",
                                                    "batch_id": batch_id, **img_meta,
                                                },
                                            )
                                    logger.info(f"Registered batch {batch_id} images into Documents system")
                                finally:
                                    from backend.models import db as _db
                                    _db.session.remove()
                        except Exception as reg_err:
                            logger.error(f"Failed to register batch images: {reg_err}")

                    if self.progress_system:
                        if batch_status.status == "completed":
                            self.progress_system.complete_process(
                                process_id=batch_id,
                                message=f"Batch generation completed: {batch_status.completed_images}/{batch_status.total_images} successful",
                                additional_data={
                                    "batch_id": batch_id,
                                    "completed": batch_status.completed_images,
                                    "failed": batch_status.failed_images,
                                    "total": batch_status.total_images
                                }
                            )
                        elif batch_status.status == "error":
                            self.progress_system.error_process(
                                process_id=batch_id,
                                message=f"Batch generation failed: {batch_status.error or 'all images failed'}",
                                additional_data={
                                    "batch_id": batch_id,
                                    "completed": batch_status.completed_images,
                                    "failed": batch_status.failed_images,
                                    "total": batch_status.total_images
                                }
                            )
                        else:
                            self.progress_system.cancel_process(
                                process_id=batch_id,
                                message=f"Batch generation cancelled: {batch_status.completed_images}/{batch_status.total_images} completed",
                                additional_data={
                                    "batch_id": batch_id,
                                    "completed": batch_status.completed_images,
                                    "failed": batch_status.failed_images,
                                    "total": batch_status.total_images
                                }
                            )

                    with self.batch_lock:
                        if batch_id in self.executors:
                            del self.executors[batch_id]
                    self._keep_or_cleanup_gpu_memory(batch_status)
                    self._release_batch_booking(batch_id)

                except Exception as e:
                    logger.error(f"Batch generation failed: {e}")
                    batch_status.status = "error"
                    batch_status.error = str(e)
                    self._cleanup_gpu_memory()
                    self._release_batch_booking(batch_id)

                    if self.progress_system:
                        self.progress_system.error_process(
                            process_id=batch_id,
                            message=f"Batch generation error: {str(e)}",
                            additional_data={"batch_id": batch_id, "error": str(e)}
                        )

            # A model kept from the last batch that this one will not render with
            # goes now, whichever engine this batch uses.
            if self._kept_model_for_batch(request) is None:
                try:
                    gen = self.image_generator
                    if gen is not None and hasattr(gen, "release_kept_pipeline"):
                        gen.release_kept_pipeline()
                except Exception as e:  # noqa: BLE001
                    logger.warning("Could not unload the kept image model: %s", e)

            # Every image would need a model that is not installed: end here,
            # before the GPU session evicts the chat model to make room for it.
            if not request.allow_model_download and request.prompts:
                refusals = [self._missing_offline_model(p) for p in request.prompts]
                if all(refusals):
                    logger.warning(f"Batch {batch_id} refused: {refusals[0]}")
                    self._finish_refused_before_start(
                        batch_id, batch_status, output_dir, request, refusals[0],
                    )
                    return

            if self._batch_uses_cuda_offline_gen():
                from backend.services.gpu_resource_policy import (
                    compositor_vram_reserve_mb,
                    gpu_session,
                )
                from backend.services.job_operation_gate import (
                    GpuBusyError,
                    GpuCapacityError,
                    get_gate,
                )
                from backend.services.job_types import JobKind

                reuse_model = self._kept_model_for_batch(request)
                vram_mb, ram_gb = self._batch_resource_estimates(request, reuse_model)
                slot_id = f"image_batch:{batch_id}"
                reserve_mb = compositor_vram_reserve_mb()
                cancel_event = self.cancel_events.get(batch_id)
                logger.info(
                    f"Batch {batch_id} acquiring gpu_session "
                    f"(vram~{vram_mb}MB ram~{ram_gb}GB)"
                )

                # Retry a resident/busy refusal with backoff until the deadline;
                # a capacity refusal is terminal. Mirrors the video batch loop
                # (batch_video_generator._run_batch). Before this, a shortfall of
                # ~130MB against a cross-encoder that idle-evicts minutes later
                # killed the whole batch on the first try. Eviction belongs to
                # gpu_session once it has won the slot; this loop never reclaims.
                try:
                    admit_deadline_s = float(
                        os.environ.get("GUAARDVARK_IMAGE_VRAM_WAIT_S", "600")  # 10 min
                    )
                except ValueError:
                    admit_deadline_s = 600.0
                admit_deadline_s = max(0.0, admit_deadline_s)
                deadline = time.time() + admit_deadline_s
                backoff_s = 2.0
                need_mb = int(vram_mb) + 1024

                def _on_gate_wait(reason: str) -> None:
                    self._report_gpu_wait(
                        batch_id, batch_status, GpuBusyError(reason), need_mb, reserve_mb
                    )

                while True:
                    if cancel_event and cancel_event.is_set():
                        self._finish_cancelled_before_start(
                            batch_id, batch_status, output_dir, request,
                            "Cancelled while waiting for GPU/VRAM",
                        )
                        return

                    try:
                        with gpu_session(
                            JobKind.VIDEO_RENDER,
                            batch_id,
                            on_busy="wait",
                            wait_timeout=120.0,
                            evict_ollama=True,
                            free_comfyui=True,
                            cross_process=True,
                            vram_estimate_mb=vram_mb,
                            ram_estimate_gb=ram_gb,
                            require_fit=True,
                            slot_id=slot_id,
                            lease_seconds=1800,
                            # 2026-08-04: diffusers-image sessions hold back the desktop
                            # compositor's VRAM share (Wayland died when 2048² jobs were
                            # admitted against raw card totals).
                            vram_reserve_mb=reserve_mb,
                            image_model=reuse_model,
                            cancel_event=cancel_event,
                            on_wait=_on_gate_wait,
                        ):
                            if batch_status.gpu_wait_reason and self.progress_system:
                                # Progress consumers merge additional_data, so the
                                # wait reason stays until it is cleared explicitly.
                                try:
                                    self.progress_system.update_process(
                                        process_id=batch_id,
                                        progress=0,
                                        message="Starting generation",
                                        additional_data={"batch_id": batch_id, "gpu_wait_reason": None},
                                    )
                                except Exception:
                                    pass
                            batch_status.gpu_wait_reason = None
                            _run_batch_body(session_held=True)
                        return
                    except GpuCapacityError as e:
                        # The estimate cannot fit this card at all. Waiting is
                        # not a strategy; fail now with the honest reason.
                        batch_status.gpu_wait_reason = None
                        self._fail_batch_gpu_busy(
                            batch_id, batch_status, output_dir, request,
                            f"Could not acquire GPU / system RAM headroom: {e}",
                        )
                        return
                    except GpuBusyError as e:
                        if cancel_event and cancel_event.is_set():
                            continue
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            batch_status.gpu_wait_reason = None
                            self._fail_batch_gpu_busy(
                                batch_id, batch_status, output_dir, request,
                                f"Could not acquire enough free VRAM after waiting "
                                f"{int(admit_deadline_s)}s: {e}",
                            )
                            return

                        self._report_gpu_wait(batch_id, batch_status, e, need_mb, reserve_mb)
                        logger.warning(
                            "Batch %s VRAM resident/busy (%s) — retrying in %.0fs (%.0fs left)",
                            batch_id, e, min(backoff_s, remaining), remaining,
                        )

                        sleep_for = min(backoff_s, max(0.5, deadline - time.time()))
                        end_sleep = time.time() + sleep_for
                        # Still in line while asleep: see JobOperationGate.waiting_for_gpu.
                        with get_gate().waiting_for_gpu(JobKind.VIDEO_RENDER, batch_id):
                            while time.time() < end_sleep:
                                if cancel_event and cancel_event.is_set():
                                    break
                                time.sleep(min(1.0, end_sleep - time.time()))
                        backoff_s = min(15.0, backoff_s * 1.5)
            else:
                _run_batch_body()

        # Already on the queue worker thread — run in-process (no nested thread).
        run_batch()
        logger.info(
            f"Finished image batch {batch_id}: status={batch_status.status} "
            f"ok={batch_status.completed_images} fail={batch_status.failed_images}"
        )

    def get_batch_status(self, batch_id: str) -> Optional[BatchGenerationStatus]:
        with self.batch_lock:
            return self.active_batches.get(batch_id)

    def cancel_batch(self, batch_id: str) -> bool:
        with self.batch_lock:
            if batch_id not in self.active_batches:
                return False

            batch_status = self.active_batches[batch_id]
            if batch_status.status in ["completed", "error", "cancelled"]:
                return False

            batch_status.status = "cancelled"
            batch_status.end_time = datetime.now()
            if not batch_status.error:
                batch_status.error = "Cancelled by user"

            # Signal the queue worker to skip if still queued; if running, loop checks status.
            ev = self.cancel_events.get(batch_id)
            if ev:
                ev.set()

            if batch_id in self.executors:
                self.executors[batch_id].shutdown(wait=False)
                del self.executors[batch_id]

            logger.info(f"Cancelled batch generation {batch_id}")
            return True

    def forget_batch(self, batch_id: str) -> None:
        """Drop every in-memory trace of a batch (after its files are deleted)."""
        with self.batch_lock:
            self.active_batches.pop(batch_id, None)
            self.cancel_events.pop(batch_id, None)
            self.executors.pop(batch_id, None)
            self.queue_order = [b for b in self.queue_order if b != batch_id]

    def list_queue(self) -> List[Dict[str, Any]]:
        """Snapshot of the in-process image batch queue for the UI panel."""
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
                "total_images": status.total_images,
                "completed_images": status.completed_images,
                "failed_images": status.failed_images,
                "is_running": batch_id == running_id,
                "start_time": status.start_time.isoformat() if status.start_time else None,
                "end_time": status.end_time.isoformat() if status.end_time else None,
                "display_name": status.display_name or batch_id,
                "kind": status.kind,
                "error": status.error,
                # Non-null while the batch is waiting on VRAM rather than stuck.
                "gpu_wait_reason": getattr(status, "gpu_wait_reason", None),
            })
        return snapshot

    def start_blueprint_batch(self, csv_content: str) -> str:
        batch_id = f"blueprint_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        output_dir = self._create_output_directory(batch_id)
        images_dir = output_dir / "images"
        thumbnails_dir = output_dir / "thumbnails"
        images_dir.mkdir(parents=True, exist_ok=True)
        thumbnails_dir.mkdir(parents=True, exist_ok=True)

        try:
            stream = io.StringIO(csv_content)
            reader = csv.DictReader(stream)
            total = sum(1 for row in reader if (row.get('city') or row.get('City') or row.get('name')))
        except Exception:
            total = 0

        batch_status = BatchGenerationStatus(
            batch_id=batch_id,
            status="pending",
            total_images=total,
            completed_images=0,
            failed_images=0,
            output_dir=str(output_dir),
        )
        with self.batch_lock:
            self.active_batches[batch_id] = batch_status

        def run_blueprint():
            self._run_blueprint_batch(batch_id, csv_content, output_dir, batch_status)

        thread = threading.Thread(target=run_blueprint, daemon=True)
        thread.start()
        logger.info(f"Started blueprint batch {batch_id} with {total} rows")
        return batch_id

    def _run_blueprint_batch(
        self,
        batch_id: str,
        csv_content: str,
        output_dir: Path,
        batch_status: BatchGenerationStatus,
    ) -> None:
        try:
            from PIL import Image, ImageDraw
            from werkzeug.utils import secure_filename
            import math
        except ImportError as e:
            logger.error(f"Blueprint dependencies missing: {e}")
            batch_status.status = "error"
            batch_status.error = str(e)
            batch_status.end_time = datetime.now()
            return

        batch_status.status = "running"
        batch_status.start_time = datetime.now()
        images_dir = output_dir / "images"
        thumbnails_dir = output_dir / "thumbnails"
        results: List[BatchImageResult] = []

        width, height = 1900, 600

        PALETTES = {
            'tech': {
                'bg': '#020617',
                'lines': '#1e293b',
                'nodes': ['#2563eb', '#3b82f6', '#475569', '#1d4ed8', '#1e40af']
            },
            'faith': {
                'bg': '#020617',
                'lines': '#334155',
                'nodes': ['#F0C986', '#FDE68A', '#D97706', '#FFFFFF']
            },
            'radar': {
                'bg': '#020617',
                'lines': '#1e293b',
                'nodes': ['#F0C986', '#ef4444']
            },
            'circuit': {
                'bg': '#0a0a0a',
                'lines': '#1a3a1a',
                'nodes': ['#22c55e', '#4ade80', '#15803d', '#86efac']
            },
            'scales': {
                'bg': '#0c1222',
                'lines': '#1e3a5f',
                'nodes': ['#c0c0c0', '#e2e8f0', '#94a3b8', '#ffffff']
            },
            'pulse': {
                'bg': '#021a1a',
                'lines': '#0f3d3d',
                'nodes': ['#06b6d4', '#22d3ee', '#ef4444', '#ffffff']
            },
            'lattice': {
                'bg': '#1a1209',
                'lines': '#3d2e1a',
                'nodes': ['#f97316', '#fb923c', '#78716c', '#d6d3d1']
            },
        }

        stream = io.StringIO(csv_content, newline=None)
        csv_input = csv.DictReader(stream)

        for row in csv_input:
            if batch_status.status == "cancelled":
                logger.info(f"Blueprint batch {batch_id} cancelled after {len(results)} rows")
                break

            city = row.get('city') or row.get('City') or row.get('name')
            if not city:
                continue

            style = (row.get('style') or row.get('Style') or 'tech').lower().strip()

            count_raw = row.get('count') or row.get('patents') or row.get('value') or '100'
            try:
                data_count = int(count_raw)
            except Exception:
                data_count = 50

            state_raw = row.get('state') or row.get('State') or row.get('STATE') or ''
            state = state_raw.strip().lower() if state_raw and state_raw.strip() else 'oh'

            csv_id = row.get('id') or row.get('ID') or row.get('Id')
            csv_filename = row.get('filename') or row.get('file_name')

            if csv_id:
                safe_name = secure_filename(f"{csv_id.strip()}.webp")
            elif csv_filename:
                if not csv_filename.lower().endswith(('.png', '.webp')):
                    csv_filename += '.webp'
                elif csv_filename.lower().endswith('.png'):
                    csv_filename = csv_filename[:-4] + '.webp'
                safe_name = secure_filename(csv_filename)
            else:
                slug_city = city.lower().strip().replace(' ', '-').replace('_', '-')
                slug_state = state.lower().strip()
                safe_name = secure_filename(f"{slug_city}-{slug_state}-{style}.webp")

            try:
                if style in PALETTES:
                    palette_key = style
                elif style in ['constellation', 'foundation']:
                    palette_key = 'faith'
                else:
                    palette_key = 'tech'
                colors = PALETTES[palette_key]

                img = Image.new('RGB', (width, height), color=colors['bg'])
                draw = ImageDraw.Draw(img)
                # Per-row generator: the layout is reproducible per city without
                # reseeding the process-wide RNG from a background thread.
                rng = random.Random(city)

                density = min(data_count, 1000)
                nodes = []

                if style == 'tech':
                    hero_color = colors['nodes'][sum(ord(c) for c in city) % len(colors['nodes'])]
                    for i in range(density):
                        x = rng.randint(50, width - 50)
                        y = rng.randint(50, height - 50)
                        nodes.append((x, y))
                        if i > 0 and i % 3 == 0:
                            prev = nodes[rng.randint(0, i - 1)]
                            draw.line([x, y, prev[0], prev[1]], fill=colors['lines'], width=1)
                    for x, y in nodes:
                        draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=hero_color)

                elif style == 'constellation':
                    for _ in range(density):
                        x = rng.randint(50, width - 50)
                        y = rng.randint(50, height - 50)
                        nodes.append((x, y))

                    for i, (x1, y1) in enumerate(nodes):
                        node_color = rng.choice(colors['nodes'])
                        draw.ellipse([x1 - 2, y1 - 2, x1 + 2, y1 + 2], fill=node_color)

                        draw.ellipse([x1 - 4, y1 - 4, x1 + 4, y1 + 4], outline=node_color, width=0)

                        closest_dist = float('inf')
                        closest_idx = -1

                        check_indices = rng.sample(range(len(nodes)), min(20, len(nodes)))
                        for j in check_indices:
                            if i == j: continue
                            x2, y2 = nodes[j]
                            dist = (x1-x2)**2 + (y1-y2)**2
                            if dist < closest_dist and dist < 40000:
                                closest_dist = dist
                                closest_idx = j

                        if closest_idx != -1:
                            x2, y2 = nodes[closest_idx]
                            draw.line([x1, y1, x2, y2], fill=colors['lines'], width=1)

                elif style == 'radar':
                    cx, cy = width // 2, height // 2

                    for r in range(100, 1000, 150):
                        draw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=colors['lines'], width=1)

                    for i in range(density):
                        angle = rng.uniform(0, 2 * math.pi)
                        dist = rng.uniform(0, height // 2 - 20)
                        x = int(cx + dist * math.cos(angle) * (width/height))
                        y = int(cy + dist * math.sin(angle))

                        is_crisis = rng.random() < 0.2
                        node_color = colors['nodes'][1] if is_crisis else colors['nodes'][0]

                        size = 3
                        draw.polygon([(x, y-size), (x+size, y), (x, y+size), (x-size, y)], fill=node_color)

                elif style == 'foundation':
                    grid_size = 40
                    for i in range(density):
                        gx = rng.randint(2, (width // grid_size) - 2) * grid_size
                        gy = rng.randint(2, (height // grid_size) - 2) * grid_size
                        nodes.append((gx, gy))

                        if i > 0 and i % 2 == 0:
                            prev = nodes[rng.randint(0, i-1)]
                            mid_x = prev[0]
                            mid_y = gy
                            draw.line([gx, gy, mid_x, mid_y], fill=colors['lines'], width=1)
                            draw.line([mid_x, mid_y, prev[0], prev[1]], fill=colors['lines'], width=1)

                        draw.rectangle([gx-2, gy-2, gx+2, gy+2], fill=colors['nodes'][0])

                elif style == 'circuit':
                    trace_y_lanes = list(range(60, height - 60, 35))
                    for ty in trace_y_lanes:
                        jitter = rng.randint(-2, 2)
                        draw.line([40, ty + jitter, width - 40, ty + jitter], fill=colors['lines'], width=1)

                    for i in range(density):
                        lane = rng.choice(trace_y_lanes)
                        x = rng.randint(60, width - 60)
                        y = lane + rng.randint(-4, 4)
                        nodes.append((x, y))
                        node_color = rng.choice(colors['nodes'])

                        if rng.random() < 0.3:
                            pw, ph = rng.choice([(6, 4), (8, 3), (4, 6)])
                            draw.rectangle([x - pw, y - ph, x + pw, y + ph], fill=node_color, outline=colors['lines'])
                        else:
                            draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=node_color)

                        if i > 0 and i % 4 == 0:
                            target_lane = rng.choice(trace_y_lanes)
                            draw.line([x, y, x, target_lane], fill=colors['lines'], width=1)

                elif style == 'scales':
                    cx = width // 2

                    draw.line([cx, 30, cx, height - 30], fill=colors['lines'], width=1)

                    for by in range(80, height - 40, 70):
                        beam_w = rng.randint(200, width // 2 - 50)
                        draw.line([cx - beam_w, by, cx + beam_w, by], fill=colors['lines'], width=1)

                    half_density = density // 2
                    left_nodes = []
                    right_nodes = []
                    for i in range(half_density):
                        x = rng.randint(50, cx - 30)
                        y = rng.randint(50, height - 50)
                        left_nodes.append((x, y))
                        mx = cx + (cx - x)
                        right_nodes.append((mx, y))

                    for i, (x, y) in enumerate(left_nodes):
                        node_color = rng.choice(colors['nodes'])
                        draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=node_color)
                        if i > 0 and i % 3 == 0:
                            prev = left_nodes[rng.randint(0, i - 1)]
                            draw.line([x, y, prev[0], prev[1]], fill=colors['lines'], width=1)

                    for i, (mx, y) in enumerate(right_nodes):
                        node_color = rng.choice(colors['nodes'])
                        draw.ellipse([mx - 2, y - 2, mx + 2, y + 2], fill=node_color)
                        if i > 0 and i % 3 == 0:
                            prev = right_nodes[rng.randint(0, i - 1)]
                            draw.line([mx, y, prev[0], prev[1]], fill=colors['lines'], width=1)

                    nodes = left_nodes + right_nodes

                elif style == 'pulse':
                    num_leads = 5
                    lead_spacing = height // (num_leads + 1)

                    for lead in range(num_leads):
                        base_y = lead_spacing * (lead + 1)
                        points = []
                        x = 40
                        while x < width - 40:
                            if rng.random() < 0.08:
                                spike_h = rng.randint(30, lead_spacing // 2)
                                direction = 1 if rng.random() < 0.7 else -1
                                points.extend([(x, base_y), (x + 4, base_y - spike_h * direction),
                                               (x + 8, base_y + spike_h * direction // 3), (x + 12, base_y)])
                                x += 16
                            else:
                                y = base_y + rng.randint(-3, 3)
                                points.append((x, y))
                                x += rng.randint(4, 8)

                        if len(points) >= 2:
                            for j in range(len(points) - 1):
                                draw.line([points[j], points[j + 1]], fill=colors['nodes'][0], width=1)

                    for i in range(density):
                        x = rng.randint(50, width - 50)
                        y = rng.randint(50, height - 50)
                        nodes.append((x, y))
                        node_color = rng.choice(colors['nodes'])
                        s = 2
                        draw.line([x - s, y, x + s, y], fill=node_color, width=1)
                        draw.line([x, y - s, x, y + s], fill=node_color, width=1)

                elif style == 'lattice':
                    mesh_size = 30
                    post_spacing = mesh_size * 6

                    for row_i in range(height // mesh_size + 1):
                        for col_i in range(width // mesh_size + 1):
                            cx = col_i * mesh_size + (mesh_size // 2 if row_i % 2 else 0)
                            cy = row_i * mesh_size
                            if 40 < cx < width - 40 and 40 < cy < height - 40:
                                half = mesh_size // 3
                                draw.line([cx, cy - half, cx + half, cy], fill=colors['lines'], width=1)
                                draw.line([cx + half, cy, cx, cy + half], fill=colors['lines'], width=1)
                                draw.line([cx, cy + half, cx - half, cy], fill=colors['lines'], width=1)
                                draw.line([cx - half, cy, cx, cy - half], fill=colors['lines'], width=1)

                    for px in range(post_spacing, width - 40, post_spacing):
                        draw.line([px, 40, px, height - 40], fill=colors['nodes'][2], width=3)
                        draw.ellipse([px - 4, 36, px + 4, 44], fill=colors['nodes'][0])

                    for i in range(min(density, 300)):
                        x = rng.randint(2, width // mesh_size - 2) * mesh_size
                        y = rng.randint(2, height // mesh_size - 2) * mesh_size
                        nodes.append((x, y))
                        node_color = rng.choice(colors['nodes'][:2])
                        draw.rectangle([x - 2, y - 2, x + 2, y + 2], fill=node_color)

                full_path = images_dir / safe_name
                thumb_path = thumbnails_dir / safe_name
                img.save(full_path)
                img.thumbnail((300, 95))
                img.save(thumb_path)

                result = BatchImageResult(
                    prompt_id=city,
                    success=True,
                    image_path=str(full_path),
                    thumbnail_path=str(thumb_path),
                    metadata={"city": city, "state": state, "style": style, "filename": safe_name},
                )

            except Exception as e:
                logger.warning(f"Blueprint row failed for city={city}: {e}")
                result = BatchImageResult(
                    prompt_id=city,
                    success=False,
                    error=str(e),
                    metadata={"city": city, "state": state},
                )

            results.append(result)
            with self.batch_lock:
                batch_status.results = results
                if result.success:
                    batch_status.completed_images += 1
                else:
                    batch_status.failed_images += 1

        batch_status.end_time = datetime.now()
        if batch_status.status != "cancelled":
            batch_status.status = "completed"
        batch_status.display_name = f"Multi-Style Blueprints - {batch_status.completed_images} items"

        metadata = {
            "batch_id": batch_id,
            "display_name": batch_status.display_name,
            "status": batch_status.status,
            "error": batch_status.error,
            "total_images": batch_status.total_images,
            "completed_images": batch_status.completed_images,
            "failed_images": batch_status.failed_images,
            "start_time": batch_status.start_time.isoformat() if batch_status.start_time else None,
            "end_time": batch_status.end_time.isoformat() if batch_status.end_time else None,
            "results": [
                {
                    "prompt_id": r.prompt_id,
                    "success": r.success,
                    "image_path": r.image_path,
                    "thumbnail_path": r.thumbnail_path,
                    "metadata": r.metadata,
                    "error": r.error,
                }
                for r in results
            ],
        }
        meta_path = output_dir / "batch_metadata.json"
        use_indent = 2 if len(results) <= 200 else None
        with open(meta_path, 'w') as f:
            json.dump(metadata, f, indent=use_indent)

        logger.info(f"Blueprint batch {batch_id} completed: {batch_status.completed_images}/{batch_status.total_images}")

    def list_active_batches(self) -> List[BatchGenerationStatus]:
        with self.batch_lock:
            return list(self.active_batches.values())

    def _status_from_metadata(
        self, batch_folder: Path, *, include_results: bool = False
    ) -> Optional[BatchGenerationStatus]:
        """Rebuild a BatchGenerationStatus from a batch folder's metadata file."""
        metadata_file = batch_folder / "batch_metadata.json"
        if not metadata_file.exists():
            return None
        try:
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
        except (json.JSONDecodeError, ValueError, OSError) as e:
            logger.warning(f"Failed to load metadata for batch {batch_folder.name}: {e}")
            return None

        try:
            status = BatchGenerationStatus(
                batch_id=metadata.get("batch_id", batch_folder.name),
                status=metadata.get("status", "unknown"),
                total_images=metadata.get("total_images", 0),
                completed_images=metadata.get("completed_images", 0),
                failed_images=metadata.get("failed_images", 0),
                start_time=datetime.fromisoformat(metadata["start_time"]) if metadata.get("start_time") else None,
                end_time=datetime.fromisoformat(metadata["end_time"]) if metadata.get("end_time") else None,
                output_dir=str(batch_folder),
                display_name=metadata.get("display_name"),
                retry_data=metadata.get("retry_data"),
                error=metadata.get("error"),
                error_kind=metadata.get("error_kind"),
                kind=batch_kind_of(metadata.get("batch_id", batch_folder.name), metadata.get("kind")),
            )
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"Failed to load metadata for batch {batch_folder.name}: {e}")
            return None

        if include_results:
            status.results = [
                BatchImageResult(
                    prompt_id=r.get("prompt_id", ""),
                    success=r.get("success", False),
                    image_path=r.get("image_path"),
                    thumbnail_path=r.get("thumbnail_path"),
                    generation_time=r.get("generation_time", 0.0) or 0.0,
                    error=r.get("error"),
                    error_kind=r.get("error_kind"),
                    metadata=r.get("metadata") or {},
                )
                for r in metadata.get("results", []) or []
                if isinstance(r, dict)
            ]
        return status

    def find_batch_status(
        self, batch_id: str, *, include_results: bool = False
    ) -> Optional[BatchGenerationStatus]:
        """In-memory status first, then the batch's own folder, then a full scan.

        Batch folders are named after the batch id and are found wherever they
        were moved under Images/; the scan of every batch's metadata stays as
        the fallback for a folder that was renamed.
        """
        status = self.get_batch_status(batch_id)
        if status:
            return status

        from werkzeug.utils import secure_filename
        from backend.services.output_registration import find_batch_dir
        safe_id = secure_filename(batch_id or "")
        batch_folder = find_batch_dir(self.base_output_dir, safe_id) if safe_id else None
        if batch_folder:
            direct = self._status_from_metadata(batch_folder, include_results=include_results)
            if direct:
                return direct

        for candidate in self.list_all_batches():
            if candidate.batch_id == batch_id and candidate.output_dir:
                if include_results and not candidate.results:
                    reloaded = self._status_from_metadata(
                        Path(candidate.output_dir), include_results=True
                    )
                    return reloaded or candidate
                return candidate
        return None

    def list_all_batches(self) -> List[BatchGenerationStatus]:
        active_batches = self.list_active_batches()
        active_batch_ids = {batch.batch_id for batch in active_batches}
        
        completed_batches = []
        
        if not self.base_output_dir.exists():
            logger.warning(f"Base output directory does not exist: {self.base_output_dir}")
            return active_batches
        
        from backend.services.output_registration import iter_batch_dirs
        try:
            for batch_folder in iter_batch_dirs(self.base_output_dir):
                if batch_folder.name in active_batch_ids:
                    continue
                batch_status = self._status_from_metadata(batch_folder)
                if batch_status:
                    completed_batches.append(batch_status)
                    
        except Exception as e:
            logger.error(f"Error scanning batch directories: {e}")
        
        all_batches = active_batches + completed_batches
        
        all_batches.sort(
            key=lambda b: b.start_time if b.start_time else datetime.min,
            reverse=True
        )
        
        return all_batches

    def create_batch_from_csv(self, csv_content: str, **kwargs) -> BatchImageRequest:
        # Form-level knobs fill empty CSV cells (model/size/steps/guidance).
        prompts = self._parse_csv_prompts(
            csv_content,
            form_model=kwargs.get("model"),
            form_width=kwargs.get("width"),
            form_height=kwargs.get("height"),
            form_steps=kwargs.get("steps"),
            form_steps_explicit=kwargs.get("steps_explicit", False),
            form_steps_metadata={k: kwargs[k] for k in ("steps_requested", "steps_notice") if k in kwargs},
            form_guidance=kwargs.get("guidance"),
            form_style=kwargs.get("style"),
        )

        if not prompts:
            raise ValueError("No valid prompts found in CSV")

        batch_id = self._generate_batch_id()
        output_dir = str(self._create_output_directory(batch_id))

        return BatchImageRequest(
            batch_id=batch_id,
            prompts=prompts,
            output_dir=output_dir,
            **{k: v for k, v in kwargs.items() if k in _BATCH_REQUEST_PARAMS}
        )

    def create_batch_from_prompts(self, prompt_list: List[str], **kwargs) -> BatchImageRequest:
        from backend.services.stills_defaults import resolve_stills_defaults

        prompt_params = ['model', 'style', 'width', 'height', 'steps', 'guidance',
                        'negative_prompt',
                        'content_preset', 'auto_enhance', 'enhance_anatomy',
                        'enhance_faces', 'enhance_hands', 'loras', 'subject_ids',
                        'trigger_word', 'adapter_loras', 'adapter_scale', 'text_encoders']

        model = kwargs.get("model") or "auto"
        resolved = resolve_stills_defaults(
            model,
            width=kwargs.get("width"),
            height=kwargs.get("height"),
            steps=kwargs.get("steps"),
            steps_explicit=kwargs.get("steps_explicit", False),
            guidance=kwargs.get("guidance"),
            replace_legacy_sd_markers=True,
        )
        # Apply resolved defaults into kwargs for BatchPrompt construction.
        filled = dict(kwargs)
        filled.setdefault("model", model)
        filled.setdefault("width", resolved["width"])
        filled.setdefault("height", resolved["height"])
        filled.setdefault("steps", resolved["steps"])
        filled.setdefault("guidance", resolved["guidance"])
        # Prefer resolved family values when caller passed legacy 512/20/7.5.
        filled["width"] = resolved["width"]
        filled["height"] = resolved["height"]
        filled["steps"] = resolved["steps"]
        filled["guidance"] = resolved["guidance"]

        from backend.services.image_prompt_sanitize import sanitize_image_prompt
        # A typed seed numbers the batch from itself, so a reused image seed
        # renders that image first and the copies after it still differ.
        base_seed = kwargs.get("seed")
        prompts = []
        for i, prompt in enumerate(prompt_list):
            cleaned = sanitize_image_prompt(prompt)
            if not cleaned:
                continue
            prompts.append(BatchPrompt(
                id=f"prompt_{i+1}",
                prompt=cleaned,
                seed=(int(base_seed) + i) % SEED_SPACE if base_seed is not None else None,
                metadata={
                    "steps_explicit": kwargs.get("steps_explicit", False),
                    "steps_requested": kwargs.get("steps_requested", resolved["steps_requested"]),
                    "steps_notice": resolved["steps_notice"] or kwargs.get("steps_notice"),
                },
                **{k: v for k, v in filled.items() if k in prompt_params}
            ))

        if not prompts:
            raise ValueError("No valid prompts provided")

        batch_id = self._generate_batch_id()
        output_dir = str(self._create_output_directory(batch_id))

        return BatchImageRequest(
            batch_id=batch_id,
            prompts=prompts,
            output_dir=output_dir,
            **{k: v for k, v in kwargs.items() if k in _BATCH_REQUEST_PARAMS}
        )

    def get_service_status(self) -> Dict[str, Any]:
        with self.batch_lock:
            active_batches = len([
                b for b in self.active_batches.values()
                if b.status in self._ACTIVE_STATUSES
            ])

        image_generator_status = None
        if self.image_generator:
            try:
                if hasattr(self.image_generator, 'get_service_status'):
                    status = self.image_generator.get_service_status()
                    if isinstance(status, dict):
                        image_generator_status = status
                    else:
                        logger.warning(f"Image generator status returned non-dict type: {type(status)}")
                        image_generator_status = {
                            "service_available": hasattr(self.image_generator, 'service_available') and self.image_generator.service_available,
                            "error": f"Status format not serializable: {type(status)}"
                        }
                else:
                    logger.warning("Image generator does not have get_service_status method")
                    image_generator_status = {
                        "service_available": hasattr(self.image_generator, 'service_available') and self.image_generator.service_available,
                        "error": "get_service_status method not available"
                    }
            except Exception as e:
                logger.warning(f"Failed to get image generator status: {e}")
                image_generator_status = {
                    "service_available": False,
                    "error": str(e)
                }

        return {
            "service_available": self.service_available,
            "active_batches": active_batches,
            "total_tracked_batches": len(self.active_batches),
            "base_output_dir": str(self.base_output_dir),
            "cache_dir": str(self.cache_dir),
            "image_generator_status": image_generator_status,
            "image_generator_available": self.image_generator is not None
        }


_batch_generator_instance = None

def get_batch_image_generator() -> BatchImageGenerator:
    global _batch_generator_instance
    if _batch_generator_instance is None:
        _batch_generator_instance = BatchImageGenerator()
    return _batch_generator_instance


def start_batch_from_csv(csv_content: str, **kwargs) -> str:
    generator = get_batch_image_generator()
    request = generator.create_batch_from_csv(csv_content, **kwargs)
    return generator.start_batch_generation(request)


def start_batch_from_prompts(prompts: List[str], **kwargs) -> str:
    generator = get_batch_image_generator()
    request = generator.create_batch_from_prompts(prompts, **kwargs)
    return generator.start_batch_generation(request)


def get_batch_status(batch_id: str) -> Optional[BatchGenerationStatus]:
    generator = get_batch_image_generator()
    return generator.get_batch_status(batch_id)


def cancel_batch(batch_id: str) -> bool:
    generator = get_batch_image_generator()
    return generator.cancel_batch(batch_id)
