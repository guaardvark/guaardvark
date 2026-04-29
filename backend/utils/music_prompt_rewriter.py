"""Natural-language → ACE-Step tag-prompt rewriter.

ACE-Step is tag-trained: it expects a comma-separated genre/instrument/mood
vocabulary, the way SD1.5 expects danbooru tags. Vague descriptors like
"professional" or "futuristic" without a genre anchor let the model fall to
its strongest training prior — frequently country/folk — even when the user
asks for piano and cello.

This module asks the local Ollama chat model to translate user prose into a
clean tag prompt + a paired negative_prompt. The rewriter runs on the main
backend (where Ollama lives), BEFORE we hit the audio_foundry plugin's
`/generate/music` endpoint — that endpoint will request VRAM via the
orchestrator and evict Ollama, so we have to get our LLM call in first
while it's still hot.

Returns None on any failure (Ollama down, model refused, JSON parse error)
so the caller can transparently fall back to the user's raw prompt.
"""
from __future__ import annotations

import json
import logging
from typing import Optional, TypedDict

import requests

from backend.config import OLLAMA_BASE_URL
from backend.utils.llm_service import get_saved_active_model_name

logger = logging.getLogger(__name__)


class RewriteResult(TypedDict):
    style_prompt: str
    negative_prompt: str
    tags_used: list[str]


# A short, opinionated vocabulary lesson. The few-shot examples are the load-
# bearing part — they teach the model both the format (comma-separated tags)
# and the *kinds* of tags that work (genre + mood + instrument + tempo +
# production texture). Examples are drawn from common ACE-Step prompt patterns
# observed on the StepFun model card and in user demos.
_SYSTEM_PROMPT = """You are a music prompt translator for ACE-Step, a tag-based music generation model.

ACE-Step expects comma-separated tags, not natural language. Vague modifiers like "professional" or "futuristic" alone produce country/folk drift — every prompt MUST include at least one explicit GENRE anchor.

Tag vocabulary (use these as anchors):
- Genres: cinematic, ambient, synthwave, lo-fi, classical, orchestral, jazz, electronic, hip-hop, rock, folk, cyberpunk, drum and bass, trip-hop, post-rock, neoclassical, dark ambient
- Moods: dark, uplifting, melancholy, energetic, calm, epic, romantic, tense, ethereal, dreamy, hopeful, ominous
- Instruments: piano, cello, violin, synth pads, electric guitar, acoustic guitar, drums, strings, brass, choir, harp, glockenspiel, 808 bass, analog synth
- Tempo/feel: slow tempo, mid-tempo, fast tempo, driving rhythm, sparse, lush
- Production: reverb-heavy, dry mix, lo-fi texture, polished mix, vinyl crackle, sidechained

You MUST output a JSON object with exactly these keys:
- "style_prompt": comma-separated tags, 6-12 tags total, anchored by a genre
- "negative_prompt": comma-separated tags to AVOID, including any genres/instruments the user implicitly doesn't want
- "tags_used": JSON array listing each tag in style_prompt

Rules:
- ALWAYS include "no vocals" in negative_prompt when the user asks for instrumental or doesn't mention vocals/lyrics.
- If the user names instruments (e.g. "piano, cello"), include them as tags AND add their common confusables to negative_prompt (cello → fiddle, banjo; synth → acoustic guitar).
- Translate vague mood words: "professional" → "polished mix"; "futuristic" → "synthwave" or "cinematic electronic"; "epic" → "epic, orchestral, lush strings".
- Never invent lyrics. Never add a genre the user didn't imply.

Examples:

User: "futuristic professional piano cello"
Output: {"style_prompt": "cinematic, ambient electronic, piano, cello, slow tempo, ethereal pads, polished mix, reverb-heavy", "negative_prompt": "country, folk, fiddle, banjo, no vocals, lo-fi texture, acoustic guitar", "tags_used": ["cinematic", "ambient electronic", "piano", "cello", "slow tempo", "ethereal pads", "polished mix", "reverb-heavy"]}

User: "dark synth driving for a chase scene"
Output: {"style_prompt": "synthwave, dark, driving rhythm, analog synth, 808 bass, fast tempo, cinematic, tense", "negative_prompt": "country, folk, acoustic guitar, no vocals, calm, sparse", "tags_used": ["synthwave", "dark", "driving rhythm", "analog synth", "808 bass", "fast tempo", "cinematic", "tense"]}

User: "chill lofi study beats with rain"
Output: {"style_prompt": "lo-fi, hip-hop, mid-tempo, jazzy piano, vinyl crackle, sparse drums, dreamy, lo-fi texture", "negative_prompt": "rock, metal, fast tempo, no vocals, distorted guitar, orchestral", "tags_used": ["lo-fi", "hip-hop", "mid-tempo", "jazzy piano", "vinyl crackle", "sparse drums", "dreamy", "lo-fi texture"]}

User: "uplifting orchestral epic with choir"
Output: {"style_prompt": "orchestral, cinematic, epic, lush strings, brass, choir, uplifting, mid-tempo, hopeful", "negative_prompt": "electronic, synth, lo-fi texture, dark, no vocals, country", "tags_used": ["orchestral", "cinematic", "epic", "lush strings", "brass", "choir", "uplifting", "mid-tempo", "hopeful"]}

Output ONLY the JSON object, no commentary."""


