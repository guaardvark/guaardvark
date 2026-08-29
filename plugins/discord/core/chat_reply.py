"""Shared helpers for turning a unified-chat result into Discord messages."""
import base64
import io
import logging
import os
from urllib.parse import urlparse

import discord

logger = logging.getLogger("discord_bot")

DISCORD_MAX_LENGTH = 2000
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_OUTBOUND_BYTES = 8 * 1024 * 1024
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov")


def user_session_id(user_id: int) -> str:
    return f"discord_user_{user_id}"


def channel_session_id(channel_id: int) -> str:
    return f"discord_channel_{channel_id}"


def split_message(text: str, max_length: int = DISCORD_MAX_LENGTH) -> list[str]:
    """Split a long message into chunks that fit Discord's limit."""
    if text is None:
        return [""]
    if len(text) <= max_length:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_length)
        if split_at == -1:
            split_at = max_length
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    return chunks


def is_image_attachment(attachment) -> bool:
    content_type = (getattr(attachment, "content_type", None) or "").lower()
    name = (getattr(attachment, "filename", None) or "").lower()
    if content_type.startswith("image/"):
        return True
    return name.endswith(IMAGE_EXTENSIONS)


def first_image_attachment(attachments) -> object | None:
    for att in attachments or []:
        if is_image_attachment(att):
            return att
    return None


async def attachment_to_b64(attachment) -> str | None:
    """Read a Discord attachment as base64, or None if missing/too large."""
    if attachment is None:
        return None
    size = getattr(attachment, "size", 0) or 0
    if size > MAX_IMAGE_BYTES:
        logger.warning("Skipping oversized attachment %s (%s bytes)", getattr(attachment, "filename", "?"), size)
        return None
    data = await attachment.read()
    if not data or len(data) > MAX_IMAGE_BYTES:
        return None
    return base64.b64encode(data).decode("ascii")


def _filename_for_media(url: str, media_type: str) -> str:
    name = os.path.basename(urlparse(url).path) if url else ""
    name = name.split("?")[0]
    if name:
        return name
    if media_type == "video":
        return "video.mp4"
    return "image.png"


async def files_from_generated(api, images: list) -> list[discord.File]:
    """Download generated media from the backend as Discord file attachments."""
    files: list[discord.File] = []
    for img in images or []:
        if len(files) >= 10:
            break
        url = (img or {}).get("url") or (img or {}).get("image_url")
        if not url:
            continue
        try:
            raw = await api.fetch_by_url(url)
        except Exception:
            logger.warning("Failed to fetch generated media %s", url, exc_info=True)
            continue
        if not raw or len(raw) > MAX_OUTBOUND_BYTES:
            continue
        media_type = (img or {}).get("type") or "image"
        name = _filename_for_media(url, media_type)
        files.append(discord.File(io.BytesIO(raw), filename=name))
    return files


async def send_chunks(send_fn, text: str, files: list | None = None):
    """Send text in Discord-sized chunks; attach files to the first message.

    ``send_fn`` is ``async (content, *, files=None) -> message``.
    Responses over 4000 chars go as a markdown file instead of many chunks.
    """
    files = files or []
    if text and len(text) > 4000:
        md = discord.File(io.BytesIO(text.encode("utf-8")), filename="response.md")
        await send_fn(
            f"Response too long ({len(text)} chars). See attached file.",
            files=[md, *files][:10],
        )
        return
    chunks = split_message(text or "") if text else [""]
    if not chunks:
        chunks = [""]
    first_files = files or None
    for i, chunk in enumerate(chunks):
        kwargs = {}
        if i == 0 and first_files:
            kwargs["files"] = first_files
        content = chunk or ("Here's what Guaardvark made." if first_files else "")
        if not content and not kwargs:
            continue
        await send_fn(content or " ", **kwargs)
