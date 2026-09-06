"""Chat / MCP tools that start a music-video or Film Crew plan.

They create the project and kick analysis / screenwriting. They do not
approve cuts or start a GPU render — that stays a human gate in Studio.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)

_AUDIO_EXT = (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac")
_MUSIC_VIDEO_RE = re.compile(
    r"\b(generate|create|make|produce)\b.{0,40}\bmusic[\s-]?video\b",
    re.IGNORECASE,
)
_FILM_CREW_RE = re.compile(
    r"\b(start|run|create|make)\b.{0,40}\bfilm[\s-]?crew\b"
    r"|\b(film|produce)\b.{0,20}\bscript\b",
    re.IGNORECASE,
)


def wants_music_video(message: str) -> bool:
    return bool(message and _MUSIC_VIDEO_RE.search(message))


def wants_film_crew(message: str) -> bool:
    return bool(message and _FILM_CREW_RE.search(message))


_SLASH_MUSIC_VIDEO_RE = re.compile(r"^\s*/music-video\b", re.IGNORECASE)
_SLASH_FILM_CREW_RE = re.compile(r"^\s*/film-crew\b", re.IGNORECASE)


def is_music_video_request(message: str) -> bool:
    return bool(_SLASH_MUSIC_VIDEO_RE.match(message or "") or wants_music_video(message))


def is_film_crew_request(message: str) -> bool:
    return bool(_SLASH_FILM_CREW_RE.match(message or "") or wants_film_crew(message))


def parse_music_video_nl(message: str) -> dict:
    """Pull a song path/id and a style prompt out of a chat line. Pure."""
    text = (message or "").strip()
    text = re.sub(r"^\s*/music-video\b[:\s]*", "", text, flags=re.I)
    text = _MUSIC_VIDEO_RE.sub("", text)
    song = None
    for token in text.split():
        cleaned = token.strip(".,;:\"'")
        if cleaned.isdigit():
            song = cleaned
            break
        if cleaned.lower().endswith(_AUDIO_EXT) or "/" in cleaned:
            song = cleaned
            break
    style = text
    if song:
        style = style.replace(song, " ")
    style = re.sub(
        r"\b(for|from|of|using|with|to)\s+(this\s+)?(song|track|audio)\b[:\s]*",
        " ",
        style,
        flags=re.I,
    )
    style = " ".join(style.split()).strip(" ,.-")
    return {"song": song, "style_prompt": style or None}


def parse_film_crew_nl(message: str) -> dict:
    """Pull a script body (or path) out of a chat line. Pure."""
    text = (message or "").strip()
    text = re.sub(r"^\s*/film-crew\b[:\s]*", "", text, flags=re.I)
    text = _FILM_CREW_RE.sub("", text)
    text = re.sub(r"^\s*(with|:)\s*", "", text)
    script = " ".join(text.split()).strip()
    return {"script_text": script or None}


_OUTSIDE_DATA_DIRS = "must be inside the uploads or outputs directory or the install root"


def _local_media_path(ref: str):
    """``ref`` as a Path when it names a file under a directory this install owns.

    Returns (path, None), (None, error) when the path is outside those
    directories, or (None, None) when ``ref`` does not point at an existing
    file at all. Chat and MCP callers hand these tools arbitrary strings, so
    the same containment as output registration applies.
    """
    from backend.services.output_registration import registrable_path

    text = (ref or "").strip()
    if not text:
        return None, None
    candidate = Path(text).expanduser()
    inside = registrable_path(str(candidate))
    if inside is not None and inside.is_file():
        return inside, None
    if candidate.is_file():
        return None, f"{text} {_OUTSIDE_DATA_DIRS}"
    return None, None


def _script_body(script_text: str):
    """Return (text, None) or (None, error). A one-line path to a script file
    under the data directories is read; any other text is the script itself."""
    text = (script_text or "").strip()
    if not text:
        return None, "script_text is required"
    if "\n" not in text:
        path, err = _local_media_path(text)
        if err:
            return None, f"script file {err}"
        if path is not None:
            try:
                return path.read_text(encoding="utf-8"), None
            except OSError as e:
                return None, f"could not read script file: {e}"
    return text, None


def _document_from_song_ref(song: str):
    """Return (Document, None) or (None, error). Creates a row for a local file."""
    from backend.models import Document, db

    ref = (song or "").strip()
    if not ref:
        return None, "song is required (document id or path to an audio file)"
    if ref.isdigit():
        doc = db.session.get(Document, int(ref))
        if not doc:
            return None, f"song document {ref} not found"
        return doc, None
    path, err = _local_media_path(ref)
    if err:
        return None, f"song file {err}"
    if path is None:
        return None, f"song file not found: {ref}"
    resolved = str(path)
    existing = Document.query.filter_by(path=resolved).first()
    if existing:
        return existing, None
    doc = Document(
        filename=path.name,
        path=resolved,
        type=(path.suffix.lstrip(".") or "audio")[:50],
        size=path.stat().st_size,
        index_status="STORED",
    )
    db.session.add(doc)
    db.session.commit()
    return doc, None


class MusicVideoTool(BaseTool):
    """Start a beat-synced music-video plan. Does not approve or render clips."""

    name = "generate_music_video"
    description = (
        "Start a music-video project from a song and a visual style. Uploads or "
        "attaches the song, writes unique cut prompts, and stops at the approval "
        "gate — it does not spend GPU rendering clips. Use when the user asks to "
        "make a music video. Pass song as a document id or a path to an audio file."
    )
    parameters = {
        "song": ToolParameter(
            name="song",
            type="string",
            description="Document id or filesystem path of the song (mp3/wav/flac/ogg).",
            required=True,
        ),
        "style_prompt": ToolParameter(
            name="style_prompt",
            type="string",
            description="Visual style for the Director (mood, palette, movement).",
            required=True,
        ),
        "name": ToolParameter(
            name="name",
            type="string",
            description="Project name. Defaults to the song filename.",
            required=False,
        ),
        "i2v_model": ToolParameter(
            name="i2v_model",
            type="string",
            description="Optional I2V model id. Default: the active video model.",
            required=False,
        ),
    }

    def execute(self, song: str, style_prompt: str, name: str | None = None,
                i2v_model: str | None = None, **kwargs) -> ToolResult:
        style_prompt = (style_prompt or "").strip()
        if not style_prompt:
            return ToolResult(success=False, error="style_prompt is required")
        try:
            from backend.models import db
            from backend.services.music_video_service import MusicVideoService
            from backend.api.music_video_api import _resolve_song

            doc, err = _document_from_song_ref(song)
            if err:
                return ToolResult(success=False, error=err)
            song_path = _resolve_song(doc.id)
            if not song_path:
                return ToolResult(success=False, error=f"song document {doc.id} is not on disk")

            settings = {}
            if (i2v_model or "").strip():
                settings["i2v_model"] = i2v_model.strip()
            from backend.services.video_model_registry import resolve_active_video_model
            picked, resolve_err = resolve_active_video_model(
                "i2v", settings.get("i2v_model"), surface="music-video",
            )
            if resolve_err:
                return ToolResult(success=False, error=resolve_err)
            settings["i2v_model"] = picked

            title = (name or "").strip() or Path(doc.filename).stem
            svc = MusicVideoService(db.session)
            mv = svc.create(
                name=title,
                song_document_id=doc.id,
                song_path=song_path,
                style_prompt=style_prompt,
                project_id=None,
                settings=settings,
            )
            if svc.advance_if_predecessor(mv.id, expected_predecessor="draft"):
                try:
                    svc.dispatch_agent(mv.id, "analyzer")
                except Exception as e:  # noqa: BLE001
                    logger.warning("music-video analyzer dispatch failed for %s: %s", mv.id, e)
                db.session.refresh(mv)

            studio = f"/music-video"
            return ToolResult(
                success=True,
                output="\n".join([
                    f"Music video '{mv.name}' created (id {mv.id}, stage: {mv.current_stage}).",
                    "Analysis is running. Approve the cut plan in Studio before any clip renders.",
                    f"Open Music Video: {studio}",
                ]),
                metadata={
                    "music_video_id": mv.id,
                    "stage": mv.current_stage,
                    "studio_url": studio,
                    "i2v_model": settings.get("i2v_model"),
                    "approved": False,
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("generate_music_video failed")
            return ToolResult(success=False, error=str(e))


class FilmCrewTool(BaseTool):
    """Start a Film Crew production from a script. Does not render shots."""

    name = "start_film_crew"
    description = (
        "Start a five-role Film Crew production from a screenplay. The screenwriter "
        "begins at once; casting, storyboards and GPU renders wait for you in Studio. "
        "Use when the user asks to film a script or start the film crew."
    )
    parameters = {
        "script_text": ToolParameter(
            name="script_text",
            type="string",
            description="Screenplay or scene list (plain text).",
            required=True,
        ),
        "name": ToolParameter(
            name="name",
            type="string",
            description="Production name. Defaults to the first line of the script.",
            required=False,
        ),
        "video_model": ToolParameter(
            name="video_model",
            type="string",
            description="Optional I2V / scene model id. Default: the active video model.",
            required=False,
        ),
    }

    def execute(self, script_text: str, name: str | None = None,
                video_model: str | None = None, **kwargs) -> ToolResult:
        script_text, script_err = _script_body(script_text)
        if script_err:
            return ToolResult(success=False, error=script_err)
        try:
            from backend.models import db
            from backend.services.production_service import ProductionService
            from backend.services.video_model_registry import (
                resolve_active_video_model, VIDEO_MODEL_REGISTRY, model_capabilities,
            )

            settings = {}
            explicit = (video_model or "").strip() or None
            if explicit:
                if explicit not in VIDEO_MODEL_REGISTRY or not model_capabilities(explicit):
                    return ToolResult(success=False, error=f"video_model '{explicit}' is not a video model")
            picked, resolve_err = resolve_active_video_model("i2v", explicit, surface="film-crew")
            if resolve_err:
                return ToolResult(success=False, error=resolve_err)
            settings["video_model"] = picked

            first = next((ln.strip() for ln in script_text.splitlines() if ln.strip()), "Film Crew")
            title = (name or "").strip() or first[:80]
            svc = ProductionService(db.session)
            prod = svc.create(
                name=title, script_text=script_text, project_id=None, settings=settings,
            )
            if svc.advance_if_predecessor(prod.id, expected_predecessor="draft"):
                try:
                    svc.dispatch_agent(prod.id, "screenwriter")
                except Exception as e:  # noqa: BLE001
                    logger.warning("film-crew screenwriter dispatch failed for %s: %s", prod.id, e)
                db.session.refresh(prod)

            studio = "/film-crew"
            return ToolResult(
                success=True,
                output="\n".join([
                    f"Film Crew '{prod.name}' created (id {prod.id}, stage: {prod.current_stage}).",
                    "The screenwriter is running. Casting, storyboards and renders wait in Studio.",
                    f"Open Film Crew: {studio}",
                ]),
                metadata={
                    "production_id": prod.id,
                    "stage": prod.current_stage,
                    "studio_url": studio,
                    "video_model": picked,
                    "rendered": False,
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("start_film_crew failed")
            return ToolResult(success=False, error=str(e))
