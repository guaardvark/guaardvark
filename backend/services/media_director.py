"""Media Director — generalized LLM director for image (and future cross-media) generation.

Reuses battle-tested primitives from music_video_director (and video_director) for:
- Dedicated small model (gemma4:e4b default) + embedding filter + resolve.
- Strict pure-visual JSON contracts, tolerant parsing, batching to avoid truncation.
- Never-raise + post-guards (distinctness, energy/mood cues, style hygiene).
- Treatment (narrative) vs. shots/prompts (visual only) separation.
- Planning modes + extra_guidance.
- Diagnostics surfaced.

For BatchImage: supports expand_image_plan (concept -> N coherent shots + optional treatment)
and enhance_prompts (per-prompt cinematic enrichment).

For chat: enhance single prompts before the generator.

Vision post-refine hook stub for future critique loops (using VisionAnalyzer / curator patterns).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, List, Optional, Dict

# Reuse ALL vetted primitives (model safety, parse, chat, guards) to avoid regressions.
from backend.services.music_video_director import (
    DIRECTOR_MODEL,
    _director_candidates,
    _resolve_model,
    _is_embedding_model,
    _parse_prompts as _base_parse_prompts,
    _director_options as _base_options,
    _is_mostly_style,
    _ensure_distinct_and_energy_aware,
    _director_chat as _base_director_chat,
    _installed_model_tags,
)

log = logging.getLogger(__name__)

# Image-specific dedicated model (same small fast one; user can override).
DEFAULT_DIRECTOR_MODEL = DIRECTOR_MODEL

# Most prompts one prose rewrite call carries; enhance_prompts splits larger
# prose batches into chunks of this size. Measured 2026-10-06 against a stub
# model with estimated token counts: a single prose call of 15 (pessimistic),
# 17 (central) or 20 (optimistic) prompts overflows the 4096-token window set in
# _options and the model returns nothing. Not yet measured on a live model.
DIRECTOR_BATCH_SIZE = 12

# Image "storyboard" contract: one concept -> N connected visual prompts.
_SYSTEM_STORYBOARD_IMAGE = """You are a visual director for still image generation.
Given ONE high-level concept and N, produce N distinct but visually coherent image prompts that feel like a series or storyboard.
Each prompt MUST:
- Be PURE VISUAL only (subject appearance, setting, framing/composition, camera/lens/angle if applicable, lighting, color palette, texture, mood, key style elements). No backstory, names, plot, "the image shows".
- Maintain world/character/motif consistency across the set (use recurring visual descriptors drawn from the concept).
- Vary framing, angle, action/moment, density, lighting to show progression or interesting variations.
- Be concise comma/phrase style suitable for SD/Flux/etc. (aim <30 words).
- Honor any global STYLE provided as a suffix (do NOT duplicate full style in prompt).
Return STRICT JSON: {"treatment": "<optional short evocative treatment>", "prompts": ["prompt1", "prompt2", ...]} with exactly N entries in order."""

_SYSTEM_ENHANCE_IMAGE = """You are a cinematic visual director for still images.
Rewrite each short user idea into a rich, shot-ready PURE VISUAL prompt (subject + setting + framing/composition + lighting + palette + mood + texture + style elements).
Keep the user's core intent. Enrich, never replace.
Be one flowing descriptive phrase. No narration, no "the image depicts", no meta.
STYLE (if given) is appended by caller; do not repeat it wholesale.
Return STRICT JSON: {"prompts": ["enriched1", "enriched2", ...]} exactly one per input, same order."""

# Rewrite contract for natural-language encoders (Z-Image). Ported from the
# prompt-enhancer template the model's authors ship with their demo: lock the
# fixed facts, reason out a concrete scene when the idea is a brief rather than
# a picture, add photographic detail, quote any in-image text, no metaphors, no
# quality tags. Measured on this box 2026-09-07 with gemma4:e4b: 1-8 s per
# prompt, 130-155 words, and the couch scene rendered clean on every seed.
_SYSTEM_ENHANCE_IMAGE_NATURAL = """You rewrite each short image idea into one detailed, concrete visual description for a text-to-image model.

