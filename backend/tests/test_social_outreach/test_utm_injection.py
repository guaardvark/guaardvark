"""UTM tagging on guaardvark.com links in outbound posts."""
from unittest.mock import patch, MagicMock
from backend.services.social_outreach import persona


def test_apply_utm_tags_adds_params_to_bare_url():
    text = "Check out https://guaardvark.com for the demo."
    tagged = persona.apply_utm_tags(text, platform="reddit", campaign="v253")
    assert "utm_source=reddit" in tagged
    assert "utm_medium=outreach" in tagged
    assert "utm_campaign=v253" in tagged


def test_apply_utm_tags_preserves_existing_params():
    text = "https://guaardvark.com/release?ref=existing"
    tagged = persona.apply_utm_tags(text, platform="discord", campaign="v253")
    assert "ref=existing" in tagged
    assert "utm_source=discord" in tagged


def test_apply_utm_tags_skips_non_guaardvark_urls():
    text = "See https://github.com/guaardvark/guaardvark and https://reddit.com/r/x"
    tagged = persona.apply_utm_tags(text, platform="reddit", campaign="v253")
    assert "utm_" not in tagged


def test_apply_utm_tags_handles_subdomain():
    text = "Read https://docs.guaardvark.com/quickstart"
    tagged = persona.apply_utm_tags(text, platform="reddit", campaign="v253")
    assert "utm_source=reddit" in tagged


def test_draft_outreach_text_applies_utm_via_persona_wrapper():
    """End-to-end: a draft from persona.draft_outreach_text should have UTM-tagged links."""
    fake = MagicMock(message=MagicMock(content='{"comment": "Try it: https://guaardvark.com/release", "grade": 0.8, "rationale": "x"}'))
    with patch("backend.services.social_outreach.persona.ollama.chat", return_value=fake):
        out = persona.draft_outreach_text(
            platform="reddit",
            context={"url": "x", "title": "y", "body": "z"},
            tone="warm",
            campaign="v253",
        )
    assert "utm_source=reddit" in out["comment"]
