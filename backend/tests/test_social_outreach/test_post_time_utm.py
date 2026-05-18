"""UTM injection at the servo posting boundary catches edited drafts.

The original UTM injection only ran in persona.draft_outreach_text (the LLM
draft path). User-edited drafts (snippet inserts, hand-typed URLs) bypassed
it. These tests verify the post-time guard catches those cases.
"""
from unittest.mock import MagicMock, patch

from backend.services.social_outreach import persona


def test_apply_utm_tags_tags_user_edited_link():
    """A draft edited via OutreachPage's 'Insert site URL' (bare URL) gets tagged."""
    user_edited = "Try https://guaardvark.com — does the things you'd expect."
    tagged = persona.apply_utm_tags(user_edited, platform="reddit", campaign="v253")
    assert "utm_source=reddit" in tagged
    assert "utm_campaign=v253" in tagged


def test_apply_utm_tags_tags_hand_typed_link():
    """A draft where the user hand-typed the URL also gets tagged."""
    hand_typed = "Made with https://guaardvark.com (local-first AI)."
    tagged = persona.apply_utm_tags(hand_typed, platform="discord", campaign="v253")
    assert "utm_source=discord" in tagged


def test_record_post_endpoint_tags_posted_text(monkeypatch):
    """POST /record-post must run posted_text through apply_utm_tags before persisting.

    Caller may have forgotten to tag (e.g., a Discord cog that posts without
    going through reddit_outreach.run_one_pass).
    """
    from backend.api import social_outreach_api as api_mod

    captured = {}

    def fake_apply(text, **kw):
        captured["text"] = text
        captured["kw"] = kw
        return text + "?utm_marker=ran"

    # Mock kill switch on, capture the apply_utm_tags call, mock DB write.
    monkeypatch.setattr(api_mod.kill_switch, "is_enabled", lambda: True)
    monkeypatch.setattr(api_mod.kill_switch, "record_post", lambda p: None)
    monkeypatch.setattr(api_mod.persona, "apply_utm_tags", fake_apply)

    # No audit_id branch — the easier code path; goes to log_outreach_event.
    log_calls = []
    monkeypatch.setattr(api_mod.audit, "log_outreach_event",
                        lambda **kw: log_calls.append(kw))

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api_mod.social_outreach_bp)
    client = app.test_client()
    resp = client.post(
        "/api/social-outreach/record-post",
        json={"platform": "reddit", "posted_text": "Try https://guaardvark.com"},
    )

    assert resp.status_code == 200
    assert captured.get("text") == "Try https://guaardvark.com"
    assert captured.get("kw", {}).get("platform") == "reddit"
    assert captured.get("kw", {}).get("campaign") == "v253"
    # The fake_apply mutated the text — log_outreach_event got the tagged version.
    assert log_calls, "expected log_outreach_event to be called"
    assert log_calls[0]["posted_text"] == "Try https://guaardvark.com?utm_marker=ran"
