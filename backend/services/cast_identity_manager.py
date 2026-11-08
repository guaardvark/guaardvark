"""Cast Identity Manager — vision-first chokepoint for Subject identity.

Owns: rescan refs → bible/marks/class → caption refresh → recompose stale
SubjectSample.image_prompt rows. Deterministic orchestration (not an inventing LLM).

Distinct from FilmCrew ``CastingDirector`` (advisory cast/LoRA recommendations).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


def subject_is_vision_grounded(subject) -> bool:
    cfg = getattr(subject, "training_settings_json", None) or {}
    return bool(cfg.get("bible_vision_grounded"))


def recompose_sample_prompt(
    sample,
    *,
    trigger: str,
    class_token: str = "person",
    identity_marks: str = "",
    include_bible: bool = False,
    bible: str = "",
) -> str:
    """Rebuild image_prompt from identity core + stored angle/variation fields."""
    from backend.services.character_generator_service import _compose_prompt
    from backend.services.swarm.agents.character_designer import ShotVariation

    v = ShotVariation(
        framing=getattr(sample, "framing", None) or "",
        expression=getattr(sample, "expression", None) or "",
        lighting=getattr(sample, "lighting", None) or "",
        scene=getattr(sample, "scene", None) or "",
    )
    angle = getattr(sample, "angle", None) or ""
    return _compose_prompt(
        trigger,
        bible or "",
        v,
        angle,
        include_bible=include_bible,
        class_token=class_token,
        identity_marks=identity_marks,
    )


def recompose_sheet_prompts(
    subject,
    *,
    include_bible: bool = False,
) -> int:
    """Rewrite non-promoted SubjectSample.image_prompt rows. Returns count updated."""
    from backend.models import db, SubjectSample
    from backend.services.character_identity_prompt import (
        resolve_class_token,
        short_marks_from_subject,
    )

    trigger = (getattr(subject, "trigger_word", None) or subject.name or "").strip()
    cls = resolve_class_token(subject)
    marks = short_marks_from_subject(subject)
    bible = (getattr(subject, "bible", None) or "").strip()

    rows = (
        SubjectSample.query
        .filter_by(subject_id=subject.id, promoted_to_training=False)
        .all()
    )
    n = 0
    for row in rows:
        new_p = recompose_sample_prompt(
            row,
            trigger=trigger,
            class_token=cls,
            identity_marks=marks,
            include_bible=include_bible and not bool(getattr(subject, "lora_path", None)),
            bible=bible,
        )
        if (row.image_prompt or "").strip() != new_p:
            row.image_prompt = new_p
            n += 1
    if n:
        db.session.commit()
    return n


def sync_identity_from_refs(
    subject_id: int,
    *,
    refresh_captions: bool = True,
    refresh_sample_prompts: bool = True,
    ensure_ollama: bool = True,
) -> dict[str, Any]:
    """Vision-rescan refs and refresh bible, captions, and sheet prompts.

    Returns dict with ok=True/False plus bible, marks, class_token, samples_updated, …
    """
    from backend.models import db, Subject

    subject = db.session.get(Subject, subject_id)
    if subject is None:
        return {"ok": False, "error": "not_found"}

    refs = list(subject.ref_image_paths or [])
    existing_on_disk = [p for p in refs if p and Path(p).is_file()]
    if not existing_on_disk:
        return {
            "ok": False,
            "error": "no_refs",
            "message": "Upload reference photos first.",
        }

    if ensure_ollama:
        try:
            from backend.services.plugin_bridge import PluginUnavailable, ensure_plugins_for_stage
            ensure_plugins_for_stage("cast", "planning", job_critical=True)
        except Exception as e:
            # PluginUnavailable or import
            return {
                "ok": False,
                "error": "ollama_unavailable",
                "message": f"Ollama could not be started for vision identity: {e}",
            }

    from backend.services.character_bible_from_refs import (
        persist_bible_on_subject,
        rebuild_bible_from_refs,
    )
    from backend.services.character_identity_prompt import (
        resolve_class_token,
        sanitize_class_token,
    )

    result = rebuild_bible_from_refs(
        refs,
        name=subject.name or "",
        trigger_word=subject.trigger_word or None,
        kind=getattr(subject, "kind", None) or "character",
    )
    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("error") or "bible_rebuild_failed",
            **{k: result.get(k) for k in ("tags", "sources_used", "failed_images")},
        }

    persist_bible_on_subject(subject, result, refresh_captions=refresh_captions)
    db.session.refresh(subject)

    # Clear manual-override flag after successful vision sync
    cfg = dict(subject.training_settings_json or {})
    cfg.pop("bible_manual_override", None)
    cfg["bible_vision_grounded"] = True
    if result.get("class_token"):
        cfg["class_token"] = sanitize_class_token(result["class_token"])
    subject.training_settings_json = cfg
    db.session.commit()

    samples_updated = 0
    if refresh_sample_prompts:
        try:
            # Always identity-core + angle fields — full bible in sample prompts
            # is what poisoned regen/train with invented prose.
            samples_updated = recompose_sheet_prompts(subject, include_bible=False)
        except Exception as e:  # noqa: BLE001
            log.warning("sync_identity: sample prompt recompose failed: %s", e)

    cls = sanitize_class_token(result.get("class_token") or "") or resolve_class_token(subject)
    return {
        "ok": True,
        "bible": subject.bible,
        "trigger_word": subject.trigger_word,
        "tags": result.get("tags") or [],
        "marks": result.get("marks") or "",
        "class_token": cls,
        "method": result.get("method") or "open_consensus",
        "descriptions_used": result.get("descriptions_used"),
        "sources_used": result.get("sources_used") or [],
        "captions_refreshed": bool(result.get("captions_refreshed")),
        "samples_updated": samples_updated,
        "vision_grounded": True,
    }


def ensure_vision_identity(
    subject,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """If refs exist and identity is not vision-grounded, run sync.

    Returns {ok, synced, error?, ...}. When already grounded, ok=True synced=False.
    """
    refs = list(getattr(subject, "ref_image_paths", None) or [])
    if not refs:
        return {"ok": True, "synced": False, "skipped": "no_refs"}
    if subject_is_vision_grounded(subject) and not force:
        return {"ok": True, "synced": False, "skipped": "already_grounded"}

    out = sync_identity_from_refs(int(subject.id))
    if not out.get("ok"):
        return {**out, "synced": False}
    return {**out, "synced": True}
