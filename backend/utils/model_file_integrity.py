"""Is a model weight file on disk whole, or the front part of a download?

``file_problem`` answers from the file's own structure, reading the header (or,
for a zip, the end record) and never the tensor data:

  .safetensors / .sft   8-byte header length + JSON header; the last tensor's
                        end must land exactly on the end of the file.
  .gguf                 key/value and tensor tables; the file must reach the
                        end of the last tensor.
  .pt .pth .ckpt .bin   only when the file is a zip (torch.save's format): the
                        central directory at the end must be readable.

Formats with no length of their own to compare against (ONNX, legacy pickle
checkpoints, raw .bin) are reported whole when they are non-empty.

Verdicts are cached per path and dropped when the file's mtime or size changes.
Standard library only, so plugin services can import it.
"""

import json
import os
import struct
import threading
import zipfile
from pathlib import Path
from typing import Optional, Union

MISSING = "missing"
EMPTY = "empty"

# safetensors refuses a larger header (its MAX_HEADER_SIZE).
_MAX_SAFETENSORS_HEADER = 100_000_000

_SAFETENSORS_SUFFIXES = (".safetensors", ".sft")
_GGUF_SUFFIXES = (".gguf",)
_MAYBE_ZIP_SUFFIXES = (".pt", ".pth", ".ckpt", ".bin")

_CACHE_LIMIT = 4096
_cache: dict = {}
_cache_lock = threading.Lock()


class _Truncated(Exception):
    """The file ends before a structure its header declares."""


def _gb(n: int) -> str:
    return f"{n / 1e9:.2f} GB"


def _cut_short(have: int, want: int) -> str:
    return f"truncated: {_gb(have)} on disk, the file's header describes {_gb(want)}"


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
        return _cut_short(size, expected)
    if size > expected:
        return (
            f"corrupt: {size - expected} bytes after the last tensor "
            "(safetensors rejects a file its header does not fully cover)"
        )
    return None


# GGUF metadata value types -> byte width of the fixed-size ones.
_GGUF_FIXED = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
_GGUF_STRING, _GGUF_ARRAY, _GGUF_UINT32 = 8, 9, 4
# ggml tensor type -> (elements per block, bytes per block). A type missing here
# only loosens the check to "the file reaches that tensor's first byte".
_GGML_BLOCKS = {
    0: (1, 4), 1: (1, 2), 2: (32, 18), 3: (32, 20), 6: (32, 22), 7: (32, 24),
    8: (32, 34), 9: (32, 36), 10: (256, 84), 11: (256, 110), 12: (256, 144),
    13: (256, 176), 14: (256, 210), 15: (256, 292), 24: (1, 1), 25: (1, 2),
    26: (1, 4), 27: (1, 8), 28: (1, 8), 30: (1, 2),
}


def _gguf_problem(path: str, size: int) -> Optional[str]:
    with open(path, "rb") as f:
        def take(n: int) -> bytes:
            data = f.read(n)
            if len(data) < n:
                raise _Truncated()
            return data

        def u32() -> int:
            return struct.unpack("<I", take(4))[0]

        def u64() -> int:
            return struct.unpack("<Q", take(8))[0]

        def skip(n: int) -> None:
            if n < 0 or f.tell() + n > size:
                raise _Truncated()
            f.seek(n, os.SEEK_CUR)

        def skip_value(vtype: int) -> None:
            if vtype in _GGUF_FIXED:
                skip(_GGUF_FIXED[vtype])
            elif vtype == _GGUF_STRING:
                skip(u64())
            elif vtype == _GGUF_ARRAY:
                etype, count = u32(), u64()
                if etype in _GGUF_FIXED:
                    skip(_GGUF_FIXED[etype] * count)
                elif etype in (_GGUF_STRING, _GGUF_ARRAY):
                    if count > size:
                        raise _Truncated()
                    for _ in range(count):
                        skip_value(etype)
                else:
                    raise ValueError("unknown GGUF array type")
            else:
                raise ValueError("unknown GGUF value type")

        try:
            if take(4) != b"GGUF":
                return "corrupt header: not a GGUF file"
            version = u32()
            if version not in (2, 3):
                # v1 and big-endian files use other field widths; left unchecked.
                return None
            tensor_count, kv_count = u64(), u64()
            if tensor_count > size or kv_count > size:
                return "corrupt header: GGUF table counts exceed the file size"
            alignment = 32
            for _ in range(kv_count):
                key = take(u64())
                vtype = u32()
                if key == b"general.alignment" and vtype == _GGUF_UINT32:
                    alignment = u32() or 32
                else:
                    skip_value(vtype)
            data_len = 0
            for _ in range(tensor_count):
                skip(u64())
                n_dims = u32()
                if n_dims > 8:
                    return "corrupt header: GGUF tensor with too many dimensions"
                elements = 1
                for _ in range(n_dims):
                    elements *= u64()
                ttype, offset = u32(), u64()
                per_block, block_bytes = _GGML_BLOCKS.get(ttype, (0, 0))
                nbytes = (elements // per_block) * block_bytes if per_block else 1
                data_len = max(data_len, offset + nbytes)
            # Tensor data starts on the next alignment boundary; a file with
            # no tensors ends where its tables do.
            data_start = -(-f.tell() // alignment) * alignment if tensor_count else f.tell()
        except _Truncated:
            return "truncated: the file ends inside its GGUF header"
        except (ValueError, struct.error):
            return "corrupt header: the GGUF header cannot be read"
    expected = data_start + data_len
    if size < expected:
        return _cut_short(size, expected)
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


def _examine(path: str, size: int) -> Optional[str]:
    suffix = os.path.splitext(path)[1].lower()
    try:
        if suffix in _SAFETENSORS_SUFFIXES:
            return _safetensors_problem(path, size)
        if suffix in _GGUF_SUFFIXES:
            return _gguf_problem(path, size)
        if suffix in _MAYBE_ZIP_SUFFIXES:
            return _zip_problem(path)
    except OSError as e:
        return f"unreadable: {e.strerror or e}"
    return None


def file_problem(path: Union[str, Path]) -> Optional[str]:
    """Why ``path`` cannot be loaded as a whole model file, or None when it can.

    ``MISSING`` when there is no such file, ``EMPTY`` for a zero-byte one,
    otherwise a sentence starting "truncated", "corrupt" or "unreadable".
    """
    key = os.fspath(path)
    try:
        st = os.stat(key)
    except OSError:
        return MISSING
    if not os.path.isfile(key):
        return MISSING
    if st.st_size == 0:
        return EMPTY
    stamp = (st.st_mtime_ns, st.st_size)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
    problem = _examine(key, st.st_size)
    with _cache_lock:
        if len(_cache) >= _CACHE_LIMIT:
            _cache.clear()
        _cache[key] = (stamp, problem)
    return problem


def file_complete(path: Union[str, Path]) -> bool:
    """True when ``path`` is on disk, non-empty and passes its format's check."""
    return file_problem(path) is None


def file_incomplete(path: Union[str, Path]) -> Optional[str]:
    """The problem with a file that is on disk but not whole; None when it is
    whole or not there at all."""
    problem = file_problem(path)
    return None if problem in (None, MISSING) else problem
