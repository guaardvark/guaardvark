"""Celery wiring for the lora_trainer plugin.

Same factory pattern as production_swarm_tasks. The task body is intentionally
thin: load Subject, call mock_trainer (or real trainer in v1.1), persist
results. No state-machine interaction with Production — training is per-Subject
and the cast endpoint already records the user's chosen action."""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from celery import Celery
from flask import current_app

from backend.models import db, Subject
from backend.utils.clock import utcnow
from backend.utils.platform import (
    LORA_LOAD_TIMEOUT_S,
    LORA_REAP_STUCK_AFTER_S,
    LORA_TRAIN_TASK_SOFT_TIME_LIMIT_S,
    LORA_TRAIN_TASK_TIME_LIMIT_S,
    LORA_TRAIN_TIMEOUT_S,
)

logger = logging.getLogger(__name__)

# How long a Cast training waits for the card (a release cooldown, a batch of
# stills) before failing as busy. The task limits and the stuck-training reap
# in backend.utils.platform allow for it.
CAST_TRAIN_GPU_WAIT_S = 10 * 60
# The card's cross-process lease outlives the longest run the limits allow.
CAST_TRAIN_GPU_LEASE_S = CAST_TRAIN_GPU_WAIT_S + LORA_LOAD_TIMEOUT_S + LORA_TRAIN_TIMEOUT_S + 600

# training_settings_json key binding a saved LoRA to the run (job id) that saved
# it, written with the LoRA and cleared when that run marks the member 'trained'.
LORA_SAVED_FOR_JOB_KEY = "lora_saved_for_job"
# A run past its save is in its one-still smoke render (about a minute on a 16 GB
# NVIDIA card, cooling retry included). Two pipeline loads at the load cap plus
# half an hour is past any live render, so a member still 'training' that long
# after its save lost its worker.
LORA_SMOKE_REAP_AFTER_S = 2 * LORA_LOAD_TIMEOUT_S + 30 * 60
LORA_SAVED_NOTE = "Training finished and the LoRA was saved (worker stopped during the test render). Ready to use."
# training_settings_json key holding a note for the Cast page as a stable code
# plus the LoRA it is about. The page shows it only while training_error is set
# (every new run clears that) and that LoRA is still the member's default.
TRAINING_NOTE_KEY = "training_note"
NOTE_LORA_SAVED = "lora_saved_worker_stopped"


def _output_dir() -> str:
    from backend.config import STORAGE_DIR
    return (current_app.config.get("LORA_OUTPUT_DIR")
            or os.path.join(STORAGE_DIR, "training", "loras"))


