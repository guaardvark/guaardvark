"""Durable consent records for likeness references.

A face photo may only drive ``generate_identity`` when the person using it has
confirmed they have the right to that likeness. The confirmation is stored,
not passed: a ``.consent`` sidecar next to the reference image, the same
convention the voice-clone reference clips use (``audio_foundry_api``), plus a
content-hash copy under ``OUTPUT_DIR/consent/`` so the same photo attached
again in a later chat turn (the chat engine writes each attachment to a fresh
temp path) is recognised without asking twice.

Only the consent step writes a record: the chat approval card, or a future UI
upload route. Tools read it; MCP and CLI callers cannot substitute a flag.

The reference path arrives as a tool argument, so it is chosen by the caller —
a model following a conversation, not a person filling in a form. Two limits
follow from that, and they are the reason this module does not simply open what
it is handed:

* **A sidecar is only written inside a directory this install owns.** Writing
  ``<path>.consent`` beside an arbitrary path would let a caller drop a file
  anywhere the process can write. An image living outside those directories —
  a photo the user pointed at by typing its path — is still consented, by its
  content-hash record under ``OUTPUT_DIR/consent/``, which is somewhere this
  install already owns.
* **Only regular image files are read.** A consent record has no business
  reading, hashing and indexing a private key because a prompt named one, and
  a path naming a FIFO would otherwise hang the request on ``open``.

The *location* of the image stays unrestricted on purpose: "your own photo,
wherever you keep it" is the feature, so no containment root is applied to the
read. Nothing is written outside the install to serve it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from backend.utils.path_guard import PathEscapesRoot, contained_path

logger = logging.getLogger(__name__)

SIDECAR_SUFFIX = ".consent"

# Leading bytes of the raster formats a reference photo arrives as. This is a
# guard, not validation: a truncated PNG still hashes fine and still counts.
# It exists to refuse a path that is not a picture at all.
_IMAGE_SIGNATURES = (
    b"\x89PNG\r\n\x1a\n",   # PNG
    b"\xff\xd8\xff",          # JPEG
    b"GIF87a",
    b"GIF89a",
    b"BM",                     # BMP
    b"II*\x00",                # TIFF, little-endian
    b"MM\x00*",                # TIFF, big-endian
)


def _looks_like_an_image(head: bytes) -> bool:
    if any(head.startswith(sig) for sig in _IMAGE_SIGNATURES):
        return True
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return True
    if head[4:8] == b"ftyp":  # ISO-BMFF: HEIC, HEIF, AVIF
        return True
    return False


def _is_image_file(path: str) -> bool:
    """True when the first bytes on disk are an image signature.

    Only a regular file is read. The caller chose this path, so it can name a
    FIFO — whose ``open`` blocks until a writer appears, hanging the request —
    or a device node. ``O_NONBLOCK`` returns instead of waiting, and the
    ``fstat`` is on the descriptor actually opened, so the check cannot be
    raced by swapping the path after a separate ``stat``.
    """
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        return _looks_like_an_image(os.read(fd, 16))
    except OSError:
        return False
    finally:
        if fd is not None:
            os.close(fd)


def _app_roots() -> tuple[str, ...]:
    """The directories this install owns. A sidecar is written inside one or not
    at all."""
    try:
        from backend.config import OUTPUT_DIR, STORAGE_DIR, UPLOAD_DIR
        roots = (STORAGE_DIR, OUTPUT_DIR, UPLOAD_DIR)
    except Exception:
        roots = ("data",)
    return tuple(os.path.abspath(r) for r in roots if r)


def owned_sidecar_path(image_path: str) -> Optional[str]:
    """``<image>.consent`` when the image sits inside a directory this install
    owns, else ``None`` — the caller then relies on the content-hash record."""
    if not image_path:
        return None
    wanted = os.path.abspath(image_path) + SIDECAR_SUFFIX
    for root in _app_roots():
        try:
            return contained_path(root, wanted)
        except PathEscapesRoot:
            continue
    return None


def sidecar_path(image_path: str) -> str:
    """The sidecar naming convention, ``<image>.consent``.

    Naming only. Do not open the result: the path is unguarded, and a caller
    supplied the image path. Use :func:`owned_sidecar_path`, which returns
    ``None`` rather than a path outside this install.
    """
    return f"{image_path}{SIDECAR_SUFFIX}"


def _hash_dir() -> str:
    try:
        from backend.config import OUTPUT_DIR
    except Exception:
        OUTPUT_DIR = "."
    return os.path.join(OUTPUT_DIR, "consent")


def content_hash(image_path: str) -> Optional[str]:
    """SHA-256 of the file bytes; None when it cannot be read or is not an image.

    The digest is over the whole file, unchanged, so records written before the
    signature guard existed still match.
    """
    if not _is_image_file(image_path):
        return None
    try:
        h = hashlib.sha256()
        with open(image_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _hash_record_path(digest: str) -> str:
    # The digest is hex and cannot escape, but the guard says so at the sink.
    return contained_path(_hash_dir(), f"{digest}{SIDECAR_SUFFIX}")


def _read_record(path: str, image_path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        return json.loads(text) if text else {"path": image_path}
    except (OSError, ValueError):
        # An empty or hand-made sidecar still counts as consent, as it does for
        # voice clips; only its details are unknown.
        return {"path": image_path}


def consent_record(image_path: str) -> Optional[Dict[str, Any]]:
    """The stored record for this image, by sidecar first, then by content hash.

    The sidecar is only consulted where one could have been written; an image
    outside this install's directories is answered by its hash record alone.
    """
    if not image_path:
        return None
    sidecar = owned_sidecar_path(image_path)
    if sidecar and os.path.isfile(sidecar):
        return _read_record(sidecar, image_path)
    digest = content_hash(image_path)
    if digest:
        by_hash = _hash_record_path(digest)
        if os.path.isfile(by_hash):
            return _read_record(by_hash, image_path)
    return None


def has_consent(image_path: str) -> bool:
    """True when a consent record exists for this image (sidecar or content hash)."""
    return consent_record(image_path) is not None


def record_consent(image_path: str, source: str, *, session_id: Optional[str] = None,
                   note: Optional[str] = None) -> Dict[str, Any]:
    """Write the record (who/when/how) as JSON to the sidecar and the hash copy.

    ``source`` names the step that obtained the confirmation, for example
    ``chat_approval`` or ``ui_upload``. Returns the record written.
    """
    if not image_path or not os.path.isfile(image_path):
        raise FileNotFoundError(f"reference image not found: {image_path}")
    if not _is_image_file(image_path):
        raise ValueError(f"reference is not an image file: {image_path}")
    digest = content_hash(image_path)
    record: Dict[str, Any] = {
        "path": os.path.abspath(image_path),
        "sha256": digest,
        "source": source,
        "session_id": session_id,
        "note": note,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    payload = json.dumps(record, indent=2)
    stored = False
    sidecar = owned_sidecar_path(image_path)
    if sidecar:
        with open(sidecar, "w", encoding="utf-8") as f:
            f.write(payload)
        stored = True
    else:
        logger.info(
            "consent sidecar not written for %s: outside this install's directories; "
            "the content-hash record covers it instead",
            image_path,
        )
    if digest:
        try:
            os.makedirs(_hash_dir(), exist_ok=True)
            with open(_hash_record_path(digest), "w", encoding="utf-8") as f:
                f.write(payload)
            stored = True
        except OSError as exc:
            logger.warning("consent hash record not written for %s: %s", image_path, exc)
    if not stored:
        raise OSError(f"no consent record could be stored for {image_path}")
    return record
