"""Compile Guaardvark's structured intent into a MiniMax H3 prompt.

H3 is prompted with named sections, numbered shots with cut times that add
up to the clip's length, stable speaker ids, tagged dialogue and, in the
reference build, tags that name each reference in the order it was wired
into the graph. A free-text prompt with "cinematic, smooth motion" appended
is the wrong shape for it. This module owns that shape so every product path
(Video Generator, Film Crew, music video, the chat tool) writes the same
prompt.

Deterministic first: templating guarantees the parts the model is sensitive
to. An optional polish pass asks the local director model to enrich the
descriptions and returns the same structure, which is rendered and validated
again; on any failure the deterministic prompt stands.

The structure follows MiniMax's published prompt-writing guide (see
backend/prompt_bundles/minimax_h3/NOTICE.md for the pointer). Base modes:
an alignment instruction for keyframe modes, then
``integrated_multimodal_description``, ``overall_soundscape``,
``non_diegetic_music``. Reference mode: ``subject_definitions``, ``summary``,
``retention_analysis``, ``detailed_description``, ``overall_soundscape``,
``non_diegetic_music``.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

BUNDLE_DIR = Path(__file__).resolve().parent.parent / "prompt_bundles" / "minimax_h3"
NATIVE_FPS = 24
MIN_SECONDS = 3.0
MAX_SECONDS = 15.0
MODES = ("t2va", "i2va", "fl2va", "l2va", "ref2va")
VISUAL_RETENTION = ("fully_preserved", "partially_preserved", "attribute_transfer", "weak_reference")
AUDIO_RETENTION = ("fully_copy", "partially_copy", "reference", "weak_reference")
TASK_TYPES = (
    "keyframe completion", "reference generation", "video editing",
    "video continuation", "audio reuse", "audio reference",
)
# Words the guide calls abstract: they name a feeling, not something visible
# or audible. Reported as warnings, never rewritten.
ABSTRACT_WORDS = ("beautiful", "epic", "stunning", "amazing", "breathtaking", "gorgeous", "awesome")
# Guaardvark's prompt styles as the style opening the guide asks for.
STYLE_OPENINGS = {
    "cinematic": "Live-action, cinematic",
    "realistic": "Live-action, photorealistic",
    "artistic": "Stylized, painterly",
    "anime": "2D-animated, anime",
    "3d_animation": "3D CG animation",
    "stop_motion": "Claymation, stop-motion",
    "hand_drawn": "Hand-drawn 2D animation",
    "western_cartoon": "2D-animated cartoon",
}
_SECTION_MARKERS = ("integrated_multimodal_description:", "detailed_description:", "subject_definitions:")


def load_languages() -> list[str]:
    try:
        return list(json.loads((BUNDLE_DIR / "languages.json").read_text(encoding="utf-8")))
    except Exception:
        return ["English"]


def normalize_language(lang: Optional[str]) -> str:
    """The model card's language name for a code or name, defaulting to English."""
    wanted = (lang or "English").strip()
    aliases = {"en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "fr": "French",
               "de": "German", "it": "Italian", "pt": "Portuguese", "ru": "Russian", "es": "Spanish",
               "ar": "Arabic"}
    wanted = aliases.get(wanted.lower(), wanted)
    for name in load_languages():
        if name.lower() == wanted.lower():
            return name
    return "English"


# ─── intent ─────────────────────────────────────────────────────────────────

@dataclass
class H3Dialogue:
    speaker: str                 # stable name; the compiler assigns (S1), (S2) by first vocal event
    text: str
    lang: str = "English"
    intro: str = ""              # how the speaker is introduced the first time ("a calm, raspy voice")
    voiceover: bool = False


@dataclass
class H3Shot:
    description: str             # what is visible; composition, subjects, action
    duration_s: float = 0.0      # 0 = share the remaining time evenly
    camera: Optional[str] = None # a natural sentence, e.g. "The camera pushes in with small amplitude at slow speed."
    dialogue: list = field(default_factory=list)   # H3Dialogue
    transition: str = "the camera cuts to"


@dataclass
class H3Subject:
    id: int
    description: str             # what the label denotes and the features to follow
    pictures: list = field(default_factory=list)   # 1-based <Picture N> indices that define it
    videos: list = field(default_factory=list)     # 1-based <Video N> indices
    retention: str = "fully_preserved"
    note: str = ""               # retention detail after the marker