def _train_impl(subject_id: int, job_id: str | None = None) -> dict:
    """Picks mock or real trainer based on:
       1. GUAARDVARK_LORA_BACKEND env var (mock|real|auto, default auto)
       2. Auto: real if plugins/lora_trainer/venv-torch/bin/python exists, else mock.
       Logs which backend it picked."""
    import os
    from backend.utils.unified_progress_system import get_unified_progress

    s = db.session.get(Subject, subject_id)
    if s is None:
        if job_id:
            try:
                get_unified_progress().error_process(job_id, "subject not found in _train_impl")
            except Exception:
                pass
        return {"status": "failed", "error": f"subject {subject_id} not found"}

    # Training set = the subject's uploaded reference images (the primary Step-1
    # flow) UNION any APPROVED, done generated samples (the fallback: "no images?
    # generate some with the Character Generator, approve the good ones, train").
    # Without this union the approve step was cosmetic — approved samples never
    # reached the trainer, which only ever read ref_image_paths.
    from backend.models import SubjectSample
    train_images = list(s.ref_image_paths or [])
    approved = (
        SubjectSample.query
        .filter_by(subject_id=s.id, approved=True, status="done")
        .all()
    )
    for smp in approved:
        if smp.image_path and smp.image_path not in train_images:
            train_images.append(smp.image_path)

    # Ensure VLM .txt sidecars exist before gate/captions (upload may have skipped
    # if Ollama was down; train is the second chance).
    try:
        from backend.services.character_captioner import ensure_subject_image_captions
        from backend.services.character_identity_prompt import resolve_class_token
        token = (s.trigger_word or "").strip() or s.name
        cfg = dict(s.training_settings_json or {})
        marks = (cfg.get("bible_identity_marks") or "").strip()
        if not marks and cfg.get("bible_vision_tags"):
            marks = ", ".join(cfg["bible_vision_tags"][:12])[:200]
        # Do NOT dump invented full bible into captions — that fights the pixels.
        # Captions missing "a photo of {token}" or the subject's class are rewritten.
        cap_sum = ensure_subject_image_captions(
            [p for p in train_images if p],
            trigger=token,
            identity_marks=marks,
            class_token=resolve_class_token(s),
            kind=s.kind or "character",
        )
        logger.info(
            "lora_trainer: caption ensure for subject %s written=%s skipped=%s failed=%s",
            subject_id, cap_sum.get("written"), cap_sum.get("skipped"), cap_sum.get("failed"),
        )
        if job_id and cap_sum.get("written"):
            try:
                get_unified_progress().update_process(
                    job_id, 12,
                    f"Captioned {cap_sum['written']} training image(s)…",
                )
            except Exception:
                pass
    except Exception as e:
        logger.warning("lora_trainer: caption ensure failed (non-fatal): %s", e)

    from backend.services.lora_pretrain_gate import validate_cast_training, build_training_captions
    gate = validate_cast_training(s, train_images)
    if not gate["pass"]:
        msg = "; ".join(gate["failures"]) or "pre-train validation failed"
        logger.warning("lora_trainer: pretrain gate failed for subject %s: %s", subject_id, msg)
        if job_id:
            try:
                get_unified_progress().error_process(job_id, f"Dataset check failed: {msg}")
            except Exception:
                pass
        return {"status": "failed", "error": msg, "used_images": train_images, "gate": gate}
    if gate.get("warnings"):
        logger.info("lora_trainer: pretrain warnings for subject %s: %s", subject_id, gate["warnings"])

    image_captions = build_training_captions(s, [p for p in train_images if p])
    from backend.services.lora_training_settings import settings_for_subject
    train_settings = settings_for_subject(s)

    # Multi-base train via media_model_registry (zimage-turbo + sdxl-legacy ready).
    try:
        from backend.services.media_model_registry import assert_train_ready
        train_profile = assert_train_ready(train_settings.get("base_model_id"))
    except ValueError as e:
        msg = str(e)
        logger.error("lora_trainer: train base not ready for subject %s: %s", subject_id, msg)
        if job_id:
            try:
                get_unified_progress().error_process(job_id, msg)
            except Exception:
                pass
        return {"status": "failed", "error": msg, "used_images": train_images}

    backend = os.environ.get("GUAARDVARK_LORA_BACKEND", "auto").lower()
    # Mock training is a TEST-ONLY backend. Per the NO-MOCKS-IN-PRODUCTION policy
    # (CLAUDE.md), a production process must NEVER fall back to it: a 27-byte fake
    # LoRA that reports status="ok" is exactly what polluted subject 16's pipeline
    # (it set training_status='trained' on a stub that then fails to load at render).
    # So the mock is reachable only under pytest; everywhere else we FAIL LOUD.
    _under_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    use_real = False
    allow_mock = False
    if backend == "real":
        use_real = True
    elif backend == "mock":
        if _under_pytest:
            allow_mock = True
        else:
            msg = ("GUAARDVARK_LORA_BACKEND=mock is a test-only backend and is "
                   "refused in production (NO-MOCKS policy). Unset it or set it to "
                   "'real'/'auto' to train a genuine LoRA.")
            logger.error("lora_trainer: %s", msg)
            return {"status": "failed", "error": msg, "used_images": train_images}
    elif backend == "auto":
        from plugins.lora_trainer.real_trainer import RealLoraTrainer
        use_real = RealLoraTrainer.is_available()

    if use_real:
        from plugins.lora_trainer.real_trainer import RealLoraTrainer, _TRAINER
        logger.info(f"lora_trainer: using REAL backend for subject {subject_id}")
        if job_id:
            try:
                get_unified_progress().update_process(job_id, 20, "Starting real LoRA trainer (SDXL)")
            except Exception:
                pass
        # Real LoRA training is a full GPU load on the shared 16GB card — claim
        # the GPU exclusively (LORA_TRAIN slot) so it serializes against video
        # render / model finetune. The MOCK path below is CPU-only and is NOT
        # gated. On contention, return a clean failed result (rather than
        # raising) so train_subject_lora_for_subject marks the Subject 'failed'
        # instead of leaving it stuck in 'training'.
        from backend.services.job_operation_gate import GpuBusyError, GpuCapacityError
        from backend.services.job_types import JobKind
        from backend.services.gpu_resource_policy import gpu_session_when_free
        from backend.services.plugin_bridge import ensure_plugins_for_stage
        # The base's measured training peak from the media registry, for the
        # storage format this run uses, not one figure shared by every base.
        from backend.services.media_model_registry import train_vram_mb as _train_vram_mb
        train_vram_mb = _train_vram_mb(train_profile, train_settings["base_storage"])
        try:
            # Cast train stage is reclaim-only (empty plugin list) — symmetry hook
            # for future requirements; lora_trainer is a tool, not a sidecar.
            ensure_plugins_for_stage("cast", "train")
            # gpu_session = the gate's exclusivity PLUS VRAM reclaim once the slot
            # is won. evict_ollama/free_comfyui are the actual fix for the observed OOM:
            # ollama keeps a chat model (~6GB) resident and ComfyUI can hold a FLUX,
            # which left no room for SDXL on the 16GB card. The bare gate did no
            # VRAM math, so training claimed "exclusive" while ollama still owned
            # 6.7GB → CUDA OOM. Reclaim runs only AFTER we hold the slot.
            # cross_process: the Flask API renders stills under its own in-PID
            # gate, so only the file lease keeps training off a card mid-render.
            from backend.services.gpu_resource_policy import compositor_vram_reserve_mb

            def _waiting(reason):
                if job_id:
                    try:
                        get_unified_progress().update_process(job_id, 20, f"Waiting for the GPU: {reason}")
                    except Exception:
                        pass

            # Waits its turn: a run queued behind another one otherwise failed on
            # the 8 s release cooldown alone.
            with gpu_session_when_free(JobKind.LORA_TRAIN, f"subject_{s.id}",
                                       wait_s=CAST_TRAIN_GPU_WAIT_S, on_wait=_waiting,
                                       evict_ollama=True, free_comfyui=True,
                                       vram_estimate_mb=train_vram_mb, require_fit=True,
                                       cross_process=True, lease_seconds=CAST_TRAIN_GPU_LEASE_S,
                                       vram_reserve_mb=compositor_vram_reserve_mb()):
                if job_id:
                    try:
                        get_unified_progress().update_process(job_id, 30, "GPU claimed, training epochs running (can take hours)")
                    except Exception:
                        pass
                try:
                    if job_id:
                        try:
                            get_unified_progress().update_process(
                                job_id, 25,
                                f"Training {train_profile.get('name')} LoRA "
                                f"(base={train_profile.get('id')})",
                            )
                        except Exception:
                            pass
                    def _on_step(step, total):
                        # A run outlasts the progress system's no-update timeout,
                        # so each step line the trainer logs keeps the job alive.
                        if job_id and total:
                            get_unified_progress().update_process(
                                job_id, 30 + int(60 * step / total),
                                f"Training step {step} of {total}",
                            )

                    real_result = _TRAINER.train_subject_lora(
                        subject_id=s.id,
                        subject_name=s.name,
                        trigger_word=s.trigger_word,
                        ref_image_paths=train_images,
                        output_dir=_output_dir(),
                        image_prompts=image_captions,
                        resolution=train_settings["resolution"],
                        rank=train_settings["rank"],
                        alpha=train_settings["alpha"],
                        learning_rate=train_settings["learning_rate"],
                        steps=train_settings["steps"],
                        lora_targets=train_settings["lora_targets"],
                        base_storage=train_settings["base_storage"],
                        base_model_id=train_profile.get("id"),
                        on_step=_on_step,
                    )
                    if isinstance(real_result, dict):
                        real_result.setdefault("used_images", train_images)
                    return real_result
                finally:
                    # Free the ~7GB of SDXL the trainer daemon holds. Without this
                    # the daemon stays resident IDLE between jobs, and a single
                    # leftover daemon starves the next run on the shared 16GB card
                    # — the exact OOM observed in the field (subject 16: 137MiB free, a zombie
                    # daemon holding 6.7GB). Shutting down also drops any
                    # half-applied PEFT/LoRA state from a failed run. Reload on the
                    # next job is ~6s (model is disk-cached), a cheap price for not
                    # leaking the card. Best-effort: never let cleanup mask the
                    # real training result/error.
                    try:
                        _TRAINER.shutdown()
                    except Exception as _e:
                        logger.warning(f"lora_trainer: daemon shutdown after subject {subject_id} failed (non-fatal): {_e}")
        except GpuCapacityError as e:
            # Waiting cannot help, so say what the base needs rather than "busy".
            logger.warning(f"lora_trainer: card too small for subject {subject_id}: {e}")
            msg = (f"{train_profile.get('name')} character training needs about "
                   f"{train_vram_mb / 1024:.1f} GB of GPU memory, more than this card can "
                   f"give it. {e}")
            return {"status": "failed", "error": msg, "used_images": train_images}
        except GpuBusyError as e:
            logger.warning(f"lora_trainer: GPU busy for subject {subject_id}: {e}")
            return {"status": "failed", "error": f"GPU busy: {e}", "used_images": train_images}

    # Reaching here means the real trainer was NOT selected. Outside of pytest that
    # is a hard failure — we do NOT silently produce a fake LoRA (NO-MOCKS policy).
    # The most common cause in 'auto' is that RealLoraTrainer.is_available() returned
    # False: the accelerator probe (CUDA or Apple MPS) didn't see a device (or timed
    # out under contention). Fail loud with guidance; the caller marks the Subject
    # 'failed' with this message.
    if not allow_mock:
        msg = ("Real LoRA trainer unavailable (accelerator probe failed: no CUDA or "
               "Apple MPS). Verify the GPU is free (nvidia-smi) or MPS is available "
               "(Apple Silicon), and the trainer venv exists "
               "(plugins/lora_trainer/scripts/setup_venv.sh), then retry. To bypass "
               "the probe under contention, set GUAARDVARK_LORA_BACKEND=real. "
               "Mock training is disabled by policy.")
        logger.error("lora_trainer: %s (subject %s)", msg, subject_id)
        if job_id:
            try:
                get_unified_progress().error_process(job_id, msg)
            except Exception:
                pass
        return {"status": "failed", "error": msg, "used_images": train_images}

    # TEST-ONLY mock path (allow_mock is True only under pytest).
    from plugins.lora_trainer.mock_trainer import train_subject_lora
    logger.info(f"lora_trainer: using TEST-ONLY MOCK backend for subject {subject_id}")
    if job_id:
        try:
            get_unified_progress().update_process(job_id, 60, "Running mock trainer (test-only path)")
        except Exception:
            pass
    result = train_subject_lora(
        subject_id=s.id,
        subject_name=s.name,
        ref_image_paths=train_images,
        output_dir=_output_dir(),
    )
    # Attach the images that were actually used (for snapshot on success)
    if isinstance(result, dict):
        result.setdefault("used_images", train_images)
    return result


