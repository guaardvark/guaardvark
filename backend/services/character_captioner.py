"""VLM auto-captioner for character LoRA training data.

WHY THIS EXISTS — the freckle / horse-head story (2026-06-23):
The first ``sage_harlow`` LoRA trained on 8 head-only images whose ``.txt`` captions were
(a) never read (the run used ``caption_strategy: "filename"``) and (b) had no identity marks
and no full-body framing. The model overfit one face with no learned body, so under motion it
improvised a body/animal → "horse-head". The fix is real captions with: the trigger word bound
to the FIXED identity marks (freckles, eye color, beauty mark, hair highlights) AND explicit,
VARIED framing tags (full body / three-quarter / profile) so the LoRA actually learns a body.

This module is the missing caption step. There was NO auto-captioner anywhere in the repo —
captions were 100% hand-written. It reuses the existing offline VLM (``VisionAnalyzer`` →
Ollama Gemma-vision, the same one ``film_curator_service`` uses — no new model download, stays
fully offline) to describe ONLY the variable, non-identity attributes of each frame, then
deterministically front-loads the trigger word and appends the fixed identity marks.

Caption layout:  ``<trigger>, <framing>, <pose/gaze>, <expression>, <outfit>, <setting>,
                   <lighting>, <fixed identity marks>``

Used by:
  * ``scripts/caption_dataset.py`` — caption a manual SimpleTuner dataset before training.
  * ``backend/tasks/character_generation_tasks.generate_samples`` — write ``.txt`` sidecars
    alongside generated training images so the app-generated set is trainable as-is.

It NEVER runs training or GPU work; captioning is a Vision (Ollama) call only.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

# Canonical framing vocabulary. The captioner asks the VLM to lead with exactly one of these,
# and the pre-train gate uses ``detect_framing`` to measure pose coverage across the dataset
# (e.g. "are there enough full-body shots?"). Order = roughly tight → wide.
FRAMING_TAGS = [
    "close-up",
    "head and shoulders",
    "upper body",
    "three-quarter view",
    "full body",
    "wide shot",
]
# Framings that supervise the body (the coverage the horse-head failure was missing).
FULL_BODY_FRAMINGS = {"three-quarter view", "full body", "wide shot"}

_VLM_CAPTION_PROMPT = (
    "You are writing a concise training caption for ONE image of a cast character "
    "(human, animal, costumed figure, or creature). "
    "Output ONLY comma-separated visual tags (no sentences, no 'the image shows', no name). "
    "Cover, in this order:\n"
    "1) FRAMING — choose EXACTLY ONE: close-up, head and shoulders, upper body, "
    "three-quarter view, full body, wide shot.\n"
    "2) head/gaze direction and body/pose (or animal stance).\n"
    "3) expression if visible.\n"
    "4) clothing/outfit OR fur/feathers/scales/armor/costume surface with colors "
    "(VARIABLE details for this shot — not a full identity dump).\n"
    "5) background/setting.\n"
    "6) lighting.\n"
    "Describe ONLY these VARIABLE shot attributes. Do NOT invent a different species "
    "or costume than what is shown. Keep it under 30 words. Start with the framing tag."
)

# Props and environments get their own wording: asked about "a cast character", the
# VLM hunts for a figure in a street scene and captions a city as whatever creature
# it finds there. Framing words stay within FRAMING_TAGS so the coverage check reads them.
_VLM_CAPTION_PROMPTS = {
    "character": _VLM_CAPTION_PROMPT,
    "prop": (
        "You are writing a concise training caption for ONE image of a prop: a single object, "
        "vehicle or item. "
        "Output ONLY comma-separated visual tags (no sentences, no 'the image shows', no name). "
        "Cover, in this order:\n"
        "1) FRAMING — choose EXACTLY ONE: close-up (a detail of the object), three-quarter view, "
        "full body (the whole object fills the frame), wide shot (the object small in a scene).\n"
        "2) viewing angle: front, side, rear, top-down or interior.\n"
        "3) its state in this shot, if any (doors open, lights on, in motion).\n"
        "4) background/setting.\n"
        "5) lighting.\n"
        "Describe ONLY these VARIABLE shot attributes, not the object's own shape, colours or "
        "materials. Keep it under 30 words. Start with the framing tag."
    ),
    "environment": (
        "You are writing a concise training caption for ONE image of an environment: a place "
        "that keeps the same look in every shot. "
        "Output ONLY comma-separated visual tags (no sentences, no 'the image shows', no name). "
        "Cover, in this order:\n"
        "1) FRAMING — choose EXACTLY ONE: wide shot, close-up (a detail of the place).\n"
        "2) viewpoint: street level, aerial, from the water, interior, or similar.\n"
        "3) which part of the place this shot shows (a market street, a rooftop terrace, a room).\n"
        "4) people, vehicles or objects in the shot.\n"
        "5) weather and lighting.\n"
        "Describe ONLY these VARIABLE shot attributes, not the place's overall style or colour "
        "palette. Keep it under 30 words. Start with the framing tag."
    ),
}


def caption_prompt_for(kind: str | None) -> str:
    """Caption instruction for a Cast subject kind (character, prop, environment)."""
    return _VLM_CAPTION_PROMPTS.get((kind or "character").strip().lower(), _VLM_CAPTION_PROMPT)


def _analyzer():
    """Lazy-build the shared offline vision wrapper (same one film_curator uses)."""
    from backend.utils.vision_analyzer import VisionAnalyzer
    return VisionAnalyzer()


def _clean_vlm(text: str) -> str:
    """Normalize a VLM reply into a flat comma-separated tag string."""
    t = (text or "").strip()
    # Strip code fences / quotes / common prefixes.
    t = re.sub(r"^```.*?$", "", t, flags=re.MULTILINE).strip().strip('"').strip("'")
    t = re.sub(r"(?i)^(here('?s)?( is)?|this image (shows|depicts)|the (image|photo) (shows|depicts)|caption)\s*", "", t)
    # Strip a leading colon/dash left by a removed prefix (e.g. "shows: full body").
    t = t.lstrip(" :;-–—,")
    # Newlines / numbered list markers → commas.
    t = re.sub(r"^\s*\d+[\.\)]\s*", "", t, flags=re.MULTILINE)
    t = t.replace("\n", ", ")
    # Collapse repeated commas/whitespace and trailing punctuation.
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s*,\s*(,\s*)+", ", ", t)
    t = t.strip().strip(".,; ")
    return t


def detect_framing(caption: str) -> Optional[str]:
    """Return the canonical framing tag present in a caption, or None. Used by the gate's
    pose-coverage check and by the captioner to confirm the VLM led with a framing."""
    c = (caption or "").lower()
    # Prefer the most specific / widest match so 'full body' wins over a stray 'body'.
    for tag in ("wide shot", "full body", "three-quarter view", "upper body",
                "head and shoulders", "close-up"):
        if tag in c:
            return tag
    # Common synonyms the VLM might emit.
    if re.search(r"\bfull[- ]length\b", c):
        return "full body"
    if re.search(r"\b3/4\b|\bthree quarter\b", c):
        return "three-quarter view"
    if re.search(r"\bportrait\b|\bheadshot\b", c):
        return "close-up"
    return None


def compose_caption(
    trigger: str,
    vlm_desc: str,
    identity_marks: str = "",
    *,
    class_token: str = "person",
) -> str:
    """Assemble caption: ``a photo of {trigger}, {class}, <vlm>, <marks>``.

    Human class anchor is required so LoRA training matches infer and Z-Image
    cannot bind the trigger to cartoon animals. Never raises.
    """
    from backend.services.character_identity_prompt import compose_identity_core

    trigger = (trigger or "").strip().strip(",")
    body = _clean_vlm(vlm_desc)
    # Strip accidental leading trigger / photo-of prefix from the VLM body.
    for prefix in (
        f"a photo of {trigger}",
        f"photo of {trigger}",
        trigger,
    ):
        if prefix and body.lower().startswith(prefix.lower()):
            body = body[len(prefix):].strip().strip(",").strip()
    marks = (identity_marks or "").strip().strip(",")
    # Marks ride in the identity core; VLM body is the variable middle.
    core = compose_identity_core(trigger, class_token, "")
    parts = [p for p in (core, body, marks) if p]
    # Dedupe if body already contains marks
    out = ", ".join(parts)
    return out


def vlm_shot_description(
    image_path: str | Path,
    *,
    kind: str = "character",
    analyzer=None,
) -> Optional[str]:
    """The VLM's variable-attribute tags for one image, or None when it gave none."""
    analyzer = analyzer or _analyzer()
    try:
        # VisionAnalyzer.analyze expects a PIL Image (it base64-encodes internally), not a path.
        from PIL import Image
        img = Image.open(str(image_path)).convert("RGB")
        res = analyzer.analyze(img, caption_prompt_for(kind), think=False)
        if getattr(res, "success", False) and getattr(res, "description", "").strip():
            return res.description
        log.warning("captioner: VLM failed for %s (%s)",
                    image_path, getattr(res, "error", "no description"))
    except Exception as e:  # noqa: BLE001 — captioning must never explode a dataset run
        log.warning("captioner: exception on %s (%s)", image_path, e)
    return None


