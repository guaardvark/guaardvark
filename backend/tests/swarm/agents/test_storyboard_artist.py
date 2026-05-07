from unittest.mock import MagicMock
from pathlib import Path

from backend.services.swarm.agents.storyboard_artist import StoryboardArtist


def test_storyboard_calls_generator_with_loras(tmp_path):
    gen = MagicMock()
    gen.generate_image.return_value = str(tmp_path / "shot_1.png")

    artist = StoryboardArtist(image_generator=gen)
    result = artist.generate_for_shot(
        prompt="Dean enters the kitchen, wide shot",
        lora_paths=["/loras/dean.safetensors", "/loras/kitchen.safetensors"],
        output_dir=str(tmp_path),
        shot_number=1,
    )

    assert result == str(tmp_path / "shot_1.png")
    gen.generate_image.assert_called_once()
    kwargs = gen.generate_image.call_args.kwargs
    assert kwargs["prompt"] == "Dean enters the kitchen, wide shot"
    assert kwargs["loras"] == ["/loras/dean.safetensors", "/loras/kitchen.safetensors"]
    assert kwargs["output_path"] == str(tmp_path / "shot_1.png")
    assert kwargs["width"] == 1024
    assert kwargs["height"] == 1024


def test_storyboard_creates_output_dir(tmp_path):
    gen = MagicMock()
    output_dir = tmp_path / "nested" / "storyboard"
    gen.generate_image.return_value = str(output_dir / "shot_3.png")

    artist = StoryboardArtist(image_generator=gen)
    artist.generate_for_shot(
        prompt="x", lora_paths=[], output_dir=str(output_dir), shot_number=3,
    )
    assert output_dir.exists()


def test_storyboard_works_with_no_loras(tmp_path):
    gen = MagicMock()
    gen.generate_image.return_value = str(tmp_path / "shot_5.png")
    artist = StoryboardArtist(image_generator=gen)
    artist.generate_for_shot(prompt="x", lora_paths=[], output_dir=str(tmp_path), shot_number=5)
    assert gen.generate_image.call_args.kwargs["loras"] == []


def test_storyboard_custom_dimensions(tmp_path):
    gen = MagicMock()
    gen.generate_image.return_value = str(tmp_path / "shot_1.png")
    artist = StoryboardArtist(image_generator=gen)
    artist.generate_for_shot(
        prompt="x", lora_paths=[], output_dir=str(tmp_path),
        shot_number=1, width=1920, height=1080,
    )
    kwargs = gen.generate_image.call_args.kwargs
    assert kwargs["width"] == 1920
    assert kwargs["height"] == 1080
