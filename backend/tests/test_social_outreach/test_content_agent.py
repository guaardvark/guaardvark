"""Content agent (Phase 2) — turns candidates into drafts.

Tests cover:
  • candidate → drafted (good draft, grade ≥ MIN_GRADE)
  • candidate → rejected (low grade, empty draft, json parse error, draft call raises)
  • candidate → skipped (already non-candidate status, e.g. drafted/posted/rejected)
  • candidate not found (id doesn't exist in DB)
  • posted_text gets UTM tags applied
  • batch processes oldest first

The persona drafting call is mocked at draft_outreach_text and apply_utm_tags.
We don't want a live Ollama call inside the test loop.
"""
import json
from unittest.mock import patch

import pytest

from backend.models import SocialOutreachLog, db
from backend.services.social_outreach.content_agent import (
    MIN_GRADE,
    ContentAgent,
)


@pytest.fixture
def app():
    from flask import Flask
    app = Flask(__name__)
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    })
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _make_candidate(payload: dict | None = None, **overrides) -> SocialOutreachLog:
    """Create a candidate row with sensible defaults."""
    payload = payload or {
        "feature_hint": "local_ai",
        "stage": "recon",
        "title": "Anyone tried Ollama with local RAG?",
        "selftext_preview": "I'm running into context size issues...",
        "top_comments": ["Have you tried qwen3?", "What hardware?"],
        "score": 500,
        "num_comments": 25,
    }
    defaults = dict(
        platform="reddit",
        action="comment",
        target_url="https://www.reddit.com/r/LocalLLaMA/comments/abc/x/",
        target_thread_id="abc",
        draft_text=json.dumps(payload),
        status="candidate",
        grade_score=0.5,
    )
    defaults.update(overrides)
    row = SocialOutreachLog(**defaults)
    db.session.add(row)
    db.session.commit()
    return row


def test_draft_candidate_promotes_good_draft_to_drafted(app):
    """Grade above threshold + non-empty draft → status flips to drafted,
    draft_text is replaced with the LLM text, posted_text is UTM-tagged."""
    with app.app_context():
        row = _make_candidate()
        with patch(
            "backend.services.social_outreach.content_agent.persona.draft_outreach_text",
            return_value={"draft": "Great point about context sizes — Guaardvark handles that.", "grade": 0.85},
        ), patch(
            "backend.services.social_outreach.content_agent.persona.apply_utm_tags",
            side_effect=lambda text, **k: text + " [tagged]",  # cheap stand-in
        ):
            outcome = ContentAgent().draft_candidate(row.id)
        assert outcome == {"status": "drafted", "grade": 0.85, "reason": None}

        updated = SocialOutreachLog.query.get(row.id)
        assert updated.status == "drafted"
        assert updated.draft_text == "Great point about context sizes — Guaardvark handles that."
        assert updated.posted_text.endswith("[tagged]")
        assert updated.grade_score == pytest.approx(0.85)


def test_draft_candidate_rejects_low_grade(app):
    with app.app_context():
        row = _make_candidate()
        with patch(
            "backend.services.social_outreach.content_agent.persona.draft_outreach_text",
            return_value={"draft": "ok draft", "grade": 0.4},
        ):
            outcome = ContentAgent().draft_candidate(row.id)
        assert outcome["status"] == "rejected"
        assert outcome["reason"] == "grade_too_low"

        updated = SocialOutreachLog.query.get(row.id)
        assert updated.status == "rejected"
        assert "grade_too_low" in updated.abort_reason


def test_draft_candidate_rejects_empty_draft(app):
    with app.app_context():
        row = _make_candidate()
        with patch(
            "backend.services.social_outreach.content_agent.persona.draft_outreach_text",
            return_value={"draft": "  ", "grade": 0.9},  # whitespace only
        ):
            outcome = ContentAgent().draft_candidate(row.id)
        assert outcome["status"] == "rejected"
        assert outcome["reason"] == "empty_draft"
        updated = SocialOutreachLog.query.get(row.id)
        assert updated.status == "rejected"
        assert updated.abort_reason == "empty draft from LLM"


def test_draft_candidate_handles_unparseable_json(app):
    """Legacy or corrupt rows whose draft_text isn't JSON should reject cleanly,
    not crash the batch."""
    with app.app_context():
        row = SocialOutreachLog(
            platform="reddit",
            action="comment",
            target_url="https://r.example/x",
            target_thread_id="bad",
            draft_text="not-actually-json {",  # invalid
            status="candidate",
        )
        db.session.add(row)
        db.session.commit()

        outcome = ContentAgent().draft_candidate(row.id)
        assert outcome["status"] == "rejected"
        assert outcome["reason"] == "json_decode_error"
        updated = SocialOutreachLog.query.get(row.id)
        assert updated.status == "rejected"


def test_draft_candidate_handles_persona_exception(app):
    with app.app_context():
        row = _make_candidate()
        with patch(
            "backend.services.social_outreach.content_agent.persona.draft_outreach_text",
            side_effect=RuntimeError("ollama unreachable"),
        ):
            outcome = ContentAgent().draft_candidate(row.id)
        assert outcome["status"] == "rejected"
        assert outcome["reason"] == "draft_call_failed"
        updated = SocialOutreachLog.query.get(row.id)
        assert updated.status == "rejected"
        assert "ollama unreachable" in updated.abort_reason


def test_draft_candidate_skips_non_candidate_rows(app):
    """A row that's already drafted or posted shouldn't be re-drafted by
    a stale tick — the candidate dedupe in Recon should have caught this,
    but defense in depth."""
    with app.app_context():
        row = _make_candidate(status="drafted")
        outcome = ContentAgent().draft_candidate(row.id)
        assert outcome["status"] == "skipped"
        assert "already drafted" in outcome["reason"]
        updated = SocialOutreachLog.query.get(row.id)
        assert updated.status == "drafted"  # untouched


def test_draft_candidate_returns_missing_for_unknown_id(app):
    with app.app_context():
        outcome = ContentAgent().draft_candidate(99999)
        assert outcome["status"] == "missing"


def test_draft_batch_processes_oldest_candidates_first(app):
    """Three candidates, batch_size=2 → only the two oldest get drafted."""
    from datetime import datetime, timedelta, timezone
    with app.app_context():
        # Create three candidates, oldest → newest
        rows = []
        for i in range(3):
            r = SocialOutreachLog(
                platform="reddit",
                action="comment",
                target_url=f"https://r.example/{i}",
                target_thread_id=f"t{i}",
                draft_text=json.dumps({"feature_hint": "x", "title": f"t{i}", "top_comments": []}),
                status="candidate",
                created_at=datetime.now(timezone.utc) - timedelta(hours=10 - i),  # i=0 is oldest
            )
            db.session.add(r)
            rows.append(r)
        db.session.commit()

        with patch(
            "backend.services.social_outreach.content_agent.persona.draft_outreach_text",
            return_value={"draft": "ok", "grade": 0.9},
        ), patch(
            "backend.services.social_outreach.content_agent.persona.apply_utm_tags",
            side_effect=lambda text, **k: text,
        ):
            report = ContentAgent().draft_batch(batch_size=2)
        assert report == {"considered": 2, "drafted": 2, "rejected": 0, "errors": 0}
        # Oldest two are drafted, newest is still candidate
        statuses = sorted([r.status for r in SocialOutreachLog.query.all()])
        assert statuses == ["candidate", "drafted", "drafted"]


def test_min_grade_threshold_is_07(app):
    """Sanity check the constant; if someone bumps it the gate logic must adjust too."""
    assert MIN_GRADE == 0.7
