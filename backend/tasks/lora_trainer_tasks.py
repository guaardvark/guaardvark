"""Celery wiring for the lora_trainer plugin.

Same factory pattern as production_swarm_tasks. The task body is intentionally
thin: load Subject, call mock_trainer (or real trainer in v1.1), persist
results. No state-machine interaction with Production — training is per-Subject
and the cast endpoint already records the user's chosen action."""
from __future__ import annotations
import logging
from celery import Celery
from flask import current_app

from backend.models import db, Subject

logger = logging.getLogger(__name__)


def _output_dir() -> str:
    return (current_app.config.get("LORA_OUTPUT_DIR")
            or "data/training/loras")


def _train_impl(subject_id: int) -> dict:
    """Picks mock or real trainer based on:
       1. GUAARDVARK_LORA_BACKEND env var (mock|real|auto, default auto)
       2. Auto: real if plugins/lora_trainer/venv-torch/bin/python exists, else mock.
       Logs which backend it picked."""
    import os
    s = db.session.get(Subject, subject_id)
    if s is None:
        return {"status": "failed", "error": f"subject {subject_id} not found"}

    backend = os.environ.get("GUAARDVARK_LORA_BACKEND", "auto").lower()
    use_real = False
    if backend == "real":
        use_real = True
    elif backend == "auto":
        from plugins.lora_trainer.real_trainer import RealLoraTrainer
        use_real = RealLoraTrainer.is_available()

    if use_real:
        from plugins.lora_trainer.real_trainer import RealLoraTrainer, _TRAINER
        logger.info(f"lora_trainer: using REAL backend for subject {subject_id}")
        return _TRAINER.train_subject_lora(
            subject_id=s.id,
            subject_name=s.name,
            ref_image_paths=s.ref_image_paths or [],
            output_dir=_output_dir(),
        )

    from plugins.lora_trainer.mock_trainer import train_subject_lora
    logger.info(f"lora_trainer: using MOCK backend for subject {subject_id}")
    return train_subject_lora(
        subject_id=s.id,
        subject_name=s.name,
        ref_image_paths=s.ref_image_paths or [],
        output_dir=_output_dir(),
    )


def create_lora_trainer_tasks(celery_app: Celery):
    @celery_app.task(name="lora_trainer.train_lora")
    def train_lora_task(subject_id: int):
        with current_app.app_context():
            train_subject_lora_for_subject(subject_id)

    return {"train_lora": train_lora_task}


def train_subject_lora_for_subject(subject_id: int) -> None:
    """Module-level entry point — directly callable from tests."""
    s = db.session.get(Subject, subject_id)
    if s is None:
        logger.warning(f"train_lora called for unknown subject {subject_id}")
        return
    if s.training_status != "training":
        # Cast endpoint sets training_status='training' before dispatching.
        # If it's anything else, someone double-dispatched or the row was
        # raced. Idempotency: do nothing.
        logger.info(f"skip train_lora for subject {subject_id} (status={s.training_status!r})")
        return
    result = _train_impl(subject_id)
    if result.get("status") == "ok":
        s.lora_path = result["lora_path"]
        s.lora_version = result.get("lora_version", 1)
        s.training_status = "trained"
    else:
        s.training_status = "failed"
        # Stash error somewhere readable by the UI. Subject doesn't have an
        # error column today — log it and use ref_image_paths' sidecar for now.
        # (v1.1 may add a dedicated error column.)
        logger.warning(f"lora train failed for subject {subject_id}: {result.get('error')}")
    db.session.commit()
