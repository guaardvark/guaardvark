"""Cast LoRA pre-train validation — adapted from scripts/pretrain_gate.py for the
in-app training path (ref images + approved samples, not a flat dataset dir)."""
from __future__ import annotations

import math
import re
from pathlib import Path

from backend.models import Subject, SubjectSample


def _framing_helpers():
    try:
        from backend.services.character_captioner import detect_framing, FULL_BODY_FRAMINGS
        return detect_framing, FULL_BODY_FRAMINGS
    except Exception:
        FULL = {"three-quarter view", "full body", "wide shot"}

        def detect_framing(c: str):
            c = (c or "").lower()
            for tag in ("wide shot", "full body", "three-quarter view", "upper body",
                        "head and shoulders", "close-up"):
                if tag in c:
                    return tag
            return None

        return detect_framing, FULL


def is_bare_caption(caption: str, token: str) -> bool:
    """True when caption is essentially just ``a photo of {token}`` (no visual traits)."""
    t = (caption or "").strip().lower().rstrip(".")
    tok = (token or "").strip().lower()
    if not t:
        return True
    if tok and t in {tok, f"a photo of {tok}", f"photo of {tok}"}:
        return True
    if tok and t.startswith(f"a photo of {tok}"):
        # Allow a short trailing clause; still bare if almost nothing after.
        rest = t[len(f"a photo of {tok}"):].strip(" ,;-")
        return len(rest) < 12
    return False


def _is_ref_path(subject: Subject, image_path: str) -> bool:
    refs = set(subject.ref_image_paths or [])
    if image_path in refs:
        return True
    try:
        return "cast_refs" in Path(image_path).parts
    except Exception:
        return False


def _is_class_anchored(caption: str, token: str, class_token: str = "person") -> bool:
    """True when caption looks like ``a photo of {token}, {class}…`` (not invent prose)."""
    t = (caption or "").strip().lower()
    tok = (token or "").strip().lower()
    if not t or not tok:
        return False
    if t.startswith(f"a photo of {tok}"):
        return True
    cls = (class_token or "person").strip().lower()
    return f"a photo of {tok}, {cls}" in t


def _read_sidecar(image_path: str) -> str:
    sidecar = Path(image_path).with_suffix(".txt")
    if not sidecar.is_file():
        return ""
    try:
        return sidecar.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _caption_for_path(
    subject: Subject,
    image_path: str,
    sample_by_path: dict[str, SubjectSample],
) -> str:
    """Prefer vision/ref sidecars; never feed invented full Sample.image_prompt into train."""
    from backend.services.character_identity_prompt import (
        compose_identity_core,
        resolve_class_token,
        short_marks_from_subject,
    )

    token = (subject.trigger_word or "").strip() or subject.name
    cls = resolve_class_token(subject)
    marks = short_marks_from_subject(subject)
    bare = compose_identity_core(token, cls, marks)

    # 1) Reference photos: sidecar first, then class-anchored bare fallback.
    if _is_ref_path(subject, image_path):
        text = _read_sidecar(image_path)
        if text:
            if token.lower() not in text.lower():
                return f"{bare}, {text}" if bare else text
            return text
        return bare

    # 2) Approved generated samples: class-anchored sidecar, else recompose from
    #    identity core + angle fields — never pass legacy invented image_prompt.
    smp = sample_by_path.get(image_path)
    text = _read_sidecar(image_path)
    if text and _is_class_anchored(text, token, cls):
        return text

    if smp is not None:
        try:
            from backend.services.cast_identity_manager import recompose_sample_prompt
            return recompose_sample_prompt(
                smp,
                trigger=token,
                class_token=cls,
                identity_marks=marks,
                include_bible=False,
                bible="",
            )
        except Exception:
            pass

    if text:
        if token.lower() not in text.lower():
            return f"{bare}, {text}" if bare else text
        return text
    return bare


# Features a character keeps in every picture. A caption that names one binds it
# to that word, and the trigger word then carries nothing: measured 2026-11-20 on
# Z-Image Turbo with the training adapter (31 pictures, 1024 px, rank 32, 1500
# steps, renders prompted with the trigger word alone). Captions naming the hair
# streaks, tattoos and piercing gave a LoRA that drew none of them and no stable
# face; the same pictures captioned with framing, pose, clothing and setting only
# gave the same face and streaks in 15 of 15 renders and the arm tattoos in 9 of 9.
_PERMANENT_FEATURE_RE = re.compile(
    r"\b(hair\w*|\w*haired|tattoo\w*|inked|eyes?|\w+-eyed|skin\w*|complexion|freckle\w*|"
    r"scar(?:s|red)?|birthmark\w*|moles?|beauty mark\w*|piercing\w*|pierced|septum|"
    r"nose (?:ring|hoop|stud)\w*|lips?|lipstick|makeup|eyeliner|eyeshadow|"
    r"beard\w*|bearded|stubble|mustache\w*|moustache\w*|sideburns)\b",
    re.I,
)


def _identity_mark_tags(subject: Subject) -> set[str]:
    cfg = getattr(subject, "training_settings_json", None) or {}
    tags = [t for t in (cfg.get("bible_identity_marks") or "").split(",")]
    tags += [str(t) for t in (cfg.get("bible_vision_tags") or [])]
    return {t.strip().lower() for t in tags if t and t.strip()}


