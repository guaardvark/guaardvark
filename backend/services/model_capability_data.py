"""Declared facts about models, kept apart from the logic that reads them.

Everything here is data. The resolver in ``model_capability_resolver`` decides
how to combine it; nothing in this file decides anything. Keeping the two apart
is what makes "declare the limit in data, next to the thing it constrains"
structurally true rather than aspirational — a new model family is a row here,
not a branch in a call site.

Nothing in this file is consulted while Ollama can answer for itself. Ollama's
``/api/show`` knows what a model can do; these tables cover the two things it
cannot tell us (what coordinate convention a vision model emits) and the one
case where it is unavailable (Ollama down).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Vision, by name — the LAST resort
# ---------------------------------------------------------------------------
# Used only when Ollama cannot be reached. Name inference is wrong often enough
# to be dangerous: this box has `VladimirGav/gemma4-26b-16GB-VRAM-Uncensored`,
# which every name-based rule in the codebase called a vision model and which
# has no vision tower at all — sending it an image earns an Ollama 400. It also
# has `qwen3.5:9b` and `ornith-1.5:9b`, both genuinely multimodal and both
# missed by the older pattern lists. So: a hint for a degraded mode, never a
# source of truth.
VISION_NAME_FALLBACK = (
    "gemma4", "gemma-4", "qwen3-vl", "qwen2.5vl", "qwen2-vl", "qwen3.5", "qwen3.6",
    "llava", "bakllava", "moondream", "minicpm-v", "llama3.2-vision", "granite-vision",
    "pixtral", "molmo", "cogvlm", "internvl", "phi-vision", "deepseek-vl", "ministral-3",
    "devstral", "muse-glimmer", "ornith",
)

# ---------------------------------------------------------------------------
# Coordinate conventions, by architecture family
# ---------------------------------------------------------------------------
# `order`      "yx" for Google-style box_2d [y1,x1,y2,x2], "xy" for [x1,y1,x2,y2]
# `grid`       the value coordinates are normalised to, or None for raw pixels
# `confidence` how much we trust it; anything below 0.9 wants a probe run
#
# Only families actually measured on this project appear here. A family that is
# absent is not assumed — it falls through to the prompt contract (see the
# resolver) with low confidence, which is honest about being a guess and is
# still strictly better than the silent "xy" default it replaces.
# ---------------------------------------------------------------------------
# Request dialects
# ---------------------------------------------------------------------------
# A convention is not only how to READ a model's numbers; it is how to ASK.
# ministral-3:14b answers "[]" (not visible) to the Google box_2d request and
# answers a plain point request at ~140px. A model can look unable to point
# when it has merely been asked in a foreign dialect. Each style here is the
# exact prompt the servo sends, the shape it expects back, and whether the
# numbers come normalised to a grid or in raw pixels of the image sent.
#
# google_box2d's prompt is the servo's historical string VERBATIM. gemma4:e4b's
# path must stay byte-identical, and a test pins that.
COORD_STYLES = {
    "google_box2d": {
        "shape": "box", "grid": 1000, "orders": ("yx", "xy"),
        "prompt": (
            "Detect the {target}. Reply with ONLY a JSON list "
            '[{{"box_2d": [y1, x1, y2, x2], "label": "{target}"}}] '
            "with coordinates normalized to 1000. If the target is not visible, "
            "reply with an empty list []."
        ),
    },
    "qwen_bbox2d_abs": {
        "shape": "box", "grid": None, "orders": ("xy", "yx"),
        "prompt": (
            "Locate the {target} in the image. Reply with ONLY a JSON list "
            '[{{"bbox_2d": [x1, y1, x2, y2], "label": "{target}"}}] '
            "using absolute pixel coordinates of this image. If the target is not "
            "visible, reply with an empty list []."
        ),
    },
    "point_xy_abs": {
        "shape": "point", "grid": None, "orders": ("xy",),
        "prompt": (
            "Where is the {target}? Reply with ONLY JSON "
            '{{"x": <pixels from the left edge>, "y": <pixels from the top edge>}} '
            "giving the centre of the {target} in absolute pixels of this image. "
            "If it is not visible, reply with {{}}."
        ),
    },
    "point_xy_norm": {
        "shape": "point", "grid": 1000, "orders": ("xy",),
        "prompt": (
            "Where is the {target}? Reply with ONLY JSON "
            '{{"x": <0-1000>, "y": <0-1000>}} giving the centre of the {target} '
            "normalized to a 1000x1000 grid, x from the left edge, y from the top. "
            "If it is not visible, reply with {{}}."
        ),
    },
}
DEFAULT_STYLE = "google_box2d"


def style_overlay(style: str, order: str) -> dict:
    """A vision_config overlay that pins one dialect and one axis order."""
    st = COORD_STYLES[style]
    return {"coord_style": style, "coord_order": order,
            "internal_width": st["grid"] or 0}


# ---------------------------------------------------------------------------
# Coordinate conventions, by architecture family
# ---------------------------------------------------------------------------
# `style`      which request dialect (COORD_STYLES key)
# `order`      "yx" for Google-style box_2d [y1,x1,y2,x2], "xy" otherwise
# `grid`       normalisation denominator, None for raw pixels
# `confidence` below MEASURED_CONFIDENCE the resolver treats it as a guess
# `min_num_predict`     token budget the anchor call needs to finish answering
# `think_uncontrollable` Ollama's think:false is ignored for this family, so the
#                        budget must cover the thinking too (declared limit, in
#                        data, next to the family it constrains)
#
# Only families measured on this project appear. An absent family falls through
# to the prompt contract with low confidence, which is honest about being a
# guess and still better than the silent "xy" it replaced.
FAMILY_COORD_DEFAULTS = {
    "gemma4": {
        "style": "google_box2d", "order": "yx", "grid": 1000, "normalised": True,
        "confidence": 0.95, "min_num_predict": 128,
        "note": ("Google box_2d, [y1,x1,y2,x2] normalised to 1000. Matches the "
                 "measured, shipping gemma4:e4b row and Google's own published "
                 "notebook. Two years of this project's servo data sit on it."),
    },
    "qwen35": {
        "style": "google_box2d", "order": "xy", "grid": 1000, "normalised": True,
        "confidence": 0.9, "min_num_predict": 128,
        "note": ("Answers the Google-style request but in [x1,y1,x2,y2] order. "
                 "Probed on qwen3.5:9b 2026-09-22: read as xy, median error 15px; "
                 "read as yx, 345px. This is why an untested model looks like a bad "
                 "model — and why qwen3.5:9b, which out-points gemma4:e4b by a "
                 "factor of four, went unfound."),
    },
    "qwen3vl": {
        "style": "qwen_bbox2d_abs", "order": "xy", "grid": None, "normalised": False,
        "confidence": 0.5, "min_num_predict": 1024, "think_uncontrollable": True,
        "note": ("Native dialect is bbox_2d in absolute pixels. The only installed tag "
                 "(qwen3-vl:8b-thinking-q8_0) ignores think:false and spends the whole "
                 "budget thinking: done_reason=length at 256/512/1024 (2026-09-22). "
                 "Half its answers truncate and the rest miss by hundreds of pixels. "
                 "Confidence stays below the measured bar until a non-thinking tag is "
                 "probed."),
    },
}

# ---------------------------------------------------------------------------
# Models that Ollama does not serve
# ---------------------------------------------------------------------------
# An MCP client driving the screen, or a hosted model, has no /api/show. Declare
# it here or the resolver treats it as unknown and refuses to click, which is
# the correct default for something we know nothing about.
EXTERNAL_MODEL_ROWS: dict = {}

# ---------------------------------------------------------------------------
# Pointing accuracy that ships with the code
# ---------------------------------------------------------------------------
# A fresh clone has no measurement store, so every eye was "unmeasured" and a
# model that cannot point was trusted to. The stock chat model, gemma4:e2b,
# lands clicks a median 255px from the target. These rows let a fresh clone
# rank eyes on evidence. A local measurement for the same model always wins.
#
# Each row holds only for the exact build it was measured on: `digest` is the
# prefix of the Ollama manifest digest from /api/tags, and a tag whose installed
# digest differs (re-pulled, re-quantised, a Modelfile variant) is treated as
# unmeasured rather than assumed to point the same.
SHIPPED_ACCURACY_SCREEN = "1000x1000"
SHIPPED_ACCURACY_SOURCE = (
    "eye_bakeoff --mode anchor, 2026-09-24: 30 targets on two 1000x1000 vision "
    "trainer boards (six frames of five labelled dots), each model read in its own "
    "axis order, calibration off. Median distance from the dot centre; a hit is "
    "within the dot's 26px radius."
)
SHIPPED_EYE_ACCURACY = {
    "gemma4:e2b": {"digest": "7fbdbf8f5e45", "median_px": 255.0, "median_x": 34.0,
                   "median_y": 254.0, "hit_rate": 0.07, "n": 30},
    "gemma4:e4b": {"digest": "c6eb396dbd59", "median_px": 48.8, "median_x": 23.0,
                   "median_y": 38.0, "hit_rate": 0.13, "n": 30},
    "gemma4:latest": {"digest": "c6eb396dbd59", "median_px": 48.8, "median_x": 23.0,
                      "median_y": 38.0, "hit_rate": 0.13, "n": 30},
    "gemma4:12b": {"digest": "4eb23ef187e2", "median_px": 5.8, "median_x": 3.0,
                   "median_y": 4.0, "hit_rate": 1.0, "n": 30},
    "qwen3.5:9b": {"digest": "6488c96fa5fa", "median_px": 14.4, "median_x": 7.0,
                   "median_y": 8.0, "hit_rate": 0.73, "n": 30},
    "qwen3.6:27b-q4_K_M": {"digest": "3a40c32f1450", "median_px": 2.2, "median_x": 1.0,
                           "median_y": 1.0, "hit_rate": 0.93, "n": 30},
}

# How often each eye answers the correction loop's question right on both axes
# (a red ring 60px left/right/above/below of a labelled dot, or on it; 25
# probes on one trainer frame; eye_bakeoff --judge, 2026-09-24). Pointing and
# judging are separate skills: gemma4:e4b misses dots by ~50px yet judges 25
# of 25, which is why the loop can walk its clicks onto a target. Same
# digest binding as the accuracy rows.
SHIPPED_JUDGE_SOURCE = (
    "eye_bakeoff --judge, 2026-09-24: 25 probes on one 1000x1000 vision trainer "
    "frame (five labelled dots x five marker offsets), both axes right / probes."
)
SHIPPED_EYE_JUDGE = {
    "gemma4:e2b": {"digest": "7fbdbf8f5e45", "both_rate": 0.76, "n": 25, "ms_median": 174},
    "gemma4:e4b": {"digest": "c6eb396dbd59", "both_rate": 1.0, "n": 25, "ms_median": 259},
    "gemma4:latest": {"digest": "c6eb396dbd59", "both_rate": 1.0, "n": 25, "ms_median": 259},
    "gemma4:12b": {"digest": "4eb23ef187e2", "both_rate": 1.0, "n": 25, "ms_median": 461},
    "qwen3.5:9b": {"digest": "6488c96fa5fa", "both_rate": 0.96, "n": 25, "ms_median": 492},
    "qwen3.6:27b-q4_K_M": {"digest": "3a40c32f1450", "both_rate": 1.0, "n": 25, "ms_median": 2956},
    "minicpm-v4.6:latest": {"digest": "e95583acac77", "both_rate": 0.2, "n": 25, "ms_median": 5137},
    "moondream:latest": {"digest": "55fc3abd3867", "both_rate": 0.0, "n": 25, "ms_median": 1817},
    "granite3.2-vision:latest": {"digest": "3be41a661804", "both_rate": 0.0, "n": 25, "ms_median": 553},
    "llava-phi3:latest": {"digest": "c7edd7b87593", "both_rate": 0.0, "n": 25, "ms_median": 313},
}

# The correction loop runs only for an eye measured to judge at least this
# well; an unmeasured eye still runs it. At 0.76 gemma4:e2b's loop left its
# clicks where they were (median 237px before and after, 30 targets); at
# 0.96-1.0 the loop roughly doubled hits for gemma4:e4b.
EYE_JUDGE_MIN_BOTH_RATE = 0.9

# When a model that can see points this badly, and an installed model points
# well, the user's model keeps deciding and the good pointer does the looking
# (split mode, the path a blind model already takes). Measured on the same
# boards: at 49px gemma4:e4b hit 4 of 30 dots, at 14px qwen3.5:9b hit 22, so
# 40px is where a model stops being able to press a typical control. 24px is
# the vision trainer's dot diameter, the bar an eye must clear to be lent.
# GUAARDVARK_EYE_BORROW=0 keeps every sighted model looking for itself.
EYE_BORROW_NATIVE_WORSE_THAN_PX = 40.0
EYE_BORROW_EYE_AT_MOST_PX = 24.0


# ---------------------------------------------------------------------------
# Name rules used by ollama_resource_manager
# ---------------------------------------------------------------------------
# Regexes over the lower-cased tag. Each decides something Ollama can answer
# for itself; they cover a server that is not answering yet and unit tests
# that never reach one. They are kept as they were: where they disagree with
# Ollama or with each other, model_capabilities records both answers.
#
# Families that reason in Ollama's hidden ``thinking`` channel. A match wins
# over /api/show (model_supports_thinking), so think:false is sent to these even
# when Ollama does not list "thinking". What thinking cost when left on:
#   * gemma4 12B, chat, 2026-09-06: 1,163 tokens / ~40 s for a 554-char reply
#     against 183 tokens / ~10 s for an 858-char reply with thinking off.
#   * gemma4 12B, summarisation (raptor_service): ~45x slower, shorter output.
#   * qwen3.5 9B, structured extraction: 2-4k reasoning tokens per call, enough
#     to blow a 120 s request timeout.
THINKING_NAME_PATTERNS = [
    r'deepseek-r1', r'thinking', r'gemma[\-_]?4', r'qwen3',
]

# is_vision_model's fallback when the capability resolver cannot be imported.
VISION_NAME_PATTERNS = [
    r'vl\b', r'vision', r'llava', r'moondream', r'bakllava',
    r'minicpm-v', r'llama.*vision', r'granite.*vision', r'gemma.*vision',
    # Gemma 4 integrates vision natively — match even without "vision" suffix
    r'gemma[\-_]?4',
]

# Not a default text chat model (vision-only or embedding). Natively multimodal
# chat models (Gemma 4) are left out on purpose.
NON_TEXT_NAME_PATTERNS = [
    r'vl\b', r'vision', r'llava', r'moondream', r'bakllava',
    r'minicpm-v', r'llama.*vision', r'granite.*vision', r'gemma.*vision',
    r'embed', r'retrieval', r'minilm',
]

# ---------------------------------------------------------------------------
# Declared capability rows
# ---------------------------------------------------------------------------
# A row replaces what Ollama's /api/show reports for one tag, field by field.
# Shipped rows are for a build whose /api/show is known to be wrong; there are
# none. A machine adds its own in data/config/model_capabilities.json (gitignored,
# like the rest of data/config), shaped {"<tag>": {"tools": false, ...}}; a local
# row wins over a shipped one. Vision is not overridable here: it has its own
# authority order in model_capability_resolver (EXTERNAL_MODEL_ROWS).
MODEL_CAPABILITY_ROWS: dict = {}
OVERRIDABLE_FIELDS = (
    "tools", "thinking", "completion", "embedding", "native_context", "embedding_dim",
)


def name_looks_vision(tag: str) -> bool:
    """Degraded-mode guess. See VISION_NAME_FALLBACK for why this is last."""
    if not tag:
        return False
    short = tag.split("/")[-1].lower()
    return any(m in short for m in VISION_NAME_FALLBACK)
