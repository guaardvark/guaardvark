
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from celery import shared_task
from celery.exceptions import Retry

try:
    from backend.models import db, TrainingJob, DeviceProfile
    from backend.utils.unified_progress_system import get_unified_progress, ProcessType, ProcessStatus
    from backend.utils.progress_emitter import emit_progress_event
except ImportError as e:
    logging.error(f"Failed to import dependencies: {e}")
    db = TrainingJob = DeviceProfile = None
    get_unified_progress = None
    ProcessType = None
    ProcessStatus = None
    emit_progress_event = None

logger = logging.getLogger(__name__)

TRAINING_DIR = Path(os.environ.get('GUAARDVARK_ROOT', '.')) / "training"
PROCESSED_DIR = TRAINING_DIR / "processed"
MODELS_DIR = TRAINING_DIR / "models"
MODELFILES_DIR = Path(os.environ.get('GUAARDVARK_ROOT', '.')) / "data" / "modelfiles"

MODEL_TEMPLATES = {
    'llama-3': 'llama-3.1-instruct.modelfile',
    'llama3': 'llama-3.1-instruct.modelfile',
    'gemma-3': 'gemma-3-text.modelfile',
    'gemma3': 'gemma-3-text.modelfile',
    'gemma-2': 'gemma-3-text.modelfile',
    'gemma2': 'gemma-3-text.modelfile',
}


def _update_job_status(job_id: str, **kwargs):
    if not db or not TrainingJob:
        logger.warning("Cannot update job status: models not available")
        return
    
    try:
        from flask import current_app
        with current_app.app_context():
            job = db.session.query(TrainingJob).filter(
                TrainingJob.job_id == job_id
            ).first()
            
            if not job:
                logger.error(f"Job not found: {job_id}")
                return
            
            for key, value in kwargs.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            
            db.session.commit()
            logger.debug(f"Updated job {job_id}: {kwargs}")
    except Exception as e:
        logger.error(f"Error updating job status: {e}", exc_info=True)
        if db:
            db.session.rollback()


def _emit_progress(job_id: str, progress: int, message: str, status: str = "processing", metrics: dict = None):
    try:
        if emit_progress_event:
            if progress == 0 and status == "processing":
                emit_progress_event(
                    process_id=job_id,
                    progress=0,
                    message=message,
                    status="start",  # Use "start" to create the process
                    process_type="training",
                    additional_data={
                        "job_id": job_id,
                        "metrics": metrics or {}
                    }
                )
            else:
                emit_progress_event(
                    process_id=job_id,
                    progress=progress,
                    message=message,
                    status=status,
                    process_type="training",
                    additional_data={
                        "job_id": job_id,
                        "metrics": metrics or {}
                    }
                )
        elif get_unified_progress:
            progress_system = get_unified_progress()
            if progress_system:
                from backend.utils.unified_progress_system import ProcessStatus
                existing = progress_system.get_process(job_id)
                if not existing:
                    progress_system.create_process(
                        ProcessType.TRAINING,
                        message,
                        additional_data={"job_id": job_id, "metrics": metrics or {}},
                        process_id=job_id
                    )
                
                if status == "complete":
                    progress_system.complete_process(job_id, message, {"job_id": job_id, "metrics": metrics or {}})
                elif status == "error":
                    progress_system.error_process(job_id, message, {"job_id": job_id, "metrics": metrics or {}})
                elif status == "cancelled":
                    progress_system.cancel_process(job_id, message, {"job_id": job_id, "metrics": metrics or {}})
                else:
                    progress_system.update_process(job_id, progress, message, {"job_id": job_id, "metrics": metrics or {}})
    except Exception as e:
        logger.warning(f"Could not emit progress: {e}")