def create_lora_trainer_tasks(celery_app: Celery):
    # Task limits are derived from the one platform flag in backend.utils.platform
    # so the daemon caps, these task limits, and the reaper cutoff cannot drift
    # apart. They replace the global task_soft_time_limit / task_time_limit,
    # which are shorter than a training run.
    task_limits = {}
    if LORA_TRAIN_TASK_TIME_LIMIT_S is not None:
        task_limits["soft_time_limit"] = LORA_TRAIN_TASK_SOFT_TIME_LIMIT_S
        task_limits["time_limit"] = LORA_TRAIN_TASK_TIME_LIMIT_S

    @celery_app.task(name="lora_trainer.train_lora", **task_limits)
    def train_lora_task(subject_id: int, job_id: str | None = None):
        with current_app.app_context():
            train_subject_lora_for_subject(subject_id, job_id=job_id)

    @celery_app.task(name="lora_trainer.reap_stuck_training")
    def reap_stuck_training_task():
        with current_app.app_context():
            return reap_stuck_training_subjects()

    return {"train_lora": train_lora_task, "reap_stuck_training": reap_stuck_training_task}


def _superseded_by(s: Subject, job_id: str | None) -> str | None:
    """Why this run no longer owns the member's 'training' state, or None.

    A cancel moves the status; a cancel followed by a new Train puts it back to
    'training' under the new run's job id, which this run must not finish.
    """
    if s.training_status != "training":
        return s.training_status
    if job_id and s.current_training_job_id != job_id:
        return f"training (job {s.current_training_job_id})"
    return None