@dataclass
class H3AudioRef:
    index: int                   # 1-based <Audio N>, wiring order
    role: str = "reference"      # AUDIO_RETENTION value
    description: str = ""        # what the audio is
    speaker: Optional[str] = None  # dialogue speaker whose voice it references


@dataclass
class H3Intent:
    duration_s: float
    mode: str = "t2va"
    style: str = ""              # style opening; "" lets the shot description lead
    shots: list = field(default_factory=list)      # H3Shot
    subjects: list = field(default_factory=list)   # H3Subject (reference mode)
    audio_refs: list = field(default_factory=list) # H3AudioRef (reference mode)
    picture_frames: list = field(default_factory=list)  # <Picture N> used as concrete frames, reference mode
    task_types: list = field(default_factory=list)
    soundscape: str = ""
    music: str = "N/A"
    summary: str = ""
    language: str = "English"


# ─── timing ─────────────────────────────────────────────────────────────────

def snap_duration(seconds: float, fps: int = NATIVE_FPS) -> tuple[int, float]:
    """(frames, seconds) on the model's 17k+5 grid, snapped up like the
    generator does, so the timeline the prompt describes is the one rendered."""
    seconds = max(MIN_SECONDS, min(MAX_SECONDS, float(seconds or 0) or 5.0))
    n = max(5, int(round(seconds * fps)))
    frames = n + (5 - n % 17) % 17
    return frames, round(frames / fps, 2)


def _timestamp(seconds: float) -> str:
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m:02d}:{s:06.3f}"


def _distribute(shots: list, total: float) -> list[float]:
    """Shot lengths that sum exactly to ``total``: declared lengths scaled
    proportionally, undeclared ones sharing what is left evenly."""
    if not shots:
        return []
    declared = [float(s.duration_s or 0) for s in shots]
    fixed = sum(d for d in declared if d > 0)
    free = [i for i, d in enumerate(declared) if d <= 0]
    out = list(declared)
    if free:
        remaining = max(0.0, total - fixed)
        if remaining <= 0:
            # every second is spoken for; give the free shots a share anyway
            scale = total / (fixed + len(free))
            out = [d * scale if d > 0 else scale for d in declared]
        else:
            share = remaining / len(free)
            for i in free:
                out[i] = share
    else:
        scale = total / fixed if fixed else 1.0
        out = [d * scale for d in declared]
    out = [round(v, 2) for v in out]
    out[-1] = round(total - sum(out[:-1]), 2)
    return out


# ─── rendering ──────────────────────────────────────────────────────────────

class _Speakers:
    def __init__(self):
        self.ids: dict[str, str] = {}

    def tag(self, name: str) -> tuple[str, bool]:
        key = (name or "voice").strip()
        first = key not in self.ids
        if first:
            self.ids[key] = f"(S{len(self.ids) + 1})"
        return self.ids[key], first


def _render_dialogue(d: H3Dialogue, speakers: _Speakers, subject_tag: str = "") -> str:
    sid, first = speakers.tag(d.speaker)
    who = f"{subject_tag} " if subject_tag else ""
    if first and d.intro:
        who += f"{d.speaker}, {d.intro.strip().rstrip('.')}, {sid}"
    else:
        who += f"{d.speaker} {sid}" if first else sid
    text = d.text.strip()
    if text and text[-1] not in ".!?":
        text += "."
    line = f"<d>[{normalize_language(d.lang)}] {text}</d>"
    if d.voiceover:
        return f"{who} says in an off-screen voiceover: {line} while their lips remain completely closed."
    return f"{who} says: {line}"


