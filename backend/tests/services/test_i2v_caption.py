"""Empty I2V auto-caption does not invent a subject description."""
from backend.services.batch_video_generator import (
    _I2V_MOTION_ONLY,
    _i2v_prompt_from_caption,
)


def test_caption_becomes_motion_suffix():
    prompt, empty = _i2v_prompt_from_caption("a red fox in snow")
    assert empty is False
    assert prompt.startswith("a red fox in snow")
    assert "Subtle natural motion" in prompt


def test_empty_caption_is_motion_only_not_invented():
    prompt, empty = _i2v_prompt_from_caption("")
    assert empty is True
    assert prompt == _I2V_MOTION_ONLY
    assert "fox" not in prompt.lower()


def test_whitespace_caption_is_empty():
    prompt, empty = _i2v_prompt_from_caption("   \n")
    assert empty is True
    assert prompt == _I2V_MOTION_ONLY
