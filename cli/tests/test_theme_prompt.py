"""Theme-aware prompt colors, compact banner, auto/day palettes."""

from llx.theme import (
    THEMES,
    detect_light_background,
    get_banner,
    get_compact_banner,
    prompt_colors,
    resolve_theme_name,
    set_active_theme,
    should_use_compact_banner,
)


class TestPalettes:
    def test_day_palette_exists(self):
        assert "day" in THEMES
        assert THEMES["day"]["brand"].startswith("#")

    def test_auto_resolves_to_real_palette(self):
        name = resolve_theme_name("auto")
        assert name in THEMES
        assert name != "auto"

    def test_prompt_colors_follow_active_theme(self):
        set_active_theme("hacker")
        colors = prompt_colors()
        assert colors["brand"] == THEMES["hacker"]["brand"]
        assert colors["error"].startswith("#")
        set_active_theme("default")

    def test_set_auto_theme_accepted(self):
        assert set_active_theme("auto") is True
        set_active_theme("default")


class TestBanner:
    def test_compact_forced(self):
        assert should_use_compact_banner("compact") is True
        assert should_use_compact_banner("full") is False

    def test_compact_banner_renders(self):
        panel = get_compact_banner("2.8.1", "online", "model")
        assert panel is not None

    def test_get_banner_compact_flag(self):
        panel = get_banner("2.8.1", "online", "model", compact=True)
        assert panel is not None


class TestLightDetection:
    def test_colorfgbg_light(self, monkeypatch):
        monkeypatch.setenv("COLORFGBG", "0;15")
        assert detect_light_background() is True

    def test_colorfgbg_dark(self, monkeypatch):
        monkeypatch.setenv("COLORFGBG", "15;0")
        assert detect_light_background() is False
