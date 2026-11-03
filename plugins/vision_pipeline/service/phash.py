"""Perceptual hash with Pillow and numpy only.

Same construction as ``imagehash.phash`` (32x32 greyscale, 2-D DCT-II,
top-left 8x8 block, median threshold, 64 bits) so the Hamming distances
the change detector was tuned on keep their meaning, without the extra
dependency that the shared backend venv did not actually carry.
"""
import numpy as np
from PIL import Image

_HASH_SIZE = 8
_HIGHFREQ_FACTOR = 4
_IMG_SIZE = _HASH_SIZE * _HIGHFREQ_FACTOR


def _dct_matrix(n: int) -> np.ndarray:
    """Orthonormal DCT-II basis, rows are frequencies."""
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    m = np.cos(np.pi * (2 * i + 1) * k / (2 * n))
    m[0, :] *= 1 / np.sqrt(2)
    return m * np.sqrt(2 / n)


_DCT = _dct_matrix(_IMG_SIZE)


def phash(image: Image.Image) -> int:
    """64-bit perceptual hash as an int."""
    grey = image.convert("L").resize((_IMG_SIZE, _IMG_SIZE), Image.LANCZOS)
    pixels = np.asarray(grey, dtype=np.float64)
    dct = _DCT @ pixels @ _DCT.T
    low = dct[:_HASH_SIZE, :_HASH_SIZE]
    bits = (low > np.median(low)).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")
