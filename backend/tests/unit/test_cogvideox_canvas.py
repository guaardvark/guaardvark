"""CogVideoX-5b T2V defaults to the canvas measured to fit a 16 GB card.

720x480 is the trained canvas but runs out of memory on a 16 GB card, so the
registry declares 672x384 and records the measurement next to it.
"""

from backend.services.video_model_registry import clip_defaults_for


def test_16gb_card_gets_the_canvas_that_fits():
    d = clip_defaults_for("cogvideox-5b", total_vram_mb=16376)
    assert (d["width"], d["height"]) == (672, 384)
