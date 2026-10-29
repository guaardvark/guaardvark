# backend/services/song_recipe.py
# The recipe of a song or sound effect (see media_recipe.py for the shape),
# read from the metadata each audio path files with its Document:
#   ACE-Step v1 / 1.5 and Stable Audio Open (the Audio Foundry plugin) carry
#   `backend` and `model`; MiniMax Music 3 carries source "music_generation".
# Voice clips share the Audio folder and are recognised but not remade here.

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# Audio Foundry backend name -> (kind, the `model` value /generate/music takes).
# Sound effects go to /generate/fx, which takes no model.
AUDIO_BACKENDS = {
    "ace_step_v1_3.5b": ("song", "ace-step"),
    "ace_step_1.5_turbo": ("song", "ace-step-1.5"),
    "stable_audio_open_1.0": ("sound", None),
}
VOICE_BACKENDS = ("chatterbox", "kokoro", "piper")


def _row(rows, key: str, value: Any) -> None:
    if value is None or value == "" or value == []:
        return
    rows.append({"key": key, "value": value})


def audio_kind(meta: Dict[str, Any]) -> Optional[str]:
    """song, sound or voice for a generated audio file, or None."""
    backend = str(meta.get("backend") or "")
    if backend in AUDIO_BACKENDS:
        return AUDIO_BACKENDS[backend][0]
    if meta.get("source") == "music_generation":
        return "song"
    if any(v in backend for v in VOICE_BACKENDS):
        return "voice"
    return None


def audio_title(meta: Dict[str, Any]) -> str:
    """A short name for a song or sound: the start of what was asked for."""
    arrangement = meta.get("arrangement") or {}
    text = (arrangement.get("caption") or meta.get("style_prompt") or meta.get("caption")
            or meta.get("prompt") or meta.get("text") or "")
    text = " ".join(str(text).split())
    return text[:90] + ("…" if len(text) > 90 else "")


def audio_recipe(recipe: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
    kind = audio_kind(meta) or "file"
    recipe["kind"] = kind
    arrangement = meta.get("arrangement") or {}
    recipe["prompt"] = (
        arrangement.get("caption") or meta.get("style_prompt") or meta.get("caption")
        or meta.get("prompt") or meta.get("text") or ""
    )

    rows = recipe["rows"]
    _row(rows, "model", meta.get("model") or meta.get("backend"))
    _row(rows, "seed", meta.get("seed"))
    _row(rows, "duration_s", meta.get("requested_duration_s") or meta.get("duration_s"))
    _row(rows, "steps", meta.get("steps"))
    _row(rows, "guidance", meta.get("guidance_scale"))
    _row(rows, "bpm", arrangement.get("bpm"))
    _row(rows, "key", arrangement.get("keyscale"))
    _row(rows, "time_signature", arrangement.get("timesignature"))
    _row(rows, "language", arrangement.get("vocal_language"))
    if kind == "song":
        _row(rows, "instrumental", bool(meta.get("instrumental_only")))
    _row(rows, "negative_prompt", meta.get("negative_prompt"))
    lyrics = meta.get("lyrics") or arrangement.get("lyrics") or ""
    if lyrics and lyrics.strip() != "[Instrumental]":
        recipe["lyrics"] = lyrics

    if kind in ("song", "sound") and recipe["prompt"]:
        recipe["regenerate"] = {"available": True, "same_seed": meta.get("seed") is not None, "reason": None}
        recipe["adjust"] = {"path": "/audio", "query": {"retry_doc": recipe["document_id"]}}
    else:
        recipe["regenerate"]["reason"] = "voice" if kind == "voice" else "no_settings"
    return recipe


def audio_regenerate_request(meta: Dict[str, Any], same_seed: bool) -> Tuple[str, Dict[str, Any]]:
    """("music" | "fx", the request Audio Studio would send) to make this song
    or sound again with the settings it was made with."""
    seed = meta.get("seed") if same_seed else None
    duration = meta.get("requested_duration_s") or meta.get("duration_s")
    backend = str(meta.get("backend") or "")
    kind = audio_kind(meta)

    if kind == "sound":
        payload: Dict[str, Any] = {"prompt": meta.get("prompt") or "", "async": True}
        if duration:
            payload["duration_s"] = duration
        if seed is not None:
            payload["seed"] = seed
        return "fx", payload
    if kind != "song":
        raise ValueError("Only songs and sound effects can be made again here.")

    instrumental = bool(meta.get("instrumental_only"))
    payload = {"instrumental_only": instrumental, "async": True}
    if duration:
        payload["duration_s"] = duration
    if seed is not None:
        payload["seed"] = seed

    if meta.get("source") == "music_generation":
        # MiniMax Music 3: the caption and lyrics it was given, at its own step count.
        payload.update({"model": meta.get("model"), "style_prompt": meta.get("caption") or meta.get("style_prompt") or ""})
        if meta.get("steps"):
            payload["steps"] = meta["steps"]
        if not instrumental and meta.get("lyrics"):
            payload["lyrics"] = meta["lyrics"]
        return "music", payload

    model = AUDIO_BACKENDS[backend][1]
    if model == "ace-step-1.5":
        # The accepted arrangement goes back as it was, so it is not planned again.
        arrangement = meta.get("arrangement") or {}
        payload.update({
            "model": model,
            "arrangement_accepted": True,
            "style_prompt": arrangement.get("caption") or meta.get("style_prompt") or "",
        })
        lyrics = arrangement.get("lyrics") or meta.get("lyrics") or ""
        if not instrumental and lyrics.strip() and lyrics.strip() != "[Instrumental]":
            payload["lyrics"] = lyrics.strip()
        for key in ("bpm", "keyscale", "timesignature"):
            if arrangement.get(key):
                payload[key] = arrangement[key]
        if arrangement.get("vocal_language") and arrangement["vocal_language"] != "unknown":
            payload["vocal_language"] = arrangement["vocal_language"]
        return "music", payload

    payload["style_prompt"] = meta.get("style_prompt") or ""
    if meta.get("negative_prompt"):
        payload["negative_prompt"] = meta["negative_prompt"]
    if not instrumental and meta.get("lyrics"):
        payload["lyrics"] = meta["lyrics"]
    return "music", payload
