"""Character Generator Celery tasks.

Stills route by subject/LoRA base via media_model_registry:
  - Z-Image (product default) → offline_image_generator (Diffusers)
  - FLUX / SDXL legacy → ComfyUIImageGenerator

Both hang off ``JobKind.VIDEO_RENDER`` gpu_session so concurrent stills jobs
do not OOM the 16GB card.

Task names:
  character.generate_samples(subject_id)   — full plan + image loop
  character.regen_sample(sample_id, ...)   — single image regen

Registration: call ``create_character_generation_tasks(celery_app)`` from
``celery_app.py`` exactly like ``create_production_swarm_tasks``.
"""
from __future__ import annotations

import logging
import random
import shutil
from pathlib import Path

from celery import Celery
from flask import current_app

log = logging.getLogger(__name__)


# ── helpers ────────────────────────────────────────────────────────────────────

def _sample_output_dir(subject_id: int) -> Path:
    """Canonical on-disk location for a subject's generated reference images.

    Mirrors _storyboard_path() in production_swarm_tasks: everything under
    data/outputs/ so it lives in the DATA_DIR subtree (backed up, served by
    the static-file route).
    """
    try:
        from backend.config import STORAGE_DIR
        base = Path(STORAGE_DIR) / "outputs" / "character_samples" / str(subject_id)
    except Exception:
        base = (Path(__file__).resolve().parents[2] / "data" / "outputs"
                / "character_samples" / str(subject_id))
    base.mkdir(parents=True, exist_ok=True)
    return base


def _sample_image_path(subject_id: int, index: int) -> str:
    return str(_sample_output_dir(subject_id) / f"sample_{index}.png")


def _aspect_for_row(row) -> tuple[int, int]:
    """Pick the canvas for a shot. A standing full-length figure needs vertical room, so
    full-body slots render PORTRAIT (832x1216, /16-aligned); face/close-up/medium stay square
    1024x1024. Full-body detection is shared with the prompt composer (is_full_body) so the canvas
    and the prompt's framing directive never disagree. Note: 832x1216 (~1.01 MP) < 1024^2 (~1.05 MP),
    so portrait costs no extra VRAM."""
    from backend.services.character_generator_service import is_full_body
    if is_full_body(getattr(row, "angle", "") or "", getattr(row, "framing", "") or ""):
        return 832, 1216
    return 1024, 1024


def _resolve_cast_still_route(subject, lora_paths: list[str] | None = None) -> dict:
    """Decide offline Z-Image vs Comfy FLUX/SDXL for Cast stills.

    Prefers LoRA sidecar base when generating with a trained adapter; otherwise
    the subject's train/inference base from media_model_registry.
    """
    from backend.services.media_model_registry import (
        get_profile,
        resolve_inference_for_loras,
        subject_base_model_id,
    )

    lora_paths = [p for p in (lora_paths or []) if p]
    if lora_paths:
        try:
            info = resolve_inference_for_loras(lora_paths)
            engine = info.get("inference_engine") or "offline"
            if info.get("family") == "zimage" or engine == "offline":
                return {
                    "engine": "offline",
                    "model_key": info.get("offline_model_key") or "zimage-turbo",
                    "comfy_model": None,
                    "base_model_id": info.get("base_model_id"),
                    "family": info.get("family") or "zimage",
                    "vram_estimate_mb": int(
                        (info.get("profile") or {}).get("vram_infer_mb") or 11000
                    ),
                }
            return {
                "engine": "comfy",
                "model_key": None,
                "comfy_model": info.get("comfy_model_tag") or "sdxl",
                "base_model_id": info.get("base_model_id"),
                "family": info.get("family") or "sdxl",
                "vram_estimate_mb": 9000,
            }
        except Exception as e:
            log.warning(
                "Cast still route: LoRA resolve failed (%s); falling back to subject base",
                e,
            )

    base_id = subject_base_model_id(subject)
    profile = get_profile(base_id) or {}
    if profile.get("inference_engine") == "offline" or profile.get("family") == "zimage":
        return {
            "engine": "offline",
            "model_key": profile.get("offline_model_key") or "zimage-turbo",
            "comfy_model": None,
            "base_model_id": profile.get("id") or base_id,
            "family": profile.get("family") or "zimage",
            "vram_estimate_mb": int(profile.get("vram_infer_mb") or 11000),
        }
    return {
        "engine": "comfy",
        "model_key": None,
        "comfy_model": profile.get("comfy_model_tag") or "flux-schnell",
        "base_model_id": profile.get("id") or base_id,
        "family": profile.get("family") or "flux",
        "vram_estimate_mb": int(profile.get("vram_infer_mb") or 9000),
    }