def _promote_to_trained(subject_id: int, job_id: str | None = None) -> tuple[Subject | None, str | None]:
    """Mark a finished run 'trained' once the post-train smoke render is over.

    'trained' is what the Cast page, the job handoff and the night shift treat
    as "the card is free for the next job", so it is committed only after the
    smoke render has released the GPU, whether it passed, failed or raised.
    Returns ``(subject, None)`` when promoted, or ``(None, reason)`` when a
    cancel, a newer run or a delete took the row meanwhile.
    """
    # The smoke render commits on this session and may have left it failed.
    db.session.rollback()
    s = db.session.get(Subject, subject_id, populate_existing=True)
    if s is None:
        return None, "deleted"
    if job_id and s.training_status == "trained" and s.current_training_job_id == job_id:
        # The reaper promoted this run's saved LoRA while the render overran.
        if s.training_error == LORA_SAVED_NOTE:
            s.training_error = None
        _clear_saved_marker(s)
        _set_training_note(s, None)
        db.session.commit()
        return s, None
    moved_to = _superseded_by(s, job_id)
    if moved_to:
        return None, moved_to
    s.training_status = "trained"
    _clear_saved_marker(s)
    _set_training_note(s, None)
    db.session.commit()
    return s, None


def _clear_saved_marker(s: Subject) -> None:
    cfg = s.training_settings_json
    if isinstance(cfg, dict) and LORA_SAVED_FOR_JOB_KEY in cfg:
        cfg = dict(cfg)
        cfg.pop(LORA_SAVED_FOR_JOB_KEY)
        s.training_settings_json = cfg


