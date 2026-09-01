"""Resolve Document rows to uploadable files and validate them against a target.

``validate_against`` backs both the hard reject on POST /publish and the live
preflight the compose modal calls as you type, so the rules cannot drift apart.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
from typing import Any, Dict, Iterable, List, Optional

from backend.services.connections.base import Capabilities, MediaItem

logger = logging.getLogger(__name__)


class MediaResolveError(Exception):
    """A requested document could not be turned into an uploadable file."""


def _document_abs_path(doc) -> Optional[str]:
    """Absolute path for a Document, tolerating the several shapes of `path`."""
    from backend import config

    raw = (doc.path or "").strip()
    if not raw:
        return None
    if os.path.isabs(raw) and os.path.exists(raw):
        return raw
    candidate = os.path.join(config.UPLOAD_DIR, raw)
    if os.path.exists(candidate):
        return candidate
    return None


def resolve_media(document_ids: Iterable[int]) -> List[MediaItem]:
    """Turn Document ids into MediaItems. Requires an app context."""
    from backend.models import Document

    items: List[MediaItem] = []
    for doc_id in document_ids or []:
        doc = Document.query.get(doc_id)
        if doc is None:
            raise MediaResolveError(f"Document {doc_id} not found.")
        path = _document_abs_path(doc)
        if not path:
            raise MediaResolveError(f"File for document {doc_id} is missing on disk.")

        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        alt_text = None
        attribution = None
        has_audio = False
        if doc.file_metadata:
            try:
                meta = json.loads(doc.file_metadata) if isinstance(doc.file_metadata, str) else dict(doc.file_metadata)
                alt_text = meta.get("original_prompt") or None
                attribution = meta.get("attribution") or None
                has_audio = bool(meta.get("has_audio"))
            except (ValueError, TypeError):
                pass

        item = MediaItem(
            path=path,
            mime=mime,
            bytes=os.path.getsize(path),
            document_id=doc.id,
            alt_text=alt_text,
            attribution=attribution,
            has_audio=has_audio,
        )
        if item.kind == "video":
            item.duration_s = probe_duration(path)
        items.append(item)
    return items


def probe_duration(path: str) -> Optional[float]:
    """Seconds of media at ``path`` via ffprobe, or None when unavailable."""
    try:
        from backend.services.swarm.clients import FfmpegRunner
        value = FfmpegRunner().probe_duration(path)
        return float(value) if value else None
    except Exception:  # noqa: BLE001 — a missing ffprobe just skips the duration check
        return None


def disclosure_line(items: List[MediaItem]) -> Optional[str]:
    """The machine-generated disclosure a post carries when its media names a
    model whose license asks to be credited. Plain text in the post body; no
    platform flag is set and nothing is sent anywhere else."""
    names = []
    for item in items:
        if item.attribution and item.attribution not in names:
            names.append(item.attribution)
    if not names:
        return None
    return f"Generated with {', '.join(names)} on Guaardvark."


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n / 1:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def validate_against(
    caps: Capabilities,
    media: List[MediaItem],
    body: str = "",
    title: Optional[str] = None,
    visibility: Optional[str] = None,
) -> List[str]:
    """Return human-readable violations. An empty list means the post is valid."""
    problems: List[str] = []

    text = body or ""
    if caps.max_text_chars is not None and len(text) > caps.max_text_chars:
        problems.append(
            f"Text is {len(text)} characters; the limit is {caps.max_text_chars}."
        )
    if not caps.text and text.strip():
        problems.append("This target does not accept text.")

    images = [m for m in media if m.kind == "image"]
    videos = [m for m in media if m.kind == "video"]
    audio = [m for m in media if m.kind == "audio"]

    if caps.requires_media and not media:
        problems.append("This target requires at least one image or video.")
    if images and not caps.images:
        problems.append("This target does not accept images.")
    if videos and not caps.video:
        problems.append("This target does not accept video.")
    if caps.video and caps.max_video_seconds is not None:
        for item in videos:
            if item.duration_s and item.duration_s > caps.max_video_seconds:
                problems.append(
                    f"{os.path.basename(item.path)} runs {item.duration_s:.0f}s; "
                    f"the limit is {caps.max_video_seconds:.0f}s."
                )
    if audio and not caps.audio:
        problems.append("This target does not accept audio.")

    if caps.images and len(images) > caps.max_images:
        problems.append(
            f"{len(images)} images selected; the limit is {caps.max_images}."
        )
    if caps.max_image_bytes is not None:
        for item in images:
            if item.bytes > caps.max_image_bytes:
                problems.append(
                    f"{os.path.basename(item.path)} is {_human_bytes(item.bytes)}; "
                    f"the image limit is {_human_bytes(caps.max_image_bytes)}."
                )
    if caps.max_video_bytes is not None:
        for item in videos:
            if item.bytes > caps.max_video_bytes:
                problems.append(
                    f"{os.path.basename(item.path)} is {_human_bytes(item.bytes)}; "
                    f"the video limit is {_human_bytes(caps.max_video_bytes)}."
                )

    if caps.accepted_mime:
        for item in media:
            if item.mime not in caps.accepted_mime:
                problems.append(
                    f"{os.path.basename(item.path)} is {item.mime}, which this "
                    "target does not accept."
                )

    if title and not caps.supports_title:
        problems.append("This target does not support a title.")

    if visibility and caps.visibilities and visibility not in caps.visibilities:
        allowed = ", ".join(caps.visibilities)
        problems.append(f"Visibility '{visibility}' is not supported (allowed: {allowed}).")

    if not text.strip() and not media:
        problems.append("Nothing to post — add text or media.")

    return problems


def media_refs_json(media: List[MediaItem]) -> str:
    return json.dumps([m.to_dict() for m in media])


def media_from_refs(raw: Optional[str]) -> List[MediaItem]:
    """Rehydrate MediaItems stored on a PublishRecord."""
    if not raw:
        return []
    try:
        entries: List[Dict[str, Any]] = json.loads(raw)
    except (ValueError, TypeError):
        return []
    items: List[MediaItem] = []
    for e in entries:
        if not isinstance(e, dict) or not e.get("path"):
            continue
        items.append(
            MediaItem(
                path=e["path"],
                mime=e.get("mime") or "application/octet-stream",
                bytes=int(e.get("bytes") or 0),
                document_id=e.get("document_id"),
                role=e.get("role") or "primary",
                alt_text=e.get("alt_text"),
            )
        )
    return items
