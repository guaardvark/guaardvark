"""Character generator service — orchestrates the "Casting Director" agents into a
complete FLUX reference sheet (bible + N composed image prompts).

Built to the empirically-verified pattern (see character_designer.py docstring):
  1. ONE bible+trigger call (small schema → reliable).
  2. Shots generated in BATCHES with a per-batch top-up loop (the model is flaky on
     large strict JSON; small batches + retry keep coverage).
  3. Prompts COMPOSED in code as `trigger + verbatim bible + variation` — we never
     trust the model to echo the bible (it paraphrases/corrupts it per shot).

Coverage is taxonomy-driven: the service decides the angle distribution for N and
asks the ShotDesigner to fill specific slots, so we get good 3D coverage instead of
hoping the model distributes angles well.

Returns plain dicts (no DB) so it is unit-testable now; Phase 2 persists the result
into SubjectSample rows. GPU-free — pure LLM (Ollama).
"""
from __future__ import annotations
import hashlib
import logging
import math
import re

log = logging.getLogger(__name__)

from backend.services.swarm.agents.character_designer import (
    BibleDesigner,
    ShotDesigner,
    ShotVariation,
)

# Angle taxonomy (label, weight). Two "face-forward" buckets intentionally over-weight frontal
# coverage (one plain, one expressive) — frontal identity matters most to a LoRA. Full-body is
# now ~32% (front + three-quarter), up from 15%, so the eventual LoRA training set isn't ~72%
# face-framed (which would bake a close-up bias into the trained character). Weights sum to 1.0.
_ANGLE_WEIGHTS: list[tuple[str, float]] = [
    ("face-forward", 0.18),
    ("three-quarter left", 0.11),
    ("three-quarter right", 0.11),
    ("profile left", 0.07),
    ("profile right", 0.07),
    ("full-body front", 0.20),
    ("full-body three-quarter", 0.12),
    ("face-forward", 0.14),  # expressive frontal variety
]


def _angle_slots(n: int) -> list[str]:
    """Deterministically allocate exactly `n` angle slots by the taxonomy (largest-remainder)."""
    if n <= 0:
        return []
    exact = [w * n for _, w in _ANGLE_WEIGHTS]
    counts = [int(math.floor(x)) for x in exact]
    remainder = n - sum(counts)
    # hand the leftover slots to the largest fractional parts
    order = sorted(range(len(exact)), key=lambda i: exact[i] - counts[i], reverse=True)
    for k in range(remainder):
        counts[order[k % len(order)]] += 1
    slots: list[str] = []
    for (label, _), c in zip(_ANGLE_WEIGHTS, counts):
        slots.extend([label] * c)
    return slots[:n]


def _fallback_trigger(name: str) -> str:
    """A rare fabricated token if the model dropped trigger_word. Deterministic per name."""
    cons = "".join(c for c in name.lower() if c.isalpha() and c not in "aeiou")[:4] or "chr"
    suffix = hashlib.sha1(name.encode("utf-8")).hexdigest()[:3]
    return f"{cons}{suffix}"


def is_full_body(angle: str = "", framing: str = "") -> bool:
    """True when a shot calls for a head-to-toe figure — by its angle label ('full-body ...') or
    its framing ('full-body'). Shared by _compose_prompt (prompt ordering) and the task layer
    (portrait aspect) so the prompt's framing directive and the canvas never disagree."""
    return ("full-body" in (angle or "").lower()) or ((framing or "").strip().lower() == "full-body")


# Emphatic full-length directive. For full-body slots this LEADS the prompt (before the bible) so
# the framing isn't drowned by the ~195-token, face-saturated identity bible that follows.
_FULL_BODY_LEAD = (
    "full body shot, head to toe, full-length, entire figure visible, "
    "standing, feet visible, wide framing"
)

# Fine facial MINUTIAE (the "EXACT placement" marks the bible packs in per character_designer's
# _BIBLE_SYSTEM). Word-boundary matched so 'scar' doesn't catch 'scarlet'. These magnetise FLUX
# toward a close-up face and, at full-body size, won't even be visible — so we drop clauses that
# are PURELY these. Coarse identity (face shape, eye colour) is NOT in this set and stays.
_FACE_MICRO_RE = re.compile(
    r"\b(freckles?|moles?|scars?|dimples?|eyelashes?|philtrum|nostrils?|"
    r"wrinkles?|stubble|blemish(?:es)?|birthmarks?|beauty\s+marks?|crow'?s\s+feet)\b",
    re.IGNORECASE,
)
# A clause survives even if it names a minutia, when it ALSO carries coarse body/hair/wardrobe
# identity (so we never strip the build/outfit/hair the full-body shot most needs to stay on-model).
_KEEP_RE = re.compile(
    r"\b(build|frame|tall|short|height|shoulders?|torso|physique|athletic|slender|stocky|"
    r"lean|muscular|hair|skin|complexion|wears?|wearing|jacket|shirt|trousers|pants|dress|"
    r"coat|boots|shoes|outfit|wardrobe|aged?|years?)\b",
    re.IGNORECASE,
)