def _set_training_note(s: Subject, code: str | None) -> None:
    cfg = dict(s.training_settings_json or {})
    if code:
        cfg[TRAINING_NOTE_KEY] = {"code": code, "lora_path": s.lora_path}
    elif TRAINING_NOTE_KEY in cfg:
        cfg.pop(TRAINING_NOTE_KEY)
    else:
        return
    s.training_settings_json = cfg


def _saved_lora_for_current_run(s: Subject) -> dict | None:
    """The save marker when the member's current run already saved its LoRA.

    Evidence: the marker names the member's current job id, the member's
    default LoRA (``lora_path``) is the file it names, and that file passes the
    shared model-file integrity check. Returns None otherwise.
    """
    from backend.utils.model_file_integrity import file_complete

    cfg = s.training_settings_json if isinstance(s.training_settings_json, dict) else {}
    marker = cfg.get(LORA_SAVED_FOR_JOB_KEY)
    if not isinstance(marker, dict):
        return None
    job_id, path = marker.get("job_id"), marker.get("lora_path")
    if not job_id or job_id != s.current_training_job_id:
        return None
    if not path or path != s.lora_path:
        return None
    try:
        if not file_complete(path):
            return None
    except Exception:
        return None
    return marker


def _smoke_and_finish(
    subject_id: int,
    s: Subject,
    job_id: str | None,
    *,
    run_smoke: bool,
    used_images: list,
    base_model_hint: str | None = None,
) -> None:
    """After a committed save: the smoke render, 'trained', then the job's end."""
    from backend.utils.unified_progress_system import get_unified_progress

    smoke = None
    try:
        if run_smoke and s.lora_path:
            if job_id:
                try:
                    get_unified_progress().update_process(
                        job_id, 92, "Checking the new LoRA with a test render",
                    )
                except Exception:
                    pass
            from backend.services.lora_posttrain_smoke import run_lora_smoke_test
            from backend.services.lora_training_settings import settings_for_subject
            train_settings = settings_for_subject(s)
            smoke = run_lora_smoke_test(
                subject_id=subject_id,
                lora_path=s.lora_path,
                trigger_word=s.trigger_word or s.name,
                resolution=train_settings.get("resolution", 768),
                base_model_id=train_settings.get("base_model_id") or base_model_hint,
                ref_image_paths=list(used_images or [])[:8],
                subject=s,
            )
            if smoke.get("ok"):
                logger.info("lora smoke test passed for subject %s", subject_id)
            else:
                logger.warning(
                    "lora smoke test did not pass for subject %s: %s",
                    subject_id, smoke.get("error"),
                )
    except Exception as e:
        # Includes Celery's SoftTimeLimitExceeded: the LoRA is saved either way.
        logger.warning("lora smoke test for subject %s raised (non-fatal): %s", subject_id, e)
        smoke = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        s, moved_to = _promote_to_trained(subject_id, job_id)

    if s is None:
        logger.warning(
            "lora train for %s saved its LoRA but status is %r; not marked trained",
            subject_id, moved_to,
        )
        if job_id:
            try:
                get_unified_progress().error_process(job_id, f"training superseded (status={moved_to})")
            except Exception:
                pass
        return

    if job_id:
        try:
            get_unified_progress().update_process(job_id, 95, "Finalizing LoRA")
            additional = {
                "lora_path": s.lora_path,
                "subject_id": subject_id,
                "smoke_test": smoke,
            }
            msg = "LoRA training complete"
            if smoke and smoke.get("ok"):
                msg += " (smoke render verified LoRA loads)"
            elif smoke and smoke.get("error"):
                msg += f" (smoke render skipped: {smoke['error'][:120]})"
            get_unified_progress().complete_process(job_id, msg, additional_data=additional)
        except Exception:
            pass


