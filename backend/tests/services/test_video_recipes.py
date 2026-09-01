"""Outreach video recipes fill slots into a bundled preset and queue it
through generate_video with sound required."""
from backend.services.social_outreach import video_recipes as vr


def test_recipes_are_the_ad_and_social_presets():
    recipes = vr.list_recipes()
    slugs = {r["slug"] for r in recipes}
    assert {"product-reveal-vertical", "ugc-taste-test", "seamless-web-loop"} <= slugs
    assert all(r["category"] in vr.OUTREACH_CATEGORIES for r in recipes)


def test_fill_recipe_compiles_and_keeps_missing_slots_visible():
    preset, prompt = vr.fill_recipe("product-reveal-vertical", {"product": "a matte black bottle"})
    assert preset["ratio"] == "9:16"
    assert prompt.startswith("integrated_multimodal_description: [Shot 1]")
    assert "[Shot 2] At" in prompt and "[Shot 3] At" in prompt
    assert "non_diegetic_music:" in prompt


def test_queue_recipe_video_asks_for_sound(monkeypatch):
    calls = {}

    class _Tool:
        def execute(self, **kw):
            calls.update(kw)
            from backend.services.agent_tools import ToolResult
            return ToolResult(success=True, output="ok", metadata={"batch_id": "b1", "studio_url": "/video?batch=b1"})

    monkeypatch.setattr("backend.tools.image_tools.VideoGeneratorTool", _Tool)
    out = vr.queue_recipe_video("ugc-taste-test", slots={})
    assert out["success"] and out["batch_id"] == "b1"
    assert calls["audio"] is True and calls["aspect_ratio"] == "9:16" and calls["duration_s"] == 8
    assert calls["style"] == "none" and calls["model"] == "minimax-h3-int8"
    assert calls["prompt"].startswith("integrated_multimodal_description:")
