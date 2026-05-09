"""Persona wrapper must always inject OUTWARD_FACING_SYSTEM_BLOCK + FEATURE_BLURBS."""
from unittest.mock import patch, MagicMock

from backend.services.social_outreach import persona


def test_draft_outreach_text_injects_system_block():
    fake_resp = MagicMock(message=MagicMock(content='{"comment": "hi", "grade": 0.8, "rationale": "x"}'))
    with patch("backend.services.social_outreach.persona.ollama.chat", return_value=fake_resp) as m:
        out = persona.draft_outreach_text(
            platform="reddit",
            context={"url": "https://reddit.com/x", "title": "test", "body": "test"},
            tone="warm",
        )
    assert m.called
    messages = m.call_args.kwargs.get("messages") or m.call_args.args[1] if len(m.call_args.args) > 1 else m.call_args.kwargs["messages"]
    system_msg = next((msg for msg in messages if msg["role"] == "system"), None)
    assert system_msg is not None
    assert persona.OUTWARD_FACING_SYSTEM_BLOCK in system_msg["content"]


def test_draft_outreach_text_injects_feature_blurbs():
    fake_resp = MagicMock(message=MagicMock(content='{"comment": "hi", "grade": 0.8, "rationale": "x"}'))
    with patch("backend.services.social_outreach.persona.ollama.chat", return_value=fake_resp) as m:
        persona.draft_outreach_text(
            platform="reddit",
            context={"url": "https://reddit.com/x", "title": "ollama question", "body": "vram"},
            tone="warm",
        )
    messages = m.call_args.kwargs.get("messages") or m.call_args.args[1]
    user_msg = next((msg for msg in messages if msg["role"] == "user"), None)
    assert user_msg is not None
    # At least one feature blurb should be selected via RELEVANCE_KEYWORDS
    assert any(b in user_msg["content"] for b in persona.FEATURE_BLURBS.values())
