"""The smallest files that pass backend.utils.model_file_integrity, for tests
that need "this model file is installed" without real weights."""
from __future__ import annotations

import json
import struct
from pathlib import Path


def safetensors_bytes(tensor_bytes: int = 0, name: str = "w") -> bytes:
    """A whole safetensors file: a header and, when asked, one tensor's data."""
    header = {}
    if tensor_bytes:
        header[name] = {"dtype": "U8", "shape": [tensor_bytes], "data_offsets": [0, tensor_bytes]}
    raw = json.dumps(header).encode()
    return struct.pack("<Q", len(raw)) + raw + b"\0" * tensor_bytes


def gguf_bytes() -> bytes:
    """A whole GGUF v3 file with no metadata and no tensors."""
    return b"GGUF" + struct.pack("<IQQ", 3, 0, 0)


def onnx_bytes(graph_bytes: int = 4) -> bytes:
    """A whole ONNX file as the check reads it: ir_version, then one
    length-prefixed graph field."""
    assert graph_bytes < 128
    return b"\x08\x07" + b"\x3a" + bytes([graph_bytes]) + b"\0" * graph_bytes


def whisper_ggml_bytes(tensor_bytes: int = 16) -> bytes:
    """A whole whisper.cpp ggml file: hyperparameters, a 1-bin mel filter, a
    one-word vocabulary and one float32 tensor."""
    hparams = (1, 1500, 384, 6, 4, 448, 384, 6, 4, 80, 0)
    head = b"lmgg" + struct.pack("<11i", *hparams)
    mel = struct.pack("<2i", 80, 1) + b"\0" * (4 * 80)
    vocab = struct.pack("<i", 1) + struct.pack("<i", 1) + b"a"
    tensor = struct.pack("<3i", 1, 1, 0) + struct.pack("<i", tensor_bytes // 4) + b"w" + b"\0" * tensor_bytes
    return head + mel + vocab + tensor


def write_model_file(path: Path) -> Path:
    """Write a whole stand-in for the format ``path`` is named as."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix in (".safetensors", ".sft"):
        path.write_bytes(safetensors_bytes())
    elif suffix == ".gguf":
        path.write_bytes(gguf_bytes())
    elif suffix == ".onnx":
        path.write_bytes(onnx_bytes())
    else:
        path.write_bytes(b"x")
    return path
