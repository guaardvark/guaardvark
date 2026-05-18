"""Core image upscaling logic.

Handles pre/post-processing, tiling, and single-image inference.
Works with any spandrel-loaded model.
"""
import logging
import math
from typing import Optional

import cv2
import numpy as np
import torch

logger = logging.getLogger("upscaling.upscaler")

import logging

logger = logging.getLogger("upscaling.upscaler")

_face_restorer = None

def _get_face_restorer():
    global _face_restorer
    if _face_restorer is None:
        try:
            from gfpgan import GFPGANer
            import os
            weights_dir = os.path.join(os.path.dirname(__file__), "..", "gfpgan", "weights")
            os.makedirs(weights_dir, exist_ok=True)
            _face_restorer = GFPGANer(
                model_path='https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth',
                upscale=1,
                arch='clean',
                channel_multiplier=2,
                bg_upsampler=None
            )
        except ImportError:
            logger.error("gfpgan is not installed. Face enhancement will be skipped.")
            _face_restorer = "missing"
    return _face_restorer if _face_restorer != "missing" else None


def _pre_process(
    img: np.ndarray,
    device: str = "cuda",
    precision: str = "bf16",
) -> torch.Tensor:
    """Convert HWC uint8 numpy image to NCHW float tensor on device."""
    tensor = torch.from_numpy(img.transpose(2, 0, 1)).float() / 255.0
    tensor = tensor.unsqueeze(0).to(device)
    if precision == "bf16" and torch.cuda.is_available() and torch.cuda.is_bf16_supported():
        tensor = tensor.bfloat16()
    elif precision == "fp16":
        tensor = tensor.half()
    return tensor


def _post_process(tensor: torch.Tensor) -> np.ndarray:
    """Convert NCHW float tensor to HWC uint8 numpy image."""
    output = tensor.squeeze(0).float().clamp(0, 1).cpu().numpy()
    output = (output.transpose(1, 2, 0) * 255.0).round().astype(np.uint8)
    return output


def _tile_process(
    img_tensor: torch.Tensor,
    model: torch.nn.Module,
    scale: int,
    tile_size: int,
    tile_pad: int = 10,
) -> torch.Tensor:
    """Process image in tiles to avoid VRAM OOM."""
    batch, channel, height, width = img_tensor.shape
    out_h = height * scale
    out_w = width * scale
    output = img_tensor.new_zeros((batch, channel, out_h, out_w))

    tiles_x = math.ceil(width / tile_size)
    tiles_y = math.ceil(height / tile_size)

    for y in range(tiles_y):
        for x in range(tiles_x):
            x_start = x * tile_size
            x_end = min(x_start + tile_size, width)
            y_start = y * tile_size
            y_end = min(y_start + tile_size, height)

            x_start_pad = max(x_start - tile_pad, 0)
            x_end_pad = min(x_end + tile_pad, width)
            y_start_pad = max(y_start - tile_pad, 0)
            y_end_pad = min(y_end + tile_pad, height)

            tile = img_tensor[:, :, y_start_pad:y_end_pad, x_start_pad:x_end_pad]

            with torch.no_grad():
                out_tile = model(tile)

            ox_start = x_start * scale
            ox_end = x_end * scale
            oy_start = y_start * scale
            oy_end = y_end * scale

            crop_x_start = (x_start - x_start_pad) * scale
            crop_x_end = crop_x_start + (x_end - x_start) * scale
            crop_y_start = (y_start - y_start_pad) * scale
            crop_y_end = crop_y_start + (y_end - y_start) * scale

            output[:, :, oy_start:oy_end, ox_start:ox_end] = (
                out_tile[:, :, crop_y_start:crop_y_end, crop_x_start:crop_x_end]
            )

    return output


def _sharpen(img: np.ndarray, amount: float = 0.3, radius: int = 1) -> np.ndarray:
    """Apply a mild unsharp mask to restore micro-contrast after upscaling.

    Args:
        img: HWC uint8 BGR image.
        amount: Sharpening strength (0.0 = none, 1.0 = full).
        radius: Gaussian blur kernel radius (1 = 3x3, 2 = 5x5).
    """
    ksize = radius * 2 + 1
    blurred = cv2.GaussianBlur(img, (ksize, ksize), 0)
    sharpened = cv2.addWeighted(img, 1.0 + amount, blurred, -amount, 0)
    return sharpened


@torch.no_grad()
def upscale_image(
    img: np.ndarray,
    model: torch.nn.Module,
    scale: int,
    outscale: Optional[float] = None,
    tile_size: int = 0,
    device: str = "cuda",
    precision: str = "bf16",
    sharpen: float = 0.3,
    denoise_strength: float = 0.0,
    two_pass: bool = False,
    face_enhance: bool = False,
) -> np.ndarray:
    """Upscale a single image (HWC uint8 numpy).

    Args:
        img: Input image as HWC uint8 numpy array (BGR).
        model: Loaded spandrel model.
        scale: Model's native scale factor.
        outscale: Desired output scale. If different from model scale,
                  LANCZOS4 post-resize is applied.
        tile_size: Tile size for processing. 0 = no tiling.
        device: torch device string.
        precision: "bf16", "fp16", or "fp32".
        sharpen: Post-upscale unsharp mask strength (0.0 to disable).

    Returns:
        Upscaled image as HWC uint8 numpy array (BGR).
    """
    h_in, w_in = img.shape[:2]

    # Strip alpha channel entirely if present (prevents transparent black frames)
    if len(img.shape) == 3 and img.shape[2] == 4:
        img = img[:, :, :3]

    if denoise_strength > 0:
        strength = int(denoise_strength * 10)
        if strength > 0:
            img = cv2.fastNlMeansDenoisingColored(img, None, h=strength, hColor=strength, templateWindowSize=7, searchWindowSize=21)

    tensor = _pre_process(img, device=device, precision=precision)

    if tile_size > 0:
        output_tensor = _tile_process(tensor, model, scale, tile_size)
    else:
        output_tensor = model(tensor)

    if two_pass:
        output_tensor = output_tensor.clamp(0, 1)
        if tile_size > 0:
            output_tensor = _tile_process(output_tensor, model, scale, tile_size)
        else:
            output_tensor = model(output_tensor)
        scale = scale * scale

    output = _post_process(output_tensor)

    if outscale is not None and abs(outscale - scale) > 0.01:
        target_w = int(w_in * outscale)
        target_h = int(h_in * outscale)
        output = cv2.resize(output, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)

    if face_enhance:
        restorer = _get_face_restorer()
        if restorer:
            _, _, restored_img = restorer.enhance(output, has_aligned=False, only_center_face=False, paste_back=True)
            if restored_img is not None:
                output = restored_img

    if sharpen > 0:
        output = _sharpen(output, amount=sharpen)

    return output
