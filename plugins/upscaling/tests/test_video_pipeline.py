import pytest
from service.video_pipeline import get_video_info


def test_get_video_info_structure():
    """get_video_info returns expected dict structure (mock test)."""
    from unittest.mock import patch

    mock_probe = {
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30/1",
                "nb_frames": "300",
            },
            {"codec_type": "audio"},
        ]
    }
    with patch("service.video_pipeline.ffmpeg.probe", return_value=mock_probe):
        info = get_video_info("/fake/path.mp4")
        assert info["width"] == 1920
        assert info["height"] == 1080
        assert info["fps"] == 30.0
        assert info["nb_frames"] == 300
        assert info["has_audio"] is True
