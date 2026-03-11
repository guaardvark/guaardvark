
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Dict, Any, Optional

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import or_

from backend.models import db, TrainingJob, DeviceProfile, TrainingDataset
from backend.utils.response_utils import success_response, error_response
from backend.utils.db_utils import ensure_db_session_cleanup
from pathlib import Path

TRAINING_DIR = Path(os.environ.get('GUAARDVARK_ROOT', '.')) / "training"

training_bp = Blueprint("training", __name__, url_prefix="/api/training")
logger = logging.getLogger(__name__)


@training_bp.route("/jobs", methods=["GET"])
@ensure_db_session_cleanup
def list_jobs():
    try:
        status = request.args.get("status")
        dataset_id = request.args.get("dataset_id", type=int)
        
        query = db.session.query(TrainingJob)
        
        if status:
            query = query.filter(TrainingJob.status == status)
        if dataset_id:
            query = query.filter(TrainingJob.dataset_id == dataset_id)
        
        jobs = query.order_by(TrainingJob.created_at.desc()).all()
        
        return success_response([job.to_dict() for job in jobs])
    except Exception as e:
        logger.error(f"Error listing training jobs: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/images", methods=["GET"])
@ensure_db_session_cleanup
def list_image_folders():
    try:
        images_dir = TRAINING_DIR / "images"
        if not images_dir.exists():
            return success_response([])
            
        folders = []
        for item in images_dir.iterdir():
            if item.is_dir():
                count = len(list(item.glob("*.jpg"))) + len(list(item.glob("*.png"))) + len(list(item.glob("*.jpeg")))
                folders.append({
                    "name": item.name,
                    "path": str(item),
                    "image_count": count
                })
        
        return success_response(folders)
    except Exception as e:
        logger.error(f"Error listing image folders: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/hardware", methods=["GET"])
@ensure_db_session_cleanup
def get_hardware_capabilities():
    try:
        from backend.services.hardware_service import HardwareService
        caps = HardwareService.get_system_capabilities()
        return success_response(caps)
    except Exception as e:
        logger.error(f"Error getting hardware capabilities: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/jobs", methods=["POST"])
@ensure_db_session_cleanup
def create_job():
    try:
        data = request.get_json()
        
        if not data.get("name"):
            return error_response("Job name is required", 400)
        if not data.get("base_model"):
            return error_response("Base model is required", 400)
        if not data.get("dataset_id"):
            return error_response("Dataset ID is required", 400)
        
        device_profile_id = data.get("device_profile_id")
        device_profile = None
        if device_profile_id:
            device_profile = db.session.get(DeviceProfile, device_profile_id)
            if not device_profile:
                return error_response("Device profile not found", 400)
            if not device_profile.is_active:
                return error_response("Device profile is not active", 400)
            
            config = data.get("config", {})
            batch_size = config.get("batch_size", device_profile.max_batch_size)
            seq_length = config.get("seq_length", device_profile.max_seq_length)
            
            if batch_size > device_profile.max_batch_size:
                return error_response(
                    f"Batch size {batch_size} exceeds device profile maximum {device_profile.max_batch_size}",
                    400
                )
            
            if seq_length > device_profile.max_seq_length:
                return error_response(
                    f"Sequence length {seq_length} exceeds device profile maximum {device_profile.max_seq_length}",
                    400
                )
            
            if device_profile.device_type == "gpu" and device_profile.gpu_vram_mb:
                estimated_vram = batch_size * 2048
                if estimated_vram > device_profile.gpu_vram_mb * 0.9:
                    return error_response(
                        f"Estimated VRAM usage ({estimated_vram}MB) exceeds available VRAM ({device_profile.gpu_vram_mb}MB)",
                        400
                    )
        
        job_id = str(uuid.uuid4())
        
        queue = "training"
        if device_profile:
            if device_profile.device_type == "gpu":
                queue = "training_gpu"
            else:
                queue = "training"
        
        job = TrainingJob(
            job_id=job_id,
            name=data["name"],
            base_model=data["base_model"],
            output_model_name=data.get("output_model_name"),
            dataset_id=data["dataset_id"],
            config_json=json.dumps(data.get("config", {})),
            device_profile_id=device_profile_id,
            status="pending",
            pipeline_stage="pending"
        )
        
        db.session.add(job)
        db.session.commit()
        
        if data.get("start_immediately", False):
            from backend.tasks.training_tasks import finetune_model_task
            task = finetune_model_task.apply_async(
                args=[job_id, json.loads(job.config_json)],
                queue=queue
            )
            job.celery_task_id = task.id
            job.status = "running"
            db.session.commit()
        
        logger.info(f"Created training job: {job_id} - {job.name} (queue: {queue})")
        return success_response(job.to_dict(), status_code=201)
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error creating training job: {e}", exc_info=True)
        return error_response(f"Database error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Error creating training job: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/jobs/<int:job_id>", methods=["GET"])
