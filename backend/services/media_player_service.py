#!/usr/bin/env python3
"""
Media Player Service - MPRIS2 control via gdbus, VLC launch, and music file search.
"""

import json
import logging
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MEDIA_CONTROL_ENABLED = os.getenv("GUAARDVARK_MEDIA_CONTROL", "true").lower() == "true"

MUSIC_EXTENSIONS = {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".aac", ".opus", ".wma"}

VLC_PATH = "/snap/bin/vlc"

MPRIS2_BUS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS2_OBJECT_PATH = "/org/mpris/MediaPlayer2"
MPRIS2_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
MPRIS2_PROPS_IFACE = "org.freedesktop.DBus.Properties"


class MediaPlayerService:
    """Singleton service for media player control via gdbus + MPRIS2 and VLC."""

    _instance: Optional["MediaPlayerService"] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    @classmethod
    def get_instance(cls) -> "MediaPlayerService":
        return cls()

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        logger.info("MediaPlayerService initialized")

    def _get_music_directory(self) -> str:
        """Read music directory from DB settings, fall back to ~/Music."""
        try:
            from backend.models import db, Setting

            setting = db.session.get(Setting, "music_directory")
            if setting and setting.value and setting.value.strip():
                return setting.value.strip()
        except Exception as e:
            logger.debug(f"Could not read music_directory setting: {e}")
        return str(Path.home() / "Music")

    # ===== gdbus helpers =====

    def _gdbus_call(self, bus_name: str, method: str) -> subprocess.CompletedProcess:
        """Call an MPRIS2 Player method via gdbus."""
        return subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                bus_name,
                "--object-path",
                MPRIS2_OBJECT_PATH,
                "--method",
                f"{MPRIS2_PLAYER_IFACE}.{method}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

    def _gdbus_get_property(self, bus_name: str, iface: str, prop: str) -> str:
        """Get a D-Bus property via gdbus."""
        result = subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                bus_name,
                "--object-path",
                MPRIS2_OBJECT_PATH,
                "--method",
                "org.freedesktop.DBus.Properties.Get",
                iface,
                prop,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        return result.stdout.strip()

    def _find_player_bus(self, player_name: Optional[str] = None) -> str:
        """Find the MPRIS2 bus name for a player."""
        if player_name:
            return MPRIS2_BUS_PREFIX + player_name

        # List all bus names and find MPRIS2 players
        result = subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.freedesktop.DBus",
                "--object-path",
                "/org/freedesktop/DBus",
                "--method",
                "org.freedesktop.DBus.ListNames",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to list D-Bus names: {result.stderr.strip()}")

        for name in re.findall(r"'([^']+)'", result.stdout):
            if name.startswith(MPRIS2_BUS_PREFIX):
                return name

        raise RuntimeError(
            "No media player is currently running. Say 'play <something>' to start."
        )

    def _player_display_name(self, bus_name: str) -> str:
        if bus_name.startswith(MPRIS2_BUS_PREFIX):
            return bus_name[len(MPRIS2_BUS_PREFIX) :]
        return bus_name

    # ===== MPRIS2 Methods =====

    def list_players(self) -> Dict[str, Any]:
        """List all running MPRIS2 media players."""
        if not MEDIA_CONTROL_ENABLED:
            return {"success": False, "error": "Media control disabled"}
        try:
            result = subprocess.run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    "org.freedesktop.DBus",
                    "--object-path",
                    "/org/freedesktop/DBus",
                    "--method",
                    "org.freedesktop.DBus.ListNames",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            players = [
                name[len(MPRIS2_BUS_PREFIX) :]
                for name in re.findall(r"'([^']+)'", result.stdout)
                if name.startswith(MPRIS2_BUS_PREFIX)
            ]
            return {"success": True, "players": players, "count": len(players)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _call_player_method(
        self, method: str, player_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Call a method on the MPRIS2 Player interface."""
        if not MEDIA_CONTROL_ENABLED:
            return {"success": False, "error": "Media control disabled"}
        try:
            bus_name = self._find_player_bus(player_name)
            display = self._player_display_name(bus_name)
            result = self._gdbus_call(bus_name, method)
            if result.returncode != 0:
                return {"success": False, "error": result.stderr.strip()}
            return {"success": True, "player": display, "action": method.lower()}
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"MPRIS2 {method} failed: {e}"}

    def play(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call_player_method("Play", player_name)

    def pause(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call_player_method("Pause", player_name)

    def play_pause(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call_player_method("PlayPause", player_name)

    def stop(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call_player_method("Stop", player_name)

    def next_track(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call_player_method("Next", player_name)

    def previous_track(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call_player_method("Previous", player_name)

    def get_status(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        """Get current playback status and track metadata."""
        if not MEDIA_CONTROL_ENABLED:
            return {"success": False, "error": "Media control disabled"}
        try:
            bus_name = self._find_player_bus(player_name)
            display = self._player_display_name(bus_name)

            # Get PlaybackStatus
            raw_status = self._gdbus_get_property(
                bus_name, MPRIS2_PLAYER_IFACE, "PlaybackStatus"
            )
            status = re.search(r"'([^']+)'", raw_status)
            status = status.group(1) if status else "Unknown"

            # Get Metadata
            raw_meta = self._gdbus_get_property(
                bus_name, MPRIS2_PLAYER_IFACE, "Metadata"
            )
            track_info = self._parse_metadata(raw_meta)

            # Get Volume
            try:
                raw_vol = self._gdbus_get_property(
                    bus_name, MPRIS2_PLAYER_IFACE, "Volume"
                )
                vol_match = re.search(r"([\d.]+)", raw_vol)
                if vol_match:
                    track_info["player_volume"] = round(float(vol_match.group(1)) * 100)
            except Exception:
                pass

            return {
                "success": True,
                "player": display,
                "status": status,
                "track": track_info,
            }
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"Failed to get status: {e}"}

    def _parse_metadata(self, raw: str) -> Dict[str, Any]:
        """Parse gdbus metadata output into a dict."""
        info: Dict[str, Any] = {
            "title": "Unknown",
            "artist": "Unknown",
            "album": "Unknown",
        }

        # Extract title: 'xesam:title': <'Some Title'>
        title = re.search(r"'xesam:title':\s*<'([^']*)'", raw)
        if title:
            info["title"] = title.group(1)

        # Extract artist: 'xesam:artist': <['Artist1', 'Artist2']>
        artist = re.search(r"'xesam:artist':\s*<\[([^\]]*)\]>", raw)
        if artist:
            artists = re.findall(r"'([^']*)'", artist.group(1))
            if artists:
                info["artist"] = ", ".join(artists)

        # Extract album
        album = re.search(r"'xesam:album':\s*<'([^']*)'", raw)
        if album:
            info["album"] = album.group(1)

        # Extract length (microseconds)
        length = re.search(r"'mpris:length':\s*<(?:int64\s+|uint64\s+)?(\d+)>", raw)
        if length:
            info["length_seconds"] = int(length.group(1)) // 1_000_000

        # Extract art URL
        art = re.search(r"'mpris:artUrl':\s*<'([^']*)'", raw)
        if art:
            info["art_url"] = art.group(1)

        return info

    # ===== Volume Control =====

    def get_volume(self) -> Dict[str, Any]:
        """Get current system volume via amixer."""
        try:
            result = subprocess.run(
                ["amixer", "get", "Master"], capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return {"success": False, "error": result.stderr.strip()}
            match = re.search(r"\[(\d+)%\]", result.stdout)
            level = int(match.group(1)) if match else None
            mute_match = re.search(r"\[(on|off)\]", result.stdout)
            muted = mute_match.group(1) == "off" if mute_match else False
            return {"success": True, "volume": level, "muted": muted}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def set_volume(self, level: str) -> Dict[str, Any]:
        """Set system volume. Accepts '50', '+10', '-10', 'mute', 'unmute'."""
        if not MEDIA_CONTROL_ENABLED:
            return {"success": False, "error": "Media control disabled"}
        try:
            level = level.strip()
            if level.lower() == "mute":
                cmd = ["amixer", "set", "Master", "mute"]
            elif level.lower() == "unmute":
                cmd = ["amixer", "set", "Master", "unmute"]
            elif level.startswith("+") or level.startswith("-"):
                amount = level.lstrip("+-")
                direction = "+" if level.startswith("+") else "-"
                cmd = ["amixer", "set", "Master", f"{amount}%{direction}"]
            else:
                cmd = ["amixer", "set", "Master", f"{level}%"]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                return {"success": False, "error": result.stderr.strip()}

            current = self.get_volume()
            return {
                "success": True,
                "volume": current.get("volume"),
                "muted": current.get("muted", False),
                "action": f"set volume to {level}",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ===== Music File Search =====

    def find_music_files(
        self, query: str, search_dirs: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Search for music files matching a query."""
        if not search_dirs:
            search_dirs = [self._get_music_directory()]

        query_lower = query.lower()
        query_parts = query_lower.split()
        matches = []

        for search_dir in search_dirs:
            search_path = Path(search_dir).expanduser()
            if not search_path.is_dir():
                continue
            try:
                for root, dirs, files in os.walk(search_path):
                    for filename in files:
                        ext = Path(filename).suffix.lower()
                        if ext not in MUSIC_EXTENSIONS:
                            continue
                        search_text = (filename + " " + Path(root).name).lower()
                        if all(part in search_text for part in query_parts):
                            matches.append(os.path.join(root, filename))
                            if len(matches) >= 50:
                                break
                    if len(matches) >= 50:
                        break
            except PermissionError:
                continue

        matches.sort()
        return {
            "success": True,
            "files": matches,
            "count": len(matches),
            "query": query,
            "search_dirs": search_dirs,
        }

    # ===== VLC Launch =====

    def _kill_existing_vlc(self):
        """Kill any existing VLC instances before launching a new one."""
        try:
            subprocess.run(["pkill", "-f", "vlc"], capture_output=True, timeout=3)
        except Exception:
            pass

    def launch_vlc(
        self,
        files: Optional[List[str]] = None,
        directory: Optional[str] = None,
        shuffle: bool = False,
    ) -> Dict[str, Any]:
        """Launch VLC with specified files or directory. Kills existing VLC first."""
        if not MEDIA_CONTROL_ENABLED:
            return {"success": False, "error": "Media control disabled"}

        # Kill any existing VLC to avoid multiple instances
        self._kill_existing_vlc()

        vlc_cmd = VLC_PATH
        if not os.path.exists(vlc_cmd):
            vlc_cmd = "vlc"

        cmd = [vlc_cmd]
        if shuffle:
            cmd.append("--random")

        if directory:
            cmd.append(directory)
        elif files:
            cmd.extend(files)
        else:
            return {"success": False, "error": "No files or directory specified"}

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            file_count = len(files) if files else 0
            return {
                "success": True,
                "pid": proc.pid,
                "file_count": file_count,
                "directory": directory,
                "shuffle": shuffle,
                "action": "launched VLC",
            }
        except FileNotFoundError:
            return {
                "success": False,
                "error": "VLC not found. Install VLC to play music.",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to launch VLC: {e}"}

    # ===== High-Level Play =====

    def play_music(self, query: str = "", shuffle: bool = False) -> Dict[str, Any]:
        """Find music files matching query and play them in VLC.
        If query is empty or generic, plays all music in the music directory."""
        if not MEDIA_CONTROL_ENABLED:
            return {"success": False, "error": "Media control disabled"}

        music_dir = self._get_music_directory()

        # For empty/generic queries, play the entire music directory
        if not query or query.lower() in (
            "music",
            "some music",
            "my music",
            "all",
            "everything",
            "anything",
            "songs",
        ):
            return self.launch_vlc(directory=music_dir, shuffle=shuffle or True)

        search_result = self.find_music_files(query)
        if not search_result["success"]:
            return search_result

        files = search_result["files"]
        if not files:
            return {
                "success": False,
                "error": f"No music files found matching '{query}' in {music_dir}. "
                f"Check that your music directory is set correctly in Settings.",
            }

        launch_result = self.launch_vlc(files=files, shuffle=shuffle)
        if not launch_result["success"]:
            return launch_result

        return {
            "success": True,
            "action": "playing",
            "query": query,
            "file_count": len(files),
            "shuffle": shuffle,
            "files": files[:10],
            "total_matches": len(files),
        }


def get_media_service() -> MediaPlayerService:
    return MediaPlayerService.get_instance()
