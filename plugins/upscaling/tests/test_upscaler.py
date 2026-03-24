import numpy as np
import pytest
from service.upscaler import upscale_image, _pre_process, _post_process


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