def caption_image(
    image_path: str | Path,
    *,
    trigger: str,
    identity_marks: str = "",
    class_token: str = "person",
    kind: str = "character",
    analyzer=None,
) -> str:
    """Caption a single image. Returns the composed caption string, or a minimal
    identity-core fallback if the VLM is unavailable."""
    desc = vlm_shot_description(image_path, kind=kind, analyzer=analyzer)
    if desc is None:
        log.warning("captioner: using trigger+marks fallback for %s", image_path)
    return compose_caption(trigger, desc or "", identity_marks, class_token=class_token)


def caption_dataset(
    dataset_dir: str | Path,
    *,
    trigger: str,
    identity_marks: str = "",
    class_token: str = "person",
    overwrite: bool = False,
    dry_run: bool = False,
    analyzer=None,
) -> dict:
    """Write ``<stem>.txt`` sidecars for every image in ``dataset_dir``.

    Skips images that already have a caption unless ``overwrite``. With ``dry_run`` it captions
    but writes nothing (returns the proposed captions for review). Returns a summary dict with
    per-image results and a framing-coverage tally so the caller (and the pre-train gate) can
    see whether full-body shots are present. Never raises on a single image — it logs and moves on.
    """
    d = Path(dataset_dir)
    images = sorted(p for p in d.iterdir() if p.suffix.lower() in IMAGE_EXTS) if d.is_dir() else []
    analyzer = analyzer or (None if dry_run is None else _analyzer())
    results = []
    framing_tally: dict[str, int] = {}
    written = skipped = 0
    for img in images:
        sidecar = img.with_suffix(".txt")
        if sidecar.exists() and not overwrite:
            skipped += 1
            existing = sidecar.read_text(encoding="utf-8").strip()
            fr = detect_framing(existing)
            framing_tally[fr or "unknown"] = framing_tally.get(fr or "unknown", 0) + 1
            results.append({"image": img.name, "caption": existing, "framing": fr, "action": "skipped"})
            continue
        caption = caption_image(
            img, trigger=trigger, identity_marks=identity_marks,
            class_token=class_token, analyzer=analyzer,
        )
        fr = detect_framing(caption)
        framing_tally[fr or "unknown"] = framing_tally.get(fr or "unknown", 0) + 1
        if not dry_run:
            sidecar.write_text(caption + "\n", encoding="utf-8")
            written += 1
        results.append({"image": img.name, "caption": caption, "framing": fr,
                        "action": "dry-run" if dry_run else "written"})
    full_body = sum(framing_tally.get(f, 0) for f in FULL_BODY_FRAMINGS)
    return {
        "dir": str(d),
        "images": len(images),
        "written": written,
        "skipped": skipped,
        "framing_tally": framing_tally,
        "full_body_count": full_body,
        "results": results,
    }


