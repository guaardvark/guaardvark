"""A song's ComfyUI prompt id is forgotten when its wait ends, even by an
exception: while any id is remembered, interrupt() skips the unscoped
fallback that reaches prompts the Celery worker queued."""
import contextlib
from unittest.mock import patch

import pytest

from backend.services.comfyui_music_generator import ComfyUIMusicGenerator
from backend.services.comfyui_video_generator import ComfyUIVideoGenerator


class _VG:
    cache_dir = "."

    def _queue_prompt(self, workflow):
        ComfyUIVideoGenerator._track_prompt("song-prompt")
        return "song-prompt"

    def _wait_for_completion(self, prompt_id, timeout, hard_ceiling_s):
        raise RuntimeError("ComfyUI went away")

    _forget_prompt = staticmethod(ComfyUIVideoGenerator._forget_prompt)


def test_song_prompt_is_forgotten_when_the_wait_raises(tmp_path):
    with patch("backend.services.video_model_registry.preflight_video_model", return_value=(True, None)), \
            patch("backend.services.video_model_registry.model_capabilities", return_value={}), \
            patch("backend.services.video_model_registry.vram_mb_for_model", return_value=0), \
            patch("backend.services.gpu_resource_policy.gpu_session",
                  lambda *a, **k: contextlib.nullcontext()):
        with pytest.raises(RuntimeError):
            ComfyUIMusicGenerator(_VG()).generate(caption="x", seconds=5, output_dir=tmp_path)
    assert "song-prompt" not in ComfyUIVideoGenerator._known_prompts()
