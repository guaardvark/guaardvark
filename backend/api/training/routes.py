
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


def _not_started(job, what: str, exc: Exception):
    """Answer for a job whose task was not queued: the job is failed with the
    reason, so it does not sit at pending or running with nothing behind it."""
    from backend.celery_dispatch import TaskNotStarted

    job.status = "failed"
    job.error_message = f"Failed to start {what} task: {exc}"
    db.session.commit()
    if isinstance(exc, TaskNotStarted):
        return error_response(f"Failed to start {what}: {exc}", 503, exc.code)
    return error_response(f"Failed to start {what}: {exc}", 500)


# Fine-tunes run on the training worker: its Celery process waits for the GPU
# and supervises the trainer subprocess, which claims the card through the
# cross-process lease, so the main worker stays free.
TRAINING_QUEUE = "training"


def _dispatch_finetune(job, *, resume: bool = False):
    """Queue the job's fine-tune on the training worker and mark it running.
    Raises TaskNotStarted when the queue does not take it."""
    from backend.tasks.training_tasks import finetune_model_task
    config = json.loads(job.config_json) if job.config_json else {}
    task = finetune_model_task.apply_async(
        args=[job.job_id, config], kwargs={"resume": resume}, queue=TRAINING_QUEUE)
    job.celery_task_id = task.id
    job.status = "running"
    job.pipeline_stage = "training"
    job.error_message = None
    db.session.commit()
    return task


def _cancel(job) -> bool:
    """Mark the job cancelled first (so its task, reading the status, ends it
    as cancelled), then stop its trainer and drop its queued task. Returns
    whether a trainer process was signalled."""
    job.status = "cancelled"
    job.error_message = "Cancelled by user"
    pid, job.pid = job.pid, None
    db.session.commit()
    pid_terminated = False
    if pid:
        from backend.services.training_runner import stop_trainer
        pid_terminated = stop_trainer(pid)
    if job.celery_task_id:
        try:
            from celery import current_app as celery_app
            # No terminate: a running task notices the cancel itself and
            # releases the GPU; killing the pool process would strand its lease.
            celery_app.control.revoke(job.celery_task_id)
        except Exception as e:
            logger.warning(f"Could not revoke Celery task: {e}")
    return pid_terminated


def _job_dict(job) -> dict:
    """The job as the Training page reads it, with whether its base model's
    export to Ollama has been verified (the page offers Export only then) and,
    when it has, the levels Export offers and why it cannot run now
    (export_blocked, with export_blocked_code; GGUF_CONVERTER_MISSING sends
    the page to the converter's Install)."""
    from backend.services import training_base_models, training_gguf_converter
    verified = training_base_models.export_verified(job.base_model)
    code = blocked = None
    if verified:
        from backend.tasks.training_tasks import export_block
        block = export_block(job.base_model)
        if block:
            code, blocked = block
    return {**job.to_dict(), "export_verified": verified, "export_blocked": blocked,
            "export_blocked_code": code,
            "export_levels": training_gguf_converter.levels() if verified else []}


def _output_name_problem(name) -> Optional[str]:
    """Why a trained model may not take this name, or None. The trainer makes
    a folder of that name under the training models folder, so it must stay
    a relative name without ``..`` (an Ollama name such as "me/model:tag" is
    fine)."""
    if name is None or name == "":
        return None
    if not isinstance(name, str):
        return "The output model name must be text."
    if (os.path.isabs(name) or "\\" in name or "\x00" in name
            or any(part in ("", ".", "..") for part in name.split("/"))):
        return "The output model name cannot be a path."
    return None


def _export_refused(job):
    """The 409 for a job whose export cannot run now, or None."""
    from backend.tasks.training_tasks import export_block
    block = export_block(job.base_model, job.lora_path)
    if block:
        return error_response(block[1], 409, block[0])
    return None


def _export_level(data: dict):
    """(level, None) for the request's quantization, or (None, the 400)."""
    from backend.services import training_gguf_converter
    try:
        return training_gguf_converter.level_for(data.get("quantization")), None
    except ValueError as e:
        return None, error_response(str(e), 400, "UNSUPPORTED_LEVEL")


def _put_back(job, before, what: str, exc: Exception):
    """Answer for a step started on an existing job (export, import, resume)
    whose task was not queued: nothing ran, so the job returns to the status
    and stage it had, and the step can be started again."""
    job.status, job.pipeline_stage = before
    db.session.commit()
    return error_response(f"Failed to start {what}: {exc}", 503, exc.code)


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

        return success_response([_job_dict(job) for job in jobs])
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