def _ensure_period(text: str) -> str:
    text = (text or "").strip()
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _render_shots(intent: H3Intent, seconds: float, subject_tags: dict | None = None) -> tuple[str, list[str]]:
    """The numbered shot body and any warnings."""
    warnings: list[str] = []
    lengths = _distribute(intent.shots, seconds)
    speakers = _Speakers()
    parts = []
    t = 0.0
    for i, shot in enumerate(intent.shots):
        desc = _ensure_period(shot.description)
        for tag in re.findall(r"<Subject \d+>", desc):
            if subject_tags is not None and tag not in subject_tags:
                warnings.append(f"shot {i + 1} names {tag}, which is not defined")
        pieces = []
        if i == 0:
            opening = _ensure_period(intent.style) if intent.style else ""
            head = f"[Shot 1] {opening + ' ' if opening and intent.mode != 'ref2va' else ''}{desc}"
        else:
            head = f"[Shot {i + 1}] At {_timestamp(t)}, {shot.transition} {desc[0].lower() + desc[1:] if desc else ''}"
        pieces.append(head.strip())
        if shot.camera:
            pieces.append(_ensure_period(shot.camera))
        for d in shot.dialogue or []:
            tag = ""
            if subject_tags:
                for stag, name in subject_tags.items():
                    if name and name.lower() == (d.speaker or "").lower():
                        tag = stag
            pieces.append(_render_dialogue(d, speakers, tag))
        parts.append(" ".join(pieces))
        t += lengths[i]
    for word in ABSTRACT_WORDS:
        if re.search(rf"\b{word}\b", " ".join(parts), flags=re.IGNORECASE):
            warnings.append(f"abstract word '{word}' names a feeling, not something visible")
    return " ".join(parts), warnings


def _alignment_instruction(intent: H3Intent, seconds: float) -> str:
    n = max(1, len(intent.shots))
    end = f"{seconds:.2f}"
    if intent.mode == "i2va":
        return "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
    if intent.mode == "fl2va":
        return (
            "How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns "
            f"with the 0.00-second mark of the target video; Picture 2 (from Shot {n}) aligns with the "
            f"{end}-second mark of the target video."
        )
    if intent.mode == "l2va":
        return (
            f"How the reference pictures align with the target video — <Picture 1> (from [Shot {n}]) "
            f"aligns with the {end}-second mark of the target video."
        )
    return ""


def _soundscape(intent: H3Intent) -> str:
    text = (intent.soundscape or "").strip()
    return _ensure_period(text) if text else (
        "Natural ambience of the scene with the physical sounds of the described actions."
    )


def _music(intent: H3Intent) -> str:
    text = (intent.music or "").strip()
    return _ensure_period(text) if text and text.upper() != "N/A" else "N/A"


def compile_base(intent: H3Intent) -> tuple[str, dict]:
    frames, seconds = snap_duration(intent.duration_s)
    body, warnings = _render_shots(intent, seconds)
    sections = [f"integrated_multimodal_description: {body}", f"overall_soundscape: {_soundscape(intent)}",
                f"non_diegetic_music: {_music(intent)}"]
    instruction = _alignment_instruction(intent, seconds)
    prompt = (instruction + "\n\n" if instruction else "") + "\n\n".join(sections)
    return prompt, {"mode": intent.mode, "frames": frames, "seconds": seconds, "warnings": warnings,
                    "shots": len(intent.shots)}


def compile_reference(intent: H3Intent) -> tuple[str, dict]:
    frames, seconds = snap_duration(intent.duration_s)
    warnings: list[str] = []
    subject_tags = {f"<Subject {s.id}>": s.description.split(",")[0].strip() for s in intent.subjects}
    defs = []
    for s in intent.subjects:
        refs = [f"<Picture {p}>" for p in s.pictures] + [f"<Video {v}>" for v in s.videos]
        source = f" in {', '.join(refs)}" if refs else ""
        defs.append(f"<Subject {s.id}> is {s.description.strip().rstrip('.')}{source}.")
        if s.retention not in VISUAL_RETENTION:
            warnings.append(f"subject {s.id} retention '{s.retention}' is not one of {', '.join(VISUAL_RETENTION)}")
    for p in intent.picture_frames:
        defs.append(f"<Picture {p}> is the first frame of [Shot 1]." if p == intent.picture_frames[0]
                    else f"<Picture {p}> is a frame anchor for the final shot.")
    for a in intent.audio_refs:
        if a.role not in AUDIO_RETENTION:
            warnings.append(f"audio {a.index} role '{a.role}' is not one of {', '.join(AUDIO_RETENTION)}")
        if a.speaker:
            defs.append(f"<Audio {a.index}> is the voice-timbre reference for {a.speaker}.")
        else:
            defs.append(f"<Audio {a.index}> is {(a.description or 'a reference audio track').strip().rstrip('.')}.")
    task_types = [t for t in intent.task_types if t in TASK_TYPES] or ["reference generation"]
    summary_text = (intent.summary or "").strip() or (
        f"The target video shows {', '.join(subject_tags) or 'the described scene'} across "
        f"{max(1, len(intent.shots))} shot(s) over {seconds:.2f} seconds."
    )
    summary = f"[{' + '.join(dict.fromkeys(task_types))}] {_ensure_period(summary_text)}"
    retention = []
    for s in intent.subjects:
        note = s.note.strip() or "its defining features are retained"
        retention.append(f"<Subject {s.id}> (appears in [Shot 1]): {s.retention} - {note}.")
    for a in intent.audio_refs:
        note = a.description.strip() or ("its voice timbre guides the delivery" if a.speaker else "the track is used as declared")
        retention.append(f"<Audio {a.index}>: {a.role} - {note.rstrip('.')}.")
    body, shot_warnings = _render_shots(intent, seconds, subject_tags)
    warnings.extend(shot_warnings)
    opening = _ensure_period(intent.style) if intent.style else "The target video keeps the look of its references."
    sections = [
        "subject_definitions:\n" + "\n".join(defs),
        "summary:\n" + summary,
        "retention_analysis:\n" + "\n".join(retention),
        "detailed_description:\n" + opening + "\n" + body,
        "overall_soundscape:\n" + _soundscape(intent),
        "non_diegetic_music:\n" + _music(intent),
    ]
    return "\n\n".join(sections), {"mode": "ref2va", "frames": frames, "seconds": seconds, "warnings": warnings,
                                   "shots": len(intent.shots)}


