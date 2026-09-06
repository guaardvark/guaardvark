"""CLI video generate omits SVD-era fps/size/steps unless the user typed them."""
from unittest.mock import MagicMock, patch

from llx.commands.videos import _build_gen_params, videos_models


def test_build_gen_params_omits_unset_canvas():
    params = _build_gen_params(
        model=None, duration=None, fps=None, width=None, height=None, steps=None,
        guidance=7.5, motion=1.0, seed=None, frames_only=False,
    )
    assert "fps" not in params
    assert "width" not in params
    assert "height" not in params
    assert "duration_frames" not in params
    assert "num_inference_steps" not in params
    assert "model" not in params


def test_build_gen_params_keeps_typed_values():
    params = _build_gen_params(
        model="wan22-5b", duration=73, fps=24, width=1280, height=704, steps=20,
        guidance=7.5, motion=1.0, seed=42, frames_only=False,
    )
    assert params["model"] == "wan22-5b"
    assert params["fps"] == 24
    assert params["width"] == 1280
    assert params["seed"] == 42


def test_videos_models_hits_the_api():
    mock_client = MagicMock()
    mock_client.get.return_value = {
        "success": True,
        "data": {
            "models": [
                {"id": "wan22-5b", "name": "Wan 5B", "is_ready": True, "active": True,
                 "capabilities": {"supports_t2v": True}, "vram_mb": 11000},
            ],
            "active_t2v": "wan22-5b",
        },
    }
    with patch("llx.commands.videos.get_client", return_value=mock_client), \
         patch("llx.commands.videos.get_global_server", return_value="http://127.0.0.1:5000"), \
         patch("llx.commands.videos.get_global_json", return_value=True):
        videos_models(server="http://127.0.0.1:5000", json_out=True)
    mock_client.get.assert_called_once_with("/api/batch-video/models")