@training_bp.route("/datasets/inspect", methods=["GET"])
def inspect_dataset():
    """What a dataset path holds: files, rows, usable rows, formats, a few
    clipped sample rows and the first problems (file and line, never text).
    ?path= a .jsonl or .json file or a folder on this machine; ~ is followed."""
    from backend.services.training.scripts import dataset_formats
    try:
        return success_response(dataset_formats.inspect(request.args.get("path", "")))
    except Exception as e:
        logger.error(f"Error inspecting dataset path: {e}", exc_info=True)
        return error_response(f"Could not read the dataset: {type(e).__name__}", 500)


def training_datasets_dir() -> Path:
    """Where parsed transcripts are written and the dataset picker opens."""
    from backend.config import STORAGE_DIR
    return Path(STORAGE_DIR) / "training" / "datasets"


@training_bp.route("/datasets/locations", methods=["GET"])
def dataset_locations():
    """Starting folders for the dataset picker: Guaardvark's training datasets
    folder and the home folder, each with whether it exists, and the one the
    picker opens in."""
    datasets_dir = training_datasets_dir()
    home = Path(os.path.expanduser("~"))
    locations = [
        {"id": "datasets", "label": "Training datasets", "path": str(datasets_dir),
         "exists": datasets_dir.is_dir()},
        {"id": "home", "label": "Home", "path": str(home), "exists": home.is_dir()},
    ]
    default = next((loc["path"] for loc in locations if loc["exists"]), "~")
    return success_response({"locations": locations, "default": default})


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


@training_bp.route("/libraries", methods=["GET"])
def get_training_libraries():
    """Settings > Training libraries: the pinned libraries, what is installed,
    the hardware verdict and the install or remove in progress. ?plan=1 also
    hands out the one-use token the modal's Install and Remove buttons send."""
    try:
        from backend.services import training_libraries
        return success_response(training_libraries.status(with_plan_token=request.args.get("plan") == "1"))
    except Exception as e:
        logger.error(f"Error reading training libraries: {e}", exc_info=True)
        return error_response(str(e), 500)


def _libraries_in_use():
    """Why the training libraries may not change now, or None. A running job
    (training, export or import) has them loaded."""
    running = db.session.query(TrainingJob).filter(TrainingJob.status == "running").count()
    if running:
        return (f"{running} training job(s) running. Installing or removing the training "
                f"libraries now could stop them; wait for them to finish or cancel them.")
    return None


@training_bp.route("/libraries/install", methods=["POST"])
@ensure_db_session_cleanup
def install_training_libraries():
    """Install the pinned training libraries into the backend's Python.

    Only the modal's Install click sends what this needs:
    {"confirm": "install", "plan_token": <from GET /libraries?plan=1>,
     "anyway": true when the hardware verdict says not practical}."""
    from backend.services import training_libraries
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != "install":
        return error_response(training_libraries.NEEDS_CLICK, 403, "NEEDS_CLICK")
    try:
        in_use = _libraries_in_use()
        if in_use:
            return error_response(in_use, 409, "TRAINING_RUNNING")
        result = training_libraries.start_install(data.get("plan_token"), anyway=data.get("anyway") is True)
        logger.info("Training libraries install started (anyway=%s)", data.get("anyway") is True)
        return success_response(result, "Install started", status_code=202)
    except training_libraries.Refused as e:
        return error_response(str(e), e.status, e.code)
    except Exception as e:
        logger.error(f"Error starting the training libraries install: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/libraries/remove", methods=["POST"])
@ensure_db_session_cleanup
def remove_training_libraries():
    """Uninstall the training libraries and what their install added.
    Body: {"confirm": "remove", "plan_token": <from GET /libraries?plan=1>}."""
    from backend.services import training_libraries
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != "remove":
        return error_response(training_libraries.NEEDS_CLICK, 403, "NEEDS_CLICK")
    try:
        in_use = _libraries_in_use()
        if in_use:
            return error_response(in_use, 409, "TRAINING_RUNNING")
        result = training_libraries.start_remove(data.get("plan_token"))
        logger.info("Training libraries remove started")
        return success_response(result, "Remove started", status_code=202)
    except training_libraries.Refused as e:
        return error_response(str(e), e.status, e.code)
    except Exception as e:
        logger.error(f"Error starting the training libraries remove: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/libraries/gguf-converter", methods=["GET"])
