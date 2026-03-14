"""
Prompt Enhancement Utility for Video Generation.

Enriches user prompts with quality/style descriptors to improve
output from Wan2.2, CogVideoX, and other video generation models.
No LLM calls, no API calls — pure string concatenation.
"""

# Style enhancement suffixes keyed by preset name.
# Each entry is appended to the user's prompt as-is.
STYLE_SUFFIXES = {
    "cinematic": (
        "Cinematic lighting, shallow depth of field, film grain, "
        "color graded, 35mm film, professional cinematography, "
        "smooth natural motion, high quality, masterpiece"
    ),
    "realistic": (
        "Photorealistic, natural lighting, ultra detailed, 8K, "
        "sharp focus, realistic textures, lifelike motion, "
        "high quality, masterpiece"
    ),
    "artistic": (
        "Artistic, painterly, vivid colors, expressive brushstrokes, "
        "dramatic composition, creative motion, high quality, masterpiece"
    ),
    "anime": (
        "Anime style, cel shaded, vibrant colors, dynamic poses, "
        "fluid animation, detailed linework, high quality, masterpiece"
    ),
}

# Quality-focused negative prompts per style.
# These target only technical defects — no content restrictions.
NEGATIVE_PROMPTS = {
    "cinematic": (
        "blurry, low quality, pixelated, oversaturated, static, "
        "jerky motion, artifacts, distorted, poorly rendered, "
        "low resolution, watermark, text overlay"
    ),
    "realistic": (
        "blurry, low quality, pixelated, overexposed, underexposed, "
        "artifacts, distorted, poorly rendered, plastic skin, "
        "low resolution, watermark, text overlay"
    ),
    "artistic": (
        "blurry, low quality, pixelated, muddy colors, flat lighting, "
        "artifacts, distorted, poorly rendered, low resolution, "
        "watermark, text overlay"
    ),
    "anime": (
        "blurry, low quality, pixelated, bad anatomy, deformed, "
        "artifacts, distorted, poorly rendered, low resolution, "
        "watermark, text overlay, 3d render"
    ),
    "none": (
        "blurry, low quality, pixelated, artifacts, distorted, "
        "poorly rendered, low resolution"
    ),
}


def enhance_video_prompt(prompt: str, style: str = "cinematic") -> str:
    """Enhance a user prompt with quality descriptors for better video generation.

    Preserves the user's original intent and appends style-specific
    quality descriptors.  The ``"none"`` style returns the prompt unchanged.

    Args:
        prompt: The user's original prompt text.
        style: One of "cinematic", "realistic", "artistic", "anime", or "none".

    Returns:
        The enhanced prompt string.
    """
    if not prompt or not prompt.strip():
        return prompt

    style = (style or "cinematic").lower().strip()

    if style == "none":
        return prompt

    suffix = STYLE_SUFFIXES.get(style)
    if not suffix:
        # Unknown style — return unmodified
        return prompt

    # Normalise trailing punctuation so the join reads naturally
    trimmed = prompt.rstrip()
    if trimmed and trimmed[-1] not in ".!?":
        trimmed += "."

    return f"{trimmed} {suffix}"


def get_default_negative_prompt(style: str = "cinematic") -> str:
    """Get a quality-focused negative prompt (no content restrictions).

    Only targets technical defects: blur, artifacts, distortion, etc.

    Args:
        style: One of "cinematic", "realistic", "artistic", "anime", or "none".

    Returns:
        A negative prompt string focused on quality issues.
    """
    style = (style or "cinematic").lower().strip()
    return NEGATIVE_PROMPTS.get(style, NEGATIVE_PROMPTS["none"])