# Conservative default model — falls back to whatever is installed if the
# saved active model is gone. We don't pin a specific model because the user
# may be running gemma4, qwen3-vl, llama3, or ministral, and any of them can
# do this rewrite competently.
_REWRITE_TIMEOUT_S = 30


def rewrite_music_prompt(
    user_text: str,
    instrumental: bool = True,
    model: Optional[str] = None,
) -> Optional[RewriteResult]:
    """Rewrite natural-language music intent into ACE-Step tag prompts.

    Args:
        user_text: The user's free-form description (chips + free text joined).
        instrumental: If True, biases the rewriter toward instrumental output
            (the system prompt rules already enforce "no vocals" in negative
            when this is True; when False, we drop that nudge).
        model: Override the Ollama model. None = use the saved active model.

    Returns:
        A RewriteResult dict on success, or None if anything went wrong —
        callers must handle None by falling back to the raw user prompt.
    """
    text = (user_text or "").strip()
    if not text:
        return None

    chosen_model = model or get_saved_active_model_name() or "gemma4:e4b"

    user_msg = text
    if not instrumental:
        # Tell the model the user expects vocals so it doesn't auto-add
        # "no vocals" to the negative. Keeps the system prompt's instrumental
        # default behavior, but lets the caller flip it.
        user_msg = f"{text}\n\n[The user wants vocals — do NOT include 'no vocals' in negative_prompt.]"

    payload = {
        "model": chosen_model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        # Ollama's native JSON mode — guarantees the response.message.content
        # is parseable JSON without us having to strip ```json fences.
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.3,  # Low — we want consistent tag output, not creative drift
            "num_ctx": 2048,     # Plenty for the system prompt + a short user line
        },
    }

    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=payload,
            timeout=_REWRITE_TIMEOUT_S,
        )
        resp.raise_for_status()
    except (requests.ConnectionError, requests.Timeout) as e:
        logger.warning("Music prompt rewrite — Ollama unreachable: %s", e)
        return None
    except requests.HTTPError as e:
        logger.warning("Music prompt rewrite — Ollama returned %s: %s", resp.status_code, e)
        return None

    try:
        body = resp.json()
        content = body["message"]["content"]
    except (ValueError, KeyError) as e:
        logger.warning("Music prompt rewrite — bad response shape: %s", e)
        return None

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning("Music prompt rewrite — model didn't return valid JSON: %s; content=%r", e, content[:200])
        return None

    style_prompt = (parsed.get("style_prompt") or "").strip()
    negative_prompt = (parsed.get("negative_prompt") or "").strip()
    tags_used = parsed.get("tags_used") or []

    if not style_prompt:
        logger.warning("Music prompt rewrite — empty style_prompt; refusing to return")
        return None

    if not isinstance(tags_used, list):
        # Be tolerant — if the model returned a string, split it ourselves.
        tags_used = [t.strip() for t in str(tags_used).split(",") if t.strip()]

    logger.info(
        "Music prompt rewrite — model=%s, style=%r, neg=%r",
        chosen_model, style_prompt[:80], negative_prompt[:80],
    )

    return RewriteResult(
        style_prompt=style_prompt,
        negative_prompt=negative_prompt,
        tags_used=[str(t) for t in tags_used],
    )