def compile(intent: H3Intent, *, polish: bool = False, model: Optional[str] = None) -> tuple[str, dict]:
    """(prompt, diagnostics). ``polish`` asks the local director model to
    enrich the intent first; the result is re-rendered by the same templating
    and falls back to the unpolished intent on any failure."""
    if intent.mode not in MODES:
        intent.mode = "t2va"
    if not intent.shots:
        intent.shots = [H3Shot(description="the described scene")]
    if polish:
        try:
            intent = polish_intent(intent, model=model)
        except Exception as e:  # noqa: BLE001 — the deterministic prompt stands
            logger.info("H3 prompt polish skipped: %s", e)
    if intent.mode == "ref2va":
        return compile_reference(intent)
    return compile_base(intent)


def looks_compiled(prompt: str) -> bool:
    return any(marker in (prompt or "") for marker in _SECTION_MARKERS)


# ─── intent builders ────────────────────────────────────────────────────────

def _camera_from_motion(motion_strength: Optional[float]) -> Optional[str]:
    if motion_strength is None:
        return None
    if motion_strength <= 0.75:
        return "The camera holds a static shot with only small amplitude movement at slow speed."
    if motion_strength >= 1.5:
        return "The camera moves with large amplitude at fast speed, following the action."
    return None


def intent_from_plain_prompt(
    prompt: str, duration_s: float, *, mode: str = "t2va", style: Optional[str] = None,
    language: str = "English", motion_strength: Optional[float] = None,
) -> H3Intent:
    """One shot from a free-text prompt; ``mode`` follows the frames supplied."""
    opening = STYLE_OPENINGS.get((style or "").lower(), "") if style and style != "none" else ""
    return H3Intent(
        duration_s=duration_s, mode=mode if mode in MODES else "t2va", style=opening,
        shots=[H3Shot(description=prompt.strip(), camera=_camera_from_motion(motion_strength))],
        language=normalize_language(language),
    )


def intent_from_director(result, duration_s: float, *, mode: str = "t2va", style: Optional[str] = None,
                         language: str = "English") -> H3Intent:
    """Shots from a DirectorResult: prompt, camera, duration and any dialogue
    the planner attached (director_service.ShotPrompt.dialogue)."""
    shots = []
    for shot in getattr(result, "shots", []) or []:
        dialogue = [
            H3Dialogue(speaker=d.get("speaker") or getattr(shot, "speaker", None) or "the character",
                       text=d.get("text", ""), lang=d.get("lang") or language,
                       intro=d.get("intro", ""), voiceover=bool(d.get("voiceover")))
            for d in (getattr(shot, "dialogue", None) or []) if isinstance(d, dict) and d.get("text")
        ]
        shots.append(H3Shot(
            description=getattr(shot, "prompt", "") or "",
            duration_s=float(getattr(shot, "duration", 0) or 0),
            camera=getattr(shot, "camera", None),
            dialogue=dialogue,
        ))
    opening = STYLE_OPENINGS.get((style or "").lower(), "") if style and style != "none" else ""
    return H3Intent(duration_s=duration_s, mode=mode if mode in MODES else "t2va", style=opening,
                    shots=shots, language=normalize_language(language))