def _render_cast_still(
    *,
    route: dict,
    prompt: str,
    loras: list[str],
    output_path: str,
    seed: int,
    width: int,
    height: int,
    offline_gen=None,
    comfy_gen=None,
    subject=None,
    use_lora: bool = True,
) -> str:
    """Render one Cast still via character_still_pipeline; raises on failure."""
    from backend.services.character_still_pipeline import render_character_still

    still = render_character_still(
        prompt,
        subjects=[subject] if subject is not None else None,
        lora_paths=list(loras) if loras else None,
        apply_subject_loras=bool(use_lora and loras),
        include_bible=False,  # Cast sheet prompts already compose trigger+bible
        source="cast",
        width=width,
        height=height,
        seed=seed,
        output_path=output_path,
        keep_pipeline=True,
    )
    if not still.success or not still.image_path:
        raise RuntimeError(still.error or "character still failed")
    return still.image_path


def _verify_angle_relabel_regen(
    *,
    row,
    subject,
    output_path: str,
    route: dict,
    loras: list[str],
    use_lora: bool,
    offline_gen=None,
    comfy_gen=None,
    analyzer=None,
) -> dict:
    """Vision-check still vs planned angle; one regen on mismatch; always relabel final.

    Returns {match, regenerated, planned, observed, model}. Non-fatal on vision errors.
    """
    from backend.services.character_angle_verify import (
        apply_relabel,
        strengthen_prompt_for_angle,
        verify_sample_angle,
    )
    from backend.models import db

    planned = row.angle or ""
    result = {
        "match": True,
        "regenerated": False,
        "planned": planned,
        "observed": None,
        "model": None,
    }
    try:
        v1 = verify_sample_angle(output_path, planned, analyzer=analyzer)
    except Exception as e:  # noqa: BLE001
        log.warning("angle verify skipped (vision error): %s", e)
        return result

    result["model"] = v1.get("model")
    result["observed"] = v1.get("observed")
    result["match"] = bool(v1.get("match", True))

    if v1.get("ok") and not v1.get("match"):
        log.info(
            "Character Generator: angle mismatch sample %s planned=%r observed=%r — regen once",
            row.index, planned, v1.get("observed"),
        )
        try:
            strong = strengthen_prompt_for_angle(row.image_prompt or subject.name, planned)
            new_seed = random.randint(1, 2 ** 31 - 1)
            width, height = _aspect_for_row(row)
            _render_cast_still(
                route=route,
                prompt=strong,
                loras=loras,
                output_path=output_path,
                seed=new_seed,
                width=width,
                height=height,
                offline_gen=offline_gen,
                comfy_gen=comfy_gen,
                subject=subject,
                use_lora=use_lora,
            )
            row.seed = new_seed
            # Keep original image_prompt (plan); strengthened text was regen-only.
            result["regenerated"] = True
            v2 = verify_sample_angle(output_path, planned, analyzer=analyzer)
            result["observed"] = v2.get("observed") or result["observed"]
            result["match"] = bool(v2.get("match", True))
            result["model"] = v2.get("model") or result["model"]
        except Exception as e:  # noqa: BLE001
            log.warning(
                "Character Generator: angle regen failed for sample %s: %s — relabeling only",
                row.index, e,
            )

    # Honest UI label = what the final pixels show
    if result.get("observed"):
        apply_relabel(row, result["observed"])
        try:
            db.session.add(row)
        except Exception:
            pass
        log.info(
            "Character Generator: sample %s angle %r → %r (regen=%s match=%s)",
            row.index, planned, result["observed"], result["regenerated"], result["match"],
        )
    return result


