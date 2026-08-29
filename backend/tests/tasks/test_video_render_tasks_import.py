"""The video render tasks must import — otherwise Celery silently drops them.

2026-08-28: video_text_overlay replaced _DEFAULT_FONT with resolve_font_path(),
video_timeline_render kept importing the old name, and celery_app logged
"Could not import video render tasks" while every timeline render quietly
never ran. An import error in this chain is a feature outage, not a warning.
"""
import importlib


def test_video_render_task_chain_imports():
    import backend.services.video_timeline_render as vtr
    import backend.tasks.video_render_tasks as vrt
    importlib.reload(vtr)
    importlib.reload(vrt)
    assert callable(getattr(vrt, "create_video_render_tasks", None))


def test_drawtext_filter_uses_a_font_that_exists(monkeypatch):
    from backend.services import video_text_overlay as vto
    from backend.services.video_timeline_render import _build_drawtext_filter
    monkeypatch.setattr(vto, "resolve_font_path", lambda: "/fonts/Bold.ttf")
    import backend.services.video_timeline_render as vtr
    monkeypatch.setattr(vtr, "resolve_font_path", lambda: "/fonts/Bold.ttf")
    flt = _build_drawtext_filter({"text": "hello", "fontSize": 40}, "[v0]", "[v1]")
    assert "fontfile=/fonts/Bold.ttf" in flt