def get_gguf_converter():
    """Settings > Training libraries > GGUF converter: the pinned llama.cpp
    converter Export uses, whether it is installed and intact, and the install
    in progress. ?plan=1 also hands out the one-use token Install and Remove send."""
    try:
        from backend.services import training_gguf_converter
        return success_response(
            training_gguf_converter.status(with_plan_token=request.args.get("plan") == "1"))
    except Exception as e:
        logger.error(f"Error reading the GGUF converter: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/libraries/gguf-converter/install", methods=["POST"])
def install_gguf_converter():
    """Fetch the pinned converter files from GitHub and check them.
    Only the Install click sends what this needs:
    {"confirm": "install", "plan_token": <from GET /libraries/gguf-converter?plan=1>}."""
    from backend.services import training_gguf_converter
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != "install":
        return error_response(training_gguf_converter.NEEDS_CLICK, 403, "NEEDS_CLICK")
    try:
        result = training_gguf_converter.start_install(data.get("plan_token"))
        logger.info("GGUF converter install started")
        return success_response(result, "Install started", status_code=202)
    except training_gguf_converter.Refused as e:
        return error_response(str(e), e.status, e.code)
    except Exception as e:
        logger.error(f"Error starting the GGUF converter install: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/libraries/gguf-converter/remove", methods=["POST"])
@ensure_db_session_cleanup
def remove_gguf_converter():
    """Delete the installed converter.
    Body: {"confirm": "remove", "plan_token": <from GET /libraries/gguf-converter?plan=1>}."""
    from backend.services import training_gguf_converter
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != "remove":
        return error_response(training_gguf_converter.NEEDS_CLICK, 403, "NEEDS_CLICK")
    exporting = db.session.query(TrainingJob).filter(
        TrainingJob.status == "running", TrainingJob.pipeline_stage == "exporting").count()
    if exporting:
        return error_response(f"{exporting} export(s) running; remove the converter once they "
                              f"finish.", 409, "EXPORT_RUNNING")
    try:
        return success_response(training_gguf_converter.start_remove(data.get("plan_token")), "Removed")
    except training_gguf_converter.Refused as e:
        return error_response(str(e), e.status, e.code)
    except Exception as e:
        logger.error(f"Error removing the GGUF converter: {e}", exc_info=True)
        return error_response(str(e), 500)


def _libraries_missing_response():
    """The refusal for a job that would fail for want of the training
    libraries (one missing, or a requirement of theirs unmet), naming where
    to install or repair them; None when they are ready. Export and import
    run without them (the merge needs torch, transformers and peft only)."""
    from backend.services import training_libraries
    reason = training_libraries.unavailable_reason()
    if reason:
        return error_response(reason, 409, "TRAINING_LIBRARIES_MISSING")
    return None


def _job_refusal(base_model, job_config: dict):
    """The refusal for a job its base model cannot run here, or for settings
    past the limits the model declares; None when it may be created."""
    from backend.services import training_base_models
    reason = training_base_models.refusal_for_job(base_model, vision=bool(job_config.get("images_path")))
    if reason:
        return error_response(reason, 400, "BASE_MODEL_UNAVAILABLE")
    entry = training_base_models.get(base_model)
    if entry:
        for key, limit_key, label in (("batch_size", "max_batch_size", "Batch size"),
                                      ("seq_length", "max_seq_length", "Sequence length")):
            value = job_config.get(key)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                return error_response(f"{label} must be a whole number of 1 or more", 400)
            if value > entry[limit_key]:
                return error_response(f"{label} {value} is above the {entry[limit_key]} "
                                      f"{entry['name']} is set up for", 400)
    steps = job_config.get("steps")
    if steps is not None and (isinstance(steps, bool) or not isinstance(steps, int) or steps < 1):
        return error_response("steps must be a whole number of 1 or more, or left out", 400)
    return None


