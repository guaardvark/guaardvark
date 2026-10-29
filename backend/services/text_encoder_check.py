"""What a .safetensors file holds, read from its header: which text encoder,
or that it is a diffusion model, a LoRA or a language model instead.

A text encoder that stands in for a shipped one has to be the same
architecture. FLUX.1's DualCLIPLoader reads a T5-XXL into one slot and a
CLIP-L into the other; Wan's UMT5-XXL, SDXL's CLIP-G or a UNET in either slot
fails inside ComfyUI with a shape error, or loads and renders noise. The header
names every tensor and its shape, so these checks read only the header: from
the Hub before anything downloads, and from disk before a render.
"""

from __future__ import annotations

import json
import logging
import re
import struct
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ARCH_LABELS = {
    "t5-xxl": "T5-XXL",
    "umt5-xxl": "UMT5-XXL",
    "t5": "T5 (not XXL)",
    "clip-l": "CLIP-L",
    "clip-g": "CLIP-G",
    "clip-h": "CLIP-H",
    "clip": "CLIP",
    "llm": "language-model encoder",
    "diffusion": "diffusion model",
    "lora": "LoRA",
}

# T5 v1.1 XXL and UMT5-XXL are both 4096 wide; the vocabularies differ, and
# UMT5 gives every block its own relative-position bias where T5 has one in
# block 0 only.
_T5_XXL_WIDTH = 4096
_T5_VOCAB = 32128
_UMT5_VOCAB = 256384
# CLIP text towers by embedding width: ViT-L/14 (SD 1.5, FLUX), OpenCLIP ViT-H
# (SD 2), OpenCLIP ViT-bigG (SDXL's second encoder).
_CLIP_WIDTHS = {768: "clip-l", 1024: "clip-h", 1280: "clip-g"}

_DIFFUSION_MARKERS = (
    "double_blocks.", "single_blocks.", "model.diffusion_model.", "joint_blocks.",
    "input_blocks.", "transformer_blocks.", "img_in.", "time_in.",
)
_LORA_MARKERS = ("lora_up", "lora_down", "lora_a.", "lora_b.", ".lokr_", ".hada_")
_BLOCK_RE = re.compile(r"(?:^|\.)block\.(\d+)\.")
_LAYER_RE = re.compile(r"(?:^|\.)(?:layers|resblocks)\.(\d+)\.")

MAX_HEADER_BYTES = 100 * 1024 * 1024


def classify(tensors: dict) -> Optional[str]:
    """The architecture id (a key of ARCH_LABELS) for ``{name: shape}``, or
    None when the tensors match nothing known."""
    if not tensors:
        return None
    names = [n for n in tensors if n != "__metadata__"]
    low = [n.lower() for n in names]
    if any(m in n for n in low for m in _LORA_MARKERS):
        return "lora"
    if any(m in n for n in low for m in _DIFFUSION_MARKERS):
        return "diffusion"

    t5_blocks = {int(m.group(1)) for n in names if "encoder." in n for m in [_BLOCK_RE.search(n)] if m}
    if t5_blocks:
        width = _t5_width(tensors, names)
        vocab = _first_dim(tensors, names, ("shared.weight", "embed_tokens.weight"))
        biased = {
            int(m.group(1)) for n in names if "relative_attention_bias" in n
            for m in [_BLOCK_RE.search(n)] if m
        }
        if vocab == _UMT5_VOCAB or len(biased) > 1:
            return "umt5-xxl" if width in (None, _T5_XXL_WIDTH) else "t5"
        if width not in (None, _T5_XXL_WIDTH):
            return "t5"
        if vocab not in (None, _T5_VOCAB):
            return "t5"
        return "t5-xxl"

    token_embedding = next((n for n in names if n.endswith("token_embedding.weight")), None)
    if token_embedding and any(_LAYER_RE.search(n) for n in names):
        shape = tensors.get(token_embedding) or []
        width = int(shape[1]) if len(shape) >= 2 else None
        return _CLIP_WIDTHS.get(width, "clip")

    if any(n.endswith("embed_tokens.weight") for n in names) and any(
        _LAYER_RE.search(n) and "self_attn" in n for n in names
    ):
        return "llm"
    return None


def _first_dim(tensors: dict, names: list, suffixes: tuple) -> Optional[int]:
    for n in names:
        if n.endswith(suffixes):
            shape = tensors.get(n) or []
            if shape:
                return int(shape[0])
    return None


def _t5_width(tensors: dict, names: list) -> Optional[int]:
    """d_model, from the embedding or else the first attention query."""
    for n in names:
        if n.endswith(("shared.weight", "embed_tokens.weight")):
            shape = tensors.get(n) or []
            if len(shape) >= 2:
                return int(shape[1])
    for n in names:
        if n.endswith("SelfAttention.q.weight"):
            shape = tensors.get(n) or []
            if len(shape) >= 2:
                return int(shape[1])
    return None


def label(arch: Optional[str]) -> str:
    return ARCH_LABELS.get(arch or "", "an unrecognised file")


def is_text_encoder(arch: Optional[str]) -> bool:
    return arch in ("t5-xxl", "umt5-xxl", "t5", "clip-l", "clip-g", "clip-h", "clip", "llm")


def local_tensors(path) -> dict:
    """``{name: shape}`` from a .safetensors file on disk, reading only its header."""
    with open(path, "rb") as f:
        head = f.read(8)
        if len(head) < 8:
            raise ValueError("file is shorter than a safetensors header")
        (size,) = struct.unpack("<Q", head)
        if not 0 < size <= MAX_HEADER_BYTES:
            raise ValueError("no safetensors header")
        header = json.loads(f.read(size))
    return {k: list(v.get("shape") or []) for k, v in header.items() if k != "__metadata__"}


def hf_tensors(hf_repo: str, filename: str, revision: str = "main") -> Optional[dict]:
    """``{name: shape}`` for a file on the Hub, fetched as its header alone.

    None when the header cannot be read (a gated repo without access, the
    network): the check is then made on the downloaded file before a render.
    """
    if not filename.lower().endswith(".safetensors"):
        return None
    try:
        from huggingface_hub import HfApi

        meta = HfApi().parse_safetensors_file_metadata(hf_repo, filename, revision=revision)
    except Exception as e:  # noqa: BLE001 — an unread header is not a verdict
        logger.info("Could not read the header of %s/%s: %s", hf_repo, filename, e)
        return None
    return {name: list(info.shape) for name, info in meta.tensors.items()}


def mismatch(arch: Optional[str], want: str, filename: str, slot_label: str) -> Optional[str]:
    """Why ``arch`` cannot fill a slot that reads ``want``; None when it can or
    when the file could not be classified."""
    if arch is None or arch == want:
        return None
    name = Path(filename or "").name
    if arch == "diffusion":
        return (f"{name} is a diffusion model, not a text encoder. "
                "Add it as a new generation model instead.")
    if arch == "lora":
        return f"{name} is a LoRA, not a text encoder. Add it as a LoRA on a model instead."
    return (f"{name} is a {label(arch)}; the {slot_label} slot reads a {label(want)}, "
            "so it would fail to load or render noise.")


def local_mismatch(path, want: str, slot_label: str) -> Optional[str]:
    """``mismatch`` for an installed file. A header that cannot be read is left
    to ComfyUI's own loader."""
    p = Path(path)
    try:
        arch = classify(local_tensors(p))
    except (OSError, ValueError) as e:
        logger.info("Could not read the header of %s: %s", p.name, e)
        return None
    return mismatch(arch, want, p.name, slot_label)
