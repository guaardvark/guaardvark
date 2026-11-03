"""Scorsese: measure how a video is shot and cut, then keep that as a style recipe.

``analyze`` measures a video with ffmpeg, OpenCV and librosa (shots, camera
motion, lighting, colour, pacing, beat alignment) into one JSON document.
``recipe`` turns the measured profile into a style recipe the Video Editor,
the Music Video director and the Film Crew can follow, and compares profiles.
``jobs`` keeps the on-disk record of an analysis run for the API and Celery.

Every number here is measured by classical computer vision and carries
``source: "cv"``. A vision model may label shots later; its labels never
replace a measured value.
"""

from backend.services.scorsese.analyze import analyze_video, detect_shots, probe
from backend.services.scorsese.recipe import (
    build_recipe,
    distance,
    list_recipes,
    load_recipe,
    save_recipe,
    style_clause,
)

__all__ = ["analyze_video", "detect_shots", "probe", "build_recipe", "distance", "list_recipes",
           "load_recipe", "save_recipe", "style_clause"]
