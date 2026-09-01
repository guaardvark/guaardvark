"""Video recipes for outreach: a preset from the H3 prompt bundle with slots
filled from a product or a hook, queued through the same generate_video tool
the chat uses, so the result lands in the batch pipeline and the Approvals
page like any other clip. Nothing here posts anything.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from backend.services import h3_prompt_compiler as h3

OUTREACH_CATEGORIES = ("ads_products", "vlog_social")
_SLOT = re.compile(r"\{(\w+)\}")


def list_recipes() -> list[dict]:
    """Presets in the outreach categories, with the slots their text names."""
    bundle = json.loads((h3.BUNDLE_DIR / "presets.json").read_text(encoding="utf-8"))
    out = []
    for preset in bundle.get("presets", []):
        if preset.get("category") not in OUTREACH_CATEGORIES:
            continue
        slots = sorted({
            name
            for shot in preset["intent"].get("shots", [])
            for name in _SLOT.findall(shot.get("description", ""))
        })
        out.append({
            "slug": preset["slug"], "title": preset["title"], "category": preset["category"],
            "duration_s": preset.get("duration_s"), "ratio": preset.get("ratio"),
            "mode": preset.get("mode", "t2va"), "slots": slots,
        })
    return out


def fill_recipe(slug: str, slots: Optional[Dict[str, str]] = None) -> tuple[dict, str]:
    """(preset, compiled prompt) with ``{slot}`` markers replaced by the values
    given; a slot without a value keeps its marker so the omission is visible."""
    bundle = json.loads((h3.BUNDLE_DIR / "presets.json").read_text(encoding="utf-8"))
    preset = next((p for p in bundle.get("presets", []) if p["slug"] == slug), None)
    if preset is None:
        raise KeyError(f"unknown video recipe '{slug}'")
    values = {k: str(v) for k, v in (slots or {}).items()}
    intent_data = json.loads(json.dumps(preset["intent"]))
    for shot in intent_data.get("shots", []):
        shot["description"] = _SLOT.sub(lambda m: values.get(m.group(1), m.group(0)), shot.get("description", ""))
    intent = h3.intent_from_dict({**intent_data, "duration_s": preset.get("duration_s", 5),
                                  "mode": preset.get("mode", "t2va")})
    prompt, _ = h3.compile(intent)
    return preset, prompt


def queue_recipe_video(slug: str, *, model: str = "minimax-h3-int8", slots: Optional[Dict[str, str]] = None,
                       wait: bool = False) -> Dict[str, Any]:
    """Queue the recipe as a clip with its own soundtrack. Returns the tool's
    result dict (batch id, Studio link) or its error."""
    from backend.tools.image_tools import VideoGeneratorTool
    preset, prompt = fill_recipe(slug, slots)
    result = VideoGeneratorTool().execute(
        prompt=prompt, model=model, aspect_ratio=preset.get("ratio"),
        duration_s=preset.get("duration_s"), audio=True, style="none", wait_for_result=wait,
    )
    return {"success": result.success, "error": result.error, **(result.metadata or {})}
