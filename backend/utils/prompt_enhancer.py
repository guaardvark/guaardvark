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
    "3d_animation": (
        "3D-animated, Pixar-style polished CGI, expressive characters, "
        "soft global illumination, subsurface scattering, smooth rigging, "
        "appealing character design, high quality, masterpiece"
    ),
    "stop_motion": (
        "stop-motion animation, tactile clay textures, handcrafted miniatures, "
        "slight handcraft imperfection between frames, warm practical lighting, "
        "shallow depth of field, high quality, masterpiece"
    ),
    "hand_drawn": (
        "hand-drawn 2D animation, Studio Ghibli aesthetic, painterly watercolor "
        "backgrounds, expressive line work, gentle character motion, "
        "soft natural color palette, high quality, masterpiece"
    ),
    "western_cartoon": (
        "classic western animated cartoon style, bold outlines, flat shading, "
        "vibrant saturated palette, exaggerated expressions, snappy keyframed "
        "motion, high quality, masterpiece"
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
    "3d_animation": (
        "blurry, low quality, flat shading, low-poly, jagged edges, "
        "artifacts, distorted, poorly rendered, low resolution, "
        "watermark, text overlay, uncanny valley"
    ),
    "stop_motion": (
        "blurry, low quality, smooth digital motion, CGI look, "
        "artifacts, distorted, poorly rendered, low resolution, "
        "watermark, text overlay"
    ),
    "hand_drawn": (
        "blurry, low quality, photorealistic, 3d render, plastic textures, "
        "artifacts, distorted, poorly rendered, low resolution, "
        "watermark, text overlay"
    ),
    "western_cartoon": (
        "blurry, low quality, photorealistic, soft shading, muddy colors, "
        "artifacts, distorted, poorly rendered, low resolution, "
        "watermark, text overlay"
    ),
    "none": (
        "blurry, low quality, pixelated, artifacts, distorted, "
        "poorly rendered, low resolution"
    ),
}


def enhance_video_prompt(
    prompt: str,
    style: str = "cinematic",
    width: int = 0,
    height: int = 0,
) -> str:
    """Enhance a user prompt with quality descriptors for better video generation.

    Preserves the user's original intent and appends style-specific
    quality descriptors.  The ``"none"`` style returns the prompt unchanged.

    When portrait/vertical dimensions are detected (height > width), adds
    composition guidance so the model frames content for vertical viewing.

    Args:
        prompt: The user's original prompt text.
        style: One of "cinematic", "realistic", "artistic", "anime",
            "3d_animation", "stop_motion", "hand_drawn", "western_cartoon",
            or "none".
        width: Video width in pixels (used for orientation detection).
        height: Video height in pixels (used for orientation detection).

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

    # Portrait/vertical orientation — guide the model to compose for vertical
    if height > width and width > 0:
        portrait_hint = (
            "Vertical portrait composition, tall framing, subject centered "
            "in frame, close-up or medium shot, no important content at "
            "the left or right edges."
        )
        return f"{trimmed} {portrait_hint} {suffix}"

    return f"{trimmed} {suffix}"


def get_default_negative_prompt(style: str = "cinematic") -> str:
    """Get a quality-focused negative prompt (no content restrictions).

    Only targets technical defects: blur, artifacts, distortion, etc.

    Args:
        style: One of "cinematic", "realistic", "artistic", "anime",
            "3d_animation", "stop_motion", "hand_drawn", "western_cartoon",
            or "none".

    Returns:
        A negative prompt string focused on quality issues.
    """
    style = (style or "cinematic").lower().strip()
    return NEGATIVE_PROMPTS.get(style, NEGATIVE_PROMPTS["none"])
