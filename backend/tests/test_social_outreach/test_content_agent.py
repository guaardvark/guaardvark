"""Content agent (Phase 2) — turns candidates into drafts.

Tests cover:
  • candidate → drafted (good draft, grade ≥ MIN_GRADE, external grader passes/skipped)
  • candidate → rejected (low self-grade, low external grade, empty draft, json parse error, draft call raises)
  • candidate → skipped (already non-candidate status, e.g. drafted/posted/rejected)
  • candidate not found (id doesn't exist in DB)
  • posted_text gets UTM tags applied
  • batch processes oldest first

The persona drafting call is mocked at draft_outreach_text and apply_utm_tags.
The external grader is mocked at grade_draft_externally so tests don't depend
on a running Ollama instance.
"""
import json
from unittest.mock import patch

import pytest


# Default external grader mock — returns skipped=True so the second-opinion
# gate falls through to "trust self-grade", same as production behavior when
# the grader model isn't loaded. Individual tests override to test the gate.
EXT_SKIPPED = {"grade": 0.0, "skipped": True, "model": None, "reason": "test_default"}
EXT_PASS = {"grade": 0.9, "skipped": False, "model": "test", "reason": "looks good", "engages": 1, "on_topic": 1, "appropriate_tone": 1, "concise": 1}
EXT_FAIL = {"grade": 0.25, "skipped": False, "model": "test", "reason": "generic boilerplate", "engages": 0, "on_topic": 1, "appropriate_tone": 0, "concise": 1}


@pytest.fixture(autouse=True)
def mock_external_grader():
    """Default-mock the external grader to skipped=True so tests don't hit
    Ollama. Tests that want to exercise the gate override the patch locally."""
    with patch(
        "backend.services.social_outreach.content_agent.external_grader.grade_draft_externally",
        return_value=EXT_SKIPPED,
    ):
        yield

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
        "top_comments": ["Have you tried gemma4?", "What hardware?"],
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
        assert outcome["status"] == "drafted"
        assert outcome["grade"] == pytest.approx(0.85)
        assert outcome["reason"] is None

        db.session.expire_all()
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

        db.session.expire_all()
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
        db.session.expire_all()
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
        db.session.expire_all()
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
        db.session.expire_all()
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
        db.session.expire_all()
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
        db.session.expire_all()
        statuses = sorted([r.status for r in SocialOutreachLog.query.all()])
        assert statuses == ["candidate", "drafted", "drafted"]


def test_min_grade_threshold_is_07(app):
    """Sanity check the constant; if someone bumps it the gate logic must adjust too."""
    assert MIN_GRADE == 0.7


def test_external_grader_low_score_rejects_even_if_self_grade_high(app):
    """Self-grade is 0.9 (clearly above 0.7) but external grader says 0.25 →
    reject. This is the whole point of the second-opinion gate: catch drafts
    that the writer overrated."""
    with app.app_context():
        row = _make_candidate()
        with patch(
            "backend.services.social_outreach.content_agent.persona.draft_outreach_text",
            return_value={"draft": "looks great to me", "grade": 0.9},
        ), patch(
            "backend.services.social_outreach.content_agent.external_grader.grade_draft_externally",
            return_value=EXT_FAIL,
        ):
            outcome = ContentAgent().draft_candidate(row.id)
        assert outcome["status"] == "rejected"
        assert outcome["reason"] == "external_grade_too_low"
        db.session.expire_all()
        updated = SocialOutreachLog.query.get(row.id)
        assert updated.status == "rejected"
        assert "external_grade_too_low" in updated.abort_reason


def test_external_grader_skipped_falls_through_to_self_grade(app):
    """When the external grader returns skipped=True (model unavailable, call
    failed, etc.) we should NOT block on it — the self-grade alone gates."""
    with app.app_context():
        row = _make_candidate()
        with patch(
            "backend.services.social_outreach.content_agent.persona.draft_outreach_text",
            return_value={"draft": "valid draft", "grade": 0.85},
        ), patch(
            "backend.services.social_outreach.content_agent.external_grader.grade_draft_externally",
            return_value={"grade": 0.0, "skipped": True, "model": None, "reason": "no_grader_model_loaded"},
        ), patch(
            "backend.services.social_outreach.content_agent.persona.apply_utm_tags",
            side_effect=lambda text, **k: text,
        ):
            outcome = ContentAgent().draft_candidate(row.id)
        assert outcome["status"] == "drafted"


def test_unsupported_action_is_rejected(app):
    """ContentAgent should refuse any action it doesn't know how to map to
    a persona mode. Recon writes "comment" today; if a future caller writes
    "abort" or anything else, fail loud rather than silent-default to share."""
    with app.app_context():
        row = _make_candidate(action="abort")
        outcome = ContentAgent().draft_candidate(row.id)
        assert outcome["status"] == "rejected"
        assert outcome["reason"] == "unsupported_action"
        db.session.expire_all()
        updated = SocialOutreachLog.query.get(row.id)
        assert updated.status == "rejected"
        assert "abort" in updated.abort_reason
