"""Regression: /record-post must respect the kill switch."""
from unittest.mock import patch

import pytest


def test_record_post_returns_403_when_kill_switch_off(app, client):
    """When kill_switch.is_enabled() returns False, /record-post must
    refuse to flip status='posted' and must return 403."""
    from backend.api import social_outreach_api
    app.register_blueprint(social_outreach_api.social_outreach_bp)
    
    with patch("backend.api.social_outreach_api.kill_switch.is_enabled", return_value=False):
        resp = client.post(
            "/api/social-outreach/record-post",
            json={"audit_id": 1, "platform": "reddit", "url": "https://reddit.com/x", "text": "hi"},
        )
    assert resp.status_code == 403
    body = resp.get_json()
    assert "kill" in body.get("error", "").lower() or "disabled" in body.get("error", "").lower()
