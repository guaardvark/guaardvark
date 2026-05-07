"""Storyboard Artist — generation-driven agent. Takes a shot prompt + Subject
LoRAs, calls ComfyUI to produce one storyboard image per shot.

Unlike the LLM-driven agents (Screenwriter, Cinematographer, etc.), this agent
doesn't call an LLM and doesn't parse JSON. It's a thin wrapper around the
ComfyUI image-generation client that knows how to stack LoRAs for character
and environment consistency.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ImageGenerator(Protocol):
    """Minimal interface this agent needs from a ComfyUI client.

    Implementations live elsewhere (tools/comfyui_client.py or similar) — this
    Protocol just documents the contract for testing and dependency injection.
    """

    def generate_image(
        self,
        *,
        prompt: str,
        loras: list[str],
        output_path: str,
        width: int = 1024,
        height: int = 1024,
    ) -> str:
        ...


class StoryboardArtist:
    """Generation-driven agent — calls ImageGenerator.generate_image with stacked LoRAs."""

    name = "storyboard_artist"

    def __init__(self, image_generator: ImageGenerator):
        self.image_generator = image_generator

    def generate_for_shot(
        self,
        *,
        prompt: str,
        lora_paths: list[str],
        output_dir: str,
        shot_number: int,
        width: int = 1024,
        height: int = 1024,
    ) -> str:
        """Generate one storyboard image for a shot. Returns the output path."""
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        output_path = str(Path(output_dir) / f"shot_{shot_number}.png")
        return self.image_generator.generate_image(
            prompt=prompt,
            loras=lora_paths,
            output_path=output_path,
            width=width,
            height=height,
        )