@ensure_db_session_cleanup
def get_job(job_id):
    try:
        job = db.session.get(TrainingJob, job_id)
        if not job:
            return error_response("Job not found", 404)
        
        return success_response(job.to_dict())
    except Exception as e:
        logger.error(f"Error getting training job {job_id}: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/jobs/<int:job_id>", methods=["DELETE"])
@ensure_db_session_cleanup
def delete_job(job_id):
    try:
        job = db.session.get(TrainingJob, job_id)
        if not job:
            return error_response("Job not found", 404)
        
        if job.status == "running":
            try:
                from celery import current_app as celery_app
                if job.celery_task_id:
                    celery_app.control.revoke(job.celery_task_id, terminate=True)
            except Exception as e:
                logger.warning(f"Could not cancel Celery task: {e}")
        
        db.session.delete(job)
        db.session.commit()
        
        logger.info(f"Deleted training job: {job_id}")
        return success_response({"message": "Job deleted"})
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error deleting training job: {e}", exc_info=True)
        return error_response(f"Database error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Error deleting training job: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/jobs/<int:job_id>/cancel", methods=["POST"])
@ensure_db_session_cleanup
def cancel_job(job_id):
    import os
    import signal
    import time

    try:
        job = db.session.get(TrainingJob, job_id)
        if not job:
            return error_response("Job not found", 404)

        if job.status not in ["pending", "running"]:
            return error_response(f"Job cannot be cancelled (status: {job.status})", 400)

        pid_terminated = False

        if job.pid:
            try:
                logger.info(f"Sending SIGTERM to PID {job.pid}")
                os.kill(job.pid, signal.SIGTERM)
                pid_terminated = True

                time.sleep(2)

                try:
                    os.kill(job.pid, 0)
                    logger.info(f"Process {job.pid} still running, sending SIGKILL")
                    time.sleep(3)
                    try:
                        os.kill(job.pid, 0)
                        os.kill(job.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                except ProcessLookupError:
                    logger.info(f"Process {job.pid} terminated gracefully")

            except ProcessLookupError:
                logger.info(f"Process {job.pid} already terminated")
            except PermissionError:
                logger.warning(f"Permission denied to terminate PID {job.pid}")
            except Exception as e:
                logger.warning(f"Error terminating process: {e}")

        if job.celery_task_id:
            try:
                from celery import current_app as celery_app
                celery_app.control.revoke(job.celery_task_id, terminate=True)
                logger.info(f"Revoked Celery task: {job.celery_task_id}")
            except Exception as e:
                logger.warning(f"Could not revoke Celery task: {e}")

        job.status = "cancelled"
        job.error_message = "Cancelled by user"
        job.pid = None
        db.session.commit()

        logger.info(f"Cancelled training job: {job_id} (pid_terminated={pid_terminated})")
        return success_response({
            **job.to_dict(),
            "pid_terminated": pid_terminated
        })
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error cancelling training job: {e}", exc_info=True)
        return error_response(f"Database error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Error cancelling training job: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/jobs/<int:job_id>/resume", methods=["POST"])
@ensure_db_session_cleanup
def resume_job(job_id):
    try:
        job = db.session.get(TrainingJob, job_id)
        if not job:
            return error_response("Job not found", 404)

        if job.status not in ["failed", "cancelled"]:
            return error_response(f"Job cannot be resumed (status: {job.status})", 400)

        if not job.is_resumable:
            return error_response("Job is not resumable (no checkpoint available)", 400)

        job.status = "pending"
        job.error_message = None
        job.progress = 0
        job.pid = None
        db.session.commit()

        job_config = {}
        if job.config_json:
            import json
            job_config = json.loads(job.config_json)

        try:
            from backend.tasks.training_tasks import finetune_model_task

            task = finetune_model_task.apply_async(
                args=[job.job_id, job_config],
                kwargs={"resume": True},
                queue="training_gpu"
            )

            job.celery_task_id = task.id
            job.status = "running"
            job.pipeline_stage = "training"
            db.session.commit()

            logger.info(f"Resumed training job: {job_id} with task {task.id}")
            return success_response({
                **job.to_dict(),
                "celery_task_id": task.id,
                "resumed_from_checkpoint": job.checkpoint_path
            })

        except ImportError as e:
            logger.error(f"Could not import training task: {e}")
            return error_response("Training tasks not available", 500)

    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error resuming training job: {e}", exc_info=True)
        return error_response(f"Database error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Error resuming training job: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/device-profiles", methods=["GET"])
@ensure_db_session_cleanup
def list_device_profiles():
    try:
        profiles = db.session.query(DeviceProfile).filter(
            DeviceProfile.is_active == True
        ).order_by(DeviceProfile.is_default.desc(), DeviceProfile.name).all()
        
        return success_response([profile.to_dict() for profile in profiles])
    except Exception as e:
        logger.error(f"Error listing device profiles: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/device-profiles", methods=["POST"])
@ensure_db_session_cleanup
def create_device_profile():
    try:
        data = request.get_json()
        
        if not data.get("name"):
            return error_response("Profile name is required", 400)
        
        existing = db.session.query(DeviceProfile).filter(
            DeviceProfile.name == data["name"]
        ).first()
        if existing:
            return error_response("Profile name already exists", 400)
        
        if data.get("is_default"):
            db.session.query(DeviceProfile).update({"is_default": False})
        
        profile = DeviceProfile(
            name=data["name"],
            device_type=data.get("device_type", "gpu"),
            gpu_vram_mb=data.get("gpu_vram_mb"),
            system_ram_mb=data.get("system_ram_mb"),
            max_batch_size=data.get("max_batch_size", 2),
            max_seq_length=data.get("max_seq_length", 2048),
            supports_4bit=data.get("supports_4bit", True),
            requires_cpu_offload=data.get("requires_cpu_offload", False),
            is_default=data.get("is_default", False),
            is_active=data.get("is_active", True)
        )
        
        db.session.add(profile)
        db.session.commit()
        
        logger.info(f"Created device profile: {profile.name}")
        return success_response(profile.to_dict(), status_code=201)
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error creating device profile: {e}", exc_info=True)
        return error_response(f"Database error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Error creating device profile: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/device-profiles/<int:profile_id>", methods=["PUT"])
@ensure_db_session_cleanup
def update_device_profile(profile_id):
    try:
        profile = db.session.get(DeviceProfile, profile_id)
        if not profile:
            return error_response("Profile not found", 404)
        
        data = request.get_json()
        
        if "name" in data:
            if data["name"] != profile.name:
                existing = db.session.query(DeviceProfile).filter(
                    DeviceProfile.name == data["name"]
                ).first()
                if existing:
                    return error_response("Profile name already exists", 400)
            profile.name = data["name"]
        
        if "device_type" in data:
            profile.device_type = data["device_type"]
        if "gpu_vram_mb" in data:
            profile.gpu_vram_mb = data["gpu_vram_mb"]
        if "system_ram_mb" in data:
            profile.system_ram_mb = data["system_ram_mb"]
        if "max_batch_size" in data:
            profile.max_batch_size = data["max_batch_size"]
        if "max_seq_length" in data:
            profile.max_seq_length = data["max_seq_length"]
        if "supports_4bit" in data:
            profile.supports_4bit = data["supports_4bit"]
        if "requires_cpu_offload" in data:
            profile.requires_cpu_offload = data["requires_cpu_offload"]
        if "is_active" in data:
            profile.is_active = data["is_active"]
        
        if "is_default" in data and data["is_default"]:
            db.session.query(DeviceProfile).filter(
                DeviceProfile.id != profile_id
            ).update({"is_default": False})
            profile.is_default = True
        elif "is_default" in data:
            profile.is_default = False
        
        db.session.commit()
        
        logger.info(f"Updated device profile: {profile_id}")
        return success_response(profile.to_dict())
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error updating device profile: {e}", exc_info=True)
        return error_response(f"Database error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Error updating device profile: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/device-profiles/<int:profile_id>", methods=["DELETE"])
@ensure_db_session_cleanup
def delete_device_profile(profile_id):
    try:
        profile = db.session.get(DeviceProfile, profile_id)
        if not profile:
            return error_response("Profile not found", 404)
        
        jobs_using = db.session.query(TrainingJob).filter(
            TrainingJob.device_profile_id == profile_id
        ).count()
        if jobs_using > 0:
            return error_response(f"Cannot delete profile: {jobs_using} job(s) are using it", 400)
        
        db.session.delete(profile)
        db.session.commit()
        
        logger.info(f"Deleted device profile: {profile_id}")
        return success_response({"message": "Profile deleted"})
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error deleting device profile: {e}", exc_info=True)
        return error_response(f"Database error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Error deleting device profile: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/base-models", methods=["GET"])
@ensure_db_session_cleanup
def list_base_models():
    try:
        from backend.api.model_api import get_available_ollama_models
        
        models = get_available_ollama_models()
        
        base_models = [m for m in models if ":" in m.get("name", "")]
        
        return success_response(base_models)
    except Exception as e:
        logger.error(f"Error listing base models: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/pipeline/parse", methods=["POST"])
@ensure_db_session_cleanup
def start_parse_job():
    try:
        data = request.get_json()
        
        if not data.get("input_path"):
            return error_response("input_path is required", 400)
        
        job_id = str(uuid.uuid4())
        job = TrainingJob(
            job_id=job_id,
            name=data.get("name", f"Parse: {data['input_path']}"),
            pipeline_stage="parsing",
            status="pending",
            config_json=json.dumps({
                "input_path": data["input_path"],
                "recursive": data.get("recursive", True)
            })
        )
        
        db.session.add(job)
        db.session.commit()
        
        try:
            from backend.tasks.training_tasks import parse_transcripts_task
            task = parse_transcripts_task.apply_async(
                args=[job_id, data["input_path"], data.get("recursive", True)],
                queue="training"
            )
            job.celery_task_id = task.id
            job.status = "running"
            db.session.commit()
            logger.info(f"Started parse task for job {job_id}: {task.id}")
        except Exception as e:
            logger.error(f"Failed to start parse task: {e}", exc_info=True)
        
        logger.info(f"Created parse job: {job_id}")
        return success_response(job.to_dict(), status_code=201)
    except Exception as e:
        logger.error(f"Error starting parse job: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/pipeline/filter", methods=["POST"])
@ensure_db_session_cleanup
def start_filter_job():
    try:
        data = request.get_json()

        if not data.get("input_path"):
            return error_response("input_path is required", 400)

        job_id = str(uuid.uuid4())
        job = TrainingJob(
            job_id=job_id,
            name=data.get("name", f"Filter: {data['input_path']}"),
            pipeline_stage="filtering",
            status="pending",
            config_json=json.dumps({
                "input_path": data["input_path"],
                "min_score": data.get("min_score", 0.5)
            })
        )

        db.session.add(job)
        db.session.commit()

        try:
            from backend.tasks.training_tasks import filter_dataset_task
            task = filter_dataset_task.apply_async(
                args=[job_id, data["input_path"], data.get("min_score", 0.5)],
                queue="training"
            )
            job.celery_task_id = task.id
            job.status = "running"
            db.session.commit()
            logger.info(f"Started filter task for job {job_id}: {task.id}")
        except Exception as e:
            logger.error(f"Failed to start filter task: {e}", exc_info=True)

        logger.info(f"Created filter job: {job_id}")
        return success_response(job.to_dict(), status_code=201)
    except Exception as e:
        logger.error(f"Error starting filter job: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/jobs/<int:job_id>/export", methods=["POST"])
@ensure_db_session_cleanup
def export_job_to_gguf(job_id):
    try:
        job = db.session.get(TrainingJob, job_id)
        if not job:
            return error_response("Job not found", 404)

        if job.status != "completed" or job.pipeline_stage not in ["training", "completed"]:
            return error_response(
                f"Job must be completed before export (status: {job.status}, stage: {job.pipeline_stage})",
                400
            )

        if not job.lora_path:
            return error_response("No LoRA adapter found for this job", 400)

        lora_path = Path(job.lora_path)
        if not lora_path.exists():
            return error_response(f"LoRA adapter path not found: {job.lora_path}", 400)

        data = request.get_json() or {}
        quantization = data.get("quantization", "q4_k_m")

        job.pipeline_stage = "exporting"
        job.status = "running"
        job.quantization_level = quantization
        db.session.commit()

        try:
            from backend.tasks.training_tasks import export_gguf_task
            task = export_gguf_task.apply_async(
                args=[job.job_id, str(lora_path), quantization],
                queue="training"
            )
            job.celery_task_id = task.id
            db.session.commit()
            logger.info(f"Started GGUF export task for job {job_id}: {task.id}")
        except Exception as e:
            job.status = "failed"
            job.error_message = f"Failed to start export task: {str(e)}"
            db.session.commit()
            logger.error(f"Failed to start export task: {e}", exc_info=True)
            return error_response(f"Failed to start export: {str(e)}", 500)

        return success_response({
            "message": f"Export started for job {job_id}",
            "job": job.to_dict(),
            "quantization": quantization
        }, status_code=202)
    except Exception as e:
        logger.error(f"Error exporting job {job_id}: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/jobs/<int:job_id>/import-ollama", methods=["POST"])
@ensure_db_session_cleanup
def import_job_to_ollama(job_id):
    try:
        job = db.session.get(TrainingJob, job_id)
        if not job:
            return error_response("Job not found", 404)

        if not job.gguf_path:
            return error_response("No GGUF file found. Export to GGUF first.", 400)

        gguf_path = Path(job.gguf_path)
        if not gguf_path.exists():
            return error_response(f"GGUF file not found: {job.gguf_path}", 400)

        data = request.get_json() or {}
        model_name = data.get("model_name") or job.output_model_name or f"guaardvark-{job.name.lower().replace(' ', '-')}"

        job.pipeline_stage = "importing"
        job.status = "running"
        db.session.commit()

        try:
            from backend.tasks.training_tasks import import_ollama_task
            task = import_ollama_task.apply_async(
                args=[job.job_id, str(gguf_path), model_name],
                queue="training"
            )
            job.celery_task_id = task.id
            db.session.commit()
            logger.info(f"Started Ollama import task for job {job_id}: {task.id}")
        except Exception as e:
            job.status = "failed"
            job.error_message = f"Failed to start import task: {str(e)}"
            db.session.commit()
            logger.error(f"Failed to start import task: {e}", exc_info=True)
            return error_response(f"Failed to start import: {str(e)}", 500)

        return success_response({
            "message": f"Ollama import started for job {job_id}",
            "job": job.to_dict(),
            "model_name": model_name
        }, status_code=202)
    except Exception as e:
        logger.error(f"Error importing job {job_id} to Ollama: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/jobs/<int:job_id>/export-to-ollama", methods=["POST"])
@ensure_db_session_cleanup
def export_to_ollama(job_id):
    try:
        job = db.session.get(TrainingJob, job_id)
        if not job:
            return error_response("Job not found", 404)

        if job.status != "completed" or job.pipeline_stage not in ["training", "completed", "exporting"]:
            if not job.gguf_path:
                return error_response(
                    f"Job must be completed before export (status: {job.status}, stage: {job.pipeline_stage})",
                    400
                )

        data = request.get_json() or {}
        quantization = data.get("quantization", "q4_k_m")
        model_name = data.get("model_name") or job.output_model_name or f"guaardvark-{job.name.lower().replace(' ', '-')}"

        if job.gguf_path and Path(job.gguf_path).exists():
            logger.info(f"GGUF already exists at {job.gguf_path}, skipping to import")
            job.pipeline_stage = "importing"
            job.status = "running"
            db.session.commit()

            from backend.tasks.training_tasks import import_ollama_task
            task = import_ollama_task.apply_async(
                args=[job.job_id, job.gguf_path, model_name],
                queue="training"
            )
            job.celery_task_id = task.id
            db.session.commit()

            return success_response({
                "message": f"Ollama import started (GGUF already exists)",
                "job": job.to_dict(),
                "model_name": model_name,
                "skipped_export": True
            }, status_code=202)

        if not job.lora_path:
            return error_response("No LoRA adapter found for this job", 400)

        lora_path = Path(job.lora_path)
        if not lora_path.exists():
            return error_response(f"LoRA adapter path not found: {job.lora_path}", 400)

        job.pipeline_stage = "exporting"
        job.status = "running"
        job.output_model_name = model_name
        job.quantization_level = quantization
        db.session.commit()

        try:
            from backend.tasks.training_tasks import export_gguf_task, import_ollama_task
            from celery import chain

            workflow = chain(
                export_gguf_task.s(job.job_id, str(lora_path), quantization),
                import_ollama_task.s(model_name)
            )
            result = workflow.apply_async(queue="training")
            job.celery_task_id = result.id
            db.session.commit()
            logger.info(f"Started export-to-ollama workflow for job {job_id}: {result.id}")
        except Exception as e:
            job.status = "failed"
            job.error_message = f"Failed to start export-to-ollama workflow: {str(e)}"
            db.session.commit()
            logger.error(f"Failed to start export-to-ollama workflow: {e}", exc_info=True)
            return error_response(f"Failed to start workflow: {str(e)}", 500)

        return success_response({
            "message": f"Export to Ollama started for job {job_id}",
            "job": job.to_dict(),
            "model_name": model_name,
            "quantization": quantization
        }, status_code=202)
    except Exception as e:
        logger.error(f"Error in export-to-ollama for job {job_id}: {e}", exc_info=True)
        return error_response(str(e), 500)