# ── task implementations (plain functions for testability) ─────────────────────

def generate_samples(subject_id: int, job_id: str | None = None, use_lora: bool = False,
                     append: bool = True) -> dict:
    """Plan + generate a batch of reference-sheet samples for a Subject.

    Flow:
      1. Load the Subject row; validate it exists.
      2. Call generate_character_sheet() (LLM-only, GPU-free).
      3. Persist bible + trigger_word onto the Subject.
      4. Delete any existing SubjectSample rows (idempotent re-plan).
      5. Insert one SubjectSample per shot (status=pending).
      6. Loop shots under gpu_exclusive, generating each image via FLUX.
      7. Update each row status→done/failed + image_path.

    Mirror of run_storyboard_artist: same gate, same commit pattern, same
    error surface (raises on ComfyUI unavailability so the Celery task retries
    or marks itself failed — no silent rot).

    job_id (optional): unified progress process id from dispatch. When present we
    drive updates so the job appears in unified queue / Activity / sockets for
    long batch runs of many cast items.
    """
    from backend.models import db, Subject, SubjectSample
    from backend.services.character_generator_service import generate_character_sheet
    from backend.services.job_types import JobKind
    from backend.services.plugin_bridge import PluginUnavailable, ensure_plugins_for_stage

    subject = db.session.get(Subject, subject_id)
    if subject is None:
        log.error("generate_samples: Subject %s not found", subject_id)
        return {"error": "subject_not_found"}

    # --- 0. Bring up Ollama for planning (job_critical bypasses user-disable) -
    try:
        ensure_plugins_for_stage("cast", "planning", job_critical=True)
    except PluginUnavailable as e:
        msg = f"Ollama could not be started for Cast planning: {e}"
        log.error("generate_samples: %s", msg)
        if job_id:
            try:
                from backend.utils.unified_progress_system import get_unified_progress
                get_unified_progress().error_process(job_id, msg)
            except Exception:
                pass
        return {"error": msg}

    # --- 1. LLM planning (GPU-free) -----------------------------------------
    # With trained LoRA: reuse stored bible, never invent a new look, and compose
    # prompts as trigger+variation only (identity from adapter).
    # With refs: Cast Identity Manager syncs vision bible before sheet compose;
    # never call BibleDesigner when refs exist.
    refs = list(subject.ref_image_paths or [])
    if refs:
        from backend.services.cast_identity_manager import ensure_vision_identity
        sync = ensure_vision_identity(subject)
        if not sync.get("ok"):
            msg = sync.get("message") or sync.get("error") or "identity_sync_failed"
            log.error("generate_samples: identity sync failed for %s: %s", subject_id, msg)
            if job_id:
                try:
                    from backend.utils.unified_progress_system import get_unified_progress
                    get_unified_progress().error_process(job_id, msg)
                except Exception:
                    pass
            return {"error": msg}
        db.session.refresh(subject)

    from backend.services.character_identity_prompt import (
        resolve_class_token,
        short_marks_from_subject,
    )
    class_tok = resolve_class_token(subject)
    id_marks = short_marks_from_subject(subject)
    log.info(
        "Character Generator: planning sheet for subject %s (%s) use_lora=%s refs=%d "
        "class=%s marks=%r",
        subject_id, subject.name, use_lora, len(refs), class_tok, id_marks[:80],
    )
    plan = generate_character_sheet(
        name=subject.name,
        kind=subject.kind,
        description=subject.description or "",
        trigger_word=subject.trigger_word or None,
        existing_bible=subject.bible or None,
        ref_image_paths=refs,
        prefer_vision_bible=bool(refs),
        include_bible_in_prompts=not bool(use_lora),
        invent_bible=not bool(refs or use_lora),
        class_token=class_tok,
        identity_marks=id_marks,
    )

    if plan.get("error"):
        log.error("Character Generator: bible generation failed for subject %s: %s",
                  subject_id, plan["error"])
        if job_id:
            try:
                from backend.utils.unified_progress_system import get_unified_progress
                get_unified_progress().error_process(job_id, plan["error"])
            except Exception:
                pass
        return {"error": plan["error"]}

    bible = plan["bible"]
    trigger = plan["trigger_word"]
    shots = plan["shots"]

    # --- 2. Persist bible + trigger on Subject --------------------------------
    # LoRA path: do not clobber a good vision bible with empty; still store trigger.
    if bible:
        subject.bible = bible
    if trigger:
        subject.trigger_word = trigger
    if plan.get("vision_grounded"):
        cfg = dict(subject.training_settings_json or {})
        cfg["bible_vision_grounded"] = True
        if plan.get("tags"):
            cfg["bible_vision_tags"] = plan["tags"][:32]
            cfg["bible_identity_marks"] = ", ".join(plan["tags"][:12])[:200]
        subject.training_settings_json = cfg
    db.session.flush()

    # --- 3. Prepare the sample set (APPEND by default) -----------------------
    # APPEND mode: "generate an additional batch" STACKS onto the user's curated
    # set instead of wiping it. We keep every APPROVED sample and clear only the
    # un-approved leftovers (rejects/pending/failed from a prior run) so the tab
    # doesn't accumulate junk. New rows are indexed ABOVE every kept index so
    # neither the DB rows nor the sample_<index>.png files collide with the
    # approved keepers. REPLACE mode (append=False) restores the old clean slate.
    if append:
        # Keep approved keepers AND anything already promoted into Training Data.
        SubjectSample.query.filter(
            SubjectSample.subject_id == subject_id,
            SubjectSample.approved.is_(False),
            SubjectSample.promoted_to_training.is_(False),
        ).delete(synchronize_session=False)
        db.session.flush()
        kept = SubjectSample.query.filter_by(subject_id=subject_id).all()
        base_index = (max((s.index for s in kept), default=-1)) + 1
    else:
        # REPLACE clears the generation sheet only — never drop promoted rows
        # (durable Training Data ownership lives on ref_image_paths + these flags).
        SubjectSample.query.filter_by(
            subject_id=subject_id, promoted_to_training=False,
        ).delete()
        db.session.flush()
        kept = SubjectSample.query.filter_by(subject_id=subject_id).all()
        base_index = (max((s.index for s in kept), default=-1)) + 1

    # --- 4. Insert THIS batch's SubjectSample rows (status=pending) ----------
    subject = db.session.get(Subject, subject_id)
    if subject is None:
        log.error("generate_samples: Subject %s not found (may have been deleted)", subject_id)
        db.session.rollback()
        if job_id:
            try:
                from backend.utils.unified_progress_system import get_unified_progress
                get_unified_progress().error_process(job_id, f"Subject {subject_id} not found")
            except Exception:
                pass
        return {"error": "subject_not_found"}

    try:
        for shot in shots:
            row = SubjectSample(
                subject_id=subject_id,
                index=base_index + shot["index"],
                angle=shot.get("angle") or "",
                framing=shot.get("framing") or "",
                expression=shot.get("expression") or "",
                lighting=shot.get("lighting") or "",
                scene=shot.get("scene") or "",
                image_prompt=shot.get("image_prompt") or "",
                placeholder=bool(shot.get("placeholder", False)),
                status="pending",
                approved=False,
            )
            db.session.add(row)
        db.session.commit()
    except Exception as commit_err:
        db.session.rollback()
        log.error("generate_samples: DB error creating SubjectSample rows for subject %s: %s", subject_id, commit_err)
        if job_id:
            try:
                from backend.utils.unified_progress_system import get_unified_progress
                get_unified_progress().error_process(job_id, f"DB error creating samples: {commit_err}")
            except Exception:
                pass
        return {"error": f"db_commit_failed: {commit_err}"}
    # This batch = ONLY the rows we just created (status=pending), now with PKs.
    # In append mode the kept approved samples are status=done and are deliberately
    # excluded here so the loop never re-renders (and overwrites) the user's keepers.
    sample_rows = SubjectSample.query.filter_by(
        subject_id=subject_id, status="pending"
    ).order_by(SubjectSample.index).all()
    log.info("Character Generator: %s %d new SubjectSample rows for subject %s (base_index=%d)",
             "appended" if append else "inserted", len(sample_rows), subject_id, base_index)

    loras_for_gen = []
    if use_lora and subject.lora_path:
        loras_for_gen = [subject.lora_path]
        log.info(
            "Character Generator: using trained LoRA for additional consistent images: %s",
            subject.lora_path,
        )

    route = _resolve_cast_still_route(subject, loras_for_gen)
    engine_label = (
        f"offline/{route.get('model_key')}"
        if route.get("engine") == "offline"
        else f"comfy/{route.get('comfy_model')}"
    )
    log.info(
        "Character Generator: stills route subject=%s base=%s family=%s via %s",
        subject_id,
        route.get("base_model_id"),
        route.get("family"),
        engine_label,
    )

    if job_id:
        try:
            from backend.utils.unified_progress_system import get_unified_progress
            get_unified_progress().update_process(
                job_id, 15,
                f"Plan complete — {len(sample_rows)} shots ready ({engine_label})",
            )
        except Exception:
            pass

    # --- 5. Image generation loop (GPU-exclusive) ----------------------------
    # Comfy only when the route needs it (FLUX/SDXL). Z-Image uses offline Diffusers.
    if route.get("engine") == "comfy":
        try:
            ensure_plugins_for_stage("cast", "generate_samples", job_critical=True)
        except PluginUnavailable as e:
            msg = f"ComfyUI could not be started for Cast stills: {e}"
            log.error("generate_samples: %s", msg)
            if job_id:
                try:
                    from backend.utils.unified_progress_system import get_unified_progress
                    get_unified_progress().error_process(job_id, msg)
                except Exception:
                    pass
            return {"error": msg}

    done_count = 0
    failed_count = 0

    from backend.utils.unified_progress_system import get_unified_progress
    total = len(sample_rows)

    if job_id:
        try:
            get_unified_progress().update_process(
                job_id, 5, f"Starting {engine_label} generation of {total} samples"
            )
        except Exception:
            pass

    # gpu_session (not bare gpu_exclusive) so Ollama is evicted UNDER the held slot.
    # free_comfyui frees VRAM for offline Z-Image; require_fit matches train/offline paths.
    from backend.services.character_generation_cancel import job_is_cancelled
    from backend.services.gpu_resource_policy import gpu_session

    cancelled = False
    offline_gen = None
    comfy_gen = None
    if route.get("engine") == "offline":
        from backend.services.offline_image_generator import get_image_generator
        offline_gen = get_image_generator()
    else:
        from backend.services.comfyui_image_generator import ComfyUIImageGenerator
        comfy_gen = ComfyUIImageGenerator(model=route.get("comfy_model") or "flux-schnell")

    try:
        with gpu_session(
            JobKind.VIDEO_RENDER,
            f"char_samples_{subject_id}",
            evict_ollama=True,
            free_comfyui=True,
            vram_estimate_mb=int(route.get("vram_estimate_mb") or 11000),
            require_fit=True,
            cross_process=True,
        ):
            for idx, row in enumerate(sample_rows):
                # Cooperative cancel: API marks pending/generating → cancelled and/or
                # flips the unified-progress job. Stop before starting the next shot.
                db.session.expire_all()
                fresh = db.session.get(SubjectSample, row.id)
                if fresh is None or fresh.status == "cancelled" or job_is_cancelled(job_id):
                    cancelled = True
                    log.info(
                        "Character Generator: cancel detected before sample %d/%d — stopping",
                        idx + 1, total,
                    )
                    break
                row = fresh

                output_path = _sample_image_path(subject_id, row.index)
                seed = random.randint(1, 2 ** 31 - 1)
                row.status = "generating"
                row.seed = seed
                db.session.commit()

                pct = int(((idx) / total) * 100) if total else 0
                if job_id:
                    try:
                        get_unified_progress().update_process(
                            job_id, max(5, pct),
                            f"Generating sample {idx+1}/{total} ({row.angle or 'shot'})"
                        )
                    except Exception:
                        pass

                width, height = _aspect_for_row(row)
                try:
                    prompt_txt = (row.image_prompt or subject.name or "").strip()
                    log.info(
                        "Character Generator: render sample %s/%s subject=%s use_lora=%s "
                        "loras=%s prompt=%r",
                        idx + 1, total, subject_id, bool(loras_for_gen),
                        [Path(p).name for p in loras_for_gen],
                        prompt_txt[:220],
                    )
                    _render_cast_still(
                        route=route,
                        prompt=prompt_txt,
                        loras=loras_for_gen,
                        output_path=output_path,
                        seed=seed,
                        width=width,
                        height=height,
                        offline_gen=offline_gen,
                        comfy_gen=comfy_gen,
                        subject=subject,
                        use_lora=bool(loras_for_gen),
                    )
                    # Cancel may have landed while sampling — don't overwrite cancelled.
                    db.session.refresh(row)
                    if row.status == "cancelled" or job_is_cancelled(job_id):
                        cancelled = True
                        log.info(
                            "Character Generator: cancel detected after sample %d — stopping",
                            row.index,
                        )
                        break
                    row.image_path = output_path
                    # Vision: one regen on angle mismatch, then relabel to observed.
                    try:
                        _verify_angle_relabel_regen(
                            row=row,
                            subject=subject,
                            output_path=output_path,
                            route=route,
                            loras=loras_for_gen,
                            use_lora=bool(loras_for_gen),
                            offline_gen=offline_gen,
                            comfy_gen=comfy_gen,
                        )
                    except Exception as _ve:  # noqa: BLE001
                        log.warning(
                            "Character Generator: angle verify failed for sample %d: %s",
                            row.index, _ve,
                        )
                    row.status = "done"
                    done_count += 1
                    # Textfile caption: identity-core + variation (not invented bible dump).
                    try:
                        from backend.services.cast_identity_manager import (
                            recompose_sample_prompt,
                        )
                        cap = recompose_sample_prompt(
                            row,
                            trigger=(subject.trigger_word or subject.name or "").strip(),
                            class_token=class_tok,
                            identity_marks=id_marks,
                            include_bible=False,
                            bible="",
                        ).strip()
                        if not cap:
                            cap = (row.image_prompt or subject.name or "").strip()
                        if cap:
                            Path(output_path).with_suffix(".txt").write_text(
                                cap + "\n", encoding="utf-8"
                            )
                    except Exception as _se:  # noqa: BLE001
                        log.warning(
                            "Character Generator: caption sidecar failed for sample %d: %s",
                            row.index, _se,
                        )
                    log.info(
                        "Character Generator: sample %d/%d done (%s) angle=%s",
                        row.index + 1, len(sample_rows), output_path, row.angle,
                    )
                except Exception as exc:
                    db.session.refresh(row)
                    if row.status == "cancelled" or job_is_cancelled(job_id):
                        cancelled = True
                        log.info(
                            "Character Generator: cancel interrupted sample %d — stopping (%s)",
                            row.index, exc,
                        )
                        break
                    row.status = "failed"
                    failed_count += 1
                    log.error("Character Generator: sample %d failed: %s", row.index, exc)
                finally:
                    db.session.commit()

                if cancelled:
                    break

                if job_id:
                    try:
                        get_unified_progress().update_process(
                            job_id, int(((idx + 1) / total) * 100),
                            f"Sample {idx+1}/{total} complete"
                        )
                    except Exception:
                        pass
    finally:
        if offline_gen is not None and hasattr(offline_gen, "_unload_pipeline"):
            try:
                offline_gen._unload_pipeline()
            except Exception:
                pass

    # Leave any leftover pending rows cancelled so the UI stops polling.
    if cancelled:
        leftovers = (
            SubjectSample.query
            .filter(
                SubjectSample.subject_id == subject_id,
                SubjectSample.status.in_(("pending", "generating")),
            )
            .all()
        )
        for leftover in leftovers:
            leftover.status = "cancelled"
        db.session.commit()
        if job_id:
            try:
                get_unified_progress().cancel_process(
                    job_id,
                    f"Cancelled — {done_count} done before stop",
                    additional_data={
                        "subject_id": subject_id,
                        "done": done_count,
                        "failed": failed_count,
                        "total": total,
                    },
                )
            except Exception:
                pass
        log.info(
            "Character Generator: cancelled subject %s — %d done, %d failed before stop",
            subject_id, done_count, failed_count,
        )
        return {
            "subject_id": subject_id,
            "done": done_count,
            "failed": failed_count,
            "total": len(sample_rows),
            "cancelled": True,
        }

    if job_id:
        try:
            get_unified_progress().complete_process(
                job_id,
                f"Character sample generation finished: {done_count} done, {failed_count} failed",
                additional_data={"subject_id": subject_id, "done": done_count, "failed": failed_count, "total": total}
            )
        except Exception:
            pass

    log.info("Character Generator: finished subject %s — %d done, %d failed",
             subject_id, done_count, failed_count)
    return {"subject_id": subject_id, "done": done_count, "failed": failed_count,
            "total": len(sample_rows)}


