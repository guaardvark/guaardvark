"""Shape prompts into the JSON caption PAN 2 is trained on.

PAN 2 reads one JSON object per clip (style, lighting, view, subjects, the
first frame, timed events, quality), for the positive and the negative alike.
A plain sentence sent as-is is out of distribution for it, so every PAN 2
render goes through this module: a caption the person wrote in that shape is
sent unchanged, and a plain prompt is placed into the structure word for word.
The negative is the reference workflow's caption
(plugins/comfyui/scripts/prompt_bundles/pan2/negative.json).
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

from backend.config import GUAARDVARK_ROOT

logger = logging.getLogger(__name__)

BUNDLE_DIR = Path(GUAARDVARK_ROOT) / "plugins" / "comfyui" / "scripts" / "prompt_bundles" / "pan2"
NATIVE_FPS = 24
CAPTION_KEYS = ("video_style", "init_frame_visual_description", "events")


def parse_caption(text: Optional[str]) -> Optional[dict]:
    """The caption as a dict when ``text`` is a PAN 2 JSON caption, else None."""
    raw = (text or "").strip()
    if not raw.startswith("{"):
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if isinstance(data, dict) and any(k in data for k in CAPTION_KEYS):
        return data
    return None


@lru_cache(maxsize=1)
def reference_negative() -> dict:
    return json.loads((BUNDLE_DIR / "negative.json").read_text(encoding="utf-8"))


def _seconds(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _style_line(style: Optional[str]) -> str:
    from backend.services.h3_prompt_compiler import STYLE_OPENINGS

    opening = STYLE_OPENINGS.get((style or "").lower()) if style and style != "none" else None
    return f"{opening} footage in one uninterrupted continuous shot." if opening else \
        "One uninterrupted continuous shot."


def _view_line(width: int, height: int, motion_strength: Optional[float], first_frame: bool) -> str:
    parts = ["Camera placement matching the supplied frame." if first_frame else "Camera placement that suits the scene."]
    if height > width > 0:
        parts.append("Vertical portrait framing with the subject centered.")
    if motion_strength is not None and motion_strength <= 0.75:
        parts.append("The camera holds still.")
    elif motion_strength is not None and motion_strength >= 1.5:
        parts.append("The camera moves fast, following the action.")
    return " ".join(parts)


def caption_from_prompt(prompt: str, *, duration_s: float, style: Optional[str] = None,
                        width: int = 0, height: int = 0, first_frame: bool = False,
                        motion_strength: Optional[float] = None) -> dict:
    """One event spanning the clip, carrying the prompt verbatim."""
    text = prompt.strip()
    span = f"0 to {_seconds(max(duration_s, 0.1))}"
    return {
        "video_style": _style_line(style),
        "lighting": "Natural, consistent lighting that fits the scene.",
        "perspective_view": _view_line(width, height, motion_strength, first_frame),
        "subjects_lifecycle": [],
        "init_frame_visual_description": (f"The view matches the supplied image. {text}" if first_frame else text),
        "events": [{
            "event_index": 0,
            "time_range_sec": span,
            "event_description": text,
            "camera_motion": [],
            "contained_actions": [{"time_range_sec": span, "action": text}],
        }],
        "quality": {
            "video_quality": "Sharp, detailed footage with stable faces, hands and shapes.",
            "text_logos_overlays": None,
            "artifacts": None,
        },
    }


def caption_text(prompt: str, **context) -> str:
    """The prompt to send: a person's caption unchanged, otherwise the compiled one."""
    if parse_caption(prompt) is not None:
        return prompt.strip()
    return json.dumps(caption_from_prompt(prompt, **context), ensure_ascii=False)


def negative_text(user_negative: Optional[str]) -> str:
    """The negative caption: a person's JSON caption unchanged; plain words a
    person typed lead the reference caption's style line; none gives the reference."""
    if parse_caption(user_negative) is not None:
        return user_negative.strip()
    data = json.loads(json.dumps(reference_negative()))
    extra = (user_negative or "").strip()
    if extra:
        data["video_style"] = f"{extra}, {data.get('video_style', '')}".rstrip(", ")
    return json.dumps(data, ensure_ascii=False)


def enhance_for_family(prompt: str, *, style: str = "cinematic", width: int = 0, height: int = 0,
                       fidelity_mode: bool = False, motion_strength: Optional[float] = None,
                       duration_s: Optional[float] = None, first_frame: bool = False, **_ignored) -> str:
    """Called by prompt_enhancer.enhance_video_prompt for the pan2 family."""
    if not prompt:
        return prompt
    return caption_text(
        prompt, duration_s=duration_s or 121 / NATIVE_FPS, style=None if fidelity_mode else style,
        width=width, height=height, first_frame=first_frame, motion_strength=motion_strength,
    )
