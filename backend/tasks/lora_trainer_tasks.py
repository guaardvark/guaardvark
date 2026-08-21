"""Celery wiring for the lora_trainer plugin.

Same factory pattern as production_swarm_tasks. The task body is intentionally
thin: load Subject, call mock_trainer (or real trainer in v1.1), persist
results. No state-machine interaction with Production — training is per-Subject
and the cast endpoint already records the user's chosen action."""
from __future__ import annotations
import json
import logging
from pathlib import Path
from celery import Celery
from flask import current_app

from backend.models import db, Subject

logger = logging.getLogger(__name__)


def _output_dir() -> str:
    return (current_app.config.get("LORA_OUTPUT_DIR")
            or "data/training/loras")


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
        # Legacy captions missing "a photo of {token}, man|person" are rewritten.
        cap_sum = ensure_subject_image_captions(
            [p for p in train_images if p],
            trigger=token,
            identity_marks=marks,
            class_token=resolve_class_token(s),
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
        from backend.services.job_operation_gate import GpuBusyError
        from backend.services.job_types import JobKind
        from backend.services.gpu_resource_policy import gpu_session
        from backend.services.plugin_bridge import ensure_plugins_for_stage
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
            with gpu_session(JobKind.LORA_TRAIN, f"subject_{s.id}",
                             evict_ollama=True, free_comfyui=True,
                             vram_estimate_mb=12000, require_fit=True,
                             cross_process=True, lease_seconds=4 * 3600,
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
                        base_model_id=train_profile.get("id"),
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
        except GpuBusyError as e:
            logger.warning(f"lora_trainer: GPU busy for subject {subject_id}: {e}")
            return {"status": "failed", "error": f"GPU busy: {e}", "used_images": train_images}

    # Reaching here means the real trainer was NOT selected. Outside of pytest that
    # is a hard failure — we do NOT silently produce a fake LoRA (NO-MOCKS policy).
    # The most common cause in 'auto' is that RealLoraTrainer.is_available() returned
    # False: the venv-torch CUDA probe didn't see a GPU (or timed out under contention).
    # Fail loud with guidance; the caller marks the Subject 'failed' with this message.
    if not allow_mock:
        msg = ("Real LoRA trainer unavailable (venv-torch/CUDA probe failed). "
               "Verify the GPU is free (nvidia-smi) and the trainer venv exists "
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
    @celery_app.task(name="lora_trainer.train_lora")
    def train_lora_task(subject_id: int, job_id: str | None = None):
        with current_app.app_context():
            train_subject_lora_for_subject(subject_id, job_id=job_id)

    @celery_app.task(name="lora_trainer.reap_stuck_training")
    def reap_stuck_training_task():
        with current_app.app_context():
            return reap_stuck_training_subjects()

    return {"train_lora": train_lora_task, "reap_stuck_training": reap_stuck_training_task}


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
    # Subject still marked 'training' may be promoted to 'trained'.
    db.session.refresh(s)
    if s.training_status != "training":
        logger.warning(
            "lora train for %s finished but status is %r; result not recorded",
            subject_id, s.training_status,
        )
        if job_id:
            try:
                get_unified_progress().error_process(job_id, f"training superseded (status={s.training_status})")
            except Exception:
                pass
        return

    # Perform all DB updates and commit BEFORE notifying the progress system.
    # This avoids a race where the frontend receives the "complete" event and
    # does loadSubject() before the status='trained' is committed, leaving the
    # UI stuck with polling=true / spinner / "starting training" even after the
    # GPU work is finished.
    if result.get("status") == "ok":
        s.lora_path = result["lora_path"]
        s.lora_version = result.get("lora_version", 1)
        s.training_status = "trained"
        s.training_error = None
        from datetime import datetime
        s.last_trained_at = datetime.utcnow()

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

        smoke = None
        if is_real_trained and s.lora_path:
            from backend.services.lora_posttrain_smoke import run_lora_smoke_test
            from backend.services.lora_training_settings import settings_for_subject
            train_settings = settings_for_subject(s)
            smoke = run_lora_smoke_test(
                subject_id=subject_id,
                lora_path=s.lora_path,
                trigger_word=s.trigger_word or s.name,
                resolution=train_settings.get("resolution", 768),
                base_model_id=train_settings.get("base_model_id")
                or (result.get("base_model_id") if isinstance(result, dict) else None),
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
    else:
        s.training_status = "failed"
        err = result.get("error") or "training failed"
        s.training_error = err[:2000]
        logger.warning(f"lora train failed for subject {subject_id}: {err}")

        if "CUDA not available" in err or "cuda.is_available" in err.lower():
            s.training_error = (
                "CUDA not available inside the isolated trainer venv.\n\n"
                "Your RTX 4070 Ti SUPER should work. Run these on the host:\n"
                "  1. nvidia-smi   (must show your 4070 Ti SUPER and a recent driver)\n"
                "  2. cd plugins/lora_trainer && ./scripts/setup_venv.sh\n"
                "  3. Reboot if drivers were just installed.\n"
                "Then click 'Train LoRA' again from the Cast page.\n\n"
                "Original error: " + err
            )[:2000]
        db.session.commit()

        if job_id:
            try:
                get_unified_progress().error_process(job_id, result.get("error") or "training failed")
            except Exception:
                pass


def reap_stuck_training_subjects() -> dict:
    """Flip Subjects wedged in training_status='training' past the train cap to
    'failed' so the UI re-enables the Train button. A worker that dies mid-run
    (its trainer daemon now reaped by PR_SET_PDEATHSIG) loses the Celery task, so
    nothing marks the Subject failed — it would otherwise stay 'training' forever.
    The 45-minute cutoff is deliberately > the 30-min _TRAIN_TIMEOUT_S, so a job
    that is genuinely still running is never reaped. Uses the DB clock to avoid
    process/DB timezone skew."""
    from sqlalchemy import text
    stale = (
        Subject.query
        .filter(Subject.training_status == "training",
                Subject.updated_at < text("now() - interval '45 minutes'"))
        .all()
    )
    for s in stale:
        s.training_status = "failed"
        s.training_error = "Training did not finish (worker stopped or timed out). Safe to retry."
        logger.warning(f"reap_stuck_training: subject {s.id} ({s.name}) stuck in 'training' — marked failed")
    if stale:
        db.session.commit()
    return {"reaped": len(stale)}
