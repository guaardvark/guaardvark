"""Core image upscaling logic.

Handles pre/post-processing, tiling, and single-image inference.
Works with any spandrel-loaded model.
"""
import logging
import math
import threading
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
import torch

from service.model_manager import (
    FACE_ENHANCE_DIR,
    GFPGAN_FILE,
    face_helper_dir,
    missing_face_enhance_message,
)

logger = logging.getLogger("upscaling.upscaler")

_face_restorer = None
_gfpgan_import_error: Optional[str] = None
_face_helper_swap_lock = threading.Lock()


class FaceEnhanceUnavailable(RuntimeError):
    """Face Enhancement was asked for and cannot run. The message says why
    and, when an Install fixes it, names the Install."""


def _gfpganer_class():
    """GFPGANer, or None when gfpgan does not import in this service (tried once)."""
    global _gfpgan_import_error
    if _gfpgan_import_error is not None:
        return None
    try:
        from gfpgan import GFPGANer
    except Exception as e:  # noqa: BLE001 - a broken CV stack fails in many ways
        _gfpgan_import_error = str(e) or type(e).__name__
        logger.warning(f"Face Enhancement is unavailable: gfpgan does not import ({e})")
        return None
    return GFPGANer


def face_enhance_supported() -> bool:
    """Whether Face Enhancement can run here once its weights are installed."""
    return _gfpganer_class() is not None


def face_enhance_unavailable_message() -> Optional[str]:
    """Why a Face Enhancement request cannot run, or None when it can."""
    if not face_enhance_supported():
        return (
            "Face Enhancement cannot run on this install: the gfpgan package does "
            f"not load in the upscaling service ({_gfpgan_import_error}). Turn Face "
            "Enhancement off to upscale without it."
        )
    return missing_face_enhance_message()


def _gfpganer_reading_helper_from(helper_dir: Path, **kwargs):
    """GFPGANer whose face detector and parser load from ``helper_dir``.

    GFPGANer hands FaceRestoreHelper a fixed model_rootpath ('gfpgan/weights',
    relative to the working directory) and takes no argument to change it, so
    the helper class it builds is swapped for the length of the constructor.
    facexlib downloads a file it does not find in that folder; the caller has
    checked that both are in helper_dir. Same approach as the backend's
    face_restoration_service (sidecars do not import the backend).
    """
    import gfpgan.utils as gfpgan_utils

    with _face_helper_swap_lock:
        helper_cls = gfpgan_utils.FaceRestoreHelper

        def helper(*args, **helper_kwargs):
            helper_kwargs["model_rootpath"] = str(helper_dir)
            return helper_cls(*args, **helper_kwargs)

        gfpgan_utils.FaceRestoreHelper = helper
        try:
            return gfpgan_utils.GFPGANer(**kwargs)
        finally:
            gfpgan_utils.FaceRestoreHelper = helper_cls


def _get_face_restorer():
    """The GFPGAN face restorer, built from the installed files only.

    Raises FaceEnhanceUnavailable when gfpgan does not import or a weight file
    is missing: weights arrive through the Install, never from here.
    """
    global _face_restorer
    if _face_restorer is None:
        problem = face_enhance_unavailable_message()
        if problem:
            raise FaceEnhanceUnavailable(problem)
        _face_restorer = _gfpganer_reading_helper_from(
            face_helper_dir(),
            model_path=str(FACE_ENHANCE_DIR / GFPGAN_FILE),
            upscale=1,
            arch='clean',
            channel_multiplier=2,
            bg_upsampler=None,
        )
    return _face_restorer


def _pre_process(
    img: np.ndarray,
    device: str = "cuda",
    precision: str = "bf16",
) -> torch.Tensor:
    """Convert HWC uint8 numpy image to NCHW float tensor on device."""
    # Copy so frames from ffmpeg/cv2 (often non-writable) are safe for torch.
    arr = np.array(img, copy=True, dtype=np.uint8)
    tensor = torch.from_numpy(arr.transpose(2, 0, 1)).float() / 255.0
    tensor = tensor.unsqueeze(0).to(device)
    if precision == "bf16" and torch.cuda.is_available() and torch.cuda.is_bf16_supported():
        tensor = tensor.bfloat16()
    elif precision == "fp16":
        tensor = tensor.half()
    return tensor


class UnusableOutputError(ValueError):
    """The model returned something that is not a picture. The message says what."""


# An input whose brightest value is at or below this is treated as black, so a
# black result from it is not a fault.
_BLACK_INPUT_MAX = 16


def _require_picture(output: torch.Tensor, img: np.ndarray) -> None:
    """Raise UnusableOutputError when the model's output holds NaN or infinity,
    or is black all over although the input was not."""
    if not torch.isfinite(output).all():
        raise UnusableOutputError(
            "The upscaling model returned no picture (its output is not a number). "
            "Nothing was saved. Pick another model."
        )
    if float(output.max()) * 255.0 < 0.5 and int(img.max()) > _BLACK_INPUT_MAX:
        raise UnusableOutputError(
            "The upscaling model returned an all-black picture from an input that "
            "is not black. Nothing was saved. Pick another model."
        )


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


def fit_long_edge(width: int, height: int, long_edge: int) -> Tuple[int, int]:
    """(width, height) scaled so the longer side is ``long_edge``, aspect kept.

    A 4K target is 3840 on the long side: 1920x1080 -> 3840x2160 and a
    portrait 1080x1920 -> 2160x3840.
    """
    factor = long_edge / max(width, height)
    return max(1, round(width * factor)), max(1, round(height * factor))


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
    target_size: Optional[Tuple[int, int]] = None,
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
        target_size: Exact output (width, height). The model runs at its
                  native scale and LANCZOS4 resizes to this; overrides outscale.

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

    _require_picture(output_tensor, img)
    output = _post_process(output_tensor)

    if target_size is not None:
        target_w, target_h = int(target_size[0]), int(target_size[1])
        if output.shape[1] != target_w or output.shape[0] != target_h:
            output = cv2.resize(output, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)
    elif outscale is not None and abs(outscale - scale) > 0.01:
        target_w = int(w_in * outscale)
        target_h = int(h_in * outscale)
        output = cv2.resize(output, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)

    if face_enhance:
        restorer = _get_face_restorer()
        _, _, restored_img = restorer.enhance(output, has_aligned=False, only_center_face=False, paste_back=True)
        if restored_img is not None:
            output = restored_img

    if sharpen > 0:
        output = _sharpen(output, amount=sharpen)

    return output
