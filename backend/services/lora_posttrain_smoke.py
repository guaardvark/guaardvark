"""Best-effort smoke render after a successful cast LoRA train."""
from __future__ import annotations

import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)


def run_lora_smoke_test(
    *,
    subject_id: int,
    lora_path: str,
    trigger_word: str,
    resolution: int = 768,
    base_model_id: str | None = None,
    ref_image_paths: list[str] | None = None,
    subject=None,
) -> dict:
    """Generate one quick still with the new LoRA via character_still_pipeline.

    Uses the same identity-core prompt shape as Cast LoRA generate. Retries once
    after a short wait when the GPU is still cooling down post-train.
    Also scores identity vs training refs when available. Non-fatal on failure.
    """
    token = (trigger_word or "").strip() or f"subject_{subject_id}"
    out_dir = Path(lora_path).parent / "smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(out_dir / f"smoke_{subject_id}.png")

    from backend.services.character_identity_prompt import (
        compose_identity_core,
        resolve_class_token,
        short_marks_from_subject,
    )

    # Prefer live Subject when provided; else load for class/marks.
    subj = subject
    if subj is None:
        try:
            from backend.models import Subject
            subj = Subject.query.get(subject_id)
        except Exception:
            subj = None

    cls = resolve_class_token(subj) if subj is not None else "person"
    marks = short_marks_from_subject(subj) if subj is not None else ""
    core = compose_identity_core(token, cls, marks)
    prompt = f"{core}, portrait, neutral studio lighting, sharp focus"
    res = max(512, (int(resolution) // 64) * 64)

    last_err: Exception | None = None
    for attempt in range(2):
        try:
            return _run_smoke_once(
                subject_id=subject_id,
                lora_path=lora_path,
                prompt=prompt,
                out_path=out_path,
                resolution=res,
                base_model_id=base_model_id,
                ref_image_paths=ref_image_paths,
                subject=subj,
            )
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            cool = "cooling" in msg or "cooldown" in msg or "cool down" in msg
            if cool and attempt == 0:
                log.info(
                    "lora smoke: GPU cooling for subject %s — retry in 10s",
                    subject_id,
                )
                time.sleep(10)
                continue
            log.warning(
                "lora smoke test failed for subject %s (non-fatal): %s",
                subject_id, e,
            )
            return {"ok": False, "error": str(e)}

    log.warning(
        "lora smoke test failed for subject %s (non-fatal): %s",
        subject_id, last_err,
    )
    return {"ok": False, "error": str(last_err or "smoke failed")}


def _run_smoke_once(
    *,
    subject_id: int,
    lora_path: str,
    prompt: str,
    out_path: str,
    resolution: int,
    base_model_id: str | None,
    ref_image_paths: list[str] | None,
    subject=None,
) -> dict:
    from backend.services.gpu_resource_policy import gpu_session
    from backend.services.job_types import JobKind
    from backend.services.character_still_pipeline import render_character_still
    from backend.services.media_model_registry import read_lora_sidecar

    meta = read_lora_sidecar(lora_path) or {}
    bid = base_model_id or meta.get("base_model_id")

    log.info(
        "lora smoke: subject=%s prompt=%r lora=%s",
        subject_id, prompt[:200], Path(lora_path).name,
    )

    from backend.services.gpu_resource_policy import compositor_vram_reserve_mb
    with gpu_session(
        JobKind.LORA_TRAIN,
        f"smoke_{subject_id}",
        evict_ollama=True,
        free_comfyui=True,
        vram_estimate_mb=11000,
        require_fit=True,
        cross_process=True,
        vram_reserve_mb=compositor_vram_reserve_mb(),
    ):
        still = render_character_still(
            prompt,
            subjects=[subject] if subject is not None else None,
            lora_paths=[lora_path],
            apply_subject_loras=True,
            include_bible=False,
            source="smoke",
            width=resolution,
            height=resolution,
            seed=42,
            output_path=out_path,
            keep_pipeline=False,
        )

    if not still.success or not still.image_path or not Path(still.image_path).is_file():
        return {"ok": False, "error": still.error or "smoke image missing", "base_model_id": bid}

    identity = {}
    refs = [p for p in (ref_image_paths or []) if p and Path(p).is_file()][:8]
    if refs:
        try:
            from backend.services.video_consistency_metrics import score_smoke_vs_refs
            m = score_smoke_vs_refs(refs, still.image_path)
            identity = m.get("identity") or {}
            log.info(
                "lora smoke identity for subject %s: score=%s method=%s",
                subject_id, identity.get("score"), identity.get("method"),
            )
        except Exception as e:
            log.debug("smoke identity score skipped: %s", e)

    try:
        from backend.models import db, Subject
        s = subject if subject is not None else db.session.get(Subject, subject_id)
        if s is not None:
            cfg = dict(s.training_settings_json or {})
            cfg["smoke_identity"] = {
                "ok": True,
                "path": still.image_path,
                "score": identity.get("score"),
                "method": identity.get("method"),
                "base_model_id": bid or still.metadata.get("base_model_id"),
                "family": still.metadata.get("family"),
                "lora_strength": still.metadata.get("lora_strength"),
                "prompt": prompt[:300],
            }
            s.training_settings_json = cfg
            db.session.commit()
    except Exception as e:
        log.debug("could not persist smoke_identity: %s", e)

    log.info("lora smoke ok for subject %s → %s", subject_id, still.image_path)
    return {
        "ok": True,
        "path": still.image_path,
        "base_model_id": bid or still.metadata.get("base_model_id"),
        "identity": identity,
        "family": still.metadata.get("family"),
        "lora_strength": still.metadata.get("lora_strength"),
        "prompt": prompt,
    }
