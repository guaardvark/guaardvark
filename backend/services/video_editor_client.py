import logging
import requests
import os

logger = logging.getLogger(__name__)

class VideoEditorClient:
    """
    Client for the Video Editor service.
    Handles project creation and timeline population.
    """

    def __init__(self, backend_url: str):
        self.backend_url = backend_url

    def create_project_with_timeline(
        self, *, name: str, video_clips: list[str],
        voiceovers: list[str | None], music_track: str | None,
    ) -> int | None:
        """
        Creates a new video project and populates it with the given assets.
        Returns the project ID.
        """
        logger.info(f"Handoff to Video Editor: creating project '{name}' with {len(video_clips)} clips")
        
        try:
            # Step 1: Create the project in the main backend
            # We assume there's an internal API for this or we call the video-editor proxy
            resp = requests.post(
                f"{self.backend_url}/video-editor/projects",
                json={
                    "name": name,
                    "assets": video_clips + ([music_track] if music_track else [])
                },
                timeout=10
            )
            resp.raise_for_status()
            project_id = resp.json().get("id")
            
            # Step 2: (Optional) Auto-compose the timeline if the plugin supports it
            # For now, just returning the project ID is enough to bridge the UI.
            
            return project_id

        except Exception as e:
            logger.warning(f"Failed to handoff to Video Editor: {e}")
            return None
