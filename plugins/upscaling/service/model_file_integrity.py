"""Is a model weight file on disk whole, or the front part of a download?

The same verdicts and wording as backend/utils/model_file_integrity.py for the
formats an upscaler ships in (sidecars do not import the backend):

  .pt .pth .ckpt .bin   only when the file is a zip (torch.save's format): the
                        central directory at the end must be readable.
  .safetensors / .sft   8-byte header length + JSON header; the last tensor's
                        end must land exactly on the end of the file.

A legacy pickle checkpoint carries no length to compare against and is reported
whole when it is non-empty. Standard library only; reads the header or the zip
end record, never the tensor data.
"""

import json
import os
import struct
import zipfile
from pathlib import Path
from typing import Optional, Union

MISSING = "missing"
EMPTY = "empty"

# safetensors refuses a larger header (its MAX_HEADER_SIZE).
_MAX_SAFETENSORS_HEADER = 100_000_000

_SAFETENSORS_SUFFIXES = (".safetensors", ".sft")
_MAYBE_ZIP_SUFFIXES = (".pt", ".pth", ".ckpt", ".bin")


def _gb(n: int) -> str:
    return f"{n / 1e9:.2f} GB"


def _safetensors_problem(path: str, size: int) -> Optional[str]:
    with open(path, "rb") as f:
        head = f.read(8)
        if len(head) < 8:
            return "truncated: shorter than a safetensors header"
        (header_len,) = struct.unpack("<Q", head)
        if header_len == 0 or header_len > _MAX_SAFETENSORS_HEADER:
            return "corrupt header: not a safetensors file"
        if 8 + header_len > size:
            return "truncated: the file ends inside its header"
        raw = f.read(header_len)
    try:
        header = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return "corrupt header: the safetensors header is not JSON"
    if not isinstance(header, dict):
        return "corrupt header: the safetensors header is not a table of tensors"
    data_end = 0
    for name, info in header.items():
        if name == "__metadata__":
            continue
        try:
            end = int(info["data_offsets"][1])
        except (TypeError, KeyError, IndexError, ValueError):
            return f"corrupt header: tensor {name!r} has no data offsets"
        data_end = max(data_end, end)
    expected = 8 + header_len + data_end
    if size < expected:
        return f"truncated: {_gb(size)} on disk, the file's header describes {_gb(expected)}"
    if size > expected:
        return (
            f"corrupt: {size - expected} bytes after the last tensor "
            "(safetensors rejects a file its header does not fully cover)"
        )
    return None


def _zip_problem(path: str) -> Optional[str]:
    with open(path, "rb") as f:
        if f.read(4) != b"PK\x03\x04":
            return None
    try:
        with zipfile.ZipFile(path):
            return None
    except (zipfile.BadZipFile, OSError, ValueError):
        return "truncated: the checkpoint archive has no readable end record"


def file_problem(path: Union[str, Path]) -> Optional[str]:
    """Why ``path`` cannot be loaded as a whole model file, or None when it can.

    ``MISSING`` when there is no such file, ``EMPTY`` for a zero-byte one,
    otherwise a sentence starting "truncated", "corrupt" or "unreadable".
    """
    key = os.fspath(path)
    try:
        size = os.stat(key).st_size
    except OSError:
        return MISSING
    if not os.path.isfile(key):
        return MISSING
    if size == 0:
        return EMPTY
    suffix = os.path.splitext(key)[1].lower()
    try:
        if suffix in _SAFETENSORS_SUFFIXES:
            return _safetensors_problem(key, size)
        if suffix in _MAYBE_ZIP_SUFFIXES:
            return _zip_problem(key)
    except OSError as e:
        return f"unreadable: {e.strerror or e}"
    return None
