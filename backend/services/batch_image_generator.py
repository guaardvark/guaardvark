
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
    from backend.services.media_director import expand_image_plan, direct_prompts as media_direct_enhance
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

@dataclass
class BatchImageResult:
    prompt_id: str
    success: bool
    image_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    generation_time: float = 0.0
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

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

    def _generate_batch_id(self) -> str:
        from backend.services.output_registration import bates_name
        # Bates-stamped batch folder: ImageBatch_04-02-2026_001
        return bates_name("image_batch", "", self.base_output_dir)

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
                    g_in = _cell_float('guidance', form_guidance)
                    seed = int(row['seed']) if row.get('seed') and str(row.get('seed')).strip() else None

                    resolved = resolve_stills_defaults(
                        row_model,
                        width=w_in,
                        height=h_in,
                        steps=s_in,
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
                self.image_generator._unload_pipeline()
            try:
                from backend.services.gpu_memory_orchestrator import get_orchestrator
                get_orchestrator().release_model("sd:pipeline")
            except Exception:
                pass
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

    def _resolve_batch_model_key(self, model_key: str) -> str:
        """Map a batch prompt model key to a catalog key for resource estimates."""
        if not model_key or model_key in ("auto", ""):
            gen = self.image_generator
            if gen:
                # Match offline auto-router: zimage first on consumer cards
                for preferred in ("zimage-turbo", "krea2-turbo", "sd-xl"):
                    if gen.available_models.get(preferred):
                        if gen._is_model_downloaded(gen.available_models[preferred]):
                            return preferred
                return "zimage-turbo"
            return "zimage-turbo"
        return model_key

    def _batch_resource_estimates(self, request: BatchImageRequest) -> Tuple[int, float]:
        """Worst-case (vram_mb, ram_gb) across offline-generator prompts in this batch."""
        if not self.image_generator:
            return 4000, 6.0
        gen = self.image_generator
        vram_mb = 4000
        ram_gb = 6.0
        for prompt in request.prompts:
            if prompt.loras or getattr(prompt, "subject_ids", None):
                # Cast LoRAs: estimate by family from first LoRA when possible
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
            else:
                model_key = self._resolve_batch_model_key(prompt.model)
            if self._is_comfy_flux_model(model_key):
                # Full FLUX-dev fp8 stills: ~12GB+ with T5; serialize workers elsewhere
                vram_mb = max(vram_mb, 12000)
                ram_gb = max(ram_gb, 16.0)
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

            # If the model is already loaded (resident), its memory footprint is already
            # reflected in the system's available RAM. Avoid double-gating it — but the
            # per-generation activation surcharge above 1MP still applies.
            if getattr(gen, "_pipeline", None) is not None and getattr(gen, "_current_model", None) == catalog_id:
                model_vram = 1024 + max(0, gen._vram_estimate_mb(catalog_id, pw, ph) - gen._vram_estimate_mb(catalog_id))
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

    @staticmethod
    def _is_comfy_flux_model(model_key: str | None) -> bool:
        k = (model_key or "").strip().lower()
        return k in ("flux-dev", "flux", "flux.1-dev", "flux1-dev") or k.startswith("flux-dev")

    def _generate_with_comfy_flux(self, prompt: BatchPrompt, batch_id: str = "") -> Optional[ImageGenerationResult]:
        """Plain FLUX.1-dev stills (no LoRA) via ComfyUI — max-quality batch path."""
        try:
            from backend.services.comfyui_image_generator import ComfyUIImageGenerator
        except Exception as e:
            logger.warning("ComfyUI FLUX path unavailable: %s", e)
            return ImageGenerationResult(
                success=False,
                error=f"ComfyUI FLUX unavailable: {e}",
                prompt_used=prompt.prompt,
            )

        width = prompt.width if prompt.width and prompt.width >= 768 else 1024
        height = prompt.height if prompt.height and prompt.height >= 768 else 1024
        # Flux Dev ~2.0 MP design range — soft-clamp before Comfy (not 2048²).
        try:
            from backend.services.image_resolution_limits import clamp_image_dimensions
            ow, oh = width, height
            width, height, warns = clamp_image_dimensions(width, height, "flux")
            for msg in warns:
                logger.warning("FLUX batch: %s", msg)
            if (width, height) != (ow, oh):
                logger.info("FLUX batch resolution %sx%s → %sx%s", ow, oh, width, height)
        except Exception as e:
            logger.debug("FLUX dim clamp skipped: %s", e)
        steps = int(prompt.steps or 28)
        # FluxGuidance value (user "guidance" slider); KSampler cfg stays 1.0 inside Comfy.
        guidance = float(prompt.guidance) if prompt.guidance is not None else 3.5

        import tempfile, os as _os, time as _time
        out_path = _os.path.join(
            tempfile.gettempdir(), f"flux_{prompt.id}_{int(_time.time() * 1000)}.png"
        )
        # FLUX is deterministic: identical prompt + identical seed = identical
        # pixels. This branch used to pin the seed to a literal 42 whenever the
        # caller supplied none, so a batch of N prompts burned full GPU time
        # rendering N copies of the same image. Derive per-slot instead.
        seed = resolve_image_seed(prompt, batch_id)
        try:
            gen = ComfyUIImageGenerator(model="flux-dev")
            path = gen.generate_image(
                prompt=prompt.prompt,
                loras=[],
                output_path=out_path,
                width=width,
                height=height,
                negative_prompt=prompt.negative_prompt or None,
                seed=seed,
                steps=steps,
                cfg=guidance,
                model="flux-dev",
            )
            return ImageGenerationResult(
                success=True,
                image_path=path,
                prompt_used=prompt.prompt,
                model_used="flux-dev",
                image_size=(width, height),
                # The seed actually rendered, not the (often None) requested one —
                # otherwise the batch cannot report or reproduce its own output.
                seed_used=seed,
            )
        except Exception as e:
            logger.error("FLUX.1-dev batch generation failed: %s", e)
            return ImageGenerationResult(
                success=False, error=str(e), prompt_used=prompt.prompt
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
            still = render_character_still(
                final_prompt,
                subject_ids=sid_list or None,
                lora_paths=lora_list or None,
                include_bible=True,  # vision bible pins costume (armor, emblem, …)
                source="batch",
                width=width,
                height=height,
                steps=prompt.steps,
                guidance=prompt.guidance,
                seed=prompt.seed,
                negative_prompt=prompt.negative_prompt or "",
                output_path=out_path,
                style=prompt.style or "realistic",
                keep_pipeline=True,
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
            )
        except Exception as e:
            logger.error(f"Character LoRA generation failed: {e}")
            return ImageGenerationResult(success=False, error=str(e), prompt_used=final_prompt)

    def _generate_single_image(self, batch_id: str, prompt: BatchPrompt, output_dir: Path,
                             batch_status: BatchGenerationStatus) -> BatchImageResult:
        start_time = time.time()

        try:
            restore_faces = getattr(batch_status, 'restore_faces', False)
            face_restoration_weight = getattr(batch_status, 'face_restoration_weight', 0.5)
            remove_background = getattr(batch_status, 'remove_background', False)

            # Character casting: subject_ids / LoRAs → character_still_pipeline
            # (Z-Image offline or Comfy SDXL/FLUX by train base).
            if getattr(prompt, "subject_ids", None) or getattr(prompt, "loras", None):
                result = self._generate_with_character_lora(prompt)
            elif self._is_comfy_flux_model(prompt.model):
                result = self._generate_with_comfy_flux(prompt, batch_id)
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
                    content_preset=prompt.content_preset,
                    enhance_anatomy=prompt.enhance_anatomy,
                    enhance_faces=prompt.enhance_faces,
                    enhance_hands=prompt.enhance_hands,
                    restore_faces=restore_faces,
                    face_restoration_weight=face_restoration_weight,
                    remove_background=remove_background,
                    hold_gpu=False,
                    replace_legacy_sd_markers=True,
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
                    error=error_msg
                )

            image_ext = Path(result.image_path).suffix or ".png"
            # Bates-stamped filename: ImageGen_04-02-2026_001.png
            from backend.services.output_registration import bates_name
            image_filename = bates_name("image", image_ext, output_dir / "images")
            target_path = output_dir / "images" / image_filename

            import shutil
            shutil.move(result.image_path, target_path)

            thumbnail_path = None
            if batch_status and hasattr(batch_status, 'generate_thumbnails') and batch_status.generate_thumbnails:
                thumbnail_path = self._create_thumbnail(str(target_path), output_dir / "thumbnails")

            if self.progress_system:
                completed = batch_status.completed_images + 1
                progress = int((completed / batch_status.total_images) * 100)

                self.progress_system.update_process(
                    process_id=batch_id,
                    progress=progress,
                    message=f"Generated image {completed}/{batch_status.total_images}: {prompt.prompt[:50]}...",
                    additional_data={
                        "batch_id": batch_id,
                        "generated_count": completed,
                        "target_count": batch_status.total_images,
                        "completed": completed,
                        "total": batch_status.total_images,
                        "current_prompt": prompt.prompt[:100]
                    }
                )

            return BatchImageResult(
                prompt_id=prompt.id,
                success=True,
                image_path=str(target_path),
                thumbnail_path=thumbnail_path,
                generation_time=time.time() - start_time,
                metadata={
                    "original_prompt": prompt.prompt,
                    "style": prompt.style,
                    "dimensions": f"{prompt.width}x{prompt.height}",
                    "steps": prompt.steps,
                    "guidance": prompt.guidance,
                    "seed_used": result.seed_used,
                    "model_used": result.model_used
                }
            )

        except Exception as e:
            logger.error(f"Exception during image generation for prompt {prompt.id}: {e}")
            return BatchImageResult(
                prompt_id=prompt.id,
                success=False,
                generation_time=time.time() - start_time,
                error=str(e)
            )
        finally:
            import gc
            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        # Pipeline unload is deferred to batch completion (_cleanup_gpu_memory at
        # run_batch end). Per-image cleanup defeated keep_pipeline_loaded and caused
        # full Z-Image reload + RAM spike → OOM on multi-image batches.

    def _save_batch_metadata(self, batch_status: BatchGenerationStatus, output_dir: Path):
        try:
            metadata = {
                "batch_id": batch_status.batch_id,
                "display_name": getattr(batch_status, "display_name", None),
                "status": batch_status.status,
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

    def _apply_director(self, request: BatchImageRequest) -> None:
        """If director_mode or storyboard_concept, rewrite prompts via Media Director.

        Mutates in place and disables per-prompt offline auto_enhance (director already
        produced full visual prompts). Uses stills_policy for the enhance ladder so
        chat/batch share the same director behavior. Never raises.
        """
        if not MEDIA_DIRECTOR_AVAILABLE:
            return
        try:
            from backend.services.stills_policy import apply_enhance_to_prompts, resolve_enhance_mode

            if getattr(request, "storyboard_concept", None):
                concept = (request.storyboard_concept or "").strip()
                n = len(request.prompts)
                if concept and n > 0:
                    from backend.services.media_director import storyboard_from_concept
                    res = storyboard_from_concept(
                        concept, n,
                        style=(getattr(request, "look_and_feel", None) or ""),
                        extra_guidance=getattr(request, "director_guidance", None),
                    )
                    shots = res.get("prompts") or []
                    for i, p in enumerate(request.prompts):
                        if i < len(shots) and shots[i]:
                            p.prompt = shots[i]
                            p.auto_enhance = False
                    request.auto_enhance = False
                    logger.info(
                        "Media Director storyboard expanded %s prompts for batch %s",
                        len(shots), request.batch_id,
                    )
                    return

            mode = resolve_enhance_mode(
                director=bool(getattr(request, "director_mode", False)),
                auto_enhance=getattr(request, "auto_enhance", True),
            )
            if mode != "director":
                return

            raw = [bp.prompt for bp in request.prompts if (bp.prompt or "").strip()]
            if not raw:
                return
            # Prefer stills_policy director path (enhance_prompts); fall back to
            # media_direct_enhance alias if needed.
            style = getattr(request, "style", "") or ""
            guidance = getattr(request, "director_guidance", None)
            directed = apply_enhance_to_prompts(
                raw, enhance_mode="director", style=style, extra_guidance=guidance,
            )
            if not directed or directed == raw:
                # Fallback to batch's media_direct_enhance if policy path no-op'd
                try:
                    directed = media_direct_enhance(raw, style=style, extra_guidance=guidance)
                except Exception:
                    directed = raw
            idx = 0
            changed = 0
            for bp in request.prompts:
                if (bp.prompt or "").strip() and idx < len(directed) and directed[idx]:
                    newp = directed[idx].strip()
                    if newp and newp != (bp.prompt or "").strip():
                        bp.prompt = newp
                        changed += 1
                    bp.auto_enhance = False
                    idx += 1
            request.auto_enhance = False
            logger.info(
                "Media Director enhanced %s prompts for batch %s (director_mode=%s)",
                changed, request.batch_id, getattr(request, "director_mode", False),
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

        batch_id = request.batch_id or self._generate_batch_id()
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
            display_name=first_prompt or batch_id,
        )
        
        batch_status.restore_faces = request.restore_faces
        batch_status.face_restoration_weight = request.face_restoration_weight
        batch_status.generate_thumbnails = request.generate_thumbnails
        batch_status.remove_background = request.remove_background

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

                    max_workers = 1 if self._batch_uses_cuda_offline_gen() else request.max_workers

                    results = []

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

                                            batch_status.results = results

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
                            from backend.services.output_registration import ensure_subfolder, register_file
                            try:
                                app = current_app._get_current_object()
                            except RuntimeError:
                                from backend.app import get_or_create_app
                                app = get_or_create_app()
                            with app.app_context():
                                try:
                                    ensure_subfolder("Images", batch_id)
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
                                                file_metadata={"source": "batch_generation", "batch_id": batch_id, **img_meta},
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
                    self._cleanup_gpu_memory()

                except Exception as e:
                    logger.error(f"Batch generation failed: {e}")
                    batch_status.status = "error"
                    batch_status.error = str(e)
                    self._cleanup_gpu_memory()

                    if self.progress_system:
                        self.progress_system.error_process(
                            process_id=batch_id,
                            message=f"Batch generation error: {str(e)}",
                            additional_data={"batch_id": batch_id, "error": str(e)}
                        )

            if self._batch_uses_cuda_offline_gen():
                from backend.services.gpu_resource_policy import (
                    compositor_vram_reserve_mb,
                    gpu_session,
                    vram_probe_snapshot,
                )
                from backend.services.job_operation_gate import GpuBusyError, GpuCapacityError
                from backend.services.job_types import JobKind

                vram_mb, ram_gb = self._batch_resource_estimates(request)
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

                while True:
                    if cancel_event and cancel_event.is_set():
                        batch_status.status = "cancelled"
                        batch_status.error = "Cancelled while waiting for GPU/VRAM"
                        batch_status.end_time = datetime.now()
                        batch_status.gpu_wait_reason = None
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
                        ):
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
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            batch_status.gpu_wait_reason = None
                            self._fail_batch_gpu_busy(
                                batch_id, batch_status, output_dir, request,
                                f"Could not acquire enough free VRAM after waiting "
                                f"{int(admit_deadline_s)}s: {e}",
                            )
                            return

                        snap = vram_probe_snapshot(reserve_mb=reserve_mb)
                        wait_msg = (
                            f"Waiting for VRAM — "
                            f"{(snap.get('free_mb') or 0) / 1024:.1f}GB free, "
                            f"need ~{need_mb / 1024:.1f}GB"
                        )
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
                        logger.warning(
                            "Batch %s VRAM resident/busy (%s) — retrying in %.0fs (%.0fs left)",
                            batch_id, e, min(backoff_s, remaining), remaining,
                        )

                        sleep_for = min(backoff_s, max(0.5, deadline - time.time()))
                        end_sleep = time.time() + sleep_for
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
                random.seed(city)

                density = min(data_count, 1000)
                nodes = []

                if style == 'tech':
                    hero_color = colors['nodes'][sum(ord(c) for c in city) % len(colors['nodes'])]
                    for i in range(density):
                        x = random.randint(50, width - 50)
                        y = random.randint(50, height - 50)
                        nodes.append((x, y))
                        if i > 0 and i % 3 == 0:
                            prev = nodes[random.randint(0, i - 1)]
                            draw.line([x, y, prev[0], prev[1]], fill=colors['lines'], width=1)
                    for x, y in nodes:
                        draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=hero_color)

                elif style == 'constellation':
                    for _ in range(density):
                        x = random.randint(50, width - 50)
                        y = random.randint(50, height - 50)
                        nodes.append((x, y))

                    for i, (x1, y1) in enumerate(nodes):
                        node_color = random.choice(colors['nodes'])
                        draw.ellipse([x1 - 2, y1 - 2, x1 + 2, y1 + 2], fill=node_color)

                        draw.ellipse([x1 - 4, y1 - 4, x1 + 4, y1 + 4], outline=node_color, width=0)

                        closest_dist = float('inf')
                        closest_idx = -1

                        check_indices = random.sample(range(len(nodes)), min(20, len(nodes)))
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
                        angle = random.uniform(0, 2 * math.pi)
                        dist = random.uniform(0, height // 2 - 20)
                        x = int(cx + dist * math.cos(angle) * (width/height))
                        y = int(cy + dist * math.sin(angle))

                        is_crisis = random.random() < 0.2
                        node_color = colors['nodes'][1] if is_crisis else colors['nodes'][0]

                        size = 3
                        draw.polygon([(x, y-size), (x+size, y), (x, y+size), (x-size, y)], fill=node_color)

                elif style == 'foundation':
                    grid_size = 40
                    for i in range(density):
                        gx = random.randint(2, (width // grid_size) - 2) * grid_size
                        gy = random.randint(2, (height // grid_size) - 2) * grid_size
                        nodes.append((gx, gy))

                        if i > 0 and i % 2 == 0:
                            prev = nodes[random.randint(0, i-1)]
                            mid_x = prev[0]
                            mid_y = gy
                            draw.line([gx, gy, mid_x, mid_y], fill=colors['lines'], width=1)
                            draw.line([mid_x, mid_y, prev[0], prev[1]], fill=colors['lines'], width=1)

                        draw.rectangle([gx-2, gy-2, gx+2, gy+2], fill=colors['nodes'][0])

                elif style == 'circuit':
                    trace_y_lanes = list(range(60, height - 60, 35))
                    for ty in trace_y_lanes:
                        jitter = random.randint(-2, 2)
                        draw.line([40, ty + jitter, width - 40, ty + jitter], fill=colors['lines'], width=1)

                    for i in range(density):
                        lane = random.choice(trace_y_lanes)
                        x = random.randint(60, width - 60)
                        y = lane + random.randint(-4, 4)
                        nodes.append((x, y))
                        node_color = random.choice(colors['nodes'])

                        if random.random() < 0.3:
                            pw, ph = random.choice([(6, 4), (8, 3), (4, 6)])
                            draw.rectangle([x - pw, y - ph, x + pw, y + ph], fill=node_color, outline=colors['lines'])
                        else:
                            draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=node_color)

                        if i > 0 and i % 4 == 0:
                            target_lane = random.choice(trace_y_lanes)
                            draw.line([x, y, x, target_lane], fill=colors['lines'], width=1)

                elif style == 'scales':
                    cx = width // 2

                    draw.line([cx, 30, cx, height - 30], fill=colors['lines'], width=1)

                    for by in range(80, height - 40, 70):
                        beam_w = random.randint(200, width // 2 - 50)
                        draw.line([cx - beam_w, by, cx + beam_w, by], fill=colors['lines'], width=1)

                    half_density = density // 2
                    left_nodes = []
                    right_nodes = []
                    for i in range(half_density):
                        x = random.randint(50, cx - 30)
                        y = random.randint(50, height - 50)
                        left_nodes.append((x, y))
                        mx = cx + (cx - x)
                        right_nodes.append((mx, y))

                    for i, (x, y) in enumerate(left_nodes):
                        node_color = random.choice(colors['nodes'])
                        draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=node_color)
                        if i > 0 and i % 3 == 0:
                            prev = left_nodes[random.randint(0, i - 1)]
                            draw.line([x, y, prev[0], prev[1]], fill=colors['lines'], width=1)

                    for i, (mx, y) in enumerate(right_nodes):
                        node_color = random.choice(colors['nodes'])
                        draw.ellipse([mx - 2, y - 2, mx + 2, y + 2], fill=node_color)
                        if i > 0 and i % 3 == 0:
                            prev = right_nodes[random.randint(0, i - 1)]
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
                            if random.random() < 0.08:
                                spike_h = random.randint(30, lead_spacing // 2)
                                direction = 1 if random.random() < 0.7 else -1
                                points.extend([(x, base_y), (x + 4, base_y - spike_h * direction),
                                               (x + 8, base_y + spike_h * direction // 3), (x + 12, base_y)])
                                x += 16
                            else:
                                y = base_y + random.randint(-3, 3)
                                points.append((x, y))
                                x += random.randint(4, 8)

                        if len(points) >= 2:
                            for j in range(len(points) - 1):
                                draw.line([points[j], points[j + 1]], fill=colors['nodes'][0], width=1)

                    for i in range(density):
                        x = random.randint(50, width - 50)
                        y = random.randint(50, height - 50)
                        nodes.append((x, y))
                        node_color = random.choice(colors['nodes'])
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
                        x = random.randint(2, width // mesh_size - 2) * mesh_size
                        y = random.randint(2, height // mesh_size - 2) * mesh_size
                        nodes.append((x, y))
                        node_color = random.choice(colors['nodes'][:2])
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
        batch_status.status = "completed"

        metadata = {
            "batch_id": batch_id,
            "display_name": f"Multi-Style Blueprints - {batch_status.completed_images} items",
            "status": "completed",
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

    def list_all_batches(self) -> List[BatchGenerationStatus]:
        active_batches = self.list_active_batches()
        active_batch_ids = {batch.batch_id for batch in active_batches}
        
        completed_batches = []
        
        if not self.base_output_dir.exists():
            logger.warning(f"Base output directory does not exist: {self.base_output_dir}")
            return active_batches
        
        try:
            for batch_folder in self.base_output_dir.iterdir():
                if not batch_folder.is_dir():
                    continue
                
                batch_id = batch_folder.name
                
                if batch_id in active_batch_ids:
                    continue
                
                metadata_file = batch_folder / "batch_metadata.json"
                if not metadata_file.exists():
                    continue
                
                try:
                    with open(metadata_file, 'r') as f:
                        metadata = json.load(f)
                    
                    batch_status = BatchGenerationStatus(
                        batch_id=metadata.get("batch_id", batch_id),
                        status=metadata.get("status", "unknown"),
                        total_images=metadata.get("total_images", 0),
                        completed_images=metadata.get("completed_images", 0),
                        failed_images=metadata.get("failed_images", 0),
                        start_time=datetime.fromisoformat(metadata["start_time"]) if metadata.get("start_time") else None,
                        end_time=datetime.fromisoformat(metadata["end_time"]) if metadata.get("end_time") else None,
                        output_dir=str(batch_folder),
                        display_name=metadata.get("display_name"),
                        retry_data=metadata.get("retry_data"),
                    )
                    
                    completed_batches.append(batch_status)
                    
                except (json.JSONDecodeError, ValueError, KeyError) as e:
                    logger.warning(f"Failed to load metadata for batch {batch_id}: {e}")
                    continue
                    
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
            form_guidance=kwargs.get("guidance"),
            form_style=kwargs.get("style"),
        )

        if not prompts:
            raise ValueError("No valid prompts found in CSV")

        batch_id = self._generate_batch_id()
        output_dir = str(self._create_output_directory(batch_id))

        # Include director knobs so UI director_mode actually reaches _apply_director
        # (was silently dropped — dead plumbing bug).
        batch_params = [
            'max_workers', 'preserve_order', 'generate_thumbnails',
            'save_metadata', 'user_id', 'project_id', 'content_preset',
            'auto_enhance', 'enhance_anatomy', 'enhance_faces', 'enhance_hands',
            'restore_faces', 'face_restoration_weight', 'remove_background',
            'director_mode', 'director_guidance', 'storyboard_concept',
            'planning_mode', 'director_model', 'user_treatment',
        ]

        return BatchImageRequest(
            batch_id=batch_id,
            prompts=prompts,
            output_dir=output_dir,
            **{k: v for k, v in kwargs.items() if k in batch_params}
        )

    def create_batch_from_prompts(self, prompt_list: List[str], **kwargs) -> BatchImageRequest:
        from backend.services.stills_defaults import resolve_stills_defaults

        prompt_params = ['model', 'style', 'width', 'height', 'steps', 'guidance',
                        'negative_prompt',
                        'content_preset', 'auto_enhance', 'enhance_anatomy',
                        'enhance_faces', 'enhance_hands', 'loras', 'subject_ids',
                        'trigger_word']
        
        batch_params = [
            'max_workers', 'preserve_order', 'generate_thumbnails',
            'save_metadata', 'user_id', 'project_id', 'content_preset',
            'auto_enhance', 'enhance_anatomy', 'enhance_faces', 'enhance_hands',
            'restore_faces', 'face_restoration_weight', 'remove_background',
            'director_mode', 'director_guidance', 'storyboard_concept',
            'planning_mode', 'director_model', 'user_treatment',
        ]

        model = kwargs.get("model") or "auto"
        resolved = resolve_stills_defaults(
            model,
            width=kwargs.get("width"),
            height=kwargs.get("height"),
            steps=kwargs.get("steps"),
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
        prompts = []
        for i, prompt in enumerate(prompt_list):
            cleaned = sanitize_image_prompt(prompt)
            if not cleaned:
                continue
            prompts.append(BatchPrompt(
                id=f"prompt_{i+1}",
                prompt=cleaned,
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
            **{k: v for k, v in kwargs.items() if k in batch_params}
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