def _soften_face_detail(bible: str) -> str:
    """For full-body slots: drop bible clauses that are PURELY fine facial minutiae so they don't
    pull FLUX toward a close-up or eat the token budget the full-length directive needs. A clause
    is dropped only when it matches _FACE_MICRO_RE and NOT _KEEP_RE. Never returns empty (falls
    back to the full bible) so identity is never wiped out."""
    frags = [f.strip() for f in re.split(r"(?<=[.;,])\s+", bible) if f.strip()]
    kept = [f for f in frags if not (_FACE_MICRO_RE.search(f) and not _KEEP_RE.search(f))]
    return " ".join(kept).strip() or bible.strip()


def _compose_prompt(
    trigger: str,
    bible: str,
    v: ShotVariation,
    angle: str,
    *,
    include_bible: bool = True,
    class_token: str = "person",
    identity_marks: str = "",
) -> str:
    """Compose shot prompt: identity core + optional bible + variation phrases.

    When ``include_bible=False`` (trained LoRA path), use
    ``a photo of {trigger}, {class}[, short marks]`` + angle/scene — never dump
    the full bible paragraph (invented prose fights pixels), but always keep a
    human class anchor so Z-Image cannot invent cartoon animals.
    """
    bits = [
        angle,
        v.framing,
        f"{v.expression} expression" if v.expression else "",
        v.lighting,
        v.scene,
    ]
    variation = ", ".join(b for b in bits if b)
    if not include_bible or not (bible or "").strip():
        from backend.services.character_identity_prompt import compose_identity_core
        core = compose_identity_core(trigger, class_token, identity_marks)
        if is_full_body(angle, v.framing):
            core = f"{_FULL_BODY_LEAD}. {core}".strip()
        return f"{core}. {variation}." if variation else core
    if is_full_body(angle, v.framing):
        identity = _soften_face_detail(bible)
        core = f"{_FULL_BODY_LEAD}. {trigger} {identity}".strip()
    else:
        core = f"{trigger} {bible}".strip()
    return f"{core}. {variation}." if variation else core


def _default_llm(*, system: str, user: str, model: str = "gemma4:12b") -> str:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    # Prefer the OpenAI-compatible provider (the same cloud/remote model the
    # chat bot uses via GUAARDVARK_OPENAI_BASE_URL / GUAARDVARK_OPENAI_MODEL)
    # when it is configured. Fall back to the local Ollama model otherwise.
    try:
        from backend.services import openai_provider
        if openai_provider.available():
            from backend.config import OPENAI_DEFAULT_MODEL
            resp = openai_provider.chat(
                model=OPENAI_DEFAULT_MODEL,
                messages=messages,
                stream=False,
            )
            content = (resp.get("message", {}) or {}).get("content", "") or ""
            if content.strip():
                return content
    except Exception as e:
        log.warning(
            "OpenAI-compatible Character Generator LLM failed (%s); falling back to Ollama %s",
            e, model,
        )

    import ollama
    from backend.utils.ollama_resource_manager import think_payload
    resp = ollama.chat(
        model=model,
        messages=messages,
        format="json",  # hardens JSON parsing for the strict-schema agents
        **think_payload(model),
    )
    return resp["message"]["content"]


def _gen_batch(agent: ShotDesigner, bible: str, batch_angles: list[str], max_retries: int) -> list[ShotVariation]:
    """Generate variations for a batch of angle slots, topping up on shortfall."""
    needed = list(batch_angles)
    collected: list[ShotVariation] = []
    attempts = 0
    while needed and attempts <= max_retries:
        inv = agent.invoke({"bible": bible, "requested_angles": needed})
        produced = inv.output.shots if (inv.status == "ok" and inv.output) else []
        take = produced[: len(needed)]
        collected.extend(take)
        needed = needed[len(take):]
        attempts += 1
    # pad any still-missing slots with angle-only placeholders (user can regenerate in UI)
    while len(collected) < len(batch_angles):
        collected.append(ShotVariation())
    return collected[: len(batch_angles)]