def caption_without_identity(caption: str, subject: Subject) -> str:
    """The caption with the character's permanent features taken out, leaving the
    trigger word, the class and what changes between pictures. Other kinds of
    subject pass through unchanged (not measured)."""
    if (getattr(subject, "kind", None) or "character") != "character":
        return caption
    tags = [t.strip() for t in (caption or "").split(",")]
    tags = [t for t in tags if t]
    if len(tags) <= 2:
        return caption
    marks = _identity_mark_tags(subject)
    # The first two tags are "a photo of <trigger>" and the class.
    kept = tags[:2] + [
        t for t in tags[2:]
        if t.lower() not in marks and not _PERMANENT_FEATURE_RE.search(t)
    ]
    return ", ".join(kept)


def build_training_captions(
    subject: Subject,
    train_images: list[str],
) -> list[str]:
    """Per-image captions for the trainer (parallel to train_images)."""
    approved = (
        SubjectSample.query
        .filter_by(subject_id=subject.id, approved=True, status="done")
        .all()
    )
    sample_by_path = {s.image_path: s for s in approved if s.image_path}
    return [
        caption_without_identity(_caption_for_path(subject, p, sample_by_path), subject)
        for p in train_images
    ]


def caption_coverage_stats(
    subject: Subject,
    train_images: list[str] | None = None,
) -> dict:
    """Stats for Cast UI / train gate: how many captions are rich vs bare fallback."""
    paths = train_images
    if paths is None:
        paths = list(subject.ref_image_paths or [])
        approved = (
            SubjectSample.query
            .filter_by(subject_id=subject.id, approved=True, status="done")
            .all()
        )
        for smp in approved:
            if smp.image_path and smp.image_path not in paths:
                paths.append(smp.image_path)
    existing = [p for p in (paths or []) if p and Path(p).is_file()]
    token = (subject.trigger_word or "").strip() or subject.name
    captions = build_training_captions(subject, existing)
    bare = sum(1 for c in captions if is_bare_caption(c, token))
    rich = max(0, len(captions) - bare)
    return {
        "images": len(existing),
        "rich_captions": rich,
        "bare_captions": bare,
        "captions": captions,
        "trigger_word": token,
    }


def _angle_unverified_paths(subject: Subject) -> set[str]:
    from backend.services.character_angle_verify import ANGLE_UNVERIFIED
    rows = SubjectSample.query.filter_by(subject_id=subject.id, angle_state=ANGLE_UNVERIFIED).all()
    return {s.image_path for s in rows if s.image_path}


def validate_cast_training(
    subject: Subject,
    train_images: list[str],
    *,
    min_images: int = 4,
    require_trigger_in_captions: bool = True,
) -> dict:
    """Return {pass, failures, warnings, captions, framing, ...}."""
    detect_framing, FULL_BODY_FRAMINGS = _framing_helpers()
    failures: list[str] = []
    warnings: list[str] = []
    token = (subject.trigger_word or "").strip() or subject.name

    existing = [p for p in train_images if p and Path(p).is_file()]
    missing = [p for p in train_images if p and not Path(p).is_file()]
    for p in missing:
        failures.append(f"missing image file: {p}")

    n = len(existing)
    if n < min_images:
        failures.append(f"only {n} trainable image(s); need at least {min_images}")

    captions = build_training_captions(subject, existing)
    # A sample whose angle check could not run keeps its planned label in the
    # caption; that label was never seen in the image, so it is not coverage.
    unverified_paths = _angle_unverified_paths(subject)
    framing_tally: dict[str, int] = {}
    bare_count = 0
    angle_unverified = 0
    for path, cap in zip(existing, captions):
        if path in unverified_paths:
            angle_unverified += 1
        else:
            fr = detect_framing(cap) or "unknown"
            framing_tally[fr] = framing_tally.get(fr, 0) + 1
        if require_trigger_in_captions and token.lower() not in cap.lower():
            failures.append(f"trigger '{token}' missing from caption for an image")
        if is_bare_caption(cap, token):
            bare_count += 1

    rich_count = max(0, len(captions) - bare_count)
    if n and bare_count > 0.5 * n:
        warnings.append(
            f"{bare_count}/{n} captions are bare \"a photo of {token}\" fallbacks — "
            "VLM captioning should run before train; identity will be weak without "
            "visual trait text on the uploads"
        )

    full_body = sum(framing_tally.get(f, 0) for f in FULL_BODY_FRAMINGS)
    need_full = max(2, math.ceil(0.15 * n)) if n else 2
    if n and full_body < need_full:
        warnings.append(
            f"only {full_body} full-body/three-quarter/wide caption(s); "
            f"recommend >= {need_full} for stable body identity (framing={framing_tally})"
        )

    if n < 12:
        warnings.append(
            f"only {n} images — workable for a quick LoRA, but 12–30 with varied "
            "framing/outfits improves robustness"
        )

    return {
        "pass": not failures,
        "images": n,
        "failures": failures,
        "warnings": warnings,
        "captions": captions,
        "framing": framing_tally,
        "full_body_count": full_body,
        "full_body_recommended": need_full,
        "angle_unverified": angle_unverified,
        "bare_captions": bare_count,
        "rich_captions": rich_count,
    }