def train_subject_lora_for_subject(subject_id: int, job_id: str | None = None) -> None:
    """Module-level entry point — directly callable from tests."""
    from backend.utils.unified_progress_system import get_unified_progress

    s = db.session.get(Subject, subject_id)
    if s is None:
        logger.warning(f"train_lora called for unknown subject {subject_id}")
        if job_id:
            try:
                get_unified_progress().error_process(job_id, "subject not found")
            except Exception:
                pass
        return
    if s.training_status != "training":
        # Cast endpoint sets training_status='training' before dispatching.
        # If it's anything else, someone double-dispatched or the row was
        # raced. Idempotency: do nothing.
        logger.info(f"skip train_lora for subject {subject_id} (status={s.training_status!r})")
        if job_id:
            try:
                get_unified_progress().error_process(job_id, f"skipped (status={s.training_status})")
            except Exception:
                pass
        return

    saved = _saved_lora_for_current_run(s) if job_id else None
    if saved is not None and saved.get("job_id") == job_id:
        # A redelivered message for a run whose worker died after saving its
        # LoRA: finish that run rather than train again.
        logger.warning(
            "lora train for %s: job %s already saved %s; finishing it without retraining",
            subject_id, job_id, s.lora_path,
        )
        _smoke_and_finish(
            subject_id, s, job_id,
            # Recorded only after a verified real train (see below).
            run_smoke=bool(s.last_trained_image_paths),
            used_images=list(s.last_trained_image_paths or []),
        )
        return

    if job_id:
        try:
            get_unified_progress().update_process(job_id, 10, "Preparing training images (refs + approved samples)")
        except Exception:
            pass

    try:
        result = _train_impl(subject_id, job_id=job_id)
    except Exception as e:
        # Daemon protocol failures (stdout closed, watchdog kill, non-JSON reply)
        # escape _train_impl; without this the Subject sits at 'training' until
        # the reaper notices.
        logger.exception("lora train for %s crashed", subject_id)
        db.session.rollback()
        s = db.session.get(Subject, subject_id)
        if s is not None and s.training_status == "training":
            s.training_status = "failed"
            s.training_error = f"{type(e).__name__}: {e}"[:2000]
            db.session.commit()
        if job_id:
            try:
                get_unified_progress().error_process(job_id, f"training crashed: {e}")
            except Exception:
                pass
        return

    # Cancel or delete may have moved the row while the GPU work ran; only a
    # Subject still in this run's 'training' may be promoted to 'trained'.
    db.session.refresh(s)
    moved_to = _superseded_by(s, job_id)
    if moved_to:
        logger.warning(
            "lora train for %s finished but status is %r; result not recorded",
            subject_id, moved_to,
        )
        if job_id:
            try:
                get_unified_progress().error_process(job_id, f"training superseded (status={moved_to})")
            except Exception:
                pass
        return

    # Perform all DB updates and commit BEFORE notifying the progress system.
    # This avoids a race where the frontend receives the "complete" event and
    # does loadSubject() before the status='trained' is committed, leaving the
    # UI stuck with polling=true / spinner / "starting training" even after the
    # GPU work is finished.
    if result.get("status") == "ok":
        # A finished run becomes the member's default, as it always has. It is
        # also recorded per base, so a LoRA the member holds for another base
        # (trained earlier or imported) stays available to renders on that base.
        try:
            from backend.services.cast_lora_selection import record_subject_lora
            from backend.services.lora_training_settings import settings_for_subject
            trained_base = (
                result.get("base_model_id") or settings_for_subject(s).get("base_model_id")
            )
            with db.session.begin_nested():
                record_subject_lora(
                    s, trained_base, result["lora_path"], source="trained", make_default=True,
                )
        except Exception as e:
            logger.warning("lora train for %s: per-base LoRA record failed: %s", subject_id, e)
        s.lora_path = result["lora_path"]
        s.lora_version = result.get("lora_version", 1)
        # training_status stays 'training' until the smoke render below has
        # released the card; see _promote_to_trained.
        s.training_error = None
        # When Z-Image is routed through ComfyUI, link the freshly-trained LoRA into
        # ComfyUI's models/loras so generation can resolve it by basename.
        try:
            from backend.services.comfyui_image_generator import ensure_lora_in_comfyui
            ensure_lora_in_comfyui(s.lora_path)
        except Exception:
            pass
        s.last_trained_at = utcnow()
        if job_id:
            # Lets the reaper tell a run that saved its LoRA and then lost its
            # worker from one that never finished (reap_stuck_training_subjects).
            s.training_settings_json = dict(
                s.training_settings_json or {},
                **{LORA_SAVED_FOR_JOB_KEY: {
                    "job_id": job_id,
                    "lora_path": s.lora_path,
                    "saved_at": s.last_trained_at.isoformat(),
                }},
            )

        # Only record image lists + promote samples after a *verified real* train.
        # real_trainer writes sidecar mock=false; anything else (or a tiny weights
        # file) is refused — no promotion, no last_trained_image_paths.
        used_images = result.get("used_images") or []
        is_real_trained = False
        try:
            lora_file = Path(s.lora_path)
            sidecar_path = lora_file.with_suffix(".json")
            sidecar = {}
            if sidecar_path.exists():
                with open(sidecar_path) as f:
                    sidecar = json.load(f)
                # Production real_trainer always writes mock=false. Require that
                # exact contract so we never promote from a test/stub adapter.
                is_real_trained = sidecar.get("mock") is False
            if is_real_trained and lora_file.exists():
                if lora_file.stat().st_size < 100:
                    is_real_trained = False
                    logger.warning(
                        "lora file for %s is too small to be a real adapter; "
                        "not recording train set / not promoting samples",
                        subject_id,
                    )
            logger.info(
                "lora train for %s: real_trained=%s size=%s",
                subject_id,
                is_real_trained,
                lora_file.stat().st_size if lora_file.exists() else 0,
            )
        except Exception as e:
            logger.warning(f"Failed to read sidecar/lora for real-training verification: {e}")
            is_real_trained = False

        if is_real_trained:
            s.last_trained_image_paths = used_images
            # Graduate approved samples used in this train into durable Training
            # Data (ref_image_paths + promoted_to_training). Only after verified
            # real success — never from test/stub adapters.
            try:
                from backend.services.sample_promotion import promote_samples_after_train
                promo = promote_samples_after_train(s, used_images)
                logger.info(
                    "lora train for %s: promoted %s sample(s) into Training Data",
                    subject_id, promo.get("promoted", 0),
                )
            except Exception as promo_err:
                logger.warning(
                    "lora train for %s: sample promotion failed (non-fatal): %s",
                    subject_id, promo_err,
                )
        else:
            s.last_trained_image_paths = []
            logger.warning(
                "lora train for %s reported ok but was not verified-real; "
                "not recording last_trained_image_paths and not promoting samples",
                subject_id,
            )

        db.session.commit()

        _smoke_and_finish(
            subject_id, s, job_id,
            run_smoke=is_real_trained,
            used_images=used_images,
            base_model_hint=result.get("base_model_id") if isinstance(result, dict) else None,
        )
    else:
        s.training_status = "failed"
        err = result.get("error") or "training failed"
        s.training_error = err[:2000]
        logger.warning(f"lora train failed for subject {subject_id}: {err}")

        if "CUDA not available" in err or "cuda.is_available" in err.lower():
            from backend.utils.platform import describe, has_cuda

            if has_cuda():
                # The host sees a CUDA GPU but the trainer's own venv does not:
                # a driver/venv mismatch, repairable on the host.
                guidance = (
                    "CUDA not available inside the isolated trainer venv, although this host "
                    f"has one ({describe()}). Run on the host:\n"
                    "  1. nvidia-smi   (must list the GPU and a recent driver)\n"
                    "  2. cd plugins/lora_trainer && ./scripts/setup_venv.sh\n"
                    "  3. Reboot if drivers were just installed.\n"
                    "Then click 'Train LoRA' again from the Cast page."
                )
            else:
                guidance = (
                    f"LoRA training needs an NVIDIA GPU (CUDA). This machine: {describe()}. "
                    "Apple Silicon (Metal) is not supported by the trainer yet."
                )
            s.training_error = (guidance + "\n\nOriginal error: " + err)[:2000]
        db.session.commit()

        if job_id:
            try:
                get_unified_progress().error_process(job_id, result.get("error") or "training failed")
            except Exception:
                pass