def generate_character_sheet(
    name: str,
    kind: str = "character",
    description: str = "",
    n: int = 32,
    *,
    llm=None,
    trigger_word: str | None = None,
    batch_size: int = 8,
    bible_model: str = "gemma4:12b",
    shot_model: str = "gemma4:12b",
    max_batch_retries: int = 2,
    existing_bible: str | None = None,
    ref_image_paths: list | None = None,
    prefer_vision_bible: bool = False,
    include_bible_in_prompts: bool = True,
    invent_bible: bool = True,
    class_token: str | None = None,
    identity_marks: str | None = None,
) -> dict:
    """Produce {bible, trigger_word, shots:[{index, angle, ..., image_prompt}]}.

    `shots` always has exactly `n` entries (placeholders for any the model couldn't
    fill — these come back with an empty/near-empty prompt and are flagged for regen).

    When refs exist and ``prefer_vision_bible`` is set, reuse ``existing_bible`` or
    vision-ground from photos instead of BibleDesigner inventing a look.
    When ``include_bible_in_prompts`` is False (LoRA generate), prompts are
    identity core (photo of trigger + class + short marks) + variation.
    """
    llm = llm or _default_llm
    vision_grounded = False
    tags: list[str] = []
    bible = (existing_bible or "").strip()
    trigger = (trigger_word or "").strip() or _fallback_trigger(name)
    refs = list(ref_image_paths or [])
    marks = (identity_marks or "").strip()

    def _try_vision_bible() -> None:
        nonlocal bible, trigger, vision_grounded, tags
        try:
            from backend.services.character_bible_from_refs import rebuild_bible_from_refs
            grounded = rebuild_bible_from_refs(
                refs, name=name, trigger_word=trigger or None,
            )
            if grounded.get("ok") and grounded.get("bible"):
                bible = grounded["bible"].strip()
                trigger = (grounded.get("trigger_word") or trigger).strip()
                vision_grounded = True
                tags = list(grounded.get("tags") or [])
        except Exception as e:  # noqa: BLE001
            log.warning("generate_character_sheet: vision bible failed: %s", e)

    # Refs ⇒ never invent appearance via BibleDesigner (identity from photos).
    if refs:
        invent_bible = False

    # Prefer vision when refs exist and bible is empty / prefer_vision_bible.
    if not bible and refs and (prefer_vision_bible or not invent_bible):
        _try_vision_bible()

    if invent_bible and not bible:
        bagent = BibleDesigner(llm)
        bagent.model = bible_model
        binv = bagent.invoke({
            "name": name, "kind": kind, "description": description,
            "trigger_word": trigger_word,
        })
        if binv.status != "ok" or not binv.output or not binv.output.bible:
            binv = bagent.invoke({
                "name": name, "kind": kind, "description": description,
                "trigger_word": trigger_word,
            })
        if binv.status != "ok" or not binv.output or not binv.output.bible:
            return {
                "bible": "", "trigger_word": "", "n_requested": n, "shots": [],
                "error": f"bible generation failed ({binv.status})",
            }
        bible = binv.output.bible.strip()
        trigger = (trigger_word or binv.output.trigger_word or _fallback_trigger(name)).strip()
    elif not bible:
        return {
            "bible": "", "trigger_word": trigger, "n_requested": n, "shots": [],
            "error": (
                "no bible available (upload refs and Rebuild bible from photos, "
                "or provide a description)"
            ),
        }

    if trigger_word:
        trigger = trigger_word.strip()

    # ShotDesigner still needs a bible string for angle/scene variety context.
    shot_bible = bible if bible else f"character named {name}"

    from backend.services.character_identity_prompt import resolve_class_token
    cls = resolve_class_token(
        class_token=class_token,
        tags=tags,
        description=description,
        bible=bible,
    )
    if not marks and tags:
        marks = ", ".join(tags[:12])[:120]

    # 2. batched shots with taxonomy-driven coverage
    slots = _angle_slots(n)
    sagent = ShotDesigner(llm)
    sagent.model = shot_model
    shots: list[dict] = []
    for start in range(0, len(slots), batch_size):
        batch = slots[start: start + batch_size]
        variations = _gen_batch(sagent, shot_bible, batch, max_batch_retries)
        for angle, v in zip(batch, variations):
            idx = len(shots)
            prompt = _compose_prompt(
                trigger, bible, v, angle,
                include_bible=include_bible_in_prompts,
                class_token=cls,
                identity_marks=marks,
            )
            shots.append({
                "index": idx,
                "angle": angle,              # authoritative from the taxonomy, not the model
                "framing": v.framing,
                "expression": v.expression,
                "lighting": v.lighting,
                "scene": v.scene,
                "image_prompt": prompt,
                "placeholder": not (v.framing or v.expression or v.lighting or v.scene),
            })

    out = {
        "bible": bible,
        "trigger_word": trigger,
        "n_requested": n,
        "shots": shots,
        "vision_grounded": vision_grounded,
        "tags": tags,
        "include_bible_in_prompts": include_bible_in_prompts,
        "class_token": cls,
        "identity_marks": marks,
    }
    return out