def intent_from_shots(shots: Iterable[Any], duration_s: float, *, subjects: Iterable[Any] = (),
                      mode: str = "t2va", style: Optional[str] = None, language: str = "English",
                      audio_refs: Iterable[H3AudioRef] = ()) -> H3Intent:
    """Shots from Film Crew rows (ProductionShot-like: description or prompt,
    duration_seconds, character_name, dialogue_text). ``subjects`` are
    (name, description, picture indices) triples or objects with those
    attributes; when given, the intent is reference mode."""
    h3_shots = []
    for shot in shots:
        get = (lambda k, default=None: (shot.get(k, default) if isinstance(shot, dict) else getattr(shot, k, default)))
        text = get("dialogue_text") or get("dialogue")
        dialogue = [H3Dialogue(speaker=get("character_name") or "the character", text=text, lang=language)] if text else []
        h3_shots.append(H3Shot(
            description=get("description") or get("prompt") or get("action") or "",
            duration_s=float(get("duration_seconds") or get("duration") or 0),
            camera=get("camera") or None,
            dialogue=dialogue,
        ))
    h3_subjects = []
    for i, subj in enumerate(subjects or [], start=1):
        if isinstance(subj, (tuple, list)):
            name, desc, pics = (list(subj) + [None, None, []])[:3]
        else:
            name, desc, pics = getattr(subj, "name", None), getattr(subj, "description", ""), getattr(subj, "pictures", [])
        h3_subjects.append(H3Subject(id=i, description=f"{name}, {desc}".strip(", "), pictures=list(pics or [])))
    opening = STYLE_OPENINGS.get((style or "").lower(), "") if style and style != "none" else ""
    intent = H3Intent(duration_s=duration_s, mode="ref2va" if h3_subjects else (mode if mode in MODES else "t2va"),
                      style=opening, shots=h3_shots, subjects=h3_subjects, audio_refs=list(audio_refs),
                      language=normalize_language(language))
    if h3_subjects:
        intent.task_types = ["reference generation"] + (["audio reference"] if intent.audio_refs else [])
        # A subject's name inside a shot becomes its tag so the model tracks identity.
        for s in h3_subjects:
            name = s.description.split(",")[0].strip()
            if not name:
                continue
            for shot in intent.shots:
                shot.description = re.sub(rf"\b{re.escape(name)}\b", f"<Subject {s.id}>", shot.description)
    return intent


def intent_from_cut(cut: dict, prompt: str, *, song_audio_index: int = 1, style: Optional[str] = None,
                    language: str = "English") -> H3Intent:
    """One music-video cut: the still is the first frame, the song slice is an
    audio anchor, so the description says the motion follows the beat."""
    duration = float(cut.get("duration_s") or cut.get("end_s", 0) - cut.get("start_s", 0) or 5.0)
    opening = STYLE_OPENINGS.get((style or "").lower(), "") if style and style != "none" else ""
    desc = prompt.strip().rstrip(".")
    desc += f". The movement lands on the beats of <Audio {song_audio_index}>, which plays throughout"
    return H3Intent(
        duration_s=duration, mode="i2va", style=opening, shots=[H3Shot(description=desc)],
        soundscape="Only the performance's own physical sounds beneath the music.",
        music=f"<Audio {song_audio_index}> is reused as the complete audience-only score.",
        language=normalize_language(language),
    )


# ─── enhancer hook ──────────────────────────────────────────────────────────