def _past_train_cap() -> list:
    """Subjects in 'training' whose row has not changed for LORA_REAP_STUCK_AFTER_S.

    Uses the DB clock to avoid process/DB timezone skew.
    """
    from sqlalchemy import text

    return (
        Subject.query
        .filter(Subject.training_status == "training",
                Subject.updated_at < text("now() - make_interval(secs => :stale_s)"))
        .params(stale_s=LORA_REAP_STUCK_AFTER_S)
        .all()
    )


def _mark_saved_run_trained(s: Subject) -> None:
    s.training_status = "trained"
    s.training_error = LORA_SAVED_NOTE
    _clear_saved_marker(s)
    _set_training_note(s, NOTE_LORA_SAVED)
    logger.warning(
        "reap_stuck_training: subject %s (%s) stuck in 'training' after its run saved %s — marked trained",
        s.id, s.name, s.lora_path,
    )


def reap_stuck_training_subjects() -> dict:
    """Flip Subjects wedged in training_status='training' past the train cap to
    'failed' so the UI re-enables the Train button. A worker that dies mid-run
    (its trainer daemon now reaped by PR_SET_PDEATHSIG) loses the Celery task, so
    nothing marks the Subject failed — it would otherwise stay 'training' forever.
    The cutoff comes from the one platform flag in backend.utils.platform
    (LORA_REAP_STUCK_AFTER_S) so it cannot drift from the daemon and task limits:
    it sits 15 minutes past the task hard-limit on every platform.

    A run that had already saved its LoRA (see _saved_lora_for_current_run) is
    marked 'trained' with LORA_SAVED_NOTE instead, and is not left for the train
    cap: it is promoted once LORA_SMOKE_REAP_AFTER_S has passed since the save.
    """
    from datetime import datetime, timedelta

    smoke_cutoff = utcnow() - timedelta(seconds=LORA_SMOKE_REAP_AFTER_S)
    promoted = 0
    for s in Subject.query.filter(Subject.training_status == "training").all():
        marker = _saved_lora_for_current_run(s)
        if marker is None:
            continue
        try:
            saved_at = datetime.fromisoformat(str(marker.get("saved_at")))
        except ValueError:
            continue
        if saved_at < smoke_cutoff:
            _mark_saved_run_trained(s)
            promoted += 1

    failed = 0
    for s in _past_train_cap():
        if _saved_lora_for_current_run(s) is not None:
            _mark_saved_run_trained(s)
            promoted += 1
            continue
        s.training_status = "failed"
        s.training_error = "Training did not finish (worker stopped or timed out). Safe to retry."
        logger.warning(f"reap_stuck_training: subject {s.id} ({s.name}) stuck in 'training' — marked failed")
        failed += 1
    if promoted or failed:
        db.session.commit()
    return {"reaped": failed + promoted, "failed": failed, "trained": promoted}