def _class_anchor_re(class_token: str) -> re.Pattern:
    """Whole-word pattern for the class words a caption may anchor on.

    The subject's own class (``white wolf``) and its head noun (``wolf``) count;
    a human class also accepts every human word, so a ``person`` subject keeps
    captions that say ``man`` or ``woman``.
    """
    from backend.services.character_identity_prompt import (
        _CLASS_HUMAN,
        sanitize_class_token,
    )

    cls = sanitize_class_token(class_token)
    head = cls.split()[-1]
    words = {cls, head}
    if cls in _CLASS_HUMAN or head in _CLASS_HUMAN:
        words |= _CLASS_HUMAN
    alternation = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"\b({alternation})\b", re.I)


def ensure_subject_image_captions(
    image_paths: list[str],
    *,
    trigger: str,
    identity_marks: str = "",
    class_token: str = "person",
    kind: str = "character",
    overwrite: bool = False,
    analyzer=None,
) -> dict:
    """Write ``.txt`` sidecars for cast ref/sample images missing rich captions.

    Used by cast upload + train dispatch so in-app LoRA training is not stuck with
    bare ``"a photo of {token}"`` fallbacks. An image the VLM could not caption gets
    no sidecar and counts as failed, so the next pass (train dispatch, the train
    task) captions it again instead of keeping a fallback that looks finished.
    Skips images that already have a
    non-bare sidecar unless ``overwrite``. Never raises on a single image failure.
    """
    token = (trigger or "").strip()
    marks = (identity_marks or "").strip()
    results = []
    written = skipped = failed = 0
    framing_tally: dict[str, int] = {}
    analyzer = analyzer  # lazy inside caption_image if None

    bare_prefixes = (
        f"a photo of {token}".lower() if token else "",
        f"photo of {token}".lower() if token else "",
    )
    _class_re = _class_anchor_re(class_token)

    def _is_bare(text: str) -> bool:
        t = (text or "").strip().lower().rstrip(".")
        if not t:
            return True
        if token and t in {f"a photo of {token.lower()}", token.lower()}:
            return True
        if token and t.startswith(f"a photo of {token.lower()}") and len(t) < len(token) + 24:
            return True
        for bp in bare_prefixes:
            if bp and t == bp:
                return True
        return False

    def _missing_class_anchor(text: str) -> bool:
        """True when caption lacks ``a photo of {token}`` + the subject's class early on."""
        t = (text or "").strip().lower()
        if not t:
            return True
        if token and f"a photo of {token.lower()}" not in t:
            return True
        # Class should appear early (identity core), not only buried in scene text.
        head = t[:120]
        return _class_re.search(head) is None

    for path in image_paths or []:
        p = Path(path)
        if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
            continue
        sidecar = p.with_suffix(".txt")
        existing = ""
        if sidecar.is_file():
            try:
                existing = sidecar.read_text(encoding="utf-8").strip()
            except OSError:
                existing = ""
        if (
            existing
            and not _is_bare(existing)
            and not _missing_class_anchor(existing)
            and not overwrite
        ):
            skipped += 1
            fr = detect_framing(existing)
            framing_tally[fr or "unknown"] = framing_tally.get(fr or "unknown", 0) + 1
            results.append({"image": p.name, "caption": existing, "framing": fr, "action": "skipped"})
            continue
        try:
            desc = vlm_shot_description(p, kind=kind, analyzer=analyzer)
            if desc is None:
                failed += 1
                results.append({"image": p.name, "error": "vision model gave no caption",
                                "action": "failed"})
                continue
            caption = compose_caption(
                token or p.stem, desc, marks, class_token=class_token,
            )
            sidecar.write_text(caption + "\n", encoding="utf-8")
            written += 1
            fr = detect_framing(caption)
            framing_tally[fr or "unknown"] = framing_tally.get(fr or "unknown", 0) + 1
            results.append({"image": p.name, "caption": caption, "framing": fr, "action": "written"})
        except Exception as e:  # noqa: BLE001
            failed += 1
            log.warning("ensure_subject_image_captions: failed on %s: %s", path, e)
            results.append({"image": p.name, "error": str(e)[:200], "action": "failed"})

    full_body = sum(framing_tally.get(f, 0) for f in FULL_BODY_FRAMINGS)
    return {
        "images": len(results),
        "written": written,
        "skipped": skipped,
        "failed": failed,
        "framing_tally": framing_tally,
        "full_body_count": full_body,
        "results": results,
    }


# Convenience: pull a compact identity-marks line out of a character_profile.md so the manual
# path doesn't have to hand-type it. Best-effort — returns "" if the file isn't parseable.
def marks_from_profile(profile_path: str | Path) -> str:
    """Heuristically extract fixed identity marks (skin/freckles, eyes, hair, distinguishing
    marks, tattoo) from a character_profile.md. Best-effort, returns a comma string."""
    try:
        text = Path(profile_path).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""
    wanted = {
        "skin": r"(?i)skin tone[:*\s]+(.+)",
        "eyes": r"(?i)\*\*eyes:\*\*\s*(.+)",
        "marks": r"(?i)distinguishing marks[:*\s]+(.+)",
        "hair": r"(?i)\*\*hair:\*\*\s*(.+)",
    }
    out: list[str] = []
    for _, pat in wanted.items():
        m = re.search(pat, text)
        if m:
            frag = re.sub(r"\([^)]*\)", "", m.group(1)).strip().strip(".")
            # keep it short — first clause only
            frag = re.split(r"[.;]", frag)[0].strip()
            if frag:
                out.append(frag.lower())
    return ", ".join(out)