def enhance_for_family(prompt: str, *, style: str = "cinematic", width: int = 0, height: int = 0,
                       fidelity_mode: bool = False, motion_strength: Optional[float] = None,
                       duration_s: Optional[float] = None, first_frame: bool = False,
                       last_frame: bool = False, language: str = "English",
                       h3_intent: Optional[dict] = None, polish: bool = False, **_ignored) -> str:
    """Called by prompt_enhancer.enhance_video_prompt for the minimax family.
    A prompt that already carries the sections passes through untouched."""
    if not prompt or looks_compiled(prompt):
        return prompt
    if h3_intent:
        try:
            intent = intent_from_dict(h3_intent)
            return compile(intent, polish=polish)[0]
        except Exception as e:  # noqa: BLE001 — fall back to the plain prompt path
            logger.info("H3 intent could not be used (%s); compiling the plain prompt", e)
    mode = "fl2va" if (first_frame and last_frame) else "i2va" if first_frame else "l2va" if last_frame else "t2va"
    intent = intent_from_plain_prompt(
        prompt, duration_s or 5.17, mode=mode, style=None if fidelity_mode else style,
        language=language, motion_strength=motion_strength,
    )
    if height > width > 0:
        intent.shots[0].description += " Vertical portrait framing with the subject centered"
    return compile(intent, polish=polish)[0]


def intent_from_dict(data: dict) -> H3Intent:
    """Rebuild an intent from its JSON form (the request field h3_intent)."""
    shots = [
        H3Shot(description=s.get("description", ""), duration_s=float(s.get("duration_s") or 0),
               camera=s.get("camera"), transition=s.get("transition") or "the camera cuts to",
               dialogue=[H3Dialogue(**{k: v for k, v in d.items() if k in ("speaker", "text", "lang", "intro", "voiceover")})
                         for d in s.get("dialogue", []) if d.get("text")])
        for s in data.get("shots", [])
    ]
    subjects = [H3Subject(id=int(s["id"]), description=s.get("description", ""), pictures=list(s.get("pictures", [])),
                          videos=list(s.get("videos", [])), retention=s.get("retention", "fully_preserved"),
                          note=s.get("note", "")) for s in data.get("subjects", [])]
    audio_refs = [H3AudioRef(index=int(a["index"]), role=a.get("role", "reference"),
                             description=a.get("description", ""), speaker=a.get("speaker"))
                  for a in data.get("audio_refs", [])]
    return H3Intent(
        duration_s=float(data.get("duration_s") or 5.17), mode=data.get("mode", "t2va"), style=data.get("style", ""),
        shots=shots, subjects=subjects, audio_refs=audio_refs, picture_frames=list(data.get("picture_frames", [])),
        task_types=list(data.get("task_types", [])), soundscape=data.get("soundscape", ""),
        music=data.get("music", "N/A"), summary=data.get("summary", ""),
        language=normalize_language(data.get("language")),
    )


def intent_to_dict(intent: H3Intent) -> dict:
    from dataclasses import asdict
    return asdict(intent)


# ─── optional polish ────────────────────────────────────────────────────────

_POLISH_SYSTEM = (
    "You improve a shot list for a video model that renders picture and sound together. "
    "You receive JSON with shots (description, camera, dialogue), a soundscape and music. "
    "Return the same JSON shape. Make every shot description concrete: composition, subject "
    "appearance and position, environment, lighting, the action and its result. Write camera "
    "moves as a motion type with amplitude and speed. Keep every dialogue text exactly as given. "
    "Write the soundscape as ambient and physical sounds only, and the music as instrumentation, "
    "tempo and dynamics or N/A. Never add words like beautiful, epic, stunning or cinematic. "
    "Return JSON only."
)


def polish_intent(intent: H3Intent, model: Optional[str] = None) -> H3Intent:
    """Ask the local director model to enrich descriptions, keeping the
    structure and the dialogue text. Raises on any failure; compile() catches."""
    import ollama
    from backend.services.director_service import DIRECTOR_MODEL, _resolve_model
    payload = intent_to_dict(intent)
    resp = ollama.chat(
        model=_resolve_model(model or DIRECTOR_MODEL),
        messages=[{"role": "system", "content": _POLISH_SYSTEM},
                  {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        format="json",
        options={"temperature": 0.4},
    )
    content = resp["message"]["content"] if isinstance(resp, dict) else resp.message.content
    data = json.loads(content)
    polished = intent_from_dict({**payload, **{k: v for k, v in data.items() if k in ("shots", "soundscape", "music", "style")}})
    # Dialogue is the person's text; a polish that dropped or changed it is discarded.
    original = [(d.speaker, d.text) for s in intent.shots for d in s.dialogue]
    kept = [(d.speaker, d.text) for s in polished.shots for d in s.dialogue]
    if original != kept or len(polished.shots) != len(intent.shots):
        raise ValueError("polish changed the shot count or the dialogue")
    return polished
