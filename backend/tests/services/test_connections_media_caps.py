"""Publishing carries a per-platform clip-length cap and a disclosure line."""
from types import SimpleNamespace

from backend.services.connections.base import Capabilities, MediaItem
from backend.services.connections import media as media_util
from backend.services.connections import publish_service as ps


def _clip(seconds, attribution=None):
    return MediaItem(path="/x/clip.mp4", mime="video/mp4", bytes=10, duration_s=seconds, attribution=attribution)


def test_duration_cap_is_data_and_enforced():
    caps = Capabilities(video=True, max_video_seconds=15)
    assert caps.to_dict()["max_video_seconds"] == 15
    assert media_util.validate_against(caps, [_clip(12.0)]) == []
    problems = media_util.validate_against(caps, [_clip(20.0)])
    assert problems == ["clip.mp4 runs 20s; the limit is 15s."]
    assert media_util.validate_against(Capabilities(video=True), [_clip(500.0)]) == []
    # An unknown duration is not a violation.
    assert media_util.validate_against(caps, [_clip(None)]) == []


def test_disclosure_names_each_attributed_model_once():
    assert media_util.disclosure_line([_clip(5)]) is None
    line = media_util.disclosure_line([_clip(5, "MiniMax H3"), _clip(5, "MiniMax H3"), _clip(5, "Other")])
    assert line == "Generated with MiniMax H3, Other on Guaardvark."


def test_body_gets_the_disclosure_unless_the_connection_opts_out():
    items = [_clip(5, "MiniMax H3")]
    on = SimpleNamespace(config=None)
    assert ps._body_with_disclosure("Look at this", items, on) == "Look at this\n\nGenerated with MiniMax H3 on Guaardvark."
    assert ps._body_with_disclosure("", items, on) == "Generated with MiniMax H3 on Guaardvark."
    already = "Hi\n\nGenerated with MiniMax H3 on Guaardvark."
    assert ps._body_with_disclosure(already, items, on) == already
    off = SimpleNamespace(config='{"disclose_ai_media": false}')
    assert ps._body_with_disclosure("Look", items, off) == "Look"
    assert ps._body_with_disclosure("Look", [_clip(5)], on) == "Look"
