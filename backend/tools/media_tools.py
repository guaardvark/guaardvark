#!/usr/bin/env python3
"""
Media Player Tools
Tools for controlling music playback, volume, and checking current track info.
"""

import logging

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.services.media_player_service import get_media_service, MEDIA_CONTROL_ENABLED

logger = logging.getLogger(__name__)


def _disabled_result():
    return ToolResult(
        success=False,
        error="Media control disabled. Set GUAARDVARK_MEDIA_CONTROL=true to enable."
    )


class MediaPlayTool(BaseTool):
    """Play music by searching for songs/artists/albums, or resume playback."""

    name = "media_play"
    description = (
        "Play music by searching for songs, artists, albums, or genres. "
        "If user says 'play some music' or similar generic request without a specific artist/song, "
        "use query='music' to play all music. "
        "If no query is given at all, resumes current playback. "
        "Launches VLC with matching music files from the configured music directory."
    )
    parameters = {
        "query": ToolParameter(
            name="query", type="string", required=False,
            description="Search query for music (e.g. 'Alice in Chains', 'jazz'). "
                        "Use 'music' for generic 'play some music' requests. "
                        "Omit entirely to resume paused playback."
        ),
        "shuffle": ToolParameter(
            name="shuffle", type="bool", required=False,
            description="Shuffle the playlist", default=False
        ),
        "directory": ToolParameter(
            name="directory", type="string", required=False,
            description="Play all music in a specific directory path"
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        if not MEDIA_CONTROL_ENABLED:
            return _disabled_result()

        service = get_media_service()
        query = kwargs.get("query", "").strip() if kwargs.get("query") else ""
        shuffle = kwargs.get("shuffle", False)
        directory = kwargs.get("directory", "").strip() if kwargs.get("directory") else ""

        if directory:
            result = service.launch_vlc(directory=directory, shuffle=shuffle)
        elif query:
            result = service.play_music(query, shuffle=shuffle)
        else:
            # Resume playback
            result = service.play()

        if result.get("success"):
            output = result.get("action", "playing")
            if result.get("file_count"):
                output += f" ({result['file_count']} tracks)"
            if result.get("query"):
                output += f" matching '{result['query']}'"
            if result.get("shuffle"):
                output += " (shuffled)"
            return ToolResult(success=True, output=output, metadata=result)
        else:
            return ToolResult(success=False, error=result.get("error", "Unknown error"))


class MediaControlTool(BaseTool):
    """Control media playback: pause, stop, next, previous, toggle."""

    name = "media_control"
    description = (
        "Control media playback. Actions: pause, stop, next (skip to next track), "
        "previous (go back), toggle (play/pause toggle)."
    )
    parameters = {
        "action": ToolParameter(
            name="action", type="string", required=True,
            description="Action to perform: pause, stop, next, previous, toggle"
        ),
        "player": ToolParameter(
            name="player", type="string", required=False,
            description="Specific player name (default: auto-detect)"
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        if not MEDIA_CONTROL_ENABLED:
            return _disabled_result()

        service = get_media_service()
        action = kwargs.get("action", "").strip().lower()
        player = kwargs.get("player")

        action_map = {
            "pause": (service.pause, "Paused"),
            "stop": (service.stop, "Stopped"),
            "next": (service.next_track, "Skipped to next track"),
            "previous": (service.previous_track, "Went to previous track"),
            "prev": (service.previous_track, "Went to previous track"),
            "toggle": (service.play_pause, "Toggled playback"),
        }

        if action not in action_map:
            return ToolResult(
                success=False,
                error=f"Unknown action '{action}'. Use: pause, stop, next, previous, toggle"
            )

        func, label = action_map[action]
        result = func(player)
        if result.get("success"):
            output = label
            if result.get("player"):
                output += f" on {result['player']}"
            return ToolResult(success=True, output=output, metadata=result)
        else:
            return ToolResult(success=False, error=result.get("error", "Unknown error"))


class MediaVolumeTool(BaseTool):
    """Get or set the system audio volume."""

    name = "media_volume"
    description = (
        "Get or set the system audio volume level (0-100). "
        "Supports absolute ('50'), relative ('+10', '-10'), 'mute', and 'unmute'."
    )
    parameters = {
        "level": ToolParameter(
            name="level", type="string", required=False,
            description="Volume level: absolute (e.g. '50'), relative ('+10', '-10'), "
                        "'mute', or 'unmute'. Omit to get current volume."
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        if not MEDIA_CONTROL_ENABLED:
            return _disabled_result()

        service = get_media_service()
        level = kwargs.get("level", "").strip() if kwargs.get("level") else ""

        if not level:
            result = service.get_volume()
            if result.get("success"):
                vol = result.get("volume")
                muted = result.get("muted", False)
                status = f"Volume: {vol}%" + (" (muted)" if muted else "")
                return ToolResult(success=True, output=status, metadata=result)
            else:
                return ToolResult(success=False, error=result.get("error"))

        result = service.set_volume(level)
        if result.get("success"):
            vol = result.get("volume")
            muted = result.get("muted", False)
            output = f"Volume set to {vol}%" + (" (muted)" if muted else "")
            return ToolResult(success=True, output=output, metadata=result)
        else:
            return ToolResult(success=False, error=result.get("error"))


class MediaStatusTool(BaseTool):
    """Get current playback status and track info."""

    name = "media_status"
    description = (
        "Get current playback status: what's playing, track info "
        "(title, artist, album), player state, and volume level."
    )
    parameters = {
        "player": ToolParameter(
            name="player", type="string", required=False,
            description="Specific player name (default: auto-detect)"
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        if not MEDIA_CONTROL_ENABLED:
            return _disabled_result()

        service = get_media_service()
        player = kwargs.get("player")

        result = service.get_status(player)
        if result.get("success"):
            track = result.get("track", {})
            status = result.get("status", "Unknown")
            output = (
                f"Status: {status}\n"
                f"Title: {track.get('title', 'Unknown')}\n"
                f"Artist: {track.get('artist', 'Unknown')}\n"
                f"Album: {track.get('album', 'Unknown')}"
            )
            if track.get("length_seconds"):
                mins, secs = divmod(track["length_seconds"], 60)
                output += f"\nLength: {mins}:{secs:02d}"
            return ToolResult(success=True, output=output, metadata=result)
        else:
            return ToolResult(success=False, error=result.get("error"))
