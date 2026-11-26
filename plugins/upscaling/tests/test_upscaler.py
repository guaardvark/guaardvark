import numpy as np
import pytest
import torch
from service.upscaler import fit_long_edge, upscale_image, _pre_process, _post_process


def test_pre_process_shape():
    """Pre-process converts HWC numpy to NCHW float tensor."""
    img = np.random.randint(0, 255, (64, 64, 3), dtype=np.uint8)
    tensor = _pre_process(img, device="cpu", precision="fp32")
    assert tensor.shape == (1, 3, 64, 64)
    assert tensor.dtype.is_floating_point


def test_post_process_shape():
    """Post-process converts NCHW tensor back to HWC uint8 numpy."""
    import torch
    tensor = torch.rand(1, 3, 128, 128)
    img = _post_process(tensor)
    assert img.shape == (128, 128, 3)
    assert img.dtype == np.uint8


def test_post_process_clamps():
    """Post-process clamps values to 0-255."""
    import torch
    tensor = torch.tensor([[[[1.5, -0.5], [0.5, 0.0]]]])  # 1,1,2,2
    img = _post_process(tensor)
    assert img.max() <= 255
    assert img.min() >= 0


class _NearestX4:
    """A stand-in 4x model: nearest-neighbour, no weights, CPU."""

    def __call__(self, tensor):
        import torch.nn.functional as F
        return F.interpolate(tensor, scale_factor=4, mode="nearest")


@pytest.mark.parametrize("size, long_edge, expected", [
    ((1920, 1080), 3840, (3840, 2160)),
    ((1080, 1920), 3840, (2160, 3840)),
    ((1920, 1080), 7680, (7680, 4320)),
    ((512, 512), 3840, (3840, 3840)),
    ((1366, 768), 3840, (3840, 2159)),
])
def test_fit_long_edge_keeps_the_aspect(size, long_edge, expected):
    assert fit_long_edge(*size, long_edge) == expected


def _upscale(img, **kwargs):
    return upscale_image(img, _NearestX4(), 4, device="cpu", precision="fp32", sharpen=0.0, **kwargs)


def test_target_size_is_the_output_size():
    img = np.random.randint(0, 255, (27, 48, 3), dtype=np.uint8)
    out = _upscale(img, target_size=(3840, 2160))
    assert out.shape == (2160, 3840, 3)


def test_target_size_wins_over_outscale():
    img = np.random.randint(0, 255, (27, 48, 3), dtype=np.uint8)
    out = _upscale(img, outscale=2.0, target_size=(320, 180))
    assert out.shape == (180, 320, 3)


def test_without_a_target_the_scale_decides():
    img = np.random.randint(0, 255, (27, 48, 3), dtype=np.uint8)
    assert _upscale(img).shape == (108, 192, 3)
    assert _upscale(img, outscale=2.0).shape == (54, 96, 3)


class _Scaled(torch.nn.Module):
    """A 4x "model" whose output is its nearest-neighbour upscale times ``factor``."""

    def __init__(self, factor):
        super().__init__()
        self.factor = factor

    def forward(self, x):
        return torch.nn.functional.interpolate(x, scale_factor=4) * self.factor


def test_nan_or_black_model_output_is_refused():
    from service.upscaler import UnusableOutputError

    grey = np.full((8, 8, 3), 120, dtype=np.uint8)
    for factor in (float("nan"), 0.0):
        with pytest.raises(UnusableOutputError):
            upscale_image(grey, _Scaled(factor), 4, device="cpu", precision="fp32")


def test_dark_and_black_inputs_still_upscale():
    for value in (0, 12):
        frame = np.full((8, 8, 3), value, dtype=np.uint8)
        out = upscale_image(frame, _Scaled(1.0), 4, device="cpu", precision="fp32", sharpen=0)
        assert out.shape == (32, 32, 3)
        assert int(out.max()) == value