Work in this order for every idea:
1. Lock the fixed facts: the subjects, how many of them, what they are doing, their pose or state, and any named brand, colour, or text. Keep every one of them exactly. Never add or remove people.
2. If the idea is a question or a design brief rather than a scene, first decide on one specific, drawable scene that answers it, then describe that scene.
3. Add photographic detail: framing and camera angle, where each subject is and how their bodies are arranged, lighting and time of day, materials and textures, colour palette, and depth (foreground, midground, background).
4. Any text that must appear in the image is written verbatim inside double quotes, with its position, size, and font style. If no text is needed, spend the space on visual detail instead.

Rules: objective and physical, no metaphors, no emotional language, no quality tags such as "8K", "masterpiece" or "high quality", and no instructions to the model. Each description is one paragraph of plain prose, 80 to 200 words.
STYLE (if given) describes the medium or look to honour; weave it in as a short clause, do not repeat it wholesale.
Return STRICT JSON: {"prompts": ["description1", "description2", ...]} exactly one per input, same order."""

# Per-attempt time limits for the director's Ollama calls. The module-level
# ``ollama.chat`` client has no timeout, so without one a hung model would hold
# the stage, and the GPU work queued behind it, indefinitely. A non-streaming chat sends nothing
# until the reply is complete, so the read limit bounds the whole generation:
# an allowance for loading the model plus one per prompt. The per-prompt figure
# is about four times the 1-8 s per prompt measured for the prose contract
# above, to leave room for slower GPUs; the load allowance is a ceiling for a
# cold load from disk, not a measurement. These bound a stall; they are not
# expected waits.
DIRECTOR_CONNECT_TIMEOUT_S = 10.0
DIRECTOR_LOAD_ALLOWANCE_S = 120.0
DIRECTOR_PER_PROMPT_S = 30.0
DIRECTOR_MAX_TIMEOUT_S = 900.0


def _director_timeout_s(n: int) -> float:
    """Seconds one director attempt may take for ``n`` prompts."""
    return min(DIRECTOR_MAX_TIMEOUT_S,
               DIRECTOR_LOAD_ALLOWANCE_S + DIRECTOR_PER_PROMPT_S * max(1, int(n)))


def _director_chat(timeout_s: float, **kwargs) -> Any:
    """One ``ollama`` chat call that gives up after ``timeout_s`` seconds.

    Uses its own client (same host resolution as ``ollama.chat``) so the limit
    applies to this call only; raises the httpx timeout error when it fires.
    """
    import httpx
    import ollama
    timeout = httpx.Timeout(timeout_s, connect=min(timeout_s, DIRECTOR_CONNECT_TIMEOUT_S))
    with ollama.Client(timeout=timeout) as client:
        return client.chat(**kwargs)


def _is_timeout(exc: BaseException) -> bool:
    try:
        import httpx
        return isinstance(exc, httpx.TimeoutException)
    except Exception:  # noqa: BLE001
        return False


def _options(n: int, sampling: Optional[dict] = None) -> dict:
    n = max(1, n)
    opts = {"temperature": 0.68, "num_ctx": 4096, "num_predict": min(3072, 180 * n + 512)}
    if sampling:
        # Sampling-profile knobs override temperature etc.; window/budget stay authoritative
        # (num_predict sized to N is the JSON-truncation fix).
        opts.update({k: v for k, v in sampling.items() if k not in ("num_ctx", "num_predict")})
    return opts

def _parse_image_prompts(content: str, n: int) -> List[str]:
    """Tolerant list parse for image plans/enhance. Accepts {"prompts": [...]}, {"shots": [...]}, bare array."""
    if not content:
        return []
    text = content.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    data: Any = None
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
            except Exception:
                pass
    if data is None:
        return []
    if isinstance(data, list):
        out = [str(x).strip() for x in data if str(x).strip()]
        return out[:n] if n else out
    if isinstance(data, dict):
        for key in ("prompts", "shots", "images", "items"):
            arr = data.get(key)
            if isinstance(arr, list):
                out = [str(x).strip() for x in arr if str(x).strip()]
                return out[:n] if n else out
        # Fallback single "prompt"
        if isinstance(data.get("prompt"), str):
            return [data["prompt"].strip()]
    return []

# Media a prompt can name for itself. Chat and the Studio send style "realistic"
# when nobody picked one; a prompt that asks for a poster, a painting or anime is
# rewritten as that medium, not as a photograph ("a vintage travel poster" came
# back as "A realistic travel poster" and rendered as a photo).
_NAMED_MEDIUM = re.compile(
    r"\b(poster|illustration|illustrated|painting|painted|watercolou?r|gouache|acrylic|"
    r"sketch|drawing|drawn|pencil|charcoal|ink|anime|manga|cartoon|comic|pixel art|vector|"
    r"flat design|3d render|low[- ]poly|infographic|logo|icon|sticker|storybook|claymation|"
    r"stained glass|mosaic|woodcut|linocut|ukiyo-e|cel[- ]shaded|art deco|risograph|screen ?print)s?\b",
    re.IGNORECASE,
)


def _style_for(prompts: List[str], style: str) -> str:
    """The style the rewrite honours: none when it is the unchosen "realistic"
    and every prompt names its own medium."""
    if (style or "").strip().lower() == "realistic" and prompts and all(
        _NAMED_MEDIUM.search(p or "") for p in prompts
    ):
        return ""
    return style


def _style_clause(style: Optional[str]) -> str:
    style = (style or "").strip()
    return f"\nGlobal visual style/aesthetic to honor: {style}." if style else ""


def verbatim_prompts_env_forced() -> bool:
    """True when VERBATIM_PROMPTS in the environment forces verbatim mode on.

    The Settings toggle cannot turn this off; the settings API reports it so
    the page can show the switch as forced instead of showing a stored value
    the generators ignore.
    """
    import os
    return os.environ.get("VERBATIM_PROMPTS", "").strip().lower() in ("1", "true", "yes", "on")


def verbatim_prompts_enabled() -> bool:
    """True when the operator turned ON 'verbatim prompts' — send the user's EXACT words
    to the image/video model and SKIP director-LLM rewrite, offline style stuffing, and
    word-budget truncation. This is the 'no soft limits' switch (your machine, your rules).

    OFF by default (keeps cinematic enrichment). Sources, in order:
      * env VERBATIM_PROMPTS=1/true/yes/on — restart-proof, wins (good for a live demo), then
      * the runtime SystemSetting 'verbatim_prompts' (toggle in Settings, no restart).
    Any error → False (enrichment stays on).

    NOTE: this runs during image/video generation, which the unified chat engine executes
    in ThreadPoolExecutor worker threads that do NOT inherit the request's Flask app
    context. A DB read without an app context raises "working outside of application
    context"; if we let that fall through to the bare except we'd silently return False
    and IGNORE the Settings toggle (the bug: verbatim ON, prompt still rewritten). So when
    no context is active we push the app singleton's context for the read — mirroring the
    pattern unified_chat_engine._save_message uses for the same reason.

    Call sites that MUST honor this (not just media_director.enhance_prompts):
      * offline_image_generator.generate_image (enhance + 75-word clip)
      * ImageGeneratorTool / batch image (auto_enhance)
      * comfyui_video_generator prompt enhance
    """
    if verbatim_prompts_env_forced():
        return True
    try:
        from flask import has_app_context
        from backend.models import SystemSetting, db

        def _read() -> bool:
            row = db.session.query(SystemSetting).filter_by(key="verbatim_prompts").first()
            return bool(row and str(row.value).lower() == "true")

        if has_app_context():
            return _read()

        # No active app context (chat tool worker thread / Celery task) — push the
        # app singleton's context so the DB-backed toggle is actually honored instead
        # of silently defaulting to OFF.
        try:
            from backend.app import app as _flask_app
        except Exception:
            _flask_app = None
        if _flask_app is not None:
            with _flask_app.app_context():
                return _read()
        return False
    except Exception:
        return False


# Back-compat private alias (older call sites / tests).
_verbatim_prompts_enabled = verbatim_prompts_enabled


def enhance_prompts(
    prompts: List[str],
    *,
    style: str = "",
    extra_guidance: Optional[str] = None,
    model: Optional[str] = None,
    cast_descriptors: Optional[List[str]] = None,
    sampling: Optional[dict] = None,
    prompt_style: Optional[str] = None,
) -> List[str]:
    """Enrich a list of user prompts into rich pure-visual shot-ready prompts via director LLM.
    Best-effort: on any failure returns originals (or lightly cued).
    Used by chat ImageGeneratorTool and batch pre-pass.

    ``prompt_style="natural"`` selects the prose contract for LLM-encoder models
    (Z-Image); anything else keeps the comma-phrase contract for CLIP-era models.
    A prose batch larger than ``DIRECTOR_BATCH_SIZE`` is rewritten in chunks of
    that size; a chunk the director cannot rewrite keeps its originals, and a
    timeout keeps the originals for that chunk and every one after it.
    """
    if not prompts:
        return []
    if verbatim_prompts_enabled():
        log.info("media_director: verbatim prompts ON — sending user prompts to the model as-is (no director rewrite)")
        return list(prompts)
    n = len(prompts)
    style = _style_for(list(prompts), style)
    prose = (prompt_style or "").lower() == "natural"
    chunk_size = DIRECTOR_BATCH_SIZE if prose else n
    if chunk_size < n:
        log.info(
            "media_director.enhance_prompts: prose batch of %d sent in chunks of %d",
            n, chunk_size,
        )
    out: List[str] = []
    for start in range(0, n, chunk_size):
        chunk = list(prompts[start:start + chunk_size])
        rewritten, timed_out = _enhance_chunk(
            chunk, prose=prose, style=style, extra_guidance=extra_guidance,
            model=model, cast_descriptors=cast_descriptors, sampling=sampling,
        )
        if timed_out:
            out.extend(prompts[start:])
            break
        out.extend(rewritten)
    return out


def _enhance_chunk(
    prompts: List[str],
    *,
    prose: bool,
    style: str,
    extra_guidance: Optional[str],
    model: Optional[str],
    cast_descriptors: Optional[List[str]],
    sampling: Optional[dict],
) -> tuple[List[str], bool]:
    """One director rewrite of ``prompts`` down the model ladder.

    Returns ``(prompts_out, timed_out)``: the rewrites when a model answered with
    exactly one per input, otherwise the originals.
    """
    n = len(prompts)
    style_c = _style_clause(style)
    guidance = f"\nExtra direction: {extra_guidance.strip()}." if extra_guidance and extra_guidance.strip() else ""
    cast = ""
    if cast_descriptors:
        cast = "\nConsistent visual character/subject descriptors to weave in naturally (visual only): " + "; ".join(cast_descriptors[:3])

    user = (
        f"STYLE: {style or '(none)'}\n"
        f"INPUT IDEAS ({n}):\n" + "\n".join(f"{i+1}. {p}" for i,p in enumerate(prompts)) +
        f"{style_c}{guidance}{cast}\n\n"
        "TASK: Return ONLY JSON with 'prompts' array of exactly N enriched pure-visual prompts. Preserve order and core intent."
    )
    # NOTE: do NOT use the music-director's _director_chat here — its parser hunts for a
    # "shots" array, but this enrich contract returns {"prompts": [...]}. Mismatched parsing
    # silently returned [] → originals (the batch-director no-op bug, fixed 2026-06-23).
    # Mirror storyboard_from_concept: own chat call + _parse_image_prompts (list-aware).
    opts = _options(n, sampling)
    if prose:
        system = _SYSTEM_ENHANCE_IMAGE_NATURAL
        # Prose descriptions run ~150 words each; the phrase budget clips them.
        opts["num_predict"] = max(int(opts.get("num_predict", 0)), min(4096, 320 * n + 256))
    else:
        system = _SYSTEM_ENHANCE_IMAGE
    # The active chat model goes first; a model that errors or hands back the wrong
    # number of prompts is skipped for the next family on the ladder (gemma, qwen, ...).
    # A timeout ends the ladder: Ollama queues requests behind the stalled one, so
    # the next model would only wait out the same stall.
    timeout_s = _director_timeout_s(n)
    for resolved in _director_candidates(model or DEFAULT_DIRECTOR_MODEL)[:3]:
        try:
            from backend.utils.ollama_resource_manager import think_payload
            resp = _director_chat(
                timeout_s,
                model=resolved,
                format="json",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                options=opts,
                **think_payload(resolved),
            )
            out = _parse_image_prompts(resp["message"]["content"], n)
            if len(out) == n:
                log.info("media_director.enhance_prompts: %d prompt(s) rewritten by %s", n, resolved)
                return [p.strip() for p in out], False
            log.warning(
                "media_director.enhance_prompts: %s parsed %d/%d prompts; trying the next model",
                resolved, len(out), n,
            )
        except Exception as e:  # noqa: BLE001
            if _is_timeout(e):
                log.warning(
                    "media_director.enhance_prompts: %s timed out after %.0fs; keeping the original prompts",
                    resolved, timeout_s,
                )
                return list(prompts), True
            log.warning("media_director.enhance_prompts: %s failed (%s); trying the next model", resolved, e)
    # Fallback: return originals (caller may still do keyword enhance)
    return list(prompts), False


_SYSTEM_REFINE_EDIT = """You are an expert prompt writer for an instruction-based image EDITOR (FLUX Kontext).
Rewrite the user's terse edit request into ONE precise, imperative edit directive.
Rules:
- Name the TARGET (what changes), the CHANGE (exactly what it becomes — material, color, placement, style), and what to PRESERVE (keep the same face, identity, pose, framing, lighting, background, and everything not mentioned).
- Do NOT add new subjects, do NOT describe the whole scene, do NOT turn it into a from-scratch image prompt.
- Keep it to one or two sentences.
Return ONLY JSON: {"instruction": "<the rewritten edit directive>"}."""


def refine_edit_instruction(instruction: str, *, model: Optional[str] = None,
                            sampling: Optional[dict] = None) -> str:
    """Rewrite a terse image-EDIT instruction into a precise Kontext edit directive via the
    director LLM (gemma4). Best-effort: returns the ORIGINAL on any failure.

    Distinct from enhance_prompts (which writes a txt2img *visual* prompt and would inject
    new subjects) — this MUST stay an edit directive: target + change + what-to-preserve."""
    instr = (instruction or "").strip()
    if not instr:
        return instruction
    if verbatim_prompts_enabled():
        log.info("media_director: verbatim prompts ON — using edit instruction as-is (no Kontext rewrite)")
        return instruction
    resolved = _resolve_model(model or DEFAULT_DIRECTOR_MODEL)
    timeout_s = _director_timeout_s(1)
    try:
        from backend.utils.ollama_resource_manager import think_payload
        import json as _json
        resp = _director_chat(
            timeout_s,
            model=resolved,
            format="json",
            messages=[
                {"role": "system", "content": _SYSTEM_REFINE_EDIT},
                {"role": "user", "content": f"User edit request: {instr}"},
            ],
            options=_options(1, sampling),
            **think_payload(resolved),
        )
        data = _json.loads(resp["message"]["content"])
        refined = (data.get("instruction") or "").strip()
        if refined:
            return refined
        log.warning("media_director.refine_edit_instruction returned empty; using original")
    except Exception as e:  # noqa: BLE001
        if _is_timeout(e):
            log.warning(
                "media_director.refine_edit_instruction: %s timed out after %.0fs; using original",
                resolved, timeout_s,
            )
        else:
            log.warning("media_director.refine_edit_instruction failed (%s); using original", e)
    return instruction


def storyboard_from_concept(
    concept: str,
    n: int,
    *,
    style: str = "",
    extra_guidance: Optional[str] = None,
    model: Optional[str] = None,
    sampling: Optional[dict] = None,
) -> Dict[str, Any]:
    """Expand ONE concept into N coherent visual prompts (+ optional treatment).
    Returns {"treatment": str|None, "prompts": [str, ...]} .
    Never raises; on failure returns {"treatment": None, "prompts": [concept] * n} (or energy cued).
    """
    if n < 1:
        n = 1
    resolved = _resolve_model(model or DEFAULT_DIRECTOR_MODEL)
    style_c = _style_clause(style)
    guidance = f"\nExtra direction: {extra_guidance.strip()}." if extra_guidance and extra_guidance.strip() else ""

    user = (
        f"CONCEPT: {concept}\nN={n}\nSTYLE: {style or '(none)'}{style_c}{guidance}\n\n"
        "TASK: Return ONLY the JSON with optional 'treatment' and exactly N 'prompts'."
    )
    timeout_s = _director_timeout_s(n)
    try:
        # Use a direct chat wrapper for storyboard (rich)
        from backend.utils.ollama_resource_manager import think_payload
        opts = _options(n, sampling)
        resp = _director_chat(
            timeout_s,
            model=resolved,
            format="json",
            messages=[
                {"role": "system", "content": _SYSTEM_STORYBOARD_IMAGE},
                {"role": "user", "content": user},
            ],
            options=opts,
            **think_payload(resolved),
        )
        content = resp["message"]["content"]
        data = _parse_storyboard_output(content, n)
        prompts = data.get("prompts") or []
        if len(prompts) != n:
            prompts = _ensure_image_distinct(prompts or [], concept, n, style)
        return {"treatment": data.get("treatment"), "prompts": prompts[:n]}
    except Exception as e:  # noqa: BLE001
        if _is_timeout(e):
            log.warning(
                "media_director.storyboard_from_concept: %s timed out after %.0fs; simple fallback",
                resolved, timeout_s,
            )
        else:
            log.warning("media_director.storyboard_from_concept failed (%s); simple fallback", e)
        base = concept.strip()
        return {"treatment": None, "prompts": [base] * n}

def _parse_storyboard_output(content: str, n: int) -> Dict[str, Any]:
    try:
        data = json.loads(content)
    except Exception:
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(content[start:end+1])
            except Exception:
                data = {}
        else:
            data = {}
    if not isinstance(data, dict):
        data = {}
    prompts = data.get("prompts") or data.get("shots") or []
    if isinstance(prompts, list):
        prompts = [str(p).strip() for p in prompts if str(p).strip()][:n]
    else:
        prompts = []
    treatment = data.get("treatment") if isinstance(data.get("treatment"), str) else None
    return {"treatment": treatment, "prompts": prompts}

def _ensure_image_distinct(prompts: List[str], concept: str, n: int, style: str) -> List[str]:
    """Light deterministic guard (mirrors MV energy cues for images)."""
    out = []
    cues = [
        "wide establishing framing, atmospheric depth",
        "medium shot, focused action",
        "tight dramatic close, strong contrast",
        "dynamic angle from low, motion implication",
        "overhead or high angle, sparse or dense texture",
    ]
    for i in range(n):
        base = (prompts[i] if i < len(prompts) else concept).strip()
        cue = cues[i % len(cues)]
        if style and style.lower() not in base.lower():
            candidate = f"{base}, {cue}, {style}".strip(", ")
        else:
            candidate = f"{base}, {cue}".strip(", ")
        out.append(candidate)
    return out

def expand_image_plan(
    idea: str,
    n: int,
    *,
    look_and_feel: str = "",
    user_treatment: Optional[str] = None,
    planning_mode: str = "narrative",
    extra_guidance: Optional[str] = None,
    model: Optional[str] = None,
    cast_descriptors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """High-level BatchImage-friendly entry: concept/idea + N -> {treatment, shots}.
    shots = list of dicts with 'prompt' (and index).
    Mirrors MV plan shape for UI reuse.
    """
    res = storyboard_from_concept(
        idea, n, style=look_and_feel, extra_guidance=(extra_guidance or user_treatment), model=model
    )
    prompts = res.get("prompts") or []
    # light enhance pass for consistency if cast
    if cast_descriptors and prompts:
        try:
            enriched = enhance_prompts(prompts, style=look_and_feel, cast_descriptors=cast_descriptors, model=model)
            if len(enriched) == len(prompts):
                prompts = enriched
        except Exception:
            pass
    shots = [{"index": i, "prompt": p} for i, p in enumerate(prompts[:n])]
    return {
        "treatment": res.get("treatment") or (user_treatment or ""),
        "shots": shots,
        "planning_mode": planning_mode,
        "director_model": model or DEFAULT_DIRECTOR_MODEL,
        "director_diagnostics": None,  # future: surface fallback info
    }

# Public aliases for batch compatibility
direct_prompts = enhance_prompts
