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


def write_model_file(path: Path) -> Path:
    """Write a whole stand-in for the format ``path`` is named as."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix in (".safetensors", ".sft"):
        path.write_bytes(safetensors_bytes())
    elif suffix == ".gguf":
        path.write_bytes(gguf_bytes())
    else:
        path.write_bytes(b"x")
    return path