def _start_refusal(base_model, dataset_id, job_config, device_profile_id):
    """Why a fine-tune with these settings may not be created or started, as
    an error response, or None. Creating a job and starting a pending one
    apply the same checks."""
    missing_libraries = _libraries_missing_response()
    if missing_libraries:
        return missing_libraries

    if not isinstance(job_config, dict):
        return error_response("config must be an object", 400)
    refusal = _job_refusal(base_model, job_config)
    if refusal:
        return refusal

    # The trainer reads the dataset's file when the config names none, so a
    # dataset it cannot read is refused here rather than failing the run.
    dataset = db.session.get(TrainingDataset, dataset_id)
    if not dataset:
        return error_response("Dataset not found", 400)
    if not (job_config.get("data_path") or job_config.get("dataset_path")):
        from backend.tasks.training_tasks import dataset_training_files
        _, reason = dataset_training_files(dataset.path)
        if reason:
            return error_response(f"Dataset '{dataset.name}' cannot be trained on: {reason}", 400)

    if device_profile_id:
        device_profile = db.session.get(DeviceProfile, device_profile_id)
        if not device_profile:
            return error_response("Device profile not found", 400)
        if not device_profile.is_active:
            return error_response("Device profile is not active", 400)

        batch_size = job_config.get("batch_size", device_profile.max_batch_size)
        seq_length = job_config.get("seq_length", device_profile.max_seq_length)

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

        from backend.services import training_base_models
        entry = training_base_models.get(base_model)
        if entry and device_profile.device_type == "gpu" and device_profile.gpu_vram_mb:
            if entry["vram_mb"] > device_profile.gpu_vram_mb:
                return error_response(
                    f"{entry['name']} is budgeted {entry['vram_mb']} MB of GPU memory "
                    f"({entry['vram_measured']}); this device profile has {device_profile.gpu_vram_mb} MB",
                    400
                )
    return None


@training_bp.route("/jobs", methods=["POST"])
@ensure_db_session_cleanup
def create_job():
    try:
        data = request.get_json(silent=True) or {}

        if not data.get("name"):
            return error_response("Job name is required", 400)
        if not data.get("base_model"):
            return error_response("Base model is required", 400)
        if not data.get("dataset_id"):
            return error_response("Dataset ID is required", 400)

        name_problem = _output_name_problem(data.get("output_model_name"))
        if name_problem:
            return error_response(name_problem, 400)

        device_profile_id = data.get("device_profile_id")
        refusal = _start_refusal(data["base_model"], data["dataset_id"], data.get("config") or {},
                                 device_profile_id)
        if refusal:
            return refusal

        job_id = str(uuid.uuid4())

        job = TrainingJob(
            job_id=job_id,
            name=data["name"],
            base_model=data["base_model"],
            output_model_name=data.get("output_model_name"),
            dataset_id=data["dataset_id"],
            config_json=json.dumps(data.get("config") or {}),
            device_profile_id=device_profile_id,
            status="pending",
            pipeline_stage="pending"
        )
        
        db.session.add(job)
        db.session.commit()
        
        if data.get("start_immediately", False):
            from backend.celery_dispatch import TaskNotStarted
            try:
                _dispatch_finetune(job)
            except TaskNotStarted as e:
                return _not_started(job, "training", e)

        logger.info(f"Created training job: {job_id} - {job.name}")
        return success_response(_job_dict(job), status_code=201)
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

        return success_response(_job_dict(job))
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
        
        if job.status in ("pending", "running"):
            _cancel(job)

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
    """Cancel a pending or running job: it is marked cancelled, its trainer
    process (the job's pid) is stopped, and its task ends as cancelled,
    releasing the GPU."""
    try:
        job = db.session.get(TrainingJob, job_id)
        if not job:
            return error_response("Job not found", 404)

        if job.status not in ["pending", "running"]:
            return error_response(f"Job cannot be cancelled (status: {job.status})", 400)

        pid_terminated = _cancel(job)

        logger.info(f"Cancelled training job: {job_id} (pid_terminated={pid_terminated})")
        return success_response({
            **_job_dict(job),
            "pid_terminated": pid_terminated
        })
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error cancelling training job: {e}", exc_info=True)
        return error_response(f"Database error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Error cancelling training job: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/jobs/<int:job_id>/start", methods=["POST"])