@shared_task(bind=True, name='training.parse_transcripts')
def parse_transcripts_task(self, job_id: str, input_path: str, recursive: bool = True):
    logger.info(f"Starting parse_transcripts_task for job {job_id}")
    
    try:
        _update_job_status(job_id, status="running", pipeline_stage="parsing", started_at=datetime.utcnow(), celery_task_id=self.request.id)
        _emit_progress(job_id, 0, "Starting transcript parsing...", "start")
        
        sys.path.insert(0, str(TRAINING_DIR / "scripts"))
        from transcript_parser import TranscriptParser
        
        parser = TranscriptParser(output_dir=str(PROCESSED_DIR))
        
        input_path_obj = Path(input_path)
        if not input_path_obj.exists():
            raise FileNotFoundError(f"Input path not found: {input_path}")
        
        _emit_progress(job_id, 10, f"Parsing transcripts from {input_path}...", "processing")
        
        if input_path_obj.is_file():
            pairs = parser.parse_file(str(input_path_obj))
        elif input_path_obj.is_dir():
            pairs = []
            pattern = "**/*" if recursive else "*"
            for file_path in input_path_obj.glob(pattern):
                if file_path.is_file() and file_path.suffix in ['.jsonl', '.json', '.md', '.txt', '.html', '.docx']:
                    file_pairs = parser.parse_file(str(file_path))
                    pairs.extend(file_pairs)
                    _emit_progress(job_id, 10 + int(80 * len(pairs) / max(1, len(list(input_path_obj.glob(pattern))))), 
                                 f"Parsed {len(pairs)} pairs from {file_path.name}...", "processing")
        else:
            raise ValueError(f"Invalid input path: {input_path}")
        
        output_filename = f"training_corpus_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        output_path = PROCESSED_DIR / output_filename
        
        _emit_progress(job_id, 90, f"Saving {len(pairs)} training pairs...", "processing")
        
        with open(output_path, 'w', encoding='utf-8') as f:
            for pair in pairs:
                f.write(json.dumps(pair) + '\n')
        
        _update_job_status(job_id, 
                          status="completed",
                          pipeline_stage="parsing",
                          completed_at=datetime.utcnow(),
                          progress=100,
                          config_json=json.dumps({
                              "input_path": input_path,
                              "output_path": str(output_path),
                              "pairs_count": len(pairs)
                          }))
        
        _emit_progress(job_id, 100, f"Completed: {len(pairs)} training pairs saved to {output_filename}", "complete")
        
        logger.info(f"Parse task completed for job {job_id}: {len(pairs)} pairs")
        return {"output_path": str(output_path), "pairs_count": len(pairs)}
        
    except Exception as e:
        logger.error(f"Error in parse_transcripts_task: {e}", exc_info=True)
        _update_job_status(job_id, status="failed", error_message=str(e))
        _emit_progress(job_id, 0, f"Error: {str(e)}", "error")
        raise


