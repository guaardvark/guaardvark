
import csv
import io
import json
import logging
import os
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
    from backend.config import CACHE_DIR
    config_available = True
except ImportError:
    config_available = False
    CACHE_DIR = "/tmp/guaardvark_cache"

@dataclass
class BatchPrompt:
    id: str
    prompt: str
    negative_prompt: str = ""
    style: str = "realistic"
    width: int = 512
    height: int = 512
    steps: int = 20
    guidance: float = 7.5
    seed: Optional[int] = None
    model: str = "sd-1.5"
    metadata: Dict[str, Any] = field(default_factory=dict)
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
    status: str  # "pending", "running", "completed", "error", "cancelled"
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

class BatchImageGenerator:

    def __init__(self):
        project_root = Path(__file__).parent.parent.parent
        self.base_output_dir = project_root / "data" / "outputs" / "batch_images"
        self.cache_dir = Path(CACHE_DIR) / "batch_generation"

        self.base_output_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.active_batches: Dict[str, BatchGenerationStatus] = {}
        self.batch_lock = threading.Lock()
        self.executors: Dict[str, ThreadPoolExecutor] = {}

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

        logger.info(f"BatchImageGenerator initialized - Service available: {self.service_available}")

    def _generate_batch_id(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        return f"batch_{timestamp}_{unique_id}"

    def _create_output_directory(self, batch_id: str) -> Path:
        output_dir = self.base_output_dir / batch_id
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "images").mkdir(exist_ok=True)
        (output_dir / "thumbnails").mkdir(exist_ok=True)
        return output_dir

    def _parse_csv_prompts(self, csv_content: str) -> List[BatchPrompt]:
        prompts = []

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
                
                prompt_text = row['prompt'].strip() if row['prompt'] else ''
                if not prompt_text:
                    logger.warning(f"Row {i+1} has empty prompt, skipping")
                    continue

                prompt_id = row.get('id', '').strip() or f"prompt_{i+1}"

                try:
                    width = int(row.get('width', 512)) if row.get('width', '').strip() else 512
                    height = int(row.get('height', 512)) if row.get('height', '').strip() else 512
                    steps = int(row.get('steps', 20)) if row.get('steps', '').strip() else 20
                    guidance = float(row.get('guidance', 7.5)) if row.get('guidance', '').strip() else 7.5
                    seed = int(row['seed']) if row.get('seed') and row['seed'].strip() else None
                    
                    width = (width // 8) * 8
                    height = (height // 8) * 8
                    
                    if width < 64:
                        width = 64
                    if height < 64:
                        height = 64
                        
                except (ValueError, TypeError) as e:
                    logger.warning(f"Row {i+1} has invalid numeric values, using defaults: {e}")
                    width = 512
                    height = 512
                    steps = 20
                    guidance = 7.5
                    seed = None

                prompt = BatchPrompt(
                    id=prompt_id,
                    prompt=prompt_text,
                    negative_prompt=row.get('negative_prompt', '').strip() if row.get('negative_prompt') else '',
                    style=row.get('style', 'realistic').strip() if row.get('style') else 'realistic',
                    width=width,
                    height=height,
                    steps=steps,
                    guidance=guidance,
                    seed=seed,
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

                thumb_filename = Path(image_path).stem + ".jpg"
                thumb_path = thumbnail_dir / thumb_filename
                image.save(thumb_path, "JPEG", quality=85)

            return str(thumb_path)

        except Exception as e:
            logger.warning(f"Failed to create thumbnail for {image_path}: {e}")
            return None

    def _cleanup_gpu_memory(self):
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def _generate_single_image(self, batch_id: str, prompt: BatchPrompt, output_dir: Path,
                             batch_status: BatchGenerationStatus) -> BatchImageResult:
        start_time = time.time()

        try:
            restore_faces = getattr(batch_status, 'restore_faces', False)
            face_restoration_weight = getattr(batch_status, 'face_restoration_weight', 0.5)
            
            gen_request = ImageGenerationRequest(
                prompt=prompt.prompt,
                negative_prompt=prompt.negative_prompt,
                width=prompt.width,
                height=prompt.height,
                num_inference_steps=prompt.steps,
                guidance_scale=prompt.guidance,
                style=prompt.style,
                seed=prompt.seed,
                model=prompt.model,
                content_preset=prompt.content_preset,
                auto_enhance=prompt.auto_enhance,
                enhance_anatomy=prompt.enhance_anatomy,
                enhance_faces=prompt.enhance_faces,
                enhance_hands=prompt.enhance_hands,
                restore_faces=restore_faces,
                face_restoration_weight=face_restoration_weight
            )

            result = self.image_generator.generate_image(gen_request)

            if result.success and result.image_path:
                image_ext = Path(result.image_path).suffix or ".png"
                image_filename = f"{prompt.id}{image_ext}"
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
            else:
                error_msg = result.error or "Unknown generation error"
                logger.error(f"Image generation failed for prompt {prompt.id}: {error_msg}")

                return BatchImageResult(
                    prompt_id=prompt.id,
                    success=False,
                    generation_time=time.time() - start_time,
                    error=error_msg
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
            self._cleanup_gpu_memory()

    def _save_batch_metadata(self, batch_status: BatchGenerationStatus, output_dir: Path):
        try:
            metadata = {
                "batch_id": batch_status.batch_id,
                "status": batch_status.status,
                "total_images": batch_status.total_images,
                "completed_images": batch_status.completed_images,
                "failed_images": batch_status.failed_images,
                "start_time": batch_status.start_time.isoformat() if batch_status.start_time else None,
                "end_time": batch_status.end_time.isoformat() if batch_status.end_time else None,
                "generation_summary": {
                    "total_generation_time": sum(r.generation_time for r in batch_status.results),
                    "successful_images": [r for r in batch_status.results if r.success],
                    "failed_images": [r for r in batch_status.results if not r.success]
                },
                "results": [
                    {
                        "prompt_id": r.prompt_id,
                        "success": r.success,
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

    def start_batch_generation(self, request: BatchImageRequest) -> str:
        if not self.service_available:
            raise RuntimeError("Batch image generation service not available")

        batch_id = request.batch_id or self._generate_batch_id()
        output_dir = self._create_output_directory(batch_id)

        batch_status = BatchGenerationStatus(
            batch_id=batch_id,
            status="pending",
            total_images=len(request.prompts),
            completed_images=0,
            failed_images=0,
            output_dir=str(output_dir)
        )
        
        batch_status.restore_faces = request.restore_faces
        batch_status.face_restoration_weight = request.face_restoration_weight
        batch_status.generate_thumbnails = request.generate_thumbnails

        with self.batch_lock:
            self.active_batches[batch_id] = batch_status

        def run_batch():
            try:
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

                max_workers = 1 if self.image_generator and hasattr(self.image_generator, '_device') and self.image_generator._device == 'cuda' else request.max_workers

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
                            future = executor.submit(
                                self._generate_single_image,
                                batch_id, prompt, output_dir, batch_status
                            )
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

                                    # Save metadata incrementally so results survive batch removal
                                    if request.save_metadata:
                                        try:
                                            self._save_batch_metadata(batch_status, output_dir)
                                        except Exception:
                                            pass  # Non-critical, best-effort

                                except Exception as e:
                                    logger.error(f"Task failed: {e}")
                                    with self.batch_lock:
                                        batch_status.failed_images += 1

                batch_status.end_time = datetime.now()
                batch_status.status = "completed" if batch_status.status != "cancelled" else "cancelled"

                if request.save_metadata:
                    self._save_batch_metadata(batch_status, output_dir)

                # Register images into Documents/Files system
                if batch_status.status == "completed" and batch_status.completed_images > 0:
                    try:
                        from flask import current_app
                        from backend.services.image_registration_service import register_batch_images
                        # Get app for context - try current_app first, fall back to import
                        try:
                            app = current_app._get_current_object()
                        except RuntimeError:
                            from backend.app import create_app
                            app = create_app()
                        with app.app_context():
                            try:
                                batch_name = getattr(request, 'batch_name', None) or batch_id
                                register_batch_images(
                                    batch_id=batch_id,
                                    batch_output_dir=str(output_dir),
                                    batch_name=batch_name,
                                )
                                logger.info(f"Registered batch {batch_id} images into Documents system")
                            finally:
                                from backend.models import db as _db
                                _db.session.remove()
                    except Exception as reg_err:
                        logger.error(f"Failed to register batch images into Documents system: {reg_err}")
                        # Don't fail the batch if registration fails

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

        thread = threading.Thread(target=run_batch, daemon=True)
        thread.start()

        logger.info(f"Started batch generation {batch_id} with {len(request.prompts)} prompts")
        return batch_id

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

            if batch_id in self.executors:
                self.executors[batch_id].shutdown(wait=False)
                del self.executors[batch_id]

            logger.info(f"Cancelled batch generation {batch_id}")
            return True

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
                        output_dir=str(batch_folder)
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
        prompts = self._parse_csv_prompts(csv_content)

        if not prompts:
            raise ValueError("No valid prompts found in CSV")

        batch_id = self._generate_batch_id()
        output_dir = str(self._create_output_directory(batch_id))

        prompt_params = ['model', 'style', 'width', 'height', 'steps', 'guidance',
                        'content_preset', 'auto_enhance', 'enhance_anatomy',
                        'enhance_faces', 'enhance_hands']
        
        batch_params = ['max_workers', 'preserve_order', 'generate_thumbnails', 
                       'save_metadata', 'user_id', 'project_id', 'content_preset',
                       'auto_enhance', 'enhance_anatomy', 'enhance_faces', 'enhance_hands',
                       'restore_faces', 'face_restoration_weight']

        return BatchImageRequest(
            batch_id=batch_id,
            prompts=prompts,
            output_dir=output_dir,
            **{k: v for k, v in kwargs.items() if k in batch_params}
        )

    def create_batch_from_prompts(self, prompt_list: List[str], **kwargs) -> BatchImageRequest:
        prompt_params = ['model', 'style', 'width', 'height', 'steps', 'guidance',
                        'content_preset', 'auto_enhance', 'enhance_anatomy',
                        'enhance_faces', 'enhance_hands']
        
        batch_params = ['max_workers', 'preserve_order', 'generate_thumbnails', 
                       'save_metadata', 'user_id', 'project_id', 'content_preset',
                       'auto_enhance', 'enhance_anatomy', 'enhance_faces', 'enhance_hands',
                       'restore_faces', 'face_restoration_weight']

        prompts = [
            BatchPrompt(
                id=f"prompt_{i+1}",
                prompt=prompt.strip(),
                **{k: v for k, v in kwargs.items() if k in prompt_params}
            )
            for i, prompt in enumerate(prompt_list) if prompt.strip()
        ]

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
            active_batches = len([b for b in self.active_batches.values() if b.status == "running"])

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