def regen_sample(sample_id: int, prompt_override: str | None = None, seed: int | None = None, job_id: str | None = None) -> dict:
    """Regenerate the image for a single SubjectSample.

    Cloned from regen_storyboard_shot: same GPU gate, same single-commit pattern.
    prompt_override replaces the stored image_prompt for this generation only
    (the stored prompt is NOT overwritten — the user is exploring, not editing the
    plan).  Pass seed=None for a fresh random seed.
    """
    from backend.models import db, Subject, SubjectSample
    from backend.services.job_types import JobKind
    from backend.services.plugin_bridge import PluginUnavailable, ensure_plugins_for_stage

    from backend.utils.unified_progress_system import get_unified_progress

    row = db.session.get(SubjectSample, sample_id)
    if row is None:
        log.error("regen_sample: SubjectSample %s not found", sample_id)
        if job_id:
            try:
                get_unified_progress().error_process(job_id, "sample not found")
            except Exception:
                pass
        return {"error": "sample_not_found"}

    subject = db.session.get(Subject, row.subject_id)
    if subject is None:
        log.error("regen_sample: Subject %s not found for sample %s", row.subject_id, sample_id)
        if job_id:
            try:
                get_unified_progress().error_process(job_id, "subject not found")
            except Exception:
                pass
        return {"error": "subject_not_found"}

    # Regen with trained LoRA when available (identity lock); otherwise base sheet.
    use_lora = bool(subject.lora_path)
    route = _resolve_cast_still_route(
        subject, [subject.lora_path] if use_lora else []
    )

    if route.get("engine") == "comfy":
        try:
            ensure_plugins_for_stage("cast", "regen_sample", job_critical=True)
        except PluginUnavailable as e:
            msg = f"ComfyUI could not be started for Cast regen: {e}"
            log.error("regen_sample: %s", msg)
            if job_id:
                try:
                    get_unified_progress().error_process(job_id, msg)
                except Exception:
                    pass
            return {"error": msg}

    effective_seed = seed if seed is not None else random.randint(1, 2 ** 31 - 1)
    effective_prompt = prompt_override if prompt_override else (row.image_prompt or "")
    output_path = _sample_image_path(row.subject_id, row.index)

    row.status = "generating"
    row.seed = effective_seed
    db.session.commit()

    if job_id:
        try:
            get_unified_progress().update_process(job_id, 10, "Starting regen")
        except Exception:
            pass

    from backend.services.character_generation_cancel import job_is_cancelled
    from backend.services.gpu_resource_policy import gpu_session

    offline_gen = None
    comfy_gen = None
    if route.get("engine") == "offline":
        from backend.services.offline_image_generator import get_image_generator
        offline_gen = get_image_generator()
    else:
        from backend.services.comfyui_image_generator import ComfyUIImageGenerator
        comfy_gen = ComfyUIImageGenerator(model=route.get("comfy_model") or "flux-schnell")

    try:
        with gpu_session(
            JobKind.VIDEO_RENDER,
            f"char_regen_{row.subject_id}",
            evict_ollama=True,
            free_comfyui=True,
            vram_estimate_mb=int(route.get("vram_estimate_mb") or 11000),
            require_fit=True,
            cross_process=True,
        ):
            try:
                if job_is_cancelled(job_id):
                    row.status = "cancelled"
                    db.session.commit()
                    return {"sample_id": sample_id, "status": "cancelled", "cancelled": True}
                if job_id:
                    try:
                        get_unified_progress().update_process(job_id, 50, "Generating image")
                    except Exception:
                        pass
                width, height = _aspect_for_row(row)
                _render_cast_still(
                    route=route,
                    prompt=effective_prompt,
                    loras=[subject.lora_path] if use_lora else [],
                    output_path=output_path,
                    seed=effective_seed,
                    width=width,
                    height=height,
                    offline_gen=offline_gen,
                    comfy_gen=comfy_gen,
                    subject=subject,
                    use_lora=use_lora,
                )
                db.session.refresh(row)
                if row.status == "cancelled" or job_is_cancelled(job_id):
                    row.status = "cancelled"
                    log.info("regen_sample: sample %s cancelled after render", sample_id)
                else:
                    row.image_path = output_path
                    # If user overrode the prompt, temporarily use it for strengthen-on-mismatch.
                    _orig_prompt = row.image_prompt
                    if prompt_override:
                        row.image_prompt = effective_prompt
                    try:
                        _verify_angle_relabel_regen(
                            row=row,
                            subject=subject,
                            output_path=output_path,
                            route=route,
                            loras=[subject.lora_path] if use_lora else [],
                            use_lora=use_lora,
                            offline_gen=offline_gen,
                            comfy_gen=comfy_gen,
                        )
                    except Exception as _ve:  # noqa: BLE001
                        log.warning("regen_sample: angle verify failed: %s", _ve)
                    finally:
                        if prompt_override:
                            row.image_prompt = _orig_prompt
                    row.status = "done"
                    log.info(
                        "regen_sample: sample %s regenerated → %s angle=%s",
                        sample_id, output_path, row.angle,
                    )
                    if job_id:
                        try:
                            get_unified_progress().update_process(job_id, 90, "Image ready")
                        except Exception:
                            pass
            except Exception as exc:
                db.session.refresh(row)
                if row.status == "cancelled" or job_is_cancelled(job_id):
                    row.status = "cancelled"
                    log.info("regen_sample: sample %s cancelled during render (%s)", sample_id, exc)
                else:
                    row.status = "failed"
                    log.error("regen_sample: sample %s failed: %s", sample_id, exc)
                    if job_id:
                        try:
                            get_unified_progress().error_process(job_id, str(exc))
                        except Exception:
                            pass
            finally:
                db.session.commit()
    finally:
        if offline_gen is not None and hasattr(offline_gen, "_unload_pipeline"):
            try:
                offline_gen._unload_pipeline()
            except Exception:
                pass

    if job_id:
        try:
            if row.status == "cancelled":
                get_unified_progress().cancel_process(
                    job_id, "Regen cancelled",
                    additional_data={"sample_id": sample_id, "status": row.status},
                )
            else:
                get_unified_progress().complete_process(
                    job_id, "Regen complete",
                    additional_data={"sample_id": sample_id, "status": row.status},
                )
        except Exception:
            pass

    return {"sample_id": sample_id, "status": row.status, "image_path": row.image_path}


# ── factory ────────────────────────────────────────────────────────────────────

def create_character_generation_tasks(celery_app: Celery):
    """Register character.* tasks with the Celery app.

    Called from backend/celery_app.py alongside create_production_swarm_tasks.
    """

    @celery_app.task(name="character.generate_samples")
    def generate_samples_task(subject_id: int, job_id: str | None = None, use_lora: bool = False,
                              append: bool = True):
        with current_app.app_context():
            return generate_samples(subject_id, job_id=job_id, use_lora=use_lora, append=append)

    @celery_app.task(name="character.regen_sample")
    def regen_sample_task(sample_id: int, prompt_override: str | None = None,
                          seed: int | None = None, job_id: str | None = None):
        with current_app.app_context():
            return regen_sample(sample_id, prompt_override=prompt_override, seed=seed, job_id=job_id)

    return {
        "generate_samples": generate_samples_task,
        "regen_sample": regen_sample_task,
    }
