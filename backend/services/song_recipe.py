# backend/services/song_recipe.py
# The recipe of a song or sound effect (see media_recipe.py for the shape),
# read from the metadata each audio path files with its Document:
#   ACE-Step v1 / 1.5 and Stable Audio Open (the Audio Foundry plugin) carry
#   `backend` and `model`; MiniMax Music 3 carries source "music_generation".

from __future__ import annotations

from typing import Any, Dict


def _row(rows, key: str, value: Any) -> None:
    if value is None or value == "" or value == []:
        return
    rows.append({"key": key, "value": value})


def audio_recipe(recipe: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
    model = meta.get("model") or meta.get("backend")
    is_sound = "stable-audio" in str(model or "").lower() or "sao" in str(meta.get("backend") or "").lower()
    recipe["kind"] = "sound" if is_sound else "song"
    arrangement = meta.get("arrangement") or {}
    recipe["prompt"] = (
        arrangement.get("caption") or meta.get("style_prompt") or meta.get("caption") or meta.get("prompt") or ""
    )

    rows = recipe["rows"]
    _row(rows, "model", model)
    _row(rows, "seed", meta.get("seed"))
    _row(rows, "duration_s", meta.get("requested_duration_s") or meta.get("duration_s"))
    _row(rows, "steps", meta.get("steps"))
    _row(rows, "guidance", meta.get("guidance_scale"))
    _row(rows, "bpm", arrangement.get("bpm"))
    _row(rows, "key", arrangement.get("keyscale"))
    _row(rows, "time_signature", arrangement.get("timesignature"))
    _row(rows, "language", arrangement.get("vocal_language"))
    if not is_sound:
        _row(rows, "instrumental", bool(meta.get("instrumental_only")))
    _row(rows, "negative_prompt", meta.get("negative_prompt"))
    lyrics = meta.get("lyrics") or ""
    if lyrics and lyrics.strip() != "[Instrumental]":
        recipe["lyrics"] = lyrics
    recipe["regenerate"]["reason"] = "not_yet"
    return recipe


def regenerate_audio(meta: Dict[str, Any], same_seed: bool) -> Dict[str, Any]:
    """Queue this song or sound again (not offered yet: recipes say so)."""
    raise ValueError("Songs and sounds cannot be made again from the Media page yet.")
