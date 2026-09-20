"""The FLUX branch of stills_pipeline passes the caller's model tag through.

``_build_workflow`` chooses the FLUX-dev graph when the tag contains both
"flux" and "dev", and the schnell GGUF graph otherwise. The pipeline used to
hard-code ``model="flux"`` at the call site, so a flux-dev request rendered on
schnell while still carrying the flux family's 28 steps / cfg 3.5 — dev
sampling settings on a 4-step distilled model.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.services.stills_pipeline import _generate_comfy_flux


def _run(model: str, tmp_path):
    out = tmp_path / "flux.png"
    out.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    with patch(
        "backend.services.comfyui_image_generator.ComfyUIImageGenerator"
    ) as Comfy:
        gen = MagicMock()
        gen.generate_image.return_value = str(out)
        Comfy.return_value = gen
        result = _generate_comfy_flux(
            prompt="a lighthouse in fog",
            negative="",
            model=model,
            width=1024,
            height=1024,
            steps=28,
            guidance=3.5,
            seed=7,
            enhance_mode="none",
            output="path",
            output_dir=str(tmp_path),
        )
    assert result.success is True, result.error
    return gen.generate_image.call_args[1]


@pytest.mark.parametrize("tag", ["flux-dev", "flux1-dev", "FLUX-Dev"])
def test_a_dev_tag_reaches_the_generator_unchanged(tag, tmp_path):
    assert _run(tag, tmp_path)["model"] == tag


@pytest.mark.parametrize("tag", ["flux", "flux-schnell"])
def test_a_schnell_tag_is_still_a_schnell_tag(tag, tmp_path):
    """No "dev" in the tag, so _build_workflow keeps the schnell GGUF graph."""
    passed = _run(tag, tmp_path)["model"]
    assert passed == tag
    assert "dev" not in passed.lower()


def test_the_build_dispatch_this_relies_on_still_splits_dev_from_schnell():
    """Pin the condition the fix depends on, so a rename here fails loudly."""
    import inspect

    from backend.services.comfyui_image_generator import ComfyUIImageGenerator

    src = inspect.getsource(ComfyUIImageGenerator._build_workflow)
    assert '"flux" in ml and "dev" in ml' in src