@shared_task(bind=True, name='training.filter_dataset')
def filter_dataset_task(self, job_id: str, input_path: str, min_score: float = 0.5):
    logger.info(f"Starting filter_dataset_task for job {job_id}")
    
    try:
        _update_job_status(job_id, status="running", pipeline_stage="filtering", started_at=datetime.utcnow(), celery_task_id=self.request.id)
        _emit_progress(job_id, 0, "Starting dataset filtering...", "start")
        
        input_path_obj = Path(input_path)
        if not input_path_obj.exists():
            raise FileNotFoundError(f"Input path not found: {input_path}")
        
        _emit_progress(job_id, 10, f"Loading dataset from {input_path}...", "processing")
        
        pairs = []
        with open(input_path_obj, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    pairs.append(json.loads(line))
        
        _emit_progress(job_id, 30, f"Filtering {len(pairs)} pairs (min_score={min_score})...", "processing")
        
        filtered_pairs = []
        for i, pair in enumerate(pairs):
            if not pair.get("instruction") or not pair.get("output"):
                continue
            
            inst_len = len(pair.get("instruction", ""))
            out_len = len(pair.get("output", ""))
            if inst_len < 10 or out_len < 10:
                continue
            if inst_len > 10000 or out_len > 10000:
                continue
            
            filtered_pairs.append(pair)
            
            if (i + 1) % 100 == 0:
                _emit_progress(job_id, 30 + int(50 * (i + 1) / len(pairs)), 
                             f"Filtered {len(filtered_pairs)}/{i+1} pairs...", "processing")
        
        output_filename = f"filtered_dataset_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        output_path = PROCESSED_DIR / output_filename
        
        _emit_progress(job_id, 90, f"Saving {len(filtered_pairs)} filtered pairs...", "processing")
        
        with open(output_path, 'w', encoding='utf-8') as f:
            for pair in filtered_pairs:
                f.write(json.dumps(pair) + '\n')
        
        _update_job_status(job_id,
                          status="completed",
                          pipeline_stage="filtering",
                          completed_at=datetime.utcnow(),
                          progress=100,
                          config_json=json.dumps({
                              "input_path": input_path,
                              "output_path": str(output_path),
                              "min_score": min_score,
                              "original_count": len(pairs),
                              "filtered_count": len(filtered_pairs)
                          }))
        
        _emit_progress(job_id, 100, 
                     f"Completed: {len(filtered_pairs)}/{len(pairs)} pairs passed filtering", 
                     "complete")
        
        logger.info(f"Filter task completed for job {job_id}: {len(filtered_pairs)}/{len(pairs)} pairs")
        return {"output_path": str(output_path), "filtered_count": len(filtered_pairs), "original_count": len(pairs)}
        
    except Exception as e:
        logger.error(f"Error in filter_dataset_task: {e}", exc_info=True)
        _update_job_status(job_id, status="failed", error_message=str(e))
        _emit_progress(job_id, 0, f"Error: {str(e)}", "error")
        raise


@shared_task(bind=True, name='training.finetune_model',
             soft_time_limit=86400, time_limit=172800)
def finetune_model_task(self, job_id: str, config: dict, resume: bool = False):
    logger.info(f"Starting finetune_model_task for job {job_id} (resume={resume})")

    current_pid = os.getpid()

    try:
        _update_job_status(
            job_id,
            status="running",
            pipeline_stage="training",
            started_at=datetime.utcnow(),
            celery_task_id=self.request.id,
            pid=current_pid
        )
        _emit_progress(job_id, 0, "Starting model fine-tuning...", "start")
        
        from flask import current_app
        with current_app.app_context():
            job = db.session.query(TrainingJob).filter(
                TrainingJob.job_id == job_id
            ).first()
            
            if not job:
                raise ValueError(f"Job not found: {job_id}")
            
            job_config = json.loads(job.config_json) if job.config_json else {}
            device_profile = None
            if job.device_profile_id:
                device_profile = db.session.get(DeviceProfile, job.device_profile_id)
        
        base_model = job.base_model
        data_path = job_config.get("data_path") or job_config.get("dataset_path")
        images_path = job_config.get("images_path")

        if not data_path:
            raise ValueError("data_path not found in job config")
        
        output_name = job.output_model_name or f"llamax1-{base_model.replace('/', '-').replace(':', '-')}"
        max_steps = job_config.get("steps", 500)
        learning_rate = job_config.get("lr", 2e-4)
        batch_size = job_config.get("batch_size", device_profile.max_batch_size if device_profile else 2)
        lora_rank = job_config.get("rank", 16)
        max_seq_length = job_config.get("seq_length", device_profile.max_seq_length if device_profile else 2048)
        offload_to_cpu = job_config.get("cpu_offload", device_profile.requires_cpu_offload if device_profile else False)
        
        _update_job_status(job_id, total_steps=max_steps)
        
        def progress_callback(step, total_steps, loss, metrics):
            progress = int((step / total_steps) * 100) if total_steps > 0 else 0
            _update_job_status(job_id, 
                             current_step=step,
                             progress=progress,
                             metrics_json=json.dumps(metrics))
            _emit_progress(job_id, progress, 
                         f"Training step {step}/{total_steps} (loss: {loss:.4f})", 
                         "processing",
                         metrics)
        
        _emit_progress(job_id, 5, f"Loading model {base_model}...", "processing")
        
        sys.path.insert(0, str(Path(os.environ.get('GUAARDVARK_ROOT', '.')) / "backend" / "services" / "training" / "scripts"))
        
        if images_path:
             _emit_progress(job_id, 8, f"Detected vision task. Using Qwen-VL trainer with images from {images_path}", "processing")
             from finetune_vision import finetune

             resume_msg = " (resuming from checkpoint)" if resume else ""
             _emit_progress(job_id, 10, f"Starting vision training loop{resume_msg}...", "processing")
             model_dir = finetune(
                base_model=base_model,
                data_path=data_path,
                image_folder=images_path,
                output_name=output_name,
                max_steps=max_steps,
                learning_rate=learning_rate,
                batch_size=batch_size,
                lora_rank=lora_rank,
                max_seq_length=max_seq_length,
                offload_to_cpu=offload_to_cpu,
                progress_callback=progress_callback,
                resume=resume
            )
        else:
             from finetune_model import finetune

             resume_msg = " (resuming from checkpoint)" if resume else ""
             _emit_progress(job_id, 10, f"Starting text training loop{resume_msg}...", "processing")
             model_dir = finetune(
                base_model=base_model,
                data_path=data_path,
                output_name=output_name,
                max_steps=max_steps,
                learning_rate=learning_rate,
                batch_size=batch_size,
                lora_rank=lora_rank,
                max_seq_length=max_seq_length,
                offload_to_cpu=offload_to_cpu,
                progress_callback=progress_callback,
                resume=resume
            )
        
        checkpoint_dir = Path(model_dir) / "checkpoints"
        checkpoint_path = None
        if checkpoint_dir.exists():
            checkpoints = list(checkpoint_dir.glob("checkpoint-*"))
            if checkpoints:
                checkpoints.sort(key=lambda x: int(x.name.split("-")[1]) if "-" in x.name else 0)
                checkpoint_path = str(checkpoints[-1])

        _update_job_status(job_id,
                          status="completed",
                          pipeline_stage="training",
                          completed_at=datetime.utcnow(),
                          progress=100,
                          lora_path=str(Path(model_dir) / "lora"),
                          checkpoint_path=checkpoint_path,
                          is_resumable=bool(checkpoint_path),
                          pid=None)

        _emit_progress(job_id, 100, f"Training complete! Model saved to {model_dir}", "complete")

        logger.info(f"Training task completed for job {job_id}: {model_dir}")
        return {"model_dir": model_dir, "lora_path": str(Path(model_dir) / "lora")}

    except Exception as e:
        logger.error(f"Error in finetune_model_task: {e}", exc_info=True)

        checkpoint_path = None
        is_resumable = False
        try:
            output_name = config.get("output_name") or f"llamax1-{config.get('base_model', 'model').replace('/', '-')}"
            checkpoint_dir = MODELS_DIR / output_name / "checkpoints"
            if checkpoint_dir.exists():
                checkpoints = list(checkpoint_dir.glob("checkpoint-*"))
                if checkpoints:
                    checkpoints.sort(key=lambda x: int(x.name.split("-")[1]) if "-" in x.name else 0)
                    checkpoint_path = str(checkpoints[-1])
                    is_resumable = True
        except Exception:
            pass

        _update_job_status(
            job_id,
            status="failed",
            error_message=str(e),
            checkpoint_path=checkpoint_path,
            is_resumable=is_resumable,
            pid=None
        )
        _emit_progress(job_id, 0, f"Error: {str(e)}", "error")
        raise


@shared_task(bind=True, name='training.export_gguf')
def export_gguf_task(self, job_id: str, model_dir: str, quantization: str = 'q4_k_m'):
    logger.info(f"Starting export_gguf_task for job {job_id}")
    
    try:
        _update_job_status(job_id, status="running", pipeline_stage="exporting", started_at=datetime.utcnow(), celery_task_id=self.request.id)
        _emit_progress(job_id, 0, "Starting GGUF export...", "start")
        
        _emit_progress(job_id, 10, "Loading model for export...", "processing")
        
        sys.path.insert(0, str(Path(os.environ.get('GUAARDVARK_ROOT', '.')) / "backend" / "services" / "training" / "scripts"))
        from finetune_model import export_to_gguf
        
        _emit_progress(job_id, 20, f"Exporting to GGUF ({quantization})...", "processing")
        
        gguf_path = export_to_gguf(model_dir, quantization)
        
        if not gguf_path:
            raise ValueError("GGUF export failed")
        
        _update_job_status(job_id,
                          pipeline_stage="exporting",
                          progress=100,
                          gguf_path=str(gguf_path))
        
        _emit_progress(job_id, 100, f"GGUF export complete: {gguf_path}", "complete")
        
        logger.info(f"Export task completed for job {job_id}: {gguf_path}")
        return {"gguf_path": str(gguf_path)}
        
    except Exception as e:
        logger.error(f"Error in export_gguf_task: {e}", exc_info=True)
        _update_job_status(job_id, status="failed", error_message=str(e))
        _emit_progress(job_id, 0, f"Error: {str(e)}", "error")
        raise


def _detect_model_architecture(model_name: str, gguf_filename: str) -> str:
    combined = f"{model_name} {gguf_filename}".lower()

    for pattern, template in MODEL_TEMPLATES.items():
        if pattern in combined:
            return template

    return 'llama-3.1-instruct.modelfile'


def _generate_modelfile_from_template(template_name: str, gguf_path: Path, mmproj_path: Path = None) -> str:
    template_path = MODELFILES_DIR / template_name

    if not template_path.exists():
        logger.warning(f"Template not found: {template_path}, using fallback")
        content = f"FROM {gguf_path}\n"
        content += "PARAMETER temperature 0.7\n"
        content += "PARAMETER top_p 0.9\n"
        return content

    with open(template_path, 'r') as f:
        content = f.read()

    content = content.replace('{{GGUF_PATH}}', str(gguf_path))

    if mmproj_path and '{{MMPROJ_PATH}}' in content:
        content = content.replace('{{MMPROJ_PATH}}', str(mmproj_path))
    elif '{{MMPROJ_PATH}}' in content:
        lines = content.split('\n')
        content = '\n'.join(line for line in lines if '{{MMPROJ_PATH}}' not in line)

    return content


@shared_task(bind=True, name='training.import_ollama')
def import_ollama_task(self, job_id: str, model_dir: str, model_name: str):
    logger.info(f"Starting import_ollama_task for job {job_id}")

    try:
        _update_job_status(job_id, status="running", pipeline_stage="importing", started_at=datetime.utcnow(), celery_task_id=self.request.id)
        _emit_progress(job_id, 0, "Starting Ollama import...", "start")

        model_dir_obj = Path(model_dir)

        gguf_files = list(model_dir_obj.glob("*.gguf"))
        if not gguf_files:
            raise FileNotFoundError(f"No GGUF file found in {model_dir}")

        main_gguf = None
        mmproj_gguf = None
        for gf in gguf_files:
            if 'mmproj' in gf.name.lower():
                mmproj_gguf = gf
            else:
                main_gguf = gf

        if not main_gguf:
            main_gguf = gguf_files[0]

        _emit_progress(job_id, 20, f"Creating Modelfile for {model_name}...", "processing")

        template_name = _detect_model_architecture(model_name, main_gguf.name)

        if mmproj_gguf and 'gemma' in template_name:
            template_name = 'gemma-3-vision.modelfile'

        logger.info(f"Using template: {template_name} for {model_name}")

        modelfile_content = _generate_modelfile_from_template(template_name, main_gguf, mmproj_gguf)

        modelfile_path = model_dir_obj / "Modelfile"
        with open(modelfile_path, 'w') as f:
            f.write(modelfile_content)

        _emit_progress(job_id, 50, f"Importing {model_name} to Ollama...", "processing")
        
        import subprocess
        result = subprocess.run(
            ["ollama", "create", model_name, "-f", str(modelfile_path)],
            capture_output=True,
            text=True,
            cwd=str(model_dir_obj)
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"Ollama import failed: {result.stderr}")
        
        _update_job_status(job_id,
                          status="completed",
                          pipeline_stage="importing",
                          completed_at=datetime.utcnow(),
                          progress=100,
                          ollama_model_name=model_name)
        
        _emit_progress(job_id, 100, f"Ollama import complete: {model_name}", "complete")
        
        logger.info(f"Import task completed for job {job_id}: {model_name}")
        return {"ollama_model_name": model_name}
        
    except Exception as e:
        logger.error(f"Error in import_ollama_task: {e}", exc_info=True)
        _update_job_status(job_id, status="failed", error_message=str(e))
        _emit_progress(job_id, 0, f"Error: {str(e)}", "error")
        raise


@shared_task(bind=True, name='training.full_pipeline',
             soft_time_limit=259200, time_limit=345600)
def full_training_pipeline_task(self, job_id: str, config: dict):
    logger.info(f"Starting full_training_pipeline_task for job {job_id}")
    
    try:
        _update_job_status(job_id, status="running", started_at=datetime.utcnow())
        _emit_progress(job_id, 0, "Starting full training pipeline...", "processing")

        _emit_progress(job_id, 1, "Freeing GPU memory (unloading Ollama models)...", "processing")
        try:
            from backend.services.gpu_resource_coordinator import unload_ollama_models, get_available_vram

            initial_vram = get_available_vram()
            logger.info(f"Initial VRAM: {initial_vram.get('available_mb', 'unknown')} MB available")

            unload_result = unload_ollama_models()
            if unload_result.get("success"):
                models_unloaded = unload_result.get("models_unloaded", [])
                vram_freed = unload_result.get("vram_freed_mb", 0)
                if models_unloaded:
                    logger.info(f"Unloaded Ollama models: {models_unloaded}, freed {vram_freed} MB")
                    _emit_progress(job_id, 2, f"Freed {vram_freed} MB GPU memory", "processing")
                else:
                    logger.info("No Ollama models were loaded")
            else:
                logger.warning(f"Failed to unload Ollama models: {unload_result.get('error')}")
        except Exception as e:
            logger.warning(f"Could not unload Ollama models (non-fatal): {e}")

        from flask import current_app
        with current_app.app_context():
            job = db.session.query(TrainingJob).filter(
                TrainingJob.job_id == job_id
            ).first()
            
            if not job:
                raise ValueError(f"Job not found: {job_id}")
            
            job_config = json.loads(job.config_json) if job.config_json else {}
            job_config.update(config)
        
        parse_output_path = None
        if job_config.get("input_path"):
            _update_job_status(job_id, pipeline_stage="parsing")
            _emit_progress(job_id, 5, "Step 1/5: Parsing transcripts...", "processing")
            
            parse_result = parse_transcripts_task(job_id, 
                                                  job_config["input_path"], 
                                                  job_config.get("recursive", True))
            parse_output_path = parse_result.get("output_path")
            job_config["parse_output"] = parse_output_path
        
        filter_output_path = parse_output_path or job_config.get("dataset_path")
        if job_config.get("min_score") is not None and filter_output_path:
            _update_job_status(job_id, pipeline_stage="filtering")
            _emit_progress(job_id, 20, "Step 2/5: Filtering dataset...", "processing")
            
            filter_result = filter_dataset_task(job_id,
                                               filter_output_path,
                                               job_config.get("min_score", 0.5))
            filter_output_path = filter_result.get("output_path")
            job_config["data_path"] = filter_output_path
        elif filter_output_path:
            job_config["data_path"] = filter_output_path
        
        _update_job_status(job_id, pipeline_stage="training")
        _emit_progress(job_id, 30, "Step 3/5: Training model...", "processing")
        
        if not job_config.get("data_path"):
            raise ValueError("No dataset path available for training")
        
        with current_app.app_context():
            job.config_json = json.dumps(job_config)
            db.session.commit()
        
        train_result = finetune_model_task(job_id, job_config)
        model_dir = train_result.get("model_dir")
        
        _update_job_status(job_id, pipeline_stage="exporting")
        _emit_progress(job_id, 80, "Step 4/5: Exporting to GGUF...", "processing")
        
        export_result = export_gguf_task(job_id,
                                        model_dir,
                                        job_config.get("quantization", "q4_k_m"))
        
        _update_job_status(job_id, pipeline_stage="importing")
        _emit_progress(job_id, 90, "Step 5/5: Importing to Ollama...", "processing")
        
        model_name = job.output_model_name or job_config.get("ollama_model_name")
        if not model_name:
            model_name = f"llamax1-{job.base_model.replace('/', '-').replace(':', '-')}"
        
        import_result = import_ollama_task(job_id, model_dir, model_name)
        
        _update_job_status(job_id,
                          status="completed",
                          pipeline_stage="importing",
                          completed_at=datetime.utcnow(),
                          progress=100)
        
        _emit_progress(job_id, 100, "Full pipeline complete!", "complete")
        
        logger.info(f"Full pipeline completed for job {job_id}")
        return {
            "parse_output": parse_output_path,
            "filter_output": filter_output_path if job_config.get("min_score") is not None else None,
            "model_dir": model_dir,
            "gguf_path": export_result.get("gguf_path"),
            "ollama_model_name": import_result.get("ollama_model_name")
        }
        
    except Exception as e:
        logger.error(f"Error in full_training_pipeline_task: {e}", exc_info=True)
        _update_job_status(job_id, status="failed", error_message=str(e))
        _emit_progress(job_id, 0, f"Error: {str(e)}", "error")
        raise
