"""Vision-ground a cast Subject.bible from uploaded reference photos.

Trust-the-model path: open per-photo description → consensus JSON from Gemma
(no hand-built cape/belt/hair taxonomy). Opposite of BibleDesigner (text invent).

Used by Cast Identity Manager sync, plan/generate when refs exist, and train gate.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

# Open-ended — do not quiz the model on human face categories.
_PER_IMAGE_PROMPT = (
    "Look at this reference photo of ONE cast character. "
    "The character may be a human, animal, costumed figure, creature, robot, or other being.\n"
    "In 2–4 short sentences, describe the FIXED visual identity you actually see: "
    "what kind of being/character it is, and the distinctive appearance details that "
    "should stay the same in every shot.\n"
    "Do NOT invent anything not visible. "
    "Do NOT mention camera angle, shot framing, or background unless it is part of the costume. "
    "Do NOT fill empty categories. Plain prose only."
)

_CONSENSUS_PROMPT = (
    "These are independent descriptions of the SAME {what} from different reference photos.\n"
    "Label (may be wrong — trust the photos/descriptions): {name}\n\n"
    "Descriptions:\n{block}\n\n"
    "Synthesize ONE identity record. Prefer traits that appear in multiple descriptions; "
    "drop one-off {noise} noise; do not invent traits unsupported by the descriptions.\n"
    "Return ONLY valid JSON (no markdown fences):\n"
    '{{\n'
    '  "class_token": "what it IS — short noun phrase, e.g. {examples} (not a proper name)",\n'
    '  "marks": "comma-separated distinctive FIXED traits, about 8–20 words",\n'
    '  "bible": "2–4 sentence identity paragraph: what it is + consistent appearance. '
    'End with: Keep this exact appearance in every shot."\n'
    "}}"
)

# Props and environments are described as what they are. Asked about "ONE cast
# character", the vision model finds a figure in a street scene and the place's
# identity becomes that figure (a city of 23 photos came back as "ethereal koi fish").
_PER_IMAGE_PROMPTS = {
    "character": _PER_IMAGE_PROMPT,
    "prop": (
        "Look at this reference photo of ONE prop: a single object, vehicle or item that must "
        "look the same in every shot.\n"
        "In 2–4 short sentences, describe the FIXED design you actually see: what kind of "
        "object it is, its shape, colours, materials, markings and ornaments.\n"
        "Do NOT invent anything not visible. "
        "Do NOT describe people, the background or the lighting. "
        "Do NOT mention camera angle or shot framing. Plain prose only."
    ),
    "environment": (
        "Look at this reference photo of ONE place: an environment that must keep the same "
        "look in every shot.\n"
        "In 2–4 short sentences, describe the FIXED look of the place you actually see: what "
        "kind of place it is, its architecture or landscape, colour palette, light, weather "
        "and signature details.\n"
        "People, vehicles and creatures passing through are not the place; mention them only "
        "when they are part of its look. "
        "Do NOT invent anything not visible. "
        "Do NOT mention camera angle or shot framing. Plain prose only."
    ),
}

_CONSENSUS_WORDING = {
    "character": {
        "what": "cast character", "noise": "pose/background",
        "examples": "man, woman, white wolf, dog, robot, costumed man",
    },
    "prop": {
        "what": "prop (one object)", "noise": "angle/background",
        "examples": "sports car, sword, desk lamp, guitar",
    },
    "environment": {
        "what": "place (one environment, seen in different views)",
        "noise": "passer-by/vehicle",
        "examples": "harbor city, pine forest, desert town, recording studio",
    },
}

# Class anchor when consensus names none; "person" would make a place a human.
_FALLBACK_CLASS = {"prop": "prop", "environment": "place"}


def _kind(kind: Optional[str]) -> str:
    k = (kind or "character").strip().lower()
    return k if k in _PER_IMAGE_PROMPTS else "character"


def sample_ref_paths(paths: list[str], *, max_n: int = 12) -> list[str]:
    """Pick up to max_n existing image paths, spread across the list."""
    existing = [
        p for p in (paths or [])
        if p and Path(p).is_file() and Path(p).suffix.lower() in IMAGE_EXTS
    ]
    if not existing:
        return []
    if len(existing) <= max_n:
        return existing
    step = len(existing) / max_n
    return [existing[min(len(existing) - 1, int(i * step))] for i in range(max_n)]


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I)
    t = re.sub(r"\s*```$", "", t)
    return t.strip().strip('"').strip("'")


def describe_identity_open(image_path: str, *, analyzer=None, kind: str = "character") -> str:
    """Open vision prose for one ref. Empty string on failure."""
    try:
        from PIL import Image
        from backend.utils.vision_analyzer import VisionAnalyzer
        az = analyzer or VisionAnalyzer()
        img = Image.open(str(image_path)).convert("RGB")
        res = az.analyze(
            img, _PER_IMAGE_PROMPTS[_kind(kind)], think=False, temperature=0.2, num_predict=220,
        )
        if not getattr(res, "success", False):
            log.warning(
                "bible_from_refs: vision failed on %s: %s",
                image_path, getattr(res, "error", "?"),
            )
            return ""
        text = _strip_fences(getattr(res, "description", "") or "")
        # Drop common waffle prefixes
        text = re.sub(
            r"(?i)^(this (image|photo|picture) (shows|depicts|features)|"
            r"in this (image|photo)|here('?s| is))\s*[:\-]?\s*",
            "",
            text,
        ).strip()
        return text
    except Exception as e:  # noqa: BLE001
        log.warning("bible_from_refs: exception on %s: %s", image_path, e)
        return ""


def _parse_consensus_json(raw: str) -> dict[str, str]:
    """Extract class_token / marks / bible from model JSON (tolerant)."""
    t = _strip_fences(raw)
    # Try whole string, then first {...} blob
    candidates = [t]
    m = re.search(r"\{[\s\S]*\}", t)
    if m:
        candidates.insert(0, m.group(0))
    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        bible = (data.get("bible") or data.get("identity") or "").strip()
        marks = (data.get("marks") or data.get("identity_marks") or "").strip()
        cls = (data.get("class_token") or data.get("class") or data.get("species") or "").strip()
        if bible:
            return {"bible": bible, "marks": marks, "class_token": cls}
    # A reply that does not parse is never used as the bible: it may be
    # truncated JSON, and the bible feeds the class token and every caption.
    # The caller falls back to the first photo description instead.
    return {}


# Preferred consensus model. It is a preference, not a requirement: the tag is
# resolved against what Ollama has installed (same family first, then the active
# chat model, the hardware tier, any Gemma, any chat model).
CONSENSUS_MODEL_PREFERENCE = "gemma4:12b"

NO_CONSENSUS_MODEL = (
    "no installed chat model for the identity consensus step "
    "(Ollama lists none, or is unreachable); install a chat model and retry"
)


def resolve_consensus_model(preferred: Optional[str] = None) -> Optional[str]:
    """Installed chat model for the consensus step, or None when none is installed."""
    from backend.services.ollama_chat_model import installed_chat_tags, resolve_chat_model

    tags = installed_chat_tags()
    if not tags:
        return None
    return resolve_chat_model(preferred or CONSENSUS_MODEL_PREFERENCE, installed=tags)


def _default_consensus_llm(*, system: str, user: str, model: Optional[str] = None) -> str:
    import ollama
    from backend.utils.ollama_resource_manager import think_payload
    model = model or resolve_consensus_model()
    if not model:
        raise RuntimeError(NO_CONSENSUS_MODEL)
    resp = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        format="json",
        options={"temperature": 0.2, "num_predict": 400},
        **think_payload(model),
    )
    return (resp.get("message") or {}).get("content") or ""


def consensus_identity_from_descriptions(
    descriptions: list[str],
    *,
    name: str = "",
    llm=None,
    model: Optional[str] = None,
    kind: str = "character",
) -> dict[str, str]:
    """Merge open per-photo descriptions into class_token / marks / bible.

    ``model`` None lets the default LLM resolve an installed chat model; when
    the consensus call fails, the first description becomes the bible.
    """
    cleaned = [d.strip() for d in descriptions if (d or "").strip()]
    if not cleaned:
        return {}

    # Single description: still ask consensus so class_token/marks are structured,
    # but if LLM fails, fall back to the prose as bible.
    block = "\n".join(f"{i + 1}. {d}" for i, d in enumerate(cleaned))
    user = _CONSENSUS_PROMPT.format(
        name=(name or "character").strip(), block=block, **_CONSENSUS_WORDING[_kind(kind)],
    )
    system = (
        "You synthesize visual identity for offline cast/LoRA training. "
        "Output JSON only. Trust the photo descriptions over the label name."
    )
    call = llm or _default_consensus_llm
    try:
        raw = call(system=system, user=user, model=model)
    except TypeError:
        # Older llm callables may only take system/user
        raw = call(system=system, user=user)
    except Exception as e:  # noqa: BLE001
        log.warning("bible_from_refs: consensus LLM failed: %s", e)
        raw = ""

    parsed = _parse_consensus_json(raw) if raw else {}
    if parsed.get("bible"):
        return parsed

    # Fallback: first description as bible; marks = short slice
    bible = cleaned[0]
    if len(cleaned) > 1:
        bible = (
            f"{(name or 'Character').strip()}: {cleaned[0]} "
            f"(also consistent with {len(cleaned) - 1} other refs). "
            f"Keep this exact appearance in every shot."
        )
    elif "keep this exact appearance" not in bible.lower():
        bible = bible.rstrip(".") + ". Keep this exact appearance in every shot."
    return {"bible": bible, "marks": "", "class_token": ""}


def marks_from_bible(bible: str, *, max_chars: int = 200) -> str:
    """Best-effort short marks when consensus omitted them — first clause slice."""
    t = (bible or "").strip()
    if not t:
        return ""
    # Drop trailing keep-this sentence
    t = re.split(r"(?i)keep this exact appearance", t, maxsplit=1)[0].strip(" .;")
    # Prefer text after ":" if "Name: …"
    if ":" in t[:48]:
        t = t.split(":", 1)[1].strip()
    # Comma-join first ~few clauses
    parts = [p.strip() for p in re.split(r"[.;]", t) if p.strip()]
    if parts:
        t = parts[0]
    if len(t) > max_chars:
        t = t[:max_chars].rsplit(",", 1)[0].strip()
    return t


# ── Legacy helpers (tests / older callers) — thin wrappers on open path ─────

def _clean_tags(text: str) -> list[str]:
    """Legacy: split prose/tags into tokens (kept for older unit tests)."""
    t = _strip_fences(text).replace("\n", ", ")
    parts = [p.strip().strip(".").lower() for p in t.split(",") if p.strip()]
    return [p for p in parts if len(p) >= 3 and p not in ("none", "n/a", "null")]


def extract_identity_tags(image_path: str, *, analyzer=None) -> list[str]:
    """Legacy API: open describe → rough tag split (prefer describe_identity_open)."""
    prose = describe_identity_open(image_path, analyzer=analyzer)
    return _clean_tags(prose) if prose else []


def merge_identity_tags(tag_lists: list[list[str]], *, min_count: int = 1) -> list[str]:
    """Legacy frequency merge — used only by older tests; prefer consensus_identity_*."""
    from collections import Counter
    flat: list[str] = []
    for tags in tag_lists:
        flat.extend(tags)
    if not flat:
        return []
    chosen: list[str] = []
    for tag, n in Counter(flat).most_common():
        if n < min_count:
            continue
        if tag not in chosen:
            chosen.append(tag)
        if len(chosen) >= 28:
            break
    return chosen


def tags_to_bible(tags: list[str], *, name: str = "") -> str:
    """Legacy: join tags into a bible paragraph."""
    if not tags:
        return ""
    who = (name or "the subject").strip()
    body = ", ".join(tags)
    return (
        f"{who}: {body}. "
        f"Keep this exact appearance in every shot."
    )


def short_identity_marks(tags: list[str], *, max_chars: int = 200) -> str:
    """Legacy compact marks from tag list."""
    if not tags:
        return ""
    s = ", ".join(tags)
    if len(s) > max_chars:
        s = s[:max_chars].rsplit(",", 1)[0]
    return s


def rebuild_bible_from_refs(
    ref_image_paths: list[str],
    *,
    name: str = "",
    trigger_word: str | None = None,
    max_refs: int = 12,
    analyzer=None,
    min_tag_count: int = 1,  # noqa: ARG001 — kept for call-site compat
    llm=None,
    consensus_model: Optional[str] = None,
    kind: str = "character",
) -> dict[str, Any]:
    """Scan refs with open vision + consensus; return grounded bible dict.

    ``consensus_model`` is a preference resolved against installed Ollama chat
    models; with the default LLM and none installed, returns ok False with that
    reason instead of building a bible from the fallback.

    Returns:
      {ok, bible, trigger_word, tags, marks, class_token, sources_used, …}
    """
    from backend.services.character_generator_service import _fallback_trigger
    from backend.services.character_identity_prompt import sanitize_class_token

    sampled = sample_ref_paths(ref_image_paths, max_n=max_refs)
    trigger = (trigger_word or "").strip() or _fallback_trigger(name or "chr")
    if not sampled:
        return {
            "ok": False,
            "bible": "",
            "trigger_word": trigger,
            "tags": [],
            "marks": "",
            "class_token": "person",
            "sources_used": [],
            "error": "no reference images found on disk",
        }

    if llm is None:
        consensus_model = resolve_consensus_model(consensus_model)
        if not consensus_model:
            return {
                "ok": False,
                "bible": "",
                "trigger_word": trigger,
                "tags": [],
                "marks": "",
                "class_token": "person",
                "sources_used": sampled,
                "error": NO_CONSENSUS_MODEL,
            }
        log.info("bible_from_refs: consensus model %s", consensus_model)

    descriptions: list[str] = []
    errors: list[str] = []
    for p in sampled:
        prose = describe_identity_open(p, analyzer=analyzer, kind=kind)
        if prose:
            descriptions.append(prose)
        else:
            errors.append(Path(p).name)

    if not descriptions:
        return {
            "ok": False,
            "bible": "",
            "trigger_word": trigger,
            "tags": [],
            "marks": "",
            "class_token": "person",
            "sources_used": sampled,
            "error": "vision produced no usable identity descriptions",
            "failed_images": errors,
        }

    consensus = consensus_identity_from_descriptions(
        descriptions, name=name, llm=llm, model=consensus_model, kind=kind,
    )
    bible = (consensus.get("bible") or "").strip()
    marks = (consensus.get("marks") or "").strip() or marks_from_bible(bible)
    cls = sanitize_class_token(consensus.get("class_token") or "")
    # If consensus left class empty, infer from bible/marks text
    if cls == "person" and not (consensus.get("class_token") or "").strip():
        if _kind(kind) in _FALLBACK_CLASS:
            cls = _FALLBACK_CLASS[_kind(kind)]
        else:
            from backend.services.character_identity_prompt import resolve_class_token
            cls = resolve_class_token(tags=[marks], bible=bible, description=name)

    if not bible:
        return {
            "ok": False,
            "bible": "",
            "trigger_word": trigger,
            "tags": [],
            "marks": "",
            "class_token": cls,
            "sources_used": sampled,
            "error": "consensus produced empty bible",
            "failed_images": errors,
        }

    # tags: keep marks split for UI/debug + older callers
    tags = _clean_tags(marks) if marks else _clean_tags(bible)[:16]

    return {
        "ok": True,
        "bible": bible,
        "trigger_word": trigger,
        "tags": tags,
        "marks": marks,
        "class_token": cls,
        "sources_used": sampled,
        "failed_images": errors,
        "vision_grounded": True,
        "method": "open_consensus",
        "descriptions_used": len(descriptions),
        "consensus_model": consensus_model,
    }


def persist_bible_on_subject(subject, result: dict, *, refresh_captions: bool = True) -> dict:
    """Write bible/trigger/class onto Subject; optionally refresh caption sidecars."""
    from backend.models import db

    if not result.get("ok") or not result.get("bible"):
        return result

    subject.bible = result["bible"]
    if result.get("trigger_word"):
        subject.trigger_word = result["trigger_word"]

    from backend.services.character_identity_prompt import (
        resolve_class_token,
        sanitize_class_token,
    )
    cfg = dict(getattr(subject, "training_settings_json", None) or {})
    cfg["bible_vision_grounded"] = True
    cfg["bible_vision_tags"] = list(result.get("tags") or [])[:32]
    cfg["bible_identity_marks"] = result.get("marks") or ""
    cls = sanitize_class_token(result.get("class_token") or "")
    kind = _kind(getattr(subject, "kind", None))
    if cls == "person" and not result.get("class_token"):
        cls = _FALLBACK_CLASS.get(kind) or resolve_class_token(
            subject,
            tags=list(result.get("tags") or []),
            bible=result.get("bible") or "",
        )
    cfg["class_token"] = cls
    cfg["bible_vision_method"] = result.get("method") or "open_consensus"
    subject.training_settings_json = cfg
    db.session.commit()

    if refresh_captions:
        try:
            from backend.services.character_captioner import ensure_subject_image_captions
            paths = list(subject.ref_image_paths or [])
            ensure_subject_image_captions(
                paths,
                trigger=(subject.trigger_word or subject.name or "").strip(),
                identity_marks=result.get("marks") or "",
                class_token=cls,
                kind=kind,
                overwrite=True,
            )
            result["captions_refreshed"] = True
        except Exception as e:  # noqa: BLE001
            log.warning("persist_bible: caption refresh failed: %s", e)
            result["captions_refreshed"] = False
            result["caption_error"] = str(e)[:200]
    return result