@ensure_db_session_cleanup
def start_job(job_id):
    """Start a fine-tune that was created without starting (pending, never
    queued), after the same checks as creating it."""
    try:
        job = db.session.get(TrainingJob, job_id)
        if not job:
            return error_response("Job not found", 404)
        if job.status != "pending" or job.celery_task_id or job.pipeline_stage not in (None, "pending"):
            return error_response(
                f"Only a training job that has not started can be started (status: {job.status}, "
                f"stage: {job.pipeline_stage}). Use Resume for a failed or cancelled run.", 400)
        if not job.base_model or not job.dataset_id:
            return error_response("This job has no base model or dataset to train on.", 400)

        config = json.loads(job.config_json) if job.config_json else {}
        refusal = _start_refusal(job.base_model, job.dataset_id, config, job.device_profile_id)
        if refusal:
            return refusal

        from backend.celery_dispatch import TaskNotStarted
        before = (job.status, job.pipeline_stage)
        try:
            _dispatch_finetune(job)
        except TaskNotStarted as e:
            return _put_back(job, before, "training", e)

        logger.info(f"Started training job: {job_id} with task {job.celery_task_id}")
        return success_response(_job_dict(job))
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error starting training job: {e}", exc_info=True)
        return error_response(f"Database error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Error starting training job {job_id}: {e}", exc_info=True)
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

        missing_libraries = _libraries_missing_response()
        if missing_libraries:
            return missing_libraries

        before = (job.status, job.pipeline_stage)
        job.status = "pending"
        job.error_message = None
        job.progress = 0
        job.pid = None
        db.session.commit()

        from backend.celery_dispatch import TaskNotStarted
        try:
            try:
                task = _dispatch_finetune(job, resume=True)
            except TaskNotStarted as e:
                # Back to failed or cancelled, so Resume stays available.
                job.error_message = str(e)
                return _put_back(job, before, "resume", e)

            logger.info(f"Resumed training job: {job_id} with task {task.id}")
            return success_response({
                **_job_dict(job),
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
    """The declared base models (training_base_models.BASE_MODELS) with
    whether each is downloaded and fits this GPU, plus the download in
    progress. ?plan=1 also hands out the one-use token Download and Remove send."""
    try:
        from backend.services import training_base_models
        return success_response(
            training_base_models.status(with_plan_token=request.args.get("plan") == "1"))
    except Exception as e:
        logger.error(f"Error listing base models: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/base-models/install", methods=["POST"])
@ensure_db_session_cleanup
def install_base_model():
    """Download one declared base model from Hugging Face, without a token.

    Only the Download click sends what this needs:
    {"confirm": "download", "model": <id>, "plan_token": <from GET /base-models?plan=1>}."""
    from backend.services import training_base_models
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != "download":
        return error_response(training_base_models.NEEDS_CLICK, 403, "NEEDS_CLICK")
    try:
        result = training_base_models.start_install(data.get("plan_token"), data.get("model"))
        logger.info("Training base model download started: %s", data.get("model"))
        return success_response(result, "Download started", status_code=202)
    except training_base_models.Refused as e:
        return error_response(str(e), e.status, e.code)
    except Exception as e:
        logger.error(f"Error starting a base model download: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/base-models/remove", methods=["POST"])
@ensure_db_session_cleanup
def remove_base_model():
    """Delete one base model's weights.
    Body: {"confirm": "remove", "model": <id>, "plan_token": <from GET /base-models?plan=1>}."""
    from backend.services import training_base_models
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != "remove":
        return error_response(training_base_models.NEEDS_CLICK, 403, "NEEDS_CLICK")
    running = db.session.query(TrainingJob).filter(
        TrainingJob.status == "running", TrainingJob.base_model == data.get("model")).count()
    if running:
        return error_response(f"{running} training job(s) on this model are running; wait for them "
                              f"to finish or cancel them.", 409, "TRAINING_RUNNING")
    try:
        return success_response(training_base_models.start_remove(data.get("plan_token"), data.get("model")),
                                "Removed")
    except training_base_models.Refused as e:
        return error_response(str(e), e.status, e.code)
    except Exception as e:
        logger.error(f"Error removing a base model: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/base-models/status", methods=["GET"])
@ensure_db_session_cleanup
def base_model_status():
    """Whether the chosen base model is already on this machine.

    Fine-tuning loads the base model through Hugging Face; a cache miss is a
    multi-GB download the form must announce before the job is created."""
    name = (request.args.get("name") or "").strip()
    if not name:
        return error_response("name is required", 400)
    try:
        from backend.services.local_weights import download_status
        return success_response(download_status(name))
    except Exception as e:
        logger.error(f"Error checking base model status: {e}", exc_info=True)
        return error_response(str(e), 500)


@training_bp.route("/pipeline/parse", methods=["POST"])
@ensure_db_session_cleanup
def start_parse_job():
    try:
        data = request.get_json(silent=True) or {}

        input_path = data.get("input_path")
        if not isinstance(input_path, str) or not input_path.strip():
            return error_response("input_path is required", 400)
        name = data.get("name")
        name = name.strip() if isinstance(name, str) else ""
        if not name:
            name = f"Parse: {os.path.basename(input_path.rstrip('/')) or input_path}"

        job_id = str(uuid.uuid4())
        job = TrainingJob(
            job_id=job_id,
            name=name,
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
            return _not_started(job, "parse", e)

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

        # Compared against each pair's score, so it has to be a number.
        min_score = data.get("min_score", 0.5)
        if isinstance(min_score, bool) or not isinstance(min_score, (int, float)):
            return error_response("min_score must be a number", 400)

        job_id = str(uuid.uuid4())
        job = TrainingJob(
            job_id=job_id,
            name=data.get("name", f"Filter: {data['input_path']}"),
            pipeline_stage="filtering",
            status="pending",
            config_json=json.dumps({
                "input_path": data["input_path"],
                "min_score": min_score
            })
        )

        db.session.add(job)
        db.session.commit()

        try:
            from backend.tasks.training_tasks import filter_dataset_task
            task = filter_dataset_task.apply_async(
                args=[job_id, data["input_path"], min_score],
                queue="training"
            )
            job.celery_task_id = task.id
            job.status = "running"
            db.session.commit()
            logger.info(f"Started filter task for job {job_id}: {task.id}")
        except Exception as e:
            logger.error(f"Failed to start filter task: {e}", exc_info=True)
            return _not_started(job, "filter", e)

        logger.info(f"Created filter job: {job_id}")
        return success_response(job.to_dict(), status_code=201)
    except Exception as e:
        logger.error(f"Error starting filter job: {e}", exc_info=True)
        return error_response(str(e), 500)


def _folder_build_options(data: dict):
    """(options, None) from a Build from folder request, or (None, error
    response). The folder must be one Bulk Import may read: the same roots
    and refusals. options: folder, kinds, per_1000, target, include_large."""
    from backend.api.files_api import resolve_import_directory
    from backend.services.training import folder_dataset as fd

    folder = data.get("folder")
    if not isinstance(folder, str) or not folder.strip():
        return None, error_response("folder is required", 400)
    resolved, refusal = resolve_import_directory(folder.strip())
    if refusal:
        return None, error_response(refusal, 403, "PATH_NOT_ALLOWED")
    if not os.path.isdir(resolved):
        return None, error_response("folder is not a directory", 400)
    kinds = data.get("kinds") or list(fd.DEFAULT_KINDS)
    if not isinstance(kinds, list) or not all(isinstance(k, str) for k in kinds):
        return None, error_response("kinds must be a list such as [\".md\", \".txt\"]", 400)
    per_1000 = data.get("pairs_per_1000_words", fd.DEFAULT_PAIRS_PER_1000_WORDS)
    if isinstance(per_1000, bool) or not isinstance(per_1000, (int, float)) \
            or not 0 < per_1000 <= fd.MAX_PAIRS_PER_1000_WORDS:
        return None, error_response(
            f"pairs_per_1000_words must be a number above 0 and at most {fd.MAX_PAIRS_PER_1000_WORDS}", 400)
    target = data.get("target_pairs", "enough")
    if not (target in ("enough", "all") or (isinstance(target, int) and not isinstance(target, bool)
                                            and 100 <= target <= 1_000_000)):
        return None, error_response('target_pairs must be "enough", "all" or a whole number from 100', 400)
    include_large = data.get("include_large_files") or []
    if not isinstance(include_large, list) or not all(isinstance(p, str) for p in include_large):
        return None, error_response("include_large_files must be a list of paths inside the folder", 400)
    return {"folder": resolved, "kinds": fd.normalise_kinds(kinds), "per_1000": per_1000,
            "target": target, "include_large": include_large}, None


def _check_payload(prepared: dict) -> dict:
    return {
        "folder": prepared["folder"],
        "kinds": prepared["kinds"],
        "files": prepared["files"],
        "words": prepared["words"],
        "per_file": [{"source_path": d["source_path"], "words": d["words"]} for d in prepared["docs"]],
        "skipped": prepared["skipped"],
        "unreadable": prepared["unreadable"],
        "scrub": prepared["scrub"],
    }


@training_bp.route("/pipeline/build-from-folder/check", methods=["POST"])
def check_folder_build():
    """Scan and scrub only (no model): what a build would read and remove."""
    from backend.services.training import folder_dataset as fd

    data = request.get_json(silent=True) or {}
    opts, problem = _folder_build_options(data)
    if problem:
        return problem
    try:
        prepared = fd.prepare(opts["folder"], opts["kinds"], include_large=opts["include_large"])
    except Exception as e:
        logger.error(f"Folder build check failed: {e}", exc_info=True)
        return error_response(f"Could not read the folder: {e}", 500)
    return success_response({**_check_payload(prepared),
                             "estimate": fd.estimate(prepared, opts["per_1000"], target=opts["target"])})


@training_bp.route("/pipeline/build-from-folder/probe", methods=["POST"])
def probe_folder_build():
    """Write (and discard) the pairs of three chunks to measure the time a
    build would take. Uses the local chat model; writes nothing."""
    from backend.services.training import folder_dataset as fd
    from backend.services.training.pair_generator import GenerationUnavailable

    data = request.get_json(silent=True) or {}
    opts, problem = _folder_build_options(data)
    if problem:
        return problem
    try:
        prepared = fd.prepare(opts["folder"], opts["kinds"], include_large=opts["include_large"])
        probe = fd.probe_timing(prepared, opts["per_1000"], target=opts["target"])
    except GenerationUnavailable as e:
        return error_response(str(e), 503, "MODEL_UNAVAILABLE")
    except Exception as e:
        logger.error(f"Folder build probe failed: {e}", exc_info=True)
        return error_response(f"Could not time the build: {e}", 500)
    estimate = fd.estimate(prepared, opts["per_1000"], probe, target=opts["target"])
    # The per-file counts only feed the estimate; the page has no use for a path per file.
    probe.pop("passages_per_file", None)
    return success_response({"probe": probe, "estimate": estimate})


@training_bp.route("/pipeline/build-from-folder", methods=["POST"])
@ensure_db_session_cleanup
def start_folder_build():
    """Build a messages dataset from a folder of notes, as a training-queue job."""
    data = request.get_json(silent=True) or {}
    opts, problem = _folder_build_options(data)
    if problem:
        return problem
    folder = opts["folder"]
    name = data.get("name")
    name = name.strip() if isinstance(name, str) else ""
    name = name or f"Build: {os.path.basename(folder.rstrip('/')) or folder}"

    job_id = str(uuid.uuid4())
    job = TrainingJob(
        job_id=job_id,
        name=name,
        pipeline_stage="building",
        status="pending",
        config_json=json.dumps({"input_path": folder, "kinds": opts["kinds"], "pairs_per_1000_words": opts["per_1000"],
                                "target_pairs": opts["target"], "include_large_files": opts["include_large"]}),
    )
    db.session.add(job)
    db.session.commit()
    try:
        from backend.tasks.training_tasks import build_folder_dataset_task
        task = build_folder_dataset_task.apply_async(
            args=[job_id, folder, opts["kinds"], opts["per_1000"], opts["target"], opts["include_large"]],
            queue="training")
        job.celery_task_id = task.id
        job.status = "running"
        db.session.commit()
    except Exception as e:
        logger.error(f"Failed to start folder build: {e}", exc_info=True)
        return _not_started(job, "folder build", e)
    return success_response(job.to_dict(), status_code=201)


@training_bp.route("/scrub-terms", methods=["GET"])
def get_scrub_terms():
    """The per-install word list a folder build replaces (names, machines, clients)."""
    from backend.services.training import folder_dataset as fd
    from backend.services.training.folder_scrub import load_terms

    return success_response({"terms": load_terms(fd.scrub_terms_path())})


@training_bp.route("/scrub-terms", methods=["PUT"])
def put_scrub_terms():
    from backend.services.training import folder_dataset as fd
    from backend.services.training.folder_scrub import save_terms

    data = request.get_json(silent=True) or {}
    terms = data.get("terms")
    if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
        return error_response("terms must be a list of strings", 400)
    return success_response({"terms": save_terms(fd.scrub_terms_path(), terms)})


@training_bp.route("/jobs/<int:job_id>/export-anyway", methods=["POST"])
@ensure_db_session_cleanup
def export_anyway(job_id):
    """Release a run the export gate held, on a person's explicit choice.

    Body (optional): {"by": who chose, "via": where from}. The override is
    recorded in the job's config as ``export_override`` beside the gate's own
    verdict, and the job becomes "completed" so the export routes accept it.
    Nothing is exported until one of them is called."""
    try:
        from backend.tasks.training_tasks import WORSE_THAN_BASE
        from backend.utils.clock import utcnow

        job = db.session.get(TrainingJob, job_id)
        if not job:
            return error_response("Job not found", 404)
        if job.status != WORSE_THAN_BASE:
            return error_response(
                f"Only a run held as '{WORSE_THAN_BASE}' can be exported anyway (status: {job.status})",
                400
            )
        if not job.lora_path or not Path(job.lora_path).exists():
            return error_response(f"LoRA adapter path not found: {job.lora_path}", 400)

        # Releasing a run for an export that cannot run would only change its status.
        refused = _export_refused(job)
        if refused:
            return refused

        data = request.get_json(silent=True) or {}
        config = json.loads(job.config_json) if job.config_json else {}
        config["export_override"] = {
            "by": str(data.get("by") or "person")[:100],
            "via": str(data.get("via") or "api")[:50],
            "at": utcnow().isoformat(timespec="seconds") + "Z",
            "from_address": request.remote_addr,
            "held_status": job.status,
            "gate_note": job.error_message,
        }
        job.config_json = json.dumps(config)
        job.status = "completed"
        job.error_message = None
        db.session.commit()

        logger.info(f"Export gate overridden for training job {job_id} by "
                    f"{config['export_override']['by']} via {config['export_override']['via']}")
        return success_response(job.to_dict())
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error overriding the export gate for job {job_id}: {e}", exc_info=True)
        return error_response(f"Database error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Error overriding the export gate for job {job_id}: {e}", exc_info=True)
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

        refused = _export_refused(job)
        if refused:
            return refused

        data = request.get_json() or {}
        quantization, bad_level = _export_level(data)
        if bad_level:
            return bad_level

        before = (job.status, job.pipeline_stage)
        job.pipeline_stage = "exporting"
        job.status = "running"
        job.quantization_level = quantization
        db.session.commit()

        from backend.celery_dispatch import TaskNotStarted
        try:
            from backend.tasks.training_tasks import export_gguf_task
            # export_gguf_task takes the training output folder, which holds lora/.
            task = export_gguf_task.apply_async(
                args=[job.job_id, str(lora_path.parent), quantization],
                queue="training"
            )
            job.celery_task_id = task.id
            db.session.commit()
            logger.info(f"Started GGUF export task for job {job_id}: {task.id}")
        except TaskNotStarted as e:
            return _put_back(job, before, "export", e)
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

        before = (job.status, job.pipeline_stage)
        job.pipeline_stage = "importing"
        job.status = "running"
        db.session.commit()

        from backend.celery_dispatch import TaskNotStarted
        try:
            from backend.tasks.training_tasks import import_ollama_task
            # import_ollama_task looks for the .gguf files in a folder.
            task = import_ollama_task.apply_async(
                args=[job.job_id, str(gguf_path.parent), model_name],
                queue="training"
            )
            job.celery_task_id = task.id
            db.session.commit()
            logger.info(f"Started Ollama import task for job {job_id}: {task.id}")
        except TaskNotStarted as e:
            return _put_back(job, before, "import", e)
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
        model_name = data.get("model_name") or job.output_model_name or f"guaardvark-{job.name.lower().replace(' ', '-')}"
        name_problem = _output_name_problem(model_name)
        if name_problem:
            return error_response(name_problem, 400)
        requested = str(data.get("quantization") or "").lower()

        # A GGUF already exported at the level asked for (or with none asked
        # for) is registered as it is; another level is exported again.
        same_level = not requested or requested == (job.quantization_level or "").lower()
        if job.gguf_path and Path(job.gguf_path).exists() and same_level:
            logger.info(f"GGUF already exists at {job.gguf_path}, skipping to import")
            before = (job.status, job.pipeline_stage)
            job.pipeline_stage = "importing"
            job.status = "running"
            db.session.commit()

            from backend.celery_dispatch import TaskNotStarted
            from backend.tasks.training_tasks import import_ollama_task
            try:
                task = import_ollama_task.apply_async(
                    args=[job.job_id, str(Path(job.gguf_path).parent), model_name],
                    queue="training"
                )
            except TaskNotStarted as e:
                return _put_back(job, before, "import", e)
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

        refused = _export_refused(job)
        if refused:
            return refused
        quantization, bad_level = _export_level(data)
        if bad_level:
            return bad_level

        before = (job.status, job.pipeline_stage)
        job.pipeline_stage = "exporting"
        job.status = "running"
        job.output_model_name = model_name
        job.quantization_level = quantization
        db.session.commit()

        from backend.celery_dispatch import TaskNotStarted
        try:
            from backend.tasks.training_tasks import export_gguf_task, import_ollama_task
            from celery import chain

            # Both steps take the training output folder (export reads lora/
            # in it and writes the .gguf beside it). The import signature is
            # immutable so the chain does not prepend the export's result;
            # then_import keeps the job running between the two steps.
            model_dir = str(lora_path.parent)
            workflow = chain(
                export_gguf_task.s(job.job_id, model_dir, quantization, then_import=True),
                import_ollama_task.si(job.job_id, model_dir, model_name)
            )
            result = workflow.apply_async(queue="training")
            job.celery_task_id = result.id
            db.session.commit()
            logger.info(f"Started export-to-ollama workflow for job {job_id}: {result.id}")
        except TaskNotStarted as e:
            return _put_back(job, before, "export to Ollama", e)
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
