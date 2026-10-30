"""The unchosen "realistic" style yields to a medium the prompt names.

Chat and the Studio send style "realistic" when nobody picked one. "A vintage
travel poster" was rewritten as "A realistic travel poster" and rendered as a
photograph; a prompt that names its medium is now rewritten without the style.
"""
import pytest

try:
    from backend.services.media_director import _style_for
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.mark.parametrize("prompt", [
    "A vintage travel poster for the city of Lisbon",
    "two posters on a wall",
    "an ink drawing of a heron",
    "anime character on a cliff",
    "a watercolour of a harbour",
])
def test_a_named_medium_drops_the_default_style(prompt):
    assert _style_for([prompt], "realistic") == ""


@pytest.mark.parametrize("prompt", [
    "portrait of an old fisherman",
    "an iconic red barn at dawn",
    "an inkjet printer on a desk",
])
def test_photo_prompts_keep_it(prompt):
    assert _style_for([prompt], "realistic") == "realistic"


def test_a_chosen_style_and_mixed_batches_are_kept():
    assert _style_for(["a poster"], "cinematic") == "cinematic"
    assert _style_for(["a poster", "a red fox in the snow"], "realistic") == "realistic"
