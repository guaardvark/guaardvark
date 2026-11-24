"""Is a model weight file on disk whole, or the front part of a download?

``file_problem`` answers from the file's own structure, reading the header (or,
for a zip, the end record) and never the tensor data:

  .safetensors / .sft   8-byte header length + JSON header; the last tensor's
                        end must land exactly on the end of the file.
  .gguf                 key/value and tensor tables; the file must reach the
                        end of the last tensor.
  .onnx                 top-level protobuf fields; each must end inside the file.
  .pt .pth .ckpt .bin   a zip (torch.save's format): the central directory at
                        the end must be readable. A whisper.cpp ggml file:
                        every tensor must end inside the file.

Formats with no length of their own to compare against (legacy pickle
checkpoints, CTranslate2 and other raw .bin) are reported whole when they are
non-empty; ``check_kind`` returns None for them. A registry that knows such a
file's size as published records it with ``expect_size``, and the file is then
cut short when it is smaller than that.

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
_ONNX_SUFFIXES = (".onnx",)
_MAYBE_ZIP_SUFFIXES = (".pt", ".pth", ".ckpt", ".bin")

_CACHE_LIMIT = 4096
_cache: dict = {}
_cache_lock = threading.Lock()
# path -> size in bytes the registry that owns the file recorded for it.
_expected_sizes: dict = {}


class _Truncated(Exception):
    """The file ends before a structure its header declares."""


def _gb(n: int) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.2f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.1f} MB"
    return f"{n} bytes"


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


def _varint(buf: bytes, at: int):
    """(value, index after it), or (None, at) when ``buf`` ends inside it."""
    value = shift = 0
    while at < len(buf) and shift < 64:
        byte = buf[at]
        at += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, at
        shift += 7
    return None, at


def _onnx_problem(path: str, size: int) -> Optional[str]:
    # An ONNX file is one ModelProto message: a few top-level fields, the graph
    # (nearly all of the file) among them as a single length-prefixed field.
    with open(path, "rb") as f:
        pos = 0
        while pos < size:
            f.seek(pos)
            head = f.read(20)
            tag, at = _varint(head, 0)
            if tag is None:
                return "truncated: the file ends inside an ONNX field"
            field, wire = tag >> 3, tag & 7
            if field == 0 or wire not in (0, 1, 2, 5):
                return "corrupt header: not an ONNX model"
            if wire == 0:
                value, at = _varint(head, at)
                if value is None:
                    return "truncated: the file ends inside an ONNX field"
                pos += at
            elif wire == 2:
                length, at = _varint(head, at)
                if length is None:
                    return "truncated: the file ends inside an ONNX field"
                pos += at + length
            else:
                pos += at + (8 if wire == 1 else 4)
    if pos > size:
        return _cut_short(size, pos)
    return None


_WHISPER_GGML_MAGIC = b"lmgg"


def _whisper_ggml_problem(path: str, size: int) -> Optional[str]:
    """whisper.cpp's ggml layout: hyperparameters, mel filters, vocabulary, then
    tensors to the end of the file. Anything that does not read as that layout
    is left unjudged (None); only a tensor running past the end is a verdict."""
    with open(path, "rb") as f:
        def i32(count: int = 1):
            data = f.read(4 * count)
            if len(data) < 4 * count:
                raise _Truncated()
            return struct.unpack(f"<{count}i", data)

        try:
            f.seek(4)
            hparams = i32(11)
            n_vocab, n_mels = hparams[0], hparams[9]
            if not 0 < n_vocab < 1_000_000 or n_mels not in (80, 128):
                return None
            n_mel, n_fft = i32(2)
            if n_mel != n_mels or not 0 < n_fft < 100_000:
                return None
            f.seek(4 * n_mel * n_fft, os.SEEK_CUR)
            (words,) = i32()
            if not 0 < words < 1_000_000:
                return None
            for _ in range(words):
                (length,) = i32()
                if not 0 <= length < 4096:
                    return None
                f.seek(length, os.SEEK_CUR)
            if f.tell() > size:
                raise _Truncated()
            while f.tell() < size:
                n_dims, name_len, ttype = i32(3)
                per_block, block_bytes = _GGML_BLOCKS.get(ttype, (0, 0))
                if not 1 <= n_dims <= 4 or not 0 < name_len <= 256 or not per_block:
                    return None
                elements = 1
                for dim in i32(n_dims):
                    if dim <= 0:
                        return None
                    elements *= dim
                name = f.read(name_len)
                if len(name) < name_len:
                    raise _Truncated()
                if not name.isascii():
                    return None
                end = f.tell() + (elements // per_block) * block_bytes
                if end > size:
                    return _cut_short(size, end)
                f.seek(end)
        except _Truncated:
            return "truncated: the file ends inside a whisper ggml table"
    return None


def _bin_family(path: str) -> Optional[str]:
    with open(path, "rb") as f:
        magic = f.read(4)
    if magic == b"PK\x03\x04":
        return "zip"
    if magic == _WHISPER_GGML_MAGIC:
        return "ggml"
    return None


def check_kind(path: Union[str, Path]) -> Optional[str]:
    """The structure check ``file_problem`` applies to ``path``: "safetensors",
    "gguf", "onnx", "zip" or "ggml"; None when the format carries no length to
    check and the file is only tested for being non-empty."""
    key = os.fspath(path)
    suffix = os.path.splitext(key)[1].lower()
    if suffix in _SAFETENSORS_SUFFIXES:
        return "safetensors"
    if suffix in _GGUF_SUFFIXES:
        return "gguf"
    if suffix in _ONNX_SUFFIXES:
        return "onnx"
    if suffix in _MAYBE_ZIP_SUFFIXES:
        try:
            return _bin_family(key)
        except OSError:
            return None
    return None


def expect_size(path: Union[str, Path], nbytes: Optional[int]) -> None:
    """Record the size a registry publishes for ``path``. Used only for a file
    whose format has no structure check: one smaller than this is reported cut
    short. A larger or equal file is left alone, since a person may have
    dropped in another build of the same model."""
    if not nbytes or int(nbytes) <= 0:
        return
    key = os.fspath(path)
    with _cache_lock:
        if _expected_sizes.get(key) != int(nbytes):
            _expected_sizes[key] = int(nbytes)
            _cache.pop(key, None)


def _examine(path: str, size: int) -> Optional[str]:
    try:
        kind = check_kind(path)
        if kind is None:
            want = _expected_sizes.get(path)
            if want and size < want:
                return f"truncated: {_gb(size)} on disk, the published file is {_gb(want)}"
            return None
        if kind == "safetensors":
            return _safetensors_problem(path, size)
        if kind == "gguf":
            return _gguf_problem(path, size)
        if kind == "onnx":
            return _onnx_problem(path, size)
        if kind == "zip":
            return _zip_problem(path)
        if kind == "ggml":
            return _whisper_ggml_problem(path, size)
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
